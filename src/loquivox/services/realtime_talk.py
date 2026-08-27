"""
Talk mode held by one OpenAI Realtime session — speech in, speech out.

The cascade (STT → LLM → TTS) is four network hops and a turn detector; this is
one connection to a model that hears and answers directly, so the reply keeps
the prosody of speech and the server owns turn-taking and interruptions. What
is lost is the choice of LLM: the conversation is held by whatever Realtime
model is configured, not by ``STATE.ai_chat_model``.

That only covers the *conversation*. Loquivox exists to produce a text, and the
writing pass is a plain text completion either way — so the transcripts of both
sides are collected into the same ``TalkSession`` the cascade fills, and
``generate()`` afterwards does not know or care which path was taken.

Threading, as everywhere else here: the SDK is async, so the session runs an
asyncio loop in its own thread. Captured audio crosses over with
``run_coroutine_threadsafe``; playback is a second thread draining a queue, so
a barge-in only has to empty that queue.
"""
from __future__ import annotations

import asyncio
import base64
import math
import queue
import threading
import time
from typing import Dict, List, Optional

import numpy as np
import sounddevice as sd

import loquivox.config as config_module
from loquivox.services.audio import resolve_input_device
from loquivox.services.talk import FINISH_MARKER, _strip_marker, user_said_done
from loquivox.services.vad import EchoGate
from loquivox.state import STATE
from loquivox.transcription.streaming import float32_to_pcm16

#: Realtime audio is 24 kHz mono PCM16, both ways
RATE: int = 24000
#: playback blocks; small enough that a barge-in cuts within ~40 ms
BLOCK: int = 1024
#: longest a closing session waits for the last reply to finish playing, in case
#: the end-of-audio event never comes
SPEECH_TAIL_TIMEOUT: float = 15.0
#: how long the microphone stays gated after the last block was written — the
#: sound card is still draining it, and that tail is echo like the rest
ECHO_HANGOVER: float = 0.25


