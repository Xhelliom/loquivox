"""
Checks for the capture halo: niri's geometry parsing and the two flashes
(halo around the window, thumbnail fallback) appearing, then disappearing.

    python tests/test_capture_halo.py        (the GTK half needs a display)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.platform.wayland import niri_window_geometry

# The window sits inside its tile, offset by the border.
info = {"layout": {"tile_pos_in_workspace_view": [10.0, 20.0],
                   "window_size": [800, 600],
                   "window_offset_in_tile": [2.0, 3.0]}}
assert niri_window_geometry(info) == (12, 23, 800, 600), niri_window_geometry(info)
# Real niri 26.04 payload of a tiled window: no position, only the layout slot.
tiled = {"layout": {"pos_in_scrolling_layout": [2, 1], "tile_size": [460.0, 872.0],
                    "window_size": [460, 872], "tile_pos_in_workspace_view": None,
                    "window_offset_in_tile": [0.0, 0.0]}}
assert niri_window_geometry(tiled) is None
# Without a tile position: unknown, not zero.
assert niri_window_geometry({"layout": {"tile_pos_in_workspace_view": None,
                                        "window_size": [1, 1]}}) is None
assert niri_window_geometry({}) is None
assert niri_window_geometry(None) is None
print("✓ niri geometry parsing OK")

from loquivox.ui import capture_halo
from gi.repository import Gdk, GLib, Gtk

if Gdk.Display.get_default() is None:
    print("(no display: GTK half skipped)")
    sys.exit(0)

png = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
from gi.repository import GdkPixbuf
GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48).savev(png, "png", [], [])

seen = {}
def check(kind, geometry):
    capture_halo.show_capture(png, geometry=geometry, duration_ms=300)
    def probe():
        w = capture_halo._current
        seen[kind] = (w is not None, w and w.kind, w and w.get_accept_focus())
        seen[kind + "_realized"] = bool(w and w.get_realized())
        return False
    GLib.timeout_add(100, probe)
    def after():
        seen[kind + "_gone"] = capture_halo._current is None
        return False
    GLib.timeout_add(1200, after)

# The thumbnail case must not depend on what the real backend knows.
import loquivox.platform as _platform
class _NoGeometry:
    def focused_window_geometry(self):
        return None
_platform.get_screenshot = lambda: _NoGeometry()

assert capture_halo.CLICK_THROUGH.is_empty()  # clicks pass through

check("halo", (100, 100, 400, 300))
GLib.timeout_add(1500, lambda: check("thumb", None) or False)
GLib.timeout_add(3000, Gtk.main_quit)
Gtk.main()

assert seen["halo"] == (True, "halo", False), seen
assert seen["halo_realized"] and seen["thumb_realized"], seen
assert seen["halo_gone"], seen
assert seen["thumb"] == (True, "thumb", False), seen
assert seen["thumb_gone"], seen
print("✓ halo and thumbnail appear then disappear")
