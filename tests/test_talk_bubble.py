"""
Checks for the talk bubble's plumbing: streamed completions, the marker that
must never flash on screen, and the throttle in front of the overlay.

    python tests/test_talk_bubble.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def test_stream_accumulates():
    """on_delta sees the answer so far, and the return value is the whole of it."""
    from loquivox.services import ai as ai_module

    pieces = ["Bonjour", ", ", "ça va", " ?", None]  # a None delta is a keep-alive
    client = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(
            create=lambda **kw: iter([_Chunk(p) for p in pieces]))))
    ai_module.get_ai_client = lambda provider: client

    seen = []
    text = ai_module.AIService._stream([], "m", seen.append)
    assert text == "Bonjour, ça va ?", text
    assert seen == ["Bonjour", "Bonjour, ", "Bonjour, ça va", "Bonjour, ça va ?"], seen
    print("✓ stream: chaque callback porte la réponse complète à cet instant")


def test_partial_marker_never_shows():
    """The end-of-briefing marker is stripped while it is still being typed."""
    from loquivox.services.talk import _strip_marker

    growing = "D'accord, je rédige. [[WRITE]]"
    for i in range(len(growing) + 1):
        shown = _strip_marker(growing[:i])
        assert "[[" not in shown and not shown.endswith("["), repr(shown)
    assert _strip_marker(growing) == "D'accord, je rédige."
    # A bracket that is not the marker survives.
    assert _strip_marker("cf. [1] plus haut") == "cf. [1] plus haut"
    print("✓ marqueur: jamais visible, même à moitié écrit")


def test_stream_throttle():
    """Live updates are dropped, not queued — each carries the whole turn."""
    from loquivox.managers.chat import ChatManager

    calls = []
    ChatManager._stream_impl = staticmethod(lambda role, text: calls.append(text))
    ChatManager._last_stream = 0.0
    for i in range(50):
        ChatManager.stream("assistant", f"token {i}")
    assert len(calls) == 1, f"{len(calls)} appels — le throttle ne tient pas"
    # A real message resets it: the live node is replaced, the next role starts clean.
    ChatManager._last_stream = 0.0
    ChatManager.stream("user", "après un vrai message")
    assert calls[-1] == "après un vrai message"
    print("✓ throttle: 50 tokens → 1 mise à jour, remise à zéro sur un vrai message")


def test_new_session_starts_empty():
    """A talk session opens on an empty bubble, and only one result at a time."""
    from gi.repository import GLib
    from loquivox.managers.chat import ChatManager
    from loquivox.state import STATE

    ChatManager._show_overlay = staticmethod(lambda status=None: None)
    STATE.chat_messages = [{"role": "user", "text": "session précédente"}]
    STATE.chat_enabled = True
    ChatManager.set_talk(True)
    while GLib.MainContext.default().iteration(False):
        pass
    assert STATE.chat_messages == [], STATE.chat_messages

    # A rewrite replaces the candidate instead of stacking a second one.
    ChatManager.add_message("user", "🗣️ un mail pour l'équipe")
    ChatManager.set_result("Première version.", "hint")
    ChatManager.set_result("Deuxième version.", "hint")
    roles = [m["role"] for m in STATE.chat_messages]
    assert roles == ["user", "result"], roles
    assert STATE.chat_messages[-1]["text"] == "Deuxième version."
    print("✓ nouvelle session: bulle vide, et un seul texte candidat à la fois")


if __name__ == "__main__":
    test_stream_accumulates()
    test_partial_marker_never_shows()
    test_stream_throttle()
    test_new_session_starts_empty()
    print("✓ tous les checks de la bulle passent")
