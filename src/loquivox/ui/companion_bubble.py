"""
The companion's bubble: a drop of water on the desktop, no transcript.

A 56 px glass drop (Cairo, no WebView) in an 88 px window that leaves room for
its shadow. The *water* says the state: light blue on local standby, RED as soon
as a cloud session is open (the cost is visible), faded grey when silent.
Colours are crossfaded by springs, never cut. The waves say the voice: their
height follows the real audio level, rightwards when the user talks, leftwards
(with a faint white glow) when the companion does, a barely-there sway on
standby, flat and still when silent. Never takes focus. Drag it anywhere; the
spot is remembered in settings.json. A click calls ``on_click``, a right click
pops a menu of presence modes.

Motion: every value is a spring that restarts from what is on screen, so any
animation can be interrupted. The render timer runs at ~60 fps only while
something moves, 15 fps for the standby sway, and not at all otherwise. With
GTK animations off there are no waves and no springs: colour and water level
alone carry the state.

Wayland: gtk-layer-shell anchored top+right, dragged by changing the margins
(``begin_move_drag`` only exists on X11). X11: a keep-above POPUP moved by hand.

    python -m loquivox.ui.companion_bubble --demo   # cycles states, fake audio level
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
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

SIZE = 88                        # window side, px: the drop plus its shadow and glow
DROP = 56                        # the drop's diameter, px (logical: GTK scales it)
DEFAULT_POSITION = (24, 24)      # (distance from the right edge, from the top)
SETTINGS_KEY = "companion_position"
DRAG_THRESHOLD = 4               # px of travel before a press becomes a drag
SILENT_ALPHA = 0.6               # the grey water of the silent mode
WATER = 0.55                     # share of the drop filled with water
LOUD = 0.02                      # level above which the waves count as moving

# state → water colour (r, g, b). Cost reads from colour, who talks from motion.
_BLUE, _RED, _GREY = (0.353, 0.706, 0.941), (0.898, 0.282, 0.302), (0.55, 0.57, 0.60)
STATES = {
    "idle":      _BLUE,   # local standby
    "listening": _RED,    # the user talks to an open cloud session
    "speaking":  _RED,    # the companion talks
    "cloud":     _RED,    # a cloud session is open and billed
    "silent":    _GREY,   # muted
}


class Spring:
    """Damped spring (Apple's response/damping): chases ``target`` from wherever it is."""

    def __init__(self, value: float = 0.0, response: float = 0.3, damping: float = 1.0):
        self.value = self.target = value
        self.velocity = 0.0
        self.response, self.damping = response, damping

    def step(self, dt: float) -> None:
        w = 2 * math.pi / self.response
        n = max(1, math.ceil(dt * 120))
        h = dt / n
        for _ in range(n):
            self.velocity += (w * w * (self.target - self.value)
                              - 2 * self.damping * w * self.velocity) * h
            self.value += self.velocity * h

    @property
    def rest(self) -> bool:
        return abs(self.target - self.value) < 1e-3 and abs(self.velocity) < 1e-2

    def jump(self) -> None:
        self.value, self.velocity = self.target, 0.0


def wave_dir(state: str) -> int:
    """+1: waves run left to right (the user talks); -1: right to left (the companion)."""
    return -1 if state == "speaking" else 1


def tick_interval(state: str, *, moving: bool, loud: bool, anim: bool, visible: bool) -> int:
    """Render timer period in ms; 0 = no timer. ``moving``: a spring is still travelling."""
    if not visible or not anim:
        return 0
    if moving or loud or state in ("listening", "speaking"):
        return 16
    return 0 if state == "silent" else 66


@dataclass
class Look:
    """Everything ``paint`` needs, as plain numbers (so it can be drawn offscreen)."""
    rgb: Tuple[float, float, float]
    alpha: float = 1.0          # water opacity
    level: float = 0.0          # smoothed audio level 0..1
    amp: float = 1.0            # 0 = flat water, 1 = full waves
    phase: Tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    opacity: float = 1.0
    stretch: float = 0.0        # 0..0.08 along ``angle``
    angle: float = 0.0
    speaking: float = 0.0       # 0..1 white inner glow
    state: str = "idle"         # for the alternative styles (companion_bubble_styles)


def paint(cr: cairo.Context, look: Look) -> None:
    c, r = SIZE / 2, DROP / 2
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.push_group()
    cr.translate(c, c)
    cr.rotate(look.angle)
    cr.scale(look.scale * (1 + look.stretch), look.scale / (1 + look.stretch))
    cr.rotate(-look.angle)
    # soft drop shadow: a few concentric arcs, so it shows on light and dark alike
    for i in range(5):
        cr.set_source_rgba(0, 0, 0, 0.25 / 5)
        cr.arc(0, 2.5, r + 1 + i * 2.2, 0, 2 * math.pi)
        cr.fill()
    # glass body
    body = cairo.RadialGradient(-r * .3, -r * .3, r * .1, 0, 0, r)
    body.add_color_stop_rgba(0, 0.95, 0.98, 1, 0.16)
    body.add_color_stop_rgba(1, 0.80, 0.88, 1, 0.34)
    cr.set_source(body)
    cr.arc(0, 0, r, 0, 2 * math.pi)
    cr.fill()
    # water: two sines of different phase and speed, clipped to the drop
    cr.save()
    cr.arc(0, 0, r - 0.5, 0, 2 * math.pi)
    cr.clip()
    top = r - 2 * r * (WATER + 0.12 * look.level)
    a1 = look.amp * (0.8 + 6.0 * look.level)
    a2 = a1 * 0.5
    pts = [(x, top + a1 * math.sin(x * 0.19 + look.phase[0]) + a2 * math.sin(x * 0.33 + look.phase[1]))
           for x in range(int(-r) - 1, int(r) + 2, 2)]
    cr.move_to(pts[0][0], r + 1)
    for x, y in pts:
        cr.line_to(x, y)
    cr.line_to(pts[-1][0], r + 1)
    cr.close_path()
    cr.set_source_rgba(*look.rgb, 0.85 * look.alpha)
    cr.fill()
    cr.move_to(*pts[0])
    for x, y in pts[1:]:
        cr.line_to(x, y)
    cr.set_source_rgba(1, 1, 1, 0.5 * look.alpha)
    cr.set_line_width(1.2)
    cr.stroke()
    if look.speaking > 0.01:            # faint white glow from the rim inwards
        glow = cairo.RadialGradient(0, 0, r * .55, 0, 0, r)
        glow.add_color_stop_rgba(0, 1, 1, 1, 0)
        glow.add_color_stop_rgba(1, 1, 1, 1, 0.30 * look.speaking)
        cr.set_source(glow)
        cr.paint()
    cr.restore()
    # specular highlight, top left
    cr.save()
    cr.translate(-r * .36, -r * .46)
    cr.rotate(-0.6)
    cr.scale(r * .34, r * .17)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.35)
    cr.fill()
    # 1 px light rim
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.set_line_width(1)
    cr.arc(0, 0, r - 0.5, 0, 2 * math.pi)
    cr.stroke()
    cr.pop_group_to_source()
    cr.paint_with_alpha(look.opacity)


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


