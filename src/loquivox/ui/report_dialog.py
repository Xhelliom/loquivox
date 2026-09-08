"""
The diagnostic report, shown before it is shared.

A window with the redacted report in a text view, a checkbox to keep what
was said, and two ways out: copy, or save to a file. Showing the text rather
than writing a file straight away is the point — the user reads what leaves
the machine, sees that the key is gone and the transcripts are counted rather
than quoted, and only then decides.

``build_report()`` shells out (``journalctl``) and probes the sound card, so it
runs on a worker thread and the text view fills when it returns; the window
appears at once with a placeholder, the way every other overlay does.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from loquivox.diagnostics import (build_report, default_log_export_path,
                                  default_report_path, export_log)


def save_text_as(parent: Optional[Gtk.Window], default: Path, text: str,
                 extra: Optional[Gtk.Widget] = None,
                 produce=None) -> Optional[str]:
    """
    One Save… dialog for both windows. Returns the path written, or None.

    ``produce`` (called after OK, with ``extra`` still alive) lets the caller
    build the text from a checkbox in the chooser — the log export decides
    whether to keep spoken content at the moment of saving, not before.
    """
    chooser = Gtk.FileChooserDialog(title="Save for sharing", parent=parent,
                                    action=Gtk.FileChooserAction.SAVE)
    chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
    chooser.set_do_overwrite_confirmation(True)
    chooser.set_current_folder(str(default.parent))
    chooser.set_current_name(default.name)
    if extra is not None:
        extra.show_all()
        chooser.set_extra_widget(extra)
    try:
        if chooser.run() != Gtk.ResponseType.OK:
            return None
        target = chooser.get_filename()
        body = produce() if produce is not None else text
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
        return target
    finally:
        chooser.destroy()


def keep_content_check() -> Gtk.CheckButton:
    chk = Gtk.CheckButton(
        label="Include what was said (transcripts, screen descriptions)")
    chk.set_tooltip_text("Keys, addresses and names are removed either way.")
    return chk


class ReportDialog:
    """Singleton window: opening it twice raises the one that is open."""

    _instance: Optional[Gtk.Window] = None
    _view: Optional[Gtk.TextView] = None
    _status: Optional[Gtk.Label] = None
    _keep: Optional[Gtk.CheckButton] = None
    _generation = 0

    @classmethod
    def show(cls) -> None:
        if cls._instance is not None:
            cls._instance.present()
            return
        cls._instance = cls._build()
        cls._instance.show_all()
        cls._regenerate()

    @classmethod
    def _build(cls) -> Gtk.Window:
        win = Gtk.Window(title="Loquivox — Diagnostic report")
        win.set_default_size(720, 560)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_keep_above(True)
        win.connect("destroy", cls._on_destroy)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(12)

        intro = Gtk.Label()
        intro.set_halign(Gtk.Align.START)
        intro.set_xalign(0)
        intro.set_line_wrap(True)
        intro.set_markup(
            "What a bug report needs: version, system, tools, config and the "
            "recent log. <b>API keys, your user name, home directory, e-mail "
            "and IP addresses are removed.</b> What you said is replaced by "
            "its length unless you tick the box. Read it over, then copy it "
            "into a GitHub issue or save it to a file.")
        root.pack_start(intro, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        cls._view = Gtk.TextView()
        cls._view.set_editable(False)
        cls._view.set_monospace(True)
        cls._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for m in ("top", "bottom", "left", "right"):
            getattr(cls._view, f"set_{m}_margin")(8)
        scroll.add(cls._view)
        root.pack_start(scroll, True, True, 0)

        cls._keep = Gtk.CheckButton(
            label="Include what was said (transcripts, screen descriptions)")
        cls._keep.set_tooltip_text(
            "Off: a dropped transcript shows as '[… 42 chars]'. On: the "
            "sentence itself — useful when the bug is about what was heard.")
        cls._keep.connect("toggled", lambda _w: cls._regenerate())
        root.pack_start(cls._keep, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._status = Gtk.Label()
        cls._status.set_halign(Gtk.Align.START)
        cls._status.set_xalign(0)
        cls._status.set_ellipsize(Pango.EllipsizeMode.END)
        actions.pack_start(cls._status, True, True, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _w: win.destroy())
        save = Gtk.Button(label="Save…")
        save.connect("clicked", cls._on_save)
        copy = Gtk.Button(label="Copy")
        copy.get_style_context().add_class("suggested-action")
        copy.connect("clicked", cls._on_copy)
        actions.pack_end(copy, False, False, 0)
        actions.pack_end(save, False, False, 0)
        actions.pack_end(close, False, False, 0)
        root.pack_start(actions, False, False, 0)

        win.add(root)
        return win

    # -----------------------------------------------------------------
    @classmethod
    def _regenerate(cls) -> None:
        """Rebuild the text off the main thread; a stale result is dropped."""
        if cls._view is None or cls._keep is None:
            return
        cls._generation += 1
        generation = cls._generation
        keep = cls._keep.get_active()
        cls._set_text("Collecting… (a few seconds: the journal and the sound card are asked)")
        cls._say("Collecting…")

        def work() -> None:
            try:
                text = build_report(keep_content=keep)
            except Exception as e:      # the window must still say something
                text = f"Could not build the report: {e}"
            GLib.idle_add(cls._deliver, generation, text)

        threading.Thread(target=work, daemon=True, name="loquivox-report").start()

    @classmethod
    def _deliver(cls, generation: int, text: str) -> bool:
        if generation == cls._generation and cls._view is not None:
            cls._set_text(text)
            cls._say(f"{len(text.splitlines())} lines · "
                     f"{'with' if cls._keep and cls._keep.get_active() else 'without'} spoken content")
        return False

    @classmethod
    def _set_text(cls, text: str) -> None:
        if cls._view is not None:
            cls._view.get_buffer().set_text(text)

    @classmethod
    def _text(cls) -> str:
        if cls._view is None:
            return ""
        buf = cls._view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    @classmethod
    def _say(cls, msg: str) -> None:
        if cls._status is not None:
            cls._status.set_text(msg)

    # -----------------------------------------------------------------
    @classmethod
    def _on_copy(cls, _btn: Gtk.Button) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(cls._text(), -1)
        clipboard.store()
        cls._say("📋 Copied — paste it into the issue")

    @classmethod
    def _on_save(cls, _btn: Gtk.Button) -> None:
        try:
            target = save_text_as(cls._instance, default_report_path(), cls._text())
        except OSError as e:
            cls._say(f"❌ Could not save: {e}")
            return
        if target:
            cls._say(f"💾 Saved to {target}")

    @classmethod
    def _on_destroy(cls, _win: Gtk.Window) -> None:
        cls._instance = None
        cls._view = None
        cls._status = None
        cls._keep = None


class LogDialog:
    """
    The log itself, live, for the user's own eyes.

    Unlike the report this is NOT redacted: it never leaves the machine on its
    own, and a user reading their own log wants the sentence that was dropped,
    not its length. It refreshes every two seconds while open and keeps the
    view pinned to the newest lines unless the user has scrolled up.
    """

    LINES = 500
    REFRESH_MS = 2000

    _instance: Optional[Gtk.Window] = None
    _view: Optional[Gtk.TextView] = None
    _status: Optional[Gtk.Label] = None
    _timer: int = 0
    _shown: str = ""

    @classmethod
    def show(cls) -> None:
        if cls._instance is not None:
            cls._instance.present()
            return
        cls._instance = cls._build()
        cls._instance.show_all()
        cls._refresh()
        cls._timer = GLib.timeout_add(cls.REFRESH_MS, cls._tick)

    @classmethod
    def _build(cls) -> Gtk.Window:
        from loquivox.diagnostics import log_file_path

        path = log_file_path()
        win = Gtk.Window(title="Loquivox — Log")
        win.set_default_size(760, 520)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_keep_above(True)
        win.connect("destroy", cls._on_destroy)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(12)

        intro = Gtk.Label()
        intro.set_halign(Gtk.Align.START)
        intro.set_xalign(0)
        intro.set_line_wrap(True)
        intro.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        intro.set_markup(
            f"<tt>{GLib.markup_escape_text(str(path) if path else '(disabled)')}</tt> — "
            f"last {cls.LINES} lines, refreshed live. Nothing on screen is "
            "redacted; <b>Save for sharing…</b> writes the whole log with keys, "
            "names and addresses removed.")
        root.pack_start(intro, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        cls._view = Gtk.TextView()
        cls._view.set_editable(False)
        cls._view.set_monospace(True)
        cls._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for m in ("top", "bottom", "left", "right"):
            getattr(cls._view, f"set_{m}_margin")(8)
        scroll.add(cls._view)
        root.pack_start(scroll, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._status = Gtk.Label()
        cls._status.set_halign(Gtk.Align.START)
        cls._status.set_xalign(0)
        cls._status.set_ellipsize(Pango.EllipsizeMode.END)
        actions.pack_start(cls._status, True, True, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _w: win.destroy())
        report = Gtk.Button(label="Diagnostic report…")
        report.connect("clicked", lambda _w: ReportDialog.show())
        open_btn = Gtk.Button(label="Open file")
        open_btn.set_tooltip_text("Open the whole log in your text editor")
        open_btn.set_sensitive(path is not None)
        open_btn.connect("clicked", cls._on_open)
        copy = Gtk.Button(label="Copy")
        copy.connect("clicked", cls._on_copy)
        share = Gtk.Button(label="Save for sharing…")
        share.get_style_context().add_class("suggested-action")
        share.set_tooltip_text("The whole log, with keys, names and addresses "
                               "removed — the file to attach to a bug report")
        share.set_sensitive(path is not None)
        share.connect("clicked", lambda _w: cls.save_for_sharing(cls._instance, cls._say))
        actions.pack_end(share, False, False, 0)
        actions.pack_end(report, False, False, 0)
        actions.pack_end(copy, False, False, 0)
        actions.pack_end(open_btn, False, False, 0)
        actions.pack_end(close, False, False, 0)
        root.pack_start(actions, False, False, 0)

        win.add(root)
        return win

    @classmethod
    def _tail(cls) -> str:
        from loquivox.diagnostics import log_file_path

        path = log_file_path()
        if path is None:
            return "(log file disabled via LOQUIVOX_LOG_FILE)"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            return f"(no log yet at {path})"
        except OSError as e:
            return f"(unreadable: {e})"
        return "".join(lines[-cls.LINES:])

    @classmethod
    def _refresh(cls) -> None:
        if cls._view is None:
            return
        text = cls._tail()
        if text == cls._shown:
            return
        parent = cls._view.get_parent()
        adj = parent.get_vadjustment() if isinstance(parent, Gtk.ScrolledWindow) else None
        at_end = adj is None or \
            adj.get_value() + adj.get_page_size() >= adj.get_upper() - 2
        cls._shown = text
        buf = cls._view.get_buffer()
        buf.set_text(text)
        if at_end:
            # After the buffer settles, not now: the upper bound is stale.
            GLib.idle_add(cls._scroll_to_end)
        if cls._status is not None:
            cls._status.set_text(f"{len(text.splitlines())} lines")

    @classmethod
    def _scroll_to_end(cls) -> bool:
        if cls._view is not None:
            buf = cls._view.get_buffer()
            cls._view.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0.0, 1.0)
        return False

    @classmethod
    def _tick(cls) -> bool:
        if cls._instance is None:
            return False
        cls._refresh()
        return True

    @classmethod
    def _say(cls, msg: str) -> None:
        if cls._status is not None:
            cls._status.set_text(msg)

    @staticmethod
    def save_for_sharing(parent: Optional[Gtk.Window], say=print) -> None:
        """
        Write the whole log, redacted, where the user says. Also the Support
        tab's "Save log…", which is why it takes its window and its status
        line rather than the dialog's.
        """
        chk = keep_content_check()
        try:
            target = save_text_as(
                parent, default_log_export_path(), "", extra=chk,
                produce=lambda: export_log(keep_content=chk.get_active())
                or "(the log is empty)\n")
        except OSError as e:
            say(f"❌ Could not save: {e}")
            return
        if target:
            say(f"💾 Redacted log saved to {target}")

    @classmethod
    def _on_copy(cls, _btn: Gtk.Button) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(cls._shown, -1)
        clipboard.store()
        cls._say("📋 Copied (unredacted)")

    @classmethod
    def _on_open(cls, _btn: Gtk.Button) -> None:
        from loquivox.diagnostics import log_file_path

        path = log_file_path()
        if path is None:
            return
        try:
            Gtk.show_uri_on_window(cls._instance, path.as_uri(), Gdk.CURRENT_TIME)
        except Exception as e:
            cls._say(f"❌ Could not open: {e}")

    @classmethod
    def _on_destroy(cls, _win: Gtk.Window) -> None:
        if cls._timer:
            GLib.source_remove(cls._timer)
        cls._timer = 0
        cls._instance = None
        cls._view = None
        cls._status = None
        cls._shown = ""
