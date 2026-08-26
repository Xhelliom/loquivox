"""
Wayland platform backends.

Uses: wtype, wl-copy, wl-paste, grim
Compositor-specific: niri msg (optional, for terminal detection)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Tuple

from loquivox.config import CFG
from loquivox.platform.base import ClipboardBackend, InputBackend, ScreenshotBackend

# Terminal app-ids matched against focused window info (lowercase).
_TERMINAL_KEYWORDS: Tuple[str, ...] = (
    "terminal", "terminator", "tilix", "alacritty", "kitty",
    "konsole", "xterm", "urxvt", "sakura", "terminology",
    "guake", "tilda", "yakuake", "wezterm", "foot",
    "cool-retro-term", "hyper", "tabby", "rio", "ghostty",
)


class WaylandClipboard(ClipboardBackend):
    """Clipboard via wl-copy / wl-paste (Wayland)."""

    def copy(self, text: str) -> None:
        try:
            proc = subprocess.Popen(
                ["wl-copy"],
                stdin=subprocess.PIPE,
            )
            proc.communicate(input=text.encode("utf-8"))
        except Exception as e:
            print(f"⚠️ Wayland clipboard copy error: {e}")

    def paste(self) -> str:
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout
        except Exception:
            return ""


class WaylandInput(InputBackend):
    """Input simulation via wtype (Wayland)."""

    # Cache for terminal detection — a single dictation insertion calls
    # is_terminal_focused() several times; this avoids repeated `niri msg`
    # subprocesses within a short window.
    _term_cache_value: bool = False
    _term_cache_time: float = 0.0

    def simulate_paste(self, is_terminal: bool = False) -> None:
        if is_terminal:
            # Ctrl+Shift+V: wtype needs modifiers spelled out
            subprocess.run(
                ["wtype", "-M", "ctrl", "-M", "shift", "-P", "v", "-p", "v", "-m", "shift", "-m", "ctrl"],
                timeout=2,
            )
        else:
            subprocess.run(
                ["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"],
                timeout=2,
            )

    def simulate_copy(self, is_terminal: bool = False) -> None:
        if is_terminal:
            subprocess.run(
                ["wtype", "-M", "ctrl", "-M", "shift", "-P", "c", "-p", "c", "-m", "shift", "-m", "ctrl"],
                timeout=2,
            )
        else:
            subprocess.run(
                ["wtype", "-M", "ctrl", "-P", "c", "-p", "c", "-m", "ctrl"],
                timeout=2,
            )

    def is_terminal_focused(self) -> bool:
        """
        Detect focused terminal via compositor IPC, cached for a short TTL.

        Currently supports niri (via `niri msg focused-window`).
        Returns False for unsupported compositors (safe default → Ctrl+V).
        """
        now = time.monotonic()
        if now - WaylandInput._term_cache_time < CFG.TERMINAL_CACHE_TTL:
            return WaylandInput._term_cache_value
        result = self._detect_terminal()
        WaylandInput._term_cache_value = result
        WaylandInput._term_cache_time = now
        return result

    def _detect_terminal(self) -> bool:
        """Query the compositor for the focused window's terminal-ness."""
        # Try niri IPC
        try:
            result = subprocess.run(
                ["niri", "msg", "-j", "focused-window"],
                capture_output=True, text=True, timeout=1,
            )
            if result.returncode == 0 and result.stdout.strip():
                info = json.loads(result.stdout)
                app_id = (info.get("app_id") or "").lower()
                title = (info.get("title") or "").lower()
                combined = f"{app_id} {title}"
                return any(kw in combined for kw in _TERMINAL_KEYWORDS)
        except (FileNotFoundError, json.JSONDecodeError, Exception):
            pass

        # Fallback: cannot detect → safe default (Ctrl+V)
        return False


