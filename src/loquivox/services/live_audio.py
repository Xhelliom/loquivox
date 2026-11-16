"""
What the two server-side Live engines share: the microphone gate and the jitter
buffer, which know nothing of either protocol (``live_talk`` for gpt-live-1,
``gemini_live`` for Google).

A mixin and not a helper object on purpose: the rest of each engine reads the
same few attributes (``_chunks``, ``_audible``, ``_last_audio``, ``_gate``…) and
their tests pin them. A class sets ``IN_RATE`` (what the microphone is captured
at and the server is sent) and ``OUT_RATE`` (what comes back), and provides
``_quiet()`` (nothing more of the reply is on its way to the ears) and
``_send_block(chunk)``.

**A gated microphone sends zeros, never nothing** — both engines need a
continuous input stream (see ``LiveTalk._mic_chunks``'s history); the Realtime
engine's own gate rightly sends nothing, so never align the two.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional

import numpy as np

import loquivox.config as config_module
from loquivox.state import STATE
from loquivox.services.talk import _strip_marker, echo_note, user_said_done, user_said_stop
from loquivox.services.vad import EchoGate

#: playback blocks; small enough that a barge-in cuts within ~40 ms
BLOCK: int = 1024
#: how long the microphone stays gated after the last block was written
ECHO_HANGOVER: float = 0.25
#: Audio in hand before playback starts, and again after the buffer ran dry.
#: A server that streams at real time rather than ahead of it (measured on
#: gpt-live-1: 100 ms frames, up to ~230 ms between two) leaves a player with
#: nothing in hand clicking on every late frame; up to 100 ms is met by the first
#: frame alone, 101–200 ms waits for a second — a step, not a dial.
PREBUFFER: float = 0.2
#: longest a closing session waits for the last reply to finish playing
SPEECH_TAIL_TIMEOUT: float = 15.0


class LiveAudio:
    """Microphone gate + playback jitter buffer for a Live engine."""

    IN_RATE: int = 24000
    OUT_RATE: int = 24000

    def _init_turns(self) -> None:
        #: the turn being transcribed right now, per role: text and when the
        #: last fragment for it arrived
        self._live: Dict[str, str] = {}
        self._last_delta: Dict[str, float] = {}
        self._lock = threading.Lock()
        #: set once the briefing is over — a finish phrase or the model's marker
        self.done = False
        self._speech_deadline: Optional[float] = None
        #: last transcript or audible audio, never cleared (``idle_for``)
        self._last_activity = time.monotonic()

    def _init_audio(self) -> None:
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
        #: when the last audible output chunk arrived; starts in the past: a
        #: session that has said nothing is not mid-sentence
        self._last_audio: float = 0.0
        #: ``(who, level 0..1)`` for the companion's bubble: "in" = microphone,
        #: "out" = what the speakers are playing
        self.on_level: Optional[Callable[[str, float], None]] = None
        self._gate: Optional[EchoGate] = None
        self._held: List[np.ndarray] = []
        self._echo_until: float = 0.0

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
            self._gate = EchoGate(self.IN_RATE, threshold=cfg.TALK_VAD_THRESHOLD, headset=True)
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
            self._gate = EchoGate(self.IN_RATE, threshold=cfg.TALK_VAD_THRESHOLD,
                                  margin=cfg.TALK_BARGE_IN_MARGIN,
                                  headset=cfg.TALK_ECHO_GUARD == "headset")
        self._gate.feed(mono)
        if self._gate.speech_seconds < cfg.TALK_BARGE_IN_MS / 1000.0:
            self._held.append(mono)
            kept = sum(len(c) for c in self._held) / self.IN_RATE
            while len(self._held) > 1 and kept > cfg.TALK_BARGE_IN_KEEP:
                kept -= len(self._held.pop(0)) / self.IN_RATE
            return [np.zeros_like(mono)]
        chunks = self._held + [mono]
        if self._held:
            print(f"✋ Cut off — {self._gate.loudest:.3f} over an echo peaking "
                  f"at {self._gate.echo_peak:.3f}")
            self._held = []
            self._flush_audio()   # the half the server does for Realtime
        return chunks

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
            if not self._primed and self._queued >= int(PREBUFFER * self.OUT_RATE) * 2:
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

    def idle_for(self) -> float:
        """Seconds since anyone last said or heard anything (audible audio, transcript)."""
        if self._live:
            return 0.0
        return time.monotonic() - self._last_activity

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
