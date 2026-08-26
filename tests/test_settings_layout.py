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
from gi.repository import Gtk  # noqa: E402

from loquivox.ui.settings_dialog import SettingsDialog  # noqa: E402

#: what _create_dialog asks for; nothing may make the window wider than this
DEFAULT_WIDTH = 640

TABS = ["Models", "Talk", "Refinement", "Hotkeys", "Appearance", "API Keys"]


def _notebook(win: Gtk.Window) -> Gtk.Notebook:
    return win.get_child().get_children()[0]


def test_every_tab_builds():
    """All six pages construct, in the documented order."""
    SettingsDialog.show()
    win = SettingsDialog._instance
    nb = _notebook(win)
    labels = [nb.get_tab_label_text(nb.get_nth_page(i))
              for i in range(nb.get_n_pages())]
    assert labels == TABS, labels
    print(f"✓ six tabs build: {', '.join(labels)}")


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


if __name__ == "__main__":
    test_every_tab_builds()
    test_window_fits_its_default_width()
    test_label_columns_align()
    test_handlers_find_their_widgets()
    test_greying_a_control_greys_its_label()
    SettingsDialog._instance.destroy()
    print("\nAll settings-layout checks passed.")
