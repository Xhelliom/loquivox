"""
Loquivox — Application entry point.
"""
from __future__ import annotations

import os
import threading
import warnings

# Suppress libEGL warnings by forcing software rendering for GTK/WebKit
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*Specified provider 'CUDAExecutionProvider'.*")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib

from loquivox.config import CFG, HOTKEY_DESCRIPTIONS
from loquivox.handlers.keyboard import KeyboardHandler
from loquivox.secrets import load_secrets
from loquivox.state import STATE
from loquivox.ui.hotkey_bar import HotkeyBar
from loquivox.ui.tray import TrayManager


def _warm_webkit() -> bool:
    """
    Pay WebKit's one-off startup cost now instead of on the first hotkey.

    The first WebView in a process takes ~270 ms to build — library init and
    the web process pool — and every one after it takes ~3 ms. That first one
    used to be the chat overlay opening on F4 or F6, which froze the GTK loop,
    and with it the recording overlay's animation, at exactly the moment a
    window was supposed to be appearing. The warmth outlives the view, so one
    throwaway is enough. Silent on failure: a machine without WebKit has no
    chat overlay either.
    """
    try:
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2
        WebKit2.WebView()
    except Exception:
        pass
    return False


def main() -> None:
    """Application entry point."""
    # Load UI-managed API keys into the environment before any backend reads
    # them (keys present in the file win; inherited env keys still apply).
    load_secrets()

    print("🚀 Loquivox is running.")

    i = 1
    for mode_id, (label, _specs) in CFG.HOTKEY_DEFS.items():
        desc = HOTKEY_DESCRIPTIONS.get(mode_id, "Unknown Mode")
        print(f" {i}. {label:<13}: {desc}")
        i += 1
    print(f"\n🧠 {STATE.ai_provider}: chat {STATE.ai_chat_model} · "
          f"vision {STATE.ai_vision_model}")
    print("📌 System tray icon active")

    # Start keyboard listener in background thread
    keyboard_thread = threading.Thread(target=KeyboardHandler.run, daemon=True)
    keyboard_thread.start()

    # Screen-edge hotkey cheat sheet (no-op when turned off in Settings).
    HotkeyBar.start()

    GLib.idle_add(_warm_webkit, priority=GLib.PRIORITY_LOW)

    # Run GTK main loop (blocks)
    TrayManager.start()


if __name__ == "__main__":
    main()
