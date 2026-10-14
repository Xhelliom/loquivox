"""
Mock-ups for the companion's bubble: three sci-fi looks beside the shipped drop.

Same frame as the bubble (``SIZE`` window, ``DROP`` body, the same ``Look`` fed
by the same springs), so a style is a drop-in for ``paint``. The shipped look,
``goutte``, stays the default until the operator picks another one:

    LOQUIVOX_BUBBLE_STYLE=reacteur loquivox
    python -m loquivox.ui.companion_bubble --demo --style radar [--state idle]
    python -m loquivox.ui.companion_bubble --gallery   # the four side by side (goutte included)
    python -m loquivox.ui.companion_bubble --frames DIR  # PNG frames for the GIFs

Every state reads from motion as well as colour: standby turns and breathes
slowly, a cloud session ticks faster, listening turns one way and draws in,
speaking turns the other and pushes out, silent is still (``look.amp`` = 0
freezes every clock here). Spin rates integrate (``Rig``) so a change of state
never makes a ring jump.
"""
from __future__ import annotations

import math
import os
import random
from typing import Callable, Dict, Tuple

import cairo

from loquivox.ui import companion_bubble as cb
from loquivox.ui.companion_bubble import DROP, SIZE, SILENT_ALPHA, STATES, Look, wave_dir

R = DROP / 2
TAU = 2 * math.pi
DEFAULT_STYLE = "goutte"
SPIN = {"idle": 0.25, "cloud": 0.7, "listening": 1.4, "speaking": -1.4, "silent": 0.0}  # rad/s
DEMO_STATES = ("idle", "cloud", "listening", "speaking", "silent")


class Rig:
    """The clocks a style integrates from ``look`` (one per drawing surface)."""

    def __init__(self) -> None:
        self.t = None
        self.spin = self.spin2 = self.breath = self.rise = 0.0
        rnd = random.Random(7)
        self.parts = [(rnd.uniform(-.8, .8), rnd.random(), rnd.uniform(.5, 1.2), rnd.uniform(.7, 1.5))
                      for _ in range(14)]

    def step(self, look: Look, t: float) -> None:
        dt = 0.0 if self.t is None else min(0.1, max(0.0, t - self.t))
        self.t = t
        rate = SPIN[look.state] * (1 + 3 * look.level) * look.amp
        self.spin += rate * dt
        self.spin2 -= 0.6 * rate * dt
        self.breath += dt * (1.9 if look.state == "cloud" else 0.9) * look.amp
        self.rise += dt * (0.12 + 0.8 * look.level) * look.amp

    def breathe(self, look: Look) -> float:
        return 0.5 + 0.5 * math.sin(self.breath) * look.amp


def _dir(look: Look) -> int:
    """+1 pushes out (speaking), -1 draws in (listening), 0 otherwise."""
    return {"speaking": 1, "listening": -1}.get(look.state, 0)


def _light(rgb, w: float = 0.55):
    return tuple(c + (1 - c) * w for c in rgb)


def _begin(cr: cairo.Context, look: Look, clear: bool = True) -> None:
    if clear:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
    cr.push_group()
    cr.translate(SIZE / 2, SIZE / 2)
    cr.rotate(look.angle)
    cr.scale(look.scale * (1 + look.stretch), look.scale / (1 + look.stretch))
    cr.rotate(-look.angle)


def _end(cr: cairo.Context, look: Look) -> None:
    cr.pop_group_to_source()
    cr.paint_with_alpha(look.opacity)


def _disc(cr, radius, alpha=0.55, tint=(0.04, 0.07, 0.12)) -> None:
    """The dark glass behind the instruments: they stay legible on a light desktop."""
    g = cairo.RadialGradient(0, 0, radius * .2, 0, 0, radius)
    g.add_color_stop_rgba(0, *tint, alpha * .6)
    g.add_color_stop_rgba(1, *tint, alpha)
    cr.set_source(g)
    cr.arc(0, 0, radius, 0, TAU)
    cr.fill()


def _glow(cr, rgb, radius, alpha) -> None:
    g = cairo.RadialGradient(0, 0, 0, 0, 0, radius)
    g.add_color_stop_rgba(0, *_light(rgb, .6), alpha)
    g.add_color_stop_rgba(.45, *rgb, alpha * .55)
    g.add_color_stop_rgba(1, *rgb, 0)
    cr.set_source(g)
    cr.arc(0, 0, radius, 0, TAU)
    cr.fill()