class Drag:
    """Pointer → margins, no GTK. The travel is always measured from the press:
    pos = pos at press + (pointer - press point), neither ever updated.

    What the pointer coordinates are relative to depends on the compositor.
    ``"click"``: the surface as it was at the press (niri; X11 root coords) —
    travel is ``p - press``. ``"surface"``: the surface as it is now, so a move
    we applied shows up as a jump back — travel is ``p - press + moved``.
    Pass ``mode`` when known (X11); otherwise the first step is DECIDE_AT px
    (both readings agree before anything has moved) and the next event tells
    them apart: it either followed the pointer or stayed near the press."""

    DECIDE_AT = 16   # px of first step: far enough apart to tell the readings

    def __init__(self) -> None:
        self.press: Optional[Tuple[float, float, Tuple[int, int]]] = None
        self.dragging = False
        self.mode: Optional[str] = None

    def start(self, x: float, y: float, pos: Tuple[int, int], mode: Optional[str] = None) -> None:
        self.press, self.dragging, self.mode = (x, y, pos), False, mode
        self._moved = (0.0, 0.0)    # surface displacement applied so far, screen axes

    def motion(self, x: float, y: float, screen: Tuple[int, int]):
        """New (margin-right, margin-top) and the (dx, dy) travelled, or None."""
        if self.press is None:
            return None
        px, py, (mr, mt) = self.press
        mx, my = self._moved
        a = (x - px, y - py)                  # coordinates relative to the press
        b = (a[0] + mx, a[1] + my)            # relative to the surface as moved
        moved = math.hypot(mx, my)
        if self.mode is None:
            if not moved:
                # Nothing moved yet, so both readings agree: wait for a first
                # step big enough to tell them apart on the next event.
                if math.hypot(*a) < self.DECIDE_AT:
                    self.dragging = self.dragging or math.hypot(*a) >= DRAG_THRESHOLD
                    return None
            else:
                # We moved the surface by `moved`. Coordinates tied to the press
                # have followed the pointer (≈ moved along that direction);
                # coordinates tied to the surface have stayed near the press (≈ 0).
                along = (a[0] * mx + a[1] * my) / moved
                self.mode = "click" if along >= moved / 2 else "surface"
        dx, dy = b if self.mode == "surface" else a
        self.dragging = self.dragging or math.hypot(dx, dy) >= DRAG_THRESHOLD
        if not self.dragging:
            return None
        pos = clamp((round(mr - dx), round(mt + dy)), screen)
        self._moved = (mr - pos[0], pos[1] - mt)
        return pos, (dx, dy)


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
        self._pos = load_position()
        self._press = None       # (x, y, pos) while the left button is down
        self._drag = Drag()
        self._hover = False
        self._level = 0.0        # raw level, written from any thread
        self._loud = False
        self._phase = [0.0, 0.0]
        self._last = 0.0         # monotonic µs of the previous tick
        self._vel = (0.0, 0.0)   # pointer velocity while dragging, px/s
        self._last_move = 0.0
        self._timer = self._timer_ms = 0

        r, g, b = STATES["idle"]
        self._sp = dict(
            r=Spring(r), g=Spring(g), b=Spring(b), alpha=Spring(1.0),
            calm=Spring(1.0),                           # 1 = waves, 0 = flat water
            level=Spring(0.0, response=0.12),
            scale=Spring(1.0, response=0.25, damping=0.8),
            bump=Spring(0.0, response=0.25, damping=0.7),   # the wake-word start
            stretch=Spring(0.0, response=0.4),
            glow=Spring(0.0),
            appear=Spring(0.0, response=0.35, damping=0.8),
        )
        self._angle = 0.0

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
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", lambda *_: self._set_hover(True))
        self.connect("leave-notify-event", lambda *_: self._set_hover(False))
        self.connect("map", self._on_map)
        self.connect("unmap", lambda *_: self._stop_timer())
        self.connect("destroy", lambda *_: self._stop_timer())
        self._apply_position()
        self._recolor()
        self.show_all()

    # ----------------------------------------------------------------- state
    @run_on_main_thread
    def set_state(self, state: str) -> None:
        """Thread-safe. Unknown names are ignored."""
        if state in STATES and state != self._state:
            self._state = state
            self._recolor()
            self._kick()

    def set_level(self, x: float) -> None:
        """Thread-safe, cheap: the audio level 0..1 (user's mic, or the companion's voice)."""
        self._level = max(0.0, min(1.0, float(x)))
        loud = self._level > LOUD
        if loud != self._loud:
            self._loud = loud
            GLib.idle_add(self._kick)

    @run_on_main_thread
    def pulse(self) -> None:
        """Wake word heard: a small jolt of scale, 1 → 1.08 → 1."""
        self._sp["bump"].target = 0.08
        self._kick()
        GLib.timeout_add(110, self._unbump)

    def _unbump(self) -> bool:
        self._sp["bump"].target = 0.0
        self._kick()
        return False

    def _recolor(self) -> None:
        sp, st = self._sp, self._state
        sp["r"].target, sp["g"].target, sp["b"].target = STATES[st]
        sp["alpha"].target = SILENT_ALPHA if st == "silent" else 1.0
        sp["calm"].target = 0.0 if st == "silent" else 1.0
        sp["glow"].target = 1.0 if st == "speaking" else 0.0

    def _anim(self) -> bool:
        s = Gtk.Settings.get_default()
        return bool(s.get_property("gtk-enable-animations")) if s else True

    # ----------------------------------------------------------------- timer
    def _moving(self) -> bool:
        return not all(s.rest for s in self._sp.values())

    def _kick(self) -> bool:
        """Re-evaluate the render timer (and paint once: the last frame of a stopped animation)."""
        if not self._anim():
            for s in self._sp.values():
                s.jump()
            self._sp["calm"].value = 0.0
        want = tick_interval(self._state, moving=self._moving(), loud=self._loud,
                             anim=self._anim(), visible=self.get_mapped())
        if want != self._timer_ms:
            self._stop_timer()
            if want:
                self._last = GLib.get_monotonic_time()
                self._timer, self._timer_ms = GLib.timeout_add(want, self._tick), want
        self.area.queue_draw()
        return False

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = self._timer_ms = 0

    def _on_map(self, *_) -> None:
        """The drop falls in: scale 0.6 → 1 and opacity 0 → 1."""
        a = self._sp["appear"]
        a.value, a.velocity, a.target = 0.0, 0.0, 1.0
        if not self._anim():
            a.jump()
        self._kick()

    def _tick(self) -> bool:
        now = GLib.get_monotonic_time()
        dt = min(0.05, (now - self._last) / 1e6)
        self._last = now
        sp = self._sp
        sp["level"].target = self._level
        # the stretch eases back once the pointer stops
        if self._drag.dragging and now / 1e6 - self._last_move > 0.08:
            sp["stretch"].target = 0.0
        for s in sp.values():
            s.step(dt)
        speed = 1 + 2 * sp["level"].value
        d = wave_dir(self._state)
        self._phase[0] -= d * dt * 5.0 * speed
        self._phase[1] -= d * dt * 3.1 * speed
        self._phase = [p % (2 * math.pi) for p in self._phase]
        want = tick_interval(self._state, moving=self._moving(), loud=self._loud,
                             anim=self._anim(), visible=self.get_mapped())
        self.area.queue_draw()
        if want != self._timer_ms:
            self._timer = 0          # returning False below removes this source
            self._kick()
            return False
        return True

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
    def _rest_scale(self) -> None:
        """Press 0.95, hover 1.04, else 1; every change restarts from the shown value."""
        sc = self._sp["scale"]
        sc.target = 0.95 if self._press is not None else 1.04 if self._hover else 1.0
        sc.response, sc.damping = (0.4, 1.0) if self._press is None else (0.25, 0.8)
        self._kick()

    def _set_hover(self, on: bool) -> bool:
        self._hover = on
        self._rest_scale()
        return False

    def _on_press(self, _w, ev) -> bool:
        if ev.button == 1:
            # X11: root coordinates, which do not move with the window.
            x, y = (ev.x, ev.y) if self._wl else (ev.x_root, ev.y_root)
            self._drag.start(x, y, self._pos, None if self._wl else "click")
            self._press = True
            self._rest_scale()
        elif ev.button == 3:
            self._popup_menu(ev)
        return True

    def _on_motion(self, _w, ev) -> bool:
        if self._press is None:
            return False
        x, y = (ev.x, ev.y) if self._wl else (ev.x_root, ev.y_root)
        step = self._drag.motion(x, y, self._screen_size())
        if step is None:
            return True
        self._pos, (dx, dy) = step
        self._apply_position()
        self._stretch_toward(dx, dy)
        return True

    def _stretch_toward(self, dx: float, dy: float) -> None:
        """Lean into the motion: up to 8 % along the direction of travel."""
        now = GLib.get_monotonic_time() / 1e6
        dt = max(now - self._last_move, 1e-3) if self._last_move else 0.016
        self._last_move = now
        speed = math.hypot(dx, dy) / dt
        if speed > 1:
            self._angle = math.atan2(dy, dx)
        sp = self._sp["stretch"]
        sp.response, sp.damping = 0.12, 1.0
        sp.target = min(0.08, speed / 12000)
        self._kick()

    def _on_release(self, _w, ev) -> bool:
        if ev.button != 1 or self._press is None:
            return False
        self._press = None
        sp = self._sp["stretch"]
        sp.target, sp.response, sp.damping = 0.0, 0.4, 1.0
        self._rest_scale()
        if self._drag.dragging:
            save_position(clamp(self._pos, self._screen_size()))
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
    def look(self) -> Look:
        sp, anim = self._sp, self._anim()
        a = sp["appear"].value
        return Look(
            rgb=tuple(max(0.0, min(1.0, sp[k].value)) for k in "rgb"),
            alpha=max(0.0, min(1.0, sp["alpha"].value)),
            level=max(0.0, min(1.0, sp["level"].value)),
            amp=max(0.0, sp["calm"].value) if anim else 0.0,
            phase=tuple(self._phase),
            scale=(0.6 + 0.4 * a) * sp["scale"].value * (1 + sp["bump"].value),
            opacity=max(0.0, min(1.0, a)),
            stretch=sp["stretch"].value, angle=self._angle,
            speaking=max(0.0, min(1.0, sp["glow"].value)), state=self._state)

    def _on_draw(self, _w, cr: cairo.Context) -> bool:
        from loquivox.ui.companion_bubble_styles import draw_current
        draw_current(cr, self.look())    # the shipped drop unless another style is chosen
        return True


