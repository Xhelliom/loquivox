"""
The companion's memory of what it must not repeat: stored, applied to a source
before it can wake anything, and edited without a restart.

No microphone, no GTK, no network — a source the core has never heard of and a
judge that answers from a table:

    PYTHONPATH=src python tests/test_memory.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.managers.chat as chat_module  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)

from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.services import memory, plugins  # noqa: E402
from loquivox.services.plugins import Plugin  # noqa: E402
from loquivox.state import STATE  # noqa: E402

FEED: list = []
ASKED: list = []


def _watch(put, stop):
    sent = 0
    while not stop.is_set():
        while sent < len(FEED):
            put({"role": "system", "content": FEED[sent]})
            sent += 1
        stop.wait(0.01)


def _judge(rules, source, content):
    """Stands in for the model: a rule covers a message that shares its first word."""
    ASKED.append((source, content))
    return [i for i, r in enumerate(rules) if r["text"].split()[0].lower() in content.lower()]


class _Presence:
    def release(self): pass
    def resume(self): pass
    def start(self): pass
    def take_tail(self): return []


class _Bubble:
    def set_state(self, s): pass
    def set_level(self, x): pass
    def pulse(self): pass


def _wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _reset():
    memory.FILE.unlink(missing_ok=True)
    memory.JUDGE = _judge
    FEED.clear()
    ASKED.clear()


def test_a_rule_is_stored_in_a_readable_file_and_survives_a_restart():
    _reset()
    rule = memory.add("Copilot reviews, no need to repeat them", source="beacon")
    text = memory.FILE.read_text(encoding="utf-8")
    assert "Copilot reviews" in text and 'source = "beacon"' in text and "date =" in text
    memory._cache.clear()           # a new process reads the file, nothing else
    assert [r["text"] for r in memory.rules()] == ["Copilot reviews, no need to repeat them"]
    assert memory.rules()[0]["id"] == rule["id"] and memory.rules()[0]["dropped"] == 0
    print("✓ rule stored in memory.toml, read back")


def test_the_tool_saves_and_confirms_out_loud():
    _reset()
    out = memory.PLUGIN.run({"rule": "Copilot reviews", "source": ""})
    assert [r["text"] for r in memory.rules()] == ["Copilot reviews"]
    assert memory.rules()[0]["source"] == ""
    msg = memory.PLUGIN.message({}, out)
    assert msg["role"] == "system" and "Copilot reviews" in msg["content"]
    assert "one short spoken sentence" in msg["content"].lower()
    assert memory.PLUGIN.run({"rule": "  "}) == "", "an empty rule was stored"
    assert memory.PLUGIN.chat_only and memory.PLUGIN.is_tool
    print("✓ tool saves a rule and asks for a spoken confirmation")


def test_a_covered_source_message_wakes_nothing_and_the_others_pass():
    _reset()
    memory.add("Copilot reviews", source="")
    plugins.register(Plugin(name="beacon", watch=_watch))
    plugins.start_sources()
    STATE.presence_mode = "active"
    STATE.talk_active = STATE.companion_session = STATE.recording = False
    said: list = []

    def converse(session, prefix=()):
        while (found := session.desks.take()) is not None:
            said.append(found[1]["content"])
    c = comp.Companion(_Presence(), _Bubble(), look=lambda s: None, converse=converse)
    try:
        FEED.append("Copilot reviews: 3 comments on PR 12")
        assert _wait(lambda: len(ASKED) == 1), "the filter never saw the message"
        time.sleep(0.1)
        assert not plugins.sources_waiting(), "a discarded message is waiting"
        assert not c.announce_if_due(), "a discarded message opened a session"
        assert said == []
        assert memory.rules()[0]["dropped"] == 1, "the rule did not count it"

        FEED.append("Deploy failed on staging")
        assert _wait(plugins.sources_waiting), "an uncovered message was swallowed"
        assert c.announce_if_due() and "Deploy failed" in said[0]
    finally:
        plugins.stop_sources()
        plugins._REGISTRY.pop("beacon", None)
    print("✓ covered message: no wake, no session, counted; others pass")


def test_a_scoped_rule_is_not_even_asked_about_another_source():
    _reset()
    memory.add("Copilot reviews", source="beacon")
    assert not memory.discards("elsewhere", "Copilot reviews again")
    assert ASKED == [], "the judge was called for a rule that cannot apply"
    assert memory.discards("beacon", "Copilot reviews again")
    print("✓ a scope narrows what the judge is asked")


def test_no_rules_means_no_judge_and_a_failing_judge_lets_through():
    _reset()
    assert not memory.discards("beacon", "anything") and ASKED == []
    memory.add("x")
    memory.JUDGE = lambda *a: 1 / 0
    assert not memory.discards("beacon", "x"), "a broken judge swallowed a message"
    print("✓ no rules: no call; a failing judge fails open")


def test_editing_in_settings_takes_effect_at_once():
    _reset()
    a = memory.add("Copilot reviews")
    memory.add("Dependabot")
    memory.discards("beacon", "Copilot again")
    # the Settings writer: keep id, change text; drop one; add one
    memory.replace([(a["id"], "Review bots"), (None, "Nightly build")])
    assert [r["text"] for r in memory.rules()] == ["Review bots", "Nightly build"]
    assert memory.rules()[0]["dropped"] == 1, "editing a rule lost its count"
    assert not memory.discards("beacon", "Dependabot bump"), "a deleted rule still applied"
    print("✓ edit / delete / add apply without restart, counts kept")


def test_the_conversation_model_is_told_the_rules():
    _reset()
    from loquivox.services.talk import TalkSession
    assert "Copilot" not in TalkSession(write=False).system_prompt()
    memory.add("Copilot reviews")
    prompt = TalkSession(write=False).system_prompt()
    assert "Copilot reviews" in prompt, "the model does not know what not to bring up"
    print("✓ rules reach the conversation prompt")


def test_the_core_names_no_source():
    plug = (ROOT / "src/loquivox/services/plugins.py").read_text(encoding="utf-8")
    assert "memory.discards" in plug
    for name in ("memory.py", "../handlers/companion.py"):
        src = (ROOT / "src/loquivox/services" / name).read_text(encoding="utf-8").lower()
        assert not re.search(r"\bboard\b|copilot", src), name
    print("✓ filter and core name no source")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
