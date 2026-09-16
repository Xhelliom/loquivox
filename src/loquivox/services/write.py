"""
The write plugin: a text produced *during* the conversation instead of after it.

F6's briefing ends in one text and the session ends with it — you discuss, you
press Enter, a text comes out, it is over. That is one text per conversation,
and reaching it costs the conversation.

This turns the same writing pass into a tool. The model calls
``write_text`` — named so the conversation core, which is full of the word
"write", can be checked for naming no plugin — the
generation runs on a background thread with the conversation as its brief, the
text lands at the user's cursor, and the answer comes back between two turns
like any other plugin's — so nothing closes, the voice model keeps the floor,
and "no, shorter" is the next turn rather than the next session. Which is what
makes a full-duplex engine a co-worker rather than an interviewer: it does the
work while you keep talking to it.

It is ``chat_only``: in a briefing, the end IS the text, and a model offered
both ways to finish takes both.

The brief is the conversation — that is what ``TalkSession.generate()`` reads,
previous versions of the text included, so a second call is a revision and not
a fresh attempt at the same thing. The one argument is the last word on it
("plus court, sans le dernier paragraphe"): a transcript buries that line among
twenty others, and a model that has just been told it has no business hoping
the writing pass digs it back out.

It also gives the model something to fill. Measured on ``gpt-oss-120b``
through Groq, the empty-schema version came back as
``Parsing failed. The model generated output that could not be parsed`` — the
call never reached the desk at all — on the turn right after the model said,
out loud, that it had the function.
"""
from __future__ import annotations

import threading
from typing import Any, Dict

import loquivox.config as config_module
from loquivox.services.plugins import Plugin, register

#: one text at a time. The generation takes seconds and the model is told so,
#: but a turn is shorter than that: asked twice, it would paste twice into a
#: document we cannot tidy up afterwards. The second call is dropped — the
#: first one's answer is already on its way.
_writing = threading.Lock()


def _run(arguments: Dict[str, Any]) -> str:
    """The desk's background job. The session is the one holding the microphone."""
    from loquivox.state import STATE   # lazy: pulls the audio stack in

    return _write(STATE.talk_session, str(arguments.get("instruction", "")))


def _write(session, instruction: str = "") -> str:
    """
    Write the text the conversation has worked out, and put it at the cursor.

    Runs on the desk's thread: the generation is a full completion over the
    whole conversation, several seconds of it, and the point of this plugin is
    that nobody waits for it.

    ponytail: pasted where the cursor is, so a revision lands *next to* the
    previous one unless the user selects it first (the paste replaces a
    selection). Replacing it by ourselves would mean tracking what we typed in
    a window we know nothing about; the user's own selection is a better
    answer than any guess we could make here.
    """
    if session is None or not session.turns:
        return ""      # nothing said yet, or no session: an unusable call
    if not _writing.acquire(blocking=False):
        return ""      # already writing this one — see _writing
    try:
        text = session.generate(instruction)
        if not text:
            return "The text could not be written — say so and ask whether to retry."
        from loquivox.handlers.mode import ModeHandler  # lazy: mode.py imports services
        from loquivox.managers.chat import ChatManager

        ModeHandler._deliver_talk_text(text, typed=True)
        ChatManager.set_result(text, "📋 Collé au curseur — on continue")
        print(f"✍️  Text written and pasted ({len(text)} chars)")
        return text
    finally:
        _writing.release()


def result_message(arguments: Dict[str, Any], result: str) -> Dict[str, str]:
    """
    The written text as a system message for either engine.

    It stays in the session's turns, which is what makes the next call a
    revision: ``generate()`` reads them, so the model rewrites the text it
    can see rather than writing a second one from the same brief.
    """
    return {"role": "system", "content": (
        "TEXT WRITTEN AND PASTED at the user's cursor. This is what they now "
        f"have in front of them:\n---\n{result}\n---\n"
        "Say in one short spoken sentence that it is written, and nothing else "
        "about it — they can read it. If they want it changed, talk it through "
        "and call write_text again: it rewrites this text from the whole "
        "conversation."
    )}


