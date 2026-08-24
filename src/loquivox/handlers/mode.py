"""
Unified handler for all recording modes.
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

import numpy as np

import loquivox.config as config_module
from loquivox.config import CFG
from loquivox.decorators import run_on_main_thread
from loquivox.managers.chat import ChatManager
from loquivox.managers.history import HistoryManager
from loquivox.managers.overlay import OverlayManager
from loquivox.platform import get_clipboard
from loquivox.services.ai import AIService
from loquivox.services.audio import AudioService
from loquivox.services.clipboard import ClipboardService
from loquivox.services.image import ImageService
from loquivox.services.tts import TTSService
from loquivox.state import STATE

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib


class ModeHandler:
    """Unified handler for all recording modes."""

    @staticmethod
    @run_on_main_thread
    def stop_recording_safe() -> None:
        """Safely stop recording and process (callable from any thread)."""
        if not STATE.recording:
            return

        print("🛑 Voice Stop Triggered (Silence)")
        OverlayManager.set_transcribing()
        audio_data = AudioService.stop_recording()

        # Hand off the live session (if any) and clear it so the next recording
        # starts clean.
        session = STATE.stream_session
        STATE.stream_session = None

        if session is not None:
            ModeHandler.process_stream_async(STATE.current_mode, session, audio_data)
        elif audio_data is not None:
            ModeHandler.process_audio_async(STATE.current_mode, audio_data)
        else:
            OverlayManager.hide()

    @staticmethod
    @run_on_main_thread
    def cancel_active() -> None:
        """
        Abort the current recording and/or in-flight transcription without
        inserting any text (bound to the 'cancel' hotkey, default Esc).

        Bumping ``recording_generation`` makes any transcription already running
        in a worker thread fail the stale-guard in ``process()`` — so it is
        dropped before it can paste. The audio stream (if still open) is stopped
        and its buffer discarded. Safe to call from any thread.
        """
        was_active = STATE.recording or STATE.stream_session is not None
        # Supersede any in-flight transcription so its result is discarded.
        STATE.recording_generation += 1
        if STATE.recording:
            try:
                AudioService.stop_recording()  # closes the stream, no processing
            except Exception:
                pass
        STATE.audio_buffer = []
        STATE.stream_session = None
        STATE.current_mode = None
        STATE.paused = False
        OverlayManager.hide()
        if was_active:
            print("✖️  Cancelled — nothing inserted")

    @staticmethod
    def process_audio_async(mode: str, audio_data: np.ndarray,
                            level_override: Optional[int] = None) -> None:
        """
        Transcribe and process audio off the calling thread.

        Safe to call from any thread (e.g. the keyboard listener): the
        blocking Groq call runs in a worker thread and all UI work is
        marshalled back to the GTK main loop via GLib.idle_add.
        ``level_override`` forces a refinement level for this dictation.
        """
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._process_worker,
            args=(mode, audio_data, generation, level_override),
            daemon=True,
        ).start()

    @staticmethod
    def _process_worker(mode: str, audio_data: np.ndarray, generation: int,
                        level_override: Optional[int] = None) -> None:
        """Worker thread for processing audio."""
        transcribed = None
        try:
            transcribed = AudioService.transcribe(audio_data)
        except Exception:
            pass

        if transcribed:
            transcribed = ModeHandler._maybe_postprocess(mode, transcribed, level_override)
            # Run processing (API calls etc) on the GTK main thread
            GLib.idle_add(lambda: ModeHandler.process(mode, transcribed, generation))
        else:
            # Nothing to insert (too short / failed) — clear the indicator, but
            # only if a newer recording hasn't already taken over the overlay.
            OverlayManager.hide(generation)

    @staticmethod
    def _maybe_postprocess(mode: str, text: str, level_override: Optional[int] = None) -> str:
        """
        Apply optional LLM post-processing to dictation text (refinement level
        or translate). Runs here in the worker thread so the LLM round-trip
        never blocks the GTK loop. No-op unless mode is 'dictation'. A
        ``level_override`` (from the on-the-fly chooser) forces that level.
        """
        if mode != "dictation":
            return text
        from loquivox.services.postprocess import PostProcessor
        return PostProcessor.process(text, level_override)

    @staticmethod
    def process_stream_async(mode: str, session, audio_data: Optional[np.ndarray],
                             level_override: Optional[int] = None) -> None:
        """
        Finalize a live streaming session off-thread, then process the result.

        ``audio_data`` is the buffered recording, kept as the offline fallback
        if the live stream produced nothing (handled inside the dispatcher).
        ``level_override`` forces a refinement level for this dictation.
        """
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._stream_worker,
            args=(mode, session, audio_data, generation, level_override),
            daemon=True,
        ).start()

    @staticmethod
    def _stream_worker(mode: str, session, audio_data: Optional[np.ndarray],
                       generation: int, level_override: Optional[int] = None) -> None:
        """Worker thread: finalize the stream (with batch fallback) and process."""
        from loquivox.transcription import get_dispatcher

        transcribed = None
        try:
            transcribed = get_dispatcher().finish_stream(
                session, audio_data, STATE.capture_rate
            )
        except Exception:
            pass

        if transcribed:
            transcribed = ModeHandler._maybe_postprocess(mode, transcribed, level_override)
            GLib.idle_add(lambda: ModeHandler.process(mode, transcribed, generation))
        else:
            OverlayManager.hide(generation)

    @staticmethod
    def process(mode: str, transcribed_text: str, generation: Optional[int] = None) -> None:
        """Route to appropriate handler based on mode."""
        # --- Stale Guard ---
        # Drop a result whose recording was superseded by a newer one while the
        # transcription was in flight.
        if generation is not None and generation != STATE.recording_generation:
            print(f"⏭️ Ignored stale transcription (gen {generation}): '{transcribed_text}'")
            # Do NOT hide unconditionally: a newer recording owns the overlay now.
            OverlayManager.hide(generation)
            return

        # --- Hallucination Guard ---
        # Whisper often emits canned phrases ("Thank you", "Merci", "Untertitel")
        # on silence; filter them to prevent weird loops.
        clean = transcribed_text.strip().lower().rstrip(".!?")
        if clean in CFG.HALLUCINATIONS or len(clean) < 2:
            print(f"⚠️ Ignored Hallucination: '{transcribed_text}'")
            OverlayManager.hide(generation)
            return

        handlers = {
            "dictation": ModeHandler._handle_dictation,
            "ai": ModeHandler._handle_ai,
            "ai_rewrite": ModeHandler._handle_ai_rewrite,
            "vision": ModeHandler._handle_vision,
        }
        handler = handlers.get(mode)
        try:
            if handler and transcribed_text:
                handler(transcribed_text)
        finally:
            # Clear the 'transcribing' indicator once insertion is done.
            OverlayManager.hide(generation)

    @staticmethod
    def _handle_dictation(text: str) -> None:
        """Handle dictation mode: transcribe and type."""
        HistoryManager.add_answer(f"[Dictation] {text}")
        ChatManager.add_message("user", f"🎤 {text}")
        ClipboardService.type_text(text)

    @staticmethod
    def _handle_ai(text: str) -> None:
        """Handle AI chat mode: get response and type."""
        response = AIService.chat(text)
        if not response:
            return
        ModeHandler._deliver_response(
            response, history_user=text, chat_user_text=text, output="type"
        )

    @staticmethod
    def _rewrite_prompt(instruction: str, original: str) -> str:
        """The rewrite prompt — extracted so redo/re-dictate can rebuild it."""
        return (
            f"INSTRUCTION:\n{instruction}\n\n"
            f"ORIGINAL TEXT:\n{original}\n\n"
            "Rewrite the original text based on the instruction. "
            "Output ONLY the finished text, without introduction or formatting."
        )

    @staticmethod
    def _handle_ai_rewrite(text: str) -> None:
        """
        Rewrite the selected text per the dictated instruction, with a review
        panel. The selection (captured up-front on the main thread) and the AI
        call are handed to the shared worker so the GTK loop never freezes.
        """
        original = get_clipboard().paste().strip()
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._ai_action_worker,
            args=("ai_rewrite", text, generation,
                  lambda instr: AIService.chat(ModeHandler._rewrite_prompt(instr, original))),
            kwargs=dict(
                output="paste",
                history_fmt=lambda i: f"[Rewrite] {i}\nOriginal: {original[:200]}...",
                chat_fmt=lambda i: f"✍️ {i}",
            ),
            daemon=True,
        ).start()

    @staticmethod
    def _handle_vision(text: str) -> None:
        """
        Handle vision mode: screenshot + AI analysis, with a review panel.

        Runs on the GTK main thread. The recording overlay is torn down
        *immediately* (no fade) so it never lands in the screenshot, then the
        blocking capture + vision call + review are handed to a worker thread.
        """
        OverlayManager.hide_immediate()
        # Capture the generation now (process() already validated it) so a late
        # vision answer from a superseded/cancelled recording is dropped.
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._vision_action_worker, args=(text, generation), daemon=True
        ).start()

    @staticmethod
    def _vision_action_worker(text: str, generation: int) -> None:
        """Worker: wait out the compositor repaint, capture, then run the panel flow."""
        import time

        # Give the compositor a frame to drop the just-destroyed overlay before
        # grabbing the screen (the window is already gone, this is just paint).
        time.sleep(0.12)

        image_b64 = ImageService.take_screenshot()
        if not image_b64:
            OverlayManager.hide(generation)
            return
        # The same screenshot is reused across redo/re-dictate (no re-capture).
        ModeHandler._ai_action_worker(
            "vision", text, generation,
            lambda instr: AIService.vision(instr, image_b64),
            output="type",
            history_fmt=lambda i: f"[Screenshot] {i}",
            chat_fmt=lambda i: f"📸 {i}",
        )

    @staticmethod
    def _ai_action_worker(mode: str, instruction: str, generation: int, run_ai,
                          *, output: str, history_fmt, chat_fmt,
                          max_redos: int = 5) -> None:
        """
        Shared rewrite/vision flow on a worker thread: show the thinking panel,
        run the AI call off the GTK loop, present the result for review, then
        deliver (accept) / loop (redo) / re-record the instruction (redict) /
        drop (reject or timeout). Generation-guarded throughout so a superseded
        recording never shows a panel or inserts text.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle

        redos = 0
        try:
            while True:
                if generation != STATE.recording_generation:
                    return
                OverlayManager.set_ai_panel(mode, instruction, generation=generation)
                response = run_ai(instruction)
                if not response:
                    return
                if generation != STATE.recording_generation:
                    return
                OverlayManager.set_ai_panel(mode, instruction, result=response,
                                            generation=generation)

                action = KeyboardHandler.capture_review(mode)
                if action == "accept":
                    resp, instr, gen = response, instruction, generation
                    GLib.idle_add(lambda: ModeHandler._deliver_response(
                        resp, history_user=history_fmt(instr),
                        chat_user_text=chat_fmt(instr),
                        generation=gen, output=output))
                    return
                if action == "copy":
                    get_clipboard().copy(response)
                    print("📋 Result copied to the clipboard")
                    return
                if action == "redo":
                    redos += 1
                    if redos > max_redos:
                        return
                    continue
                if action == "redict":
                    new = KeyboardHandler.record_instruction(mode)
                    # start_recording bumped the generation — adopt the new one.
                    generation = STATE.recording_generation
                    if not new:
                        return
                    instruction = new
                    continue
                return  # reject
        finally:
            OverlayManager.hide(generation)

    @staticmethod
    def _deliver_response(response: str, *, history_user: str,
                          chat_user_text: Optional[str] = None,
                          generation: Optional[int] = None,
                          output: Optional[str] = "type") -> None:
        """
        Record an AI answer everywhere (histories, chat overlay), optionally emit
        it at the cursor, and speak it. The single delivery path for every mode.
        Runs on the GTK main thread.

        - ``generation``: when given, the answer is dropped if a newer recording
          has superseded it (the same stale-guard as ``process()``).
        - ``chat_user_text``: user bubble to add to the overlay; pass ``None`` when
          the caller already echoed it (typed chat).
        - ``output``: ``"type"`` types at the cursor, ``"paste"`` pastes over the
          selection (rewrite), ``None`` emits nothing (typed chat).
        """
        if generation is not None and generation != STATE.recording_generation:
            print(f"⏭️ Dropped stale AI answer (gen {generation})")
            return

        HistoryManager.add_message("user", history_user)
        HistoryManager.add_message("assistant", response)
        HistoryManager.add_answer(response)

        if chat_user_text is not None:
            ChatManager.add_message("user", chat_user_text)
        ChatManager.add_message("assistant", response)

        if output == "type":
            ClipboardService.type_text(response)
        elif output == "paste":
            ClipboardService.paste_text(response)
        TTSService.speak(response)

    # --- Talk mode (spoken conversation → one generated text) ---------------

    @staticmethod
    def start_talk_session() -> None:
        """
        Open a talk session: several spoken turns to work out what the text
        should say, then one generation pass that writes it.

        Called from the keyboard listener thread on the 'talk' hotkey. The
        session itself runs in its own worker (it blocks on recording,
        transcription, the model and the user's keys), and holds the keyboard
        for its whole life — see ``GrabbedKeys``.
        """
        if STATE.talk_active or STATE.recording:
            return
        STATE.talk_active = True
        threading.Thread(target=ModeHandler._talk_worker, daemon=True).start()

    @staticmethod
    def _talk_worker() -> None:
        """
        Worker thread: converse, then write the text, until the user is done.

        'Keep talking' from the review panel loops back into the conversation
        with everything already said still in the brief, so a wrong result is
        one sentence away from being right.
        """
        from loquivox.handlers.keyboard import GrabbedKeys  # lazy: avoid import cycle
        from loquivox.services.talk import TalkSession

        session = TalkSession()
        try:
            with GrabbedKeys() as keys:
                if not keys.alive:
                    print("⚠️  Talk mode needs keyboard access — is your user in "
                          "the 'input' group?")
                    return
                print("🗣️  Talk mode — speak freely. Enter: write the text · "
                      "Space: end this turn · Esc: drop the conversation")
                while True:
                    if ModeHandler._talk_converse(session, keys) == "cancel":
                        print("✖️  Talk mode cancelled — nothing written")
                        return
                    if ModeHandler._talk_generate(session, keys) != "talk":
                        return
        finally:
            STATE.vad = None
            if STATE.recording:  # a turn died mid-flight — close the stream
                try:
                    AudioService.stop_recording()
                except Exception:
                    pass
            STATE.stream_session = None
            STATE.audio_buffer = []
            STATE.current_mode = None
            STATE.talk_active = False
            OverlayManager.hide()

    @staticmethod
    def _talk_converse(session, keys) -> str:
        """
        Run spoken turns until the user asks for the text ("finish"), drops the
        conversation ("cancel"), or the turn budget runs out.
        """
        cfg = config_module.CFG
        while session.user_turns < cfg.TALK_MAX_TURNS:
            idle_action = "finish" if session.user_turns else "cancel"
            audio, action = ModeHandler._talk_listen(keys, idle_action)
            if action == "cancel":
                return "cancel"

            text = ModeHandler._talk_transcribe(audio)
            if action == "finish":
                # Whatever was being said when Enter came still counts as brief.
                if text:
                    ChatManager.add_message("user", f"🗣️ {text}")
                    session.add_user(text)
                return "finish"
            if not text:
                continue  # nothing said (or a hallucination) — just listen again

            ChatManager.add_message("user", f"🗣️ {text}")
            OverlayManager.set_status("Thinking…")
            reply = session.reply(text)
            if not reply:
                continue
            ChatManager.add_message("assistant", reply)
            OverlayManager.set_status("Speaking…")
            # Blocking on purpose: the next turn starts recording the moment
            # this returns, and must not capture the assistant's own voice.
            TTSService.speak(reply, wait=True, force=cfg.TALK_SPEAK_REPLIES)
        print(f"🗣️  Talk mode: {cfg.TALK_MAX_TURNS} turns reached — writing the text")
        return "finish"

    @staticmethod
    def _talk_listen(keys, idle_action: str) -> Tuple[Optional[np.ndarray], str]:
        """
        Record one spoken turn and return ``(audio, action)``.

        The turn ends on its own when the local VAD hears a long enough pause
        (that is talk mode's whole point — no key to hold), and otherwise on
        Space / the talk key, on the turn timeout, or on Enter/Esc, which end
        the conversation. A turn where nothing at all was said for
        ``TALK_IDLE_TIMEOUT`` yields ``idle_action``: the user has walked away
        from the microphone, not paused mid-sentence.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle
        from loquivox.services.vad import VoiceActivityDetector

        cfg = config_module.CFG
        STATE.vad = None
        STATE.current_mode = "talk"
        OverlayManager.show("talk")
        AudioService.start_recording()
        if cfg.TALK_VAD:
            STATE.vad = VoiceActivityDetector(
                STATE.capture_rate,
                threshold=cfg.TALK_VAD_THRESHOLD,
                silence_ms=cfg.TALK_VAD_SILENCE_MS,
                min_speech_ms=cfg.TALK_VAD_MIN_SPEECH_MS,
            )

        mapping = KeyboardHandler.talk_listen_keys()
        action = "send"
        deadline = time.monotonic() + cfg.TALK_TURN_TIMEOUT
        # Exclusive only while we wait on the user: no keystroke of this turn
        # reaches the app underneath, and nothing here can block on the network.
        with keys.exclusive():
            while True:
                pressed = keys.poll(mapping, 0.1)
                if pressed is not None:
                    action = pressed
                    break
                vad = STATE.vad
                if vad is not None:
                    if vad.ended:
                        break
                    if not vad.speech_started and vad.elapsed >= cfg.TALK_IDLE_TIMEOUT:
                        action = idle_action
                        break
                if time.monotonic() >= deadline:
                    break

        STATE.vad = None
        audio = AudioService.stop_recording()
        if action == "cancel":
            # Nothing will be transcribed — close the live session by hand,
            # since only _talk_transcribe would have finalized it.
            stream, STATE.stream_session = STATE.stream_session, None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        else:
            OverlayManager.set_transcribing()
        return audio, action

    @staticmethod
    def _talk_transcribe(audio: Optional[np.ndarray]) -> Optional[str]:
        """
        Turn a recorded talk turn into text, or None if it held no usable speech.

        Finalizes the live session when a streaming backend is in use (with the
        buffered audio as its fallback), exactly like ``_stream_worker``, and
        applies ``process()``'s hallucination guard — a spoken conversation
        would otherwise happily reply to Whisper's "Merci" on silence.
        """
        from loquivox.transcription import get_dispatcher

        stream = STATE.stream_session
        STATE.stream_session = None
        text = None
        try:
            if stream is not None:
                text = get_dispatcher().finish_stream(stream, audio, STATE.capture_rate)
            elif audio is not None:
                text = AudioService.transcribe(audio)
        except Exception:
            text = None

        text = (text or "").strip()
        if not text:
            return None
        clean = text.lower().rstrip(".!?")
        if clean in CFG.HALLUCINATIONS or len(clean) < 2:
            print(f"⚠️ Talk mode ignored: '{text}'")
            return None
        return text

    @staticmethod
    def _talk_generate(session, keys) -> str:
        """
        Write the final text from the conversation and put it up for review.

        Returns "talk" to go back to the conversation (V), or "done" once the
        text has been delivered, dropped, or left on the clipboard. Nothing is
        ever typed without an explicit accept — on timeout the text lands on the
        clipboard instead, so the conversation is never wasted.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle

        if not session.user_turns:
            print("✖️  Nothing was said — talk mode closed")
            return "done"

        cfg = config_module.CFG
        brief = session.brief()
        mapping = KeyboardHandler.talk_review_keys()
        while True:
            OverlayManager.set_ai_panel("talk", brief)
            text = session.generate()
            if not text:
                print("⚠️  Talk mode: no text was produced")
                return "done"

            OverlayManager.set_ai_panel("talk", brief, result=text)
            with keys.exclusive():
                action = keys.poll(mapping, cfg.TALK_REVIEW_TIMEOUT) or "timeout"
            if action == "redo":
                continue
            if action == "talk":
                return "talk"
            if action == "reject":
                print("✖️  Generated text discarded")
                return "done"
            if action == "accept":
                ModeHandler._deliver_talk_text(text, typed=True)
            else:  # copy, or the review timing out
                ModeHandler._deliver_talk_text(text, typed=False)
            return "done"

    @staticmethod
    @run_on_main_thread
    def _deliver_talk_text(text: str, *, typed: bool) -> None:
        """
        Hand the generated text over: at the cursor when accepted, and on the
        clipboard either way (that is what "ready to paste" means here).

        ``type_text`` restores whatever the clipboard held before it borrowed
        it, so the result is copied *after* — it is what the user wants to have
        in hand. Runs on the GTK main thread, like every other delivery.
        """
        if typed:
            ClipboardService.type_text(text)
        get_clipboard().copy(text)
        HistoryManager.add_answer(text)
        ChatManager.add_message("assistant", text)
        print("📋 Talk mode: text typed and copied" if typed
              else "📋 Talk mode: text copied to the clipboard")

    # --- Typed chat (from the chat overlay input box) ------------------------

    @staticmethod
    def submit_text_chat(text: str) -> None:
        """
        Handle a message typed into the chat overlay (not voice).

        Shows the user's message right away, then runs the LLM call off-thread.
        Unlike the voice path, the answer is NOT typed at the cursor (focus is on
        the overlay, not another app) — it only lands in the chat + optional TTS.
        A new submission is ignored while one is still in flight, so concurrent
        workers can't deliver answers out of order. Safe to call from the GTK
        main thread (the WebKit message handler).
        """
        text = (text or "").strip()
        if not text:
            return
        if STATE.chat_busy:
            print("⏳ Chat busy — ignoring submission until the current answer arrives")
            return
        STATE.chat_busy = True
        ChatManager.add_message("user", text)  # echo immediately
        threading.Thread(
            target=ModeHandler._text_chat_worker, args=(text,), daemon=True
        ).start()

    @staticmethod
    def _text_chat_worker(text: str) -> None:
        """Worker: call the chat model, then deliver the answer to the overlay."""
        # chat() builds its messages from the current history, so call it BEFORE
        # appending this turn (avoids a duplicated turn). @safe_execute returns
        # None on failure, so this never raises.
        response = AIService.chat(text)
        if not response:
            STATE.chat_busy = False  # nothing to deliver — free the lock now
            return

        def _done():
            # User bubble was already echoed by submit_text_chat → chat_user_text=None.
            ModeHandler._deliver_response(
                response, history_user=text, chat_user_text=None, output=None
            )
            # Release only AFTER this turn is in conversation_history, so the next
            # submission's chat() call sees it (keeps turns correctly ordered).
            STATE.chat_busy = False
            return False

        GLib.idle_add(_done)
