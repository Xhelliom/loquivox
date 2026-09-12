# CLAUDE.md

Guidance for AI agents (and humans) working in this repository.

## What Loquivox is

A Linux voice assistant & AI companion. It runs in the background, listens for
**global hotkeys** (via `evdev`, so it works under both X11 and Wayland), and
exposes four recording modes plus a few session actions. Speech is transcribed
by Whisper (Groq Cloud by default, or local `whisper.cpp` offline), then routed
to dictation / chat / rewrite / vision. Results are typed at the cursor and/or
shown in a GTK overlay, with optional TTS read-back.

- Language: Python ≥ 3.8, GTK3 via PyGObject. UI overlays use `gtk-layer-shell`
  on Wayland; the chat overlay is a WebKit2 webview.
- AI backend: Groq Cloud (chat, vision, TTS). Needs `GROQ_API_KEY`.
- Entry point: `loquivox` → `loquivox.app:main` (or `python -m loquivox`).

## Hotkeys & modes (source of truth: `src/loquivox/config.py` → `HOTKEY_DEFS`)

| Default key      | Mode id      | What it does                                              |
|------------------|--------------|----------------------------------------------------------|
| `R-Alt` / `F3`   | `dictation`  | Transcribe speech → type at cursor                       |
| `F4`             | `ai`         | Spoken conversation about the screen + selected text     |
| `F6`             | `talk`       | Spoken conversation → one generated text (reworks the selection if any) |
| `F9`             | `pin`        | Toggle chat overlay "always on top"                      |
| `F10`            | `tts`        | Toggle TTS read-back of AI answers                       |
| `Esc`            | `cancel`     | Abort active recording / in-flight transcription         |
| `Space`          | `pause`      | Pause / resume the current recording                     |
| *(unbound)*      | `refine`     | Stop recording, then pick this dictation's refine level  |

- The first spec in each list is the primary key; the rest are aliases (incl.
  media keys). Specs support chords like `"ALT+SPACE"` or `"CTRL+SHIFT+D"`.
- Recording modes — the ones whose key records while it is held — are listed in
  `CFG.RECORDING_MODES`; `KeyboardHandler._is_recording_mode` is the only test
  (only `dictation` today), and everything else (`pin`, `tts`, `cancel`,
  `pause`, `refine`, `talk`, `ai`) is a session action whose key-up does nothing.
- `ai` (F4) is a talk session with `write=False`: same worker, same bubble,
  same engines, but `TalkSession.system_prompt()` swaps in `TALK_CHAT_PROMPT`
  (no marker protocol, no depth clause), the screen is always captured, the
  selection copied at the key press rides along in `context_clause()`, and the
  turns are poured into `STATE.conversation_history` at the end so the bubble's
  typed input continues the same discussion. Nothing is generated or typed —
  unless the user says one of `TALK_WRITE_PHRASES` ("écris ça"): `user_asked_write`
  flips `session.write`, the conversation loop returns and `_talk_worker` runs
  the same writing phase as F6 on everything said so far.
- F6 copies the selection too, as the text to rework: "select, F6, say how,
  done" replaces the old one-shot rewrite (F7) and vision (F8) modes, whose
  shared `_ai_action_worker` and Cairo review panel are gone with them. **T**
  during either session (`_talk_take_selection`) replaces that selection with
  whatever is highlighted now, the way S replaces the screen description — it
  travels in `context_clause()`, so both engines pick it up the same way.
- `CFG.MODES` is a *different* table: the overlay's appearance. `talk` has an
  entry there for its look while owning the microphone through its own session
  rather than through the hold-key, which is why it is not in `RECORDING_MODES`
  and never goes through `ModeHandler.process()` — see the talk-mode section.
- Hold-to-talk by default; `STATE.toggle_mode` switches to press-to-start/stop.
- Hotkeys are user-overridable in `config.toml` `[hotkeys]` and live-editable in
  the Settings dialog (which calls `KeyboardHandler.reload_hotkeys()`).

When you add or rename a hotkey/mode, update **all** of: `config.py`
(`HOTKEY_DEFS`, `HOTKEY_DESCRIPTIONS`, `MODES`), the dispatch table in
`handlers/mode.py` `process()`, `settings_dialog.py`, `README.md`, and the
landing page `docs/index.html` (its `ACTIONS`/feature cards). These drift
easily. The startup banner and the hover hotkey bar both read
`HOTKEY_DESCRIPTIONS`, so they follow on their own.

## Architecture (`src/loquivox/`)