def _wait_for_png(path: str, timeout: float = 2.0) -> bool:
    """
    True once *path* is a PNG that has been written all the way to its end.

    niri's screenshot action answers before the file is on disk (measured: rc=0
    at 39 ms, the file at 100 ms), so returning on the exit code alone reads as
    "this session cannot frame a window" and falls back to the whole screen —
    the one thing the framing exists to avoid.

    The end is checked rather than the size settling: a writer that pauses
    mid-file looks exactly like a finished one to a size poll, and a PNG always
    closes with a 12-byte IEND chunk, so there is nothing to guess.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(path, "rb") as f:
                f.seek(-8, os.SEEK_END)
                if f.read(4) == b"IEND":
                    return True
        except OSError:
            pass  # not there yet, or shorter than a trailer
        time.sleep(0.03)
    return False


class WaylandScreenshot(ScreenshotBackend):
    """Screenshot via grim (Wayland)."""

    def take_screenshot(self, output_path: str) -> bool:
        try:
            result = subprocess.run(
                ["grim", output_path],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def take_window_screenshot(self, output_path: str) -> bool:
        """
        The focused window via niri's own IPC, which is the only compositor
        here that will frame a window for a client.

        grim cannot do it: capturing a window needs its geometry, and niri
        reports ``tile_pos_in_workspace_view`` as null, so there is nothing to
        hand ``grim -g``. Its screenshot action does the framing itself.

        niri also always copies the shot to the clipboard, with no flag to skip
        it, so this costs the user whatever they had copied. Restoring that is
        opt-in (``TALK_SCREENSHOT_RESTORE_CLIPBOARD``): a talk session ends by
        leaving its generated text on the clipboard anyway, so by default the
        only loss is something from a minute ago.
        """
        import loquivox.config as config_module  # live: reload_config() rebinds it

        saved = None
        if config_module.CFG.TALK_SCREENSHOT_RESTORE_CLIPBOARD:
            # ponytail: text only — that is all ClipboardBackend speaks, and a
            # general save/restore means re-offering arbitrary MIME types.
            # Upgrade to `wl-paste --list-types` + a wl-copy per type if
            # someone loses an image to this.
            saved = WaylandClipboard().paste()
        try:
            result = subprocess.run(
                ["niri", "msg", "action", "screenshot-window",
                 "--path", output_path, "--write-to-disk", "true"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                return False
        except Exception:
            return False
        framed = _wait_for_png(output_path)
        if saved:
            # After the wait, never before: niri puts the image on the
            # clipboard around the same time it finishes writing the file.
            WaylandClipboard().copy(saved)
        return framed

    def pointer_position(self):
        """
        Pointer coordinates, when the compositor is willing to say.

        Wayland deliberately has no protocol for this — a client only learns
        the pointer position while it is over its own surface. Compositors that
        expose it do so through their own IPC, so this tries the ones that ship
        a CLI (Hyprland) and returns None everywhere else, which the caller
        reads as "capture the whole screen".
        """
        try:
            result = subprocess.run(
                ["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                x, _, y = result.stdout.strip().partition(",")
                return int(x.strip()), int(y.strip())
        except Exception:
            pass
        return None


if __name__ == "__main__":
    # Self-check for the write-settling wait, which is the whole reason the
    # niri framing works at all:  python -m loquivox.platform.wayland
    import tempfile
    import threading

    missing = f"{tempfile.gettempdir()}/loquivox-no-such-shot.png"
    start = time.monotonic()
    assert not _wait_for_png(missing, timeout=0.2), "invented a file"
    assert time.monotonic() - start < 1.0, "a missing file must fail fast"

    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    # Written late, and with a pause in the middle: a size poll reads that
    # pause as a finished file and hands over half a capture.
    def _write() -> None:
        time.sleep(0.15)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
            f.flush()
            time.sleep(0.2)
            f.write(b"\x00\x00\x00\x00IEND\xaeB`\x82")

    threading.Thread(target=_write, daemon=True).start()
    assert _wait_for_png(path, timeout=2.0), "gave up before the file landed"
    assert os.path.getsize(path) == 208 + 12, "returned on a half-written file"
    os.remove(path)
    print("✓ niri screenshot write-settling OK")