# --------------------------------------------------------------- 2. reacteur
def reacteur(cr, look: Look, rig: Rig) -> None:
    """Energy core: a glowing heart in three rings of segments that part when it speaks."""
    _begin(cr, look)
    d, lv, a = _dir(look), look.level, look.alpha
    _disc(cr, R + 1)
    _glow(cr, look.rgb, R * (0.62 + 0.14 * rig.breathe(look) + 0.28 * lv), 0.95 * a)
    cr.set_source_rgba(1, 1, 1, 0.9 * a)
    cr.arc(0, 0, 2.2 + 2.5 * lv + 1.2 * rig.breathe(look), 0, TAU)
    cr.fill()
    col = _light(look.rgb, .5)
    for rad, n, width, k in ((.58, 6, 3.2, 1.0), (.78, 8, 2.4, -1.3), (.97, 12, 1.6, 0.7)):
        seg = TAU / n
        gap = min(seg * .85, seg * .22 + 1.5 * lv)       # the segments part with the voice
        rr = R * rad + d * lv * 3.5 - (1.5 * lv if d < 0 else 0)
        cr.set_line_width(width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*col, 0.92 * a)
        for i in range(n):
            a0 = rig.spin * k + i * seg + gap / 2
            cr.new_sub_path()
            cr.arc(0, 0, rr, a0, a0 + seg - gap)
        cr.stroke()
    cr.set_source_rgba(1, 1, 1, 0.22 * a)               # 1 px rim
    cr.set_line_width(1)
    cr.arc(0, 0, R + .5, 0, TAU)
    cr.stroke()
    _end(cr, look)


# -------------------------------------------------------------------- 3. radar
def radar(cr, look: Look, rig: Rig) -> None:
    """HUD: graduated bezel, a sweep that fades behind it, a circular voice trace round the core."""
    _begin(cr, look)
    d, lv, a = _dir(look), look.level, look.alpha
    col = _light(look.rgb, .45)
    _disc(cr, R + 1, .6)
    cr.set_source_rgba(*col, 0.75 * a)                  # bezel
    cr.set_line_width(1.1)
    cr.arc(0, 0, R - .5, 0, TAU)
    cr.stroke()
    cr.set_line_width(1)
    for i in range(36):                                 # graduations, slowly counter-turning
        ang = i * TAU / 36 + rig.spin2 * .3
        l = 4.5 if i % 9 == 0 else 2.2
        cr.move_to((R - 1) * math.cos(ang), (R - 1) * math.sin(ang))
        cr.line_to((R - 1 - l) * math.cos(ang), (R - 1 - l) * math.sin(ang))
        cr.set_source_rgba(*col, (0.8 if i % 9 == 0 else .4) * a)
        cr.stroke()
    cr.set_source_rgba(*col, 0.28 * a)                  # inner range ring and cross hair
    cr.arc(0, 0, R * .62, 0, TAU)
    cr.stroke()
    for ang in (0, math.pi / 2):
        cr.move_to(-R * .8 * math.cos(ang), -R * .8 * math.sin(ang))
        cr.line_to(R * .8 * math.cos(ang), R * .8 * math.sin(ang))
    cr.stroke()
    sign = -1 if look.state == "speaking" else 1
    head = rig.spin * 2
    for i in range(22):                                 # sweep: 22 wedges of decaying alpha
        a0 = head - sign * i * .075
        cr.move_to(0, 0)
        (cr.arc if sign > 0 else cr.arc_negative)(0, 0, R - 5, a0 - sign * .075, a0)
        cr.close_path()
        cr.set_source_rgba(*look.rgb, 0.4 * (1 - i / 22) ** 1.6 * a)
        cr.fill()
    cr.move_to(0, 0)                                    # the sweep's leading edge
    cr.line_to((R - 5) * math.cos(head), (R - 5) * math.sin(head))
    cr.set_source_rgba(*_light(look.rgb, .7), 0.9 * a)
    cr.set_line_width(1.2)
    cr.stroke()
    base = 11 + 1.5 * rig.breathe(look) + d * lv * 2     # the voice, as a ring that wobbles
    amp = 0.6 + 8 * lv
    ph0, ph1 = look.phase
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for j in range(72):
        ang = j * TAU / 72
        rr = base + amp * look.amp * (.55 * math.sin(5 * ang + ph0) + .35 * math.sin(9 * ang - ph1))
        (cr.move_to if j == 0 else cr.line_to)(rr * math.cos(ang), rr * math.sin(ang))
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 0.9 * a)
    cr.set_line_width(1.5)
    cr.stroke()
    cr.set_source_rgba(*_light(look.rgb, .7), 0.95 * a)
    cr.arc(0, 0, 2.2 + rig.breathe(look), 0, TAU)
    cr.fill()
    _end(cr, look)


