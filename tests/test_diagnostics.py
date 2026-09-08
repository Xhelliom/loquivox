"""
Self-check for the diagnostic report's redaction and the log tee (no
framework: `python tests/test_diagnostics.py`; no GTK needed).

What would break silently without it: a key format the redactor no longer
recognises goes straight into a GitHub issue, and nobody notices until the key
is revoked. The other way round is quieter still — a redaction rule that eats
the error message the report exists to carry.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loquivox import diagnostics  # noqa: E402
from loquivox.diagnostics import REDACTED, redact  # noqa: E402


def test_keys_never_survive():
    """Every provider's key format, in every place a log could show it."""
    groq = "gsk_" + "A1b2C3d4" * 6
    openai = "sk-proj-" + "xYz9" * 10
    deepgram = "0123456789abcdef" * 2 + "01234567"
    samples = [
        f"GROQ_API_KEY={groq}",
        f'Exec=env GROQ_API_KEY="{groq}" /home/x/venv/bin/loquivox',
        f'{{"api_key": "{openai}", "model": "gpt"}}',
        f"Authorization: Bearer {openai}",
        f"deepgram token {deepgram} rejected",
        f"error: {openai} is invalid",
        "https://api.example.com/v1/tts?key=" + groq + "&voice=x",
    ]
    for line in samples:
        out = redact(line)
        for secret in (groq, openai, deepgram):
            assert secret not in out, f"{line!r} → {out!r}"
    assert redact(f"GROQ_API_KEY={groq}") == f"GROQ_API_KEY={REDACTED}"
    print("✓ no key format survives the redactor")


def test_identity_is_masked():
    home = str(Path.home())
    out = redact(f"reading {home}/.config/loquivox/config.toml for jane.doe@example.org from 192.168.1.20")
    assert home not in out and "~/.config" in out, out
    assert "jane.doe@example.org" not in out and "[email]" in out, out
    assert "192.168.1.20" not in out and "[ip]" in out, out
    assert "127.0.0.1" in redact("listening on 127.0.0.1:8080")
    print("✓ home, e-mail and address are masked")


def test_spoken_content_is_elided_by_default_and_kept_on_request():
    lines = [
        "⚠️ Ignored Hallucination: 'Sous-titres réalisés par la communauté'",
        "⏭️ Ignored stale transcription (gen 3): 'bonjour, envoie le rapport à Marc'",
        "👁️  Screen context (0.4s): A mail client with a draft to the bank",
        "🔎 Research (2.1s): The capital of Peru is Lima, population 10 million",
    ]
    for line in lines:
        out = redact(line)
        assert "[… " in out and "chars]" in out, out
        assert line.split(": ", 1)[0] in out, f"lost the label: {out}"
        assert line.split(": ", 1)[1].strip("'") not in out, out
        assert redact(line, content=False) == line, "content=False must keep it"
    print("✓ transcripts are elided by default, kept with content=False")


def test_diagnostics_stay_readable():
    """The rules must not eat the lines the report exists to carry."""
    keep = [
        "🎚️  Transcription backend: primary=groq, fallback=whispercpp",
        "ImportError: cannot import name '_gi' from partially initialized module 'gi'",
        "⚠️ config.toml [hotkeys]: unknown mode 'rewrite' — ignored",
        "🔉 echo peak 0.031 · loudest 0.058 · prompt tokens: 898",
        "⌨️  Listening on 2 keyboard device(s)",
        "⚠️  groq failed: Error code: 401 - {'error': {'message': 'Invalid API Key'}}",
        "Traceback (most recent call last):",
    ]
    for line in keep:
        assert redact(line) == line, f"{line!r} → {redact(line)!r}"
    print("✓ backend names, tracebacks and error messages stay intact")


def test_log_tee_timestamps_and_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "loquivox.log"
        sink = diagnostics._LogSink(path, max_bytes=200)
        buf = io.StringIO()
        tee = diagnostics._Tee(buf, sink)
        tee.write("🚀 Loquivox is running.\n")
        tee.write("partial ")
        tee.write("line\n")
        assert buf.getvalue() == "🚀 Loquivox is running.\npartial line\n"
        text = path.read_text()
        assert "🚀 Loquivox is running." in text and "partial line" in text
        for line in text.splitlines():
            assert line[4] == "-" and line[19] == " ", f"no timestamp: {line!r}"
        assert " partial line" in text and "partial \n" not in text, \
            "a line written in two prints must land as one stamped line"

        for i in range(40):
            tee.write(f"line {i}\n")
        assert path.with_name("loquivox.log.1").exists(), "no rotation past max_bytes"
        assert path.stat().st_size < 200 + 60
        tee.flush()
    print("✓ the tee stamps whole lines and rotates by size")


def test_report_builds_without_gtk_and_is_redacted():
    """The report must come out on the machine where GTK is what is broken."""
    os.environ["GROQ_API_KEY"] = "gsk_" + "Q" * 40
    try:
        report = diagnostics.build_report()
    finally:
        del os.environ["GROQ_API_KEY"]
    assert "gsk_" + "Q" * 40 not in report
    assert "GROQ_API_KEY: set (environment)" in report, report
    for title in ("## Loquivox", "## System", "## Tools on PATH", "## Log"):
        assert title in report, f"missing section {title}"
    assert str(Path.home()) not in report, "the home directory leaked"
    print("✓ the report builds GTK-free, lists key presence, leaks no value")


def test_report_cli_writes_a_file():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "report.txt"
        said = []
        assert diagnostics.report_cli([str(target)], out=said.append) == 0
        assert target.exists() and "## Loquivox" in target.read_text()
        assert any(str(target) in s for s in said), said
        said.clear()
        assert diagnostics.report_cli(["-"], out=said.append) == 0
        assert said and "## Loquivox" in said[0]
    print("✓ --report writes a file, or prints with '-'")


def test_log_export_is_whole_and_redacted():
    """The shareable log: both generations, every key gone, content elided."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "loquivox.log"
        key = "gsk_" + "Z" * 40
        log.with_name("loquivox.log.1").write_text("old line one\n")
        log.write_text(f"GROQ_API_KEY={key}\n"
                       "⚠️ Ignored Hallucination: 'merci d'avoir regardé la vidéo'\n")
        os.environ["LOQUIVOX_LOG_FILE"] = str(log)
        try:
            text = diagnostics.export_log()
            assert text.startswith("# Loquivox"), text
            assert "old line one" in text and key not in text, text
            assert "merci d'avoir" not in text and "chars]" in text, text
            kept = diagnostics.export_log(keep_content=True)
            assert "merci d'avoir" in kept and key not in kept, kept

            target = Path(tmp) / "share.txt"
            said = []
            assert diagnostics.report_cli([str(target)], out=said.append,
                                          full_log=True) == 0
            assert target.read_text() == text
            os.environ["LOQUIVOX_LOG_FILE"] = str(Path(tmp) / "absent.log")
            assert diagnostics.export_log() == ""
        finally:
            del os.environ["LOQUIVOX_LOG_FILE"]
    print("✓ the log export covers both generations, redacted")


if __name__ == "__main__":
    test_keys_never_survive()
    test_identity_is_masked()
    test_spoken_content_is_elided_by_default_and_kept_on_request()
    test_diagnostics_stay_readable()
    test_log_tee_timestamps_and_rotates()
    test_report_builds_without_gtk_and_is_redacted()
    test_report_cli_writes_a_file()
    test_log_export_is_whole_and_redacted()
    print("\nAll diagnostics checks passed.")
