"""
The look plugin: re-read the screen when the user asks, and only then.

The session opens on one capture (or none). "Regarde ça", "et là ?" means the
window has changed and the user wants the model to see it, so the model calls
``look_at_screen``: the active window is captured again, described by the
vision model and handed to the session as its new context — the same worker
as the S key, so the capture halo, the framing and the description cost are
the ones already tuned there. Never called by a window change: a capture
uploads a window, and the user is the one who decides when.

``chat_only`` — a briefing has S for this, and an open conversation (F4, the
companion) has nothing else.
"""
from __future__ import annotations

from typing import Any, Dict

import loquivox.config as config_module
from loquivox.i18n import N_, _
from loquivox.services.plugins import Plugin, register


def _run(arguments: Dict[str, Any]) -> str:
    """The desk's background job: capture, describe, and set the context."""
    from loquivox.handlers.mode import ModeHandler  # lazy: mode.py imports services
    from loquivox.state import STATE

    session = STATE.talk_session
    if session is None:
        return ""
    return ModeHandler._talk_screen_worker(session) or ""


def result_message(arguments: Dict[str, Any], result: str) -> Dict[str, str]:
    """What the new capture shows, as a system message for either engine."""
    return {"role": "system", "content": (
        "SCREEN RE-CAPTURED just now at the user's request. This replaces any "
        f"earlier description of their screen:\n---\n{result}\n---\n"
        "Answer what they asked about THIS window, in a sentence or two."
    )}


PLUGIN = Plugin(
    name="look_at_screen",
    description=(
        "Capture the user's active window again and look at it. Call it when "
        "they ask you to look at something — \"regarde ça\", \"et là ?\", "
        "\"look at this\" — or after they say they changed window. Never call "
        "it on your own: a capture is only ever made on request. It takes a "
        "couple of seconds; say you are looking, and wait for what it shows."
    ),
    parameters={"type": "object", "properties": {}},
    started=(
        "The window is being captured and described. Say in a few words that "
        "you are looking, then stop: you have NOT seen it yet, so do not say "
        "what is on it. A system message will carry it when it is ready."
    ),
    run=_run,
    message=result_message,
    enabled=lambda: bool(config_module.CFG.TALK_LOOK_TOOL),
    chat_only=True,
    note=lambda args: _("👁️ Looking…"),
    result_note=N_("👁️ Window seen"),
)
register(PLUGIN)
