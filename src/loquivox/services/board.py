"""
The board plugin: what Collie Board has just pinged about, said out loud.

Collie Board is a kanban whose cards are started as real agents in worktrees;
when one of them stops and wants something, the board notifies — a push, a
bell, a digest. This makes a talk session a fourth surface for the same facts:
the assistant tells you, mid-conversation, that a card needs you, and asks what
you want to do about it.

**Nothing is added on the board's side**, which is its ADR 0013 (*"the bridge
does not know its consumers"*): the durable service does not learn that an
ephemeral one exists. So this is a *reader*. It polls
``GET /api/notifications/log``, whose ``id`` is a cursor by construction, keeps
the largest one it has seen, and takes what is above it.

It is also the registry's first **source** rather than a tool: nothing the
model can call, a ``watch`` that runs for the session and puts messages on its
own desk. Everything after that is the machinery ``research`` already uses —
the desk, the sweep between two turns, both engines — which is the point:
"an answer arrives between two turns" never depended on the model having asked.

Two things the log's shape dictates:

- **The content is reused, not recomposed.** An entry already carries
  ``status``, ``cardTitle``, ``cwd``, ``cardStatus`` and ``subtitle`` — the
  exact fields the board's own ``notifyContent()`` renders for the push, the
  toast and the bell. ``marker()`` and ``content()`` below are that same
  composition, in Python; a fourth surface does not get a sentence of its own.
- **The log keeps the trace of an alert that has since retracted** (ADR 0011:
  the coordinator clears its slot, the history deliberately does not). A
  question you already answered at the keyboard is therefore still in the log,
  so the card is re-read before anything is said, and a card that has moved
  since is dropped in silence.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import loquivox.config as config_module
from loquivox.services.plugins import Plugin, register

#: ponytail: the poll interval, not a setting. The board debounces for 30s
#: before it fires at all, so anything under a few seconds only spends
#: requests; anything over it is a delay the user can feel.
POLL_SEC = 5.0


def base_url() -> str:
    """The bridge, on loopback. Follows the board's own port override."""
    return f"http://127.0.0.1:{os.environ.get('COLLIE_BOARD_PORT', '8788')}"


