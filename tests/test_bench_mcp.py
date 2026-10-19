"""
Self-check for tests/bench_mcp.py, no network (the model is faked):

    PYTHONPATH=src python tests/test_bench_mcp.py     # needs the `mcp` extra

The scenario file is well formed, and the scoring tells a model that does it
right from one that lies — which is all the bench has to be trusted for.
"""
from __future__ import annotations

import _hermetic  # noqa: F401

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import mcp  # noqa: F401
except ImportError:
    print("⏭  mcp extra not installed — skipped")
    raise SystemExit(0)

import bench_mcp  # noqa: E402
import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as chat_module  # noqa: E402
from loquivox.services import ai as ai_module, mcp_tools  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)
real = ai_module.AIService.complete
cfg = config_module.CFG
data = bench_mcp.load()
by_name = {s["name"]: s for s in data["scenario"]}

# 1. the file: enough scenarios, the card's two, every server and tool real
assert len(by_name) >= 10, len(by_name)
assert "lot sans dire le projet" in by_name and "un outil qui échoue" in by_name
for sc in by_name.values():
    for t in sc["turns"]:
        for c in t.get("calls", []):
            assert c["tool"].split(".")[0] in data["servers"], c
print(f"✓ {len(by_name)} scenarios, every call names a declared server")

log = Path(tempfile.mkdtemp()) / "calls.jsonl"
os.environ["BENCH_LOG"] = str(log)
bench_mcp.start_servers(data["servers"], log)


def fake(liar):
    def complete(messages, on_delta=None, tools=None, tool_calls_out=None, **kw):
        said = [m["content"] for m in messages if m["role"] == "user"][-1]
        got = any(m["role"] == "tool" for m in messages)
        if liar:
            return "C'est fait !"
        if got:
            return "Voilà."
        call = None
        if "lot 13 de loquivox" in said:
            call = ("launch_lot", {"project": "loquivox", "lot": 13})
        elif "exporte" in said:
            call = ("export_report", {"project": "loquivox"})
        if call:
            tool_calls_out.append({"id": "c", "name": f"mcp_board_{call[0]}",
                                   "arguments": json.dumps(call[1])})
        return ""
    return staticmethod(complete)


try:
    # 2. a model that calls the right tool is held for the yes and passes
    ai_module.AIService.complete = fake(liar=False)
    checks = bench_mcp.run_scenario(by_name["lot 13 explicite"], log)
    assert all(p for _l, p in checks), checks
    print("✓ right call, held for the yes → every check passes")

    # 3. the failing tool: the failure is said, nothing is claimed done
    checks = bench_mcp.run_scenario(by_name["un outil qui échoue"], log)
    assert all(p for _l, p in checks), checks
    print("✓ a failing tool is scored on what is said about it")

    # 4. a model that claims it without calling anything fails the bench
    ai_module.AIService.complete = fake(liar=True)
    for name in ("un outil absent", "lot 13 explicite"):
        checks = bench_mcp.run_scenario(by_name[name], log)
        assert any(not p for _l, p in checks), (name, checks)
    print("✓ \"c'est fait\" with no call behind it is caught")
finally:
    ai_module.AIService.complete = real
    config_module.CFG = cfg
    mcp_tools.stop()
