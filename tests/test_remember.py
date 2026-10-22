"""
The ``remember`` tool and the facts' Settings writer: "retiens que…" adds a
dated fact that ``recall`` finds at once; a fact edited or deleted in Settings
is taken into account without a restart; a fact learned by itself survives
a Apply that did not show it. No network:

    PYTHONPATH=src python tests/test_remember.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.services import memory, plugins  # noqa: E402

memory.FACTS_FILE.unlink(missing_ok=True)

# the tool: chat_only, adds a fact, the model confirms in one sentence
assert memory.REMEMBER.chat_only and memory.REMEMBER.is_tool and memory.REMEMBER in plugins.all_plugins()
out = memory.REMEMBER.run({"text": "je préfère les mails courts", "project": ""})
assert out == "je préfère les mails courts"
assert "ONE short spoken sentence" in memory.REMEMBER.message({}, out)["content"]
assert memory.REMEMBER.run({"text": "  "}) == ""        # nothing to keep, nothing written
[fact] = memory.facts()
assert fact["text"] == out and fact["date"] == date.today().isoformat() and fact["source"] == "asked"
assert "mails courts" in memory.RECALL.run({"query": "mails"})          # recall sees it at once

# a session fact (auto) sits beside it; Settings edits one, deletes the other, adds a third
memory.FACTS_FILE.write_text(memory.FACTS_FILE.read_text(encoding="utf-8") +
    '\n[[facts]]\nid = "s1"\ntext = "Postgres pour Zeta"\nproject = "zeta"\ndate = "2026-10-01"\nsource = "session"\n',
    encoding="utf-8")
asked = fact["id"]
memory.add_fact("vu en vocal pendant l'ouverture")           # learned while the dialog was open
late = memory.facts()[-1]["id"]
memory.replace_facts([(asked, "je préfère les mails très courts", "mail"), ("s1", "", ""),
                      (None, "Zeta part en novembre", "zeta")], shown=[asked, "s1"])
by_id = {f["id"]: f for f in memory.facts()}
assert by_id[asked]["text"] == "je préfère les mails très courts" and by_id[asked]["project"] == "mail"
assert by_id[asked]["date"] == fact["date"] and by_id[asked]["source"] == "asked"   # date and source kept
assert "s1" not in by_id and late in by_id                                  # deleted; the unseen one kept
assert any(f["text"] == "Zeta part en novembre" and f["source"] == "asked" for f in by_id.values())
assert memory.RECALL.run({"query": "Postgres"}) == memory.NOTHING           # no restart needed
assert "très courts" in memory.RECALL.run({"query": "mails"}) or "très courts" in memory.RECALL.run({"project": "mail"})
print("ok")
