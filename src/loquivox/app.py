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

from loquivox.config import CFG, HOTKEY_DESCRIPTIONS
from loquivox.handlers.keyboard import KeyboardHandler
from loquivox.secrets import load_secrets
from loquivox.ui.hotkey_bar import HotkeyBar
from loquivox.ui.tray import TrayManager


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
    print(f"\n🧠 Chat {CFG.MODEL_CHAT} · Vision {CFG.MODEL_VISION}")
    print("📌 System tray icon active")

    # Start keyboard listener in background thread
    keyboard_thread = threading.Thread(target=KeyboardHandler.run, daemon=True)
    keyboard_thread.start()

    # Screen-edge hotkey cheat sheet (no-op when turned off in Settings).
    HotkeyBar.start()

    # Run GTK main loop (blocks)
    TrayManager.start()


if __name__ == "__main__":
    main()