class RealtimeTalk:
    """One spoken conversation, held end to end by a Realtime model."""

    def __init__(self, session, *, model: str = "", voice: str = "",
                 instructions: str = "") -> None:
        cfg = config_module.CFG
        self._session = session
        self._model = model or cfg.TALK_REALTIME_MODEL
        self._voice = voice or cfg.TALK_REALTIME_VOICE
        self._instructions = instructions or session.system_prompt()
        #: turn being transcribed right now, per role — shown live in the bubble
        self._live: Dict[str, str] = {}
        self._audio: "queue.Queue" = queue.Queue()
        #: set while nothing more is coming for the reply being played. Starts
        #: set: a session that has said nothing is not mid-sentence.
        self._audio_done = threading.Event()
        self._audio_done.set()
        self._writing = False          # a chunk is on its way to the sound card
        #: echo gate, live while a reply is playing (see ``_mic_chunks``)
        self._gate: Optional[EchoGate] = None
        self._held: List[np.ndarray] = []
        self._echo_until: float = 0.0
        self._speech_deadline: Optional[float] = None
        self._loop = asyncio.new_event_loop()
        self._conn = None
        self._ready = threading.Event()
        self._stop = False
        #: the screen description already handed to the model, if any. Kept as
        #: the text rather than a flag: S starts a fresh capture mid-session,
        #: and a flag would silently swallow the new one.
        self._context_pushed = ""
        #: set once the briefing is over — a finish phrase or the model's marker
        self.done = False
        #: the exception that ended the session, if any
        self.error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._player: Optional[threading.Thread] = None
        self._mic: Optional[sd.InputStream] = None

    # --- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 15.0) -> None:
        """
        Open the session, the microphone and the speakers. Raises on failure,
        which the caller reads as "fall back to the cascade".
        """
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        if self.error is not None:
            raise self.error
        if self._conn is None:
            raise RuntimeError("Realtime session did not open in time")
        self._player = threading.Thread(target=self._play, daemon=True)
        self._player.start()
        self._mic = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                   device=resolve_input_device(),
                                   callback=self._on_audio)
        self._mic.start()
        print(f"🔊 Realtime talk — {self._model} / {self._voice}")

    def close(self) -> None:
        """Stop everything. Safe to call twice."""
        self._stop = True
        self._echo_report()
        if self._mic is not None:
            self._mic.stop()
            self._mic.close()
            self._mic = None
        self._audio.put(None)  # wake the player so it can exit
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def finished_speaking(self) -> bool:
        """
        True once the reply that ended the briefing has actually been heard.

        A reply's transcript is complete while its audio is still queued, so
        the event that sets ``done`` lands mid-sentence — closing on it cuts
        the assistant off in the middle of "Parfait, je vais rédiger…". Asked
        as a predicate rather than waited on, so Esc keeps working while the
        sentence finishes, and bounded, so a missing end-of-audio event cannot
        hold a session open.
        """
        if self._audio_done.is_set() and self._audio.empty() and not self._writing:
            return True
        now = time.monotonic()
        if self._speech_deadline is None:
            self._speech_deadline = now + SPEECH_TAIL_TIMEOUT
        return now >= self._speech_deadline

    def push_context(self) -> bool:
        """
        Hand the model the screen description, whenever a capture comes back.

        The session opens with ``instructions`` that cannot contain it: the
        screenshot is taken and described beside the first turn precisely so
        the microphone is not kept waiting (see ``_talk_screen_worker``). A
        ``session.update`` is a partial update — only the fields sent are
        touched — so the voice, the format and the turn detection all survive
        it, and the clause applies from the next reply on.

        Written as a predicate polled by the conversation loop rather than a
        callback: the loop already ticks every 50 ms with nothing to do, and
        this costs a string compare until there is something new to say. True
        means it was just sent — which happens again for every fresh capture,
        since S exists to replace a description that has gone stale.
        """
        if self._conn is None or self._stop:
            return False
        clause = self._session.context_clause()
        if not clause or clause == self._context_pushed:
            return False
        self._context_pushed = clause
        try:
            asyncio.run_coroutine_threadsafe(
                self._conn.session.update(session={
                    "type": "realtime",
                    "instructions": self._instructions + "\n\n" + clause,
                }),
                self._loop)
        except Exception as e:
            print(f"⚠️  Screen context not sent to the Realtime session: {e}")
            return False
        return True

    # --- microphone → session ---------------------------------------------

    def _echo_report(self) -> None:
        """
        Print what the last reply measured, and start the next one clean.

        Called when the gate reopens — and from ``close()``, because a session
        someone gave up on and killed mid-reply is exactly the one whose
        numbers are worth seeing.
        """
        if self._gate is None:
            return
        print(self._gate.report)
        self._gate = None
        self._held.clear()

    def _mic_chunks(self, mono: np.ndarray) -> List[np.ndarray]:
        """
        What of this captured chunk may reach the model.

        The server hears the microphone continuously and cannot know that what
        it hears is the assistant's own voice coming back off the speakers —
        it reads it as the user interrupting and stops the reply dead, every
        time. So the microphone is gated here while a reply plays, by the
        ``EchoGate`` that also arbitrates the cascade's barge-in, and opens
        only after ``TALK_BARGE_IN_MS`` of speech above the echo. What was held
        back leaves with it, so the words that cut the assistant off are not
        lost — ``TALK_BARGE_IN_KEEP`` of them, the window the cascade replays
        into its next turn. ``TALK_BARGE_IN = false`` never opens it.
        """
        cfg = config_module.CFG
        now = time.monotonic()
        if not (self._audio_done.is_set() and self._audio.empty()
                and not self._writing):
            self._echo_until = now + ECHO_HANGOVER
        if now >= self._echo_until:
            self._echo_report()
            return [mono]
        if not cfg.TALK_BARGE_IN:
            return []
        if self._gate is None:
            self._gate = EchoGate(RATE, threshold=cfg.TALK_VAD_THRESHOLD,
                                  margin=cfg.TALK_BARGE_IN_MARGIN)
        self._gate.feed(mono)
        if self._gate.speech_seconds < cfg.TALK_BARGE_IN_MS / 1000.0:
            self._held.append(mono)
            kept = sum(len(c) for c in self._held) / RATE
            while len(self._held) > 1 and kept > cfg.TALK_BARGE_IN_KEEP:
                kept -= len(self._held.pop(0)) / RATE
            return []
        chunks = self._held + [mono]
        if self._held:
            print(f"✋ Cut off — {self._gate.loudest:.3f} over an echo peaking "
                  f"at {self._gate.echo_peak:.3f}")
            self._held = []
        return chunks

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback: push captured audio to the model."""
        if self._conn is None or self._stop:
            return
        mono = indata[:, 0].copy()  # PortAudio reuses the buffer; the gate keeps it
        # The recording overlay draws whatever lands here; without it a whole
        # realtime conversation shows a flat waveform.
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono)
        except Exception:
            pass
        for chunk in self._mic_chunks(mono):
            pcm = base64.b64encode(float32_to_pcm16(chunk)).decode("ascii")
            try:
                asyncio.run_coroutine_threadsafe(
                    self._conn.input_audio_buffer.append(audio=pcm),
                    self._loop)
            except Exception:
                pass  # the loop is closing — the session is on its way out

    # --- session → speakers ------------------------------------------------

    def _play(self) -> None:
        """Drain the audio queue into the sound card until the session ends."""
        try:
            with sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                    blocksize=BLOCK) as out:
                while not self._stop:
                    chunk = self._audio.get()
                    if chunk is None:
                        break
                    self._writing = True
                    try:
                        out.write(chunk)
                    finally:
                        self._writing = False
        except Exception as e:
            print(f"❌ Realtime playback error: {e}")

    def _flush_audio(self) -> None:
        """
        Drop everything not yet played — the user has started speaking.

        The server stops generating on its own; this is the half it cannot do,
        since what is already on this side would otherwise keep talking over
        them for as long as the queue is deep.
        """
        try:
            while True:
                self._audio.get_nowait()
        except queue.Empty:
            pass
        self._audio_done.set()  # nothing left to hear — this reply is over

    # --- the session itself ------------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_task())
        except Exception as e:
            if not self._stop:
                self.error = e
        finally:
            self._ready.set()
            self._loop.close()

    async def _session_task(self) -> None:
        from openai import AsyncOpenAI

        cfg = config_module.CFG
        client = AsyncOpenAI()
        try:
            async with client.realtime.connect(model=self._model) as conn:
                await conn.session.update(session={
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self._instructions,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": RATE},
                            "transcription": {"model": cfg.TALK_REALTIME_TRANSCRIBE},
                            # The server decides when a turn ends AND stops
                            # generating when the user speaks: turn-taking and
                            # barge-in both come from here.
                            "turn_detection": {"type": "semantic_vad"},
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": RATE},
                            "voice": self._voice,
                        },
                    },
                })
                self._conn = conn
                self._ready.set()
                async for event in conn:
                    self._handle(event)
                    if self._stop or self.done:
                        break
        except Exception as e:
            if not self._stop:
                self.error = e
            self._ready.set()

    def _handle(self, event) -> None:
        """Route one server event. Never raises — a session must not die on one."""
        etype = getattr(event, "type", "")
        if etype == "response.output_audio.delta":
            delta = getattr(event, "delta", "") or ""
            if delta:
                self._audio_done.clear()
                self._audio.put(base64.b64decode(delta))
        elif etype in ("response.output_audio.done", "response.done"):
            self._audio_done.set()
        elif etype == "input_audio_buffer.speech_started":
            self._flush_audio()
        elif etype.endswith("input_audio_transcription.delta"):
            self._on_delta("user", getattr(event, "delta", "") or "")
        elif etype.endswith("input_audio_transcription.completed"):
            self._live.pop("user", None)
            self._on_user_said(getattr(event, "transcript", "") or "")
        elif etype == "response.output_audio_transcript.delta":
            self._on_delta("assistant", getattr(event, "delta", "") or "")
        elif etype == "response.output_audio_transcript.done":
            self._live.pop("assistant", None)
            self._on_assistant_said(getattr(event, "transcript", "") or "")
        elif etype == "error":
            self.error = RuntimeError(getattr(event, "error", etype))

    def _on_delta(self, role: str, delta: str) -> None:
        """
        Show a turn in the bubble as the server transcribes it.

        The Realtime session sends the words of both sides while they are still
        being said; without this the bubble would only ever show finished
        turns, which is the one thing the cascade would do better.
        """
        if not delta:
            return
        from loquivox.managers.chat import ChatManager

        text = self._live[role] = self._live.get(role, "") + delta
        ChatManager.stream(role, _strip_marker(text) if role == "assistant" else text)

    def _on_user_said(self, text: str) -> None:
        """Record a spoken user turn, and notice if it ends the briefing."""
        text = text.strip()
        if not text:
            return
        from loquivox.managers.chat import ChatManager

        ChatManager.add_message("user", f"🗣️ {text}")
        self._session.add_user(text)
        if user_said_done(text):
            self.done = True

    def _on_assistant_said(self, text: str) -> None:
        """Record a spoken reply, stripping the end-of-briefing marker."""
        from loquivox.managers.chat import ChatManager

        reply = self._session.add_assistant(text)
        if reply.done:
            self.done = True
        if reply.text:
            ChatManager.add_message("assistant", reply.text)


if __name__ == "__main__":
    # Self-check for the event routing (no network, no audio device):
    #     python -m loquivox.services.realtime_talk
    class _Event:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

    # The real session: recording an assistant turn (and reading the marker) is
    # its job now, and a fake would only re-implement what is being checked.
    from loquivox.services.talk import TalkSession

    import loquivox.managers.chat as chat_module
    chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)
    live: list = []
    chat_module.ChatManager.stream = staticmethod(
        lambda role, text: live.append((role, text)))

    talk = RealtimeTalk(TalkSession())
    talk._handle(_Event(type="response.output_audio.delta",
                        delta=base64.b64encode(b"\x01\x02").decode()))
    assert talk._audio.qsize() == 1
    # Speech from the user drops what has not been played: the assistant must
    # not keep talking over them out of a queue the server can't see.
    talk._handle(_Event(type="input_audio_buffer.speech_started"))
    assert talk._audio.qsize() == 0, "would have talked over the user"

    # Deltas reach the bubble live; the completed turn replaces them.
    talk._handle(_Event(type="conversation.item.input_audio_transcription.delta",
                        delta="je voudrais"))
    assert talk._live["user"] == "je voudrais", talk._live
    talk._handle(_Event(type="response.output_audio_transcript.delta",
                        delta=f"D'accord. {FINISH_MARKER}"))
    assert "[[" not in live[-1][1], live  # a marker never reaches the screen
    talk._handle(_Event(type="conversation.item.input_audio_transcription.completed",
                        transcript="je voudrais un mail pour l'équipe"))
    assert "user" not in talk._live, talk._live
    assert talk._session.turns[-1]["content"].startswith("je voudrais")
    assert not talk.done
    # The briefing ends on the transcript, but the voice is still playing:
    # the session must not close until the sentence has been heard.
    talk._handle(_Event(type="response.output_audio.delta",
                        delta=base64.b64encode(b"\x00\x01" * 32).decode()))
    talk._handle(_Event(type="response.output_audio_transcript.done",
                        transcript=f"D'accord, je rédige. {FINISH_MARKER}"))
    assert talk.done and not talk.finished_speaking(), \
        "closing here cuts the assistant off mid-sentence"
    talk._handle(_Event(type="response.done"))
    talk._audio.get_nowait()                       # the player drains it
    assert talk.finished_speaking(), "the session never closes"
    assert talk.done, "the model's marker must end the briefing"
    assert FINISH_MARKER not in talk._session.turns[-1]["content"]

    # The echo gate: on speakers the microphone hears the reply, and anything
    # forwarded from it makes the server's VAD interrupt the assistant.
    import dataclasses

    def _noise(rng, amp, samples=2400):        # 2400 = 100 ms
        return (rng.standard_normal(samples) * amp).astype(np.float32)

    rng = np.random.default_rng(2)
    gate = RealtimeTalk(TalkSession())
    echo, voice = _noise(rng, 0.03), _noise(rng, 0.2)
    assert len(gate._mic_chunks(echo)) == 1, "nothing playing — the mic goes through"
    gate._handle(_Event(type="response.output_audio.delta",
                        delta=base64.b64encode(b"\x00" * 64).decode()))
    for _ in range(30):                 # 3 s of the reply leaking back in
        assert not gate._mic_chunks(echo), "the assistant would cut itself off"
    held = sum(len(c) for c in gate._held) / RATE
    assert held <= config_module.CFG.TALK_BARGE_IN_KEEP + 0.1, f"unbounded buffer ({held:.1f}s)"
    opened = []
    for _ in range(8):                  # the user talks over it, louder
        opened += gate._mic_chunks(voice)
    assert len(opened) > 4, "a real interruption never reached the model"
    assert not gate._held, "the words that cut in were dropped"
    # Barge-in off: the reply is heard to the end, whatever the room does.
    saved = config_module.CFG
    config_module.CFG = dataclasses.replace(saved, TALK_BARGE_IN=False)
    try:
        assert not [c for _ in range(10) for c in gate._mic_chunks(voice)], \
            "half duplex still forwarded the room"
    finally:
        config_module.CFG = saved
    # Reply over: the gate reopens, and the next one starts from scratch.
    gate._audio.get_nowait()
    gate._audio_done.set()
    gate._echo_until = 0.0
    assert len(gate._mic_chunks(echo)) == 1, "the microphone stayed shut"
    assert gate._gate is None, "the next reply inherits this one's echo"

    # A loud reply with pauses between its sentences — the case that made a
    # falling activation level cut the assistant off: the room measured during
    # a pause is quieter than the syllable that follows it.
    loud = RealtimeTalk(TalkSession())
    loud._handle(_Event(type="response.output_audio.delta",
                        delta=base64.b64encode(b"\x00" * 64).decode()))
    silence = np.zeros(2400, dtype=np.float32)
    for _ in range(6):
        for chunk in [_noise(rng, 0.12)] * 8 + [silence] * 6:
            assert not loud._mic_chunks(chunk), \
                f"the reply interrupted itself (bar {loud._gate.bar:.3f})"

    # A headset: no echo to measure, and a voice that comes up over several
    # blocks the way a real one does. A bar that keeps tracking climbs ahead of
    # it and barge-in dies — which is exactly what shipped once.
    head = RealtimeTalk(TalkSession())
    head._handle(_Event(type="response.output_audio.delta",
                        delta=base64.b64encode(b"\x00" * 64).decode()))
    for _ in range(50):                 # 1 s of reply, nothing leaking back
        assert not head._mic_chunks(_noise(rng, 0.002, 480)), "a headset leaks?"
    ramp = [_noise(rng, 0.004 * 1.15 ** i, 480) for i in range(40)]  # 20 ms blocks
    assert [c for block in ramp for c in head._mic_chunks(block)], \
        f"the bar followed the voice up — barge-in is dead (bar {head._gate.bar:.3f})"

    spoken = RealtimeTalk(TalkSession())
    spoken._handle(_Event(type="conversation.item.input_audio_transcription.completed",
                          transcript="vas-y"))
    assert spoken.done, "a spoken finish phrase must end the briefing"

    # The screen context reaches the session once, and only once it exists.
    sent: list = []

    class _Conn:
        class session:
            @staticmethod
            async def update(session):
                sent.append(session)

    ctx_session = TalkSession()
    ctx = RealtimeTalk(ctx_session)
    ctx._conn = _Conn()
    ctx._loop = asyncio.new_event_loop()
    threading.Thread(target=ctx._loop.run_forever, daemon=True).start()
    try:
        assert not ctx.push_context(), "nothing to send before the capture returns"
        ctx_session.set_context("un terminal montrant une stack trace")
        assert ctx.push_context(), "the description never reached the model"
        assert not ctx.push_context(), "sent twice — the loop polls 20×/s"
        # S captures again mid-session: the new description must get through.
        ctx_session.set_context("le meme terminal, la stack trace corrigee")
        assert ctx.push_context(), "a fresh capture never reached the model"
        assert not ctx.push_context(), "the fresh one resent on every tick"
        time.sleep(0.2)
        assert len(sent) == 2, sent
        assert "stack trace" in sent[0]["instructions"], sent[0]
        assert "corrigee" in sent[1]["instructions"], sent[1]
        # A partial update: touching the voice or the turn detection here would
        # reconfigure the session mid-conversation.
        assert set(sent[0]) == {"type", "instructions"}, sent[0]
        assert ctx_session.system_prompt()[:40] in sent[0]["instructions"], \
            "the base prompt was replaced instead of extended"
    finally:
        ctx._loop.call_soon_threadsafe(ctx._loop.stop)
    print("✓ RealtimeTalk event routing OK")
