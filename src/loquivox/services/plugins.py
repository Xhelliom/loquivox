"""
The extension surface both talk engines share.

``research`` was the first of these and the shape it took is the shape they all
take: **one tool** the model may call, whose answer is too slow to wait for, so
it comes back *between two turns* rather than in the middle of a sentence. That
is the whole pattern — a tool, a desk where its answers pile up, and a
conversation loop that empties the desk at the one moment nobody is talking.

A plugin declares that in one place: the tool (name, description, parameters —
the two wire shapes are derived, never written twice), what to run in the
background, what its answer looks like as a system message, the config key that
gates it, and its card in Settings. Nothing in ``talk.py``,
``realtime_talk.py`` or ``handlers/mode.py`` knows any plugin by name: they
compose the tool list from what is enabled, route calls by tool name, and sweep
the desks.

Deliberately NOT a plugin *system*: no entry points, no scanning, no loading
from outside the tree. A plugin is a module in this package plus a line in
``_MODULES`` — which is the only place adding one touches anything shared.
"""
from __future__ import annotations

import importlib
import json
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

#: the in-tree plugin modules, imported once on first use. Each one calls
#: ``register()`` at import. This tuple is the whole "discovery".
_MODULES: Tuple[str, ...] = (
    "loquivox.services.research",
    "loquivox.services.board",
    "loquivox.services.write",
)

_REGISTRY: Dict[str, "Plugin"] = {}
_loaded = False


@dataclass(frozen=True)
class Plugin:
    """
    One tool the conversation can call, and what happens when it does.

    ``run`` is called on a background thread with the parsed arguments and
    returns the answer as text; returning "" drops the job silently (an
    unusable call, an empty result). It must not raise — the desk catches, but
    a plugin that swallows its own errors can say something useful instead.

    A plugin may instead be a **source**: no tool at all, a ``watch`` that runs
    for the whole session and puts its own messages on the desk (see
    ``services/board.py``). Everything downstream is unchanged — the desk, the
    sweep between two turns, both engines — because a plugin's answer was never
    tied to the model having asked for it.
    """

    #: the tool's name — what the model calls and what dispatch routes on.
    #: A source has no call to route; the name is still its key everywhere else.
    name: str
    #: what the model is told the tool is for
    description: str = ""
    #: JSON-Schema for the call's arguments
    parameters: Dict[str, Any] = field(default_factory=dict)
    #: the tool's immediate return value: the model speaks from this while the
    #: real answer is on its way, so it acknowledges instead of going quiet
    started: str = ""
    #: the work itself, on a thread. Arguments in, spoken-ready text out.
    #: ``None`` for a source: nothing to call, so nothing is put on the table.
    run: Optional[Callable[[Dict[str, Any]], str]] = None
    #: the answer as a system message for either engine — it lands in the
    #: session's turns, so the writing pass has the facts too
    message: Optional[Callable[[Dict[str, Any], str], Dict[str, str]]] = None
    #: whether the tool is on the table at all, read from CFG at session start
    enabled: Callable[[], bool] = lambda: True
    #: the *native* capability this plugin stands in for, if any — a backend
    #: that can already do this itself makes the plugin a second, competing
    #: answer to the same question rather than an addition. Naming it here is
    #: what lets a conversation core step the plugin aside without knowing
    #: which plugin it is (see ``Desks.realtime_tools_except``). "" for a
    #: plugin that duplicates nothing, which is almost all of them.
    provides: str = ""
    #: only offered in an open conversation (F4), never in a briefing (F6)
    #: whose whole ending IS the text it produces. A briefing already has a
    #: way to finish; a tool that finishes it differently is a second one, and
    #: a model handed both takes both.
    chat_only: bool = False
    #: one line for the bubble when the call starts, "" for none
    note: Optional[Callable[[Dict[str, Any]], str]] = None
    #: one line for the bubble when the answer is handed back
    result_note: str = ""
    #: builds this plugin's card in Settings. Called with the SettingsDialog
    #: class (for ``_group``/``_field``/``_actions``), the page box and its
    #: label size group.
    settings: Optional[Callable[..., None]] = None
    #: a source's own loop, run on a daemon thread for the length of the
    #: session. Called with the desk's ``put`` and the event ``Desks.close()``
    #: sets; it must return once that event is set, and must not raise.
    watch: Optional[Callable[[Callable[[Dict[str, str]], None], threading.Event], None]] = None
    #: whether a queued message still holds, asked at the moment it is about
    #: to be handed back — which can be well after it was queued, if the user
    #: kept talking. A message this answers False to is dropped in silence.
    #: ``None`` means an answer never goes stale.
    still_true: Optional[Callable[[Dict[str, str]], bool]] = None

    @property
    def is_tool(self) -> bool:
        """Whether the model is offered this at all — a source is not."""
        return self.run is not None

    @property
    def chat_tool(self) -> Dict[str, Any]:
        """Chat completions wrap the function…"""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}

    @property
    def realtime_tool(self) -> Dict[str, Any]:
        """…Realtime sessions declare it flat. One definition, two shapes."""
        return {"type": "function", "name": self.name,
                "description": self.description, "parameters": self.parameters}


