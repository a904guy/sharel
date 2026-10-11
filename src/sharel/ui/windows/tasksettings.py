"""Task settings (ShareX's TaskSettingsForm) for the default settings or one workflow.

Sections: Task (workflows only: description, job, after-capture / after-upload tasks, destinations
and the FTP / custom uploader / screenshots folder overrides), General, Image, Capture, Upload,
Actions, Watch folders, Tools, Advanced. For a workflow each section has ShareX's "Use default
settings" switch (``use_default_*``); while it is on the section's rows are insensitive and the
task runs with the default section (see ``core.config.effective_task_settings``).

Every edit saves at once: the defaults into ApplicationConfig.json, a workflow into
HotkeysConfig.json.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...core.config import HotkeySettings, TaskSettings, WatchFolder
from ...core.nameparser import NameParser, NameParserType, WindowInfo
from ..forms import Form
from ._sectioned import SectionedWindow, WindowRegistry, button_row, group

log = logging.getLogger("sharel.ui.tasksettings")

_REGISTRY = WindowRegistry()

# ShareX's name pattern help menu (CodeMenu of NameParser tokens).
TOKEN_HELP: list[tuple[str, str]] = [
    ("%t", "Title of the active window"), ("%pn", "Process name of the active window"),
    ("%y", "Year"), ("%yy", "Year (2 digits)"), ("%mo", "Month"), ("%mon", "Month name"),
    ("%mon2", "Month name (short)"), ("%w", "Weekday name"), ("%w2", "Weekday name (short)"),
    ("%wy", "Week of the year"), ("%d", "Day"), ("%h", "Hour"), ("%mi", "Minute"), ("%s", "Second"),
    ("%ms", "Millisecond"), ("%pm", "AM/PM"), ("%unix", "Unix time"),
    ("%i", "Auto increment number (%i{3} pads to 3 digits)"), ("%ia", "Auto increment, base 36"),
    ("%iAa", "Auto increment, base 62"), ("%ib{16}", "Auto increment in base {n}"),
    ("%ix", "Auto increment, hexadecimal"), ("%rn", "Random digit (%rn{5} = 5 digits)"),
    ("%ra", "Random alphanumeric character (%ra{10})"), ("%rna", "Random non-ambiguous alphanumeric"),
    ("%rx", "Random hexadecimal digit"), ("%guid", "Random GUID"), ("%radjective", "Random adjective"),
    ("%ranimal", "Random animal"), ("%remoji", "Random emoji"), ("%rf{path}", "Random line from a file"),
    ("%width", "Image width"), ("%height", "Image height"), ("%un", "User name"), ("%uln", "User login name"),
    ("%cn", "Computer name"), ("%n", "New line"),
]

# (page name, title, use-default switch, attribute holding the section)
SECTIONS = [
    ("general", "General", "use_default_general_settings"),
    ("image", "Image", "use_default_image_settings"),
    ("capture", "Capture", "use_default_capture_settings"),
    ("upload", "Upload", "use_default_upload_settings"),
    ("actions", "Actions", "use_default_actions"),
    ("watch", "Watch folders", ""),
    ("tools", "Tools", "use_default_tool_settings"),
    ("advanced", "Advanced", "use_default_advanced_settings"),
]

CUSTOM_DESTINATIONS = {"image": "CustomImageUploader", "text": "CustomTextUploader", "file": "CustomFileUploader"}


def open_task_settings(app, hotkey: HotkeySettings | None = None,
                       on_close: Callable[[], None] | None = None) -> TaskSettingsWindow:
    key = (id(app), id(hotkey) if hotkey is not None else None)
    win = _REGISTRY.get_or_create(key, lambda: TaskSettingsWindow(app, hotkey))
    if on_close is not None and on_close not in win.on_close_callbacks:
        win.on_close_callbacks.append(on_close)
    return win


def destination_choices(category: str) -> list[tuple[str, str]]:
    """[(key "category/name", title)] for every working destination of a category (+ custom uploader)."""
    from ...uploaders import registry
    out = [(s.key, s.title) for s in registry.by_category(category) if s.available]
    out.sort(key=lambda kv: kv[1].lower())
    if category in CUSTOM_DESTINATIONS:
        out.append((f"{category}/{CUSTOM_DESTINATIONS[category]}", "Custom uploader"))
    return out


def preview_name(pattern: str, ts: TaskSettings, auto_increment: int = 0) -> str:
    """What ``pattern`` would produce now, with a sample window and image (no side effects)."""
    up = ts.upload_settings
    adv = ts.advanced_settings
    np = NameParser(NameParserType.FileName, window=WindowInfo("Window title", "process"), image_width=1920,
                    image_height=1080, auto_increment=auto_increment,
                    time_zone_offset_hours=up.custom_time_zone_offset if up.use_custom_time_zone else None,
                    max_name_length=adv.names_parser_max_name_length,
                    max_title_length=adv.names_parser_max_title_length)
    try:
        return np.parse(pattern)
    except Exception as e:
        return f"(invalid pattern: {e})"


def _sample_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (480, 320), (52, 101, 164, 255))
    d = ImageDraw.Draw(img)
    for i in range(0, 480, 40):
        d.rectangle([i, 0, i + 19, 320], fill=(114, 159, 207, 255))
    d.ellipse([170, 90, 310, 230], fill=(237, 212, 0, 255))
    return img


class TaskSettingsWindow(SectionedWindow):
    def __init__(self, app, hotkey: HotkeySettings | None) -> None:
        self.hotkey = hotkey
        self.is_default = hotkey is None
        self.ts: TaskSettings = app.store.app.default_task_settings if hotkey is None else hotkey.task_settings
        super().__init__(app, self._title(), 1000, 720)
        self.on_close_callbacks: list[Callable[[], None]] = []
        self.gates: dict[str, list[Gtk.Widget]] = {}
        self.switches: dict[str, Adw.SwitchRow] = {}
        self._names_dirty = False
        self.build()
        self.connect("close-request", self._on_close)

    def _title(self) -> str:
        return "Task settings" if self.is_default else f"Task settings: {self.hotkey.name}"

    # -- saving --

    def changed(self) -> None:
        if self.is_default:
            self.app.store.save_app()
        else:
            self.app.store.save_hotkeys()
        self.app.settings_changed()

    def _on_close(self, *_a) -> bool:
        if self.app.watchers is not None:
            self.app.watchers.apply()
        if self._names_dirty:
            self.app.register_hotkeys()
        for cb in self.on_close_callbacks:
            try:
                cb()
            except Exception:
                log.exception("task settings close callback failed")
        return False

    # -- building --

    def build(self) -> None:
        self.clear_pages()
        self.gates.clear()
        self.switches.clear()
        if not self.is_default:
            self.add_page("task", "Task", self._task_groups())
        builders = {"general": self._general_groups, "image": self._image_groups, "capture": self._capture_groups,
                    "upload": self._upload_groups, "actions": self._actions_groups, "watch": self._watch_groups,
                    "tools": self._tools_groups, "advanced": self._advanced_groups}
        for name, title, switch in SECTIONS:
            groups = builders[name]()
            if switch and not self.is_default:
                sw = self._use_default_switch(switch, groups)
                groups = [group("", "", [sw]), *groups]
            self.add_page(name, title, groups)

    def _use_default_switch(self, attr: str, gated: list[Gtk.Widget]) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title="Use default settings", subtitle="Take this section from the default task settings",
                            active=getattr(self.ts, attr))
        self.gates[attr] = gated
        self.switches[attr] = row

        def toggled(r, _p):
            setattr(self.ts, attr, r.get_active())
            self._gate(attr)
            self.changed()
        row.connect("notify::active", toggled)
        self._gate(attr)
        return row

    def _override_switch(self, attr: str, title: str, gated: list[Gtk.Widget]) -> Adw.SwitchRow:
        """ShareX's "Override …" check: the inverse of a ``use_default_*`` switch, or an ``override_*``
        switch as it is."""
        inverse = attr.startswith("use_default_")
        row = Adw.SwitchRow(title=title, active=getattr(self.ts, attr) != inverse)
        self.gates[attr] = gated
        self.switches[attr] = row

        def toggled(r, _p):
            setattr(self.ts, attr, r.get_active() != inverse)
            self._gate(attr)
            self.changed()
        row.connect("notify::active", toggled)
        self._gate(attr)
        return row

    def _gate(self, attr: str) -> None:
        on = bool(getattr(self.ts, attr))
        # use_default_* on -> section disabled; override_* on -> its rows enabled.
        enabled = on if attr.startswith("override_") else not on
        for w in self.gates.get(attr, []):
            w.set_sensitive(enabled)

    def form(self, obj, meta: dict | None = None) -> Form:
        return Form(obj, self.changed, parent=self, meta=meta)

    # -- Task (workflows) --

    def _task_groups(self) -> list[Gtk.Widget]:
        ts = self.ts
        desc = Adw.EntryRow(title="Description", text=ts.description)
        desc.set_tooltip_text("Workflow name; empty uses the task's name. Run it with sharel -workflow \"name\".")

        def on_desc(r):
            ts.description = r.get_text()
            self._names_dirty = True
            self.set_title(self._title())
            self.changed()
        desc.connect("changed", on_desc)
        from .hotkeysettings import job_picker, job_title
        job_row = Adw.ActionRow(title="Task", subtitle=f"{job_title(ts.job)} ({ts.job.value})")
        btn = Gtk.Button(label="Change task…", valign=Gtk.Align.CENTER)

        def picked(job):
            ts.job = job
            job_row.set_subtitle(f"{job_title(job)} ({job.value})")
            self._names_dirty = True
            self.set_title(self._title())
            self.changed()
        btn.connect("clicked", lambda *_: job_picker(self, picked))
        job_row.add_suffix(btn)
        self.job_row = job_row
        out = [group("Task", "", [desc, job_row])]

        f = self.form(ts)
        for title, attr, field_name in (("After capture", "use_default_after_capture_job", "after_capture_job"),
                                        ("After upload", "use_default_after_upload_job", "after_upload_job")):
            rows = f.rows(only=[field_name])
            sw = self._override_switch(attr, f"Override {title.lower()} tasks", rows)
            out.append(group(title, "", [sw, *rows]))
        dest_rows = f.rows(only=["image_destination", "image_file_destination", "text_destination",
                                 "text_file_destination", "file_destination", "url_shortener_destination",
                                 "url_sharing_service_destination"])
        sw = self._override_switch("use_default_destinations", "Override destinations", dest_rows)
        out.append(group("Destinations", "", [sw, *dest_rows]))

        # FTP account / custom uploader / screenshots folder overrides.
        from ...uploaders.services.file_ftp import FTPSettings
        accounts = self.app.store.uploaders.settings_for("file/FTP", FTPSettings).ftp_account_list
        ftp_names = [f"{i + 1}. {getattr(a, 'name', '') or getattr(a, 'host', '') or 'Account'}"
                     for i, a in enumerate(accounts)] or ["(no FTP accounts)"]
        ftp_combo = self._index_combo("FTP account", "ftp_index", ftp_names)
        items = self.app.store.uploaders.custom_uploader_items()
        cu_names = [f"{i + 1}. {getattr(it, 'name', '') or 'Custom uploader'}"
                    for i, it in enumerate(items)] or ["(no custom uploaders)"]
        cu_combo = self._index_combo("Custom uploader", "custom_uploader_index", cu_names)
        folder_rows = self.form(ts, {"screenshots_folder": {"kind": "folder"}}).rows(only=["screenshots_folder"])
        ov = []
        for attr, title, gated in (("override_ftp", "Override FTP account", [ftp_combo]),
                                   ("override_custom_uploader", "Override custom uploader", [cu_combo]),
                                   ("override_screenshots_folder", "Override screenshots folder", folder_rows)):
            ov.append(self._override_switch(attr, title, gated))
            ov += gated
        out.append(group("Overrides", "", ov))
        return out

    def _index_combo(self, title: str, attr: str, names: list[str]) -> Adw.ComboRow:
        cur = getattr(self.ts, attr)
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(names),
                           selected=cur if 0 <= cur < len(names) else 0)

        def sel(r, _p):
            setattr(self.ts, attr, int(r.get_selected()))
            self.changed()
        row.connect("notify::selected", sel)
        return row

    # -- General --

    def _general_groups(self) -> list[Gtk.Widget]:
        gs = self.ts.general_settings
        meta = {n: {"kind": "file"} for n in ("custom_capture_sound_path", "custom_task_completed_sound_path",
                                             "custom_action_completed_sound_path", "custom_error_sound_path")}
        f = self.form(gs, meta)
        return [
            f.group("Notifications", only=["play_sound_after_capture", "play_sound_after_upload",
                                           "play_sound_after_action", "show_toast_notification_after_task_completed",
                                           "disable_notifications", "disable_notifications_on_fullscreen"]),
            f.group("Toast window", only=["toast_window_duration", "toast_window_fade_duration",
                                          "toast_window_placement", "toast_window_size",
                                          "toast_window_left_click_action", "toast_window_right_click_action",
                                          "toast_window_middle_click_action", "toast_window_auto_hide"]),
            f.group("Custom sounds", only=[*meta, "use_custom_capture_sound", "use_custom_task_completed_sound",
                                           "use_custom_action_completed_sound", "use_custom_error_sound"]),
        ]

    # -- Image --

    def _image_groups(self) -> list[Gtk.Widget]:
        im = self.ts.image_settings
        meta = {"image_jpeg_quality": {"min": 0, "max": 100}, "image_webp_quality": {"min": 0, "max": 100},
                "image_auto_jpeg_quality": {"min": 0, "max": 100, "label": "Second format JPEG quality"},
                "image_auto_use_jpeg": {"label": "Use a second format when the image is too large"},
                "image_auto_use_jpeg_size": {"label": "Size limit (KB)", "min": 1},
                "image_format2": {"label": "Second format"},
                "thumbnail_width": {"min": 0}, "thumbnail_height": {"min": 0,
                                                                  "help": "0 keeps the aspect ratio"},
                "thumbnail_check_size": {"label": "Only create a thumbnail when the image is larger"}}
        f = self.form(im, meta)
        groups = [
            f.group("Image", only=["image_format", "image_png_bit_depth", "image_jpeg_quality", "image_webp_quality",
                                   "image_gif_quality", "file_exist_action"]),
            f.group("Size fallback", only=["image_auto_use_jpeg", "image_auto_use_jpeg_size", "image_format2",
                                           "image_auto_jpeg_quality"]),
            self._effects_group(im),
            f.group("Thumbnail", only=["thumbnail_width", "thumbnail_height", "thumbnail_name",
                                       "thumbnail_check_size"]),
        ]
        return groups

    def _effects_group(self, im) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title="Effects")
        self.effects_summary = Adw.ActionRow(title="Image effect presets")
        names = [str(p.get("name", f"Preset {i + 1}")) if isinstance(p, dict) else f"Preset {i + 1}"
                 for i, p in enumerate(im.image_effect_presets)]
        self._refresh_effects_summary()
        g.add(self.effects_summary)
        if names:
            sel = Adw.ComboRow(title="Preset used after capture", model=Gtk.StringList.new(names),
                               selected=min(max(0, im.selected_image_effect_preset), len(names) - 1))

            def on_sel(r, _p):
                im.selected_image_effect_preset = int(r.get_selected())
                self.changed()
            sel.connect("notify::selected", on_sel)
            g.add(sel)
        for r in self.form(im).rows(only=["show_image_effects_window_after_capture",
                                          "image_effect_only_region_capture"]):
            g.add(r)
        g.add(button_row("Edit presets", "Image effects…", self.open_effects))
        return g

    def _refresh_effects_summary(self) -> None:
        presets = self.ts.image_settings.image_effect_presets
        if not presets:
            self.effects_summary.set_subtitle("No presets (Image effects… to create one)")
            return
        parts = []
        for i, p in enumerate(presets):
            p = p if isinstance(p, dict) else {}
            n = len(p.get("effects", []) or [])
            parts.append(f"{p.get('name', f'Preset {i + 1}')} ({n} effect{'s' if n != 1 else ''})")
        self.effects_summary.set_subtitle(", ".join(parts))

    def open_effects(self) -> None:
        from .. import gtkutil
        try:
            from .effects import open_effects_window
        except ImportError as e:
            gtkutil.alert(self, "Image effects unavailable", f"The image effects window could not be loaded: {e}")
            return

        def done(_result=None):
            self.changed()
            self.build()
            self.show_page("image")
        open_effects_window(self.app, _sample_image(), self.ts, done)

    # -- Capture --

    def _capture_groups(self) -> list[Gtk.Widget]:
        cs = self.ts.capture_settings
        f = self.form(cs, {"capture_custom_window": {"help": "Window title or WM class for \"Capture pre "
                                                             "configured window\""},
                           "screenshot_delay": {"min": 0, "max": 300},
                           "capture_custom_region": {"label": "Custom region"}})
        groups = [f.group("Capture", only=["show_cursor", "capture_transparent", "capture_shadow",
                                           "capture_shadow_offset", "capture_client_area", "capture_auto_hide_taskbar",
                                           "screenshot_delay", "capture_backend", "capture_custom_region",
                                           "capture_custom_window"])]
        groups.append(group("Region capture", "", self.form(cs.surface_options).rows()))
        rec = cs.screen_recording
        groups.append(group("Screen recorder", "", self.form(rec).rows(skip=["ffmpeg"])))
        groups.append(group("Screen recorder: FFmpeg",
                            "Video/audio codecs and their options; screen video comes from a PipeWire screen cast.",
                            self.form(rec.ffmpeg).rows()))
        groups.append(group("Scrolling capture", "", self.form(cs.scrolling_capture).rows()))
        groups.append(group("OCR", "", self.form(cs.ocr).rows()))
        groups.append(group("Auto capture", "", self.form(cs.auto_capture).rows()))
        return groups

    # -- Upload --

    def _upload_groups(self) -> list[Gtk.Widget]:
        up = self.ts.upload_settings
        f = self.form(up)
        naming = Adw.PreferencesGroup(title="File naming")
        self.pattern_rows: dict[str, tuple[Adw.EntryRow, Gtk.Label]] = {}
        for attr, title in (("name_format_pattern", "Name pattern for capture or clipboard upload"),
                            ("name_format_pattern_active_window", "Name pattern for window capture")):
            entry, preview = self._pattern_row(up, attr, title)
            naming.add(entry)
            naming.add(preview)
        for r in f.rows(only=["use_custom_time_zone", "custom_time_zone_offset", "file_upload_use_named_pattern",
                              "file_upload_replace_problematic_characters"]):
            naming.add(r)
        return [
            naming,
            f.group("URL regex replace", only=["url_regex_replace", "url_regex_replace_pattern",
                                               "url_regex_replace_replacement"]),
            f.group("Clipboard upload", only=["clipboard_upload_url_contents", "clipboard_upload_shorten_url",
                                              "clipboard_upload_share_url", "clipboard_upload_auto_index_folder"]),
            self._filters_group(up),
        ]

    def _pattern_row(self, up, attr: str, title: str) -> tuple[Adw.EntryRow, Adw.ActionRow]:
        entry = Adw.EntryRow(title=title, text=getattr(up, attr))
        preview_row = Adw.ActionRow(title="Preview")
        preview_row.add_css_class("property")
        label = Gtk.Label(xalign=0, selectable=True, wrap=True)
        preview_row.add_suffix(label)

        def update(*_a):
            counter = self.app.store.app.name_parser_auto_increment_number
            label.set_label(preview_name(getattr(up, attr), self.ts, counter))

        def on_change(r):
            setattr(up, attr, r.get_text())
            update()
            self.changed()
        entry.connect("changed", on_change)
        menu = Gtk.MenuButton(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Insert token")
        menu.add_css_class("flat")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for token, desc in TOKEN_HELP:
            b = Gtk.Button()
            b.add_css_class("flat")
            row = Gtk.Box(spacing=12)
            t = Gtk.Label(label=token, xalign=0, width_chars=12)
            t.add_css_class("monospace")
            row.append(t)
            row.append(Gtk.Label(label=desc, xalign=0))
            b.set_child(row)
            b.set_tooltip_text(desc)
            b.connect("clicked", lambda *_a, tok=token: (self.insert_token(entry, tok), pop.popdown()))
            box.append(b)
        pop.set_child(Gtk.ScrolledWindow(child=box, min_content_height=360, propagate_natural_width=True))
        menu.set_popover(pop)
        entry.add_suffix(menu)
        update()
        self.pattern_rows[attr] = (entry, label)
        return entry, preview_row

    @staticmethod
    def insert_token(entry: Gtk.Editable, token: str) -> None:
        text = entry.get_text()
        pos = entry.get_position()
        pos = len(text) if pos < 0 or pos > len(text) else pos
        entry.set_text(text[:pos] + token + text[pos:])
        entry.set_position(pos + len(token))

    def _filters_group(self, up) -> Adw.PreferencesGroup:
        """Uploader filters: files whose extension matches go to another destination."""
        g = Adw.PreferencesGroup(title="Uploader filters",
                                 description="Files with these extensions (e.g. \"png jpg\" or \"*.mp4\") go to the "
                                             "chosen destination instead of the default one.")
        choices = [(k, f"{cat.capitalize()}: {t}") for cat in ("image", "text", "file")
                   for k, t in destination_choices(cat)]
        keys = [k for k, _t in choices]
        add = Gtk.Button(label="Add filter", valign=Gtk.Align.CENTER)
        g.set_header_suffix(add)
        self.filter_rows: list[Adw.ExpanderRow] = []

        def title_of(flt):
            return f"{flt.get('extensions', '') or '(no extensions)'} → {flt.get('destination', '') or '?'}"

        def rebuild():
            for r in self.filter_rows:
                g.remove(r)
            self.filter_rows.clear()
            for i, flt in enumerate(up.uploader_filters):
                exp = Adw.ExpanderRow(title=title_of(flt))
                en = Adw.SwitchRow(title="Enabled", active=bool(flt.get("enabled", True)))
                ext = Adw.EntryRow(title="Extensions", text=str(flt.get("extensions", "")))
                cur = f"{flt.get('category', 'file')}/{flt.get('destination', '')}"
                dest = Adw.ComboRow(title="Destination", model=Gtk.StringList.new([t for _k, t in choices]),
                                    selected=keys.index(cur) if cur in keys else 0)

                def set_enabled(r, _p, flt=flt):
                    flt["enabled"] = r.get_active()
                    self.changed()

                def set_ext(r, flt=flt, exp=exp):
                    flt["extensions"] = r.get_text().strip()
                    exp.set_title(title_of(flt))
                    self.changed()

                def set_dest(r, _p, flt=flt, exp=exp):
                    cat, _s, name = keys[r.get_selected()].partition("/")
                    flt["category"], flt["destination"] = cat, name
                    exp.set_title(title_of(flt))
                    self.changed()
                en.connect("notify::active", set_enabled)
                ext.connect("changed", set_ext)
                dest.connect("notify::selected", set_dest)
                for r in (en, ext, dest):
                    exp.add_row(r)
                rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
                rm.add_css_class("flat")
                rm.connect("clicked", lambda *_a, i=i: (up.uploader_filters.pop(i), self.changed(), rebuild()))
                exp.add_suffix(rm)
                g.add(exp)
                self.filter_rows.append(exp)

        def add_filter(*_a):
            cat, _s, name = keys[0].partition("/")
            up.uploader_filters.append({"enabled": True, "extensions": "", "category": cat, "destination": name})
            self.changed()
            rebuild()
        add.connect("clicked", add_filter)
        self.add_filter_button = add
        rebuild()
        return g

    # -- Actions --

    def _actions_groups(self) -> list[Gtk.Widget]:
        meta = {"is_active": {"label": "Enabled"}, "path": {"kind": "file", "label": "Program"},
                "args": {"label": "Arguments", "help": "%input = the file, %output = the output file"},
                "output_extension": {"help": "Set it when the program writes a new file (%output)"},
                "extensions": {"label": "Extensions filter", "help": "e.g. \"png jpg\"; empty = every file"},
                "external_programs": {"label": "Actions"}}
        rows = self.form(self.ts, meta).rows(only=["external_programs"])
        for r in rows:
            if isinstance(r, Adw.ExpanderRow):
                r.set_expanded(True)
        self.actions_row = rows[0]
        return [group("Actions", "Programs run on the file after capture/upload when \"Perform actions\" is in the "
                                 "after capture tasks. Arguments: %input = the file, %output = the program's output "
                                 "file (with the output extension).", rows)]

    # -- Watch folders --

    def _watch_groups(self) -> list[Gtk.Widget]:
        ts = self.ts
        enable = Adw.SwitchRow(title="Watch folders", subtitle="Upload new files that appear in these folders",
                               active=ts.watch_folder_enabled)
        g = Adw.PreferencesGroup(title="Folders")
        add = Gtk.Button(label="Add folder", valign=Gtk.Align.CENTER)
        g.set_header_suffix(add)
        self.watch_rows: list[Adw.ExpanderRow] = []
        self.watch_enable_row = enable
        self.add_watch_button = add

        def on_enable(r, _p):
            ts.watch_folder_enabled = r.get_active()
            self.apply_watchers()
        enable.connect("notify::active", on_enable)

        def rebuild():
            for r in self.watch_rows:
                g.remove(r)
            self.watch_rows.clear()
            for i, wf in enumerate(ts.watch_folder_list):
                g.add(self._watch_row(wf, i, rebuild))

        def add_folder(*_a):
            ts.watch_folder_list.append(WatchFolder())
            self.changed()
            rebuild()
            self.watch_rows[-1].set_expanded(True)
        add.connect("clicked", add_folder)
        rebuild()
        return [group("", "", [enable]), g]

    def _watch_row(self, wf: WatchFolder, index: int, rebuild) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=wf.folder_path or "(no folder)", subtitle=wf.filter)
        folder = Adw.EntryRow(title="Folder", text=wf.folder_path, show_apply_button=True)
        flt = Adw.EntryRow(title="Filter (e.g. *.png; *.jpg)", text=wf.filter, show_apply_button=True)

        def set_folder(r):
            wf.folder_path = r.get_text().strip()
            exp.set_title(wf.folder_path or "(no folder)")
            self.apply_watchers()

        def set_filter(r):
            wf.filter = r.get_text().strip() or "*.*"
            exp.set_subtitle(wf.filter)
            self.apply_watchers()
        folder.connect("apply", set_folder)
        flt.connect("apply", set_filter)
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
        browse.add_css_class("flat")

        def chosen(path):
            if path:
                folder.set_text(path)
                set_folder(folder)
        from .. import gtkutil
        browse.connect("clicked", lambda *_: gtkutil.select_folder(self, "Watch folder", chosen))
        folder.add_suffix(browse)
        exp.add_row(folder)
        exp.add_row(flt)
        for attr, title in (("include_subdirectories", "Include subdirectories"),
                            ("move_files_to_screenshots_folder", "Move files to the screenshots folder")):
            sw = Adw.SwitchRow(title=title, active=getattr(wf, attr))

            def toggled(r, _p, attr=attr):
                setattr(wf, attr, r.get_active())
                self.apply_watchers()
            sw.connect("notify::active", toggled)
            exp.add_row(sw)
        rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
        rm.add_css_class("flat")

        def remove(*_a):
            if wf in self.ts.watch_folder_list:
                self.ts.watch_folder_list.remove(wf)
            self.apply_watchers()
            rebuild()
        rm.connect("clicked", remove)
        exp.add_suffix(rm)
        exp.folder_entry = folder  # type: ignore[attr-defined]
        exp.filter_entry = flt  # type: ignore[attr-defined]
        self.watch_rows.append(exp)
        return exp

    def apply_watchers(self) -> None:
        self.changed()
        if self.app.watchers is not None:
            self.app.watchers.apply()

    # -- Tools / Advanced --

    def _tools_groups(self) -> list[Gtk.Widget]:
        tools = self.ts.tool_settings
        f = self.form(tools, {"indexer_custom_css": {"multiline": True}})
        prefixes = [("screen_color_picker", "Screen color picker"), ("indexer", "Directory indexer"),
                    ("image_combiner", "Image combiner"), ("image_splitter", "Image splitter"),
                    ("image_thumbnailer", "Image thumbnailer"), ("image_resizer", "Image resizer"),
                    ("image_converter", "Image converter"), ("image_watermark", "Image watermark"),
                    ("video_thumbnailer", "Video thumbnailer"), ("video_converter", "Video converter"),
                    ("border_window", "Borderless window")]
        import dataclasses
        names = [fl.name for fl in dataclasses.fields(tools)]
        groups = []
        used: set[str] = set()
        for prefix, title in prefixes:
            only = [n for n in names if n.startswith(prefix + "_") and n not in used]
            used.update(only)
            if only:
                groups.append(f.group(title, only=only))
        rest = [n for n in names if n not in used]
        if rest:
            groups.append(f.group("Other", only=rest))
        return groups

    def _advanced_groups(self) -> list[Gtk.Widget]:
        adv = self.ts.advanced_settings
        f = self.form(adv, {"text_custom": {"multiline": True}})
        return [
            f.group("Processing", only=["process_images_during_file_upload", "process_images_during_clipboard_upload",
                                        "process_images_during_browser_upload",
                                        "use_after_capture_tasks_during_file_upload", "upload_images_to_image_host",
                                        "upload_text_files_to_text_host", "text_task_save_as_file",
                                        "auto_clear_clipboard", "region_capture_disable_annotation",
                                        "image_extensions", "text_extensions"]),
            f.group("Text", only=["text_file_extension", "text_format", "text_custom", "text_custom_encode_input"]),
            f.group("Results", only=["early_copy_url", "result_force_https", "clipboard_content_format",
                                     "balloon_tip_content_format", "open_url_format", "auto_shorten_url_length",
                                     "auto_close_after_upload_form", "after_upload_form_show_qr_code"]),
            f.group("Name parser", only=["names_parser_max_name_length", "names_parser_max_title_length"]),
            f.group("Tools", only=["tool_settings_title"]),
        ]
