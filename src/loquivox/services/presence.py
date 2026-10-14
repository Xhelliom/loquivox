"""
Presence — the always-on, strictly local listener behind the companion mode.

It keeps one microphone stream open outside any session, watches it for a wake
word with openwakeword and for voice with the energy VAD, and tells the caller
when someone is addressing the assistant. Nothing is recorded, nothing leaves
the machine: the cloud session (``services/live_talk.py``) opens only after
``on_wake`` fires.

openwakeword is optional (``pip install -e '.[wake]'``, or Settings → Companion,
which installs it ``--no-deps``: its ``tflite-runtime`` requirement has no wheel
for Python 3.14 and only the ONNX engine is used here). Without it — or without
its model files, fetched once with ``openwakeword.utils.download_models()`` —
the listener still runs, says so once, and only the hotkey starts a session.

The microphone is shared with nobody: ``LiveTalk.start`` opens its own
``sd.InputStream`` (and waits for a Bluetooth headset to switch profile), so
the caller must ``release()`` the device first and ``resume()`` afterwards.
``release`` really closes the stream; ``resume`` reopens it and clears the wake
model's memory so the phrase that woke us cannot fire a second time.

Threading: the PortAudio callback does the cheap work only; ``on_wake`` runs on
its own thread, so it may block (and call ``release``).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from loquivox.services.audio import resolve_input_device
from loquivox.services.vad import VoiceActivityDetector

RATE: int = 16000
#: openwakeword scores 80 ms frames
BLOCK: int = 1280
#: built-in openwakeword model; the French phrase is the owner's call (phase 1)
WAKE_MODEL: str = "hey_jarvis"
WAKE_THRESHOLD: float = 0.5
#: what openwakeword's ONNX engine really imports; installed beside it ``--no-deps``
WAKE_DEPS = ("onnxruntime", "scipy", "scikit-learn", "tqdm", "requests")


def load_wake_model(name: str):
    """The ONNX model for ``name``; raises with the cause if the package or its files are missing."""
    from openwakeword.model import Model
    return Model(wakeword_models=[name], inference_framework="onnx")


def wake_problem(name: str) -> str:
    """Why ``name`` cannot be loaded, or "" when it can — the only test a ✓ may rest on."""
    try:
        load_wake_model(name)
    except Exception as exc:
        return f"openwakeword unavailable ({exc})"
    return ""
#: after a wake, ignore further ones for this long (seconds)
REFRACTORY: float = 2.0
#: longest stretch kept after a wake word (seconds)
TAIL_MAX: int = 30


class Presence:
    def __init__(self, on_wake: Callable[[], None], *, model=None,
                 wake_model: str = WAKE_MODEL, threshold: float = WAKE_THRESHOLD,
                 stream_factory=None) -> None:
        self.on_wake = on_wake
        self.wake_error = ""  # why the wake word is off, "" when it is not
        self.wake_model = wake_model
        self.threshold = threshold
        self._model = model
        self._warned = False
        self._stream_factory = stream_factory or self._open_input
        self._stream = None
        self._vad = VoiceActivityDetector(RATE)
        self._last_wake = -REFRACTORY
        self._lock = threading.Lock()
        #: True while someone is talking (energy VAD), whatever the wake word says
        self.speaking = False
        #: called from the audio callback with the voice level 0..1 (0 when quiet)
        self.on_level: Optional[Callable[[float], None]] = None
        #: audio heard since the wake word, handed to the cloud session by take_tail()
        self._tail: Optional[list] = None

    @property
    def listening(self) -> bool:
        return self._stream is not None

    @staticmethod
    def _open_input(callback):
        return sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                              blocksize=BLOCK, device=resolve_input_device(),
                              callback=callback)

    def _load_model(self):
        try:
            model = load_wake_model(self.wake_model)
            self.wake_error = ""
            return model
        except Exception as exc:  # missing package or missing model files
            self.wake_error = f"openwakeword unavailable ({exc})"
            if not self._warned:
                print(f"⚠️  Presence: openwakeword unavailable ({exc}) — "
                      "wake word off, hotkey only")
                self._warned = True
            return None

    def set_wake_model(self, name: str) -> None:
        """Swap the wake word without closing the microphone."""
        self.wake_model = name
        self._warned = False
        self._model = self._load_model()

    def start(self) -> None:
        if self._model is None:
            self._model = self._load_model()
        self.resume()

    def stop(self) -> None:
        self.release()

    def release(self) -> None:
        """Close the microphone for real, so another InputStream can open."""
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self.speaking = False

    def take_tail(self) -> list:
        """What was said since the wake word (float32 blocks); empties the buffer."""
        tail, self._tail = self._tail or [], None
        return tail

    def resume(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._tail = None
            if self._model is not None:
                self._model.reset()
            self._vad = VoiceActivityDetector(RATE)
            self._stream = self._stream_factory(self._on_audio)
            self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        block = np.asarray(indata, dtype=np.float32).reshape(-1)
        if self._vad.ended:   # one detector per utterance: start over
            self._vad = VoiceActivityDetector(RATE)
        self._vad.feed(block)
        # ponytail: capped at TAIL_MAX seconds; the session opens in a few
        if self._tail is not None and len(self._tail) < TAIL_MAX * RATE // BLOCK:
            self._tail.append(block.copy())
        self.speaking = self._vad.speech_started and not self._vad.ended
        if self.on_level:
            rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
            self.on_level(min(1.0, rms * 6) if self.speaking else 0.0)
        if self._model is None:
            return
        pcm = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16)
        scores = self._model.predict(pcm)
        now = time.monotonic()
        if (max(scores.values(), default=0.0) >= self.threshold
                and now - self._last_wake >= REFRACTORY):
            self._last_wake = now
            self._tail = []
            threading.Thread(target=self.on_wake, daemon=True).start()


if __name__ == "__main__":
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[3] / "tests" / "test_presence.py"),
                   run_name="__main__")
