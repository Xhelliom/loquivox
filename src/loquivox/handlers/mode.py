"""
Unified handler for all recording modes.
"""
from __future__ import annotations

import math
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


#: how long the talk bubble waits before opening, so the recording overlay gets
#: the main thread — and its fade-in — to itself first
TALK_BUBBLE_DELAY_MS: int = 450


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
        ModeHandler.reset_capture()
        OverlayManager.hide()
        if was_active:
            print("✖️  Cancelled — nothing inserted")

    @staticmethod
    def reset_capture() -> None:
        """
        Close whatever capture is open and forget it: the stream, the live
        session, the buffer, the mode. Safe from any thread and on an already
        idle state — it is the teardown both ``cancel_active`` and the talk
        session end with, so neither can forget half of it.
        """
        if STATE.recording:
            try:
                AudioService.stop_recording()  # closes the stream, no processing
            except Exception:
                pass
        session = STATE.stream_session
        STATE.stream_session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        STATE.audio_buffer = []
        STATE.current_mode = None
        STATE.paused = False

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
        transcribed = ModeHandler.finalize_transcript(None, audio_data)
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
        transcribed = ModeHandler.finalize_transcript(session, audio_data)
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
        if ModeHandler.is_hallucination(transcribed_text):
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
    def is_hallucination(text: str) -> bool:
        """
        True for what Whisper emits on silence ("Thank you", "Merci",
        "Untertitel") rather than for speech — those would otherwise be typed,
        or answered in talk mode. Every transcript passes through here.
        """
        clean = text.strip().lower().rstrip(".!?")
        return clean in CFG.HALLUCINATIONS or len(clean) < 2

    @staticmethod
    def finalize_transcript(session, audio: Optional[np.ndarray]) -> Optional[str]:
        """
        Turn a finished capture into text: the live session when a streaming
        backend owns one (with the buffered audio as its fallback), else a batch
        transcription. Returns None on failure — this is the single place that
        knows which of the two paths applies.
        """
        from loquivox.transcription import get_dispatcher

        try:
            if session is not None:
                return get_dispatcher().finish_stream(session, audio, STATE.capture_rate)
            if audio is not None:
                return AudioService.transcribe(audio)
        except Exception:
            pass
        return None

    @staticmethod
    def _handle_dictation(text: str) -> None:
        """Handle dictation mode: transcribe and type."""
        HistoryManager.add_answer(f"[Dictation] {text}")
        # Recorded, not shown: dictation's answer is the text landing at the
        # cursor. Opening a window for it would put something in front of what
        # the user is typing into — see ChatManager.add_message.
        ChatManager.add_message("user", f"🎤 {text}", summon=False)
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
        from loquivox.services.turn_detector import prewarm_async
        prewarm_async()  # download / build the ONNX session off the hot path
        STATE.talk_active = True
        # Up front, before anything that can block. Opening the keyboards takes
        # ~450 ms and the conversation engine up to a second more; the user
        # pressed a key and needs to see that it registered, not to wonder.
        from loquivox.handlers.keyboard import KeyboardHandler
        OverlayManager.show("talk", hints=KeyboardHandler.TALK_HINTS)
        # Sequenced, not simultaneous. Building either window blocks the GTK
        # loop for ~100ms and both then fade in, so opening them together is
        # what makes F6 feel sluggish. The recording overlay is the one that
        # has to appear the instant the key is pressed — the bubble has nothing
        # to show until the first words come back anyway. Cosmetic only: the
        # microphone is on its own thread and misses nothing either way.
        GLib.timeout_add(TALK_BUBBLE_DELAY_MS, ModeHandler._open_talk_bubble)
        threading.Thread(target=ModeHandler._talk_worker, daemon=True).start()

    @staticmethod
    def _open_talk_bubble() -> bool:
        """Open the bubble, once the recording overlay has had the floor."""
        if STATE.talk_active:
            ChatManager.set_talk(True)
        return False

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
                if config_module.CFG.TALK_SCREENSHOT:
                    threading.Thread(target=ModeHandler._talk_screen_worker,
                                     args=(session,), daemon=True).start()
                while True:
                    if ModeHandler._talk_conversation(session, keys):  # cancelled
                        print("✖️  Talk mode cancelled — nothing written")
                        return
                    if not ModeHandler._talk_generate(session, keys):
                        return  # delivered, copied or dropped — the session is over
        finally:
            STATE.vad = None
            ModeHandler.reset_capture()  # a turn may have died mid-flight
            OverlayManager.hide()
            GLib.idle_add(ModeHandler._talk_finished)

    @staticmethod
    def _talk_finished() -> bool:
        """
        Close the session on the main thread, once the generated text is shown.

        ``_deliver_talk_text`` is queued on this same loop first, so clearing
        the flag here (rather than from the worker, which gets there first)
        means the final text lands while the overlay is still held open — and
        the auto-hide countdown starts when it is on screen, not three seconds
        before it appears.
        """
        STATE.talk_active = False
        ChatManager.set_talk(False)
        return False

    @staticmethod
    def _talk_screen_worker(session) -> None:
        """
        Hand the session what is on screen right now, in the background.

        Most of what someone is about to dictate is already in front of them —
        the error, the thread they are replying to, the page they are writing
        about — and they will not spell it out. So the screen is captured once,
        described once by the vision model, and attached as context.

        All of it happens on its own thread while the first turn is already
        being recorded: the capture costs no startup delay, and the description
        lands in time for the first reply (or, at worst, the second). Opt-in —
        it sends the screen to the cloud — and silent on failure.

        The recording overlay may be in the shot: waiting for it to be gone
        would mean delaying the microphone, which is exactly what this avoids.
        The prompt tells the model to ignore Loquivox's own windows instead.
        """
        cfg = config_module.CFG
        image = ImageService.take_screenshot(
            path=f"{cfg.TEMP_SCREEN_PATH}.talk.png",
            region=cfg.TALK_SCREENSHOT_REGION,
            cursor_px=cfg.TALK_SCREENSHOT_CURSOR_PX,
            max_px=cfg.TALK_SCREENSHOT_MAX_PX,
        )
        if not image:
            return
        description = (AIService.vision(cfg.TALK_SCREEN_PROMPT, image) or "").strip()
        if not description or config_module.TALK_SCREEN_EMPTY.lower() in description.lower():
            print("👁️  Screen context: nothing relevant on screen")
            return
        if not STATE.talk_active:
            return  # the conversation ended while we were looking
        session.set_context(description)
        print(f"👁️  Screen context: {description[:120]}"
              f"{'…' if len(description) > 120 else ''}")

    @staticmethod
    def _talk_conversation(session, keys) -> bool:
        """
        Hold the briefing through the configured engine, and say whether the
        user dropped it.

        Two paths, one promise: a conversation you can interrupt. The cascade
        builds it out of four swappable slots; the Realtime engine hands the
        whole thing to a model that hears and answers directly. Either way the
        turns land in the same ``TalkSession``, and the writing pass that
        follows is a text completion that never learns which ran.
        """
        if config_module.CFG.TALK_ENGINE == "realtime":
            return ModeHandler._talk_converse_realtime(session, keys)
        return ModeHandler._talk_converse(session, keys)

    @staticmethod
    def _talk_converse_realtime(session, keys) -> bool:
        """
        Hold the briefing through one OpenAI Realtime session.

        This loop owns nothing but the keyboard: the model decides when a turn
        ends and stops talking when it is talked over, so there is no VAD to
        poll and no reply to speak. The exclusive grab is safe for the same
        reason it is in ``_talk_listen`` — nothing here touches the network,
        the session does, on its own thread, so a hung request can never freeze
        the keyboard.

        Anything that stops it from opening (no key, no ``openai``, a model the
        account cannot reach) falls back to the cascade rather than failing:
        the user asked to talk, not to talk *this way*.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle
        from loquivox.services.realtime_talk import RealtimeTalk

        cfg = config_module.CFG
        STATE.current_mode = "talk"
        OverlayManager.show("talk", hints=KeyboardHandler.TALK_HINTS)
        OverlayManager.set_status("Connecting…")
        talk = RealtimeTalk(session)
        try:
            talk.start()
            OverlayManager.set_status("")
        except Exception as e:
            print(f"⚠️  Realtime talk unavailable ({e}) — using the cascade")
            return ModeHandler._talk_converse(session, keys)

        mapping = KeyboardHandler.talk_listen_keys()
        cancelled = False
        try:
            with keys.exclusive():
                while True:
                    pressed = keys.poll(mapping, 0.05)
                    if pressed == "cancel":
                        cancelled = True
                        break
                    if pressed == "finish":
                        break
                    # "send" (Space) means "end this turn now", which is the
                    # server's call here — ignoring it beats ending the
                    # briefing on a key that means something else.
                    if talk.error is not None:
                        break
                    # The transcript that ends the briefing arrives while its
                    # audio is still queued, so this keeps looping until the
                    # sentence has actually been heard — closing on the event
                    # itself cuts the assistant off mid-word.
                    if talk.done and talk.finished_speaking():
                        break
                    if session.user_turns >= cfg.TALK_MAX_TURNS:
                        break
        finally:
            talk.close()
        if talk.error is not None:
            print(f"⚠️  Realtime talk ended: {talk.error}")
        return cancelled

    @staticmethod
    def _talk_converse(session, keys) -> bool:
        """
        Run spoken turns until the briefing is over. Returns True if the user
        dropped the conversation, False once it is ready to be written.

        Four things can end the briefing: Enter (handled in ``_talk_listen``),
        the user saying so in a short utterance (caught here, before the model
        is called), the model answering with its end-of-briefing marker, and
        ``TALK_MAX_TURNS``. The middle two are independently toggleable — see
        ``services/talk.py``. Everything said is kept as part of the brief
        either way.
        """
        from loquivox.services.talk import user_said_done

        cfg = config_module.CFG
        prefix = None  # audio captured over a reply — the next turn's first words
        while session.user_turns < cfg.TALK_MAX_TURNS:
            idle_action = "finish" if session.user_turns else "cancel"
            audio, action = ModeHandler._talk_listen(keys, idle_action, prefix=prefix)
            prefix = None
            if action == "cancel":
                return True

            text = ModeHandler._talk_transcribe(audio)
            if action == "finish":
                # Whatever was being said when Enter came still counts as brief.
                if text:
                    ChatManager.add_message("user", f"🗣️ {text}")
                    session.add_user(text)
                return False
            if not text:
                continue  # nothing said (or a hallucination) — just listen again

            ChatManager.add_message("user", f"🗣️ {text}")
            if user_said_done(text):
                # "Vas-y, écris-le" — no point paying for a reply that would
                # only say "ok"; the turn still counts as part of the brief.
                session.add_user(text)
                return False

            OverlayManager.set_status("Thinking…")
            reply = session.reply(
                text, on_delta=lambda t: ChatManager.stream("assistant", t))
            if reply is None:
                continue
            if reply.text:
                ChatManager.add_message("assistant", reply.text)
                OverlayManager.set_status("Speaking…")
                prefix = ModeHandler._talk_speak(reply.text)
                if prefix is not None:
                    session.mark_interrupted()
            if reply.done:
                # The model took the floor to the writing phase — either because
                # the user said so in words the phrase list doesn't cover, or
                # (finish_by_model) because it judged the brief complete.
                return False
        print(f"🗣️  Talk mode: {cfg.TALK_MAX_TURNS} turns reached — writing the text")
        return False

    @staticmethod
    def _talk_listen(keys, idle_action: str,
                     prefix: Optional[np.ndarray] = None) -> Tuple[Optional[np.ndarray], str]:
        """
        Record one spoken turn and return ``(audio, action)``.

        The turn ends on its own — that is talk mode's whole point, there is no
        key to hold. Three layers can call it, in priority order: the backend's
        own semantic VAD when it has one, else the local semantic detector
        arbitrating the pauses the energy VAD hears, else that pause alone (see
        ``_talk_turn_complete``). On top of that: Space / the talk key end the
        turn now, the turn timeout ends it eventually, and Enter/Esc end the
        conversation. A turn where nothing at all was said for
        ``TALK_IDLE_TIMEOUT`` yields ``idle_action``: the user has walked away
        from the microphone, not paused mid-sentence.

        ``prefix`` is the audio of an interruption — this turn began while the
        assistant was still speaking, and starts with the words that cut it off.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle
        from loquivox.services.turn_detector import WINDOW_SEC, ready_detector, turn_complete
        from loquivox.services.vad import VoiceActivityDetector

        cfg = config_module.CFG
        STATE.vad = None
        STATE.current_mode = "talk"
        OverlayManager.show("talk", hints=KeyboardHandler.TALK_HINTS)

        # ready_detector() never blocks: while the model is still downloading
        # in the background, this turn simply ends on silence like before.
        # Asked before recording starts because the detector has to be built
        # before the microphone opens; when the backend ends turns server-side
        # the local window is never read at all, so not knowing that yet is
        # harmless.
        semantic = ready_detector() is not None

        def build_vad(rate: int) -> VoiceActivityDetector:
            vad = VoiceActivityDetector(
                rate,
                threshold=cfg.TALK_VAD_THRESHOLD,
                # With the model deciding, the pause is only a trigger — a much
                # shorter one, since a finished sentence no longer waits out the
                # full silence window to be recognised as finished.
                silence_ms=(cfg.TALK_SEMANTIC_TRIGGER_MS if semantic
                            else cfg.TALK_VAD_SILENCE_MS),
                min_speech_ms=cfg.TALK_VAD_MIN_SPEECH_MS,
            )
            if prefix is not None:
                # This turn is already under way: its first words are in the
                # buffer, not in what the microphone is about to hear. Without
                # priming, the detector would see a turn where nobody ever
                # spoke and sit out the whole TALK_IDLE_TIMEOUT.
                vad.prime(len(prefix) / rate)
            return vad

        OverlayManager.set_status("Connecting…")
        AudioService.start_recording(semantic_turns=cfg.TALK_SEMANTIC_TURNS,
                                     prefix=prefix,
                                     detector=build_vad if cfg.TALK_VAD else None)
        OverlayManager.set_status("")
        # A streaming backend may detect turns server-side (OpenAI Realtime's
        # semantic_vad). When it does, it is the authority and neither the local
        # model nor the silence window gets a vote.
        stream = STATE.stream_session
        remote_turns = stream is not None and stream.semantic_turns

        mapping = KeyboardHandler.talk_listen_keys()
        action = "send"
        deadline = time.monotonic() + cfg.TALK_TURN_TIMEOUT
        # Exclusive only while we wait on the user: no keystroke of this turn
        # reaches the app underneath, and nothing here can block on the network.
        with keys.exclusive():
            while True:
                # A short poll: this is what stands between the detector saying
                # "finished" and the recording actually stopping.
                pressed = keys.poll(mapping, 0.03)
                if pressed is not None:
                    action = pressed
                    break
                if remote_turns and stream.turn_ended:
                    break
                vad = STATE.vad
                if vad is not None:
                    if vad.ended and not remote_turns:
                        if turn_complete(vad, lambda: AudioService.snapshot_tail(WINDOW_SEC),
                                         STATE.capture_rate):
                            break
                    if not vad.speech_started and vad.elapsed >= cfg.TALK_IDLE_TIMEOUT:
                        action = idle_action
                        break
                if time.monotonic() >= deadline:
                    break

        STATE.vad = None
        audio = AudioService.stop_recording()
        if action != "cancel":
            OverlayManager.set_transcribing()
        # A cancelled turn leaves its live session open on purpose: the worker's
        # `finally` runs reset_capture(), which is the one place that closes it.
        return audio, action

    @staticmethod
    def _talk_speak(text: str) -> Optional[np.ndarray]:
        """
        Speak a reply while listening for the user talking over it.

        Returns what the microphone heard when they cut in — the start of their
        next turn, which must survive the interruption — or None if the reply
        was spoken to the end.

        The detector is armed on the first sample that actually leaves for the
        speakers, never before: its calibration window then measures the
        reply's own echo, so the activation level lands above whatever the
        speakers leak back into the microphone. On a headset there is nothing
        to leak and it stays at the plain threshold, which is why this needs no
        setting of its own for "do you wear headphones".

        Nothing is transcribed here — the live session stays shut. The only
        question being asked of the audio is "did they start speaking".
        """
        from loquivox.services.vad import VoiceActivityDetector

        cfg = config_module.CFG
        if not cfg.TALK_BARGE_IN:
            # Blocking on purpose: the next turn starts recording the moment
            # this returns, and must not capture the assistant's own voice.
            TTSService.speak(text, wait=True, force=cfg.TALK_SPEAK_REPLIES)
            return None

        stop, started = threading.Event(), threading.Event()
        AudioService.start_recording(stream=False)
        speaker = threading.Thread(
            target=TTSService.speak, args=(text,),
            kwargs={"wait": True, "force": cfg.TALK_SPEAK_REPLIES,
                    "stop": stop, "started": started},
            daemon=True,
        )
        speaker.start()
        while speaker.is_alive() and not started.wait(0.05):
            pass
        if started.is_set():
            STATE.vad = VoiceActivityDetector(
                STATE.capture_rate,
                threshold=cfg.TALK_VAD_THRESHOLD,
                # Never ends a turn on its own: `ended` would make the detector
                # inert, and the only thing read here is how much speech it has
                # heard. The pause that ends a turn is _talk_listen's business.
                silence_ms=10 ** 6,
                min_speech_ms=cfg.TALK_BARGE_IN_MS,
                # The one detector that calibrates on speech on purpose: it is
                # listening through the assistant's own echo and has to sit
                # above it, so the usual ceiling would defeat it.
                max_level=math.inf,
            )
        needed = cfg.TALK_BARGE_IN_MS / 1000.0
        while speaker.is_alive():
            vad = STATE.vad
            if vad is not None and vad.speech_seconds >= needed:
                stop.set()
                break
            time.sleep(0.03)
        speaker.join(timeout=2.0)
        # Read the tail before stop_recording(): it is the buffer being read.
        tail = (AudioService.snapshot_tail(cfg.TALK_BARGE_IN_KEEP)
                if stop.is_set() else None)
        STATE.vad = None
        AudioService.stop_recording()
        if tail is not None:
            print("✋ Cut off — listening")
        return tail

    @staticmethod
    def _talk_transcribe(audio: Optional[np.ndarray]) -> Optional[str]:
        """
        Turn a recorded talk turn into text, or None if it held no usable speech.

        Shares both halves with the one-shot modes: ``finalize_transcript`` for
        the live-session-or-batch choice, and the hallucination guard — a spoken
        conversation would otherwise happily reply to Whisper's "Merci" on
        silence.
        """
        stream = STATE.stream_session
        STATE.stream_session = None
        text = (ModeHandler.finalize_transcript(stream, audio) or "").strip()
        if not text:
            return None
        if ModeHandler.is_hallucination(text):
            print(f"⚠️ Talk mode ignored: '{text}'")
            return None
        return text

    @staticmethod
    def _talk_generate(session, keys, max_redos: int = 5) -> bool:
        """
        Write the final text from the conversation and put it up for review.

        Returns True to go back to the conversation (V), False once the text has
        been delivered, dropped, or left on the clipboard. Nothing is ever typed
        without an explicit accept — on timeout the text lands on the clipboard
        instead, so the conversation is never wasted.

        ``TALK_AUTO_PASTE`` is the one way out of that: the text is pasted the
        moment it exists, wherever the cursor was left when the conversation
        ended. No review, so no rewrite and no going back — which is the trade
        for not having to reach for the keyboard at all.
        """
        from loquivox.handlers.keyboard import KeyboardHandler  # lazy: avoid import cycle

        if not session.user_turns:
            print("✖️  Nothing was said — talk mode closed")
            return False

        from loquivox.ui.recording_overlay import review_hint_line  # lazy: cycle

        cfg = config_module.CFG
        mapping = KeyboardHandler.talk_review_keys()
        redos = 0
        while True:
            # The bubble reviews the text, so the recording overlay goes away:
            # its hint strip still names the conversation's keys, which are not
            # the ones that apply now. _talk_listen brings it back on V.
            OverlayManager.hide()
            ChatManager.refresh_overlay("✍️ Rédaction du texte…")
            text = session.generate()
            if not text:
                print("⚠️  Talk mode: no text was produced")
                return False

            if cfg.TALK_AUTO_PASTE:
                ChatManager.set_result(text, "📋 Collé au curseur")
                ModeHandler._deliver_talk_text(text, typed=True)
                return False

            ChatManager.set_result(text, review_hint_line("talk"))
            with keys.exclusive():
                action = keys.poll(mapping, cfg.TALK_REVIEW_TIMEOUT) or "timeout"
            if action == "redo":
                redos += 1
                if redos <= max_redos:
                    continue
                print("⚠️  Talk mode: too many rewrites — keeping the last one")
                action = "copy"
            if action == "talk":
                return True
            if action == "reject":
                print("✖️  Generated text discarded")
                return False
            ModeHandler._deliver_talk_text(text, typed=(action == "accept"))
            return False

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
        # Not added to the overlay: it is already there, as the result block
        # the user just accepted.
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
