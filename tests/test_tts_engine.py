"""
Self-check for the TTS engine routing and PCM playback (no framework:
`python tests/test_tts_engine.py`).

What would break silently without it: a voice from one engine sent to the other
provider's API, or a truncated PCM frame written to the sound card — both of
which fail only at speaking time, in a background thread.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.services.tts as tts_module  # noqa: E402
from loquivox.config import CFG          # noqa: E402
from loquivox.state import STATE         # noqa: E402
from loquivox.services.tts import TTSService  # noqa: E402


def test_each_engine_keeps_its_own_voices() -> None:
    for model, (provider, voices) in CFG.TTS_ENGINES.items():
        assert provider in ("groq", "openai") or provider in (tts_module.LOCAL_PROVIDER,), \
            f"{model}: unknown provider {provider}"
        assert voices, f"{model}: no voices"
        STATE.tts_model = model
        for voice in voices:
            STATE.tts_voice = voice
            picked_provider, picked_model, picked_voice = TTSService._engine()
            if picked_provider != provider:
                # A local engine that is not installed here degrades to the
                # cloud one — that is the contract, not a failure.
                assert provider in (tts_module.LOCAL_PROVIDER,), provider
                continue
            assert picked_model == model
            assert picked_voice == voice


def test_a_foreign_voice_falls_back_instead_of_reaching_the_api() -> None:
    for model, (provider, voices) in CFG.TTS_ENGINES.items():
        if provider in (tts_module.LOCAL_PROVIDER,):
            continue  # may not be installed here — covered by the test above
        STATE.tts_model = model
        STATE.tts_voice = "not-a-voice-anywhere"
        _, _, picked = TTSService._engine()
        assert picked == voices[0], f"{model}: kept a foreign voice ({picked})"


def test_unknown_model_stays_on_groq() -> None:
    STATE.tts_model = "some/model-nobody-knows"
    STATE.tts_voice = "diana"
    provider, model, voice = TTSService._engine()
    assert provider == "groq"
    assert model == "some/model-nobody-knows" and voice == "diana"


class _FakeOut:
    """Stand-in for sounddevice's RawOutputStream: records what was written."""

    def __init__(self, **_kw) -> None:
        self.written = b""
        self.stopped = False
        self.aborted = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def write(self, data) -> None:
        assert not self.stopped, "wrote after stop()"
        self.written += bytes(data)

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True


class _FakeSpeech:
    """A speech.create response that hands back ``chunks`` byte by byte."""

    def __init__(self, chunks, fail: bool = False) -> None:
        self.chunks, self.fail = chunks, fail

    def create(self, **_kw):
        if self.fail:
            raise RuntimeError("unsupported response_format: pcm")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def iter_bytes(self, _size):
        return iter(self.chunks)


class _FakeClient:
    def __init__(self, speech) -> None:
        self.audio = type("A", (), {"speech": type(
            "S", (), {"with_streaming_response": speech})()})()


def _play(chunks, fail=False, stop=None):
    out = _FakeOut()
    tts_module.sd = type("sd", (), {"RawOutputStream": lambda **kw: out})
    tts_module._NO_PCM.clear()
    tts_module.get_ai_client = (
        lambda _provider: _FakeClient(_FakeSpeech(chunks, fail)))
    ok = TTSService._stream_pcm("openai", "some-model", "alloy", "hello", stop)
    return ok, out


def test_odd_sized_chunks_never_split_a_frame() -> None:
    # A chunk boundary landing mid-sample must be carried over, not written:
    # PortAudio would take the half sample as a whole one and shear the audio.
    ok, out = _play([b"\x01\x02\x03", b"\x04\x05", b"\x06"])
    assert ok
    assert out.written == b"\x01\x02\x03\x04\x05\x06"
    assert len(out.written) % 2 == 0
    assert out.stopped, "buffer not drained — the last word would be clipped"


def test_a_model_without_pcm_falls_back_once() -> None:
    ok, _ = _play([], fail=True)
    assert ok is False, "should hand over to the whole-file path"
    assert "some-model" in tts_module._NO_PCM, "would retry the failing call forever"


def test_a_barge_in_aborts_instead_of_draining() -> None:
    # The user is talking over the reply: what is still in the sound card must
    # NOT be played out, or the assistant talks over them in return.
    stop = threading.Event()

    def chunks():
        yield b"\x01\x02\x03\x04"
        stop.set()
        yield b"\x05\x06\x07\x08"

    ok, out = _play(chunks(), stop=stop)
    assert ok
    assert out.written == b"\x01\x02\x03\x04", "kept playing after the cue to stop"
    assert out.aborted and not out.stopped, "drained the buffer instead of cutting"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✓ {name}")
    print("all good")
