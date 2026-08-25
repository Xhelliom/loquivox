"""
The turn detector must be armed BEFORE the live transcription session opens.

Opening a cloud session takes about a second. A detector armed after it spends
its calibration window a second into the turn — in the middle of a sentence the
user began the moment the microphone opened — and a level calibrated on speech
sits above their voice, so the rest of the turn reads as silence: the turn
never ends, or ends mid-sentence once max_silence runs out.

    python tests/test_turn_detection.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_detector_armed_before_session():
    from loquivox.services import audio as audio_module
    from loquivox.state import STATE

    order = []

    class _Stream:
        def start(self): order.append("mic")
        def stop(self): pass
        def close(self): pass

    class _Backend:
        stream_sample_rate = 16000

    dispatcher = types.SimpleNamespace(
        streaming_backend=lambda: _Backend(),
        start_stream=lambda *a, **k: order.append("session") or "session",
    )
    audio_module.get_dispatcher = lambda: dispatcher
    audio_module.sd = types.SimpleNamespace(InputStream=lambda **kw: _Stream())

    STATE.vad = None
    audio_module.AudioService.start_recording(
        detector=lambda rate: order.append(f"vad@{rate}") or "detector")
    assert order == ["mic", "vad@16000", "session"], order
    assert STATE.vad == "detector"
    print("✓ le détecteur est armé avant l'ouverture de la session live")


def test_level_survives_a_calibration_full_of_speech():
    """The exact failure the user hit: answering the instant the mic opens."""
    import numpy as np
    from loquivox.services.vad import VoiceActivityDetector

    rate, chunk = 16000, 1600  # 100 ms
    rng = np.random.default_rng(0)
    voice = (rng.standard_normal(chunk) * 0.2).astype(np.float32)
    quiet = (rng.standard_normal(chunk) * 0.002).astype(np.float32)

    vad = VoiceActivityDetector(rate, threshold=0.02, silence_ms=250,
                                min_speech_ms=300, calibration_ms=400)
    for _ in range(20):          # 2 s: the whole calibration window is speech
        vad.feed(voice)
    assert vad.speech_started, f"deaf level {vad._level:.3f} — the turn can't end"
    for _ in range(4):           # 400 ms of real silence
        vad.feed(quiet)
    assert vad.ended, f"turn never ends (level {vad._level:.3f})"
    # …and the turn did not end WHILE they were speaking.
    late = VoiceActivityDetector(rate, threshold=0.02, silence_ms=250,
                                 min_speech_ms=300, calibration_ms=400)
    for _ in range(60):          # 6 s of uninterrupted speech
        late.feed(voice)
    assert not late.ended, "cut off mid-sentence"
    print("✓ parler dès l'ouverture du micro ne rend plus le seuil sourd")


if __name__ == "__main__":
    test_level_survives_a_calibration_full_of_speech()
    test_detector_armed_before_session()
    print("✓ détection de fin de tour OK")
