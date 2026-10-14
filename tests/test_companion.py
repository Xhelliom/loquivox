"""
Companion mode: wake → capture → release the mic → Live session → back to standby.

Presence, bubble and the cloud session are injected; no microphone, no GTK window.

    python tests/test_companion.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.config import CFG  # noqa: E402
from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.state import STATE  # noqa: E402


class _Presence:
    def __init__(self, log): self.log = log
    def release(self): self.log.append("release")
    def resume(self): self.log.append("resume")
    def start(self): self.log.append("start")
    def take_tail(self): return ["tail"]


class _Bubble:
    def __init__(self, log): self.log = log
    def set_state(self, s): self.log.append(f"bubble:{s}")
    def set_level(self, x): self.levels = getattr(self, "levels", []) + [x]
    def pulse(self): self.pulsed = True


def _make(*, session=None):
    log = []
    c = comp.Companion(_Presence(log), _Bubble(log),
                       look=lambda s: log.append("look"),
                       converse=session or (lambda s, prefix=(): log.append("converse")))
    STATE.presence_mode = "active"
    return c, log


def test_config_declares_the_mode():
    assert "companion" in CFG.HOTKEY_DEFS and CFG.HOTKEY_DEFS["companion"][1]
    assert "companion" in config_module.HOTKEY_DESCRIPTIONS
    assert "companion" in CFG.MODES
    assert "companion" not in CFG.RECORDING_MODES
    assert CFG.COMPANION_CLICK_MODE == "silent" and CFG.COMPANION_ENABLED


def test_wake_runs_the_loop_in_order():
    c, log = _make()
    c.wake()
    assert log == ["bubble:cloud", "look", "release", "converse", "resume", "bubble:idle"], log
    assert not STATE.companion_session and not STATE.talk_active


def test_session_levels_set_listening_and_speaking():
    c, log = _make()
    STATE.companion_session = True
    c.on_level("in", 0.4)
    c.on_level("in", 0.5)                 # unchanged state: not posted twice
    c.on_level("out", 0.3)
    c.on_level("in", 0.2)
    STATE.companion_session = False
    assert log == ["bubble:listening", "bubble:speaking"], log   # held: see the next test
    assert c.bubble.levels[-1] == 0.3


def test_short_gap_keeps_speaking_long_gap_listens(monkeypatch=None):
    now = [100.0]
    real, comp.time.monotonic = comp.time.monotonic, lambda: now[0]
    try:
        c, log = _make()
        STATE.companion_session = True
        c.on_level("out", 0.3)
        now[0] += 0.3
        c.on_level("out", 0.0)            # a blank between two words
        c.on_level("in", 0.2)
        assert log == ["bubble:speaking"], log
        now[0] += comp.SPEAK_HOLD + 0.1
        c.on_level("out", 0.0)
        assert log == ["bubble:speaking", "bubble:listening"], log
    finally:
        STATE.companion_session = False
        comp.time.monotonic = real


def test_late_block_after_session_leaves_the_bubble_at_rest():
    def converse(session, prefix=()):
        c.on_level("in", 0.4)             # a live block: red "listening"
    c, log = _make(session=converse)
    c.wake()
    n, levels = len(log), list(c.bubble.levels)
    c.on_level("in", 0.5)                 # arrives after the session closed
    c.on_level("out", 0.3)
    assert log == log[:n] and log[-1] == "bubble:idle", log
    assert c.bubble.levels == levels


def test_converse_wires_on_level():
    seen = {}
    class _Talk:
        def __init__(self, s): seen["talk"] = self
    import loquivox.services.live_talk as lt
    from loquivox.handlers.mode import ModeHandler
    c, _ = _make()
    saved = (lt.LiveTalk, ModeHandler._talk_converse_server, comp._companion)
    lt.LiveTalk = _Talk
    ModeHandler._talk_converse_server = staticmethod(lambda s, k, engine, name: engine(s))
    comp._companion = c
    try:
        comp._converse(object())
    finally:
        lt.LiveTalk, ModeHandler._talk_converse_server, comp._companion = saved
    assert seen["talk"].on_level == c.on_level


def test_standby_mic_level_reaches_the_bubble_but_not_a_session():
    c, _ = _make()
    assert c.presence.on_level == c._mic_level
    c.presence.on_level(0.4)
    assert c.bubble.levels == [0.4]
    STATE.companion_session = True
    c.presence.on_level(0.9)              # a block still in flight after the wake
    STATE.companion_session = False
    assert c.bubble.levels == [0.4]


def test_wake_pulses_the_bubble():
    c, log = _make()
    c.wake()
    assert c.bubble.pulsed


def test_silence_ignores_wake_and_releases_mic():
    c, log = _make()
    c.toggle_silence()
    assert STATE.presence_mode == "silent" and log == ["release", "bubble:silent"], log
    del log[:]
    c.wake()
    assert log == [], "a wake word in silence mode opened a session"
    c.toggle_silence()
    assert STATE.presence_mode == "active" and log == ["resume", "bubble:idle"], log


def test_click_switches_to_configured_mode_and_back():
    c, log = _make()
    c.click()
    assert STATE.presence_mode == CFG.COMPANION_CLICK_MODE
    c.click()
    assert STATE.presence_mode == "active"


def test_no_session_while_another_talk_runs():
    c, log = _make()
    STATE.talk_active = True
    try:
        c.wake()
    finally:
        STATE.talk_active = False
    assert log == []


def test_silence_during_session_ends_it_and_stays_silent():
    seen = {}

    def converse(session, prefix=()):
        c.toggle_silence()                       # pressed mid-conversation
        seen["click"] = STATE.talk_click.get_nowait()

    c, log = _make(session=converse)
    c.wake()
    assert seen["click"] == "cancel"
    assert STATE.presence_mode == "silent"
    assert "resume" not in log and log[-1] == "bubble:silent", log


def test_no_transcript_in_the_chat():
    from loquivox.managers.chat import ChatManager
    STATE.chat_messages = []
    STATE.companion_session = True
    try:
        ChatManager.add_message("user", "secret")
        ChatManager.stream("user", "secret")
    finally:
        STATE.companion_session = False
    assert STATE.chat_messages == []


def test_silence_key_survives_a_running_session():
    from loquivox.handlers.keyboard import KeyboardHandler
    hit = []
    orig = comp.toggle_silence
    comp.toggle_silence = lambda: hit.append(1)
    STATE.talk_active = True
    try:
        KeyboardHandler._on_press("companion")
    finally:
        STATE.talk_active = False
        comp.toggle_silence = orig
    assert hit == [1]


def test_idle_for_counts_from_last_activity():
    from loquivox.services.live_talk import LiveTalk
    t = LiveTalk.__new__(LiveTalk)
    import threading
    t._live, t._last_delta, t._last_audio = {}, {}, 0.0
    t._lock = threading.Lock()
    t._last_activity = time.monotonic() - 30
    assert t.idle_for() >= 30
    t._live["user"] = "en cours"
    t._last_delta["user"] = time.monotonic() - 2
    assert t.idle_for() == 0.0
    # un tour clos ne doit pas effacer son activité : la réponse lente ne coupe pas
    t._last_activity = time.monotonic() - 99
    t._live.clear()
    t._live["assistant"] = ""
    t._close_turn("assistant")
    assert t.idle_for() < 1


def test_silence_key_during_f4_session_does_not_reopen_mic():
    c, log = _make()
    STATE.presence_mode = "silent"
    STATE.talk_active = True
    try:
        c.set_mode("active")
    finally:
        STATE.talk_active = False
    assert "resume" not in log, log


def test_pause_and_resume_after_talk():
    c, log = _make()
    comp._companion = c
    comp.pause_for_talk()
    assert log == ["release"], log
    comp.resume_after_talk()
    assert log == ["release", "resume"], log
    STATE.presence_mode = "silent"
    del log[:]
    comp.pause_for_talk(); comp.resume_after_talk()
    assert log == [], "silent mode must keep the mic closed"
    STATE.presence_mode = "active"
    STATE.companion_session = True
    try:
        comp.resume_after_talk()
    finally:
        STATE.companion_session = False
    assert log == []


def test_wake_restores_current_mode():
    c, _ = _make()
    STATE.current_mode = "dictation"
    c.wake()
    assert STATE.current_mode == "dictation"


def test_wake_tail_reaches_the_live_engine():
    got = {}
    c, _ = _make(session=lambda s, prefix=(): got.update(prefix=list(prefix)))
    c.wake()
    assert got["prefix"] == ["tail"]


def test_presence_keeps_audio_after_wake():
    import numpy as np
    from loquivox.services.presence import Presence, BLOCK
    class M:
        def __init__(s): s.score = 0.0
        def predict(s, pcm): return {"w": s.score}
        def reset(s): pass
    m = M()
    p = Presence(lambda: None, model=m)
    blk = np.zeros((BLOCK, 1), dtype="float32")
    p._on_audio(blk, BLOCK, None, None)
    assert p.take_tail() == []
    m.score = 1.0
    p._on_audio(blk, BLOCK, None, None)
    m.score = 0.0
    for _ in range(3):
        p._on_audio(blk, BLOCK, None, None)
    assert len(p.take_tail()) == 3 and p.take_tail() == []


def test_stop_phrase_matcher():
    from loquivox.services.talk import user_said_stop
    for yes in ("Tais-toi", "je ne te parlais pas", "Je ne te parlais pas.",
                "c'est pas à toi que je parle", "c’est pas à toi que je parle"):
        assert user_said_stop(yes), yes
    for no in ("", "Merci", "Peux-tu m'expliquer pourquoi je ne te parlais pas "
               "hier alors que tu répondais quand même à tout ce que je disais ?",
               "Dis-moi ce que veut dire tais-toi en anglais, s'il te plaît"):
        assert not user_said_stop(no), no


def test_spoken_stop_cancels_and_is_logged(capsys=None):
    import io, contextlib
    from loquivox.services.live_talk import LiveTalk
    STATE.companion_session = True
    while not STATE.talk_click.empty():
        STATE.talk_click.get_nowait()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        LiveTalk._stop_if_asked("je ne te parlais pas")
        LiveTalk._stop_if_asked("bonjour")
    STATE.companion_session = False
    assert STATE.talk_click.get_nowait() == "cancel" and STATE.talk_click.empty()
    assert out.getvalue().count("faux déclenchement") == 1, out.getvalue()
    assert "parlais" not in out.getvalue()


def test_silence_key_mid_session_is_logged():
    import io, contextlib
    c, _ = _make()
    STATE.companion_session = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        c.set_mode("silent")
    STATE.companion_session = False
    while not STATE.talk_click.empty():
        STATE.talk_click.get_nowait()
    assert out.getvalue().count("faux déclenchement") == 1


def _cut(fn, turns):
    """Run fn with a companion session that already holds `turns` user turns."""
    import io, contextlib
    from loquivox.services.talk import TalkSession
    session = TalkSession(write=False)
    for i in range(turns):
        session.add_user(f"tour {i}")
    STATE.companion_session, STATE.talk_session = True, session
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            fn()
    finally:
        STATE.companion_session, STATE.talk_session = False, None
        while not STATE.talk_click.empty():
            STATE.talk_click.get_nowait()
    return out.getvalue()


def test_key_cut_after_a_user_turn_is_not_a_misfire():
    c, _ = _make()
    out = _cut(lambda: c.set_mode("silent"), 1)
    assert "faux déclenchement" not in out and "fin de session (touche)" in out, out
    c, _ = _make()
    assert "faux déclenchement (touche)" in _cut(lambda: c.set_mode("silent"), 0)


def test_spoken_stop_is_a_misfire_only_if_it_is_the_only_turn():
    from loquivox.services.live_talk import LiveTalk
    # the stop phrase is already recorded as a turn when _stop_if_asked runs
    out = _cut(lambda: LiveTalk._stop_if_asked("je ne te parlais pas"), 1)
    assert "faux déclenchement (voix)" in out, out
    out = _cut(lambda: LiveTalk._stop_if_asked("je ne te parlais pas"), 3)
    assert "faux déclenchement" not in out and "fin de session (voix)" in out, out


def test_wake_key_opens_from_silence_and_ends_without_misfire():
    from loquivox.handlers.keyboard import KeyboardHandler
    assert CFG.HOTKEY_DEFS["companion_wake"][1] == ["F8"]
    assert "companion_wake" in config_module.HOTKEY_DESCRIPTIONS
    c, log = _make()
    comp._companion = c
    STATE.presence_mode = "silent"            # wake word ignored, the key is not
    c.wake()
    assert "converse" not in log
    c.wake(force=True)
    assert "converse" in log and STATE.presence_mode == "silent"
    assert log[-1] == "bubble:silent", log
    # pressed during a session: ends it through the same verdict as a click,
    # and logs nothing (the idle/voice paths own the misfire count)
    got = []
    def press():
        KeyboardHandler._on_press("companion_wake")
        got.append(STATE.talk_click.get_nowait())
    out = _cut(press, 0)
    assert "faux déclenchement" not in out and got == ["finish"], (out, got)


def test_the_session_publishes_its_events():
    from loquivox.services import events
    seen = []
    sub = lambda name, data: seen.append((name, data))
    events.subscribe(sub)
    try:
        c, _ = _make()
        c.wake(force=True, cause="key")
        c.on_level("out", 0.3)      # outside a session: nothing
    finally:
        events._subscribers.remove(sub)
    assert seen == [("companion.state", {"state": "cloud"}),
                    ("companion.session_opened", {"cause": "key"}),
                    ("companion.session_closed", {"cause": "key"}),
                    ("companion.state", {"state": "idle"})], seen


def test_idle_clock_waits_for_a_plugin_then_restarts():
    from loquivox.handlers.mode import ModeHandler
    class T:
        idle = 50.0
        def idle_for(self): return self.idle
    class D:
        busy = True
        def coming(self): return self.busy
    class S:
        desks = D()
    t, s, since = T(), S(), [float("-inf")]
    assert ModeHandler._companion_idle(t, s, since) == 0.0   # answer on its way
    s.desks.busy = False                                       # answer handed over, not yet said
    assert ModeHandler._companion_idle(t, s, since) < 1
    since[0] = time.monotonic() - 20                          # then 12 s+ of real silence
    assert ModeHandler._companion_idle(t, s, since) >= 20
    t.idle = 3.0                                               # it spoke: clock follows it
    assert ModeHandler._companion_idle(t, s, since) == 3.0
    # no plugin ever: plain idle_for
    assert ModeHandler._companion_idle(t, s, [float("-inf")]) == 3.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