# ------------------------------------------------------------------- 4. plasma
_N = 90
_PTS = [(math.cos(i * 2.39996) * math.sqrt(1 - y * y), y, math.sin(i * 2.39996) * math.sqrt(1 - y * y))
        for i in range(_N) for y in [1 - 2 * (i + .5) / _N]]


def plasma(cr, look: Look, rig: Rig) -> None:
    """A sphere of light points that bulges and ripples with the voice."""
    _begin(cr, look)
    d, lv, a = _dir(look), look.level, look.alpha
    _glow(cr, look.rgb, R * (.95 + .1 * rig.breathe(look) + .3 * lv), (0.35 + .25 * lv) * a)
    cs, sn = math.cos(rig.spin), math.sin(rig.spin)
    tc, ts = math.cos(.5), math.sin(.5)
    ph0, ph1 = look.phase
    dots = []
    for x0, y0, z0 in _PTS:
        k = 1 + .05 * rig.breathe(look) + lv * .7 * math.sin(4 * x0 + ph0) * math.cos(3 * y0 - ph1) \
            + d * lv * .12
        x, z = x0 * cs + z0 * sn, -x0 * sn + z0 * cs
        y, z = y0 * tc - z * ts, y0 * ts + z * tc
        dots.append((z, x * k * (R - 3), y * k * (R - 3)))
    col = _light(look.rgb, .45)
    for z, x, y in sorted(dots):                         # far to near
        depth = (z + 1) / 2
        cr.set_source_rgba(*col, (0.18 + 0.82 * depth) * a)
        cr.arc(x, y, 0.8 + 1.2 * depth, 0, TAU)
        cr.fill()
    _end(cr, look)


STYLES: Dict[str, Callable] = {"reacteur": reacteur, "radar": radar, "plasma": plasma}
VARIANTS = tuple(STYLES)
CHOICES = (DEFAULT_STYLE,) + tuple(sorted(STYLES))   # Settings → Companion

_style = None   # forced by set_style (demo); otherwise config, so Apply restyles the live bubble
_rig = Rig()


def current_style() -> str:
    """The demo's choice, the env var, then ``[companion] bubble_style``; unknown is the drop."""
    from loquivox import config
    name = _style or os.environ.get("LOQUIVOX_BUBBLE_STYLE") or config.CFG.COMPANION_BUBBLE_STYLE
    return name if name in STYLES else DEFAULT_STYLE


def set_style(name: str) -> None:
    global _style
    _style = name if name in STYLES or name == DEFAULT_STYLE else None


def draw(name: str, cr, look: Look, rig: Rig, t: float) -> None:
    fn = STYLES.get(name)
    if fn is None:
        return cb.paint(cr, look)
    rig.step(look, t)
    fn(cr, look, rig)


def draw_current(cr, look: Look) -> None:
    """The bubble's single hook: ``_on_draw`` calls this instead of ``paint``."""
    from gi.repository import GLib
    draw(current_style(), cr, look, _rig, GLib.get_monotonic_time() / 1e6)


# ------------------------------------------------- simulation (gallery, frames)
class Sim:
    """What the bubble's springs produce, with plain exponential smoothing."""

    def __init__(self) -> None:
        self.state = "idle"
        self.rgb = list(STATES["idle"])
        self.alpha = self.amp = 1.0
        self.level = self.glow = 0.0
        self.phase = [0.0, 0.0]

    def step(self, dt: float, state: str, raw: float) -> Look:
        self.state = state
        k = 1 - math.exp(-dt / 0.12)
        for i, c in enumerate(STATES[state]):
            self.rgb[i] += (c - self.rgb[i]) * k
        self.alpha += ((SILENT_ALPHA if state == "silent" else 1.0) - self.alpha) * k
        self.amp += ((0.0 if state == "silent" else 1.0) - self.amp) * k
        self.level += (raw - self.level) * (1 - math.exp(-dt / 0.06))
        speed = 1 + 2 * self.level
        d = wave_dir(state)
        self.phase[0] = (self.phase[0] - d * dt * 5.0 * speed) % TAU
        self.phase[1] = (self.phase[1] - d * dt * 3.1 * speed) % TAU
        return Look(rgb=tuple(self.rgb), alpha=self.alpha, level=self.level, amp=self.amp,
                    phase=tuple(self.phase), state=state)


