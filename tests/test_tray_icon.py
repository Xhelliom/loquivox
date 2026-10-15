"""
Self-check for the tray icon: PYTHONPATH=src python tests/test_tray_icon.py

One shipped icon per state — idle, recording, companion silent — never a theme
icon name, switched with set_icon_full (recording wins over silence).
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.state import STATE  # noqa: E402
from loquivox.ui import tray  # noqa: E402
from loquivox.ui.tray import TrayManager  # noqa: E402


class FakeIndicator:
    def __init__(self):
        self.icons = []

    def set_icon_full(self, path, desc):
        self.icons.append(Path(path).name)


def shown(recording, mode):
    STATE.recording, STATE.presence_mode = recording, mode
    STATE.indicator = FakeIndicator()
    TrayManager._refresh_icon()
    return STATE.indicator.icons


assert shown(False, "active") == ["loquivox-symbolic.svg"]
assert shown(True, "active") == ["loquivox-recording-symbolic.svg"]
assert shown(False, "silent") == ["loquivox-silent-symbolic.svg"]
assert shown(True, "silent") == ["loquivox-recording-symbolic.svg"]

for name in ("loquivox-symbolic.svg", "loquivox-recording-symbolic.svg",
             "loquivox-silent-symbolic.svg"):
    assert (Path(tray.__file__).parent.parent / "assets" / name).is_file(), name
assert "emblem-favorite" not in Path(tray.__file__).read_text()
print("ok")
