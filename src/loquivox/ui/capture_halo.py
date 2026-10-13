"""
Shows the user what the assistant just looked at.

``show_capture(png_path)`` is the only entry point and can be called from any
thread. If the focused window's geometry is known (platform backend), a blue
halo fades around exactly that window; otherwise a thumbnail of the capture
appears briefly near the bubble. Either way the window never takes focus and
lets every click through.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import cairo

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib, Gtk

try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False

from loquivox.ui.recording_overlay import BOTTOM_MARGIN

Geometry = Tuple[int, int, int, int]

DURATION_MS = 1000
HALO_RGB = (0.25, 0.55, 1.0)
HALO_WIDTH = 6
THUMB_W = 240
#: the talk bubble is bottom-anchored above the recording overlay; sit below it
THUMB_MARGIN = BOTTOM_MARGIN + 160

_current: Optional["_Flash"] = None


class _Flash(Gtk.Window):
    """A click-through, focus-less window that fades in/out then destroys itself."""

    def __init__(self, geometry: Optional[Geometry], png: Optional[str], duration_ms: int):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.geometry = geometry
        self.kind = "halo" if geometry else "thumb"
        self.pixbuf = None
        if not geometry and png:
            try:
                self.pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(png, THUMB_W, -1, True)
            except GLib.Error:
                pass
        self.duration = duration_ms
        self.t0 = time.monotonic()
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        screen = self.get_screen()
        if screen.get_rgba_visual() and screen.is_composited():
            self.set_visual(screen.get_rgba_visual())
        self.connect("draw", self._draw)
        self.connect("realize", lambda *_: self.input_shape_combine_region(cairo.Region()))

        if HAS_LAYER_SHELL and GtkLayerShell.is_supported():
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_namespace(self, "loquivox-capture")
            GtkLayerShell.set_exclusive_zone(self, -1)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            if geometry:  # whole output; the halo is painted at its coordinates
                for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                             GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                    GtkLayerShell.set_anchor(self, edge, True)
            else:
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
                GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, THUMB_MARGIN)
        else:
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            area = screen.get_display().get_primary_monitor().get_geometry()
            if geometry:
                self.move(area.x, area.y)
            else:
                self.move(area.x + (area.width - THUMB_W) // 2, area.y + area.height - THUMB_MARGIN)
        if geometry and not (HAS_LAYER_SHELL and GtkLayerShell.is_supported()):
            area = screen.get_display().get_primary_monitor().get_geometry()
            self.set_default_size(area.width, area.height)
        elif not geometry:
            h = self.pixbuf.get_height() if self.pixbuf else 1
            self.set_default_size(THUMB_W, h)
        self.show_all()
        GLib.timeout_add(16, self._tick)

    def _opacity(self) -> float:
        """Quick fade in, long fade out: 0→1 over the first 15%, →0 after."""
        p = (time.monotonic() - self.t0) * 1000 / self.duration
        return min(p / 0.15, 1.0) if p < 0.15 else max(0.0, (1 - p) / 0.85)

    def _tick(self) -> bool:
        global _current
        if (time.monotonic() - self.t0) * 1000 >= self.duration:
            if _current is self:
                _current = None
            self.destroy()
            return False
        self.queue_draw()
        return True

    def _draw(self, _w, cr) -> bool:
        a = self._opacity()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.geometry:
            x, y, w, h = self.geometry
            for i in range(HALO_WIDTH):  # soft edge: stacked strokes, fading outward
                cr.set_source_rgba(*HALO_RGB, a * (1 - i / HALO_WIDTH) * 0.9)
                cr.set_line_width(1)
                cr.rectangle(x - i - 0.5, y - i - 0.5, w + 2 * i + 1, h + 2 * i + 1)
                cr.stroke()
        elif self.pixbuf:
            _paint_pixbuf(cr, self.pixbuf, a)
        return True


def _paint_pixbuf(cr, pixbuf, alpha: float) -> None:
    from gi.repository import Gdk
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint_with_alpha(alpha)


def show_capture(png_path: Optional[str] = None, *, geometry: Optional[Geometry] = None,
                 duration_ms: int = DURATION_MS) -> None:
    """Flash what was captured. Any thread; never blocks on GTK."""
    if geometry is None:
        try:
            from loquivox.platform import get_screenshot
            geometry = get_screenshot().focused_window_geometry()
        except Exception:
            geometry = None
    if geometry is None and not png_path:
        return

    def _show() -> bool:
        global _current
        if _current is not None:
            _current.destroy()
        _current = _Flash(geometry, png_path, duration_ms)
        return False

    GLib.idle_add(_show)
