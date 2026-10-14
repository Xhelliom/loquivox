"""
Checks for the companion bubble's alternative styles.

    python tests/test_companion_bubble_styles.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cairo

from loquivox.ui import companion_bubble as cb
from loquivox.ui import companion_bubble_styles as st

assert len(st.VARIANTS) == 3 and "goutte-plus" not in st.VARIANTS, st.VARIANTS
assert st.CHOICES == ("goutte", "plasma", "radar", "reacteur"), st.CHOICES
c = cb.SIZE // 2


def frame(name, state, t, level=0.0, rig=None, sim=None):
    """Draw ``state`` at time ``t`` after a warm-up, as the bubble would have it."""
    sim, rig = sim or st.Sim(), rig or st.Rig()
    look = None
    for i in range(int(t * 20) + 1):
        look = sim.step(1 / 20, state, level)
        st.draw(name, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)), look, rig, i / 20)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cb.SIZE, cb.SIZE)
    st.draw(name, cairo.Context(surf), look, rig, t)
    return surf


def alpha(surf, x, y):
    return surf.get_data()[y * surf.get_stride() + x * 4 + 3]


# every style × every state draws something visible, inside the frame
for name in st.VARIANTS:
    for state in st.DEMO_STATES:
        s = frame(name, state, 1.0, 0.7 if state in ("listening", "speaking") else 0.0)
        assert alpha(s, 0, 0) == 0 and alpha(s, cb.SIZE - 1, cb.SIZE - 1) == 0, (name, state, "corners")
        assert any(s.get_data()[3::4]), (name, state, "blank")

# each state shows in motion, not only in colour: standby moves, silent is still
for name in st.VARIANTS:
    assert bytes(frame(name, "idle", 1.0).get_data()) != bytes(frame(name, "idle", 1.4).get_data()), name
    sim, rig = st.Sim(), st.Rig()
    for i in range(80):                                   # settle into silence
        look = sim.step(1 / 20, "silent", 0.0)
        st.draw(name, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)), look, rig, i / 20)
    a, b = (bytes(frame(name, "silent", t, rig=rig, sim=sim).get_data()) for t in (4.0, 4.5))
    assert a == b, (name, "silent must be still")

# the voice changes the picture, and listening/speaking turn opposite ways
for name in st.VARIANTS:
    assert bytes(frame(name, "speaking", 1.0, 0.0).get_data()) != \
        bytes(frame(name, "speaking", 1.0, 0.9).get_data()), name
rig = st.Rig()
for state, sign in (("listening", 1), ("speaking", -1)):
    rig, look = st.Rig(), st.Sim().step(0.05, state, 0.0)
    look.amp = 1.0
    for i in range(20):
        rig.step(look, i / 20)
    assert rig.spin * sign > 0, (state, rig.spin)

# the shipped look stays the default and is exactly cb.paint
os.environ.pop("LOQUIVOX_BUBBLE_STYLE", None)
st._style = None
assert st.current_style() == st.DEFAULT_STYLE == "goutte"
look = cb.Look(rgb=cb.STATES["idle"], level=0.3)
a, b = (cairo.ImageSurface(cairo.FORMAT_ARGB32, cb.SIZE, cb.SIZE) for _ in range(2))
cb.paint(cairo.Context(a), look)
st.draw(st.DEFAULT_STYLE, cairo.Context(b), look, st.Rig(), 0.0)
assert bytes(a.get_data()) == bytes(b.get_data())
# the choice comes from config.toml, unknown -> goutte, and follows reload_config()
from loquivox import config as cfgmod
os.environ.pop("LOQUIVOX_BUBBLE_STYLE", None)
st._style = None
for value, want in (("radar", "radar"), ("goutte-plus", "goutte"), ("nope", "goutte"), ("plasma", "plasma")):
    cfgmod._load_user_toml = lambda v=value: {"companion": {"bubble_style": v}}
    cfgmod.CFG = cfgmod._build_config()
    assert st.current_style() == want, (value, st.current_style())

# --demo --style goutte must force the default, not fall back to config.toml
cfgmod._load_user_toml = lambda: {"companion": {"bubble_style": "radar"}}
cfgmod.CFG = cfgmod._build_config()
st.set_style("goutte")
assert st.current_style() == "goutte"
st._style = None

# Apply repaints the live bubble even when it has no timer (silent)
from loquivox.handlers import companion as comp
kicked = []
class _Bubble:
    def _kick(self): kicked.append(1)
comp._companion = comp.Companion.__new__(comp.Companion)
comp._companion.bubble = _Bubble()
comp.restyle()
assert kicked, "restyle() must repaint the bubble"
src = (Path(__file__).parent.parent / "src/loquivox/ui/settings_dialog.py").read_text()
assert "companion.restyle()" in src.split("def _on_apply_companion")[1].split("def ")[0]

print("✓ companion bubble styles OK")
