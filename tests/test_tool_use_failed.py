"""
A provider 400 ``tool_use_failed`` must not cost the turn (Groq: "Tool choice
is none, but model called a tool"). Run: python tests/test_tool_use_failed.py
"""
from __future__ import annotations

import json

import loquivox.services.ai as ai_module
from loquivox.services.ai import AIService, TEXT_ONLY


class _Rejected(Exception):
    def __init__(self, generation: str):
        self.body = {"error": {"code": "tool_use_failed", "message": "Tool choice is none",
                               "failed_generation": generation}}


CALL = json.dumps({"name": "call_service", "arguments": {"entity_id": "light.office"}})
TOOLS = [{"type": "function", "function": {"name": "call_service"}}]


def _with(complete, stream, run):
    real = AIService._complete, AIService._stream
    AIService._complete, AIService._stream = staticmethod(complete), staticmethod(stream)
    ai_module.STATE.ai_chat_model = "m"
    try:
        return run()
    finally:
        AIService._complete, AIService._stream = real


def test_a_readable_call_is_handed_over_as_a_real_one():
    def stream(*a, **k):
        raise _Rejected(CALL)
    out: list = []
    assert _with(None, stream, lambda: AIService.complete([], tools=TOOLS, tool_calls_out=out)) == ""
    assert out[0]["name"] == "call_service"
    assert json.loads(out[0]["arguments"]) == {"entity_id": "light.office"}
    print("✓ failed_generation becomes a tool call")


def test_without_tools_the_turn_is_asked_again_in_text():
    seen = []

    def complete(messages, model, provider=None):
        seen.append(messages)
        if len(seen) == 1:
            raise _Rejected(CALL)
        return "Voilà."
    assert _with(complete, None, lambda: AIService.complete([{"role": "user", "content": "x"}])) == "Voilà."
    assert seen[1][-1] == {"role": "system", "content": TEXT_ONLY}
    print("✓ a tool-less call that tried a tool is retried in text")


def test_a_call_to_an_unoffered_tool_still_gets_an_answer():
    from loquivox.services.talk import NOT_DONE, TalkSession
    import loquivox.services.talk as talk_module

    session = TalkSession()
    session.add_user("allume le bureau")
    seen = []

    def stream(messages, model, on_delta, tool_calls_out=None, **extra):
        seen.append(messages)
        if len(seen) == 1:
            raise _Rejected(CALL)       # call_service: a deferred tool, not offered
        return "Il faut d'abord charger l'outil."

    def run():
        return session._with_tools([{"role": "user", "content": "x"}], None)
    def complete(m, model, provider=None):
        seen.append(m)
        return "Il faut d'abord charger l'outil."
    got = _with(complete, stream, run)
    assert got and got != "", got
    assert seen[-1][-1]["role"] == "tool" and "no tool named call_service" in seen[-1][-1]["content"]
    # and when even the retry says nothing, a sentence is spoken
    assert _with(lambda m, model, provider=None: "", lambda *a, **k: "", run) == NOT_DONE
    print("✓ an unoffered tool call is retried in text, never silent")


def test_an_invented_tool_leads_to_find_tools_not_a_failure():
    from loquivox.services.talk import TalkSession
    from loquivox.services.plugins import Plugin
    session = TalkSession()
    ran = []
    from loquivox.services.plugins import Desk
    session.desks._desks["find_tools"] = Desk(Plugin(
        name="find_tools", description="", parameters={}, started="",
        run=lambda a: ran.append(a) or "ok", message=lambda a, r: {"role": "system", "content": r}))
    session.desks._loaded.add("find_tools")
    seen = []

    def stream(messages, model, on_delta, tool_calls_out=None, **extra):
        seen.append((messages, extra.get("tools")))
        if len(seen) == 1:
            tool_calls_out.append({"id": "a", "name": "run_lot", "arguments": "{}"})
            return ""
        if len(seen) == 2:
            assert extra.get("tools"), "the retry must still offer the tools"
            tool_calls_out.append({"id": "b", "name": "find_tools", "arguments": "{}"})
            return ""
        return "Trouvé."
    got = _with(lambda m, model: "Trouvé.", stream, lambda: session._with_tools([{"role": "user", "content": "x"}], None))
    assert got == "Trouvé." and ran, (got, ran)
    print("✓ an invented tool is answered, then find_tools runs")


def test_a_failed_tool_says_so():
    from loquivox.services import mcp_tools
    assert mcp_tools.FAILED_FRAME.format(tool="notify", server="board", content="ÉCHEC : boom").count("ÉCHEC")
    from loquivox.services.plugins import NO_FALSE_DONE
    assert "ÉCHEC" in NO_FALSE_DONE
    print("✓ failure frame and rule name the failure")


def test_other_errors_still_surface():
    def complete(messages, model, provider=None):
        raise ValueError("boom")
    # safe_execute prints and returns None: the error is reported, not retried
    assert _with(complete, None, lambda: AIService.complete([])) is None
    print("✓ unrelated errors keep the usual path")


if __name__ == "__main__":
    test_a_readable_call_is_handed_over_as_a_real_one()
    test_without_tools_the_turn_is_asked_again_in_text()
    test_a_call_to_an_unoffered_tool_still_gets_an_answer()
    test_an_invented_tool_leads_to_find_tools_not_a_failure()
    test_a_failed_tool_says_so()
    test_other_errors_still_surface()