```
app.py            main(): load secrets, print hotkeys, start keyboard thread + GTK tray
config.py         Config dataclass + CFG singleton; TOML loading; chord parsing
state.py          AppState + SettingsManager + STATE singleton (runtime mutable state)
api.py            Groq client init
secrets.py        load_secrets(): UI-managed API keys → environment
decorators.py     safe_execute (error guard), run_on_main_thread (GLib marshalling)
platform/         Session detect + backend factory; X11 (xdotool/xclip/gnome-screenshot)
                  vs Wayland (wtype/wl-clipboard/grim). base.py = ABCs.
transcription/    Pluggable speech-to-text: factory + dispatcher; backends for
                  groq, whispercpp (local), deepgram, openai_realtime; streaming.py
services/         audio (record+transcribe), ai (chat+vision), tts (engine per
                  CFG.TTS_ENGINES: Groq Orpheus EN / OpenAI multilingual /
                  Piper local via tts_piper.py), realtime_talk (speech-to-speech
                  talk mode), live_talk (gpt-live-1: speech in/out, thinking
                  delegated back to our own model),
                  clipboard, image, postprocess (LLM refinement of dictation),
                  talk (conversation → one text), vad (energy trigger),
                  turn_detector (Smart Turn v3 semantic end-of-turn)
managers/         history, chat (overlay state + auto-hide), overlay (recording indicator)
ui/               recording_overlay, chat_overlay (WebKit2; voice + typed input via
                  JS→Python `signal` IPC → ModeHandler.submit_text_chat; doubles
                  as the talk bubble, see below), settings_dialog, tray,
                  hotkey_bar (top-edge tab that expands on hover into the hotkey list)
handlers/         mode.py (routes a transcript per mode), keyboard.py (evdev listener)
```

### Key flow
`keyboard.py` (listener thread) detects a chord → starts `AudioService` +
`OverlayManager` → on release, `ModeHandler.process_audio_async` /
`process_stream_async` runs transcription **in a worker thread** → result is
marshalled to the GTK main loop via `GLib.idle_add` → `ModeHandler.process()`
applies stale-guard + hallucination-guard, then dispatches to
`_handle_dictation`, the only recording mode left. Everything spoken to the
AI goes through the talk session (F4 and F6, below).

## Talk mode (`handlers/mode.py`, `services/talk.py`, `services/vad.py`)

A whole conversation, not a single utterance — so it does NOT use the
`process_audio_async` → `process()` path. `KeyboardHandler._on_press("talk")`
spawns `ModeHandler._talk_worker`, which owns everything until the session ends.

**Three engines, one promise** (`CFG.TALK_ENGINE`, dispatched in
`_talk_conversation`): a conversation you can interrupt.
- `cascade` (default) — STT → LLM → TTS, described below. Four slots, each
  local or cloud: transcription (`transcription/`), turn detection
  (`services/turn_detector.py`), the LLM (`api.get_ai_client`), the voice
  (`CFG.TTS_ENGINES`). ~1.8s before you hear a reply.
- `realtime` — `services/realtime_talk.py`: one OpenAI Realtime session holds
  the whole conversation, speech in and speech out. The server owns turn-taking
  AND interruption; ~1.0s, and the reply keeps the prosody of speech. What is
  lost is the choice of LLM. Anything that stops it opening falls back to the
  cascade. Note that a reply's *transcript* is complete while its *audio* is
  still queued, so `done` lands mid-sentence: the loop breaks on
  `finished_speaking()`, never on `done` alone, or the assistant is cut off in
  the middle of "Parfait, je vais rédiger…".
- `live` — `services/live_talk.py`: one `gpt-live-1` session, which *splits*
  the job rather than holding it. See the Live engine section below.

The last two share one loop, `_talk_converse_server`, because both answer the
same five questions per tick — context to hand over, plugin answer to slip in,
turn to close, error, and is the briefing over *and heard*. Either way the
turns land in the same `TalkSession`, because the writing pass is a text
completion that never learns which engine ran. That is the whole reason the
split works: Loquivox produces a *text*, so only the conversation half is
interchangeable.

The cascade, step by step:

