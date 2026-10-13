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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
