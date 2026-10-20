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

#: tab -> its sub-pages (None: a single page), in the documented order
SUBTABS = {
    "Dictation": ["Transcription", "Refinement"],
    "Conversation": ["Engine", "Flow", "Screen"],
    "Companion": ["General", "Voice & thinking", "Presence", "Notifications",
                  "Memory"],
    "Sound": ["Microphone & music", "Echo guard", "Spoken voice"],
    "Tools": ["Search", "Writing & screen", "MCP servers", "Rules"],
    "Models & keys": ["Models by role", "Keys", "Presets"],
    "Appearance": None, "Hotkeys": None, "Support": None,
}
TABS = list(SUBTABS)

#: the height a sub-page may ask for, in windows: about a screen and a half
MAX_SCREENS = 1.5


def _leaves(win: Gtk.Window):
    """(tab, sub-page or None, ScrolledWindow) for every page that scrolls."""
    nb = _notebook(win)
    out = []
    for i, tab in enumerate(TABS):
        page = nb.get_nth_page(i)
        if SUBTABS[tab] is None:
            out.append((tab, None, page))
            continue
        for j, sub in enumerate(SUBTABS[tab]):
            out.append((tab, sub, page.get_nth_page(j)))
    return out


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
    for i, tab in enumerate(TABS):
        page = nb.get_nth_page(i)
        if SUBTABS[tab] is None:
            assert isinstance(page, Gtk.ScrolledWindow), tab
            continue
        assert isinstance(page, Gtk.Notebook) and page.get_scrollable(), (
            f"{tab} is not a scrollable nested notebook")
        assert page.get_tab_pos() == Gtk.PositionType.TOP, f"{tab}: tabs not on top"
        subs = [page.get_tab_label_text(page.get_nth_page(j))
                for j in range(page.get_n_pages())]
        assert subs == SUBTABS[tab], (tab, subs)
        assert all(isinstance(page.get_nth_page(j), Gtk.ScrolledWindow)
                   for j in range(page.get_n_pages())), f"{tab}: a sub-page without its own scroll"
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


def _settle(win: Gtk.Window) -> None:
    """Let a just-shown page get its allocation: one pass of events is not always enough."""
    import time
    win.show_all()
    for _ in range(5):
        time.sleep(0.02)
        while Gtk.events_pending():
            Gtk.main_iteration()


def test_label_columns_align():
    """Every page puts its label column at one x, across separate cards."""
    win = SettingsDialog._instance
    nb = _notebook(win)
    for i, tab in enumerate(TABS):
        nb.set_current_page(i)
        inner = nb.get_nth_page(i)
        for j in range(inner.get_n_pages() if SUBTABS[tab] else 0):
            inner.set_current_page(j)
            _settle(win)
            widths = _field_label_widths(inner.get_nth_page(j))
            assert len(set(widths)) <= 1, (
                f"{tab} › {SUBTABS[tab][j]} has ragged label columns: "
                f"{sorted(set(widths))}")
        if SUBTABS[tab] is None:
            _settle(win)
            widths = _field_label_widths(inner)
            assert len(set(widths)) <= 1, (
                f"tab {tab} has ragged label columns: {sorted(set(widths))}")
    print("✓ label columns align on every sub-page")


def test_no_subpage_needs_more_than_a_screen_and_a_half():
    """Splitting is the point: no sub-page may ask for a long scroll."""
    win = SettingsDialog._instance
    _w, height = win.get_size()
    too_tall = []
    for tab, sub, sw in _leaves(win):
        natural = sw.get_child().get_preferred_height()[1]   # the page body
        if natural > MAX_SCREENS * height:
            too_tall.append(f"{tab} › {sub or '-'}: {natural}px")
    assert not too_tall, f"over {MAX_SCREENS} screens of {height}px: {too_tall}"
    print(f"✓ every sub-page fits in {MAX_SCREENS} screens")


