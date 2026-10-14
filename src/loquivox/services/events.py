"""
The internal event bus: what Loquivox says happened, without knowing who listens.

``emit(name, **data)`` is called from wherever the thing happens — the audio
thread included — and returns at once: a subscriber that raises is a warning,
and the only subscriber today (``services/rules.py``) merely queues. Nothing
here names an integration. ``EVENTS`` is the catalog: the one place the names
and their data are written down, read by ``--rules-check``, ``--rules-catalog``
and the docs, so a new event is one entry here and one ``emit`` call.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

#: event → {data key: what it holds}
EVENTS: Dict[str, Dict[str, str]] = {
    "companion.state": {
        "state": "the bubble's state: idle, cloud, listening, speaking or silent"},
    "companion.session_opened": {
        "cause": "wake_word, key or notification"},
    "companion.session_closed": {
        "cause": "how that session had been opened: wake_word, key or notification"},
    "notification.received": {
        "source": "the source plugin's name",
        "title": "the first line of the notification"},
    "talk.started": {"mode": "talk (F6) or ai (F4)"},
    "talk.ended": {"mode": "talk (F6) or ai (F4)"},
    "dictation.done": {"chars": "length of the text typed"},
}

_subscribers: List[Callable[[str, Dict[str, Any]], None]] = []


def subscribe(fn: Callable[[str, Dict[str, Any]], None]) -> None:
    if fn not in _subscribers:
        _subscribers.append(fn)


def emit(name: str, **data: Any) -> None:
    """Publish an event. Never blocks on, and never raises from, a subscriber."""
    for fn in list(_subscribers):
        try:
            fn(name, data)
        except Exception as e:
            print(f"⚠️  Event {name}: a subscriber failed: {e}")
