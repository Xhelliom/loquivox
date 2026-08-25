"""
Chat overlay state and message management.
"""
from __future__ import annotations

import time
from typing import Optional

from loquivox.config import CFG
from loquivox.decorators import run_on_main_thread
from loquivox.state import STATE

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib


class ChatManager:
    """Manages chat overlay state and messages."""

    @staticmethod
    def add_message(role: str, text: str, status: Optional[str] = None) -> None:
        """
        Add a message to the chat overlay.

        ``role`` becomes the bubble's CSS class, so "user", "assistant" and
        "result" (talk mode's generated text) all go through this one path.
        ``status`` is the single line shown under it — the review keys, when
        the message is something to accept or reject.
        """
        STATE.chat_messages.append({"role": role, "text": text})

        # Trim to limit
        if len(STATE.chat_messages) > CFG.CHAT_MESSAGE_LIMIT:
            STATE.chat_messages = STATE.chat_messages[-CFG.CHAT_MESSAGE_LIMIT:]

        ChatManager._last_stream = 0.0  # the live node is about to be replaced
        ChatManager.refresh_overlay(status)

    @staticmethod
    def clear() -> None:
        """
        Empty the overlay.

        Only what is on screen: the answer history and the models' own
        conversation histories are somebody else's to clear.
        """
        STATE.chat_messages = []
        ChatManager._last_stream = 0.0

    @staticmethod
    def set_result(text: str, status: str) -> None:
        """
        Put a generated text up for review, replacing the previous attempt.

        A rewrite (R) must not stack a second copy under the first: what is on
        screen is the candidate, and there is only ever one.
        """
        if STATE.chat_messages and STATE.chat_messages[-1]["role"] == "result":
            STATE.chat_messages.pop()
        ChatManager.add_message("result", text, status=status)

    @staticmethod
    def _keep_open() -> bool:
        """
        True while the overlay must not fade out: pinned, being typed into, or
        carrying a talk conversation — whose turns last far longer than the
        auto-hide delay, so without this the WebView is destroyed and rebuilt
        between every single turn.
        """
        return STATE.chat_pinned or STATE.chat_input_focused or STATE.talk_active

    @staticmethod
    def toggle_pin() -> None:
        """Toggle chat overlay pin mode."""
        if not STATE.chat_enabled:
            return
            
        STATE.chat_pinned = not STATE.chat_pinned
        if STATE.chat_pinned:
            STATE.chat_hidden = False  # pinning is how a hidden bubble comes back

        if not STATE.chat_pinned and STATE.chat_overlay_window:
            ChatManager._cancel_timer()
            STATE.chat_overlay_window.start_fade_out(callback=ChatManager._destroy)
        else:
            ChatManager.refresh_overlay()

    @staticmethod
    @run_on_main_thread
    def refresh_overlay(status_text: Optional[str] = None) -> None:
        """Refresh chat overlay on main thread."""
        ChatManager._show_overlay(status_text)

    @staticmethod
    def _show_overlay(status_text: Optional[str] = None) -> None:
        """Show or update chat overlay."""
        ChatManager._cancel_timer()

        if not STATE.chat_enabled:
            ChatManager._destroy()
            return
        if STATE.chat_hidden:
            return  # the user closed it — nothing reopens it behind their back

        if not STATE.chat_overlay_window:
            # Late import to avoid circular dependency
            from loquivox.ui.chat_overlay import ChatOverlay
            STATE.chat_overlay_window = ChatOverlay()
        elif STATE.chat_overlay_window.fade_out_active:
            STATE.chat_overlay_window.start_fade_in()

        STATE.chat_overlay_window.update_content(
            STATE.chat_messages,
            status_text,
            is_pinned=STATE.chat_pinned,
            is_tts=STATE.tts_enabled
        )

        # Don't arm the auto-hide while the text input is focused, or the
        # overlay would fade out from under the user mid-typing (an unrelated
        # refresh — e.g. a TTS toggle — would otherwise re-arm it).
        if not ChatManager._keep_open():
            STATE.chat_hide_timer = GLib.timeout_add_seconds(
                CFG.CHAT_AUTO_HIDE_SEC,
                ChatManager._auto_hide
            )

    @staticmethod
    def set_keepalive(active: bool) -> None:
        """
        Pause the auto-hide timer while the chat input box is focused, and
        resume it on blur. The ``chat_input_focused`` flag also makes
        ``_show_overlay`` skip re-arming the timer on unrelated refreshes, so
        the overlay can't vanish mid-typing. No-op when pinned. Main thread only.
        """
        STATE.chat_input_focused = active
        if active:
            ChatManager._cancel_timer()
        elif not ChatManager._keep_open() and STATE.chat_overlay_window:
            ChatManager._cancel_timer()
            STATE.chat_hide_timer = GLib.timeout_add_seconds(
                CFG.CHAT_AUTO_HIDE_SEC, ChatManager._auto_hide
            )

    @staticmethod
    @run_on_main_thread
    def set_talk(active: bool) -> None:
        """
        Open the bubble the moment talk mode starts, and hand the window back
        to the side conversation when the session ends.

        The overlay normally appears with the first message; a spoken session
        has to be visible before that, because the first thing it shows is the
        transcript of a sentence still being said.
        """
        if not STATE.chat_enabled:
            return
        # Whichever way the session turns, a bubble closed by hand does not
        # outlive it: the next F4 answer must not land in a window nobody sees.
        STATE.chat_hidden = False
        if active:
            # A new conversation starts on an empty bubble — the previous
            # session's turns are history, and reading them as this one's is
            # the confusion this avoids.
            ChatManager.clear()
        if not active and STATE.chat_overlay_window is None:
            return  # nothing to hand back
        if STATE.chat_overlay_window is None:
            from loquivox.ui.chat_overlay import ChatOverlay
            STATE.chat_overlay_window = ChatOverlay(talk=True)
        else:
            STATE.chat_overlay_window.set_talk_mode(active)
        ChatManager._show_overlay()

    #: last time a live update reached the page — the model writes far faster
    #: than anyone reads, and every update crosses a process boundary
    _last_stream: float = 0.0
    _STREAM_INTERVAL: float = 0.08

    @staticmethod
    def stream(role: str, text: str) -> None:
        """
        Show the turn in progress — the transcript being heard, then the reply
        being written — without rebuilding the page.

        Throttled: a token-by-token stream would fire dozens of times a second
        for text that is read at reading speed. Dropping updates is safe
        because each one carries the whole turn, not an increment, and the
        finished turn arrives as a real message right after.
        """
        now = time.monotonic()
        if now - ChatManager._last_stream < ChatManager._STREAM_INTERVAL:
            return
        ChatManager._last_stream = now
        ChatManager._stream_impl(role, text)

    @staticmethod
    @run_on_main_thread
    def _stream_impl(role: str, text: str) -> None:
        if STATE.chat_overlay_window:
            STATE.chat_overlay_window.set_live(role, text)

    @staticmethod
    @run_on_main_thread
    def hide_manual() -> None:
        """
        The ✕ on the bubble: hide it now, whatever is holding it open.

        Deliberately stronger than the auto-hide — that one refuses to fire
        while pinned, typed into or mid-conversation, and all three of those
        are exactly when someone reaches for the close button. The pin hotkey
        brings it back.
        """
        STATE.chat_hidden = True
        ChatManager._cancel_timer()
        if STATE.chat_overlay_window:
            STATE.chat_overlay_window.start_fade_out(callback=ChatManager._destroy)

    @staticmethod
    def _auto_hide() -> bool:
        """Auto-hide callback."""
        STATE.chat_hide_timer = None
        if not ChatManager._keep_open() and STATE.chat_overlay_window:
            STATE.chat_overlay_window.start_fade_out(callback=ChatManager._destroy)
        return False

    @staticmethod
    def _cancel_timer() -> None:
        """Cancel auto-hide timer if active."""
        if STATE.chat_hide_timer:
            GLib.source_remove(STATE.chat_hide_timer)
            STATE.chat_hide_timer = None

    @staticmethod
    def _destroy() -> None:
        """Destroy chat overlay window."""
        if STATE.chat_overlay_window:
            STATE.chat_overlay_window.close()
            STATE.chat_overlay_window = None