0. If `CFG.TALK_SCREENSHOT` (off by default — it uploads a window), `_talk_look`
   spawns `_talk_screen_worker`: it captures, has the vision model describe it
   once, and hands the text to `TalkSession.set_context`. It runs beside the
   first turn on purpose — waiting for it would delay the microphone — so the
   recording overlay may be in the shot and the prompt tells the model to
   ignore it. **S** during a conversation calls `_talk_look` again (both
   engines), replacing a description that has gone stale; the key exists only
   while the capture is enabled, and `KeyboardHandler.talk_hints()` is built
   from the same condition so the strip can never offer a dead key.

   **Framing is the whole ballgame, and `max_px` is not the knob.** The
   provider gives an image a fixed token budget whatever its size — measured on
   Groq, 898 prompt tokens for the same capture at 640, 1280 and 2048 — so a 4K
   desktop squeezed into it comes back as fluent, confident invention, while
   one window of it reads correctly at the same budget and the same model.
   Hence `TALK_SCREENSHOT_REGION = "window"` by default, served by
   `ScreenshotBackend.take_window_screenshot` (niri's own IPC on Wayland,
   `gnome-screenshot -w` on X11) and falling back to the whole screen, out
   loud, wherever that is not available. `"cursor"` still exists for X11 and
   Hyprland, where `pointer_position` can answer.

   niri's action also always copies the shot to the clipboard with no flag to
   skip it. `TALK_SCREENSHOT_RESTORE_CLIPBOARD` puts the old *text* back and is
   off by default: the session ends by leaving its generated text there anyway,
   so the only loss is something from a minute ago. Whether that matters is the
   user's call, not a default's — like every knob on this feature, it is in
   Settings → Talk.

   Two traps live here. niri's screenshot action returns *before* the PNG is on
   disk (rc=0 at 39 ms, the file at 100 ms), so `_wait_for_png` polls for the
   IEND chunk — checking the size settles instead would hand over a half-written
   capture whenever the writer pauses, and returning on the exit code alone
   reads as "this session cannot frame a window" and silently falls back. And a
   reasoning vision model can spend its entire completion budget thinking and
   answer with an empty string, which is NOT "nothing relevant on screen" —
   they print differently on purpose, because conflating them hides the one
   clue that the model is the problem. `qwen3.6-27b` did exactly that (~2000
   invisible-but-billed tokens, then hallucinated content); `MODEL_VISION`
   defaults to `qwen3.8-27b`, which answers in ~0.4s and says it cannot read
   something rather than inventing it.

   The description reaches the two engines differently, from one wording:
   `TalkSession.context_clause()` is appended per call as a system message in
   the cascade, and pushed into the Realtime session's `instructions` by
   `RealtimeTalk.push_context()` — a partial `session.update`, so the voice and
   turn detection survive it. That predicate is polled from the conversation
   loop (the capture lands after the session opened) and tracks the pushed
   *text*, not a flag: a flag would swallow every re-capture after the first.

   Debugging: every step prints (`👁️  Screen context …`, with how long it
   took), and `LOQUIVOX_KEEP_SCREENSHOT=1` writes `<path>.sent.png` — the bytes
   actually uploaded, cropped and downscaled, which is the only artefact a
   legibility problem shows up in. The raw capture never does.
1. `GrabbedKeys` (keyboard.py) keeps the input devices open for the *entire*
   session — a key pressed while the model is thinking is still queued when the
   next wait starts — but takes the exclusive grab only inside
   `with keys.exclusive():`, around the waits themselves. Never hold a grab
   across a network call: a hung request would freeze the user's keyboard.
2. Each turn: `_talk_listen` records until the turn ends (see below),
   `_talk_transcribe` transcribes (streaming backends included) and applies the
   hallucination guard, `TalkSession.reply` answers, and `_talk_speak` speaks
   the reply — blocking on purpose, the next turn must not record the
   assistant's own voice — while listening for the user cutting in.
3. The briefing ends on Enter, on a short spoken "j'ai fini" (`user_said_done`,
   matched on the transcript before any API call), or on the model appending
   its `[[WRITE]]` marker. `CFG.TALK_FINISH_ON_PHRASE` and
   `CFG.TALK_FINISH_BY_MODEL` toggle those two independently (the key is never
   a toggle); `TalkSession.system_prompt()` appends the protocol clause for that
   pair — after `CFG.TALK_INSTRUCTIONS`, the user's own persona/language
   instructions from Settings → Talk, which are *added* to the base prompt
   rather than replacing it — so neither an overridden `system_prompt` nor a
   set of instructions can drop the protocol. The marker is
   stripped before the reply is spoken, shown or stored.
4. `_talk_generate` turns the conversation into one text and shows it in the
   review panel; `_deliver_talk_text` types it (accept only) and leaves it on
   the clipboard. `V` in that panel goes back to the conversation, brief intact.

