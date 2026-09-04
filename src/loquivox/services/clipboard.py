"""
Clipboard operations for typing and pasting text.

Uses the platform abstraction layer to work on both X11 and Wayland.
Detects terminal emulators and uses the correct keyboard shortcuts
(Ctrl+Shift+V/C instead of Ctrl+V/C).
"""
from __future__ import annotations

import time

from loquivox.config import CFG
from loquivox.platform import get_clipboard, get_input


class ClipboardService:
    """Clipboard operations for typing and pasting text."""

    @staticmethod
    def type_text(text: str) -> None:
        """Paste text at cursor via clipboard (fast)."""
        if not text:
            return

        clipboard = get_clipboard()
        inp = get_input()

        # Save original clipboard
        try:
            original = clipboard.paste()
        except Exception:
            original = None

        # Add leading space to prevent word merging
        clean_text = f" {text.strip()}" if not text.startswith(" ") else text

        # Paste via clipboard — use correct shortcut for terminals
        clipboard.copy(clean_text)
        is_term = inp.is_terminal_focused()
        time.sleep(CFG.CLIPBOARD_PASTE_DELAY)  # let the clipboard offer settle
        inp.simulate_paste(is_terminal=is_term)

        # Restore original clipboard once the paste has been consumed
        time.sleep(CFG.CLIPBOARD_RESTORE_DELAY)
        if original is not None:
            try:
                clipboard.copy(original)
            except Exception:
                pass

    @staticmethod
    def copy_selected() -> str:
        """
        Copy the current selection and return it — "" when nothing was selected.

        Ctrl+C with no selection leaves the clipboard untouched, so what comes
        back would be whatever was copied a minute ago; the model would then be
        handed an old text as "the selection". The clipboard is emptied first
        so that case reads as nothing, and a non-empty result is really the
        selection. ponytail: the previous clipboard content is not restored —
        a talk session ends by leaving its own text there anyway.
        """
        clipboard = get_clipboard()
        inp = get_input()
        clipboard.copy("")
        inp.simulate_copy(is_terminal=inp.is_terminal_focused())
        time.sleep(CFG.CLIPBOARD_PASTE_DELAY)
        return clipboard.paste().strip()

    @staticmethod
    def paste_text(text: str) -> None:
        """Paste text directly via clipboard."""
        clipboard = get_clipboard()
        inp = get_input()
        clipboard.copy(text)
        is_term = inp.is_terminal_focused()
        inp.simulate_paste(is_terminal=is_term)
