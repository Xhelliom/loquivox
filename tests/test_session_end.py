"""
A companion / chat session is not capped by TALK_MAX_TURNS (a briefing's limit),
every session end prints its reason, and a delegation arriving after close() is
not run.

    PYTHONPATH=src python tests/test_session_end.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import contextlib
import io
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
import loquivox.handlers.mode as mode_module  # noqa: E402
from loquivox.handlers.mode import ModeHandler  # noqa: E402
from loquivox.services.live_talk import LiveTalk  # noqa: E402
from loquivox.state import STATE  # noqa: E402


class Talk:
    error = None
    done = False

    def __init__(self, session):
        self.session, self.ticks = session, 0

    def start(self): pass
    def close(self): pass
    def tick(self):
        self.ticks += 1
        self.session.user_turns = 31          # past the briefing limit
        if self.ticks >= 3:
            self.done = True                  # the test's own way out
    def push_context(self): return False
    def push_result(self): pass
    def finished_speaking(self): return True
    def idle_for(self): return 0.0


class Keys:
    grabbed = True
    @contextlib.contextmanager
    def exclusive(self, grab=True): yield


def run(write: bool) -> tuple:
    session = SimpleNamespace(write=write, user_turns=0, desks=SimpleNamespace(coming=lambda: False))
    talk = Talk(session)
    mode_module.CFG = config_module.CFG = replace(config_module.CFG, TALK_MAX_TURNS=30)
    ModeHandler._talk_wait = staticmethod(lambda *a, **k: None)
    ModeHandler._show_talk_overlay = staticmethod(lambda *a, **k: None)
    STATE.companion_session = not write
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ModeHandler._talk_converse_server(session, Keys(), lambda s: talk, "Live")
    return talk.ticks, out.getvalue()


def test_companion_is_not_capped():
    ticks, out = run(write=False)
    assert ticks == 3, ticks
    assert "session ended: briefing over" in out, out
    print("✓ a session without write goes past TALK_MAX_TURNS")


def test_briefing_is_capped_and_says_why():
    ticks, out = run(write=True)
    assert ticks == 1 and "session ended: max_turns" in out, (ticks, out)
    print("✓ a briefing still stops at TALK_MAX_TURNS, with its reason")


def test_delegation_after_close_is_dropped():
    talk = LiveTalk.__new__(LiveTalk)
    talk._stop, talk._seen = True, set()
    import threading
    talk._lock = threading.Lock()
    talk._on_delegation(SimpleNamespace(delegation=SimpleNamespace(id="d1", target="client")))
    assert not talk._seen, "a closed session must not even register the delegation"
    print("✓ a delegation after close() is not started")


if __name__ == "__main__":
    test_companion_is_not_capped()
    test_briefing_is_capped_and_says_why()
    test_delegation_after_close_is_dropped()
