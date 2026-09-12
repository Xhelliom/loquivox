"""
Talk mode held by one OpenAI Live session — speech in, speech out, thinking out.

Where the Realtime engine hands the whole conversation to one model, ``gpt-live-1``
splits the job: it hears, it speaks, it owns the rhythm of the exchange — and
whenever something needs actual reasoning it *delegates*. Which is the point of
this engine existing: with ``delegation: {"type": "client"}`` the thing it
delegates to is ours, so the choice of LLM comes back. The voice is OpenAI's,
the thinking is whatever ``[models] chat`` points at, plugins included.

The trade is real and worth stating. ``session.delegation.created`` carries
"metadata, not task text" — no tool name, no arguments, not even the utterance
that triggered it. The voice model asks for help; working out what help means is
``TalkSession.consult``'s job, from the transcript. So the voice model no longer
picks a tool: ours does, one level down.

``delegation: {"type": "responses"}`` is the other half of the setting and the
other trade: OpenAI's managed backend issues real, named tool calls, but the
model is OpenAI's — which is what the Realtime engine already gives us. Both are
here because the only way to compare them honestly is to run both.

Two things the Live API does not do that the Realtime one did, and both are
answered here rather than worked around:

- **No turn-completed event.** Transcripts arrive as deltas with no ``.done``
  of any kind — "transcript deltas have no item ID or authoritative turn-
  completed event". A turn closes on a gap (``TALK_LIVE_TURN_GAP``), swept by
  ``tick()``.
- **No speech_started.** The server never says the user has cut in, so the
  ``EchoGate`` that already gates the microphone is what flushes the speakers
  too: when it opens, the reply stops.

Threading is the Realtime engine's, unchanged: the SDK is async, so the session
runs an asyncio loop on its own thread, captured audio crosses with
``run_coroutine_threadsafe``, and playback is a second thread draining a queue
so a barge-in only has to empty it.
"""
from __future__ import annotations

import asyncio
import base64
import queue
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import sounddevice as sd

import loquivox.config as config_module
from loquivox.services import media
from loquivox.services.audio import resolve_input_device
from loquivox.services.talk import (_strip_marker, echo_note, user_asked_write,
                                    user_said_done)
from loquivox.services.vad import EchoGate
from loquivox.state import STATE
from loquivox.transcription.streaming import float32_to_pcm16

#: Live audio is 24 kHz mono PCM16, both ways (``audio.format`` also accepts
#: 16 kHz and the two G.711 rates; one format covers input and output and
#: cannot change once the session is open).
RATE: int = 24000
#: playback blocks; small enough that a barge-in cuts within ~40 ms
BLOCK: int = 1024
#: longest a closing session waits for the last reply to finish playing
SPEECH_TAIL_TIMEOUT: float = 15.0
#: how long the microphone stays gated after the last block was written
ECHO_HANGOVER: float = 0.25
#: Every append (instructions, thinking, commentary) is capped at 500 tokens by
#: the API. Counting them properly would mean a tokenizer for a model we do not
#: have one for, so this is a deliberately pessimistic character budget — ~3.2
#: characters per token, against the ~4 that English averages and the ~3.5 that
#: French does. Overshooting costs a second append; undershooting costs the
#: whole message.
APPEND_CHARS: int = 1500


