"""
A page reload must not replay the conversation's entry animation.

``update_content`` replaces the whole page on every message, and every
``.message-wrapper`` starts at opacity 0 and fades in: without a marker the
whole conversation blinked at each turn. Only the messages that are new since
the last render may animate.

    python tests/test_bubble_reload.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _overlay():
    from loquivox.ui.chat_overlay import ChatOverlay

    pages, js = [], []
    fake = types.SimpleNamespace(
        talk=False, _shown=0, _sig=None, _sent=[], _ready=True, _pending_js=None,
        _run_js=lambda script: js.append(script),
        webview=types.SimpleNamespace(load_html=lambda html, base: pages.append(html)),
        _talk_bar=lambda: "", _render_markdown=ChatOverlay._render_markdown)
    fake.js = js
    return ChatOverlay, fake, pages


def _classes(html):
    return re.findall(r'<div class="message-wrapper ([a-z ]*)">', html)


def test_only_new_messages_animate():
    cls, o, pages = _overlay()
    msgs = [{"role": "user", "text": "a"}]
    cls.update_content(o, msgs)
    assert _classes(pages[-1]) == ["user"], _classes(pages[-1])

    msgs.append({"role": "assistant", "text": "b"})
    cls.update_content(o, msgs)
    assert _classes(pages[-1]) == ["user seen", "assistant"], _classes(pages[-1])
    print("✓ un rechargement ne rejoue que l'animation du dernier message")


def test_cleared_conversation_animates_again():
    cls, o, pages = _overlay()
    msgs = [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}]
    cls.update_content(o, msgs)
    cls.update_content(o, [{"role": "user", "text": "c"}])
    assert _classes(pages[-1]) == ["user"], _classes(pages[-1])
    print("✓ après un clear, le premier message s'anime de nouveau")


def test_css_has_a_static_state():
    from loquivox.ui.chat_overlay import CHAT_CSS

    assert ".message-wrapper.seen" in CHAT_CSS
    print("✓ la feuille de style connaît .seen")


def test_talk_appends_without_reloading():
    cls, o, pages = _overlay()
    o.talk = True
    kb = types.ModuleType("loquivox.handlers.keyboard")  # the real one pulls in the audio/AI stack
    kb.KeyboardHandler = types.SimpleNamespace(talk_hints=lambda: [])
    sys.modules["loquivox.handlers.keyboard"] = kb
    msgs = [{"role": "user", "text": "a"}]
    cls.update_content(o, msgs)
    assert len(pages) == 1
    o._ready = True  # what load-changed FINISHED does
    msgs.append({"role": "assistant", "text": "b"})
    cls.update_content(o, msgs)
    assert len(pages) == 1 and len(o.js) == 1 and "appendMessages" in o.js[0], (len(pages), o.js)
    msgs[0] = {"role": "user", "text": "changed"}
    o._ready = True
    cls.update_content(o, msgs)
    assert len(pages) == 2, "un message réécrit recharge la page"
    print("✓ en talk, un message ajouté est injecté sans recharger la page")


if __name__ == "__main__":
    test_only_new_messages_animate()
    test_cleared_conversation_animates_again()
    test_css_has_a_static_state()
    test_talk_appends_without_reloading()
