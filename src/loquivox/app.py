"""
Loquivox — Application entry point.

``loquivox`` starts the app; ``loquivox --report`` writes the diagnostic
report and exits, ``--version`` prints the version. The two flags are handled
before GTK is imported, because the machine that needs a report is often the
one where the GTK stack is what is broken.
"""
from __future__ import annotations

import os
import sys
import threading
import warnings

# Suppress libEGL warnings by forcing software rendering for GTK/WebKit
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*Specified provider 'CUDAExecutionProvider'.*")

from loquivox import __version__

USAGE = """\
usage: loquivox [--report [PATH] [--keep-content]] [--version]

  (no flag)        run the assistant (tray icon + global hotkeys)
  --report [PATH]  write the redacted diagnostic report (default:
                   ~/loquivox-report-<date>.txt; '-' prints it) and exit
  --keep-content   with --report: keep transcripts and screen descriptions
                   (keys, addresses and names are removed either way)
  --version        print the version and exit
"""


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
        import gi
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2
        WebKit2.WebView()
    except Exception:
        pass
    return False


def main() -> None:
    """Application entry point: flags first, the app itself otherwise."""
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(USAGE, end="")
        return
    if "--version" in argv:
        print(f"loquivox {__version__}")
        return
    if "--report" in argv:
        from loquivox.diagnostics import report_cli
        sys.exit(report_cli([a for a in argv if a != "--report"]))
    _run()


def _run() -> None:
    """Start the assistant: log file, keys, keyboard thread, GTK tray loop."""
    # Mirror stdout/stderr into ~/.local/state/loquivox/loquivox.log before
    # the first print, so the report has something to carry when the app
    # was started from the autostart entry and nobody saw the terminal.
    from loquivox.diagnostics import install_log_file
    log_path = install_log_file()

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib

    from loquivox.config import CFG, HOTKEY_DESCRIPTIONS
    from loquivox.handlers.keyboard import KeyboardHandler
    from loquivox.secrets import load_secrets
    from loquivox.state import STATE
    from loquivox.ui.hotkey_bar import HotkeyBar
    from loquivox.ui.tray import TrayManager

    # Load UI-managed API keys into the environment before any backend reads
    # them (keys present in the file win; inherited env keys still apply).
    load_secrets()

    print(f"🚀 Loquivox {__version__} is running.")
    if log_path is not None:
        print(f"🪵 Log: {log_path} · bug report: loquivox --report")

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
