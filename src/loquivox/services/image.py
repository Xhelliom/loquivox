"""
Screenshot and image encoding service.

Uses the platform abstraction layer to work on both X11 and Wayland.

Captures can be reframed before upload — cropped around the mouse pointer, and
downscaled to a maximum edge. That matters on a 4K screen: a full capture is
several megabytes of base64 for a model that will downscale it anyway, so the
resize is what keeps talk mode's screen context cheap. Reframing uses
GdkPixbuf, which PyGObject already brings in — no new dependency, no Pillow.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from loquivox.config import CFG
from loquivox.decorators import safe_execute
from loquivox.platform import get_screenshot


class ImageService:
    """Screenshot and image encoding service."""

    @staticmethod
    @safe_execute("Screenshot")
    def take_screenshot(*, path: Optional[str] = None, region: str = "screen",
                        cursor_px: int = 0, max_px: int = 0) -> Optional[str]:
        """
        Take a screenshot and return it base64-encoded (PNG), or None on failure.

        ``region="window"`` captures the focused window alone — the one that
        actually buys legibility, since the image's token budget is fixed
        whatever its size. ``region="cursor"`` crops a ``cursor_px``-wide box
        around the pointer
        (its height follows the screen's aspect ratio) — when the session can
        say where the pointer is; it silently keeps the whole screen when it
        can't. ``max_px`` caps the long edge of the result. Both default to off,
        so the vision-mode call is unchanged. ``path`` overrides the temp file,
        so two captures can be in flight without fighting over it.
        """
        output = path or CFG.TEMP_SCREEN_PATH
        screenshot = get_screenshot()
        # "window" asks the session to frame the focused window; not every one
        # can, and the whole screen is always a usable answer.
        framed = region == "window" and screenshot.take_window_screenshot(output)
        if not framed and not screenshot.take_screenshot(output):
            print("❌ Screenshot failed")
            return None
        if region == "window" and not framed:
            print("ℹ️  This session cannot frame a single window — "
                  "keeping the whole screen")

        # A framed window is already the crop; only the downscale is left.
        data = (ImageService._reframe(output,
                                      region="screen" if framed else region,
                                      cursor_px=cursor_px, max_px=max_px)
                if (region == "cursor" or max_px) else None)
        try:
            if data is None:
                with open(output, "rb") as f:
                    data = f.read()
            if os.environ.get("LOQUIVOX_KEEP_SCREENSHOT"):
                ImageService._keep(output, data)
            return base64.b64encode(data).decode("utf-8")
        finally:
            try:
                os.remove(output)
            except OSError:
                pass

    @staticmethod
    def _keep(output: str, data: bytes) -> None:
        """
        Debug aid: leave the bytes that were *sent* on disk, cropped and
        downscaled — not the raw capture, which is the one thing a legibility
        problem never shows up in. Off unless ``LOQUIVOX_KEEP_SCREENSHOT`` is set.
        """
        path = output + ".sent.png"
        try:
            with open(path, "wb") as f:
                f.write(data)
            print(f"📷 Sent to the vision model: {path} "
                  f"({len(data) // 1024} KB)")
        except OSError as e:
            print(f"⚠️  Could not keep the screenshot ({e})")

    @staticmethod
    def _reframe(path: str, *, region: str, cursor_px: int,
                 max_px: int) -> Optional[bytes]:
        """
        The capture cropped and/or downscaled, as PNG bytes.

        Encoded straight to memory: the caller only ever base64s the result, so
        writing it back over the file just to read it again would be a second
        full PNG encode of a multi-megapixel image. Best-effort — None means
        the caller sends the file as captured, which is always still usable.
        """
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            width, height = pixbuf.get_width(), pixbuf.get_height()

            if region == "cursor" and cursor_px > 0:
                point = get_screenshot().pointer_position()
                if point is None:
                    print("ℹ️  Pointer position unavailable on this session — "
                          "keeping the whole screen")
                else:
                    box_w = max(160, min(width, int(cursor_px)))
                    box_h = max(120, min(height, round(box_w * height / width)))
                    # Clamp so the box stays inside the capture even at an edge.
                    x = max(0, min(width - box_w, point[0] - box_w // 2))
                    y = max(0, min(height - box_h, point[1] - box_h // 2))
                    pixbuf = pixbuf.new_subpixbuf(x, y, box_w, box_h)
                    width, height = box_w, box_h

            if max_px and max(width, height) > max_px:
                scale = max_px / float(max(width, height))
                pixbuf = pixbuf.scale_simple(
                    max(1, int(width * scale)), max(1, int(height * scale)),
                    GdkPixbuf.InterpType.BILINEAR,
                )

            ok, data = pixbuf.save_to_bufferv("png", [], [])
            return data if ok else None
        except Exception as e:
            print(f"⚠️  Could not reframe the screenshot ({e}) — sending it as captured")
            return None