The conversation lives in the `TalkSession`, never in `STATE.conversation_history`
— a long spoken briefing must not pollute the F4 chat context. `STATE.vad` is the
detector the audio callback feeds while a turn is being recorded; `STATE.talk_active`
guards against a second session. Knobs live under `[talk]` in config.toml.

### The Live engine (`services/live_talk.py`)

`gpt-live-1` is not another Realtime. The voice model hears, speaks and owns
the rhythm of the exchange, and **delegates** anything that needs reasoning.
Which is the point: with `delegation: {"type": "client"}` the thing it
delegates to is ours, so the choice of LLM comes back — the voice is OpenAI's,
the thinking is whatever `[models] chat` points at, plugins included.

`CFG.TALK_LIVE_DELEGATION` (`resolve_delegation()`, shared with the Settings
dialog so a greyed field can never disagree with the session) picks the side,
once, when the session opens — `delegation.type` is immutable and changing it
means a new session:

- `client` — `TalkSession.consult()`: our model, `desks.chat_tools` on the
  table, answer back through `session.commentary.append`.
- `responses` — OpenAI's managed backend (`TALK_LIVE_BACKEND_MODEL`). Real,
  named tool calls arrive inside a `response.event` envelope; **dispatch on the
  nested type**, which the SDK hands over as a plain `dict`, never on the outer
  one, and only `response.output_item.done` identifies a call.
- `auto` — `responses` when the provider is openai, else `client`. Both values
  can be forced, and that is why they exist: comparing the two modes honestly
  means holding the model constant, which `auto` alone cannot do.

**The trade, stated once so it is not rediscovered.**
`session.delegation.created` carries "metadata, not task text" — no tool name,
no arguments, not even the utterance that caused it, and it can arrive *before*
that sentence finishes transcribing (hence `_flush_turns()` before consulting).
So in client mode the voice model no longer picks a tool: ours does, one level
down. `tests/test_plugins.py` drives both routes with a plugin the core has
never heard of.

Three things the Live API does not give us, each answered rather than worked
around:

- **No turn-completed event at all.** "Transcript deltas have no item ID or
  authoritative turn-completed event." `tick()` closes a turn on a gap of
  `TALK_LIVE_TURN_GAP`, per role — the two speakers may legitimately overlap,
  so a role is only ever closed by *its own* silence. Deltas are concatenated
  exactly as received; trimming them or inserting spaces is documented as
  wrong, not merely unidiomatic.
- **No `speech_started`.** The server never says the user cut in, so the
  `EchoGate` in `_mic_chunks` is both halves of a barge-in: it decides it *and*
  flushes the speakers. `push_result` reads an open user turn as "someone has
  the floor", the transcript being the only evidence available.
- **Instructions are frozen** after `session.started` (16,384 tokens) and every
  append is capped at 500. So the Realtime engine's `session.update` trick has
  no equivalent: `push_context()` goes through `session.thinking.append`
  instead — the screen description is something to *know*, and an appended
  *instruction* "can interrupt the model's current speech", which is precisely
  what a capture landing mid-sentence must not do. `_chunks()` splits anything
  longer on sentence boundaries; nothing is truncated.

Cost is two meters, not one: $0.05/min of voice **plus** whatever the backend
spends. Sessions have a duration limit (`session.closed` with
`reason: "expired"`) and there is no resumption — a replacement session must be
given its context back.

### Plugins (`services/plugins.py`)

The extension surface both engines share, and `services/research.py` is its
first client. The pattern it generalizes: **one tool** the model may call whose
answer is too slow to wait for, so it comes back *between two turns* rather
than mid-sentence.

