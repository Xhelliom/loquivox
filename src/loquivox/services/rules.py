"""
Event rules: "when Loquivox does X, call this MCP tool" — all in config.toml.

Nothing here names an integration, a server or an entity: a rule is data,

    [[rules]]
    when = "companion.state"                  # an event of services/events.py
    if   = { state = "speaking" }             # equality on the event's data
    do   = [ { server = "s", tool = "t", args = { x = "{state}" } } ]

and ``{key}`` in an argument is replaced by the event's value (a string that
is only that placeholder keeps the value's type). ``do`` may be one table or
a list: the actions run in order. They go straight to the MCP client
(``mcp_tools.call_tool``) — no model, and no spoken yes, since the user wrote
them. "Back to how it was" is configuration, not code: one rule saves, another
restores.

**Never on the voice's path.** ``on_event`` only matches and queues; one worker
runs every rule, in the order their events arrived (a restore never overtakes
the slow call it follows). Each rule has a one-place slot: while an action
runs, newer events replace the waiting one (a burst is at most one call in
flight plus the last event), and two runs of a rule are ``MIN_GAP`` apart. A server that is absent
or an action that fails is one warning per distinct error, then silence; the
last outcome is kept for Settings → Tools → Rules.

Written to be written by an agent: ``loquivox --rules-catalog`` lists the
events and every declared server's tools with their schemas, and
``loquivox --rules-check`` validates each rule against them (both run without
GTK, see ``cli``).
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import loquivox.config as config_module
from loquivox.i18n import _
from loquivox.services import events
from loquivox.services.plugins import Plugin, register

#: seconds between the end of one run of a rule and the start of the next
MIN_GAP = 0.2

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


# ------------------------------------------------------------------ the rules
def normalize(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """A ``[[rules]]`` table as ``{name, when, if, do: [action…], enabled}``."""
    do = raw.get("do") or []
    return {
        "name": str(raw.get("name") or f"{raw.get('when', '?')}#{index + 1}"),
        "when": str(raw.get("when", "")),
        "if": dict(raw.get("if") or {}),
        "do": [dict(a) for a in ([do] if isinstance(do, dict) else do) if isinstance(a, dict)],
        "enabled": bool(raw.get("enabled", True)),
    }


def rules() -> List[Dict[str, Any]]:
    return [normalize(r, i) for i, r in enumerate(config_module.CFG.RULES)]


def substitute(value: Any, data: Dict[str, Any]) -> Any:
    """``{key}`` → the event's value, through dicts and lists."""
    if isinstance(value, dict):
        return {k: substitute(v, data) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, data) for v in value]
    if not isinstance(value, str):
        return value
    whole = _PLACEHOLDER.fullmatch(value)
    if whole and whole.group(1) in data:
        return data[whole.group(1)]
    return _PLACEHOLDER.sub(lambda m: str(data.get(m.group(1), m.group(0))), value)


# --------------------------------------------------------------------- engine
#: rule name → (arrival number, rule, data): the latest event waiting for each rule
_pending: Dict[str, tuple] = {}
#: rule name → monotonic end of its last run
_ended: Dict[str, float] = {}
_cond = threading.Condition()
_seq = 0
_worker: Optional[threading.Thread] = None
#: rule name → (ok, time.time(), detail): the last outcome, for Settings
_last: Dict[str, tuple] = {}
_warned: set = set()


def _invoke(server: str, tool: str, args: Dict[str, Any]) -> str:
    from loquivox.services import mcp_tools
    return mcp_tools.call_tool(server, tool, args)


def _run(rule: Dict[str, Any], data: Dict[str, Any]) -> None:
    detail = ""
    try:
        for action in rule["do"]:
            detail = _invoke(str(action.get("server", "")), str(action.get("tool", "")),
                             substitute(dict(action.get("args") or {}), data))
        _last[rule["name"]] = (True, time.time(), detail[:200])
    except Exception as e:
        why = f"{type(e).__name__}: {e}".strip(": ")
        _last[rule["name"]] = (False, time.time(), why)
        if (rule["name"], why) not in _warned:     # once, not per event
            _warned.add((rule["name"], why))
            print(f"⚠️  Rule {rule['name']}: {why}")


