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


SPEAK_HOLD = 0.8   # s; same order as live_talk.AUDIO_IDLE (1.0), shorter so listening comes back fast


def _converse(session, prefix=()) -> None:
    from loquivox.handlers.mode import ModeHandler
    from loquivox.services.live_talk import LiveTalk

    def engine(s):
        talk = LiveTalk(s)
        talk.prefix = list(prefix)
        if _companion is not None:
            talk.on_level = _companion.on_level
        return talk
    ModeHandler._talk_converse_server(session, _NoKeys(), engine, "Live")


class Companion:
    def __init__(self, presence, bubble, *, look: Callable = _look,
                 converse: Callable = _converse) -> None:
        self.presence, self.bubble = presence, bubble
        self._look, self._converse = look, converse
        self._out = 0.0      # last output level: the companion has the floor above this
        self._loud = float("-inf")   # monotonic time of the last audible output block
        self._voice = None   # "listening" / "speaking" last posted to the bubble
        presence.on_level = self._mic_level

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
            from loquivox.services.talk import log_companion_cut
            session = STATE.talk_session
            log_companion_cut("touche", session.user_turns if session else 0)
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
    def _mic_level(self, level: float) -> None:
        """Standby microphone → the water. A session owns the bubble's level (and
        the mic is released then); the blocks still in flight after the wake are dropped."""
        if not STATE.companion_session:
            self.bubble.set_level(level)

    def on_level(self, who: str, level: float) -> None:
        """Audio thread: feed the bubble's water, and say who has the floor.
        The companion keeps it for SPEAK_HOLD after its last audible block, so the
        gaps between two words do not flip the waves' direction.
        Blocks still in flight after the session closed are dropped: they would
        put "listening" back on a bubble already at rest."""
        if not STATE.companion_session:
            return
        now = time.monotonic()
        if who == "out":
            self._out = level
            if level >= 0.02:
                self._loud = now
        speaking = now - self._loud < SPEAK_HOLD
        self.bubble.set_level(self._out if speaking and who != "out" else level)
        voice = "speaking" if speaking else "listening"
        if voice != self._voice:
            self._voice = voice
            self.bubble.set_state(voice)

    def wake(self, force: bool = False) -> None:
        """Wake word heard (on its own thread): run one conversation, block until over.
        ``force`` (the wake key) opens it in silence mode too."""
        if (not (force or self.presence_on()) or STATE.talk_active or STATE.recording
                or STATE.companion_session):
            return
        from loquivox.services.talk import TalkSession
        STATE.talk_active = STATE.companion_session = True
        session = TalkSession(write=False)
        STATE.talk_session = session
        previous_mode = STATE.current_mode
        STATE.current_mode, STATE.talk_engine = "ai", "live"
        STATE.echo_advised = False
        self._out, self._loud, self._voice = 0.0, float("-inf"), None
        self.bubble.pulse()                # heard: "I'm listening"
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
            self.bubble.set_level(0.0)
            print("🫧 Companion: session closed")
            if self.presence_on():
                self.presence.resume()
            self.bubble.set_state(self._standby())

    def wake_key(self) -> None:
        """The wake key: open a session, or end the one that is open."""
        if STATE.companion_session:
            STATE.talk_click.put("finish")
        else:
            threading.Thread(target=self.wake, kwargs={"force": True}, daemon=True).start()

    def on_wake(self) -> None:   # Presence callback
        self.wake()


_companion: Optional[Companion] = None


def install(presence, bubble) -> Companion:
    global _companion
    _companion = Companion(presence, bubble)
    return _companion


def set_wake_word(name: str) -> None:
    """Settings changed the wake word: reload it in the running listener."""
    if _companion is not None:
        _companion.presence.set_wake_model(name)
        if _companion.presence.wake_error:  # the caller must not report a success
            raise RuntimeError(_companion.presence.wake_error)


def toggle_silence() -> None:
    if _companion is not None:
        _companion.toggle_silence()


def wake_key() -> None:
    if _companion is not None:
        _companion.wake_key()


def pause_for_talk() -> None:
    """F4/F6 open their own microphone: stand the listener down meanwhile."""
    if _companion is not None and _companion.presence_on():
        _companion.presence.release()


def resume_after_talk() -> None:
    if _companion is not None and _companion.presence_on() and not STATE.companion_session:
        _companion.presence.resume()
