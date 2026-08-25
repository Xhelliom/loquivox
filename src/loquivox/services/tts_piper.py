"""
Local text-to-speech through Piper — offline, and the fastest of the lot.

Piper is a small VITS model run by a native binary: it starts emitting audio in
tens of milliseconds and needs no network, no key and no GPU. The voice is
flatter than a cloud model's, which is the trade for a spoken assistant that
keeps working on a train.

Two ways to run it, in this order: the ``piper-tts`` package's Python API,
which keeps the loaded voice in memory between replies (~0.1s to the first
samples), or the ``piper`` binary on PATH, which reloads the model on every
sentence (~1.2s). Neither is a hard dependency.

Voices are ~60 MB ONNX files fetched once into
``~/.cache/loquivox/piper`` from the Piper voice repository; a voice id encodes
its own path there (``fr_FR-siwis-medium`` → ``fr/fr_FR/siwis/medium/…``), so
any voice the repository ships works without a table to keep in sync. A
filesystem path is taken as-is, for a voice built or downloaded by hand.

Everything here degrades: no binary, no voice, no network → the caller is told
so and speaks through the cloud engine instead, exactly like a missing
onnxruntime falls back to the silence VAD.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from loquivox.transcription.util import download_file

#: where downloaded voices live
VOICES_DIR: Path = Path.home() / ".cache" / "loquivox" / "piper"
#: the voice repository, addressed by the path a voice id encodes
HF_VOICE: str = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}"
#: a plausible voice is at least this big — smaller means an error page
MIN_VOICE_BYTES: int = 1_000_000

_command: Optional[List[str]] = None
_checked = False
_lock = threading.Lock()
#: the loaded voice, kept between replies — reloading it costs ~1s a sentence
_voice_cache: dict = {}


def command() -> Optional[List[str]]:
    """
    How to run Piper on this machine, or None if it is not installed.

    Resolved once per process: this sits in front of every spoken reply.
    """
    global _command, _checked
    if _checked:
        return _command
    with _lock:
        if _checked:
            return _command
        found = shutil.which("piper")
        if found:
            _command = [found]
        else:
            try:
                import piper  # noqa: F401  (the pip package, no console script)
                _command = [sys.executable, "-m", "piper"]
            except ImportError:
                _command = None
        _checked = True
        return _command


def is_available() -> bool:
    """True when Piper can speak here."""
    return command() is not None


def _module_voice(path: Path):
    """
    The loaded ``PiperVoice`` for ``path``, or None if the package is absent.

    Kept in memory: loading the ONNX voice is the whole of Piper's startup
    cost, and paying it once per reply would put the local engine two seconds
    behind the cloud it is meant to replace.
    """
    key = str(path)
    if key in _voice_cache:
        return _voice_cache[key]
    with _lock:
        if key in _voice_cache:
            return _voice_cache[key]
        try:
            from piper import PiperVoice
            _voice_cache[key] = PiperVoice.load(str(path))
        except Exception as e:
            print(f"⚠️  Piper voice failed to load ({e}) — using the binary")
            _voice_cache[key] = None
        return _voice_cache[key]


def _repo_path(voice: str, suffix: str = ".onnx") -> str:
    """``fr_FR-siwis-medium`` → ``fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx``."""
    locale, speaker, quality = voice.split("-", 2)
    return f"{locale.split('_')[0]}/{locale}/{speaker}/{quality}/{voice}{suffix}"


def ensure_voice(voice: str, timeout: float = 120.0) -> Optional[Path]:
    """
    The local ``.onnx`` for ``voice``, downloading it once (~60 MB) if needed.

    A path is used as it is. Both the model and its ``.onnx.json`` (which
    carries the sample rate) are fetched; the write is atomic and size-checked,
    so an interrupted download cannot leave a file that fails forever after.
    Returns None — with one printed reason — when it cannot be had.
    """
    voice = (voice or "").strip()
    if not voice:
        return None
    if "/" in voice or voice.endswith(".onnx"):
        path = Path(voice).expanduser()
        return path if path.exists() else None
    path = VOICES_DIR / f"{voice}.onnx"
    if path.exists() and path.stat().st_size >= MIN_VOICE_BYTES:
        return path
    try:
        print(f"⬇️  Downloading the Piper voice {voice} (~60 MB) → {path}")
        download_file(HF_VOICE.format(path=_repo_path(voice)), path,
                      min_bytes=MIN_VOICE_BYTES, timeout=timeout)
        download_file(HF_VOICE.format(path=_repo_path(voice, ".onnx.json")),
                      path.with_suffix(".onnx.json"), timeout=timeout)
        return path
    except Exception as e:
        print(f"⚠️  Could not fetch the Piper voice {voice}: {e}")
        return None


def _sample_rate(path: Path, default: int = 22050) -> int:
    """The voice's own sample rate, from the config file shipped beside it."""
    try:
        config = json.loads(path.with_suffix(".onnx.json").read_text())
        return int(config["audio"]["sample_rate"])
    except Exception:
        return default


@contextmanager
def stream(voice: str, text: str):
    """
    Yield ``(sample_rate, iterator of PCM bytes)`` spoken by a local voice.

    Raw int16 mono, the same shape the cloud engines stream, so playback and
    barge-in are one code path. An interruption abandons the generator
    mid-sentence; the subprocess path also gets killed on the way out.
    """
    cmd = command()
    path = ensure_voice(voice)
    if cmd is None or path is None:
        raise RuntimeError(f"Piper unavailable (binary={bool(cmd)}, voice={voice})")

    loaded = _module_voice(path)
    if loaded is not None:
        # One chunk per sentence, straight from the model held in memory —
        # abandoning the generator mid-sentence is what makes a barge-in
        # instant, with no process to kill.
        yield (loaded.config.sample_rate,
               (chunk.audio_int16_bytes for chunk in loaded.synthesize(text)))
        return

    process = subprocess.Popen(
        cmd + ["--model", str(path), "--output-raw"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
        yield _sample_rate(path), iter(lambda: process.stdout.read(4096), b"")
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()


if __name__ == "__main__":
    # Self-check for the id → repository path mapping (no network needed):
    #     python -m loquivox.services.tts_piper
    assert _repo_path("fr_FR-siwis-medium") == \
        "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx"
    # An underscore in the speaker must not be mistaken for a separator.
    assert _repo_path("fr_FR-mls_1840-low") == \
        "fr/fr_FR/mls_1840/low/fr_FR-mls_1840-low.onnx"
    assert _repo_path("en_US-lessac-medium", ".onnx.json") == \
        "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
    # The voices offered in Settings live in CFG.TTS_ENGINES — the one list.
    from loquivox.config import CFG
    for name in CFG.TTS_ENGINES["piper"][1]:
        assert _repo_path(name).endswith(f"/{name}.onnx"), name
    print(f"✓ Piper voice paths OK — installed: {is_available()}")
