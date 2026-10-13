"""
Presence listener: mic held outside any session, wake word local, release/resume.

No real microphone, no openwakeword: both are injected.

    python tests/test_presence.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.services import presence  # noqa: E402


class _Stream:
    """Stands in for sd.InputStream; remembers every instance opened."""
    opened: list = []

    def __init__(self, callback, **_):
        self.callback, self.active = callback, False
        _Stream.opened.append(self)

    def start(self): self.active = True
    def stop(self): self.active = False
    def close(self): self.active = False

    def push(self, level=0.0):
        self.callback((np.ones((presence.BLOCK, 1), "float32") * level), 0, None, None)


class _Model:
    def __init__(self): self.score, self.resets = 0.0, 0
    def predict(self, frame):
        assert frame.dtype == np.int16 and len(frame) == presence.BLOCK
        return {"hey_jarvis": self.score}
    def reset(self): self.resets += 1


def _make(woken, model=None):
    _Stream.opened = []
    p = presence.Presence(lambda: woken.append(1), stream_factory=_Stream, model=model)
    return p


def test_wake_fires_on_callback():
    woken, m = [], _Model()
    p = _make(woken, m)
    p.start()
    s = _Stream.opened[-1]
    s.push(0.01)
    time.sleep(0.05)
    assert not woken
    m.score = 0.9
    s.push(0.01)
    time.sleep(0.2)
    assert woken == [1]
    s.push(0.01)                      # refractory: no second trigger
    time.sleep(0.05)
    assert woken == [1]
    p.stop()


def test_release_frees_device_and_resume_reopens():
    woken, m = [], _Model()
    p = _make(woken, m)
    p.start()
    first = _Stream.opened[-1]
    assert first.active
    before = m.resets
    p.release()
    assert not first.active and not p.listening
    p.resume()
    assert _Stream.opened[-1] is not first and _Stream.opened[-1].active
    assert m.resets == before + 1              # a held-over wake word must not fire on resume
    p.stop()
    assert not _Stream.opened[-1].active


def test_without_openwakeword_starts_and_warns_once(capsys=None):
    import io, contextlib
    out = io.StringIO()
    sys.modules["openwakeword"] = sys.modules["openwakeword.model"] = None  # import fails
    with contextlib.redirect_stdout(out):
        p = _make([], None)
        p.start(); p.release(); p.resume(); p.stop()
        p.start(); p.stop()                    # second start: _warned must keep it silent
    assert out.getvalue().count("Presence: openwakeword unavailable") == 1
    assert _Stream.opened[-1] is not None  # mic still opened


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
