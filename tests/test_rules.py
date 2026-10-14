"""
Self-check for the event rules (services/events.py + services/rules.py):

    PYTHONPATH=src python tests/test_rules.py     # the MCP parts need the `mcp` extra

Pins the card's promises: a rule turns an event into MCP calls with a test
server and test rules (nothing here, or in the code, names an integration);
actions never hold the emitter; a burst is one call in flight plus the last
event; an absent server or a failing tool is one warning; `--rules-check`
and `--rules-catalog` work without GTK and the first fails on a wrong rule.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.services import events, rules  # noqa: E402

FIXTURE = {"command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")],
           "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}}


def _cfg(rule_tables=(), **servers):
    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "RULES": list(rule_tables), "MCP_SERVERS": servers})


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _reset():
    rules._slots.clear(), rules._last.clear(), rules._warned.clear()


def test_substitute_and_normalize():
    data = {"state": "speaking", "n": 3}
    assert rules.substitute({"a": "{state}", "b": ["x {state}"], "c": "{n}", "d": "{nope}", "e": 1}, data) == \
        {"a": "speaking", "b": ["x speaking"], "c": 3, "d": "{nope}", "e": 1}
    one = rules.normalize({"when": "w", "do": {"server": "s", "tool": "t"}})
    assert one["do"] == [{"server": "s", "tool": "t"}] and one["if"] == {} and one["enabled"]
    print("✓ {key} is replaced (typed when alone); `do` is one table or a list")


def test_matching_and_order():
    calls = []
    rules._invoke = lambda s, t, a: calls.append((s, t, a)) or "ok"
    _reset()
    _cfg([{"when": "companion.state", "if": {"state": "speaking"}, "name": "r1",
           "do": [{"server": "s", "tool": "first", "args": {"v": "{state}"}},
                  {"server": "s", "tool": "second"}]},
          {"when": "companion.state", "if": {"state": "listening"}, "name": "r2",
           "do": {"server": "s", "tool": "other"}}])
    rules.on_event("companion.state", {"state": "speaking"})
    rules.on_event("talk.started", {"mode": "talk"})
    assert _wait(lambda: len(calls) == 2)
    time.sleep(0.1)
    assert calls == [("s", "first", {"v": "speaking"}), ("s", "second", {})], calls
    assert rules._last["r1"][0] is True and "r2" not in rules._last
    print("✓ `when` + `if` select the rule; the actions run in order")


def test_burst_is_one_in_flight_plus_the_last():
    seen = []

    def slow(s, t, a):
        seen.append(a["i"])
        time.sleep(0.3)
        return "ok"
    rules._invoke = slow
    _reset()
    _cfg([{"when": "companion.state", "name": "burst", "do": {"server": "s", "tool": "t", "args": {"i": "{i}"}}}])
    began = time.monotonic()
    for i in range(30):
        rules.on_event("companion.state", {"state": "x", "i": i})
    assert time.monotonic() - began < 0.1, "emitting must never wait for an action"
    assert _wait(lambda: len(seen) == 2 and rules._last["burst"][0]), seen
    time.sleep(0.5)
    assert seen[0] == 0 and seen[1] == 29 and len(seen) == 2, seen
    print("✓ a burst of 30 events = one call in flight + the last event; the emitter never waits")


def test_min_gap_between_runs():
    stamps = []
    rules._invoke = lambda s, t, a: stamps.append((time.monotonic(), "start")) or stamps.append((time.monotonic(), "end")) or "ok"
    _reset()
    _cfg([{"when": "companion.state", "name": "gap", "do": {"server": "s", "tool": "t"}}])
    rules.on_event("companion.state", {"state": "a"})
    assert _wait(lambda: len(stamps) == 2)
    rules.on_event("companion.state", {"state": "b"})
    assert _wait(lambda: len(stamps) == 4)
    assert stamps[2][0] - stamps[1][0] >= rules.MIN_GAP - 0.01, stamps
    print("✓ two runs of a rule are MIN_GAP apart")


def test_bus_survives_a_subscriber_that_raises():
    def boom(name, data):
        raise RuntimeError("nope")
    events.subscribe(boom)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        events.emit("companion.state", state="idle")
    events._subscribers.remove(boom)
    assert "subscriber failed" in out.getvalue()
    print("✓ a failing subscriber is a warning, not an exception")


def test_absent_server_warns_once():
    from loquivox.services import mcp_tools
    rules._invoke = mcp_tools.call_tool
    _reset()
    _cfg([{"when": "companion.state", "name": "ghost", "do": {"server": "nobody", "tool": "t"}}])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for i in range(3):
            rules.on_event("companion.state", {"state": "a"})
            assert _wait(lambda: "ghost" in rules._last)
            time.sleep(0.4)
    assert out.getvalue().count("Rule ghost") == 1, out.getvalue()
    assert rules._last["ghost"][0] is False and "not declared" in rules._last["ghost"][2]
    print("✓ an absent server is one warning, whatever the number of events")


def test_a_real_server_receives_the_call():
    from loquivox.services import mcp_tools
    _reset()
    rules._invoke = mcp_tools.call_tool
    _cfg([{"when": "companion.state", "if": {"state": "speaking"}, "name": "real",
           "do": [{"server": "fx", "tool": "weather", "args": {"city": "{state}"}}]},
          {"when": "companion.state", "name": "boom",
           "do": [{"server": "fx", "tool": "nonexistent"}]}], fx=FIXTURE)
    mcp_tools.sync(timeout=20)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rules.on_event("companion.state", {"state": "speaking"})
        assert _wait(lambda: "real" in rules._last and "boom" in rules._last)
    assert rules._last["real"] == (True, rules._last["real"][1], "It is raining in speaking."), rules._last
    assert rules._last["boom"][0] is False, "an unknown tool is a failure, not a crash"
    assert out.getvalue().count("Rule boom") == 1
    print("✓ a rule reaches a real stdio MCP server with the event's data; a bad tool is one warning")


def test_check_and_catalog():
    from loquivox.services import mcp_tools
    _cfg([], fx=FIXTURE)
    mcp_tools.sync(timeout=20)
    good = {"when": "companion.state", "if": {"state": "speaking"}, "name": "good",
            "do": {"server": "fx", "tool": "weather", "args": {"city": "{state}"}}}
    bad = [
        {"when": "nope.event", "do": {"server": "fx", "tool": "weather", "args": {"city": "x"}}},
        {"when": "companion.state", "if": {"colour": "x"}, "do": {"server": "fx", "tool": "weather", "args": {"city": "x"}}},
        {"when": "companion.state", "do": {"server": "ghost", "tool": "t"}},
        {"when": "companion.state", "do": {"server": "fx", "tool": "weathr"}},
        {"when": "companion.state", "do": {"server": "fx", "tool": "weather"}},
        {"when": "companion.state", "do": {"server": "fx", "tool": "weather", "args": {"city": "x", "zip": 1}}},
        {"when": "companion.state", "do": {"server": "fx", "tool": "weather", "args": {"city": 12}}},
        {"when": "companion.state", "do": {"server": "fx", "tool": "weather", "args": {"city": "{colour}"}}},
    ]
    report = rules.check([good, *bad], mcp_tools.describe)
    assert report["good"] == [], report
    errors = [e for k, v in report.items() if k != "good" for e in v]
    assert len(errors) == len(bad) and all(errors), report
    print("✓ check accepts a good rule and names what is wrong in each bad one")


def _loquivox(home, flag):
    code = ("import sys; sys.argv=['loquivox', %r]\n"
            "from loquivox.app import main\n"
            "try: main()\n"
            "except SystemExit as e: print('EXIT', e.code); print('GTK', 'gi' in sys.modules)\n" % flag)
    env = {**os.environ, "HOME": home, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          timeout=90, cwd=home)


def test_cli_without_gtk():
    toml = lambda rule: (f'[mcp.servers.fx]\ncommand = "{sys.executable}"\nargs = ["{ROOT}/tests/mcp_fixture_server.py"]\n'
                         f'env = {{ PYTHONPATH = "{ROOT / "src"}" }}\n{rule}')
    for rule, code in (('[[rules]]\nwhen = "companion.state"\nif = { state = "speaking" }\n'
                        'do = { server = "fx", tool = "weather", args = { city = "{state}" } }\n', "0"),
                       ('[[rules]]\nwhen = "companion.state"\ndo = { server = "fx", tool = "weathr" }\n', "1")):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".config/loquivox").mkdir(parents=True)
            (Path(home) / ".config/loquivox/config.toml").write_text(toml(rule))
            r = _loquivox(home, "--rules-check")
            assert f"EXIT {code}" in r.stdout and "GTK False" in r.stdout, (r.stdout, r.stderr[-800:])
            if code == "1":
                assert "weathr" in r.stdout
            r = _loquivox(home, "--rules-catalog")
            assert "companion.state" in r.stdout and "weather" in r.stdout and "GTK False" in r.stdout, (r.stdout, r.stderr[-800:])
            assert '"city"' in r.stdout, "the argument schema is listed"
    print("✓ --rules-check (exit 0 / 1) and --rules-catalog run without GTK")


def test_no_integration_in_the_code():
    for path in (ROOT / "src/loquivox/services/events.py", ROOT / "src/loquivox/services/rules.py"):
        text = path.read_text().lower()
        assert not re.search(r"home.?assistant|light\.turn|scene\.(create|turn)|ha-mcp", text), path
    print("✓ no source file names an integration or an entity")


if __name__ == "__main__":
    test_substitute_and_normalize()
    test_matching_and_order()
    test_burst_is_one_in_flight_plus_the_last()
    test_min_gap_between_runs()
    test_bus_survives_a_subscriber_that_raises()
    test_no_integration_in_the_code()
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("⏭  mcp extra not installed — the MCP parts are skipped")
        raise SystemExit(0)
    test_absent_server_warns_once()
    test_a_real_server_receives_the_call()
    test_check_and_catalog()
    test_cli_without_gtk()
    print("\n✓ the event rules hold")
    sys.stdout.flush()
    os._exit(0)     # the MCP loop's daemon thread
