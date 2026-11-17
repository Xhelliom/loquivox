"""
Manual bench of the REAL GeminiLive with tools, against the Google API, no
physical microphone: synthesized French (OpenAI TTS) goes through ``_on_audio``
in real time, the playback callback is drained at real time, and a fake MCP
server that acts (tests/mcp_bench_server.py board) says what REALLY ran. Costs
tokens; needs GEMINI_API_KEY, OPENAI_API_KEY, ``.[gemini]``, ``.[mcp]``, openai, scipy.

    BENCH_AGENT_PROVIDER=groq BENCH_AGENT_MODEL=openai/gpt-oss-120b \\
    python tests/bench_gemini_tools.py yes [consult|native]   # an acting tool is held until a spoken yes
    python tests/bench_gemini_tools.py source [consult|native] # a board source is said between two turns

``yes``: asks "marque la carte 42 comme terminée", checks nothing acted before
a yes, then says "oui, vas-y" and checks the action ran afterwards (read from
the server's log, never from what the model says). ``source``: after a short
exchange, a standing-style source (``loads = "board"``) puts a notification
and ``push_result`` (polled like ``_talk_converse_server``) must get it said.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import numpy as np  # noqa: E402
from openai import OpenAI  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as cm  # noqa: E402

cm.ChatManager.add_message = staticmethod(lambda r, t="": print("  BUBBLE", r, t[:100]))
cm.ChatManager.stream = staticmethod(lambda r, t: None)
from bench_mcp import sandbox_memory, start_servers  # noqa: E402
from loquivox.services import mcp_tools, plugins  # noqa: E402
from loquivox.services.gemini_live import GeminiLive  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402

T0 = time.monotonic()


def log(*a):
    print(f"[{time.monotonic() - T0:6.1f}s]", *a, flush=True)


def tts(text):
    r = OpenAI().audio.speech.create(model="gpt-4o-mini-tts", voice="alloy",
                                     input=text, response_format="pcm")
    pcm = np.frombuffer(r.read(), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(pcm, 2, 3).astype(np.float32)   # 24k -> 16k


def until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def acted(logfile):
    rows = [json.loads(x) for x in Path(logfile).read_text().splitlines() if x.strip()] \
        if Path(logfile).exists() else []
    return [r for r in rows if r["acts"] and r["ok"]]


def main(scenario, mode):
    from loquivox.secrets import load_secrets
    from loquivox.services.ai import AIService
    from loquivox.state import STATE
    load_secrets()
    # The agent slot is the user's own (maybe a provider with no key here):
    # BENCH_AGENT_PROVIDER / BENCH_AGENT_MODEL override it for the bench only.
    STATE.ai_agent_provider = os.environ.get("BENCH_AGENT_PROVIDER", STATE.ai_agent_provider)
    STATE.ai_agent_model = os.environ.get("BENCH_AGENT_MODEL", STATE.ai_agent_model)
    print("agent:", AIService.agent_target())
    logfile = Path(tempfile.mkdtemp(prefix="gemini-tools-")) / "calls.jsonl"
    sandbox_memory()
    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "TALK_GEMINI_DELEGATION": mode})
    start_servers({"board": {"args": ["tests/mcp_bench_server.py", "board"]}}, logfile)
    if scenario == "source":   # a source that loads the board server, as the real one does
        fired = threading.Event()
        plugins.register(plugins.Plugin(
            name="bench_board_source", loads="board",
            watch=lambda put, stop: (fired.wait(120), put({"role": "system", "content":
                "Card 42 (« Refonte du login », loquivox) is blocked: waiting for a review."}),
                stop.wait(120))))
    session = TalkSession(write=False)
    STATE.talk_session = session   # what the acting tools read their spoken yes from
    talk = GeminiLive(session)
    talk._thread = threading.Thread(target=talk._run, daemon=True)
    talk._thread.start()
    talk._ready.wait(20)
    assert talk.error is None, talk.error
    log("open OK", talk._model, mode, "tools:", [d["name"] for d in
        talk._config().get("tools", [{}])[0].get("function_declarations", [])][:6])

    real_handle, ev = talk._handle, {"calls": [], "heard": [], "said": []}

    def handle(m):
        for c in getattr(getattr(m, "tool_call", None), "function_calls", None) or []:
            ev["calls"].append((time.monotonic(), c.name, dict(c.args or {})))
            log("CALL", c.name, dict(c.args or {}))
        sc = getattr(m, "server_content", None)
        if sc is not None:
            for k, key in (("input_transcription", "heard"), ("output_transcription", "said")):
                t = getattr(getattr(sc, k, None), "text", "")
                if t:
                    ev[key].append(t)
        real_handle(m)
    talk._handle = handle

    stop, feed = threading.Event(), []   # feed: float32 blocks waiting to be "spoken"

    def play():
        buf = bytearray(2048)
        while not stop.is_set():
            talk._on_play(buf, 1024, None, None)
            time.sleep(1024 / 24000)

    def mic():
        blk, i = 320, 0
        while not stop.is_set():
            time.sleep(blk / 16000)
            chunk = feed.pop(0) if feed else np.zeros(blk, np.float32)
            talk._on_audio(chunk.reshape(-1, 1), blk, None, None)
            i += 1

    def say(text):
        sig = tts(text)
        log("USER SAYS:", text)
        feed.extend(np.pad(sig, (0, (-len(sig)) % 320)).reshape(-1, 320))
        until(lambda: not feed, 60)

    def reply_over(timeout=60):
        n = len(ev["said"])
        until(lambda: len(ev["said"]) > n, timeout)
        until(lambda: talk._quiet() and talk.finished_speaking(), 30)
        time.sleep(1.0)

    threading.Thread(target=play, daemon=True).start()
    threading.Thread(target=mic, daemon=True).start()
    time.sleep(2)
    ok = True
    if scenario == "yes":
        say("Marque la carte 42 comme terminée sur le board.")
        reply_over()
        before = acted(logfile)
        log("ASSISTANT SAID:", "".join(ev["said"]))
        log("acted before the yes:", before)
        ok &= not before
        t_yes = time.monotonic()
        ran = False
        for n_yes in (1, 2):   # a hold exists only after an unconfirmed call: the first yes may only create it
            say("Oui, vas-y.")
            ran = until(lambda: bool(acted(logfile)), 25)
            log(f"yes #{n_yes}:", "acted" if ran else "not acted yet", acted(logfile),
                f"(+{time.monotonic() - t_yes:.1f}s after the first yes)")
            if ran:
                break
            reply_over()
        reply_over()
        log("ASSISTANT SAID IN TOTAL:", "".join(ev["said"]))
        ok &= ran
    else:
        say("Bonjour, dis-moi simplement bonjour.")
        reply_over()
        n = len(ev["said"])
        t_put = time.monotonic()
        fired.set()
        # polled like _talk_converse_server does, every 50 ms
        spoke = until(lambda: (talk.push_result(), len(ev["said"]) > n)[1], 90)
        reply_over()
        log("SOURCE SAID?", spoke, f"first words +{time.monotonic() - t_put:.1f}s after the notification")
        log("ASSISTANT SAID:", "".join(ev["said"][n:]))
        ok &= spoke and any(w in "".join(ev["said"][n:]).lower() for w in ("42", "login", "bloqu", "relecture", "review"))
    log("heard:", "".join(ev["heard"]))
    log("RESULT", "PASS" if ok else "FAIL")
    stop.set()
    talk.close()
    mcp_tools.stop()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "yes", sys.argv[2] if len(sys.argv) > 2 else "consult")
