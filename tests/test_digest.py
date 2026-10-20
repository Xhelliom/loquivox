"""
Back from silence: what was held is summarised in ONE session, not said one by one.

A source the core has never heard of; no microphone, no GTK, no network:

    PYTHONPATH=src python tests/test_digest.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import dataclasses
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as chat_module  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)

from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.services import memory, plugins  # noqa: E402
from loquivox.services.plugins import Plugin  # noqa: E402
from loquivox.state import STATE  # noqa: E402

STALE: set = set()
FEED: list = []


def _watch(put, stop):
    sent = 0
    while not stop.is_set():
        while sent < len(FEED):
            put({"role": "system", "content": FEED[sent]})
            sent += 1
        stop.wait(0.01)


class _Presence:
    def release(self): pass
    def resume(self): pass
    def start(self): pass
    def take_tail(self): return []


class _Bubble:
    def set_state(self, s): pass
    def set_level(self, x): pass
    def pulse(self): pass


def _setup(digest_max=5):
    FEED.clear()
    STALE.clear()
    config_module.CFG = dataclasses.replace(config_module.CFG, COMPANION_DIGEST_MAX=digest_max,
                                            COMPANION_SOURCE_SILENCE_SEC=0.0)
    plugins.register(Plugin(
        name="beacon", watch=_watch,
        still_true=lambda m: m["content"] not in STALE,
        urgency=lambda m: 1 if m["content"].startswith("QUESTION") else 0))
    plugins.start_sources()
    said: list = []

    def converse(session, prefix=()):
        while (found := session.desks.take()) is not None:
            said.append(found[1]["content"])
    c = comp.Companion(_Presence(), _Bubble(), look=lambda s: None, converse=converse)
    STATE.presence_mode = "silent"
    STATE.talk_active = STATE.companion_session = STATE.recording = False
    return c, said


def _teardown():
    plugins.stop_sources()
    plugins._REGISTRY.pop("beacon", None)


def _feed(*texts):
    FEED.extend(texts)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if sum(d._results.qsize() for d in plugins._standing.values()) >= len(FEED):
            return
        time.sleep(0.01)
    raise AssertionError("the source never spoke")


def test_three_held_messages_become_one_session_urgent_first():
    c, said = _setup()
    try:
        _feed("done A", "QUESTION B", "done C")
        assert not c.announce_if_due(), "silence must keep everything queued"
        STATE.presence_mode = "active"
        assert c.announce_if_due()
        assert len(said) == 1, said
        text = said[0]
        assert all(t in text for t in ("done A", "QUESTION B", "done C")), text
        assert text.index("QUESTION B") < text.index("done A"), "the question comes first"
        assert not c.announce_if_due(), "said twice"
    finally:
        _teardown()
    print("✓ three held messages: one session, the question first")


def test_a_message_gone_stale_is_not_in_the_summary():
    c, said = _setup()
    try:
        _feed("done A", "done B", "done C")
        STALE.add("done B")
        STATE.presence_mode = "active"
        assert c.announce_if_due()
        assert len(said) == 1 and "done B" not in said[0] and "done A" in said[0], said
    finally:
        _teardown()
    print("✓ a message that stopped being true is dropped from the summary")


def test_a_memory_rule_applies_to_the_summary():
    c, said = _setup()
    real = memory.discards
    try:
        _feed("done A", "noise B", "done C")
        memory.discards = lambda source, content: content == "noise B"
        STATE.presence_mode = "active"
        assert c.announce_if_due()
        assert "noise B" not in said[0] and "done A" in said[0], said
    finally:
        memory.discards = real
        _teardown()
    print("✓ « stop repeating » rules apply to the summary")


def test_nothing_held_nothing_opens():
    c, said = _setup()
    try:
        STATE.presence_mode = "active"
        assert not c.announce_if_due() and not said
        _feed("x")
        STALE.add("x")      # the only message is stale: no session either
        assert not c.announce_if_due() and not said
    finally:
        _teardown()
    print("✓ empty queue: no session")


def test_over_the_cap_says_the_count_and_the_three_most_important():
    c, said = _setup(digest_max=4)
    try:
        _feed("done 1", "done 2", "done 3", "QUESTION 4", "done 5", "QUESTION 6")
        STATE.presence_mode = "active"
        assert c.announce_if_due()
        text = said[0]
        assert "6" in text and "QUESTION 4" in text and "QUESTION 6" in text, text
        assert "done 1" in text and "done 2" not in text, text   # urgency ties keep arrival order
    finally:
        _teardown()
    print("✓ over the cap: the count and the top three")


def test_one_message_is_still_said_on_its_own():
    c, said = _setup()
    try:
        _feed("done A")
        STATE.presence_mode = "active"
        assert c.announce_if_due()
        assert "done A" in said[0] and "ONE short spoken sentence" in said[0], said
    finally:
        _teardown()
    print("✓ a single message keeps the usual one-sentence frame")


if __name__ == "__main__":
    test_three_held_messages_become_one_session_urgent_first()
    test_a_message_gone_stale_is_not_in_the_summary()
    test_a_memory_rule_applies_to_the_summary()
    test_nothing_held_nothing_opens()
    test_over_the_cap_says_the_count_and_the_three_most_important()
    test_one_message_is_still_said_on_its_own()
