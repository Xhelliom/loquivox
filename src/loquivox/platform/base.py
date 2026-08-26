"""
Abstract base classes for platform backends.

Each backend (X11, Wayland) must implement these interfaces.
Adding a new platform (e.g. macOS) requires only a new module
that provides concrete implementations of these ABCs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class ClipboardBackend(ABC):
    """Platform-specific clipboard operations."""

    @abstractmethod
    def copy(self, text: str) -> None:
        """Copy text to the system clipboard."""

    @abstractmethod
    def paste(self) -> str:
        """Return the current clipboard contents as text."""


class InputBackend(ABC):
    """Platform-specific keyboard/input simulation."""

    @abstractmethod
    def simulate_paste(self, is_terminal: bool = False) -> None:
        """
        Simulate a paste keystroke (Ctrl+V or Ctrl+Shift+V for terminals).
        """

    @abstractmethod
    def simulate_copy(self, is_terminal: bool = False) -> None:
        """
        Simulate a copy keystroke (Ctrl+C or Ctrl+Shift+C for terminals).
        """

    @abstractmethod
    def is_terminal_focused(self) -> bool:
        """
        Check if the currently focused window is a terminal emulator.

        Used to decide between Ctrl+V and Ctrl+Shift+V style shortcuts.
        Returns False if detection is not possible (safe default).
        """


class ScreenshotBackend(ABC):
    """Platform-specific screenshot capture."""

    @abstractmethod
    def take_screenshot(self, output_path: str) -> bool:
        """
        Capture a full-screen screenshot and save to *output_path*.

        Returns:
            True on success, False on failure.
        """

    def take_window_screenshot(self, output_path: str) -> bool:
        """
        Capture the focused window alone, or return False when the session
        cannot — the caller then falls back to the whole screen.

        The point is pixels per character, not framing: a vision model gets a
        fixed token budget for an image whatever its size, so a 4K desktop
        downscaled into it is unreadable while one window of it is not. On a
        tiling compositor this is also the better question — the focused window
        IS what the user is looking at, and there is no pointer to ask about.
        """
        return False

    def pointer_position(self) -> Optional[Tuple[int, int]]:
        """
        Where the mouse pointer is, in the same coordinates as a full-screen
        capture — or None when the session cannot tell.

        None is a normal answer, not a failure: Wayland has no protocol for a
        client to ask where the pointer is (only the compositor knows, and only
        some of them expose it). Callers must degrade instead of insisting —
        cropping around the cursor becomes cropping nothing.
        """
        return None
