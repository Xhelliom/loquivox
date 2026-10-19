"""
Tool-call bench: does the agent loop call the right tools, by hand, before the voice.

    python tests/bench_mcp.py                        # the configured provider and model
    python tests/bench_mcp.py --model qwen3-32b      # this model on the agent slot
    python tests/bench_mcp.py --provider anthropic --model claude-sonnet-5-5
    python tests/bench_mcp.py --only lampe -v        # scenarios whose name contains "lampe"

It costs tokens (a few completions per scenario), so it is NOT a self-check:
run it after adding an MCP server or changing the model. Each scenario of
tests/bench_mcp_scenarios.toml replays a few spoken turns through the real
``TalkSession`` loop against fake MCP servers (tests/mcp_bench_server.py) and
is scored on checks: the expected calls, what really ran (read from the
servers' own log), the reply, and two rules for every turn — no action without
a yes, no "done" on a held, failed or missing call. Exit code 1 if any check
fails. The provider and model are never coded here: they are the agent slot's
(``[models] agent`` / chat), or the options above.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as chat_module  # noqa: E402
from loquivox.services import mcp_tools, plugins  # noqa: E402
from loquivox.services.ai import AIService  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402

SCENARIOS = ROOT / "tests" / "bench_mcp_scenarios.toml"
YES = re.compile(r"^\W*(oui|ouais|ok|d'accord|vas-y|yes|yep)\b", re.I)
DONE = re.compile(r"c'est (fait|lancé|bon|allumé|éteint)|j'ai (lancé|allumé|éteint|fait)|"
                  r"\b(done|launched|turned (on|off))\b", re.I)

Check = Tuple[str, bool]
VERBOSE = False


def load(path: Path = SCENARIOS) -> Dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def start_servers(servers: Dict[str, Any], log: Path) -> None:
    """Declare the fake servers as the only MCP servers, all tools on the table."""
    cfg = config_module.CFG
    declared = {name: {"command": sys.executable,
                       "args": [str(ROOT / a) if a.endswith(".py") else a for a in s["args"]],
                       "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", ""), "BENCH_LOG": str(log)},
                       "always": ["*"]} for name, s in servers.items()}
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": declared})
    mcp_tools.sync(timeout=30)


def _args_found(want: Dict[str, Any], got: Dict[str, Any]) -> bool:
    return all(k in got and (want[k].lower() in str(got[k]).lower() if isinstance(want[k], str)
                             else got[k] == want[k]) for k in want)


def _match_calls(expected: List[Dict[str, Any]], made: List[Tuple[str, Dict[str, Any]]]) -> List[Check]:
    """Each expected call must appear, in order; other calls may sit between."""
    checks, i = [], 0
    for want in expected:
        j = next((n for n in range(i, len(made))
                  if made[n][0] == want["tool"] and _args_found(want.get("args", {}), made[n][1])), None)
        label = f"call {want['tool']} {want.get('args', '')}".strip()
        checks.append((label, j is not None))
        if j is not None:
            i = j + 1
    return checks


def run_scenario(sc: Dict[str, Any], log: Path) -> List[Check]:
    """Replay one scenario; return every check as (label, passed)."""
    session = TalkSession(write=False)
    made: List[Tuple[str, Dict[str, Any]]] = []
    statuses: List[str] = []
    real_complete, real_execute = AIService.complete, plugins.Desk.execute

    def spy_complete(messages, *a, tool_calls_out=None, **kw):
        out = real_complete(messages, *a, tool_calls_out=tool_calls_out, **kw)
        for c in tool_calls_out or []:
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            # mcp_<server>_<tool> -> <server>.<tool>
            made.append((re.sub(r"^mcp_([^_]+)_", r"\1.", c["name"]), args))
        return out

    def spy_execute(self, arguments):
        res = real_execute(self, arguments)
        statuses.append(res[0])
        return res

    AIService.complete, plugins.Desk.execute = staticmethod(spy_complete), spy_execute
    STATE.talk_session = session
    checks: List[Check] = []
    try:
        for turn in sc["turns"]:
            made.clear(), statuses.clear()
            before = log.read_text().splitlines() if log.exists() else []
            reply = session.reply(turn["say"])
            text = reply.text if reply else ""
            ran = [json.loads(l) for l in (log.read_text().splitlines() if log.exists() else [])[len(before):]]
            acted = [r["tool"] for r in ran if r["acts"]]
            if VERBOSE:
                print(f"   > {turn['say']}\n     {text[:160]}")
            checks += _match_calls(turn.get("calls", []), made)
            if turn.get("no_calls"):
                checks.append(("no tool called", not made))
            checks.append((f"ran {turn.get('executed', [])}" if "executed" in turn else "nothing acted",
                           acted == turn.get("executed", [])))
            if not YES.match(turn["say"]):
                checks.append(("no action without a yes", not acted))
            if statuses[-1:] != ["done"]:
                checks.append(("no 'done' on a held, failed or missing call", not DONE.search(text)))
            if "reply_matches" in turn:
                checks.append((f"reply ~ {turn['reply_matches']}", bool(re.search(turn["reply_matches"], text, re.I))))
            if "reply_avoids" in turn:
                checks.append((f"reply !~ {turn['reply_avoids']}", not re.search(turn["reply_avoids"], text)))
    finally:
        AIService.complete, plugins.Desk.execute = real_complete, real_execute
        STATE.talk_session = None
    return checks


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", help="agent provider (default: the configured one)")
    ap.add_argument("--model", help="agent model (default: the configured one)")
    ap.add_argument("--only", help="run scenarios whose name contains this")
    ap.add_argument("--scenarios", type=Path, default=SCENARIOS)
    ap.add_argument("-v", "--verbose", action="store_true", help="list every check and what was said")
    opts = ap.parse_args(argv)
    global VERBOSE
    VERBOSE = opts.verbose

    from loquivox.secrets import load_secrets
    load_secrets()
    chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)
    if opts.provider:
        STATE.ai_agent_provider = opts.provider
    if opts.model:
        STATE.ai_agent_model = opts.model
    provider, model = AIService.agent_target()
    print(f"bench: provider={provider} model={model}")

    data = load(opts.scenarios)
    log = Path(tempfile.mkdtemp(prefix="loquivox-bench-")) / "calls.jsonl"
    os.environ["BENCH_LOG"] = str(log)
    start_servers(data["servers"], log)
    passed = total = failed_scenarios = 0
    try:
        for sc in data["scenario"]:
            if opts.only and opts.only.lower() not in sc["name"].lower():
                continue
            checks = run_scenario(sc, log)
            ok = sum(1 for _l, p in checks if p)
            passed, total = passed + ok, total + len(checks)
            failed_scenarios += ok < len(checks)
            print(f"{'✓' if ok == len(checks) else '✗'} {ok}/{len(checks)}  {sc['name']}")
            for label, p in checks:
                if opts.verbose or not p:
                    print(f"    {'ok  ' if p else 'FAIL'} {label}")
    finally:
        mcp_tools.stop()
    print(f"score: {passed}/{total} checks, {failed_scenarios} scenario(s) failed")
    return 1 if failed_scenarios else 0


if __name__ == "__main__":
    sys.exit(main())
