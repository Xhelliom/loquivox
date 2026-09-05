<div align="center">

<img src="assets/logo.png" alt="Loquivox Logo" width="160" height="auto" />

# Loquivox

**A voice assistant & AI companion for Linux — talk it through out loud, dictate anywhere, from a global hotkey.**

### 👉 [**loquivox — visit the website**](https://xhelliom.github.io/loquivox/) 👈

*Screenshots, a live talk-mode demo, the hotkey map and one-command install instructions.*

[![Website](https://img.shields.io/badge/Website-loquivox-2aa198?style=for-the-badge)](https://xhelliom.github.io/loquivox/)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-emerald?style=for-the-badge)](LICENSE)
[![X11 & Wayland](https://img.shields.io/badge/X11-%26%20Wayland-8b5cf6?style=for-the-badge&logo=linux&logoColor=white)](#-supported-platforms)

![Loquivox Demo](assets/demo.gif)

</div>

---

> [!NOTE]
> **Loquivox is a fork of [Dianjeol/LinuxWhisper](https://github.com/Dianjeol/LinuxWhisper)** (MIT).
> It keeps the original idea — voice at your cursor, everywhere — and takes it further:
> a **spoken conversation** with an AI that reasons, looks things up on the web and writes
> the text you were after; pluggable transcription backends with a **fully local, private
> engine**; live streaming; dictation refinement levels; Wayland/Niri support; a settings UI;
> and distro packaging (AUR / `.deb`). All credit for the original work goes to
> [Dianjeol](https://github.com/Dianjeol).

---

## ✨ What it does

Loquivox sits in the background and listens for **global hotkeys** (via `evdev`, so it works
identically under X11 and Wayland). One key dictates at the cursor. The other two open a
**spoken conversation** with an AI — speech in, speech out — that asks the right questions,
looks things up on the web while it keeps talking, reads your screen if you let it, and can
be cut off mid-sentence like a person. At the end it writes the text the conversation was about.

| | Feature | |
|:---:|:---|:---|
| 🗣️ | **Talk mode** (`F6`) | Discuss what you need out loud, then say the word: the whole conversation becomes one finished text, typed and copied. Select text first and it is the text to rework (*"make it formal"*, *"shorten this"*). Details below. |
| 💬 | **AI chat** (`F4`) | The same conversation, about what is on your screen — the error, the page, the text you selected. Nothing written at the end, unless you say *"write it"*. Or type into the overlay. |
| 🔎 | **Web search** | Mid-conversation, the assistant hands a question to a web-searching model, says it is looking it up, and carries on; the answer arrives between turns. |
| 🎭 | **Two engines** | *Cascade*: transcription → chat model → voice, each slot local or cloud. *Realtime*: one OpenAI speech-to-speech session, faster and with the prosody of speech. |
| ✋ | **Interruptible** | Start talking over the reply and it stops and listens; your first words are kept. Turns end on *meaning*, thanks to an on-device turn-detection model. |
| 👁️ | **Screen context** | Optionally, the focused window is captured and described once by a vision model, so you never have to read the error dialog out loud. |
| 🎙️ | **Dictation** (`F3`) | Speak, and your words are typed at the cursor — in *any* application. |
| 🪄 | **Refinement levels** | Raw transcript, grammar fix, light/medium/strong reformulation, or a custom prompt — pick it per dictation, on the fly. |
| 🌍 | **Live translation** | Dictate in one language, have the text typed in another. |
| 🔊 | **Voice feedback** | Optional TTS reads AI answers aloud, so you stay hands-free end to end. |
| 🔒 | **Local & private mode** | Run speech recognition **100% on your machine** — no cloud, no API key. See below. |
| ⚡ | **Live streaming** | Optional streaming backends show partial text in the overlay *while* you speak. |
| ⌨️ | **Fully remappable** | Every hotkey is editable in the UI, combos included (`Alt+Space`, `Ctrl+Shift+D`…). |
| 🐧 | **Linux-native** | GTK3 overlays, `gtk-layer-shell` on Wayland, tray icon, no Electron. |

Nice touches: a hover cheat-sheet tab at the top of the screen, a review of the
generated text before it is typed, microphone selection, a custom vocabulary for
proper nouns and jargon, conversation history in the tray, and nine color schemes.

---

## 🔒 Local & private: run the model yourself

Your voice is the most personal input there is. Loquivox lets you keep it on your machine.

**Local speech recognition (no cloud, no key, no network):** the `whispercpp` backend runs
the Whisper model locally through the standalone [whisper.cpp](https://github.com/ggerganov/whisper.cpp)
`whisper-cli` engine — shipped as a static binary by the AUR and `.deb` packages. Models from
`tiny` to `large-v3-turbo` are downloaded once to `~/.local/share/loquivox/models/` and used
offline forever after.

```toml
# ~/.config/loquivox/config.toml
[transcription]
backend = "whispercpp"          # 100% local — nothing leaves your machine
whispercpp_model = "large-v3-turbo"
```

Or select it in **Settings → Models**. Set `backend = "auto"` to prefer the cloud when
a key is available and fall back to the local engine otherwise — the local engine is also the
automatic **fallback** (`fallback = "whispercpp"`) whenever the cloud backend fails: no
network, no key, API error.

**Transcription backends at a glance:**

| Backend | Where it runs | Key needed | Notes |
|:---|:---|:---|:---|
| `whispercpp` | 🖥️ **Your machine** | — | Fully offline. Default fallback. |
| `groq` | ☁️ Groq Cloud | `GROQ_API_KEY` | Default. `whisper-large-v3`, very fast. |
| `deepgram` | ☁️ Deepgram | `DEEPGRAM_API_KEY` | `nova-3`, live streaming. `pip install -e '.[deepgram]'` |
| `openai_realtime` | ☁️ OpenAI | `OPENAI_API_KEY` | `gpt-4o-transcribe`, live streaming. `pip install -e '.[openai]'` |
| `auto` | — | — | Cloud if a key is present, local otherwise. |

> [!IMPORTANT]
> **Scope of the offline mode today:** local execution covers *speech-to-text*, which is what
> dictation needs — so **dictation works fully offline**. The AI features (chat, talk,
> web search and the optional dictation refinement) call a cloud provider: **Groq** or
> **OpenAI**, picked in Settings → Models. Pointing them at a self-hosted chat model is
> not supported yet. In talk mode's cascade engine the voice and the turn detection are
> local too — Settings → Models offers OpenAI's multilingual voices (`OPENAI_API_KEY`,
> Groq's Orpheus only speaks English) and **Piper**, which speaks French on your machine
> with no key and no network; the *Private* preset picks all of that at once, so nothing
> but the conversation itself leaves the machine.

---

## ⌨️ Command center

| Key | Action | What it does |
|:---:|:---|:---|
| `R-Alt` / `F3` | **Dictate** | Transcribe your voice to text at the cursor |
| `F4` | **Chat** | Talk about what's on screen and the text you selected — say *"write it"* to turn it into a text |
| `F6` | **Talk** | Talk it through with the AI, then get the text it was all about — select text first to rework it, or press `T` mid-conversation to send a selection |
| `F9` | **Pin** | Toggle "always on top" for the chat overlay |
| `F10` | **TTS** | Toggle spoken read-back of AI answers |
| `Esc` | **Cancel** | Abort the active recording / transcription (nothing inserted) |
| `Space` | **Pause** | Pause / resume the current recording |
| *(unbound)* | **Refine** | Stop, then pick this dictation's refinement level |

Keys are **hold-to-talk** by default; tick *Toggle Mode* in the tray menu for press-to-start / press-to-stop.
All of them are remappable in **Settings → Hotkeys**, combos included. `Refine` ships unbound —
assign a key to use it.

Both conversations (`F4` and `F6`) can **look things up**: when the model needs a fact it
hands the question to a web-searching model (Settings → Models → Search) and keeps talking;
the answer is spoken as soon as it is back. Inside a conversation, `S` looks at the screen
again and `T` sends the current selection — see the key table under Talk mode.

> [!TIP]
> Forgot a key? A thin tab sits at the top-center of your screen — hover it and the full list
> drops down. Turn it off in **Settings → Appearance & comfort**.

---

## 🗣️ Talk mode: think out loud, get the text

Dictation gives you what you said. Talk mode gives you what you *meant*.

Press `F6` and just talk — no key to hold. The assistant answers out loud (two sentences,
never more) and you keep going: it asks who the text is for, what tone you want, what must
absolutely be in it. When you're done briefing it, press `Enter` — and the entire conversation
becomes **one finished text**, ready to paste.

`F4` is the same conversation with a different reason to talk: it looks at your screen and
the text you selected, and you discuss it — check an error, get an opinion, understand a
page. Nothing is written at the end, until you say *"write it"* (or *"écris ça"* in French): the chat
then turns into a briefing on the spot and the text is generated from everything said so far.

**Speech to speech, two ways** — in Settings → Models, or with one of the presets there
(*Fast*, *Natural*, *Private*):

| Engine | How | Latency | What you get |
|:---|:---|:---:|:---|
| **Cascade** (default) | transcription → turn detection → chat model → voice, four slots, each one local or cloud | ~1.8 s | Your choice of model at every step, including a **fully local voice** (Piper: no key, no network, first sample in 0.2 s) |
| **Realtime** | one OpenAI Realtime session hears and answers directly | ~1.0 s | The prosody of speech; the server handles turn-taking and interruption. The chat model is OpenAI's for the conversation |

Either way the text written at the end comes from your chosen chat model: the conversation
half is interchangeable, the writing pass never learns which engine ran.

**It looks things up while it talks.** The conversational models are fast and light, and
it shows the moment a fact is needed — a price, a date, a version number, who someone is.
So both engines get one tool, `research(question)`: the assistant says in one sentence that
it is looking it up and **keeps the conversation going**, while a web-searching model
(Groq's compound model by default, ~2 s, or OpenAI's Responses API with `web_search`)
fetches the answer on a thread. The result is handed back **between turns**, never
mid-sentence, and stays in the brief so the final text has the facts. Pick the provider in
Settings → Models → Search; `research = false` under `[talk]` turns the tool off.

**You can watch it happen.** A bubble opens on the hotkey, just above the recording
indicator: your words appear as they are heard, the reply as it is written — sentence by
sentence, not in one block when the model has finished thinking. It grows and shrinks with
the exchange, stays translucent enough to read what is behind it, and closes with the ✕
(the pin hotkey brings it back). Live text needs a streaming transcription backend; with a
batch one your turn simply appears when you finish it.

**Or skip the review entirely.** Settings → Talk → *Paste the finished text
straight away* pastes it where your cursor was the moment the conversation
ended, with no key to press — the trade being that there is then no redo and
no going back. Off by default: talk mode otherwise never types anything you
have not accepted.

**Tell it how to talk to you.** Settings → Talk has an *Instructions* box for the standing
things: the language to answer in, the tone, the kind of work you usually discuss. It is
added to the built-in prompt rather than replacing it, so the conversation still asks one
question at a time and still knows when to stop.

**Cut it off whenever you like.** Start talking while it is still speaking and it
stops mid-sentence and listens — your first words are kept as the start of your turn,
not lost to the interruption. It works out how loudly it can hear itself by listening
to its own reply, so a headset needs no setting at all; on speakers with the volume up,
`libpipewire-module-echo-cancel` keeps it from cutting itself off. Turn it off with
`barge_in = false`.

**It listens like an editor, not a dictaphone.** What it gets is a *transcript* —
spoken language that wanders, backtracks, and where the recognizer mishears a name or a
technical term. So it never quietly guesses: it quotes the odd bit back and asks what you
meant, you re-say it, and the correction replaces what came before. Set `depth = "deep"` in
`config.toml` and it goes further, asking the question that makes you pin down what you
actually want — the unstated assumption, the objection your reader will raise.

**Three ways to say you're done**, all leading to the same finished text:

| | | |
|:---|:---|:---|
| `Enter` | always works, whatever the settings | — |
| *"I'm done"*, *"go ahead"*, *"that's it"* (French phrases work too) | said out loud, caught on the transcript before the model is even called | `finish_on_phrase` |
| the assistant decides | it hands itself over once it could write the text well | `finish_by_model` |

The last two are independent toggles — in **Settings → Talk**, or under `[talk]` in
`config.toml`. Turn both off and nothing writes the text until *you* press the key.

**It can look at your screen.** Half of what you're about to dictate is already in
front of you — the error dialog, the thread you're answering, the page you're describing —
and you won't say it out loud. Tick *Send a screenshot as context* in **Settings → Talk** and
the focused window is captured when the session opens, described once by the vision model, and
handed to the conversation as context. It runs **in parallel with your first sentence**, so it
costs no startup delay, and it's one vision call per session — not one per turn. Press `S`
during the conversation to look again when the screen has changed, and `T` to send whatever
is selected now. `F4` always captures.

Off by default: it sends your window to the cloud. The *focused window* is the default
region because framing is what makes text legible to a vision model — a whole 4K desktop
squeezed into the same token budget comes back as confident invention. A box around the
cursor is available where the compositor can report the pointer (X11, Hyprland), and the
full screen is the fallback everywhere else. The image is downscaled to `screenshot_max_px`
before upload.

**Turns end on meaning, not on a stopwatch.** A silence timer can't tell *"…and then, uh…"*
from a finished sentence. So the pause is only a trigger: when Loquivox hears one, a small
audio model — [Smart Turn v3](https://github.com/pipecat-ai/smart-turn), 8 MB, ~10 ms on CPU,
23 languages — reads the *prosody* of your last 8 seconds and answers "landed" or "still
going". Trail off and you keep the floor; land your sentence and the assistant replies in
~250 ms instead of waiting out a full second of silence. It works on every backend, offline
included, because it listens to the waveform rather than to a transcript.

```bash
pip install -e '.[turn]'   # onnxruntime; the model is fetched on first use
```

No onnxruntime, no network on first run, `semantic_turns = false`? Talk mode falls back to
plain silence detection — nothing breaks. And with the `openai_realtime` backend, OpenAI's own
`semantic_vad` does the job server-side instead.

| Key | While talking | On the generated text |
|:---:|:---|:---|
| `Space` | End this turn now, don't wait for the pause | — |
| `S` | Look at the screen again (when the screenshot is enabled) | — |
| `T` | Send the current selection into the conversation | — |
| `Enter` | Stop talking, write the text | Accept: typed at the cursor **and** copied |
| `C` | — | Copy only, nothing typed |
| `R` | — | Write it again |
| `V` | — | Back into the conversation — fix it by saying what's wrong |
| `Esc` | Drop the conversation | Discard the text |

Nothing is ever typed without your `Enter`. If you walk away, the text goes to the clipboard
rather than into whatever has focus. Turn detection, timeouts and both prompts are tunable in
`config.toml` under `[talk]` — see [`config.example.toml`](config.example.toml).

---

## 🛠️ Install

**Prerequisites** — a Linux desktop (X11 or Wayland), and your user in the `input` group so
global hotkeys can read `/dev/input`:

```bash
sudo usermod -aG input $USER   # then log out and back in
```

<details open>
<summary><b>Arch Linux (AUR)</b></summary>

```bash
yay -S loquivox
```
Builds and bundles the static `whisper-cli` engine, so offline transcription works out of the box.
</details>

<details>
<summary><b>Debian / Ubuntu (.deb)</b></summary>

Download the latest package from the [releases page](https://github.com/Xhelliom/loquivox/releases), then:
```bash
sudo apt install ./loquivox_*_amd64.deb
```
</details>

<details>
<summary><b>Any distro — setup script</b></summary>

```bash
git clone https://github.com/Xhelliom/loquivox.git && cd loquivox
./setup.sh
```
The script detects your distribution (`apt` / `pacman`) and session type (X11 / Wayland),
installs the right system packages, creates a venv, and optionally adds Loquivox to autostart.
</details>

<details>
<summary><b>From source (development)</b></summary>

```bash
git clone https://github.com/Xhelliom/loquivox.git && cd loquivox
python3 -m venv --system-site-packages venv && source venv/bin/activate
pip install -e .                 # add '.[deepgram]' / '.[openai]' for streaming backends
```
PyGObject, GTK3, WebKit2GTK, `gtk-layer-shell` and the platform tools must come from your
distro — see [`packaging/system-dependencies.md`](packaging/system-dependencies.md).
</details>

### Run it

```bash
export GROQ_API_KEY="your_key"   # optional if you only use the local backend
loquivox                         # or: python -m loquivox
```

The key can also be set from the tray → **Settings → API Keys**, and is stored for you.
Get a free one at [console.groq.com](https://console.groq.com).

---

## 🖥️ Supported platforms

| Distribution | X11 | Wayland |
|:---|:---:|:---:|
| **Debian / Ubuntu** | ✅ | ✅ |
| **Arch Linux** | ✅ | ✅ (incl. **Niri**) |

The session type is auto-detected and the matching backends are used:

| Capability | X11 | Wayland |
|:---|:---|:---|
| Clipboard | `xclip` | `wl-clipboard` |
| Key simulation | `xdotool` | `wtype` |
| Screenshots | `gnome-screenshot` | `grim` |
| Overlays | GTK window hints | `gtk-layer-shell` |
| Global hotkeys | `evdev` | `evdev` |

<details>
<summary><b>Niri users — recommended layer rules</b></summary>

Add to `~/.config/niri/config.kdl` for clean overlays:
```kdl
layer-rule {
    match namespace="loquivox-recording"
    shadow { on false }
}

layer-rule {
    match namespace="loquivox-chat"
    shadow { on false }
}
```
</details>

---

## ⚙️ Configuration

Most things are adjustable from the tray icon (**Settings**: Models, Talk, Refinement, Hotkeys,
Appearance & comfort, API Keys, Support). Everything else lives in an optional TOML file:

```bash
cp config.example.toml ~/.config/loquivox/config.toml
```

[`config.example.toml`](config.example.toml) documents every key — backend selection, models,
language, vocabulary, microphone, refinement, hotkeys, overlay geometry. Any key you omit falls
back to the built-in default, and a missing or malformed file is simply ignored: the app always
starts. UI-toggled preferences (voice, color scheme, …) are stored separately in
`~/.config/loquivox/settings.json`.

---

## 🐛 Reporting a bug

Loquivox keeps a log at `~/.local/state/loquivox/loquivox.log` (everything it would
have printed to a terminal, timestamped, rotated at 2 MB). To send a report, use either:

- **tray icon → Diagnostic report…**, or **Settings → Support** — shows the report, with
  **Copy** and **Save…**. The same tab has **View log…** (live, unredacted, for your own
  eyes) and **Save log…**, which writes the *whole* log with the same redaction — the file
  to attach when 400 lines are not enough.
- ```bash
  loquivox --report            # writes ~/loquivox-report-<date>.txt
  loquivox --report -          # or prints it
  loquivox --export-log        # the whole log, redacted → ~/loquivox-log-<date>.txt
  ```

The report carries the version, distro, session type and compositor, which tools and
optional packages are installed, the effective config, *whether* each API key is set, the
last 400 log lines and the matching lines of the user journal. **API keys, your user name,
home directory, e-mail and IP addresses are removed**, and what you said (transcripts,
screen descriptions) is replaced by its length — tick the box in the window, or pass
`--keep-content`, if the bug is about what was heard. Read it over, then attach it to the
issue. `LOQUIVOX_LOG_FILE=/elsewhere.log` moves the log; an empty value disables it.

---

## 🧩 How it works

```
Dictation (F3, hold the key)
keyboard.py  ──▶  AudioService  ──▶  transcription backend  ──▶  ModeHandler
 (evdev,          (record while       (groq / whispercpp /      (refine, then type
  own thread)      the key is held)    deepgram / openai)        at the cursor)
                                              │                        │
                                        worker thread ──GLib.idle_add──▶ GTK main loop
                                                                        (type / overlay / TTS)

Talk & chat (F6 / F4, a whole session)
_talk_worker ──▶ cascade:  listen (VAD + Smart Turn) ─▶ STT ─▶ chat model ─▶ TTS ─▶ barge-in
             └─▶ realtime: one OpenAI Realtime session, speech in / speech out
                     │                 ▲ research(question) → web-searching model, on a thread
                     ▼                 │ answer injected between turns
                TalkSession ──▶ writing pass (chat model) ──▶ review bubble ──▶ type + clipboard
```

```
src/loquivox/
├── app.py            # main(): secrets, hotkey banner, keyboard thread + GTK tray
├── config.py         # Config dataclass + CFG singleton, TOML layering, chord parsing
├── state.py          # AppState + SettingsManager (runtime state & user prefs)
├── platform/         # X11 vs Wayland backends behind ABCs (clipboard, typing, screenshot)
├── transcription/    # Pluggable STT: factory, dispatcher, groq / whispercpp / streaming
├── services/         # audio, ai, tts (+ piper), talk, realtime_talk, research, vad,
│                     # turn_detector, postprocess, clipboard, image, media
├── managers/         # history, chat overlay state, recording overlay
├── ui/               # recording overlay, WebKit2 chat overlay, settings, tray, hotkey bar
└── handlers/         # mode.py (route a transcript), keyboard.py (evdev listener)
```

Contributor notes, threading rules and the conventions to follow live in
[`CLAUDE.md`](CLAUDE.md); packaging details in [`packaging/`](packaging/).

---

## 🤝 Contributing

Issues and pull requests are welcome — bug reports, distro/compositor test reports, and
backend additions especially. There is no automated test suite yet, so please describe how you
verified your change by running the app.

## 📄 License

MIT — see [LICENSE](LICENSE). Original work © Dianjeol ([LinuxWhisper](https://github.com/Dianjeol/LinuxWhisper)),
fork and subsequent changes under the same license.
</content>
</invoke>
