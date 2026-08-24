"""
OpenAI Realtime streaming backend → real-time transcription.

The OpenAI Realtime SDK is async (``AsyncOpenAI().realtime.connect``), so the
session runs an asyncio event loop in a dedicated thread. Captured audio is
handed across via ``asyncio.run_coroutine_threadsafe``; transcription deltas
arrive as ``conversation.item.input_audio_transcription.delta`` events and the
final text as ``...transcription.completed``.

OpenAI Realtime audio is 24 kHz mono PCM16 → ``stream_sample_rate = 24000`` so
capture matches the wire format with no resampling.

Two modes of turn detection:
  - push-to-talk (default, every mode but talk): ``turn_detection: null`` — the
    buffer is committed by hand when the key is released, one segment, one
    transcript.
  - ``semantic_turns=True`` (talk mode): ``turn_detection: semantic_vad`` — the
    server decides when the speaker has finished from what they actually said,
    chunks the audio itself and emits one transcript per chunk. ``turn_ended``
    then carries that decision to the talk loop, and the chunks are joined.

Optional dependency (``pip install -e '.[openai]'``) and ``OPENAI_API_KEY`` in
the environment. Missing either → ``is_available()`` False → offline fallback.
Batch ``transcribe`` raises so a stream failure lands on the offline fallback.
"""
from __future__ import annotations

import asyncio
import base64
import os
import threading
import time
from typing import List, Optional

import numpy as np

from .base import BackendUnavailable, TranscriptionBackend
from .streaming import PartialCallback, StreamingSession, float32_to_pcm16


