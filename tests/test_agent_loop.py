"""
Self-check for the tool agent loop (``TalkSession._with_tools``), no network:

    PYTHONPATH=src python tests/test_agent_loop.py     # needs the `mcp` extra

One delegation chains several tool calls on the real MCP fixture server, with
the model faked: it sees each real result, a missing argument is read rather
than guessed, an acting tool waits for the spoken yes, and what is said
afterwards follows the call's real status — never the model's story.
"""
from __future__ import annotations

import _hermetic  # noqa: F401

import json
import os
import sys
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
from loquivox.services import ai as ai_module, mcp_tools  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402
from loquivox.state import STATE  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)

cfg = config_module.CFG
config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": {"fx": {
    "command": sys.executable, "args": [str(ROOT / "tests/mcp_agent_server.py")],
    "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}, "always": ["*"]}}})
mcp_tools.sync(timeout=20)

real = ai_module.AIService.complete


def call(name, **args):
    return {"id": f"c-{name}", "name": f"mcp_fx_{name}", "arguments": json.dumps(args)}


def drive(script, session):
    """A model that follows ``script(messages) -> list of calls | str``."""
    seen = []

    def fake(messages, on_delta=None, tools=None, tool_calls_out=None, **kw):
        seen.append((messages, [t["function"]["name"] for t in (tools or [])]))
        step = script(messages)
        if isinstance(step, str):
            return step
        tool_calls_out.extend(step)
        return ""
    ai_module.AIService.complete = staticmethod(fake)
    STATE.talk_session = session
    try:
        return session.consult(), seen
    finally:
        ai_module.AIService.complete = real
        STATE.talk_session = None


def tools_text(messages):
    return "\n".join(m["content"] for m in messages if m.get("role") == "tool")


try:
    # 1. read, then act: the missing lot is found, not guessed; the acting call waits
    session = TalkSession()
    session.add_user("lance le prochain lot de loquivox")

    def chain(messages):
        t = tools_text(messages)
        if "launched" in t:
            return "C'est lancé."
        if "13" not in t:
            return [call("find_lot", project="loquivox")]
        return [call("launch_lot", project="loquivox", lot=13)]

    answer, seen = drive(chain, session)
    assert "13" in tools_text(seen[1][0]), "the second round never saw the first real result"
    assert all("mcp_fx_launch_lot" in names for _m, names in seen), "tools were removed in a round"
    assert "launch_lot" in answer and "project=loquivox" in answer and "lot=13" in answer, answer
    assert "launched" not in answer and "lancé" not in answer.lower(), answer
    print("✓ two calls chained in one delegation; the lot is read, the question comes from the frozen arguments")

    # 2. the spoken yes lets that exact action through, once
    session.add_user("oui")
    answer, seen = drive(
        lambda m: "C'est lancé." if "launched" in tools_text(m)
        else [call("launch_lot", project="loquivox", lot=13, confirmed=True)], session)
    assert answer == "C'est lancé." and "launched lot 13 of loquivox" in tools_text(seen[1][0])
    print("✓ the acting tool runs after the yes")

    # 2b. a yes does not cover other arguments
    session.add_user("oui")
    answer, _s = drive(lambda m: [call("launch_lot", project="loquivox", lot=14, confirmed=True)], session)
    assert "lot=14" in answer, answer
    print("✓ a yes is worth one action only")

    # 3. a failed call is never "done", whatever the model says
    session = TalkSession()
    session.add_user("fais broken")
    answer, _s = drive(lambda m: "C'est fait !" if "ÉCHEC" in tools_text(m)
                       else [call("broken", what="x")], session)
    assert "failed" in answer and "broken" in answer and "fait" not in answer.lower(), answer
    print("✓ what is said after a failed call is the failure")

    # 4. bounded in rounds, tools kept to the end
    session = TalkSession()
    session.add_user("boucle")
    config_module.CFG = config_module.CFG.__class__(
        **{**config_module.CFG.__dict__, "AGENT_MAX_TURNS": 4})
    answer, seen = drive(lambda m: [call("find_lot", project="x")], session)
    assert len(seen) == 4 and all(names for _m, names in seen), len(seen)
    assert answer and "13" not in answer
    print("✓ the loop stops at its bound with the tools still on the table")

    # 4b. bound hit right after a call that worked: the action happened, say so
    session = TalkSession()
    session.add_user("lance")
    config_module.CFG = config_module.CFG.__class__(
        **{**config_module.CFG.__dict__, "AGENT_MAX_TURNS": 1})
    answer, _s = drive(lambda m: [call("find_lot", project="x")], session)
    assert "find_lot" in answer.lower() or "find lot" in answer.lower(), answer
    assert "was done" in answer or "a été fait" in answer, answer
    print("✓ the bound after a done call does not say nothing was done")

    # 4c. the words written before a tool call are said at once, once, marker-free
    config_module.CFG = config_module.CFG.__class__(
        **{**config_module.CFG.__dict__, "AGENT_MAX_TURNS": 4})
    session = TalkSession()
    session.add_user("où en est le lot ?")
    spoken, order = [], []
    fake_real = ai_module.AIService.complete

    def lead_in(messages, on_delta=None, tools=None, tool_calls_out=None, **kw):
        if not tools_text(messages):
            tool_calls_out.extend([call("find_lot", project="x")])
            return "Je regarde. [[WRITE]]"
        order.append(list(spoken))     # what was already said when round 2 started
        return "Le lot 13 est prêt."
    ai_module.AIService.complete = staticmethod(lead_in)
    try:
        reply = session._answer(None, spoken.append)
    finally:
        ai_module.AIService.complete = fake_real
    assert spoken == ["Je regarde."], spoken
    assert order == [["Je regarde."]], (order, "the lead-in waited for the tools")
    assert reply.text == "Le lot 13 est prêt." and not reply.done, reply
    print("✓ a lead-in before tool calls is said first, not again, without the marker")

    # 4e. a lead-in the user cut in on stops the loop: no more reads, nothing kept
    session = TalkSession()
    session.add_user("où en est le lot ?")
    rounds = []

    def chatty(messages, on_delta=None, tools=None, tool_calls_out=None, **kw):
        rounds.append(1)
        tool_calls_out.extend([call("find_lot", project="x")])
        return f"Je regarde {len(rounds)}."
    ai_module.AIService.complete = staticmethod(chatty)
    try:
        reply = session._answer(None, lambda t: False)
    finally:
        ai_module.AIService.complete = fake_real
    assert reply is None and len(rounds) == 1, (reply, rounds)
    assert all("Je regarde" not in (t["content"] or "") for t in session.turns), session.turns
    print("✓ a cut lead-in stops the loop and is not kept in the turns")

    # 5. the loop names no provider or model
    src = (ROOT / "src/loquivox/services/talk.py").read_text()
    body = src[src.index("def _with_tools"):src.index("def consult")]
    assert not any(w in body.lower() for w in ("groq", "openai", "anthropic", "claude", "gpt")), "provider named"
    print("✓ the loop names no provider")
finally:
    ai_module.AIService.complete = real
    config_module.CFG = cfg
    mcp_tools.stop()
