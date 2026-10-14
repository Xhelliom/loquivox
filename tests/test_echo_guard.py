"""
Self-check for TALK_ECHO_GUARD (config reading, old keys included):

    PYTHONPATH=src python tests/test_echo_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402


def read(talk: dict):
    saved = config_module._load_user_toml
    config_module._load_user_toml = lambda: {"talk": talk}
    try:
        return config_module._build_config()
    finally:
        config_module._load_user_toml = saved


assert read({}).TALK_ECHO_GUARD == "speakers", "the default changed"
assert read({"headset": True}).TALK_ECHO_GUARD == "headset", "old headset = true"
assert read({"headset": False}).TALK_ECHO_GUARD == "speakers", "old headset = false"
old = read({"barge_in": False, "headset": False})
assert old.TALK_ECHO_GUARD == "speakers" and not old.TALK_BARGE_IN, "old half duplex"
for guard in ("off", "headset", "speakers"):
    assert read({"echo_guard": guard}).TALK_ECHO_GUARD == guard
assert read({"echo_guard": "off", "headset": True}).TALK_ECHO_GUARD == "off", "new key wins"
assert read({"echo_guard": "bogus"}).TALK_ECHO_GUARD == "speakers", "garbage accepted"

# cascade (handlers/mode.py _talk_speak): same logic, as pure functions
from loquivox.services.vad import calibrates_echo, reply_blocks_mic  # noqa: E402

for barge_in in (True, False):
    assert not reply_blocks_mic(barge_in, "off"), "off must never close the mic"
assert reply_blocks_mic(False, "headset") and reply_blocks_mic(False, "speakers")
assert not reply_blocks_mic(True, "speakers")
assert not calibrates_echo("off") and not calibrates_echo("headset")
assert calibrates_echo("speakers")
print("ok")
