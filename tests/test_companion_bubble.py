"""
Checks for the companion bubble: states, colours, and the remembered position.

    python tests/test_companion_bubble.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.ui import companion_bubble as cb

# Every state is there, and the colours tell them apart at a glance.
assert set(cb.STATES) == {"idle", "listening", "speaking", "cloud", "silent"}, cb.STATES
assert len(set(cb.STATES.values())) == len(cb.STATES), "two states share a colour"
r, g, b = cb.STATES["cloud"]
assert r > 0.7 and g < 0.4 and b < 0.4, "the cloud state must read red"
assert not any(r2 > 0.7 and g2 < 0.4 and b2 < 0.4
               for k, (r2, g2, b2) in cb.STATES.items() if k != "cloud"), "only cloud is red"

# The position survives a restart, and saving it keeps the other settings.
from loquivox.state import SettingsManager, AppState

with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "settings.json"
    object.__setattr__(cb.CFG, "SETTINGS_FILE", f)   # CFG is frozen
    assert cb.load_position() == cb.DEFAULT_POSITION
    f.write_text(json.dumps({"color_scheme": "x"}))
    cb.save_position((12, 345))
    assert cb.load_position() == (12, 345)
    assert json.loads(f.read_text())["color_scheme"] == "x"
    # SettingsManager.save (every settings toggle) must not wipe the position.
    SettingsManager.save(AppState())
    assert cb.load_position() == (12, 345), "SettingsManager.save erased the position"
    f.write_text("{not json")
    assert cb.load_position() == cb.DEFAULT_POSITION

# A drag cannot push the bubble off screen.
assert cb.clamp((-50, 9999), (1920, 1080)) == (0, 1080 - cb.SIZE)
# Clicks: a press/release without travel is a click, a drag is not.
from types import SimpleNamespace as NS

saved, clicks = [], []
cb.save_position = saved.append
bubble = NS(_on_click=lambda: clicks.append(1), _press=None, _dragging=False, _pos=(24, 24),
            _apply_position=lambda: None, _popup_menu=lambda ev: clicks.append("menu"))
bubble._on_press = lambda ev: cb.CompanionBubble._on_press(bubble, None, ev)
bubble._on_motion = lambda ev: cb.CompanionBubble._on_motion(bubble, None, ev)
bubble._on_release = lambda ev: cb.CompanionBubble._on_release(bubble, None, ev)
ev = lambda b, x=10, y=10: NS(button=b, x=x, y=y)

bubble._on_press(ev(1)); bubble._on_release(ev(1))
assert clicks == [1] and not saved, (clicks, saved)
bubble._on_press(ev(1)); bubble._on_motion(ev(1, 10 + cb.DRAG_THRESHOLD + 5)); bubble._on_release(ev(1))
assert clicks == [1] and saved == [bubble._pos], (clicks, saved)
bubble._on_press(ev(3))
assert clicks == [1, "menu"], clicks

# The redraw timer only runs while a state pulses, and is removed afterwards.
live = set()
cb.GLib = NS(timeout_add=lambda ms, f: live.add(1) or 1, source_remove=lambda t: live.discard(1))
t = NS(_timer=0, _state="idle", _tick=None)
t._stop_timer = lambda: cb.CompanionBubble._stop_timer(t)
for state, expected in (("idle", False), ("listening", True), ("cloud", True),
                        ("silent", False), ("speaking", True), ("idle", False)):
    t._state = state
    cb.CompanionBubble._sync_timer(t)
    assert bool(live) == expected == bool(t._timer), state

print("✓ companion bubble OK")
