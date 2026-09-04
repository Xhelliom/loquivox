"""
The research tool: a question handed to a better-read model, in the background.

The conversational models are fast and light, which is the point of them, and
it shows the moment a fact is needed. So both talk engines get ONE function,
``research(question)``: the model asks, the answer is fetched by a model that
can search the web, and the conversation goes on in the meantime. When the
answer lands, the conversation loop hands it back in as a system message and
the assistant says so — the model never blocks on it, the user never waits in
silence.

Two providers, one call each and no agent loop: OpenAI's Responses API with
its built-in ``web_search`` tool, or a Groq "compound" model that searches on
its own. Picked in Settings → Models.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import loquivox.config as config_module

#: what both engines are told the tool is for — wording shared on purpose
_DESCRIPTION = (
    "Look something up with a web-searching model when the conversation needs "
    "a fact you are not sure of: current events, prices, dates, versions, a "
    "definition, a person, a place. It runs in the background: say in one "
    "short sentence that you are looking it up, then carry on talking. The "
    "result arrives later as a system message."
)
_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The question to research, complete and self-contained, "
                           "in the user's language.",
        }
    },
    "required": ["question"],
}
#: Realtime sessions declare functions flat…
REALTIME_TOOL: Dict[str, Any] = {
    "type": "function", "name": "research",
    "description": _DESCRIPTION, "parameters": _PARAMETERS,
}
#: …chat completions wrap them.
CHAT_TOOL: Dict[str, Any] = {"type": "function", "function": {
    "name": "research", "description": _DESCRIPTION, "parameters": _PARAMETERS,
}}

#: the tool's immediate return value — the model speaks from this while the
#: real answer is on its way
STARTED: str = (
    "Research started in the background; it takes 5 to 15 seconds. Tell the "
    "user in one short sentence that you are looking it up, then continue the "
    "conversation. The result will arrive as a system message: when it does, "
    "say you have it and give it."
)

_PROMPT = (
    "You answer a question asked out loud in the middle of a conversation, so "
    "the answer will be READ ALOUD by a voice assistant: plain spoken prose, no "
    "markdown, no lists, no URLs, at most 120 words. Search the web when the "
    "question needs current or verifiable information. Give facts with their "
    "dates when they matter, name the source in words if it adds credibility, "
    "and say plainly when you could not find something. Answer in the language "
    "of the question."
)


_CITATION_RE = re.compile(r"\s*\(\s*\[[^\]]*\]\([^)]*\)\s*\)")   # ([site](url))
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")                     # [text](url) → text
_URL_RE = re.compile(r"https?://\S+")


def spoken(text: str) -> str:
    """
    Strip what a voice cannot say: markdown links and bare URLs. The prompt
    asks for none, and the web_search tool appends citations anyway.
    """
    text = _CITATION_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"(\d) \.(\d)", r"\1.\2", text)  # compound writes "3 .14 .7"
    return " ".join(_URL_RE.sub("", text).split())


def question_of(arguments: str) -> str:
    """The question in a tool call's JSON arguments, or "" if unreadable."""
    try:
        return str(json.loads(arguments or "{}").get("question", "")).strip()
    except (ValueError, AttributeError):
        return ""


def result_message(question: str, result: str) -> Dict[str, str]:
    """
    The research result as a system message for either engine.

    Kept in the session's turns too, so the writing pass and the rest of the
    conversation have the facts, not just the reply that mentioned them.
    """
    return {"role": "system", "content": (
        f"RESEARCH RESULT for « {question} »:\n{result}\n\n"
        "Tell the user you have the answer and give it in a few spoken "
        "sentences, then carry on with the conversation."
    )}


def run_research(question: str) -> str:
    """Ask the configured provider, blocking. Returns a spoken-ready text; never raises."""
    from loquivox.api import get_ai_client

    cfg = config_module.CFG
    started = time.time()
    try:
        if cfg.RESEARCH_PROVIDER == "openai":
            # Low reasoning effort on the gpt-5 family: measured at 16 s with
            # the default against 2 s for Groq, for the same one-paragraph
            # answer — a wait the conversation has to carry.
            extra = {"reasoning": {"effort": "low"}} if "gpt-5" in cfg.MODEL_RESEARCH else {}
            response = get_ai_client("openai").responses.create(
                model=cfg.MODEL_RESEARCH, tools=[{"type": "web_search"}],
                instructions=_PROMPT, input=question, **extra)
            text = (response.output_text or "").strip()
        else:
            # Groq's compound models search on their own; no tool to declare.
            response = get_ai_client("groq").chat.completions.create(
                model=cfg.MODEL_RESEARCH,
                messages=[{"role": "system", "content": _PROMPT},
                          {"role": "user", "content": question}])
            text = (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"⚠️  Research failed ({time.time() - started:.1f}s): {e}")
        return f"The research could not be done ({e.__class__.__name__}). Say so."
    text = spoken(text)
    print(f"🔎 Research ({time.time() - started:.1f}s): {text[:100]}"
          f"{'…' if len(text) > 100 else ''}")
    return text or "The research came back empty. Say so."


class ResearchDesk:
    """
    The questions in flight and the answers waiting to be spoken, for one
    session. ``ask`` returns at once; ``take`` never blocks — the conversation
    loops poll it between two turns, at the one moment a reply can be slipped
    in without cutting anyone off.
    """

    def __init__(self) -> None:
        self._results: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.pending: int = 0

    def ask(self, question: str) -> None:
        if not question:
            return
        self.pending += 1
        print(f"🔎 Research: {question}")
        from loquivox.managers.chat import ChatManager
        ChatManager.add_message("note", f"🔎 Recherche en cours : {question}")
        threading.Thread(target=self._work, args=(question,), daemon=True).start()

    def _work(self, question: str) -> None:
        result = run_research(question)
        self._results.put((question, result))
        self.pending -= 1

    def ready(self) -> bool:
        return not self._results.empty()

    def take(self) -> Optional[Tuple[str, str]]:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None


if __name__ == "__main__":
    assert question_of('{"question": " Who won? "}') == "Who won?"
    assert question_of("not json") == ""
    desk = ResearchDesk()
    assert not desk.ready() and desk.take() is None
    desk._results.put(("q", "r"))
    assert desk.ready() and desk.take() == ("q", "r") and not desk.ready()
    assert result_message("q", "r")["role"] == "system"
    assert spoken("Python 3.14.7 est sortie. ([python.org](https://x.y/z)) Voir https://a.b aussi.") \
        == "Python 3.14.7 est sortie. Voir aussi."
    assert spoken("selon [le site](https://x) officiel") == "selon le site officiel"
    assert spoken("la 3 .14 .7") == "la 3.14.7"
    print("✓ research desk OK")
