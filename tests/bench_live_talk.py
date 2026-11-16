"""
Manual bench of the REAL LiveTalk (gpt-live-1) against the OpenAI API, no physical
microphone: a synthesized French sentence (OpenAI TTS, 24 kHz native) is fed through
``_on_audio`` in real time after 2 s of silence, then zeros. Costs tokens, needs
OPENAI_API_KEY — so it is not a self-check. Run with a clean HOME for default config.

    python tests/bench_live_talk.py plain   # latency end of sentence -> first sound, turns, finished_speaking
    python tests/bench_live_talk.py int     # 2nd sentence over the reply -> gate opens, speakers flushed, 2nd answer
"""
import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import numpy as np
from openai import OpenAI
import loquivox.managers.chat as cm
cm.ChatManager.add_message = staticmethod(lambda r, t="": print("  BUBBLE", r, t[:90]))
cm.ChatManager.stream = staticmethod(lambda r, t: None)
from loquivox.services.live_talk import LiveTalk, RATE
from loquivox.services.talk import TalkSession

def tts(text):
    r = OpenAI().audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="pcm")
    return np.frombuffer(r.read(), dtype=np.int16).astype(np.float32) / 32768   # 24 kHz

S1 = tts("Bonjour, peux-tu me raconter en trois phrases l'histoire de la tour Eiffel ?")
S2 = tts("Stop, attends, j'ai une autre question : quelle heure est-il à Tokyo quand il est midi à Paris ?")

def run(interrupt):
    t = LiveTalk(TalkSession(write=False))
    ev = {}
    real_audio = t._on_play
    real_flush = t._flush_audio
    def flush():
        ev.setdefault("flushed", time.monotonic()); real_flush()
    t._flush_audio = flush
    real_handle = t._handle
    def h(e):
        ty = getattr(e, "type", "")
        real_handle(e)
    t._handle = h
    t._thread = threading.Thread(target=t._run, daemon=True); t._thread.start()
    t._ready.wait(20); assert t.error is None, t.error
    assert t._started.is_set(); print("open OK", t._model, t._voice)
    stop = threading.Event()
    def play():
        buf = bytearray(2048)
        while not stop.is_set():
            t._on_play(buf, 1024, None, None); time.sleep(1024 / RATE)
    threading.Thread(target=play, daemon=True).start()
    def mic():
        blk = 480; sig = np.concatenate([np.zeros(RATE * 2, np.float32), S1])
        t0 = time.monotonic(); ev["t0"] = t0; i = 0; sent2 = False
        while not stop.is_set():
            time.sleep(max(0, t0 + i * blk / RATE - time.monotonic()))
            chunk = sig[i*blk:(i+1)*blk] if i * blk < len(sig) else np.zeros(blk, np.float32)
            if i * blk >= len(sig) and "end1" not in ev: ev["end1"] = time.monotonic()
            if interrupt and "first" in ev and not sent2 and time.monotonic() - ev["first"] > 1.5:
                sent2 = True; ev["s2"] = time.monotonic(); sig = S2; i = 0; t0 = time.monotonic(); continue
            chunk = np.pad(chunk, (0, blk - len(chunk)))
            t._on_audio(chunk.reshape(-1, 1), blk, None, None); i += 1
            t.tick()
            # first AUDIBLE reply frame (the server streams silent frames all along)
            if t._last_audio and "first" not in ev and t._last_audio > ev.get("t0", 1e18): ev["first"] = t._last_audio
    threading.Thread(target=mic, daemon=True).start()
    deadline = time.monotonic() + 60; qs = None
    while time.monotonic() < deadline:
        t.tick()
        asst = [m for m in t._session.turns if m["role"] == "assistant"]
        qs = qs if (t._quiet() and "first" in ev) else None
        qs = qs or (time.monotonic() if (t._quiet() and "first" in ev) else None)
        if len(asst) >= (2 if interrupt else 1) and qs and time.monotonic() - qs > 2.5: break
        time.sleep(0.05)
    t.tick(); time.sleep(1.5); t.tick()
    out = {"first_ms_after_end": round((ev["first"] - ev["end1"]) * 1000) if "first" in ev and "end1" in ev else None,
           "quiet": t._quiet(), "finished_speaking": t.finished_speaking(),
           "turns": [(m["role"], m["content"][:80]) for m in t._session.turns], "error": str(t.error)}
    if interrupt:
        out["gate_opened_flush_ms_after_s2"] = round((ev["flushed"] - ev["s2"]) * 1000) if "flushed" in ev and "s2" in ev else None
    stop.set(); t.close(); time.sleep(1)
    return out
print(run(sys.argv[1] == "int"))
