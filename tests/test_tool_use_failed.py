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

    def complete(messages, model):
        seen.append(messages)
        if len(seen) == 1:
            raise _Rejected(CALL)
        return "Voilà."
    assert _with(complete, None, lambda: AIService.complete([{"role": "user", "content": "x"}])) == "Voilà."
    assert seen[1][-1] == {"role": "system", "content": TEXT_ONLY}
    print("✓ a tool-less call that tried a tool is retried in text")


def test_other_errors_still_surface():
    def complete(messages, model):
        raise ValueError("boom")
    # safe_execute prints and returns None: the error is reported, not retried
    assert _with(complete, None, lambda: AIService.complete([])) is None
    print("✓ unrelated errors keep the usual path")


if __name__ == "__main__":
    test_a_readable_call_is_handed_over_as_a_real_one()
    test_without_tools_the_turn_is_asked_again_in_text()
    test_other_errors_still_surface()