class OpenAIRealtimeSession(StreamingSession):
    """A single OpenAI Realtime transcription session (async loop in a thread)."""

    def __init__(self, model: str, language: str, on_partial: Optional[PartialCallback],
                 *, semantic_turns: bool = False, eagerness: str = "auto") -> None:
        self._model = model
        self._language = language
        self._on_partial = on_partial
        self._semantic = bool(semantic_turns) and self._supports_turn_detection(model)
        self._eagerness = (eagerness or "auto").strip().lower()
        self._segments: List[str] = []
        self._turn_ended = False
        self._interim = ""
        self._final = ""
        self._error: Optional[Exception] = None
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._conn = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait until the session is configured (or failed) before feeding audio.
        self._ready.wait(timeout=10.0)
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime connect failed: {self._error}")

    #: transcription models the API rejects any turn_detection config on —
    #: it must be omitted or null (gpt-realtime-whisper and friends).
    _NO_TURN_DETECTION = ("whisper",)

    @classmethod
    def _supports_turn_detection(cls, model: str) -> bool:
        """False for models that require ``turn_detection: null``."""
        if any(tag in (model or "").lower() for tag in cls._NO_TURN_DETECTION):
            print(f"ℹ️  {model} takes no server-side turn detection — "
                  "talk mode will use the local detector")
            return False
        return True

    @property
    def semantic_turns(self) -> bool:
        """True when the server is the one closing turns (semantic VAD)."""
        return self._semantic

    @property
    def turn_ended(self) -> bool:
        """True once the server's semantic VAD has closed the current turn."""
        return self._turn_ended

    def _turn_detection(self) -> Optional[dict]:
        """The session's ``turn_detection`` config: None = push-to-talk."""
        if not self._semantic:
            return None
        detection = {"type": "semantic_vad"}
        if self._eagerness and self._eagerness != "auto":
            detection["eagerness"] = self._eagerness
        return detection

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        except Exception as e:
            self._error = e
            self._ready.set()
        finally:
            self._finished.set()
            self._loop.close()

    async def _session(self) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        transcription: dict = {"model": self._model}
        if self._language:
            transcription["language"] = self._language
        try:
            # A transcription-only session connects with intent=transcription
            # (NOT a realtime model — passing gpt-4o-transcribe to connect is
            # rejected as invalid_model). The transcription model goes in the
            # session config below.
            async with client.realtime.connect(
                extra_query={"intent": "transcription"}
            ) as conn:
                self._conn = conn
                # GA Realtime transcription-session shape (openai>=2): audio
                # config is nested under audio.input; format is 24 kHz PCM.
                await conn.session.update(session={
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "transcription": transcription,
                            # None = push-to-talk (commit by hand); semantic_vad
                            # = the server closes turns itself, for talk mode.
                            "turn_detection": self._turn_detection(),
                        }
                    },
                })
                self._ready.set()
                async for event in conn:
                    if self._handle_event(event):
                        break
        except Exception as e:
            self._error = e
            self._ready.set()

    def _handle_event(self, event) -> bool:
        """Return True to stop the event loop (transcription complete)."""
        etype = getattr(event, "type", "")
        if etype.endswith("input_audio_buffer.speech_started"):
            self._turn_ended = False  # a new phrase began — the last turn is history
        elif etype.endswith("input_audio_buffer.speech_stopped"):
            self._turn_ended = True   # the semantic VAD closed this phrase
        elif etype.endswith("input_audio_transcription.delta"):
            self._interim += getattr(event, "delta", "") or ""
            if self._on_partial:
                self._on_partial(self._interim.strip())
        elif etype.endswith("input_audio_transcription.completed"):
            final = (getattr(event, "transcript", "") or self._interim).strip()
            if self._semantic:
                # One transcript per chunk: keep collecting, the session stays
                # open until the talk loop finishes the turn.
                if final:
                    self._segments.append(final)
                self._interim = ""
                return False
            self._final = final
            return True
        elif etype.endswith("input_audio_transcription.failed") or etype == "error":
            self._error = RuntimeError(getattr(event, "error", etype))
            return True
        return False

    def _submit(self, coro) -> None:
        if self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def feed(self, audio: np.ndarray) -> None:
        if self._conn is None or self._error:
            return
        b64 = base64.b64encode(float32_to_pcm16(audio)).decode("ascii")
        self._submit(self._conn.input_audio_buffer.append(audio=b64))

    async def _commit(self) -> None:
        await self._conn.input_audio_buffer.commit()

    def finish(self, timeout: float = 8.0) -> Optional[str]:
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime error: {self._error}")
        if self._semantic:
            # The server already committed the buffer when its VAD closed the
            # turn — committing again is an error. Just let the transcript of
            # the last chunk land, then join everything said this turn.
            self._await_segment(timeout)
            self.close()
            if self._error:
                raise BackendUnavailable(f"OpenAI Realtime error: {self._error}")
            return " ".join(self._segments).strip() or self._interim.strip() or None
        if self._conn is not None:
            self._submit(self._commit())
        # Wait for the .completed event (which ends the loop) or timeout.
        self._finished.wait(timeout)
        self.close()
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime error: {self._error}")
        return (self._final or self._interim).strip() or None

    def _await_segment(self, timeout: float, grace: float = 0.4) -> None:
        """
        Wait for this turn's transcript to land: up to ``timeout`` for the first
        chunk, then a short ``grace`` in case the semantic VAD split the turn
        and the last chunk is still in flight.
        """
        deadline = time.monotonic() + timeout
        settled: Optional[float] = None
        while time.monotonic() < deadline:
            if self._error or self._finished.is_set():
                return
            if self._segments:
                now = time.monotonic()
                if settled is None:
                    settled = now + grace
                elif now >= settled:
                    return
            time.sleep(0.05)

    def close(self) -> None:
        self._stop = True
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)


class OpenAIRealtimeBackend(TranscriptionBackend):
    """Streaming live transcription via the OpenAI Realtime API."""

    name = "openai_realtime"
    supports_streaming = True
    stream_sample_rate = 24000  # OpenAI Realtime input is 24 kHz PCM16 mono

    def __init__(self, model: str, eagerness: str = "auto") -> None:
        self._model = model
        self._eagerness = eagerness

    def is_available(self) -> bool:
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def start_stream(self, sample_rate: int, language: str,
                     on_partial: PartialCallback,
                     *, semantic_turns: bool = False) -> StreamingSession:
        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable(
                "openai not installed — run pip install -e '.[openai]'"
            ) from e
        return OpenAIRealtimeSession(self._model, language, on_partial,
                                     semantic_turns=semantic_turns,
                                     eagerness=self._eagerness)

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        # Streaming-only here: force the dispatcher's offline batch fallback.
        raise BackendUnavailable("openai_realtime is streaming-only; use start_stream")