def test_a_plugin_can_aim_at_a_subpage():
    """Plugin.tab "tab/sub" lands the card on that sub-page; a bare tab, on its first."""
    from loquivox.services import plugins
    seen = []
    win = SettingsDialog._instance
    for tab, sub, sw in _leaves(win):
        if tab == "Companion" and sub == "Memory":
            assert "no longer repeats" in " ".join(
                w.get_label() for w in _widgets(sw) if isinstance(w, Gtk.Label))
            seen.append("memory")
    assert seen == ["memory"]
    assert {p.tab for p in plugins.all_plugins() if p.settings} >= {
        "companion/memory", "tools/search"}
    print("✓ plugin cards reach their sub-page")


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
    cls._fill_preset(*cls.PRESETS[0][2:])   # a preset fills every combo it names
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
    cls._talk_shot_region.set_active_id("window")
    cls._on_talk_shot_toggled(cls._talk_shot_check)
    region = cls._talk_shot_cursor
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
    checked = 0
    for tab, sub, page in _leaves(win):    # the ScrolledWindow itself
        where = f"{tab} › {sub}" if sub else tab
        reached = []
        page.connect("scroll-event", lambda *a: reached.append(True))
        controls = _scroll_hungry(page)
        for w in controls:
            before = _value_of(w)
            assert w.emit("scroll-event", _wheel(win)), (
                f"{type(w).__name__} on {where} still handles the wheel")
            assert _value_of(w) == before, (
                f"{type(w).__name__} on {where} changed value on scroll")
            checked += 1
        assert reached or not controls, (
            f"{where}: the wheel never reached the page")
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
    return _notebook(SettingsDialog._instance).get_nth_page(TABS.index(name))


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
                      ("_tts_enabled_check", "Sound"),
                      ("_engine_combo", "Conversation"),
                      ("_talk_semantic_check", "Conversation"),
                      ("_media_combo", "Sound"),
                      ("_media_duck_scale", "Sound"),
                      ("_comp_duck_ai", "Sound"),
                      ("_backend_combo", "Dictation"),
                      ("_pp_scale", "Dictation"),
                      ("_voice_combo", "Sound")):
        assert _on_tab(getattr(cls, attr), tab), f"{attr} is not on {tab}"
    assert _on_tab(_control("Models & keys", "Final text"), "Models & keys")
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
        assert STATE.tts_enabled == saved[2], "read-aloud must wait for Apply"
        cls._on_apply_voice(None)
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

    # F4 and the companion capture whatever the F6 box says: framing stays live.
    for shot in (True, False):
        cls._talk_shot_check.set_active(shot)
        for region, want in (("window", False), ("screen", False), ("cursor", True)):
            cls._talk_shot_region.set_active_id(region)
            assert cls._talk_shot_cursor.get_sensitive() is want, (shot, region)
        assert cls._talk_shot_region.get_sensitive() and cls._talk_shot_max.get_sensitive()

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

    print("✓ ducking, box width, custom prompt and cutting in follow their switch")


def test_live_fields_are_the_engines_and_the_companion_has_its_own():
    """F4/F6's Live fields follow the engine alone; the companion's sit on its tab."""
    cls = SettingsDialog
    for engine, on in (("cascade", False), ("live", True)):
        cls._engine_combo.set_active_id(engine)
        assert cls._live_voice.get_sensitive() is on, engine
    cls._engine_combo.set_active_id("cascade")
    # the companion's: "like the conversation" first, and its own value wins
    assert cls._comp_voice.get_active_id() == ""
    assert cls._comp_voice.get_model()[0][0] == "Like the conversation"
    cls._comp_delegation.set_active_id("client")
    assert not cls._comp_backend.get_sensitive() and not cls._comp_web.get_sensitive()
    cls._comp_delegation.set_active_id("responses")
    assert cls._comp_backend.get_sensitive() and cls._comp_web.get_sensitive()
    cls._comp_delegation.set_active_id("")
    print("✓ Live fields: the conversation's follow its engine, the companion's are its own")


