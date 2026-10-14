"""
Ducking lowers every stream but Loquivox's and restores each exactly; the companion
ducks on who is speaking; a mic reopened mid-restore keeps the original volume.
    python tests/test_media_duck.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

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
OURS = {"application.name": "Loquivox"}


class FakeAudio:
    """pactl + playerctl: music at 80%/70% (balance), a Flatpak player, our own voice."""

    def __init__(self):
        self.vol = {7: [52428, 45875], 9: [65536, 65536], 11: [65536, 65536]}
        self.props = {7: {"application.name": "PipeWire ALSA [music-assistant-companion]"},
                      9: {"application.name": "Plexamp"}, 11: OURS}
        self.calls = []

    def pactl(self, *a):
        self.calls.append(("pactl",) + a)
        if a[0] == "set-sink-input-volume":
            self.vol[int(a[1])] = [int(v) for v in a[2:]]
        return ""

    def playerctl(self, *a):
        self.calls.append(("playerctl",) + a)
        return f"{PLAYER}\tPlaying\n" if a[0] == "-a" else ""

    def run(self, cmd, **kw):
        chans = ("front-left", "front-right")
        inputs = [{"index": i, "properties": self.props[i],
                   "volume": {c: {"value": v} for c, v in zip(chans, vs)}}
                  for i, vs in self.vol.items()]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(inputs), "")


def _install(fake, **cfg):
    config_module.CFG = replace(config_module.CFG, MEDIA="duck", MEDIA_DUCK_VOLUME=0.2,
                                MEDIA_GRACE_S=0.05, **cfg)
    media._ducked.clear(); media._playing.clear()
    media._timer, media._voice_duck = None, False
    media._pactl, media._playerctl = fake.pactl, fake.playerctl
    media.subprocess = type("S", (), {"run": staticmethod(fake.run),
                                      "SubprocessError": subprocess.SubprocessError})
    media._headset_profile_active = lambda: False


T = int(0.2 * 65536)


def test_ducks_everything_but_ourselves_and_restores_exactly():
    f = FakeAudio(); _install(f); before = {k: list(v) for k, v in f.vol.items()}
    media.mic_opened()
    assert f.vol[11] == before[11]                       # our voice is never lowered
    assert f.vol[9] == [T, T] and f.vol[7][0] == T       # any source, by stream
    assert f.vol[7][1] == 45875 * T // 52428             # balance kept
    assert not any(c[:2] == ("pactl", "set-sink-input-volume") and c[2] == "11" for c in f.calls)
    assert not any(c[0] == "pactl" and c[1] in ("set-sink-volume", "set-sink-mute") for c in f.calls)
    media.mic_closed(); time.sleep(0.4)
    assert f.vol == before, f.vol                        # volume AND balance
    assert ("playerctl", "-p", PLAYER, "play") in f.calls


def test_new_stream_untouched_and_gone_stream_ignored():
    f = FakeAudio(); _install(f)
    media.mic_opened()
    f.vol[20] = [65536, 65536]; f.props[20] = {"application.name": "Game"}   # appears while ducked
    del f.vol[9]; del f.props[9]                                              # leaves meanwhile
    media.mic_closed(); time.sleep(0.4)
    assert f.vol[20] == [65536, 65536] and 9 not in f.vol
    assert f.vol[7] == [52428, 45875]
    assert not any(c[:3] == ("pactl", "set-sink-input-volume", "20") for c in f.calls)


def test_mic_reopened_during_restore_keeps_the_original():
    f = FakeAudio(); _install(f)
    media._PROFILE_WAIT_S = 1.0
    media._headset_profile_active = lambda: (time.sleep(0.05) or True)  # HFP: restore waits
    media.mic_opened()
    media.mic_closed()
    time.sleep(0.2)                       # grace over, _restore is waiting for A2DP
    media.mic_opened()                    # reopened: streams still ducked
    time.sleep(1.3)                       # the old restore wakes up
    assert f.vol[7][0] == T, f.vol        # still ducked: the mic is open
    media._headset_profile_active = lambda: False
    media.mic_closed(); time.sleep(0.4)
    assert f.vol[7] == [52428, 45875], f.vol   # the ORIGINAL, not the ducked one
    media._PROFILE_WAIT_S = 8.0


def test_our_identity_is_recognised():
    assert media.is_ours({"application.name": "Loquivox"})
    assert media.is_ours({"application.id": "loquivox"})
    assert media.is_ours({"application.process.id": str(__import__("os").getpid())})
    assert not media.is_ours({"application.name": "PipeWire ALSA [python3.14]"})


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