def _settings(dialog, vbox, labels) -> None:
    """This plugin's card in Settings → Plugins."""
    from gi.repository import Gtk

    cfg = config_module.CFG
    body = dialog._group(
        vbox, "Writing",
        "In chat mode (F4), let the conversation produce a text without ending: "
        "ask for it out loud, it is written from everything said so far and "
        "pasted at your cursor, and the conversation carries on — so the next "
        "sentence can be \"shorter, and drop the last paragraph\". Off, F4 "
        "writes nothing and F6 remains the way to get a text.")

    check = Gtk.CheckButton(label="Let the conversation write and paste a text")
    check.set_active(bool(cfg.TALK_WRITE_TOOL))
    dialog._field(body, "Writing", check, labels)

    status = Gtk.Label()

    def _apply(_btn=None) -> None:
        """Write [talk] write_tool."""
        from loquivox.config_io import ConfigWriteError, update_section

        enabled = check.get_active()
        try:
            update_section("talk", {"write_tool": enabled})
        except ConfigWriteError as e:
            status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        print(f"✍️  Write tool: {'on' if enabled else 'off'}")
        status.set_markup(
            f"<small>✓ Writing {'on' if enabled else 'off'} — takes effect on "
            "the next talk session.</small>")

    dialog._actions(body, _apply, status)


PLUGIN = Plugin(
    name="write_text",
    description=(
        "Write the text the conversation has been working out and paste it at "
        "the user's cursor. Call it when they ask for the text — \"écris ça\", "
        "\"write it\", \"go ahead and draft it\" — and again, later, whenever "
        "they ask for a different version: each call rewrites the whole text "
        "from the conversation, so changes are discussed, not patched. Never "
        "answer that you cannot write and never ask what to write: everything "
        "the text needs is in the conversation, and calling this IS writing "
        "it. It runs in the background and takes a few seconds; the "
        "conversation does not stop and neither do you."
    ),
    parameters={
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": "What THIS version must be, in the user's own "
                               "words — \"plus court\", \"sans le dernier "
                               "paragraphe\", \"plus direct\". Leave it out "
                               "when they just asked for the text: the "
                               "conversation is the brief either way.",
            }
        },
    },
    started=(
        "The text is being written in the background from the conversation so "
        "far; it will be pasted at the user's cursor. Say in one short sentence "
        "that you are writing it, then stop. You have NOT written it yet: do "
        "not say what it contains and do not read it out. A system message will "
        "carry it when it is done."
    ),
    run=_run,
    message=result_message,
    enabled=lambda: bool(config_module.CFG.TALK_WRITE_TOOL),
    # A briefing already ends in a text — see the module docstring.
    chat_only=True,
    note=lambda args: "✍️ Rédaction en cours…",
    result_note="📝 Texte rédigé",
    settings=_settings,
)
register(PLUGIN)


if __name__ == "__main__":
    assert PLUGIN.chat_tool["function"]["name"] == "write_text"
    assert PLUGIN.realtime_tool["name"] == "write_text"
    # The conversation core is full of the word "write" (``session.write``):
    # a plugin called that could never be checked out of it. See
    # tests/test_plugins.py::test_the_conversation_core_names_no_plugin.
    assert "_" in PLUGIN.name
    assert PLUGIN.chat_only, "a briefing would be offered a second way to finish"
    assert PLUGIN.is_tool and not PLUGIN.provides

    # No session, or nothing said: an unusable call, dropped rather than run.
    assert _write(None) == ""

    class _Empty:
        turns: list = []
    assert _write(_Empty()) == "", "an empty conversation reached the writing pass"

    # Asked again while one is being written: dropped, never a second paste.
    class _Slow:
        turns = [{"role": "user", "content": "x"}]

        def generate(self, instruction=""):
            assert _write(self) == "", "a second call got through and pasted twice"
            return ""     # the caller's own failure path, tested below
    assert _write(_Slow()).startswith("The text could not be written")
    assert not _writing.locked(), "the lock outlived the call"

    # The call's one argument reaches the writing pass.
    class _Asked:
        turns = [{"role": "user", "content": "x"}]
        seen = ""

        def generate(self, instruction=""):
            _Asked.seen = instruction
            return ""
    _write(_Asked(), "plus court")
    assert _Asked.seen == "plus court", "the instruction never reached generate()"

    # The text comes back inside the message, so generate() sees it next time.
    message = result_message({}, "Bonjour Marie,\nà lundi.")
    assert message["role"] == "system"
    assert "Bonjour Marie," in message["content"] and "à lundi." in message["content"]
    print("✓ write plugin OK")