A plugin is one `Plugin` dataclass in one file — `name`/`description`/
`parameters` (the chat and Realtime wire shapes are *derived*, never written
twice), `run` (the background work), `message` (its answer as a system
message), `started` (the stub the model speaks from meanwhile), `enabled` (the
config key that gates it), and `settings` (its card in Settings, built with the
dialog's own `_group`/`_field`/`_actions` so it lines up with the rest).
Registering it is a `register()` call at import plus a line in `_MODULES` —
that tuple is the whole "discovery", deliberately: no entry points, no
scanning, nothing loaded from outside the tree.

`TalkSession.desks` is a `Desks`, one `Desk` per *enabled* plugin, snapshotted
when the session opens (a tool appearing mid-conversation is one the model was
never told about). Everything the conversation core does goes through it and
names no plugin: `desks.chat_tools` / `desks.realtime_tools` compose the tool
list, `desks.get(name)` routes a call — `TalkSession._answer` in the cascade,
`RealtimeTalk._handle` on the server event — and `desks.take()` sweeps every
desk for one answer. Adding a plugin therefore touches `talk.py`,
`realtime_talk.py`, `live_talk.py` and `handlers/mode.py` not at all;
`tests/test_plugins.py` pins that by driving a plugin the core has never heard
of through all three engines — including both of the Live engine's delegation
modes, which reach a plugin by opposite routes.

The answer is handed back *between turns only*: the cascade's `_talk_listen`
breaks with `action="result"` when a desk is ready and the VAD has not heard
speech, and `_talk_converse` speaks `TalkSession.reply_with_result`; the
Realtime loop polls `RealtimeTalk.push_result`, which waits for the audio queue
to drain and the server to report speech stopped, then injects a system item
and asks for a response. The result lives in `session.turns` as a system
message so the writing pass has the facts; the tool call and its stub never do
— a "tool" role would leak into the F4 history and the generation prompt.
`AIService.complete` reassembles streamed `tool_calls` for the cascade.

`research` itself: the model asks a question, `run_research` answers it on a
thread with OpenAI Responses + `web_search` or a Groq compound model
(`CFG.RESEARCH_PROVIDER` / `CFG.MODEL_RESEARCH`, Settings → Models → Search),
gated by `CFG.TALK_RESEARCH`.

### The talk bubble (`ui/chat_overlay.py`, `managers/chat.py`)

The chat overlay IS the bubble — `ChatOverlay(talk=True)` / `set_talk_mode()`
re-anchors the same window rather than opening a second one, so the WebView,
its page and the conversation on it all survive the switch. In talk mode it
sits centred just above the recording overlay (bottom-anchored: it grows
upwards, so the newest line never moves), takes its height from its content
via a `ResizeObserver` → `{action:'Resize'}` → `_apply_height`, drops the input
bar (the keyboard is grabbed anyway) and shows the session's own keys.

`ChatManager.set_talk(True)` opens it on the hotkey, before the first word.
The turn in progress is written into a `#live` node by `set_live()` →
`run_javascript`, NEVER by re-rendering: `update_content` reloads the whole
page, which would restart every animation and lose the scroll position. A real
message reloads the page and the live node disappears with it, which is how it
is cleared. Updates are throttled in `ChatManager.stream` (each one carries the
whole turn, so dropping one is free) and deferred until `load-changed` reports
FINISHED, keeping only the last.

Both halves are live: the transcript through `AudioService._on_partial` (so it
needs a streaming backend — with a batch one the user's turn only appears when
it is over), and the reply through `AIService.complete(..., on_delta=)` in the
cascade or the server's `*_transcript.delta` events in Realtime. A half-typed
`[[WRITE]]` must never flash on screen — `_strip_marker` handles the partial
form, and there is a self-check for it.

`set_talk(True)` also calls `ChatManager.clear()`: a session opens on an empty
bubble, because reading the previous conversation's turns as this one's is the
whole confusion. It clears what is on screen only — `answer_history` and the
models' own histories belong to `HistoryManager`, which now routes its own
reset through the same `clear()`.

The end-of-session review lives in the bubble:
`ChatManager.set_result()` adds a message with `role="result"`, which `role`
turns into a CSS class like any other, so the markdown rendering and the copy
button come for free and only the styling is new. It replaces the previous
candidate rather than stacking (R rewrites in place). The key line under it is
`review_hint_line()` from `recording_overlay.py`. `_talk_generate` hides the
recording overlay while reviewing: its hint strip names the conversation's
keys, which are not the ones that apply then.

`_handle_talk` shows the recording overlay before anything that can block —
`GrabbedKeys()` spends ~300–450 ms opening the input devices and the
conversation engine up to a second more, and a key press has to register on
screen before either. `OverlayManager._show_impl` reuses a same-mode window
through `GtkOverlay.reset()` rather than rebuilding it, which is what makes
that possible without the overlay cross-fading with itself — and which also
stops talk mode rebuilding it once per turn.

Opening it is sequenced, not simultaneous: `_handle_talk` defers
`ChatManager.set_talk(True)` by `TALK_BUBBLE_DELAY_MS` so the recording overlay
— the one that has to appear the instant the key is pressed — gets the main
thread and its fade to itself. And `app.py` builds one throwaway `WebKit2.WebView`
on a low-priority idle at startup: the FIRST view in a process costs 110–270 ms
of blocked GTK loop and every one after it costs ~3 ms, so that cost used to
land on whichever hotkey opened the overlay first.

Two things stay off this window on purpose, both compositor cost rather than
Python cost (the GTK loop measures fine either way — the spend is in the
WebProcess and the compositor): no `backdrop-filter`, which would have the
compositor read back and blur everything under a window that resizes as it
fills, and no promoted scroll layer, which would be reallocated on every one of
those resizes. Resizes themselves are coalesced over `RESIZE_COALESCE_MS`, and
the MutationObserver watches `#chat`'s direct children only — routing the live
node through `checkScroll()` would start a dozen smooth-scroll animations a
second, so `set_live` scrolls itself, instantly.

Dictation is the one mode that adds a message with `summon=False`: its answer
is the text landing at the cursor, and a layer-shell window mapping at that
moment takes the keyboard focus `ClipboardService.type_text` aims Ctrl+V at.
For the same reason the bubble asks for `KeyboardMode.NONE` (it has no input
bar and the session holds the keyboard), and `_destroy` clears
`chat_input_focused` — a window torn down while its input box had focus never
emits the blur that would clear it, and `_keep_open()` then answers True
forever: the overlay stops auto-hiding and keeps the focus every later paste
was meant for.

The ✕ on it calls `ChatManager.hide_manual`, which is deliberately stronger
than the auto-hide (that one refuses to fire while pinned, typed into or
mid-conversation — exactly when someone reaches for the close button). It sets
`STATE.chat_hidden`, which `_show_overlay` honours; the pin hotkey or the next
talk session clears it.

### Barge-in (`_talk_speak`)

Talking over a reply cuts it off, the way one does with a person. While the
reply plays, the microphone stays open with `start_recording(stream=False)` —
captured, never transcribed: sending the reply's own echo to an API would be
wrong and paid for. `TTSService.speak` takes two events: `stop` (set on an
interruption, and the PCM stream calls `abort()`, not `stop()`, so what is
still in the sound card is *not* drained) and `started`, set on the first
sample that actually reaches the speakers.

Both engines ask that question through one object, `EchoGate` in
`services/vad.py`: a block counts as speech if it is `TALK_BARGE_IN_MARGIN`
times louder than the echo, measured over the reply's opening 600 ms and then
**frozen**. `VoiceActivityDetector` cannot be reused for it, and the reason is
worth keeping: it keeps tracking, and tracking fails both ways here. Downwards
— its level falls back to the quietest block since — the pauses between the
assistant's own sentences drag it to the room and the next syllable of echo
reads as speech. Upwards, a voice comes up over several blocks, each still
under the bar, so a bar that follows them climbs ahead of the speaker and is
never crossed; that killed barge-in on headsets, where there is no echo to
measure at all.

The Realtime engine needs the gate in front of the microphone itself, and there
it is not optional: the server hears the microphone continuously and cannot be
told what is echo, so it reads the assistant's own voice as an interruption and
stops the reply dead. `RealtimeTalk._mic_chunks` therefore holds the captured
audio back while a reply plays and forwards it only once the gate has heard
`TALK_BARGE_IN_MS`, with `TALK_BARGE_IN_KEEP` seconds of held audio going out
ahead of it — the window `snapshot_tail` replays in the cascade.

The margin is the room and the volume, not the software, so it is a setting
(Settings → Talk) and every reply prints the two numbers it is set from
(`🔉 echo peak … · loudest …`). Sometimes no value works: measured on a
laptop's speakers, a voice beat the echo by ×1.2. The gate then stays shut and
loud speakers are half duplex, which is the failure to prefer — not being able
to interrupt is a nuisance, being cut off mid-sentence every time is unusable —
and acoustic echo cancellation (PipeWire's `libpipewire-module-echo-cancel`,
selected as the microphone) is the only real fix. `TALK_BARGE_IN = false` makes
the gate unconditional.

When the echo alone comes back above `EchoGate.ADVICE_GAIN` × the speech
threshold, that fix is the *only* outcome available, so `services/talk.py`
`echo_note()` says it — once a session (`STATE.echo_advised`), from whichever
engine ran, and into the bubble as a `note` message rather than to stdout
alone: Loquivox usually runs as a service, where nothing printed is ever read.
Without it the user sees an assistant that talks over them and cannot be
stopped, with no reason given.

`started` is what arms the gate in the cascade, and it matters that it is that
event and not the call to `speak()`: the calibration window has to measure the
reply's own echo, so it must not open on the silence before the first sample
reaches the speakers. On a headset it measures a quiet room, the bar stays at
`TALK_VAD_THRESHOLD`, and that is why there is no "do you wear headphones"
setting.

What was heard when the user cut in is not thrown away: `snapshot_tail` keeps
`TALK_BARGE_IN_KEEP` seconds and hands them to the next `_talk_listen` as its
`prefix`, which seeds the recording buffer (the audio callback replays it into
the live session, see `STATE.stream_fed`) and primes the VAD — a turn that
begins mid-sentence has no quiet room to calibrate on, and no priming would
mean waiting out the whole `TALK_IDLE_TIMEOUT`. `TalkSession.mark_interrupted`
appends a marker so the model does not carry on as though all of it was heard.

### How a turn ends (three layers, in priority order)

1. **Server-side** — when the streaming session reports `semantic_turns` (only
   OpenAI Realtime, via `turn_detection: semantic_vad`, and only in talk mode),
   its `turn_ended` is the authority and the local layers are skipped.
2. **Semantic** — `services/turn_detector.py` (Smart Turn v3, ONNX via
   optional `onnxruntime`). The energy VAD is only the *trigger*: on a pause of
   `TALK_SEMANTIC_TRIGGER_MS`, `_talk_turn_complete` asks the model for
   P(finished) over `AudioService.snapshot_tail(8s)`. Below the threshold it
   calls `vad.rearm(TALK_VAD_SILENCE_MS)` and listening continues, bounded by
   `TALK_TURN_MAX_SILENCE_MS`.
3. **Silence** — no onnxruntime / no model / `semantic_turns = false`: the
   `TALK_VAD_SILENCE_MS` pause ends the turn, as it always did.

Underneath all three sits the energy VAD's activation level, and two rules keep
it honest — both fixing the same failure, where the level ends up calibrated on
speech and the rest of the turn then reads as silence (the turn never ends, or
`TALK_TURN_MAX_SILENCE_MS` cuts it mid-sentence):

- **Armed before the session opens.** `AudioService.start_recording` takes a
  `detector` factory and installs it itself, between starting the microphone
  and opening the live transcription session — that open costs ~1s on a cloud
  backend, and a detector armed after it begins calibrating a second into a
  sentence the user started immediately.
- **A ceiling on calibration.** `VoiceActivityDetector.max_level` caps what
  400 ms of listening may conclude (`CALIBRATION_MAX_GAIN` × threshold): a room
  is never as loud as the voice in it. The level may then only come back down,
  so one real pause repairs a bad guess. `math.inf` opts out — the barge-in
  detector in `_talk_speak` calibrates on the assistant's echo on purpose.

Every layer degrades to the next one; a missing model is a printed warning, never
an error. `services/_whisper_features.py` is vendored from Pipecat (BSD-2) — keep
it in sync with upstream instead of editing it.

## Logs & the diagnostic report (`diagnostics.py`)

The app logs through `print()`, and `install_log_file()` (called first thing
in `app._run`) tees stdout/stderr into `~/.local/state/loquivox/loquivox.log`
— timestamped, rotated at 2 MB, one generation kept — so a session started
from the autostart entry leaves a trace. Uncaught tracebacks land there too:
Python prints them to `sys.stderr`, which is the tee. `LOQUIVOX_LOG_FILE`
moves it, an empty value disables it.

`build_report()` is what a user attaches to a bug: environment, tools, config,
key *presence*, the log tail and the user journal, all through `redact()`.
Keys, e-mail, home dir, user/host names, IPs and URL query strings are always
removed; the user's own words are elided by default (`keep_content=True`
keeps them). Content is recognised by *shape* — `label: 'sentence'` at the
end of a line, and the `Screen context (…): ` / `Research (…): ` prefixes —
so **a new print that quotes what the user said or saw must follow one of
those shapes** (or add a rule to `_CONTENT_PATTERNS`), and
`tests/test_diagnostics.py` pins both directions: no key format survives, and
no error message is eaten. Reachable as `loquivox --report` (handled in
`app.main` *before* GTK is imported, so it works where GTK is the problem)
and as tray → "Diagnostic report…" / Settings → Support (`ui/report_dialog.py`,
which shows the text before anything is copied or saved). The same tab's
"View log…" (`LogDialog`) is the raw log, live and *unredacted* — it is for
the user's own eyes and never leaves the machine on its own; "Save log…" /
`loquivox --export-log` (`export_log()`) is the whole log, both generations,
through the same `redact()`.

