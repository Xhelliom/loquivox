"""
Manual bench of the REAL GeminiLive held past its ~10 min connection limit, no
physical microphone: French sentences (OpenAI TTS) fed through ``_on_audio`` in real
time, zeros between them. Checks the real ``go_away``, the reopening with the
resumption handle, that a question after it recalls a turn from before it, and that
``error`` stays None. Costs tokens, needs GEMINI_API_KEY, OPENAI_API_KEY, ``.[gemini]``.

    python tests/bench_gemini_long.py [minutes=16] [--force-at SECONDS]

Google does not always send a go_away within 16 min (compression on). ``--force-at``
then raises the flag a real go_away would: the reconnection path runs for real, with
a real handle, against the real server — what it does not prove is Google's own signal.
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

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else 16.0
FORCE_AT = float(sys.argv[sys.argv.index("--force-at") + 1]) if "--force-at" in sys.argv else None

T0 = time.monotonic()
def log(*a): print(f"[{time.monotonic() - T0:7.1f}s]", *a, flush=True)

def say(text):
    from openai import OpenAI
    r = OpenAI().audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=text,
                                     response_format="pcm")
    pcm = np.frombuffer(r.read(), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(pcm, 2, 3).astype(np.float32)   # 24k -> 16k

REMEMBER = say("Je voudrais que tu mémorises le mot cerisier. Confirme-moi en une phrase.")
CHAT = say("Dis-moi simplement un chiffre entre un et dix.")
RECALL = say("Quel est le mot que je t'ai demandé de mémoriser au début de la conversation ?")

t = GeminiLive(TalkSession(write=False))
ev = {"go_away": None, "gone": None, "back": None, "dropped": 0, "recalled_at": None, "audio": []}
real_handle, real_open = t._handle, t._open
def handle(m):
    kinds = [k for k in ("go_away", "session_resumption_update", "setup_complete", "usage_metadata")
             if getattr(m, k, None) is not None]
    sc0 = getattr(m, "server_content", None)
    heard = getattr(getattr(sc0, "input_transcription", None), "text", "")
    if heard: log("HEARD", repr(heard))
    if kinds and kinds != ["usage_metadata"]: log("msg", kinds)
    if getattr(m, "go_away", None) is not None and ev["go_away"] is None:
        ev["go_away"] = time.monotonic(); log("GO_AWAY", m.go_away)
    if getattr(m, "session_resumption_update", None) is not None and ev.get("h0") is None \
            and getattr(m.session_resumption_update, "new_handle", None):
        ev["h0"] = time.monotonic(); log("first resumption handle")
    sc = getattr(m, "server_content", None)
    if sc is not None and getattr(sc, "model_turn", None):
        ev["audio"].append(time.monotonic())
    real_handle(m)
t._handle = handle
def opened():
    cm_ = real_open(); log("opening connection (handle:", bool(t._handle_token), ")"); return cm_
t._open = opened
orig_set = type(t)._conn if False else None

t._thread = threading.Thread(target=t._run, daemon=True); t._thread.start()
t._ready.wait(20); assert t.error is None, t.error
log("open OK", t._model)

stop = threading.Event()
def play():
    buf = bytearray(2048)
    while not stop.is_set():
        t._on_play(buf, 1024, None, None); time.sleep(1024 / 24000)
def watch():   # how long the microphone had nowhere to go
    was = True
    while not stop.is_set():
        up = t._conn is not None
        if was and not up: ev["gone"] = time.monotonic()
        if up and not was: ev["back"] = time.monotonic(); log("connection back")
        was = up; time.sleep(0.005)
threading.Thread(target=play, daemon=True).start()
threading.Thread(target=watch, daemon=True).start()

BLK = 320
queue = [(2, REMEMBER)]
state = {"sig": None, "i": 0, "recalled": False, "chat_at": 60}
def mic():
    t0 = time.monotonic(); n = 0
    while not stop.is_set():
        time.sleep(max(0, t0 + n * BLK / 16000 - time.monotonic())); n += 1
        el = time.monotonic() - T0
        if state["sig"] is None:
            if queue and el >= queue[0][0]: state["sig"] = queue.pop(0)[1]; state["i"] = 0
            elif not queue and ev["back"] and el > ev["back"] - T0 + 30 and not state["recalled"]:
                state["recalled"] = True; state["sig"] = RECALL; state["i"] = 0; log("asking the recall question")
            elif not queue and el >= state["chat_at"]:
                state["chat_at"] = el + 90; state["sig"] = CHAT; state["i"] = 0
        chunk = np.zeros(BLK, np.float32)
        if state["sig"] is not None:
            part = state["sig"][state["i"]:state["i"] + BLK]; chunk[:len(part)] = part; state["i"] += BLK
            if state["i"] >= len(state["sig"]): state["sig"] = None
        t._on_audio(chunk.reshape(-1, 1), BLK, None, None)
threading.Thread(target=mic, daemon=True).start()

# at least MINUTES, and until the recall has been answered; give up at MINUTES + 10
# when Google never sends a go_away
deadline, hard = T0 + MINUTES * 60, T0 + (MINUTES + 10) * 60
while t.error is None and time.monotonic() < hard:
    if time.monotonic() >= deadline and state["recalled"] and ev["recalled_at"] \
            and time.monotonic() - ev["recalled_at"] > 25 and t._quiet():
        break
    if state["recalled"] and ev["recalled_at"] is None: ev["recalled_at"] = time.monotonic()
    if FORCE_AT and not ev["go_away"] and time.monotonic() - T0 > FORCE_AT:
        ev["go_away"] = time.monotonic(); ev["forced"] = True; log("FORCED go_away"); t._goaway = True
    time.sleep(0.5)
stop.set(); t.close(); time.sleep(0.5)

turns = [(m["role"], m["content"]) for m in t._session.turns]
for r, c in turns: log(r, c[:110])
gap = ev["back"] - ev["gone"] if ev["gone"] and ev["back"] else None
last_after = max((a for a in ev["audio"] if ev["back"] and a > ev["back"]), default=None)
res = {"go_away_at_s": round(ev["go_away"] - T0, 1) if ev["go_away"] else None, "forced": ev.get("forced", False),
       "reconnect_s": round(gap, 2) if gap else None, "error": t.error,
       "recalled_cerisier": any("cerisier" in turns[i + 1][1].lower() for i, (r, c) in enumerate(turns[:-1])
                               if r == "user" and "mémoriser" in c and turns[i + 1][0] == "assistant"),
       "ran_s": round(time.monotonic() - T0)}
log(res)
sys.exit(0 if res["go_away_at_s"] and res["recalled_cerisier"] and t.error is None else 1)
