"""
Self-check for the mail format of write_text and the paste of multi-line text:

    PYTHONPATH=src python tests/test_write_mail.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loquivox.config as config_module  # noqa: E402
from loquivox.services import write  # noqa: E402
from loquivox.services import clipboard as clip  # noqa: E402


class FakeSession:
    turns = [{"role": "user", "content": "réponds oui pour jeudi"}]

    def __init__(self):
        self.seen = None

    def generate(self, instruction=""):
        self.seen = instruction
        return "Bonjour,\n\nOui pour jeudi.\n\nCordialement,\nStéphane"


def test_mail_flag_adds_format_and_signature():
    cfg = config_module.CFG
    object.__setattr__(cfg, "TALK_MAIL_SIGNATURE", "Stéphane W.")
    clause = write.mail_clause()
    for word in ("salutation", "paragraph", "Stéphane W."):
        assert word in clause, (word, clause)
    assert "mail" in write.PLUGIN.parameters["properties"], "no mail argument"
    s = FakeSession()
    from loquivox.handlers.mode import ModeHandler
    from loquivox.managers.chat import ChatManager
    ModeHandler._deliver_talk_text = staticmethod(lambda text, typed: None)
    ChatManager.set_result = staticmethod(lambda *a, **k: None)
    write._write(s, "oui pour jeudi", mail=True)
    assert "Stéphane W." in s.seen and "oui pour jeudi" in s.seen, s.seen
    s2 = FakeSession()
    write._write(s2, "plus court")
    assert s2.seen == "plus court", "a non-mail text must be left alone"
    print("✓ mail=true carries the format clause and the signature")


def test_no_signature_setting_does_not_invent_one():
    object.__setattr__(config_module.CFG, "TALK_MAIL_SIGNATURE", "")
    assert "do not invent" in write.mail_clause().lower()
    print("✓ empty signature: the model is told not to invent a name")


def test_paste_keeps_newlines_and_no_leading_space_on_a_mail():
    pasted = []

    class Clip:
        def copy(self, t): pasted.append(t)
        def paste(self): return ""

    class Inp:
        def is_terminal_focused(self): return False
        def simulate_paste(self, is_terminal=False): pass

    clip.get_clipboard, clip.get_input = (lambda: Clip()), (lambda: Inp())
    clip.time.sleep = lambda s: None
    mail = "Bonjour,\n\nOui pour jeudi.\n\nCordialement"
    clip.ClipboardService.type_text(mail)
    assert pasted[0] == mail, repr(pasted[0])
    print("✓ a multi-line text is pasted verbatim")


if __name__ == "__main__":
    test_mail_flag_adds_format_and_signature()
    test_no_signature_setting_does_not_invent_one()
    test_paste_keeps_newlines_and_no_leading_space_on_a_mail()