## Threading rules (important)

- The keyboard listener and all network/transcription work run **off** the GTK
  main thread. Never touch GTK widgets directly from those threads.
- Marshal UI work back with `GLib.idle_add` or the `@run_on_main_thread`
  decorator (see `decorators.py`). `OverlayManager.hide()` is already safe.
- A `recording_generation` counter (`STATE`) is the stale-guard: a newer
  recording bumps it so an older in-flight transcription is dropped before it
  pastes. Preserve this when editing the async paths.

## Config

- Defaults live in the frozen `Config` dataclass (`config.py`). User overrides:
  `~/.config/loquivox/config.toml`, layered in `_build_config()`. See
  `config.example.toml` for the documented schema.
- A missing/malformed TOML is ignored — the app must always start on defaults.
- UI-toggled prefs (TTS voice, color scheme, etc.) live in
  `~/.config/loquivox/settings.json` via `SettingsManager` in `state.py`.
- Transcription settings apply live (dispatcher reconfigured); some startup-only
  settings (e.g. overlay size) need a restart — see `reload_config()` docstring.
- Settings → **Models** is the one tab for *who does the work*, in the order the
  sound travels: presets → conversation engine → transcription → chat/vision →
  voice. A preset writes several of those at once (config.toml *and*
  settings.json), so `_refresh_engine_widgets` puts every combo back in step —
  and it must read `config_module.CFG`, since `reload_config()` rebinds the
  singleton the module-level `CFG` import no longer points at.