def _get(path: str) -> Optional[Dict[str, Any]]:
    """One GET against the bridge, or None if it did not answer with JSON."""
    try:
        with urllib.request.urlopen(base_url() + path, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


# --- the sentence, as the board's three other surfaces write it -------------

def repo_of(cwd: str) -> str:
    """
    The short repo name behind a cwd — a card's pane lives in
    ``<…>/worktrees/<repo>/<branch>``, so the last segment is the branch.
    """
    parts = cwd.rstrip("/").split("/")
    if "worktrees" in parts:
        index = len(parts) - 1 - parts[::-1].index("worktrees")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[-1] if parts else cwd


def marker(entry: Dict[str, Any]) -> str:
    """What is LEFT TO DO — the board's own word, not what the pane did."""
    status = entry.get("status")
    if status == "blocked":
        return "Needs you"
    if status == "stalled":
        return "Stalled"
    if status == "ready":
        return "Ready"
    return "Review" if entry.get("cardStatus") == "review" else "Done"


def subject(entry: Dict[str, Any]) -> str:
    """
    What the notification is ABOUT: the card, or the repo when there is no card.

    Named on its own because the composed line below is positional — the model
    reading it out loud otherwise has to guess which of two names is the card,
    and it guesses wrong often enough to say the repo instead.
    """
    return str(entry.get("cardTitle") or "") or repo_of(str(entry.get("cwd") or ""))


def content(entry: Dict[str, Any]) -> str:
    """
    One log entry as the line the phone would have shown.

    The subject is the work — the card title, the repo when there is no card —
    and the repo appears exactly once: as the subject, or in the body.
    """
    repo = repo_of(str(entry.get("cwd") or ""))
    subject_ = subject(entry)
    body = " · ".join(part for part in (entry.get("session"),
                                        None if subject_ == repo else repo,
                                        entry.get("subtitle")) if part)
    return f"{marker(entry)} · {subject_}" + (f"\n{body}" if body else "")


def alert_message(entry: Dict[str, Any]) -> Dict[str, str]:
    """The notification as a system message, for either engine."""
    return {"role": "system", "content": (
        "A notification from the user's kanban board has just arrived, about "
        f"« {subject(entry)} »:\n\n{content(entry)}\n\n"
        "This is not about the text being written. Interrupt yourself politely "
        "and say it out loud in ONE short spoken sentence in the user's "
        "language — what it is about, named as above, and what it wants — then "
        "ask what they want to do about it. Do not read the lines out as they "
        "are written, and invent nothing beyond them."
    )}


# --- the source ------------------------------------------------------------

def still_standing(entry: Dict[str, Any]) -> bool:
    """
    Whether the alert is still true, read off the card rather than the log.

    The log keeps a retracted alert's trace on purpose, so an entry alone is
    not permission to speak: the card is re-read and its column compared with
    the one the alert fired on. Moved since — answered at the keyboard, or by
    the board itself — and the question is not asked out loud a second time.

    A card that cannot be re-read stays unspoken: speaking is the irreversible
    half here. An entry with no card behind it has nothing to contradict it.
    """
    card_id = entry.get("cardId")
    if not card_id:
        return True
    fired_on = entry.get("cardStatus")
    if not fired_on:
        return True
    card = _get(f"/api/cards/{card_id}")
    if not isinstance(card, dict) or not isinstance(card.get("card"), dict):
        return False
    return card["card"].get("status") == fired_on


def _entries() -> Optional[List[Dict[str, Any]]]:
    """The log, newest first, or None when the bridge is not there."""
    log = _get("/api/notifications/log")
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        return None
    return [e for e in log["entries"] if isinstance(e, dict)
            and isinstance(e.get("id"), int)]


def _watch(put: Callable[[Dict[str, str]], None], stop: threading.Event) -> None:
    """
    Poll the log for the length of the session, putting what is new on the desk.

    The cursor is seeded with the log as it stands, and nothing is said about
    what was already there: a conversation that opens by reciting fifty old
    notifications is the failure mode this exists to avoid. The seeding is
    retried until the bridge actually answers, so a board that starts late does
    not dump its backlog either.
    """
    cursor: Optional[int] = None
    while not stop.is_set():
        entries = _entries()
        if entries is not None:
            newest = max((e["id"] for e in entries), default=0)
            if cursor is None:
                cursor = newest                      # seed: today is turn zero
            else:
                for entry in sorted((e for e in entries if e["id"] > cursor),
                                    key=lambda e: e["id"]):
                    # Read at the keyboard, or no longer true: not said aloud.
                    if not entry.get("read") and still_standing(entry):
                        print(f"📋 Board: {content(entry).splitlines()[0]}")
                        put(alert_message(entry))
                cursor = max(cursor, newest)
        stop.wait(POLL_SEC)


def _settings(dialog, vbox, labels) -> None:
    """This plugin's card in Settings, built with the dialog's own helpers."""
    from gi.repository import Gtk

    body = dialog._group(
        vbox, "Board",
        "Collie Board notifies when one of its cards stops and needs you. "
        "With this on, a conversation in progress is told too: the assistant "
        "interrupts itself, says which card wants what, and asks what to do. "
        f"It only reads the local board ({base_url()}) — nothing is sent, and "
        "an alert you have already handled is not repeated out loud.")

    check = Gtk.CheckButton(label="Let the board interrupt a conversation")
    check.set_active(bool(config_module.CFG.TALK_BOARD))
    dialog._field(body, "Notifications", check, labels)

    status = Gtk.Label()

    def _apply(_btn=None) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        enabled = check.get_active()
        try:
            update_section("talk", {"board": enabled})
        except ConfigWriteError as e:
            status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        reachable = enabled and _entries() is not None
        note = "" if not enabled or reachable else " — but the board did not answer"
        status.set_markup(
            f"<small>✓ Board notifications {'on' if enabled else 'off'}{note}"
            " — takes effect on the next talk session.</small>")

    dialog._actions(body, _apply, status)


PLUGIN = Plugin(
    name="board",
    enabled=lambda: bool(config_module.CFG.TALK_BOARD),
    watch=_watch,
    result_note="📋 Notification du board",
    settings=_settings,
)
register(PLUGIN)


if __name__ == "__main__":
    assert repo_of("/home/u/.herdr/worktrees/loquivox/board-x") == "loquivox"
    assert repo_of("/home/u/git/perso/loquivox") == "loquivox"
    assert repo_of("/home/u/git/perso/loquivox/") == "loquivox"
    assert repo_of("") == ""

    # The board's own words, and the repo exactly once.
    assert marker({"status": "blocked"}) == "Needs you"
    assert marker({"status": "done", "cardStatus": "review"}) == "Review"
    assert marker({"status": "done", "cardStatus": "working"}) == "Done"
    assert marker({"status": "stalled"}) == "Stalled"
    assert marker({"status": "ready"}) == "Ready"

    card = {"status": "done", "cardStatus": "review", "cardTitle": "Fix the gate",
            "cwd": "/h/.herdr/worktrees/loquivox/board-x", "subtitle": "3 files"}
    assert content(card) == "Review · Fix the gate\nloquivox · 3 files"
    bare = {"status": "blocked", "cwd": "/h/git/perso/loquivox"}
    assert content(bare) == "Needs you · loquivox", content(bare)
    assert content({"status": "done", "cwd": "/h/git/x", "subtitle": "s"}) \
        == "Done · x\ns"
    assert subject(card) == "Fix the gate" and subject(bare) == "loquivox"
    assert "« Fix the gate »" in alert_message(card)["content"]
    assert alert_message(card)["role"] == "system"

    # An entry with no card is taken at face value; one whose card moved is not.
    assert still_standing({"status": "done"})
    assert still_standing({"cardId": "x"}), "no cardStatus to compare against"
    cards = {"live": {"card": {"status": "review"}}, "moved": {"card": {"status": "done"}}}
    _get = lambda path: cards.get(path.rsplit("/", 1)[-1])   # noqa: E731
    assert still_standing({"cardId": "live", "cardStatus": "review"})
    assert not still_standing({"cardId": "moved", "cardStatus": "review"})
    assert not still_standing({"cardId": "gone", "cardStatus": "review"}), \
        "a card that cannot be re-read was spoken anyway"

    # The watch seeds on what is already there, then speaks only what is new.
    log = {"entries": [{"id": 2, "status": "done", "cwd": "/h/git/x"}]}
    _get = lambda path: log if "notifications" in path else {"card": {"status": "review"}}  # noqa: E731
    said, stop = [], threading.Event()
    POLL_SEC = 0.01
    watcher = threading.Thread(target=_watch, args=(said.append, stop), daemon=True)
    watcher.start()
    stop.wait(0.05)
    assert not said, "the backlog was recited"
    log["entries"].insert(0, {"id": 3, "status": "blocked", "cwd": "/h/git/x",
                              "cardId": "live", "cardStatus": "review"})
    log["entries"].insert(0, {"id": 4, "status": "blocked", "cwd": "/h/git/y",
                              "read": True})
    stop.wait(0.05)
    stop.set()
    watcher.join(2)
    assert len(said) == 1, said
    assert "Needs you · x" in said[0]["content"], said
    assert "· y" not in said[0]["content"], "an entry already read at the keyboard spoke"
    print("✓ board plugin OK")
