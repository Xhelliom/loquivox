"""
Keep other sounds out of the way of the microphone, and bring them back.

``media = "duck"`` lowers EVERY output stream that is not Loquivox's while the
microphone is open (or someone is speaking), like a phone during a call, and
restores each one exactly afterwards; ``"resume"`` only presses play again;
``"off"`` does nothing. No player is looked up by name — names never matched
(Music Assistant, a Flatpak's PID) — so it works for any source. Loquivox's own
streams are recognised by the identity app.py gives the
process; the sink's volume is never touched. Both modes resume, because
opening a Bluetooth headset's microphone switches it from A2DP to HFP, the
stereo sink vanishes for an instant and every player pauses itself — then
never resumes (a classic Bluetooth limit: ducking only shows on speakers, a
wired headset, or an LE Audio headset). The grace period matters because talk
mode reopens the microphone once per turn.

``playerctl`` (MPRIS) is only used to press play afterwards; a missing binary
is a silent no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set

import loquivox.config as config_module

APP_NAME, APP_ID = "Loquivox", "loquivox"
# app.py sets PULSE_PROP / PIPEWIRE_PROPS to these before any stream opens.

_lock = threading.Lock()
_ducked: Dict[int, List[int]] = {}   # sink-input → raw per-channel volumes before the duck
_playing: Set[str] = set()           # players to press play on afterwards
_gen = 0                             # bumped per microphone opening: a restore in flight sees a reopen
_timer: Optional[threading.Timer] = None
_vlock = threading.Lock()
_voice_duck = False   # someone speaks: the sounds are down, whatever the mic does
_voice_want = False
_voice_busy = False
_PROFILE_WAIT_S = 8.0  # ponytail: bound on the A2DP switch-back, not a setting


def _playerctl(*args: str) -> str:
    try:
        return subprocess.run(["playerctl", *args], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def is_ours(props: dict) -> bool:
    """A stream Loquivox opened: never ducked."""
    return (props.get("application.name") == APP_NAME
            or props.get("application.id") == APP_ID
            or props.get("application.process.id") == str(os.getpid()))


def _others() -> Dict[int, List[int]]:
    """Every sink-input that is not ours → its raw per-channel volumes."""
    try:
        out = subprocess.run(["pactl", "--format=json", "list", "sink-inputs"],
                             capture_output=True, text=True, timeout=2).stdout
        inputs = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {si["index"]: [int(c["value"]) for c in si["volume"].values()]
            for si in inputs if si.get("volume") and not is_ours(si.get("properties", {}))}


def _set_volume(idx: int, raw: List[int]) -> None:
    _pactl("set-sink-input-volume", str(idx), *map(str, raw))


def _duck_now() -> None:
    """Lower every stream that is not ours (lock held). Once per duck: a stream
    that shows up while ducked is left alone, one already below stays below."""
    if _ducked:
        return
    target = int(config_module.CFG.MEDIA_DUCK_VOLUME * 65536)
    for idx, raw in _others().items():
        _ducked[idx] = raw
        loudest = max(raw, default=0)
        if loudest > target:   # scaled, so the balance survives
            _set_volume(idx, [v * target // loudest for v in raw])


def _unduck() -> None:
    """Put back exactly what was lowered; a stream that is gone is ignored."""
    alive = _others().keys() if _ducked else ()
    for idx, raw in _ducked.items():
        if idx in alive:
            _set_volume(idx, raw)
    _ducked.clear()


def mic_opened(duck: bool = True) -> None:
    """
    Call BEFORE the input stream opens: a headset pauses players once it has.
    ``duck=False``: the microphone alone lowers nothing (a companion session,
    where ``duck_voice`` decides).
    """
    global _timer, _gen
    cfg = config_module.CFG
    if cfg.MEDIA == "off":
        return
    with _lock:
        _gen += 1
        if _timer is not None:  # reopened within the grace period: still ours
            _timer.cancel()
            _timer = None
            return
        for line in _playerctl("-a", "--format", "{{playerName}}\t{{status}}",
                               "status").splitlines():
            name, status = (line.split("\t") + [""])[:2]
            if status == "Playing":
                _playing.add(name)
        if cfg.MEDIA == "duck" and (duck or _voice_duck):
            _duck_now()


def mic_closed() -> None:
    """Restore what was lowered, unless the microphone reopens first."""
    global _timer
    with _lock:
        if not _playing and not _ducked:
            return
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(config_module.CFG.MEDIA_GRACE_S, _restore)
        _timer.daemon = True
        _timer.start()


def _pactl(*args: str) -> str:
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _headset_profile_active() -> bool:
    """A Bluetooth card still on HFP: its A2DP sink is not back yet."""
    return "Active Profile: headset-head-unit" in _pactl("list", "cards")


def await_headset_mic(timeout: float = 3.0) -> None:
    """
    Call AFTER the input stream opens and BEFORE any output stream: wait for a
    Bluetooth headset to leave A2DP for the profile that carries its microphone.

    Opening the microphone is what triggers that switch, and the switch tears
    down the A2DP sink — a speaker stream opened before it has finished sits on
    a sink that is gone, and the transport errors out. Returns at once when the
    microphone is not a headset's, or the headset needs no switch (LE Audio).
    """
    # ponytail: the default source only — an input_device naming a headset
    # explicitly skips the wait; resolve that name through pactl if it matters
    if not _pactl("get-default-source").startswith("bluez_input"):
        return
    deadline = time.monotonic() + timeout
    while ("Active Profile: a2dp-sink" in _pactl("list", "cards")
           and time.monotonic() < deadline):
        time.sleep(0.1)


def duck_voice(on: bool) -> None:
    """
    Lower (or bring back) the music because someone is speaking. Safe from an
    audio callback: the pactl calls run on a worker, the last request wins.
    """
    global _voice_want, _voice_busy
    if config_module.CFG.MEDIA != "duck":
        return
    with _vlock:
        _voice_want = on
        if _voice_busy:
            return
        _voice_busy = True
    threading.Thread(target=_voice_loop, daemon=True).start()


def _voice_loop() -> None:
    global _voice_busy, _voice_duck
    while True:
        with _vlock:
            want = _voice_want
        with _lock:
            _voice_duck = want
            (_duck_now if want else _unduck)()
        with _vlock:
            if _voice_want == want:
                _voice_busy = False
                return


def _restore() -> None:
    global _timer, _voice_duck
    with _lock:
        _timer, _voice_duck = None, False
        gen = _gen
    # The grace period has passed, but a Bluetooth headset takes a while
    # longer to switch back to A2DP. Pressing play before that starts the
    # music on the vanishing HFP sink, and the player pauses itself again
    # when it goes. So wait for the profile, bounded: the switch may not
    # happen at all (music on speakers, wired headset).
    for _ in range(int(_PROFILE_WAIT_S / 0.25)):
        if not _headset_profile_active():
            break
        time.sleep(0.25)
    with _lock:
        if gen != _gen:   # the microphone reopened meanwhile: it is ours again
            return
        players = sorted(_playing)
        _playing.clear()
        _unduck()
    for name in players:
        _playerctl("-p", name, "play")
    if players:
        print(f"▶️  Media resumed: {', '.join(players)}")
