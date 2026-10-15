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

import _hermetic  # noqa: F401  (must precede any loquivox import)

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

TABS = ["Dictation", "Conversation", "Companion", "Tools", "Models & keys",
        "Appearance", "Hotkeys", "Support"]


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
    cls._update_media_sensitivity()
    cls._update_talk_sensitivity()
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
    from loquivox import config as config_module
    ai_key = config_module.CFG.HOTKEY_DEFS['ai'][0]
    talk_key = config_module.CFG.HOTKEY_DEFS['talk'][0]
    assert talk_key in _label_before(cls._talk_depth).get_text()
    assert "cascade" in cls._talk_semantic_check.get_label().lower()
    tip = cls._talk_semantic_check.get_tooltip_text()
    assert f"Cascade engine, {ai_key} and {talk_key}" in tip
    for engine, on in (("realtime", False), ("live", False), ("cascade", True)):
        cls._engine_combo.set_active_id(engine)
        cls._on_talk_engine_changed(cls._engine_combo)
        assert cls._talk_semantic_check.get_sensitive() is on, engine
        assert cls._talk_depth.get_sensitive(), "depth reads for F6 on every engine"
    # Right after construction, before anyone touches the combo.
    import dataclasses
    from loquivox import config as config_module
    real_cfg = config_module.CFG
    try:
        for engine, on in (("realtime", False), ("cascade", True)):
            config_module.CFG = dataclasses.replace(real_cfg, TALK_ENGINE=engine)
            win = cls._create_dialog()
            assert cls._talk_semantic_check.get_sensitive() is on, engine
            win.destroy()
    finally:
        config_module.CFG = real_cfg
        cls.show()  # widgets on the class point at the dialog again

    # The install thread's completion must not wake the box outside the cascade.
    import threading
    from gi.repository import GLib
    real = (cls._semantic_needs_onnx, cls._pip_install, Gtk.MessageDialog.run)
    cls._semantic_needs_onnx = classmethod(lambda c: True).__get__(None, cls)
    cls._pip_install = classmethod(lambda c, pkg: (True, "")).__get__(None, cls)
    Gtk.MessageDialog.run = lambda self: Gtk.ResponseType.OK
    try:
        cls._engine_combo.set_active_id("live")
        cls._talk_semantic_check.set_active(False)
        cls._talk_semantic_check.set_active(True)  # runs the install -> done()
        assert not cls._talk_semantic_check.get_sensitive()  # greyed while installing
        for _ in range(200):
            while Gtk.events_pending():
                Gtk.main_iteration()
            if cls._talk_semantic_check.get_label().endswith("(ready)"):
                break
            threading.Event().wait(0.01)
        assert cls._talk_semantic_check.get_label().endswith("(ready)"), "done() never ran"
        assert not cls._talk_semantic_check.get_sensitive(), "woken outside the cascade"
    finally:
        cls._semantic_needs_onnx, cls._pip_install, Gtk.MessageDialog.run = real
    print("✓ Conversation settings name their mode; cascade-only one greys elsewhere")


def _tab(name: str) -> Gtk.Widget:
    nb = _notebook(SettingsDialog._instance)
    return nb.get_nth_page(TABS.index(name))


def _widgets(widget, found=None):
    found = [] if found is None else found
    found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _widgets(child, found)
    return found


def _on_tab(widget: Gtk.Widget, name: str) -> bool:
    return _under(widget, _tab(name))


def _control(tab: str, label: str) -> Gtk.Widget:
    """The control in the row whose field label reads ``label`` on ``tab``."""
    for w in _widgets(_tab(tab)):
        if (isinstance(w, Gtk.Label) and w.get_name() == "field-label"
                and w.get_text() == label):
            siblings = w.get_parent().get_children()
            return siblings[siblings.index(w) + 1]
    raise AssertionError(f"no field {label!r} on tab {tab}")


def test_settings_live_where_they_are_used():
    """Tray-only switches, the companion's sounds and its memory each have their tab."""
    cls = SettingsDialog
    for attr, tab in (("_toggle_mode_check", "Dictation"),
                      ("_chat_enabled_check", "Appearance"),
                      ("_tts_enabled_check", "Models & keys"),
                      ("_engine_combo", "Conversation"),
                      ("_talk_semantic_check", "Conversation"),
                      ("_media_combo", "Companion"),
                      ("_media_duck_scale", "Companion"),
                      ("_comp_duck_ai", "Companion"),
                      ("_backend_combo", "Dictation"),
                      ("_pp_scale", "Dictation"),
                      ("_voice_combo", "Models & keys")):
        assert _on_tab(getattr(cls, attr), tab), f"{attr} is not on {tab}"
    assert _on_tab(_control("Models & keys", "Provider"), "Models & keys")
    assert not any(_on_tab(w, "Models & keys") for w in (cls._engine_combo,))
    titles = lambda tab: " ".join(w.get_label() for w in _widgets(_tab(tab))
                                  if isinstance(w, Gtk.Label))
    assert "no longer repeats" in titles("Companion"), "memory card is not on Companion"
    assert "no longer repeats" not in titles("Tools")
    assert "Writing" in titles("Tools") and "Search" in titles("Tools")
    print("✓ every setting sits on the tab of the thing it changes")


