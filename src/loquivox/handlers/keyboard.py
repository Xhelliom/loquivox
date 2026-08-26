"""
Global keyboard listener using evdev.

Reads key events directly from /dev/input/ devices, which works on both
X11 and Wayland without any display server integration. Requires the
user to be in the 'input' group.
"""
from __future__ import annotations

import logging
import selectors
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Set, Tuple

import evdev
from evdev import InputDevice, ecodes

from loquivox.config import (
    CFG, MODIFIER_CODES, POSTPROCESS_MAX_LEVEL, modifier_name, resolve_hotkeys,
)
from loquivox.handlers.mode import ModeHandler
from loquivox.managers.chat import ChatManager
from loquivox.managers.overlay import OverlayManager
from loquivox.services.audio import AudioService
from loquivox.services.clipboard import ClipboardService
from loquivox.services.tts import TTSService
from loquivox.state import STATE

logger = logging.getLogger(__name__)

#: "yes" wherever a modal waits for one — Enter, and the keypad's when it exists.
_CONFIRM_CODES: Tuple[int, ...] = (
    ecodes.KEY_ENTER, getattr(ecodes, "KEY_KPENTER", ecodes.KEY_ENTER),
)


class GrabbedKeys:
    """
    Keyboard access for a modal flow that spans several waits (talk mode).

    ``capture_review`` & co open the keyboards, grab them, wait for ONE decision
    and close everything. A talk session instead runs for minutes across many
    waits — every spoken turn, then the review of the generated text — so it
    keeps the devices OPEN for its whole life: a key pressed while the model is
    thinking stays queued on our fd and is still there when the next wait
    starts, nothing is lost between turns.

    The exclusive grab, though, is taken only around the waits themselves
    (``with keys.exclusive():``) and dropped for the blocking work in between.
    Holding it across a network call would mean a hung request leaves the user
    with a frozen keyboard — never worth it. Keys pressed during those short
    gaps reach the focused app as well as us; keys pressed while we wait do not.

    Use it as a context manager. It MUST be closed, and MUST run off the GTK
    main thread. Pointers are read but never grabbed (grabbing one freezes the
    cursor), exactly like the one-shot captures.
    """

    def __init__(self) -> None:
        self._devices = KeyboardHandler._find_keyboards()
        self._selector = selectors.DefaultSelector()
        self._grabbed = False
        # Classified once: a device cannot grow a mouse while we hold its fd,
        # and capabilities() is an ioctl we would otherwise repeat per wait.
        self._grabbable: List[InputDevice] = []
        for dev in self._devices:
            try:
                self._selector.register(dev, selectors.EVENT_READ)
            except Exception:
                continue
            if not KeyboardHandler._is_pointer(dev):
                self._grabbable.append(dev)

    def __enter__(self) -> "GrabbedKeys":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def alive(self) -> bool:
        """False when no keyboard could be opened — there is nothing to wait on."""
        return bool(self._devices)

    @contextmanager
    def exclusive(self):
        """
        Hold the exclusive grab for the duration of the block — wrap every wait
        on the user, and nothing else.
        """
        self._grab()
        try:
            yield self
        finally:
            self._ungrab()

    def _grab(self) -> None:
        for dev in self._grabbable:
            try:
                dev.grab()
            except Exception:
                pass  # grab is best-effort; the keys still get read
        self._grabbed = True

    def _ungrab(self) -> None:
        if not self._grabbed:
            return
        for dev in self._grabbable:
            try:
                dev.ungrab()
            except Exception:
                pass
        self._grabbed = False
        # The key-ups that happened while we held the grab never reached the
        # listener, so its view of what is held is stale — drop it.
        KeyboardHandler._held.clear()
        KeyboardHandler._active.clear()

    def poll(self, mapping: Dict[int, Any], timeout: float) -> Optional[Any]:
        """
        Wait up to ``timeout`` seconds for one of ``mapping``'s keys (keycode →
        whatever the caller wants back) and return its value, or None if none
        came. Key-ups and autorepeats are ignored, so a held key fires exactly
        once. Values are opaque: a verdict string for the review panel, a
        ``("set", level)`` tuple for the refinement chooser.
        """
        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for key_obj, _ in self._selector.select(timeout=remaining):
                try:
                    events = list(key_obj.fileobj.read())
                except Exception:
                    continue
                for event in events:
                    if event.type != ecodes.EV_KEY or event.value != 1:
                        continue
                    action = mapping.get(event.code)
                    if action is not None:
                        return action

    def close(self) -> None:
        """Ungrab and close every device (safe to call more than once)."""
        self._ungrab()
        for dev in self._devices:
            try:
                self._selector.unregister(dev)
            except Exception:
                pass
            try:
                dev.close()
            except Exception:
                pass
        self._devices = []
        self._grabbable = []
        try:
            self._selector.close()
        except Exception:
            pass


