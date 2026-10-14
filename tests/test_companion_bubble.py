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

# Every state is there; the water says the cost: blue standby, red for any cloud
# session (cloud, listening, speaking), grey when silent.
assert set(cb.STATES) == {"idle", "listening", "speaking", "cloud", "silent"}, cb.STATES
def _is_red(c): return c[0] > 0.8 and c[1] < 0.4 and c[2] < 0.4
for k in ("cloud", "listening", "speaking"):
    assert _is_red(cb.STATES[k]), k
assert not _is_red(cb.STATES["idle"]) and not _is_red(cb.STATES["silent"])
assert cb.STATES["idle"][2] > cb.STATES["idle"][0], "standby reads blue"
assert cb.SILENT_ALPHA == 0.6 and cb.DROP == 56 and cb.SIZE >= 84

# Springs: critical damping never overshoots; an underdamped one does; both settle.
s1 = cb.Spring(0.0, response=0.3, damping=1.0); s1.target = 1.0
peak = 0.0
for _ in range(120):
    s1.step(1 / 60); peak = max(peak, s1.value)
assert peak <= 1.001 and s1.rest, (peak, s1.value)
s2 = cb.Spring(0.0, response=0.25, damping=0.7); s2.target = 1.0
peak = 0.0
for _ in range(120):
    s2.step(1 / 60); peak = max(peak, s2.value)
assert peak > 1.0 and s2.rest
assert cb.Spring(0.5).rest

# Who talks, without colour: user → rightwards, companion → leftwards.
assert cb.wave_dir("listening") == 1 and cb.wave_dir("speaking") == -1
assert cb.wave_dir("idle") == 1

# The level is clamped to 0..1 and the draw never fails.
import cairo
def render(**kw):
    look = cb.Look(**kw)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cb.SIZE, cb.SIZE)
    cb.paint(cairo.Context(surf), look)
    return surf
def alpha_at(surf, x, y):
    return surf.get_data()[y * surf.get_stride() + x * 4 + 3]
for st in cb.STATES:
    surf = render(rgb=cb.STATES[st], alpha=1.0, level=0.8, amp=1.0, speaking=st == "speaking")
c = cb.SIZE // 2
surf = render(rgb=cb.STATES["idle"], alpha=1.0, level=0.0, amp=1.0)
assert alpha_at(surf, c, c) > 100, "the drop is opaque enough to be seen"
assert alpha_at(surf, c, c + cb.DROP // 2 + 3) > 0, "the shadow/rim reaches past the glass"
assert alpha_at(surf, 0, 0) == 0, "the corners stay transparent"
# a silent drop and a loud one differ only in the water
assert render(rgb=(1, 0, 0), alpha=1, level=0.0, amp=0.0).get_data() != \
    render(rgb=(0, 0, 1), alpha=1, level=0.0, amp=0.0).get_data()

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
bubble = NS(_on_click=lambda: clicks.append(1), _press=None, _drag=cb.Drag(), _settle=0, _pos=(24, 24),
            _screen_size=lambda: (1920, 1080), add_tick_callback=lambda f: None, _on_settle=None,
            _apply_position=lambda: None, _rest_scale=lambda: None, _stretch_toward=lambda dx, dy: None,
            _sp={"stretch": cb.Spring()}, _popup_menu=lambda ev: clicks.append("menu"))
bubble._on_press = lambda ev: cb.CompanionBubble._on_press(bubble, None, ev)
bubble._on_motion = lambda ev: cb.CompanionBubble._on_motion(bubble, None, ev)
bubble._press_pos_update = lambda: cb.CompanionBubble._press_pos_update(bubble)
bubble._on_release = lambda ev: cb.CompanionBubble._on_release(bubble, None, ev)
ev = lambda b, x=10, y=10: NS(button=b, x=x, y=y)

bubble._on_press(ev(1)); bubble._on_release(ev(1))
assert clicks == [1] and not saved, (clicks, saved)
bubble._on_press(ev(1)); bubble._on_motion(ev(1, 10 + cb.DRAG_THRESHOLD + 5)); bubble._on_release(ev(1))
assert clicks == [1] and saved == [bubble._pos], (clicks, saved)
bubble._on_press(ev(3))
assert clicks == [1, "menu"], clicks

# The render timer: ~60 fps only while something moves, 15 fps for the idle
# sway, none when silent and settled, hidden, or when GTK animations are off.
iv = cb.tick_interval
assert iv("listening", moving=False, loud=False, anim=True, visible=True) == 16
assert iv("speaking", moving=False, loud=False, anim=True, visible=True) == 16
assert iv("idle", moving=True, loud=False, anim=True, visible=True) == 16
assert iv("idle", moving=False, loud=True, anim=True, visible=True) == 16
assert iv("idle", moving=False, loud=False, anim=True, visible=True) == 66
assert iv("cloud", moving=False, loud=False, anim=True, visible=True) == 66
assert iv("silent", moving=False, loud=False, anim=True, visible=True) == 0
assert iv("silent", moving=True, loud=False, anim=True, visible=True) == 16
assert iv("idle", moving=True, loud=True, anim=True, visible=False) == 0
assert iv("speaking", moving=True, loud=True, anim=False, visible=True) == 0

# Drag: events arriving before the new margins are committed are ignored, so the
# bubble follows the pointer 1:1 instead of adding the gap once per event.
d = cb.Drag(); d.start(40, 40, (100, 100))
assert d.motion(41, 41, True) is None and not d.dragging, "below threshold = a click"
(pos, (dx, dy)) = d.motion(60, 50, True)
assert pos == (80, 110) and (dx, dy) == (20, 10), pos
d.press = (*d.press[:2], pos)
assert d.motion(60, 50, False) is None, "stale origin: ignored"
pos2, _ = d.motion(70, 50, True)       # settled; surface under pointer again
assert pos2 == (50, 120), pos2
assert cb.clamp((3804, 2124), (3840, 2160)) == (3840 - cb.SIZE, 2160 - cb.SIZE)
print("drag ok")

print("✓ companion bubble OK")
