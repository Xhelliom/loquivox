"""
The companion's own Live settings: empty = "like the conversation", a value of
its own wins — for the voice, the delegation, the backend model, the web search
and the instructions.

No microphone, no GTK, no network:

    python tests/test_companion_live.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.handlers import companion as comp  # noqa: E402
from loquivox.services.live_talk import LiveTalk  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402


def _cfg(**kw):
    return dataclasses.replace(config_module.CFG, **kw)


def _open(cfg):
    """A companion session built the way ``wake`` / ``_converse`` build it."""
    real = config_module.CFG
    config_module.CFG = cfg
    try:
        session = TalkSession(write=False, instructions=comp.instructions(cfg))
        talk = LiveTalk(session, **comp.live_options(cfg))
        return talk, talk._config()["instructions"], talk._config()
    finally:
        config_module.CFG = real


def test_unset_follows_the_conversation():
    base = _cfg(TALK_LIVE_VOICE="cedar", TALK_LIVE_DELEGATION="responses",
                TALK_LIVE_BACKEND_MODEL="talk-model", TALK_LIVE_WEB_SEARCH="native",
                TALK_INSTRUCTIONS="Parle comme un pirate.")
    talk, instr, conf = _open(base)
    assert talk._voice == "cedar" and talk._delegation == "responses"
    assert conf["delegation"]["responses"]["model"] == "talk-model"
    assert "Parle comme un pirate." in instr
    print("✓ empty companion keys follow [talk]")


def test_its_own_value_wins():
    base = _cfg(TALK_LIVE_VOICE="cedar", TALK_LIVE_DELEGATION="client",
                TALK_LIVE_BACKEND_MODEL="talk-model", TALK_LIVE_WEB_SEARCH="plugin",
                TALK_INSTRUCTIONS="Parle comme un pirate.",
                COMPANION_LIVE_VOICE="marin", COMPANION_LIVE_DELEGATION="responses",
                COMPANION_LIVE_BACKEND_MODEL="comp-model",
                COMPANION_LIVE_WEB_SEARCH="native",
                COMPANION_INSTRUCTIONS="Sois bref.")
    talk, instr, conf = _open(base)
    assert talk._voice == "marin" and talk._delegation == "responses"
    assert conf["delegation"]["responses"]["model"] == "comp-model"
    assert talk._web_search == "native"
    assert "Sois bref." in instr and "pirate" not in instr
    assert comp.live_options(base)["voice"] == "marin"
    print("✓ a companion value of its own wins")


def test_keys_load_from_toml():
    import tomllib
    toml = ('[companion]\nlive_voice = "Verse"\nlive_delegation = "client"\n'
            'live_backend_model = "x"\nlive_web_search = "native"\n'
            'instructions = " Sois bref. "\n')
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(toml)
        real = config_module.CONFIG_FILE
        config_module.CONFIG_FILE = p
        try:
            cfg = config_module._build_config()
        finally:
            config_module.CONFIG_FILE = real
    assert (cfg.COMPANION_LIVE_VOICE, cfg.COMPANION_LIVE_DELEGATION,
            cfg.COMPANION_LIVE_BACKEND_MODEL, cfg.COMPANION_LIVE_WEB_SEARCH,
            cfg.COMPANION_INSTRUCTIONS) == ("verse", "client", "x", "native", "Sois bref.")
    print("✓ [companion] live_* and instructions are read from config.toml")


if __name__ == "__main__":
    test_unset_follows_the_conversation()
    test_its_own_value_wins()
    test_keys_load_from_toml()
