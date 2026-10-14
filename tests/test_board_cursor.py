"""
Self-check for the board reader's cursor, no network and no GTK:

    PYTHONPATH=src python tests/test_board_cursor.py

The log's ``id`` restarts at 1 with the bridge, so the cursor is ``ts``.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.services.board as board  # noqa: E402
from loquivox.state import SettingsManager  # noqa: E402

store: dict = {}
SettingsManager.load = staticmethod(lambda: dict(store))                 # type: ignore
SettingsManager.update = staticmethod(lambda k, v: store.__setitem__(k, v))  # type: ignore
board._get = lambda path: log if "notifications" in path else {"card": {"status": "review"}}
board.POLL_SEC = 0.01
board.time.time = lambda: now / 1000  # type: ignore
board.config_module.CFG = type("C", (), {"TALK_BOARD_CATCHUP_SEC": 300})()


def e(id_, ts, **kw):
    return {"id": id_, "ts": ts, "status": "blocked", "cwd": "/h/git/x", **kw}


def session(seconds=0.05):
    said, stop = [], threading.Event()
    t = threading.Thread(target=board._watch, args=(said.append, stop), daemon=True)
    t.start()
    stop.wait(seconds)
    stop.set()
    t.join(2)
    return said


now = 10_000_000
log = {"entries": [e(2, now - 5000), e(1, now - 6000)]}

# First ever session: the backlog is marked seen, not recited.
assert not session()

# Cursor is a ts and survives the session (and a process: it lives in settings).
assert store["board_cursor"]["ts"] == now - 5000, store

# Second session, nothing new: nothing re-said.
assert not session()

# A new one arrives between two sessions: said once, then not again.
log["entries"].insert(0, e(3, now - 2000))
assert len(session()) == 1
assert not session(), "same notification said twice across sessions"

# Bridge restart: ids restart at 1, ts keeps going. Nothing is swallowed.
now += 60_000
log = {"entries": [e(2, now - 1000), e(1, now - 3000)]}
said = session()
assert len(said) == 2, f"restart made the reader deaf: {said}"
assert not session()

# Two entries of the same ms in one run: both said, neither twice.
now += 1000
log["entries"][:0] = [e(4, now - 100), e(3, now - 100)]
assert len(session()) == 2
assert not session()

# Older than the catch-up ceiling: marked seen, not said.
now += 3_600_000
log["entries"][:0] = [e(5, now - 3_000_000), e(6, now - 1000)]
said = session()
assert len(said) == 1 and "x" in said[0]["content"], said
assert not session(), "a stale entry came back"
print("✓ board cursor OK")
