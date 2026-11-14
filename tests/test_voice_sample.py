"""
The voice sample: gpt-live is made to say a line with the chosen voice, once,
and the recording is kept. No network, no GTK, no sound card:

    python tests/test_voice_sample.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import asyncio
import base64
import sys
import wave
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from loquivox.services import voice_sample as vs  # noqa: E402

BLOCK = vs.BLOCK_BYTES


def test_input_is_continuous_zeros_around_the_question():
    """The rule of the module: a gap in the input stalls the voice. Never nothing."""
    question = np.full(2400 * 3, 5000, dtype=np.int16)         # 300 ms
    gen = vs.input_blocks(question)
    blocks = [next(gen) for _ in range(30)]                    # 3 s, far past the end
    assert all(len(b) == BLOCK for b in blocks), "a block was short or missing"
    pcm = np.frombuffer(b"".join(blocks), dtype=np.int16)
    loud = np.flatnonzero(pcm)
    assert loud.size == question.size, "the question did not go through whole"
    assert loud[0] >= vs.LEAD_SECONDS * vs.RATE - 1, "no silence ahead of the question"
    assert not pcm[loud[-1] + 1:].any() and pcm.size > loud[-1] + 2400, "no zeros after it"


def test_trim_keeps_a_margin_and_cuts_the_rest():
    pcm = np.zeros(24000, dtype=np.int16)
    pcm[10000:10100] = 9000
    out = vs.trim_silence(pcm)
    margin = vs.RATE // 10
    assert len(out) == 100 + 2 * margin
    assert vs.trim_silence(np.zeros(10, dtype=np.int16)).size >= 0   # all silence: no crash


def test_wav_is_a_real_wav(tmp=None):
    path = Path(_hermetic.HOME) / "x.wav"
    path.write_bytes(vs.wav_bytes(np.ones(240, dtype=np.int16)))
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 24000, 240)


class _Conn:
    """A gpt-live that speaks once the question has started to arrive."""

    def __init__(self, voice_frames=6, fail=None):
        self.appended, self.started_with, self.closed = [], None, False
        self.voice_frames, self.fail = voice_frames, fail
        self.session = NS(start=self._start, close=self._close,
                          input_audio=NS(append=self._append))

    async def _start(self, session):
        self.started_with = session

    async def _close(self):
        self.closed = True

    async def _append(self, audio):
        self.appended.append(base64.b64decode(audio))

    def __aiter__(self):
        return self._events()

    async def _events(self):
        yield NS(type="session.started")
        if self.fail:
            yield NS(type="error", error=NS(message=self.fail))
            return
        sent = 0
        while True:
            await asyncio.sleep(0.002)
            if len(self.appended) < 3:
                continue
            sent += 1
            loud = sent <= self.voice_frames
            pcm = (np.full(2400, 8000 if loud else 0, dtype=np.int16)).tobytes()
            yield NS(type="session.output_audio.delta",
                     delta=base64.b64encode(pcm).decode())


def _run(conn, **kw):
    return asyncio.run(vs.converse(conn, "voice-x", "Hello there.",
                                   np.zeros(4800, dtype=np.int16),
                                   tick=0.005, idle=0.05, deadline=0.3, **kw))


def test_converse_records_the_voice_until_it_goes_quiet():
    conn = _Conn()
    pcm = _run(conn)
    assert conn.started_with["audio"]["output"]["voice"] == "voice-x"
    assert conn.started_with["delegation"] == {"type": "client"}
    assert "Hello there." in conn.started_with["instructions"]
    assert conn.closed
    assert np.count_nonzero(pcm) == 6 * 2400, "only the audible frames are the sample"
    assert conn.appended and all(len(b) == BLOCK for b in conn.appended), \
        "the input must be whole blocks, tick after tick"
    assert len(conn.appended) > 3 + 10, "the stream stopped while the voice was answering"


def test_converse_error_and_mute_session_raise_with_the_cause():
    for conn, text in ((_Conn(fail="bad key"), "bad key"), (_Conn(voice_frames=0), "nothing")):
        try:
            _run(conn)
        except RuntimeError as e:
            assert text in str(e), e
        else:
            raise AssertionError("no error")


def test_cache_second_call_makes_no_session(monkeypatch_calls=[]):
    calls = []

    def fake_record(voice, lang):
        calls.append(voice)
        return np.full(4800, 3000, dtype=np.int16)

    real = vs._record
    vs._record = fake_record
    try:
        p1, cached1 = vs.voice_sample("sample-voice", "fr")
        p2, cached2 = vs.voice_sample("sample-voice", "fr")
    finally:
        vs._record = real
    assert p1 == p2 and p1.exists() and p1.parent.name == "voices"
    assert p1.name == "sample-voice-fr.wav"
    assert (cached1, cached2) == (False, True) and calls == ["sample-voice"]
    assert not list(p1.parent.glob("*.tmp")), "the write was not atomic"


def test_voice_name_cannot_escape_the_cache():
    try:
        vs.voice_sample("../../etc/x", "en")
    except ValueError:
        return
    raise AssertionError("a path was accepted as a voice name")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
