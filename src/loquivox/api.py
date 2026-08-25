"""
Chat/vision API clients — lazily initialized, thread-safe.

Clients are created on first use (not at import time) so a missing key no
longer crashes the whole service at startup: the tray stays up and the error
surfaces when transcription/AI is actually invoked.

Groq is the default provider; OpenAI speaks the same chat-completions dialect,
so ``get_ai_client()`` returns whichever one the user picked and the callers in
``services/ai.py`` stay identical either way.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from groq import Groq

_client: Optional[Groq] = None
_lock = threading.Lock()


class GroqKeyMissing(RuntimeError):
    """Raised when GROQ_API_KEY is unset and the client is needed."""


def has_api_key() -> bool:
    """True if a Groq API key is present in the environment."""
    return bool(os.environ.get("GROQ_API_KEY"))


def get_client() -> Groq:
    """
    Return the shared Groq client, creating it on first use.

    Thread-safe via double-checked locking. Raises ``GroqKeyMissing`` if the
    API key is absent — callers (wrapped in @safe_execute) should surface this
    rather than crash.
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                api_key = os.environ.get("GROQ_API_KEY")
                if not api_key:
                    raise GroqKeyMissing(
                        "GROQ_API_KEY is not set. Add it to your environment "
                        "(e.g. ~/.config/environment.d/loquivox.conf)."
                    )
                _client = Groq(api_key=api_key)
    return _client


_ai_clients: Dict[str, Any] = {}


class OpenAIKeyMissing(RuntimeError):
    """Raised when OPENAI_API_KEY is unset and the OpenAI client is needed."""


def get_ai_client(provider: str):
    """
    The chat/vision client for ``provider`` ("groq" or "openai"), cached.

    Both SDKs expose the same ``chat.completions.create`` and ``models.list``,
    which is why the AI service never has to know which one it holds.
    """
    if provider != "openai":
        return get_client()
    with _lock:
        client = _ai_clients.get("openai")
        if client is None:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise OpenAIKeyMissing(
                    "OPENAI_API_KEY is not set — add it in Settings → API Keys."
                )
            from openai import OpenAI
            client = _ai_clients["openai"] = OpenAI(api_key=api_key)
        return client
