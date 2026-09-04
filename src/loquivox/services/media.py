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

import subprocess
import threading
import time
from typing import Dict, Optional

import loquivox.config as config_module

_lock = threading.Lock()
_playing: Dict[str, str] = {}  # player → volume when the microphone opened
_timer: Optional[threading.Timer] = None
_PROFILE_WAIT_S = 8.0  # ponytail: bound on the A2DP switch-back, not a setting


def _playerctl(*args: str) -> str:
    try:
        return subprocess.run(["playerctl", *args], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def mic_opened() -> None:
    """Call BEFORE the input stream opens: a headset pauses players once it has."""
    global _timer
    cfg = config_module.CFG
    if cfg.MEDIA == "off":
        return
    with _lock:
        if _timer is not None:  # reopened within the grace period: still ours
            _timer.cancel()
            _timer = None
            return
        _playing.clear()
        for line in _playerctl("-a", "--format", "{{playerName}}\t{{status}}\t{{volume}}",
                               "status").splitlines():
            name, status, volume = (line.split("\t") + ["", ""])[:3]
            if status == "Playing":
                _playing[name] = volume
        if cfg.MEDIA == "duck":
            for name in _playing:
                _playerctl("-p", name, "volume", str(cfg.MEDIA_DUCK_VOLUME))


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


def _headset_profile_active() -> bool:
    """A Bluetooth card still on HFP: its A2DP sink is not back yet."""
    try:
        out = subprocess.run(["pactl", "list", "cards"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Active Profile: headset-head-unit" in out


def _restore() -> None:
    global _timer
    with _lock:
        players, _timer = dict(_playing), None
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
    for name, volume in players.items():
        if volume:
            _playerctl("-p", name, "volume", volume)
        _playerctl("-p", name, "play")
    if players:
        print(f"▶️  Media resumed: {', '.join(players)}")


if __name__ == "__main__":  # self-check of the grace-period logic, no playerctl
    from dataclasses import replace
    config_module.CFG = replace(config_module.CFG, MEDIA="resume", MEDIA_GRACE_S=0.1)
    _playing["fake"] = "1.0"
    mic_closed(); mic_opened()            # reopened in time → snapshot kept
    assert _playing == {"fake": "1.0"} and _timer is None
    mic_closed(); time.sleep(0.3)         # closed for good → fired and cleared
    assert _playing == {} and _timer is None
    print("ok")
