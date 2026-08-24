"""
Semantic end-of-turn detection for talk mode (Smart Turn v3).

A silence timer cannot tell "…and then, uh…" from a finished sentence: it only
knows how long the microphone has been quiet. Smart Turn is a small audio model
(Whisper-tiny encoder + a linear head, ~8M parameters) that listens to the last
8 seconds of *speech* — its prosody, its intonation, the way it lands — and
returns the probability that the speaker is done. It is audio-native, so unlike
the text-based detectors it needs no transcript and works with every Loquivox
backend, offline whisper.cpp included.

It never runs continuously. The energy VAD (``services/vad.py``) stays the
trigger; this model is asked one question, only when a pause is heard:

    pause detected  →  P(turn complete)  →  ≥ threshold: the turn ends
                                        →  < threshold: keep listening

The model is an 8 MB int8 ONNX file, fetched once into ``~/.cache/loquivox``
and run through ``onnxruntime`` (an optional dependency:
``pip install -e '.[turn]'``). Anything missing — no onnxruntime, no network on
first use, a corrupt file — degrades to plain silence detection with one
warning, never an error.

Model and feature extraction: Smart Turn v3 by Daily/Pipecat, BSD 2-Clause
(https://github.com/pipecat-ai/smart-turn). Not affiliated.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

import loquivox.config as config_module
from loquivox.services._whisper_features import compute_whisper_log_mel_features
from loquivox.transcription.util import WHISPER_RATE, to_mono_16k

#: where the downloaded model is cached
MODEL_DIR: Path = Path.home() / ".cache" / "loquivox"
#: the model reads exactly this much audio (seconds), keeping the END of it
WINDOW_SEC: int = 8
#: a plausible model file is at least this big — smaller means we saved an
#: error page, not an ONNX graph
MIN_MODEL_BYTES: int = 200_000

#: the model repository; its file list is read at resolve time so a new
#: Smart Turn release is picked up without a Loquivox update
HF_REPO: str = "pipecat-ai/smart-turn-v3"
HF_API: str = f"https://huggingface.co/api/models/{HF_REPO}"
HF_FILE: str = f"https://huggingface.co/{HF_REPO}/resolve/main/{{name}}"
#: used when the repository can't be listed (offline, API change)
DEFAULT_MODEL_URL: str = HF_FILE.format(name="smart-turn-v3.2-cpu.onnx")


class SmartTurnDetector:
    """Smart Turn v3 wrapped for one question: is this pause the end of a turn?"""

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._session = None
        self._lock = threading.Lock()

    def load(self) -> bool:
        """
        Build the ONNX session (idempotent, safe from any thread).

        Returns False — with one printed reason — when the model can't be run,
        which the caller reads as "fall back to plain silence detection".
        """
        if self._session is not None:
            return True
        with self._lock:
            if self._session is not None:
                return True
            try:
                import onnxruntime as ort
            except ImportError:
                print("⚠️  Semantic turn detection needs onnxruntime — "
                      "pip install -e '.[turn]' (falling back to silence VAD)")
                return False
            try:
                options = ort.SessionOptions()
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.inter_op_num_threads = 1
                options.intra_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(self.model_path), sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as e:
                print(f"⚠️  Turn-detection model failed to load: {e}")
                return False
            print(f"🧠 Semantic turn detection ready ({self.model_path.name})")
            return True

    def probability(self, audio: np.ndarray, sample_rate: int) -> Optional[float]:
        """
        Probability (0..1) that the speaker has finished, or None if the model
        could not answer (caller then behaves like the plain VAD).

        ``audio`` is the tail of the recording as float32 mono at
        ``sample_rate``; it is resampled to 16 kHz and cropped/padded to the
        model's 8-second window — padding goes at the FRONT, so the end of the
        speech always sits at the end of the window, where the model expects it.
        """
        if audio is None or len(audio) == 0 or not self.load():
            return None
        try:
            samples = to_mono_16k(audio, sample_rate)
            window = WINDOW_SEC * WHISPER_RATE
            if len(samples) > window:
                samples = samples[-window:]
            elif len(samples) < window:
                samples = np.pad(samples, (window - len(samples), 0))
            features = compute_whisper_log_mel_features(samples)
            outputs = self._session.run(
                None, {"input_features": np.expand_dims(features, axis=0)}
            )
            return float(np.asarray(outputs[0]).reshape(-1)[0])
        except Exception as e:
            print(f"⚠️  Turn detection failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Model file + process-wide instance
# ---------------------------------------------------------------------------
def _rank_model_file(name: str) -> tuple:
    """Sort key picking the newest CPU/int8 ONNX in the repository's file list."""
    lowered = name.lower()
    return (("cpu" in lowered or "int8" in lowered), name)