def test_every_companion_setting_is_on_the_companion_tab():
    """Companion pages hold the companion's fields; none is left on another tab."""
    cls = SettingsDialog
    win = cls._instance
    where = {}
    for tab, sub, sw in _leaves(win):
        for w in _widgets(sw):
            where[id(w)] = (tab, sub)
    for name, sub in (("_comp_wake", "General"), ("_comp_click", "General"),
                      ("_comp_style", "General"), ("_comp_voice", "Voice & thinking"),
                      ("_comp_delegation", "Voice & thinking"),
                      ("_comp_backend", "Voice & thinking"),
                      ("_comp_web", "Voice & thinking"),
                      ("_comp_instructions", "Voice & thinking"),
                      ("_comp_enabled", "Presence"), ("_comp_lock", "Presence"),
                      ("_comp_idle", "Presence"), ("_comp_source_silence", "Presence")):
        assert where.get(id(getattr(cls, name))) == ("Companion", sub), name
    notifs = [w for t, s, sw in _leaves(win) if (t, s) == ("Companion", "Notifications")
              for w in _widgets(sw) if isinstance(w, Gtk.Label)]
    assert any("Wakes the companion" in w.get_label() for w in notifs)
    print("✓ the companion is set on the Companion tab")


def test_companion_live_settings_apply_and_are_documented():
    import tempfile
    from loquivox import config as config_module, config_io

    cls = SettingsDialog
    real = config_module.CONFIG_FILE
    with tempfile.TemporaryDirectory() as d:
        config_module.CONFIG_FILE = config_io.CONFIG_FILE = Path(d) / "config.toml"
        try:
            cls._comp_voice.set_active_id("cedar")
            cls._comp_delegation.set_active_id("responses")
            cls._comp_backend.set_text("m-x")
            cls._comp_web.set_active_id("native")
            cls._comp_instructions.get_buffer().set_text("Sois bref.")
            cls._comp_source_silence.set_value(4.5)
            cls._on_apply_companion(None)
            cfg = config_module.CFG
            assert (cfg.COMPANION_LIVE_VOICE, cfg.COMPANION_LIVE_DELEGATION,
                    cfg.COMPANION_LIVE_BACKEND_MODEL, cfg.COMPANION_LIVE_WEB_SEARCH,
                    cfg.COMPANION_INSTRUCTIONS, cfg.COMPANION_SOURCE_SILENCE_SEC) == (
                "cedar", "responses", "m-x", "native", "Sois bref.", 4.5), cfg
            cls._comp_voice.set_active_id("")      # back to "like the conversation"
            cls._on_apply_companion(None)
            assert config_module.CFG.COMPANION_LIVE_VOICE == ""
        finally:
            config_module.CONFIG_FILE = config_io.CONFIG_FILE = real
            config_module.reload_config()
    example = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text()
    block = example.split("\n[companion]", 1)[1].split("\n[", 1)[0]
    for key in ("live_voice", "live_delegation", "live_backend_model",
                "live_web_search", "instructions"):
        assert f"\n# {key} = " in block or f"\n{key} = " in block, key
    print("✓ companion Live settings apply and are documented")


def test_read_aloud_is_cascade_only():
    cls = SettingsDialog
    for engine, want in (("cascade", True), ("realtime", False), ("live", False)):
        cls._engine_combo.set_active_id(engine)
        assert cls._talk_speak_check.get_sensitive() is want, engine
    cls._engine_combo.set_active_id("cascade")
    print("✓ read-replies-aloud follows the cascade")


def _dialog_texts():
    return " ".join(w.get_label() or "" for w in _widgets(SettingsDialog._instance.get_child())
                    if isinstance(w, Gtk.Label))


def test_agent_model_is_a_combo():
    assert isinstance(SettingsDialog._agent_model, Gtk.ComboBoxText)
    print("✓ agent model is a combo")