def fake_level(state: str, t: float) -> float:
    """A voice-like envelope, deterministic so the GIFs are reproducible."""
    if state not in ("listening", "speaking"):
        return 0.0
    return min(1.0, abs(math.sin(t * 3)) * (0.45 + 0.55 * abs(math.sin(t * 7.3))))


def state_at(t: float, per_state: float) -> str:
    return DEMO_STATES[int(t / per_state) % len(DEMO_STATES)]


def _cell(cr, name: str, look: Look, rig: Rig, t: float, zoom: int, x: float) -> None:
    """One style on its own surface: a style clears its whole canvas, as the bubble's window needs."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE * zoom, SIZE * zoom)
    c = cairo.Context(surf)
    c.scale(zoom, zoom)
    draw(name, c, look, rig, t)
    cr.set_source_surface(surf, x, 0)
    cr.paint()


def gallery(per_state: float = 3.0, zoom: int = 3) -> None:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    win = Gtk.Window(title="Loquivox — bulle du compagnon, 4 styles")
    box = Gtk.Box(spacing=0)
    win.add(box)
    sim, clock = Sim(), [0.0, GLib.get_monotonic_time()]
    cells = []
    for name in CHOICES:
        area = Gtk.DrawingArea()
        area.set_size_request(SIZE * zoom, SIZE * zoom + 30)
        rig, look = Rig(), [Look(rgb=STATES["idle"])]

        def on_draw(_w, cr, name=name, rig=rig, look=look):
            cr.set_source_rgb(.09, .1, .14)
            cr.paint()
            _cell(cr, name, look[0], rig, clock[0], zoom, 0)
            _label(cr, f"{name} · {look[0].state}", SIZE * zoom / 2, SIZE * zoom + 20)
            return True
        area.connect("draw", on_draw)
        box.pack_start(area, True, True, 0)
        cells.append((area, look))

    def tick():
        now = GLib.get_monotonic_time()
        dt, clock[1] = (now - clock[1]) / 1e6, now
        clock[0] += dt
        st = state_at(clock[0], per_state)
        look = sim.step(min(dt, .05), st, fake_level(st, clock[0]))
        for area, slot in cells:
            slot[0] = look
            area.queue_draw()
        return True
    GLib.timeout_add(33, tick)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


def _label(cr, text: str, x: float, y: float) -> None:
    cr.set_source_rgba(.85, .88, .95, .9)
    cr.select_font_face("monospace")
    cr.set_font_size(13)
    w = cr.text_extents(text).width
    cr.move_to(x - w / 2, y)
    cr.show_text(text)


def render_frames(outdir: str, fps: int = 20, per_state: float = 2.0, zoom: int = 3) -> None:
    """Offscreen PNGs: ``<style>/NNNN.png`` for each style and ``gallery/NNNN.png`` side by side."""
    from pathlib import Path
    out = Path(outdir)
    names = list(CHOICES)
    sets = {n: [n] for n in names}
    sets["gallery"] = names
    cell = SIZE * zoom
    for set_name, members in sets.items():
        (out / set_name).mkdir(parents=True, exist_ok=True)
        sim, rigs = Sim(), [Rig() for _ in members]
        n = int(per_state * fps * len(DEMO_STATES))
        for f in range(n):
            t = f / fps
            st = state_at(t, per_state)
            look = sim.step(1 / fps, st, fake_level(st, t))
            surf = cairo.ImageSurface(cairo.FORMAT_RGB24, cell * len(members), cell + 30)
            cr = cairo.Context(surf)
            cr.set_source_rgb(.09, .1, .14)
            cr.paint()
            for i, name in enumerate(members):
                _cell(cr, name, look, rigs[i], t, zoom, i * cell)
                _label(cr, f"{name} · {st}", i * cell + cell / 2, cell + 20)
            surf.write_to_png(str(out / set_name / f"{f:04d}.png"))
