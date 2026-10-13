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
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "settings.json"
    assert cb.load_position(f) == cb.DEFAULT_POSITION
    f.write_text(json.dumps({"color_scheme": "x"}))
    cb.save_position((12, 345), f)
    assert cb.load_position(f) == (12, 345)
    assert json.loads(f.read_text())["color_scheme"] == "x"
    f.write_text("{not json")
    assert cb.load_position(f) == cb.DEFAULT_POSITION

# A drag cannot push the bubble off screen.
assert cb.clamp((-50, 9999), (1920, 1080)) == (0, 1080 - cb.SIZE)
print("✓ companion bubble OK")
