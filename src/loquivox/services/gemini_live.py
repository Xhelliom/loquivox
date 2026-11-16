"""
Talk mode held by one Google Gemini Live session — the second provider of the
``live`` engine (``[talk] live_provider = "google"``), not a fourth engine.

Same public surface as ``LiveTalk`` (``start``, ``close``, ``tick``,
``push_context``, ``push_result``, ``finished_speaking``, ``idle_for``, ``done``,
``error``, ``prefix``, ``on_level``), because ``_talk_converse_server`` only
knows those. The microphone gate, the jitter buffer and the way a turn lands in
``TalkSession`` are ``live_audio.LiveAudio``'s, shared with ``live_talk``.

What differs from gpt-live-1, and why this file is not a subclass of it:

- **The server says what the other one never does.** ``turn_complete`` closes a
  turn (no gap to tune, ``tick()`` has nothing to do), ``interrupted`` says the
  user cut in (the speakers are emptied on it, no guess), and a reply is over
  when its turn is complete *and* the jitter buffer is empty.
- **16 kHz in, 24 kHz out.** The microphone is captured at 16 kHz.
- **v1beta.** The SDK opens ``v1`` by default and the Live API answers 404
  there: ``HttpOptions(api_version="v1beta")``, always.
- **Context is a message, not instructions.** ``system_instruction`` is fixed at
  open; the screen description goes in through ``send_client_content`` with
  ``turn_complete=False`` ("more to come", so the model waits instead of
  answering it), framed as background, as ``QUIET_CONTEXT`` does for gpt-live-1.
- **Still sends zeros, never nothing, when the microphone is held back.** Not
  required here (silence and ``audio_stream_end`` both work) but the input is
  billed either way and the code is the shared one.

Long sessions: one connection lasts ~10 min (a ``go_away`` announces the end), so
the context is always compressed (``sliding_window``), the last
``session_resumption_update`` handle is kept, and on ``go_away`` or a dropped
socket the session reopens with it — the microphone stays open, the playback
buffer is emptied, one line goes to the journal. Without a handle (or on a first
open after a companion pause, #122) the turns so far go back in through
``send_client_content`` with ``user``/``model`` roles and ``turn_complete=False``.

No tools yet (a following card): the model talks and ``TalkSession`` records the
turns; plugins, MCP, sources and the spoken yes come with ``push_result``.

Threading is the other engines': the SDK is async, so the session runs an
asyncio loop on its own thread, captured audio crosses with
``run_coroutine_threadsafe``, playback is a PortAudio callback.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

import loquivox.config as config_module
from loquivox.services import media
from loquivox.services.live_audio import SPEECH_TAIL_TIMEOUT, LiveAudio  # noqa: F401
from loquivox.state import STATE
from loquivox.transcription.streaming import float32_to_pcm16

#: the Live API reads PCM16 mono at 16 kHz and answers at 24 kHz
IN_RATE: int = 16000
OUT_RATE: int = 24000
#: the SDK defaults to ``v1``, where the Live endpoint does not exist (HTTP 404)
API_VERSION: str = "v1beta"
#: the extended-thinking model refuses to open without a ``thinking_level``
THINKING_MODEL_MARK: str = "thinking"
#: framed around a screen description / selection handed over mid-call. There is
#: no system role after the setup, so the text says what it is.
CONTEXT_FRAME: str = (
    "[Background for you only, not something the user said or asked. The "
    "application captured it by itself — a description of their screen, or the "
    "text they had selected. Do not answer it, do not read it out, do not "
    "mention it unprompted: wait for the user to speak, then use it to "
    "understand them.]\n"
)


#: after a go_away, how long the current reply may finish on the old connection:
#: ``time_left`` minus the margin, or the fallback when the server gives none
GOAWAY_MARGIN = 3.0
GOAWAY_FALLBACK = 8.0


class GeminiLive(LiveAudio):
    """One spoken conversation, voiced and thought by Gemini Live."""

    IN_RATE = IN_RATE
    OUT_RATE = OUT_RATE

    def __init__(self, session, *, model: str = "", voice: str = "",
                 instructions: str = "", **_openai_only: Any) -> None:
        # ``**_openai_only``: the companion hands every Live engine the same
        # options (delegation, backend model, web search); none applies here.
        cfg = config_module.CFG
        self._session = session
        #: float32 blocks sent ahead of the microphone (companion: the wake-word tail)
        self.prefix: list = []
        self._model = model or cfg.TALK_GEMINI_MODEL
        self._voice = voice or cfg.TALK_GEMINI_VOICE
        self._instructions = instructions or session.system_prompt()
        self._init_audio()
        self._init_turns()
        #: a reply is being produced: from its first audio to ``turn_complete``
        #: or ``interrupted``
        self._speaking = False
        self._context_pushed = ""
        #: latest ``session_resumption_update.new_handle``; a go_away reopens with it
        self._handle_token: Optional[str] = None
        self._goaway = False
        self._goaway_deadline = 0.0
        self._loop = asyncio.new_event_loop()
        self._task: Optional["asyncio.Task"] = None
        self._conn = None
        self._ready = threading.Event()
        self._started = threading.Event()
        self._stop = False
        self.error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._out = None
        self._mic = None

    # --- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 20.0) -> None:
        """
        Open the session, the microphone and the speakers. Raises on failure,
        which the caller reads as "fall back to the cascade" — no SDK, no key
        and a refused connection all land there.
        """
        try:
            import google.genai  # noqa: F401  (lazy: optional ``.[gemini]`` extra)
        except ImportError as e:
            raise RuntimeError("google-genai is not installed — pip install 'loquivox[gemini]'") from e
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is not set — add it in Settings → Models & keys → Keys")
        import sounddevice as sd
        from loquivox.services.audio import resolve_input_device

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        if self.error is not None:
            raise self.error
        if self._conn is None or not self._started.is_set():
            raise RuntimeError("Gemini Live session did not start in time")
        media.mic_opened(duck=not STATE.companion_session)
        self._mic = sd.InputStream(samplerate=IN_RATE, channels=1, dtype="float32",
                                   device=resolve_input_device(), callback=self._on_audio)
        self._mic.start()
        for block in self.prefix:   # what was said before the session was open
            self._send_block(block)
        # The speakers after the microphone, never before: on a Bluetooth
        # headset the microphone is what switches it to HFP.
        media.await_headset_mic()
        self._out = sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16",
                                       blocksize=1024, callback=self._on_play)
        self._out.start()
        print(f"🔊 Gemini Live — {self._model} / {self._voice}")

    def close(self) -> None:
        """Stop everything. Safe to call twice."""
        self._stop = True
        self._echo_report()
        self._flush_turns()
        if self._mic is not None:
            self._mic.stop()
            self._mic.close()
            self._mic = None
            media.mic_closed()
        if self._out is not None:
            self._out.close()      # discards whatever is still buffered
            self._out = None
        try:
            if self._task is not None:
                # Leaving the SDK's ``async with`` is what closes the socket.
                self._loop.call_soon_threadsafe(self._task.cancel)
            else:
                self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass   # the loop is already closed

    def _quiet(self) -> bool:
        """Nothing more of the reply is on its way to the ears: turn over, buffer empty."""
        return not self._speaking and self._audible == 0

    def _flush_audio(self) -> None:
        super()._flush_audio()
        self._speaking = False   # the reply is over (the server will say so too)

    def tick(self) -> None:
        """Nothing to sweep: ``turn_complete`` closes the turns."""

    # --- handing context to the model --------------------------------------

    def push_context(self) -> bool:
        """
        Hand the model the screen description (and the selection), whenever a
        capture lands — the text, not a flag, tracks it: S replaces a stale one.
        """
        if self._conn is None or self._stop:
            return False
        clause = self._session.context_clause()
        if not clause or clause == self._context_pushed:
            return False
        self._context_pushed = clause
        try:
            asyncio.run_coroutine_threadsafe(self._conn.send_client_content(
                turns={"role": "user", "parts": [{"text": CONTEXT_FRAME + clause}]},
                turn_complete=False), self._loop)
        except Exception as e:
            print(f"⚠️  Screen context not sent to the Gemini session: {e}")
            return False
        return True

    def push_result(self) -> bool:
        """Plugins, sources and the spoken yes: the tools card. Nothing yet."""
        return False

    # --- microphone → session ----------------------------------------------

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback: push captured audio to the model."""
        if self._conn is None or self._stop or not self._started.is_set():
            return
        mono = indata[:, 0].copy()
        if self.on_level:
            self.on_level("in", min(1.0, float(np.sqrt(np.mean(mono ** 2))) * 6))
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono)
        except Exception:
            pass
        # Never empty: a held block comes back as zeros (see LiveAudio).
        for chunk in self._mic_chunks(mono):
            self._send_block(chunk)

    def _send_block(self, chunk: np.ndarray) -> None:
        blob = {"data": float32_to_pcm16(chunk), "mime_type": f"audio/pcm;rate={IN_RATE}"}
        try:
            asyncio.run_coroutine_threadsafe(self._conn.send_realtime_input(audio=blob), self._loop)
        except Exception:
            pass  # the loop is closing — the session is on its way out

    # --- the session itself ------------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._task = self._loop.create_task(self._session_task())
            self._loop.run_until_complete(self._task)
        except (asyncio.CancelledError, Exception) as e:   # cancelled = close()
            if not self._stop and not isinstance(e, asyncio.CancelledError):
                self.error = e
        finally:
            self._ready.set()
            self._loop.close()

    @staticmethod
    def _client_kwargs() -> Dict[str, Any]:
        return {"api_key": os.environ.get("GEMINI_API_KEY"),
                "http_options": {"api_version": API_VERSION}}

    def _config(self) -> Dict[str, Any]:
        """The session to open — fixed for its whole life."""
        cfg: Dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._instructions,
            "speech_config": {"voice_config": {"prebuilt_voice_config": {
                "voice_name": self._voice}}},
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            # the context is trimmed instead of ending the session (15 min → unlimited)
            "context_window_compression": {"sliding_window": {}},
            "session_resumption": ({"handle": self._handle_token}
                                   if self._handle_token else {}),
        }
        if THINKING_MODEL_MARK in self._model:
            cfg["thinking_config"] = {
                "thinking_level": config_module.CFG.TALK_GEMINI_THINKING}
        return cfg

    def _open(self):
        """The SDK's ``connect`` context manager (the self-check swaps it)."""
        from google import genai

        client = genai.Client(**self._client_kwargs())
        return client.aio.live.connect(model=self._model, config=self._config())

    def _prior_turns(self) -> list:
        """What a fresh server session must be told: nothing when a handle carries it."""
        if self._handle_token:
            return []
        return [{"role": "user" if t["role"] == "user" else "model",
                 "parts": [{"text": t["content"]}]}
                for t in self._session.resumed + self._session.turns
                if t["role"] in ("user", "assistant") and isinstance(t["content"], str)
                and t["content"]]

    async def _session_task(self) -> None:
        failures = 0
        try:
            while not self._stop:
                try:
                    async with self._open() as conn:
                        prior = self._prior_turns()
                        if prior:   # roles user/model, "more to come": no answer to it
                            await conn.send_client_content(turns=prior, turn_complete=False)
                        self._conn = conn
                        self._goaway = False
                        self._started.set()   # connect() returns once the setup is acknowledged
                        self._ready.set()
                        failures = 0
                        await self._pump(conn)
                    why = "go_away" if self._goaway else "connection closed"
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    why = f"{type(e).__name__}: {e}"[:120]
                    failures += 1
                    if self._stop or not self._started.is_set() or failures > 3:
                        raise
                    if failures > 1 and self._handle_token:
                        # a handle the server refuses (1007…): fall back to replaying the turns
                        print("⚠️  Gemini Live: handle refused, replaying the turns instead")
                        self._handle_token = None
                if self._stop:
                    break
                self._conn = None   # the microphone callback idles until the new one
                self._flush_audio()
                self._flush_turns()
                print(f"🔁 Gemini Live: reconnecting ({why}, "
                      f"{'with handle' if self._handle_token else 'turns replayed'})")
                await asyncio.sleep(0.5 * failures)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not self._stop:
                self.error = e
            self._ready.set()

    async def _pump(self, conn) -> None:
        """Receive until the socket ends, or — after a go_away — until the reply
        in progress is finished and played (``_quiet``), bounded by the deadline."""
        it = nxt = None
        try:
            while not self._stop:
                if self._goaway and (self._quiet() or time.monotonic() >= self._goaway_deadline):
                    return
                if nxt is None:
                    it = it or conn.receive().__aiter__()   # receive() ends after each turn
                    nxt = asyncio.ensure_future(it.__anext__())
                done, _ = await asyncio.wait({nxt}, timeout=0.1 if self._goaway else None)
                if not done:
                    continue
                task, nxt = nxt, None
                try:
                    message = task.result()
                except StopAsyncIteration:
                    it = None
                    continue
                self._handle(message)
        finally:
            if nxt is not None:
                nxt.cancel()

    def _handle(self, message) -> None:
        """Route one server message. Never raises — a session must not die on one."""
        update = getattr(message, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None) \
                and getattr(update, "resumable", True) is not False:
            self._handle_token = update.new_handle
        away = getattr(message, "go_away", None)
        if away is not None:
            left = getattr(away, "time_left", None)
            if hasattr(left, "total_seconds"):
                left = left.total_seconds()
            try:
                left = float(str(left).rstrip("s")) if left is not None else GOAWAY_FALLBACK
            except ValueError:
                left = GOAWAY_FALLBACK
            self._goaway = True
            self._goaway_deadline = time.monotonic() + max(left - GOAWAY_MARGIN, 0.0)
        content = getattr(message, "server_content", None)
        if content is None:
            return
        if getattr(content, "interrupted", False):
            self._flush_audio()
            self._close_turn("assistant")   # what was said before being cut off
        turn = getattr(content, "model_turn", None)
        for part in (getattr(turn, "parts", None) or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                self._speaking_started()
                with self._audio_lock:
                    self._audible += 1
                    self._last_audio = self._last_activity = time.monotonic()
                    self._chunks.append((bytes(data), True))
                    self._queued += len(data)
        heard = getattr(getattr(content, "input_transcription", None), "text", "")
        if heard:
            self._on_delta("user", heard)
        said = getattr(getattr(content, "output_transcription", None), "text", "")
        if said:
            self._speaking_started()
            self._on_delta("assistant", said)
        if getattr(content, "turn_complete", False):
            self._speaking = False
            self._flush_turns()

    def _speaking_started(self) -> None:
        """First sign of a reply: the user's sentence is over, record it first."""
        self._speaking = True
        if "user" in self._live:
            self._close_turn("user")


if __name__ == "__main__":
    # Self-check, no network, no audio device, no SDK:
    #     python src/loquivox/services/gemini_live.py
    import base64  # noqa: F401
    import types

    import loquivox.managers.chat as chat_module
    from loquivox.services.talk import TalkSession

    chat_module.ChatManager.add_message = staticmethod(lambda role, text="": None)
    chat_module.ChatManager.stream = staticmethod(lambda role, text: None)

    def msg(**content):
        return types.SimpleNamespace(server_content=types.SimpleNamespace(**content))

    def audio(n=1200, value=1000):
        pcm = (value).to_bytes(2, "little", signed=True) * n
        return msg(model_turn=types.SimpleNamespace(parts=[
            types.SimpleNamespace(inline_data=types.SimpleNamespace(data=pcm))]))

    talk = GeminiLive(TalkSession())

    # The SDK opens v1 by default, which answers 404: v1beta, always.
    assert GeminiLive._client_kwargs()["http_options"]["api_version"] == "v1beta"
    # The extended-thinking model refuses to open without a level.
    assert "thinking_config" not in talk._config()
    ext = GeminiLive(TalkSession(), model="gemini-3.8-live-extended-thinking")
    assert ext._config()["thinking_config"]["thinking_level"] in ("low", "medium", "high")

    # A held microphone still goes out, as zeros of the same length — never nothing.
    talk._speaking = True                        # a reply is playing
    echo = np.full(480, 0.3, dtype=np.float32)
    out = talk._mic_chunks(echo)
    assert len(out) == 1 and len(out[0]) == len(echo) and not out[0].any(), \
        "a gated block was dropped instead of sent as zeros"
    talk._held.clear(); talk._gate = None; talk._speaking = False
    # …and the Live input is 16 kHz PCM16 sent through send_realtime_input.
    sent: list = []

    class _Conn:
        async def send_realtime_input(self, audio):
            sent.append(audio)

    talk._conn, talk._started = _Conn(), threading.Event()
    talk._started.set()
    threading.Thread(target=talk._loop.run_forever, daemon=True).start()
    talk._on_audio(np.full((480, 1), 0.001, dtype=np.float32), 480, None, None)
    deadline = time.monotonic() + 2
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent and sent[0]["mime_type"] == "audio/pcm;rate=16000" and len(sent[0]["data"]) == 960, sent

    # A reply: audio makes the turn "speaking"; turn_complete alone does not
    # end it — the buffer still has to be played.
    talk._handle(audio())
    assert talk._speaking and talk._audible == 1 and not talk._quiet()
    talk._handle(msg(turn_complete=True))
    assert not talk._speaking and not talk._quiet(), "the buffer was not waited for"
    talk._primed = True
    talk._on_play(bytearray(len(talk._chunks[0][0])), 1200, None, None)
    assert talk._quiet() and talk.finished_speaking(), "a played, completed reply stayed open"

    # The server says the user cut in: the speakers are emptied at once.
    talk._handle(audio())
    talk._handle(audio())
    assert talk._audible == 2
    talk._handle(msg(interrupted=True))
    assert not talk._chunks and talk._audible == 0 and talk._queued == 0 and talk._quiet(), \
        "interrupted did not empty the playback buffer"

    # Turns come from the transcriptions + turn_complete, user first.
    t = GeminiLive(TalkSession())
    t._handle(msg(input_transcription=types.SimpleNamespace(text="écris un mail")))
    t._handle(msg(output_transcription=types.SimpleNamespace(text="D'accord")))
    assert [m["role"] for m in t._session.turns] == ["user"], t._session.turns
    t._handle(msg(output_transcription=types.SimpleNamespace(text=", voilà."), turn_complete=True))
    assert [m["role"] for m in t._session.turns] == ["user", "assistant"], t._session.turns
    assert t._session.turns[-1]["content"] == "D'accord, voilà."
    t._handle(msg(input_transcription=types.SimpleNamespace(text="j'ai fini"), turn_complete=True))
    assert t.done, "the finish phrase was not heard"

    # Long sessions: compression always on, the last handle kept, go_away reopens.
    assert talk._config()["context_window_compression"] == {"sliding_window": {}}
    assert talk._config()["session_resumption"] == {}
    r = GeminiLive(TalkSession())
    r._handle(types.SimpleNamespace(session_resumption_update=types.SimpleNamespace(
        new_handle="h1", resumable=True)))
    r._handle(types.SimpleNamespace(session_resumption_update=types.SimpleNamespace(
        new_handle="h2", resumable=True)))
    r._handle(types.SimpleNamespace(session_resumption_update=types.SimpleNamespace(
        new_handle="no", resumable=False)))
    assert r._handle_token == "h2" and r._config()["session_resumption"] == {"handle": "h2"}

    class _Fake:
        def __init__(self, script):
            self.script, self.client_content, self.on_end = script, [], None

        async def send_client_content(self, turns, turn_complete):
            self.client_content.append((turns, turn_complete))

        async def receive(self):
            for m in self.script.pop(0) if self.script else []:
                yield m
            if self.on_end:   # the last scripted connection has said everything
                self.on_end()
            await asyncio.sleep(0.01)

    opened: list = []

    def run_session(g, scripts, first_turns=(), first_end=None):
        g._session.resumed = list(first_turns)
        conns = [_Fake([s]) for s in scripts]
        conns[0].on_end = first_end
        conns[-1].on_end = lambda: setattr(g, "_stop", True)

        class _CM:
            async def __aenter__(self_):
                opened.append(g._handle_token)
                c = conns[min(len(opened) - 1, len(conns) - 1)]
                return c

            async def __aexit__(self_, *a):
                return False
        g._open = _CM
        opened.clear()
        asyncio.run(g._session_task())
        return conns

    upd = types.SimpleNamespace(session_resumption_update=types.SimpleNamespace(
        new_handle="H", resumable=True))
    bye = types.SimpleNamespace(go_away=types.SimpleNamespace(time_left="3s"))   # deadline: now
    g = GeminiLive(TalkSession())
    g._chunks.append((b"\0\0", True)); g._audible = 1
    conns = run_session(g, [[upd, bye], [audio(), msg(turn_complete=True)]])
    assert opened[:2] == [None, "H"], f"go_away did not reopen with the handle: {opened}"
    assert g.error is None and not conns[1].client_content, "a handle must not replay turns"
    assert g._conn is None or g._stop

    # go_away in the middle of a reply: the rest still lands in the buffer on the
    # same connection, and the reopen waits for turn_complete and the playback.
    slow = types.SimpleNamespace(go_away=types.SimpleNamespace(time_left="30s"))
    g4 = GeminiLive(TalkSession())
    seen: dict = {}

    def _first_end():
        seen["opened"], seen["chunks"] = len(opened), len(g4._chunks)
        g4._chunks.clear(); g4._audible = 0   # the speakers played it all

    conns = run_session(g4, [[upd, audio(), slow, audio(), msg(turn_complete=True)], []],
                        first_end=_first_end)
    assert seen == {"opened": 1, "chunks": 2}, f"reply cut at go_away: {seen}"
    assert opened[:2] == [None, "H"] and g4.error is None

    # No handle: the turns so far go back with user/model roles, turn_complete=False.
    g2 = GeminiLive(TalkSession())
    g2._session.turns.append({"role": "assistant", "content": "Salut"})
    conns = run_session(g2, [[bye], []],
                        first_turns=[{"role": "user", "content": "Bonjour"}])
    assert conns[0].client_content == [([{"role": "user", "parts": [{"text": "Bonjour"}]},
                                         {"role": "model", "parts": [{"text": "Salut"}]}], False)]

    # A dropped socket with a handle also reopens; the first open failing raises.
    g3 = GeminiLive(TalkSession())

    class _Boom(_Fake):
        async def receive(self):
            raise ConnectionError("1011")
            yield

    seq = [_Boom([]), _Fake([[audio()]])]
    seq[1].on_end = lambda: setattr(g3, "_stop", True)
    n = {"i": 0}

    class _CM3:
        async def __aenter__(self_):
            c = seq[min(n["i"], 1)]
            n["i"] += 1
            return c

        async def __aexit__(self_, *a):
            return False
    g3._open, g3._handle_token = _CM3, "H"
    asyncio.run(g3._session_task())
    assert n["i"] >= 2 and g3.error is None, "a dropped connection was not resumed"

    # Without the SDK or the key, start() raises: the caller falls back to the cascade.
    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        try:
            GeminiLive(TalkSession()).start()
        except RuntimeError as e:
            assert "GEMINI_API_KEY" in str(e) or "google-genai" in str(e), e
        else:
            raise AssertionError("start() opened without a key")
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved
    print("✓ gemini live OK")
