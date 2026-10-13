"""
Self-check for the settings window's layout, run with a real GTK main loop:

    PYTHONPATH=src python tests/test_settings_layout.py

What would break silently without it: the window is built by classmethods that
stash widgets on the class and by handlers that fetch them back later. A row
moved from one card to another leaves the class attribute unset and nothing
complains until the user clicks the thing — often in a code path (a preset, a
greyed-out group) they reach once a month.

It also pins the two numbers a dense preference window keeps losing: every page
must fit inside the window's width, and every label column must line up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from loquivox.ui.settings_dialog import SettingsDialog  # noqa: E402

#: what _create_dialog asks for; nothing may make the window wider than this
DEFAULT_WIDTH = 640

TABS = ["Models", "Talk", "Plugins", "Companion", "Refinement", "Hotkeys",
        "Appearance & comfort", "API Keys", "Support"]


def _notebook(win: Gtk.Window) -> Gtk.Notebook:
    return win.get_child().get_children()[0]


def test_every_tab_builds():
    """Every page constructs, in the documented order."""
    SettingsDialog.show()
    win = SettingsDialog._instance
    nb = _notebook(win)
    labels = [nb.get_tab_label_text(nb.get_nth_page(i))
              for i in range(nb.get_n_pages())]
    assert labels == TABS, labels
    print(f"✓ {len(labels)} tabs build: {', '.join(labels)}")


def test_window_fits_its_default_width():
    """
    No single row may set the window's minimum width.

    This is the regression that made the old window unusable long before it
    looked cluttered: a combo sized to the longest microphone name, or a
    status label holding an unbreakable API error URL, and GTK can never draw
    the window narrower than what its children demand.
    """
    win = SettingsDialog._instance
    minimum, _natural = win.get_preferred_width()
    assert minimum <= DEFAULT_WIDTH, (
        f"minimum width {minimum}px exceeds the {DEFAULT_WIDTH}px default — "
        "something in a page is asking for more room than the window has")
    print(f"✓ minimum width {minimum}px fits the {DEFAULT_WIDTH}px window")


def test_label_columns_align():
    """Every page puts its label column at one x, across separate cards."""
    win = SettingsDialog._instance
    nb = _notebook(win)
    for i in range(nb.get_n_pages()):
        nb.set_current_page(i)
        win.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()
        widths = _field_label_widths(nb.get_nth_page(i))
        assert len(set(widths)) <= 1, (
            f"tab {TABS[i]} has ragged label columns: {sorted(set(widths))}")
    print("✓ label columns align on every tab")


def _field_label_widths(widget, found=None):
    """Widths of the labels a size group is holding together on this page."""
    found = [] if found is None else found
    if isinstance(widget, Gtk.Label) and widget.get_name() == "field-label":
        found.append(widget.get_allocated_width())
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _field_label_widths(child, found)
    return found


def test_one_apply_button_outside_the_scroll():
    """
    One Apply, in the action bar — not one per card, hidden below a scroll.

    The button lives outside the notebook, so no page's ScrolledWindow can
    take it off screen, and it runs every tab's writer rather than the one the
    user happens to be looking at.
    """
    win = SettingsDialog._instance
    root = win.get_child()
    applies = [w for w in _buttons(root) if w.get_label() == "Apply"]
    assert len(applies) == 1, f"{len(applies)} Apply buttons, expected 1"
    assert not _under(applies[0], _notebook(win)), (
        "the Apply button is inside a scrollable page — it will scroll away")
    assert len(SettingsDialog._appliers) >= 6, (
        f"only {len(SettingsDialog._appliers)} writers registered — a group "
        "that lost its button must still register what it writes")
    print(f"✓ one Apply in the action bar, driving "
          f"{len(SettingsDialog._appliers)} writers")


def test_apply_runs_every_tab():
    """
    One click writes every section, and a section that throws doesn't stop it.

    The writers are stubbed: what is under test is the wiring, and the real
    ones would rewrite the config.toml of whoever ran the check.
    """
    win = SettingsDialog._instance
    apply_btn = next(w for w in _buttons(win.get_child()) if w.get_label() == "Apply")
    real, ran = SettingsDialog._appliers, []
    try:
        SettingsDialog._appliers = [(lambda _b, i=i: ran.append(i), status)
                                    for i, (_h, status) in enumerate(real)]
        apply_btn.clicked()
        assert ran == list(range(len(real))), f"only {ran} of {len(real)} ran"

        def boom(_b):
            raise RuntimeError("this section is broken")

        SettingsDialog._appliers = [(boom, real[0][1]),
                                    (lambda _b: ran.append("after"), real[1][1])]
        apply_btn.clicked()
        assert "after" in ran, "a throwing writer stopped the ones after it"
        assert "1" in SettingsDialog._apply_status.get_text(), (
            "the action bar didn't count the section that failed")
    finally:
        SettingsDialog._appliers = real
    print(f"✓ one click runs all {len(real)} writers, failures counted not fatal")


def _buttons(widget, found=None):
    found = [] if found is None else found
    if isinstance(widget, Gtk.Button):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _buttons(child, found)
    return found


def _under(widget, ancestor) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.get_parent()
    return False


def test_handlers_find_their_widgets():
    """
    Every deferred handler still resolves the widgets it stashed at build time.

    These are the ones that run long after the dialog opened — a preset button,
    a checkbox greying out its group — so a widget left behind by a moved row
    shows up here rather than as an AttributeError under the user's mouse.
    """
    cls = SettingsDialog
    cls._refresh_engine_widgets()          # presets put every combo back in step
    cls._on_talk_engine_changed(cls._engine_combo)
    cls._on_talk_shot_toggled(cls._talk_shot_check)
    cls._update_pp_sensitivity()
    cls._sync_model_entry()
    cls._set_capture_enabled(True)
    print("✓ deferred handlers resolve their widgets")


def test_greying_a_control_greys_its_label():
    """A live label beside a dead control is the tell of a hand-built row."""
    cls = SettingsDialog
    cls._talk_shot_check.set_active(False)
    cls._on_talk_shot_toggled(cls._talk_shot_check)
    region = cls._talk_shot_region
    label = _label_before(region)
    assert label is not None, "the Capture row lost its label"
    assert not region.get_sensitive() and not label.get_sensitive(), (
        "the label stayed sensitive while its control was greyed out")
    print("✓ a greyed control takes its label with it")


def _label_before(widget: Gtk.Widget):
    """The label packed just before ``widget`` in its row."""
    siblings = widget.get_parent().get_children()
    before = siblings[:siblings.index(widget)]
    return next((w for w in reversed(before) if isinstance(w, Gtk.Label)), None)


def test_wheel_over_a_control_scrolls_the_page():
    """
    The wheel scrolls the page, never the control under the pointer.

    Combos, sliders and spin buttons eat scroll events by default, so crossing
    one mid-scroll used to change a setting silently — the reason the window
    could only be scrolled along its edge.
    """
    win = SettingsDialog._instance
    nb = _notebook(win)
    checked = 0
    for i in range(nb.get_n_pages()):
        page = nb.get_nth_page(i)          # the ScrolledWindow itself
        reached = []
        page.connect("scroll-event", lambda *a: reached.append(True))
        controls = _scroll_hungry(page)
        for w in controls:
            before = _value_of(w)
            assert w.emit("scroll-event", _wheel(win)), (
                f"{type(w).__name__} on tab {TABS[i]} still handles the wheel")
            assert _value_of(w) == before, (
                f"{type(w).__name__} on tab {TABS[i]} changed value on scroll")
            checked += 1
        assert reached or not controls, (
            f"tab {TABS[i]}: the wheel never reached the page")
    print(f"\u2713 {checked} controls pass the wheel through to the page")


def _wheel(win: Gtk.Window) -> Gdk.Event:
    event = Gdk.Event.new(Gdk.EventType.SCROLL)
    event.direction = Gdk.ScrollDirection.DOWN
    event.window = win.get_window()
    event.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
    return event


def _value_of(widget: Gtk.Widget):
    if isinstance(widget, Gtk.ComboBox):
        return widget.get_active()
    return widget.get_value()


def _scroll_hungry(widget, found=None):
    """Every control that would otherwise consume a scroll event."""
    found = [] if found is None else found
    if isinstance(widget, (Gtk.ComboBox, Gtk.Range, Gtk.SpinButton)):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _scroll_hungry(child, found)
    return found


def test_companion_settings_apply_without_editing_toml():
    """Apply writes [companion] and CFG follows; the example documents each key."""
    import tempfile
    from loquivox import config as config_module, config_io

    cls = SettingsDialog
    real = config_module.CONFIG_FILE
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "config.toml"
        config_module.CONFIG_FILE = config_io.CONFIG_FILE = tmp
        try:
            cls._comp_enabled.set_active(False)
            cls._comp_wake.set_active_id("alexa")
            cls._comp_click.set_active_id("active")
            from loquivox.handlers import companion
            import threading, time
            seen, real_set = [], companion.set_wake_word
            companion.set_wake_word = seen.append

            def settle():  # the reload runs on a thread
                time.sleep(0.2)
                for t in threading.enumerate():
                    if t is not threading.main_thread():
                        t.join(1)

            try:
                cls._on_apply_companion(None)
                settle()
                cls._on_apply_companion(None)  # same word: no reload
                settle()
                assert seen == ["alexa"], seen
                cls._wake_installed = True  # openwakeword just installed
                cls._on_apply_companion(None)
                settle()
                assert seen == ["alexa", "alexa"], seen
                assert not cls._wake_installed
            finally:
                companion.set_wake_word = real_set
            cfg = config_module.CFG
            assert (cfg.COMPANION_ENABLED, cfg.COMPANION_WAKE_WORD,
                    cfg.COMPANION_CLICK_MODE) == (False, "alexa", "active"), cfg
        finally:
            config_module.CONFIG_FILE = config_io.CONFIG_FILE = real
            config_module.reload_config()
    example = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text()
    for key in ("enabled", "wake_word", "click_mode"):
        assert f"\n{key} = " in example, key
    print("✓ companion settings apply live and are documented")


def test_conversation_settings_say_where_they_apply():
    """Each Conversation setting names its mode, and the cascade-only one greys elsewhere."""
    cls = SettingsDialog
    assert "F6" in _label_before(cls._talk_depth).get_text()
    assert "cascade" in cls._talk_semantic_check.get_label().lower()
    for engine, on in (("realtime", False), ("live", False), ("cascade", True)):
        cls._engine_combo.set_active_id(engine)
        cls._on_talk_engine_changed(cls._engine_combo)
        assert cls._talk_semantic_check.get_sensitive() is on, engine
        assert cls._talk_depth.get_sensitive(), "depth reads for F6 on every engine"
    print("✓ Conversation settings name their mode; cascade-only one greys elsewhere")


if __name__ == "__main__":
    test_every_tab_builds()
    test_window_fits_its_default_width()
    test_label_columns_align()
    test_one_apply_button_outside_the_scroll()
    test_apply_runs_every_tab()
    test_handlers_find_their_widgets()
    test_greying_a_control_greys_its_label()
    test_wheel_over_a_control_scrolls_the_page()
    test_companion_settings_apply_without_editing_toml()
    test_conversation_settings_say_where_they_apply()
    SettingsDialog._instance.destroy()
    print("\nAll settings-layout checks passed.")