def test_agent_models_are_listed_from_the_agent_provider():
    """Anthropic agent + another chat provider: each combo gets its own provider's models."""
    import types
    from gi.repository import GLib
    import loquivox.api as api
    from loquivox.state import STATE

    lists = {"groq": ["chat-a", "chat-b"], "anthropic": ["claude-x", "claude-y"]}

    def fake_client(pid):
        data = [types.SimpleNamespace(id=m) for m in lists[pid]]
        return types.SimpleNamespace(models=types.SimpleNamespace(
            list=lambda: types.SimpleNamespace(data=data)))

    def entries(combo):
        model = combo.get_model()
        return [row[0] for row in model]

    real, saved = api.get_ai_client, (STATE.ai_provider, STATE.ai_agent_model)
    api.get_ai_client = fake_client
    try:
        SettingsDialog._chat_provider.set_active_id("groq")
        SettingsDialog._agent_provider.set_active_id("anthropic")
        SettingsDialog._agent_model.get_child().set_text("my-typed")
        SettingsDialog._load_models_async()
        ctx = GLib.MainContext.default()
        for _ in range(500):
            while ctx.iteration(False):
                pass
            if entries(SettingsDialog._agent_model):
                break
            import time; time.sleep(0.01)
        assert entries(SettingsDialog._agent_model) == ["claude-x", "claude-y"]
        assert entries(SettingsDialog._chat_model) == ["chat-a", "chat-b"]
        assert SettingsDialog._agent_model.get_child().get_text() == "my-typed"
    finally:
        api.get_ai_client = real
        STATE.ai_provider, STATE.ai_agent_model = saved
        SettingsDialog._agent_provider.set_active_id("")
        SettingsDialog._agent_model.get_child().set_text("")
    print("✓ agent models come from the agent's provider")


def test_capture_group_says_f4_and_companion_always_capture():
    texts = _dialog_texts()
    assert "always capture" in texts, "Screen context prose must say F4 and the companion always capture"
    print("✓ screen-context prose is true")


def test_roles_prose_names_the_agent_and_the_chat_jobs():
    texts = _dialog_texts()
    for phrase in ("The agent answers", "the chat model writes the final text",
                   "refines your dictation", "the memory judge decides", "Same as chat"):
        assert phrase in texts.replace("\n", " "), phrase
    print("✓ Models-by-role prose is true")


def test_f10_survives_an_apply_of_the_voice_page():
    """F10 pressed while the dialog is open is not undone by the next Apply."""
    import tempfile
    from loquivox import config as config_module, config_io
    from loquivox.services.tts import TTSService
    from loquivox.state import STATE
    from loquivox.managers.chat import ChatManager

    cls = SettingsDialog
    real_file, real_refresh = config_module.CONFIG_FILE, ChatManager.refresh_overlay
    before = STATE.tts_enabled
    ChatManager.refresh_overlay = classmethod(lambda c: None)
    with tempfile.TemporaryDirectory() as d:
        config_module.CONFIG_FILE = config_io.CONFIG_FILE = Path(d) / "config.toml"
        try:
            cls._tts_enabled_check.set_active(before)
            TTSService.toggle()  # F10
            assert STATE.tts_enabled != before
            while Gtk.events_pending():  # sync_tts_switch runs as an idle on the GTK loop
                Gtk.main_iteration()
            assert cls._tts_enabled_check.get_active() == STATE.tts_enabled
            cls._talk_speak_check.set_active(not cls._talk_speak_check.get_active())
            cls._on_apply_voice(None)
            assert STATE.tts_enabled != before, "Apply undid F10"
        finally:
            STATE.tts_enabled = before
            ChatManager.refresh_overlay = real_refresh
            config_module.CONFIG_FILE = config_io.CONFIG_FILE = real_file
            config_module.reload_config()
    print("✓ F10 survives an Apply of the voice page")


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
    french = re.compile(r"[àâçéèêëîïôûùü]|\b(le|la|les|des|une?|pour|quand|jamais|avec|sans|ou|et)\b")
    found = []
    for w in _widgets(SettingsDialog._instance.get_child()):
        texts = [w.get_tooltip_text() or ""]
        if isinstance(w, (Gtk.Label, Gtk.CheckButton, Gtk.Button)):
            texts.append(w.get_label() or "")
        # the instructions placeholder is a sample in the user's own language, on purpose
        found += [t for t in texts if french.search(t) and not t.startswith("Example:")]
    assert not found, found
    print("✓ no obsolete or foreign-language text left in the dialog")


