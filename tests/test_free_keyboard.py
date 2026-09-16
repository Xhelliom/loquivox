"""
Self-check for TALK_FREE_KEYBOARD, run without a keyboard or a network:

    PYTHONPATH=src python tests/test_free_keyboard.py

With the keyboard left free, every key also reaches the app being typed into,
so the session may only answer to its own hotkeys: a space typed in a mail must
not end a turn, an Esc must not cancel the conversation.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.config as config_module  # noqa: E402
import loquivox.handlers.mode as mode_module  # noqa: E402
from evdev import ecodes  # noqa: E402
from loquivox.handlers.keyboard import GrabbedKeys, KeyboardHandler  # noqa: E402
from loquivox.state import STATE  # noqa: E402

TYPING = {ecodes.KEY_SPACE, ecodes.KEY_ENTER, ecodes.KEY_ESC, ecodes.KEY_S, ecodes.KEY_T}
base = config_module.CFG

try:
    config_module.CFG = replace(base, TALK_FREE_KEYBOARD=False)
    grabbed = KeyboardHandler.talk_listen_keys()
    assert ecodes.KEY_SPACE in grabbed and ecodes.KEY_ESC in grabbed, \
        "a grabbed session lost its own keys"

    config_module.CFG = replace(base, TALK_FREE_KEYBOARD=True)
    free = KeyboardHandler.talk_listen_keys()
    assert free and not set(free) & TYPING, f"typing still drives the session: {set(free) & TYPING}"
    assert set(free.values()) == {"finish"}, free
    STATE.current_mode = "ai"
    hints = KeyboardHandler.talk_hints()
    assert hints == (([base.HOTKEY_DEFS["ai"][0]], "end"),), hints
    print(f"✓ free keyboard: the session answers to its own keys only {sorted(free)}")

    keys = GrabbedKeys.__new__(GrabbedKeys)      # no devices opened
    keys._grabbable, keys._grabbed, grabs = [], False, []
    keys._grab = lambda: grabs.append(1)
    with keys.exclusive(grab=False):
        pass
    assert not grabs, "a free wait grabbed the keyboard"
    with keys.exclusive():
        pass
    assert grabs == [1], "a normal wait did not grab"
    print("✓ exclusive(grab=False) leaves the keyboard alone")

    hit = []
    real = mode_module.ModeHandler.cancel_active
    mode_module.ModeHandler.cancel_active = staticmethod(lambda: hit.append(1))
    try:
        STATE.talk_active = True
        KeyboardHandler._on_press("cancel")
        assert not hit, "an Esc typed elsewhere reached the session's capture"
        STATE.talk_active = False
        KeyboardHandler._on_press("cancel")
        assert hit == [1], "Esc no longer cancels outside a conversation"
    finally:
        STATE.talk_active = False
        mode_module.ModeHandler.cancel_active = real
    print("✓ the global Esc leaves a conversation alone, and only a conversation")

    # One table, three views: every action the keys bind has a button, and
    # "end turn" exists for the cascade alone — the server engines close turns
    # themselves and ignore it.
    config_module.CFG = replace(base, TALK_FREE_KEYBOARD=False)
    import loquivox.ui.chat_overlay as bubble

    for engine in ("cascade", "realtime", "live"):
        STATE.talk_engine = engine
        verdicts = {v for v, _c, _k, _w in KeyboardHandler.talk_actions()}
        assert ("send" in verdicts) == (engine == "cascade"), \
            f"{engine} offers a turn key it ignores: {verdicts}"
        mapped = set(KeyboardHandler.talk_listen_keys().values())
        assert verdicts <= mapped, (engine, verdicts - mapped)
        assert len(KeyboardHandler.talk_hints()) == len(verdicts), engine
    STATE.talk_engine = "cascade"
    print("✓ keys, hints and buttons all come from talk_actions()")

    # A clicked button is read exactly once, like a key press, and only the
    # session's own verdicts get through.
    for verdict in ("send", "finish", "cancel", "screen", "select"):
        mode_module.ModeHandler.talk_click(verdict)
        assert mode_module.ModeHandler._talk_clicked() == verdict, \
            f"the bar offers {verdict} and the loop never sees it"
    assert mode_module.ModeHandler._talk_clicked() is None, "a click was read twice"
    mode_module.ModeHandler.talk_click("rm -rf")
    assert mode_module.ModeHandler._talk_clicked() is None, "anything drives the session"
    print("✓ a click is read once, and only the session's own verdicts")

    # The bar is rendered, not templated: the page keeps one slot for it, and
    # every action it offers is wired to talkAction.
    bubble_self = bubble.ChatOverlay.__new__(bubble.ChatOverlay)
    bubble_self.talk = True
    STATE.chat_messages = []
    bar = bubble_self._talk_bar()
    assert "{talk_bar}" in bubble.CHAT_HTML_TEMPLATE, "the page has no slot for the bar"
    for verdict, _c, _k, _w in KeyboardHandler.talk_actions():
        assert f'talkAction("{verdict}")' in bar, f"no button for {verdict}"
    assert 'talkAction("free")' in bar and "{" not in bar, bar
    STATE.chat_messages = [{"role": "result", "content": "x"}]
    assert bubble_self._talk_bar() == "", "the review panel is offered dead buttons"
    STATE.chat_messages = []
    bubble_self.talk = False
    assert bubble_self._talk_bar() == "", "the side panel renders the talk bar"
    print("✓ the bar is one button per action, and only while the session listens")
finally:
    config_module.CFG = base
print("\n✓ free-keyboard checks pass")
