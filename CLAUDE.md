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
| `F4`             | `ai`         | Ask the AI, answer typed + shown in chat overlay         |
| `F7`             | `ai_rewrite` | Copy selected text, speak an instruction, paste rewrite  |
| `F8`             | `vision`     | Screenshot + spoken question → Llama 4 vision answer     |
| `F6`             | `talk`       | Spoken conversation → one ready-to-paste generated text  |
| `F9`             | `pin`        | Toggle chat overlay "always on top"                      |
| `F10`            | `tts`        | Toggle TTS read-back of AI answers                       |
| `Esc`            | `cancel`     | Abort active recording / in-flight transcription         |
| `Space`          | `pause`      | Pause / resume the current recording                     |
| *(unbound)*      | `refine`     | Stop recording, then pick this dictation's refine level  |

- The first spec in each list is the primary key; the rest are aliases (incl.
  media keys). Specs support chords like `"ALT+SPACE"` or `"CTRL+SHIFT+D"`.
- Recording modes are `dictation`, `ai`, `ai_rewrite`, `vision` (see
  `CFG.MODES`). The rest (`pin`, `tts`, `cancel`, `pause`, `refine`) are
  non-recording session actions — see `KeyboardHandler._NON_RECORDING_ACTIONS`.
- `talk` is in both lists: it has a `CFG.MODES` entry (overlay look) but its key
  only *starts* a session, so the listener treats it as non-recording. It never
  goes through `ModeHandler.process()` — see the talk-mode section below.
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
services/         audio (record+transcribe), ai (chat+vision), tts (Orpheus),
                  clipboard, image, postprocess (LLM refinement of dictation),
                  talk (conversation → one text), vad (energy trigger),
                  turn_detector (Smart Turn v3 semantic end-of-turn)
managers/         history, chat (overlay state + auto-hide), overlay (recording indicator)
ui/               recording_overlay, chat_overlay (WebKit2; voice + typed input via
                  JS→Python `signal` IPC → ModeHandler.submit_text_chat), settings_dialog, tray,
                  hotkey_bar (top-edge tab that expands on hover into the hotkey list)
handlers/         mode.py (routes a transcript per mode), keyboard.py (evdev listener)
```

### Key flow
`keyboard.py` (listener thread) detects a chord → starts `AudioService` +
`OverlayManager` → on release, `ModeHandler.process_audio_async` /
`process_stream_async` runs transcription **in a worker thread** → result is
marshalled to the GTK main loop via `GLib.idle_add` → `ModeHandler.process()`
applies stale-guard + hallucination-guard, then dispatches to
`_handle_dictation/_handle_ai/_handle_ai_rewrite/_handle_vision`.

## Talk mode (`handlers/mode.py`, `services/talk.py`, `services/vad.py`)

A whole conversation, not a single utterance — so it does NOT use the
`process_audio_async` → `process()` path. `KeyboardHandler._on_press("talk")`
spawns `ModeHandler._talk_worker`, which owns everything until the session ends:

1. `GrabbedKeys` (keyboard.py) keeps the input devices open for the *entire*
   session — a key pressed while the model is thinking is still queued when the
   next wait starts — but takes the exclusive grab only inside
   `with keys.exclusive():`, around the waits themselves. Never hold a grab
   across a network call: a hung request would freeze the user's keyboard.
2. Each turn: `_talk_listen` records until the turn ends (see below),
   `_talk_transcribe` transcribes (streaming backends included) and applies the
   hallucination guard, `TalkSession.reply` answers, and the reply is spoken
   with `wait=True` — blocking is deliberate, the next turn must not record the
   assistant's own voice.
3. The briefing ends on Enter, on a short spoken "j'ai fini" (`user_said_done`,
   matched on the transcript before any API call), or on the model appending
   its `[[WRITE]]` marker. `CFG.TALK_FINISH_ON_PHRASE` and
   `CFG.TALK_FINISH_BY_MODEL` toggle those two independently (the key is never
   a toggle); `TalkSession.system_prompt()` appends the protocol clause for that
   pair, so an overridden `system_prompt` still carries it. The marker is
   stripped before the reply is spoken, shown or stored.
4. `_talk_generate` turns the conversation into one text and shows it in the
   review panel; `_deliver_talk_text` types it (accept only) and leaves it on
   the clipboard. `V` in that panel goes back to the conversation, brief intact.

The conversation lives in the `TalkSession`, never in `STATE.conversation_history`
— a long spoken briefing must not pollute the F4 chat context. `STATE.vad` is the
detector the audio callback feeds while a turn is being recorded; `STATE.talk_active`
guards against a second session. Knobs live under `[talk]` in config.toml.

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

Every layer degrades to the next one; a missing model is a printed warning, never
an error. `services/_whisper_features.py` is vendored from Pipecat (BSD-2) — keep
it in sync with upstream instead of editing it.

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

- There is **no automated test suite** in the repo today; verify changes by
  running the app. CI only builds the whisper.cpp engine binary on version bumps.
- Packaging recipes (AUR, .deb) live under `packaging/`.

## Conventions

- Match the surrounding style: module docstrings, type hints, `from __future__
  import annotations`, dataclasses for config.
- Wrap fallible service calls with `@safe_execute("Label")` rather than bare
  try/except where it fits the existing pattern.
- Platform-specific behavior goes behind the ABCs in `platform/base.py`, never
  inlined with `if wayland:` checks in services.
- Keep `config.example.toml` and the docs in sync when you add a config key.