def register(plugin: Plugin) -> None:
    """Add a plugin to the registry. Called at import by each plugin module."""
    _REGISTRY[plugin.name] = plugin


def all_plugins() -> List[Plugin]:
    """Every registered plugin, importing the in-tree modules on first use."""
    global _loaded
    if not _loaded:
        _loaded = True
        for module in _MODULES:
            try:
                importlib.import_module(module)
            except Exception as e:   # a broken plugin must not kill talk mode
                print(f"⚠️  Plugin {module} not loaded: {e}")
    return list(_REGISTRY.values())


def enabled_plugins() -> List[Plugin]:
    """The plugins their config keys currently turn on."""
    return [p for p in all_plugins() if p.enabled()]


def _arguments(raw: str) -> Dict[str, Any]:
    """A tool call's JSON arguments, or {} if unreadable."""
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class Desk:
    """
    One plugin's calls in flight and answers waiting, for one session.

    Two ways in, because the protocols differ on who waits. ``ask`` returns at
    once and ``take`` never blocks — the conversation loops poll it between two
    turns, at the one moment a reply can be slipped in without cutting anyone
    off. ``run_now`` hands the answer straight back to a caller that is already
    holding the line.
    """

    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin
        self._results: "queue.Queue[Dict[str, str]]" = queue.Queue()
        self.pending: int = 0

    def put(self, message: Dict[str, str]) -> None:
        """Queue an answer nobody asked for — a source's way in."""
        self._results.put(message)

    def ask(self, arguments: str) -> None:
        """Start the work for one tool call. Never blocks, never raises."""
        args = _arguments(arguments)
        self._note(args)
        self.pending += 1
        threading.Thread(target=self._work, args=(args,), daemon=True).start()

    def run_now(self, arguments: str) -> Optional[Dict[str, str]]:
        """
        Do the work on *this* thread and return the answer, queueing nothing.

        For a protocol that is waiting on it. The Live engine's Responses
        delegation pauses the backend response until the tool result is
        submitted — "submit every required result for the pending tool calls
        before continuing" — so there is no gap to slip an answer into and
        nothing to sweep: the answer *is* the reply to the call.

        Same work, same message, same never-raises contract as ``ask``. What
        changes is who holds the wait, and therefore who must not be called
        from the main thread.
        """
        args = _arguments(arguments)
        self._note(args)
        return self._answer(args)

    def _note(self, args: Dict[str, Any]) -> None:
        """Say in the bubble that the call has started, if the plugin wants to."""
        note = self.plugin.note(args) if self.plugin.note else ""
        if note:
            from loquivox.managers.chat import ChatManager
            ChatManager.add_message("note", note)

    def _answer(self, args: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        The plugin's answer as a message, or None for an unusable call.

        ``message`` is inside the guard with ``run``, not after it: it is the
        plugin's code too, it reads the same arguments, and a caller told this
        never raises may be holding something open until it returns.
        """
        try:
            result = self.plugin.run(args)
            return self.plugin.message(args, result) if result else None
        except Exception as e:
            print(f"⚠️  Plugin {self.plugin.name} failed: {e}")
            return None

    def _work(self, args: Dict[str, Any]) -> None:
        message = self._answer(args)
        if message is not None:
            self._results.put(message)
        self.pending -= 1

    def ready(self) -> bool:
        return not self._results.empty()

    def take(self) -> Optional[Dict[str, str]]:
        """The next answer that still holds; a stale one is dropped, not returned."""
        while True:
            try:
                message = self._results.get_nowait()
            except queue.Empty:
                return None
            if self.plugin.still_true is None or self._holds(message):
                return message

    def _holds(self, message: Dict[str, str]) -> bool:
        """The plugin's own re-check, and a raising one drops the message."""
        try:
            return bool(self.plugin.still_true(message))
        except Exception as e:
            print(f"⚠️  Plugin {self.plugin.name} could not re-check its answer: {e}")
            return False


class Desks:
    """
    Every enabled plugin's desk, for one session — the object both engines
    talk to instead of knowing any plugin.

    The set is snapshotted when the session opens: a tool that appears
    mid-conversation would be one the model was never told about, and Settings
    already says its changes take effect on the next talk session.

    ``chat`` is the session's shape, not a plugin's name: an open conversation
    (F4) or a briefing that ends in one text (F6). It is all a ``chat_only``
    plugin is filtered on.
    """

    def __init__(self, chat: bool = False) -> None:
        self._desks: Dict[str, Desk] = {p.name: Desk(p) for p in enabled_plugins()
                                        if chat or not p.chat_only}
        #: set when the session ends — the one thing a source watches for
        self.closed = threading.Event()
        for desk in self._desks.values():
            if desk.plugin.watch is not None:
                threading.Thread(target=self._watch, args=(desk,), daemon=True).start()

    def _watch(self, desk: Desk) -> None:
        try:
            desk.plugin.watch(desk.put, self.closed)
        except Exception as e:      # a source must not take the session with it
            print(f"⚠️  Plugin {desk.plugin.name} stopped watching: {e}")

    def close(self) -> None:
        """End the session's sources. Idempotent; called from the talk worker."""
        self.closed.set()

    @property
    def chat_tools(self) -> List[Dict[str, Any]]:
        return [d.plugin.chat_tool for d in self._desks.values() if d.plugin.is_tool]

    @property
    def realtime_tools(self) -> List[Dict[str, Any]]:
        return [d.plugin.realtime_tool for d in self._desks.values() if d.plugin.is_tool]

    def realtime_tools_except(self, native: Tuple[str, ...]) -> List[Dict[str, Any]]:
        """
        The same list, minus the plugins a native tool now covers.

        For a backend that can do something itself: offering it both its own
        tool and a plugin answering the same question does not add a
        capability, it adds a coin toss, and the two do not answer to the same
        settings. So the plugin steps aside.

        The core still names no plugin. It matches on the capability a plugin
        *declares* (``Plugin.provides``) against the native tools actually
        switched on, so every plugin that duplicates nothing — which is nearly
        all of them, now and later — is untouched by any of this.
        """
        return [d.plugin.realtime_tool for d in self._desks.values()
                if d.plugin.is_tool
                and (not d.plugin.provides or d.plugin.provides not in native)]

    def get(self, name: str) -> Optional[Desk]:
        """
        The desk for a tool call's name, or None if it is not ours.

        A source's desk answers None here even though it exists: it was never
        on the table, so a call naming it is a hallucination, and routing one
        into a plugin with no ``run`` is how that becomes a crash.
        """
        desk = self._desks.get(name)
        return desk if desk is not None and desk.plugin.is_tool else None

    def ready(self) -> bool:
        return any(desk.ready() for desk in self._desks.values())

    def coming(self) -> bool:
        """
        Whether an answer is on its way — still being worked on, or queued and
        waiting for the next gap in the conversation.

        Both halves count, because what a caller does with this is decide
        whether something is about to arrive, not which leg of the journey it
        is on. The Live engine's client delegation asks: it hands the voice
        model a stub and must tell it to hold the floor for the real answer.
        """
        return any(desk.pending or desk.ready() for desk in self._desks.values())

    def take(self) -> Optional[Tuple[Plugin, Dict[str, str]]]:
        """Sweep the desks for one answer to hand back. None when there is none."""
        for desk in self._desks.values():
            found = desk.take()
            if found is not None:
                return desk.plugin, found
        return None


if __name__ == "__main__":
    import time

    assert _arguments('{"a": 1}') == {"a": 1}
    assert _arguments("not json") == {} and _arguments("[1]") == {} and _arguments("") == {}

    # One definition, two wire shapes.
    p = Plugin(name="echo", description="d", parameters={"type": "object"},
               started="wait", run=lambda a: a.get("say", ""),
               message=lambda a, r: {"role": "system", "content": r},
               result_note="back")
    assert p.chat_tool == {"type": "function", "function": {
        "name": "echo", "description": "d", "parameters": {"type": "object"}}}
    assert p.realtime_tool == {"type": "function", "name": "echo",
                               "description": "d", "parameters": {"type": "object"}}

    desk = Desk(p)
    assert not desk.ready() and desk.take() is None
    desk.ask('{"say": "hello"}')
    for _ in range(100):
        if desk.ready():
            break
        time.sleep(0.01)
    assert desk.take() == {"role": "system", "content": "hello"}
    desk.ask('{"say": ""}')        # an empty answer is dropped, not queued
    desk.ask("garbage")
    time.sleep(0.1)
    assert not desk.ready(), "an empty result reached the conversation"

    # A source: no tool, its own loop, and the same desk downstream.
    seen = threading.Event()

    def _source(put, stop):
        put({"role": "system", "content": "ping"})
        seen.set()
        stop.wait(30)

    src = Plugin(name="src", watch=_source)
    assert not src.is_tool and p.is_tool

    # A message is re-checked when it is handed back, not when it was queued:
    # what was true at the poll may not be by the next gap in the conversation.
    valid = {"a": True, "b": False}
    stale = Desk(Plugin(name="stale", still_true=lambda m: valid[m["content"]]))
    stale.put({"role": "system", "content": "a"})
    stale.put({"role": "system", "content": "b"})
    stale.put({"role": "system", "content": "a"})
    valid["a"] = False
    assert stale.ready() and stale.take() is None, "a stale answer was handed back"
    stale.put({"role": "system", "content": "b"})
    valid["b"] = True
    assert stale.take() == {"role": "system", "content": "b"}
    crashing = Desk(Plugin(name="crash", still_true=lambda m: 1 / 0))
    crashing.put({"role": "system", "content": "x"})
    assert crashing.take() is None, "a raising re-check let the message through"

    boom = Plugin(name="boom", description="", parameters={}, started="",
                  run=lambda a: 1 / 0, message=lambda a, r: {})
    bad = Desk(boom)
    bad.ask("{}")
    time.sleep(0.1)
    assert not bad.ready(), "a raising plugin queued something"
    assert bad.pending == 0, "a raising plugin was left counted as in flight"

    # A plugin that raises while *shaping* its answer is no different: both
    # halves are its code, and a caller holding a backend open on the promise
    # that this never raises must not be hung by either.
    shaper = Plugin(name="shaper", description="", parameters={}, started="",
                    run=lambda a: "fine", message=lambda a, r: 1 / 0)
    rude = Desk(shaper)
    rude.ask("{}")
    time.sleep(0.1)
    assert not rude.ready() and rude.pending == 0
    assert rude.run_now("{}") is None, "run_now raised despite its contract"

    # run_now: the same work and the same message, handed back instead of
    # queued — and nothing lands on the desk for a sweep to find later.
    direct = Desk(p)
    assert direct.run_now('{"say": "hello"}') == {"role": "system", "content": "hello"}
    assert not direct.ready(), "run_now also queued its answer"
    assert direct.run_now('{"say": ""}') is None      # unusable call, no message
    assert Desk(boom).run_now("{}") is None           # and it still never raises

    # The registry sweeps whatever is enabled, and only that.
    saved = dict(_REGISTRY)
    try:
        _REGISTRY.clear()
        register(p)
        register(src)
        register(Plugin(name="off", description="", parameters={}, started="",
                        run=lambda a: "x", message=lambda a, r: {},
                        enabled=lambda: False))
        register(Plugin(name="seeker", description="", parameters={}, started="",
                        run=lambda a: "x", message=lambda a, r: {},
                        provides="web_search"))
        register(Plugin(name="scribe", description="", parameters={}, started="",
                        run=lambda a: "x", message=lambda a, r: {},
                        chat_only=True))
        # A briefing already ends in a text; a tool that writes one is only on
        # the table in an open conversation.
        assert "scribe" not in [t["name"] for t in Desks().realtime_tools]
        assert "scribe" in [t["name"] for t in Desks(chat=True).realtime_tools]
        assert Desks().get("scribe") is None and Desks(chat=True).get("scribe")
        desks = Desks(chat=True)
        assert [t["function"]["name"] for t in desks.chat_tools] \
            == ["echo", "seeker", "scribe"]
        assert [t["name"] for t in desks.realtime_tools] == ["echo", "seeker", "scribe"]
        # A native tool stands a declaring plugin down, and only that one.
        assert [t["name"] for t in desks.realtime_tools_except(("web_search",))] \
            == ["echo", "scribe"]
        assert [t["name"] for t in desks.realtime_tools_except(())] \
            == ["echo", "seeker", "scribe"]
        assert [t["name"] for t in desks.realtime_tools_except(("other",))] \
            == ["echo", "seeker", "scribe"]
        assert "src" not in [t["name"] for t in desks.realtime_tools_except(())], \
            "a source was declared to a backend as a tool it cannot run"
        assert desks.get("off") is None and desks.get("echo") is not None
        # A source is never routed to, and never offered to the model.
        assert desks.get("src") is None
        assert "src" not in [t["name"] for t in desks.realtime_tools]
        assert seen.wait(2), "the source's watch never ran"
        found = desks.take()
        assert found is not None and found[1]["content"] == "ping"
        desks.close()
        assert desks.closed.is_set()

        assert not desks.ready() and desks.take() is None
        assert not desks.coming(), "idle desks announced an answer on its way"
        desks.get("echo").ask('{"say": "hi"}')
        for _ in range(100):
            if desks.ready():
                break
            time.sleep(0.01)
        # Still coming once the work itself is done: it is queued, and the
        # question a caller asks is whether something is about to arrive, not
        # which leg of the journey it is on.
        assert desks.coming(), "a queued answer read as nothing coming"
        plugin, message = desks.take()
        assert plugin.name == "echo" and message["content"] == "hi"
        assert not desks.coming(), "a handed-back answer was still counted"
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
    print("✓ plugin registry OK")
