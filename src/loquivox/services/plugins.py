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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

#: the in-tree plugin modules, imported once on first use. Each one calls
#: ``register()`` at import. This tuple is the whole "discovery".
_MODULES: Tuple[str, ...] = (
    "loquivox.services.research",
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
    """

    #: the tool's name — what the model calls and what dispatch routes on
    name: str
    #: what the model is told the tool is for
    description: str
    #: JSON-Schema for the call's arguments
    parameters: Dict[str, Any]
    #: the tool's immediate return value: the model speaks from this while the
    #: real answer is on its way, so it acknowledges instead of going quiet
    started: str
    #: the work itself, on a thread. Arguments in, spoken-ready text out.
    run: Callable[[Dict[str, Any]], str]
    #: the answer as a system message for either engine — it lands in the
    #: session's turns, so the writing pass has the facts too
    message: Callable[[Dict[str, Any], str], Dict[str, str]]
    #: whether the tool is on the table at all, read from CFG at session start
    enabled: Callable[[], bool] = lambda: True
    #: one line for the bubble when the call starts, "" for none
    note: Optional[Callable[[Dict[str, Any]], str]] = None
    #: one line for the bubble when the answer is handed back
    result_note: str = ""
    #: builds this plugin's card in Settings. Called with the SettingsDialog
    #: class (for ``_group``/``_field``/``_actions``), the page box and its
    #: label size group.
    settings: Optional[Callable[..., None]] = None

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

    ``ask`` returns at once; ``take`` never blocks — the conversation loops
    poll it between two turns, at the one moment a reply can be slipped in
    without cutting anyone off.
    """

    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin
        self._results: "queue.Queue[Dict[str, str]]" = queue.Queue()
        self.pending: int = 0

    def ask(self, arguments: str) -> None:
        """Start the work for one tool call. Never blocks, never raises."""
        args = _arguments(arguments)
        note = self.plugin.note(args) if self.plugin.note else ""
        if note:
            from loquivox.managers.chat import ChatManager
            ChatManager.add_message("note", note)
        self.pending += 1
        threading.Thread(target=self._work, args=(args,), daemon=True).start()

    def _work(self, args: Dict[str, Any]) -> None:
        try:
            result = self.plugin.run(args)
        except Exception as e:
            print(f"⚠️  Plugin {self.plugin.name} failed: {e}")
            result = ""
        if result:
            self._results.put(self.plugin.message(args, result))
        self.pending -= 1

    def ready(self) -> bool:
        return not self._results.empty()

    def take(self) -> Optional[Dict[str, str]]:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None


class Desks:
    """
    Every enabled plugin's desk, for one session — the object both engines
    talk to instead of knowing any plugin.

    The set is snapshotted when the session opens: a tool that appears
    mid-conversation would be one the model was never told about, and Settings
    already says its changes take effect on the next talk session.
    """

    def __init__(self) -> None:
        self._desks: Dict[str, Desk] = {p.name: Desk(p) for p in enabled_plugins()}

    @property
    def chat_tools(self) -> List[Dict[str, Any]]:
        return [d.plugin.chat_tool for d in self._desks.values()]

    @property
    def realtime_tools(self) -> List[Dict[str, Any]]:
        return [d.plugin.realtime_tool for d in self._desks.values()]

    def get(self, name: str) -> Optional[Desk]:
        """The desk for a tool call's name, or None if it is not ours."""
        return self._desks.get(name)

    def ready(self) -> bool:
        return any(desk.ready() for desk in self._desks.values())

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

    boom = Plugin(name="boom", description="", parameters={}, started="",
                  run=lambda a: 1 / 0, message=lambda a, r: {})
    bad = Desk(boom)
    bad.ask("{}")
    time.sleep(0.1)
    assert not bad.ready(), "a raising plugin queued something"

    # The registry sweeps whatever is enabled, and only that.
    saved = dict(_REGISTRY)
    try:
        _REGISTRY.clear()
        register(p)
        register(Plugin(name="off", description="", parameters={}, started="",
                        run=lambda a: "x", message=lambda a, r: {},
                        enabled=lambda: False))
        desks = Desks()
        assert [t["function"]["name"] for t in desks.chat_tools] == ["echo"]
        assert [t["name"] for t in desks.realtime_tools] == ["echo"]
        assert desks.get("off") is None and desks.get("echo") is not None
        assert not desks.ready() and desks.take() is None
        desks.get("echo").ask('{"say": "hi"}')
        for _ in range(100):
            if desks.ready():
                break
            time.sleep(0.01)
        plugin, message = desks.take()
        assert plugin.name == "echo" and message["content"] == "hi"
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
    print("✓ plugin registry OK")
