"""
Optional post-processing of dictated text via the Groq LLM.

After transcription, the raw text can be cleaned up, reformulated, or translated
before it is typed. This is opt-in (default off) and runs off the GTK thread
(in the transcription worker), so it never freezes the UI. On any failure it
returns the original text unchanged.

Modes (``[postprocess] mode`` in config.toml):
  - ``none``        — pass through (default)
  - ``correct``     — fix spelling/grammar/punctuation, same language
  - ``reformulate`` — polish, same language; prompt is editable via
                      ``[postprocess] reformulate_prompt`` (default is minimal,
                      intent-preserving — see ``config.DEFAULT_REFORMULATE_PROMPT``)
  - ``translate``   — translate into ``[postprocess] target_language``

Reads ``config.CFG`` through the module so settings changes apply live
(reload_config rebinds the module global).
"""
from __future__ import annotations

from typing import Optional

import loquivox.config as config_module
from loquivox.api import get_client
from loquivox.decorators import safe_execute

# Static task prompts. "reformulate" is NOT here — its prompt is user-editable
# and read live from config (config.POSTPROCESS_REFORMULATE_PROMPT). Each insists
# on returning ONLY the resulting text so the output can be typed verbatim.
_PROMPTS = {
    "correct": (
        "You fix dictated text: correct spelling, grammar, punctuation and "
        "capitalization. Keep the original language and meaning. Do not add or "
        "remove content. Output ONLY the corrected text, with no preamble."
    ),
    "translate": (
        "You are a translator. Translate the user's text into {lang}. Preserve "
        "meaning, tone and formatting. Output ONLY the translation, with no "
        "preamble."
    ),
}

# Modes that trigger an LLM call (everything else, incl. "none", passes through).
_VALID_MODES = frozenset({"correct", "reformulate", "translate"})

# Minimal ISO-639-1 → English name map for nicer translate prompts; unknown
# codes are passed through as-is (a full language name also works).
_LANG_NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    "hi": "Hindi", "pl": "Polish",
}


class PostProcessor:
    """LLM post-processing of dictation text (correct / reformulate / translate)."""

    @staticmethod
    def _lang_name(code: str) -> str:
        code = (code or "").strip()
        return _LANG_NAMES.get(code.lower(), code or "English")

    @staticmethod
    @safe_execute("PostProcess")
    def _run(text: str, system_prompt: str) -> Optional[str]:
        response = get_client().chat.completions.create(
            model=config_module.CFG.MODEL_CHAT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        out = (response.choices[0].message.content or "").strip()
        return out or None

    @classmethod
    def _system_prompt(cls, mode: str) -> str:
        """Resolve the system prompt for a mode (reformulate is user-editable)."""
        if mode == "reformulate":
            return config_module.CFG.POSTPROCESS_REFORMULATE_PROMPT
        if mode == "translate":
            return _PROMPTS["translate"].format(
                lang=cls._lang_name(config_module.CFG.POSTPROCESS_TARGET_LANG)
            )
        return _PROMPTS["correct"]

    @classmethod
    def process(cls, text: str) -> str:
        """Return post-processed text, or the original on none/failure."""
        mode = (config_module.CFG.POSTPROCESS_MODE or "none").strip().lower()
        if not text or mode not in _VALID_MODES:
            return text

        print(f"✨ Post-processing ({mode})…")
        result = cls._run(text, cls._system_prompt(mode))
        return result or text