def _drain() -> None:
    """The one worker: jobs leave in the order their events arrived, whatever the rule."""
    while True:
        with _cond:
            while not _pending:
                _cond.wait()
            name = min(_pending, key=lambda n: _pending[n][0])
            gap = MIN_GAP - (time.monotonic() - _ended.get(name, float("-inf")))
            if gap > 0:
                _cond.wait(gap)     # events arriving now replace the waiting one
                continue
            _name, rule, data = _pending.pop(name)
        _run(rule, data)
        with _cond:
            _ended[name] = time.monotonic()


def on_event(name: str, data: Dict[str, Any]) -> None:
    """Subscriber: match, queue, return. Called on the emitter's thread."""
    global _seq, _worker
    for rule in rules():
        if (not rule["enabled"] or rule["when"] != name
                or any(data.get(k) != v for k, v in rule["if"].items())):
            continue
        with _cond:
            _seq += 1
            _pending[rule["name"]] = (_seq, rule, dict(data))
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_drain, daemon=True, name="rules")
                _worker.start()
            _cond.notify()


def start() -> None:
    """Subscribe to the bus (the servers are brought up by the app, not here)."""
    events.subscribe(on_event)


# ------------------------------------------------------------ check & catalog
def _schema_errors(schema: Dict[str, Any], args: Dict[str, Any]) -> List[str]:
    props = schema.get("properties") or {}
    errors = [f"missing argument {k!r}" for k in schema.get("required", []) if k not in args]
    for k, v in args.items():
        if k not in props:
            if schema.get("additionalProperties") is not True and props:
                errors.append(f"unknown argument {k!r} (known: {', '.join(props)})")
            continue
        want = _TYPES.get(props[k].get("type"))
        placeholder = isinstance(v, str) and _PLACEHOLDER.search(v)
        if want and not placeholder and (not isinstance(v, want) or (
                isinstance(v, bool) and props[k].get("type") in ("integer", "number"))):
            errors.append(f"argument {k!r} should be {props[k]['type']}, got {json.dumps(v)}")
    return errors


def _placeholders(value: Any) -> set:
    if isinstance(value, dict):
        return set().union(*map(_placeholders, value.values()), set())
    if isinstance(value, list):
        return set().union(*map(_placeholders, value), set())
    return set(_PLACEHOLDER.findall(value)) if isinstance(value, str) else set()


def check(raw_rules: List[Dict[str, Any]], tools_of) -> Dict[str, List[str]]:
    """
    Rule name → its problems (empty: fine). ``tools_of(server)`` returns the
    server's tools, or None when it is undeclared or not reachable.
    """
    servers = config_module.CFG.MCP_SERVERS
    report: Dict[str, List[str]] = {}
    for i, raw in enumerate(raw_rules):
        rule = normalize(raw, i)
        errs = report.setdefault(rule["name"], [])
        fields = events.EVENTS.get(rule["when"])
        if fields is None:
            errs.append(f"unknown event {rule['when']!r} (known: {', '.join(events.EVENTS)})")
        else:
            errs += [f"`if` on {k!r}: the event has no such data (it has: {', '.join(fields) or 'none'})"
                     for k in rule["if"] if k not in fields]
        if not rule["do"]:
            errs.append("no action in `do`")
        for n, action in enumerate(rule["do"], 1):
            at = f"action {n}"
            server, tool = action.get("server"), action.get("tool")
            if not server or not tool:
                errs.append(f"{at}: `server` and `tool` are both needed")
                continue
            if server not in servers:
                errs.append(f"{at}: server {server!r} is not declared in [mcp.servers] "
                            f"(declared: {', '.join(servers) or 'none'})")
                continue
            if not servers[server].get("enabled", True):
                errs.append(f"{at}: server {server!r} is switched off")
                continue
            if fields is not None:
                errs += [f"{at}: {{{k}}} is not data of {rule['when']}"
                         for k in sorted(_placeholders(action.get("args"))) if k not in fields]
            tools = tools_of(server)
            if tools is None:
                errs.append(f"{at}: server {server!r} is not reachable")
                continue
            found = next((t for t in tools if t.name == tool), None)
            if found is None:
                errs.append(f"{at}: server {server!r} has no tool {tool!r} "
                            f"(it has: {', '.join(t.name for t in tools)})")
                continue
            errs += [f"{at}: {e}" for e in _schema_errors(
                found.input_schema or {}, dict(action.get("args") or {}))]
    return report


