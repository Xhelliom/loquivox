"""
Self-check for the look plugin (services/look.py):

    PYTHONPATH=src python tests/test_look.py

"Regarde ça" is the only thing that captures: the plugin is offered to both
engines, its run re-captures through the screen worker for the session that
holds the line, and it does nothing without a session or a description.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.handlers.mode import ModeHandler  # noqa: E402
from loquivox.services import look, plugins  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402


def test_offered_to_both_engines_in_chat_only():
    plugins.all_plugins()
    session = TalkSession(write=False)
    chat = {t["function"]["name"] for t in session.desks.chat_tools}
    realtime = {t["name"] for t in session.desks.realtime_tools}
    assert "look_at_screen" in chat and "look_at_screen" in realtime, (chat, realtime)
    briefing = {t["function"]["name"] for t in TalkSession(write=True).desks.chat_tools}
    assert "look_at_screen" not in briefing, "a briefing has the S key already"
    print("✓ look_at_screen is offered to chat and Realtime/Live, not to a briefing")


def test_run_recaptures_for_the_current_session():
    calls = []
    original = ModeHandler._talk_screen_worker
    ModeHandler._talk_screen_worker = staticmethod(
        lambda s: calls.append(s) or "Un terminal avec une erreur.")
    session = TalkSession(write=False)
    STATE.talk_session = session
    try:
        assert not calls, "captured without being asked"
        assert look._run({}) == "Un terminal avec une erreur."
        assert calls == [session], calls
        STATE.talk_session = None
        assert look._run({}) == "" and len(calls) == 1, "captured with no session"
        ModeHandler._talk_screen_worker = staticmethod(lambda s: "")
        STATE.talk_session = session
        assert look._run({}) == ""
    finally:
        ModeHandler._talk_screen_worker = original
        STATE.talk_session = None
    msg = look.result_message({}, "Un terminal.")
    assert msg["role"] == "system" and "Un terminal." in msg["content"]
    print("✓ run captures once per call, for the session on the line")


def test_the_config_key_gates_it():
    cfg = config_module.CFG
    try:
        config_module.CFG = type(cfg)(**{**cfg.__dict__, "TALK_LOOK_TOOL": False})
        assert not look.PLUGIN.enabled()
    finally:
        config_module.CFG = cfg
    print("✓ TALK_LOOK_TOOL turns it off")


if __name__ == "__main__":
    test_offered_to_both_engines_in_chat_only()
    test_run_recaptures_for_the_current_session()
    test_the_config_key_gates_it()