def test_tray_only_settings_are_set_from_the_dialog():
    """toggle_mode (on Apply), chat_enabled and TTS on/off (at once) are set from the dialog."""
    from loquivox.state import STATE
    from loquivox.managers.chat import ChatManager
    cls = SettingsDialog
    saved = (STATE.toggle_mode, STATE.chat_enabled, STATE.tts_enabled)
    real = (ChatManager._destroy, ChatManager.refresh_overlay)
    ChatManager._destroy = ChatManager.refresh_overlay = staticmethod(lambda *a: None)
    try:
        cls._toggle_mode_check.set_active(not saved[0])
        assert STATE.toggle_mode == saved[0], "press-to-record must wait for Apply"
        cls._on_apply_recording(None)
        cls._chat_enabled_check.set_active(not saved[1])
        cls._tts_enabled_check.set_active(not saved[2])
        assert (STATE.toggle_mode, STATE.chat_enabled, STATE.tts_enabled) == tuple(
            not v for v in saved)
    finally:
        STATE.toggle_mode, STATE.chat_enabled, STATE.tts_enabled = saved
        ChatManager._destroy, ChatManager.refresh_overlay = real
    print("✓ press-to-record, show chat and TTS on/off are in the dialog")


def test_dependent_fields_grey_out():
    """Each field that means nothing without another goes dead with it."""
    cls = SettingsDialog
    from loquivox.config import POSTPROCESS_CUSTOM_LEVEL

    def on(*ws):
        return [w.get_sensitive() for w in ws]

    duck = (cls._media_duck_scale, cls._comp_duck_ai, cls._comp_duck_user)
    for media, want in (("off", False), ("resume", False), ("duck", True)):
        cls._media_combo.set_active_id(media)
        assert on(*duck) == [want] * 3, (media, on(*duck))

    for region, want in (("window", False), ("screen", False), ("cursor", True)):
        cls._talk_shot_check.set_active(True)
        cls._talk_shot_region.set_active_id(region)
        assert cls._talk_shot_cursor.get_sensitive() is want, region
    cls._talk_shot_check.set_active(False)
    assert not cls._talk_shot_cursor.get_sensitive()
    assert not cls._talk_shot_region.get_sensitive()

    cls._pp_translate_check.set_active(False)
    for level, want in ((0, False), (4, False), (POSTPROCESS_CUSTOM_LEVEL, True)):
        cls._pp_scale.set_value(level)
        assert cls._pp_prompt_label.get_sensitive() is want, level
    cls._pp_translate_check.set_active(True)
    assert not cls._pp_prompt_label.get_sensitive(), "translate overrides Custom"
    cls._pp_translate_check.set_active(False)

    for guard, want in (("off", False), ("headset", False), ("speakers", True)):
        cls._talk_echo_guard.set_active_id(guard)
        assert cls._talk_barge_margin.get_sensitive() is want, guard

    check = _control("Tools", "Research")
    provider, model = _control("Tools", "Provider"), _control("Tools", "Model")
    for active in (False, True):
        check.set_active(active)
        assert on(provider, model) == [active] * 2, active
    print("✓ ducking, box width, custom prompt, cutting in and search follow their switch")


def test_no_stale_wording():
    """Rewrite and vision modes are gone, and "hold" alone no longer describes recording."""
    import re
    bad = re.compile(r"rewrite|, vision\b|\bhold\b|\bheld down\b|Apparence|onglet", re.I)
    seen = []
    for w in _widgets(SettingsDialog._instance.get_child()):
        texts = [w.get_tooltip_text() or "", w.get_tooltip_markup() or ""]
        if isinstance(w, (Gtk.Label, Gtk.CheckButton, Gtk.Button)):
            texts.append(w.get_label() or "")
        seen += [t for t in texts if bad.search(t)]
    assert not seen, seen
    for fr in ("Baisser", "Ce que le compagnon", "Supprimer", "Ajouter"):
        assert not any(isinstance(w, (Gtk.Label, Gtk.CheckButton)) and fr in (w.get_label() or "")
                       for w in _widgets(SettingsDialog._instance.get_child())), fr
    print("✓ no obsolete or foreign-language text left in the dialog")


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
    test_settings_live_where_they_are_used()
    test_tray_only_settings_are_set_from_the_dialog()
    test_dependent_fields_grey_out()
    test_no_stale_wording()
    SettingsDialog._instance.destroy()
    print("\nAll settings-layout checks passed.")
