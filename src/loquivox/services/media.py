"""
Keep music out of the way of the microphone, and bring it back afterwards.

``media = "duck"`` lowers the volume of every playing player while the
microphone is open and restores it after; ``"resume"`` only presses play
again afterwards; ``"off"`` does nothing. Both modes resume, because of the
case that motivated all this: opening a Bluetooth headset's microphone
switches it from A2DP to HFP, the stereo sink vanishes for an instant and
every player pauses itself — then never resumes. Nothing in the audio stack
can prevent that (a classic Bluetooth limit): ducking only shows on speakers,
a wired headset, or an LE Audio headset, where the music survives the
microphone. The grace period matters because talk mode reopens the
microphone once per turn — music must not come back between two sentences.

Players are driven through ``playerctl`` (MPRIS); a missing binary is a
silent no-op.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Dict, Optional

import loquivox.config as config_module

_lock = threading.Lock()
# player → {sink-input index: volume when the microphone opened}. Ducking acts
# on the player's audio STREAM, never on its MPRIS volume: some players (Music
# Assistant) expose the system sink's volume there, and lowering it silences
# everything, the companion's own voice included.
_playing: Dict[str, Dict[int, str]] = {}
_pending: Dict[str, Dict[int, str]] = {}  # being restored: a reopened mic takes them back
_timer: Optional[threading.Timer] = None
_voice_duck = False   # the companion asks for the music down, whatever the mic does
_warned: set = set()
_vlock = threading.Lock()
_voice_want = False
_voice_busy = False
_PROFILE_WAIT_S = 8.0  # ponytail: bound on the A2DP switch-back, not a setting


def _playerctl(*args: str) -> str:
    try:
        return subprocess.run(["playerctl", *args], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\.instance\d+$", "", text.lower()))


def _streams(player: str) -> Dict[int, str]:
    """Sink-inputs of ``player`` → their volume, matched on application name/binary/id."""
    try:
        out = subprocess.run(["pactl", "--format=json", "list", "sink-inputs"],
                             capture_output=True, text=True, timeout=2).stdout
        inputs = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    me, found = _norm(player), {}
    for si in inputs:
        props = si.get("properties", {})
        names = [_norm(props.get(k, "")) for k in
                 ("application.name", "application.process.binary", "application.id")]
        if me and any(len(n) >= 3 and (n in me or me in n) for n in names):
            # ponytail: loudest channel, set back on all — a balance is lost
            found[si["index"]] = max((int(c["value_percent"].rstrip("%"))
                                      for c in si.get("volume", {}).values()),
                                     default=100)
    return {i: f"{v}%" for i, v in found.items()}


def _set_streams(streams: Dict[int, str], volume: Optional[str] = None) -> None:
    for idx, original in streams.items():
        _pactl("set-sink-input-volume", str(idx), volume or original)


def _duck_now() -> None:
    """Lower every snapshotted stream (lock held). Never below what it was."""
    target = int(config_module.CFG.MEDIA_DUCK_VOLUME * 100)
    for streams in _playing.values():
        _set_streams({i: v for i, v in streams.items() if int(v[:-1]) > target}, f"{target}%")


def _unduck() -> None:
    for streams in _playing.values():
        _set_streams(streams)


def mic_opened(duck: bool = True) -> None:
    """
    Call BEFORE the input stream opens: a headset pauses players once it has.
    ``duck=False``: the microphone alone lowers nothing (a companion session,
    where ``duck_voice`` decides).
    """
    global _timer
    cfg = config_module.CFG
    if cfg.MEDIA == "off":
        return
    with _lock:
        if _timer is not None:  # reopened within the grace period: still ours
            _timer.cancel()
            _timer = None
            return
        carried = dict(_pending)   # reopened while _restore waits for A2DP
        _pending.clear()
        _playing.clear()
        for line in _playerctl("-a", "--format", "{{playerName}}\t{{status}}",
                               "status").splitlines():
            name, status = (line.split("\t") + [""])[:2]
            if status == "Playing" or name in carried:
                # a stream already lowered by the restore in flight reads
                # ducked: its original is the one the restore took
                _playing[name] = carried.get(name) or (
                    _streams(name) if cfg.MEDIA == "duck" else {})
                if cfg.MEDIA == "duck" and not _playing[name] and name not in _warned:
                    _warned.add(name)
                    print(f"⚠️  Media: no audio stream found for '{name}' — "
                          "its volume is left alone")
        for name, streams in carried.items():
            _playing.setdefault(name, streams)   # paused by the switch: still ours
        if cfg.MEDIA == "duck" and (duck or _voice_duck):
            _duck_now()


def mic_closed() -> None:
    """Restore what was playing, unless the microphone reopens first."""
    global _timer
    with _lock:
        if not _playing:
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
        _pending.update(_playing)
        _playing.clear()
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
        players = dict(_pending)   # empty: the microphone reopened meanwhile
        _pending.clear()
    for name, streams in players.items():
        _set_streams(streams)
        _playerctl("-p", name, "play")
    if players:
        print(f"▶️  Media resumed: {', '.join(players)}")
