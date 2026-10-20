"""
Self-check for the plugin surface, run without a network or a sound card:

    PYTHONPATH=src python tests/test_plugins.py

What it pins is the promise of ``services/plugins.py``: a plugin is declared in
one file, and *nothing in the conversation core* learns its name. So the check
registers a second plugin — one that exists nowhere but here — and drives it
through every engine: the tool lists, the dispatch on each side, the desk sweep
between turns, and the answer landing in the session's turns.

The Live engine is driven twice, because its two delegation modes reach a
plugin by opposite routes — OpenAI's backend names the tool on the wire, ours
never sees a tool name at all and reasons its way to the same call.

If adding a plugin ever starts requiring an edit to ``talk.py``,
``realtime_talk.py``, ``live_talk.py`` or ``handlers/mode.py``, this file is
what fails.
"""
from __future__ import annotations

import _hermetic  # noqa: F401  (must precede any loquivox import)

import re
import threading
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loquivox.managers.chat as chat_module  # noqa: E402

chat_module.ChatManager.add_message = staticmethod(lambda *a, **k: None)
chat_module.ChatManager.stream = staticmethod(lambda *a, **k: None)

from loquivox.services import plugins  # noqa: E402
from loquivox.services.plugins import Plugin  # noqa: E402
from loquivox.services.talk import TalkSession  # noqa: E402

