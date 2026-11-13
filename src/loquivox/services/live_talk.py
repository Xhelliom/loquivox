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
import dataclasses
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import sounddevice as sd

import loquivox.config as config_module
from loquivox.i18n import _
from loquivox.services import mcp_tools, media
from loquivox.services.audio import resolve_input_device
from loquivox.services.talk import _strip_marker, echo_note, user_said_done, user_said_stop
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
#: silence this long ends a reply; shorter silence is a pause in it and plays
#: like the rest. Silence rather than an absence of deltas: gpt-live-1 never
#: stops sending, it streams 100 ms frames at real time for as long as it hears
#: the microphone. Longer than a pause inside a reply — measured up to 0.7 s over
#: five real sessions — so a plugin's answer never lands in one, and validated
#: at this value in real use.
AUDIO_IDLE: float = 1.0
#: loudest sample (int16) a delta may hold and still be silence. Between replies
#: the frames are mostly exact zeros, but not always: measured, about four a
#: minute carry a peak of 1–7, seconds away from any speech (13–16k). Counted as
#: audio, those would hold a reply open at random.
SILENT_PEAK: int = 64
#: Audio in hand before playback starts, and again after the buffer ran dry.
#: This server streams at real time rather than ahead of it (measured: 100 ms
#: frames, up to ~230 ms between two), so a player with nothing in hand clicks
#: on every late frame — which the Realtime engine never has to care about, its
#: server sending a reply faster than it is spoken. The frames make this a step,
#: not a dial: up to 100 ms is met by the first frame alone and plays like no
#: buffer at all (measured on one 25 s stream: 42 gaps from 0 to 100 ms), while
#: 101–200 ms waits for a second frame (4 gaps) — adding ~100 ms, not 200.
PREBUFFER: float = 0.2
#: longest the reader waits for ``session.closed`` once ``close()`` asked
CLOSE_TIMEOUT: float = 5.0
#: Every append (instructions, thinking, commentary) is capped at 500 tokens by
#: the API. Counting them properly would mean a tokenizer for a model we do not
#: have one for, so this is a deliberately pessimistic character budget — ~3.2
#: characters per token, against the ~4 that English averages and the ~3.5 that
#: French does. Overshooting costs a second append; undershooting costs the
#: whole message.
APPEND_CHARS: int = 1500
#: Appended to the session's instructions, so the voice model knows where the
#: thinking ``push_context`` sends comes from. Without it, a screen description
#: landing a second into the call was answered out loud as though the user had
#: read it: the model is full duplex, so nothing makes it wait for the user.
QUIET_CONTEXT: str = (
    "During the call, background may reach you silently in your thinking: a "
    "description of the user's screen, or the text they had selected. The "
    "application captured it on its own — the user has not said it, read it "
    "aloud or asked about it. Never speak because it arrived and never describe "
    "it unprompted: wait for the user to speak, then use it to understand them."
)
#: Sent as thinking beside a delegation answer while a plugin is still at work,
#: because that answer is a stub and the real one cannot be handed over until
#: ``push_result`` finds a gap. Nothing otherwise tells the voice model to leave
#: one: measured on ``gpt-live-1``, a call whose answer was ready within the
#: second reached it 23 s later, because it had kept the floor for all of them
#: — restating the brief as a question its own answer had already settled.
AWAIT_RESULT: str = (
    "A result you are waiting on is still being produced and will reach you "
    "shortly. Say the line you have just been given, then stop and wait in "
    "silence: do not restate it, do not recap the conversation, and do not ask "
    "the user to confirm anything. Speak again when the result arrives, or "
    "when the user does."
)


