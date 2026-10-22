"""
End-of-session memory: 1 to 3 dated facts in facts.toml, nothing for an empty
or failed session, and the journal never quotes a fact. Dummy extractor, no network:

    PYTHONPATH=src python tests/test_session_facts.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import contextlib
import io
import sys
import tomllib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.services import memory  # noqa: E402

SECRET = "le mot de passe du projet Zeta est tulipe"
TALK = [{"role": "user", "content": "on garde Postgres pour Zeta"},
        {"role": "assistant", "content": "Noté."},
        {"role": "system", "content": "ignored"}]


def facts():
    try:
        return tomllib.loads(memory.FACTS_FILE.read_text(encoding="utf-8")).get("facts", [])
    except OSError:
        return []


def run(turns):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        n = memory.remember_session(turns)
    return n, buf.getvalue()


# an empty session adds nothing and never asks the model
memory.EXTRACT = lambda t: (_ for _ in ()).throw(AssertionError("asked"))
assert run([])[0] == 0 and run([TALK[1]])[0] == 0 and not facts()

# a real exchange: capped at 3, dated, journal silent on the content
memory.EXTRACT = lambda t: [{"text": SECRET, "project": "zeta"}] + \
    [{"text": f"fait {i}", "project": ""} for i in range(5)]
n, log = run(TALK)
assert n == 3 and len(facts()) == 3, facts()
f = facts()[0]
assert f["text"] == SECRET and f["project"] == "zeta" and f["source"] == "session"
assert f["date"] == date.today().isoformat() and f["id"]
assert "tulipe" not in log and "Zeta" not in log

# a failing extractor writes nothing and does not quote
def boom(t):
    raise RuntimeError(SECRET)
memory.EXTRACT = boom
n, log = run(TALK)
assert n == 0 and len(facts()) == 3 and "tulipe" not in log and "failed" in log

# nothing worth keeping
memory.EXTRACT = lambda t: []
assert run(TALK)[0] == 0 and len(facts()) == 3
print("ok")
