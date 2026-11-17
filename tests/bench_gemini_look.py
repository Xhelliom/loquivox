"""
Manual bench of the REAL GeminiLive looking at the screen on request, no physical
microphone (French OpenAI TTS fed through ``_on_audio``). Same cadence with and
without a look, so the cost of a look is the difference of the two runs' usage.
Costs tokens; needs GEMINI_API_KEY, OPENAI_API_KEY, ``.[gemini]``, a Wayland/X11 window.

    python tests/bench_gemini_look.py [--look] [minutes=5]

--look: at 30 s ``look()`` sends the real active window (the production ``_frame()``),
then asks "describe my window". Prints every frame sent (the journal), the reply, and
the last ``usage_metadata`` by modality. The frame is saved next to the log.
"""
import os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import numpy as np
from scipy.signal import resample_poly
import loquivox.managers.chat as cm
cm.ChatManager.add_message = staticmethod(lambda r, t="": None)
cm.ChatManager.stream = staticmethod(lambda r, t: None)
from loquivox.services.gemini_live import GeminiLive
from loquivox.services.talk import TalkSession

LOOK = "--look" in sys.argv
MINUTES = float(next((a for a in sys.argv[1:] if not a.startswith("--")), 5))
T0 = time.monotonic()
def log(*a): print(f"[{time.monotonic() - T0:7.1f}s]", *a, flush=True)

def say(text):
    from openai import OpenAI
    r = OpenAI().audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=text,
                                     response_format="pcm")
    pcm = np.frombuffer(r.read(), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(pcm, 2, 3).astype(np.float32)

CHAT = say("Dis-moi simplement un chiffre entre un et dix.")
WHAT = say("Décris-moi en deux phrases ce que tu vois dans ma fenêtre.")

t = GeminiLive(TalkSession(write=False))
usage = {}
real_handle, real_frame = t._handle, t._frame
def handle(m):
    u = getattr(m, "usage_metadata", None)
    if u is not None: usage["last"] = u
    real_handle(m)
t._handle = handle
def frame():
    f = real_frame()
    if f:
        path = f"/tmp/bench_gemini_look.{f['mime_type'].split('/')[1]}"
        open(path, "wb").write(f["data"]); log("frame", f["mime_type"], len(f["data"]), "bytes ->", path)
    return f
t._frame = frame
t._thread = threading.Thread(target=t._run, daemon=True); t._thread.start()
t._ready.wait(20); assert t.error is None, t.error
log("open OK", t._model, "look" if LOOK else "no look")
stop = threading.Event()
def play():
    buf = bytearray(2048)
    while not stop.is_set():
        t._on_play(buf, 1024, None, None); time.sleep(1024 / 24000)
threading.Thread(target=play, daemon=True).start()

BLK = 320
state = {"sig": None, "i": 0, "next": 10, "looked": False, "asked": False}
def mic():
    t0 = time.monotonic(); n = 0
    while not stop.is_set():
        time.sleep(max(0, t0 + n * BLK / 16000 - time.monotonic())); n += 1
        el = time.monotonic() - T0
        if state["sig"] is None:
            if LOOK and not state["looked"] and el >= 30:
                state["looked"] = True; log("look() ->", t.look())
            elif LOOK and state["looked"] and not state["asked"] and el >= 36:
                state["asked"] = True; state["sig"] = WHAT; state["i"] = 0; log("asking what it sees")
            elif el >= state["next"]:
                state["next"] = el + 60; state["sig"] = CHAT; state["i"] = 0
        chunk = np.zeros(BLK, np.float32)
        if state["sig"] is not None:
            part = state["sig"][state["i"]:state["i"] + BLK]; chunk[:len(part)] = part; state["i"] += BLK
            if state["i"] >= len(state["sig"]): state["sig"] = None
        t._on_audio(chunk.reshape(-1, 1), BLK, None, None)
threading.Thread(target=mic, daemon=True).start()

while t.error is None and time.monotonic() < T0 + MINUTES * 60: time.sleep(0.2)
stop.set(); t.close(); time.sleep(0.5)
for m in t._session.turns: log(m["role"], m["content"][:200])
u = usage.get("last")
log("USAGE", u)
sys.exit(0 if t.error is None else 1)
