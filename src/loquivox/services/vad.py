"""
Voice activity detection — end-of-turn detection for talk mode.

Talk mode is a spoken conversation: the user must be able to just *talk*, and
have their turn end when they stop speaking. That is what a server-side VAD
(OpenAI Realtime's ``server_vad``) does for streaming sessions — but Loquivox
must work the same way on every backend, including batch Groq Whisper and the
offline whisper.cpp. So it is done locally, on the audio chunks the recorder
already captures, with the same knobs as a server VAD:

  - ``threshold``       activation level (RMS): louder than this counts as speech
  - ``silence_ms``      how long a pause must last to end the turn
  - ``min_speech_ms``   speech needed before a pause can end anything (kills
                        key-clicks and coughs)

Mic levels vary wildly from machine to machine, so the first ``calibration_ms``
of every turn are spent measuring the room instead of deciding: the activation
level ends up as ``max(threshold, noise_floor * NOISE_MARGIN)``.

This detector answers "has the microphone gone quiet", which is not the same
question as "has the speaker finished" — a silence timer cannot tell a finished
sentence from a hesitation. When ``services/turn_detector.py`` is available it
takes that second decision and this one becomes its trigger: a much shorter
silence window, and ``rearm()`` whenever the model rules the turn unfinished.
Alone (no model, or ``semantic_turns = false``), it decides on its own.

Threading: ``feed()`` is called from the PortAudio callback thread while the
talk loop reads the flags from its worker thread. Both are plain attribute
reads/writes on floats and bools, which the GIL makes atomic — no lock needed.
"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np


class VoiceActivityDetector:
    """Energy-based end-of-turn detector for one conversation turn."""

    #: the measured noise floor is multiplied by this to get the activation level
    NOISE_MARGIN: float = 3.0
    #: absolute floor added on top, so a dead-silent room still needs real speech
    NOISE_OFFSET: float = 0.004

    def __init__(self, sample_rate: int, *, threshold: float = 0.02,
                 silence_ms: int = 900, min_speech_ms: int = 300,
                 calibration_ms: int = 400) -> None:
        self.sample_rate = max(1, int(sample_rate))
        self.threshold = float(threshold)
        self._silence = max(0.0, silence_ms / 1000.0)
        self._min_speech = max(0.0, min_speech_ms / 1000.0)
        self._calibration = max(0.0, calibration_ms / 1000.0)

        self._noise_samples: List[float] = []
        self._level = self.threshold      # activation level, refined by calibration
        self._speech = 0.0                # seconds of speech seen this turn
        self._last_voice: Optional[float] = None  # elapsed at the last voiced chunk

        #: seconds of audio fed so far
        self.elapsed = 0.0
        #: True once speech has been heard at all this turn
        self.speech_started = False
        #: True once a long-enough pause has closed the turn
        self.ended = False

    def feed(self, audio: np.ndarray) -> None:
        """
        Push one captured chunk (float32 mono at ``sample_rate``).

        Cheap enough for the audio callback: one RMS per chunk. Once ``ended``
        is set the detector is inert — the turn is over, later chunks are the
        tail the caller is about to stop recording.
        """
        if self.ended or audio is None or len(audio) == 0:
            return

        self.elapsed += len(audio) / self.sample_rate
        # One fused dot product, no float64 copy of the block: this runs in the
        # PortAudio callback, where an overrun costs dropped audio.
        rms = math.sqrt(float(audio @ audio) / len(audio))

        # Calibration window: listen to the room, decide nothing.
        if self.elapsed <= self._calibration:
            self._noise_samples.append(rms)
            return
        if self._noise_samples:
            floor = float(np.median(self._noise_samples))
            self._level = max(self.threshold, floor * self.NOISE_MARGIN + self.NOISE_OFFSET)
            self._noise_samples = []

        if rms >= self._level:
            self._speech += len(audio) / self.sample_rate
            self._last_voice = self.elapsed
            self.speech_started = True
        elif (self.speech_started
              and self._speech >= self._min_speech
              and self._last_voice is not None
              and self.elapsed - self._last_voice >= self._silence):
            self.ended = True

    def rearm(self, silence_ms: Optional[int] = None) -> None:
        """
        Re-open a turn the semantic detector judged unfinished.

        The pause we just reported stops counting: another full silence window
        has to elapse before ``ended`` can fire again. ``silence_ms`` replaces
        that window — talk mode uses a short trigger for the first check and a
        longer one for the re-checks, so a hesitation doesn't spin the model.
        Speech already heard still counts (``min_speech_ms`` stays satisfied).
        """
        self.ended = False
        self._last_voice = self.elapsed
        if silence_ms is not None:
            self._silence = max(0.0, silence_ms / 1000.0)

    @property
    def silent_for(self) -> float:
        """Seconds since the last voiced chunk (``elapsed`` if none yet)."""
        if self._last_voice is None:
            return self.elapsed
        return self.elapsed - self._last_voice


if __name__ == "__main__":
    # Self-check for the turn state machine (no audio device needed).
    RATE, CHUNK = 16000, 1600           # 100 ms frames
    vad = VoiceActivityDetector(RATE, threshold=0.02, silence_ms=500,
                                min_speech_ms=200, calibration_ms=300)
    quiet = np.zeros(CHUNK, dtype=np.float32)
    voice = (np.random.default_rng(0).standard_normal(CHUNK) * 0.2).astype(np.float32)

    for _ in range(3):                  # 300 ms calibrating on a quiet room
        vad.feed(quiet)
    assert not vad.speech_started, "calibration must not count as speech"
    for _ in range(5):                  # 500 ms of speech
        vad.feed(voice)
    assert vad.speech_started and not vad.ended
    for _ in range(3):                  # 300 ms pause — shorter than silence_ms
        vad.feed(quiet)
    assert not vad.ended, "a short pause must not end the turn"
    for _ in range(3):                  # 600 ms of silence — the turn is over
        vad.feed(quiet)
    assert vad.ended
    print("✓ VoiceActivityDetector turn detection OK")
