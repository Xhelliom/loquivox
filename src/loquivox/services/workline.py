"""The focused window and the project it belongs to, as one line for the conversation."""
from __future__ import annotations

import re

from loquivox.services.board import _get


def work_line(window) -> str:
    """
    One line on what the user is working on, or "" when nothing is known.

    ``window`` is ``(title, app)`` of the focused window. The project is the
    board repo whose name appears in the title (a terminal or editor titles
    itself with its cwd); no board, no match, no window → "". Reads, calls no model.
    """
    if not window or not any(window):
        return ""
    title, app = window
    data = _get("/api/repos")
    repos = data.get("repos") if isinstance(data, dict) else None
    low = f"{title} {app}".lower()
    hit = next((r["name"] for r in repos or []
                if isinstance(r, dict) and r.get("name")
                and re.search(rf"(?<![\w-]){re.escape(r['name'].lower())}(?![\w-])", low)), None)
    line = f"window: {title}" + (f" ({app})" if app else "")
    return line + (f" — project: {hit}" if hit else "")
