"""
Self-check for the interface translation: PYTHONPATH=src python tests/test_i18n.py

English is the source language, French is complete. No GTK needed: the check
reads the source, so a string added to the UI without a French line fails here.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import ast
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "loquivox"
sys.path.insert(0, str(SRC.parent))

from loquivox import i18n  # noqa: E402
from loquivox.locale_fr import CATALOG  # noqa: E402

# Every file that puts words in front of the user. Log lines (print) are not
# among the things checked: diagnostics.py reads them, they stay English.
UI_FILES = (sorted((SRC / "ui").glob("*.py"))
            + [SRC / "services" / f"{n}.py" for n in (
                "board", "look", "mcp_tools", "memory", "research", "rules", "tts", "vad",
                "write", "postprocess", "live_talk")]
            + [SRC / "handlers" / "mode.py", SRC / "handlers" / "keyboard.py",
               SRC / "handlers" / "companion.py", SRC / "config.py"])
# Developer tooling with its own demo windows, not the product's interface.
UI_FILES = [f for f in UI_FILES if f.name not in ("companion_bubble_styles.py", "companion_bubble.py")]


def marked(path: Path):
    """(line, message) for every _("…") / N_("…") literal in the file."""
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(n, ast.Call) and getattr(n.func, "id", None) in ("_", "N_")
                and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            yield n.lineno, n.args[0].value


# 1. nothing marked is missing from the French catalogue
missing = [f"{f.name}:{line} {msg[:60]!r}" for f in UI_FILES for line, msg in marked(f)
           if msg not in CATALOG]
assert not missing, "untranslated:\n  " + "\n  ".join(missing)

# 2. no entry is dead, and each keeps its placeholders and markup intact
used = {msg for f in SRC.rglob("*.py") for _line, msg in marked(f)}
dead = [m[:60] for m in CATALOG if m not in used]
assert not dead, f"catalogue entries no longer used: {dead}"
for en, fr in CATALOG.items():
    for pattern in (r"\{[^}]*\}", r"</?[a-z]+>"):
        assert sorted(re.findall(pattern, en)) == sorted(re.findall(pattern, fr)), (en, fr)

# 3. a visible label handed to GTK as a bare literal is a missed string
WIDGETS = {"Label", "Button", "CheckButton", "MenuItem", "CheckMenuItem", "Expander"}
SETTERS = {"set_label", "set_tooltip_text", "set_placeholder_text", "set_title"}
bare = []
for f in sorted((SRC / "ui").glob("*.py")) + [SRC / "services" / "board.py"]:
    if f.name in ("companion_bubble_styles.py", "companion_bubble.py"):
        continue
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "attr", getattr(n.func, "id", ""))
        args = [k.value for k in n.keywords if k.arg == "label"] if name in WIDGETS else (
            n.args[:1] if name in SETTERS else [])
        bare += [f"{f.name}:{n.lineno} {a.value[:40]!r}" for a in args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.strip()
                 and a.value != "Loquivox"]
assert not bare, "not passed through _():\n  " + "\n  ".join(bare)

# 4. the function itself: English by default, French on request, the locale in Auto
assert i18n._("Quit") == "Quit"
assert i18n.set_language("fr") == "fr" and i18n._("Quit") == "Quitter"
assert i18n._("Not in the catalogue") == "Not in the catalogue"
assert i18n.set_language("en") == "en" and i18n._("Quit") == "Quit"
import os  # noqa: E402
for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
    os.environ.pop(var, None)
os.environ["LANG"] = "fr_FR.UTF-8"
assert i18n.set_language("auto") == "fr"
os.environ["LANG"] = "de_DE.UTF-8"
assert i18n.set_language("auto") == "en"          # a locale we do not ship
os.environ["LANG"] = "C"
assert i18n.set_language("auto") == "en"
assert i18n.set_language("klingon") == "en"
print(f"ok — {len(CATALOG)} messages, {len(UI_FILES)} files")
