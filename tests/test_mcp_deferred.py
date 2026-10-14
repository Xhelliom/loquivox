"""
Self-check for deferred MCP tools (services/mcp_tools.py + services/plugins.py), no network:

    PYTHONPATH=src python tests/test_mcp_deferred.py     # needs the `mcp` extra

Dozens of tools slow the voice and lose the model, so only the internal
plugins, the tools a server marks ``always`` and ``find_tools`` are offered at
the start; the rest wait until ``find_tools`` (or a source that speaks and
declares the server as its own) loads them — in the cascade, Realtime and both
Live delegations. Prints the weight of the tool context before and after.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import mcp  # noqa: F401
except ImportError:
    print("⏭  mcp extra not installed — skipped")
    raise SystemExit(0)

import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as chat_module  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)

from loquivox.services import mcp_tools, plugins  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402


def _fixture(bulk: int = 0, **extra):
    return {"command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")],
            "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", ""), "BULK": str(bulk)}, **extra}


def _servers(**servers):
    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": servers})
    mcp_tools.sync(timeout=20)


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _offered(desks) -> set:
    return {t["function"]["name"] for t in desks.chat_tools}


def _declare():
    _servers(fx=_fixture(description="Weather lookups and clean-up", always=["weather"]),
             fy=_fixture(bulk=20, description="Records of the Y service"),
             fz=_fixture(description="Z"))


def _find(session, **args):
    """What the model's find_tools call does, on its desk."""
    desk = session.desks.get("find_tools")
    assert desk is not None, "find_tools is not offered"
    desk.ask(json.dumps(args))
    assert _wait(desk.ready), "find_tools never answered"
    return session.desks.take()


def test_only_the_always_tools_and_find_tools_are_offered():
    _declare()
    offered = _offered(TalkSession().desks)
    mine = {n for n in offered if n.startswith("mcp_")}
    assert mine == {"mcp_fx_weather"}, mine
    assert "find_tools" in offered, offered
    finder = next(t for t in TalkSession().desks.chat_tools
                  if t["function"]["name"] == "find_tools")["function"]
    for line in ("fx", "Weather lookups and clean-up", "fy", "Records of the Y service", "fz"):
        assert line in finder["description"], (line, finder["description"])
    print("✓ 3 servers: only the `always` tools and find_tools at the start")


def test_find_tools_by_server_and_by_query():
    session = STATE.talk_session = TalkSession()
    _, message = _find(session, server="fz")
    assert {"mcp_fz_weather", "mcp_fz_delete_all", "mcp_fz_hostile"} <= _offered(session.desks)
    assert "mcp_fz_weather" in message["content"], "the model is not told what it can call"
    assert not any(n.startswith("mcp_fy_") for n in _offered(session.desks))
    _find(session, query="records")
    assert "mcp_fy_bulk_3" in _offered(session.desks), "a query matches the description of a tool"
    assert session.desks.get("mcp_fy_bulk_3") is not None
    # a second session starts from scratch: what was loaded is the session's
    assert "mcp_fz_weather" not in _offered(TalkSession().desks)
    STATE.talk_session = None
    print("✓ find_tools loads by server or by query, for this session only")


def test_a_deferred_tool_is_not_callable_before_it_is_loaded():
    session = TalkSession()
    assert session.desks.get("mcp_fy_bulk_0") is None, "a tool never offered was routed to"
    print("✓ a call to a tool that was never loaded is dropped")


def test_the_cascade_calls_it_on_the_next_turn():
    import loquivox.services.talk as talk_module

    session = STATE.talk_session = TalkSession()
    seen = []

    def _fake(messages, on_delta=None, tools=None, tool_calls_out=None):
        names = [t["function"]["name"] for t in (tools or [])]
        if not any(m.get("role") == "tool" for m in messages):  # a turn's first pass
            seen.append(names)
            if "mcp_fz_weather" not in names:
                tool_calls_out.append({"id": "c1", "name": "find_tools",
                                       "arguments": '{"server": "fz"}'})
        return "ok"

    real = talk_module.AIService.complete
    talk_module.AIService.complete = staticmethod(_fake)
    try:
        session.reply("quelle météo à Lyon ?")
        assert _wait(session.desks.ready)
        session.reply("alors ?")
    finally:
        talk_module.AIService.complete = real
        STATE.talk_session = None
    assert "mcp_fz_weather" not in seen[0] and "mcp_fz_weather" in seen[1], seen
    print("✓ cascade: the loaded tool is on the table at the next turn")