if __name__ == "__main__":
    import argparse
    import itertools
    import random
    import sys
    ap = argparse.ArgumentParser(description="Companion bubble demo (the only life it has outside the app)")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--style", help="goutte (default), goutte-plus, reacteur, radar, plasma")
    ap.add_argument("--state", help="pin one state instead of cycling (e.g. idle, to measure CPU)")
    ap.add_argument("--gallery", action="store_true", help="the four styles side by side")
    ap.add_argument("--frames", metavar="DIR", help="render PNG frames of every style and exit")
    args = ap.parse_args()
    # `-m` runs this file as __main__: share it, or the styles would import a second copy.
    sys.modules.setdefault("loquivox.ui.companion_bubble", sys.modules["__main__"])
    from loquivox.ui import companion_bubble_styles as styles
    if args.frames:
        styles.render_frames(args.frames)
        sys.exit()
    if args.gallery:
        styles.gallery()
        sys.exit()
    if args.style:
        styles.set_style(args.style)
    bubble = CompanionBubble(on_click=lambda: print("click"),
                             modes=[(m, lambda m=m: print("mode", m)) for m in ("Veille", "Toujours à l'écoute", "Muet")])
    states = itertools.cycle((args.state,) if args.state else ("idle", "cloud", "listening", "speaking", "silent"))
    clock = [0.0]

    def _next():
        s = next(states)
        print("state:", s)
        bubble.set_state(s)
        bubble.pulse() if s == "cloud" else None
        return True

    def _audio():
        clock[0] += 0.05
        s = bubble._state
        talking = s in ("listening", "speaking")
        bubble.set_level(abs(math.sin(clock[0] * 3)) * random.uniform(0.4, 1.0) if talking else 0.0)
        return True

    GLib.timeout_add(3000, _next)
    GLib.timeout_add(50, _audio)
    bubble.connect("destroy", Gtk.main_quit)
    Gtk.main()