def resolve_model_url(configured: str = "", timeout: float = 15.0) -> str:
    """
    The URL to fetch the model from: the configured one, else the newest
    CPU/int8 ``.onnx`` the model repository currently ships.

    Resolving from the repository listing rather than hard-coding a filename
    means a Smart Turn point release keeps working; ``DEFAULT_MODEL_URL`` is
    the safety net when the listing can't be read.
    """
    configured = (configured or "").strip()
    if configured:
        return configured
    try:
        with urllib.request.urlopen(HF_API, timeout=timeout) as response:
            data = json.load(response)
        names = [
            f["rfilename"] for f in data.get("siblings", [])
            if str(f.get("rfilename", "")).endswith(".onnx")
        ]
        if names:
            return HF_FILE.format(name=max(names, key=_rank_model_file))
    except Exception as e:
        print(f"⚠️  Could not list {HF_REPO} ({e}) — using the default model file")
    return DEFAULT_MODEL_URL


def ensure_model(url: str, path: Path, timeout: float = 60.0) -> Optional[Path]:
    """
    Return the local model path, downloading it once (~8 MB) if missing.

    Written to a ``.part`` file and renamed only once it looks like a real
    model, so an interrupted or redirected download can never leave a corrupt
    file behind that would fail forever after.
    """
    if path.exists() and path.stat().st_size >= MIN_MODEL_BYTES:
        return path
    tmp = path.with_suffix(".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"⬇️  Downloading the turn-detection model (~8 MB) → {path}")
        with urllib.request.urlopen(url, timeout=timeout) as response, open(tmp, "wb") as out:
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                out.write(block)
        if tmp.stat().st_size < MIN_MODEL_BYTES:
            raise RuntimeError(f"got {tmp.stat().st_size} bytes — not a model")
        tmp.replace(path)
        return path
    except Exception as e:
        print(f"⚠️  Could not fetch the turn-detection model: {e}")
        print("   Talk mode falls back to silence detection. Set "
              "[talk] turn_model_path in config.toml to use a local copy.")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


_detector: Optional[SmartTurnDetector] = None
_resolved = False
_resolve_lock = threading.Lock()


def get_turn_detector() -> Optional[SmartTurnDetector]:
    """
    The shared detector, or None when semantic turns are off or unavailable.

    Resolved once per process: the download and the failure warning happen at
    most one time, and every later call is a dictionary lookup — it sits in the
    talk loop's hot path.
    """
    global _detector, _resolved
    cfg = config_module.CFG
    if not cfg.TALK_SEMANTIC_TURNS:
        return None
    if _resolved:
        return _detector
    with _resolve_lock:
        if _resolved:
            return _detector
        override = (cfg.TALK_TURN_MODEL_PATH or "").strip()
        if override:
            path = Path(override).expanduser()
            if not path.exists():
                print(f"⚠️  [talk] turn_model_path not found: {path}")
                path = None
        else:
            url = resolve_model_url(cfg.TALK_TURN_MODEL_URL)
            path = ensure_model(url, MODEL_DIR / url.rsplit("/", 1)[-1])
        detector = SmartTurnDetector(path) if path else None
        if detector is not None and not detector.load():
            detector = None
        _detector, _resolved = detector, True
        return _detector


def ready_detector() -> Optional[SmartTurnDetector]:
    """
    The detector if it is already resolved and loaded, else None — never blocks.

    ``get_turn_detector`` may sit on a download or an ONNX build; the talk loop
    must not, so it asks this instead and simply falls back to silence detection
    for the turns that happen while ``prewarm_async`` is still working. On the
    very first session that is usually the first turn only.
    """
    if not config_module.CFG.TALK_SEMANTIC_TURNS:
        return None
    return _detector if _resolved else None


def prewarm_async() -> None:
    """
    Resolve + load the model in the background (called when a talk session
    starts), so the first pause isn't waiting on a download or an ONNX build.
    """
    threading.Thread(target=get_turn_detector, daemon=True).start()


if __name__ == "__main__":
    # Self-check for the feature pipeline (no model file needed). With
    # `transformers` installed it also asserts parity with the reference
    # extractor Smart Turn was trained against:
    #     python -m loquivox.services.turn_detector
    audio = np.random.default_rng(0).standard_normal(3 * WHISPER_RATE).astype(np.float32) * 0.1
    window = WINDOW_SEC * WHISPER_RATE
    padded = np.pad(audio, (window - len(audio), 0))
    features = compute_whisper_log_mel_features(padded)
    assert features.shape == (80, 800), features.shape
    assert features.dtype == np.float32
    print(f"✓ log-mel OK — {features.shape}, range "
          f"[{features.min():.2f}, {features.max():.2f}]")

    try:
        from transformers import WhisperFeatureExtractor
    except ImportError:
        print("· transformers not installed — parity check skipped")
    else:
        reference = WhisperFeatureExtractor(chunk_length=WINDOW_SEC)(
            padded, sampling_rate=WHISPER_RATE, return_tensors="np",
            padding="max_length", max_length=window, truncation=True,
            do_normalize=True,
        ).input_features.squeeze(0)
        assert np.allclose(features, reference, atol=1e-4), \
            f"max diff {np.abs(features - reference).max()}"
        print("✓ parity with transformers.WhisperFeatureExtractor OK")