- Every page in `settings_dialog.py` is built from three helpers, never by
  hand: `_group()` (a title, one dim line of prose, a framed body), `_field()`
  (one label → control row, joined to the page's `Gtk.SizeGroup` so the label
  column lines up across separate cards) and `_actions()` (registers the
  group's writer and packs its status line, always last). Hand-packing a row is
  what let each tab drift into its own spacing. `_field()` also ellipsizes
  combos — a `Gtk.Window` can never be narrower than its widest child, so one
  long microphone name used to set the window's width.
  `tests/test_settings_layout.py` pins all of that.
- **There is exactly one Apply**, in the action bar `_create_dialog()` packs
  below the notebook — outside every page's `ScrolledWindow`, so no amount of
  scrolling or tab-switching can take it off screen. It runs *every* registered
  writer, not the visible tab's: a group never owns a button, it calls
  `_actions()` with the classmethod that writes its settings. Seven Apply
  buttons scattered over six tabs is what this replaced, and the question they
  raised — "which one do I click?" — has no good answer when a single change
  can touch two cards. Each writer still reports into its own status line;
  `_apply_all` only counts the unhappy ones (a status opening `❌`/`⚠️`) into
  the one line beside the button, because those lines are a tab and a scroll
  away from the click. A writer that raises is caught so it cannot stop the
  ones after it.

## Transcription backends

`CFG.BACKEND`: `groq` (default), `whispercpp` (offline, local binary),
`deepgram` / `openai_realtime` (live streaming), or `auto`. `CFG.FALLBACK_BACKEND`
(default `whispercpp`) kicks in automatically when the primary is unavailable.
The offline `whisper-cli` binary is built by `.github/workflows/build-whisper-engine.yml`
and shipped by the packaging recipes — it is NOT a Python dependency.

## Post-processing / refinement (dictation only)

`services/postprocess.py` optionally cleans dictation via the Groq chat model
before typing. Levels 0–5: `Off / Correct / Light / Medium / Strong / Custom`
(`POSTPROCESS_LEVELS` in config.py), plus a separate `translate` axis
(`POSTPROCESS_TRANSLATE` + `POSTPROCESS_TARGET_LANG`). The `refine` hotkey lets
the user pick a level for a single dictation on the fly.

## Running & developing

```bash
./setup.sh                       # detects distro (apt/pacman) + session, installs deps
export GROQ_API_KEY="..."        # or set it via the tray/settings UI
loquivox                         # or: python -m loquivox

pip install -e .                 # editable install
pip install -e '.[deepgram]'     # streaming extra (also '.[openai]')
sudo usermod -aG input $USER     # required for global hotkeys (re-login after)
```

- There is **no test framework** in the repo; `tests/` holds standalone
  self-checks (`python tests/test_<x>.py`, most need GTK; `test_diagnostics.py`
  does not). Verify UI changes by running the app. CI only builds the
  whisper.cpp engine binary on version bumps.
- Packaging recipes (AUR, .deb) live under `packaging/`.

## Conventions

- Match the surrounding style: module docstrings, type hints, `from __future__
  import annotations`, dataclasses for config.
- Wrap fallible service calls with `@safe_execute("Label")` rather than bare
  try/except where it fits the existing pattern.
- Platform-specific behavior goes behind the ABCs in `platform/base.py`, never
  inlined with `if wayland:` checks in services.
- Keep `config.example.toml` and the docs in sync when you add a config key.