def test_long_contents_keep_the_window_narrow():
    """A 30-tool MCP server, a long rule and a long error must not widen the window."""
    from loquivox.services import mcp_tools, memory, rules

    long = "x" * 120
    saved = mcp_tools.status, rules.rules, memory.rules
    mcp_tools.status = lambda: [{
        "name": "homeassistant", "enabled": True, "state": "failed", "error": long,
        "tools": {f"ha_a_very_long_tool_name_number_{i}": bool(i % 2) for i in range(30)}}]
    rules.rules = lambda: [{"name": "r", "enabled": True, "when": long, "if": {"k": long},
                            "do": [{"server": "s", "tool": "t"}]}]
    rules._last["r"] = (False, 0.0, long)
    memory.rules = lambda: [{"id": "1", "text": long * 3, "source": long, "dropped": 0}]
    try:
        SettingsDialog._instance.destroy()
        SettingsDialog._instance = None
        SettingsDialog.show()
        minimum, _ = SettingsDialog._instance.get_preferred_width()
    finally:
        mcp_tools.status, rules.rules, memory.rules = saved
        rules._last.pop("r", None)
    assert minimum < 800, f"minimum width {minimum}px with long contents (limit 800)"
    print(f"✓ long MCP/rule/memory contents: minimum width {minimum}px")


def _where():
    """id(widget) -> (tab, sub-page) for every page of the open dialog."""
    where = {}
    for tab, sub, sw in _leaves(SettingsDialog._instance):
        for w in _widgets(sw):
            where[id(w)] = (tab, sub)
    return where


def test_the_sound_tab_holds_mic_music_echo_and_voice():
    cls, where = SettingsDialog, _where()
    for name, sub in (("_mic_combo", "Microphone & music"),
                      ("_media_combo", "Microphone & music"),
                      ("_media_duck_scale", "Microphone & music"),
                      ("_comp_duck_ai", "Microphone & music"),
                      ("_comp_duck_user", "Microphone & music"),
                      ("_talk_echo_guard", "Echo guard"),
                      ("_talk_barge_margin", "Echo guard"),
                      ("_tts_enabled_check", "Spoken voice"),
                      ("_talk_speak_check", "Spoken voice"),
                      ("_tts_engine_combo", "Spoken voice"),
                      ("_voice_combo", "Spoken voice")):
        assert where.get(id(getattr(cls, name))) == ("Sound", sub), (name, where.get(id(getattr(cls, name))))
    print("✓ Sound: microphone & music, echo guard, spoken voice")


def _tmp_config():
    import tempfile
    from loquivox import config as config_module, config_io
    d = tempfile.TemporaryDirectory()
    real = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = config_io.CONFIG_FILE = Path(d.name) / "config.toml"

    def restore():
        config_module.CONFIG_FILE = config_io.CONFIG_FILE = real
        config_module.reload_config()
        d.cleanup()
    return restore


def test_mic_music_and_echo_have_their_own_writers():
    from loquivox import config as config_module
    cls = SettingsDialog
    restore = _tmp_config()
    try:
        cls._mic_combo.append("hw:test", "hw:test")
        cls._mic_combo.set_active_id("hw:test")
        cls._media_combo.set_active_id("duck")
        cls._media_duck_scale.set_value(35)
        duck_before = config_module.CFG.COMPANION_DUCK_ON_ASSISTANT
        cls._comp_duck_ai.set_active(not duck_before)
        cls._talk_echo_guard.set_active_id("headset")
        cls._talk_barge_margin.set_value(2.5)
        # the companion's, transcription's and conversation's writers leave them alone
        for apply in (cls._on_apply_companion, cls._on_apply_transcription, cls._on_apply_talk):
            apply(None)
        cfg = config_module.CFG
        assert (cfg.INPUT_DEVICE, cfg.MEDIA, cfg.TALK_ECHO_GUARD) != ("hw:test", "duck", "headset"), cfg
        assert cfg.TALK_BARGE_IN_MARGIN != 2.5 and cfg.COMPANION_DUCK_ON_ASSISTANT == duck_before
        for apply in (cls._on_apply_mic, cls._on_apply_music, cls._on_apply_echo):
            apply(None)
        cfg = config_module.CFG
        assert (cfg.INPUT_DEVICE, cfg.MEDIA, cfg.MEDIA_DUCK_VOLUME) == ("hw:test", "duck", 0.35), cfg
        assert cfg.COMPANION_DUCK_ON_ASSISTANT == (not duck_before)
        assert (cfg.TALK_ECHO_GUARD, cfg.TALK_BARGE_IN_MARGIN) == ("headset", 2.5), cfg
    finally:
        restore()
        cls._media_combo.set_active_id("resume")
        cls._talk_echo_guard.set_active_id("speakers")
    print("✓ microphone, music and echo guard are written by their own Apply writers")


