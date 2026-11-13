"""
Self-check for [hotkeys] active (all / chords / none), no keyboard needed:

    PYTHONPATH=src python tests/test_hotkey_scope.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from evdev import ecodes  # noqa: E402
from loquivox.config import spec_active  # noqa: E402
from loquivox.handlers.keyboard import KeyboardHandler as K  # noqa: E402
from loquivox.state import STATE  # noqa: E402

base = replace(config_module.CFG, HOTKEY_DEFS={
    **config_module.CFG.HOTKEY_DEFS,
    "dictation": ("ALT+SPACE / F3", ["ALT+SPACE", "F3"]),
    "pause": ("SPACE", ["SPACE"]),
    "cancel": ("ESC", ["ESC"]),
})


def press(code, *held):
    K._held = set(held) | {code}
    return K._match_binding(code)


def load(scope):
    K.reload_hotkeys(replace(base, HOTKEYS_ACTIVE=scope))
    STATE.recording = False
    STATE.talk_active = False
    STATE.stream_session = None


ALT, F = ecodes.KEY_LEFTALT, ecodes
try:
    load("all")
    assert press(F.KEY_F4) == "ai" and press(F.KEY_F3) == "dictation"
    assert press(F.KEY_SPACE, ALT) == "dictation"

    load("chords")
    for k in (F.KEY_F4, F.KEY_F6, F.KEY_F7, F.KEY_F8, F.KEY_F9, F.KEY_F10, F.KEY_F3):
        assert press(k) is None, k
    assert press(F.KEY_SPACE, ALT) == "dictation", "Alt+Space must still dictate"
    assert press(F.KEY_ESC) is None and press(F.KEY_SPACE) is None, "idle: no session keys"
    STATE.recording = True   # a recording started by Alt+Space
    assert press(F.KEY_ESC) == "cancel" and press(F.KEY_SPACE) == "pause"

    load("none")
    assert press(F.KEY_SPACE, ALT) is None and press(F.KEY_F4) is None
    STATE.recording = True
    assert press(F.KEY_ESC) == "cancel", "Esc must still stop a running recording"

    assert [spec_active("ai", s, sc) for sc in ("all", "chords", "none")
            for s in ("F4", "CTRL+F4")] == [True, True, False, True, False, False]
    assert spec_active("cancel", "ESC", "none")
finally:
    load("all")
print("✓ hotkey scope OK")
