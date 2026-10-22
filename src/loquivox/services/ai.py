"""
AI chat and vision completion service.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from loquivox.api import get_ai_client
from loquivox.config import CFG
from loquivox.decorators import safe_execute
from loquivox.state import STATE


TEXT_ONLY = ("Do not call any tool: answer in plain text only, in the "
             "user's language.")


def _failed_call(error: Exception) -> Optional[tuple]:
    """
    ``(name, arguments)`` the model tried to call when the provider rejected
    the generation with ``tool_use_failed`` (Groq: "Tool choice is none, but
    model called a tool"), else None. ``name`` is "" if it cannot be read.
    """
    body = getattr(error, "body", None)
    body = body.get("error", body) if isinstance(body, dict) else {}
    if body.get("code") != "tool_use_failed":
        return None
    raw = str(body.get("failed_generation") or "")
    try:
        data = json.loads(raw)
        return str(data.get("name", "")), json.dumps(data.get("arguments") or {})
    except (ValueError, AttributeError):
        m = re.search(r"<function=([\w-]+)[ >]*(\{.*?\})?\s*(?:</function>|$)", raw, re.S)
        return (m.group(1), m.group(2) or "{}") if m else ("", "{}")


class AIService:
    """AI chat and vision completion service."""

    @staticmethod
    def _create(messages: List[Dict[str, Any]], model: str,
                provider: Optional[str] = None, **extra):
        """
        Start a completion through the provider the user picked.

        The one place that knows the provider's quirks: Groq's reasoning models
        otherwise answer with their <think> block inline (qwen does), which
        would be typed at the cursor and read aloud, and "hidden" is Groq-only
        so it is never sent to OpenAI.
        """
        provider = provider or STATE.ai_provider
        kwargs = {"reasoning_format": "hidden"} if provider == "groq" else {}
        return get_ai_client(provider).chat.completions.create(
            model=model, messages=messages, **kwargs, **extra
        )

    @staticmethod
    def _complete(messages: List[Dict[str, Any]], model: str,
                  provider: Optional[str] = None) -> Optional[str]:
        """One completion, waited for."""
        return AIService._create(messages, model, provider).choices[0].message.content

    @staticmethod
    def agent_target() -> tuple:
        """
        ``(provider, model)`` for the tool agent, read at every call so Settings
        applies without a restart. Unset follows the chat. A model id belongs to
        its provider, so a provider of its own without a model falls back to the
        chat's pair whole rather than sending it ids it has never heard of.
        """
        return AIService._role_target(STATE.ai_agent_provider, STATE.ai_agent_model)

    @staticmethod
    def judge_target() -> tuple:
        """``(provider, model)`` for the memory judge; the same fallback as the agent's."""
        return AIService._role_target(STATE.ai_judge_provider, STATE.ai_judge_model)

    @staticmethod
    def _role_target(own_provider: str, own_model: str) -> tuple:
        provider = own_provider or STATE.ai_provider
        model = own_model
        if not model:
            if provider != STATE.ai_provider:
                return STATE.ai_provider, STATE.ai_chat_model
            model = STATE.ai_chat_model
        return provider, model

    @staticmethod
    def build_messages(user_content: str) -> List[Dict[str, Any]]:
        """Build API messages with system prompt and conversation history."""
        messages = [{"role": "system", "content": CFG.SYSTEM_PROMPT}]
        messages.extend(STATE.conversation_history)
        messages.append({"role": "user", "content": user_content})
        return messages

    @staticmethod
    @safe_execute("AI Chat")
    def chat(prompt: str) -> Optional[str]:
        """Send chat completion request."""
        return AIService._complete(AIService.build_messages(prompt),
                                   STATE.ai_chat_model)

    @staticmethod
    def _stream(messages: List[Dict[str, Any]], model: str,
                on_delta: Callable[[str], None],
                tool_calls_out: Optional[List[Dict[str, str]]] = None,
                provider: Optional[str] = None,
                **extra) -> str:
        """
        The same completion, handed over sentence by sentence as it is written.

        ``on_delta`` receives the answer SO FAR, not the increment: every caller
        wants to display the whole thing, and accumulating here means the UI
        can drop a callback (it throttles) without losing text.
        """
        text = ""
        calls: Dict[int, Dict[str, str]] = {}
        for chunk in AIService._create(messages, model, provider, stream=True, **extra):
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            for call in getattr(delta, "tool_calls", None) or []:
                # A tool call streams like text: the name in one chunk, the
                # JSON arguments spread over the next ones. Reassembled here
                # so the caller gets whole calls, never fragments.
                slot = calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                slot["id"] = slot["id"] or (call.id or "")
                if call.function is not None:
                    slot["name"] = slot["name"] or (call.function.name or "")
                    slot["arguments"] += call.function.arguments or ""
            if delta.content:
                text += delta.content
                on_delta(text)
        if tool_calls_out is not None:
            tool_calls_out.extend(calls[i] for i in sorted(calls))
        return text

    @staticmethod
    @safe_execute("AI Completion")
    def complete(messages: List[Dict[str, Any]],
                 model: Optional[str] = None,
                 on_delta: Optional[Callable[[str], None]] = None,
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tool_calls_out: Optional[List[Dict[str, str]]] = None,
                 provider: Optional[str] = None) -> Optional[str]:
        """
        Raw chat completion for callers that build their own message list.

        Unlike ``chat``, this ignores the global conversation history and system
        prompt — talk mode keeps its conversation to itself, so a long spoken
        session never pollutes (or gets polluted by) the F4 chat history.

        With ``on_delta`` the answer is streamed to that callback as it comes;
        the return value is the same either way, so a caller that only wants
        the finished text is unaffected — and a failed stream falls back to the
        error path of ``safe_execute`` exactly like a failed one-shot call.
        """
        model = model or STATE.ai_chat_model
        extra = {"tools": tools} if tools else {}
        try:
            if on_delta is None and not tools:
                return AIService._complete(messages, model, provider=provider)
            return AIService._stream(messages, model, on_delta or (lambda _t: None),
                                     tool_calls_out, provider=provider, **extra)
        except Exception as e:
            # The model called a tool this request did not offer (or garbled
            # the call) and the provider threw the whole generation away. A
            # turn must not die of that: a call we can read is handed to the
            # caller as a real one; anything else is asked again, in text.
            failed = _failed_call(e)
            if failed is None:
                raise
            name, arguments = failed
            print(f"⚠️  tool_use_failed ({name or '?'}) — recovering")
            if tool_calls_out is not None and tools and name:
                tool_calls_out.append({"id": "recovered", "name": name,
                                       "arguments": arguments})
                return ""
            return AIService._complete(
                messages + [{"role": "system", "content": TEXT_ONLY}], model, provider)

    @staticmethod
    def vision(prompt: str, image_base64: str) -> Optional[str]:
        """Send vision completion request with image, on the vision provider.

        Raises on an HTTP error: the caller says it, a bare None would read as
        "the model answered nothing".
        """
        messages = AIService.build_messages(prompt)
        # Replace last user message with multimodal content
        messages[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ]
        }
        return AIService._complete(messages, STATE.ai_vision_model,
                                   STATE.ai_vision_provider)
