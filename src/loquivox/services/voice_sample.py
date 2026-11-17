"""
A Live voice, heard for real: gpt-live is made to say one line with it.

The Live voices exist only inside gpt-live (twelve of the twenty-two have no
TTS twin, the only non-English ones among them), so the one honest preview is a
short session that hears a spoken question and answers it. The answer is
trimmed and kept in ``~/.cache/loquivox/voices/<voice>-<lang>.wav``: the first
click costs a session of a few seconds, every later one is a file.

Same connection and the same rules as ``live_talk`` (``client.live.connect()``,
``session.started`` before any audio, 24 kHz PCM16) — and its first rule above
all: **the input is a continuous stream, zeros around the question, never
nothing**, or the voice stalls waiting for it.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
import wave
from pathlib import Path
from typing import Iterator, Tuple

import numpy as np

import loquivox.config as config_module
from loquivox.services.live_talk import RATE, _is_audible

#: input block, as ``live_talk`` sends them (100 ms of int16)
BLOCK_BYTES: int = RATE // 10 * 2
#: silence heard by the model before the question starts
LEAD_SECONDS: float = 0.5
#: silence after the voice that ends the recording, and the longest session
IDLE_SECONDS: float = 1.0
DEADLINE_SECONDS: float = 30.0
#: silence kept at each end of the sample
MARGIN = RATE // 10

#: language -> (the question, the line the voice must answer with)
SAMPLES = {
    "fr": ("Bonjour ! Peux-tu te présenter en une phrase ?",
           "Bonjour, je suis là pour vous écouter et vous aider. "
           "Dites-moi simplement ce dont vous avez besoin."),
    "en": ("Hi! Can you introduce yourself in one sentence?",
           "Hi, I'm here to listen and help. Just tell me what you need."),
}


def _dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "loquivox" / "voices"


def sample_path(voice: str, lang: str) -> Path:
    if not voice or not all(c.isalnum() or c in "-_" for c in voice):
        raise ValueError(f"invalid voice name: {voice!r}")
    return _dir() / f"{voice}-{lang}.wav"


def input_blocks(question: np.ndarray) -> Iterator[bytes]:
    """Zeros, the question, then zeros for ever — one whole block per tick."""
    lead = np.zeros(int(LEAD_SECONDS * RATE), dtype=np.int16)
    pcm = np.concatenate([lead, question.astype(np.int16)]).tobytes()
    pcm += bytes(-len(pcm) % BLOCK_BYTES)
    for i in range(0, len(pcm), BLOCK_BYTES):
        yield pcm[i:i + BLOCK_BYTES]
    while True:
        yield bytes(BLOCK_BYTES)


def trim_silence(pcm: np.ndarray) -> np.ndarray:
    loud = np.flatnonzero(np.abs(pcm.astype(np.int32)) > 64)
    if not loud.size:
        return pcm
    return pcm[max(0, loud[0] - MARGIN):loud[-1] + 1 + MARGIN]


def wav_bytes(pcm: np.ndarray) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


async def converse(conn, voice: str, line: str, question: np.ndarray, *,
                   tick: float = 0.1, idle: float = IDLE_SECONDS,
                   deadline: float = DEADLINE_SECONDS) -> np.ndarray:
    """Open the session on ``conn``, play the question, return what the voice said."""
    await conn.session.start(session={
        "model": config_module.CFG.TALK_LIVE_MODEL,
        "instructions": (
            "You are letting the user hear your voice. When asked to introduce "
            "yourself, answer with exactly this line, adding or changing nothing, "
            "in a natural and warm tone, then be quiet: " + line),
        "audio": {"format": {"type": "audio/pcm", "rate": RATE},
                  "output": {"voice": voice}},
        "delegation": {"type": "client"},
    })
    started, state = asyncio.Event(), {"last": None, "out": [], "error": None}

    async def read() -> None:
        async for ev in conn:
            kind = getattr(ev, "type", "")
            if kind == "session.started":
                started.set()
            elif kind == "session.output_audio.delta":
                pcm = base64.b64decode(getattr(ev, "delta", "") or "")
                if _is_audible(pcm):
                    state["last"] = time.monotonic()
                if state["last"] is not None:
                    state["out"].append(pcm)
            elif kind == "error":
                err = getattr(ev, "error", ev)
                state["error"] = getattr(err, "message", None) or str(err)
                return
            elif kind == "session.closed":
                return

    reader = asyncio.ensure_future(read())
    end = time.monotonic() + deadline
    blocks = input_blocks(question)
    try:
        while not reader.done() and time.monotonic() < end:
            if started.is_set():
                await conn.session.input_audio.append(
                    audio=base64.b64encode(next(blocks)).decode("ascii"))
                if state["last"] and time.monotonic() - state["last"] > idle:
                    break
            await asyncio.sleep(tick)
    finally:
        reader.cancel()
        try:
            await conn.session.close()
        except Exception:
            pass
    if state["error"]:
        raise RuntimeError(f"gpt-live: {state['error']}")
    if not state["out"]:
        raise RuntimeError(f"gpt-live said nothing with the voice “{voice}”")
    return np.frombuffer(b"".join(state["out"]), dtype=np.int16)


def _question_pcm(lang: str) -> np.ndarray:
    """The spoken question (TTS "alloy"), itself cached."""
    path = _dir() / f"_question-{lang}.pcm"
    if not path.exists():
        from loquivox.api import get_ai_client
        data = get_ai_client("openai").audio.speech.create(
            model="gpt-4o-mini-tts", voice="alloy", input=SAMPLES[lang][0],
            response_format="pcm").content
        _write_atomic(path, data)
    return np.frombuffer(path.read_bytes(), dtype=np.int16)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _record(voice: str, lang: str) -> np.ndarray:
    from openai import AsyncOpenAI

    question = _question_pcm(lang)

    async def go() -> np.ndarray:
        async with AsyncOpenAI().live.connect() as conn:
            return await converse(conn, voice, SAMPLES[lang][1], question)

    return asyncio.run(go())


def voice_sample(voice: str, lang: str) -> Tuple[Path, bool]:
    """The wav of ``voice`` and whether it came from the cache. Blocking: never on GTK."""
    lang = lang if lang in SAMPLES else "en"
    path = sample_path(voice, lang)
    if path.exists():
        print(f"🔊 voice sample {voice}: cache")
        return path, True
    started = time.monotonic()
    _write_atomic(path, wav_bytes(trim_silence(_record(voice, lang))))
    print(f"🔊 voice sample {voice}: recorded in {time.monotonic() - started:.1f}s")
    return path, False


def gemini_line() -> str:
    return SAMPLES["fr"][1]


async def gemini_converse(session) -> np.ndarray:
    """Ask an open Gemini Live session to say the French line; return its 24 kHz PCM."""
    await session.send_client_content(
        turns={"role": "user", "parts": [{"text": "Bonjour ! Peux-tu te présenter en une phrase ?"}]},
        turn_complete=True)
    out = []
    async for m in session.receive():
        content = getattr(m, "server_content", None)
        for part in (getattr(getattr(content, "model_turn", None), "parts", None) or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                out.append(bytes(data))
        if getattr(content, "turn_complete", False):
            break
    if not out:
        raise RuntimeError("Gemini Live said nothing with this voice")
    return np.frombuffer(b"".join(out), dtype=np.int16)


def _record_gemini(voice: str, model: str) -> np.ndarray:
    from loquivox.services.gemini_live import GeminiLive

    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError("google-genai is not installed — pip install 'loquivox[gemini]'") from e
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set — add it in Settings → Models & keys → Keys")

    async def go() -> np.ndarray:
        client = genai.Client(**GeminiLive._client_kwargs())
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": (
                "You are letting the user hear your voice. When asked to introduce "
                "yourself, answer with exactly this line, in French, adding nothing, "
                "in a natural and warm tone: " + gemini_line()),
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": voice}}},
        }
        async with client.aio.live.connect(model=model, config=config) as session:
            return await asyncio.wait_for(gemini_converse(session), DEADLINE_SECONDS)

    return asyncio.run(go())


def gemini_voice_sample(voice: str, model: str) -> Tuple[Path, bool]:
    """Like ``voice_sample`` for Google: cached per voice AND model, French. Blocking."""
    path = sample_path("gemini-{}-{}".format(model.replace(".", "_"), voice), "fr")
    if path.exists():
        print(f"🔊 gemini voice sample {voice}: cache")
        return path, True
    _write_atomic(path, wav_bytes(trim_silence(_record_gemini(voice, model))))
    print(f"🔊 gemini voice sample {voice}: recorded")
    return path, False
