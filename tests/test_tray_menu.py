"""
Self-check for the tray menu: PYTHONPATH=src python tests/test_tray_menu.py

The menu is history, Clear History, companion silence (F7's switch, ticked
while silent), the chat toggle, Settings and Quit — nothing else, so no setting
lives only in the tray.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from loquivox.state import STATE  # noqa: E402
from loquivox.ui.tray import TrayManager  # noqa: E402


_menu = None   # keep it: a menu garbage-collected takes its items with it


def entries():
    global _menu
    _menu = TrayManager._build_menu()
    return {i.get_child().get_text(): i for i in _menu.get_children()
            if i.get_child() is not None}   # separators have no label


def labels():
    return list(entries())


def item(label):
    return entries()[label]


STATE.answer_history = []
assert labels() == ["(No History)", "Clear History", "Companion silence",
                    "Show chat", "Settings…", "Quit"], labels()

STATE.presence_mode = "active"
assert not item("Companion silence").get_active()
STATE.presence_mode = "silent"
assert item("Companion silence").get_active()

STATE.chat_enabled = False
assert not item("Show chat").get_active()

# ticking it does what F7 does
calls = []
from loquivox.handlers import companion  # noqa: E402
companion.toggle_silence = lambda: calls.append(1)
STATE.presence_mode = "active"
item("Companion silence").set_active(True)
assert calls == [1]
print("ok")
