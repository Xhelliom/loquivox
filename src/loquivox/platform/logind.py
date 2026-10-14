"""
Lock/unlock from logind, on the system bus — shared by X11 and Wayland.

Only the ``Lock`` / ``Unlock`` signals (``loginctl lock-session``, what a shell's
lock button usually listens to) are used: paired by construction. ``LockedHint``
is deliberately ignored, edges included — on niri + Noctalia it sticks at "yes"
with the screen unlocked, so a true edge with no false one would keep the
companion silent until F7. A shell that emits neither signal is covered by the
idle fallback (``watch_idle``). Callbacks arrive on the GLib main loop.
"""
from __future__ import annotations

from typing import Callable, Optional

from loquivox.platform.base import AwayBackend

BUS = "org.freedesktop.login1"
SESSION = "org.freedesktop.login1.Session"
MANAGER = "/org/freedesktop/login1"


class LogindAway(AwayBackend):
    _on_lock: Optional[Callable[[bool], None]] = None

    def watch_lock(self, on_locked: Callable[[bool], None]) -> bool:
        try:
            from gi.repository import Gio, GLib
            bus = self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)  # keep it: a dropped ref drops the match
            # ".../session/auto" is an alias for method calls only: logind emits
            # its signals on the real path (".../session/_31"), so resolve it.
            path = bus.call_sync(
                BUS, MANAGER, BUS + ".Manager", "GetSession",
                GLib.Variant("(s)", ("auto",)), GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            bus.signal_subscribe(
                BUS, SESSION, None, path, None, Gio.DBusSignalFlags.NONE,
                lambda _c, _s, _p, _i, member, params, *_: self._signal(
                    member, params.unpack() if params is not None else None))
        except Exception as e:
            print(f"⚠️  Lock watch unavailable (logind): {e}")
            return False
        self._on_lock = on_locked
        return True

    def _signal(self, member: str, args) -> None:
        if member in ("Lock", "Unlock") and self._on_lock:
            self._on_lock(member == "Lock")
