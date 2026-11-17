"""
Manual bench of the REAL GeminiLive against the Google API, no physical microphone:
a synthesized French sentence (OpenAI TTS) is fed through ``_on_audio`` in real time
after 2 s of silence, then zeros. Costs tokens, needs GEMINI_API_KEY, OPENAI_API_KEY and
``.[gemini]`` — so it is not a self-check.

    python tests/bench_gemini_live.py plain   # latency end of sentence -> first sound, turn_complete, finished_speaking
    python tests/bench_gemini_live.py int     # a second sentence during the reply -> interrupted, buffer emptied, 2nd answer
"""
import os, sys, time, threading, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import numpy as np
from scipy.signal import resample_poly
from openai import OpenAI
import loquivox.managers.chat as cm
cm.ChatManager.add_message = staticmethod(lambda r, t="": print("  BUBBLE", r, t[:90]))
cm.ChatManager.stream = staticmethod(lambda r, t: None)
from loquivox.services.gemini_live import GeminiLive
from loquivox.services.talk import TalkSession

def tts(text):
    r = OpenAI().audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="pcm")
    pcm = np.frombuffer(r.read(), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(pcm, 2, 3).astype(np.float32)   # 24k -> 16k

S1 = tts("Bonjour, peux-tu me raconter en trois phrases l'histoire de la tour Eiffel ?")
S2 = tts("Stop, attends, j'ai une autre question : quelle heure est-il à Tokyo quand il est midi à Paris ?")
print("sentences", len(S1)/16000, len(S2)/16000)

def run(interrupt):
    t = GeminiLive(TalkSession(write=False))
    ev = {}
    real = t._handle
    def h(m):
        sc = getattr(m, "server_content", None)
        now = time.monotonic()
        if sc is not None:
            if getattr(sc, "model_turn", None) and "first" not in ev: ev["first"] = now
            if getattr(sc, "model_turn", None): ev.setdefault("n", 0); ev["n"] += 1
            if getattr(sc, "interrupted", False): ev["interrupted"] = now
            if getattr(sc, "turn_complete", False): ev.setdefault("tc", now); ev["ntc"] = ev.get("ntc",0)+1
            it = getattr(sc, "input_transcription", None)
            if it and it.text: ev.setdefault("heard", "")
            if it and it.text: ev["heard"] += it.text
        real(m)
    t._handle = h
    t._thread = threading.Thread(target=t._run, daemon=True); t._thread.start()
    t._ready.wait(20); assert t.error is None, t.error
    assert t._started.is_set(); print("open OK (v1beta)", t._model)
    # play callback drains in real time
    stop = threading.Event()
    def play():
        buf = bytearray(2048)
        while not stop.is_set():
            t._on_play(buf, 1024, None, None); time.sleep(1024/24000)
    threading.Thread(target=play, daemon=True).start()
    def feed(sig, after_silence=0):
        pass
    def mic():
        blk = 320
        timeline = [np.zeros(16000 * 2, np.float32), S1]
        sig = np.concatenate(timeline)
        t0 = time.monotonic(); i = 0; sent2 = False
        while not stop.is_set():
            due = t0 + i * blk / 16000
            time.sleep(max(0, due - time.monotonic()))
            if i == 0: ev["t0"] = time.monotonic()
            if i * blk < len(sig): chunk = sig[i*blk:(i+1)*blk]
            else: chunk = np.zeros(blk, np.float32)
            if i * blk == len(sig) or (len(chunk) < blk and i*blk < len(sig)): ev["end1"] = time.monotonic()
            if interrupt and "first" in ev and not sent2 and time.monotonic() - ev["first"] > 1.5:
                sent2 = True; ev["s2"] = time.monotonic(); sig = np.concatenate([np.zeros(0, np.float32), S2]); i = 0; t0 = time.monotonic()
                continue
            if len(chunk) < blk: chunk = np.pad(chunk, (0, blk - len(chunk)))
            t._on_audio(chunk.reshape(-1, 1), blk, None, None); i += 1
    t._mic_started = True
    threading.Thread(target=mic, daemon=True).start()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if interrupt and "interrupted" in ev and ev.get("ntc",0) >= 2 and t._quiet(): break
        if not interrupt and "tc" in ev and t._quiet(): break
        time.sleep(0.05)
    out = {"first_ms_after_end": round((ev.get("first", 0) - ev.get("end1", 0)) * 1000) if "first" in ev else None,
           "turn_complete": "tc" in ev, "interrupted": "interrupted" in ev, "quiet": t._quiet(),
           "finished_speaking": t.finished_speaking(), "heard": ev.get("heard"), "turns": [(m["role"], m["content"][:80]) for m in t._session.turns], "error": str(t.error)}
    if interrupt and "interrupted" in ev:
        out["interrupt_after_s2_ms"] = round((ev["interrupted"] - ev["s2"]) * 1000)
        out["buffer_empty_after"] = not t._chunks
    stop.set(); t.close(); time.sleep(0.5)
    return out
mode = sys.argv[1]
print(run(mode == "int"))