def test_realtime_pushes_the_new_tools_to_the_session():
    import asyncio

    from loquivox.services.realtime_talk import RealtimeTalk

    session = STATE.talk_session = TalkSession()
    talk = RealtimeTalk(session)
    sent = []
    updates = []
    talk._conn = type("C", (), {"session": type("S", (), {
        "update": staticmethod(lambda session: updates.append(session) or None)})})()
    real = asyncio.run_coroutine_threadsafe
    asyncio.run_coroutine_threadsafe = lambda coro, loop: sent.append(coro)
    try:
        assert not talk.push_tools(), "nothing was loaded, nothing to push"
        _find(session, server="fz")
        assert talk.push_tools(), "the loaded tools never reached the session"
        assert not talk.push_tools(), "the same list was pushed twice"
    finally:
        asyncio.run_coroutine_threadsafe = real
        STATE.talk_session = None
    assert any(t["name"] == "mcp_fz_weather" for t in updates[-1]["tools"]), updates
    print("✓ Realtime: session.update carries the loaded tools")


def test_always_as_a_string_is_a_one_item_list_with_exact_names():
    for always in ("weather", ["weather"]):
        _servers(fx=_fixture(always=always))
        names = {n for n in _offered(TalkSession().desks) if n.startswith("mcp_")}
        assert names == {"mcp_fx_weather"}, (always, names)   # not weather_alerts
    print("✓ always = \"x\" equals [\"x\"], exact match")


def test_live_client_delegation_reads_the_same_list_and_responses_gets_everything():
    from loquivox.services.live_talk import LiveTalk

    session = TalkSession()
    base = _offered(session.desks)                   # client mode: chat_tools, per call
    _find(STATE.__setattr__("talk_session", session) or session, server="fz")
    STATE.talk_session = None
    assert _offered(session.desks) - base >= {"mcp_fz_weather"}
    # responses: the delegation's tools are fixed when the session opens
    talk = LiveTalk(TalkSession())
    talk._delegation = "responses"
    names = {t.get("name") for t in talk._config()["delegation"]["responses"]["tools"]}
    assert {"mcp_fy_bulk_0", "mcp_fz_weather"} <= names, names
    print("✓ Live: client reads the live list, responses declares everything up front")


def test_a_source_that_spoke_loads_its_server():
    session = TalkSession()
    plugins.register(plugins.Plugin(name="pinger", loads="fy", watch=lambda put, stop: None))
    try:
        session = TalkSession()
        assert not any(n.startswith("mcp_fy_") for n in _offered(session.desks))
        desk = session.desks._desks["pinger"]
        desk.put({"role": "system", "content": "a card moved"})
        found = session.desks.take()
        assert found is not None and found[0].name == "pinger"
        assert "mcp_fy_bulk_0" in _offered(session.desks), "the linked server was not preloaded"
        assert "mcp_fz_weather" not in _offered(session.desks), "another server was loaded too"
    finally:
        plugins._REGISTRY.pop("pinger", None)
    from loquivox.services import board
    assert board.PLUGIN.loads == "board"
    print("✓ a source that just spoke loads its linked server (board → `board`)")


def test_the_weight_of_the_tool_context():
    _declare()
    after = len(json.dumps(TalkSession().desks.chat_tools))
    _servers(fx=_fixture(always=["*"]), fy=_fixture(bulk=20, always=["*"]),
             fz=_fixture(always=["*"]))
    before = len(json.dumps(TalkSession().desks.chat_tools))
    assert after < before / 2, (before, after)
    print(f"✓ tool context, 3 servers: {before} chars (~{before // 4} tokens) before, "
          f"{after} chars (~{after // 4} tokens) after")


if __name__ == "__main__":
    real = config_module.CFG
    try:
        test_only_the_always_tools_and_find_tools_are_offered()
        test_find_tools_by_server_and_by_query()
        test_a_deferred_tool_is_not_callable_before_it_is_loaded()
        test_the_cascade_calls_it_on_the_next_turn()
        test_realtime_pushes_the_new_tools_to_the_session()
        test_live_client_delegation_reads_the_same_list_and_responses_gets_everything()
        test_a_source_that_spoke_loads_its_server()
        test_the_weight_of_the_tool_context()
        test_always_as_a_string_is_a_one_item_list_with_exact_names()
    finally:
        config_module.CFG = real
        mcp_tools.stop()
    print("\n✓ deferred MCP tools hold")
