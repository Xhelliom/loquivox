"""
The research plugin: a question handed to a better-read model, in the background.

The conversational models are fast and light, which is the point of them, and
it shows the moment a fact is needed. So both talk engines can be given ONE
function, ``research(question)``: the model asks, the answer is fetched by a
model that can search the web, and the conversation goes on in the meantime.
When the answer lands, the conversation loop hands it back in as a system
message and the assistant says so — the model never blocks on it, the user
never waits in silence.

Two providers, one call each and no agent loop: OpenAI's Responses API with
its built-in ``web_search`` tool, or a Groq "compound" model that searches on
its own. Picked in Settings → Models.

This is also the reference plugin: everything below the prompts is the
quintuplet ``services/plugins.py`` asks for — the tool, the background work,
the answer as a system message, the config key that gates it, the Settings
card — and nothing in the conversation core mentions it by name.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict

import loquivox.config as config_module
from loquivox.services.plugins import Plugin, register

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


# --- the plugin -----------------------------------------------------------

def question_of(arguments: Dict[str, Any]) -> str:
    """The question in a tool call's arguments, stripped, or ""."""
    return str(arguments.get("question", "")).strip()


def _run(arguments: Dict[str, Any]) -> str:
    """The desk's background job: one question, one spoken-ready answer."""
    question = question_of(arguments)
    if not question:
        return ""
    print(f"🔎 Research: {question}")
    return run_research(question)


def result_message(arguments: Dict[str, Any], result: str) -> Dict[str, str]:
    """
    The research result as a system message for either engine.

    Kept in the session's turns too, so the writing pass and the rest of the
    conversation have the facts, not just the reply that mentioned them.
    """
    return {"role": "system", "content": (
        f"RESEARCH RESULT for « {question_of(arguments)} »:\n{result}\n\n"
        "Tell the user you have the answer and give it in a few spoken "
        "sentences, then carry on with the conversation."
    )}


def _settings(dialog, vbox, labels) -> None:
    """
    This plugin's card in Settings → Models, built with the dialog's own
    helpers so it lines up with every other group on the page.

    The widgets live in this closure rather than on the dialog class: a plugin
    owns its settings the way it owns its tool.
    """
    from gi.repository import Gtk

    cfg = config_module.CFG
    body = dialog._group(
        vbox, "Search",
        "The conversational models are quick and lightly read. When one of "
        "them needs a fact, it hands the question to this model, which can "
        "search the web, and keeps talking; the answer is given when it "
        "comes back. OpenAI's Responses API with web search, or a Groq "
        "compound model.")

    check = Gtk.CheckButton(label="Let the conversation look things up")
    check.set_active(bool(cfg.TALK_RESEARCH))
    dialog._field(body, "Research", check, labels)

    provider = Gtk.ComboBoxText()
    for pid in cfg.RESEARCH_PROVIDERS:
        provider.append(pid, {"groq": "Groq", "openai": "OpenAI"}.get(pid, pid))
    provider.set_active_id(cfg.RESEARCH_PROVIDER)
    dialog._field(body, "Provider", provider, labels)

    model = Gtk.ComboBoxText.new_with_entry()
    for mid in cfg.RESEARCH_DEFAULT_MODELS.values():
        model.append(mid, mid)
    model.get_child().set_text(cfg.MODEL_RESEARCH)
    dialog._field(body, "Model", model, labels)

    def _default_model(_combo) -> None:
        pid = provider.get_active_id() or "openai"
        model.get_child().set_text(cfg.RESEARCH_DEFAULT_MODELS.get(pid, ""))
    provider.connect("changed", _default_model)

    status = Gtk.Label()

    def _apply(_btn=None) -> None:
        """Write [models] research_provider/research and [talk] research."""
        from loquivox.config_io import ConfigWriteError, update_section

        pid = provider.get_active_id() or "openai"
        mid = model.get_child().get_text().strip()
        enabled = check.get_active()
        if enabled and not mid:
            status.set_markup("<small>⚠️ A model id is needed.</small>")
            return
        try:
            update_section("models", {"research_provider": pid, "research": mid})
            update_section("talk", {"research": enabled})
        except ConfigWriteError as e:
            status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        print(f"🔎 Research: {'on' if enabled else 'off'} — {pid} / {mid}")
        status.set_markup(
            f"<small>✓ {'Research on' if enabled else 'Research off'} — takes effect "
            "on the next talk session.</small>")

    dialog._actions(body, _apply, status)


PLUGIN = Plugin(
    name="research",
    description=(
        "Look something up with a web-searching model when the conversation needs "
        "a fact you are not sure of: current events, prices, dates, versions, a "
        "definition, a person, a place. It runs in the background: say in one "
        "short sentence that you are looking it up, then carry on talking. The "
        "result arrives later as a system message."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to research, complete and self-contained, "
                               "in the user's language.",
            }
        },
        "required": ["question"],
    },
    started=(
        "Research started in the background; it takes 5 to 15 seconds. Tell the "
        "user in one short sentence that you are looking it up, then continue the "
        "conversation. The result will arrive as a system message: when it does, "
        "say you have it and give it."
    ),
    run=_run,
    message=result_message,
    enabled=lambda: bool(config_module.CFG.TALK_RESEARCH),
    note=lambda args: (f"🔎 Recherche en cours : {question_of(args)}"
                       if question_of(args) else ""),
    result_note="🔎 Résultat reçu — l'assistant vous le donne",
    settings=_settings,
)
register(PLUGIN)


if __name__ == "__main__":
    assert question_of({"question": " Who won? "}) == "Who won?"
    assert question_of({}) == ""
    assert PLUGIN.chat_tool["function"]["name"] == "research"
    assert PLUGIN.realtime_tool["name"] == "research"
    assert result_message({"question": "q"}, "r")["role"] == "system"
    assert "q" in result_message({"question": "q"}, "r")["content"]
    assert _run({"question": ""}) == "", "an empty question reached the provider"
    assert PLUGIN.note({"question": "x"}) and not PLUGIN.note({})
    assert spoken("Python 3.14.7 est sortie. ([python.org](https://x.y/z)) Voir https://a.b aussi.") \
        == "Python 3.14.7 est sortie. Voir aussi."
    assert spoken("selon [le site](https://x) officiel") == "selon le site officiel"
    assert spoken("la 3 .14 .7") == "la 3.14.7"
    print("✓ research plugin OK")
