"""
Screen-edge hotkey cheat sheet.

A thin tab sits at the top-center of the screen; hovering it drops down a panel
listing every bound hotkey (keycap pill + what it does), and it retracts when
the pointer leaves. The "what were my shortcuts again?" surface.

Drawing reuses the recording overlay's primitives (keycaps, rounded rects,
colour scheme, fade easing) so both surfaces read as the same app.

On Wayland: gtk-layer-shell, anchored to the top edge (no exclusive zone, so it
never steals desktop space). On X11: a keep-above POPUP moved to the top edge.
"""
from __future__ import annotations

from typing import List, Tuple

import cairo

from loquivox.config import CFG, HOTKEY_DESCRIPTIONS
from loquivox.platform import SESSION_TYPE
from loquivox.state import STATE
from loquivox.ui.recording_overlay import HAS_LAYER_SHELL, GtkOverlay

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

if HAS_LAYER_SHELL:
    from gi.repository import GtkLayerShell


class HotkeyBar(Gtk.Window):
    """Top-of-screen tab that expands on hover into the hotkey list."""

    TAB_W, TAB_H = 130, 5     # resting tab: visible enough to aim at, small
                              # enough not to eat clicks meant for the desktop
    ROW_H = 26
    TITLE_H = 32
    PAD = 16
    KEY_GAP = 14              # between the keycap column and the descriptions
    SLIDE_PX = 8
    FRAME_MS = 16

    # ------------------------------------------------------------- lifecycle
    @classmethod
    def start(cls) -> None:
        """Show the bar if enabled — no-op when already up or turned off."""
        if STATE.hotkey_bar_window or not STATE.show_hotkey_bar:
            return
        STATE.hotkey_bar_window = cls()

    @classmethod
    def stop(cls) -> None:
        """Tear the bar down (Settings toggle)."""
        win, STATE.hotkey_bar_window = STATE.hotkey_bar_window, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def __init__(self):
        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
        else:
            super().__init__(type=Gtk.WindowType.POPUP)

        self._font = GtkOverlay._ui_font_family()
        self._opacity = 0.0       # 0 = tab, 1 = panel; eased by _animate
        self._open = False
        self._timer = None
        self._layout()

        self._setup_window()
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.TAB_W, self.TAB_H)
        self.area.connect("draw", self._on_draw)
        self.add(self.area)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _setup_window(self) -> None:
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_accept_focus(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "loquivox-hotkeys")
            GtkLayerShell.set_exclusive_zone(self, -1)   # overlay, reserve nothing
            # Anchored to the top edge only → the compositor centers it there.
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        else:
            self.set_keep_above(True)
        self.set_default_size(self.TAB_W, self.TAB_H)
        self._move_top_center(self.TAB_W)

    def _on_destroy(self, *_a) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    # ---------------------------------------------------------------- layout
    @staticmethod
    def rows() -> List[Tuple[List[str], str]]:
        """
        ``([key, …], description)`` for every BOUND hotkey, in config order.

        Unbound actions ('refine' by default) are skipped — there is no shortcut
        to recall. Read live, so a rebind in Settings shows on the next hover.
        """
        out: List[Tuple[List[str], str]] = []
        for action in CFG.HOTKEY_DEFS:
            keys = GtkOverlay.primary_keys(action)
            if keys:
                out.append((keys, HOTKEY_DESCRIPTIONS.get(action, action)))
        return out

    def _layout(self) -> None:
        """
        (Re)measure the panel against the live bindings.

        Measuring means drawing on a throwaway 1x1 surface at alpha 0 — the same
        code path as the real paint, so the window can never be a few px short.
        """
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        black = (0.0, 0.0, 0.0)
        self._rows = self.rows()
        self._key_w = max((self._draw_keys(cr, 0, 0, keys, black, black, 0.0)
                           for keys, _ in self._rows), default=0.0)
        desc_w = max((GtkOverlay._draw_text_at(cr, self._font, desc, 0, 0, 8.5, black, 0.0)
                      for _, desc in self._rows), default=0.0)
        self._panel_w = int(self.PAD * 2 + self._key_w + self.KEY_GAP + desc_w)
        self._panel_h = int(self.TITLE_H + len(self._rows) * self.ROW_H + self.PAD)

    def _move_top_center(self, w: int) -> None:
        """X11 only — layer-shell keeps the surface centered on its own."""
        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            return
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        g = monitor.get_geometry()
        self.move(g.x + (g.width - w) // 2, g.y)

    def _resize(self, w: int, h: int) -> None:
        self.area.set_size_request(w, h)
        self.resize(w, h)
        self.queue_resize()
        self._move_top_center(w)

    # ------------------------------------------------------------- hover/anim
    def _on_enter(self, _w, event) -> bool:
        # INFERIOR crossings are the pointer moving onto our own child widget,
        # not a real enter/leave — ignoring them stops an open/close flicker.
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._open = True
        self._layout()                      # pick up any rebind since last hover
        self._resize(self._panel_w, self._panel_h)
        self._start_anim()
        return False

    def _on_leave(self, _w, event) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._open = False
        self._start_anim()
        return False

    def _start_anim(self) -> None:
        """Run the easing tick only while there is something to ease."""
        if self._timer is None:
            self._timer = GLib.timeout_add(self.FRAME_MS, self._animate)

    def _animate(self) -> bool:
        target = 1.0 if self._open else 0.0
        self._opacity += (target - self._opacity) * 0.22
        done = abs(target - self._opacity) < 0.02
        if done:
            self._opacity = target
            if not self._open:              # fully retracted → back to the tab
                self._resize(self.TAB_W, self.TAB_H)
            self._timer = None
        self.area.queue_draw()
        return not done

    # ----------------------------------------------------------------- paint
    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        w = widget.get_allocated_width()
        a = GtkOverlay._smoothstep(self._opacity)
        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])
        bg = GtkOverlay._hex_to_rgb(scheme["bg"])
        accent = GtkOverlay._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt = GtkOverlay._hex_to_rgb(scheme["text"])
        surface = GtkOverlay._hex_to_rgb(scheme.get("surface", scheme["bg"]))

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Resting tab, fading out as the panel takes over. Drawn from above the
        # top edge so only its rounded bottom shows.
        if a < 1.0:
            GtkOverlay._rounded_rect_path(cr, (w - self.TAB_W) / 2, -4,
                                          self.TAB_W, self.TAB_H + 4, 3)
            cr.set_source_rgba(*accent, 0.5 * (1.0 - a))
            cr.fill()
        if a <= 0.02:
            return True

        cr.translate(0, -(1.0 - a) * self.SLIDE_PX)   # slides down as it opens

        # Panel body: square at the screen edge, rounded at the bottom.
        GtkOverlay._rounded_rect_path(cr, 0, -14, w, self._panel_h + 14, 14)
        cr.set_source_rgba(*bg, 0.96 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*accent, 0.30 * a)
        cr.set_line_width(1)
        cr.stroke()

        GtkOverlay._draw_text_at(cr, self._font, "Loquivox — hotkeys", self.PAD,
                                 self.TITLE_H / 2 + 2, 9.0, accent, a, Pango.Weight.BOLD)
        for i, (keys, desc) in enumerate(self._rows):
            cy = self.TITLE_H + i * self.ROW_H + self.ROW_H / 2
            self._draw_keys(cr, self.PAD, cy, keys, surface, txt, a)
            GtkOverlay._draw_text_at(cr, self._font, desc,
                                     self.PAD + self._key_w + self.KEY_GAP, cy,
                                     8.5, txt, 0.85 * a)
        return True

    @staticmethod
    def _draw_keys(cr, x, cy, keys, base, txt, a) -> float:
        """
        Draw one row's keycap run at (x, cy) and return its width.

        With ``a=0`` it paints nothing and only measures — that's how the panel
        sizes itself, so measurement and paint can't drift apart.
        """
        cx = x
        for i, key in enumerate(keys):
            if i:  # chord: a dim "+" between the caps
                cx += 3 + GtkOverlay._draw_text_at(cr, "Monospace", "+", cx + 3, cy,
                                                   6.5, txt, 0.45 * a) + 3
            cx += GtkOverlay._draw_keycap(cr, cx, cy, key, base, txt, a)
        return cx - x


if __name__ == "__main__":
    # Self-check for the pure row/measure logic (no window, no compositor).
    rows = HotkeyBar.rows()
    assert rows, rows
    assert all(keys and desc for keys, desc in rows), rows
    # Unbound actions never make a row; bound ones always do.
    bound = [a for a in CFG.HOTKEY_DEFS if GtkOverlay.primary_keys(a)]
    assert len(rows) == len(bound), (rows, bound)

    bar = HotkeyBar.__new__(HotkeyBar)          # skips __init__ → no GTK window
    bar._font = "Sans"
    bar._layout()
    assert bar._key_w > 0 and bar._panel_w > bar._key_w, (bar._key_w, bar._panel_w)
    assert bar._panel_h >= HotkeyBar.TITLE_H + len(rows) * HotkeyBar.ROW_H
    print(f"✓ hotkey bar OK — {len(rows)} rows → {bar._panel_w}x{bar._panel_h}px")
