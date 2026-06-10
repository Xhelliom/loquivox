"""
Floating recording overlay with a smoothed EQ-style waveform.

On Wayland: uses gtk-layer-shell for proper overlay behaviour.
On X11: uses classic GTK window hints (POPUP, keep-above).

Animation notes:
- Fade + slide are done entirely in cairo (every paint is scaled by an opacity
  that eases 0→1 on show and →0 on close, and the content is translated a few
  px), so it looks smooth regardless of whether the compositor honours
  per-window opacity on a layer-shell surface.
- The bars are temporally smoothed: each frame eases toward a target (fast
  attack, slow decay) so the waveform never jumps, and gently "breathes" when
  there's no sound.
"""
from __future__ import annotations

import math
import queue
from typing import List, Tuple

import cairo
import numpy as np

from linuxwhisper.config import CFG
from linuxwhisper.platform import SESSION_TYPE
from linuxwhisper.state import STATE

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

# Optional gtk-layer-shell for Wayland
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False


class GtkOverlay(Gtk.Window):
    """Floating recording overlay with a smoothed EQ-style waveform."""

    NUM_BARS = 28
    MAX_BAR = 16          # px half-height of the tallest bar
    FRAME_MS = 16         # ~60 fps
    SLIDE_PX = 10         # how far the content slides up while fading in

    def __init__(self, mode: str):
        # Layer-shell requires TOPLEVEL; X11 uses POPUP
        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
        else:
            super().__init__(type=Gtk.WindowType.POPUP)

        self.mode = mode
        self.config = CFG.MODES.get(mode, CFG.MODES["dictation"])
        self._setup_window()
        self._setup_ui()
        self.show_all()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_app_paintable(True)
        self.set_decorated(False)

        # Enable transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        w, h = CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT

        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            # --- Wayland: gtk-layer-shell ---
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "linuxwhisper-recording")
            GtkLayerShell.set_exclusive_zone(self, -1)

            # Anchor to bottom center
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 80)

            # No keyboard interaction needed
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.NONE
            )
        else:
            # --- X11: classic approach ---
            self.set_keep_above(True)

            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geometry = monitor.get_geometry()
            x = (geometry.width - w) // 2
            y = geometry.height - h - 80
            self.move(x, y)

        self.set_default_size(w, h)

    def _setup_ui(self) -> None:
        """Setup drawing area and animation state."""
        self.transcribing = False
        self.live_text = ""
        self._tick = 0
        self._last_audio_tick = 0
        self._opacity = 0.0           # eases 0→1 on show
        self._closing = False
        self._bars: List[float] = [0.0] * self.NUM_BARS
        self._targets: List[float] = [0.0] * self.NUM_BARS

        # Use the desktop's UI font (portable, no font dependency to install).
        settings = Gtk.Settings.get_default()
        fontname = (settings.get_property("gtk-font-name") if settings else None) or "Sans 10"
        self._font_family = Pango.FontDescription(fontname).get_family() or "Sans"

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT)
        self.drawing_area.connect("draw", self._on_draw)
        self.add(self.drawing_area)
        self.timeout_id = GLib.timeout_add(self.FRAME_MS, self._animate)

    def set_transcribing(self) -> None:
        """Switch the overlay to the post-recording 'transcribing' state."""
        self.transcribing = True
        self.drawing_area.queue_draw()

    def set_live_text(self, text: str) -> None:
        """Update the live partial-transcript text shown while streaming."""
        self.live_text = text or ""
        self.drawing_area.queue_draw()

    # ---------------------------------------------------------------- anim
    def _animate(self) -> bool:
        """Per-frame tick (~60 fps): ease opacity + bars, then repaint."""
        self._tick += 1

        # Opacity easing (fade in on show, fade out on close) — gentle.
        target = 0.0 if self._closing else 1.0
        self._opacity += (target - self._opacity) * 0.14
        if self._closing and self._opacity < 0.03:
            self.timeout_id = None
            self.destroy()
            return False  # removes this timeout source

        if not self.transcribing:
            self._update_bars()
        self.drawing_area.queue_draw()
        return True

    def _update_bars(self) -> None:
        """Compute new bar targets from the latest audio, then ease toward them."""
        data = None
        while not STATE.viz_queue.empty():
            try:
                data = STATE.viz_queue.get_nowait()
            except queue.Empty:
                break

        n = self.NUM_BARS
        if data is not None and len(data) > 0:
            self._last_audio_tick = self._tick
            step = max(1, len(data) // n)
            for i in range(n):
                seg = data[i * step:(i + 1) * step]
                amp = float(np.max(np.abs(seg))) if len(seg) else 0.0
                # Perceptual shaping: sqrt-ish so quiet speech is visible and
                # loud peaks saturate gracefully instead of clipping hard.
                self._targets[i] = min(1.0, (amp * 6.0) ** 0.6)
        elif self._tick - self._last_audio_tick > 14:
            # Idle → slow, gentle breathing wave across the bars.
            for i in range(n):
                self._targets[i] = 0.05 + 0.04 * (0.5 + 0.5 * math.sin(self._tick * 0.045 + i * 0.45))
        else:
            # Between audio frames: let targets drift down softly.
            for i in range(n):
                self._targets[i] *= 0.93

        # Ease each bar toward its target. Low coefficients = calm, unhurried
        # motion (gentle rise, slow graceful fall).
        for i in range(n):
            t = self._targets[i]
            coef = 0.28 if t > self._bars[i] else 0.08
            self._bars[i] += (t - self._bars[i]) * coef

    @staticmethod
    def _smoothstep(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return x * x * (3 - 2 * x)

    # ---------------------------------------------------------------- draw
    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> None:
        """Draw overlay content (everything scaled by the fade opacity)."""
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        a = self._smoothstep(self._opacity)

        # Clear to fully transparent first (we own the surface).
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Slide the content up as it fades in.
        cr.translate(0, (1.0 - a) * self.SLIDE_PX)

        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])
        bg_rgb = self._hex_to_rgb(scheme.get(self.config["bg"], scheme["bg"]))
        fg_rgb = self._hex_to_rgb(scheme.get(self.config["fg"], scheme["accent"]))

        # Background rounded rect + subtle accent border.
        self._draw_rounded_rect(cr, w, h, 16)
        cr.set_source_rgba(*bg_rgb, 0.92 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg_rgb, 0.18 * a)
        cr.set_line_width(1)
        cr.stroke()

        if self.transcribing:
            text = "Transcription…"
        elif self.live_text:
            text = self.live_text[-32:]
            if len(self.live_text) > 32:
                text = "…" + text
        else:
            text = self.config["text"]

        # Icon (vector, drawn in cairo — no font dependency)
        if self.transcribing:
            self._icon_spinner(cr, 30, h / 2, fg_rgb, a)
        else:
            self._draw_icon(cr, self.mode, 30, h / 2, fg_rgb, a)

        # Text (Pango → crisp, uses the desktop font)
        self._draw_text(cr, text, 112, 19, 9.5, fg_rgb, a)

        # Activity area
        if self.transcribing:
            self._draw_pulse(cr, 58, 212, 42, fg_rgb, a)
        else:
            self._draw_bars(cr, 58, 212, 42, fg_rgb, a)

    # ---------------------------------------------------------------- text
    def _draw_text(self, cr: cairo.Context, text: str, cx: float, cy: float,
                   size: float, color: Tuple[float, ...], a: float) -> None:
        """Center text horizontally at cx, vertically at cy, via Pango."""
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(self._font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgba(*color, a)
        cr.move_to(cx - tw / 2, cy - th / 2)
        PangoCairo.show_layout(cr, layout)

    # --------------------------------------------------------------- icons
    def _draw_icon(self, cr: cairo.Context, mode: str, cx: float, cy: float,
                   color: Tuple[float, ...], a: float) -> None:
        """Dispatch to a vector glyph for the recording mode."""
        cr.set_source_rgba(*color, a)
        cr.set_line_width(1.8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        drawer = {
            "dictation": self._icon_mic,
            "ai": self._icon_sparkle,
            "ai_rewrite": self._icon_pencil,
            "vision": self._icon_camera,
        }.get(mode, self._icon_mic)
        drawer(cr, cx, cy)

    def _icon_mic(self, cr: cairo.Context, cx: float, cy: float) -> None:
        # capsule body
        r = 3.6
        top, bot = cy - 9, cy - 1
        cr.new_sub_path()
        cr.arc(cx, top + r, r, math.pi, 2 * math.pi)
        cr.arc(cx, bot - r, r, 0, math.pi)
        cr.close_path()
        cr.stroke()
        # holder arc
        cr.arc(cx, cy - 3, 6.5, math.radians(20), math.radians(160))
        cr.stroke()
        # stem + base
        cr.move_to(cx, cy + 3.5)
        cr.line_to(cx, cy + 7)
        cr.stroke()
        cr.move_to(cx - 4, cy + 7)
        cr.line_to(cx + 4, cy + 7)
        cr.stroke()

    def _icon_pencil(self, cr: cairo.Context, cx: float, cy: float) -> None:
        # shaft
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx + 4, cy - 4)
        cr.stroke()
        # tip
        cr.move_to(cx + 4, cy - 4)
        cr.line_to(cx + 7, cy - 7)
        cr.stroke()
        # nib mark
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx - 3, cy + 6.5)
        cr.stroke()

    def _icon_camera(self, cr: cairo.Context, cx: float, cy: float) -> None:
        # viewfinder bump
        cr.move_to(cx - 3, cy - 6)
        cr.line_to(cx + 1, cy - 6)
        cr.stroke()
        # body
        self._rounded_rect_path(cr, cx - 9, cy - 5, 18, 12, 2.5)
        cr.stroke()
        # lens
        cr.arc(cx, cy + 1, 3.4, 0, 2 * math.pi)
        cr.stroke()

    def _icon_sparkle(self, cr: cairo.Context, cx: float, cy: float) -> None:
        # four-point star (AI)
        s, w = 9.0, 2.6
        cr.move_to(cx, cy - s)
        cr.curve_to(cx + w, cy - w, cx + w, cy - w, cx + s, cy)
        cr.curve_to(cx + w, cy + w, cx + w, cy + w, cx, cy + s)
        cr.curve_to(cx - w, cy + w, cx - w, cy + w, cx - s, cy)
        cr.curve_to(cx - w, cy - w, cx - w, cy - w, cx, cy - s)
        cr.close_path()
        cr.fill()

    def _icon_spinner(self, cr: cairo.Context, cx: float, cy: float,
                      color: Tuple[float, ...], a: float) -> None:
        """Rotating arc spinner for the transcribing state."""
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        start = (self._tick * 0.12) % (2 * math.pi)
        cr.set_source_rgba(*color, a)
        cr.arc(cx, cy, 7, start, start + math.radians(270))
        cr.stroke()

    def _rounded_rect_path(self, cr: cairo.Context, x: float, y: float,
                           w: float, h: float, r: float) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw_rounded_rect(self, cr: cairo.Context, w: int, h: int, r: int) -> None:
        """Draw rounded rectangle path."""
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw_bars(self, cr: cairo.Context, x1: int, x2: int, cy: int,
                   color: Tuple[float, ...], a: float) -> None:
        """Draw the smoothed, mirrored EQ bars."""
        n = self.NUM_BARS
        slot = (x2 - x1) / n
        cr.set_line_width(max(2.0, slot * 0.5))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for i in range(n):
            level = self._bars[i]
            hh = max(0.6, level * self.MAX_BAR)
            x = x1 + slot * (i + 0.5)
            # Taller bars are brighter for a bit of depth.
            cr.set_source_rgba(*color, (0.4 + 0.6 * level) * a)
            cr.move_to(x, cy - hh)
            cr.line_to(x, cy + hh)
            cr.stroke()

    def _draw_pulse(self, cr: cairo.Context, x1: int, x2: int, cy: int,
                    color: Tuple[float, ...], a: float) -> None:
        """Three pulsing dots to signal transcription in progress."""
        num_dots = 3
        spacing = (x2 - x1) / (num_dots + 1)
        for i in range(num_dots):
            phase = self._tick / 14.0 - i * 0.7
            alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase))
            cr.set_source_rgba(*color, alpha * a)
            cr.arc(x1 + spacing * (i + 1), cy, 4, 0, 2 * math.pi)
            cr.fill()

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> Tuple[float, float, float]:
        """Convert hex color to RGB tuple (0-1 range)."""
        h = hex_str.lstrip('#')
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def close(self) -> None:
        """Begin the fade-out; the animation tick destroys the window at the end."""
        if self._closing:
            return
        self._closing = True  # _animate fades opacity to 0, then destroys
