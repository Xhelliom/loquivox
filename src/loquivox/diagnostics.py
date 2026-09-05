"""
Logs on disk, and a diagnostic report a user can hand over without a second
thought.

Loquivox talks through ``print()``: 140-odd lines, emoji first, that are read
by nobody when the app starts from the autostart entry — stdout then goes to
the session journal at best, to ``/dev/null`` at worst. So when something
breaks, the first reply to a bug report is "can you run it from a terminal
and paste what it says", and the second is "please remove your API key from
what you pasted". This module removes both round trips:

- ``install_log_file()`` tees ``sys.stdout``/``sys.stderr`` into
  ``~/.local/state/loquivox/loquivox.log`` (timestamped, rotated at
  ``LOG_MAX_BYTES``, one previous generation kept). A tee rather than a
  ``logging`` migration: every existing print keeps working, and so does the
  default exception hook — Python prints an uncaught traceback, from any
  thread, to ``sys.stderr``, which is now the file too.
- ``build_report()`` assembles what a maintainer asks for first — version,
  distro, session type, compositor, the tools on PATH, the config, which keys
  are set, and the tail of that log — and passes all of it through
  ``redact()`` on the way out.

``redact()`` has two layers. The first is never optional: API keys (Groq,
OpenAI, Deepgram, ``KEY=value`` and ``"api_key": "..."`` forms, bearer
tokens), e-mail addresses, the home directory, the user and host names, IP
addresses and URL query strings. The second, on by default, is the user's own
words: what the five content-carrying prints quote (a transcript the stale
guard dropped, a hallucination, what the vision model read off the screen, a
research answer). A bug in the audio path is diagnosed from timings and
backend names, not from what was said — so the report leaves that out unless
the user chooses to keep it, and says how long it was instead.

Reachable three ways: ``loquivox --report`` (no GTK needed, so it works on the
machine where GTK is the problem), the tray's "Diagnostic report…", and
``LOQUIVOX_LOG_FILE`` to move or (empty) disable the log file.
"""
from __future__ import annotations

import datetime as _dt
import getpass
import io
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from loquivox import __version__

#: XDG state dir: logs are state, not config and not cache.
LOG_FILE: Path = Path(
    os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
) / "loquivox" / "loquivox.log"

#: Rotate past this size; one previous generation (``loquivox.log.1``) is kept.
LOG_MAX_BYTES = 2 * 1024 * 1024

#: How much of the log the report carries. Recent enough to hold the last
#: few sessions, short enough to paste into an issue.
REPORT_LOG_LINES = 400

#: A quoted string this long or longer, alone after a label, is the user's
#: words — ``backend: 'groq'`` stays readable, a sentence does not.
CONTENT_QUOTE_MIN = 12

REDACTED = "[REDACTED]"

_TOOLS = ("wtype", "wl-copy", "wl-paste", "grim", "niri", "hyprctl",
          "xdotool", "xclip", "xsel", "gnome-screenshot", "whisper-cli",
          "pactl", "wpctl", "journalctl")

_PACKAGES = ("groq", "openai", "deepgram-sdk", "onnxruntime", "piper-tts",
             "sounddevice", "numpy", "evdev", "PyGObject", "tomlkit")

_GI_LIBS = (("Gtk", "3.0"), ("GtkLayerShell", "0.1"), ("WebKit2", "4.1"),
            ("AyatanaAppIndicator3", "0.1"))