def _chunks(text: str, limit: int = APPEND_CHARS) -> List[str]:
    """
    ``text`` split into pieces that fit one append, on sentence boundaries.

    Nothing is dropped: the cap is on one event, not on what may be said, and
    the API's own guidance is to stream results as several appends. Splitting
    on sentences rather than at the character means a chunk is still something
    the voice model can read and paraphrase on its own.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    out: List[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "),
                  window.rfind("\n"))
        if cut <= limit // 4:          # no usable boundary — fall back to a space
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        out.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return [chunk for chunk in out if chunk]


class LiveTalk:
    """One spoken conversation, voiced by gpt-live-1 and thought by us."""

    def __init__(self, session, *, model: str = "", voice: str = "",
                 instructions: str = "") -> None:
        cfg = config_module.CFG
        self._session = session
        self._model = model or cfg.TALK_LIVE_MODEL
        self._voice = voice or cfg.TALK_LIVE_VOICE
        self._instructions = instructions or session.system_prompt()
        self._delegation = resolve_delegation()
        #: turn being transcribed right now, per role: text and when the last
        #: delta for it arrived. There is no end-of-turn event, so this pair is
        #: the whole turn machinery (see ``tick``).
        self._live: Dict[str, str] = {}
        self._last_delta: Dict[str, float] = {}
        self._audio: "queue.Queue" = queue.Queue()
        #: set while nothing more is coming for the reply being played
        self._audio_done = threading.Event()
        self._audio_done.set()
        self._writing = False
        self._gate: Optional[EchoGate] = None
        self._held: List[np.ndarray] = []
        self._echo_until: float = 0.0
        self._speech_deadline: Optional[float] = None
        self._loop = asyncio.new_event_loop()
        self._conn = None
        self._ready = threading.Event()
        self._started = threading.Event()
        self._stop = False
        #: the screen description already handed over, kept as the text rather
        #: than a flag: S starts a fresh capture and a flag would swallow it
        self._context_pushed = ""
        #: client delegations being worked on, so a second event for one in
        #: flight does not start the backend twice
        self._working: set = set()
        self._lock = threading.Lock()
        #: set once the briefing is over — a finish phrase or the model's marker
        self.done = False
        self.error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._player: Optional[threading.Thread] = None
        self._mic: Optional[sd.InputStream] = None

    # --- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 20.0) -> None:
        """
        Open the session, the microphone and the speakers. Raises on failure,
        which the caller reads as "fall back to the cascade".

        Longer default than the Realtime engine's: this waits for the socket
        *and* for ``session.started``, which the API requires before any audio
        or command is sent.
        """
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        if self.error is not None:
            raise self.error
        if self._conn is None or not self._started.is_set():
            raise RuntimeError("Live session did not start in time")
        self._player = threading.Thread(target=self._play, daemon=True)
        self._player.start()
        media.mic_opened()
        self._mic = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                   device=resolve_input_device(),
                                   callback=self._on_audio)
        self._mic.start()
        print(f"🔊 Live talk — {self._model} / {self._voice} · "
              f"delegation: {self._delegation}"
              + (f" ({config_module.CFG.TALK_LIVE_BACKEND_MODEL})"
                 if self._delegation == "responses" else ""))

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
        self._audio.put(None)
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _quiet(self) -> bool:
        """
        True when nothing more of the current reply is on its way to the ears.

        Same three callers as the Realtime engine, asking the same question:
        the microphone gate (what it hears is still echo), the end of the
        session (the last sentence has not been heard yet) and ``push_result``
        (there is no gap to slip an answer into).
        """
        return (self._audio_done.is_set() and self._audio.empty()
                and not self._writing)

    def finished_speaking(self) -> bool:
        """
        True once the reply that ended the briefing has actually been heard.

        The Live API emits no end-of-output-audio event at all — "GPT-Live does
        not emit an output-audio-done event", the playback queue is the only
        thing that knows — so this leans entirely on ``_quiet()``, bounded so a
        session can never be held open by audio that stopped arriving.
        """
        if self._quiet():
            return True
        now = time.monotonic()
        if self._speech_deadline is None:
            self._speech_deadline = now + SPEECH_TAIL_TIMEOUT
        return now >= self._speech_deadline

    # --- turns, built out of deltas ----------------------------------------

    def tick(self) -> None:
        """
        Close any turn nobody has added to for ``TALK_LIVE_TURN_GAP``.

        The Live API sends transcript deltas and never says a turn is over, so
        this sweep *is* the turn boundary. Polled from the conversation loop,
        which already ticks every 50 ms with nothing to do.

        OpenAI is explicit that the threshold is ours to choose and that the
        grouping must stay revisable — the two speakers may legitimately
        overlap, so a role is only ever closed by its own silence, never by the
        other one starting.
        """
        now = time.monotonic()
        gap = config_module.CFG.TALK_LIVE_TURN_GAP
        for role in ("user", "assistant"):
            if role in self._live and now - self._last_delta.get(role, now) >= gap:
                self._close_turn(role)

    def _flush_turns(self) -> None:
        """Close whatever is still open — the session is ending or delegating."""
        for role in ("user", "assistant"):
            self._close_turn(role)

    def _close_turn(self, role: str) -> None:
        """Record one finished turn, if there is one."""
        with self._lock:
            text = (self._live.pop(role, "") or "").strip()
            self._last_delta.pop(role, None)
        if not text:
            return
        if role == "user":
            self._on_user_said(text)
        else:
            self._on_assistant_said(text)

    def _on_delta(self, role: str, delta: str) -> None:
        """
        Accumulate a transcript fragment and show it in the bubble.

        Concatenated exactly as received — no trimming, no inserted spaces —
        because the API's fragments already carry their own spacing and
        "don't trim fragments or insert spaces between them" is the documented
        rule, not a style preference.
        """
        if not delta:
            return
        from loquivox.managers.chat import ChatManager

        with self._lock:
            text = self._live[role] = self._live.get(role, "") + delta
            self._last_delta[role] = time.monotonic()
        ChatManager.stream(role, _strip_marker(text) if role == "assistant" else text)

    def _on_user_said(self, text: str) -> None:
        """Record a spoken user turn, and notice if it ends the briefing."""
        from loquivox.managers.chat import ChatManager

        ChatManager.add_message("user", f"🗣️ {text}")
        self._session.add_user(text)
        if not self._session.write and user_asked_write(text):
            self._session.write = True   # "écris ça": the chat becomes a briefing
            self.done = True
        elif user_said_done(text):
            self.done = True

    def _on_assistant_said(self, text: str) -> None:
        """Record a spoken reply, stripping the end-of-briefing marker."""
        from loquivox.managers.chat import ChatManager

        reply = self._session.add_assistant(text)
        if reply.done:
            self.done = True
        if reply.text:
            ChatManager.add_message("assistant", reply.text)

    # --- handing context and results to the voice model --------------------

    def _append(self, kind: str, content: str,
                delegation_id: Optional[str] = None) -> None:
        """
        Send one ``session.<kind>.append``, in as many events as it takes.

        ``delegation_id`` is required on every append even when it is ``None``
        — that is the API's shape for "general session context", not an
        omission — so it is always passed.
        """
        if self._conn is None or self._stop:
            return
        target = getattr(self._conn.session, kind)
        for chunk in _chunks(content):
            asyncio.run_coroutine_threadsafe(
                target.append(content=chunk, delegation_id=delegation_id),
                self._loop)

    def push_context(self) -> bool:
        """
        Hand the voice model the screen description, whenever a capture lands.

        Through ``session.thinking.append``, not ``instructions.append``: the
        description is something to know, not a rule to follow, and an appended
        instruction "can interrupt the model's current speech or behavior" —
        which is precisely what a screenshot coming back mid-sentence must not
        do. ``instructions`` themselves are fixed once the session has started,
        so the Realtime engine's ``session.update`` trick has no equivalent
        here; this is the replacement, not a workaround.

        Polled like the Realtime engine's, and tracking the pushed *text* for
        the same reason: S exists to replace a description that has gone stale,
        and a flag would swallow every capture after the first.
        """
        if self._conn is None or self._stop:
            return False
        clause = self._session.context_clause()
        if not clause or clause == self._context_pushed:
            return False
        self._context_pushed = clause
        try:
            self._append("thinking", clause)
        except Exception as e:
            print(f"⚠️  Screen context not sent to the Live session: {e}")
            return False
        return True

    def push_result(self) -> bool:
        """
        Hand a plugin's answer to the voice model — between turns only.

        Same contract as the Realtime engine's: sweep every desk, wait for the
        moment nobody is talking, keep the answer in the session's turns for
        the writing pass. Through ``commentary.append``, which is the event for
        something the model should say out loud, in its own words.

        "Nobody is talking" is one condition short of the Realtime engine's
        here: there is no ``speech_started`` to tell us the user has the floor,
        so an open user turn stands in for it — the transcript is the only
        evidence available that someone is mid-sentence.
        """
        if self._conn is None or self._stop or not self._session.desks.ready():
            return False
        if not self._quiet() or "user" in self._live:
            return False
        found = self._session.desks.take()
        if found is None:
            return False
        plugin, message = found
        self._session.turns.append(message)
        if plugin.result_note:
            from loquivox.managers.chat import ChatManager
            ChatManager.add_message("note", plugin.result_note)
        try:
            self._append("commentary", message["content"])
        except Exception as e:
            print(f"⚠️  {plugin.name} result not sent to the Live session: {e}")
            return False
        return True

    # --- delegation --------------------------------------------------------

    def _on_delegation(self, event) -> None:
        """
        The voice model has asked for help. Work out what that means.

        In client mode the event says nothing but "I need something" — so the
        open user turn is closed first (a delegation can arrive before the
        sentence that caused it finishes transcribing) and the backend reasons
        over the conversation. On a thread: the answer is a network round trip,
        and nothing may block the session's event loop while the user is still
        being listened to.
        """
        delegation = getattr(event, "delegation", None)
        did = getattr(delegation, "id", "") or ""
        target = getattr(delegation, "target", "") or ""
        if target != "client" or not did:
            return          # a "responses" delegation runs on OpenAI's side
        with self._lock:
            if did in self._working:
                return
            self._working.add(did)
        threading.Thread(target=self._consult, args=(did,), daemon=True).start()

    def _consult(self, delegation_id: str) -> None:
        """Run the backend for one client delegation and speak its answer."""
        from loquivox.managers.chat import ChatManager

        try:
            self._flush_turns()
            ChatManager.add_message("note", "🧠 Thinking…")
            started = time.time()
            answer = self._session.consult()
            took = time.time() - started
            if not answer:
                print(f"⚠️  Backend returned nothing for {delegation_id}")
                return
            print(f"🧠 Delegation ({took:.1f}s): '{answer}'")
            self._append("commentary", answer, delegation_id=delegation_id)
        except Exception as e:
            print(f"⚠️  Delegation failed: {e}")
        finally:
            with self._lock:
                self._working.discard(delegation_id)

    def _on_response_event(self, event) -> None:
        """
        One nested Responses event, for ``responses`` delegation.

        The envelope carries the backend's own lifecycle inside ``event``, and
        the SDK types that field as a plain ``dict`` rather than parsing it —
        the nested Responses lifecycle is open-ended and the guide says to
        tolerate types it has never seen. So this reads it as a mapping and
        dispatches on the *nested* type; treating a top-level ``response.*``
        as an unwrapped Responses event is the documented way to get this
        wrong.

        Only completed function calls matter here — "an arguments-done event
        alone is not sufficient to identify the call" — and each is answered
        with the plugin's stub so the model acknowledges instead of going
        quiet, then the response is continued explicitly.
        """
        inner = getattr(event, "event", None)
        if not isinstance(inner, dict):
            return
        if inner.get("type") != "response.output_item.done":
            return
        item = inner.get("item")
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return
        desk = self._session.desks.get(str(item.get("name") or ""))
        if desk is None:
            return
        desk.ask(str(item.get("arguments") or ""))
        call_id = str(item.get("call_id") or "")
        asyncio.run_coroutine_threadsafe(
            self._tool_started(call_id, desk.plugin.started), self._loop)

    async def _tool_started(self, call_id: str, started: str) -> None:
        """Answer the call with its stub, then continue the backend response."""
        await self._conn.response.item.create(item={
            "type": "function_call_output", "call_id": call_id, "output": started})
        await self._conn.response.create()

    # --- microphone → session ---------------------------------------------

    def _echo_report(self) -> None:
        """Print what the last reply measured, and start the next one clean."""
        if self._gate is None:
            return
        print(self._gate.report)
        echo_note(self._gate)
        self._gate = None
        self._held.clear()

    def _mic_chunks(self, mono: np.ndarray) -> List[np.ndarray]:
        """
        What of this captured chunk may reach the model.

        Identical in shape to the Realtime engine's and for the same reason —
        the server hears the microphone continuously and reads the assistant's
        own voice off the speakers as an interruption — with one addition it
        cannot have: when the gate opens, this also flushes the speakers.
        There is no ``speech_started`` here to do it, so the gate is both
        halves of a barge-in, deciding it and acting on it.
        """
        cfg = config_module.CFG
        now = time.monotonic()
        if not self._quiet():
            self._echo_until = now + ECHO_HANGOVER
        if now >= self._echo_until:
            self._echo_report()
            return [mono]
        if not cfg.TALK_BARGE_IN:
            return []
        if self._gate is None:
            self._gate = EchoGate(RATE, threshold=cfg.TALK_VAD_THRESHOLD,
                                  margin=cfg.TALK_BARGE_IN_MARGIN)
        self._gate.feed(mono)
        if self._gate.speech_seconds < cfg.TALK_BARGE_IN_MS / 1000.0:
            self._held.append(mono)
            kept = sum(len(c) for c in self._held) / RATE
            while len(self._held) > 1 and kept > cfg.TALK_BARGE_IN_KEEP:
                kept -= len(self._held.pop(0)) / RATE
            return []
        chunks = self._held + [mono]
        if self._held:
            print(f"✋ Cut off — {self._gate.loudest:.3f} over an echo peaking "
                  f"at {self._gate.echo_peak:.3f}")
            self._held = []
            self._flush_audio()   # the half the server does for Realtime
        return chunks

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback: push captured audio to the model."""
        if self._conn is None or self._stop or not self._started.is_set():
            return
        mono = indata[:, 0].copy()
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono)
        except Exception:
            pass
        for chunk in self._mic_chunks(mono):
            pcm = base64.b64encode(float32_to_pcm16(chunk)).decode("ascii")
            try:
                asyncio.run_coroutine_threadsafe(
                    self._conn.session.input_audio.append(audio=pcm), self._loop)
            except Exception:
                pass  # the loop is closing — the session is on its way out

    # --- session → speakers ------------------------------------------------

    def _play(self) -> None:
        """Drain the audio queue into the sound card until the session ends."""
        try:
            with sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                    blocksize=BLOCK) as out:
                while not self._stop:
                    chunk = self._audio.get()
                    if chunk is None:
                        break
                    self._writing = True
                    try:
                        out.write(chunk)
                    finally:
                        self._writing = False
        except Exception as e:
            print(f"❌ Live playback error: {e}")

    def _flush_audio(self) -> None:
        """Drop everything not yet played — the user has started speaking."""
        try:
            while True:
                self._audio.get_nowait()
        except queue.Empty:
            pass
        self._audio_done.set()

    # --- the session itself ------------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_task())
        except Exception as e:
            if not self._stop:
                self.error = e
        finally:
            self._ready.set()
            self._loop.close()

    def _config(self) -> Dict[str, Any]:
        """
        The session to start with.

        Everything that matters is fixed here and cannot be changed later: the
        model, the instructions, the voice, the audio format and — the one that
        decides what this engine *is* — the delegation mode. Changing any of
        them means a new session.
        """
        cfg = config_module.CFG
        session: Dict[str, Any] = {
            "model": self._model,
            "instructions": self._instructions,
            "audio": {
                "format": {"type": "audio/pcm", "rate": RATE},
                "output": {"voice": self._voice},
            },
        }
        if self._delegation == "responses":
            responses: Dict[str, Any] = {
                "model": cfg.TALK_LIVE_BACKEND_MODEL,
                "instructions": cfg.TALK_LIVE_BACKEND_PROMPT,
            }
            tools = self._session.desks.realtime_tools
            if tools:
                # The Responses wire shape for a function is the flat one the
                # Realtime sessions use, so the plugin's existing definition
                # serves both and nothing is written a third time.
                responses["tools"] = tools
                responses["tool_choice"] = "auto"
            session["delegation"] = {"type": "responses", "responses": responses}
        else:
            session["delegation"] = {"type": "client"}
        return session

    async def _session_task(self) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        try:
            async with client.live.connect() as conn:
                self._conn = conn
                # The connection does not start the session: session.start is
                # the first message and nothing may be sent until the server
                # answers session.started.
                await conn.session.start(session=self._config())
                self._ready.set()
                async for event in conn:
                    self._handle(event)
                    if self._stop or self.done:
                        break
        except Exception as e:
            if not self._stop:
                self.error = e
            self._ready.set()

    def _handle(self, event) -> None:
        """Route one server event. Never raises — a session must not die on one."""
        etype = getattr(event, "type", "")
        if etype == "session.output_audio.delta":
            delta = getattr(event, "delta", "") or ""
            if delta:
                self._audio_done.clear()
                self._audio.put(base64.b64decode(delta))
        elif etype == "session.input_transcript.delta":
            self._on_delta("user", getattr(event, "delta", "") or "")
        elif etype == "session.output_transcript.delta":
            self._on_delta("assistant", getattr(event, "delta", "") or "")
        elif etype == "session.delegation.created":
            self._on_delegation(event)
        elif etype == "response.event":
            self._on_response_event(event)
        elif etype == "session.started":
            self._started.set()
        elif etype == "session.closed":
            self._stop = True
            usage = getattr(event, "usage", None)
            seconds = getattr(usage, "seconds", None)
            reason = getattr(event, "reason", "") or "?"
            print(f"🔚 Live session closed ({reason})"
                  + (f" — {seconds}s" if seconds is not None else ""))
        elif etype == "error":
            self.error = RuntimeError(getattr(event, "error", etype))


