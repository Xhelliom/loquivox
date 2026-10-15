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
from loquivox.services import events, media, plugins
from loquivox.state import STATE, SettingsManager

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


SOURCE_POLL = 0.5     # s between two looks at the sources' queue
SOURCE_RETRY = 30.0   # s before a message a session left unsaid may wake it again
DUCK_ROOM_MARGIN = 2.0   # speech must be this many times louder than the room (music included)
DUCK_CALIBRATION = 6     # mic blocks (~0.6 s) that only measure the room at session start
DUCK_CONFIRM = 3         # consecutive loud blocks that make it speech, not a click
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
        self._away: set = set()   # why the companion is silent without being asked: "lock", "idle"
        self._before = "active"   # the user's mode, to return to when the last reason is gone
        self._emitted = None  # last state published as an event
        self._voice = None   # "listening" / "speaking" last posted to the bubble
        self._heard = float("-inf")   # monotonic time of the last word heard in standby
        self._retry = float("-inf")   # no source wake before this
        self._ducking = False         # the music is down because someone speaks
        self._user_run = 0            # consecutive mic blocks above the speech bar
        self._user_last = float("-inf")   # monotonic time of the last confirmed user speech
        self._room = 0.0              # loudest mic level heard WITHOUT speech, music included
        self._room_blocks = 0
        presence.on_level = self._mic_level

    def _set_state(self, state: str) -> None:
        """The bubble's state, and the event saying so (once per change)."""
        self.bubble.set_state(state)
        if state != self._emitted:
            self._emitted = state
            events.emit("companion.state", state=state)

    # ------------------------------------------------------------------ modes
    def start_modes(self) -> None:
        """Restore the mode the user left (F7 / click), written by ``set_mode``."""
        saved = SettingsManager.load().get("presence_mode")
        if saved in dict(MODES):
            STATE.presence_mode = saved
            from loquivox.ui.tray import TrayManager  # lazy: GTK
            TrayManager.refresh_icon()  # the tray may already be up

    def start(self) -> None:
        self.start_modes()
        if self.presence_on():
            self.presence.start()
        self._set_state(self._standby())
        plugins.start_sources()
        self._watch_away()
        threading.Thread(target=self._announce_loop, daemon=True).start()

    def presence_on(self) -> bool:
        return STATE.presence_mode == "active"

    def _standby(self) -> str:
        return "idle" if self.presence_on() else "silent"

    def _watch_away(self) -> None:
        from loquivox.platform import get_away
        away = get_away()
        away.watch_lock(self._on_lock)
        if config_module.CFG.COMPANION_SILENCE_IDLE_MIN > 0:
            away.watch_idle(config_module.CFG.COMPANION_SILENCE_IDLE_MIN * 60, self._on_idle)

    def _on_lock(self, locked: bool) -> None:
        # CFG is read per event (Settings rebinds it). An Unlock always goes through:
        # the box unticked during a lock must not leave the silence stuck until F7.
        if not locked or config_module.CFG.COMPANION_SILENCE_ON_LOCK:
            self.set_away("lock", locked)

    def _on_idle(self, idle: bool) -> None:
        # Talking touches neither keyboard nor mouse: idleness must never cut a
        # conversation. Only the lock closes a session.
        # ponytail: dropped, not deferred — the edge does not repeat, so a user who
        # stays idle after the conversation is not silenced until the next activity cycle.
        if idle and (STATE.companion_session or STATE.talk_active):
            return
        self.set_away("idle", idle)

    def set_away(self, reason: str, on: bool) -> None:
        """Silence for a reason that is not a choice: nothing is persisted, and the
        mode from before comes back once every reason is gone. A mode the user picks
        meanwhile wins (``set_mode`` forgets the reasons)."""
        if on and not self._away:
            self._before = STATE.presence_mode
        was = bool(self._away)
        (self._away.add if on else self._away.discard)(reason)
        if on and not was:
            print(f"🫧 Companion: silence ({reason})")
            if STATE.presence_mode == "silent" and STATE.companion_session:
                self._cut_session()   # F8 session in silence: set_mode would exit early
            self.set_mode("silent", persist=False)
        elif was and not self._away:
            print(f"🫧 Companion: back to {self._before}")
            self.set_mode(self._before, persist=False)

    def set_mode(self, mode: str, persist: bool = True) -> None:
        """Switch presence mode. Mid-conversation it also ends the conversation."""
        try:
            self._set_mode(mode, persist)
        finally:   # the tray's ticked "silence" follows, whoever asked (F7, click, lock)
            from loquivox.ui.tray import TrayManager  # lazy: GTK
            TrayManager.update_menu()

    def _set_mode(self, mode: str, persist: bool) -> None:
        if persist:
            self._away.clear()
        if mode not in dict(MODES) or mode == STATE.presence_mode:
            if persist and mode in dict(MODES):
                SettingsManager.update("presence_mode", mode)
            return
        STATE.presence_mode = mode
        if persist:
            SettingsManager.update("presence_mode", mode)
        print(f"🫧 Companion: {mode}")
        if STATE.companion_session:
            self._cut_session()
            return
        if not self.presence_on():
            self.presence.release()
        elif not STATE.talk_active:   # F4/F6 hold the mic; resume_after_talk reopens it
            self.presence.resume()
        self._set_state(self._standby())

    def _cut_session(self) -> None:
        from loquivox.services.talk import log_companion_cut
        session = STATE.talk_session
        log_companion_cut("touche", session.user_turns if session else 0)
        STATE.talk_click.put("cancel")   # the session's own exit restores the rest

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
        if level > 0:
            self._heard = time.monotonic()
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
        self._duck(who, level, now)
        voice = "speaking" if speaking else "listening"
        if voice != self._voice:
            self._voice = voice
            self._set_state(voice)

    def _user_speaking(self, level: float, now: float) -> bool:
        """
        Real speech from the user, never the music coming back into the mic.
        The bar is the speech threshold or ``DUCK_ROOM_MARGIN`` times the room,
        whichever is higher, and the room is only learned while the music is up
        and nothing speaks: ducking lowers the music, so a bar that followed the
        mic down would be crossed by the music coming back — the pumping.
        The first ``DUCK_CALIBRATION`` blocks only measure.
        """
        bar = max(config_module.CFG.TALK_VAD_THRESHOLD * 6, self._room * DUCK_ROOM_MARGIN)
        if self._room_blocks < DUCK_CALIBRATION:
            self._room_blocks += 1
            self._room = max(self._room, level)
            return False
        if level >= bar:
            self._user_run += 1
            if self._user_run >= DUCK_CONFIRM:
                self._user_last = now
        else:
            self._user_run = 0
            if not self._ducking:
                self._room = max(level, self._room * 0.99)
        return now - self._user_last < config_module.CFG.COMPANION_DUCK_HOLD_SEC

    def _duck(self, who: str, level: float, now: float) -> None:
        """Music down while the companion speaks and/or the user does, up ``duck_hold`` after."""
        cfg = config_module.CFG
        assistant = cfg.COMPANION_DUCK_ON_ASSISTANT and now - self._loud < cfg.COMPANION_DUCK_HOLD_SEC
        user = False
        if who == "in":   # the assistant's own echo is not the user
            if now - self._loud < SPEAK_HOLD:
                self._user_run = 0
            else:
                user = self._user_speaking(level, now)
        else:
            user = now - self._user_last < cfg.COMPANION_DUCK_HOLD_SEC
        want = assistant or (cfg.COMPANION_DUCK_ON_USER and user)
        if want != self._ducking:
            self._ducking = want
            media.duck_voice(want)

    def wake(self, force: bool = False, cause: str = "wake_word") -> None:
        """Wake word heard (on its own thread): run one conversation, block until over.
        ``force`` (the wake key) opens it in silence mode too. ``cause`` says why
        it opened (wake_word, key, notification), for the events."""
        if (self._away or not (force or self.presence_on()) or STATE.talk_active or STATE.recording
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
        self._ducking, self._user_run, self._user_last = False, 0, float("-inf")
        self._room, self._room_blocks = 0.0, 0
        self.bubble.pulse()                # heard: "I'm listening"
        self._set_state("cloud")     # red from the capture on: the cost is visible
        opened = False
        try:
            if cause != "notification":    # a source woke us: the screen has nothing to do with it
                self._look(session)        # before the mic is released and the cloud opens
            self.presence.release()
            print("🫧 Companion: session open")
            events.emit("companion.session_opened", cause=cause)
            opened = True
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
            if opened:     # a restore rule must not replay a snapshot that was never taken
                events.emit("companion.session_closed", cause=cause)
            if self.presence_on():
                self.presence.resume()
            self._set_state(self._standby())

    def announce_if_due(self) -> bool:
        """A standing source has something and nobody is speaking: open a session
        for it (the sweep between two turns then says it). Blocks until over.
        Never in silence mode, never over a recording or a talk session."""
        now = time.monotonic()
        if (not self.presence_on() or STATE.talk_active or STATE.recording
                or STATE.companion_session or now < self._retry
                or now - self._heard < config_module.CFG.COMPANION_SOURCE_SILENCE_SEC
                or not plugins.sources_waiting()):
            return False
        self.wake(cause="notification")
        if plugins.sources_waiting():   # the session ended without saying it: not in a loop
            self._retry = time.monotonic() + SOURCE_RETRY
        return True

    def _announce_loop(self) -> None:
        while True:
            try:
                self.announce_if_due()
            except Exception as e:
                print(f"⚠️  Companion could not announce a source: {e}")
            time.sleep(SOURCE_POLL)

    def wake_key(self) -> None:
        """The wake key: open a session, or end the one that is open."""
        if STATE.companion_session:
            STATE.talk_click.put("finish")
        else:
            threading.Thread(target=self.wake, kwargs={"force": True, "cause": "key"}, daemon=True).start()

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


def restyle() -> None:
    """Settings changed the bubble's style: repaint it now (a silent bubble has no timer)."""
    if _companion is not None:
        _companion.bubble._kick()


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
