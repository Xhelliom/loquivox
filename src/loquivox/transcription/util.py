"""
Shared audio-prep helpers for transcription backends.

Recording is captured as float32 mono at ``CFG.SAMPLE_RATE`` (shape ``(N, 1)``).
Backends need it in different shapes — Groq wants a 16 kHz WAV upload, local
whisper.cpp wants a 1-D float32 array at exactly 16 kHz — so the conversions
live here rather than being duplicated per backend.
"""
from __future__ import annotations

import io
import shutil
import urllib.request
from math import gcd
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from scipy.io.wavfile import write as wav_write
from scipy.signal import resample_poly

WHISPER_RATE = 16000  # every Whisper variant runs internally at 16 kHz


def download_file(url: str, dest: Path, *, min_bytes: int = 0,
                  timeout: Optional[float] = None) -> None:
    """
    Fetch ``url`` into ``dest``, atomically.

    The body lands in a sibling ``.part`` file and is renamed only once it is
    complete and at least ``min_bytes`` long, so an interrupted download — or a
    captive-portal HTML page — can never leave behind something that looks like
    a usable model. Raises on any failure, having removed the partial file;
    callers decide what that means (a fallback, or ``BackendUnavailable``).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, \
                open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        size = partial.stat().st_size
        if size < min_bytes:
            raise RuntimeError(f"got {size} bytes, expected at least {min_bytes}")
        partial.replace(dest)
    except Exception:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def resample_down(audio: np.ndarray, src_rate: int, target_rate: int) -> Tuple[np.ndarray, int]:
    """
    Downsample ``audio`` to ``target_rate``. No-op if the target is 0/disabled
    or not below the source rate (we never upsample on this path — it would only
    inflate the payload with no quality gain).
    """
    if not target_rate or target_rate >= src_rate:
        return audio, src_rate
    g = gcd(int(target_rate), int(src_rate))
    resampled = resample_poly(audio, target_rate // g, src_rate // g, axis=0)
    return resampled.astype(np.float32), target_rate


def to_mono_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """
    Return a contiguous 1-D float32 array at exactly 16 kHz, mono — the rate
    every Whisper variant runs at. The whisper.cpp backend converts this to a
    16-bit PCM WAV for the engine. Resamples up or down as needed.
    """
    data = audio
    if src_rate != WHISPER_RATE:
        g = gcd(WHISPER_RATE, int(src_rate))
        data = resample_poly(data, WHISPER_RATE // g, src_rate // g, axis=0)
    if data.ndim > 1:
        data = data.reshape(data.shape[0], -1).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def write_wav(audio: np.ndarray, rate: int) -> io.BytesIO:
    """Encode ``audio`` to an in-memory WAV buffer ready for upload."""
    buf = io.BytesIO()
    buf.name = "audio.wav"
    wav_write(buf, rate, audio)
    buf.seek(0)
    return buf