def resolve_delegation(choice: Optional[str] = None) -> str:
    """
    Which side does the thinking — "client" or "responses", never "auto".

    ``auto`` follows the backend already configured under ``[models]``: an
    OpenAI provider gets OpenAI's managed backend, anything else gets ours,
    because ours is the only one that can reach it. Both values can be forced,
    and that is the point of them existing — comparing the two modes honestly
    means holding the model constant, which ``auto`` alone cannot do.

    ``choice`` overrides the configured value, for the Settings dialog asking
    what a combo it has not applied yet would resolve to. The rule lives here
    and only here, so the dialog can never grey out a field on a different
    answer from the one the session will get.
    """
    cfg = config_module.CFG
    picked = (choice if choice is not None else cfg.TALK_LIVE_DELEGATION or "auto")
    picked = picked.strip().lower()
    if picked not in cfg.TALK_LIVE_DELEGATIONS:
        picked = "auto"
    if picked != "auto":
        return picked
    return "responses" if cfg.AI_PROVIDER == "openai" else "client"


if __name__ == "__main__":
    # Self-check for the event routing and the turn machinery (no network, no
    # audio device):
    #     python -m loquivox.services.live_talk
    class _Event:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

    from loquivox.services.talk import FINISH_MARKER, TalkSession

    import loquivox.managers.chat as chat_module
    messages: list = []
    chat_module.ChatManager.add_message = staticmethod(
        lambda role, text="": messages.append((role, text)))
    live: list = []
    chat_module.ChatManager.stream = staticmethod(
        lambda role, text: live.append((role, text)))

    # An append never exceeds one event's budget, and never loses a word.
    long = ("Le train part à huit heures. " * 200).strip()
    pieces = _chunks(long)
    assert len(pieces) > 1 and all(len(p) <= APPEND_CHARS for p in pieces), \
        [len(p) for p in pieces]
    assert "".join(p.replace(" ", "") for p in pieces) == long.replace(" ", "")
    assert _chunks("") == [] and _chunks("court") == ["court"]
    # No sentence boundary at all must still split rather than overflow.
    assert all(len(p) <= APPEND_CHARS for p in _chunks("a" * (APPEND_CHARS * 3)))

    talk = LiveTalk(TalkSession())
    talk._handle(_Event(type="session.output_audio.delta",
                        delta=base64.b64encode(b"\x01\x02").decode()))
    assert talk._audio.qsize() == 1

    # Deltas reach the bubble live and are concatenated exactly as received.
    talk._handle(_Event(type="session.input_transcript.delta", delta="je voudrais"))
    talk._handle(_Event(type="session.input_transcript.delta", delta=" un mail"))
    assert talk._live["user"] == "je voudrais un mail", talk._live
    assert not talk._session.turns, "a fragment was taken for a turn"

    # A half-typed marker never reaches the screen.
    talk._handle(_Event(type="session.output_transcript.delta",
                        delta=f"D'accord. {FINISH_MARKER}"))
    assert "[[" not in live[-1][1], live

    # There is no turn-completed event: the gap closes the turn, and only for
    # the speaker who fell silent.
    talk._last_delta["user"] = time.monotonic() - 99
    talk.tick()
    assert "user" not in talk._live and "assistant" in talk._live, talk._live
    assert talk._session.turns[-1]["content"] == "je voudrais un mail"
    # …and closing the assistant's reads the marker, once, where every engine
    # reads it.
    talk._last_delta["assistant"] = time.monotonic() - 99
    talk.tick()
    assert talk.done, "the end-of-briefing marker was not read"
    assert "[[" not in talk._session.turns[-1]["content"]

    # A turn still open when the session ends is not thrown away.
    tail = LiveTalk(TalkSession())
    tail._handle(_Event(type="session.input_transcript.delta", delta="dernier mot"))
    tail._flush_turns()
    assert tail._session.turns[-1]["content"] == "dernier mot"

    # A finish phrase ends the briefing, on the accumulated deltas.
    phrase = LiveTalk(TalkSession())
    phrase._handle(_Event(type="session.input_transcript.delta", delta="j'ai fini"))
    phrase._flush_turns()
    assert phrase.done, "the finish phrase was not heard"

    # A client delegation carries no task text, so it must reach the backend as
    # a request to reason over the conversation — and only once per ID.
    consulted: list = []
    deleg = LiveTalk(TalkSession())
    deleg._session.consult = lambda: (consulted.append(1), "il fait 12 degrés")[1]
    sent: list = []
    deleg._append = lambda kind, content, delegation_id=None: sent.append(
        (kind, content, delegation_id))
    event = _Event(type="session.delegation.created",
                   delegation=_Event(id="item_1", type="delegation", target="client"))
    deleg._on_delegation(event)
    deleg._on_delegation(event)          # a repeat must not start it twice
    for _ in range(200):
        if sent:
            break
        time.sleep(0.01)
    assert sent == [("commentary", "il fait 12 degrés", "item_1")], sent
    assert len(consulted) == 1, consulted
    # A "responses" delegation is OpenAI's to run: nothing happens on this side.
    sent.clear()
    deleg._on_delegation(_Event(type="session.delegation.created",
                                delegation=_Event(id="item_2", target="responses")))
    time.sleep(0.05)
    assert not sent, sent

    # The nested Responses envelope routes by the INNER type, and only a
    # completed function call starts a desk.
    class _Desk:
        def __init__(self) -> None:
            self.asked: list = []
            self.plugin = _Event(started="je regarde")

        def ask(self, arguments: str) -> None:
            self.asked.append(arguments)

    desk = _Desk()
    resp = LiveTalk(TalkSession())
    resp._session.desks.get = lambda name: desk if name == "lookup" else None
    # The stub reply is scheduled on the session's loop, which is not running
    # here: capture the coroutine instead of awaiting it, so what is checked is
    # the real ``_tool_started`` being built for the real call.
    scheduled: list = []
    real_schedule = asyncio.run_coroutine_threadsafe

    def _capture(coro, loop):
        coro.close()
        scheduled.append(coro.__qualname__)

    asyncio.run_coroutine_threadsafe = _capture
    # …and the SDK hands the nested event over as a plain dict, not a model.
    resp._handle(_Event(type="response.event", delegation_id="item_3",
                        event={"type": "response.output_text.delta",
                               "delta": "ignored"}))
    assert not desk.asked, "an outer-type dispatch leaked through"
    resp._handle(_Event(type="response.event", delegation_id="item_3",
                        event={"type": "response.completed",
                               "response": {"output": []}}))
    assert not desk.asked, "an empty terminal output started something"
    resp._handle(_Event(type="response.event", delegation_id="item_3",
                        event={"type": "response.output_item.done",
                               "item": {"type": "function_call",
                                        "name": "lookup", "call_id": "call_1",
                                        "arguments": '{"question": "x"}'}}))
    asyncio.run_coroutine_threadsafe = real_schedule
    assert desk.asked == ['{"question": "x"}'], desk.asked
    assert scheduled == ["LiveTalk._tool_started"], scheduled

    # "auto" follows the configured provider; forcing either overrides it.
    cfg = config_module.CFG
    for provider, choice, expected in (("groq", "auto", "client"),
                                       ("openai", "auto", "responses"),
                                       ("openai", "client", "client"),
                                       ("groq", "responses", "responses"),
                                       ("groq", "nonsense", "client")):
        config_module.CFG = cfg.__class__(**{**cfg.__dict__,
                                             "AI_PROVIDER": provider,
                                             "TALK_LIVE_DELEGATION": choice})
        assert resolve_delegation() == expected, (provider, choice)
    config_module.CFG = cfg

    print("✓ live talk routing OK")