class KeyboardHandler:
    """Global keyboard listener using evdev (works on X11 + Wayland)."""

    # Resolved bindings keyed by TRIGGER keycode → list of (mode, modifier
    # groups). Rebuilt by reload_hotkeys() so settings edits apply live. The
    # listener tracks held keys (_held) so combos like ALT+SPACE match; _active
    # remembers which mode a trigger fired so its key-up releases the right one.
    _BY_TRIGGER: Dict[int, List[Tuple[str, Tuple[frozenset, ...]]]] = {}
    _held: set = set()
    _active: Dict[int, str] = {}

    @classmethod
    def reload_hotkeys(cls, config: Any = None) -> None:
        """
        Rebuild the trigger→bindings map from ``config`` (or the live ``CFG``).

        Called once at import time and again by the settings UI after the user
        edits hotkeys, so new bindings take effect immediately without a
        restart. Bindings sharing a trigger are sorted most-specific-first (most
        modifiers), so a combo (ALT+SPACE) wins over the bare key (SPACE) when
        its modifiers are held. A brand-new dict is assigned (never mutated in
        place) so the listener thread always reads a consistent map.
        """
        cfg = config if config is not None else CFG
        by_trigger: Dict[int, List[Tuple[str, Tuple[frozenset, ...]]]] = {}
        for mode_id, bindings in resolve_hotkeys(cfg).items():
            for trigger, mods in bindings:
                by_trigger.setdefault(trigger, []).append((mode_id, mods))
        for entries in by_trigger.values():
            entries.sort(key=lambda b: len(b[1]), reverse=True)
        cls._BY_TRIGGER = by_trigger

    @staticmethod
    def _is_keyboard(dev: InputDevice) -> bool:
        """Heuristic: a device with EV_KEY exposing typical keyboard keys."""
        try:
            caps = dev.capabilities()
        except Exception:
            return False
        if ecodes.EV_KEY not in caps:
            return False
        key_caps = caps[ecodes.EV_KEY]
        # Require some function/letter keys to filter out mice, lid switches, etc.
        return ecodes.KEY_F1 in key_caps or ecodes.KEY_A in key_caps

    @staticmethod
    def _is_pointer(dev: InputDevice) -> bool:
        """
        True if the device also drives a pointer (mouse / touchpad).

        Some keyboards-with-extra-keys are really mice (e.g. a Logitech G900
        exposes keyboard-mapped G-keys): they pass ``_is_keyboard`` but carry the
        cursor, so ``grab()``-ing them freezes the mouse. We read them (their keys
        still work) but must never grab them.
        """
        try:
            caps = dev.capabilities()
        except Exception:
            return False
        if ecodes.EV_REL in caps or ecodes.EV_ABS in caps:
            return True
        keys = caps.get(ecodes.EV_KEY, [])
        return any(b in keys for b in (ecodes.BTN_LEFT, ecodes.BTN_MOUSE, ecodes.BTN_TOUCH))

    @classmethod
    def _find_keyboards(cls) -> List[InputDevice]:
        """Discover all keyboard input devices (opens a fresh handle each)."""
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = InputDevice(path)
            except Exception:
                continue
            if cls._is_keyboard(dev):
                keyboards.append(dev)
                logger.debug("Found keyboard: %s (%s)", dev.name, dev.path)
            else:
                try:
                    dev.close()
                except Exception:
                    pass

        if not keyboards:
            logger.warning(
                "No keyboard devices found! "
                "Make sure you are in the 'input' group: "
                "sudo usermod -aG input $USER"
            )
        return keyboards

    @staticmethod
    def keycode_to_name(code: int) -> Optional[str]:
        """Reverse an evdev keycode to a clean key name (e.g. 'HOME'), or None."""
        name = ecodes.KEY.get(code)
        if isinstance(name, (list, tuple)):
            name = name[0]
        return name.replace("KEY_", "") if name else None

    @classmethod
    def capture_next_key(cls, timeout: float = 6.0) -> Optional[str]:
        """
        Block until the next key (or chord) is pressed and return its spec, or
        None on timeout / no device. Returns a combo like ``"ALT+SPACE"`` when
        modifiers are held, a lone modifier name (``"RIGHTALT"``) if a modifier
        is tapped on its own, or a plain key name (``"F3"``) otherwise.

        Used by the settings UI's "capture" button. Devices are grabbed for the
        (short) duration so the keypress doesn't leak to the focused app or
        trigger an existing hotkey. Meant to run in a background thread; always
        ungrabs.
        """
        import time

        devices = cls._find_keyboards()
        if not devices:
            return None

        sel = selectors.DefaultSelector()
        grabbed: List[InputDevice] = []
        for dev in devices:
            try:
                sel.register(dev, selectors.EVENT_READ)
            except Exception:
                continue
            try:
                dev.grab()
                grabbed.append(dev)
            except Exception:
                pass  # grab is best-effort; capture still works passively

        held: List[int] = []  # modifier keycodes currently down, in press order
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                for key_obj, _ in sel.select(timeout=remaining):
                    try:
                        events = list(key_obj.fileobj.read())
                    except Exception:
                        continue
                    for event in events:
                        if event.type != ecodes.EV_KEY:
                            continue
                        code = event.code
                        if event.value == 1:  # key down
                            if code in MODIFIER_CODES:
                                if code not in held:
                                    held.append(code)  # part of a combo
                            else:
                                trigger = cls.keycode_to_name(code)
                                if trigger:
                                    mods = [modifier_name(c) for c in held]
                                    return "+".join([*mods, trigger])
                        elif event.value == 0 and code in held:  # modifier up
                            held.remove(code)
                            if not held:  # a lone modifier tapped → bind it as-is
                                name = cls.keycode_to_name(code)
                                if name:
                                    return name
        finally:
            for dev in grabbed:
                try:
                    dev.ungrab()
                except Exception:
                    pass
            for dev in devices:
                try:
                    sel.unregister(dev)
                except Exception:
                    pass
                try:
                    dev.close()
                except Exception:
                    pass

    @classmethod
    def _match_binding(cls, trigger: int) -> Optional[str]:
        """Mode whose chord (this trigger + currently-held modifiers) matches."""
        for mode, mods in cls._BY_TRIGGER.get(trigger, ()):  # most-specific first
            if all(group & cls._held for group in mods):
                return mode
        return None

    @classmethod
    def _is_recording_mode(cls, mode: str) -> bool:
        """
        True for the modes whose key records while it is held.

        NOT ``mode in CFG.MODES``: that table is the overlay's appearance, and
        'talk' has an entry there for its look while owning the microphone
        through its own session rather than through the hold-key.
        """
        return mode in CFG.RECORDING_MODES

    @classmethod
    def trigger_codes(cls, action: str) -> Set[int]:
        """
        Keycodes currently bound to ``action``, from the live binding map — the
        same source the listener matches on, so a rebind is picked up for free.
        """
        return {code for code, entries in cls._BY_TRIGGER.items()
                if any(bound == action for bound, _mods in entries)}

    @classmethod
    def _handle_key_event(cls, event: evdev.InputEvent) -> None:
        """Track held keys and fire press/release on the matching chord."""
        code = event.code
        if event.value == 1:        # key down
            cls._held.add(code)
            mode = cls._match_binding(code)
            if mode is not None:
                cls._active[code] = mode
                cls._on_press(mode)
        elif event.value == 0:      # key up
            cls._held.discard(code)
            mode = cls._active.pop(code, None)
            if mode is not None:
                cls._on_release(mode)
        # event.value == 2 (autorepeat): ignore

    # Hotkeys that act on the session itself rather than starting a recording.
    @classmethod
    def _on_press(cls, mode: str) -> None:
        """Handle key press for a recognized mode."""
        # Cancel the active recording / in-flight transcription (no insert).
        if mode == "cancel":
            ModeHandler.cancel_active()
            return

        # Pause / resume the current recording (only while one is active).
        if mode == "pause":
            if STATE.recording:
                cls._toggle_pause()
            return

        # Stop the active recording, then pick a refinement level for it.
        if mode == "refine":
            if STATE.recording:
                cls._stop_and_choose()
            return

        # Talk mode: a whole spoken conversation, driven by its own worker,
        # which owns the precondition (it owns the flag).
        if mode == "talk":
            ModeHandler.start_talk_session()
            return

        # Pin toggle (non-recording action)
        if mode == "pin":
            if not STATE.recording:
                ChatManager.toggle_pin()
            return

        # TTS toggle (non-recording action)
        if mode == "tts":
            if not STATE.recording:
                TTSService.toggle()
            return

        # Toggle mode: pressing same key again stops recording
        if STATE.recording and STATE.toggle_mode:
            if mode == STATE.current_mode:
                cls._stop_and_process()
            return

        if STATE.recording:
            return

        # A talk session owns the microphone for its whole life, including the
        # gaps between turns where the keyboard is not grabbed.
        if STATE.talk_active:
            return

        # Start recording for this mode
        if cls._is_recording_mode(mode):
            STATE.current_mode = mode

            # For rewrite mode, copy selected text first
            if mode == "ai_rewrite":
                ClipboardService.copy_selected()

            OverlayManager.show(mode)
            AudioService.start_recording()

    @classmethod
    def _toggle_pause(cls) -> None:
        """Flip the paused state of the active recording and reflect it on the overlay."""
        STATE.paused = not STATE.paused
        OverlayManager.set_paused(STATE.paused)
        print("⏸️  Paused" if STATE.paused else "▶️  Resumed")

    @classmethod
    def _on_release(cls, mode: str) -> None:
        """Handle key release for a recognized mode."""
        # Session-action keys only act on press; their release is a no-op
        # (and must not stop a recording that is still in progress, e.g. paused).
        if not cls._is_recording_mode(mode):
            return

        if not STATE.recording:
            return

        # In toggle mode, release does nothing
        if STATE.toggle_mode:
            return

        # Hold mode: release key stops recording
        if mode == STATE.current_mode:
            cls._stop_and_process()

    @classmethod
    def _stop_and_process(cls) -> None:
        """
        Stop recording and hand transcription off-thread.

        Runs on the keyboard listener thread, so it must not block on the
        network or touch GTK directly: the overlay hide is already marshalled
        to the main loop, stop_recording() is not a GTK call, and
        transcription + processing run in a worker thread.
        """
        OverlayManager.set_transcribing()
        audio_data = AudioService.stop_recording()

        # Route the live session (if any) or the buffered audio, like the
        # silence-stop path in ModeHandler.stop_recording_safe.
        session = STATE.stream_session
        STATE.stream_session = None

        if session is not None:
            ModeHandler.process_stream_async(STATE.current_mode, session, audio_data)
        elif audio_data is not None:
            ModeHandler.process_audio_async(STATE.current_mode, audio_data)
        else:
            OverlayManager.hide()

    # --- On-the-fly refinement chooser (bound to the 'refine' hotkey) --------

    @classmethod
    def _stop_and_choose(cls) -> None:
        """
        Stop the recording, then let the user pick a refinement level for THIS
        dictation (overriding the configured default) before processing.
        Runs the (blocking, grabbed) chooser in a background thread.
        """
        import threading

        audio_data = AudioService.stop_recording()
        session = STATE.stream_session
        STATE.stream_session = None
        mode = STATE.current_mode
        generation = STATE.recording_generation
        if session is None and audio_data is None:
            OverlayManager.hide()
            return
        threading.Thread(
            target=cls._refine_choose_worker,
            args=(mode, session, audio_data, generation),
            daemon=True,
        ).start()

    @classmethod
    def _refine_choose_worker(cls, mode, session, audio_data, generation) -> None:
        """Show the chooser, then route processing with the chosen level."""
        default = int(CFG.POSTPROCESS_LEVEL or 0)
        level = cls.capture_refinement(default)
        if level is None:  # cancelled
            OverlayManager.hide(generation)
            print("✖️  Refinement choice cancelled — nothing inserted")
            return
        OverlayManager.set_transcribing()
        if session is not None:
            ModeHandler.process_stream_async(mode, session, audio_data, level_override=level)
        elif audio_data is not None:
            ModeHandler.process_audio_async(mode, audio_data, level_override=level)
        else:
            OverlayManager.hide(generation)

    @classmethod
    def capture_refinement(cls, default_level: int, timeout: float = 12.0) -> Optional[int]:
        """
        Grab the keyboard and let the user choose a refinement level (0..MAX),
        shown live on the overlay. Returns the chosen level, or None if Esc is
        pressed. Selection: digits 0-N, ←/→ to step, the 'refine' key again to
        cycle, Enter to confirm; auto-confirms on timeout.
        """
        level = max(0, min(POSTPROCESS_MAX_LEVEL, int(default_level)))
        mapping: Dict[int, Any] = {ecodes.KEY_ESC: "cancel"}
        mapping.update({code: "confirm" for code in _CONFIRM_CODES})
        for n in range(POSTPROCESS_MAX_LEVEL + 1):
            mapping[getattr(ecodes, f"KEY_{n}")] = ("set", n)
            keypad = getattr(ecodes, f"KEY_KP{n}", None)
            if keypad is not None:
                mapping[keypad] = ("set", n)
        mapping.update({ecodes.KEY_UP: ("step", -1), ecodes.KEY_LEFT: ("step", -1),
                        ecodes.KEY_DOWN: ("step", 1), ecodes.KEY_RIGHT: ("step", 1)})
        for code in cls.trigger_codes("refine"):  # re-pressing the hotkey cycles
            mapping.setdefault(code, "cycle")

        with GrabbedKeys() as keys:
            if not keys.alive:
                return level  # no input device → just use the default
            with keys.exclusive():
                OverlayManager.set_choosing(level)
                while True:
                    # Every poll restarts the timeout, so activity keeps the
                    # chooser alive; None means it ran out → auto-confirm.
                    action = keys.poll(mapping, timeout)
                    if action is None or action == "confirm":
                        return level
                    if action == "cancel":
                        return None
                    if action == "cycle":
                        level = (level + 1) % (POSTPROCESS_MAX_LEVEL + 1)
                    elif action[0] == "set":
                        level = action[1]
                    else:  # ("step", ±1)
                        level = max(0, min(POSTPROCESS_MAX_LEVEL, level + action[1]))
                    OverlayManager.set_choosing(level)

    # --- Talk mode key maps --------------------------------------------------

    @classmethod
    def talk_listen_keys(cls) -> Dict[int, str]:
        """
        Keycode → action while a talk turn is being recorded.

        Space (or the talk key itself) ends the turn now instead of waiting for
        the VAD to hear the pause; Enter ends the conversation and writes the
        text; Esc drops the whole thing.
        """
        import loquivox.config as config_module

        mapping: Dict[int, str] = {ecodes.KEY_SPACE: "send", ecodes.KEY_ESC: "cancel"}
        mapping.update({code: "finish" for code in _CONFIRM_CODES})
        for code in cls.trigger_codes("talk"):
            mapping.setdefault(code, "send")
        if config_module.CFG.TALK_SCREENSHOT:
            # Only when the screen is already part of the deal: S sends what is
            # on screen to the cloud, and a key that does that must not exist
            # for someone who turned the capture off.
            mapping[ecodes.KEY_S] = "screen"
        return mapping

    @classmethod
    def talk_hints(cls) -> Tuple[Tuple[List[str], str], ...]:
        """
        What the overlay shows while a talk turn is recording — built from the
        same condition as ``talk_listen_keys`` so the strip cannot offer a key
        that does nothing.
        """
        import loquivox.config as config_module

        hints: List[Tuple[List[str], str]] = [
            (["Space"], "end turn"), (["Enter"], "write it"), (["Esc"], "cancel"),
        ]
        if config_module.CFG.TALK_SCREENSHOT:
            hints.append((["S"], "look again"))
        return tuple(hints)

    @classmethod
    def talk_review_keys(cls) -> Dict[int, str]:
        """The review verdicts, with V dropping back into the conversation."""
        return {**cls._REVIEW_KEYS, ecodes.KEY_V: "talk"}

    # --- AI action panel review (rewrite/vision) -----------------------------

    #: keycode → verdict in the AI review panel. One table, so the panel, its
    #: talk variant and the hint line can never drift apart.
    _REVIEW_KEYS: Dict[int, str] = {
        ecodes.KEY_ESC: "reject",
        ecodes.KEY_R: "redo",
        ecodes.KEY_V: "redict",
        ecodes.KEY_C: "copy",
        **{code: "accept" for code in _CONFIRM_CODES},
    }

    @classmethod
    def _review_action(cls, code: int) -> Optional[str]:
        """Map an evdev keycode to a review action (pure → testable without a device)."""
        return cls._REVIEW_KEYS.get(code)

    @classmethod
    def capture_review(cls, mode: str, timeout: float = 90.0) -> str:
        """
        Grab the keyboard and wait for the user's decision on the AI result shown
        in the review panel. Returns "accept" / "reject" / "redo" / "redict" /
        "copy".

        Times out to "reject" — a review must NEVER auto-insert unreviewed text.
        MUST run on a worker thread: it blocks.
        """
        with GrabbedKeys() as keys:
            if not keys.alive:
                return "reject"
            with keys.exclusive():
                return keys.poll(cls._REVIEW_KEYS, timeout) or "reject"

    @classmethod
    def record_instruction(cls, mode: str, timeout: float = 12.0) -> Optional[str]:
        """
        Re-record an instruction for the panel's 're-dictate' (V) action and
        return its transcription, or None if cancelled / empty.

        Reuses thread-safe primitives (``AudioService`` start/stop/transcribe are
        not GTK calls). The mode's own trigger (or Enter) stops the recording,
        Esc cancels, and running out of time stops it too — whatever was captured
        is transcribed. Runs on the worker thread. NOTE: ``start_recording`` bumps
        ``recording_generation`` — the caller must re-read it afterwards.
        """
        mapping: Dict[int, str] = {ecodes.KEY_ESC: "cancel"}
        mapping.update({code: "stop" for code in _CONFIRM_CODES})
        for code in cls.trigger_codes(mode):
            mapping.setdefault(code, "stop")

        with GrabbedKeys() as keys:
            if not keys.alive:
                return None
            OverlayManager.show(mode)
            AudioService.start_recording()
            with keys.exclusive():
                action = keys.poll(mapping, timeout)  # None on timeout → auto-stop
            audio = AudioService.stop_recording()

        if action == "cancel" or audio is None:
            return None
        OverlayManager.set_transcribing()
        try:
            return (AudioService.transcribe(audio) or "").strip() or None
        except Exception:
            return None

    # Re-scan interval (seconds) to pick up keyboards that (re)appear, e.g.
    # after resume from suspend or USB hotplug.
    _RESCAN_INTERVAL_SEC: float = 3.0
    _stop: bool = False

    @classmethod
    def stop(cls) -> None:
        """Request the listener loop to exit (the daemon thread will end)."""
        cls._stop = True

    @classmethod
    def _sync_devices(
        cls,
        sel: "selectors.BaseSelector",
        registered: Dict[str, InputDevice],
    ) -> None:
        """
        Register any keyboards not already tracked.

        Compares by device path so existing handles are never reopened
        (avoids fd leaks). Vanished devices are pruned lazily on read
        failure in the main loop, since list_devices() may briefly omit a
        device that is still readable.
        """
        for path in evdev.list_devices():
            if path in registered:
                continue
            try:
                dev = InputDevice(path)
            except Exception:
                continue
            if not cls._is_keyboard(dev):
                try:
                    dev.close()
                except Exception:
                    pass
                continue
            try:
                sel.register(dev, selectors.EVENT_READ)
                registered[path] = dev
                logger.info("Registered keyboard: %s (%s)", dev.name, path)
            except Exception:
                try:
                    dev.close()
                except Exception:
                    pass

    @classmethod
    def _drop_device(
        cls,
        sel: "selectors.BaseSelector",
        registered: Dict[str, InputDevice],
        device: InputDevice,
    ) -> None:
        """Unregister, close and forget a disconnected device."""
        logger.warning("Device disconnected: %s", device.path)
        try:
            sel.unregister(device)
        except Exception:
            pass
        registered.pop(device.path, None)
        try:
            device.close()
        except Exception:
            pass

    @classmethod
    def run(cls) -> None:
        """
        Start the evdev keyboard listener (blocking).

        Monitors all keyboard devices using a selector. Survives device
        disconnects (suspend/resume, hotplug): it never exits on its own,
        and re-scans every ``_RESCAN_INTERVAL_SEC`` to (re)register
        keyboards as they come back. Runs in a background daemon thread —
        started from app.py.
        """
        import time

        cls._stop = False
        sel = selectors.DefaultSelector()
        registered: Dict[str, InputDevice] = {}

        cls._sync_devices(sel, registered)
        if registered:
            print(f"⌨️  Listening on {len(registered)} keyboard device(s)")
        else:
            print(
                "⏳ No keyboard accessible yet — will keep scanning.\n"
                "   If this persists: sudo usermod -aG input $USER (then re-login)."
            )

        last_scan = time.monotonic()
        try:
            while not cls._stop:
                for key, _ in sel.select(timeout=cls._RESCAN_INTERVAL_SEC):
                    device = key.fileobj
                    try:
                        for event in device.read():
                            if event.type == ecodes.EV_KEY:
                                cls._handle_key_event(event)
                    except OSError:
                        # Device disconnected — drop it and keep going. It will
                        # be re-registered by the periodic re-scan when it
                        # reappears (new event number after resume).
                        cls._drop_device(sel, registered, device)

                now = time.monotonic()
                if now - last_scan >= cls._RESCAN_INTERVAL_SEC:
                    cls._sync_devices(sel, registered)
                    last_scan = now
        except Exception as e:
            logger.error("Keyboard listener error: %s", e)
        finally:
            for dev in list(registered.values()):
                try:
                    dev.close()
                except Exception:
                    pass
            sel.close()


# Populate the keycode→mode map from the config loaded at import time.
KeyboardHandler.reload_hotkeys()


if __name__ == "__main__":
    # Self-check for the pure review-key mapping (no device / GTK needed).
    _ra = KeyboardHandler._review_action
    assert _ra(ecodes.KEY_ENTER) == "accept"
    assert _ra(ecodes.KEY_ESC) == "reject"
    assert _ra(ecodes.KEY_R) == "redo"
    assert _ra(ecodes.KEY_V) == "redict"
    assert _ra(ecodes.KEY_C) == "copy"
    assert _ra(ecodes.KEY_A) is None
    print("✓ _review_action mapping OK")
