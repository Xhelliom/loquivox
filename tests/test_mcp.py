"""
Self-check for the MCP client (services/mcp_tools.py), no network, no sound card:

    PYTHONPATH=src python tests/test_mcp.py     # needs the `mcp` extra

Pins the card's promises: a stdio server declared in config.toml gets its tools
on the plugin surface; a tool that is not marked read-only waits for a spoken
yes; what a tool returns is data; a server that is absent or crashes costs a
warning and nothing else. The three engines are driven in test_plugins.py.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

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

FIXTURE = {"command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")], "always": ["*"],
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


def test_a_yes_does_not_outlive_its_session():
    plugin = _plugin("mcp_fx_delete_all")
    old = STATE.talk_session = TalkSession()
    plugin.run({"what": "x"})                      # asked in the first session
    old.add_user("oui")
    new = STATE.talk_session = TalkSession()        # the question was never put here
    new.add_user("bonjour")
    assert "deleted" not in plugin.run({"what": "x", "confirmed": True}), \
        "a question asked in a previous session was honoured in the next one"
    STATE.talk_session = None
    print("✓ a pending confirmation belongs to its session")


def test_a_refusal_is_not_a_yes_and_does_not_stick():
    plugin = _plugin("mcp_fx_delete_all")
    session = STATE.talk_session = TalkSession()
    a = {"what": "r"}
    plugin.run(a)                                   # question
    session.add_user("non")
    assert not mcp_tools.awaiting(session), "a refusal must release the wait"
    plugin.run(a)                                   # the agent asks again
    assert mcp_tools.awaiting(session)
    assert "deleted" not in plugin.run({**a, "confirmed": True}), "a no was taken for a yes"
    session.add_user("bof"), session.add_user("autre chose")
    assert not mcp_tools.awaiting(session)
    # late yes: confirmed=true before the turn is counted must not re-arm; then it runs once
    b = {"what": "late"}
    plugin.run(b)
    assert "deleted" not in plugin.run({**b, "confirmed": True})
    assert "deleted" not in plugin.run({**b, "confirmed": True})
    session.add_user("oui")
    assert "deleted late" in plugin.run({**b, "confirmed": True})
    assert "deleted" not in plugin.run({**b, "confirmed": True})
    STATE.talk_session = None
    print("\u2713 a no is no, the wait ends, a late yes still runs once")


def test_settle_waits_longer_only_while_a_yes_is_awaited():
    from loquivox.services.live_talk import LiveTalk
    plugin = _plugin("mcp_fx_delete_all")
    session = STATE.talk_session = TalkSession()
    talk = LiveTalk(session)
    talk.SETTLE_MAX, talk.SETTLE_FIRST, talk.SETTLE_FIRST_YES = 0.0, 0.1, 0.6

    def waited():
        t = time.monotonic()
        talk._settle(expect_new=True)
        return time.monotonic() - t

    assert waited() < 0.4, "nothing is awaited: the short wait applies"
    plugin.run({"what": "s"})
    assert waited() >= 0.55, "an action waits for its yes: SETTLE_FIRST_YES applies"
    session.add_user("non")
    assert waited() < 0.4, "after a refusal the long wait is gone"
    STATE.talk_session = None
    print("\u2713 SETTLE_FIRST_YES applies only while a yes is awaited")


def test_two_servers_do_not_share_a_confirmation():
    _servers(fx=FIXTURE, fy=FIXTURE)
    mcp_tools.sync(timeout=20)
    session = STATE.talk_session = TalkSession()
    _plugin("mcp_fx_delete_all").run({"what": "x"})
    session.add_user("oui")
    assert "deleted" not in _plugin("mcp_fy_delete_all").run({"what": "x", "confirmed": True}), \
        "a yes for one server's tool was used by another's"
    STATE.talk_session = None
    print("✓ a confirmation is per server")


def test_apply_never_blocks_nor_retries_a_failed_server():
    ghost = {"command": "/nonexistent/binary"}
    _servers(ghost=ghost)
    mcp_tools.sync(timeout=20)
    first = mcp_tools._servers["ghost"]
    assert first.state == "failed"
    t = time.monotonic()
    mcp_tools.sync(0)
    assert time.monotonic() - t < 1.0
    assert mcp_tools._servers["ghost"] is first, "an unchanged failed server was relaunched"
    _servers(ghost={**ghost, "args": ["x"]})
    mcp_tools.sync(0)
    assert mcp_tools._servers["ghost"] is not first, "a changed config must relaunch"
    print("✓ sync(0) does not wait and only relaunches what changed")


def test_stopping_a_starting_server_cancels_it():
    slow = {**FIXTURE, "command": sys.executable, "args": [
        "-c", "import time, runpy; time.sleep(2); "
              f"runpy.run_path({str(ROOT / 'tests/mcp_fixture_server.py')!r}, run_name='__main__')"]}
    _servers(slow=slow)
    mcp_tools.sync(0)
    s = mcp_tools._servers["slow"]
    time.sleep(0.5)
    assert s.state == "starting"
    _servers()
    mcp_tools.sync(0)                               # no longer declared: stopped
    time.sleep(4)
    assert s.state == "off", s.state
    assert s.future.done(), "the task of a stopped, starting server is still running"
    assert not any(p.name.startswith("mcp_slow_") for p in plugins.all_plugins()), \
        "a server stopped while starting registered its tools"
    print("✓ a server stopped while starting is cancelled")


def _script(body: str):
    return {"command": sys.executable, "args": ["-c", body], "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}}


def _then_fixture(pre: str) -> str:
    return pre + f"; import runpy; runpy.run_path({str(ROOT / 'tests/mcp_fixture_server.py')!r}, run_name='__main__')"


def test_a_slow_server_gets_its_connect_timeout():
    assert mcp_tools.CONNECT_TIMEOUT >= 30
    _servers(slow=_script(_then_fixture("import time; time.sleep(3)")) | {"connect_timeout": 15})
    mcp_tools.sync(timeout=20)
    assert mcp_tools.status()[0]["state"] == "up", mcp_tools.status()
    _servers(slow=_script(_then_fixture("import time; time.sleep(3)")) | {"connect_timeout": 1})
    mcp_tools.sync(timeout=20)
    assert mcp_tools.status()[0]["state"] == "failed"
    print("✓ connect_timeout is per server")


def test_a_failed_server_is_retried(tmp_path="/tmp/loquivox_mcp_retry"):
    import pathlib
    marker = pathlib.Path(tmp_path)
    marker.unlink(missing_ok=True)
    body = _then_fixture(f"import pathlib,sys; m=pathlib.Path({tmp_path!r}); "
                         "(m.exists() or (m.touch(), sys.exit(3)))")
    saved = mcp_tools.RETRY_MIN
    mcp_tools.RETRY_MIN = 0.5
    try:
        _servers(flaky=_script(body))
        mcp_tools.sync(timeout=20)
        assert mcp_tools._servers["flaky"].state in ("failed", "starting")
        deadline = time.monotonic() + 15
        while mcp_tools.status()[0]["state"] != "up" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert mcp_tools.status()[0]["state"] == "up", mcp_tools.status()
        assert mcp_tools.call_tool("flaky", "weather", {"city": "Nice"})
        # first use retries at once, whatever the backoff
        marker.unlink()
        mcp_tools.RETRY_MIN = 3600
        _servers(flaky2=_script(body))
        mcp_tools.sync(timeout=20)
        assert mcp_tools._servers["flaky2"].state == "failed"
        assert "Nice" in mcp_tools.call_tool("flaky2", "weather", {"city": "Nice"})
    finally:
        mcp_tools.RETRY_MIN = saved
        marker.unlink(missing_ok=True)
    print("✓ a failed server comes back, by backoff or on first use")


def test_a_server_killed_mid_session_comes_back():
    import pathlib, signal
    pid = pathlib.Path("/tmp/loquivox_mcp_pid")
    pid.unlink(missing_ok=True)
    body = _then_fixture(f"import os,pathlib; pathlib.Path({str(pid)!r}).write_text(str(os.getpid()))")
    saved = (mcp_tools.RETRY_MIN, mcp_tools.PING_EVERY)
    mcp_tools.RETRY_MIN, mcp_tools.PING_EVERY = 0.5, 0.5
    try:
        _servers(mortal=_script(body))
        mcp_tools.sync(timeout=20)
        assert mcp_tools.status()[0]["state"] == "up", mcp_tools.status()
        first = int(pid.read_text())
        os.kill(first, signal.SIGKILL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (
                mcp_tools.status()[0]["state"] == "up" and int(pid.read_text()) != first):
            time.sleep(0.1)
        assert int(pid.read_text()) != first and mcp_tools.status()[0]["state"] == "up", mcp_tools.status()
        assert "Nice" in mcp_tools.call_tool("mortal", "weather", {"city": "Nice"})
    finally:
        mcp_tools.RETRY_MIN, mcp_tools.PING_EVERY = saved
        pid.unlink(missing_ok=True)
    print("✓ a server killed mid-session is relaunched")


def test_an_http_server_receives_its_headers_and_they_stay_out_of_the_log():
    import http.server, threading
    from loquivox.diagnostics import redact
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(dict(self.headers))
            self.send_response(500)
            self.end_headers()
        log_message = lambda *a: None

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["LOQ_TEST_MCP_TOKEN"] = "s3cr3t-value-123456"
    try:
        _servers(remote={"url": f"http://127.0.0.1:{srv.server_port}/mcp",
                         "headers": {"Authorization": "Bearer ${LOQ_TEST_MCP_TOKEN}", "X-Team": "loq"}})
        mcp_tools.sync(timeout=10)
        assert seen and seen[0].get("Authorization") == "Bearer s3cr3t-value-123456", seen
        assert seen[0].get("X-Team") == "loq"
    finally:
        srv.shutdown()
        os.environ.pop("LOQ_TEST_MCP_TOKEN")
    row = mcp_tools.status()[0]
    assert "s3cr3t" not in row["error"], row
    # a variable that does not exist: a config error naming the variable, no retry
    _servers(bad={"url": "http://127.0.0.1:1/mcp", "headers": {"Authorization": "${LOQ_NOPE}"}})
    mcp_tools.sync(timeout=10)
    s = mcp_tools._servers["bad"]
    assert s.state == "failed" and "LOQ_NOPE" in s.error and not s.retry, s.error
    for line in ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "authorization: 'Bearer abc.def-ghijk'"):
        assert "dXNlcjpw" not in redact(line) and "ghijk" not in redact(line), redact(line)
    print("✓ HTTP headers are sent, resolved from the environment, and redacted")


def test_server_stderr_stays_out_of_our_output():
    import tempfile
    noisy = _then_fixture("import sys; sys.stderr.write('NOISE-LINE\\n'); sys.stderr.flush()")
    dying = "import sys; sys.stderr.write('WHY-IT-DIED\\n'); sys.exit(2)"
    _servers(noisy=_script(noisy), dying=_script(dying))
    with tempfile.TemporaryFile("w+") as cap:
        saved = os.dup(2)
        os.dup2(cap.fileno(), 2)
        try:
            mcp_tools.sync(timeout=20)
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        cap.seek(0)
        seen = cap.read()
    states = {r["name"]: r for r in mcp_tools.status()}
    assert states["noisy"]["state"] == "up" and "NOISE-LINE" not in seen, (states, seen)
    assert states["dying"]["state"] == "failed" and "WHY-IT-DIED" in states["dying"]["error"], states
    print("✓ a server's stderr is kept for the failure message only")


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
        test_a_yes_does_not_outlive_its_session()
        test_a_refusal_is_not_a_yes_and_does_not_stick()
        test_settle_waits_longer_only_while_a_yes_is_awaited()
        test_a_missing_or_crashing_server_is_a_warning()
        test_two_servers_do_not_share_a_confirmation()
        test_apply_never_blocks_nor_retries_a_failed_server()
        test_stopping_a_starting_server_cancels_it()
        test_switching_a_server_off_takes_its_tools_away()
        test_a_slow_server_gets_its_connect_timeout()
        test_a_failed_server_is_retried()
        test_a_server_killed_mid_session_comes_back()
        test_an_http_server_receives_its_headers_and_they_stay_out_of_the_log()
        test_server_stderr_stays_out_of_our_output()
        test_the_toml_schema_is_read()
    finally:
        config_module.CFG = real
        mcp_tools.stop()
    print("\n✓ the MCP client holds")
