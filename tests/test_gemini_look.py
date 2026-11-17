"""
Gemini Live looks at the screen on request only: a few JPEG frames at 1/s
(S / look_at_screen), never a standing stream. No network, no GTK, no SDK:

    python tests/test_gemini_look.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.services.gemini_live import GeminiLive  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402


class FakeConn:
    def __init__(self):
        self.video = []

    async def send_realtime_input(self, **kw):
        if "video" in kw:
            self.video.append(kw["video"])


def _live(seconds=2):
    t = GeminiLive(TalkSession(write=False))
    t._loop = asyncio.new_event_loop()
    threading.Thread(target=t._loop.run_forever, daemon=True).start()
    t._conn = FakeConn()
    t._frame = lambda: {"data": b"\xff\xd8jpeg", "mime_type": "image/jpeg"}
    t._look_gap = 0.05   # 1 s in real life
    return t, seconds


def test_defaults_and_toml():
    cfg = config_module.Config()
    assert cfg.TALK_GEMINI_LOOK_SEC == 3 and cfg.TALK_GEMINI_RESOLUTION == "low"
    config_module.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    config_module.CONFIG_FILE.write_text(
        '[talk]\ngemini_look_sec = 5\ngemini_resolution = "high"\n', encoding="utf-8")
    try:
        cfg = config_module._build_config()
    finally:
        config_module.CONFIG_FILE.unlink()
    assert (cfg.TALK_GEMINI_LOOK_SEC, cfg.TALK_GEMINI_RESOLUTION) == (5, "high")


def test_nothing_is_sent_unless_asked():
    t, _ = _live()
    time.sleep(0.3)
    assert t._conn.video == []
    assert t._config()["media_resolution"] == "MEDIA_RESOLUTION_LOW"


def test_look_sends_exactly_look_sec_frames_then_stops():
    t, _ = _live()
    old = config_module.CFG
    config_module.CFG = type(old)(TALK_GEMINI_LOOK_SEC=3)
    try:
        assert t.look()
        time.sleep(0.6)
    finally:
        config_module.CFG = old
    assert len(t._conn.video) == 3, len(t._conn.video)
    assert t._conn.video[0]["mime_type"] == "image/jpeg"


def test_look_plugin_sends_frames_instead_of_describing():
    from loquivox.services import look
    from loquivox.state import STATE

    t, _ = _live()
    STATE.talk_session = t._session
    t._session.live_look = t.look
    try:
        assert look._run({}) == look.FRAMES
        time.sleep(0.2)
    finally:
        STATE.talk_session = None
    assert t._conn.video, "no frame sent"
    assert "frames" in look.result_message({}, look.FRAMES)["content"]


def test_failed_capture_sends_nothing():
    t, _ = _live()
    t._frame = lambda: None
    t.look()
    time.sleep(0.3)
    assert t._conn.video == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
