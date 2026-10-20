"""
Self-check: an acting MCP tool keeps its guarantees on the two routes the agent
loop does not drive — Realtime (``RealtimeTalk._handle``) and Live under the
``responses`` delegation (``LiveTalk._answer_call``):

    PYTHONPATH=src python tests/test_acting_routes.py     # needs the `mcp` extra

Pinned on each: a call without the user's yes runs nothing and hands the voice
the exact question (built from the frozen arguments) in a form that cannot be
read as a success; a call after the yes runs once; a failed call (isError) is
handed over as a failure.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import mcp  # noqa: F401
except ImportError:
    print("⏭  mcp extra not installed — skipped")
    raise SystemExit(0)

import loquivox.config as config_module  # noqa: E402
import loquivox.managers.chat as chat_module  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)
chat_module.ChatManager.stream = staticmethod(lambda *a, **k: None)

from loquivox.services import mcp_tools, plugins  # noqa: E402
from loquivox.services.live_talk import LiveTalk  # noqa: E402
from loquivox.services.realtime_talk import RealtimeTalk  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402

TOOL = "mcp_fx_delete_all"
QUESTION = "Run delete_all (fx) with what=everything"
ARGS = '{"what": "everything"}'


class _Event:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _wait(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _live(talk, args=ARGS):
    """One Responses call on the Live engine; what went back to the backend."""
    outputs = []
    real = asyncio.run_coroutine_threadsafe

    def capture(coro, loop):
        outputs.append(coro.cr_frame.f_locals.get("output"))
        coro.close()

    asyncio.run_coroutine_threadsafe = capture
    try:
        talk._handle(_Event(type="response.event", delegation_id="d", event={
            "type": "response.output_item.done",
            "item": {"type": "function_call", "name": TOOL, "call_id": "c", "arguments": args}}))
        assert _wait(lambda: bool(outputs)), "the Live engine never answered the call"
    finally:
        asyncio.run_coroutine_threadsafe = real
    return outputs[0]


def _realtime(talk, session, args=ARGS):
    """One Realtime call: the stub answered at once, then the swept answer."""
    stubs = []
    talk._loop.create_task = lambda coro: (stubs.append(coro.cr_frame.f_locals.get("started")),
                                           coro.close())
    talk._handle(_Event(type="response.function_call_arguments.done",
                        name=TOOL, arguments=args, call_id="c"))
    assert _wait(session.desks.ready), "the Realtime engine never produced an answer"
    _plugin, message = session.desks.take()
    return stubs[0], message["content"]


def _says_nothing_done(text):
    assert "NOTHING WAS DONE" in text, text
    assert "deleted" not in text, text


def main():
    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": {"fx": {
        "command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")],
        "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}, "always": ["*"]}}})
    try:
        mcp_tools.sync(timeout=20)
        for name, drive in (("Live responses", "live"), ("Realtime", "rt")):
            session = STATE.talk_session = TalkSession()
            if drive == "live":
                talk = LiveTalk(session)
                ask = lambda a=ARGS: ("", _live(talk, a))        # noqa: E731
            else:
                talk = RealtimeTalk(session)
                ask = lambda a=ARGS: _realtime(talk, session, a)   # noqa: E731

            stub, held = ask()
            _says_nothing_done(held)
            assert QUESTION in held, f"{name}: the question is not built from the frozen args: {held}"
            assert "launched" not in stub.lower() or "never" in stub.lower(), stub

            # the model insists with confirmed=true and nobody has spoken
            _stub, again = ask('{"what": "everything", "confirmed": true}')
            _says_nothing_done(again)

            # the user says yes: it runs, once
            session.add_user("oui")
            _stub, done = ask('{"what": "everything", "confirmed": true}')
            assert "deleted everything" in done, done

            # a failed call is handed over as a failure, not as a result
            real_raw = mcp_tools._call_raw
            mcp_tools._call_raw = lambda s, t, a: f"{plugins.FAILED}boom"
            try:
                ask('{"what": "x"}')
                session.add_user("oui")
                _stub, failed = ask('{"what": "x", "confirmed": true}')
            finally:
                mcp_tools._call_raw = real_raw
            _says_nothing_done(failed.replace("deleted", ""))
            assert "boom" in failed and "failed" in failed, failed
            print(f"✓ {name}: the yes is awaited, the question is the frozen one, a failure is a failure")
    finally:
        STATE.talk_session = None
        config_module.CFG = cfg
        mcp_tools.stop()


if __name__ == "__main__":
    main()
