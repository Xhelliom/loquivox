"""
A companion session opened soon after the previous one resumes its last turns
(``[companion] resume_min``); later, or with 0, it starts empty. Resumed turns
are never the user's (the spoken yes), never re-extracted by the end-of-session
memory, and the history is bounded. No network, no microphone:

    python tests/test_companion_resume.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.services import memory, talk  # noqa: E402
from loquivox.services.live_talk import LiveTalk  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402

CLOCK = [1000.0]
comp.time = type("T", (), {"monotonic": staticmethod(lambda: CLOCK[0]),
                           "sleep": staticmethod(lambda s: None)})
memory.remember_async = lambda session: REMEMBERED.append(list(session.turns))
REMEMBERED: list = []


class _Presence:
    def release(self): pass
    def resume(self): pass
    def start(self): pass
    def take_tail(self): return []


class _Bubble:
    def set_state(self, s): pass
    def set_level(self, x): pass
    def pulse(self): pass


def _cfg(minutes):
    config_module.CFG = dataclasses.replace(config_module.CFG, COMPANION_RESUME_MIN=minutes)


def _make():
    seen = []

    def converse(session, prefix=()):
        seen.append(session)
        if len(seen) == 1:      # the first session talks about Blender
            session.turns += [{"role": "user", "content": "comment extruder ?"},
                              {"role": "assistant", "content": "Touche E."},
                              {"role": "system", "content": "plugin answer"}]
    c = comp.Companion(_Presence(), _Bubble(), look=lambda s: None, converse=converse)
    STATE.presence_mode = "active"
    return c, seen


def test_resumes_within_the_window():
    _cfg(10.0)
    c, seen = _make()
    c.wake()
    CLOCK[0] += 60
    c.wake()
    second = seen[1]
    assert [t["content"] for t in second.resumed] == ["comment extruder ?", "Touche E."]
    assert second.user_turns == 0 and second.turns == []
    # the model is handed them ahead of the new turns, as user/assistant only
    second.add_user("et du coup ?")
    assert [t["content"] for t in second._spoken_to()] == [
        "comment extruder ?", "Touche E.", "et du coup ?"]
    print("✓ a session opened within resume_min resumes the last turns")


def test_starts_empty_beyond_the_window_or_with_zero():
    _cfg(10.0)
    c, seen = _make()
    c.wake()
    CLOCK[0] += 11 * 60
    c.wake()
    assert seen[1].resumed == []
    _cfg(0.0)
    c, seen = _make()
    c.wake()
    CLOCK[0] += 5
    c.wake()
    assert seen[1].resumed == []
    print("✓ beyond resume_min, or with 0, it starts empty")


def test_a_yes_from_before_confirms_nothing():
    from loquivox.services import mcp_tools
    _cfg(10.0)
    s = TalkSession(write=False)
    s.resumed = [{"role": "user", "content": "oui vas-y"}]
    assert s.user_turns == 0
    # the rule reads user_turns: nothing was said in *this* session
    assert not mcp_tools.awaiting(s) if hasattr(mcp_tools, "awaiting") else True
    print("✓ resumed turns are not user turns")


def test_history_is_bounded_and_keeps_the_latest():
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i:03d}" + "x" * 96}
             for i in range(300)]
    out = talk.resume_history(turns)
    assert len(out) <= talk.RESUME_ITEMS
    assert sum(len(t["content"]) for t in out) <= talk.RESUME_CHARS
    assert out[-1] is turns[-1] and out == turns[-len(out):]
    assert talk.resume_history([{"role": "system", "content": "x"}]) == []
    print("✓ the history is bounded, most recent kept")


def test_live_receives_the_history_as_input_items():
    _cfg(10.0)
    s = TalkSession(write=False)
    s.resumed = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    conf = LiveTalk(s)._config()
    assert conf["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "q"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a"}]}]
    assert "input" not in LiveTalk(TalkSession(write=False))._config()
    print("✓ Live opens with the history as input items")


def test_end_of_session_memory_does_not_see_resumed_turns():
    _cfg(10.0)
    REMEMBERED.clear()
    c, seen = _make()
    c.wake()
    CLOCK[0] += 30
    c.wake()
    assert REMEMBERED[1] == [], REMEMBERED[1]      # nothing new was said
    print("✓ resumed turns are not extracted twice")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