def _is_audible(pcm: bytes) -> bool:
    """Whether a PCM16 frame holds anything louder than ``SILENT_PEAK``."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
    return samples.size > 0 and int(np.abs(samples.astype(np.int32)).max()) > SILENT_PEAK


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
                 instructions: str = "", delegation: Optional[str] = None,
                 backend_model: str = "", web_search: str = "") -> None:
        cfg = config_module.CFG
        self._session = session
        #: float32 blocks sent ahead of the microphone (companion: the wake-word tail)
        self.prefix: List[np.ndarray] = []
        self._model = model or cfg.TALK_LIVE_MODEL
        self._voice = voice or cfg.TALK_LIVE_VOICE
        self._instructions = ((instructions or session.system_prompt())
                              + "\n\n" + QUIET_CONTEXT)
        self._delegation = resolve_delegation(delegation)
        self._backend_model = backend_model or cfg.TALK_LIVE_BACKEND_MODEL
        self._web_search = web_search or cfg.TALK_LIVE_WEB_SEARCH
        #: turn being transcribed right now, per role: text and when the last
        #: delta for it arrived. There is no end-of-turn event, so this pair is
        #: the whole turn machinery (see ``tick``).
        self._live: Dict[str, str] = {}
        session.settle = self._settle
        self._consulted_turns = 0
        self._last_delta: Dict[str, float] = {}
        #: the jitter buffer, all under ``_audio_lock``: output chunks as
        #: (PCM, audible), how far into the first one playback is, the bytes in
        #: hand, and whether playback is running or refilling
        self._audio_lock = threading.Lock()
        self._chunks: "deque" = deque()
        self._offset = 0
        self._queued = 0
        self._primed = False
        #: audible chunks not yet fully played. The buffer itself is no answer:
        #: streamed silence keeps it busy.
        self._audible = 0
        #: when the last audible output-audio delta arrived. There is no event
        #: saying a reply is finished, so this timestamp is what ``_quiet``
        #: reads. Starts in the past: a session that has said nothing is not
        #: mid-sentence.
        self._last_audio: float = 0.0
        #: ``(who, level 0..1)`` for the companion's bubble: "in" = microphone,
        #: "out" = what the speakers are playing
        self.on_level: Optional[Callable[[str, float], None]] = None
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
        #: every client delegation this session has taken on. Kept for the
        #: whole session, not just while the work runs: a delegation ID is one
        #: request, so a repeated event for one already answered must not
        #: consult again — and an in-flight-only guard would let a fast
        #: backend be asked twice for the same thing.
        self._seen: set = set()
        # Per delegation: the calls of one backend response, see _on_response_event.
        self._calls: dict = {}
        self._lock = threading.Lock()
        #: set once the briefing is over — a finish phrase or the model's marker
        self.done = False
        self.error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._out: Optional[sd.RawOutputStream] = None
        self._mic: Optional[sd.InputStream] = None
        #: ``session.closed`` has arrived, and how long the reader waits for it
        #: once ``close()`` has asked
        self._closed = False
        self._close_deadline: Optional[float] = None
        self._opened = time.monotonic()
        #: last transcript or audible audio, never cleared (``idle_for``)
        self._last_activity = self._opened

    def idle_for(self) -> float:
        """Seconds since anyone last said or heard anything (audible audio, transcript)."""
        if self._live:
            return 0.0
        return time.monotonic() - self._last_activity

    # --- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 20.0) -> None:
        """
        Open the session, the microphone and the speakers. Raises on failure,
        which the caller reads as "fall back to the cascade".

        Longer default than the Realtime engine's, and waiting on a different
        thing. There the session is usable as soon as our ``session.update``
        has gone out; here ``session.start`` is a *request* — "wait for
        `session.started` before sending audio or application commands" — so
        what is waited on is the server's answer, a round trip later. Releasing
        on the send instead would hand back a session that rejects everything
        put to it.
        """
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        if self.error is not None:
            raise self.error
        if self._conn is None or not self._started.is_set():
            raise RuntimeError("Live session did not start in time")
        media.mic_opened(duck=not STATE.companion_session)
        self._mic = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                   device=resolve_input_device(),
                                   callback=self._on_audio)
        self._mic.start()
        for block in self.prefix:   # what was said before the session was open
            self._send_block(block)
        # The speakers after the microphone, never before: on a Bluetooth
        # headset the microphone is what switches it to HFP, and a speaker
        # stream opened first sits on the A2DP sink that switch tears down.
        media.await_headset_mic()
        self._out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                       blocksize=BLOCK, callback=self._on_play)
        self._out.start()
        print(f"🔊 Live talk — {self._model} / {self._voice} · "
              f"delegation: {self._delegation}"
              + (f" ({self._backend_model})"
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
        if self._out is not None:
            self._out.close()      # discards whatever is still buffered
            self._out = None
        if self._loop.is_closed() or self._close_deadline is not None:
            return   # already over, or already closing
        try:
            if self._conn is not None:
                # The documented close: ask with session.close, and let the
                # reader carry on until session.closed, which brings the final
                # usage — the transport goes when the session task leaves its
                # `async with`. Bounded, so a server that never answers cannot
                # hold the socket open.
                self._close_deadline = time.monotonic() + CLOSE_TIMEOUT
                asyncio.run_coroutine_threadsafe(self._conn.session.close(), self._loop)
            else:
                self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass   # closed between the check and the call

    def _quiet(self) -> bool:
        """
        True when nothing more of the current reply is on its way to the ears.

        Same three callers as the Realtime engine, asking the same question:
        the microphone gate (what it hears is still echo), the end of the
        session (the last sentence has not been heard yet) and ``push_result``
        (there is no gap to slip an answer into).

        Asked differently, though. The Realtime engine has an event for "the
        server has finished sending"; here there is none, so the absence of
        *audible* deltas for ``AUDIO_IDLE`` stands in for it (the server itself
        never stops sending — see ``SILENT_PEAK``) — and no audible frame may
        still wait in the jitter buffer (``_audible``), since "finished
        sending" was never the same question as "finished being heard".
        """
        return (self._audible == 0
                and time.monotonic() - self._last_audio >= AUDIO_IDLE)

    def finished_speaking(self) -> bool:
        """
        True once the reply that ended the briefing has actually been heard.

        The Live API emits no end-of-output-audio event at all — "GPT-Live does
        not emit an output-audio-done event" — so this leans entirely on
        ``_quiet()``, and is bounded so a session can never be held open by
        audio that stopped arriving.
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

    #: the user's turn is "finished" once no delta came for this long
    SETTLE_QUIET = 0.5
    SETTLE_MAX = 4.0
    #: how long a delegation waits for a sentence whose transcription has not
    #: even begun (no end-of-input-transcription event exists to wait for)
    SETTLE_FIRST = 3.0
    #: same, while an action waits for its yes: a one-word "oui" transcribes late
    SETTLE_FIRST_YES = 8.0

    def _settle(self, expect_new: bool = False) -> None:
        """
        Record the user's sentence before anything reads ``turns``.

        ``session.delegation.created`` can land before the request has finished
        transcribing — or before its first delta. Flushing at once leaves it out
        of the brief and the writing pass writes something generic. So wait
        (bounded) while deltas are still arriving; and with ``expect_new`` (a
        delegation answers an utterance) also wait for the first delta if no
        user turn was recorded since the previous delegation.
        """
        now = time.monotonic
        start = now()
        first = self.SETTLE_FIRST_YES if mcp_tools.awaiting(self._session) else self.SETTLE_FIRST
        deadline = start + max(self.SETTLE_MAX, first)
        while now() < deadline:
            with self._lock:
                last = self._last_delta.get("user") if "user" in self._live else None
            if last is not None:
                if now() - last >= self.SETTLE_QUIET:
                    break
            elif (not expect_new or self._session.user_turns > self._consulted_turns
                  or now() - start >= first):
                break
            time.sleep(0.05)
        self._flush_turns()
        if expect_new:
            self._consulted_turns = self._session.user_turns

    def _flush_turns(self) -> None:
        """Close whatever is still open — the session is ending or delegating."""
        for role in ("user", "assistant"):
            self._close_turn(role)

    def _close_turn(self, role: str) -> None:
        """Record one finished turn, if there is one."""
        with self._lock:
            text = (self._live.pop(role, "") or "").strip()
            self._last_delta.pop(role, None)
            self._last_activity = time.monotonic()
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
            self._last_delta[role] = self._last_activity = time.monotonic()
        ChatManager.stream(role, _strip_marker(text) if role == "assistant" else text)

    def _on_user_said(self, text: str) -> None:
        """Record a spoken user turn, and notice if it ends the briefing."""
        from loquivox.managers.chat import ChatManager

        ChatManager.add_message("user", f"🗣️ {text}")
        self._session.add_user(text)
        # Only a briefing ends on a phrase: a chat ends on the key, and
        # "vas-y, écris ça" is the model's cue to call the write tool.
        if self._session.write and user_said_done(text):
            self.done = True
        self._stop_if_asked(text)

    @staticmethod
    def _stop_if_asked(text: str) -> None:
        """Companion only: "tais-toi" closes the session, unanswered."""
        if not STATE.companion_session or not user_said_stop(text):
            return
        # Never quote the user. The stop phrase is already in the session's turns
        # (add_user ran first), so it is a misfire only if it is the only one.
        from loquivox.services.talk import log_companion_cut
        session = STATE.talk_session
        log_companion_cut("voix", max(0, (session.user_turns if session else 0) - 1))
        STATE.talk_click.put("cancel")

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

        Under ``responses`` a tool's answer never comes this way: the backend
        is holding the line for its result, so ``_answer_call`` replies to the
        call directly. A *source* is the exception in both modes — nothing
        called it, so it has no call to reply to, and its desk is swept here
        whichever side does the thinking.

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
            ChatManager.add_message("note", _(plugin.result_note))
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
        if self._stop:
            return          # the session is closing: nobody is left to hear the answer
        with self._lock:
            if did in self._seen:
                return
            self._seen.add(did)
        threading.Thread(target=self._consult, args=(did,), daemon=True).start()

    def _consult(self, delegation_id: str) -> None:
        """Run the backend for one client delegation and speak its answer."""
        from loquivox.managers.chat import ChatManager

        if self._stop:
            return
        try:
            self._settle(expect_new=True)
            ChatManager.add_message("note", _("🧠 Thinking…"))
            started = time.time()
            answer = self._session.consult()
            took = time.time() - started
            if self._stop:   # closed while thinking: the answer would be sent to nobody
                print(f"🧠 Delegation dropped (session closed after {took:.1f}s)")
                return
            if not answer:
                # Never just return: the voice model asked for help and is
                # waiting, `_seen` means it will not ask again for this one,
                # and the bubble is showing "Thinking…". Silence would be
                # permanent. Quietly, through `thinking`, so the model knows
                # the lookup failed and can say so in its own words — or not.
                print(f"⚠️  Backend returned nothing for {delegation_id}")
                self._append("thinking",
                             "The backend could not answer that request. Say so "
                             "plainly rather than guessing, and carry on.",
                             delegation_id=delegation_id)
                return
            print(f"🧠 Delegation ({took:.1f}s): '{answer}'")
            if self._session.desks.coming():
                # Before the commentary, not after: thinking is silent, the
                # commentary is what sets the model talking, and the rule has
                # to be in hand by then. See AWAIT_RESULT.
                self._append("thinking", AWAIT_RESULT, delegation_id=delegation_id)
            self._append("commentary", answer, delegation_id=delegation_id)
        except Exception as e:
            print(f"⚠️  Delegation failed: {e}")

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
        with the plugin's *real* result, on a thread. See ``_answer_call``.
        """
        inner = getattr(event, "event", None)
        if not isinstance(inner, dict):
            return
        did = str(getattr(event, "delegation_id", "") or "")
        if inner.get("type") == "response.completed":
            # No call registered: nothing is owed, and no group is left behind.
            if did in self._calls:
                self._calls[did]["done"] = True
                self._maybe_continue(did)
            return
        if inner.get("type") != "response.output_item.done":
            return
        item = inner.get("item")
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return
        call_id = str(item.get("call_id") or "")
        # Registered here, on the loop, before any thread can answer: a fast
        # first result must see that its siblings are still owed.
        group = self._calls.setdefault(did, {"open": set(), "done": False, "n": 0})
        group["open"].add(call_id)
        group["n"] += 1
        # An unknown tool is still a call: the server wants an output for each.
        desk = self._session.desks.get(str(item.get("name") or ""))
        threading.Thread(target=self._answer_call, daemon=True,
                         args=(desk, call_id, str(item.get("arguments") or ""),
                               did)).start()

    #: How long to wait for ``response.completed`` once every known call has
    #: its output. Not observed on a paused response (the log keeps no events),
    #: so the continuation must never depend on it alone.
    CALLS_GRACE = 1.5

    def _maybe_continue(self, did: str) -> None:
        """
        ``response.create`` once every call has its output *and* the response
        is complete. Without ``response.completed`` the same continuation goes
        out ``CALLS_GRACE`` after the last output — shortcut: a call announced
        later than that would be missed, the case the completion event exists for.
        """
        group = self._calls.get(did)
        if not group or not group["n"] or group["open"]:
            return
        if group["done"]:
            del self._calls[did]
            asyncio.run_coroutine_threadsafe(self._continue(), self._loop)
        else:
            self._loop.call_later(self.CALLS_GRACE, self._grace, did, group["n"])

    def _grace(self, did: str, n: int) -> None:
        group = self._calls.get(did)
        if group and group["n"] == n and not group["open"]:
            group["done"] = True
            self._maybe_continue(did)

    async def _continue(self) -> None:
        await self._conn.response.create()

    def _answer_call(self, desk, call_id: str, arguments: str, did: str = "") -> None:
        """
        Run one plugin and give the backend its actual answer.

        The other two engines hand back the ``started`` stub here and deliver
        the real answer later, between turns — because there the conversation
        loop is *blocked* while a plugin works, and a stub is what keeps the
        assistant from going silent for five seconds.

        Live is the one place that does not apply. "Live speech and delegated
        work continue independently": the voice model keeps the conversation
        going on its own while the backend waits, so waiting costs nothing
        anyone can hear. And it buys the thing the stub route loses — the
        backend model actually *reads* the result and reasons from it, instead
        of being told "I'm looking into it" and then overhearing the answer
        secondhand through what the voice said out loud.

        So this blocks on purpose, on its own thread. The call is kept out of
        ``session.turns`` like everywhere else (a "tool" role would leak into
        the F4 history and the generation prompt) but the answer goes in, so
        the writing pass has the facts whichever engine ran.

        A plugin that never returns leaves this response pending for good.
        That is the honest cost of the trade and it is bounded: the voice model
        is unaffected and the session ends normally. A deadline here would only
        answer "gave up" to a backend that may still be handed the real result
        moments later, which is worse than one dangling call.
        """
        from loquivox.managers.chat import ChatManager

        try:
            if desk is None:
                raise LookupError("unknown tool")
            message = desk.run_now(arguments)
        except Exception as e:
            # `run_now` is documented never to raise, and the belt here is not
            # redundant: the backend response stays paused until this call is
            # answered, so a plugin that broke the contract would hang it for
            # the rest of the session.
            print(f"⚠️  Plugin call {call_id} failed: {e}")
            message = None
        if message is not None:
            self._session.turns.append(message)
            if desk.plugin.result_note:
                ChatManager.add_message("note", _(desk.plugin.result_note))
        # Something must go back either way: the backend response is paused
        # until this call is answered, so an unusable result is still an answer.
        output = message["content"] if message is not None else "No result."
        try:
            asyncio.run_coroutine_threadsafe(self._tool_result(call_id, output, did),
                                             self._loop)
        except Exception as e:
            print(f"⚠️  Call {call_id} result not sent to the backend: {e}")

    async def _tool_result(self, call_id: str, output: str, did: str = "") -> None:
        """
        Submit one tool result; the last one of its response continues it.

        Both halves are required: "appending a function result does not
        automatically continue the response". But ``response.create`` before
        every call of a parallel response has its output closes the session
        (``function_call_outputs_required``), so it waits for the last one.
        """
        await self._conn.response.item.create(item={
            "type": "function_call_output", "call_id": call_id, "output": output})
        group = self._calls.get(did)
        if group:
            group["open"].discard(call_id)
            self._maybe_continue(did)

    # --- microphone → session ---------------------------------------------

    def _echo_report(self) -> None:
        """Print what the last reply measured, and start the next one clean."""
        if self._gate is None:
            return
        print(self._gate.report)
        echo_note(self._gate)
        self._gate = None
        self._held.clear()

    def _open_mic(self, mono: np.ndarray) -> List[np.ndarray]:
        """
        ``TALK_ECHO_GUARD = "off"``: the microphone is never held back, nor
        replaced by zeros. The server never says the user cut in, so a plain
        detector at ``TALK_VAD_THRESHOLD`` (no calibration, no hold) is still
        what empties the speakers — it only listens, it gates nothing.
        """
        cfg = config_module.CFG
        if self._quiet():
            self._gate = None
            return [mono]
        if self._gate is None:
            self._gate = EchoGate(RATE, threshold=cfg.TALK_VAD_THRESHOLD, headset=True)
        self._gate.feed(mono)
        if self._gate.speech_seconds >= cfg.TALK_BARGE_IN_MS / 1000.0:
            print(f"✋ Cut off — {self._gate.loudest:.3f}")
            self._gate = None
            self._flush_audio()
        return [mono]

    def _mic_chunks(self, mono: np.ndarray) -> List[np.ndarray]:
        """
        What of this captured chunk may reach the model.

        Identical in shape to the Realtime engine's and for the same reason —
        the server hears the microphone continuously and reads the assistant's
        own voice off the speakers as an interruption — with one addition it
        cannot have: when the gate opens, this also flushes the speakers.
        There is no ``speech_started`` here to do it, so the gate is both
        halves of a barge-in, deciding it and acting on it.

        And one difference it cannot do without — **send zeros, never
        nothing**. gpt-live-1 requires a continuous input stream and paces its
        speech on it. Measured on the same spoken question: microphone flowing,
        an 18 s reply with no pause; gated blocks not sent at all, a stall every
        few words (five of 1.9 s) — it waits for input, the gate reopens on its
        pause, it resumes, the gate shuts; gated blocks sent as zeros of the
        same length, one 0.7 s pause. This had been found once already and
        never written down. The Realtime engine's copy of this gate rightly
        sends nothing — never align the two; the self-check pins it.
        """
        cfg = config_module.CFG
        if cfg.TALK_ECHO_GUARD == "off":
            return self._open_mic(mono)
        now = time.monotonic()
        if not self._quiet():
            self._echo_until = now + ECHO_HANGOVER
        if now >= self._echo_until:
            self._echo_report()
            return [mono]
        if not cfg.TALK_BARGE_IN:
            return [np.zeros_like(mono)]
        if self._gate is None:
            self._gate = EchoGate(RATE, threshold=cfg.TALK_VAD_THRESHOLD,
                                  margin=cfg.TALK_BARGE_IN_MARGIN,
                                  headset=cfg.TALK_ECHO_GUARD == "headset")
        self._gate.feed(mono)
        if self._gate.speech_seconds < cfg.TALK_BARGE_IN_MS / 1000.0:
            self._held.append(mono)
            kept = sum(len(c) for c in self._held) / RATE
            while len(self._held) > 1 and kept > cfg.TALK_BARGE_IN_KEEP:
                kept -= len(self._held.pop(0)) / RATE
            return [np.zeros_like(mono)]
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
        if self.on_level:
            self.on_level("in", min(1.0, float(np.sqrt(np.mean(mono ** 2))) * 6))
        try:
            if STATE.viz_queue.qsize() < 5:
                STATE.viz_queue.put_nowait(mono)
        except Exception:
            pass
        # Never empty: a held block comes back as zeros (see _mic_chunks), and
        # a barge-in replays the held audio on top, a moment ahead of real time
        # — the model catches up.
        for chunk in self._mic_chunks(mono):
            self._send_block(chunk)

    def _send_block(self, chunk: np.ndarray) -> None:
        pcm = base64.b64encode(float32_to_pcm16(chunk)).decode("ascii")
        try:
            asyncio.run_coroutine_threadsafe(
                self._conn.session.input_audio.append(audio=pcm), self._loop)
        except Exception:
            pass  # the loop is closing — the session is on its way out

    # --- session → speakers ------------------------------------------------

    def _on_play(self, outdata, frames: int, time_info, status) -> None:
        """
        PortAudio callback: hand the sound card exactly what it asks for.

        A jitter buffer, and a pull rather than a push: a thread writing into
        the stream cannot tell how much the sound card already holds, so it
        cannot tell a late frame from a full buffer. Here nothing plays until
        ``PREBUFFER`` is in hand, and running dry means refilling that much
        before going on — one short pause instead of a click per late frame.
        """
        need, done = len(outdata), 0
        with self._audio_lock:
            if not self._primed and self._queued >= int(PREBUFFER * RATE) * 2:
                self._primed = True
            while self._primed and done < need and self._chunks:
                chunk, audible = self._chunks[0]
                take = min(need - done, len(chunk) - self._offset)
                outdata[done:done + take] = chunk[self._offset:self._offset + take]
                done += take
                self._offset += take
                self._queued -= take
                if self._offset == len(chunk):
                    self._chunks.popleft()
                    self._offset = 0
                    if audible:
                        self._audible -= 1
            if done < need:
                outdata[done:] = bytes(need - done)
                self._primed = False
        if self.on_level:
            pcm = np.frombuffer(outdata, dtype=np.int16)[:done // 2].astype(np.float32)
            self.on_level("out", min(1.0, float(np.sqrt(np.mean(pcm ** 2))) / 32768 * 6) if pcm.size else 0.0)

    def _flush_audio(self) -> None:
        """Drop everything not yet played — the user has started speaking."""
        with self._audio_lock:
            self._chunks.clear()
            self._offset = self._queued = self._audible = 0
            self._primed = False
        self._last_audio = 0.0   # this reply is over; nothing more is coming

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
                "model": self._backend_model,
                "instructions": cfg.TALK_LIVE_BACKEND_PROMPT,
            }
            # Native tools this backend is being given, as capability names.
            # The only one today, but the shape is what keeps the rule general:
            # a plugin steps aside for a native tool by *declaring the same
            # capability*, never by being named here.
            native = (("web_search",) if self._web_search == "native"
                      else ())
            # The Responses wire shape for a function is the flat one the
            # Realtime sessions use, so the plugin's existing definition serves
            # both and nothing is written a third time.
            # The backend's tools are fixed when the session opens, so nothing
            # can be deferred here: everything MCP offers is declared up front.
            self._session.desks.load_all()
            tools = self._session.desks.realtime_tools_except(native)
            # Nothing here executes a native tool: it runs on OpenAI's side and
            # never comes back as a function call to answer.
            tools += [{"type": name} for name in native]
            if tools:
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
                # Read until session.closed rather than until close() is
                # called: the documented close is a request, answered with the
                # final usage.
                async for event in conn:
                    self._handle(event)
                    if self._closed or (self._close_deadline is not None and
                                        time.monotonic() >= self._close_deadline):
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
                chunk = base64.b64decode(delta)
                audible = _is_audible(chunk)
                with self._audio_lock:
                    if audible:
                        self._audible += 1
                        self._last_audio = self._last_activity = time.monotonic()
                    self._chunks.append((chunk, audible))
                    self._queued += len(chunk)
        elif etype == "session.input_transcript.delta":
            self._on_delta("user", getattr(event, "delta", "") or "")
        elif etype == "session.output_transcript.delta":
            self._on_delta("assistant", getattr(event, "delta", "") or "")
        elif etype == "session.delegation.created":
            self._on_delegation(event)
        elif etype == "response.event":
            self._on_response_event(event)
        elif etype == "session.started":
            # The session is open for business only now, and this is the only
            # thing that says so — hence it, not the send, releases start().
            self._started.set()
            self._ready.set()
        elif etype == "session.closed":
            self._stop = True
            self._closed = True
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
    return "responses" if STATE.ai_provider == "openai" else "client"


if __name__ == "__main__":
    # Self-check for the event routing and the turn machinery (no network, no
    # audio device):
    #     python -m loquivox.services.live_talk
    class _Event:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

    def _wait_for(predicate, timeout: float = 3.0) -> bool:
        """Give a worker thread a moment to get there."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

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

    # `start()` releases on `session.started` and on nothing else: the API
    # rejects everything sent before it, so releasing on our own send would
    # hand back a session that cannot be used.
    assert not talk._ready.is_set() and not talk._started.is_set()
    talk._handle(_Event(type="session.started"))
    assert talk._started.is_set() and talk._ready.is_set(), \
        "start() would not have been released by the server"

    talk._handle(_Event(type="session.output_audio.delta",
                        delta=base64.b64encode(b"\x01\x02").decode()))
    assert len(talk._chunks) == 1

    # A reply ends with no event to say so, so silence has to end it. Nothing
    # resets a flag here: `_quiet` reads the clock, and if it did not, the
    # microphone would stay gated for the rest of the session after the
    # assistant's first word.
    assert not talk._quiet(), "a queued reply read as silence"
    talk._primed = True         # …and the sound card takes it
    talk._on_play(bytearray(2), 1, None, None)
    assert not talk._quiet(), "the reply was over the instant a delta landed"
    talk._last_audio = time.monotonic() - (AUDIO_IDLE + 0.1)
    assert talk._quiet(), "the reply never ended — the microphone stays shut"
    # The server streams exact-zero frames between replies for as long as it
    # hears the microphone: played, but not a reply.
    talk._handle(_Event(type="session.output_audio.delta",
                        delta=base64.b64encode(bytes(4800)).decode()))
    assert len(talk._chunks) == 1 and talk._quiet(), "streamed silence kept the reply open"
    talk._flush_audio()
    # …and a barge-in ends it at once rather than after the idle window.
    talk._handle(_Event(type="session.output_audio.delta",
                        delta=base64.b64encode(b"\x01\x02").decode()))
    talk._flush_audio()
    assert talk._quiet(), "the assistant kept talking over the user"
    assert talk._audible == 0 and not talk._chunks and talk._queued == 0

    # The jitter buffer: nothing plays before PREBUFFER is in hand, then the
    # sound card gets exactly what it asks for, and running dry refills first.
    # half the prebuffer, loud enough to count as audio (SILENT_PEAK)
    frame = (1000).to_bytes(2, "little") * (int(PREBUFFER * RATE) // 2)
    out = bytearray(len(frame))
    delta = base64.b64encode(frame).decode()
    talk._handle(_Event(type="session.output_audio.delta", delta=delta))
    talk._on_play(out, len(out) // 2, None, None)
    assert not any(out) and talk._queued == len(frame), "played before the buffer filled"
    talk._handle(_Event(type="session.output_audio.delta", delta=delta))
    talk._on_play(out, len(out) // 2, None, None)
    assert bytes(out) == frame and talk._audible == 1, "a full buffer did not play"
    talk._on_play(out, len(out) // 2, None, None)
    talk._on_play(out, len(out) // 2, None, None)
    assert not any(out) and not talk._primed and talk._audible == 0, \
        "ran dry and kept going instead of refilling"
    # …and the play callback reports what it played, or the bubble's water
    # never moves with the assistant's voice.
    levels = []
    talk.on_level = lambda who, x: levels.append((who, x))
    talk._handle(_Event(type="session.output_audio.delta", delta=delta))
    talk._handle(_Event(type="session.output_audio.delta", delta=delta))
    talk._on_play(out, len(out) // 2, None, None)
    assert levels and levels[-1][0] == "out" and levels[-1][1] > 0, "_on_play reports no output level"
    talk.on_level = None
    talk._flush_audio()
    talk._last_audio = 0.0

    # While a reply plays the gate holds the microphone back — and the model
    # must still hear something: gpt-live-1 speaks in step with its input, so
    # sending nothing stalls its voice after a few words.
    sent: list = []

    class _Input:
        def append(self, audio: str) -> None:
            sent.append(audio)

    talk._conn = _Event(session=_Event(input_audio=_Input()))
    real_rct = asyncio.run_coroutine_threadsafe
    asyncio.run_coroutine_threadsafe = lambda coro, loop: None
    talk._last_audio = time.monotonic()      # a reply is playing
    talk._on_audio(np.full((480, 1), 0.001, dtype=np.float32), 480, None, None)
    asyncio.run_coroutine_threadsafe = real_rct
    assert len(sent) == 1 and not base64.b64decode(sent[0]).strip(b"\0"), \
        "the held microphone went out as nothing — the voice stalls"
    talk._conn, talk._gate, talk._held, talk._echo_until = None, None, [], 0.0
    talk._last_audio = 0.0

    # Between replies the frames are silence — mostly exact zeros, sometimes a
    # peak of a few units. Neither is a reply: counted as one, nothing is ever
    # handed back and the microphone stays behind the echo gate.
    faint = base64.b64encode(np.full(2400, 7, dtype=np.int16).tobytes()).decode()
    talk._last_audio = time.monotonic() - (AUDIO_IDLE + 0.1)
    talk._handle(_Event(type="session.output_audio.delta", delta=faint))
    assert talk._audible == 0 and talk._quiet(), "a faint idle frame read as a reply"
    # A pause inside a reply is not its end, played out or not.
    talk._last_audio = time.monotonic() - 0.5
    assert not talk._quiet(), "a half-second pause read as the end of the reply"
    talk._flush_audio()
    assert not _is_audible(bytes(4800)) and not _is_audible(b"") and _is_audible(b"\x00\x80")

    # A gated microphone still keeps time: gpt-live-1 needs zeros, never nothing.
    talk._last_audio = time.monotonic()               # a reply is playing
    echo = np.full(480, 0.3, dtype=np.float32)
    out = talk._mic_chunks(echo)
    assert len(out) == 1 and len(out[0]) == len(echo) and not out[0].any(), \
        "a gated block was dropped: gpt-live-1 needs zeros, never nothing"
    talk._held.clear(); talk._gate = None; talk._flush_audio()

    # Guard off: a reply is playing and the microphone still goes through as
    # it is — never zeros — and talking over it empties the speakers.
    saved_cfg = config_module.CFG
    config_module.CFG = dataclasses.replace(saved_cfg, TALK_ECHO_GUARD="off")
    try:
        talk._last_audio = time.monotonic()
        talk._chunks.append((bytes(4800), True)); talk._audible = 1
        talk._queued = 4800
        voice = np.full(480, 0.3, dtype=np.float32)
        flushed = False
        for _i in range(60):
            out = talk._mic_chunks(voice)
            assert len(out) == 1 and out[0] is voice, "guard off held the microphone"
            flushed = flushed or not talk._chunks
        assert flushed, "guard off: talking over the reply did not stop it"
        assert talk._mic_chunks(voice)[0].any(), "guard off sent zeros"
    finally:
        config_module.CFG = saved_cfg
        talk._gate = None; talk._flush_audio()

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
    assert _wait_for(lambda: bool(sent))
    # A repeat must not consult again — including after the first one finished,
    # which an in-flight-only guard would wave through.
    deleg._on_delegation(event)
    time.sleep(0.05)
    assert sent == [("commentary", "il fait 12 degrés", "item_1")], sent
    assert len(consulted) == 1, consulted
    # A stub answer handed over while a plugin is still working must carry the
    # rule to stop after saying it — and carry it *first*, the commentary being
    # what sets the model talking. See AWAIT_RESULT.
    sent.clear()
    held = LiveTalk(TalkSession())
    held._session.consult = lambda: "je rédige"
    held._session.desks.coming = lambda: True
    held._append = lambda kind, content, delegation_id=None: sent.append(kind)
    held._on_delegation(_Event(type="session.delegation.created",
                               delegation=_Event(id="item_8", target="client")))
    assert _wait_for(lambda: len(sent) == 2)
    assert sent == ["thinking", "commentary"], sent
    # A backend with nothing to say must still answer: the voice model asked
    # for help, `_seen` means it will not ask twice, and silence is for ever.
    sent.clear()
    mute = LiveTalk(TalkSession())
    mute._session.consult = lambda: ""
    mute._append = lambda kind, content, delegation_id=None: sent.append(
        (kind, delegation_id))
    mute._on_delegation(_Event(type="session.delegation.created",
                               delegation=_Event(id="item_9", target="client")))
    assert _wait_for(lambda: bool(sent)), "a failed lookup left the model waiting"
    # Quietly, though: the model decides whether that is worth saying aloud.
    assert sent == [("thinking", "item_9")], sent

    # A "responses" delegation is OpenAI's to run: nothing happens on this side.
    sent.clear()
    deleg._on_delegation(_Event(type="session.delegation.created",
                                delegation=_Event(id="item_2", target="responses")))
    time.sleep(0.05)
    assert not sent, sent

    # The nested Responses envelope routes by the INNER type, and the backend
    # gets the plugin's REAL answer back — it is holding the line for it.
    class _Desk:
        """Stands in for a desk whose plugin answers instantly."""

        def __init__(self, answer: str) -> None:
            self.seen: list = []
            self._answer = answer
            self.plugin = _Event(name="lookup", started="je regarde",
                                 result_note="")

        def run_now(self, arguments: str):
            self.seen.append(arguments)
            return ({"role": "system", "content": self._answer} if self._answer
                    else None)

    desk = _Desk("il fait 12 degrés")
    resp = LiveTalk(TalkSession())
    resp._session.desks.get = lambda name: desk if name == "lookup" else None
    # The reply is scheduled on the session's loop, which is not running here:
    # capture the coroutine instead of awaiting it, so what is checked is the
    # real ``_tool_result`` being built with the real output.
    outputs: list = []
    real_schedule = asyncio.run_coroutine_threadsafe

    def _capture(coro, loop):
        outputs.append((coro.__qualname__, coro.cr_frame.f_locals.get("output")))
        coro.close()

    asyncio.run_coroutine_threadsafe = _capture
    try:
        # …and the SDK hands the nested event over as a plain dict, not a model.
        resp._handle(_Event(type="response.event", delegation_id="item_3",
                            event={"type": "response.output_text.delta",
                                   "delta": "ignored"}))
        assert not desk.seen, "an outer-type dispatch leaked through"
        resp._handle(_Event(type="response.event", delegation_id="item_3",
                            event={"type": "response.completed",
                                   "response": {"output": []}}))
        assert not desk.seen, "an empty terminal output started something"
        resp._handle(_Event(type="response.event", delegation_id="item_3",
                            event={"type": "response.output_item.done",
                                   "item": {"type": "function_call",
                                            "name": "lookup", "call_id": "call_1",
                                            "arguments": '{"question": "x"}'}}))
        assert _wait_for(lambda: bool(outputs))
        assert desk.seen == ['{"question": "x"}'], desk.seen
        # The backend reads the answer itself — not the "working on it" stub.
        assert outputs == [("LiveTalk._tool_result", "il fait 12 degrés")], outputs
        # …and the writing pass gets it too, while the call itself never does.
        assert resp._session.turns == [{"role": "system",
                                        "content": "il fait 12 degrés"}], \
            resp._session.turns

        # A plugin with nothing to say must still answer: the backend response
        # stays paused until this call is replied to.
        outputs.clear()
        empty = _Desk("")
        resp._session.desks.get = lambda name: empty
        resp._handle(_Event(type="response.event", delegation_id="item_4",
                            event={"type": "response.output_item.done",
                                   "item": {"type": "function_call",
                                            "name": "lookup", "call_id": "call_2",
                                            "arguments": "{}"}}))
        assert _wait_for(lambda: bool(outputs))
        assert outputs[0][1], "the backend was left waiting for ever"

        # Parallel calls of one response, through the REAL ``_tool_result`` on a
        # running loop: a single response.create, after the last output, whether
        # ``response.completed`` lands before the outputs, after them, or never
        # (a Held plugin answers like any other, and an unknown tool still gets one).
        asyncio.run_coroutine_threadsafe = real_schedule
        wire: list = []

        class _Wire:
            @staticmethod
            async def create() -> None:
                wire.append("create")

            class item:
                @staticmethod
                async def create(item) -> None:
                    await asyncio.sleep(0.05 if item["call_id"] == "p1" else 0)
                    wire.append(item["call_id"])

        resp._conn = _Event(response=_Wire)
        resp.CALLS_GRACE = 0.3
        threading.Thread(target=resp._loop.run_forever, daemon=True).start()
        held = _Desk("Run launch_lot? — ask the user")    # a Held result is an output too
        resp._session.desks.get = lambda name: {"lookup": desk, "held": held}.get(name)

        def _call(did, cid, name):
            resp._loop.call_soon_threadsafe(resp._handle, _Event(
                type="response.event", delegation_id=did,
                event={"type": "response.output_item.done",
                       "item": {"type": "function_call", "name": name,
                                "call_id": cid, "arguments": "{}"}}))

        def _completed(did):
            resp._loop.call_soon_threadsafe(resp._handle, _Event(
                type="response.event", delegation_id=did,
                event={"type": "response.completed", "response": {}}))

        # a) completed arrives while the outputs are still on their way
        for cid, name in (("p1", "lookup"), ("p2", "held"), ("p3", "ghost")):
            _call("r_a", cid, name)
        _completed("r_a")
        assert _wait_for(lambda: wire.count("create") == 1), wire
        assert wire[-1] == "create" and sorted(wire[:-1]) == ["p1", "p2", "p3"], wire
        # b) completed arrives after every output
        wire.clear()
        for cid in ("q1", "q2"):
            _call("r_b", cid, "lookup")
        assert _wait_for(lambda: len(wire) == 2), wire
        _completed("r_b")
        assert _wait_for(lambda: wire[-1:] == ["create"]), wire
        # c) completed never comes: the grace period continues, once
        wire.clear()
        for cid in ("s1", "s2"):
            _call("r_c", cid, "lookup")
        assert _wait_for(lambda: wire[-1:] == ["create"]), wire
        time.sleep(0.6)
        assert wire.count("create") == 1 and len(wire) == 3, wire
        # d) a response without calls leaves nothing behind
        _completed("r_d")
        time.sleep(0.1)
        assert "r_d" not in resp._calls, resp._calls
        resp._loop.call_soon_threadsafe(resp._loop.stop)
    finally:
        asyncio.run_coroutine_threadsafe = real_schedule

    # Choosing OpenAI's search stands down the plugin that says it provides
    # one — and nothing else. Every plugin that duplicates no native tool is
    # on the table either way, which is the whole point of the capability
    # rather than a list of names.
    cfg = config_module.CFG

    def _with(**overrides):
        config_module.CFG = cfg.__class__(**{**cfg.__dict__, **overrides})

    def _tools(session):
        return LiveTalk(session)._config()["delegation"]["responses"]["tools"]

    searcher = {"type": "function", "name": "searcher"}
    other = {"type": "function", "name": "other"}
    tooled = TalkSession()
    # A stand-in for the desks: `realtime_tools` is a property on the real one,
    # and the exclusion it applies is checked in services/plugins.py itself.
    tooled.desks = _Event(
        realtime_tools=[searcher, other],
        realtime_tools_except=lambda native: (
            [other] if "web_search" in native else [searcher, other]))
    try:
        _with(TALK_LIVE_DELEGATION="responses", TALK_LIVE_WEB_SEARCH="plugin")
        assert _tools(tooled) == [searcher, other], _tools(tooled)

        _with(TALK_LIVE_DELEGATION="responses", TALK_LIVE_WEB_SEARCH="native")
        picked = _tools(tooled)
        assert picked == [other, {"type": "web_search"}], picked
        assert searcher not in picked, "two searches were offered at once"

        # …and the setting means nothing in client mode, where there is no
        # native table to put anything on and the plugin is all there is.
        _with(TALK_LIVE_DELEGATION="client", TALK_LIVE_WEB_SEARCH="native")
        assert LiveTalk(tooled)._config()["delegation"] == {"type": "client"}
    finally:
        config_module.CFG = cfg

    # "auto" follows the provider Settings picked (STATE); forcing either overrides it.
    for provider, choice, expected in (("groq", "auto", "client"),
                                       ("openai", "auto", "responses"),
                                       ("openai", "client", "client"),
                                       ("groq", "responses", "responses"),
                                       ("groq", "nonsense", "client")):
        STATE.ai_provider = provider
        config_module.CFG = cfg.__class__(**{**cfg.__dict__,
                                             "TALK_LIVE_DELEGATION": choice})
        assert resolve_delegation() == expected, (provider, choice)
    config_module.CFG = cfg

    print("✓ live talk routing OK")
