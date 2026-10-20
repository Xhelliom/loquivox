"""
Self-check for settings persistence, no GTK window needed:

    PYTHONPATH=src python tests/test_settings_persist.py

Two threads write in a loop; no key may be lost, and an unreadable file
is never rewritten with fewer keys.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import sounddevice  # noqa: F401
except ImportError:  # state.py imports it for AppState only; not needed here
    sys.modules["sounddevice"] = type(sys)("sounddevice")

from loquivox.config import CFG  # noqa: E402
from loquivox.state import SettingsManager  # noqa: E402

N = 300
SettingsManager.update("toggle_mode", True)
SettingsManager.update("tts_voice", "x")

def writer(prefix):
    for i in range(N):
        SettingsManager.update(f"{prefix}{i}", i)

ts = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b")]
[t.start() for t in ts]; [t.join() for t in ts]
data = json.loads(CFG.SETTINGS_FILE.read_text())
assert data["toggle_mode"] is True and data["tts_voice"] == "x", "base keys lost"
assert all(data[f"{p}{i}"] == i for p in "ab" for i in range(N)), "a written key was lost"
assert not list(CFG.SETTINGS_FILE.parent.glob("settings.json.*")), "temp file left behind"

CFG.SETTINGS_FILE.write_text("{ torn")
SettingsManager.update("k", 1)  # must log, not raise
assert CFG.SETTINGS_FILE.read_text() == "{ torn", "unreadable file was overwritten"
print("ok")
