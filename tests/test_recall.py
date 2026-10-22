"""
The ``recall`` tool: keyword/project search in facts.toml, the most recent
first (8 at most), framed as data; "nothing retained" is said, never invented;
offered by the desks like any plugin. No network:

    PYTHONPATH=src python tests/test_recall.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.services import memory, plugins  # noqa: E402

memory.FACTS_FILE.unlink(missing_ok=True)
assert memory.PLUGIN.run({"rule": "x"}) and memory.RECALL.run({"query": "loquivox"}) == memory.NOTHING

rows = [("2026-10-01", "loquivox", "on garde Postgres"), ("2026-10-05", "loquivox", "F4 écrit via write_text"),
        ("2026-10-03", "zeta", "Zeta part en novembre")]
rows += [(f"2026-09-{i:02d}", "loquivox", f"vieux fait {i}") for i in range(1, 12)]
memory.FACTS_FILE.write_text("".join(
    f'\n[[facts]]\nid = "{i}"\ntext = "{t}"\nproject = "{p}"\ndate = "{d}"\nsource = "session"\n'
    for i, (d, p, t) in enumerate(rows)), encoding="utf-8")

out = memory.RECALL.run({"query": "où on en était sur loquivox ?", "project": "loquivox"})
lines = out.splitlines()
assert len(lines) == memory.MAX_RECALL and lines[0].startswith("- [2026-10-05]"), out
assert "Zeta" not in out
msg = memory.RECALL.message({}, out)["content"]
assert "DATA" in msg and "<facts>" in msg and "Postgres" in msg

out = memory.RECALL.run({"query": "zeta"})
assert out.count("\n") == 0 and "novembre" in out

# nothing matching: a non-empty answer (an empty one reads as a failed call) that forbids inventing
out = memory.RECALL.run({"query": "kubernetes"})
assert out == memory.NOTHING and "invent nothing" in memory.RECALL.message({}, out)["content"]
assert memory.RECALL.run({}) == memory.NOTHING

# offered like any tool: chat_only, so never in a briefing
assert memory.RECALL.chat_only and memory.RECALL.is_tool and memory.RECALL in plugins.all_plugins()
print("ok")
