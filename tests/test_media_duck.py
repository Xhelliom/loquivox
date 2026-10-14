"""
Ducking acts on the player's audio stream, never on its MPRIS volume; the
companion ducks on who is speaking; a mic reopened mid-restore keeps the
original volume.

    python tests/test_media_duck.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.services import media  # noqa: E402
from loquivox.state import STATE  # noqa: E402

PLAYER = "io_music_assistant_companion"


class FakeAudio:
    """pactl + playerctl: one Music Assistant stream at 80%, one other app's."""

    def __init__(self, with_stream=True):
        self.vol = {7: 80, 9: 100}
        self.calls = []
        self.with_stream = with_stream

    def pactl(self, *a):
        self.calls.append(("pactl",) + a)
        if a[0] == "set-sink-input-volume":
            self.vol[int(a[1])] = int(a[2].rstrip("%"))
        return ""

    def playerctl(self, *a):
        self.calls.append(("playerctl",) + a)
        return f"{PLAYER}\tPlaying\n" if a[0] == "-a" else ""

    def run(self, cmd, **kw):
        inputs = [{"index": 9, "properties": {"application.name": "Firefox"},
                   "volume": {"front-left": {"value_percent": "100%"}}}]
        if self.with_stream:
            inputs.append({"index": 7, "properties": {"application.name": "Music Assistant Companion"},
                           "volume": {"front-left": {"value_percent": f"{self.vol[7]}%"},
                                      "front-right": {"value_percent": "70%"}}})
        return subprocess.CompletedProcess(cmd, 0, json.dumps(inputs), "")


def _install(fake, **cfg):
    config_module.CFG = replace(config_module.CFG, MEDIA="duck", MEDIA_DUCK_VOLUME=0.2,
                                MEDIA_GRACE_S=0.05, **cfg)
    media._playing.clear(); media._pending.clear(); media._warned.clear()
    media._timer, media._voice_duck = None, False
    media._pactl, media._playerctl = fake.pactl, fake.playerctl
    media.subprocess = type("S", (), {"run": staticmethod(fake.run),
                                      "SubprocessError": subprocess.SubprocessError})
    media._headset_profile_active = lambda: False


def test_ducks_the_stream_not_the_mpris_volume():
    f = FakeAudio(); _install(f)
    media.mic_opened()
    assert f.vol == {7: 20, 9: 100}, f.vol           # only the player's stream
    assert not any(c[:3] == ("playerctl", "-p", PLAYER) and "volume" in c for c in f.calls)
    media.mic_closed(); time.sleep(0.4)
    assert f.vol == {7: 80, 9: 100}, f.vol           # back to what it was


def test_stream_not_found_ducks_nothing_and_warns_once(capsys=None):
    f = FakeAudio(with_stream=False); _install(f)
    media.mic_opened(); media._timer.cancel() if media._timer else None
    media._playing.clear(); media.mic_opened()
    assert not [c for c in f.calls if c[0] == "pactl" and c[1] == "set-sink-input-volume"]
    assert media._warned == {PLAYER}


def test_mic_reopened_during_restore_keeps_the_original():
    f = FakeAudio(); _install(f)
    waits = iter([True, True])
    media._PROFILE_WAIT_S = 1.0
    media._headset_profile_active = lambda: (time.sleep(0.05) or True)  # HFP: restore waits
    media.mic_opened()
    media.mic_closed()
    time.sleep(0.2)                       # grace over, _restore is waiting for A2DP
    media.mic_opened()                    # reopened: the stream still reads 20%
    time.sleep(1.3)                       # the old restore wakes up
    assert f.vol[7] == 20, f.vol          # still ducked: the mic is open
    media._headset_profile_active = lambda: False
    media.mic_closed(); time.sleep(0.4)
    assert f.vol[7] == 80, f.vol          # the ORIGINAL, not the ducked 20
    media._PROFILE_WAIT_S = 8.0


class _P:
    def release(self): pass
    def resume(self): pass


class _B:
    def set_state(self, s): pass
    def set_level(self, x): pass


def _companion(**cfg):
    config_module.CFG = replace(config_module.CFG, COMPANION_DUCK_HOLD_SEC=1.0, **cfg)
    ducks = []
    media.duck_voice = ducks.append
    c = comp.Companion(_P(), _B())
    STATE.companion_session = True
    return c, ducks


def _feed(c, clock, seq):
    for who, level, dt in seq:
        clock[0] += dt
        c.on_level(who, level)


def test_assistant_ducks_while_speaking_and_not_between_phrases():
    c, ducks = _companion(COMPANION_DUCK_ON_ASSISTANT=True, COMPANION_DUCK_ON_USER=False)
    clock = [100.0]; comp.time.monotonic = lambda: clock[0]
    _feed(c, clock, [("in", 0.05, 0.1)] * 10)
    assert ducks == []                                   # an open mic alone lowers nothing
    _feed(c, clock, [("out", 0.5, 0.1)] * 5)
    assert ducks == [True]
    _feed(c, clock, [("out", 0.0, 0.1), ("in", 0.05, 0.1)] * 4)   # 0.8 s gap between phrases
    _feed(c, clock, [("out", 0.5, 0.1)] * 3)
    assert ducks == [True]                               # no pumping
    _feed(c, clock, [("in", 0.05, 0.1)] * 15)            # reply over
    assert ducks == [True, False]                        # back ~1 s after, not at standby


def test_user_speech_ducks_but_music_alone_never_does():
    c, ducks = _companion(COMPANION_DUCK_ON_ASSISTANT=False, COMPANION_DUCK_ON_USER=True)
    clock = [100.0]; comp.time.monotonic = lambda: clock[0]
    _feed(c, clock, [("in", 0.4, 0.1)] * 60)             # loud music in the mic, no speech
    assert ducks == [], ducks                            # the room is learned, no trigger
    _feed(c, clock, [("in", 0.9, 0.1)] * 5)              # a voice well above the room
    assert ducks == [True]
    # music ducked → its echo falls; it must not come back as "speech"
    _feed(c, clock, [("in", 0.1, 0.1)] * 15)
    assert ducks == [True, False]
    _feed(c, clock, [("in", 0.4, 0.1)] * 30)             # music back up: same level as before
    assert ducks == [True, False]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
