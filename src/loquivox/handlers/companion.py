"""
Companion mode: always present, listening locally, cloud only when spoken to.

``services/presence.py`` holds the microphone and the wake word, the bubble
(``ui/companion_bubble.py``) shows the state, and a conversation is the Live
engine run through the same loop F4 uses — minus everything that would put it
on screen: no transcript in the chat, no final generation, nothing poured into
``conversation_history``.

    wake word → bubble red + capture the active window (halo) → release the
    mic → Live session → silence → resume the mic, bubble back to standby

Two presence modes, ``STATE.presence_mode``: ``active`` (listening) and
``silent`` (mic closed, wake word ignored). The silence key, a click on the
bubble and its menu all go through ``set_mode``.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Callable, Optional

import loquivox.config as config_module
from loquivox.state import STATE

#: the bubble menu is this list
MODES = (("active", "Actif"), ("silent", "Silence"))


class _NoKeys:
    """Stands in for ``GrabbedKeys``: the companion never touches the keyboard."""

    @property
    def grabbed(self) -> bool:   # what _talk_wait compares the user's setting to
        from loquivox.handlers.keyboard import KeyboardHandler
        return not KeyboardHandler.free_keyboard()

    def poll(self, _mapping, timeout: float):
        time.sleep(timeout)
        return None

    def follow(self, grab: bool) -> None:
        pass

    @contextlib.contextmanager
    def exclusive(self, grab: bool = True):
        yield


def _look(session) -> None:
    from loquivox.handlers.mode import ModeHandler
    ModeHandler._talk_look(session)


def _converse(session, prefix=()) -> None:
    from loquivox.handlers.mode import ModeHandler
    from loquivox.services.live_talk import LiveTalk

    def engine(s):
        talk = LiveTalk(s)
        talk.prefix = list(prefix)
        return talk
    ModeHandler._talk_converse_server(session, _NoKeys(), engine, "Live")


class Companion:
    def __init__(self, presence, bubble, *, look: Callable = _look,
                 converse: Callable = _converse) -> None:
        self.presence, self.bubble = presence, bubble
        self._look, self._converse = look, converse

    # ------------------------------------------------------------------ modes
    def start(self) -> None:
        if self.presence_on():
            self.presence.start()
        self.bubble.set_state(self._standby())

    def presence_on(self) -> bool:
        return STATE.presence_mode == "active"

    def _standby(self) -> str:
        return "idle" if self.presence_on() else "silent"

    def set_mode(self, mode: str) -> None:
        """Switch presence mode. Mid-conversation it also ends the conversation."""
        if mode not in dict(MODES) or mode == STATE.presence_mode:
            return
        STATE.presence_mode = mode
        print(f"🫧 Companion: {mode}")
        if STATE.companion_session:
            print("🫧 Companion: faux déclenchement (touche)")
            STATE.talk_click.put("cancel")   # the session's own exit restores the rest
            return
        if not self.presence_on():
            self.presence.release()
        elif not STATE.talk_active:   # F4/F6 hold the mic; resume_after_talk reopens it
            self.presence.resume()
        self.bubble.set_state(self._standby())

    def toggle_silence(self) -> None:
        self.set_mode("active" if STATE.presence_mode == "silent" else "silent")

    def click(self) -> None:
        """One click: to the configured mode, and back if already there."""
        target = config_module.CFG.COMPANION_CLICK_MODE
        if STATE.companion_session:
            STATE.talk_click.put("finish")
        else:
            self.set_mode("active" if STATE.presence_mode == target else target)

    # ---------------------------------------------------------------- session
    def wake(self) -> None:
        """Wake word heard (on its own thread): run one conversation, block until over."""
        if (not self.presence_on() or STATE.talk_active or STATE.recording
                or STATE.companion_session):
            return
        from loquivox.services.talk import TalkSession
        STATE.talk_active = STATE.companion_session = True
        session = TalkSession(write=False)
        STATE.talk_session = session
        previous_mode = STATE.current_mode
        STATE.current_mode, STATE.talk_engine = "ai", "live"
        STATE.echo_advised = False
        self.bubble.set_state("cloud")     # red from the capture on: the cost is visible
        try:
            self._look(session)            # before the mic is released and the cloud opens
            self.presence.release()
            print("🫧 Companion: session open")
            self._converse(session, self.presence.take_tail())
        except Exception as e:
            print(f"⚠️  Companion session failed: {e}")
        finally:
            STATE.vad, STATE.talk_engine, STATE.talk_session = None, "cascade", None
            STATE.current_mode = previous_mode
            session.desks.close()
            STATE.talk_active = STATE.companion_session = False
            while not STATE.talk_click.empty():     # a click meant for this session
                STATE.talk_click.get_nowait()
            print("🫧 Companion: session closed")
            if self.presence_on():
                self.presence.resume()
            self.bubble.set_state(self._standby())

    def on_wake(self) -> None:   # Presence callback
        self.wake()


_companion: Optional[Companion] = None


def install(presence, bubble) -> Companion:
    global _companion
    _companion = Companion(presence, bubble)
    return _companion


def toggle_silence() -> None:
    if _companion is not None:
        _companion.toggle_silence()


def pause_for_talk() -> None:
    """F4/F6 open their own microphone: stand the listener down meanwhile."""
    if _companion is not None and _companion.presence_on():
        _companion.presence.release()


def resume_after_talk() -> None:
    if _companion is not None and _companion.presence_on() and not STATE.companion_session:
        _companion.presence.resume()
