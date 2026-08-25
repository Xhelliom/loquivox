"""
Application configuration.

Defaults are defined in the ``Config`` dataclass below. They can be
overridden at runtime by an optional user file:

    ~/.config/loquivox/config.toml

The TOML is loaded once at import time and layered on top of the defaults
(see ``_build_config``). A missing or malformed file falls back to defaults,
so the app always starts.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

from evdev import ecodes

CONFIG_FILE: Path = Path.home() / ".config" / "loquivox" / "config.toml"

# Refinement levels for post-processing — single source of truth for the
# settings slider and the tray submenu. 0 = off; higher = more rewriting. The
# per-level system prompts live in services.postprocess (_LEVEL_PROMPTS).
POSTPROCESS_LEVELS: tuple = (
    (0, "Off"),
    (1, "Correct"),
    (2, "Light"),
    (3, "Medium"),
    (4, "Strong"),
    (5, "Custom"),   # uses POSTPROCESS_CUSTOM_PROMPT
)
POSTPROCESS_MAX_LEVEL: int = 5

#: What the vision model is told to answer when a talk-mode screenshot holds
#: nothing worth passing on — interpolated into the prompt below, so the
#: sentinel the code matches on and the sentence the model reads are one string.
TALK_SCREEN_EMPTY: str = "(nothing relevant on screen)"
POSTPROCESS_CUSTOM_LEVEL: int = 5

# One-line description per hotkey action — single source of truth for the
# startup banner and the hover hotkey bar. Keys match HOTKEY_DEFS.
HOTKEY_DESCRIPTIONS: Dict[str, str] = {
    "dictation":  "Dictate at the cursor",
    "ai":         "Ask the AI",
    "ai_rewrite": "Rewrite the selected text",
    "vision":     "Screenshot + ask about it",
    "talk":       "Talk it through, then get the text",
    "pin":        "Pin the chat overlay on top",
    "tts":        "Read AI answers aloud",
    "cancel":     "Cancel recording / transcription",
    "pause":      "Pause / resume the recording",
    "refine":     "Stop & pick this dictation's refinement",
}


@dataclass(frozen=True)
class Config:
    """
    Immutable application configuration.

    Defaults live here; user overrides come from config.toml (see module
    docstring). To change a setting permanently without editing source,
    edit config.toml.
    """
    # --- Global Design System (Curated Color Schemes) ---
    COLOR_SCHEMES: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "Arctic Twilight": {
            "bg":        "#003049",
            "surface":   "#335c67",
            "accent":    "#669bbc",
            "text":      "#f1faee",
            "desc":      "Infinite polar blues echoing the cosmos and the soft light of evening snow."
        },
        "Volcanic Dawn": {
            "bg":        "#003049",
            "surface":   "#669bbc",
            "accent":    "#780000",
            "text":      "#fdf0d5",
            "desc":      "Intense, blazing crimson radiates energy; a bold and dramatic command of attention."
        },
        "Spring Blossom": {
            "bg":        "#a2d2ff",
            "surface":   "#cdb4db",
            "accent":    "#ffafcc",
            "text":      "#322659",
            "desc":      "Delicate pastel petals and whimsical sky blues, bringing a soft, elegant charm."
        },
        "Deep Sea Myth": {
            "bg":        "#0B0C10",
            "surface":   "#1F2833",
            "accent":    "#66FCF1",
            "text":      "#C5C6C7",
            "desc":      "Mysterious abyssal depths where cyan phosphorescence meets resilient silent strength."
        },
        "Boreal Silence": {
            "bg":        "#041C06",
            "surface":   "#064E3B",
            "accent":    "#10B981",
            "text":      "#ECFDF5",
            "desc":      "Lush, dark greens of ancient forests whispering beneath the mint-bright aurora."
        },
        "Oceanic Zen": {
            "bg":        "#002b36",
            "surface":   "#073642",
            "accent":    "#2aa198",
            "text":      "#eee8d5",
            "desc":      "Mathematically balanced depths of solarized teal, a classic of modern interface harmony."
        },
        "Amber Harvest": {
            "bg":        "#282828",
            "surface":   "#3c3836",
            "accent":    "#d65d0e",
            "text":      "#fbf1c7",
            "desc":      "Warm engineered earth tones of copper and cream, evoking a retro-industrial rustic elegance."
        },
        "Neon Nightshade": {
            "bg":        "#282a36",
            "surface":   "#44475a",
            "accent":    "#bd93f9",
            "text":      "#f8f8f2",
            "desc":      "Vibrant high-contrast purple and deep ink-blue, capturing the electric glow of a bioluminescent forest."
        },
        "Mediterranean Shore": {
            "bg":        "#264653",
            "surface":   "#2a9d8f",
            "accent":    "#f4a261",
            "text":      "#fdf1d3",
            "desc":      "Sun-drenched golden sands meet the smoky blue of midnight tides and crystalline waters."
        },
    })
    DEFAULT_SCHEME: str = "Oceanic Zen"
    SETTINGS_FILE: Path = Path.home() / ".config" / "loquivox" / "settings.json"

    # --- Audio Settings ---
    SAMPLE_RATE: int = 44100
    # Input device (microphone) NAME as reported by PortAudio. Empty string ("")
    # = use the system default input. Stored by name (not index) so it survives
    # device reordering; an unknown/disconnected name falls back to the default.
    INPUT_DEVICE: str = ""

    # --- History Limits ---
    MAX_TOKENS: int = 32000
    ANSWER_HISTORY_LIMIT: int = 15
    CHAT_MESSAGE_LIMIT: int = 20
    CHAT_AUTO_HIDE_SEC: int = 3

    # --- AI Models ---
    MODEL_CHAT: str = "llama-3.3-70b-versatile"
    MODEL_VISION: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    MODEL_WHISPER: str = "whisper-large-v3"
    MODEL_TTS: str = "canopylabs/orpheus-v1-english"

    # --- Transcription backend selection ---
    # Which engine transcribes speech. See loquivox.transcription.
    #   "groq"           — Groq Cloud Whisper, batch (default, needs GROQ_API_KEY)
    #   "whispercpp"     — local whisper.cpp, offline, no key (needs [local] extra)
    #   "openai_realtime"/"deepgram" — streaming live (added in the streaming phase)
    #   "auto"           — Groq if a key is present, else whispercpp
    BACKEND: str = "groq"
    # Offline backend used automatically when the primary is unavailable
    # (no network / no key / API error). "" disables fallback.
    FALLBACK_BACKEND: str = "whispercpp"
    # Local whisper.cpp model name (auto-downloads): tiny, base, small, medium,
    # large-v3, large-v3-turbo, and .en variants.
    WHISPERCPP_MODEL: str = "base"
    # Streaming-backend models (used in the streaming phase).
    OPENAI_MODEL: str = "gpt-4o-transcribe"
    # How eagerly OpenAI Realtime's own semantic VAD closes a turn — "auto"
    # (= medium), "low", "medium" or "high". Only used in talk mode, where that
    # backend detects turns server-side instead of the local model.
    OPENAI_TURN_EAGERNESS: str = "auto"
    DEEPGRAM_MODEL: str = "nova-3"

    # --- Post-processing (dictation text → LLM, opt-in) ---
    # Refinement intensity: 0 = off, 1 = correct … 4 = strong (POSTPROCESS_LEVELS).
    POSTPROCESS_LEVEL: int = 0
    # Translate instead of refine — a separate axis; uses POSTPROCESS_TARGET_LANG.
    POSTPROCESS_TRANSLATE: bool = False
    # Lay the result out as structured plain text (paragraphs + bullet lists) —
    # a separate axis that COMBINES with the refinement level / translate.
    POSTPROCESS_FORMAT: bool = False
    # Target language for translate (ISO-639-1 code or a language name).
    POSTPROCESS_TARGET_LANG: str = "en"
    # System prompt used by the "Custom" level (POSTPROCESS_CUSTOM_LEVEL).
    POSTPROCESS_CUSTOM_PROMPT: str = ""

    # --- Transcription ---
    # ISO-639-1 code (e.g. "en", "fr"). Empty string = Whisper autodetects.
    WHISPER_LANGUAGE: str = ""
    # Words the engine tends to drop or mangle — proper nouns, jargon, foreign
    # terms — as a comma-separated list. Each backend gets it in its own native
    # form: Whisper's `prompt` (groq) / `--prompt` (whispercpp), Deepgram's
    # `keyterm` (nova-3 only). Write it in the dictation language.
    VOCABULARY: str = ""
    # Skip transcription (and the API call) for clips shorter than this, in
    # seconds — filters accidental key-clicks. 0 disables the check.
    MIN_AUDIO_SEC: float = 0.5
    # Audio is resampled to this rate before upload (Whisper runs at 16 kHz, so
    # smaller uploads = lower latency, no quality loss). 0 disables resampling.
    UPLOAD_SAMPLE_RATE: int = 16000

    # Phrases Whisper commonly hallucinates on silence — dropped before
    # insertion. Overridable via [transcription] hallucinations = [...].
    HALLUCINATIONS: frozenset = frozenset({
        # English
        "thank you", "you're welcome", "thanks", "subtitle", "you", "bye",
        # German (Whisper artifacts)
        "untertitel",
        # French
        "merci", "merci beaucoup", "sous-titres", "sous-titrage",
        "abonnez-vous", "merci d'avoir regardé", "au revoir",
    })

    # --- Clipboard timing (seconds) ---
    CLIPBOARD_PASTE_DELAY: float = 0.05
    CLIPBOARD_RESTORE_DELAY: float = 0.03
    # Cache window for focused-window / terminal detection (seconds).
    TERMINAL_CACHE_TTL: float = 0.2

    # --- TTS Voices ---
    TTS_VOICES: Tuple[str, ...] = ("diana", "hannah", "autumn", "austin", "daniel", "troy")
    TTS_DEFAULT_VOICE: str = "diana"
    TTS_MAX_CHARS: int = 4000

    # --- Recording Overlay Geometry ---
    OVERLAY_WIDTH: int = 220
    OVERLAY_HEIGHT: int = 60
    # Overlay visual style (user-selectable in Settings → Appearance):
    #   "pill"    — capsule with a pulsing dot + horizontal waveform (default)
    #   "classic" — icon + centered text + mirrored EQ bars (the original look)
    OVERLAY_STYLES: Tuple[str, ...] = ("pill", "classic")
    DEFAULT_OVERLAY_STYLE: str = "pill"

    # --- Temp File Paths ---
    TEMP_SCREEN_PATH: str = f"/tmp/temp_screen_{os.getuid()}.png"
    TEMP_TTS_PATH: str = f"/tmp/loquivox_tts_{os.getuid()}.wav"

    # --- System Prompt ---
    SYSTEM_PROMPT: str = (
        "Act as a compassionate assistant. Base your reasoning on the principles of "
        "Nonviolent Communication and A Course in Miracles. Apply these frameworks as "
        "your underlying logic without explicitly naming them or forcing them. Let your "
        "output be grounded, clear, and highly concise. Return ONLY the direct response."
    )

    # --- Talk mode (spoken conversation → one generated text) ---
    # Turn detection: how a spoken turn ends. With VAD on, a pause ends it
    # (like the Realtime API's server_vad); Space/the talk key always ends it
    # immediately. Levels are RMS on the captured audio — see services/vad.py,
    # which calibrates against the room noise on top of this floor.
    TALK_VAD: bool = True
    TALK_VAD_THRESHOLD: float = 0.02
    TALK_VAD_SILENCE_MS: int = 900
    TALK_VAD_MIN_SPEECH_MS: int = 300
    # Semantic turn detection (services/turn_detector.py): a pause no longer
    # ends the turn by itself — Smart Turn v3 reads the prosody of the last 8
    # seconds and decides whether the sentence has landed. The energy VAD
    # becomes the trigger, which is why its silence window drops to
    # TALK_SEMANTIC_TRIGGER_MS while the model is in play: a finished sentence
    # is cut in ~250 ms instead of 900, and a hesitation isn't cut at all.
    # Needs onnxruntime (pip install -e '.[turn]'); falls back to the plain
    # silence VAD when it is missing.
    TALK_SEMANTIC_TURNS: bool = True
    # P(turn complete) at or above this ends the turn. Raise it to let yourself
    # trail off further before the assistant answers.
    TALK_TURN_THRESHOLD: float = 0.5
    # Pause that triggers a semantic check (milliseconds).
    TALK_SEMANTIC_TRIGGER_MS: int = 250
    # Silence after which the turn ends whatever the model says (milliseconds).
    TALK_TURN_MAX_SILENCE_MS: int = 4000
    # The model: empty = the newest CPU build of pipecat-ai/smart-turn-v3,
    # downloaded once (~8 MB) to ~/.cache/loquivox and reused from there. A
    # local path uses that file and never downloads; an http(s) URL pins the
    # build to fetch.
    TALK_TURN_MODEL: str = ""
    # A turn stops after this long no matter what (seconds).
    TALK_TURN_TIMEOUT: float = 60.0
    # Silence with nothing said at all for this long ends the conversation and
    # goes to the text generation (seconds).
    TALK_IDLE_TIMEOUT: float = 15.0
    # Hard stop on the conversation length (user turns).
    TALK_MAX_TURNS: int = 30
    # How much the assistant digs before writing:
    #   "minimal" — only asks when something is genuinely unclear
    #   "normal"  — asks about what would change the text (default)
    #   "deep"    — pushes the reasoning: assumptions, objections, examples
    TALK_DEPTH: str = "normal"
    # Who may end the briefing. The Enter key always can and is not a toggle —
    # these two are, independently, so you can keep as much control as you want:
    #   finish_on_phrase — saying you're done ends it ("vas-y", "j'ai fini")
    #   finish_by_model  — the assistant ends it once it judges it has enough
    # Both off = the key is the only way out.
    TALK_FINISH_ON_PHRASE: bool = True
    TALK_FINISH_BY_MODEL: bool = False
    # Spoken phrases that end the briefing on their own (matched on a SHORT
    # utterance only, accent- and punctuation-insensitive), before the model is
    # even called. Ignored when finish_on_phrase is off.
    TALK_FINISH_PHRASES: Tuple[str, ...] = (
        # French
        "j'ai fini", "j'ai termine", "c'est bon", "c'est tout", "vas-y",
        "ecris-le", "ecris le texte", "redige", "on y va", "termine",
        # English
        "i'm done", "im done", "that's it", "that's all", "go ahead",
        "write it", "write the text", "done",
    )
    # How long the generated text waits for a verdict before being left on the
    # clipboard (seconds) — it is never typed without an explicit accept.
    TALK_REVIEW_TIMEOUT: float = 120.0
    # --- Talk mode: the screen as context (opt-in) ---
    # A screenshot taken when the session starts, described once by the vision
    # model, and handed to the conversation as context — most of what you are
    # about to talk about is usually already on screen. OFF by default: it
    # sends your screen to the cloud. The capture runs in parallel with your
    # first turn, so it costs no startup delay.
    TALK_SCREENSHOT: bool = False
    # "screen" = the whole screen; "cursor" = a box around the pointer, which
    # focuses the model on what you are actually working on. The pointer can
    # only be located under X11 and Hyprland — anywhere else "cursor" falls
    # back to the whole screen.
    TALK_SCREENSHOT_REGION: str = "screen"
    # Width of that box, in pixels (its height follows the screen's aspect).
    TALK_SCREENSHOT_CURSOR_PX: int = 1200
    # The capture is downscaled to this many pixels on its long edge before
    # upload — the fix for a 4K screen. 0 disables the downscale.
    TALK_SCREENSHOT_MAX_PX: int = 1280
    # What the vision model is asked to report about that capture.
    TALK_SCREEN_PROMPT: str = (
        "The user is about to dictate a text to you by voice, and this is their "
        "screen right now: the context they will be talking about, and will "
        "probably not spell out. Describe it factually in at most 120 words — "
        "which application, what document or page, and the content that matters. "
        "Quote error messages, subject lines, names and figures verbatim rather "
        "than summarising them. Ignore Loquivox's own small recording overlay and "
        "chat panel if they are visible. If nothing on screen could plausibly be "
        f"useful, answer exactly: {TALK_SCREEN_EMPTY}"
    )

    # Speak the assistant's conversational replies even when TTS is toggled off
    # — talk mode is a voice conversation, the read-back is the point.
    TALK_SPEAK_REPLIES: bool = True

    # Conversation phase: the model is a partner working out WHAT to write.
    TALK_SYSTEM_PROMPT: str = (
        "You are on a live voice call with the user, working out a text they need "
        "to produce (an email, a message, a note, a snippet — anything). In this "
        "phase you do NOT write that text: you understand it.\n\n"
        "What reaches you is a speech transcript, so it is spoken language, not "
        "writing: it wanders, it backtracks, and the recognizer mishears words — a "
        "name, a technical term, a number. Never quietly guess at a word that looks "
        "wrong or a sentence that does not parse: quote the bit back and ask what "
        "was meant. When the user corrects something, the correction replaces what "
        "was said before it.\n\n"
        "Reply in at most two short spoken sentences — no markdown, no lists, no "
        "headings — because your answer is read aloud. Ask about one thing at a "
        "time, and only about what would actually change the text: audience, "
        "intent, tone, key facts, length. Never produce the final text until you "
        "are explicitly asked for it. Always answer in the language the user "
        "speaks."
    )
    # Generation phase: same conversation, new instructions — write the thing.
    TALK_GENERATE_PROMPT: str = (
        "You write the final text the user has just discussed with you by voice. "
        "The conversation is the brief: honour every instruction, fact and "
        "preference expressed in it, and nothing else. It is a transcript of "
        "speech, so read past the hesitations and repetitions, and where the user "
        "corrected themselves keep only the corrected version. Output ONLY the "
        "finished "
        "text — no preamble, no commentary, no surrounding quotes, no code fences "
        "unless the text itself is code. Write it in the language the user spoke, "
        "ready to paste as-is."
    )
    # The closing user turn that asks for it (appended to the conversation).
    TALK_GENERATE_REQUEST: str = (
        "The conversation is over. Write the final text now, following everything "
        "we discussed. Output only that text."
    )

    # --- Mode Definitions (icon, overlay text, colors) ---
    MODES: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "dictation":  {"icon": "🎙️", "text": "Listening...",    "bg": "bg", "fg": "accent"},
        "ai":         {"icon": "🤖", "text": "AI Listening...", "bg": "bg", "fg": "accent"},
        "ai_rewrite": {"icon": "✍️", "text": "Rewrite Mode...", "bg": "bg", "fg": "accent"},
        "vision":     {"icon": "📸", "text": "Vision Mode...",  "bg": "bg", "fg": "accent"},
        "talk":       {"icon": "🗣️", "text": "Talk Mode...",    "bg": "bg", "fg": "accent"},
    })

    #: The modes whose hotkey records audio while it is held. MODES above is the
    #: overlay's appearance table and is NOT this list: 'talk' has a look there
    #: but owns the microphone through its own session, not through the key.
    RECORDING_MODES: Tuple[str, ...] = ("dictation", "ai", "ai_rewrite", "vision")

    # --- Hotkey Definitions ---
    # format: "id": (Label, [chord specs])
    # A chord spec is "+"-joined: the LAST token is the trigger key; any leading
    # tokens are modifiers (ALT/CTRL/SHIFT/SUPER, or a specific key name). So a
    # spec is either a single key ("RIGHTALT", "F3") or a combo ("ALT+SPACE",
    # "CTRL+SHIFT+D"). evdev key names — work on both X11 and Wayland.
    # Override per-mode in config.toml under [hotkeys], e.g.
    #   dictation = ["ALT+SPACE", "F3"]
    HOTKEY_DEFS: Dict[str, Tuple[str, List[str]]] = field(default_factory=lambda: {
        "dictation":  ("R-Alt / F3", ["RIGHTALT", "F3", "F13"]),
        "ai":         ("F4",  ["F4", "F14"]),
        "ai_rewrite": ("F7",  ["F7", "PREVIOUSSONG"]),
        "vision":     ("F8",  ["F8", "PLAYPAUSE"]),
        # Spoken conversation that ends in one generated, ready-to-paste text.
        "talk":       ("F6",  ["F6"]),
        "pin":        ("F9",  ["F9", "NEXTSONG"]),
        "tts":        ("F10", ["F10", "MUTE"]),
        # Cancel the active recording / in-flight transcription (no text inserted).
        "cancel":     ("Esc", ["ESC"]),
        # Pause / resume the current recording (capture is held, not stopped).
        "pause":      ("Space", ["SPACE"]),
        # Stop the recording and choose this dictation's refinement level.
        # Unbound by default — assign a key in Settings → Hotkeys.
        "refine":     ("(unset)", []),
    })


# ---------------------------------------------------------------------------
# User config loading (config.toml -> overrides on top of defaults)
# ---------------------------------------------------------------------------
def _load_user_toml() -> Dict[str, Any]:
    """Read config.toml, returning {} if absent or malformed."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"⚠️ Failed to parse {CONFIG_FILE}: {e} — using defaults")
        return {}


