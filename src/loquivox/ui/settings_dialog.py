"""
GTK Settings dialog for voice and hotkey configuration.
"""
from __future__ import annotations

import math
from typing import Optional

import cairo

from loquivox import config as config_module
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
        ("groq",            "Groq — cloud, batch",          False, "model",            "MODEL_WHISPER"),
        ("whispercpp",      "whisper.cpp — local, offline", False, "whispercpp_model", "WHISPERCPP_MODEL"),
        ("deepgram",        "Deepgram — cloud, live",       True,  "deepgram_model",   "DEEPGRAM_MODEL"),
        ("openai_realtime", "OpenAI Realtime — cloud, live", True, "openai_model",     "OPENAI_MODEL"),
        ("auto",            "Auto — Groq if key, else local", False, None,            None),
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
        ("Autodetect", ""), ("English", "en"), ("French", "fr"), ("German", "de"),
        ("Spanish", "es"), ("Italian", "it"), ("Portuguese", "pt"), ("Dutch", "nl"),
        ("Russian", "ru"), ("Chinese", "zh"), ("Japanese", "ja"), ("Korean", "ko"),
        ("Arabic", "ar"), ("Hindi", "hi"), ("Polish", "pl"),
    ]

    _whisper_status: Optional[Gtk.Label] = None
    _hotkey_entries: Optional[dict] = None
    _hotkey_status: Optional[Gtk.Label] = None
    _hotkey_capture_btns: Optional[list] = None
    _key_entries: Optional[dict] = None
    _key_status: Optional[Gtk.Label] = None
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

    # Models tab
    _chat_model: Optional[Gtk.ComboBoxText] = None
    _vision_model: Optional[Gtk.ComboBoxText] = None
    _models_status: Optional[Gtk.Label] = None
    _preset_status: Optional[Gtk.Label] = None
    _engine_combo: Optional[Gtk.ComboBoxText] = None
    _engine_status: Optional[Gtk.Label] = None
    _realtime_model: Optional[Gtk.Entry] = None
    _realtime_voice: Optional[Gtk.ComboBoxText] = None
    _tts_engine_combo: Optional[Gtk.ComboBoxText] = None
    _realtime_test: Optional[Gtk.Button] = None

    # Talk mode
    _talk_phrase_check: Optional[Gtk.CheckButton] = None
    _talk_model_check: Optional[Gtk.CheckButton] = None
    _talk_depth: Optional[Gtk.ComboBoxText] = None
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
        dialog = Gtk.Window(title="Loquivox Settings")
        dialog.set_default_size(640, 780)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Group the (now numerous) settings into tabs instead of one long scroll.
        # Order: what the app does (Models → Talk → Refinement), then how you
        # drive it (Hotkeys, Appearance), then one-time setup (API Keys).
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)
        for m in ("top", "start", "end"):
            getattr(notebook, f"set_margin_{m}")(12)

        cls._appliers = []   # the pages fill it back in as they are built
        for label, build in (
            ("Models", cls._build_models_section),
            ("Talk", cls._build_talk_section),
            ("Refinement", cls._build_postprocess_section),
            ("Hotkeys", cls._build_hotkeys_section),
            ("Appearance", cls._build_appearance_page),
            ("API Keys", cls._build_api_keys_section),
        ):
            page = cls._page()
            build(page)
            notebook.append_page(cls._scroll(page), Gtk.Label(label=label))

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

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: dialog.destroy())
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.set_tooltip_text("Save and apply every tab, not just this one")
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

        if cls._apply_status is None:
            return
        if unhappy:
            cls._apply_status.set_markup(
                f"<small>⚠️ Applied — {unhappy} setting(s) need a look; "
                "their tab says which.</small>")
        else:
            cls._apply_status.set_markup("<small>✓ All settings applied.</small>")

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
        heading.set_markup(f'<span size="large" weight="bold">{title}</span>')
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
    # Models tab: which provider answers, and with which models
    # -----------------------------------------------------------------
    @classmethod
    def _build_models_section(cls, vbox: Gtk.Box) -> None:
        """
        Everything that decides *who* does the work: a preset, the talk-mode
        engine, the chat/vision provider, and the voice.

        Four slots — transcription, turn detection, LLM, voice — each of which
        can run here or in the cloud. Spelling that out as four menus reads
        like a kernel config, so the presets fill them in one click and the
        sections below stay for whoever wants to disagree.

        One size group for the whole page: the label column lines up across
        five separate cards, which is most of what stops it reading as a heap.
        """
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        cls._build_presets(vbox)
        cls._build_engine_section(vbox, labels)
        cls._build_transcription_section(vbox, labels)   # slot 1 — the ears
        cls._build_chat_section(vbox, labels)            # slot 2 — the brain
        cls._build_voice_section(vbox, labels)           # slot 3 — the mouth

    @classmethod
    def _build_chat_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        """Provider + chat/vision models, listed live from the provider's API."""
        body = cls._group(
            vbox, "Chat &amp; vision",
            "Who answers F4, the rewrite, vision and talk mode — and who writes "
            "the final text, whichever conversation engine is in use. The lists "
            "come from the provider itself, so a model your account no longer "
            "has stops being offered.")

        provider = Gtk.ComboBoxText()
        for pid in config_module.CFG.AI_PROVIDERS:
            provider.append(pid, {"groq": "Groq", "openai": "OpenAI"}.get(pid, pid.title()))
        provider.set_active_id(STATE.ai_provider if STATE.ai_provider in config_module.CFG.AI_PROVIDERS
                               else config_module.CFG.AI_PROVIDERS[0])
        cls._field(body, "Provider", provider, labels)

        for label, attr in (("Chat model", "_chat_model"), ("Vision model", "_vision_model")):
            combo = Gtk.ComboBoxText.new_with_entry()   # editable: a new model id still works
            setattr(cls, attr, combo)
            cls._field(body, label, combo, labels)

        cls._models_status = Gtk.Label()
        cls._actions(body, cls._on_apply_models, cls._models_status)

        cls._load_models_async()
        provider.connect("changed", cls._on_provider_changed)

    # -----------------------------------------------------------------
    # Presets: the four slots, filled in one click
    # -----------------------------------------------------------------
    #: label → (description, {section: {key: value}}, {STATE attr: value})
    PRESETS: tuple = (
        ("Fast", "Cloud cascade — live transcription, Groq answers, cloud voice. "
                 "The default, ~1.8s before you hear a reply.",
         {"talk": {"engine": "cascade"}, "transcription": {"backend": "openai_realtime"}},
         {"tts_model": "gpt-4o-mini-tts", "tts_voice": "marin"}),
        ("Natural", "Speech to speech — one OpenAI Realtime session holds the "
                    "conversation, ~1.0s and it keeps the prosody. Costs more, "
                    "and the chat model below no longer answers in talk mode.",
         {"talk": {"engine": "realtime"}}, {}),
        ("Private", "Local voice and offline transcription — nothing but the "
                    "answer itself leaves the machine. Needs piper-tts and the "
                    "whisper.cpp binary; anything missing falls back to cloud.",
         {"talk": {"engine": "cascade"}, "transcription": {"backend": "whispercpp"}},
         {"tts_model": "piper", "tts_voice": "fr_FR-siwis-medium"}),
    )

    @classmethod
    def _build_presets(cls, vbox: Gtk.Box) -> None:
        """One row of buttons that fill every slot at once."""
        body = cls._group(
            vbox, "Presets",
            "Fill every slot below in one click. Hover a preset to see what it "
            "picks; everything stays editable afterwards.")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_homogeneous(True)
        for name, tooltip, sections, state in cls.PRESETS:
            button = Gtk.Button(label=name)
            button.set_tooltip_text(tooltip)
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
    def _on_preset(cls, _btn: Gtk.Button, name: str, sections: dict, state: dict) -> None:
        """Apply a preset: config.toml for the engines, settings.json for the voice."""
        from loquivox.config_io import ConfigWriteError, update_section

        try:
            for section, values in sections.items():
                update_section(section, values)
        except ConfigWriteError as e:
            cls._preset_status.set_markup(f"<small>❌ {e}</small>")
            cls._preset_status.show()
            return
        for attr, value in state.items():
            setattr(STATE, attr, value)
        if state:
            SettingsManager.save(STATE)
        config_module.reload_config()
        cls._refresh_engine_widgets()
        picked = ", ".join(f"{k}={v}" for values in sections.values()
                           for k, v in values.items())
        print(f"🎛️  Preset {name}: {picked} {state}")
        cls._preset_status.set_markup(
            f"<small>✓ {GLib.markup_escape_text(name)} applied — takes effect on "
            "the next talk session.</small>")
        cls._preset_status.show()

    # -----------------------------------------------------------------
    # Conversation engine (talk mode): cascade or native speech-to-speech
    # -----------------------------------------------------------------
    @classmethod
    def _build_engine_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        body = cls._group(
            vbox, "Conversation engine",
            "How talk mode holds the conversation. The cascade chains the "
            "transcription, chat model and voice below — each swappable, local "
            "or cloud. Realtime hands the whole conversation to one model that "
            "hears and answers directly: faster and more natural, but the chat "
            "model no longer answers. The final text is a plain completion "
            "either way.")

        cls._engine_combo = Gtk.ComboBoxText()
        for eid in config_module.CFG.TALK_ENGINES:
            cls._engine_combo.append(eid, {
                "cascade": "Cascade — transcribe, answer, speak",
                "realtime": "Realtime — speech to speech (OpenAI)",
            }.get(eid, eid.title()))
        cls._engine_combo.set_active_id(
            config_module.CFG.TALK_ENGINE if config_module.CFG.TALK_ENGINE in config_module.CFG.TALK_ENGINES else "cascade")
        cls._field(body, "Engine", cls._engine_combo, labels)

        cls._realtime_model = Gtk.Entry()   # ids change often — free text
        cls._realtime_model.set_text(config_module.CFG.TALK_REALTIME_MODEL)
        cls._field(body, "Realtime model", cls._realtime_model, labels)

        # A closed list, not free text: the API rejects an unknown voice, and
        # it does so only once the session is already opening.
        cls._realtime_voice = Gtk.ComboBoxText()
        for vid in config_module.CFG.TALK_REALTIME_VOICES:
            cls._realtime_voice.append(vid, vid.title())
        cls._realtime_voice.set_active_id(
            config_module.CFG.TALK_REALTIME_VOICE if config_module.CFG.TALK_REALTIME_VOICE
            in config_module.CFG.TALK_REALTIME_VOICES else config_module.CFG.TALK_REALTIME_VOICES[0])
        cls._realtime_test = Gtk.Button(label="▶ Test")
        cls._realtime_test.set_tooltip_text(
            "Preview this voice (same voice, spoken by the TTS model)")
        cls._realtime_test.connect("clicked", cls._on_test_realtime_voice)
        cls._field(body, "Its voice", cls._realtime_voice, labels, extra=cls._realtime_test)

        # The two fields above only mean anything in realtime mode — greying
        # them out is the shortest answer to "why are there two voices here".
        cls._engine_combo.connect("changed", cls._on_talk_engine_changed)
        cls._on_talk_engine_changed(cls._engine_combo)

        engine_status = Gtk.Label()
        cls._actions(body, cls._on_apply_engine, engine_status)
        # The engine's own feedback used to land in the preset card's status
        # line, two groups above the setting that caused it.
        cls._engine_status = engine_status

    @classmethod
    def _on_talk_engine_changed(cls, combo: Gtk.ComboBoxText) -> None:
        """Only the realtime engine has a model and a voice of its own."""
        realtime = (combo.get_active_id() or "cascade") == "realtime"
        for widget in (cls._realtime_model, cls._realtime_voice, cls._realtime_test):
            if widget is not None:
                widget.set_sensitive(realtime)

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
    def _on_apply_engine(cls, _btn: Gtk.Button) -> None:
        """Write [talk] engine/model/voice and reload — talk mode reads CFG live."""
        from loquivox.config_io import ConfigWriteError, update_section

        engine = cls._engine_combo.get_active_id() or "cascade"
        try:
            update_section("talk", {
                "engine": engine,
                "realtime_model": cls._realtime_model.get_text().strip(),
                "realtime_voice": cls._realtime_voice.get_active_id() or "marin",
            })
        except ConfigWriteError as e:
            cls._engine_status.set_markup(f"<small>❌ {e}</small>")
            return
        config_module.reload_config()
        print(f"🗣️  Talk engine: {engine}")
        cls._engine_status.set_markup(
            f"<small>✓ {engine} — takes effect on the next talk session.</small>")

    @classmethod
    def _refresh_engine_widgets(cls) -> None:
        """
        Put every widget back in step with CFG/STATE after a preset.

        A preset writes the same keys these combos edit; leaving them showing
        the old value would make the tab lie about what the app is doing.

        Read through ``config_module`` — as every reader in this file does
        of this file: ``reload_config()`` rebinds the singleton, so the imported
        name still points at the config as it was before the preset.
        """

        live = config_module.CFG
        if cls._engine_combo is not None:
            cls._engine_combo.set_active_id(live.TALK_ENGINE)
        if cls._tts_engine_combo is not None:
            cls._tts_engine_combo.set_active_id(
                STATE.tts_model if STATE.tts_model in live.TTS_ENGINES
                else next(iter(live.TTS_ENGINES)))
        # The preset already wrote STATE.tts_model, so the combo's own handler
        # sees nothing to change and returns before refilling the voices.
        cls._fill_voices()
        if cls._backend_combo is not None:
            current = live.BACKEND.strip().lower()
            for i, (bid, *_rest) in enumerate(cls._BACKENDS):
                if bid == current:
                    cls._backend_combo.set_active(i)
                    break

    # -----------------------------------------------------------------
    # Voice (TTS): which engine speaks, and with which voice
    # -----------------------------------------------------------------
    @classmethod
    def _build_voice_section(cls, vbox: Gtk.Box, labels: Gtk.SizeGroup) -> None:
        body = cls._group(
            vbox, "Voice",
            "Reads every AI answer aloud — F4, the rewrite, vision, and talk "
            "mode on the Cascade engine. Not the Realtime voice above: that one "
            "is the model speaking for itself, this one is a separate engine "
            "that speaks written text, so it works everywhere and can be local. "
            "Applies immediately.")

        cls._tts_engine_combo = Gtk.ComboBoxText()
        for model in config_module.CFG.TTS_ENGINES:
            cls._tts_engine_combo.append(model, cls._ENGINE_LABELS.get(model, model))
        cls._tts_engine_combo.set_active_id(
            STATE.tts_model if STATE.tts_model in config_module.CFG.TTS_ENGINES
            else next(iter(config_module.CFG.TTS_ENGINES)))
        cls._field(body, "Engine", cls._tts_engine_combo, labels)

        cls._voice_combo = Gtk.ComboBoxText()
        cls._fill_voices()
        cls._voice_combo.connect("changed", cls._on_voice_changed)
        test = Gtk.Button(label="▶ Test")
        test.set_tooltip_text("Speak a line with the selected engine and voice")
        test.connect("clicked", cls._on_test_voice)
        cls._field(body, "Voice", cls._voice_combo, labels, extra=test)

        cls._note(body,
                  "Piper needs no key and no network: its voice downloads once "
                  "(~60 MB) and is flatter than the cloud's.")
        # Connected last: filling the voices above must not fire the handler.
        cls._tts_engine_combo.connect("changed", cls._on_engine_changed)

    @classmethod
    def _on_provider_changed(cls, combo: Gtk.ComboBoxText) -> None:
        """Switch provider and re-list its models (the previous ids won't exist)."""
        pid = combo.get_active_id()
        if not pid or pid == STATE.ai_provider:
            return
        STATE.ai_provider = pid
        SettingsManager.save(STATE)
        print(f"🧠 AI provider changed to: {pid}")
        cls._load_models_async()

    @classmethod
    def _load_models_async(cls) -> None:
        """Fill both combos from ``models.list()`` — off-thread, it is a network call."""
        import threading
        provider = STATE.ai_provider
        cls._set_models_status(f"⏳ Listing {provider} models…")

        def work():
            from gi.repository import GLib
            from loquivox.api import get_ai_client
            try:
                ids = sorted(m.id for m in get_ai_client(provider).models.list().data)
                error = None
            except Exception as e:
                ids, error = [], str(e)

            def done():
                for combo, current in ((cls._chat_model, STATE.ai_chat_model),
                                       (cls._vision_model, STATE.ai_vision_model)):
                    if combo is None:
                        continue
                    combo.remove_all()
                    for mid in ids:
                        combo.append_text(mid)
                    combo.get_child().set_text(current)
                if error:
                    cls._set_models_status(f"⚠️ Could not list models — {error[:120]}")
                else:
                    cls._set_models_status(
                        f"✓ {len(ids)} models available on {provider}. "
                        "Pick or type one, then Apply.")
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def _set_models_status(cls, markup: str) -> None:
        if cls._models_status is not None:
            cls._models_status.set_markup(f"<small>{GLib.markup_escape_text(markup)}</small>")

    @classmethod
    def _on_apply_models(cls, _btn: Gtk.Button) -> None:
        """Persist the two model ids (the provider is saved as soon as it changes)."""
        chat = cls._chat_model.get_child().get_text().strip()
        vision = cls._vision_model.get_child().get_text().strip()
        if not chat or not vision:
            cls._set_models_status("⚠️ Both models need an id.")
            return
        STATE.ai_chat_model, STATE.ai_vision_model = chat, vision
        SettingsManager.save(STATE)
        print(f"🧠 Models applied: chat={chat} vision={vision} ({STATE.ai_provider})")
        cls._set_models_status(f"✓ Applied — chat {chat}, vision {vision}.")

    # -----------------------------------------------------------------
    # Appearance tab: TTS voice + colour scheme gallery + overlay preview
    # -----------------------------------------------------------------
    @classmethod
    def _build_appearance_page(cls, vbox: Gtk.Box) -> None:
        # The voice lives in the Models tab, with the rest of "who does what".
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        # The preview leads the page: the two groups below only make sense as
        # changes to the thing being previewed, so it goes first, once.
        body = cls._group(
            vbox, "Recording overlay",
            "The bubble that appears while you hold a hotkey. Everything here "
            "is previewed live below; the style and the two columns apply to "
            "the next recording.")

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
            "pill": "Pill — waveform capsule",
            "classic": "Classic — icon + bars",
        }
        for sid in config_module.CFG.OVERLAY_STYLES:
            style_combo.append(sid, _STYLE_LABELS.get(sid, sid.title()))
        active_style = STATE.overlay_style if STATE.overlay_style in config_module.CFG.OVERLAY_STYLES else config_module.CFG.DEFAULT_OVERLAY_STYLE
        style_combo.set_active_id(active_style)
        style_combo.connect("changed", cls._on_overlay_style_changed)
        cls._field(body, "Style", style_combo, labels)

        # Hotkey hints column (widens the overlay). Applies to the next recording.
        hints_check = Gtk.CheckButton(
            label="Show available hotkeys next to the waveform"
        )
        hints_check.set_active(STATE.show_hints)
        hints_check.connect("toggled", cls._on_hints_toggled)
        body.pack_start(hints_check, False, False, 0)

        # Refinement chip (dictation only). Applies to the next recording.
        badge_check = Gtk.CheckButton(
            label="Show the refinement level applied to the dictation"
        )
        badge_check.set_active(STATE.show_refine_badge)
        badge_check.connect("toggled", cls._on_refine_badge_toggled)
        body.pack_start(badge_check, False, False, 0)

        # Colour scheme gallery — judged on the preview above.
        body = cls._group(
            vbox, "Color scheme",
            "Applies immediately, to the overlay above and to the chat bubble.")

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
            vbox, "Desktop",
            "Chrome that lives outside a recording.")
        bar_check = Gtk.CheckButton(
            label="Hotkey tab at the top of the screen (hover to expand)"
        )
        bar_check.set_active(STATE.show_hotkey_bar)
        bar_check.connect("toggled", cls._on_hotkey_bar_toggled)
        body.pack_start(bar_check, False, False, 0)

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
            cr, bw, bh, scheme=scheme, mode="dictation", text="Listening…",
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
            vbox, "Transcription",
            "The ears: what turns your voice into text, for every mode. "
            "Applied live.")

        # Backend selector
        cls._backend_combo = Gtk.ComboBoxText()
        current = config_module.CFG.BACKEND.strip().lower()
        active_idx = 0
        for i, (bid, label, _stream, _key, _attr) in enumerate(cls._BACKENDS):
            cls._backend_combo.append_text(label)
            if bid == current:
                active_idx = i
        cls._backend_combo.set_active(active_idx)
        cls._backend_combo.connect("changed", cls._on_backend_changed)
        cls._field(body, "Backend", cls._backend_combo, labels)

        # Model: editable combo — pick a known id or type a custom one.
        cls._model_entry = Gtk.ComboBoxText.new_with_entry()
        cls._model_entry.get_child().set_hexpand(True)
        cls._field(body, "Model", cls._model_entry, labels)

        # Language: editable combo — common languages + "Autodetect".
        cls._lang_entry = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            cls._lang_entry.append(code, f"{label}" + (f" ({code})" if code else ""))
        # Select the row matching the current code, else put the raw code in.
        if not cls._lang_entry.set_active_id(config_module.CFG.WHISPER_LANGUAGE):
            cls._lang_entry.get_child().set_text(config_module.CFG.WHISPER_LANGUAGE)
        cls._field(body, "Language", cls._lang_entry, labels)

        # Microphone: "System default" + every detected capture device.
        cls._mic_combo = Gtk.ComboBoxText()
        cls._mic_combo.append("", "System default")
        from loquivox.services.audio import list_input_devices
        for name in list_input_devices():
            cls._mic_combo.append(name, name)
        # Restore the saved choice; if it's set but not currently present
        # (mic unplugged), still show it so the selection isn't silently lost.
        if not cls._mic_combo.set_active_id(config_module.CFG.INPUT_DEVICE):
            if config_module.CFG.INPUT_DEVICE:
                cls._mic_combo.append(config_module.CFG.INPUT_DEVICE, f"{config_module.CFG.INPUT_DEVICE} (not connected)")
                cls._mic_combo.set_active_id(config_module.CFG.INPUT_DEVICE)
            else:
                cls._mic_combo.set_active(0)
        cls._field(body, "Microphone", cls._mic_combo, labels)

        # Vocabulary: each engine's own way to stop dropping/mangling words it
        # doesn't expect (Whisper `prompt`, Deepgram `keyterm`).
        cls._vocab_entry = Gtk.Entry()
        cls._vocab_entry.set_hexpand(True)
        cls._vocab_entry.set_text(config_module.CFG.VOCABULARY)
        cls._vocab_entry.set_placeholder_text("proper nouns, jargon, foreign words…")
        cls._vocab_entry.set_tooltip_text(
            "Comma-separated words the engine tends to drop or misspell — "
            "proper nouns, technical terms, foreign words. Write them in the "
            "dictation language, e.g. “Loquivox, Wayland, pull request, deploy”.\n\n"
            "Groq / whisper.cpp: sent as Whisper's prompt. "
            "Deepgram: sent as keyterms (nova-3 only). "
            "OpenAI Realtime: not supported."
        )
        cls._field(body, "Vocabulary", cls._vocab_entry, labels)

        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 2)

        # Offline fallback toggle
        cls._fallback_check = Gtk.CheckButton(label="Fall back to whisper.cpp when a backend is unavailable")
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
        dl_btn = Gtk.Button(label="Install / download")
        dl_btn.connect("clicked", cls._on_download_whispercpp)
        whisper_row.pack_start(cls._whisper_status, True, True, 0)
        whisper_row.pack_end(dl_btn, False, False, 0)
        body.pack_start(whisper_row, False, False, 0)

        cls._trans_status = Gtk.Label()
        cls._actions(body, cls._on_apply_transcription, cls._trans_status)

        cls._sync_model_entry()  # prefill model + capability hint

    @staticmethod
    def _row_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
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
            combo.get_child().set_placeholder_text("(chosen automatically)")
        else:
            combo.set_sensitive(True)
            for m in cls._MODELS.get(bid, []):
                combo.append_text(m)
            combo.get_child().set_text(getattr(config_module.CFG, cfg_attr))

        cap = "🔴 live transcription" if is_stream else "📝 batch (transcribing… indicator)"
        doc = cls._MODEL_DOCS.get(bid)
        hint = cap + (f" · models: {doc}" if doc else "")
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
            msg = "whisper.cpp: ⚪ engine not found (<tt>whisper-cli</tt> missing)"
        elif downloaded:
            msg = f"whisper.cpp: ✅ model <b>{model}</b> downloaded (offline ready)"
        else:
            msg = f"whisper.cpp: ⬇ model <b>{model}</b> not downloaded yet"
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
            status("whisper.cpp: ❌ engine binary (<tt>whisper-cli</tt>) not found")
            GLib.idle_add(lambda: btn.set_sensitive(True))
            return

        # Download the ggml model on first use (the only thing we fetch).
        if not backend.is_model_downloaded():
            status(f"whisper.cpp: ⏳ downloading model <b>{model}</b>…")
            try:
                backend._download_model()
            except Exception as e:
                status(f"whisper.cpp: ❌ download failed: {e}")
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

        mic = cls._mic_combo.get_active_id() or ""  # "" = system default
        values = {"backend": bid, "language": language, "fallback": fallback,
                  "input_device": mic,
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
            cls._set_trans_status("⚠️ Saved. No usable backend — fallback will be used.")
            return
        if active.is_available():
            cls._set_trans_status(f"✓ Applied live — backend <b>{active.name}</b>.")
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
                    f"⚠️ {human} needs its API key — set <b>{env_key}</b> in "
                    "“API Keys”, then Apply. (Offline fallback used meanwhile.)"
                )
                return
        if bid in ("whispercpp", "auto"):
            cls._set_trans_status(
                "⚠️ Saved. whisper.cpp isn’t ready — use “Install / download” above."
            )
            return
        cls._set_trans_status(
            f"⚠️ Saved, but <b>{active.name}</b> is unavailable — fallback will be used."
        )

    @classmethod
    def _install_backend_pkg_async(cls, bid: str, pip_name: str, human: str) -> None:
        """pip-install a streaming backend's package, then re-apply."""
        import threading
        cls._set_trans_status(f"⏳ Installing {human} ({pip_name})…")

        def work():
            from gi.repository import GLib
            ok, msg = cls._pip_install(pip_name)

            def done():
                if not ok:
                    cls._set_trans_status(f"❌ {human} install failed — {msg}")
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
    _MANAGED_ENV_HINT = (
        "managed install — add this optional backend via your package "
        "manager (or reinstall in a venv), not from the app"
    )

    @staticmethod
    def _pip_install(pip_name: str):
        """Run pip install in-process venv. Returns (ok, last_output_line)."""
        import importlib
        import subprocess
        import sys
        if not SettingsDialog._can_pip_install():
            return False, SettingsDialog._MANAGED_ENV_HINT
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name],
                capture_output=True, text=True, timeout=900,
            )
        except Exception as e:
            return False, str(e)
        importlib.invalidate_caches()
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, (tail[-1] if tail else "see logs")
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
            if text.lower() == lbl.lower():
                return code
        return text  # assume the user typed a raw code

    # -----------------------------------------------------------------
    # Post-processing: one Refinement level (0-4) + optional Translate
    # -----------------------------------------------------------------
    @classmethod
    def _build_postprocess_section(cls, vbox: Gtk.Box) -> None:
        from loquivox.config import POSTPROCESS_LEVELS, POSTPROCESS_MAX_LEVEL

        body = cls._group(
            vbox, "Refinement",
            "What an LLM does to a dictation before it is typed. Uses the chat "
            "model from the Models tab (needs its API key); Off adds no "
            "latency.")

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
            cls._pp_scale.add_mark(level, Gtk.PositionType.BOTTOM, label)
        cls._pp_scale.set_value(int(config_module.CFG.POSTPROCESS_LEVEL or 0))
        # Snap to whole levels while dragging, and show the level name live.
        cls._pp_scale.connect("change-value", cls._on_pp_scale_change)
        cls._pp_scale.connect("value-changed", lambda _s: cls._refresh_pp_scale_label())
        cls._refresh_pp_scale_label()
        body.pack_start(cls._pp_scale, False, False, 0)

        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                        False, False, 6)

        # Translate — a separate axis; when on it overrides the level.
        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._pp_translate_check = Gtk.CheckButton(label="Translate to")
        cls._pp_translate_check.set_active(bool(config_module.CFG.POSTPROCESS_TRANSLATE))
        cls._pp_translate_check.connect("toggled", cls._on_pp_translate_toggled)
        trow.pack_start(cls._pp_translate_check, False, False, 0)
        cls._pp_lang = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            if code:  # translation needs a real target
                cls._pp_lang.append(code, f"{label} ({code})")
        if not cls._pp_lang.set_active_id(config_module.CFG.POSTPROCESS_TARGET_LANG):
            cls._pp_lang.get_child().set_text(config_module.CFG.POSTPROCESS_TARGET_LANG)
        trow.pack_start(cls._pp_lang, True, True, 0)
        body.pack_start(trow, False, False, 0)

        # Format — a separate axis that COMBINES with the level / translate.
        cls._pp_format_check = Gtk.CheckButton(
            label="Format as structured text (paragraphs + bullet lists)")
        cls._pp_format_check.set_active(bool(config_module.CFG.POSTPROCESS_FORMAT))
        cls._pp_format_check.set_tooltip_text(
            "Lays the result out in plain-text paragraphs and lists. Combines "
            "with the refinement level (or works alone when level is Off).")
        body.pack_start(cls._pp_format_check, False, False, 0)

        # Advanced: a custom prompt that overrides the level's built-in prompt.
        # Folded away — it is a 120px text box that only the Custom level reads,
        # and unfolded it dominated a tab whose real control is one slider.
        cls._pp_prompt_label = Gtk.Expander(label="Custom level prompt")
        cls._pp_prompt_label.set_tooltip_text(
            "Used when the refinement level is set to Custom.")
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

        clear_btn = Gtk.Button(label="Clear custom prompt")
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
        """Translate on → only the language matters; off → the level + prompt do."""
        translate = bool(cls._pp_translate_check.get_active()) if cls._pp_translate_check else False
        if cls._pp_lang:
            cls._pp_lang.set_sensitive(translate)
        for w in (cls._pp_scale, cls._pp_scale_label,
                  cls._pp_prompt_view, cls._pp_prompt_scroll, cls._pp_prompt_label):
            if w:
                w.set_sensitive(not translate)

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
        name = dict(POSTPROCESS_LEVELS).get(level, level)
        cls._pp_scale_label.set_markup(f"<small><b>Refinement level:</b> {name}</small>")

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
            desc = f"translate → {lang}"
        else:
            desc = f"level {level} ({dict(POSTPROCESS_LEVELS).get(level, level)})"
        if fmt:
            desc += " + format"
        cls._pp_status.set_markup(f"<small>✓ Applied live — {desc}.</small>")
        print(f"✨ Post-processing: level={level} translate={translate} format={fmt} lang={lang}")

    # -----------------------------------------------------------------
    # Talk mode section (written to config.toml [talk])
    # -----------------------------------------------------------------
    #: (id, label) for the "how far it digs" combo — mirrors CFG.TALK_DEPTH.
    _TALK_DEPTHS = (
        ("minimal", "Minimal — only asks when something is unclear"),
        ("normal", "Normal — asks about what would change the text"),
        ("deep", "Deep — pushes the reasoning further"),
    )

    _SEMANTIC_LABEL_BASE = "End my turns on meaning, not on silence"

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
    def _build_talk_section(cls, vbox: Gtk.Box) -> None:
        """Talk-mode knobs: who may end the briefing, and how it converses."""
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        body = cls._group(
            vbox, "Conversation",
            f"How talk mode behaves once {config_module.CFG.HOTKEY_DEFS['talk'][0]} "
            "opens a session. Which engine holds it is on the Models tab.")

        cls._talk_depth = Gtk.ComboBoxText()
        for depth_id, label in cls._TALK_DEPTHS:
            cls._talk_depth.append(depth_id, label)
        cls._talk_depth.set_active_id(
            config_module.CFG.TALK_DEPTH if config_module.CFG.TALK_DEPTH in dict(cls._TALK_DEPTHS) else "normal")
        cls._field(body, "How far it digs", cls._talk_depth, labels)

        cls._talk_semantic_check = Gtk.CheckButton(
            label=cls._SEMANTIC_LABEL_BASE + (
                " (offers to install onnxruntime)" if cls._semantic_needs_onnx() else ""))
        cls._talk_semantic_check.set_active(bool(config_module.CFG.TALK_SEMANTIC_TURNS))
        cls._talk_semantic_check.connect("toggled", cls._on_talk_semantic_toggled)
        body.pack_start(cls._talk_semantic_check, False, False, 0)

        cls._talk_speak_check = Gtk.CheckButton(
            label="Read the assistant's replies aloud, even with TTS off")
        cls._talk_speak_check.set_active(bool(config_module.CFG.TALK_SPEAK_REPLIES))
        body.pack_start(cls._talk_speak_check, False, False, 0)

        body = cls._group(
            vbox, "Ending the conversation",
            "Enter always writes the text — that one can't be turned off, so "
            "there is always a way out. These are extras.")

        cls._talk_phrase_check = Gtk.CheckButton(
            label="When I say so out loud (“vas-y”, “j'ai fini”, “that's it”)")
        cls._talk_phrase_check.set_active(bool(config_module.CFG.TALK_FINISH_ON_PHRASE))
        body.pack_start(cls._talk_phrase_check, False, False, 0)

        cls._talk_model_check = Gtk.CheckButton(
            label="When the assistant judges it has enough context")
        cls._talk_model_check.set_active(bool(config_module.CFG.TALK_FINISH_BY_MODEL))
        body.pack_start(cls._talk_model_check, False, False, 0)

        cls._talk_autopaste_check = Gtk.CheckButton(
            label="Paste the finished text straight away, without reviewing it")
        cls._talk_autopaste_check.set_active(bool(config_module.CFG.TALK_AUTO_PASTE))
        cls._talk_autopaste_check.set_tooltip_text(
            "The text is copied and pasted where the cursor was when the "
            "conversation ended — no Enter, and no chance to rewrite it or go "
            "back to the conversation. Off by default: talk mode otherwise "
            "never types anything you have not accepted."
        )
        body.pack_start(cls._talk_autopaste_check, False, False, 0)

        body = cls._group(
            vbox, "Instructions",
            "Standing instructions for the conversation: language, persona, "
            "tone, the kind of work you usually discuss. Added to the built-in "
            "prompt rather than replacing it, and read by both engines. Leave "
            "empty for the default.")

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
            "Example:\nTu réponds toujours en français, simplement et sans "
            "formalisme. Le contexte est technique : sois précis, et demande une "
            "précision plutôt que de deviner. Les seuls mots anglais sont les "
            "termes techniques."
        )
        instr_scroll.add(cls._talk_instructions)
        body.pack_start(instr_scroll, False, False, 0)

        body = cls._group(
            vbox, "Screen context",
            "Most of what you are about to dictate is already on your screen. "
            "The focused window is captured when the session starts, in "
            "parallel with your first sentence, and described by the vision "
            "model; <b>S</b> looks again once something has changed. Off by "
            "default — that window leaves the machine each time.")

        cls._talk_shot_check = Gtk.CheckButton(
            label="Let the assistant see what you are working in")
        cls._talk_shot_check.set_active(bool(config_module.CFG.TALK_SCREENSHOT))
        cls._talk_shot_check.connect("toggled", cls._on_talk_shot_toggled)
        body.pack_start(cls._talk_shot_check, False, False, 0)

        cls._talk_shot_region = Gtk.ComboBoxText()
        cls._talk_shot_region.append("window", "The focused window (recommended)")
        cls._talk_shot_region.append("cursor", "A box around the cursor (X11 / Hyprland)")
        cls._talk_shot_region.append("screen", "The whole screen")
        region = config_module.CFG.TALK_SCREENSHOT_REGION
        cls._talk_shot_region.set_active_id(
            region if region in ("window", "cursor", "screen") else "screen")
        cls._talk_shot_region.set_tooltip_text(
            "Cropping is what makes the text readable: an image gets a fixed "
            "token budget whatever its size, so a whole 4K desktop comes back "
            "as confident nonsense. A session that cannot do the framing you "
            "pick falls back to the whole screen and says so."
        )
        cls._field(body, "Capture", cls._talk_shot_region, labels)

        cls._talk_shot_cursor = Gtk.SpinButton.new_with_range(200, 7680, 100)
        cls._talk_shot_cursor.set_value(int(config_module.CFG.TALK_SCREENSHOT_CURSOR_PX))
        cls._talk_shot_cursor.set_halign(Gtk.Align.START)
        cls._field(body, "Box width (px)", cls._talk_shot_cursor, labels)

        cls._talk_shot_max = Gtk.SpinButton.new_with_range(0, 7680, 128)
        cls._talk_shot_max.set_value(int(config_module.CFG.TALK_SCREENSHOT_MAX_PX))
        cls._talk_shot_max.set_halign(Gtk.Align.START)
        cls._talk_shot_max.set_tooltip_text(
            "Longest edge of the uploaded image — what keeps a 4K capture small. "
            "0 uploads it as captured."
        )
        cls._field(body, "Downscale to (px)", cls._talk_shot_max, labels)

        cls._talk_shot_clip = Gtk.CheckButton(
            label="Put back what was on the clipboard afterwards")
        cls._talk_shot_clip.set_active(
            bool(config_module.CFG.TALK_SCREENSHOT_RESTORE_CLIPBOARD))
        cls._talk_shot_clip.set_tooltip_text(
            "Capturing a window copies the image to the clipboard on niri, "
            "which has no way to skip it. Off by default — the session ends by "
            "leaving its generated text there anyway, so the only loss is "
            "whatever you had copied a minute ago. Text only."
        )
        body.pack_start(cls._talk_shot_clip, False, False, 0)
        cls._on_talk_shot_toggled(cls._talk_shot_check)

        cls._note(vbox,
                  "Timings, prompts and the turn-detection thresholds live "
                  "under <tt>[talk]</tt> in config.toml.")

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
            text="Install onnxruntime?")
        ask.format_secondary_text(
            "Ending turns on meaning needs onnxruntime (~200 MB), plus an 8 MB "
            "model downloaded the first time it runs. Without it, turns keep "
            "ending on silence.")
        answer = ask.run()
        ask.destroy()
        if answer != Gtk.ResponseType.OK:
            check.set_active(False)
            return
        check.set_label(f"{cls._SEMANTIC_LABEL_BASE} — installing onnxruntime…")
        check.set_sensitive(False)

        def work():
            from gi.repository import GLib
            ok, msg = cls._pip_install("onnxruntime")

            def done():
                check.set_sensitive(True)
                if ok:
                    check.set_label(f"{cls._SEMANTIC_LABEL_BASE} (ready)")
                else:
                    check.set_active(False)
                    check.set_label(f"{cls._SEMANTIC_LABEL_BASE} — install failed: {msg}")
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def _on_talk_shot_toggled(cls, check: Gtk.CheckButton) -> None:
        """Grey out the framing controls while the capture itself is off."""
        active = bool(check.get_active())
        for widget in (cls._talk_shot_region, cls._talk_shot_cursor,
                       cls._talk_shot_max, cls._talk_shot_clip):
            if widget is not None:
                widget.set_sensitive(active)

    @classmethod
    def _on_apply_talk(cls, _btn: Gtk.Button) -> None:
        """Write the talk knobs to config.toml and reload — next session uses them."""
        from loquivox.config_io import ConfigWriteError, update_section

        phrase = bool(cls._talk_phrase_check.get_active())
        by_model = bool(cls._talk_model_check.get_active())
        depth = cls._talk_depth.get_active_id() or "normal"
        semantic = bool(cls._talk_semantic_check.get_active())
        speak = bool(cls._talk_speak_check.get_active())
        screenshot = bool(cls._talk_shot_check.get_active())
        auto_paste = bool(cls._talk_autopaste_check.get_active())
        buffer = cls._talk_instructions.get_buffer()
        instructions = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), False).strip()
        try:
            update_section("talk", {
                "finish_on_phrase": phrase,
                "finish_by_model": by_model,
                "depth": depth,
                "semantic_turns": semantic,
                "speak_replies": speak,
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
        ends = [name for name, on in (("Enter", True), ("spoken", phrase),
                                      ("assistant", by_model)) if on]
        instr_note = (f" · {len(instructions)} chars of instructions"
                      if instructions else "")
        if auto_paste:
            instr_note += " · pasted without review"
        cls._talk_status.set_markup(
            f"<small>✓ Applied — ends on: {', '.join(ends)}{instr_note}.</small>"
        )
        print(f"🗣️  Talk: finish_on_phrase={phrase} finish_by_model={by_model} "
              f"depth={depth} semantic_turns={semantic} screenshot={screenshot}")

    # -----------------------------------------------------------------
    # API keys section (#stored in secrets.env, applied live)
    # -----------------------------------------------------------------
    @classmethod
    def _build_api_keys_section(cls, vbox: Gtk.Box) -> None:
        import os
        from loquivox.secrets import MANAGED_KEYS, SECRETS_FILE, read_secrets

        body = cls._group(
            vbox, "Provider keys",
            f"Stored in <tt>{GLib.markup_escape_text(str(SECRETS_FILE))}</tt> "
            "(chmod 600) and loaded at startup, so they survive a reboot. "
            "Applied live on save.")

        stored = read_secrets()
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        cls._key_entries = {}

        for key, label in MANAGED_KEYS.items():
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_visibility(False)  # masked
            entry.set_text(stored.get(key, ""))
            # If a key is active via the inherited env (e.g. environment.d) but
            # not stored here, hint that without exposing it.
            if not stored.get(key) and os.environ.get(key):
                entry.set_placeholder_text("(set via environment — type to override)")
            cls._field(body, label, entry, labels)
            cls._key_entries[key] = entry

        show_chk = Gtk.CheckButton(label="Show the keys")
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
            f"<small>✓ Saved {n} key(s) (chmod 600) &amp; applied live.</small>"
        )
        # Refresh the transcription status hint if it's showing an availability warning.
        if cls._backend_combo is not None:
            cls._sync_model_entry()
        print("🔑 API keys saved & applied.")

    # -----------------------------------------------------------------
    # Hotkeys section (editable)
    # -----------------------------------------------------------------
    _HOTKEY_LABELS = {
        "dictation": "Dictation", "ai": "AI Chat", "ai_rewrite": "Rewrite",
        "vision": "Vision", "talk": "Talk", "pin": "Pin Chat", "tts": "TTS Toggle",
        "cancel": "Cancel", "pause": "Pause / Resume",
        "refine": "Stop + choose level",
    }

    @classmethod
    def _build_hotkeys_section(cls, vbox: Gtk.Box) -> None:
        """Editable per-mode key bindings (evdev key names), applied on restart."""
        # Two groups, split the way the app splits them: keys you hold to
        # record, and keys that act on the session in progress. That is the
        # distinction `KeyboardHandler._is_recording_mode` makes, and reading
        # ten bindings as one flat list is what hid it.
        recording = set(config_module.CFG.RECORDING_MODES)
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        bodies = {
            True: cls._group(
                vbox, "Recording",
                "Held down to record — release to transcribe."),
            False: cls._group(
                vbox, "Session actions",
                "Pressed once, acting on whatever is already running."),
        }

        cls._hotkey_entries = {}
        cls._hotkey_capture_btns = []

        for mode_id, (_label, specs) in config_module.CFG.HOTKEY_DEFS.items():
            name = cls._HOTKEY_LABELS.get(mode_id, mode_id.replace("_", " ").title())
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_text(" ".join(specs))
            entry.set_placeholder_text("unbound")

            capture_btn = Gtk.Button(label="⌨ Set")
            capture_btn.set_tooltip_text("Press a key to bind it (no need to know its name)")
            capture_btn.connect("clicked", cls._on_capture_hotkey, mode_id, entry)

            cls._field(bodies[mode_id in recording], name, entry, labels,
                       extra=capture_btn)
            cls._hotkey_entries[mode_id] = entry
            cls._hotkey_capture_btns.append(capture_btn)

        cls._note(vbox,
                  "Click <b>⌨ Set</b> then press a key or combo (e.g. Alt+Space) — "
                  "or type space-separated specs like <tt>ALT+SPACE F3</tt> "
                  "(first = primary, rest = aliases; combos join with <tt>+</tt>).")

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
                    cls._set_hotkey_status(f"❌ invalid binding '{tok}' for {mode_id}.")
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

        cls._set_hotkey_status("✓ Saved and applied — your new hotkeys work right away.")
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
        cls._set_hotkey_status(f"⌨ Press a key for <b>{label}</b>… (6s)")

        def work():
            from gi.repository import GLib
            from loquivox.handlers.keyboard import KeyboardHandler
            name = KeyboardHandler.capture_next_key()

            def done():
                if name:
                    entry.set_text(name)
                    cls._set_hotkey_status(
                        f"✓ Captured <b>{name}</b> for {label} — click <b>Apply</b> to save."
                    )
                else:
                    cls._set_hotkey_status("⚠️ No key captured (timed out or no input device).")
                cls._set_capture_enabled(True)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    # Engine labels: which provider speaks, and what it can speak.
    _ENGINE_LABELS = {
        "canopylabs/orpheus-v1-english": "Groq Orpheus — English only (needs GROQ_API_KEY)",
        "gpt-4o-mini-tts": "OpenAI — multilingual, speaks French (needs OPENAI_API_KEY)",
        "piper": "Piper — local, offline, no key (pip install piper-tts)",
    }

    @classmethod
    def _voices(cls) -> tuple:
        """The voices of the currently selected engine."""
        return config_module.CFG.TTS_ENGINES.get(STATE.tts_model, ("", ()))[1]

    @classmethod
    def _fill_voices(cls) -> None:
        """(Re)populate the voice combo for the current engine."""
        combo = cls._voice_combo
        if combo is None:
            return
        combo.remove_all()
        voices = cls._voices()
        for voice in voices:
            combo.append(voice, voice.title())
        combo.set_active_id(STATE.tts_voice if STATE.tts_voice in voices
                            else (voices[0] if voices else None))

    @classmethod
    def _on_engine_changed(cls, combo: Gtk.ComboBoxText) -> None:
        """Switch engine, then re-offer the voices that engine actually has."""
        model = combo.get_active_id()
        if not model or model == STATE.tts_model:
            return
        STATE.tts_model = model
        voices = cls._voices()
        if STATE.tts_voice not in voices and voices:
            STATE.tts_voice = voices[0]
        print(f"🔊 TTS engine changed to: {model} (voice {STATE.tts_voice})")
        SettingsManager.save(STATE)
        cls._fill_voices()

    @staticmethod
    def _on_voice_changed(combo: Gtk.ComboBoxText) -> None:
        """Handle voice selection change."""
        voice = combo.get_active_id()
        if not voice or voice == STATE.tts_voice:
            return
        STATE.tts_voice = voice
        print(f"🎙️ Voice changed to: {voice}")
        SettingsManager.save(STATE)

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
        desc_label.set_markup(f"<small><i>{scheme.get('desc', '')}</i></small>")

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