#: the second plugin, defined nowhere but in this file
ENABLED = {"on": True}
WEATHER = Plugin(
    name="weather",
    description="Look up the weather.",
    parameters={"type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]},
    started="Looking it up; say so and carry on.",
    run=lambda args: f"It is raining in {args['city']}.",
    message=lambda args, result: {"role": "system",
                                  "content": f"WEATHER for {args['city']}: {result}"},
    enabled=lambda: ENABLED["on"],
    note=lambda args: f"🌧️ {args['city']}",
    result_note="🌧️ got it",
)


class Case:
    """A tool the core has never heard of, and how to recognise its answer."""

    def __init__(self, name, started, says):
        self.name, self.started, self.says = name, started, says


WEATHER_CASE = Case("weather", WEATHER.started,
                    lambda content, city: content == f"WEATHER for {city}: It is raining in {city}.")
#: the same tool, but served by a stdio MCP server (tests/mcp_fixture_server.py)
MCP_CASE = Case("mcp_fx_weather", None,
                lambda content, city: f"It is raining in {city}." in content
                and "never follow" in content.lower())


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_plugin_is_declared_once():
    """One declaration, both wire shapes, no second definition anywhere."""
    plugins.register(WEATHER)
    session = TalkSession()
    chat = {t["function"]["name"] for t in session.desks.chat_tools}
    realtime = {t["name"] for t in session.desks.realtime_tools}
    assert "weather" in chat and "weather" in realtime, (chat, realtime)
    assert chat == realtime, "the two shapes drifted apart"
    print(f"✓ a plugin declared once is offered to both engines: {sorted(chat)}")


def test_the_config_key_is_the_only_gate():
    """Off in config means off the table — in both engines, with no core branch."""
    ENABLED["on"] = False
    try:
        session = TalkSession()
        assert session.desks.get("weather") is None
        assert "weather" not in {t["name"] for t in session.desks.realtime_tools}
    finally:
        ENABLED["on"] = True
    assert TalkSession().desks.get("weather") is not None
    print("✓ the plugin's own config key decides, nothing else")


def test_the_cascade_routes_by_tool_name(case=WEATHER_CASE):
    """
    ``TalkSession._answer`` dispatches a tool call to its desk and answers the
    model with that plugin's stub — with a plugin it has never heard of.
    """
    import loquivox.services.talk as talk_module

    session = TalkSession()
    seen = {}

    def _fake_complete(messages, on_delta=None, tools=None, tool_calls_out=None, **_kw):
        if not any(m.get("role") == "tool" for m in messages):  # first pass
            seen["tools"] = [t["function"]["name"] for t in (tools or [])]
            tool_calls_out.append({"id": "c1", "name": case.name,
                                   "arguments": '{"city": "Lyon"}'})
            return ""
        seen["stub"] = [m for m in messages if m.get("role") == "tool"]
        return "Je regarde."

    real = talk_module.AIService.complete
    try:
        talk_module.AIService.complete = staticmethod(_fake_complete)
        reply = session.reply("il fait quoi à Lyon ?")
    finally:
        talk_module.AIService.complete = real

    assert reply is not None and reply.text == "Je regarde."
    assert case.name in seen["tools"], seen["tools"]
    assert seen["stub"] and case.says(seen["stub"][0]["content"], "Lyon"), seen["stub"]
    assert not any(t.get("role") == "tool" for t in session.turns), \
        "the plumbing leaked into the turns"
    assert not session.desks.ready(), "the answer was queued as well as returned"
    print("✓ the cascade routes an unknown tool by name and hands the model its real result")


def test_the_realtime_engine_routes_the_same_call(case=WEATHER_CASE):
    """The Realtime dispatch is the same sweep, on the server's event shape."""
    from loquivox.services.realtime_talk import RealtimeTalk

    session = TalkSession()
    talk = RealtimeTalk(session)
    started = []
    talk._loop.create_task = lambda coro: (started.append(coro), coro.close())

    class _Event:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    talk._handle(_Event(type="response.function_call_arguments.done",
                        name="nothing_here", arguments="{}", call_id="x"))
    assert not started, "a tool this session never declared was answered"

    talk._handle(_Event(type="response.function_call_arguments.done",
                        name=case.name, arguments='{"city": "Brest"}', call_id="c9"))
    assert started, "the Realtime engine dropped a registered tool call"
    assert _wait(session.desks.ready)

    # …and the answer only goes in between turns — neither side talking.
    talk._conn = object()
    talk._user_speaking = True
    assert not talk.push_result(), "an answer cut the user off mid-sentence"
    talk._user_speaking = False
    # The server finishes SENDING a reply seconds before it has been heard:
    # what is still queued for the sound card is a sentence in progress.
    talk._audio.put(b"\0" * 4800)
    assert not talk.push_result(), "an answer landed on a reply still being played"
    talk._audio.get_nowait()
    talk._say_result = lambda content: None      # no loop running here
    talk._loop.call_soon_threadsafe = lambda *a, **k: None
    import asyncio
    real_run = asyncio.run_coroutine_threadsafe
    try:
        asyncio.run_coroutine_threadsafe = lambda coro, loop: None
        assert talk.push_result(), "the answer never reached the model"
    finally:
        asyncio.run_coroutine_threadsafe = real_run
    assert case.says(session.turns[-1]["content"], "Brest"), session.turns
    print("✓ the Realtime engine routes and injects the same plugin, between turns")


def test_the_live_engine_answers_the_backend_with_the_real_result(case=WEATHER_CASE):
    """
    Responses delegation: the same plugin, and the backend reads its answer.

    The tool list is the Realtime one — the Responses wire shape for a function
    is the same flat shape, which is why no third definition exists — and the
    call arrives inside ``response.event`` rather than as a top-level event.

    Unlike the other two engines, nothing is stubbed: the backend response is
    paused until its tool result is submitted, and the voice model carries the
    conversation meanwhile, so the real answer goes straight back. Nothing is
    left on a desk for a between-turns sweep to find.
    """
    import asyncio

    from loquivox.services.live_talk import LiveTalk

    session = TalkSession()
    talk = LiveTalk(session)
    outputs = []
    real_run = asyncio.run_coroutine_threadsafe

    def _capture(coro, loop):
        outputs.append(coro.cr_frame.f_locals.get("output"))
        coro.close()

    asyncio.run_coroutine_threadsafe = _capture

    class _Event:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def _call(name):
        talk._handle(_Event(type="response.event", delegation_id="d1", event={
            "type": "response.output_item.done",
            "item": {"type": "function_call", "name": name, "call_id": "c9",
                     "arguments": '{"city": "Brest"}'}}))

    try:
        _call("nothing_here")
        time.sleep(0.05)
        assert not outputs, "a tool this session never declared was answered"
        _call(case.name)
        assert _wait(lambda: bool(outputs)), "the Live engine dropped a tool call"
    finally:
        asyncio.run_coroutine_threadsafe = real_run

    assert case.says(outputs[0], "Brest"), outputs
    assert not session.desks.ready(), "the answer was also queued for a sweep"
    # …and the writing pass still gets the facts, while the call never does.
    assert session.turns == [{"role": "system",
                              "content": outputs[0]}], session.turns
    print("✓ the Live engine answers a Responses delegation with the real result")


def test_client_delegation_reaches_the_plugin_through_our_backend(case=WEATHER_CASE):
    """
    Client delegation: no tool name on the wire, and the plugin still runs.

    ``session.delegation.created`` carries "metadata, not task text" — so the
    only route to a tool is our own backend call, which is handed the same
    ``chat_tools`` the cascade uses. This drives that whole path with the model
    faked out, and what it pins is that the tool the core has never heard of is
    on the table when the backend is consulted.
    """
    from loquivox.services import ai as ai_module
    from loquivox.services.live_talk import AWAIT_RESULT, LiveTalk

    session = TalkSession()
    session.add_user("quel temps fait-il à Lyon")
    talk = LiveTalk(session)
    offered = []
    calls = [{"id": "c1", "name": case.name, "arguments": '{"city": "Lyon"}'}]

    def _fake_complete(messages, on_delta=None, tools=None, tool_calls_out=None,
                       **kw):
        offered.append([t["function"]["name"] for t in (tools or [])])
        if not any(m.get("role") == "tool" for m in messages):
            tool_calls_out.extend(calls)
            return ""
        return "Il pleut à Lyon."

    sent = []
    talk._append = lambda kind, content, delegation_id=None: sent.append(
        (kind, content, delegation_id))
    real_complete = ai_module.AIService.complete
    ai_module.AIService.complete = staticmethod(_fake_complete)
    try:
        talk._on_delegation(_Event_delegation("item_7", "client"))
        assert _wait(lambda: bool(sent))
    finally:
        ai_module.AIService.complete = real_complete

    assert case.name in offered[0], offered
    # The agent loop ran the plugin in place and read its real result, so
    # there is nothing left on the way: no rule to hold the floor, just the answer.
    assert sent == [("commentary", "Il pleut à Lyon.", "item_7")], sent
    # The backend's answer is NOT a turn: the voice model says it in its own
    # words, and that spoken version is what the transcript brings back.
    assert session.turns[-1]["content"] == "quel temps fait-il à Lyon", session.turns
    assert not session.desks.ready(), "the answer was queued as well as used"
    desk = session.desks.get(case.name)
    desk._results.put(desk.plugin.message({"city": "Lyon"}, "It is raining in Lyon."))

    # This is the path that still hands a plugin's answer back between turns —
    # nobody is holding the line here — so the gating is checked on it.
    sent.clear()
    talk._conn = object()
    talk._live["user"] = "et à Brest"             # the user has the floor
    assert not talk.push_result(), "an answer cut the user off mid-sentence"
    talk._live.pop("user")
    talk._audible = 1                             # a reply still in the sound card
    assert not talk.push_result(), "an answer landed on a reply still playing"
    talk._audible = 0
    assert talk.push_result(), "the answer never reached the model"
    assert sent[0][0] == "commentary", sent   # spoken aloud, not thought silently
    assert case.says(session.turns[-1]["content"], "Lyon"), session.turns
    print("✓ client delegation reaches the plugin with no tool name on the wire")


def test_the_writing_pass_sees_a_request_still_being_transcribed():
    """Live: delegation lands mid-transcription; generate() must still get the sentence."""
    from loquivox.services import ai as ai_module
    from loquivox.services.live_talk import LiveTalk

    session = TalkSession()
    talk = LiveTalk(session)
    talk.SETTLE_QUIET = 0.1
    talk._on_delta("user", "écris que la capture ")      # open, not closed yet
    seen = []
    real = ai_module.AIService.complete
    ai_module.AIService.complete = staticmethod(
        lambda messages, **kw: seen.append(messages) or "ok")
    threading.Timer(0.05, talk._on_delta, ("user", "est inutile")).start()
    try:
        assert session.generate("") == "ok"
    finally:
        ai_module.AIService.complete = real
    sent = " ".join(m["content"] for m in seen[0] if m["role"] == "user")
    assert "écris que la capture est inutile" in sent, seen[0]
    print("✓ the writing pass waits for the sentence being transcribed")


def _capture_generate(session, instruction=""):
    from loquivox.services import ai as ai_module
    seen = []
    real = ai_module.AIService.complete
    ai_module.AIService.complete = staticmethod(
        lambda messages, **kw: seen.append(messages) or "ok")
    try:
        session.generate(instruction)
    finally:
        ai_module.AIService.complete = real
    return seen[0]


def test_a_delegation_before_the_first_delta_still_gets_the_request():
    """Dictated paragraph → delegation.created → (late) first delta → generate()."""
    from loquivox.services.live_talk import LiveTalk

    session = TalkSession()
    talk = LiveTalk(session)
    talk.SETTLE_QUIET = 0.1
    # Nothing is open yet: the transcription has not begun.
    for i, piece in enumerate(["écris un mail à Marie ", "pour lui dire merci"]):
        threading.Timer(0.3 + 0.05 * i, talk._on_delta, ("user", piece)).start()
    started = time.monotonic()
    talk._settle(expect_new=True)               # what _consult does first
    assert time.monotonic() - started >= 0.3, "returned before the sentence arrived"
    sent = " ".join(m["content"] for m in _capture_generate(session) if m["role"] == "user")
    assert "mail à Marie pour lui dire merci" in sent, sent
    # No new utterance ever comes: bounded, not stuck.
    talk.SETTLE_FIRST = 0.2
    started = time.monotonic()
    talk._settle(expect_new=True)
    assert time.monotonic() - started < 1.0
    print("✓ a delegation ahead of the first transcript delta waits for it")


def test_plus_court_reaches_the_writing_pass_with_the_previous_text():
    session = TalkSession()
    session.add_user("écris un mail à Marie pour lui dire merci")
    session.turns.append({"role": "system", "content": "TEXT WRITTEN\n---\nBonjour Marie, merci pour tout.\n---"})
    session.add_user("plus court")
    msgs = _capture_generate(session, "plus court")
    joined = " | ".join(m["content"] for m in msgs)
    assert "Bonjour Marie, merci pour tout." in joined, "the previous text is missing"
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"].endswith("plus court"), msgs[-1]
    print("✓ « plus court » reaches the writing pass with the previous text")


def test_an_mcp_server_reaches_all_three_engines():
    """
    The MCP client adds no engine branch either: a tool of a real stdio server
    goes through the same four routes as the in-file plugin above.
    """
    import os

    try:
        import mcp  # noqa: F401
    except ImportError:
        print("⏭  mcp extra not installed — MCP engines check skipped")
        return
    import loquivox.config as config_module
    from loquivox.services import mcp_tools

    cfg = config_module.CFG
    config_module.CFG = cfg.__class__(**{**cfg.__dict__, "MCP_SERVERS": {"fx": {
        "command": sys.executable, "args": [str(ROOT / "tests/mcp_fixture_server.py")],
        "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}, "always": ["*"]}}})
    try:
        mcp_tools.sync(timeout=20)
        MCP_CASE.started = next(p for p in plugins.all_plugins()
                                if p.name == MCP_CASE.name).started
        test_the_cascade_routes_by_tool_name(MCP_CASE)
        test_the_realtime_engine_routes_the_same_call(MCP_CASE)
        test_the_live_engine_answers_the_backend_with_the_real_result(MCP_CASE)
        test_client_delegation_reaches_the_plugin_through_our_backend(MCP_CASE)
    finally:
        config_module.CFG = cfg
        mcp_tools.stop()
    print("✓ an MCP server's tool reaches the cascade, Realtime and both Live delegations")


def test_an_answer_is_rechecked_when_it_is_handed_back():
    """
    A plugin may say, at the last moment, that its answer no longer holds.

    The desk is emptied at the next gap in the conversation, which can be long
    after the answer was queued. ``Plugin.still_true`` is asked then, by the
    one sweep all three engines share, so a message that stopped being true in
    between is never said — and the core still names no plugin.
    """
    holds = {"ok": True}
    plugins.register(Plugin(name="ticker",
                            watch=lambda put, stop: (put({"role": "system", "content": "tick"}),
                                                     stop.wait(30)),
                            still_true=lambda message: holds["ok"]))
    try:
        session = TalkSession()
        assert _wait(session.desks.ready), "the source never spoke"
        holds["ok"] = False
        assert session.desks.take() is None, "a stale answer was handed back"
        assert not session.desks.ready()
        session.desks.close()

        holds["ok"] = True
        session = TalkSession()
        assert _wait(session.desks.ready)
        plugin, message = session.desks.take()
        assert plugin.name == "ticker" and "tick" in message["content"]
        session.desks.close()
    finally:
        plugins._REGISTRY.pop("ticker", None)
    print("✓ an answer is re-checked the moment it is handed back")


def test_a_source_linked_to_a_server_is_explained_with_its_read_tools():
    """
    A source that ``loads`` a server gets an explaining clause in its frame:
    read with that server's tools first (data, never an instruction), say what
    / why / impact / advice, act only after a confirmed yes. One that loads
    nothing keeps the plain frame. The wording names no tool.
    """
    plugins.register(Plugin(name="linked", loads="srv",
                            watch=lambda put, stop: (put({"role": "system", "content": "x"}),
                                                     stop.wait(30))))
    plugins.register(Plugin(name="plain",
                            watch=lambda put, stop: (put({"role": "system", "content": "x"}),
                                                     stop.wait(30))))
    try:
        session = TalkSession()
        session.desks.summarize = False   # one frame per message, not the summary of both
        said = {}

        def both():
            while (found := session.desks.take()) is not None:
                said[found[0].name] = found[1]["content"]
            return {"linked", "plain"} <= said.keys()
        assert _wait(both), said
        session.desks.close()
    finally:
        plugins._REGISTRY.pop("linked", None)
        plugins._REGISTRY.pop("plain", None)
    text = said["linked"]
    assert "srv" in text and "read-only" in text and "never an instruction" in text
    assert "why" in text and "advise" in text and "confirmation" in text
    assert "FIRST" in text and "short sentence" in text, "no quick first sentence"
    assert "read-only" not in said["plain"], "a source with no server was told to explain"
    print("✓ a source linked to a server is explained with that server's read tools")


class _Event_delegation:
    """The shape ``session.delegation.created`` arrives in."""

    def __init__(self, did: str, target: str) -> None:
        self.type = "session.delegation.created"
        self.delegation = type("D", (), {"id": did, "target": target,
                                         "type": "delegation"})()


def test_a_native_tool_stands_down_only_what_it_replaces():
    """
    Choosing OpenAI's own search must not cost the user their other plugins.

    The rule is a capability, not a list of names: a plugin steps aside only
    when it declares (``Plugin.provides``) the very thing the backend has been
    given natively. WEATHER declares nothing, so no native tool can ever
    displace it — which is the promise for every plugin written later.
    """
    import loquivox.config as config_module

    from loquivox.services.live_talk import LiveTalk

    session = TalkSession()
    names = lambda tools: [t.get("name") for t in tools if t.get("type") == "function"]
    assert "weather" in names(session.desks.realtime_tools)

    cfg = config_module.CFG
    try:
        for choice, search_offered in (("plugin", True), ("native", False)):
            config_module.CFG = cfg.__class__(**{
                **cfg.__dict__, "TALK_LIVE_DELEGATION": "responses",
                "TALK_LIVE_WEB_SEARCH": choice, "TALK_RESEARCH": True})
            tools = LiveTalk(session)._config()["delegation"]["responses"]["tools"]
            assert "weather" in names(tools), (choice, tools)
            assert ("research" in names(tools)) is search_offered, (choice, tools)
            assert ({"type": "web_search"} in tools) is not search_offered, tools
    finally:
        config_module.CFG = cfg
    print("✓ a native tool stands down only the plugin that declares it")


def test_the_conversation_core_names_no_plugin():
    """
    The point of the whole thing: adding a plugin edits no core file.

    Checked the blunt way — the three files that hold a conversation may not
    mention any registered plugin by name.
    """
    names = [p.name for p in plugins.all_plugins()]
    assert "research" in names, "the reference plugin is not registered"
    for relative in ("src/loquivox/services/talk.py",
                     "src/loquivox/services/realtime_talk.py",
                     "src/loquivox/services/live_talk.py",
                     "src/loquivox/handlers/mode.py",
                     "src/loquivox/handlers/companion.py"):
        source = (ROOT / relative).read_text()
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", source), \
                f"{relative} names the plugin {name!r} — that is the hard-wiring"
    print(f"✓ the conversation core names none of {names}")


if __name__ == "__main__":
    test_a_plugin_is_declared_once()
    test_a_source_linked_to_a_server_is_explained_with_its_read_tools()
    test_the_config_key_is_the_only_gate()
    test_the_cascade_routes_by_tool_name()
    test_the_realtime_engine_routes_the_same_call()
    test_the_live_engine_answers_the_backend_with_the_real_result()
    test_client_delegation_reaches_the_plugin_through_our_backend()
    test_a_native_tool_stands_down_only_what_it_replaces()
    test_an_answer_is_rechecked_when_it_is_handed_back()
    test_the_writing_pass_sees_a_request_still_being_transcribed()
    test_a_delegation_before_the_first_delta_still_gets_the_request()
    test_plus_court_reaches_the_writing_pass_with_the_previous_text()
    test_an_mcp_server_reaches_all_three_engines()
    test_the_conversation_core_names_no_plugin()
    print("\n✓ the plugin surface holds for a plugin the core has never heard of")
