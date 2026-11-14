"""
The focused window and its board project reach the session as one line: no
capture, no model; nothing when unknown.

    PYTHONPATH=src python tests/test_work_context.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.platform.base import ScreenshotBackend  # noqa: E402
from loquivox.services import workline  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402

REPOS = {"repos": [{"name": "loquivox"}, {"name": "collie-board"}]}


def line(window, repos=REPOS):
    with mock.patch.object(workline, "_get", return_value=repos):
        return workline.work_line(window)


# the repo's window names its project
assert "project: loquivox" in line(("nvim ~/git/perso/loquivox/src", "kitty"))
# no match: the window alone; "loquivox-old" is not "loquivox"
assert "project" not in line(("zsh ~/git/other", "kitty"))
assert "project" not in line(("~/git/loquivox-old", "kitty"))
# board down: window alone, never an error
assert line(("zsh loquivox", "kitty"), repos=None) == "window: zsh loquivox (kitty)"
# unknown window (no niri/X11): nothing at all
assert line(None) == "" and line(("", "")) == ""
assert ScreenshotBackend.focused_window_info(None) is None

# it rides in the clause, and only when known
s = TalkSession(write=False)
assert s.context_clause() == ""
s.work = line(("nvim loquivox", "kitty"))
assert "project: loquivox" in s.context_clause()
print("ok")
