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
``GET /api/notifications/log`` and keeps a cursor on ``ts``. NOT on ``id``,
whatever ADR 0013 says: ``id`` is a counter of the bridge *process*
(``nextId = 1``, a ring of 50 in RAM) and restarts at 1, so an id cursor
swallows everything after a restart. ``ts`` (epoch ms) survives it; ``id`` only
tells apart two entries of the same millisecond within one run.

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
import time
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


class Alert(dict):
    """
    The notification as a system message that still knows its entry.

    A plain dict on the wire — every engine sends it as is — with the log entry
    kept beside it so ``still_true`` can re-read the card when the message is
    finally about to be said, not only when it was found.
    """

    entry: Dict[str, Any]


def alert_message(entry: Dict[str, Any]) -> Dict[str, str]:
    """The notification as a system message, for either engine."""
    # data only: how to say it is the frame every source gets (plugins.SOURCE_FRAME)
    message = Alert(role="system", content=f"« {subject(entry)} »:\n\n{content(entry)}")
    message.entry = entry
    return message


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
            and isinstance(e.get("id"), int)]  # ts is checked by advance()


def still_true(message: Dict[str, str]) -> bool:
    """
    Re-read the card the moment the message is about to be spoken.

    ``_watch`` already checked once, at the poll; but the desk is only emptied
    at the next gap in the conversation, which can be a minute later if the
    user kept talking, and a question answered at the keyboard in between must
    not be asked out loud anyway.
    """
    return still_standing(getattr(message, "entry", {}))


_CURSOR_KEY = "board_cursor"


def _load_cursor() -> Optional[Dict[str, Any]]:
    """``{"ts": ms, "ids": [ids seen at that exact ms]}``, or None on first run."""
    from loquivox.state import SettingsManager

    cursor = SettingsManager.load().get(_CURSOR_KEY)
    return cursor if isinstance(cursor, dict) and isinstance(cursor.get("ts"), int) else None


def _save_cursor(cursor: Dict[str, Any]) -> None:
    from loquivox.state import SettingsManager

    SettingsManager.update(_CURSOR_KEY, cursor)


def advance(entries: List[Dict[str, Any]], cursor: Optional[Dict[str, Any]],
            now_ms: float, catchup_ms: float):
    """
    One poll: ``(entries to say, new cursor)``.

    Seen means ``ts`` below the cursor, or at it with an id already recorded
    (ids are only compared inside one millisecond, never across restarts).
    Anything older than ``catchup_ms`` is marked seen but not said: an alert
    from three hours ago, read at the start of a session, is noise.
    With no cursor yet everything is marked seen — the log is turn zero.
    """
    entries = [e for e in entries if isinstance(e.get("ts"), (int, float))]
    if cursor is None:
        top = max((int(e["ts"]) for e in entries), default=int(now_ms))
        return [], {"ts": top, "ids": [e["id"] for e in entries if int(e["ts"]) == top]}
    ts, ids = cursor["ts"], list(cursor.get("ids", []))
    fresh = sorted((e for e in entries
                    if e["ts"] > ts or (e["ts"] == ts and e["id"] not in ids)),
                   key=lambda e: (e["ts"], e["id"]))
    for e in fresh:
        if e["ts"] > ts:
            ts, ids = int(e["ts"]), []
        ids.append(e["id"])
    return [e for e in fresh if now_ms - e["ts"] <= catchup_ms], {"ts": ts, "ids": ids}


def _watch(put: Callable[[Dict[str, str]], None], stop: threading.Event) -> None:
    """
    Poll the log until ``stop`` is set — the companion's life, or the
    session's when it is off — putting what is new on the desk.

    The cursor lives in settings.json, so it outlives the session and the
    process: two sessions never say the same notification, and one opening an
    hour after the last still hears what came in between — up to
    ``TALK_BOARD_CATCHUP_SEC`` old, the rest is marked seen without being said.
    First run ever, or a board that starts late, seeds on the log as it stands
    rather than reciting it. The cursor is written *before* speaking: a
    notification lost is cheaper than one said every session.
    """
    while not stop.is_set():
        entries = _entries()
        if entries is not None:
            cursor = _load_cursor()
            said, new = advance(entries, cursor, time.time() * 1000,
                                config_module.CFG.TALK_BOARD_CATCHUP_SEC * 1000)
            if new != cursor:
                _save_cursor(new)
            for entry in said:
                # Read at the keyboard, or no longer true: not said aloud.
                if not entry.get("read") and still_standing(entry):
                    print(f"📋 Board: {content(entry).splitlines()[0]}")
                    put(alert_message(entry))
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
    loads="board",      # the MCP server of the same name: its tools are what a notification calls for
    still_true=still_true,
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
    # The message carries its entry, and answers the same question later.
    alert = alert_message({"cardId": "live", "cardStatus": "review", "status": "blocked"})
    assert json.loads(json.dumps(alert)) == {"role": "system", "content": alert["content"]}
    assert still_true(alert)
    cards["live"]["card"]["status"] = "done"
    assert not still_true(alert), "a card moved after the poll was spoken anyway"
    assert still_true({"role": "system", "content": "no entry"})

    print("✓ board plugin OK")
