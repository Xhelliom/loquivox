"""
The companion's bubble: a tiny always-on-top dot, no transcript.

One small Cairo window (top-right by default) whose colour is the state of the
companion — local standby, listening, speaking, cloud session open (RED: the
cost is visible), silent. Never takes focus. Drag it anywhere with the mouse; the
spot is remembered in settings.json. A click calls ``on_click``, a right click
pops a menu of presence modes.

Wayland: gtk-layer-shell anchored top+right, dragged by changing the margins
(``begin_move_drag`` only exists on X11). X11: a keep-above POPUP moved by hand.

    python -m loquivox.ui.companion_bubble     # demo: cycles through the states
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

import cairo

from loquivox.config import CFG
from loquivox.decorators import run_on_main_thread
from loquivox.platform import SESSION_TYPE
from loquivox.state import SettingsManager
from loquivox.ui.recording_overlay import HAS_LAYER_SHELL

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk

if HAS_LAYER_SHELL:
    from gi.repository import GtkLayerShell

SIZE = 36                        # window side, px
DEFAULT_POSITION = (24, 24)      # (distance from the right edge, from the top)
SETTINGS_KEY = "companion_position"
PULSING = ("listening", "speaking", "cloud")   # the only states that animate
DRAG_THRESHOLD = 4               # px of travel before a press becomes a drag

# state → colour (r, g, b). Shape differs too (see _on_draw) so it never
# rests on colour alone.
STATES = {
    "idle":      (0.45, 0.50, 0.58),   # local standby: dim grey-blue
    "listening": (0.20, 0.72, 0.45),   # green
    "speaking":  (0.25, 0.55, 0.95),   # blue
    "cloud":     (0.90, 0.20, 0.20),   # red: the cloud session is open and billed
    "silent":    (0.25, 0.25, 0.28),   # dark: muted
}


def load_position() -> Tuple[int, int]:
    try:
        x, y = SettingsManager.load()[SETTINGS_KEY]
        return int(x), int(y)
    except Exception:
        return DEFAULT_POSITION


def save_position(pos: Tuple[int, int]) -> None:
    try:
        SettingsManager.update(SETTINGS_KEY, [int(pos[0]), int(pos[1])])
    except Exception as e:
        print(f"⚠️ Failed to save companion position: {e}")


def clamp(pos: Tuple[int, int], screen: Tuple[int, int]) -> Tuple[int, int]:
    """Keep the whole bubble on screen."""
    return (max(0, min(pos[0], screen[0] - SIZE)),
            max(0, min(pos[1], screen[1] - SIZE)))


class CompanionBubble(Gtk.Window):
    def __init__(self, on_click: Optional[Callable[[], None]] = None,
                 modes: Sequence[Tuple[str, Callable[[], None]]] = ()):
        """``modes``: ``(label, callback)`` pairs for the right-click menu."""
        wl = HAS_LAYER_SHELL and SESSION_TYPE == "wayland"
        super().__init__(type=Gtk.WindowType.TOPLEVEL if wl else Gtk.WindowType.POPUP)
        self._wl = wl
        self._on_click = on_click
        self._modes = list(modes)
        self._state = "idle"
        self._phase = 0.0
        self._pos = load_position()
        self._press = None       # (x, y, pos) while the left button is down
        self._dragging = False

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_accept_focus(False)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        self.set_default_size(SIZE, SIZE)
        if wl:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "loquivox-companion")
            GtkLayerShell.set_exclusive_zone(self, -1)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        else:
            self.set_keep_above(True)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(SIZE, SIZE)
        self.area.connect("draw", self._on_draw)
        self.add(self.area)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self._apply_position()
        self._timer = 0          # redraw timer, alive only while a state pulses
        self.connect("destroy", lambda *_: self._stop_timer())
        self.show_all()

    # ----------------------------------------------------------------- state
    @run_on_main_thread
    def set_state(self, state: str) -> None:
        """Thread-safe. Unknown names are ignored."""
        if state in STATES and state != self._state:
            self._state = state
            self._sync_timer()
            self.area.queue_draw()   # once more: the last frame of a stopped pulse

    def _sync_timer(self) -> None:
        if self._state in PULSING and not self._timer:
            self._timer = GLib.timeout_add(50, self._tick)
        elif self._state not in PULSING:
            self._stop_timer()

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # -------------------------------------------------------------- position
    def _screen_size(self) -> Tuple[int, int]:
        display = Gdk.Display.get_default()
        g = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
        return g.width, g.height

    def _apply_position(self) -> None:
        self._pos = clamp(self._pos, self._screen_size())
        mr, mt = self._pos
        if self._wl:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, mr)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, mt)
        else:
            display = Gdk.Display.get_default()
            g = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
            self.move(g.x + g.width - SIZE - mr, g.y + mt)

    # ----------------------------------------------------------------- mouse
    def _on_press(self, _w, ev) -> bool:
        if ev.button == 1:
            self._press, self._dragging = (ev.x, ev.y, self._pos), False
        elif ev.button == 3:
            self._popup_menu(ev)
        return True

    def _on_motion(self, _w, ev) -> bool:
        if self._press is None:
            return False
        px, py, (mr, mt) = self._press
        dx, dy = ev.x - px, ev.y - py
        if not self._dragging and math.hypot(dx, dy) < DRAG_THRESHOLD:
            return True
        self._dragging = True
        # The surface moves under the pointer, so (x - press x) shrinks back to
        # ~0 after each step: relative deltas stay stable, root coords would not.
        self._pos = (mr - dx, mt + dy)
        self._apply_position()
        self._press = (px, py, self._pos)
        return True

    def _on_release(self, _w, ev) -> bool:
        if ev.button != 1 or self._press is None:
            return False
        self._press = None
        if self._dragging:
            save_position(self._pos)
        elif self._on_click:
            self._on_click()
        return True

    def _popup_menu(self, ev) -> None:
        menu = Gtk.Menu()
        for label, cb in self._modes:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, cb=cb: cb())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(ev)

    # ----------------------------------------------------------------- paint
    def _tick(self) -> bool:
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        self.area.queue_draw()
        return True

    def _on_draw(self, _w, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        c, s = SIZE / 2, self._state
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        r = SIZE * (0.26 + {"listening": 0.06, "speaking": 0.05, "cloud": 0.04}.get(s, 0) * pulse)
        cr.set_source_rgba(*STATES[s], 0.25)                 # halo
        cr.arc(c, c, r + 5, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*STATES[s], 0.95)
        cr.arc(c, c, r, 0, 2 * math.pi)
        if s in ("idle", "silent"):                          # hollow = not engaged
            cr.set_line_width(2.5)
            cr.stroke()
            if s == "silent":                                # + slash
                cr.move_to(c - r * .7, c + r * .7)
                cr.line_to(c + r * .7, c - r * .7)
                cr.stroke()
        else:
            cr.fill()
        return True


if __name__ == "__main__":
    import itertools
    bubble = CompanionBubble(on_click=lambda: print("click"),
                             modes=[(m, lambda m=m: print("mode", m)) for m in ("Veille", "Toujours à l'écoute", "Muet")])
    states = itertools.cycle(STATES)

    def _next():
        s = next(states)
        print("state:", s)
        bubble.set_state(s)
        return True

    GLib.timeout_add(1500, _next)
    bubble.connect("destroy", Gtk.main_quit)
    Gtk.main()
