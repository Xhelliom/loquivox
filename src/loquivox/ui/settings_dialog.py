"""
GTK Settings dialog for voice and hotkey configuration.
"""
from __future__ import annotations

import math
from typing import Optional

import cairo

from loquivox import config as config_module
from loquivox import i18n
from loquivox.i18n import N_, _
from loquivox.decorators import run_on_main_thread
from loquivox.state import STATE, SettingsManager

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import GLib, GObject, Gtk, Pango, PangoCairo


class SettingsDialog:
    """GTK Settings dialog for voice and hotkey configuration."""

    _instance: Optional[Gtk.Window] = None
    _listbox: Optional[Gtk.ListBox] = None
    _preview_area: Optional[Gtk.DrawingArea] = None
    _preview_tick: int = 0
    _preview_timer = None

    # Transcription backends offered in the UI:
    #   (id, label, is_streaming, model config-key, CFG attr holding the model)
    # "auto" / local / cloud-batch show a "transcribing…" indicator; streaming
    # backends show live text.
    _BACKENDS = [
        ("groq",            N_("Groq — cloud, batch"),          False, "model",            "MODEL_WHISPER"),
        ("whispercpp",      N_("whisper.cpp — local, offline"), False, "whispercpp_model", "WHISPERCPP_MODEL"),
        ("deepgram",        N_("Deepgram — cloud, live"),       True,  "deepgram_model",   "DEEPGRAM_MODEL"),
        ("openai_realtime", N_("OpenAI Realtime — cloud, live"), True, "openai_model",     "OPENAI_MODEL"),
        ("auto",            N_("Auto — Groq if key, else local"), False, None,            None),
    ]

    # Known model ids per backend (verified against provider docs). The model
    # combo is editable, so a custom/newer id can still be typed.
    _MODELS = {
        "groq":            ["whisper-large-v3-turbo", "whisper-large-v3"],
        "whispercpp":      ["base", "tiny", "small", "medium", "large-v3",
                            "large-v3-turbo", "base.en", "tiny.en", "small.en"],
        "deepgram":        ["nova-3", "nova-2"],
        # gpt-live-transcribe is first: it is the only OpenAI model that emits
        # the transcript *while* you speak (the 4o ones wait for the commit).
        "openai_realtime": ["gpt-live-transcribe", "gpt-realtime-whisper",
                            "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
    }
    # Provider model-list links shown as a hint per backend.
    _MODEL_DOCS = {
        "groq":            "console.groq.com/docs/speech-to-text",
        "deepgram":        "developers.deepgram.com/docs/models-languages-overview",
        "openai_realtime": "platform.openai.com/docs/guides/realtime-transcription",
        "whispercpp":      "auto-downloads on first use",
    }
    # Common languages offered in the combo (label, ISO-639-1 code). Editable, so
    # any other Whisper-supported code can be typed directly.
    _LANGUAGES = [
        (N_("Autodetect"), ""), (N_("English"), "en"), (N_("French"), "fr"), (N_("German"), "de"),
        (N_("Spanish"), "es"), (N_("Italian"), "it"), (N_("Portuguese"), "pt"), (N_("Dutch"), "nl"),
        (N_("Russian"), "ru"), (N_("Chinese"), "zh"), (N_("Japanese"), "ja"), (N_("Korean"), "ko"),
        (N_("Arabic"), "ar"), (N_("Hindi"), "hi"), (N_("Polish"), "pl"),
    ]

    _whisper_status: Optional[Gtk.Label] = None
    _hotkey_entries: Optional[dict] = None
    _hotkey_status: Optional[Gtk.Label] = None
    _hotkey_capture_btns: Optional[list] = None
    _key_entries: Optional[dict] = None
    _key_status: Optional[Gtk.Label] = None
    _support_status: Optional[Gtk.Label] = None
    _pp_scale: Optional[Gtk.Scale] = None
    _pp_scale_label: Optional[Gtk.Label] = None
    _pp_translate_check: Optional[Gtk.CheckButton] = None
    _pp_format_check: Optional[Gtk.CheckButton] = None
    _pp_lang: Optional[Gtk.ComboBoxText] = None
    _pp_status: Optional[Gtk.Label] = None
    _pp_prompt_view: Optional[Gtk.TextView] = None
    _pp_prompt_scroll: Optional[Gtk.ScrolledWindow] = None
    _pp_prompt_label: Optional[Gtk.Expander] = None

    # Live widget handles (set while the dialog is open).
    _backend_combo: Optional[Gtk.ComboBoxText] = None
    _model_entry: Optional[Gtk.Entry] = None
    _lang_entry: Optional[Gtk.Entry] = None
    _mic_combo: Optional[Gtk.ComboBoxText] = None
    _vocab_entry: Optional[Gtk.Entry] = None
    _fallback_check: Optional[Gtk.CheckButton] = None
    _trans_status: Optional[Gtk.Label] = None
    _voice_combo: Optional[Gtk.ComboBoxText] = None

    # Models & keys tab (and the engine, on Conversation)
    _chat_provider: Optional[Gtk.ComboBoxText] = None
    _chat_model: Optional[Gtk.ComboBoxText] = None
    _research_provider: Optional[Gtk.ComboBoxText] = None
    _research_model: Optional[Gtk.ComboBoxText] = None
    _judge_provider: Optional[Gtk.ComboBoxText] = None
    _judge_model: Optional[Gtk.ComboBoxText] = None
    _vision_model: Optional[Gtk.ComboBoxText] = None
    _agent_provider: Optional[Gtk.ComboBoxText] = None
    _agent_model: Optional[Gtk.ComboBoxText] = None
    _models_status: Optional[Gtk.Label] = None
    _preset_status: Optional[Gtk.Label] = None
    _engine_combo: Optional[Gtk.ComboBoxText] = None
    _engine_status: Optional[Gtk.Label] = None
    _realtime_model: Optional[Gtk.Entry] = None
    _realtime_voice: Optional[Gtk.ComboBoxText] = None
    _live_voice: Optional[Gtk.ComboBoxText] = None
    _live_test: Optional[Gtk.Button] = None
    _live_delegation: Optional[Gtk.ComboBoxText] = None
    _live_backend: Optional[Gtk.Entry] = None
    _live_web_search: Optional[Gtk.ComboBoxText] = None
    _tts_engine_combo: Optional[Gtk.ComboBoxText] = None
    _realtime_test: Optional[Gtk.Button] = None

    # Talk mode
    _talk_phrase_check: Optional[Gtk.CheckButton] = None
    _talk_model_check: Optional[Gtk.CheckButton] = None
    _talk_depth: Optional[Gtk.ComboBoxText] = None
    _talk_echo_guard: Optional[Gtk.ComboBoxText] = None
    _talk_semantic_check: Optional[Gtk.CheckButton] = None
    _talk_speak_check: Optional[Gtk.CheckButton] = None
    _talk_instructions: Optional[Gtk.TextView] = None
    _talk_autopaste_check: Optional[Gtk.CheckButton] = None
    _talk_shot_check: Optional[Gtk.CheckButton] = None
    _talk_shot_clip: Optional[Gtk.CheckButton] = None
    _talk_shot_region: Optional[Gtk.ComboBoxText] = None
    _talk_shot_cursor: Optional[Gtk.SpinButton] = None
    _talk_shot_max: Optional[Gtk.SpinButton] = None
    _talk_status: Optional[Gtk.Label] = None

    # Switches that used to live in the tray only, and the companion's sounds
    _toggle_mode_check: Optional[Gtk.CheckButton] = None
    _recording_status: Optional[Gtk.Label] = None
    _chat_enabled_check: Optional[Gtk.CheckButton] = None
    _tts_enabled_check: Optional[Gtk.CheckButton] = None
    _media_combo: Optional[Gtk.ComboBoxText] = None
    _media_duck_scale: Optional[Gtk.Scale] = None
    _talk_barge_margin: Optional[Gtk.SpinButton] = None

    # (writer, its status label) for every group that writes something, in the
    # order the pages are built. The one Apply button runs all of them.
    _appliers: list = []
    _apply_status: Optional[Gtk.Label] = None

    @classmethod
    def show(cls) -> None:
        """Show settings dialog (singleton)."""
        if cls._instance and cls._instance.get_visible():
            cls._instance.present()
            return

        cls._instance = cls._create_dialog()
        cls._instance.show_all()

    @classmethod
    def _create_dialog(cls) -> Gtk.Window:
        """Create the settings dialog window (tabbed)."""
        dialog = Gtk.Window(title=_("Loquivox Settings"))
        dialog.set_default_size(640, 780)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Group the (now numerous) settings into tabs instead of one long scroll.
        # One tab per use, in the order you meet them: Dictation (F3), the
        # Conversation (F4/F6) and the Companion, then the Tools they can
        # reach, then who does the work and with which keys (Models & keys),
        # then how it looks (Appearance) and how it is driven (Hotkeys), and
        # where to look when something breaks (Support).
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)
        for m in ("top", "start", "end"):
            getattr(notebook, f"set_margin_{m}")(12)

        cls._appliers = []   # the pages fill it back in as they are built
        cls._build_tabs(notebook)

        root.pack_start(notebook, True, True, 0)

        # Action bar: the window's only Apply, outside the notebook and outside
        # every page's scroll, so it is on screen wherever the user has
        # scrolled to and whichever tab is showing.
        root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 0)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(actions, f"set_margin_{m}")(10)

        cls._apply_status = Gtk.Label()
        cls._apply_status.set_halign(Gtk.Align.START)
        cls._apply_status.set_xalign(0)
        cls._apply_status.set_ellipsize(Pango.EllipsizeMode.END)
        cls._apply_status.set_max_width_chars(40)
        actions.pack_start(cls._apply_status, True, True, 0)

        close_btn = Gtk.Button(label=_("Close"))
        close_btn.connect("clicked", lambda w: dialog.destroy())
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.set_tooltip_text(_("Save and apply every tab, not just this one"))
        apply_btn.connect("clicked", cls._apply_all)
        actions.pack_end(apply_btn, False, False, 0)
        actions.pack_end(close_btn, False, False, 0)
        root.pack_start(actions, False, False, 0)

        dialog.add(root)
        dialog.connect("destroy", cls._on_destroy)
        return dialog

    @classmethod
    def _apply_all(cls, _btn: Gtk.Button) -> None:
        """
        Run every group's writer, whichever tab it lives on, then say how it went.

        Each writer already reports into its own status line; this only counts
        the ones that came back unhappy, because those lines are two tabs and a
        scroll away from the button that was just clicked.
        """
        unhappy = 0
        for handler, status in cls._appliers:
            try:
                handler(None)
            except Exception as e:   # one broken section must not stop the rest
                status.set_markup(
                    f"<small>❌ {GLib.markup_escape_text(str(e))}</small>")
            if status.get_text().lstrip().startswith(("❌", "⚠️")):
                unhappy += 1

        from loquivox.ui.tray import TrayManager
        TrayManager.update_menu()   # no stale tick after an Apply
        if cls._apply_status is None:
            return
        if unhappy:
            cls._apply_status.set_markup(
                _("<small>⚠️ Applied — {unhappy} setting(s) need a look; their tab "
                           "says which.</small>").format(unhappy=unhappy))
        else:
            cls._apply_status.set_markup(_("<small>✓ All settings applied.</small>"))

    @classmethod
    def _on_destroy(cls, _w: Gtk.Widget) -> None:
        cls._instance = None
        if cls._preview_timer is not None:
            try:
                GLib.source_remove(cls._preview_timer)
            except Exception:
                pass
            cls._preview_timer = None
        cls._preview_area = None

    @classmethod
    def _tick_preview(cls) -> bool:
        """Drive the looping animation of the overlay preview."""
        if cls._instance is None or cls._preview_area is None:
            cls._preview_timer = None
            return False
        cls._preview_tick += 1
        cls._preview_area.queue_draw()
        return True

    @staticmethod
    def _page() -> Gtk.Box:
        """A padded vertical box used as a notebook tab body."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        for m in ("top", "bottom", "start", "end"):
            getattr(vbox, f"set_margin_{m}")(16)
        return vbox

    @classmethod
    def _scroll(cls, child: Gtk.Widget) -> Gtk.ScrolledWindow:
        """Wrap a tab body so it scrolls if it ever exceeds the window height."""
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cls._tame_scroll(child)
        sw.add(child)
        return sw

    @classmethod
    def _tame_scroll(cls, widget: Gtk.Widget) -> None:
        """
        The wheel scrolls the page, never whatever the pointer happens to be over.

        A combo, a slider and a spin button all eat scroll events by default, so
        crossing one while scrolling silently changes a setting — the reason the
        window could only be scrolled along its edge. Done here, on the built
        page, rather than at each of the ~30 call sites that create one.
        """
        if isinstance(widget, (Gtk.ComboBox, Gtk.Range, Gtk.SpinButton)):
            widget.connect("scroll-event", cls._scroll_the_page)
        if isinstance(widget, Gtk.Container):
            for kid in widget.get_children():
                cls._tame_scroll(kid)

    @staticmethod
    def _scroll_the_page(widget: Gtk.Widget, event) -> bool:
        """Hand the event to the parent chain, skipping the control itself."""
        # ponytail: never scrollable even when focused — clicking a combo then
        # scrolling the page with the pointer still on it is the same annoyance.
        Gtk.propagate_event(widget.get_parent(), event)
        return True

    # -----------------------------------------------------------------
    # Layout vocabulary
    #
    # Three helpers, used by every tab, are what keeps the window readable:
    # a page is a stack of *groups*, a group is a title + one dim line of
    # prose + a framed body, and a body is a stack of *fields* whose labels
    # all align. Building rows by hand is what made every tab drift into its
    # own spacing, its own alignment and its own idea of where Apply goes.
    # -----------------------------------------------------------------
    @staticmethod
    def _group(parent: Gtk.Box, title: str, subtitle: str = "") -> Gtk.Box:
        """
        A titled card. Returns the body box to pack the controls into.

        Title outside the frame, prose under it, controls enclosed: the
        enclosure is what tells the eye where one setting ends and the next
        begins, and the size/weight jump is what makes the titles scannable
        without reading a word of the body.
        """
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        heading = Gtk.Label()
        heading.set_halign(Gtk.Align.START)
        heading.set_markup(
            f'<span size="large" weight="bold">{GLib.markup_escape_text(title)}</span>')
        section.pack_start(heading, False, False, 0)

        if subtitle:
            note = Gtk.Label()
            note.set_halign(Gtk.Align.START)
            note.set_xalign(0)
            note.set_line_wrap(True)
            note.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            note.set_max_width_chars(62)   # a measure, so prose never runs edge to edge
            note.set_markup(f"<small>{subtitle}</small>")
            note.get_style_context().add_class("dim-label")
            section.pack_start(note, False, False, 0)

        frame = Gtk.Frame()               # themed border, light and dark alike
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.set_margin_top(6)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{m}")(12)
        frame.add(body)
        section.pack_start(frame, False, False, 0)

        parent.pack_start(section, False, False, 0)
        return body

    @classmethod
    def _field(cls, body: Gtk.Box, label: str, widget: Gtk.Widget,
               labels: Optional[Gtk.SizeGroup] = None,
               extra: Optional[Gtk.Widget] = None) -> None:
        """One label → control row. The size group aligns every label on a page."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl = cls._row_label(label)
        lbl.set_name("field-label")   # the aligned column, as a CSS selector
        # A live label beside a dead control is the tell that a section is
        # hand-assembled; bind it once here and every field greys out whole.
        widget.bind_property("sensitive", lbl, "sensitive",
                             GObject.BindingFlags.SYNC_CREATE)
        if labels is not None:
            labels.add_widget(lbl)
        row.pack_start(lbl, False, False, 0)
        cls._elide(widget)
        row.pack_start(widget, True, True, 0)
        if extra is not None:
            row.pack_start(extra, False, False, 0)
        body.pack_start(row, False, False, 0)

    @staticmethod
    def _elide(widget: Gtk.Widget) -> None:
        """
        Stop one long row deciding how wide the window is.

        A ComboBoxText asks for the width of its longest item — a microphone's
        full ALSA name, a provider's longest model id — and a Gtk.Window can
        never be narrower than what its children ask for. Ellipsizing the cell
        caps that at a sane measure; the full string stays in the popup.
        """
        if not isinstance(widget, Gtk.ComboBox):
            return
        for cell in widget.get_cells():
            if isinstance(cell, Gtk.CellRendererText):
                cell.set_property("ellipsize", Pango.EllipsizeMode.END)
                cell.set_property("max-width-chars", 34)

    @classmethod
    def _actions(cls, body: Gtk.Box, handler, status: Gtk.Label) -> None:
        """
        The footer of a group: the writer it registers, and its status line.

        There is one Apply in the window, pinned below the scroll, so a group
        no longer carries a button of its own — it hands over what it writes
        and keeps the line saying whether that worked. Seven buttons scattered
        across six tabs meant "which one do I click", and the answer was
        "whichever card the setting you touched happened to be in".
        """
        status.set_halign(Gtk.Align.START)
        status.set_xalign(0)
        status.set_line_wrap(True)
        # WORD_CHAR, not WORD: a status line carries API errors, and one
        # unbreakable URL in one would otherwise set the window's width.
        status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        status.set_max_width_chars(48)
        status.set_margin_top(2)
        body.pack_start(status, False, False, 0)
        cls._appliers.append((handler, status))

    @staticmethod
    def _note(body: Gtk.Box, markup: str) -> None:
        """A dim footnote inside a group, for the caveat a subtitle can't hold."""
        lbl = Gtk.Label()
        lbl.set_halign(Gtk.Align.START)
        lbl.set_xalign(0)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(58)
        lbl.set_markup(f"<small>{markup}</small>")
        lbl.get_style_context().add_class("dim-label")
        body.pack_start(lbl, False, False, 0)

    # -----------------------------------------------------------------
    # Pages: one per use. Each only assembles groups built further down.
    # -----------------------------------------------------------------
    #: The window's map: (tab, sub-pages). A tab with sub-pages is a nested
    #: notebook, tabs along the top (a sidebar would cost 120-160 px of the
    #: 640 the window has); one without is a single page. A plugin asks for its
    #: card with ``Plugin.tab = "tab/sub"``, so the dialog names no plugin.
    _TABS = (
        (N_("Dictation"), "dictation",
         (("transcription", N_("Transcription")), ("refinement", N_("Refinement")))),
        (N_("Conversation"), "conversation",
         (("engine", N_("Engine")), ("flow", N_("Flow")), ("screen", N_("Screen")))),
        (N_("Companion"), "companion",
         (("general", N_("General")), ("voice", N_("Voice & thinking")),
          ("presence", N_("Presence")), ("notifications", N_("Notifications")),
          ("memory", N_("Memory")))),
        (N_("Sound"), "sound",
         (("mic", N_("Microphone & music")), ("echo", N_("Echo guard")),
          ("voice", N_("Spoken voice")))),
        (N_("Tools"), "tools",
         (("search", N_("Search")), ("writing", N_("Writing & screen")),
          ("mcp", N_("MCP servers")), ("rules", N_("Rules")))),
        (N_("Models & keys"), "models",
         (("roles", N_("Models by role")), ("keys", N_("Keys")),
          ("presets", N_("Presets")))),
        (N_("Appearance"), "appearance", None),
        (N_("Hotkeys"), "hotkeys", None),
        (N_("Support"), "support", None),
    )

    #: "tab/sub" -> (page box, its label size group), filled by ``_build_tabs``
    _subpages: dict = {}

    @classmethod
    def _build_tabs(cls, notebook: Gtk.Notebook) -> None:
        """
        Build every page up front (one Apply must reach all of them), then
        assemble the notebooks: each page gets its own ScrolledWindow and its
        own label column, so nothing scrolls or aligns with a neighbour.
        """
        cls._subpages = {}
        for _label, key, subs in cls._TABS:
            for sub, _sl in subs or (("", ""),):
                cls._subpages[f"{key}/{sub}" if sub else key] = (
                    cls._page(), Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))

        def at(path: str):
            return cls._subpages[path]

        box, lab = at("dictation/transcription")
        cls._build_transcription_section(box, lab)
        cls._build_recording_section(box, lab)
        cls._build_postprocess_section(at("dictation/refinement")[0])

        flow, flow_lab = at("conversation/flow")
        screen, screen_lab = at("conversation/screen")
        cls._build_engine_section(*at("conversation/engine"))
        cls._build_echo_section(*at("sound/echo"))   # before the talk page, which greys by it
        cls._build_talk_section(flow, flow_lab, screen, screen_lab)

        cls._build_companion_section(at("companion/general"), at("companion/voice"),
                                     at("companion/presence"))
        cls._build_mic_section(*at("sound/mic"))
        cls._build_music_section(*at("sound/mic"))
        cls._build_voice_section(*at("sound/voice"))
        # Both read-aloud boxes exist now: the engine greys the cascade-only one.
        cls._on_talk_engine_changed(cls._engine_combo)
        # Presets first: they reach into the other tabs' slots, the shortcut lives here.
        cls._build_presets(at("models/presets")[0])
        cls._build_roles_section(*at("models/roles"))
        cls._build_api_keys_section(*at("models/keys"))
        cls._build_appearance_page(at("appearance")[0])
        cls._build_hotkeys_section(at("hotkeys")[0])
        cls._build_support_page(at("support")[0])

        for _label, key, subs in cls._TABS:
            for i, (sub, _sl) in enumerate(subs or ()):
                box, lab = at(f"{key}/{sub}")
                cls._build_plugin_cards(box, f"{key}/{sub}", lab, first=(i == 0))
                if not box.get_children():
                    cls._note(box, _("Nothing to set here yet."))

        cls._build_advanced(at)

        for label, key, subs in cls._TABS:
            if subs is None:
                notebook.append_page(cls._scroll(at(key)[0]),
                                     Gtk.Label(label=_(label)))
                continue
            inner = Gtk.Notebook()
            inner.set_scrollable(True)
            inner.set_show_border(False)   # the outer notebook already frames it
            for sub, sub_label in subs:
                inner.append_page(cls._scroll(at(f"{key}/{sub}")[0]),
                                  Gtk.Label(label=_(sub_label)))
            notebook.append_page(inner, Gtk.Label(label=_(label)))

    @classmethod
    def _fold(cls, page: Gtk.Box) -> Gtk.Box:
        """A closed « Advanced » at the foot of a page; returns the box inside."""
        fold = Gtk.Expander(label=_("Advanced"))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_margin_top(8)
        fold.add(inner)
        page.pack_end(fold, False, False, 0)
        return inner

    @staticmethod
    def _spin(value, low, high, step, digits=0) -> Gtk.SpinButton:
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_digits(digits)
        spin.set_value(value)
        return spin

    @classmethod
    def _build_advanced(cls, at) -> None:
        """
        What used to be config.toml only, folded away at the foot of the page of
        the thing it changes. One writer (``_on_apply_advanced``) for all of them:
        none is worth a card of its own, and the one Apply reaches it.
        """
        cfg = config_module.CFG

        def entry(text, hint):
            e = Gtk.Entry()
            e.set_text(text)
            e.set_tooltip_text(hint)
            return e

        page, labels = at("conversation/engine")
        body = cls._fold(page)
        cls._adv_live_model = entry(cfg.TALK_LIVE_MODEL, _("The Live engine's voice model"))
        cls._field(body, _("Live model"), cls._adv_live_model, labels)
        cls._adv_realtime_transcribe = entry(
            cfg.TALK_REALTIME_TRANSCRIBE,
            _("Transcribes your side of a Realtime conversation"))
        cls._field(body, _("Realtime transcription"), cls._adv_realtime_transcribe, labels)
        cls._advanced_status = Gtk.Label()
        cls._actions(page, cls._on_apply_advanced, cls._advanced_status)

        page, labels = at("conversation/flow")
        body = cls._fold(page)
        cls._adv_vad_threshold = cls._spin(cfg.TALK_VAD_THRESHOLD, 0.005, 0.2, 0.005, 3)
        cls._adv_vad_threshold.set_tooltip_text(
            _("Minimum voice level that counts as speech; the room's noise raises it"))
        cls._field(body, _("Voice level"), cls._adv_vad_threshold, labels)
        cls._adv_vad_silence = cls._spin(cfg.TALK_VAD_SILENCE_MS, 200, 3000, 50)
        cls._adv_vad_silence.set_tooltip_text(_("A pause this long ends your turn"))
        cls._field(body, _("Pause that ends a turn (ms)"), cls._adv_vad_silence, labels)
        cls._adv_vad_min_speech = cls._spin(cfg.TALK_VAD_MIN_SPEECH_MS, 100, 2000, 50)
        cls._adv_vad_min_speech.set_tooltip_text(_("Shorter sounds are ignored"))
        cls._field(body, _("Shortest speech (ms)"), cls._adv_vad_min_speech, labels)

        page, labels = at("companion/presence")
        body = cls._fold(page)
        cls._adv_comp_idle = cls._spin(cfg.COMPANION_IDLE_TIMEOUT, 3, 300, 1, 1)
        cls._adv_comp_idle.set_tooltip_text(
            _("Seconds of silence, both ways, that end a conversation"))
        cls._field(body, _("Conversation ends after (s)"), cls._adv_comp_idle, labels)
        cls._adv_comp_stop = entry(
            ", ".join(cfg.COMPANION_STOP_PHRASES),
            _("Said on their own, these close the conversation without a reply"))
        cls._field(body, _("Stop phrases"), cls._adv_comp_stop, labels)
        cls._note(body, _("Separated by commas; accents and punctuation are ignored."))

        page, labels = at("sound/mic")
        body = cls._fold(page)
        cls._adv_duck_hold = cls._spin(cfg.COMPANION_DUCK_HOLD_SEC, 0, 10, 0.5, 1)
        cls._adv_duck_hold.set_tooltip_text(
            _("How long the sound stays lowered after the speech stops"))
        cls._field(body, _("Lowered sound returns after (s)"), cls._adv_duck_hold, labels)

        page, labels = at("tools/writing")
        body = cls._fold(page)
        cls._adv_look_tool = Gtk.CheckButton(
            label=_("Let the model look at the window again when asked"))
        cls._adv_look_tool.set_active(bool(cfg.TALK_LOOK_TOOL))
        cls._field(body, _("Look at the screen"), cls._adv_look_tool, labels)

        page, labels = at("tools/mcp")
        body = cls._fold(page)
        cls._adv_agent_turns = cls._spin(cfg.AGENT_MAX_TURNS, 1, 30, 1)
        cls._adv_agent_turns.set_tooltip_text(
            _("Rounds of tool calls the agent may chain in one answer"))
        cls._field(body, _("Agent rounds (max)"), cls._adv_agent_turns, labels)
        cls._adv_agent_sec = cls._spin(cfg.AGENT_MAX_SEC, 10, 600, 10)
        cls._adv_agent_sec.set_tooltip_text(_("Time limit for one answer, tools included"))
        cls._field(body, _("Agent time limit (s)"), cls._adv_agent_sec, labels)

    @classmethod
    def _on_apply_advanced(cls, _btn) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        stop = [p.strip() for p in cls._adv_comp_stop.get_text().split(",") if p.strip()]
        try:
            update_section("talk", {
                "live_model": cls._adv_live_model.get_text().strip(),
                "realtime_transcribe": cls._adv_realtime_transcribe.get_text().strip(),
                "vad_threshold": round(cls._adv_vad_threshold.get_value(), 3),
                "silence_ms": int(cls._adv_vad_silence.get_value()),
                "min_speech_ms": int(cls._adv_vad_min_speech.get_value()),
                "agent_max_turns": int(cls._adv_agent_turns.get_value()),
                "agent_max_sec": float(cls._adv_agent_sec.get_value()),
                "look_tool": cls._adv_look_tool.get_active(),
            })
            update_section("companion", {
                "idle_timeout": cls._adv_comp_idle.get_value(),
                "stop_phrases": stop,
                "duck_hold": cls._adv_duck_hold.get_value(),
            })
        except ConfigWriteError as e:
            cls._advanced_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        cls._advanced_status.set_markup(
            _("<small>✓ Advanced settings saved — used from the next session.</small>"))

    # -----------------------------------------------------------------
    # Companion tab: the always-present bubble
    # -----------------------------------------------------------------
    _WAKE_WORDS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy",
                   "timer", "weather")
    _CLICK_MODES = (("silent", N_("Silence the companion")),
                    ("active", N_("Wake the companion")))

    @classmethod
    def _build_companion_section(cls, general, voice, presence) -> None:
        """The companion's own settings: three sub-pages, one writer (``_on_apply_companion``)."""
        cfg = config_module.CFG
        vbox, labels = general
        body = cls._group(
            vbox, _("Wake word and bubble"),
            _("A small bubble that listens for its wake word on this machine and "
            "opens the cloud conversation only when spoken to."))

        cls._comp_wake = Gtk.ComboBoxText()
        words = list(cls._WAKE_WORDS)
        if cfg.COMPANION_WAKE_WORD not in words:
            words.append(cfg.COMPANION_WAKE_WORD)
        for w in words:
            cls._comp_wake.append(w, w)
        cls._comp_wake.set_active_id(cfg.COMPANION_WAKE_WORD)
        cls._comp_install = Gtk.Button(label=_("Install openwakeword"))
        cls._comp_install.connect("clicked", cls._on_install_wake)
        cls._comp_install.set_visible(not cls._module_installed("openwakeword"))
        cls._comp_install.set_no_show_all(True)
        cls._field(body, _("Wake word"), cls._comp_wake, labels, extra=cls._comp_install)

        cls._comp_click = Gtk.ComboBoxText()
        for cid, label in cls._CLICK_MODES:
            cls._comp_click.append(cid, _(label))
        cls._comp_click.set_active_id(cfg.COMPANION_CLICK_MODE)
        cls._field(body, _("Click on the bubble"), cls._comp_click, labels)

        cls._comp_style = Gtk.ComboBoxText()
        for name in ("goutte", "plasma", "radar", "reacteur"):
            cls._comp_style.append(name, name)
        cls._comp_style.set_active_id(cfg.COMPANION_BUBBLE_STYLE)
        cls._field(body, _("Bubble style"), cls._comp_style, labels)

        cls._comp_status = Gtk.Label()
        cls._actions(body, cls._on_apply_companion, cls._comp_status)

        cls._build_companion_voice(*voice)
        cls._build_companion_presence(*presence)

    #: first entry of the companion's combos: its own value is unset
    _LIKE_TALK = N_("Like the conversation")
    _DELEGATION_LABELS = {
        "auto": N_("Auto — follow the chat provider"),
        "client": N_("Your backend — the chat model, with plugins"),
        "responses": N_("OpenAI's backend — named tool calls"),
    }
    _WEB_SEARCH_LABELS = {
        "plugin": N_("Yours — the search plugin, your provider"),
        "native": N_("OpenAI's — their search, run on their side"),
    }

    @classmethod
    def _build_companion_voice(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """
        Voice, thinking and instructions of the companion, which always runs on
        Live. Each field starts at "like the conversation" (empty in the
        config); a value of its own wins for the companion only.
        """
        cfg = config_module.CFG
        body = cls._group(
            vbox, _("How the companion talks and thinks"),
            _("The companion always talks through the Live engine. Each setting "
            "below follows the Conversation tab until you give it a value of "
            "its own."))

        def combo(options, current, labelled):
            box = Gtk.ComboBoxText()
            box.append("", _(cls._LIKE_TALK))
            for oid in options:
                box.append(oid, labelled(oid))
            box.set_active_id(current if current in options else "")
            return box

        cls._comp_voice = combo(cfg.TALK_LIVE_VOICES, cfg.COMPANION_LIVE_VOICE,
                                lambda v: v.title())
        cls._field(body, _("Companion's Live voice"), cls._comp_voice, labels)

        cls._comp_delegation = combo(
            cfg.TALK_LIVE_DELEGATIONS, cfg.COMPANION_LIVE_DELEGATION,
            lambda d: _(cls._DELEGATION_LABELS.get(d, d.title())))
        cls._field(body, _("Companion delegates thinking to"), cls._comp_delegation, labels)

        cls._comp_backend = Gtk.Entry()     # ids change often — free text
        cls._comp_backend.set_text(cfg.COMPANION_LIVE_BACKEND_MODEL)
        cls._comp_backend.set_placeholder_text(_(cls._LIKE_TALK))
        cls._comp_backend.set_tooltip_text(
            _("Only used when the thinking is delegated to OpenAI's backend"))
        cls._field(body, _("Companion backend model"), cls._comp_backend, labels)

        cls._comp_web = combo(
            cfg.TALK_LIVE_WEB_SEARCHES, cfg.COMPANION_LIVE_WEB_SEARCH,
            lambda w: _(cls._WEB_SEARCH_LABELS.get(w, w.title())))
        cls._field(body, _("Companion web search"), cls._comp_web, labels)

        # The backend fields mean something only when the thinking goes to OpenAI.
        cls._comp_delegation.connect("changed", lambda _w: cls._update_companion_live())
        if cls._live_delegation is not None:
            cls._live_delegation.connect("changed", lambda _w: cls._update_companion_live())
        cls._update_companion_live()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, 100)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        cls._comp_instructions = Gtk.TextView()
        cls._comp_instructions.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        cls._comp_instructions.set_left_margin(6)
        cls._comp_instructions.set_right_margin(6)
        cls._comp_instructions.get_buffer().set_text(cfg.COMPANION_INSTRUCTIONS)
        scroll.add(cls._comp_instructions)
        cls._field(body, _("Companion instructions"), scroll, labels)
        cls._note(body, _("Empty = the instructions of the Conversation tab. "
                        "Given here, they replace those for the companion."))
        cls._note(body, _("On waking, the companion captures the focused window "
                        "and has it described — as the screen framing set in "
                        "<b>Conversation › Screen</b> says."))

    @classmethod
    def _update_companion_live(cls) -> None:
        """Backend model and web search matter only once the thinking is OpenAI's."""
        from loquivox.services.live_talk import resolve_delegation

        own = cls._comp_delegation.get_active_id()
        shared = (cls._live_delegation.get_active_id() if cls._live_delegation is not None
                  else config_module.CFG.TALK_LIVE_DELEGATION)
        on = resolve_delegation(own or shared) == "responses"
        for w in (cls._comp_backend, cls._comp_web):
            w.set_sensitive(on)

    @classmethod
    def _build_companion_presence(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        cfg = config_module.CFG
        body = cls._group(
            vbox, _("Presence"),
            _("When the companion listens, and when it keeps quiet."))

        cls._comp_enabled = Gtk.CheckButton(label=_("Listen when Loquivox launches"))
        cls._comp_enabled.set_active(bool(cfg.COMPANION_ENABLED))
        cls._field(body, _("Enabled"), cls._comp_enabled, labels)

        cls._comp_lock = Gtk.CheckButton(label=_("Silence when the screen is locked"))
        cls._comp_lock.set_active(bool(cfg.COMPANION_SILENCE_ON_LOCK))
        cls._field(body, _("Lock"), cls._comp_lock, labels)
        cls._comp_idle = Gtk.SpinButton.new_with_range(0, 240, 5)
        cls._comp_idle.set_value(cfg.COMPANION_SILENCE_IDLE_MIN)
        cls._comp_idle.set_tooltip_text(_("0 = never · takes effect at next launch"))
        cls._field(body, _("Idle (min)"), cls._comp_idle, labels)

        cls._comp_source_silence = Gtk.SpinButton.new_with_range(0, 60, 0.5)
        cls._comp_source_silence.set_digits(1)
        cls._comp_source_silence.set_value(cfg.COMPANION_SOURCE_SILENCE_SEC)
        cls._comp_source_silence.set_tooltip_text(
            _("Seconds without a word heard before a notification may open a "
              "conversation by itself"))
        cls._field(body, _("Delay before announcing (s)"), cls._comp_source_silence, labels)

        cls._note(body, _("The silence key is set in the <b>Hotkeys</b> tab "
                        "(<i>Companion</i>, F7 by default); it applies right away."))

    @classmethod
    def _build_music_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """
        What happens to other sounds while the microphone is open — both when
        you dictate (``media``) and when the companion talks (the two ducking
        boxes). They were on two tabs, one of them a prerequisite of the other.
        Written by ``_on_apply_music`` alone.
        """
        cfg = config_module.CFG
        body = cls._group(
            vbox, _("Music and other sounds"),
            _("A classic Bluetooth headset pauses every player when its "
            "microphone opens, and nothing resumes them. Lowering the volume "
            "instead only shows on speakers, a wired headset or a Bluetooth "
            "LE Audio headset — on a classic one it behaves like resume."))

        cls._media_combo = Gtk.ComboBoxText()
        for mid, label in (("off", N_("Leave it alone")),
                           ("resume", N_("Resume playback afterwards")),
                           ("duck", N_("Lower every other sound, then restore it and resume"))):
            cls._media_combo.append(mid, _(label))
        cls._media_combo.set_active_id(cfg.MEDIA)
        cls._field(body, _("While recording"), cls._media_combo, labels)

        cls._media_duck_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        cls._media_duck_scale.set_value(round(cfg.MEDIA_DUCK_VOLUME * 100))
        cls._media_duck_scale.set_draw_value(True)
        cls._media_duck_scale.set_value_pos(Gtk.PositionType.RIGHT)
        cls._field(body, _("Lowered volume (%)"), cls._media_duck_scale, labels)

        cls._comp_duck_ai = Gtk.CheckButton(label=_("Lower other sounds while the companion speaks"))
        cls._comp_duck_ai.set_active(bool(cfg.COMPANION_DUCK_ON_ASSISTANT))
        cls._field(body, _("While the companion talks"), cls._comp_duck_ai, labels)
        cls._comp_duck_user = Gtk.CheckButton(label=_("Lower other sounds while I speak"))
        cls._comp_duck_user.set_active(bool(cfg.COMPANION_DUCK_ON_USER))
        cls._field(body, "", cls._comp_duck_user, labels)

        cls._note(body, _("Lowering only happens with <i>Lower every other sound</i> above."))
        cls._media_combo.connect("changed", lambda _w: cls._update_media_sensitivity())
        cls._update_media_sensitivity()

        cls._music_status = Gtk.Label()
        cls._actions(body, cls._on_apply_music, cls._music_status)

    @classmethod
    def _build_mic_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """The capture device, for dictation, conversations and the companion alike."""
        body = cls._group(
            vbox, _("Input device"),
            _("The input every mode listens to: dictation, conversations and the "
              "companion's wake word. Takes effect at the next recording."))

        # "System default" + every detected capture device.
        cls._mic_combo = Gtk.ComboBoxText()
        cls._mic_combo.append("", _("System default"))
        from loquivox.services.audio import list_input_devices
        for name in list_input_devices():
            cls._mic_combo.append(name, name)
        # Restore the saved choice; if it's set but not currently present
        # (mic unplugged), still show it so the selection isn't silently lost.
        if not cls._mic_combo.set_active_id(config_module.CFG.INPUT_DEVICE):
            if config_module.CFG.INPUT_DEVICE:
                cls._mic_combo.append(config_module.CFG.INPUT_DEVICE, _("{v0} (not connected)").format(v0=config_module.CFG.INPUT_DEVICE))
                cls._mic_combo.set_active_id(config_module.CFG.INPUT_DEVICE)
            else:
                cls._mic_combo.set_active(0)
        cls._field(body, _("Microphone"), cls._mic_combo, labels)

        cls._mic_status = Gtk.Label()
        cls._actions(body, cls._on_apply_mic, cls._mic_status)

    @classmethod
    def _on_apply_mic(cls, _btn) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        try:
            update_section("transcription",
                           {"input_device": cls._mic_combo.get_active_id() or ""})
        except ConfigWriteError as e:
            cls._mic_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        cls._mic_status.set_markup(_("<small>✓ Saved — used from the next recording.</small>"))

    @classmethod
    def _on_apply_music(cls, _btn) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        try:
            # Not a companion key: services/media.py reads it for dictation too.
            update_section("transcription", {
                "media": cls._media_combo.get_active_id() or "resume",
                "media_duck_volume": round(cls._media_duck_scale.get_value() / 100, 2),
            })
            update_section("companion", {
                "duck_on_assistant": cls._comp_duck_ai.get_active(),
                "duck_on_user": cls._comp_duck_user.get_active(),
            })
        except ConfigWriteError as e:
            cls._music_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        cls._music_status.set_markup(_("<small>✓ Saved — applies from the next recording.</small>"))

    @classmethod
    def _update_media_sensitivity(cls) -> None:
        """Lowering is one choice of ``media``: its volume and both boxes need it."""
        ducking = (cls._media_combo is not None
                   and cls._media_combo.get_active_id() == "duck")
        for w in (cls._media_duck_scale, cls._comp_duck_ai, cls._comp_duck_user):
            if w is not None:
                w.set_sensitive(ducking)

    _wake_installed = False  # openwakeword installed since the last Apply

    @classmethod
    def _on_install_wake(cls, btn: Gtk.Button) -> None:
        """Install openwakeword and fetch its model files, off the GTK thread."""
        import threading

        btn.set_sensitive(False)
        btn.set_label(_("Installing…"))

        wake = cls._comp_wake.get_active_id()

        def work():
            from loquivox.services import presence
            # openwakeword's own requirements include tflite-runtime (no wheel
            # for 3.14): take what the ONNX engine really imports, then the
            # package itself without dependencies.
            ok, msg = cls._pip_install(*presence.WAKE_DEPS)
            if ok:
                ok, msg = cls._pip_install("openwakeword>=0.6", "--no-deps")
            if ok:
                try:
                    from openwakeword.utils import download_models
                    download_models()
                except Exception as e:
                    ok, msg = False, _("model download failed: {e}").format(e=e)
            if ok:  # a ✓ rests on a real import and a loaded model, nothing else
                msg = presence.wake_problem(wake)
                ok = not msg

            def done():
                btn.set_sensitive(True)
                if ok:
                    cls._wake_installed = True
                    btn.set_visible(False)
                    cls._comp_status.set_markup(
                        _("<small>✓ openwakeword installed — Apply to load it.</small>"))
                else:
                    btn.set_label(_("Install openwakeword"))
                    cls._comp_status.set_markup(
                        _("<small>❌ install failed: {v0}</small>").format(v0=GLib.markup_escape_text(msg)))
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def _on_apply_companion(cls, _btn) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        wake = cls._comp_wake.get_active_id()
        changed = wake != config_module.CFG.COMPANION_WAKE_WORD
        buffer = cls._comp_instructions.get_buffer()
        instructions = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), False).strip()
        try:
            update_section("companion", {
                "enabled": cls._comp_enabled.get_active(),
                "wake_word": wake,
                "click_mode": cls._comp_click.get_active_id(),
                "silence_on_lock": cls._comp_lock.get_active(),
                "silence_idle_min": int(cls._comp_idle.get_value()),
                "bubble_style": cls._comp_style.get_active_id(),
                "source_silence": cls._comp_source_silence.get_value(),
                "live_voice": cls._comp_voice.get_active_id() or "",
                "live_delegation": cls._comp_delegation.get_active_id() or "",
                "live_backend_model": cls._comp_backend.get_text().strip(),
                "live_web_search": cls._comp_web.get_active_id() or "",
                "instructions": instructions,
            })
        except ConfigWriteError as e:
            cls._comp_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        from loquivox.handlers import companion
        companion.restyle()
        saved = _("<small>✓ Saved — the wake word and the click apply now, "
                  "the startup choice at the next launch.</small>")
        # Loading the model is slow and every Apply runs every tab's writer:
        # only reload when the word changed or openwakeword was just installed.
        if not (changed or cls._wake_installed):
            cls._comp_status.set_markup(saved)
            return
        cls._wake_installed = False
        cls._comp_status.set_markup(_("<small>Loading the wake word…</small>"))

        def work():
            from loquivox.handlers import companion
            try:
                companion.set_wake_word(wake)
                text = saved
            except Exception as e:
                text = _("<small>❌ wake word: {v0}</small>").format(v0=GLib.markup_escape_text(str(e)))
            GLib.idle_add(lambda: cls._comp_status.set_markup(text) or False)

        import threading
        threading.Thread(target=work, daemon=True).start()

    # -----------------------------------------------------------------
    # Plugin cards: what the conversation can reach beyond the models
    # -----------------------------------------------------------------
    @classmethod
    def _build_plugin_cards(cls, vbox: Gtk.Box, tab: str, labels: Gtk.SizeGroup,
                            first: bool = False) -> None:
        """
        One card per plugin that asked for this page ("tab/sub"). A plugin that
        names only the tab ("tools") lands on its first page (``first``).

        The dialog knows none of them by name: a plugin hands over a builder
        (see ``services/plugins.py``) and the tab it belongs on, and gets the
        page's helpers and its label size group, so its card lines up with the
        ones written by hand elsewhere. Every plugin is offered a card,
        enabled or not: a card is how you turn one on.
        """
        from loquivox.services import plugins

        for plugin in plugins.all_plugins():
            if plugin.settings is None or not (
                    plugin.tab == tab or (first and plugin.tab == tab.split("/")[0])):
                continue
            try:
                plugin.settings(cls, vbox, labels)
            except Exception as e:   # a plugin must never cost the user Settings
                print(f"⚠️  Settings for plugin {plugin.name} not built: {e}")

    @classmethod
    def _build_roles_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """
        One row per job a model does: its provider, then its model. A row left
        on "Same as chat" follows the chat's pair. The lists come from the
        provider's own API, so a model the account no longer has stops being
        offered; the search row, a different kind of model, has its own two.
        """
        cfg = config_module.CFG
        ai_key = cfg.HOTKEY_DEFS['ai'][0]
        talk_key = cfg.HOTKEY_DEFS['talk'][0]
        body = cls._group(
            vbox, _("Models by role"),
            _("One row per job, a provider then a model. The agent answers "
              "{ai_key}, {talk_key} and the companion and runs their tools; the "
              "chat model writes the final text and refines your dictation; "
              "vision reads the screen; search looks facts up on the web; the "
              "memory judge decides what the companion no longer repeats. A "
              "row left on “Same as chat” follows the chat model.").format(
                  ai_key=ai_key, talk_key=talk_key))

        def provider_combo(ids, current, like_chat):
            combo = Gtk.ComboBoxText()
            if like_chat:
                combo.append("", _("Same as chat"))
            for pid in ids:
                combo.append(pid, cls._PROVIDER_NAMES.get(pid, pid.title()))
            combo.set_active_id(current if current in ids else ("" if like_chat else ids[0]))
            combo.set_size_request(130, -1)
            cls._elide(combo)
            return combo

        def model_combo(text, placeholder=""):
            combo = Gtk.ComboBoxText.new_with_entry()   # editable: a new model id still works
            entry = combo.get_child()
            entry.set_width_chars(10)
            entry.set_text(text)
            if placeholder:
                entry.set_placeholder_text(placeholder)
            cls._elide(combo)
            return combo

        def row(label, provider, model):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.pack_start(provider, False, False, 0)
            box.pack_start(model, True, True, 0)
            cls._field(body, label, box, labels)

        providers = list(cfg.AI_PROVIDERS)
        cls._agent_provider = provider_combo(providers, STATE.ai_agent_provider, True)
        cls._agent_model = model_combo(STATE.ai_agent_model, _("Same as chat"))
        row(_("Answers"), cls._agent_provider, cls._agent_model)

        cls._chat_provider = provider_combo(providers, STATE.ai_provider, False)
        cls._chat_model = model_combo("")
        row(_("Final text"), cls._chat_provider, cls._chat_model)

        # Vision has no provider of its own: it reads the screen on the chat's.
        cls._vision_model = model_combo("")
        same = Gtk.Label(label=_("Same as chat"))
        same.set_xalign(0)
        same.set_size_request(130, -1)
        same.get_style_context().add_class("dim-label")
        row(_("Vision"), same, cls._vision_model)

        cls._research_provider = provider_combo(
            list(cfg.RESEARCH_PROVIDERS), cfg.RESEARCH_PROVIDER, False)
        cls._research_model = model_combo(cfg.MODEL_RESEARCH)
        for mid in cfg.RESEARCH_DEFAULT_MODELS.values():
            cls._research_model.append_text(mid)
        row(_("Web lookup"), cls._research_provider, cls._research_model)

        cls._judge_provider = provider_combo(providers, STATE.ai_judge_provider, True)
        cls._judge_model = model_combo(STATE.ai_judge_model, _("Same as chat"))
        row(_("Memory judge"), cls._judge_provider, cls._judge_model)

        cls._note(body, _("Search is switched on in <b>Tools › Search</b>."))
        cls._models_status = Gtk.Label()
        cls._actions(body, cls._on_apply_models, cls._models_status)

        cls._load_models_async()
        cls._chat_provider.connect("changed", lambda _c: cls._load_models_async())
        cls._agent_provider.connect("changed", lambda _c: cls._load_models_async())
        cls._judge_provider.connect("changed", lambda _c: cls._load_models_async())
        cls._research_provider.connect("changed", cls._on_research_provider_changed)

    @classmethod
    def _on_research_provider_changed(cls, combo: Gtk.ComboBoxText) -> None:
        """A model id belongs to its provider: offer the new provider's default."""
        cls._research_model.get_child().set_text(
            config_module.CFG.RESEARCH_DEFAULT_MODELS.get(combo.get_active_id() or "", ""))

    # -----------------------------------------------------------------
    # Presets: the four slots, filled in one click
    # -----------------------------------------------------------------
    #: label → (description, {section: {key: value}}, {STATE attr: value})
    PRESETS: tuple = (
        (N_("Fast"), N_("Cloud cascade — live transcription, cloud voice, and your chat "
                 "provider answers (set in Models by role). The default, ~1.8s "
                 "before you hear a reply."),
         {"talk": {"engine": "cascade"}, "transcription": {"backend": "openai_realtime"}},
         {"tts_model": "gpt-4o-mini-tts", "tts_voice": "marin"}),
        (N_("Natural"), N_("Speech to speech — one OpenAI Realtime session holds the "
                    "conversation, ~1.0s and it keeps the prosody. Costs more, "
                    "and the chat model no longer answers in talk mode."),
         {"talk": {"engine": "realtime"}}, {}),
        (N_("Private"), N_("Local voice and offline transcription — nothing but the "
                    "answer itself leaves the machine. Needs piper-tts and the "
                    "whisper.cpp binary; anything missing falls back to cloud."),
         {"talk": {"engine": "cascade"}, "transcription": {"backend": "whispercpp"}},
         {"tts_model": "piper", "tts_voice": "fr_FR-siwis-medium"}),
    )

    @classmethod
    def _build_presets(cls, vbox: Gtk.Box) -> None:
        """One row of buttons that fill the other tabs' fields at once."""
        body = cls._group(
            vbox, _("Presets"),
            _("Fill the engine, the transcription and the voice in one click — "
            "each lives on its own tab. Hover a preset to see what it picks; "
            "nothing is saved until you click Apply, and everything stays "
            "editable."))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_homogeneous(True)
        for name, tooltip, sections, state in cls.PRESETS:
            button = Gtk.Button(label=_(name))
            button.set_tooltip_text(_(tooltip))
            button.connect("clicked", cls._on_preset, name, sections, state)
            row.pack_start(button, True, True, 0)
        body.pack_start(row, False, False, 0)

        cls._preset_status = Gtk.Label()
        cls._preset_status.set_halign(Gtk.Align.START)
        cls._preset_status.set_xalign(0)
        cls._preset_status.set_line_wrap(True)
        cls._preset_status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        cls._preset_status.set_max_width_chars(58)
        cls._preset_status.get_style_context().add_class("dim-label")
        cls._preset_status.set_no_show_all(True)   # no blank line before a click
        body.pack_start(cls._preset_status, False, False, 0)

    @classmethod
    def _fill_preset(cls, sections: dict, state: dict) -> None:
        """Put a preset's values in the fields they belong to; the writers save them."""
        engine = sections.get("talk", {}).get("engine")
        if engine:
            cls._engine_combo.set_active_id(engine)
        backend = sections.get("transcription", {}).get("backend")
        for i, (bid, *_rest) in enumerate(cls._BACKENDS):
            if bid == backend:
                cls._backend_combo.set_active(i)
        if "tts_model" in state:
            cls._tts_engine_combo.set_active_id(state["tts_model"])   # refills the voices
        if "tts_voice" in state:
            cls._voice_combo.set_active_id(state["tts_voice"])

    @classmethod
    def _on_preset(cls, _btn: Gtk.Button, name: str, sections: dict, state: dict) -> None:
        cls._fill_preset(sections, state)
        cls._preset_status.set_markup(
            _("<small>{v0} filled in — click Apply to save it; it takes effect on "
              "the next talk session.</small>").format(v0=GLib.markup_escape_text(_(name))))
        cls._preset_status.show()

    # -----------------------------------------------------------------
    # Conversation engine (talk mode): cascade or native speech-to-speech
    # -----------------------------------------------------------------
    @classmethod
    def _build_engine_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        body = cls._group(
            vbox, _("How a conversation is held"),
            _("How a conversation is held. The cascade chains the transcription, "
            "the chat model and the voice — each swappable, local or cloud, "
            "on their own tabs. Realtime hands the whole conversation to one model that "
            "hears and answers directly: faster and more natural, but the chat "
            "model no longer answers. Live splits the difference — an OpenAI "
            "voice holds the conversation and delegates the thinking, which "
            "can be your own model again. The final text is a plain completion "
            "either way."))

        cls._engine_combo = Gtk.ComboBoxText()
        for eid in config_module.CFG.TALK_ENGINES:
            cls._engine_combo.append(eid, _({
                "cascade": N_("Cascade — transcribe, answer, speak"),
                "realtime": N_("Realtime — speech to speech (OpenAI)"),
                "live": N_("Live — OpenAI voice, delegated thinking"),
            }.get(eid, eid.title())))
        cls._engine_combo.set_active_id(
            config_module.CFG.TALK_ENGINE if config_module.CFG.TALK_ENGINE in config_module.CFG.TALK_ENGINES else "cascade")
        cls._field(body, _("Conversation engine"), cls._engine_combo, labels)

        cls._realtime_model = Gtk.Entry()   # ids change often — free text
        cls._realtime_model.set_text(config_module.CFG.TALK_REALTIME_MODEL)
        cls._field(body, _("Realtime model"), cls._realtime_model, labels)

        # A closed list, not free text: the API rejects an unknown voice, and
        # it does so only once the session is already opening.
        cls._realtime_voice = Gtk.ComboBoxText()
        for vid in config_module.CFG.TALK_REALTIME_VOICES:
            cls._realtime_voice.append(vid, vid.title())
        cls._realtime_voice.set_active_id(
            config_module.CFG.TALK_REALTIME_VOICE if config_module.CFG.TALK_REALTIME_VOICE
            in config_module.CFG.TALK_REALTIME_VOICES else config_module.CFG.TALK_REALTIME_VOICES[0])
        cls._realtime_test = Gtk.Button(label=_("▶ Test"))
        cls._realtime_test.set_tooltip_text(
            _("Preview this voice (same voice, spoken by the TTS model)"))
        cls._realtime_test.connect("clicked", cls._on_test_realtime_voice)
        cls._field(body, _("Realtime voice"), cls._realtime_voice, labels, extra=cls._realtime_test)

        cls._live_voice = Gtk.ComboBoxText()
        for vid in config_module.CFG.TALK_LIVE_VOICES:
            cls._live_voice.append(vid, vid.title())
        cls._live_voice.set_active_id(
            config_module.CFG.TALK_LIVE_VOICE if config_module.CFG.TALK_LIVE_VOICE
            in config_module.CFG.TALK_LIVE_VOICES else config_module.CFG.TALK_LIVE_VOICES[0])
        cls._live_test = Gtk.Button(label=_("▶ Test"))
        cls._live_test.set_tooltip_text(
            _("Preview this voice (same voice, spoken by the TTS model)"))
        cls._live_test.connect("clicked", cls._on_test_live_voice)
        cls._field(body, _("Live voice"), cls._live_voice, labels, extra=cls._live_test)

        # The setting the whole engine turns on: who thinks. Spelled out rather
        # than left to "auto", because forcing a mode is how the two are
        # compared on the same model.
        cls._live_delegation = Gtk.ComboBoxText()
        for did in config_module.CFG.TALK_LIVE_DELEGATIONS:
            cls._live_delegation.append(
                did, _(cls._DELEGATION_LABELS.get(did, did.title())))
        cls._live_delegation.set_active_id(
            config_module.CFG.TALK_LIVE_DELEGATION
            if config_module.CFG.TALK_LIVE_DELEGATION in config_module.CFG.TALK_LIVE_DELEGATIONS
            else "auto")
        cls._field(body, _("Delegate thinking to"), cls._live_delegation, labels)

        cls._live_backend = Gtk.Entry()     # ids change often — free text
        cls._live_backend.set_text(config_module.CFG.TALK_LIVE_BACKEND_MODEL)
        cls._live_backend.set_tooltip_text(
            _("Only used when the thinking is delegated to OpenAI's backend"))
        cls._field(body, _("OpenAI backend model"), cls._live_backend, labels)

        # One or the other, never both: two searches on the same table is a
        # coin toss between tools that answer to different settings, and makes
        # the two impossible to compare.
        cls._live_web_search = Gtk.ComboBoxText()
        for wid in config_module.CFG.TALK_LIVE_WEB_SEARCHES:
            cls._live_web_search.append(
                wid, _(cls._WEB_SEARCH_LABELS.get(wid, wid.title())))
        cls._live_web_search.set_active_id(
            config_module.CFG.TALK_LIVE_WEB_SEARCH
            if config_module.CFG.TALK_LIVE_WEB_SEARCH in config_module.CFG.TALK_LIVE_WEB_SEARCHES
            else "plugin")
        cls._live_web_search.set_tooltip_text(
            _("Which web search the OpenAI backend uses. Choosing theirs stands "
            "down the search plugin so the two never compete — and only that "
            "one: every other plugin stays on the table either way. Their "
            "search ignores your search provider and model."))
        cls._field(body, _("Web search"), cls._live_web_search, labels)

        # Each engine's own fields only mean anything when it is the one
        # running — greying them out is the shortest answer to "why are there
        # three voices here". The cascade's "end turns on meaning" box is on
        # this tab too, so it follows the same combo (see _build_talk_section).
        cls._engine_combo.connect("changed", cls._on_talk_engine_changed)
        cls._live_delegation.connect("changed", cls._on_talk_engine_changed)
        cls._on_talk_engine_changed(cls._engine_combo)

        engine_status = Gtk.Label()
        cls._actions(body, cls._on_apply_engine, engine_status)
        # The engine's own feedback used to land in the preset card's status
        # line, two groups above the setting that caused it.
        cls._engine_status = engine_status

    @classmethod
    def _on_talk_engine_changed(cls, _combo: Gtk.ComboBoxText) -> None:
        """
        Show each engine only the fields it reads.

        Driven by the engine combo *and* the delegation one, because the
        backend model is a field of a field: it only means anything when Live
        is running and its thinking goes to OpenAI.
        """
        # Lazy, like every service import in this file: live_talk pulls numpy
        # and the sound card, and the dialog opens on machines where talk mode
        # never runs.
        from loquivox.services.live_talk import resolve_delegation

        engine = (cls._engine_combo.get_active_id() or "cascade"
                  if cls._engine_combo is not None else "cascade")
        # The companion has its own Live fields (Companion tab): these are F4/F6's.
        live = engine == "live"
        to_openai = (cls._live_delegation is not None
                     and resolve_delegation(cls._live_delegation.get_active_id())
                     == "responses")
        for widget, on in (
                (cls._talk_semantic_check, engine == "cascade"),
                (cls._talk_speak_check, engine == "cascade"),
                (cls._realtime_model, engine == "realtime"),
                (cls._realtime_voice, engine == "realtime"),
                (cls._realtime_test, engine == "realtime"),
                (cls._live_voice, live),
                (cls._live_test, live),
                (cls._live_delegation, live),
                (cls._live_backend, live and to_openai),
                (cls._live_web_search, live and to_openai),
        ):
            if widget is not None:
                widget.set_sensitive(on)

    #: a line to try a voice on, per transcription language
    _SAMPLES = {
        "fr": "Bonjour. Voici à quoi je ressemble — est-ce que cette voix vous convient ?",
        "es": "Hola. Así es como sueno. ¿Le conviene esta voz?",
        "de": "Guten Tag. So klinge ich. Passt Ihnen diese Stimme?",
        "": "Hello. This is what I sound like — does this voice work for you?",
    }

    @classmethod
    def _sample_line(cls) -> str:
        """The test line, in whatever language is being transcribed."""

        lang = (config_module.CFG.WHISPER_LANGUAGE or "").strip().lower()[:2]
        return cls._SAMPLES.get(lang, cls._SAMPLES[""])

    @classmethod
    def _on_test_voice(cls, _btn: Gtk.Button) -> None:
        """Speak a line with whatever the combos show, saving nothing."""
        from loquivox.services.tts import TTSService

        model = cls._tts_engine_combo.get_active_id() if cls._tts_engine_combo else ""
        voice = cls._voice_combo.get_active_id() if cls._voice_combo else ""
        TTSService.preview(cls._sample_line(), model=model, voice=voice)

    @classmethod
    def _on_test_realtime_voice(cls, _btn: Gtk.Button) -> None:
        """
        Preview the Realtime voice through the TTS model.

        Same voice names and the same timbre, a different model — opening a
        Realtime session to hear what "cedar" sounds like would cost a call and
        several seconds for one word.
        """
        from loquivox.services.tts import TTSService

        voice = cls._realtime_voice.get_active_id() if cls._realtime_voice else ""
        TTSService.preview(cls._sample_line(), model="gpt-4o-mini-tts", voice=voice)

    @classmethod
    def _on_test_live_voice(cls, _btn: Gtk.Button) -> None:
        """
        Preview the Live voice through the TTS model, like the Realtime one.

        Twelve of the twenty-two are new here and the TTS model does not speak
        them, so those fall back to the default rather than failing silently:
        no preview is a better answer than another voice's.
        """
        from loquivox.services.tts import TTSService

        voice = cls._live_voice.get_active_id() if cls._live_voice else ""
        if voice not in config_module.CFG.TALK_REALTIME_VOICES:
            cls._engine_status.set_markup(
                _("<small>⚠️  No preview for {voice} — it is a Live-only "
                   "voice.</small>").format(voice=voice))
            return
        TTSService.preview(cls._sample_line(), model="gpt-4o-mini-tts", voice=voice)

    @classmethod
    def _on_apply_engine(cls, _btn: Gtk.Button) -> None:
        """Write [talk] engine/model/voice and reload — talk mode reads CFG live."""
        from loquivox.config_io import ConfigWriteError, update_section

        engine = cls._engine_combo.get_active_id() or "cascade"
        try:
            update_section("talk", {
                "engine": engine,
                "realtime_model": cls._realtime_model.get_text().strip(),
                "realtime_voice": cls._realtime_voice.get_active_id() or "marin",
                "live_voice": cls._live_voice.get_active_id() or "marin",
                "live_delegation": cls._live_delegation.get_active_id() or "auto",
                "live_backend_model": cls._live_backend.get_text().strip(),
                "live_web_search": cls._live_web_search.get_active_id() or "plugin",
            })
        except ConfigWriteError as e:
            cls._engine_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        print(f"🗣️  Talk engine: {engine}")
        cls._engine_status.set_markup(
            _("<small>✓ {engine} — takes effect on the next talk "
               "session.</small>").format(engine=engine))

    # -----------------------------------------------------------------
    # Voice (TTS): which engine speaks, and with which voice
    # -----------------------------------------------------------------
    @classmethod
    def _build_voice_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        key = config_module.CFG.HOTKEY_DEFS['tts'][0]
        body = cls._group(
            vbox, _("Spoken voice"),
            _("A separate engine that speaks written text, so it works "
              "everywhere and can be local. It is not the Realtime or Live "
              "voice (Conversation tab): those are the model speaking for "
              "itself."))

        cls._tts_enabled_check = Gtk.CheckButton(label=_("Read chat answers aloud"))
        cls._tts_enabled_check.set_active(bool(STATE.tts_enabled))
        cls._tts_enabled_check.set_tooltip_text(
            _("The same switch as {key}. It also makes conversations speak "
              "their replies when the box below is not ticked.").format(key=key))
        cls._field(body, _("Chat answers"), cls._tts_enabled_check, labels)

        cls._talk_speak_check = Gtk.CheckButton(
            label=_("Read conversation replies aloud, even with the switch above off"))
        cls._talk_speak_check.set_active(bool(config_module.CFG.TALK_SPEAK_REPLIES))
        cls._talk_speak_check.set_tooltip_text(
            _("Cascade engine only: Realtime and Live speak with their own voice. "
              "Unticked, a conversation speaks only while the switch above is on."))
        cls._field(body, _("Conversations"), cls._talk_speak_check, labels)

        cls._tts_engine_combo = Gtk.ComboBoxText()
        for model in config_module.CFG.TTS_ENGINES:
            cls._tts_engine_combo.append(model, _(cls._ENGINE_LABELS.get(model, model)))
        cls._tts_engine_combo.set_active_id(
            STATE.tts_model if STATE.tts_model in config_module.CFG.TTS_ENGINES
            else next(iter(config_module.CFG.TTS_ENGINES)))
        cls._field(body, _("Speech engine"), cls._tts_engine_combo, labels)

        cls._voice_combo = Gtk.ComboBoxText()
        cls._fill_voices()
        test = Gtk.Button(label=_("▶ Test"))
        test.set_tooltip_text(_("Speak a line with the selected engine and voice"))
        test.connect("clicked", cls._on_test_voice)
        cls._field(body, _("Speech voice"), cls._voice_combo, labels, extra=test)

        cls._note(body,
                  _("Piper needs no key and no network: its voice downloads once "
                  "(~60 MB) and is flatter than the cloud's."))
        # Connected last: filling the voices above must not fire the handler.
        cls._tts_engine_combo.connect("changed", lambda _c: cls._fill_voices())

        cls._voice_status = Gtk.Label()
        cls._actions(body, cls._on_apply_voice, cls._voice_status)

    @classmethod
    def _on_apply_voice(cls, _btn) -> None:
        """Save the voice (settings.json) and the conversation switch ([talk])."""
        from loquivox.config_io import ConfigWriteError, update_section

        try:
            update_section("talk", {"speak_replies": bool(cls._talk_speak_check.get_active())})
        except ConfigWriteError as e:
            cls._voice_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        enabled = cls._tts_enabled_check.get_active()
        refresh = enabled != STATE.tts_enabled
        STATE.tts_enabled = enabled
        STATE.tts_model = cls._tts_engine_combo.get_active_id() or STATE.tts_model
        STATE.tts_voice = cls._voice_combo.get_active_id() or STATE.tts_voice
        SettingsManager.save(STATE)
        if refresh:
            from loquivox.managers.chat import ChatManager
            ChatManager.refresh_overlay()
        print(f"🔊 Voice: {STATE.tts_model} / {STATE.tts_voice}, chat read-aloud "
              f"{'on' if enabled else 'off'}")
        cls._voice_status.set_markup(_("<small>✓ Saved.</small>"))

    _PROVIDER_NAMES = {"groq": "Groq", "openai": "OpenAI", "anthropic": "Anthropic"}

    @classmethod
    def _load_models_async(cls) -> None:
        """Fill both combos from ``models.list()`` — off-thread, it is a network call."""
        import threading
        provider = cls._chat_provider.get_active_id() or STATE.ai_provider
        agent_pid = (cls._agent_provider.get_active_id() if cls._agent_provider else "") or provider
        judge_pid = (cls._judge_provider.get_active_id() if cls._judge_provider else "") or provider
        cls._set_models_status(_("⏳ Listing {provider} models…").format(provider=provider))

        def work():
            from gi.repository import GLib
            from loquivox.api import get_ai_client

            def listing(pid):
                try:
                    return sorted(m.id for m in get_ai_client(pid).models.list().data), None
                except Exception as e:
                    return [], str(e)

            ids, error = listing(provider)
            agent_ids, agent_error = (ids, error) if agent_pid == provider else listing(agent_pid)
            judge_ids, judge_error = (ids, error) if judge_pid == provider else listing(judge_pid)

            def done():
                for combo, current, found in (
                        (cls._chat_model, STATE.ai_chat_model, ids),
                        (cls._vision_model, STATE.ai_vision_model, ids),
                        (cls._agent_model, STATE.ai_agent_model, agent_ids),
                        (cls._judge_model, STATE.ai_judge_model, judge_ids)):
                    if combo is None:
                        continue
                    typed = combo.get_child().get_text() or current
                    combo.remove_all()
                    for mid in found:
                        combo.append_text(mid)
                    combo.get_child().set_text(typed)
                failure = error or agent_error or judge_error
                if failure:
                    cls._set_models_status(_("⚠️ Could not list models — {v0}").format(v0=failure[:120]))
                else:
                    cls._set_models_status(
                        _("✓ {v0} models available on {provider}. Pick or type one, then "
                                   "Apply. An empty model follows the chat model.").format(
                            v0=len(ids), provider=provider))
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def _set_models_status(cls, markup: str) -> None:
        if cls._models_status is not None:
            cls._models_status.set_markup(f"<small>{GLib.markup_escape_text(markup)}</small>")

    @classmethod
    def _on_apply_models(cls, _btn: Gtk.Button) -> None:
        """Save the five roles: settings.json for the chat's, config.toml for the search."""
        from loquivox.config_io import ConfigWriteError, update_section

        text = lambda combo: combo.get_child().get_text().strip()
        chat, vision = text(cls._chat_model), text(cls._vision_model)
        if not chat or not vision:
            cls._set_models_status(_("⚠️ Both models need an id."))
            return
        search = text(cls._research_model)
        if not search and config_module.CFG.TALK_RESEARCH:
            cls._set_models_status(_("⚠️ The search needs a model id."))
            return
        try:
            update_section("models", {
                "research_provider": cls._research_provider.get_active_id() or "openai",
                "research": search})
        except ConfigWriteError as e:
            cls._set_models_status(f"❌ {e}")
            return
        config_module.reload_config()
        STATE.ai_provider = cls._chat_provider.get_active_id() or STATE.ai_provider
        STATE.ai_chat_model, STATE.ai_vision_model = chat, vision
        STATE.ai_agent_provider = cls._agent_provider.get_active_id() or ""
        STATE.ai_agent_model = text(cls._agent_model)
        STATE.ai_judge_provider = cls._judge_provider.get_active_id() or ""
        STATE.ai_judge_model = text(cls._judge_model)
        SettingsManager.save(STATE)
        print(f"🧠 Models applied: chat={chat} vision={vision} ({STATE.ai_provider})")
        cls._set_models_status(_("✓ Applied — chat {chat} · vision {vision} · agent {agent}.").format(
            chat=chat, vision=vision,
            agent=STATE.ai_agent_model or _("Same as chat").lower()))

    # -----------------------------------------------------------------
    # Appearance tab: TTS voice + colour scheme gallery + overlay preview
    # -----------------------------------------------------------------
    @classmethod
    def _build_appearance_page(cls, vbox: Gtk.Box) -> None:
        # The spoken voice lives in the Sound tab.
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        # The preview leads the page: the two groups below only make sense as
        # changes to the thing being previewed, so it goes first, once.
        body = cls._group(
            vbox, _("Recording overlay"),
            _("The bubble that appears while you record. Everything here "
            "is previewed live below; the style and the two columns apply to "
            "the next recording."))

        cls._preview_area = Gtk.DrawingArea()
        cls._preview_area.set_size_request(-1, 74)
        cls._preview_area.connect("draw", cls._draw_overlay_preview)
        body.pack_start(cls._preview_area, False, False, 0)
        # Loop a gentle animation so the preview shows the live effects.
        if cls._preview_timer is None:
            cls._preview_timer = GLib.timeout_add(40, cls._tick_preview)

        # Overlay style (pill vs classic) — shown live in the preview above.
        style_combo = Gtk.ComboBoxText()
        _STYLE_LABELS = {
            "pill": N_("Pill — waveform capsule"),
            "classic": N_("Classic — icon + bars"),
        }
        for sid in config_module.CFG.OVERLAY_STYLES:
            style_combo.append(sid, _(_STYLE_LABELS.get(sid, sid.title())))
        active_style = STATE.overlay_style if STATE.overlay_style in config_module.CFG.OVERLAY_STYLES else config_module.CFG.DEFAULT_OVERLAY_STYLE
        style_combo.set_active_id(active_style)
        style_combo.connect("changed", cls._on_overlay_style_changed)
        cls._field(body, _("Style"), style_combo, labels)

        # Hotkey hints column (widens the overlay). Applies to the next recording.
        hints_check = Gtk.CheckButton(
            label=_("Show available hotkeys next to the waveform")
        )
        hints_check.set_active(STATE.show_hints)
        hints_check.connect("toggled", cls._on_hints_toggled)
        body.pack_start(hints_check, False, False, 0)

        # Refinement chip (dictation only). Applies to the next recording.
        badge_check = Gtk.CheckButton(
            label=_("Show the refinement level applied to the dictation")
        )
        badge_check.set_active(STATE.show_refine_badge)
        badge_check.connect("toggled", cls._on_refine_badge_toggled)
        body.pack_start(badge_check, False, False, 0)

        # Colour scheme gallery — judged on the preview above.
        body = cls._group(
            vbox, _("Color scheme"),
            _("Applies immediately, to the overlay above and to the chat bubble."))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 230)
        cls._listbox = Gtk.ListBox()
        cls._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        cls._listbox.connect("row-activated", cls._on_scheme_selected)
        for name in config_module.CFG.COLOR_SCHEMES.keys():
            row = cls._create_theme_row(name)
            cls._listbox.add(row)
            if name == STATE.color_scheme:
                cls._listbox.select_row(row)
        scrolled.add(cls._listbox)
        body.pack_start(scrolled, True, True, 0)

        # Screen-edge hotkey cheat sheet (applies immediately) — a different
        # window entirely, so it gets its own group rather than trailing the
        # overlay's toggles.
        body = cls._group(
            vbox, _("Outside a recording"),
            _("Chrome that lives outside a recording. Applies immediately."))
        cls._chat_enabled_check = Gtk.CheckButton(
            label=_("Show the chat overlay (answers and conversation bubble)"))
        cls._chat_enabled_check.set_active(bool(STATE.chat_enabled))
        cls._chat_enabled_check.connect("toggled", cls._on_chat_enabled_toggled)
        body.pack_start(cls._chat_enabled_check, False, False, 0)
        bar_check = Gtk.CheckButton(
            label=_("Hotkey tab at the top of the screen (hover to expand)")
        )
        bar_check.set_active(STATE.show_hotkey_bar)
        bar_check.connect("toggled", cls._on_hotkey_bar_toggled)
        body.pack_start(bar_check, False, False, 0)

        body = cls._group(
            vbox, _("Interface language"),
            _("The language of the whole interface. Auto follows the system "
              "locale. Applies immediately; reopen Settings to see this window "
              "in the new language."))
        language = Gtk.ComboBoxText()
        for code, name in i18n.LANGUAGES:
            language.append(code, _("Auto") if code == "auto" else name)
        language.set_active_id(i18n.saved_choice())
        language.connect("changed", cls._on_language_changed)
        cls._field(body, _("Shown in"), language, labels)

    @staticmethod
    def _on_language_changed(combo: Gtk.ComboBoxText) -> None:
        """Switch the interface language now, and remember it."""
        choice = combo.get_active_id() or "auto"
        SettingsManager.update("language", choice)
        print(f"🌐 Interface language: {choice} -> {i18n.set_language(choice)}")
        from loquivox.ui.tray import TrayManager
        TrayManager.update_menu()

    @staticmethod
    def _on_chat_enabled_toggled(check: Gtk.CheckButton) -> None:
        """Show or hide the chat overlay right away, and remember it."""
        STATE.chat_enabled = check.get_active()
        print(f"💬 Chat overlay: {'on' if STATE.chat_enabled else 'off'}")
        SettingsManager.save(STATE)
        from loquivox.managers.chat import ChatManager
        if STATE.chat_enabled:
            ChatManager.refresh_overlay()
        else:
            ChatManager._destroy()
        from loquivox.ui.tray import TrayManager
        TrayManager.update_menu()

    @classmethod
    def _draw_overlay_preview(cls, widget: Gtk.DrawingArea, cr) -> bool:
        """
        Preview the recording overlay using the SAME renderer as the real bubble
        (GtkOverlay.render_content), so the preview matches exactly.
        """
        from loquivox.ui.recording_overlay import GtkOverlay

        scheme = config_module.CFG.COLOR_SCHEMES.get(STATE.color_scheme, config_module.CFG.COLOR_SCHEMES[config_module.CFG.DEFAULT_SCHEME])
        badge = GtkOverlay.refine_badge_for("dictation")
        bw, bh = GtkOverlay.width(badge), config_module.CFG.OVERLAY_HEIGHT
        aw, ah = widget.get_allocated_width(), widget.get_allocated_height()

        # Looping, calm waveform like the overlay's idle/recording motion.
        t = cls._preview_tick
        bars = [0.12 + 0.62 * (0.5 + 0.5 * math.sin(t * 0.13 + i * 0.5))
                for i in range(GtkOverlay.NUM_BARS)]

        # Desktop UI font (portable), same as the overlay.
        settings = Gtk.Settings.get_default()
        fontname = (settings.get_property("gtk-font-name") if settings else None) or "Sans 10"
        family = Pango.FontDescription(fontname).get_family() or "Sans"

        # Fit, then centre: with the hint column on, the real bubble is wider
        # than the card, and centring alone drew half of it off the edge.
        scale = min(1.0, aw / bw) if bw else 1.0
        cr.translate((aw - bw * scale) / 2, max(0, (ah - bh * scale) / 2))
        cr.scale(scale, scale)
        GtkOverlay.render_content(
            cr, bw, bh, scheme=scheme, mode="dictation", text=_("Listening…"),
            bars=bars, tick=t, font_family=family, transcribing=False, a=1.0,
            hints=GtkOverlay.hint_items(False, False) if STATE.show_hints else (),
            badge=badge,
        )
        return True

    # -----------------------------------------------------------------
    # Transcription section (#15)
    # -----------------------------------------------------------------
    @classmethod
    def _build_transcription_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """Backend / model / language / fallback controls, applied live."""
        body = cls._group(
            vbox, _("Transcription"),
            _("The ears: what turns your voice into text, for every mode. "
            "Applied live. The microphone is set in <b>Sound</b>."))

        # Backend selector
        cls._backend_combo = Gtk.ComboBoxText()
        current = config_module.CFG.BACKEND.strip().lower()
        active_idx = 0
        for i, (bid, label, _stream, _key, _attr) in enumerate(cls._BACKENDS):
            cls._backend_combo.append_text(_(label))
            if bid == current:
                active_idx = i
        cls._backend_combo.set_active(active_idx)
        cls._backend_combo.connect("changed", cls._on_backend_changed)
        cls._field(body, _("Backend"), cls._backend_combo, labels)

        # Model: editable combo — pick a known id or type a custom one.
        cls._model_entry = Gtk.ComboBoxText.new_with_entry()
        cls._model_entry.get_child().set_hexpand(True)
        cls._field(body, _("Transcription model"), cls._model_entry, labels)

        # Language: editable combo — common languages + "Autodetect".
        cls._lang_entry = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            cls._lang_entry.append(code, _(label) + (f" ({code})" if code else ""))
        # Select the row matching the current code, else put the raw code in.
        if not cls._lang_entry.set_active_id(config_module.CFG.WHISPER_LANGUAGE):
            cls._lang_entry.get_child().set_text(config_module.CFG.WHISPER_LANGUAGE)
        cls._field(body, _("Language"), cls._lang_entry, labels)

        # Vocabulary: each engine's own way to stop dropping/mangling words it
        # doesn't expect (Whisper `prompt`, Deepgram `keyterm`).
        cls._vocab_entry = Gtk.Entry()
        cls._vocab_entry.set_hexpand(True)
        cls._vocab_entry.set_text(config_module.CFG.VOCABULARY)
        cls._vocab_entry.set_placeholder_text(_("proper nouns, jargon, foreign words…"))
        cls._vocab_entry.set_tooltip_text(
            _("Comma-separated words the engine tends to drop or misspell — "
            "proper nouns, technical terms, foreign words. Write them in the "
            "dictation language, e.g. “Loquivox, Wayland, pull request, deploy”.\n\n"
            "Groq / whisper.cpp: sent as Whisper's prompt. "
            "Deepgram: sent as keyterms (nova-3 only). "
            "OpenAI Realtime: not supported.")
        )
        cls._field(body, _("Vocabulary"), cls._vocab_entry, labels)

        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 2)

        # Offline fallback toggle
        cls._fallback_check = Gtk.CheckButton(label=_("Fall back to whisper.cpp when a backend is unavailable"))
        cls._fallback_check.set_active(bool(config_module.CFG.FALLBACK_BACKEND))
        body.pack_start(cls._fallback_check, False, False, 0)

        # whisper.cpp availability indicator (the "is it downloaded?" check) +
        # a download button.
        whisper_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._whisper_status = Gtk.Label()
        cls._whisper_status.set_halign(Gtk.Align.START)
        cls._whisper_status.set_xalign(0)
        cls._whisper_status.set_line_wrap(True)
        cls._whisper_status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        cls._whisper_status.set_max_width_chars(44)
        cls._whisper_status.get_style_context().add_class("dim-label")
        dl_btn = Gtk.Button(label=_("Install / download"))
        dl_btn.connect("clicked", cls._on_download_whispercpp)
        whisper_row.pack_start(cls._whisper_status, True, True, 0)
        whisper_row.pack_end(dl_btn, False, False, 0)
        body.pack_start(whisper_row, False, False, 0)

        cls._trans_status = Gtk.Label()
        cls._actions(body, cls._on_apply_transcription, cls._trans_status)

        cls._sync_model_entry()  # prefill model + capability hint

    @classmethod
    def _build_recording_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """Hold the dictation key, or press it once to start and again to stop."""
        key = config_module.CFG.HOTKEY_DEFS['dictation'][0]
        body = cls._group(
            vbox, _("Recording"),
            _("How the dictation key ({key}) starts and stops a recording.").format(key=key))
        cls._toggle_mode_check = Gtk.CheckButton(
            label=_("Press once to start, again to stop (instead of holding the key)"))
        cls._toggle_mode_check.set_active(bool(STATE.toggle_mode))
        cls._field(body, _("Dictation key"), cls._toggle_mode_check, labels)
        cls._recording_status = Gtk.Label()
        cls._actions(body, cls._on_apply_recording, cls._recording_status)

    @classmethod
    def _on_apply_recording(cls, _btn) -> None:
        STATE.toggle_mode = bool(cls._toggle_mode_check.get_active())
        SettingsManager.save(STATE)
        cls._recording_status.set_markup(
            _("<small>✓ Applied — press to start, press to stop.</small>")
            if STATE.toggle_mode else
            _("<small>✓ Applied — recording while the key is down.</small>"))

    @staticmethod
    def _row_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        # A row name can be data (a rule's condition): cap it, the full text in the tooltip.
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(28)
        lbl.set_tooltip_text(text)
        return lbl

    @classmethod
    def _selected_backend(cls) -> tuple:
        """Return the _BACKENDS tuple for the currently selected combo row."""
        idx = cls._backend_combo.get_active() if cls._backend_combo else 0
        if idx < 0 or idx >= len(cls._BACKENDS):
            idx = 0
        return cls._BACKENDS[idx]

    @staticmethod
    def _combo_text(combo: Gtk.ComboBoxText) -> str:
        """Current text of an editable ComboBoxText (typed or selected)."""
        return combo.get_child().get_text().strip()

    @classmethod
    def _sync_model_entry(cls) -> None:
        """Repopulate the model combo for the selected backend + update hints."""
        bid, label, is_stream, model_key, cfg_attr = cls._selected_backend()
        combo = cls._model_entry
        combo.remove_all()
        if cfg_attr is None:  # auto — model resolved at runtime
            combo.get_child().set_text("")
            combo.set_sensitive(False)
            combo.get_child().set_placeholder_text(_("(chosen automatically)"))
        else:
            combo.set_sensitive(True)
            for m in cls._MODELS.get(bid, []):
                combo.append_text(m)
            combo.get_child().set_text(getattr(config_module.CFG, cfg_attr))

        cap = _("🔴 live transcription") if is_stream else _("📝 batch (transcribing… indicator)")
        doc = cls._MODEL_DOCS.get(bid)
        hint = cap + (_(" · models: {doc}").format(doc=_(doc)) if doc else "")
        cls._set_trans_status(hint)
        cls._refresh_whisper_status()

    @classmethod
    def _refresh_whisper_status(cls) -> None:
        """Show whether local whisper.cpp is installed and the model downloaded."""
        if cls._whisper_status is None:
            return
        from loquivox.transcription.whispercpp_backend import WhisperCppBackend
        model = config_module.CFG.WHISPERCPP_MODEL
        installed, downloaded = WhisperCppBackend(model).local_status()
        if not installed:
            msg = _("whisper.cpp: ⚪ engine not found (<tt>whisper-cli</tt> missing)")
        elif downloaded:
            msg = _("whisper.cpp: ✅ model <b>{model}</b> downloaded (offline ready)").format(model=model)
        else:
            msg = _("whisper.cpp: ⬇ model <b>{model}</b> not downloaded yet").format(model=model)
        cls._whisper_status.set_markup(f"<small>{msg}</small>")

    @classmethod
    def _on_download_whispercpp(cls, btn: Gtk.Button) -> None:
        """
        Set up local whisper.cpp entirely from the UI: pip-install the package
        if it's missing, then download + load the model — all in the background.
        """
        import threading
        btn.set_sensitive(False)
        threading.Thread(target=cls._setup_whispercpp_worker, args=(btn,), daemon=True).start()

    @classmethod
    def _setup_whispercpp_worker(cls, btn: Gtk.Button) -> None:
        from gi.repository import GLib

        from loquivox.transcription.whispercpp_backend import WhisperCppBackend

        def status(markup: str) -> None:
            GLib.idle_add(lambda: cls._whisper_status.set_markup(f"<small>{markup}</small>"))

        model = config_module.CFG.WHISPERCPP_MODEL
        backend = WhisperCppBackend(model)

        # The engine is a bundled binary (whisper-cli), not a pip package — if
        # it's missing there's nothing to install from here.
        if not backend.is_available():
            status(_("whisper.cpp: ❌ engine binary (<tt>whisper-cli</tt>) not found"))
            GLib.idle_add(lambda: btn.set_sensitive(True))
            return

        # Download the ggml model on first use (the only thing we fetch).
        if not backend.is_model_downloaded():
            status(_("whisper.cpp: ⏳ downloading model <b>{model}</b>…").format(model=model))
            try:
                backend._download_model()
            except Exception as e:
                status(_("whisper.cpp: ❌ download failed: {e}").format(e=e))
                GLib.idle_add(lambda: btn.set_sensitive(True))
                return

        GLib.idle_add(cls._refresh_whisper_status)
        GLib.idle_add(lambda: btn.set_sensitive(True))

    @classmethod
    def _on_backend_changed(cls, _combo: Gtk.ComboBoxText) -> None:
        cls._sync_model_entry()

    @classmethod
    def _set_trans_status(cls, markup: str) -> None:
        if cls._trans_status:
            cls._trans_status.set_markup(f"<small>{markup}</small>")

    @classmethod
    def _on_apply_transcription(cls, _btn: Gtk.Button) -> None:
        """Write [transcription] to config.toml and apply it live."""
        bid, label, is_stream, model_key, cfg_attr = cls._selected_backend()
        language = cls._resolve_language()
        fallback = "whispercpp" if cls._fallback_check.get_active() else ""

        values = {"backend": bid, "language": language, "fallback": fallback,
                  "vocabulary": cls._vocab_entry.get_text().strip()}
        if model_key is not None:
            model = cls._combo_text(cls._model_entry)
            if model:
                values[model_key] = model

        # 1) persist (round-trip, comments preserved)
        from loquivox.config_io import ConfigWriteError, update_section
        try:
            update_section("transcription", values)
        except ConfigWriteError as e:
            cls._set_trans_status(f"❌ {e}")
            return

        # 2) apply live: rebuild CFG + reconfigure the dispatcher
        from loquivox.transcription import get_dispatcher, reconfigure_dispatcher
        fresh = config_module.reload_config()
        reconfigure_dispatcher(fresh)

        # 3) report — and auto-install a missing streaming package if that's why.
        cls._report_backend_availability(bid)
        cls._refresh_whisper_status()
        print(f"⚙️ Transcription settings applied: {values}")

    # backend id -> (import module, pip package, env key, human name)
    _BACKEND_PKG = {
        "deepgram":        ("deepgram", "deepgram-sdk", "DEEPGRAM_API_KEY", "Deepgram"),
        "openai_realtime": ("openai",   "openai",       "OPENAI_API_KEY",   "OpenAI"),
    }

    @staticmethod
    def _module_installed(module: str) -> bool:
        import importlib.util
        return importlib.util.find_spec(module) is not None

    @classmethod
    def _report_backend_availability(cls, bid: str) -> None:
        """Status after Apply; auto-install a missing streaming package."""
        from loquivox.transcription import get_dispatcher
        active = get_dispatcher().active
        if active is None:
            cls._set_trans_status(_("⚠️ Saved. No usable backend — fallback will be used."))
            return
        if active.is_available():
            cls._set_trans_status(_("✓ Applied live — backend <b>{name}</b>.").format(name=active.name))
            return

        info = cls._BACKEND_PKG.get(bid)
        if info:
            module, pip_name, env_key, human = info
            if not cls._module_installed(module):
                cls._install_backend_pkg_async(bid, pip_name, human)
                return
            import os
            if not os.environ.get(env_key):
                cls._set_trans_status(
                    _("⚠️ {human} needs its API key — set <b>{env_key}</b> in “Models "
                               "& keys”, then Apply. (Offline fallback used meanwhile.)").format(human=human, env_key=env_key)
                )
                return
        if bid in ("whispercpp", "auto"):
            cls._set_trans_status(
                _("⚠️ Saved. whisper.cpp isn’t ready — use “Install / download” above.")
            )
            return
        cls._set_trans_status(
            _("⚠️ Saved, but <b>{name}</b> is unavailable — fallback will be "
               "used.").format(name=active.name)
        )

    @classmethod
    def _install_backend_pkg_async(cls, bid: str, pip_name: str, human: str) -> None:
        """pip-install a streaming backend's package, then re-apply."""
        import threading
        cls._set_trans_status(_("⏳ Installing {human} ({pip_name})…").format(human=human, pip_name=pip_name))

        def work():
            from gi.repository import GLib
            ok, msg = cls._pip_install(pip_name)

            def done():
                if not ok:
                    cls._set_trans_status(_("❌ {human} install failed — {msg}").format(human=human, msg=msg))
                    return False
                # Rebuild backends now that the package exists, then re-report.
                from loquivox.transcription import reconfigure_dispatcher
                reconfigure_dispatcher(config_module.reload_config())
                cls._report_backend_availability(bid)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _can_pip_install() -> bool:
        """
        Whether installing packages at runtime via pip is viable.

        True only inside a writable virtualenv. In a system/distro install
        (immutable env, PEP 668 "externally managed", or read-only
        site-packages) we must NOT shell out to pip — optional backends are
        expected to be provided by the OS package instead.
        """
        import os
        import sys
        import sysconfig

        # Not in a venv → never touch a system/managed interpreter. This is the
        # whole PEP 668 guard: inside a venv the marker lives on the *base*
        # stdlib (/usr/lib/pythonX.Y), which every venv on Arch/Debian shares,
        # so testing it here would refuse every install pip itself allows.
        if sys.prefix == sys.base_prefix:
            return False
        # site-packages must be writable.
        purelib = sysconfig.get_path("purelib") or ""
        return bool(purelib) and os.access(purelib, os.W_OK)

    # Shown when a runtime pip-install isn't possible (packaged/managed env).
    _MANAGED_ENV_HINT = N_(
        "managed install — add this optional backend via your package "
        "manager (or reinstall in a venv), not from the app"
    )

    @staticmethod
    def _pip_install(*pip_args: str):
        """Run pip install in-process venv. Returns (ok, last_output_line)."""
        import importlib
        import subprocess
        import sys
        if not SettingsDialog._can_pip_install():
            return False, _(SettingsDialog._MANAGED_ENV_HINT)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", *pip_args],
                capture_output=True, text=True, timeout=900,
            )
        except Exception as e:
            return False, str(e)
        importlib.invalidate_caches()
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, (tail[-1] if tail else _("see logs"))
        return True, "ok"

    @classmethod
    def _resolve_language(cls) -> str:
        """Map the language combo's text/selection to an ISO-639-1 code."""
        # If a known row is selected, its id IS the code.
        active_id = cls._lang_entry.get_active_id()
        if active_id is not None:
            return active_id
        # Otherwise parse free text: accept "fr", "French", or "French (fr)".
        text = cls._combo_text(cls._lang_entry)
        if not text or text.lower() in ("autodetect", "auto"):
            return ""
        if "(" in text and text.endswith(")"):
            return text[text.rfind("(") + 1:-1].strip()
        for lbl, code in cls._LANGUAGES:
            if text.lower() in (lbl.lower(), _(lbl).lower()):
                return code
        return text  # assume the user typed a raw code

    # -----------------------------------------------------------------
    # Post-processing: one Refinement level (0-4) + optional Translate
    # -----------------------------------------------------------------
    @classmethod
    def _build_postprocess_section(cls, vbox: Gtk.Box) -> None:
        from loquivox.config import POSTPROCESS_LEVELS, POSTPROCESS_MAX_LEVEL

        body = cls._group(
            vbox, _("Refinement"),
            _("What an LLM does to a dictation before it is typed. Uses the chat "
            "model from the Models &amp; keys tab (needs its API key); Off adds "
            "no latency."))

        # Refinement intensity: Off → Correct → Light → Medium → Strong.
        cls._pp_scale_label = Gtk.Label()
        cls._pp_scale_label.set_halign(Gtk.Align.START)
        cls._pp_scale_label.set_xalign(0)
        body.pack_start(cls._pp_scale_label, False, False, 0)

        cls._pp_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, POSTPROCESS_MAX_LEVEL, 1)
        cls._pp_scale.set_digits(0)
        cls._pp_scale.set_round_digits(0)
        cls._pp_scale.set_draw_value(False)
        cls._pp_scale.set_hexpand(True)
        for level, label in POSTPROCESS_LEVELS:
            cls._pp_scale.add_mark(level, Gtk.PositionType.BOTTOM, _(label))
        cls._pp_scale.set_value(int(config_module.CFG.POSTPROCESS_LEVEL or 0))
        # Snap to whole levels while dragging, and show the level name live.
        cls._pp_scale.connect("change-value", cls._on_pp_scale_change)
        cls._pp_scale.connect("value-changed", lambda _s: cls._refresh_pp_scale_label())
        cls._pp_scale.connect("value-changed", lambda _s: cls._update_pp_sensitivity())
        cls._refresh_pp_scale_label()
        body.pack_start(cls._pp_scale, False, False, 0)

        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 6)

        # Translate — a separate axis; when on it overrides the level.
        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._pp_translate_check = Gtk.CheckButton(label=_("Translate to"))
        cls._pp_translate_check.set_active(bool(config_module.CFG.POSTPROCESS_TRANSLATE))
        cls._pp_translate_check.connect("toggled", cls._on_pp_translate_toggled)
        trow.pack_start(cls._pp_translate_check, False, False, 0)
        cls._pp_lang = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            if code:  # translation needs a real target
                cls._pp_lang.append(code, f"{_(label)} ({code})")
        if not cls._pp_lang.set_active_id(config_module.CFG.POSTPROCESS_TARGET_LANG):
            cls._pp_lang.get_child().set_text(config_module.CFG.POSTPROCESS_TARGET_LANG)
        trow.pack_start(cls._pp_lang, True, True, 0)
        body.pack_start(trow, False, False, 0)

        # Format — a separate axis that COMBINES with the level / translate.
        cls._pp_format_check = Gtk.CheckButton(
            label=_("Format as structured text (paragraphs + bullet lists)"))
        cls._pp_format_check.set_active(bool(config_module.CFG.POSTPROCESS_FORMAT))
        cls._pp_format_check.set_tooltip_text(
            _("Lays the result out in plain-text paragraphs and lists. Combines "
            "with the refinement level (or works alone when level is Off)."))
        body.pack_start(cls._pp_format_check, False, False, 0)

        # Advanced: a custom prompt that overrides the level's built-in prompt.
        # Folded away — it is a 120px text box that only the Custom level reads,
        # and unfolded it dominated a tab whose real control is one slider.
        cls._pp_prompt_label = Gtk.Expander(label=_("Custom level prompt"))
        cls._pp_prompt_label.set_tooltip_text(
            _("Used when the refinement level is set to Custom — greyed out otherwise."))
        prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prompt_box.set_margin_top(8)

        cls._pp_prompt_view = Gtk.TextView()
        cls._pp_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD)
        cls._pp_prompt_view.set_left_margin(6)
        cls._pp_prompt_view.set_right_margin(6)
        cls._pp_prompt_view.get_buffer().set_text(config_module.CFG.POSTPROCESS_CUSTOM_PROMPT)
        cls._pp_prompt_scroll = Gtk.ScrolledWindow()
        cls._pp_prompt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cls._pp_prompt_scroll.set_min_content_height(90)
        cls._pp_prompt_scroll.set_shadow_type(Gtk.ShadowType.IN)
        cls._pp_prompt_scroll.add(cls._pp_prompt_view)
        prompt_box.pack_start(cls._pp_prompt_scroll, False, False, 0)

        clear_btn = Gtk.Button(label=_("Clear custom prompt"))
        clear_btn.set_halign(Gtk.Align.START)
        clear_btn.connect("clicked", cls._on_reset_pp_prompt)
        prompt_box.pack_start(clear_btn, False, False, 0)
        cls._pp_prompt_label.add(prompt_box)
        # Open it when it is the level actually in use, or when there is
        # already something in it to see.
        cls._pp_prompt_label.set_expanded(
            bool(config_module.CFG.POSTPROCESS_CUSTOM_PROMPT.strip()))
        body.pack_start(cls._pp_prompt_label, False, False, 0)

        cls._update_pp_sensitivity()

        cls._pp_status = Gtk.Label()
        cls._actions(body, cls._on_apply_postprocess, cls._pp_status)

    @classmethod
    def _update_pp_sensitivity(cls) -> None:
        """
        Translate on → only the language matters; off → the level does, and
        the custom prompt only when that level is Custom (the only one that
        reads it).
        """
        translate = bool(cls._pp_translate_check.get_active()) if cls._pp_translate_check else False
        custom = (cls._pp_scale is not None
                  and int(cls._pp_scale.get_value()) == config_module.POSTPROCESS_CUSTOM_LEVEL)
        if cls._pp_lang:
            cls._pp_lang.set_sensitive(translate)
        for w in (cls._pp_scale, cls._pp_scale_label):
            if w:
                w.set_sensitive(not translate)
        for w in (cls._pp_prompt_view, cls._pp_prompt_scroll, cls._pp_prompt_label):
            if w:
                w.set_sensitive(custom and not translate)
        if custom and not translate and cls._pp_prompt_label:
            cls._pp_prompt_label.set_expanded(True)   # picking Custom means writing one

    @classmethod
    def _on_pp_translate_toggled(cls, _chk: Gtk.CheckButton) -> None:
        cls._update_pp_sensitivity()

    @staticmethod
    def _on_pp_scale_change(scale: Gtk.Scale, _scroll, value: float):
        """Snap the slider to whole levels (it's continuous by default)."""
        scale.set_value(round(value))
        return True  # handled — don't apply the raw continuous value

    @classmethod
    def _refresh_pp_scale_label(cls) -> None:
        """Show the selected level's name next to the slider for clarity."""
        from loquivox.config import POSTPROCESS_LEVELS
        if not (cls._pp_scale and cls._pp_scale_label):
            return
        level = int(cls._pp_scale.get_value())
        name = _(dict(POSTPROCESS_LEVELS).get(level, level))
        cls._pp_scale_label.set_markup(_("<small><b>Refinement level:</b> {name}</small>").format(name=name))

    @classmethod
    def _pp_prompt_text(cls) -> str:
        """Current custom-prompt text (empty = use the level's built-in prompt)."""
        buf = cls._pp_prompt_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    @classmethod
    def _on_reset_pp_prompt(cls, _btn: Gtk.Button) -> None:
        cls._pp_prompt_view.get_buffer().set_text("")

    @classmethod
    def _on_apply_postprocess(cls, _btn: Gtk.Button) -> None:
        from loquivox.config import POSTPROCESS_LEVELS

        level = int(cls._pp_scale.get_value()) if cls._pp_scale else 0
        translate = bool(cls._pp_translate_check.get_active()) if cls._pp_translate_check else False
        fmt = bool(cls._pp_format_check.get_active()) if cls._pp_format_check else False
        lang = cls._pp_lang.get_active_id() or cls._combo_text(cls._pp_lang)
        if "(" in lang and lang.endswith(")"):  # a "Name (code)" row was typed
            lang = lang[lang.rfind("(") + 1:-1].strip()

        from loquivox.config_io import ConfigWriteError, update_section
        try:
            update_section("postprocess", {
                "level": level,
                "translate": translate,
                "format": fmt,
                "target_language": lang,
                # Written verbatim (empty string clears any prior override).
                "custom_prompt": cls._pp_prompt_text(),
            })
        except ConfigWriteError as e:
            cls._pp_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()  # PostProcessor reads config_module.CFG live
        if translate:
            desc = _("translate → {lang}").format(lang=lang)
        else:
            desc = _("level {level} ({name})").format(level=level, name=_(dict(POSTPROCESS_LEVELS).get(level, level)))
        if fmt:
            desc += _(" + format")
        cls._pp_status.set_markup(_("<small>✓ Applied live — {desc}.</small>").format(desc=desc))
        print(f"✨ Post-processing: level={level} translate={translate} format={fmt} lang={lang}")

    # -----------------------------------------------------------------
    # Conversation behaviour (written to config.toml [talk])
    # -----------------------------------------------------------------
    #: (id, label) for the "how far it digs" combo — mirrors CFG.TALK_DEPTH.
    _TALK_DEPTHS = (
        ("minimal", N_("Minimal — only asks when something is unclear")),
        ("normal", N_("Normal — asks about what would change the text")),
        ("deep", N_("Deep — pushes the reasoning further")),
    )

    #: (id, label, when to pick it) for the anti-echo combo — CFG.TALK_ECHO_GUARD.
    _ECHO_GUARDS = (
        ("off", N_("Off (headset)"),
         N_("Nothing holds the microphone back: it always reaches the model, even "
         "while it speaks. Pick this on a headset if it ignores you or you "
         "cannot get a word in. Never on speakers: the reply would cut itself off.")),
        ("headset", N_("Headset, no calibration"),
         N_("Holds the microphone while a reply plays, and lets you in at the plain "
         "voice threshold. For a headset whose own echo is measured wrongly "
         "(typically USB).")),
        ("speakers", N_("Speakers"),
         N_("Holds the microphone while a reply plays and measures the echo to tell "
         "it from your voice. Pick this when the sound comes out of speakers.")),
    )

    @classmethod
    def _build_echo_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """Anti-echo and cutting in: one setting for every engine and the companion."""
        body = cls._group(
            vbox, _("Echo guard"),
            _("What keeps the assistant from hearing its own voice, and how you "
              "cut in on it. It applies to every conversation engine and to the "
              "companion."))

        cls._talk_echo_guard = Gtk.ComboBoxText()
        for guard_id, label, _help in cls._ECHO_GUARDS:
            cls._talk_echo_guard.append(guard_id, _(label))
        cls._talk_echo_guard.set_active_id(
            config_module.CFG.TALK_ECHO_GUARD
            if config_module.CFG.TALK_ECHO_GUARD in dict((g[0], 1) for g in cls._ECHO_GUARDS)
            else "speakers")
        cls._field(body, _("Anti-echo"), cls._talk_echo_guard, labels)
        cls._talk_echo_guard.connect("changed", lambda _w: cls._update_echo_sensitivity())
        guard_help = Gtk.Label()
        guard_help.set_halign(Gtk.Align.START)
        guard_help.set_xalign(0)
        guard_help.set_line_wrap(True)
        guard_help.set_max_width_chars(58)
        guard_help.get_style_context().add_class("dim-label")
        helps = {g[0]: _(g[2]) for g in cls._ECHO_GUARDS}

        def _show_help(combo: Gtk.ComboBoxText) -> None:
            guard_help.set_markup(f"<small>{helps.get(combo.get_active_id(), '')}</small>")

        cls._talk_echo_guard.connect("changed", _show_help)
        _show_help(cls._talk_echo_guard)
        body.pack_start(guard_help, False, False, 0)

        cls._talk_barge_margin = Gtk.SpinButton.new_with_range(1.0, 6.0, 0.1)
        cls._talk_barge_margin.set_digits(1)
        cls._talk_barge_margin.set_value(float(config_module.CFG.TALK_BARGE_IN_MARGIN))
        cls._talk_barge_margin.set_halign(Gtk.Align.START)
        cls._talk_barge_margin.set_tooltip_text(
            _("How much louder than the assistant's own echo you have to be for "
            "it to stop and listen.\n\n"
            "On a headset this does nothing — nothing leaks back, so speaking "
            "interrupts as it always did. On speakers it is your room and your "
            "volume that decide: too low and the reply cuts itself off every "
            "time, too high and you cannot interrupt it at all. Every reply "
            "prints the two numbers to set it from (🔉 echo peak · loudest) in "
            "the terminal.\n\n"
            "When no value works — on a laptop's speakers a voice can beat the "
            "echo by as little as ×1.2 — the fix is a headset, or echo "
            "cancellation (PipeWire's libpipewire-module-echo-cancel).")
        )
        cls._field(body, _("Cutting in (× the echo)"), cls._talk_barge_margin, labels)
        cls._update_echo_sensitivity()

        cls._echo_status = Gtk.Label()
        cls._actions(body, cls._on_apply_echo, cls._echo_status)

    @classmethod
    def _update_echo_sensitivity(cls) -> None:
        """The cutting-in margin is read only when the anti-echo measures an echo."""
        if cls._talk_barge_margin is not None:
            cls._talk_barge_margin.set_sensitive(
                cls._talk_echo_guard.get_active_id() == "speakers")

    @classmethod
    def _on_apply_echo(cls, _btn) -> None:
        from loquivox.config_io import ConfigWriteError, update_section

        try:
            update_section("talk", {
                "echo_guard": cls._talk_echo_guard.get_active_id() or "speakers",
                "barge_in_margin": round(float(cls._talk_barge_margin.get_value()), 2),
            })
        except ConfigWriteError as e:
            cls._echo_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()   # talk mode reads CFG live
        cls._echo_status.set_markup(_("<small>✓ Saved — applies from the next reply.</small>"))

    _SEMANTIC_LABEL_BASE = N_("End my turns on meaning, not on silence (cascade)")

    @classmethod
    def _semantic_needs_onnx(cls) -> bool:
        """
        Whether ending turns on meaning needs the local ONNX detector here.

        False when onnxruntime is already there, and false when the active
        backend closes the turns server-side (OpenAI Realtime's semantic VAD) —
        the box is still worth ticking then, it just installs nothing.
        """
        if cls._module_installed("onnxruntime"):
            return False
        from loquivox.transcription import get_dispatcher
        active = get_dispatcher().active
        if active is not None and active.name == "openai_realtime":
            from loquivox.transcription.openai_realtime_backend import OpenAIRealtimeSession
            return not OpenAIRealtimeSession._supports_turn_detection(config_module.CFG.OPENAI_MODEL)
        return True

    @classmethod
    def _build_talk_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup,
                            screen: Gtk.Box, screen_labels: Gtk.SizeGroup) -> None:
        """Conversation knobs: who may end the briefing, how it converses, what it sees."""
        ai_key = config_module.CFG.HOTKEY_DEFS['ai'][0]
        talk_key = config_module.CFG.HOTKEY_DEFS['talk'][0]
        body = cls._group(
            vbox, _("Conversation"),
            _('How {ai_key} and {talk_key} conversations behave. "How far it '
                       'digs" only applies to {talk_key}; "End my turns on meaning" '
                       "only to the cascade engine. Reading replies aloud is set in "
                       "Sound.").format(ai_key=ai_key, talk_key=talk_key))

        cls._talk_depth = Gtk.ComboBoxText()
        for depth_id, label in cls._TALK_DEPTHS:
            cls._talk_depth.append(depth_id, _(label))
        cls._talk_depth.set_active_id(
            config_module.CFG.TALK_DEPTH if config_module.CFG.TALK_DEPTH in dict(cls._TALK_DEPTHS) else "normal")
        cls._talk_depth.set_tooltip_text(
            _("{talk_key} only: the briefing ends in a written text, and this "
                       "is how hard the assistant questions you before writing. "
                       "{ai_key} is an open chat and never reads it. Applies on every "
                       "engine.").format(talk_key=talk_key, ai_key=ai_key))
        cls._field(body, _("How far it digs ({talk_key})").format(talk_key=talk_key),
                   cls._talk_depth, labels)

        cls._talk_semantic_check = Gtk.CheckButton(
            label=_(cls._SEMANTIC_LABEL_BASE) + (
                _(" (offers to install onnxruntime)") if cls._semantic_needs_onnx() else ""))
        # Its label gets "(offers to install onnxruntime)" / "— installing…"
        # appended: let it wrap rather than set the window's width.
        _lbl = cls._talk_semantic_check.get_child()
        _lbl.set_line_wrap(True)
        _lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        _lbl.set_max_width_chars(52)
        cls._talk_semantic_check.set_active(bool(config_module.CFG.TALK_SEMANTIC_TURNS))
        cls._talk_semantic_check.connect("toggled", cls._on_talk_semantic_toggled)
        cls._talk_semantic_check.set_tooltip_text(
            _("Cascade engine, {ai_key} and {talk_key}. The Realtime and Live "
                       "engines close your turns on their own server, so this does "
                       "nothing there. If onnxruntime is missing, ticking asks before "
                       "installing it.").format(ai_key=ai_key, talk_key=talk_key))
        body.pack_start(cls._talk_semantic_check, False, False, 0)

        cls._talk_free_kbd_check = Gtk.CheckButton(
            label=_("Keep typing in other apps while the conversation runs"))
        cls._talk_free_kbd_check.set_active(bool(config_module.CFG.TALK_FREE_KEYBOARD))
        cls._talk_free_kbd_check.set_tooltip_text(
            _("A session normally takes the keyboard for itself while it waits on "
            "you, so nothing you press reaches the app underneath.\n\n"
            "Ticked, it leaves it alone: you carry on typing, and your shortcuts "
            "keep working. Every key then lands in that app too, so the session "
            "answers to {talk_key} / {ai_key} alone — pressed again, it "
            "ends the session — and Space / Enter / Esc / S / T no longer do "
            "anything to it. The review of the finished text still takes the "
            "keyboard: it is a question with an answer.\n\n"
            "The bubble carries the same switch, so it can also be flipped "
            "mid-conversation — and what you flip it to lands back here.").format(
                talk_key=config_module.CFG.HOTKEY_DEFS['talk'][0],
                ai_key=config_module.CFG.HOTKEY_DEFS['ai'][0])
        )
        body.pack_start(cls._talk_free_kbd_check, False, False, 0)

        cls._note(body, _("Anti-echo and cutting in are set in <b>Sound › Echo guard</b>."))

        body = cls._group(
            vbox, _("Ending the briefing ({talk_key})").format(talk_key=talk_key),
            _("Enter always writes the text — or the talk key itself, with the "
              "keyboard left free. That one can't be turned off, so there is "
              "always a way out. These are extras. A chat ({ai_key}) ends on "
              "the key alone: it writes through the tool, whenever you ask, "
              "without ending.").format(ai_key=ai_key))

        cls._talk_phrase_check = Gtk.CheckButton(
            label=_("When I say so out loud (“vas-y”, “j'ai fini”, “that's it”)"))
        cls._talk_phrase_check.set_active(bool(config_module.CFG.TALK_FINISH_ON_PHRASE))
        body.pack_start(cls._talk_phrase_check, False, False, 0)

        cls._talk_model_check = Gtk.CheckButton(
            label=_("When the assistant judges it has enough context"))
        cls._talk_model_check.set_active(bool(config_module.CFG.TALK_FINISH_BY_MODEL))
        body.pack_start(cls._talk_model_check, False, False, 0)

        cls._talk_autopaste_check = Gtk.CheckButton(
            label=_("Paste the finished text straight away, without reviewing it"))
        cls._talk_autopaste_check.set_active(bool(config_module.CFG.TALK_AUTO_PASTE))
        cls._talk_autopaste_check.set_tooltip_text(
            _("The text is copied and pasted where the cursor was when the "
            "conversation ended — no Enter, and no chance to ask for another version or go "
            "back to the conversation. Off by default: talk mode otherwise "
            "never types anything you have not accepted.")
        )
        body.pack_start(cls._talk_autopaste_check, False, False, 0)

        body = cls._group(
            vbox, _("Instructions"),
            _("Standing instructions for the conversation: language, persona, "
            "tone, the kind of work you usually discuss. Added to the built-in "
            "prompt rather than replacing it, and read by both engines. Leave "
            "empty for the default."))

        instr_scroll = Gtk.ScrolledWindow()
        instr_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        instr_scroll.set_size_request(-1, 120)
        instr_scroll.set_shadow_type(Gtk.ShadowType.IN)
        cls._talk_instructions = Gtk.TextView()
        cls._talk_instructions.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        cls._talk_instructions.set_left_margin(6)
        cls._talk_instructions.set_right_margin(6)
        cls._talk_instructions.get_buffer().set_text(
            config_module.CFG.TALK_INSTRUCTIONS)
        cls._talk_instructions.set_tooltip_text(
            _("Example:\nYou always answer in French, simply and without "
            "formality. The context is technical: be precise, and ask for a "
            "clarification rather than guess. The only English words are "
            "technical terms.")
        )
        instr_scroll.add(cls._talk_instructions)
        body.pack_start(instr_scroll, False, False, 0)

        body = cls._group(
            screen, _("Screen context"),
            _("Most of what you are about to dictate is already on your screen. "
            "The focused window is captured when the session starts, in "
            "parallel with your first sentence, and described by the vision "
            "model; <b>S</b> looks again once something has changed. {ai_key} and "
            "the companion always capture; the box below only decides it for "
            "{talk_key}, and is off by default. Each time, that window leaves "
            "the machine.").format(ai_key=ai_key, talk_key=talk_key))

        cls._talk_shot_check = Gtk.CheckButton(
            label=_("Let the assistant see what you are working in"))
        cls._talk_shot_check.set_active(bool(config_module.CFG.TALK_SCREENSHOT))
        cls._talk_shot_check.connect("toggled", cls._on_talk_shot_toggled)
        body.pack_start(cls._talk_shot_check, False, False, 0)

        cls._talk_shot_region = Gtk.ComboBoxText()
        cls._talk_shot_region.append("window", _("The focused window (recommended)"))
        cls._talk_shot_region.append("cursor", _("A box around the cursor (X11 / Hyprland)"))
        cls._talk_shot_region.append("screen", _("The whole screen"))
        region = config_module.CFG.TALK_SCREENSHOT_REGION
        cls._talk_shot_region.set_active_id(
            region if region in ("window", "cursor", "screen") else "screen")
        cls._talk_shot_region.set_tooltip_text(
            _("Cropping is what makes the text readable: an image gets a fixed "
            "token budget whatever its size, so a whole 4K desktop comes back "
            "as confident nonsense. A session that cannot do the framing you "
            "pick falls back to the whole screen and says so.")
        )
        cls._field(body, _("Capture"), cls._talk_shot_region, screen_labels)
        cls._talk_shot_region.connect("changed", lambda _w: cls._update_talk_sensitivity())

        cls._talk_shot_cursor = Gtk.SpinButton.new_with_range(200, 7680, 100)
        cls._talk_shot_cursor.set_value(int(config_module.CFG.TALK_SCREENSHOT_CURSOR_PX))
        cls._talk_shot_cursor.set_halign(Gtk.Align.START)
        cls._field(body, _("Box width (px)"), cls._talk_shot_cursor, screen_labels)

        cls._talk_shot_max = Gtk.SpinButton.new_with_range(0, 7680, 128)
        cls._talk_shot_max.set_value(int(config_module.CFG.TALK_SCREENSHOT_MAX_PX))
        cls._talk_shot_max.set_halign(Gtk.Align.START)
        cls._talk_shot_max.set_tooltip_text(
            _("Longest edge of the uploaded image — what keeps a 4K capture small. "
            "0 uploads it as captured.")
        )
        cls._field(body, _("Downscale to (px)"), cls._talk_shot_max, screen_labels)

        cls._talk_shot_clip = Gtk.CheckButton(
            label=_("Put back what was on the clipboard afterwards"))
        cls._talk_shot_clip.set_active(
            bool(config_module.CFG.TALK_SCREENSHOT_RESTORE_CLIPBOARD))
        cls._talk_shot_clip.set_tooltip_text(
            _("Capturing a window copies the image to the clipboard on niri, "
            "which has no way to skip it. Off by default — the session ends by "
            "leaving its generated text there anyway, so the only loss is "
            "whatever you had copied a minute ago. Text only.")
        )
        body.pack_start(cls._talk_shot_clip, False, False, 0)
        cls._update_talk_sensitivity()

        cls._note(screen,
                  _("Timings, prompts and the turn-detection thresholds live "
                  "under <tt>[talk]</tt> in config.toml."))

        # One writer for the whole tab: everything above lands in [talk].
        cls._talk_status = Gtk.Label()
        cls._actions(vbox, cls._on_apply_talk, cls._talk_status)

    @classmethod
    def _on_talk_semantic_toggled(cls, check: Gtk.CheckButton) -> None:
        """Ticking the box offers to install onnxruntime when it is missing."""
        if not check.get_active() or not cls._semantic_needs_onnx():
            return
        import threading

        ask = Gtk.MessageDialog(
            transient_for=cls._instance, modal=True,
            message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Install onnxruntime?"))
        ask.format_secondary_text(
            _("Ending turns on meaning needs onnxruntime (~200 MB), plus an 8 MB "
            "model downloaded the first time it runs. Without it, turns keep "
            "ending on silence."))
        answer = ask.run()
        ask.destroy()
        if answer != Gtk.ResponseType.OK:
            check.set_active(False)
            return
        check.set_label(_("{label} — installing onnxruntime…").format(label=_(cls._SEMANTIC_LABEL_BASE)))
        check.set_sensitive(False)

        def work():
            from gi.repository import GLib
            ok, msg = cls._pip_install("onnxruntime")

            def done():
                # Back to what the engine says, not to True: it may have changed meanwhile.
                cls._on_talk_engine_changed(cls._engine_combo)
                if ok:
                    check.set_label(_("{label} (ready)").format(label=_(cls._SEMANTIC_LABEL_BASE)))
                else:
                    check.set_active(False)
                    check.set_label(_("{label} — install failed: {msg}").format(
                        label=_(cls._SEMANTIC_LABEL_BASE), msg=msg))
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def _on_talk_shot_toggled(cls, _check: Gtk.CheckButton) -> None:
        cls._update_talk_sensitivity()

    @classmethod
    def _update_talk_sensitivity(cls) -> None:
        """
        Grey out the box width unless the capture is a box around the cursor.

        The box only decides whether F6 captures: F4 and the companion always
        do, so the framing controls stay live.
        """
        cursor = cls._talk_shot_region.get_active_id() == "cursor"
        if cls._talk_shot_cursor is not None:
            cls._talk_shot_cursor.set_sensitive(cursor)

    @classmethod
    def sync_talk_switch(cls) -> None:
        """
        Put the keyboard checkbox back in step with ``CFG``.

        Two writers own that key — this page and the bubble's switch — and a
        dialog left open while a conversation runs would otherwise write its
        stale checkbox back on the next Apply, silently undoing the flip. The
        page is built from ``CFG``, so this only matters while it is already up.
        """
        check = getattr(cls, "_talk_free_kbd_check", None)
        if check is not None and cls._instance is not None:
            check.set_active(bool(config_module.CFG.TALK_FREE_KEYBOARD))

    @classmethod
    @run_on_main_thread
    def sync_tts_switch(cls) -> None:
        """Put the read-aloud box back in step with ``STATE`` after F10 flipped it."""
        check = cls._tts_enabled_check
        if check is not None and cls._instance is not None:
            check.set_active(bool(STATE.tts_enabled))

    @classmethod
    def _on_apply_talk(cls, _btn: Gtk.Button) -> None:
        """Write the talk knobs to config.toml and reload — next session uses them."""
        from loquivox.config_io import ConfigWriteError, update_section

        phrase = bool(cls._talk_phrase_check.get_active())
        by_model = bool(cls._talk_model_check.get_active())
        depth = cls._talk_depth.get_active_id() or "normal"
        semantic = bool(cls._talk_semantic_check.get_active())
        free_keyboard = bool(cls._talk_free_kbd_check.get_active())
        screenshot = bool(cls._talk_shot_check.get_active())
        auto_paste = bool(cls._talk_autopaste_check.get_active())
        buffer = cls._talk_instructions.get_buffer()
        instructions = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), False).strip()
        try:
            update_section("talk", {
                "free_keyboard": free_keyboard,
                "finish_on_phrase": phrase,
                "finish_by_model": by_model,
                "depth": depth,
                "semantic_turns": semantic,
                "screenshot": screenshot,
                "instructions": instructions,
                "auto_paste": auto_paste,
                "screenshot_region": cls._talk_shot_region.get_active_id() or "window",
                "screenshot_restore_clipboard": bool(cls._talk_shot_clip.get_active()),
                "screenshot_cursor_px": int(cls._talk_shot_cursor.get_value()),
                "screenshot_max_px": int(cls._talk_shot_max.get_value()),
            })
        except ConfigWriteError as e:
            cls._talk_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()  # talk mode reads config_module.CFG live
        ends = [name for name, on in ((_("Enter"), True), (_("spoken"), phrase),
                                      (_("assistant"), by_model)) if on]
        instr_note = (_(" · {n} chars of instructions").format(n=len(instructions))
                      if instructions else "")
        if auto_paste:
            instr_note += _(" · pasted without review")
        cls._talk_status.set_markup(
            _("<small>✓ Applied — ends on: {ends}{instr_note}.</small>").format(ends=', '.join(ends), instr_note=instr_note)
        )
        print(f"🗣️  Talk: finish_on_phrase={phrase} finish_by_model={by_model} "
              f"depth={depth} semantic_turns={semantic} screenshot={screenshot}")

    # -----------------------------------------------------------------
    # API keys section (#stored in secrets.env, applied live)
    # -----------------------------------------------------------------
    @classmethod
    def _build_api_keys_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        import os
        from loquivox.secrets import MANAGED_KEYS, SECRETS_FILE, read_secrets

        body = cls._group(
            vbox, _("Provider keys"),
            _("Stored in <tt>{v0}</tt> (chmod 600) and loaded at startup, so "
                       "they survive a reboot. Applied live on save.").format(v0=GLib.markup_escape_text(str(SECRETS_FILE))))

        stored = read_secrets()
        cls._key_entries = {}

        for key, label in MANAGED_KEYS.items():
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_visibility(False)  # masked
            entry.set_text(stored.get(key, ""))
            # If a key is active via the inherited env (e.g. environment.d) but
            # not stored here, hint that without exposing it.
            if not stored.get(key) and os.environ.get(key):
                entry.set_placeholder_text(_("(set via environment — type to override)"))
            cls._field(body, label, entry, labels)
            cls._key_entries[key] = entry

        show_chk = Gtk.CheckButton(label=_("Show the keys"))
        show_chk.connect("toggled", cls._on_toggle_key_visibility)
        body.pack_start(show_chk, False, False, 0)

        cls._key_status = Gtk.Label()
        cls._actions(body, cls._on_save_keys, cls._key_status)

    @classmethod
    def _on_toggle_key_visibility(cls, chk: Gtk.CheckButton) -> None:
        for entry in (cls._key_entries or {}).values():
            entry.set_visibility(chk.get_active())

    @classmethod
    def _on_save_keys(cls, _btn: Gtk.Button) -> None:
        from loquivox.secrets import save_secrets
        values = {k: e.get_text() for k, e in (cls._key_entries or {}).items()}
        try:
            save_secrets(values)
        except OSError as e:
            cls._key_status.set_markup(f"<small>❌ {e}</small>")
            return
        # Re-evaluate backends now that keys changed.
        from loquivox.transcription import reconfigure_dispatcher
        reconfigure_dispatcher(config_module.reload_config())
        n = sum(1 for v in values.values() if v.strip())
        cls._key_status.set_markup(
            _("<small>✓ Saved {n} key(s) (chmod 600) &amp; applied "
               "live.</small>").format(n=n)
        )
        # Refresh the transcription status hint if it's showing an availability warning.
        if cls._backend_combo is not None:
            cls._sync_model_entry()
        print("🔑 API keys saved & applied.")

    # -----------------------------------------------------------------
    # Support: the log and the bug report, from where a user in trouble looks
    # -----------------------------------------------------------------
    @classmethod
    def _build_support_page(cls, vbox: Gtk.Box) -> None:
        from loquivox import __version__
        from loquivox.diagnostics import log_file_path
        from loquivox.ui.report_dialog import LogDialog, ReportDialog

        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        path = log_file_path()

        body = cls._group(
            vbox, _("Logs"),
            _("Everything Loquivox prints goes to a log file too, so a session "
            "started at login leaves a trace. View shows the last lines live, "
            "unredacted, for your own eyes; Save writes the whole log with "
            "keys, names and addresses removed, ready to attach to an issue."))
        where = Gtk.Label(label=str(path) if path else _("(disabled via LOQUIVOX_LOG_FILE)"))
        where.set_halign(Gtk.Align.START)
        where.set_xalign(0)
        where.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        where.set_selectable(True)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        view_btn = Gtk.Button(label=_("View log…"))
        view_btn.connect("clicked", lambda _w: LogDialog.show())
        save_btn = Gtk.Button(label=_("Save log…"))
        save_btn.set_tooltip_text(_("The whole log with keys, names and addresses "
                                  "removed — the file to attach to a bug report"))
        save_btn.connect("clicked", lambda _w: LogDialog.save_for_sharing(
            cls._instance, cls._support_status.set_text))
        buttons.pack_start(view_btn, False, False, 0)
        buttons.pack_start(save_btn, False, False, 0)
        cls._field(body, _("Log file"), where, labels, extra=buttons)
        cls._support_status = Gtk.Label()
        cls._support_status.set_halign(Gtk.Align.START)
        cls._support_status.set_xalign(0)
        cls._support_status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        body.pack_start(cls._support_status, False, False, 0)

        body = cls._group(
            vbox, _("Bug report"),
            _("One text with the version, system, tools, config, key presence and "
            "the recent log. API keys, your user name, home directory, e-mail "
            "and IP addresses are removed; what you said is replaced by its "
            "length unless you choose to include it. You read it before "
            "anything is copied or saved."))
        report_btn = Gtk.Button(label=_("Diagnostic report…"))
        report_btn.set_halign(Gtk.Align.START)
        report_btn.connect("clicked", lambda _w: ReportDialog.show())
        cls._field(body, _("Report"), report_btn, labels)
        cli = Gtk.Label()
        cli.set_halign(Gtk.Align.START)
        cli.set_xalign(0)
        cli.set_markup(_("<small>Without the tray: <tt>loquivox --report</tt> · version "
                   "{v0}</small>").format(v0=GLib.markup_escape_text(__version__)))
        cli.get_style_context().add_class("dim-label")
        body.pack_start(cli, False, False, 0)

    # -----------------------------------------------------------------
    # Hotkeys section (editable)
    # -----------------------------------------------------------------
    _HOTKEY_LABELS = {
        "dictation": N_("Dictation"), "ai": N_("Conversation — chat"),
        "talk": N_("Conversation — write"), "pin": N_("Pin the chat"),
        "tts": N_("Sound — read aloud"),
        "cancel": N_("Cancel"), "pause": N_("Pause / Resume"),
        "refine": N_("Stop + choose level"),
        "companion": N_("Companion — silence"), "companion_wake": N_("Companion — wake"),
    }

    @classmethod
    def _build_hotkeys_section(cls, vbox: Gtk.Box) -> None:
        """Editable per-mode key bindings (evdev key names), applied live."""
        # Two groups, split by what a key does: the ones that *open* something
        # (a recording, a conversation, the companion) and the ones that act on
        # whatever is already running. Reading ten bindings as one flat list is
        # what hid the difference.
        opens = ("dictation", "ai", "talk", "companion_wake")
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        bodies = {
            True: cls._group(
                vbox, _("Start something"),
                _("Dictation records while its key is down — or press once to "
                "start and again to stop, see the Dictation tab. The others "
                "open a conversation, or the companion's.")),
            False: cls._group(
                vbox, _("Act on a session"),
                _("Pressed once, acting on whatever is already running.")),
        }

        cls._hotkey_entries = {}
        cls._hotkey_capture_btns = []

        for mode_id, (_label, specs) in config_module.CFG.HOTKEY_DEFS.items():
            name = _(cls._HOTKEY_LABELS.get(mode_id, mode_id.replace("_", " ").title()))
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_text(" ".join(specs))
            entry.set_placeholder_text(_("unbound"))

            capture_btn = Gtk.Button(label=_("⌨ Set"))
            capture_btn.set_tooltip_text(_("Press a key to bind it (no need to know its name)"))
            capture_btn.connect("clicked", cls._on_capture_hotkey, mode_id, entry)

            cls._field(bodies[mode_id in opens], name, entry, labels,
                       extra=capture_btn)
            cls._hotkey_entries[mode_id] = entry
            cls._hotkey_capture_btns.append(capture_btn)

        cls._note(vbox,
                  _("Click <b>⌨ Set</b> then press a key or combo (e.g. Alt+Space) — "
                  "or type space-separated specs like <tt>ALT+SPACE F3</tt> "
                  "(first = primary, rest = aliases; combos join with <tt>+</tt>)."))

        cls._hotkey_status = Gtk.Label()
        cls._actions(vbox, cls._on_apply_hotkeys, cls._hotkey_status)

    @classmethod
    def _on_apply_hotkeys(cls, _btn: Gtk.Button) -> None:
        """Validate every chord spec, then write [hotkeys] to config.toml."""
        from loquivox.config import parse_chord

        parsed: dict = {}
        for mode_id, entry in cls._hotkey_entries.items():
            raw = entry.get_text().replace(",", " ").split()
            specs = []
            for tok in raw:  # validate now so we never write a broken binding
                try:
                    parse_chord(tok)
                except ValueError:
                    cls._set_hotkey_status(_("❌ invalid binding '{tok}' for {mode_id}.").format(tok=tok, mode_id=mode_id))
                    return
                specs.append(tok.strip().upper())
            parsed[mode_id] = specs  # empty = unbound (e.g. the 'refine' action)

        from loquivox.config_io import ConfigWriteError, update_section
        try:
            update_section("hotkeys", parsed)
        except ConfigWriteError as e:
            cls._set_hotkey_status(f"❌ {e}")
            return

        # Apply live: reload config and rebuild the keyboard listener's map so
        # the new bindings take effect immediately — no service restart needed.
        from loquivox.handlers.keyboard import KeyboardHandler
        KeyboardHandler.reload_hotkeys(config_module.reload_config())

        cls._set_hotkey_status(_("✓ Saved and applied — your new hotkeys work right away."))
        print(f"⌨️  Hotkeys saved and applied live: {parsed}")

    @classmethod
    def _set_hotkey_status(cls, markup: str) -> None:
        if cls._hotkey_status:
            cls._hotkey_status.set_markup(f"<small>{markup}</small>")

    @classmethod
    def _set_capture_enabled(cls, enabled: bool) -> None:
        for b in (cls._hotkey_capture_btns or []):
            b.set_sensitive(enabled)

    @classmethod
    def _on_capture_hotkey(cls, _btn: Gtk.Button, mode_id: str, entry: Gtk.Entry) -> None:
        """Capture the next key press and put its evdev name in the entry."""
        import threading
        label = cls._HOTKEY_LABELS.get(mode_id, mode_id)
        cls._set_capture_enabled(False)
        cls._set_hotkey_status(_("⌨ Press a key for <b>{label}</b>… (6s)").format(label=label))

        def work():
            from gi.repository import GLib
            from loquivox.handlers.keyboard import KeyboardHandler
            name = KeyboardHandler.capture_next_key()

            def done():
                if name:
                    entry.set_text(name)
                    cls._set_hotkey_status(
                        _("✓ Captured <b>{name}</b> for {label} — click <b>Apply</b> to "
                           "save.").format(name=name, label=label)
                    )
                else:
                    cls._set_hotkey_status(_("⚠️ No key captured (timed out or no input device)."))
                cls._set_capture_enabled(True)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    # Engine labels: which provider speaks, and what it can speak.
    _ENGINE_LABELS = {
        "canopylabs/orpheus-v1-english": N_("Groq Orpheus — English only (needs GROQ_API_KEY)"),
        "gpt-4o-mini-tts": N_("OpenAI — multilingual, speaks French (needs OPENAI_API_KEY)"),
        "piper": N_("Piper — local, offline, no key (pip install piper-tts)"),
    }

    @classmethod
    def _voices(cls) -> tuple:
        """The voices of the engine the combo shows (not yet saved)."""
        model = cls._tts_engine_combo.get_active_id() if cls._tts_engine_combo else STATE.tts_model
        return config_module.CFG.TTS_ENGINES.get(model, ("", ()))[1]

    @classmethod
    def _fill_voices(cls) -> None:
        """(Re)populate the voice combo for the current engine."""
        combo = cls._voice_combo
        if combo is None:
            return
        keep = combo.get_active_id() or STATE.tts_voice
        combo.remove_all()
        voices = cls._voices()
        for voice in voices:
            combo.append(voice, voice.title())
        combo.set_active_id(keep if keep in voices else (voices[0] if voices else None))

    @classmethod
    def _on_overlay_style_changed(cls, combo: Gtk.ComboBoxText) -> None:
        """Persist the overlay style and refresh the preview + any live overlay."""
        sid = combo.get_active_id()
        if not sid:
            return
        STATE.overlay_style = sid
        print(f"🎚️ Overlay style changed to: {sid}")
        SettingsManager.save(STATE)
        if cls._preview_area:
            cls._preview_area.queue_draw()
        if STATE.overlay_window is not None:
            try:
                STATE.overlay_window.drawing_area.queue_draw()
            except Exception:
                pass

    @classmethod
    def _on_hints_toggled(cls, check: Gtk.CheckButton) -> None:
        """
        Persist the hotkey-hints column and refresh the preview.

        A live overlay keeps its current width — the column is sized when the
        window is created, so the change lands on the next recording.
        """
        STATE.show_hints = check.get_active()
        print(f"⌨️  Overlay hotkey hints: {'on' if STATE.show_hints else 'off'}")
        SettingsManager.save(STATE)
        if cls._preview_area:
            cls._preview_area.queue_draw()

    @classmethod
    def _on_refine_badge_toggled(cls, check: Gtk.CheckButton) -> None:
        """Persist the refinement chip and refresh the preview (next recording)."""
        STATE.show_refine_badge = check.get_active()
        print(f"✨ Overlay refinement badge: {'on' if STATE.show_refine_badge else 'off'}")
        SettingsManager.save(STATE)
        if cls._preview_area:
            cls._preview_area.queue_draw()

    @staticmethod
    def _on_hotkey_bar_toggled(check: Gtk.CheckButton) -> None:
        """Show/hide the screen-edge hotkey tab right away, and persist it."""
        from loquivox.ui.hotkey_bar import HotkeyBar

        STATE.show_hotkey_bar = check.get_active()
        print(f"⌨️  Hotkey bar: {'on' if STATE.show_hotkey_bar else 'off'}")
        SettingsManager.save(STATE)
        HotkeyBar.start() if STATE.show_hotkey_bar else HotkeyBar.stop()

    @classmethod
    def _on_scheme_selected(cls, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handle theme gallery selection."""
        if not row:
            return
        # Find theme name from child labels
        name = None
        def find_name(child):
            nonlocal name
            if isinstance(child, Gtk.Label) and not child.get_style_context().has_class("dim-label"):
                name = child.get_text()
            elif hasattr(child, "get_children"):
                for c in child.get_children():
                    find_name(c)

        find_name(row.get_child())

        if name in config_module.CFG.COLOR_SCHEMES:
            STATE.color_scheme = name
            print(f"🎨 Color scheme changed to: {name}")
            SettingsManager.save(STATE)
            # Refresh the overlay preview to reflect the new theme.
            if cls._preview_area:
                cls._preview_area.queue_draw()
            # Late import to avoid circular dependency
            from loquivox.managers.chat import ChatManager
            ChatManager.refresh_overlay()

    @classmethod
    def _create_theme_row(cls, name: str) -> Gtk.ListBoxRow:
        """Create a visual card for a theme in the gallery."""
        scheme = config_module.CFG.COLOR_SCHEMES[name]
        row = Gtk.ListBoxRow()
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)

        # --- Preview Swatches ---
        swatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        colors = [scheme["bg"], scheme["surface"], scheme["accent"], scheme["text"]]
        for hex_color in colors:
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(16, 16)
            swatch.connect("draw", cls._on_draw_gallery_swatch, hex_color)
            swatch_box.pack_start(swatch, False, False, 0)

        hbox.pack_start(swatch_box, False, False, 0)

        # --- Name & Description ---
        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(label=name)
        name_label.set_halign(Gtk.Align.START)
        name_label.set_markup(f"<b>{name}</b>")

        desc_label = Gtk.Label(label=scheme.get("desc", ""))
        desc_label.set_halign(Gtk.Align.START)
        desc_label.set_line_wrap(True)
        desc_label.set_max_width_chars(30)
        desc_label.get_style_context().add_class("dim-label")
        desc_label.set_markup(f"<small><i>{_(scheme.get('desc', ''))}</i></small>")

        text_vbox.pack_start(name_label, False, False, 0)
        text_vbox.pack_start(desc_label, False, False, 0)
        hbox.pack_start(text_vbox, True, True, 0)

        row.add(hbox)
        return row

    @staticmethod
    def _on_draw_gallery_swatch(widget: Gtk.DrawingArea, cr: cairo.Context, hex_color: str) -> bool:
        """Draw a small color swatch circle in the gallery row."""
        # Convert hex to RGB
        h = hex_color.lstrip('#')
        rgb = tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

        # Draw circle
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.arc(w/2, h/2, min(w, h)/2 - 1, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.set_line_width(1)
        cr.stroke()
        return True
