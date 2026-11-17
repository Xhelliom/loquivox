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


def test_google_provider_follows_the_conversation_unless_its_own():
    base = _cfg(TALK_LIVE_PROVIDER="google", TALK_GEMINI_MODEL="talk-m", TALK_GEMINI_VOICE="Puck")
    assert comp.live_provider(base) == "google"
    assert comp.gemini_options(base) == {"model": "talk-m", "voice": "Puck"}
    own = _cfg(TALK_LIVE_PROVIDER="google", COMPANION_LIVE_PROVIDER="openai")
    assert comp.live_provider(own) == "openai"
    own = _cfg(COMPANION_LIVE_PROVIDER="google", COMPANION_GEMINI_MODEL="comp-m",
               COMPANION_GEMINI_VOICE="Kore", TALK_GEMINI_VOICE="Puck")
    assert comp.live_provider(own) == "google"
    assert comp.gemini_options(own) == {"model": "comp-m", "voice": "Kore"}
    print("✓ companion provider/model/voice: empty follows [talk], its own wins")


def test_converse_opens_the_provider_the_companion_chose():
    from loquivox.handlers.mode import ModeHandler
    seen = {}

    def fake(session, keys, engine, name):
        t = engine(session)
        seen.update(cls=type(t).__name__, name=name, model=getattr(t, "_model", None),
                    voice=t._voice, prefix=t.prefix)
    real_cfg, real_fn = config_module.CFG, ModeHandler._talk_converse_server
    config_module.CFG = _cfg(TALK_LIVE_PROVIDER="openai", COMPANION_LIVE_PROVIDER="google",
                             COMPANION_GEMINI_MODEL="comp-m", COMPANION_GEMINI_VOICE="Kore")
    ModeHandler._talk_converse_server = staticmethod(fake)
    try:
        comp._converse(TalkSession(write=False), prefix=[1, 2])
    finally:
        config_module.CFG, ModeHandler._talk_converse_server = real_cfg, real_fn
    assert seen == {"cls": "GeminiLive", "name": "Gemini Live", "model": "comp-m",
                    "voice": "Kore", "prefix": [1, 2]}, seen
    print("✓ _converse opens GeminiLive with the companion's model and voice")


def test_google_keys_load_from_toml():
    toml = ('[companion]\nlive_provider = "Google"\ngemini_model = "m"\n'
            'gemini_voice = "puck"\n')
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(toml)
        real = config_module.CONFIG_FILE
        config_module.CONFIG_FILE = p
        try:
            cfg = config_module._build_config()
        finally:
            config_module.CONFIG_FILE = real
    assert (cfg.COMPANION_LIVE_PROVIDER, cfg.COMPANION_GEMINI_MODEL,
            cfg.COMPANION_GEMINI_VOICE) == ("google", "m", "Puck")
    print("✓ [companion] live_provider/gemini_model/gemini_voice are read")


if __name__ == "__main__":
    test_google_provider_follows_the_conversation_unless_its_own()
    test_converse_opens_the_provider_the_companion_chose()
    test_google_keys_load_from_toml()
    test_unset_follows_the_conversation()
    test_its_own_value_wins()
    test_keys_load_from_toml()
