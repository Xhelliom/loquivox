"""
The one translation function for everything the user reads in the UI.

English is the source language: ``_("Settings…")`` returns its argument
untouched unless a catalogue says otherwise. Catalogues are plain dicts
(``locale_<code>.py``) wrapped in stdlib ``gettext.NullTranslations`` — no
``.mo`` build step, no dependency. The log is NOT translated: ``print()``
stays English because ``diagnostics.py`` reads it.

The choice is ``language`` in settings.json: ``auto`` (follow the locale),
``en`` or ``fr``. ``set_language()`` applies it at once for everything built
afterwards; windows already on screen keep their words until reopened.
"""
from __future__ import annotations

import gettext
import importlib
import os

LANGUAGES = (("auto", "Auto"), ("en", "English"), ("fr", "Français"))
_CODES = {code for code, _label in LANGUAGES} - {"auto"}


class _Dict(gettext.NullTranslations):
    def __init__(self, table: dict) -> None:
        super().__init__()
        self._table = table

    def gettext(self, message: str) -> str:
        return self._table.get(message, message)


def N_(message: str) -> str:
    """Mark a string for the catalogue without translating it yet (module-level tables)."""
    return message


_current: gettext.NullTranslations = gettext.NullTranslations()
_code = "en"


def system_language() -> str:
    """The locale's language if we ship it, else English."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(var, "")
        if value:
            code = value.split(":")[0].split("_")[0].split(".")[0].lower()
            return code if code in _CODES else "en"
    return "en"


def catalog(code: str) -> dict:
    """The translation table of ``code`` ({} for English)."""
    if code == "en":
        return {}
    return importlib.import_module(f"loquivox.locale_{code}").CATALOG


def set_language(choice: str = "auto") -> str:
    """Switch to ``auto``/``en``/``fr``; returns the language really in use."""
    global _current, _code
    code = system_language() if choice == "auto" else choice
    if code not in _CODES:
        code = "en"
    _current = _Dict(catalog(code)) if code != "en" else gettext.NullTranslations()
    _code = code
    return code


def current_language() -> str:
    return _code


def _(message: str) -> str:
    return _current.gettext(message)


def saved_choice() -> str:
    """``language`` from settings.json (imported late: state imports config)."""
    from loquivox.state import SettingsManager
    value = SettingsManager.load().get("language", "auto")
    return value if value == "auto" or value in _CODES else "auto"


def init() -> str:
    return set_language(saved_choice())
