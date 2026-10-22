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


def test_judge_has_its_own_slot_and_the_memory_asks_it():
    from loquivox.services import memory

    STATE.ai_provider, STATE.ai_chat_model = "groq", "chat-model"
    STATE.ai_judge_provider = STATE.ai_judge_model = ""
    assert AIService.judge_target() == ("groq", "chat-model")
    STATE.ai_judge_provider, STATE.ai_judge_model = "openai", "judge-model"
    assert AIService.judge_target() == ("openai", "judge-model")
    STATE.ai_judge_provider = "anthropic"
    STATE.ai_judge_model = ""
    assert AIService.judge_target() == ("groq", "chat-model")   # ids don't cross providers
    STATE.ai_judge_provider, STATE.ai_judge_model = "openai", "judge-model"
    seen = []
    real = AIService.complete
    AIService.complete = staticmethod(lambda m, model=None, provider=None, **kw: seen.append(
        (provider, model)) or "NONE")
    try:
        memory._ask([{"source": "", "text": "x"}], "s", "c")
    finally:
        AIService.complete = real
        STATE.ai_judge_provider = STATE.ai_judge_model = ""
    assert seen == [("openai", "judge-model")], seen
    print("✓ the memory judge follows its slot, then the chat's")


def test_the_agent_loop_uses_the_agent_target():
    """_with_tools must ask AIService.agent_target(), at every call."""
    from loquivox.services.talk import TalkSession

    seen = []

    def complete(messages, model=None, provider=None, **kw):
        seen.append((provider, model))
        return "ok"

    def run():
        return TalkSession()._with_tools([{"role": "user", "content": "x"}], None)

    real = AIService.complete
    AIService.complete = staticmethod(complete)
    try:
        STATE.ai_provider, STATE.ai_chat_model = "groq", "chat-model"
        STATE.ai_agent_provider, STATE.ai_agent_model = "anthropic", "agent-model"
        run()
        assert seen[-1] == ("anthropic", "agent-model"), seen
        STATE.ai_agent_provider = STATE.ai_agent_model = ""
        run()
        assert seen[-1] == ("groq", "chat-model"), seen
        STATE.ai_agent_provider, STATE.ai_agent_model = "openai", "o-model"   # no restart
        run()
        assert seen[-1] == ("openai", "o-model"), seen
    finally:
        AIService.complete = real
    print("✓ _with_tools follows the agent slot, then the chat's, live")


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


def test_vision_reads_on_its_own_provider_not_the_chats():
    seen = []
    STATE.ai_provider, STATE.ai_vision_provider = "anthropic", "groq"
    STATE.ai_vision_model = "qwen/qwen3.8-27b"
    real = AIService._complete
    AIService._complete = staticmethod(
        lambda messages, model, provider=None: seen.append((model, provider)) or "ok")
    try:
        assert AIService.vision("what?", "AAAA") == "ok"
    finally:
        AIService._complete = real
    assert seen == [("qwen/qwen3.8-27b", "groq")], seen
    print("✓ chat on Anthropic, vision on Groq: the capture goes to Groq")


def test_a_vision_http_error_is_raised_not_swallowed():
    # mode.py says it; a bare None would read as "the model returned nothing"
    real = AIService._complete

    def boom(messages, model, provider=None):
        raise RuntimeError("Error code: 404 - not_found_error")
    AIService._complete = staticmethod(boom)
    try:
        try:
            AIService.vision("what?", "AAAA")
        except RuntimeError as e:
            assert "404" in str(e)
        else:
            raise AssertionError("the error was swallowed")
    finally:
        AIService._complete = real
    print("✓ a vision HTTP error reaches the caller")


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


def test_anthropic_client_sends_its_version_header():
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    api._anthropic_client = None
    client = api.get_ai_client("anthropic")
    assert client.default_headers.get("anthropic-version") == "2023-06-01"
    api._anthropic_client = None
    print("✓ anthropic client lists models with its version header")


def test_auto_delegation_follows_the_provider_picked_in_settings():
    from loquivox.services.live_talk import resolve_delegation
    for provider, want in (("openai", "responses"), ("groq", "client"), ("anthropic", "client")):
        STATE.ai_provider = provider
        assert resolve_delegation("auto") == want, provider
    print("✓ auto delegation reads STATE.ai_provider")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