def _resolve_keycode(name: str) -> int:
    """
    Resolve an evdev key name to its keycode.

    Accepts "F3", "KEY_F3", "RIGHTALT", "right alt" (case/underscore tolerant).
    Raises ValueError on an unknown name.
    """
    candidate = name.strip().upper().replace(" ", "_")
    if not candidate.startswith("KEY_"):
        candidate = f"KEY_{candidate}"
    code = getattr(ecodes, candidate, None)
    if not isinstance(code, int):
        raise ValueError(f"unknown key name '{name}'")
    return code


# Modifier name → keycodes that satisfy it (any one held is enough).
_MODIFIER_ALIASES: Dict[str, Tuple[int, ...]] = {
    "ALT":     (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT),
    "CTRL":    (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL),
    "CONTROL": (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL),
    "SHIFT":   (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
    "SUPER":   (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "META":    (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "WIN":     (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
}

# Reverse map: a modifier keycode → its friendly name (used by chord capture).
_MODIFIER_NAME_BY_CODE: Dict[int, str] = {
    code: name
    for name in ("ALT", "CTRL", "SHIFT", "SUPER")
    for code in _MODIFIER_ALIASES[name]
}
#: keycodes recognised as modifiers (used by the capture UI to build combos).
MODIFIER_CODES = frozenset(_MODIFIER_NAME_BY_CODE)


def modifier_name(code: int):
    """Friendly modifier name for a keycode (KEY_LEFTALT → 'ALT'), or None."""
    return _MODIFIER_NAME_BY_CODE.get(code)


def _resolve_modifier(token: str) -> frozenset:
    """Resolve a modifier token to the set of keycodes that satisfy it."""
    key = token.strip().upper().replace(" ", "_")
    if key.startswith("KEY_"):
        key = key[4:]
    if key in _MODIFIER_ALIASES:
        return frozenset(_MODIFIER_ALIASES[key])
    return frozenset({_resolve_keycode(token)})  # a specific key acting as modifier


def parse_chord(spec: str) -> Tuple[int, Tuple[frozenset, ...]]:
    """
    Parse a chord spec into ``(trigger_keycode, (modifier_groups...))``.

    The last ``+``-separated token is the trigger; leading tokens are modifiers.
    ``"F3"`` → ``(KEY_F3, ())``; ``"ALT+SPACE"`` →
    ``(KEY_SPACE, ({KEY_LEFTALT, KEY_RIGHTALT},))``. Raises ``ValueError`` on an
    unknown key/modifier name.
    """
    parts = [p for p in spec.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError(f"empty hotkey spec '{spec}'")
    *mods, trigger = parts
    return _resolve_keycode(trigger), tuple(_resolve_modifier(m) for m in mods)


def resolve_hotkeys(cfg: "Config") -> Dict[str, list]:
    """
    Parse every mode's chord specs into ``(trigger, modifier_groups)`` bindings
    for the keyboard listener. Unparseable specs are skipped with a warning.
    """
    resolved: Dict[str, list] = {}
    for mode, (_label, specs) in cfg.HOTKEY_DEFS.items():
        bindings = []
        for spec in specs:
            try:
                bindings.append(parse_chord(spec))
            except ValueError as e:
                print(f"⚠️ hotkey [{mode}]: {e} — skipped")
        resolved[mode] = bindings
    return resolved


def _build_hotkeys(
    base: Dict[str, Tuple[str, List[str]]],
    overrides: Dict[str, Any],
) -> Dict[str, Tuple[str, List[str]]]:
    """Apply [hotkeys] overrides (lists of chord specs) onto the base defs."""
    result = dict(base)
    for mode, specs in overrides.items():
        if mode not in result:
            print(f"⚠️ config.toml [hotkeys]: unknown mode '{mode}' — ignored")
            continue
        if isinstance(specs, str):
            specs = [specs]
        specs = [str(s).strip() for s in specs if str(s).strip()]
        if not specs:
            continue
        try:  # validate now so we never store a broken binding
            for s in specs:
                parse_chord(s)
        except ValueError as e:
            print(f"⚠️ config.toml [hotkeys.{mode}]: {e} — keeping default")
            continue
        result[mode] = (" / ".join(specs), specs)
    return result


def _build_config() -> Config:
    """Construct the global Config, layering config.toml over the defaults."""
    data = _load_user_toml()
    if not data:
        return Config()

    base = Config()
    overrides: Dict[str, Any] = {}

    trans = data.get("transcription", {})
    if "backend" in trans:
        overrides["BACKEND"] = str(trans["backend"])
    if "fallback" in trans:
        overrides["FALLBACK_BACKEND"] = str(trans["fallback"])
    if "whispercpp_model" in trans:
        overrides["WHISPERCPP_MODEL"] = str(trans["whispercpp_model"])
    if "openai_model" in trans:
        overrides["OPENAI_MODEL"] = str(trans["openai_model"])
    if "openai_eagerness" in trans:
        overrides["OPENAI_TURN_EAGERNESS"] = str(trans["openai_eagerness"]).strip().lower()
    if "deepgram_model" in trans:
        overrides["DEEPGRAM_MODEL"] = str(trans["deepgram_model"])
    if "model" in trans:
        overrides["MODEL_WHISPER"] = str(trans["model"])
    if "language" in trans:
        overrides["WHISPER_LANGUAGE"] = str(trans["language"])
    if "vocabulary" in trans:
        overrides["VOCABULARY"] = str(trans["vocabulary"]).strip()
    if "input_device" in trans:
        overrides["INPUT_DEVICE"] = str(trans["input_device"])
    if "sample_rate" in trans:
        overrides["SAMPLE_RATE"] = int(trans["sample_rate"])
    if "min_audio_sec" in trans:
        overrides["MIN_AUDIO_SEC"] = float(trans["min_audio_sec"])
    if "upload_sample_rate" in trans:
        overrides["UPLOAD_SAMPLE_RATE"] = int(trans["upload_sample_rate"])
    if "hallucinations" in trans:
        overrides["HALLUCINATIONS"] = frozenset(
            str(h).strip().lower() for h in trans["hallucinations"]
        )

    post = data.get("postprocess", {})
    # Back-compat: map the old `mode` (none/correct/reformulate/translate) onto
    # the new level + translate axes when the new keys aren't present.
    _mode_to_level = {"none": 0, "correct": 1, "reformulate": 2}
    if "level" in post:
        overrides["POSTPROCESS_LEVEL"] = max(0, min(POSTPROCESS_MAX_LEVEL, int(post["level"])))
    elif "mode" in post:
        m = str(post["mode"]).strip().lower()
        if m == "translate":
            overrides["POSTPROCESS_TRANSLATE"] = True
        elif m == "reformulate" and str(post.get("reformulate_prompt", "")).strip():
            # old reformulate + a custom prompt → the new Custom level
            overrides["POSTPROCESS_LEVEL"] = POSTPROCESS_CUSTOM_LEVEL
        else:
            overrides["POSTPROCESS_LEVEL"] = _mode_to_level.get(m, 0)
    if "translate" in post:
        overrides["POSTPROCESS_TRANSLATE"] = bool(post["translate"])
    if "format" in post:
        overrides["POSTPROCESS_FORMAT"] = bool(post["format"])
    if "target_language" in post:
        overrides["POSTPROCESS_TARGET_LANG"] = str(post["target_language"])
    # `reformulate_prompt` is the old name for the custom override.
    custom = post.get("custom_prompt", post.get("reformulate_prompt"))
    if custom is not None:
        overrides["POSTPROCESS_CUSTOM_PROMPT"] = str(custom).strip()

    talk = data.get("talk", {})
    if "vad" in talk:
        overrides["TALK_VAD"] = bool(talk["vad"])
    if "vad_threshold" in talk:
        overrides["TALK_VAD_THRESHOLD"] = float(talk["vad_threshold"])
    if "silence_ms" in talk:
        overrides["TALK_VAD_SILENCE_MS"] = int(talk["silence_ms"])
    if "min_speech_ms" in talk:
        overrides["TALK_VAD_MIN_SPEECH_MS"] = int(talk["min_speech_ms"])
    if "turn_timeout" in talk:
        overrides["TALK_TURN_TIMEOUT"] = float(talk["turn_timeout"])
    if "idle_timeout" in talk:
        overrides["TALK_IDLE_TIMEOUT"] = float(talk["idle_timeout"])
    if "max_turns" in talk:
        overrides["TALK_MAX_TURNS"] = int(talk["max_turns"])
    if "review_timeout" in talk:
        overrides["TALK_REVIEW_TIMEOUT"] = float(talk["review_timeout"])
    if "depth" in talk:
        overrides["TALK_DEPTH"] = str(talk["depth"]).strip().lower()
    # Back-compat: `auto_finish` was one enum before the two toggles split it.
    if "auto_finish" in talk:
        legacy = str(talk["auto_finish"]).strip().lower()
        overrides["TALK_FINISH_ON_PHRASE"] = legacy in ("asked", "model")
        overrides["TALK_FINISH_BY_MODEL"] = legacy == "model"
    if "finish_on_phrase" in talk:
        overrides["TALK_FINISH_ON_PHRASE"] = bool(talk["finish_on_phrase"])
    if "finish_by_model" in talk:
        overrides["TALK_FINISH_BY_MODEL"] = bool(talk["finish_by_model"])
    if "finish_phrases" in talk:
        overrides["TALK_FINISH_PHRASES"] = tuple(
            str(phrase).strip().lower() for phrase in talk["finish_phrases"]
            if str(phrase).strip()
        )
    if "screenshot" in talk:
        overrides["TALK_SCREENSHOT"] = bool(talk["screenshot"])
    if "screenshot_region" in talk:
        overrides["TALK_SCREENSHOT_REGION"] = str(talk["screenshot_region"]).strip().lower()
    if "screenshot_cursor_px" in talk:
        overrides["TALK_SCREENSHOT_CURSOR_PX"] = int(talk["screenshot_cursor_px"])
    if "screenshot_max_px" in talk:
        overrides["TALK_SCREENSHOT_MAX_PX"] = int(talk["screenshot_max_px"])
    if str(talk.get("screen_prompt", "")).strip():
        overrides["TALK_SCREEN_PROMPT"] = str(talk["screen_prompt"]).strip()
    if "semantic_turns" in talk:
        overrides["TALK_SEMANTIC_TURNS"] = bool(talk["semantic_turns"])
    if "turn_threshold" in talk:
        overrides["TALK_TURN_THRESHOLD"] = float(talk["turn_threshold"])
    if "semantic_trigger_ms" in talk:
        overrides["TALK_SEMANTIC_TRIGGER_MS"] = int(talk["semantic_trigger_ms"])
    if "max_silence_ms" in talk:
        overrides["TALK_TURN_MAX_SILENCE_MS"] = int(talk["max_silence_ms"])
    # `turn_model_path`/`turn_model_url` were two keys before one took both jobs.
    for legacy in ("turn_model_url", "turn_model_path", "turn_model"):
        if str(talk.get(legacy, "")).strip():
            overrides["TALK_TURN_MODEL"] = str(talk[legacy]).strip()
    if "speak_replies" in talk:
        overrides["TALK_SPEAK_REPLIES"] = bool(talk["speak_replies"])
    if str(talk.get("system_prompt", "")).strip():
        overrides["TALK_SYSTEM_PROMPT"] = str(talk["system_prompt"]).strip()
    if str(talk.get("generate_prompt", "")).strip():
        overrides["TALK_GENERATE_PROMPT"] = str(talk["generate_prompt"]).strip()

    clip = data.get("clipboard", {})
    if "paste_delay" in clip:
        overrides["CLIPBOARD_PASTE_DELAY"] = float(clip["paste_delay"])
    if "restore_delay" in clip:
        overrides["CLIPBOARD_RESTORE_DELAY"] = float(clip["restore_delay"])
    if "terminal_cache_ttl" in clip:
        overrides["TERMINAL_CACHE_TTL"] = float(clip["terminal_cache_ttl"])

    models = data.get("models", {})
    if "chat" in models:
        overrides["MODEL_CHAT"] = str(models["chat"])
    if "vision" in models:
        overrides["MODEL_VISION"] = str(models["vision"])
    if "tts" in models:
        overrides["MODEL_TTS"] = str(models["tts"])

    overlay = data.get("overlay", {})
    if "width" in overlay:
        overrides["OVERLAY_WIDTH"] = int(overlay["width"])
    if "height" in overlay:
        overrides["OVERLAY_HEIGHT"] = int(overlay["height"])

    hotkeys = data.get("hotkeys", {})
    if hotkeys:
        overrides["HOTKEY_DEFS"] = _build_hotkeys(base.HOTKEY_DEFS, hotkeys)

    return replace(base, **overrides)


# Global config instance
CFG = _build_config()


def reload_config() -> Config:
    """
    Re-read config.toml and rebuild the global ``CFG`` (used by the settings UI
    to apply edits live).

    Note: modules that did ``from loquivox.config import CFG`` keep their old
    reference, so settings that are only read at import/startup (e.g. overlay
    size) still need a service restart. Transcription settings apply live because
    the dispatcher is reconfigured from the returned fresh config; hotkeys apply
    live because KeyboardHandler.reload_hotkeys() rebuilds its keycode map from it.
    """
    global CFG
    CFG = _build_config()
    return CFG