def test_nothing_outside_appearance_writes_before_apply():
    """Flip every control that used to write at once: no file is touched until Apply."""
    from loquivox import config_io
    from loquivox.state import STATE, SettingsManager
    cls = SettingsDialog
    wrote = []
    real = (SettingsManager.save, SettingsManager.update, config_io.update_section)
    SettingsManager.save = staticmethod(lambda *a, **k: wrote.append("save"))
    SettingsManager.update = staticmethod(lambda *a, **k: wrote.append("update"))
    config_io.update_section = lambda *a, **k: wrote.append("section")
    before = (STATE.ai_provider, STATE.tts_enabled, STATE.tts_model, STATE.tts_voice)
    try:
        other = next(p for p in cls._chat_provider.get_model() if p[1] != STATE.ai_provider)[1]
        cls._chat_provider.set_active_id(other)
        cls._tts_enabled_check.set_active(not STATE.tts_enabled)
        engines = [r[1] for r in cls._tts_engine_combo.get_model()]
        cls._tts_engine_combo.set_active_id(next(e for e in engines if e != STATE.tts_model))
        cls._voice_combo.set_active(len(cls._voice_combo.get_model()) - 1)
        for name, *_rest in cls.PRESETS:
            button = next(b for b in _buttons(_tab("Models & keys")) if b.get_label() == name)
            button.clicked()
        assert not wrote, wrote
        assert before == (STATE.ai_provider, STATE.tts_enabled, STATE.tts_model, STATE.tts_voice)
    finally:
        SettingsManager.save, SettingsManager.update = real[:2]
        config_io.update_section = real[2]
        cls._chat_provider.set_active_id(before[0])
        cls._tts_enabled_check.set_active(before[1])
        cls._tts_engine_combo.set_active_id(before[2])
    print("✓ providers, voice and presets wait for Apply")


def test_a_preset_fills_the_widgets_and_apply_saves_them():
    from loquivox.state import STATE
    cls = SettingsDialog
    restore = _tmp_config()
    saved = (STATE.tts_model, STATE.tts_voice)
    try:
        button = next(b for b in _buttons(_tab("Models & keys")) if b.get_label() == "Private")
        button.clicked()
        assert cls._engine_combo.get_active_id() == "cascade"
        assert cls._backend_combo.get_active() == [b[0] for b in cls._BACKENDS].index("whispercpp")
        assert cls._tts_engine_combo.get_active_id() == "piper"
        assert cls._voice_combo.get_active_id() == "fr_FR-siwis-medium"
        assert STATE.tts_model != "piper" or saved[0] == "piper"
        cls._on_apply_voice(None)
        assert (STATE.tts_model, STATE.tts_voice) == ("piper", "fr_FR-siwis-medium")
    finally:
        restore()
        STATE.tts_model, STATE.tts_voice = saved
        cls._tts_engine_combo.set_active_id(saved[0])
    print("✓ a preset only fills the form; Apply saves it")


def test_models_are_set_in_the_role_table_and_nowhere_else():
    cls, where = SettingsDialog, _where()
    roles = ("Answers", "Final text", "Vision", "Search", "Memory judge")
    for role in roles:
        row = _control("Models & keys", role)
        assert isinstance(row, Gtk.Box), role
        combos = [w for w in row.get_children() if isinstance(w, Gtk.ComboBox)]
        # vision has no provider of its own: it reads on the chat's
        assert len(combos) == (1 if role == "Vision" else 2), (role, "a provider and a model")
    for name in ("_agent_provider", "_agent_model", "_chat_provider", "_chat_model",
                 "_vision_model", "_research_provider", "_research_model",
                 "_judge_provider", "_judge_model"):
        assert where[id(getattr(cls, name))] == ("Models & keys", "Models by role"), name
    for name in ("_agent_provider", "_judge_provider"):
        assert getattr(cls, name).get_model()[0][0] == "Same as chat", name
    tools = {w.get_text() for t, s, sw in _leaves(cls._instance) if t == "Tools"
             for w in _widgets(sw) if isinstance(w, Gtk.Label) and w.get_name() == "field-label"}
    assert not tools & {"Provider", "Model"}, tools
    print("✓ every model role is a row of the table, and the Tools tab has no model field")