# ---------------------------------------------------------------------------
# Log file: a tee on stdout/stderr
# ---------------------------------------------------------------------------
class _LogSink:
    """One file shared by both streams: timestamps whole lines, rotates by size."""

    def __init__(self, path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._partial = ""
        self._fh: Optional[io.TextIOWrapper] = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._open()

    def _open(self) -> None:
        self._fh = open(self.path, "a", encoding="utf-8", errors="replace",
                        buffering=1)

    def _rotate_if_needed(self) -> None:
        try:
            if self._fh is None or self._fh.tell() < self.max_bytes:
                return
            self._fh.close()
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        except OSError:
            pass
        self._open()

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            data = self._partial + text
            lines = data.split("\n")
            self._partial = lines.pop()      # the unterminated remainder
            if not lines or self._fh is None:
                return
            stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._fh.write("".join(f"{stamp} {line}\n" for line in lines))
                self._rotate_if_needed()
            except (OSError, ValueError):
                pass                          # a full disk must not stop the app

    def flush(self) -> None:
        with self._lock:
            try:
                if self._fh is not None:
                    self._fh.flush()
            except (OSError, ValueError):
                pass


class _Tee(io.TextIOBase):
    """A stream that writes to the original stream and to the sink."""

    def __init__(self, stream, sink: _LogSink) -> None:
        super().__init__()
        self._stream = stream
        self._sink = sink

    def write(self, s: str) -> int:           # type: ignore[override]
        if self._stream is not None:
            try:
                self._stream.write(s)
            except (OSError, ValueError):
                pass                          # a closed stdout is not our problem
        self._sink.write(s)
        return len(s)

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except (OSError, ValueError):
                pass
        self._sink.flush()

    def fileno(self) -> int:
        if self._stream is None:
            raise io.UnsupportedOperation("no underlying stream")
        return self._stream.fileno()

    def isatty(self) -> bool:
        try:
            return bool(self._stream is not None and self._stream.isatty())
        except (OSError, ValueError):
            return False

    @property
    def encoding(self) -> str:                # type: ignore[override]
        return getattr(self._stream, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str:                  # type: ignore[override]
        return getattr(self._stream, "errors", None) or "replace"

    def writable(self) -> bool:
        return True

    @property
    def buffer(self):
        """The binary layer of the real stream, for the rare caller that
        wants it (bytes written there bypass the log, by design)."""
        return getattr(self._stream, "buffer", None)


_installed: Optional[_LogSink] = None


def log_file_path() -> Optional[Path]:
    """Where the log goes, honouring ``LOQUIVOX_LOG_FILE`` (empty = no file)."""
    override = os.environ.get("LOQUIVOX_LOG_FILE")
    if override is None:
        return LOG_FILE
    override = override.strip()
    return Path(override).expanduser() if override else None


def install_log_file() -> Optional[Path]:
    """
    Start mirroring stdout/stderr into the log file. Returns its path, or
    None when disabled or unwritable — the app runs the same either way.
    """
    global _installed
    if _installed is not None:
        return _installed.path
    path = log_file_path()
    if path is None:
        return None
    try:
        sink = _LogSink(path)
    except OSError as e:
        print(f"⚠️  Log file unavailable ({path}): {e}")
        return None
    _installed = sink
    sys.stdout = _Tee(sys.stdout, sink)   # type: ignore[assignment]
    sys.stderr = _Tee(sys.stderr, sink)   # type: ignore[assignment]
    sink.write(f"=== Loquivox {__version__} started (pid {os.getpid()}, "
               f"python {platform.python_version()}) ===\n")
    return path


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    # Provider key formats: Groq (gsk_…), OpenAI (sk-…, sk-proj-…), Deepgram
    # (40 hex chars), and the generic bearer header.
    (re.compile(r"\bgsk_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\b[0-9a-f]{40}\b"), REDACTED),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 " + REDACTED),
    # KEY=value / "api_key": "value" / token: value — whatever the spelling.
    # The value must look like one: eight characters or more, not a bare
    # number, so "prompt tokens: 898" is left alone.
    (re.compile(r"(?i)([A-Z0-9_\-]*(?:api[_\-]?key|token|secret|passw(?:or)?d)"
                r"[A-Z0-9_\-]*\"?\s*[=:]\s*[\"']?)(?!\d+\b)([^\s\"',;]{8,})"),
     r"\1" + REDACTED),
    # URL query strings carry signed URLs and keys; the path is enough.
    (re.compile(r"(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*"), r"\1?[…]"),
    # E-mail addresses, then any non-loopback IPv4.
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[email]"),
    (re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)"
                r"(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
]

_CONTENT_PATTERNS = [
    # `👁️  Screen context (0.4s): …` and `🔎 Research (2.1s): …` print the
    # text after the timing; keep the timing, drop the text.
    (re.compile(r"^(.*(?:Screen context|Research) \([^)]*\): )(.+)$", re.M),
     lambda m: m.group(1) + _elided(m.group(2))),
    # `⚠️ Ignored Hallucination: '…'`, `⏭️ Ignored stale transcription (gen 3):
    # '…'`, `⚠️ Talk mode ignored: '…'` — a label, a colon, then the user's
    # sentence quoted to the end of the line. Anchoring on that shape (rather
    # than on any quoted string) is what keeps an exception message such as
    # "cannot import name '_gi' from module 'gi'" readable.
    (re.compile(r"^(.*: )(['\"])([^'\"\n]{%d,})\2\s*$" % CONTENT_QUOTE_MIN, re.M),
     lambda m: m.group(1) + m.group(2) + _elided(m.group(3)) + m.group(2)),
]


def _elided(text: str) -> str:
    return f"[… {len(text)} chars]"


def _identity_patterns() -> List[tuple]:
    """Home directory, user name and host name — computed once per call, they
    are the same for the whole report."""
    out: List[tuple] = []
    home = str(Path.home())
    if home and home != "/":
        out.append((re.compile(re.escape(home)), "~"))
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "")
    if user and len(user) >= 3:
        out.append((re.compile(r"\b%s\b" % re.escape(user)), "[user]"))
    host = socket.gethostname()
    if host and len(host) >= 3 and host != "localhost":
        out.append((re.compile(r"\b%s\b" % re.escape(host)), "[host]"))
    return out