def _connected():
    """Bring the declared servers up (as the app would) and return ``tools_of``."""
    from loquivox.services import mcp_tools
    wait = max([mcp_tools.connect_timeout(c) for c in config_module.CFG.MCP_SERVERS.values()] or [0])
    mcp_tools.sync(timeout=wait + 1)
    return mcp_tools, mcp_tools.describe


def cli(argv: List[str]) -> int:
    """``--rules-check`` / ``--rules-catalog``. No GTK: only config, events and MCP."""
    if "--rules-catalog" in argv:
        print("EVENTS (use as `when`; the data can be tested in `if` and used as {key})")
        for name, fields in events.EVENTS.items():
            print(f"  {name}")
            for k, what in fields.items():
                print(f"      {k}: {what}")
        mcp_tools, tools_of = _connected()
        print("\nMCP SERVERS (use as `server` / `tool` / `args`)")
        if not config_module.CFG.MCP_SERVERS:
            print("  none declared — add [mcp.servers.<name>] to config.toml")
        for row in mcp_tools.status():
            print(f"  {row['name']}: {row['state']}" + (f" — {row['error']}" if row["error"] else ""))
            for t in tools_of(row["name"]) or []:
                print(f"      {t.name}: {(t.description or '').strip().splitlines()[0][:100] if t.description else ''}")
                print(f"          args: {json.dumps(t.input_schema or {}, ensure_ascii=False)}")
        mcp_tools.stop()
        return 0
    raw = config_module.CFG.RULES
    if not raw:
        print(f"No [[rules]] in {config_module.CONFIG_FILE}")
        return 0
    mcp_tools, tools_of = _connected()
    report = check(raw, tools_of)
    mcp_tools.stop()
    for name, errs in report.items():
        print(f"{'❌' if errs else '✓'} {name}")
        for e in errs:
            print(f"    {e}")
    bad = sum(bool(e) for e in report.values())
    print(f"\n{len(report) - bad}/{len(report)} rule(s) valid")
    return 1 if bad else 0


# ------------------------------------------------------------------- Settings
def _settings(dialog, vbox, labels) -> None:
    """The « Rules » card: each rule and how its last run went (edited in config.toml)."""
    from gi.repository import Gtk, Pango

    body = dialog._group(
        vbox, _("Rules"),
        _("Event → MCP action rules, written under [[rules]] in config.toml "
        "(see config.example.toml; `loquivox --rules-check` validates them)."))
    rows = rules()
    if not rows:
        body.pack_start(Gtk.Label(label=_("No rule declared."), xalign=0), False, False, 0)
    for rule in rows:
        did = _last.get(rule["name"])
        outcome = (_("not run yet") if did is None else
                   f"{_('✓ ok') if did[0] else '❌ ' + did[2]} · {time.strftime('%H:%M:%S', time.localtime(did[1]))}")
        label = Gtk.Label(label=f"{'' if rule['enabled'] else _('○ off · ')}{outcome}",
                          xalign=0, wrap=True, selectable=True,
                          max_width_chars=40, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.set_tooltip_text(" → ".join(f"{a.get('server')}.{a.get('tool')}" for a in rule["do"]))
        dialog._field(body, f"{rule['when']}" + (f" {rule['if']}" if rule["if"] else ""), label, labels)

    def _open(_btn) -> None:
        """Hand config.toml to the desktop's editor, creating it empty if need be."""
        path = config_module.CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        subprocess.Popen(["xdg-open", str(path)])

    button = Gtk.Button(label=_("Open config.toml"))
    button.set_halign(Gtk.Align.START)
    button.connect("clicked", _open)
    body.pack_start(button, False, False, 0)


register(Plugin(name="rules", enabled=lambda: False, settings=_settings,
                tab="tools/rules"))