def test_the_role_table_applies():
    import time
    from loquivox import config as config_module
    from loquivox.services.ai import AIService
    from loquivox.state import STATE
    cls = SettingsDialog
    restore = _tmp_config()
    saved = (STATE.ai_provider, STATE.ai_chat_model, STATE.ai_vision_model,
             STATE.ai_agent_provider, STATE.ai_agent_model,
             STATE.ai_judge_provider, STATE.ai_judge_model)
    try:
        cls._chat_provider.set_active_id("groq")
        cls._chat_model.get_child().set_text("chat-m")
        cls._vision_model.get_child().set_text("vision-m")
        cls._agent_provider.set_active_id("openai")
        cls._agent_model.get_child().set_text("agent-m")
        cls._research_provider.set_active_id("openai")
        cls._research_model.get_child().set_text("search-m")
        cls._judge_provider.set_active_id("")
        cls._judge_model.get_child().set_text("judge-m")
        assert STATE.ai_chat_model != "chat-m", "must wait for Apply"
        cls._on_apply_models(None)
        assert (STATE.ai_provider, STATE.ai_chat_model, STATE.ai_vision_model) == (
            "groq", "chat-m", "vision-m")
        assert AIService.agent_target() == ("openai", "agent-m")
        assert AIService.judge_target() == ("groq", "judge-m")
        cfg = config_module.CFG
        assert (cfg.RESEARCH_PROVIDER, cfg.MODEL_RESEARCH) == ("openai", "search-m")
    finally:
        restore()
        (STATE.ai_provider, STATE.ai_chat_model, STATE.ai_vision_model,
         STATE.ai_agent_provider, STATE.ai_agent_model,
         STATE.ai_judge_provider, STATE.ai_judge_model) = saved
    print("✓ the role table writes the five roles on Apply")


def test_read_aloud_keeps_both_cases_on_the_sound_tab():
    """Chat answers (F10) and conversation replies are two switches: the default speaks only the second."""
    from loquivox import config as config_module
    cls = SettingsDialog
    labels = {cls._tts_enabled_check.get_label(), cls._talk_speak_check.get_label()}
    assert len(labels) == 2 and all("aloud" in l for l in labels), labels
    restore = _tmp_config()
    try:
        cls._talk_speak_check.set_active(False)
        cls._on_apply_voice(None)
        assert config_module.CFG.TALK_SPEAK_REPLIES is False
        cls._on_apply_talk(None)
        assert config_module.CFG.TALK_SPEAK_REPLIES is False, "the talk writer must not own it"
    finally:
        restore()
        cls._talk_speak_check.set_active(True)
    print("✓ both read-aloud switches live on Sound and are written by its writer")


if __name__ == "__main__":
    test_every_tab_builds()
    test_window_fits_its_default_width()
    test_label_columns_align()
    test_no_subpage_needs_more_than_a_screen_and_a_half()
    test_a_plugin_can_aim_at_a_subpage()
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
    test_live_fields_are_the_engines_and_the_companion_has_its_own()
    test_every_companion_setting_is_on_the_companion_tab()
    test_companion_live_settings_apply_and_are_documented()
    test_read_aloud_is_cascade_only()
    test_agent_model_is_a_combo()
    test_agent_models_are_listed_from_the_agent_provider()
    test_capture_group_says_f4_and_companion_always_capture()
    test_roles_prose_names_the_agent_and_the_chat_jobs()
    test_the_sound_tab_holds_mic_music_echo_and_voice()
    test_mic_music_and_echo_have_their_own_writers()
    test_nothing_outside_appearance_writes_before_apply()
    test_a_preset_fills_the_widgets_and_apply_saves_them()
    test_models_are_set_in_the_role_table_and_nowhere_else()
    test_the_role_table_applies()
    test_read_aloud_keeps_both_cases_on_the_sound_tab()
    test_f10_survives_an_apply_of_the_voice_page()
    test_no_stale_wording()
    test_long_contents_keep_the_window_narrow()
    SettingsDialog._instance.destroy()
    print("\nAll settings-layout checks passed.")
