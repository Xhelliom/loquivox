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
import queue
import threading
import time
from typing import Dict, Optional

import numpy as np
import sounddevice as sd

import loquivox.config as config_module
from loquivox.services.audio import resolve_input_device
from loquivox.services.talk import FINISH_MARKER, _strip_marker, user_said_done
from loquivox.state import STATE
from loquivox.transcription.streaming import float32_to_pcm16

#: Realtime audio is 24 kHz mono PCM16, both ways
RATE: int = 24000
#: playback blocks; small enough that a barge-in cuts within ~40 ms
BLOCK: int = 1024
#: longest a closing session waits for the last reply to finish playing, in case
#: the end-of-audio event never comes
SPEECH_TAIL_TIMEOUT: float = 15.0


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
        self._speech_deadline: Optional[float] = None
        self._loop = asyncio.new_event_loop()
        self._conn = None
        self._ready = threading.Event()
        self._stop = False
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

    # --- microphone → session ---------------------------------------------

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback: push captured audio to the model."""
        if self._conn is None or self._stop:
            return
        mono = indata[:, 0]
        # The recording overlay draws whatever lands here; without it a whole
        # realtime conversation shows a flat waveform.
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono.copy())
        except Exception:
            pass
        pcm = base64.b64encode(float32_to_pcm16(mono)).decode("ascii")
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

    spoken = RealtimeTalk(TalkSession())
    spoken._handle(_Event(type="conversation.item.input_audio_transcription.completed",
                          transcript="vas-y"))
    assert spoken.done, "a spoken finish phrase must end the briefing"
    print("✓ RealtimeTalk event routing OK")
