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
from typing import List, Optional, Tuple

import cairo
import numpy as np

from loquivox.config import CFG
from loquivox.platform import SESSION_TYPE
from loquivox.state import STATE

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
    MAX_BAR = 10          # px half-height of the tallest bar (kept short so the
                          # text band above can grow to two lines within OVERLAY_HEIGHT)
    FRAME_MS = 16         # ~60 fps
    SLIDE_PX = 10         # how far the content slides up while fading in
    # Waveform sensitivity — the calibration knob, since no two mics read alike.
    # Applied to the RMS of each frame's audio, NOT its peak: a real mic idles
    # around rms 0.02 yet peaks past 0.5 on room noise alone, so peak-driven
    # bars sit pegged at full height and never move. Raise if your waveform
    # stays flat, lower if it saturates while you talk normally.
    VIZ_GAIN = 6.0
    # Hotkey-hints strip (STATE.show_hints): keycap pills laid out in one row.
    HINT_KEY_H = 15       # keycap height
    HINT_PAD_X = 12       # margin on each side of the strip
    HINT_GAP = 13         # space between two hotkeys

    @staticmethod
    def _ui_font_family() -> str:
        """The desktop's UI font family (portable, nothing to install)."""
        settings = Gtk.Settings.get_default()
        fontname = (settings.get_property("gtk-font-name") if settings else None) or "Sans 10"
        return Pango.FontDescription(fontname).get_family() or "Sans"

    @classmethod
    def width(cls, badge: Optional[str] = None) -> int:
        """
        Overlay width: the configured one, plus the strips carved off its right
        — the refinement badge, then the hotkey hints — when they're on.

        Hints are measured on the WIDEST state (recording — every hotkey is
        listed), so the window is sized once and never resizes as they come and
        go mid-session.
        """
        items = cls.hint_items(transcribing=False, paused=False) if STATE.show_hints else ()
        if not items and not badge:
            return CFG.OVERLAY_WIDTH
        # Measure by laying the strips out on a throwaway 1x1 surface — same code
        # path as the real paint, so the window can never be a few px too small.
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        scheme = CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME]
        font = cls._ui_font_family()
        strips = cls._render_badge(cr, 0, 0, badge, font, scheme, 1.0) if badge else 0.0
        if items:
            strips += cls._render_hints(cr, 0, 0, items, font, scheme, 1.0)
        return CFG.OVERLAY_WIDTH + int(math.ceil(strips))

    @staticmethod
    def refine_badge_for(mode: str) -> Optional[str]:
        """
        The refinement label to show for ``mode`` ("Medium", "→ EN", …), or None
        when off, opted out, or the mode isn't post-processed (dictation only).
        """
        if mode != "dictation" or not STATE.show_refine_badge:
            return None
        from loquivox.services.postprocess import PostProcessor
        return PostProcessor.current_label()

    def __init__(self, mode: str):
        # Layer-shell requires TOPLEVEL; X11 uses POPUP
        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
        else:
            super().__init__(type=Gtk.WindowType.POPUP)

        self.mode = mode
        self.config = CFG.MODES.get(mode, CFG.MODES["dictation"])
        # What post-processing this dictation will get ("Medium", "→ EN", …).
        # Read once, when the recording starts — that's what will apply — and
        # before _setup_window, which sizes the window around it.
        self.refine_badge = self.refine_badge_for(mode)
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

        w, h = self.width(self.refine_badge), CFG.OVERLAY_HEIGHT

        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            # --- Wayland: gtk-layer-shell ---
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "loquivox-recording")
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
        self.paused = False
        self.live_text = ""
        # Refinement chooser state (grows the overlay while picking a level).
        from loquivox.config import POSTPROCESS_LEVELS
        self.choosing = False
        self.choose_level = 0
        self._base_w, self._base_h = self.width(self.refine_badge), CFG.OVERLAY_HEIGHT
        self._choose_w = max(CFG.OVERLAY_WIDTH, 280)
        self._choose_h = 40 + len(POSTPROCESS_LEVELS) * 22 + 26  # title + rows + hint
        # AI action panel (rewrite/vision): enlarged, two phases.
        self.ai_panel = False
        self.ai_phase = "thinking"      # "thinking" | "review"
        self.ai_instruction = ""
        self.ai_result = ""
        self._panel_w = max(CFG.OVERLAY_WIDTH, 440)
        self._panel_h = 210
        self._tick = 0
        self._last_audio_tick = 0
        self._opacity = 0.0           # eases 0→1 on show
        self._closing = False
        self._bars: List[float] = [0.0] * self.NUM_BARS
        self._level = 0.0             # newest eased level, shifted in each frame

        self._font_family = self._ui_font_family()

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(self._base_w, self._base_h)
        self.drawing_area.connect("draw", self._on_draw)
        self.add(self.drawing_area)
        self.timeout_id = GLib.timeout_add(self.FRAME_MS, self._animate)

    def set_transcribing(self) -> None:
        """Switch the overlay to the post-recording 'transcribing' state."""
        if self.choosing:  # leaving the chooser → shrink back to normal size
            from loquivox.config import POSTPROCESS_LEVELS
            self.choosing = False
            # The on-the-fly chooser overrides the configured level for this
            # dictation, so the badge must follow what was actually picked —
            # and the window must be re-measured around the new label.
            if STATE.show_refine_badge:
                self.refine_badge = dict(POSTPROCESS_LEVELS).get(self.choose_level) \
                    if self.choose_level else None
                self._base_w = self.width(self.refine_badge)
            self._resize(self._base_w, self._base_h)
        self.transcribing = True
        self.drawing_area.queue_draw()

    def set_choosing(self, level: int) -> None:
        """Enter the refinement chooser: grow taller and show the levels."""
        self.choosing = True
        self.transcribing = False
        self.choose_level = int(level)
        self._resize(self._choose_w, self._choose_h)
        self.drawing_area.queue_draw()

    def set_ai_panel(self, phase: str, instruction: str, result: str = "") -> None:
        """
        Enter/refresh the AI action panel (rewrite/vision).

        ``phase`` is "thinking" (spinner + instruction, while the model runs) or
        "review" (shows the result, awaiting Enter/Esc/R/V). Grows the overlay to
        the panel size and takes over the surface, like the refinement chooser.
        """
        self.ai_panel = True
        self.ai_phase = phase
        self.ai_instruction = instruction or ""
        self.ai_result = result or ""
        self.transcribing = False
        self.choosing = False
        self._resize(self._panel_w, self._panel_h)
        self.drawing_area.queue_draw()

    def _resize(self, w: int, h: int) -> None:
        """Resize the overlay window + drawing area, keeping it bottom-centered."""
        self.drawing_area.set_size_request(w, h)
        self.resize(w, h)
        self.queue_resize()
        if not (HAS_LAYER_SHELL and SESSION_TYPE == "wayland"):
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            g = monitor.get_geometry()
            self.move((g.width - w) // 2, g.height - h - 80)

    def set_live_text(self, text: str) -> None:
        """Update the live partial-transcript text shown while streaming."""
        self.live_text = text or ""
        self.drawing_area.queue_draw()

    def set_paused(self, paused: bool) -> None:
        """Toggle the 'paused' indicator (bars freeze, text shows Paused)."""
        self.paused = paused
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

        # Freeze the waveform while transcribing (pulse instead) or paused.
        if not self.transcribing and not self.paused:
            self._update_bars()
        self.drawing_area.queue_draw()
        return True

    def _update_bars(self) -> None:
        """
        Push one level per FRAME onto the bar history, so the waveform scrolls
        right→left with the newest sound at the right edge.

        Deliberately NOT "one audio block spread across the bars": PortAudio
        hands us blocks of wildly varying size (4–139 frames at 16 kHz), far
        fewer than NUM_BARS, which left most bars pinned at zero — only the
        left of the waveform ever moved. Peak is taken across every block
        drained this frame, so no captured audio is skipped.
        """
        chunks = []
        while not STATE.viz_queue.empty():
            try:
                data = STATE.viz_queue.get_nowait()
            except queue.Empty:
                break
            if len(data):
                chunks.append(data)

        if chunks:
            self._last_audio_tick = self._tick
            block = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
            rms = float(np.sqrt(np.mean(np.square(block))))
            # Perceptual shaping: sqrt-ish so quiet speech is visible and loud
            # passages saturate gracefully instead of clipping hard.
            target = min(1.0, (rms * self.VIZ_GAIN) ** 0.6)
        elif self._tick - self._last_audio_tick > 14:
            # Idle → slow, gentle breathing wave.
            target = 0.05 + 0.04 * (0.5 + 0.5 * math.sin(self._tick * 0.045))
        else:
            target = self._level * 0.93  # between blocks: drift down softly

        # Ease the incoming level (fast attack, slow decay) so the scroll stays
        # calm, then shift it in at the right edge.
        coef = 0.28 if target > self._level else 0.08
        self._level += (target - self._level) * coef
        self._bars.pop(0)
        self._bars.append(self._level)

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

        # AI action panel (rewrite/vision) takes over the (enlarged) overlay.
        if self.ai_panel:
            self._draw_ai_panel(cr, w, h, scheme, a)
            return

        # Refinement chooser takes over the (enlarged) overlay while picking.
        if self.choosing:
            self._draw_chooser(cr, w, h, scheme, a)
            return

        # Live transcripts grow from the right, so we keep the full text and let
        # the renderer ellipsize the START — the latest words stay visible and
        # never spill onto the mic icon.
        live = False
        if self.transcribing:
            text = "Transcription…"
        elif self.paused:
            text = "Paused ⏸"
        elif self.live_text:
            text = self.live_text
            live = True
        else:
            text = self.config["text"]

        # Shared renderer → the settings preview looks identical to the real bubble.
        self.render_content(
            cr, w, h, scheme=scheme, mode=self.mode, text=text,
            bars=self._bars, tick=self._tick, font_family=self._font_family,
            transcribing=self.transcribing, a=a, ellipsize_start=live,
            hints=self.hint_items(self.transcribing, self.paused) if STATE.show_hints else (),
            badge=self.refine_badge,
        )

    @staticmethod
    def primary_keys(action: str) -> Optional[List[str]]:
        """
        The keycap labels of an action's primary binding — ``["Ctrl", "Space"]``
        — read from the live HOTKEY_DEFS (first spec wins), or None if unbound.
        """
        specs = CFG.HOTKEY_DEFS.get(action, ("", []))[1]
        if not specs:
            return None
        return [part.title() for part in specs[0].split("+") if part]

    @classmethod
    def hint_items(cls, transcribing: bool, paused: bool) -> List[Tuple[List[str], str]]:
        """
        The hotkeys that actually do something right now, as
        ``([key, …], action)`` — e.g. ``(["Ctrl", "Space"], "refine")``.

        Keys come from the live HOTKEY_DEFS (first spec = primary), so a rebound
        — or unbound, e.g. 'refine' by default — action stays truthful. Once
        transcribing, only cancel is left: pause/refine act on capture, which is
        already over.
        """
        keys = cls.primary_keys
        items: List[Tuple[List[str], str]] = []
        if not transcribing:
            k = keys("pause")
            if k:
                items.append((k, "resume" if paused else "pause"))
            k = keys("refine")
            if k:
                items.append((k, "refine"))
        k = keys("cancel")
        if k:
            items.append((k, "cancel"))
        return items

    @classmethod
    def _render_hints(cls, cr, x, cy, items, font_family, scheme, a) -> float:
        """
        Lay the hotkey strip out left→right at (x, cy) — keycap pills then the
        action they trigger — and return the width it takes.

        Draws and measures in one pass (``width()`` calls it on a throwaway
        surface), so the reserved strip always matches what gets painted.
        """
        base = cls._hex_to_rgb(scheme.get("surface", scheme["bg"]))
        txt = cls._hex_to_rgb(scheme["text"])
        cx = x + cls.HINT_PAD_X
        for i, (keys, action) in enumerate(items):
            if i:
                cx += cls.HINT_GAP
            for j, key in enumerate(keys):
                if j:  # chord: a dim "+" between the caps
                    cx += 2 + cls._draw_text_at(cr, font_family, "+", cx + 2, cy,
                                                6.5, txt, 0.45 * a) + 4
                cx += cls._draw_keycap(cr, cx, cy, key, base, txt, a)
            cx += 5 + cls._draw_text_at(cr, font_family, action, cx + 5, cy,
                                        7.5, txt, 0.8 * a)
        return cx - x + cls.HINT_PAD_X

    @classmethod
    def _render_badge(cls, cr, x, cy, badge, font_family, scheme, a) -> float:
        """
        Refinement chip ("✨ Medium +fmt") in its own strip at (x, cy), and the
        width it takes.

        It gets a strip of its own rather than sharing the state label: the
        label is taken over by the live transcript and by "Transcription…", and
        the refinement is exactly what one wants to see at those moments.
        Draws and measures in one pass, like ``_render_hints``.
        """
        accent = cls._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt = cls._hex_to_rgb(scheme["text"])
        pad = 9
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(7.5 * Pango.SCALE))
        fd.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(fd)
        layout.set_text(f"✨ {badge}", -1)
        tw, th = layout.get_pixel_size()

        chip_x, chip_w, chip_h = x + cls.HINT_PAD_X, tw + 2 * pad, 20
        cls._rounded_rect_path(cr, chip_x, cy - chip_h / 2, chip_w, chip_h, chip_h / 2)
        cr.set_source_rgba(*accent, 0.16 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*accent, 0.35 * a)
        cr.set_line_width(1)
        cr.stroke()

        cr.set_source_rgba(*txt, 0.92 * a)
        cr.move_to(chip_x + pad, cy - th / 2)
        PangoCairo.show_layout(cr, layout)
        return chip_w + 2 * cls.HINT_PAD_X

    @classmethod
    def _draw_keycap(cls, cr, x, cy, label, base, text_rgb, a) -> float:
        """
        One physical-looking key pill: a shadow lip peeking below a gradient
        face (cairo has no box-shadow), monospace label. Returns its width.
        """
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family("Monospace")
        fd.set_size(int(6.5 * Pango.SCALE))
        fd.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(fd)
        layout.set_text(label, -1)
        tw, th = layout.get_pixel_size()
        w, h = tw + 12, cls.HINT_KEY_H
        top = cy - h / 2

        cls._rounded_rect_path(cr, x, top + 2, w, h, 4)   # lip → the 3D feel
        cr.set_source_rgba(*cls._shade(base, 0.45), 0.9 * a)
        cr.fill()

        grad = cairo.LinearGradient(0, top, 0, top + h)   # lit from above
        grad.add_color_stop_rgba(0, *cls._shade(base, 1.7), a)
        grad.add_color_stop_rgba(1, *cls._shade(base, 1.05), a)
        cls._rounded_rect_path(cr, x, top, w, h, 4)
        cr.set_source(grad)
        cr.fill()

        cr.set_source_rgba(*text_rgb, 0.95 * a)
        cr.move_to(x + 6, cy - th / 2)
        PangoCairo.show_layout(cr, layout)
        return w

    @staticmethod
    def _shade(rgb, factor: float):
        """Lighten (factor > 1) or darken (< 1) an RGB triple, clamped to 0..1."""
        return tuple(max(0.0, min(1.0, c * factor)) for c in rgb)

    @staticmethod
    def _draw_separator(cr, x, cy, color, a) -> None:
        """Hairline splitting the overlay content from the hints strip."""
        cr.set_source_rgba(*color, 0.20 * a)
        cr.set_line_width(1)
        cr.move_to(x, cy - 13)
        cr.line_to(x, cy + 13)
        cr.stroke()

    @classmethod
    def render_content(cls, cr, w, h, *, scheme, mode, text, bars, tick,
                       font_family, transcribing=False, a=1.0,
                       ellipsize_start=False, style=None, hints=(), badge=None):
        """
        Paint the overlay bubble at (0, 0, w, h). Pure of widget state so the
        settings dialog can render an identical preview by passing its own
        scheme / looping bars.

        ``style`` picks the look ("pill" or "classic"); when None it follows the
        user's current STATE.overlay_style, so the live overlay and the settings
        preview stay in sync. ``badge`` (refinement level) and ``hints`` (see
        ``hint_items``) each claim a strip on the right — ``w`` is expected to
        already include them (see ``width()``).
        """
        if (style or STATE.overlay_style) == "pill":
            cls._render_pill(cr, w, h, scheme=scheme, mode=mode, text=text,
                             bars=bars, tick=tick, font_family=font_family,
                             transcribing=transcribing, a=a,
                             ellipsize_start=ellipsize_start, hints=hints,
                             badge=badge)
            return

        config = CFG.MODES.get(mode, CFG.MODES["dictation"])
        bg_rgb = cls._hex_to_rgb(scheme.get(config["bg"], scheme["bg"]))
        fg_rgb = cls._hex_to_rgb(scheme.get(config["fg"], scheme["accent"]))

        # Background rounded rect + subtle accent border.
        cls._rounded_rect_path(cr, 0, 0, w, h, 16)
        cr.set_source_rgba(*bg_rgb, 0.92 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg_rgb, 0.18 * a)
        cr.set_line_width(1)
        cr.stroke()

        # Icon (left), text (centered in the area right of the icon, top),
        # activity (bars / pulse, below). Boxing the text right of the icon
        # keeps long live transcripts from spilling over the glyph.
        if transcribing:
            cls._icon_spinner(cr, 30, h / 2, fg_rgb, a, tick)
        else:
            cls._draw_icon(cr, mode, 30, h / 2, fg_rgb, a)
        # The badge + hints strips claim the extra width; content lays out to
        # their left, inside the configured overlay width.
        if badge or hints:
            x = w = CFG.OVERLAY_WIDTH
            if badge:
                cls._draw_separator(cr, x, h / 2, fg_rgb, a)
                x += cls._render_badge(cr, x, h / 2, badge, font_family, scheme, a)
            if hints:
                cls._draw_separator(cr, x, h / 2, fg_rgb, a)
                cls._render_hints(cr, x, h / 2, hints, font_family, scheme, a)
        text_left, text_right = 46, w - 8
        text_w = max(40, text_right - text_left)
        # cy is the *center* of the text band; a one-line label sits centered,
        # a wrapped two-line live transcript fills the band symmetrically. Text
        # band and waveform are balanced so the content is vertically centered
        # (no top-glued text / empty middle).
        cls._draw_text(cr, font_family, text, text_left + text_w / 2, 18, 8.5,
                       fg_rgb, a, max_width=text_w, ellipsize_start=ellipsize_start)
        if transcribing:
            cls._draw_pulse(cr, 58, w - 8, 42, fg_rgb, a, tick)
        else:
            cls._draw_bars(cr, 58, w - 8, 42, fg_rgb, a, bars)

    # ---------------------------------------------------------------- pill
    @classmethod
    def _render_pill(cls, cr, w, h, *, scheme, mode, text, bars, tick,
                     font_family, transcribing, a, ellipsize_start, hints=(),
                     badge=None):
        """
        Capsule look (mirrors the landing-page mock-up): a pulsing red dot, a
        horizontal waveform, and the state label — laid out left→right inside a
        fully-rounded pill with a soft accent glow. Theme-aware via ``scheme``.

        With ``badge`` / ``hints``, the capsule keeps the full width but its
        dot/waveform/label layout is confined to the configured OVERLAY_WIDTH,
        leaving the surplus on the right to those strips.
        """
        bg_rgb = cls._hex_to_rgb(scheme["bg"])
        accent = cls._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt_rgb = cls._hex_to_rgb(scheme.get("text", scheme["accent"]))

        # Inset the capsule so the glow has room to bleed toward the edges.
        pad = 7
        px, py, pw, ph = pad, pad, w - 2 * pad, h - 2 * pad
        r = ph / 2  # fully rounded → capsule

        # Soft glow: a few translucent, expanding strokes fake a box-shadow
        # (cairo has no blur). Accent-tinted, like the mock-up's coloured halo.
        for grow, alpha in ((7, 0.05), (4.5, 0.07), (2.5, 0.10)):
            cls._rounded_rect_path(cr, px - grow, py - grow,
                                   pw + 2 * grow, ph + 2 * grow, r + grow)
            cr.set_source_rgba(*accent, alpha * a)
            cr.set_line_width(grow)
            cr.stroke()

        # Capsule body + thin accent rim.
        cls._rounded_rect_path(cr, px, py, pw, ph, r)
        cr.set_source_rgba(*bg_rgb, 0.92 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*accent, 0.35 * a)
        cr.set_line_width(1.2)
        cr.stroke()

        cy = py + ph / 2
        dot_x = px + 18
        # Width left for the dot/waveform/label once the right-hand strips are
        # carved off — so the waveform keeps its size instead of stretching.
        cw = pw - (w - CFG.OVERLAY_WIDTH) if (hints or badge) else pw
        x = px + cw
        if badge:
            cls._draw_separator(cr, x, cy, accent, a)
            x += cls._render_badge(cr, x, cy, badge, font_family, scheme, a)
        if hints:
            cls._draw_separator(cr, x, cy, accent, a)
            cls._render_hints(cr, x, cy, hints, font_family, scheme, a)

        # Left indicator: rotating spinner while transcribing, else a pulsing
        # red record dot (the universal "recording" cue from the mock-up).
        if transcribing:
            cls._icon_spinner(cr, dot_x, cy, accent, a, tick)
        else:
            pulse = 0.5 + 0.5 * math.sin(tick * 0.12)
            cr.set_source_rgba(0.937, 0.267, 0.267, (0.55 + 0.45 * pulse) * a)  # #ef4444
            cr.arc(dot_x, cy, 5, 0, 2 * math.pi)
            cr.fill()

        # Layout is [dot] [waveform] [label]. The waveform gets a FIXED region so
        # a growing live transcript never shrinks it — the label lives in the
        # remaining right-hand strip and ellipsizes there instead of eating into
        # the bars.
        bars_x1 = dot_x + 12
        bars_x2 = px + cw * 0.52          # fixed right edge of the waveform
        region_right = px + cw - 16

        # Right: the state label, ellipsized to fit its strip.
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(8.5 * Pango.SCALE))
        fd.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        lw, lh = layout.get_pixel_size()
        max_label = max(24, region_right - (bars_x2 + 12))
        if lw > max_label:
            layout.set_width(int(max_label * Pango.SCALE))
            layout.set_ellipsize(
                Pango.EllipsizeMode.START if ellipsize_start else Pango.EllipsizeMode.END
            )
            lw, lh = layout.get_pixel_size()
        label_x = region_right - lw       # right-aligned within its strip

        # Waveform in its fixed region (pulse dots while transcribing).
        if bars_x2 - bars_x1 > 14:
            if transcribing:
                cls._draw_pulse(cr, bars_x1, bars_x2, cy, accent, a, tick)
            else:
                cls._draw_bars(cr, bars_x1, bars_x2, cy, accent, a, bars)

        cr.set_source_rgba(*txt_rgb, 0.9 * a)
        cr.move_to(label_x, cy - lh / 2)
        PangoCairo.show_layout(cr, layout)

    # ---------------------------------------------------------------- text
    @staticmethod
    def _draw_text(cr, font_family, text, cx, cy, size, color, a,
                   max_width=None, ellipsize_start=False):
        """
        Center text horizontally at cx, vertically at cy, via Pango.

        When ``max_width`` is given the text is constrained to that width and
        wrapped over up to two lines. For ``ellipsize_start`` (a growing live
        transcript) we keep the most recent two lines — a leading ``…`` marks
        text that scrolled off the top — and left-align them so the words flow
        like a teleprompter. Short labels stay centered on a single line.
        """
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        cr.set_source_rgba(*color, a)
        if max_width is not None:
            layout.set_width(int(max_width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            if ellipsize_start:
                # Live transcript: wrap the full text, then keep only the window
                # starting at the second-to-last line so the latest words stay
                # visible. Done by hand (rather than Pango's multi-line START
                # ellipsize, which drops the marker on an interior line) so the
                # "…" sits at the very start and the flow reads top→bottom.
                n = layout.get_line_count()
                if n > 2:
                    start = layout.get_line(n - 2).start_index
                    tail = text.encode("utf-8")[start:].decode("utf-8", "ignore")
                    layout.set_text("… " + tail.lstrip(), -1)
                layout.set_alignment(Pango.Alignment.LEFT)
                # Safety net if the windowed text still spills over two lines.
                layout.set_height(-2)
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            else:
                layout.set_alignment(Pango.Alignment.CENTER)
                layout.set_height(-2)
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            _, th = layout.get_pixel_size()
            cr.move_to(cx - max_width / 2, cy - th / 2)
        else:
            tw, th = layout.get_pixel_size()
            cr.move_to(cx - tw / 2, cy - th / 2)
        PangoCairo.show_layout(cr, layout)

    def _draw_chooser(self, cr, w, h, scheme, a) -> None:
        """Render the refinement-level chooser on the (enlarged) overlay."""
        from loquivox.config import POSTPROCESS_LEVELS
        bg = self._hex_to_rgb(scheme["bg"])
        fg = self._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt = self._hex_to_rgb(scheme["text"])

        self._rounded_rect_path(cr, 0, 0, w, h, 16)
        cr.set_source_rgba(*bg, 0.96 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.30 * a)
        cr.set_line_width(1)
        cr.stroke()

        self._draw_text(cr, self._font_family, "Choose refinement", w / 2, 20, 9.5, fg, a)

        top, row_h = 38, 22
        for i, (lvl, label) in enumerate(POSTPROCESS_LEVELS):
            y = top + i * row_h
            selected = (lvl == self.choose_level)
            if selected:
                self._rounded_rect_path(cr, 10, y, w - 20, row_h - 3, 7)
                cr.set_source_rgba(*fg, 0.92 * a)
                cr.fill()
                row_color, weight = bg, Pango.Weight.BOLD
            else:
                row_color, weight = txt, Pango.Weight.NORMAL
            mid = y + (row_h - 3) / 2
            self._draw_text_at(cr, self._font_family, str(lvl), 22, mid, 8.5, row_color, a, weight)
            self._draw_text_at(cr, self._font_family, label, 48, mid, 8.5, row_color, a, weight)

        self._draw_text(cr, self._font_family, "0-5 / ↑↓  ·  Enter ✓  ·  Esc ✗",
                        w / 2, h - 13, 7.5, fg, a)

    def _draw_ai_panel(self, cr, w, h, scheme, a) -> None:
        """Render the AI action panel (rewrite/vision): thinking or review phase."""
        bg = self._hex_to_rgb(scheme["bg"])
        fg = self._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt = self._hex_to_rgb(scheme["text"])
        thinking = (self.ai_phase == "thinking")

        # Panel frame (same recipe as the chooser).
        self._rounded_rect_path(cr, 0, 0, w, h, 16)
        cr.set_source_rgba(*bg, 0.96 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.30 * a)
        cr.set_line_width(1)
        cr.stroke()

        # Row 1: spinner (thinking) or mode glyph (review) + label.
        if thinking:
            self._icon_spinner(cr, 24, 24, fg, a, self._tick)
        else:
            self._draw_icon(cr, self.mode, 24, 24, fg, a)
        label = {"ai_rewrite": "Rewrite", "vision": "Vision"}.get(self.mode, "AI")
        self._draw_text_at(cr, self._font_family, label, 42, 24, 10.0, fg, a,
                           Pango.Weight.BOLD)

        # Row 2: the transcribed instruction echo (ellipsized to one line).
        icon = self.config.get("icon", "")
        instr = f"{icon} {self.ai_instruction}".strip()
        self._draw_block(cr, self._font_family, instr, 16, 42, w - 30, 18, 8.5,
                         txt, a)

        # Body: "Réflexion…" while thinking, else the (wrapped) result block.
        if thinking:
            self._draw_text(cr, self._font_family, "Réflexion…", w / 2, h / 2 + 4,
                            11.0, fg, a)
        else:
            self._draw_block(cr, self._font_family, self.ai_result, 16, 66,
                             w - 30, h - 92, 9.0, txt, a)

        # Bottom hint line.
        hint = ("Échap pour annuler" if thinking
                else "Entrée ✓   ·   Échap ✗   ·   R ↻   ·   V 🎤")
        self._draw_text(cr, self._font_family, hint, w / 2, h - 15, 7.5, fg, a)

    @staticmethod
    def _draw_block(cr, font_family, text, x, y, width, max_height, size,
                    color, a, weight=Pango.Weight.NORMAL):
        """Left-aligned wrapped text block at (x, y), clipped to max_height with END ellipsis."""
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(weight)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_height(int(max_height * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        cr.set_source_rgba(*color, a)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    @staticmethod
    def _draw_text_at(cr, font_family, text, x, cy, size, color, a,
                      weight=Pango.Weight.NORMAL) -> float:
        """Left-aligned text at x, vertically centered at cy. Returns its width."""
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(weight)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgba(*color, a)
        cr.move_to(x, cy - th / 2)
        PangoCairo.show_layout(cr, layout)
        return tw

    # --------------------------------------------------------------- icons
    @classmethod
    def _draw_icon(cls, cr, mode, cx, cy, color, a):
        """Dispatch to a vector glyph for the recording mode."""
        cr.set_source_rgba(*color, a)
        cr.set_line_width(1.8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        drawer = {
            "dictation": cls._icon_mic,
            "ai": cls._icon_sparkle,
            "ai_rewrite": cls._icon_pencil,
            "vision": cls._icon_camera,
        }.get(mode, cls._icon_mic)
        drawer(cr, cx, cy)

    @staticmethod
    def _icon_mic(cr, cx, cy):
        r = 3.6
        top, bot = cy - 9, cy - 1
        cr.new_sub_path()
        cr.arc(cx, top + r, r, math.pi, 2 * math.pi)
        cr.arc(cx, bot - r, r, 0, math.pi)
        cr.close_path()
        cr.stroke()
        cr.arc(cx, cy - 3, 6.5, math.radians(20), math.radians(160))
        cr.stroke()
        cr.move_to(cx, cy + 3.5)
        cr.line_to(cx, cy + 7)
        cr.stroke()
        cr.move_to(cx - 4, cy + 7)
        cr.line_to(cx + 4, cy + 7)
        cr.stroke()

    @staticmethod
    def _icon_pencil(cr, cx, cy):
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx + 4, cy - 4)
        cr.stroke()
        cr.move_to(cx + 4, cy - 4)
        cr.line_to(cx + 7, cy - 7)
        cr.stroke()
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx - 3, cy + 6.5)
        cr.stroke()

    @classmethod
    def _icon_camera(cls, cr, cx, cy):
        cr.move_to(cx - 3, cy - 6)
        cr.line_to(cx + 1, cy - 6)
        cr.stroke()
        cls._rounded_rect_path(cr, cx - 9, cy - 5, 18, 12, 2.5)
        cr.stroke()
        cr.arc(cx, cy + 1, 3.4, 0, 2 * math.pi)
        cr.stroke()

    @staticmethod
    def _icon_sparkle(cr, cx, cy):
        s, ww = 9.0, 2.6
        cr.move_to(cx, cy - s)
        cr.curve_to(cx + ww, cy - ww, cx + ww, cy - ww, cx + s, cy)
        cr.curve_to(cx + ww, cy + ww, cx + ww, cy + ww, cx, cy + s)
        cr.curve_to(cx - ww, cy + ww, cx - ww, cy + ww, cx - s, cy)
        cr.curve_to(cx - ww, cy - ww, cx - ww, cy - ww, cx, cy - s)
        cr.close_path()
        cr.fill()

    @staticmethod
    def _icon_spinner(cr, cx, cy, color, a, tick):
        """Rotating arc spinner for the transcribing state."""
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        start = (tick * 0.12) % (2 * math.pi)
        cr.set_source_rgba(*color, a)
        # new_sub_path: arc() would otherwise join a leftover current point (e.g.
        # from a preceding text layout) to the arc — a stray diagonal.
        cr.new_sub_path()
        cr.arc(cx, cy, 7, start, start + math.radians(270))
        cr.stroke()

    @staticmethod
    def _rounded_rect_path(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @classmethod
    def _draw_bars(cls, cr, x1, x2, cy, color, a, bars):
        """Draw the smoothed, mirrored EQ bars from a list of 0..1 levels."""
        n = len(bars)
        slot = (x2 - x1) / n
        cr.set_line_width(max(2.0, slot * 0.5))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for i in range(n):
            level = bars[i]
            hh = max(0.6, level * cls.MAX_BAR)
            x = x1 + slot * (i + 0.5)
            cr.set_source_rgba(*color, (0.4 + 0.6 * level) * a)
            cr.move_to(x, cy - hh)
            cr.line_to(x, cy + hh)
            cr.stroke()

    @staticmethod
    def _draw_pulse(cr, x1, x2, cy, color, a, tick):
        """Three pulsing dots to signal transcription in progress."""
        num_dots = 3
        spacing = (x2 - x1) / (num_dots + 1)
        for i in range(num_dots):
            phase = tick / 14.0 - i * 0.7
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

    def close_immediate(self) -> None:
        """
        Destroy the window at once, skipping the ~370 ms fade-out.

        Used right before a Vision screenshot so the overlay never lingers
        in the captured image. Cancels the animation tick first so it can't
        fire on a destroyed window.
        """
        self._closing = True
        if self.timeout_id is not None:
            try:
                GLib.source_remove(self.timeout_id)
            except Exception:
                pass
            self.timeout_id = None
        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    # Self-check for the pure hints logic (no window / audio device needed).
    # Reads the live bindings, so it asserts on shape, not on specific keys —
    # 'refine' is unbound by default but may be bound in the user's config.toml.
    recording = GtkOverlay.hint_items(transcribing=False, paused=False)
    actions = [action for _keys, action in recording]
    assert "pause" in actions, recording
    assert "cancel" in actions, recording
    assert all(keys for keys, _ in recording), recording   # every hint has a key
    assert "resume" in [a for _k, a in GtkOverlay.hint_items(False, True)]
    # Once transcribing, capture is over: pause/refine go, cancel stays.
    assert GtkOverlay.hint_items(True, False) == [
        item for item in recording if item[1] == "cancel"
    ]
    assert GtkOverlay.width() > CFG.OVERLAY_WIDTH   # strip measured, non-zero
    print(f"✓ hint_items OK — {recording} → width {GtkOverlay.width()}px")

    # The refinement badge only applies to dictation, and claims its own strip
    # (so the live transcript can never paint over it).
    assert GtkOverlay.refine_badge_for("ai") is None
    assert GtkOverlay.width("Medium +fmt") > GtkOverlay.width()
    badge = GtkOverlay.refine_badge_for("dictation")
    print(f"✓ refine badge OK — dictation → {badge!r}, "
          f"width {GtkOverlay.width(badge)}px")

    # Waveform: drive _update_bars with the block sizes PortAudio really hands
    # us (139/32/4 frames at 16 kHz) — the shapes that used to leave 20 of the
    # 28 bars pinned at zero. __new__ skips __init__, so no GTK window is made.
    o = GtkOverlay.__new__(GtkOverlay)
    o._tick, o._last_audio_tick, o._level = 0, 0, 0.0
    o._bars = [0.0] * GtkOverlay.NUM_BARS
    rng = np.random.default_rng(7)
    for frame in range(90):                      # 1.5 s at 60 fps of normal speech
        o._tick = frame
        for size in (139, 32, 4):
            STATE.viz_queue.put(rng.normal(0, 0.09, size).astype(np.float32))
        o._update_bars()
    assert min(o._bars) > 0.2, o._bars           # no dead zone: the whole width lives
    assert max(o._bars) < 0.99, o._bars          # normal speech doesn't peg the meter
    print(f"✓ waveform OK — bars {min(o._bars):.2f}–{max(o._bars):.2f}, none dead")