def redact(text: str, *, content: bool = True) -> str:
    """
    Strip what must never leave the machine, and (by default) what was said.

    ``content=False`` keeps transcripts and screen descriptions — for the user
    who *wants* the maintainer to see the sentence that went wrong — but keys,
    addresses and names are removed regardless.
    """
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    for pattern, repl in _identity_patterns():
        text = pattern.sub(repl, text)
    if content:
        for pattern, repl in _CONTENT_PATTERNS:
            text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def _run(cmd: List[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(unavailable: {e.__class__.__name__})"
    return (out.stdout or out.stderr).strip()


def _os_release() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.partition("=")[2].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _compositor() -> str:
    env = os.environ
    hints = []
    if env.get("NIRI_SOCKET"):
        hints.append("niri")
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hints.append("hyprland")
    if env.get("SWAYSOCK"):
        hints.append("sway")
    if env.get("KDE_FULL_SESSION"):
        hints.append("kde")
    if env.get("GNOME_SHELL_SESSION_MODE") or "gnome" in env.get("XDG_CURRENT_DESKTOP", "").lower():
        hints.append("gnome")
    return ", ".join(hints) or "(unknown)"


def _package_versions() -> Iterable[str]:
    try:
        from importlib import metadata
    except ImportError:                  # pragma: no cover — 3.8+ has it
        return ["(importlib.metadata unavailable)"]
    for name in _PACKAGES:
        try:
            yield f"{name} {metadata.version(name)}"
        except metadata.PackageNotFoundError:
            yield f"{name} (not installed)"


def _gi_availability() -> Iterable[str]:
    try:
        import gi
    except Exception as e:
        yield f"gi: import failed — {e}"
        return
    for lib, ver in _GI_LIBS:
        try:
            gi.require_version(lib, ver)
            yield f"{lib} {ver}: yes"
        except Exception:
            yield f"{lib} {ver}: no"


def _input_access() -> str:
    """Global hotkeys need read access to /dev/input — the #1 install issue."""
    try:
        import grp
        groups = [g.gr_name for g in grp.getgrall() if getpass.getuser() in g.gr_mem]
        try:
            groups.append(grp.getgrgid(os.getgid()).gr_name)
        except KeyError:
            pass
    except Exception:
        groups = []
    devices = sorted(Path("/dev/input").glob("event*"))
    readable = [d for d in devices if os.access(d, os.R_OK)]
    return (f"in 'input' group: {'yes' if 'input' in groups else 'NO'} · "
            f"/dev/input: {len(readable)}/{len(devices)} event devices readable")


def _audio_devices() -> str:
    try:
        import sounddevice as sd
        default_in = sd.default.device[0]
        lines = []
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                mark = " (default)" if i == default_in else ""
                lines.append(f"  [{i}] {dev['name']} · {int(dev['default_samplerate'])} Hz{mark}")
        return "\n".join(lines) or "  (no input device)"
    except Exception as e:
        return f"  (sounddevice unavailable: {e})"


def _read_text(path: Path, limit: int = 20000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "(absent)"
    except OSError as e:
        return f"(unreadable: {e})"
    return text if len(text) <= limit else text[-limit:]


def _log_tail(path: Optional[Path], lines: int) -> str:
    if path is None:
        return "(log file disabled via LOQUIVOX_LOG_FILE)"
    if not path.exists():
        return f"(no log file yet at {path} — it appears once the app has run)"
    text = _read_text(path, limit=lines * 400)
    return "\n".join(text.splitlines()[-lines:])


def _journal_tail(lines: int) -> str:
    """The session journal: the only trace of a crash before the tee installed."""
    if shutil.which("journalctl") is None:
        return "(journalctl not available)"
    out = _run(["journalctl", "--user", "--no-pager", "-o", "short-iso",
                "-n", str(lines), "-g", "loquivox|Loquivox|Traceback"],
               timeout=10)
    return out or "(nothing matching in the user journal)"


def _config_summary() -> str:
    """The effective config from Python, not the file: shows what applied."""
    try:
        import loquivox.config as config_module
        cfg = config_module.CFG
        keys = ("BACKEND", "FALLBACK_BACKEND", "TALK_ENGINE", "TTS_ENGINE",
                "MODEL_CHAT", "MODEL_VISION", "OVERLAY_POSITION",
                "POSTPROCESS_LEVEL", "POSTPROCESS_TRANSLATE",
                "TALK_SCREENSHOT", "TALK_RESEARCH", "TALK_BARGE_IN")
        return "\n".join(f"  {k} = {getattr(cfg, k)!r}" for k in keys
                         if hasattr(cfg, k))
    except Exception as e:
        return f"  (config not loadable: {e})"


def _secrets_summary() -> str:
    from loquivox.secrets import MANAGED_KEYS, SECRETS_FILE, read_secrets
    stored = read_secrets()
    rows = []
    for key, label in MANAGED_KEYS.items():
        if stored.get(key):
            src = "set (secrets file)"
        elif os.environ.get(key):
            src = "set (environment)"
        else:
            src = "not set"
        rows.append(f"  {label:<9} {key}: {src}")
    rows.append(f"  secrets file: {SECRETS_FILE} "
                f"({'present' if SECRETS_FILE.exists() else 'absent'})")
    return "\n".join(rows)


def build_report(*, keep_content: bool = False,
                 log_lines: int = REPORT_LOG_LINES) -> str:
    """
    The whole diagnostic report, redacted. ``keep_content=True`` leaves the
    user's own words (transcripts, screen descriptions) in; keys, addresses and
    names are removed either way.
    """
    # config.py needs evdev and tomllib; a report from a machine where either
    # is the problem must still come out, with the files read raw.
    config_dir = Path.home() / ".config" / "loquivox"
    config_file, settings_file = config_dir / "config.toml", config_dir / "settings.json"
    try:
        from loquivox.config import CFG, CONFIG_FILE
        config_file, settings_file = CONFIG_FILE, CFG.SETTINGS_FILE
    except Exception:
        pass
    try:
        from loquivox.platform import detect_session_type
        session = detect_session_type()
    except Exception as e:
        session = f"(detection failed: {e})"
    env = os.environ
    log_path = log_file_path()
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections = [
        ("Loquivox", [
            f"version: {__version__}",
            f"generated: {now}",
            f"redaction: keys/identity always; "
            f"spoken content {'KEPT' if keep_content else 'removed'}",
            f"python: {platform.python_version()} ({sys.executable})",
            f"log file: {log_path or '(disabled)'}",
        ]),
        ("System", [
            f"os: {_os_release()} · kernel {platform.release()} · {platform.machine()}",
            f"session: {session} (XDG_SESSION_TYPE={env.get('XDG_SESSION_TYPE', '')!r})",
            f"desktop: {env.get('XDG_CURRENT_DESKTOP', '') or '(unset)'} · compositor: {_compositor()}",
            f"display: WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY', '')!r} DISPLAY={env.get('DISPLAY', '')!r}",
            f"locale: {env.get('LANG', '')!r}",
            _input_access(),
        ]),
        ("Tools on PATH", [
            f"{tool}: {'yes' if shutil.which(tool) else 'no'}" for tool in _TOOLS
        ]),
        ("GObject introspection", list(_gi_availability())),
        ("Python packages", list(_package_versions())),
        ("Audio input devices", [_audio_devices()]),
        ("Effective config (selected keys)", [_config_summary()]),
        ("API keys", [_secrets_summary()]),
        (f"config.toml ({config_file})", [_read_text(Path(config_file))]),
        (f"settings.json ({settings_file})", [_read_text(Path(settings_file))]),
        (f"Log — last {log_lines} lines", [_log_tail(log_path, log_lines)]),
        ("User journal — recent Loquivox lines", [_journal_tail(80)]),
    ]

    out = []
    for title, lines in sections:
        out.append(f"## {title}")
        out.extend(lines)
        out.append("")
    return redact("\n".join(out), content=not keep_content)


def default_report_path() -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / f"loquivox-report-{stamp}.txt"


def write_report(path: Optional[Path] = None, *, keep_content: bool = False) -> Path:
    """Write the report to ``path`` (default: ``~/loquivox-report-<stamp>.txt``)."""
    path = path or default_report_path()
    path.write_text(build_report(keep_content=keep_content), encoding="utf-8")
    return path


def report_cli(argv: List[str], out: Callable[[str], None] = print) -> int:
    """
    ``loquivox --report [PATH] [--keep-content]``: PATH ``-`` prints to stdout.

    Kept free of GTK on purpose: the machine that needs a report is often the
    one where the GTK stack is what is broken.
    """
    keep = "--keep-content" in argv
    paths = [a for a in argv if not a.startswith("--")]
    target = paths[0] if paths else None
    if target == "-":
        out(build_report(keep_content=keep))
        return 0
    path = write_report(Path(target).expanduser() if target else None,
                        keep_content=keep)
    out(f"📋 Diagnostic report written to {path}")
    out("   Keys, addresses and names are redacted"
        + ("; spoken content was KEPT (--keep-content)." if keep
           else "; spoken content is elided (add --keep-content to include it)."))
    out("   Read it over, then attach it to your bug report.")
    return 0
