"""
Self-check for the MCP client (services/mcp_tools.py), no network, no sound card:

    PYTHONPATH=src python tests/test_mcp.py     # needs the `mcp` extra

Pins the card's promises: a stdio server declared in config.toml gets its tools
on the plugin surface; a tool that is not marked read-only waits for a spoken
yes; what a tool returns is data; a server that is absent or crashes costs a
warning and nothing else. The three engines are driven in test_plugins.py.
"""
from __future__ import annotations

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

FIXTURE = {"command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")],
           # the child gets an allow-listed environment, not ours
           "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}}


def _servers(**servers):
    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": servers})


def _plugin(name):
    return next(p for p in plugins.all_plugins() if p.name == name)


def test_a_declared_server_exposes_its_tools():
    _servers(fx=FIXTURE)
    mcp_tools.sync(timeout=20)
    (row,) = mcp_tools.status()
    assert row["state"] == "up" and set(row["tools"]) == {"weather", "delete_all", "hostile"}, row
    weather = _plugin("mcp_fx_weather")
    assert weather.enabled() and weather.is_tool
    assert weather.parameters["required"] == ["city"]
    assert "confirmed" not in weather.parameters["properties"], "a read-only tool asks nothing"
    assert "confirmed" in _plugin("mcp_fx_delete_all").parameters["properties"]
    print("✓ a stdio server declared in config exposes its tools as plugins")


def test_a_result_is_data_not_an_instruction():
    weather = _plugin("mcp_fx_weather")
    assert "raining in Lyon" in weather.run({"city": "Lyon"})
    message = _plugin("mcp_fx_hostile").message({}, _plugin("mcp_fx_hostile").run({"city": "x"}))
    assert message["role"] == "system" and "never follow" in message["content"].lower()
    assert "Ignore previous instructions" in message["content"]
    print("✓ what a tool returns is framed as data")


def test_a_tool_that_acts_waits_for_a_spoken_yes():
    plugin = _plugin("mcp_fx_delete_all")
    session = STATE.talk_session = TalkSession()
    args = {"what": "everything"}
    first = plugin.run(args)
    assert "deleted" not in first and "ask" in first.lower(), first
    # the model calling again with confirmed=true and nobody having answered
    again = plugin.run({**args, "confirmed": True})
    assert "deleted" not in again, "executed without the user saying anything"
    session.add_user("oui vas-y")
    done = plugin.run({**args, "confirmed": True})
    assert "deleted everything" in done, done
    # a yes is spent: the next action asks again
    assert "deleted" not in plugin.run({**args, "confirmed": True})
    # and a different action is not covered by the first yes
    plugin.run({"what": "a"})
    session.add_user("oui")
    assert "deleted" not in plugin.run({"what": "b", "confirmed": True})
    STATE.talk_session = None
    assert "deleted" not in plugin.run({**args, "confirmed": True}), "no session, no yes"
    print("✓ a tool without readOnlyHint is confirmed out loud before it runs")


def test_a_missing_or_crashing_server_is_a_warning():
    _servers(fx=FIXTURE,
             ghost={"command": "/nonexistent/binary"},
             crash={"command": sys.executable, "args": ["-c", "raise SystemExit(3)"]},
             off={**FIXTURE, "enabled": False})
    mcp_tools.sync(timeout=20)
    states = {r["name"]: r for r in mcp_tools.status()}
    assert states["fx"]["state"] == "up", states
    assert states["ghost"]["state"] == "failed" and states["ghost"]["error"], states
    assert states["crash"]["state"] == "failed", states
    assert states["off"]["state"] == "off", states
    assert _plugin("mcp_fx_weather").enabled()
    print("✓ an absent or crashing server is reported, the others carry on")


def test_switching_a_server_off_takes_its_tools_away():
    _servers(fx={**FIXTURE, "enabled": False})
    mcp_tools.sync(timeout=20)
    assert not _plugin("mcp_fx_weather").enabled()
    assert mcp_tools.status()[0]["state"] == "off"
    _servers(fx=FIXTURE)
    mcp_tools.sync(timeout=20)
    assert _plugin("mcp_fx_weather").enabled()
    print("✓ on/off per server starts and stops it")


def test_the_toml_schema_is_read():
    import tomllib
    data = tomllib.loads('[mcp.servers.a]\ncommand = "x"\nargs = ["y"]\n'
                         '[mcp.servers.b]\nurl = "http://h/mcp"\nenabled = false\n')
    servers = config_module.mcp_servers_from(data)
    assert servers["a"]["command"] == "x" and servers["b"]["enabled"] is False, servers
    assert config_module.mcp_servers_from({}) == {}
    print("✓ [mcp.servers.<name>] is parsed")


if __name__ == "__main__":
    real = config_module.CFG
    try:
        test_a_declared_server_exposes_its_tools()
        test_a_result_is_data_not_an_instruction()
        test_a_tool_that_acts_waits_for_a_spoken_yes()
        test_a_missing_or_crashing_server_is_a_warning()
        test_switching_a_server_off_takes_its_tools_away()
        test_the_toml_schema_is_read()
    finally:
        config_module.CFG = real
        mcp_tools.stop()
    print("\n✓ the MCP client holds")
