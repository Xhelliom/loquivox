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
    #: calibration may raise the activation level at most this many times the
    #: configured threshold. A room's noise floor is never as loud as the voice
    #: on top of it, so a bigger jump means the window measured speech, not the
    #: room — see ``max_level``.
    CALIBRATION_MAX_GAIN: float = 4.0

    def __init__(self, sample_rate: int, *, threshold: float = 0.02,
                 silence_ms: int = 900, min_speech_ms: int = 300,
                 calibration_ms: int = 400) -> None:
        self.sample_rate = max(1, int(sample_rate))
        self.threshold = float(threshold)
        #: ceiling on the activation level. Calibration is a guess made from
        #: 400 ms, and when it guesses wrong it guesses UP: the microphone
        #: opens and the user is already answering, so the window measures
        #: their voice and the level lands above it — after which nothing for
        #: the rest of the turn counts as speech and the turn either never ends
        #: or ends mid-sentence.
        self.max_level = self.threshold * self.CALIBRATION_MAX_GAIN
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
            self._level = min(self.max_level,
                              max(self.threshold,
                                  floor * self.NOISE_MARGIN + self.NOISE_OFFSET))
            self._noise_samples = []

        # And it may only ever come back DOWN from there: the quietest chunk
        # since is a better noise floor than a 400 ms window that may have
        # caught the tail of the last reply. One real pause repairs the guess.
        self._level = min(
            self._level,
            max(self.threshold, rms * self.NOISE_MARGIN + self.NOISE_OFFSET),
        )

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

    def prime(self, seconds: float) -> None:
        """
        Start a turn that is already under way — a barge-in, whose first words
        were captured while the assistant was still speaking.

        Calibration goes with it: a turn that begins mid-sentence has no quiet
        room to measure, and measuring the speech instead would put the
        activation level above the speaker's own voice, so the turn would never
        end. The plain ``threshold`` stands in.
        """
        self._calibration = 0.0
        self._noise_samples = []
        self._speech = max(self._speech, max(0.0, seconds))
        self._last_voice = self.elapsed
        self.speech_started = True

    @property
    def speech_seconds(self) -> float:
        """Seconds of speech heard this turn — how sure we are someone spoke."""
        return self._speech

    @property
    def silent_for(self) -> float:
        """Seconds since the last voiced chunk (``elapsed`` if none yet)."""
        if self._last_voice is None:
            return self.elapsed
        return self.elapsed - self._last_voice


