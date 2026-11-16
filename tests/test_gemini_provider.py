"""
Gemini Live as the second provider of the Live engine: how it is chosen, how it
fails, and that its key is a managed, masked secret. No network, no GTK, no SDK:

    python tests/test_gemini_provider.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402


def _toml(text: str):
    config_module.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    config_module.CONFIG_FILE.write_text(text, encoding="utf-8")
    return config_module._build_config()


def test_defaults_keep_openai():
    cfg = config_module.Config()
    assert cfg.TALK_LIVE_PROVIDER == "openai" and cfg.TALK_GEMINI_MODEL == "gemini-3.8-live"
    assert len(cfg.TALK_GEMINI_VOICES) == 30 and cfg.TALK_GEMINI_VOICE in cfg.TALK_GEMINI_VOICES


def test_config_toml_chooses_provider_model_voice():
    cfg = _toml('[talk]\nengine = "live"\nlive_provider = "Google"\n'
                'gemini_model = "gemini-3.8-live-extended-thinking"\n'
                'gemini_voice = "puck"\ngemini_thinking = "high"\n')
    assert (cfg.TALK_LIVE_PROVIDER, cfg.TALK_GEMINI_VOICE, cfg.TALK_GEMINI_THINKING) == \
        ("google", "Puck", "high")
    assert cfg.TALK_GEMINI_MODEL.endswith("extended-thinking")
    # a typo falls back to the default rather than opening a session that cannot work
    assert _toml('[talk]\nlive_provider = "gogle"\ngemini_voice = "nobody"\n') \
        .TALK_LIVE_PROVIDER == "openai"


def test_key_is_managed_and_masked():
    from loquivox.diagnostics import redact
    from loquivox.secrets import MANAGED_KEYS

    assert "GEMINI_API_KEY" in MANAGED_KEYS
    key = "AIza" + "SyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY"
    for text in (f"GEMINI_API_KEY={key}", f"url?key={key}", f"bare {key} here"):
        assert key not in redact(text), text


def test_google_provider_picks_the_gemini_class_and_falls_back_without_a_key():
    from loquivox.handlers.mode import ModeHandler
    from loquivox.state import STATE

    calls = []
    real = ModeHandler._talk_converse_server, ModeHandler._talk_converse
    ModeHandler._talk_converse_server = staticmethod(
        lambda session, keys, engine, label: calls.append((engine.__name__, label)) or False)
    ModeHandler._talk_converse = staticmethod(lambda session, keys: calls.append("cascade") or False)
    saved = config_module.CFG
    try:
        config_module.CFG = _toml('[talk]\nengine = "live"\nlive_provider = "google"\n')
        ModeHandler._talk_conversation(object(), object())
        config_module.CFG = _toml('[talk]\nengine = "live"\n')
        ModeHandler._talk_conversation(object(), object())
    finally:
        config_module.CFG = saved
        ModeHandler._talk_converse_server, ModeHandler._talk_converse = real
        config_module.CONFIG_FILE.unlink()
    assert calls == [("GeminiLive", "Gemini Live"), ("LiveTalk", "Live")], calls
    assert STATE.talk_engine == "live"


def test_no_key_means_start_raises_so_the_loop_falls_back():
    from loquivox.services.gemini_live import GeminiLive
    from loquivox.services.talk import TalkSession

    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        GeminiLive(TalkSession()).start()
    except RuntimeError as e:
        assert "GEMINI_API_KEY" in str(e) or "google-genai" in str(e)
    else:
        raise AssertionError("opened without a key")
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
