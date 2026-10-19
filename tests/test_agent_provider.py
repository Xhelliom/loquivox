"""
Self-check for the agent's own provider/model slot, no network:

    PYTHONPATH=src python tests/test_agent_provider.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.api as api  # noqa: E402
import loquivox.config as config_module  # noqa: E402
from loquivox.diagnostics import REDACTED, redact  # noqa: E402
from loquivox.secrets import MANAGED_KEYS  # noqa: E402
from loquivox.services import ai as ai_module  # noqa: E402
from loquivox.services.ai import AIService  # noqa: E402
from loquivox.state import STATE  # noqa: E402


def test_unset_agent_follows_chat():
    STATE.ai_provider, STATE.ai_chat_model = "groq", "chat-model"
    STATE.ai_agent_provider = STATE.ai_agent_model = ""
    assert AIService.agent_target() == ("groq", "chat-model")
    print("✓ no setting → the agent runs on the chat provider and model")


def test_agent_has_its_own_slot_live():
    STATE.ai_provider, STATE.ai_chat_model = "groq", "chat-model"
    for provider in ("groq", "openai", "anthropic"):
        STATE.ai_agent_provider, STATE.ai_agent_model = provider, f"{provider}-model"
        assert AIService.agent_target() == (provider, f"{provider}-model")
    STATE.ai_agent_provider, STATE.ai_agent_model = "", "other-model"   # model only
    assert AIService.agent_target() == ("groq", "other-model")
    STATE.ai_agent_provider, STATE.ai_agent_model = "anthropic", ""    # ids don't cross providers
    assert AIService.agent_target() == ("groq", "chat-model")
    print("✓ the agent follows its slot at every call, no restart")


def test_complete_routes_to_the_agent_provider():
    seen = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen.append(kw)
                    return type("R", (), {"choices": [type("C", (), {
                        "message": type("M", (), {"content": "ok"})})]})

    asked = []
    ai_module.get_ai_client = lambda p: asked.append(p) or Client
    AIService.complete([{"role": "user", "content": "x"}], model="m", provider="anthropic")
    assert asked == ["anthropic"] and seen[0]["model"] == "m"
    assert "reasoning_format" not in seen[0], "Groq-only knob leaked to another provider"
    print("✓ complete() reaches the provider it is told to")


def test_anthropic_is_a_provider_with_a_managed_key():
    assert "anthropic" in config_module.CFG.AI_PROVIDERS
    assert "ANTHROPIC_API_KEY" in MANAGED_KEYS
    import os
    os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        api.get_ai_client("anthropic")
        raise AssertionError("no key must raise")
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-" + "Ab1_" * 10
    client = api.get_ai_client("anthropic")
    assert "anthropic.com" in str(client.base_url)
    print("✓ anthropic: provider, managed key, OpenAI-compatible client")


def test_redact_anthropic_key():
    key = "sk-ant-api03-" + "Ab1_-Cd2" * 8
    for line in (f"ANTHROPIC_API_KEY={key}", f"x-api-key: {key}", f"bad key {key} rejected"):
        assert key not in redact(line), line
    assert redact(f"ANTHROPIC_API_KEY={key}") == f"ANTHROPIC_API_KEY={REDACTED}"
    print("✓ an Anthropic key never survives the redactor")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
