"""
Presence mode survives a restart; a locked screen (or no input for a while) is a
silence that is NOT a preference: it restores what the user had chosen.

No microphone, no bus, no swayidle: the backends are fed by hand.

    python tests/test_away.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.platform import logind, wayland  # noqa: E402
from loquivox.state import STATE, SettingsManager  # noqa: E402


class _Presence:
    def __init__(self, log): self.log = log
    def release(self): self.log.append("release")
    def resume(self): self.log.append("resume")
    def start(self): self.log.append("start")


class _Bubble:
    def __init__(self, log): self.log = log
    def set_state(self, s): self.log.append(f"bubble:{s}")


def _make(saved=None):
    log = []
    if saved is None:
        SettingsManager.update("presence_mode", "active")
    else:
        SettingsManager.update("presence_mode", saved)
    STATE.presence_mode = "active"
    STATE.companion_session = False
    return comp.Companion(_Presence(log), _Bubble(log)), log


def test_silence_survives_a_restart():
    c, _ = _make()
    c.set_mode("silent")
    assert SettingsManager.load()["presence_mode"] == "silent"
    STATE.presence_mode = "active"            # a fresh process
    c2 = comp.Companion(_Presence([]), _Bubble([]))
    c2.start_modes()
    assert STATE.presence_mode == "silent"


def test_garbage_in_settings_means_active():
    SettingsManager.update("presence_mode", "bogus")
    STATE.presence_mode = "active"
    comp.Companion(_Presence([]), _Bubble([])).start_modes()
    assert STATE.presence_mode == "active"


def test_lock_silences_without_persisting_and_unlock_restores():
    c, log = _make()
    c.set_away("lock", True)
    assert STATE.presence_mode == "silent" and "release" in log
    assert SettingsManager.load()["presence_mode"] == "active"      # not a preference
    c.set_away("lock", False)
    assert STATE.presence_mode == "active" and log[-2:] == ["resume", "bubble:idle"], log


def test_unlock_does_not_wake_a_user_who_chose_silence():
    c, _ = _make()
    c.set_mode("silent")
    c.set_away("lock", True)
    c.set_away("lock", False)
    assert STATE.presence_mode == "silent"


def test_two_reasons_need_both_gone():
    c, _ = _make()
    c.set_away("lock", True)
    c.set_away("idle", True)
    c.set_away("lock", False)
    assert STATE.presence_mode == "silent"
    c.set_away("idle", False)
    assert STATE.presence_mode == "active"


def test_user_choice_while_away_wins():
    c, _ = _make()
    c.set_away("lock", True)
    c.set_mode("active")                      # F7 pressed meanwhile
    c.set_away("lock", False)
    assert STATE.presence_mode == "active"
    assert SettingsManager.load()["presence_mode"] == "active"


def test_lock_closes_the_session_in_progress():
    c, _ = _make()
    STATE.companion_session = True
    while not STATE.talk_click.empty():
        STATE.talk_click.get_nowait()
    c.set_away("lock", True)
    assert STATE.talk_click.get_nowait() == "cancel"
    STATE.companion_session = False


def test_no_session_opens_while_away():
    c, log = _make()
    c.set_away("lock", True)
    log.clear()
    c.wake(force=True, cause="key")           # F8 on a locked screen
    assert log == [] and not STATE.companion_session


def test_logind_reads_edges_not_levels():
    seen = []
    w = logind.LogindAway()
    w._on_lock = seen.append
    w._signal("Lock", None)
    w._signal("Unlock", None)
    w._signal("PropertiesChanged", ("org.freedesktop.login1.Session", {"LockedHint": True}, []))
    w._signal("PropertiesChanged", ("org.freedesktop.login1.Session", {"LockedHint": False}, []))
    w._signal("PropertiesChanged", ("org.freedesktop.login1.Session", {"IdleHint": True}, []))
    assert seen == [True, False, True, False], seen


def test_swayidle_lines():
    seen = []
    wayland.WaylandAway._idle_line("idle\n", seen.append)
    wayland.WaylandAway._idle_line("active\n", seen.append)
    wayland.WaylandAway._idle_line("noise\n", seen.append)
    assert seen == [True, False]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
