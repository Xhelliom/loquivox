"""
Audio recording and transcription service.
"""
from __future__ import annotations

import queue
from typing import Any, Optional

import numpy as np
import sounddevice as sd

from loquivox import config as config_module
from loquivox.config import CFG
from loquivox.decorators import safe_execute
from loquivox.state import STATE
from loquivox.transcription import get_dispatcher


def list_input_devices() -> list[str]:
    """Names of the available capture devices (those with input channels).

    De-duplicated, capture order preserved. Returns ``[]`` if PortAudio can't be
    queried (no audio backend, headless, …) — callers fall back to the default.
    """
    names: list[str] = []
    try:
        seen = set()
        for dev in sd.query_devices():
            if dev.get("max_input_channels", 0) > 0:
                name = (dev.get("name") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
    except Exception:
        pass
    return names


def _resolve_input_device():
    """Map the configured device NAME to a PortAudio index, or ``None`` (default).

    Read from the live config module (not the import-time ``CFG``) so a change in
    the settings dialog takes effect on the next recording without a restart.
    An empty/unknown/disconnected name resolves to ``None`` = system default.
    """
    name = (config_module.CFG.INPUT_DEVICE or "").strip()
    if not name:
        return None
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and (dev.get("name") or "").strip() == name:
                return idx
    except Exception:
        pass
    return None


class AudioService:
    """Audio recording and transcription service."""

    @staticmethod
    def audio_callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Capture audio chunks into buffer while recording (skipped if paused)."""
        if not STATE.recording or STATE.paused:
            return

        data_copy = indata.copy()
        mono = data_copy[:, 0]

        # Always buffer: the batch path needs it, and it's the safety net the
        # streaming path falls back to if the live session fails.
        STATE.audio_buffer.append(data_copy)

        # Feed the turn detector, if talk mode is listening.
        detector = STATE.vad
        if detector is not None:
            try:
                detector.feed(mono)
            except Exception:
                pass

        # Feed the live streaming session, if one is active.
        session = STATE.stream_session
        if session is not None:
            try:
                session.feed(mono)
            except Exception:
                pass

        # Feed the visualization queue (skip if full). Sent whole, NOT
        # downsampled: PortAudio blocks are small and vary wildly (4–139 frames
        # at 16 kHz), so thinning them left the overlay with near-empty chunks.
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono)
        except Exception:
            pass

    @staticmethod
    def start_recording(*, semantic_turns: bool = False) -> None:
        """
        Start audio recording stream (and a live session if the backend streams).

        ``semantic_turns`` asks a streaming backend to detect turn boundaries
        itself (OpenAI Realtime's semantic VAD) instead of transcribing one
        push-to-talk segment — talk mode only; every other mode wants the
        recording to end exactly when the key is released.
        """
        STATE.audio_buffer = []
        STATE.stream_session = None
        STATE.paused = False
        AudioService._clear_viz_queue()

        dispatcher = get_dispatcher()
        streaming_backend = dispatcher.streaming_backend()
        # A streaming backend captures at its own wire rate (avoids resampling);
        # everything else stays at the configured capture rate.
        rate = streaming_backend.stream_sample_rate if streaming_backend else CFG.SAMPLE_RATE
        STATE.capture_rate = rate

        STATE.stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype='float32',
            device=_resolve_input_device(),  # None = system default mic
            callback=AudioService.audio_callback
        )
        STATE.stream.start()
        STATE.recording = True
        STATE.recording_generation += 1

        # Open the live session AFTER recording is armed so early audio is
        # buffered (and replayable via fallback) even if the session is slow.
        if streaming_backend is not None:
            session = dispatcher.start_stream(
                rate, AudioService._on_partial, semantic_turns=semantic_turns
            )
            STATE.stream_session = session  # None → silently uses batch fallback

    @staticmethod
    def stop_recording() -> Optional[np.ndarray]:
        """Stop recording and return audio data."""
        STATE.recording = False
        STATE.paused = False
        if STATE.stream:
            STATE.stream.stop()
            STATE.stream.close()
            STATE.stream = None

        if STATE.audio_buffer:
            return np.concatenate(STATE.audio_buffer, axis=0)
        return None

    @staticmethod
    def snapshot_tail(seconds: float) -> Optional[np.ndarray]:
        """
        The last ``seconds`` of the recording in progress, float32 mono at
        ``STATE.capture_rate`` — what the semantic turn detector reads while
        the microphone is still open.

        Reads backwards from a snapshot of the buffer's LENGTH rather than
        copying the list: the callback thread only ever appends, so every index
        below that length stays valid and only the newest chunk can be missed.
        Cost is proportional to the tail asked for, not to the whole recording.
        """
        buffer = STATE.audio_buffer
        index = len(buffer)
        if not index:
            return None
        needed = max(1, int(seconds * STATE.capture_rate))
        tail, total = [], 0
        while index and total < needed:
            index -= 1
            chunk = buffer[index]
            tail.append(chunk)
            total += len(chunk)
        data = np.concatenate(tail[::-1], axis=0)
        if data.ndim > 1:
            data = data[:, 0]
        return data[-needed:] if len(data) > needed else data

    @staticmethod
    def _on_partial(text: str) -> None:
        """Live-transcript callback from a streaming session → update overlay."""
        # Late import to avoid a circular import at module load.
        from loquivox.managers.overlay import OverlayManager
        OverlayManager.set_live_text(text)

    @staticmethod
    def _clear_viz_queue() -> None:
        """Clear the visualization queue."""
        while not STATE.viz_queue.empty():
            try:
                STATE.viz_queue.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    @safe_execute("Transcription")
    def transcribe(audio_data: np.ndarray) -> Optional[str]:
        """
        Transcribe a finished recording via the configured backend.

        Backend selection, the short-clip guard and offline fallback all live
        in the dispatcher (see ``loquivox.transcription``); this stays a
        thin entry point so the recording flow is backend-agnostic.
        """
        return get_dispatcher().transcribe(audio_data, STATE.capture_rate)