class EchoGate:
    """
    Tells someone speaking over a reply from the reply's own echo.

    Talk mode keeps the microphone open while the assistant speaks, so that it
    can be interrupted. On speakers that microphone also hears the reply, and
    nothing downstream can tell the two apart: the cascade cuts its own reply
    off, and the Realtime server — which hears the microphone continuously —
    reads the assistant's voice as the user interrupting and stops it dead.
    Only this side knows what it just played, so the decision belongs here.

    The rule is one number: a block counts as speech if it is ``margin`` times
    louder than the echo. The echo is measured over the reply's opening
    ``calibration_ms`` and then FROZEN, which is the whole point and the
    difference from ``VoiceActivityDetector``. That one keeps tracking, and
    tracking fails in both directions here: downwards, the pauses between the
    assistant's own sentences drag the level to the room and the next syllable
    of echo reads as speech; upwards, a voice comes up over several blocks,
    each still under the bar, so a bar that follows them climbs ahead of the
    speaker and is never crossed — which kills barge-in on headsets, where
    there is no echo to measure at all.

    ``margin`` is the physical knob (``TALK_BARGE_IN_MARGIN``): how much of the
    speakers leaks back into the microphone is a property of the room and of
    the volume, and no default can know it. ``report`` prints the two numbers
    it is set from. When it cannot be set — measured on a laptop's speakers, a
    voice beat the echo by ×1.2 — the honest outcome is half duplex: the gate
    stays shut, the reply is heard to the end, and acoustic echo cancellation
    (PipeWire's ``libpipewire-module-echo-cancel``) is the only real fix. On a
    headset there is nothing to leak, the bar sits at ``threshold`` and
    interrupting works as it always did.

    Threading: ``feed()`` runs in the PortAudio callback while the talk loop
    reads ``speech_seconds`` from its own thread — plain float reads, as in
    ``VoiceActivityDetector``.
    """

    def __init__(self, sample_rate: int, *, threshold: float = 0.02,
                 margin: float = 2.0, calibration_ms: int = 600) -> None:
        self.sample_rate = max(1, int(sample_rate))
        self.threshold = float(threshold)
        self.margin = max(1.0, float(margin))
        #: A reply often opens with a beat of silence; calibrating on that
        #: alone would put the bar under the echo that follows.
        self._calibration = max(0.0, calibration_ms / 1000.0)
        self.elapsed = 0.0
        #: loudest block of the calibration window — the echo, as heard here
        self.echo_peak = 0.0
        #: loudest block of the whole reply, whatever it was
        self.loudest = 0.0
        #: seconds heard above the bar: how sure we are someone spoke over it
        self.speech_seconds = 0.0

    def feed(self, audio: np.ndarray) -> None:
        """Push one captured block (float32 mono at ``sample_rate``)."""
        if audio is None or len(audio) == 0:
            return
        # One fused dot product, no float64 copy: this runs in the callback.
        rms = math.sqrt(float(audio @ audio) / len(audio))
        self.elapsed += len(audio) / self.sample_rate
        self.loudest = max(self.loudest, rms)
        if self.elapsed <= self._calibration:
            self.echo_peak = max(self.echo_peak, rms)
        elif rms >= self.bar:
            self.speech_seconds += len(audio) / self.sample_rate

    @property
    def bar(self) -> float:
        """Level a block must reach to count as speech over the reply."""
        return max(self.threshold, self.echo_peak * self.margin)

    @property
    def report(self) -> str:
        """
        One line naming what this reply measured — the two numbers ``margin``
        is set from. Anything spoken over the reply had to beat the echo peak
        by the margin; ``loudest`` says by how much it actually did.
        """
        ratio = self.loudest / self.echo_peak if self.echo_peak else 0.0
        return (f"🔉 echo peak {self.echo_peak:.3f} · loudest {self.loudest:.3f}"
                f" (×{ratio:.1f}) · barge-in at ×{self.margin:g}")


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
    # A barge-in turn: primed mid-sentence, it must still be able to end.
    barged = VoiceActivityDetector(RATE, threshold=0.02, silence_ms=500,
                                   min_speech_ms=200, calibration_ms=300)
    barged.prime(0.8)
    assert barged.speech_started and barged.speech_seconds >= 0.8
    for _ in range(3):                  # speech carrying on past the interruption
        barged.feed(voice)
    assert not barged.ended
    for _ in range(8):                  # 800 ms of silence closes it
        barged.feed(quiet)
    assert barged.ended, "a primed turn must still end (calibration ate the level?)"
    # The calibration window catches the user already mid-sentence (they
    # answered the moment the microphone opened): the turn must still end.
    late = VoiceActivityDetector(RATE, threshold=0.02, silence_ms=500,
                                 min_speech_ms=200, calibration_ms=400)
    for _ in range(4):                  # 400 ms of calibration, all speech
        late.feed(voice)
    for _ in range(5):                  # they carry on
        late.feed(voice)
    assert late.speech_started, "speech during calibration left the level deaf"
    for _ in range(8):                  # 800 ms of real silence
        late.feed(quiet)
    assert late.ended, f"the turn never ends (level {late._level:.3f})"
    print("✓ VoiceActivityDetector turn detection OK")

    # EchoGate: the reply leaking into the microphone must not read as someone
    # interrupting, and someone interrupting must get through.
    rng = np.random.default_rng(1)
    echo = (rng.standard_normal(CHUNK) * 0.03).astype(np.float32)
    gate = EchoGate(RATE, threshold=0.02, margin=2.0, calibration_ms=600)
    for _ in range(30):                 # 3 s of reply leaking into the mic
        gate.feed(echo)
    assert gate.speech_seconds == 0, (
        f"the assistant would cut itself off (bar {gate.bar:.3f})")
    for _ in range(5):                  # the user talks over it, louder
        gate.feed(voice)
    assert gate.speech_seconds >= 0.4, "a real interruption went unheard"
    assert "×6" in gate.report or "×7" in gate.report, gate.report

    # A loud reply with pauses between its sentences: the room measured during
    # a pause is quieter than the syllable that follows it, which is what a
    # tracking level gets wrong.
    loud = EchoGate(RATE, threshold=0.02, margin=2.0, calibration_ms=600)
    quiet_block = np.zeros(CHUNK, dtype=np.float32)
    for _ in range(6):
        for block in [(rng.standard_normal(CHUNK) * 0.12).astype(np.float32)] * 8 \
                + [quiet_block] * 6:
            loud.feed(block)
    assert loud.speech_seconds == 0, (
        f"the reply interrupted itself (bar {loud.bar:.3f})")

    # A headset — no echo at all — and a voice that comes up over several
    # blocks the way a real one does: a bar that tracks climbs ahead of it.
    head = EchoGate(RATE, threshold=0.02, margin=2.0, calibration_ms=600)
    for _ in range(20):                 # 2 s of reply, nothing leaking back
        head.feed((rng.standard_normal(CHUNK) * 0.002).astype(np.float32))
    for i in range(40):                 # the user starts talking, gradually
        head.feed((rng.standard_normal(CHUNK) * 0.004 * 1.15 ** i).astype(np.float32))
    assert head.speech_seconds >= 0.4, (
        f"the bar followed the voice up — barge-in is dead (bar {head.bar:.3f})")
    print("✓ EchoGate OK")
