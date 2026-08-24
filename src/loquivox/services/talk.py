"""
Talk mode — a spoken conversation that ends in one generated text.

The other AI modes are one-shot: you say a thing, a text comes back. Talk mode
splits that in two. First you *discuss*: several spoken turns where the model
asks what it still needs to know (audience, tone, key facts) and you answer out
loud. Then, when you say you're done, the whole conversation becomes the brief
for a single generation pass, and the resulting text is handed over ready to
paste.

The conversation is kept here, not in the global history: a ten-turn spoken
briefing has no business landing in the F4 chat context (and vice versa). Only
the finished text goes to the shared histories.

Prompts come from the live config module (``TALK_SYSTEM_PROMPT``,
``TALK_GENERATE_PROMPT``, ``TALK_GENERATE_REQUEST``) so they can be overridden
in config.toml and reloaded without a restart. Every call is off the GTK main
thread — see ``handlers/mode.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import loquivox.config as config_module
from loquivox.services.ai import AIService


class TalkSession:
    """One spoken conversation, and the text it produces."""

    def __init__(self) -> None:
        #: the conversation so far, as API messages (user/assistant turns)
        self.turns: List[Dict[str, Any]] = []

    @property
    def user_turns(self) -> int:
        """How many times the user has spoken."""
        return sum(1 for t in self.turns if t["role"] == "user")

    def add_user(self, text: str) -> None:
        """Record a spoken turn without asking for a reply (the closing one)."""
        self.turns.append({"role": "user", "content": text})

    def reply(self, text: str) -> Optional[str]:
        """
        Add the user's spoken turn and return the assistant's spoken reply.

        Returns None if the call failed (``AIService`` swallows the error and
        the caller keeps the conversation alive); the user turn is kept either
        way, so nothing said is lost from the brief.
        """
        self.add_user(text)
        answer = AIService.complete(
            [{"role": "system", "content": config_module.CFG.TALK_SYSTEM_PROMPT}]
            + self.turns
        )
        if answer:
            self.turns.append({"role": "assistant", "content": answer})
        return answer

    def generate(self) -> Optional[str]:
        """
        Write the final text from the whole conversation.

        Same turns, different instructions: the conversation-phase system prompt
        (which forbids writing the text) is swapped for the generation one, and
        a closing user request is appended. Returns None if nothing was said, or
        if the call failed.
        """
        if not self.turns:
            return None
        cfg = config_module.CFG
        messages = (
            [{"role": "system", "content": cfg.TALK_GENERATE_PROMPT}]
            + self.turns
            + [{"role": "user", "content": cfg.TALK_GENERATE_REQUEST}]
        )
        result = AIService.complete(messages)
        return (result or "").strip() or None

    def brief(self, limit: int = 160) -> str:
        """One-line recap of what was asked for — the panel's instruction echo."""
        first = next((t["content"] for t in self.turns if t["role"] == "user"), "")
        first = " ".join(first.split())
        return first if len(first) <= limit else first[: limit - 1] + "…"

    def transcript(self) -> str:
        """The conversation as plain text, for the tray history."""
        return "\n".join(
            f"{'🗣️' if t['role'] == 'user' else '🤖'} {t['content']}"
            for t in self.turns
        )
