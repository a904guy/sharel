"""Custom uploader settings (ShareX's CustomUploaderSettingsForm).

Left: the list of custom uploaders with New / Remove / Duplicate / Move up / Move down, Import (.sxcu files,
clipboard JSON, URL), Export (selected .sxcu, all into a folder) and Clear. Dropping .sxcu files on the
window imports them. Right: the editor for every field of the selected uploader (name, destination types,
method, request URL, URL parameters / headers / body arguments tables, body type, file form name, JSON/XML
data with beautify/minify, URL / thumbnail URL / deletion URL / error message), syntax help that inserts
functions into the field being edited, a syntax tester, and per destination type the uploader "selected
for" it (``custom_*_selected``) with a Test button showing the result URL, request and raw response.

Everything is saved (debounced) to ``app.store.uploaders.custom_uploaders``.
"""
from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ...core import clipboard, desktop
from ...uploaders.custom import sxcu
from ...uploaders.custom import uploader as custom_uploader
from ...uploaders.custom.model import BODY_TYPES, DESTINATION_TYPES, METHODS, CustomUploaderItem
from .. import gtkutil

SAVE_DELAY_MS = 300
GUIDE_URL = "https://getsharex.com/docs/custom-uploader"

DEST_TITLES = {"ImageUploader": "Image uploader", "TextUploader": "Text uploader", "FileUploader": "File uploader",
               "URLShortener": "URL shortener", "URLSharingService": "URL sharing service"}
SELECTED_ATTR = {"ImageUploader": "custom_image_uploader_selected", "TextUploader": "custom_text_uploader_selected",
                 "FileUploader": "custom_file_uploader_selected", "URLShortener": "custom_url_shortener_selected",
                 "URLSharingService": "custom_url_sharing_service_selected"}

# (inserted text, description) — the syntax ShareX documents for custom uploaders
SYNTAX = [
    ("{response}", "The whole response body"),
    ("{responseurl}", "The final URL of the response (after redirects)"),
    ("{header:Location}", "A response header"),
    ("{json:data.link}", "A value from a JSON response (JSONPath, e.g. files[0].url or $.data.link)"),
    ("{xml:/upload/url}", "A value from an XML response (XPath)"),
    ("{regex:https?://\\S+|0}", "A regular expression match on the response: pattern|group"),
    ("{input}", "The text or URL being uploaded (text uploaders, shorteners, sharing services)"),
    ("{filename}", "The name of the file being uploaded"),
    ("{random:a|b|c}", "One of the values at random"),
    ("{select:a|b|c}", "Ask which value to use (a selection dialog)"),
    ("{inputbox:Title|default}", "Ask for a value (an input dialog)"),
    ("{outputbox:Title|text}", "Show text in a dialog after the upload"),
    ("{base64:user:password}", "Base64 encoding, e.g. for an Authorization: Basic header"),
]

_windows: dict[int, CustomUploadersWindow] = {}


def open_custom_uploaders(app) -> CustomUploadersWindow:
    win = _windows.get(id(app))
    if win is None:
        win = CustomUploadersWindow(app)
        _windows[id(app)] = win
        win.connect("unrealize", lambda w: _windows.get(id(app)) is w and _windows.pop(id(app), None))
    win.present()
    return win


class KeyValueTable(Gtk.Box):
    """Rows of (name, value) entries with add/remove — ShareX's parameter/header/argument grids."""

    def __init__(self, title: str, on_change: Callable[[dict[str, str]], None],
                 on_entry: Callable[[Gtk.Editable], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = title
        self.on_change = on_change
        self.on_entry = on_entry
        self.loading = False
        head = Gtk.Box(spacing=6)
        lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
        lbl.add_css_class("heading")
        head.append(lbl)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=f"Add {title.lower()} row")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.add_row("", "", focus=True))
        head.append(add)
        self.append(head)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(self.list)
        self.rows: list[tuple[Gtk.Box, Gtk.Entry, Gtk.Entry]] = []

    def set_values(self, values: dict[str, str]) -> None:
        self.loading = True
        for box, _k, _v in self.rows:
            self.list.remove(box)
        self.rows = []
        for k, v in values.items():
            self.add_row(k, v)
        self.loading = False

    def add_row(self, key: str, value: str, focus: bool = False) -> tuple[Gtk.Entry, Gtk.Entry]:
        box = Gtk.Box(spacing=4)
        k = Gtk.Entry(text=key, placeholder_text="Name", hexpand=True, tooltip_text=f"{self.title} name")
        v = Gtk.Entry(text=value, placeholder_text="Value", hexpand=True, tooltip_text=f"{self.title} value")
        rm = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=f"Remove {self.title.lower()} row")
        rm.add_css_class("flat")
        for e in (k, v):
            e.connect("changed", lambda *_: self._emit())
            fc = Gtk.EventControllerFocus()
            fc.connect("enter", lambda _c, e=e: self.on_entry(e))
            e.add_controller(fc)
        box.append(k)
        box.append(v)
        box.append(rm)
        entry = (box, k, v)
        rm.connect("clicked", lambda *_: self.remove_row(entry))
        self.list.append(box)
        self.rows.append(entry)
        if focus:
            k.grab_focus()
        self._emit()
        return k, v

    def remove_row(self, entry) -> None:
        if entry in self.rows:
            self.rows.remove(entry)
            self.list.remove(entry[0])
            self._emit()

    def values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for _b, k, v in self.rows:
            if k.get_text().strip():
                out[k.get_text().strip()] = v.get_text()
        return out

    def _emit(self) -> None:
        if not self.loading:
            self.on_change(self.values())


class CustomUploadersWindow(Adw.Window):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="Custom uploader settings", default_width=1100,
                         default_height=780)
        self.app = app
        self.cfg = app.store.uploaders
        self.items: list[CustomUploaderItem] = self.cfg.custom_uploader_items()
        self._loading = False
        self._save_source = 0
        self.busy = 0
        self.last_response = None
        self.last_editable: Gtk.Editable | None = None
        self.last_test: dict[str, Any] = {}

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        guide = Gtk.Button(label="Guide", tooltip_text="Custom uploader documentation")
        guide.connect("clicked", lambda *_: desktop.open_url(GUIDE_URL))
        header.pack_end(guide)
        help_btn = Gtk.MenuButton(label="Syntax help", tooltip_text="Insert a syntax function into the field "
                                  "being edited", popover=self._syntax_popover())
        header.pack_end(help_btn)
        toolbar.add_top_bar(header)
        self.toast = Adw.ToastOverlay()

        body = Gtk.Paned(position=290, shrink_start_child=False)
        body.set_start_child(self._build_list_side())
        body.set_end_child(self._build_editor())
        self.toast.set_child(body)
        toolbar.set_content(self.toast)
        self.set_content(toolbar)

        self._add_actions()
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)
        self.connect("close-request", self._on_close)
        self.refresh_list(0 if self.items else -1)

    # ---- layout ----

    def _build_list_side(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6, margin_end=6,
                       margin_top=6, margin_bottom=6, width_request=260)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_row_selected)
        side.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        bar = Gtk.Box(spacing=4, homogeneous=True)
        self.buttons: dict[str, Gtk.Button] = {}
        for label, icon, cb in (("New", "list-add-symbolic", self.add_new),
                                ("Remove", "list-remove-symbolic", self.remove_selected),
                                ("Duplicate", "edit-copy-symbolic", self.duplicate_selected),
                                ("Move up", "go-up-symbolic", lambda: self.move_selected(-1)),
                                ("Move down", "go-down-symbolic", lambda: self.move_selected(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=label)
            b.connect("clicked", lambda *_a, cb=cb: cb())
            bar.append(b)
            self.buttons[label] = b
        side.append(bar)
        io_bar = Gtk.Box(spacing=4, homogeneous=True)
        imp = Gio.Menu()
        imp.append("From files…", "cu.import-files")
        imp.append("From clipboard", "cu.import-clipboard")
        imp.append("From URL…", "cu.import-url")
        exp = Gio.Menu()
        exp.append("Export selected…", "cu.export")
        exp.append("Export all…", "cu.export-all")
        exp.append("Clear all uploaders", "cu.clear")
        io_bar.append(Gtk.MenuButton(label="Import", menu_model=imp, tooltip_text="Import custom uploaders"))
        io_bar.append(Gtk.MenuButton(label="Export", menu_model=exp, tooltip_text="Export custom uploaders"))
        side.append(io_bar)
        return side

    def _add_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, cb in (("import-files", self._import_files_dialog), ("import-clipboard", self.import_clipboard),
                         ("import-url", self._import_url_dialog), ("export", self._export_dialog),
                         ("export-all", self._export_all_dialog), ("clear", self._clear_dialog)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            group.add_action(a)
        self.insert_action_group("cu", group)
        self.action_group = group

    def _entry(self, title: str, attr: str, tooltip: str = "") -> Adw.EntryRow:
        row = Adw.EntryRow(title=title, use_markup=False)
        row.set_tooltip_text(tooltip or title)
        row.connect("changed", lambda r: self._set(attr, r.get_text()))
        self._track_focus(row)
        self.entries[attr] = row
        return row

    def _track_focus(self, editable: Gtk.Widget) -> None:
        fc = Gtk.EventControllerFocus()
        fc.connect("enter", lambda *_: self._remember(editable))
        editable.add_controller(fc)

    def _remember(self, editable) -> None:
        self.last_editable = editable

    def _build_editor(self) -> Gtk.Widget:
        self.entries: dict[str, Adw.EntryRow] = {}
        page = Adw.PreferencesPage()
        self.editor_page = page

        g = Adw.PreferencesGroup(title="Uploader")
        g.add(self._entry("Name", "name", "Name shown in menus; empty uses the request URL's host"))
        types = Adw.ExpanderRow(title="Destination type", use_markup=False, expanded=True)
        self.type_checks: dict[str, Gtk.CheckButton] = {}
        for t in DESTINATION_TYPES:
            r = Adw.ActionRow(title=DEST_TITLES[t], use_markup=False)
            c = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text=DEST_TITLES[t])
            c.connect("toggled", lambda *_: self._types_changed())
            r.add_prefix(c)
            r.set_activatable_widget(c)
            types.add_row(r)
            self.type_checks[t] = c
        g.add(types)
        page.add(g)

        g = Adw.PreferencesGroup(title="Request")
        self.method_row = Adw.ComboRow(title="Method", model=Gtk.StringList.new(list(METHODS)), use_markup=False)
        self.method_row.connect("notify::selected", lambda r, _p: self._set("request_method",
                                                                            METHODS[r.get_selected()]))
        g.add(self.method_row)
        g.add(self._entry("Request URL", "request_url", "e.g. https://example.com/upload"))
        page.add(g)

        g = Adw.PreferencesGroup(title="URL parameters and headers")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.params_table = KeyValueTable("URL parameters", lambda d: self._set("parameters", d), self._remember)
        self.headers_table = KeyValueTable("Headers", lambda d: self._set("headers", d), self._remember)
        box.append(self.params_table)
        box.append(self.headers_table)
        g.add(box)
        page.add(g)

        g = Adw.PreferencesGroup(title="Body")
        self.body_row = Adw.ComboRow(title="Body", model=Gtk.StringList.new(list(BODY_TYPES)), use_markup=False)
        self.body_row.connect("notify::selected", lambda r, _p: self._body_changed(BODY_TYPES[r.get_selected()]))
        g.add(self.body_row)
        self.args_table = KeyValueTable("Arguments", lambda d: self._set("arguments", d), self._remember)
        self.args_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8)
        self.args_box.append(self.args_table)
        g.add(self.args_box)
        self.file_form_row = self._entry("File form name", "file_form_name", "Multipart field holding the file")
        g.add(self.file_form_row)
        self.data_view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6,
                                      bottom_margin=6, left_margin=6, right_margin=6)
        self.data_view.set_tooltip_text("Data (JSON or XML body)")
        self.data_view.get_buffer().connect("changed", lambda b: self._set(
            "data", b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
        self._track_focus(self.data_view)
        data_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8)
        dl = Gtk.Label(label="Data", xalign=0)
        dl.add_css_class("heading")
        data_box.append(dl)
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.data_view, min_content_height=140))
        data_box.append(frame)
        fmt = Gtk.Box(spacing=6)
        self.beautify_btn = Gtk.Button(label="Beautify")
        self.beautify_btn.connect("clicked", lambda *_: self.format_data(minify=False))
        self.minify_btn = Gtk.Button(label="Minify")
        self.minify_btn.connect("clicked", lambda *_: self.format_data(minify=True))
        fmt.append(self.beautify_btn)
        fmt.append(self.minify_btn)
        data_box.append(fmt)
        self.data_box = data_box
        g.add(data_box)
        page.add(g)

        g = Adw.PreferencesGroup(title="Response")
        g.add(self._entry("URL", "url", "e.g. {json:data.link}; empty uses the whole response"))
        g.add(self._entry("Thumbnail URL", "thumbnail_url"))
        g.add(self._entry("Deletion URL", "deletion_url"))
        g.add(self._entry("Error message", "error_message", "Shown when the request fails, e.g. {json:error}"))
        syn = Adw.ActionRow(title="Test syntax", subtitle="Parse an expression against the last test response",
                            use_markup=False)
        self.syntax_btn = Gtk.Button(label="Test URL syntax…", valign=Gtk.Align.CENTER)
        self.syntax_btn.connect("clicked", lambda *_: self._syntax_test_dialog())
        syn.add_suffix(self.syntax_btn)
        g.add(syn)
        page.add(g)
        self.editor_groups = [w for w in self._groups(page)]

        g = Adw.PreferencesGroup(title="Selected custom uploaders",
                                 description="Which custom uploader each destination type uses")
        self.selected_rows: dict[str, Adw.ComboRow] = {}
        self.test_buttons: dict[str, Gtk.Button] = {}
        for t in DESTINATION_TYPES:
            r = Adw.ComboRow(title=DEST_TITLES[t], use_markup=False, model=Gtk.StringList.new([]))
            r.connect("notify::selected", lambda row, _p, t=t: self._selected_changed(t, row))
            b = Gtk.Button(label="Test", valign=Gtk.Align.CENTER, tooltip_text=f"Test the {DEST_TITLES[t].lower()}")
            b.connect("clicked", lambda *_a, t=t: self.test_selected(t))
            r.add_suffix(b)
            g.add(r)
            self.selected_rows[t] = r
            self.test_buttons[t] = b
        page.add(g)

        g = Adw.PreferencesGroup(title="Test result")
        self.result_views: dict[str, Gtk.TextView] = {}
        for key, title in (("result", "Result"), ("request", "Request"), ("response", "Response")):
            lbl = Gtk.Label(label=title, xalign=0, margin_top=6)
            lbl.add_css_class("heading")
            v = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6,
                             bottom_margin=6, left_margin=6, right_margin=6)
            v.set_tooltip_text(f"Test {title.lower()}")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.append(lbl)
            box.append(Gtk.Frame(child=Gtk.ScrolledWindow(child=v, min_content_height=90)))
            g.add(box)
            self.result_views[key] = v
        page.add(g)
        return page

    @staticmethod
    def _groups(page) -> list[Gtk.Widget]:
        out = []
        child = page.get_first_child()
        stack = [child] if child else []
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.PreferencesGroup):
                out.append(w)
                continue
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return out

    def _syntax_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6, margin_end=6,
                      margin_top=6, margin_bottom=6)
        intro = Gtk.Label(label="Click a function to insert it into the field you were editing.", xalign=0,
                          wrap=True, max_width_chars=50)
        intro.add_css_class("dim-label")
        box.append(intro)
        pop = Gtk.Popover(child=Gtk.ScrolledWindow(child=box, min_content_height=380, min_content_width=440,
                                                   propagate_natural_height=True))
        for token, desc in SYNTAX:
            b = Gtk.Button(tooltip_text=desc)
            b.add_css_class("flat")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            t = Gtk.Label(label=token, xalign=0, selectable=False)
            t.add_css_class("monospace")
            d = Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=50)
            d.add_css_class("dim-label")
            inner.append(t)
            inner.append(d)
            b.set_child(inner)
            b.connect("clicked", lambda *_a, token=token: (pop.popdown(), self.insert_syntax(token)))
            box.append(b)
        self.syntax_popover = pop
        return pop

    # ---- list ----

    def selected_index(self) -> int:
        row = self.listbox.get_selected_row()
        return row.get_index() if row is not None else -1

    def selected_item(self) -> CustomUploaderItem | None:
        i = self.selected_index()
        return self.items[i] if 0 <= i < len(self.items) else None

    def refresh_list(self, select: int | None = None) -> None:
        if select is None:
            select = self.selected_index()
        self._loading = True
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        for it in self.items:
            self.listbox.append(Gtk.Label(label=it.display_name, xalign=0, margin_start=8, margin_end=8,
                                          margin_top=6, margin_bottom=6))
        self._loading = False
        self._refresh_selected_rows()
        if 0 <= select < len(self.items):
            self.listbox.select_row(self.listbox.get_row_at_index(select))
        else:
            self.listbox.unselect_all()
            self.load_item(None)
        has = bool(self.items)
        for name in ("Remove", "Duplicate", "Move up", "Move down"):
            self.buttons[name].set_sensitive(has)

    def _refresh_names(self) -> None:
        for i, it in enumerate(self.items):
            row = self.listbox.get_row_at_index(i)
            if row is not None:
                row.get_child().set_label(it.display_name)
        self._refresh_selected_rows()

    def _refresh_selected_rows(self) -> None:
        names = [it.display_name for it in self.items]
        self._loading_sel = True
        for t, row in self.selected_rows.items():
            row.set_model(Gtk.StringList.new(names))
            if names:
                idx = min(max(getattr(self.cfg, SELECTED_ATTR[t]), 0), len(names) - 1)
                row.set_selected(idx)
            self.test_buttons[t].set_sensitive(bool(names))
        self._loading_sel = False

    def _selected_changed(self, dest_type: str, row: Adw.ComboRow) -> None:
        if getattr(self, "_loading_sel", False) or not self.items:
            return
        idx = row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION:
            setattr(self.cfg, SELECTED_ATTR[dest_type], int(idx))
            self.schedule_save()

    def _on_row_selected(self, _lb, row) -> None:
        if self._loading:
            return
        self.load_item(self.items[row.get_index()] if row is not None else None)

    def add_item(self, item: CustomUploaderItem) -> int:
        self.items.append(item)
        self._sync_cfg()
        self.refresh_list(len(self.items) - 1)
        self.schedule_save()
        return len(self.items) - 1

    def add_new(self) -> CustomUploaderItem:
        item = CustomUploaderItem()
        self.add_item(item)
        self.entries["name"].grab_focus()
        return item

    def remove_selected(self) -> None:
        i = self.selected_index()
        if not 0 <= i < len(self.items):
            return
        self.items.pop(i)
        reset = len(self.items) - 1
        for attr in SELECTED_ATTR.values():
            cur = getattr(self.cfg, attr)
            if cur == i:
                setattr(self.cfg, attr, max(reset, 0))
            elif cur > i:
                setattr(self.cfg, attr, cur - 1)
        self._sync_cfg()
        self.refresh_list(min(i, len(self.items) - 1))
        self.schedule_save()

    def duplicate_selected(self) -> None:
        item = self.selected_item()
        if item is not None:
            self.add_item(copy.deepcopy(item))

    def move_selected(self, delta: int) -> None:
        i = self.selected_index()
        j = i + delta
        if not (0 <= i < len(self.items) and 0 <= j < len(self.items)):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        for attr in SELECTED_ATTR.values():  # the selection follows the uploader
            cur = getattr(self.cfg, attr)
            if cur == i:
                setattr(self.cfg, attr, j)
            elif cur == j:
                setattr(self.cfg, attr, i)
        self._sync_cfg()
        self.refresh_list(j)
        self.schedule_save()

    def clear_all(self) -> None:
        self.items.clear()
        for attr in SELECTED_ATTR.values():
            setattr(self.cfg, attr, 0)
        self._sync_cfg()
        self.refresh_list(-1)
        self.schedule_save()

    def _sync_cfg(self) -> None:
        self.cfg.custom_uploaders = self.items

    # ---- editor ----

    def load_item(self, item: CustomUploaderItem | None) -> None:
        self._loading = True
        try:
            for g in self.editor_groups:
                g.set_sensitive(item is not None)
            it = item or CustomUploaderItem()
            for attr, row in self.entries.items():
                row.set_text(getattr(it, attr))
            for t, c in self.type_checks.items():
                c.set_active(t in it.destination_type)
            self.method_row.set_selected(METHODS.index(it.request_method) if it.request_method in METHODS else 1)
            self.body_row.set_selected(BODY_TYPES.index(it.body) if it.body in BODY_TYPES else 1)
            self.params_table.set_values(it.parameters)
            self.headers_table.set_values(it.headers)
            self.args_table.set_values(it.arguments)
            self.data_view.get_buffer().set_text(it.data)
            self._update_body_state(it.body)
        finally:
            self._loading = False

    def _set(self, attr: str, value: Any) -> None:
        if self._loading:
            return
        item = self.selected_item()
        if item is None:
            return
        setattr(item, attr, value)
        if attr in ("name", "request_url"):
            self._refresh_names()
        self.schedule_save()

    def _types_changed(self) -> None:
        self._set("destination_type", [t for t in DESTINATION_TYPES if self.type_checks[t].get_active()])

    def _body_changed(self, body: str) -> None:
        self._update_body_state(body)
        self._set("body", body)

    def _update_body_state(self, body: str) -> None:
        self.args_box.set_visible(body in ("MultipartFormData", "FormURLEncoded"))
        self.file_form_row.set_visible(body == "MultipartFormData")
        self.data_box.set_visible(body in ("JSON", "XML"))
        self.minify_btn.set_visible(body == "JSON")

    def format_data(self, minify: bool) -> None:
        """Beautify (JSON or XML) / minify (JSON) the Data template."""
        item = self.selected_item()
        if item is None or not item.data.strip():
            return
        try:
            if item.body == "XML":
                import xml.dom.minidom
                pretty = xml.dom.minidom.parseString(item.data.encode("utf-8")).toprettyxml(indent="  ")
                text = "\n".join(line for line in pretty.splitlines() if line.strip())
            else:
                obj = json.loads(item.data)
                text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) if minify else \
                    json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception as e:
            gtkutil.alert(self, "Data is not valid " + ("XML" if item.body == "XML" else "JSON"), str(e))
            return
        self.data_view.get_buffer().set_text(text)

    def insert_syntax(self, token: str) -> None:
        target = self.last_editable
        if target is None:
            target = self.entries["url"]
        if isinstance(target, Gtk.TextView):
            target.get_buffer().insert_at_cursor(token)
            return
        if isinstance(target, Gtk.Editable):
            pos = target.get_position()
            if pos < 0:
                pos = len(target.get_text())
            new_pos = target.insert_text(token, pos)
            target.set_position(new_pos if isinstance(new_pos, int) else pos + len(token))

    # ---- import / export ----

    def import_text(self, text: str | bytes, source: str = "clipboard") -> list[CustomUploaderItem]:
        try:
            items = sxcu.loads_many(text)
        except Exception as e:
            gtkutil.alert(self, f"Could not import from {source}", str(e))
            return []
        for it in items:
            self.items.append(it)
        if items:
            self._sync_cfg()
            self.refresh_list(len(self.items) - 1)
            self.schedule_save()
        return items

    def import_paths(self, paths: list[str]) -> list[CustomUploaderItem]:
        imported: list[CustomUploaderItem] = []
        errors = []
        for p in paths:
            try:
                imported += sxcu.load_many(p)
            except Exception as e:
                errors.append(f"{p}: {e}")
        if imported:
            self.items.extend(imported)
            self._sync_cfg()
            self.refresh_list(len(self.items) - 1)
            self.schedule_save()
        if errors:
            gtkutil.alert(self, "Some files could not be imported", "\n".join(errors))
        return imported

    def import_clipboard(self) -> list[CustomUploaderItem]:
        content = clipboard.read()
        if content.kind != "text" or not content.text.strip():
            gtkutil.alert(self, "The clipboard holds no text", "Copy the custom uploader JSON first.")
            return []
        return self.import_text(content.text)

    def import_url(self, url: str, done: Callable[[list], None] | None = None) -> None:
        """Download a .sxcu from ``url`` on a worker thread and import it."""
        from .destinations import make_context
        ctx = make_context(self.app)

        def fetch():
            resp = ctx.session.get(url, timeout=ctx.timeout)
            resp.raise_for_status()
            return resp.content
        def imported(data: bytes) -> None:
            items = self.import_text(data, url)
            if done is not None:
                done(items)
        self.run_worker(None, fetch, imported, "Import from URL")

    def export_to(self, path: str, item: CustomUploaderItem | None = None) -> str:
        item = item or self.selected_item()
        if item is None:
            return ""
        if not path.lower().endswith(".sxcu"):
            path += ".sxcu"
        return str(sxcu.dump(item, path))

    def export_all_to(self, folder: str) -> list[str]:
        return [str(p) for p in sxcu.dump_all(self.items, folder)]

    def _import_files_dialog(self) -> None:
        gtkutil.open_files(self, "Import custom uploaders", lambda ps: ps and self.import_paths(ps),
                           patterns=["*.sxcu", "*.json"], filter_name="Custom uploaders", multiple=True)

    def _import_url_dialog(self) -> None:
        gtkutil.prompt_text(self, "Import custom uploader from URL", "",
                            lambda url: url and url.strip() and self.import_url(url.strip()),
                            body="URL of a .sxcu file")

    def _export_dialog(self) -> None:
        item = self.selected_item()
        if item is None:
            return
        gtkutil.save_file(self, "Export custom uploader", sxcu.file_name(item),
                          lambda p: p and self._exported([self.export_to(p, item)]), patterns=["*.sxcu"])

    def _export_all_dialog(self) -> None:
        if not self.items:
            return
        gtkutil.select_folder(self, "Export all custom uploaders to",
                              lambda folder: folder and self._exported(self.export_all_to(folder)))

    def _exported(self, paths: list[str]) -> None:
        self.toast.add_toast(Adw.Toast(title=f"Exported {len(paths)} custom uploader(s)", timeout=3))

    def _clear_dialog(self) -> None:
        if not self.items:
            return
        gtkutil.ask(self, "Remove all custom uploaders?", f"{len(self.items)} custom uploader(s) will be removed.",
                    [("cancel", "Cancel"), ("clear", "Remove all")],
                    lambda r: r == "clear" and self.clear_all(), destructive="clear")

    def _on_drop(self, _target, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            self.import_paths(paths)
            return True
        return False

    # ---- test ----

    def run_worker(self, button: Gtk.Button | None, fn: Callable[[], Any], on_done: Callable[[Any], None],
                   what: str) -> None:
        if button is not None:
            button.set_sensitive(False)
        self.busy += 1

        def finish(ok, value):
            self.busy -= 1
            if button is not None:
                button.set_sensitive(True)
            if ok:
                on_done(value)
            else:
                gtkutil.alert(self, f"{what} failed", str(value) or value.__class__.__name__)

        def work():
            try:
                res = fn()
            except Exception as e:
                gtkutil.idle(finish, False, e)
                return
            gtkutil.idle(finish, True, res)
        threading.Thread(target=work, daemon=True, name=f"cu-{what}").start()

    def test_selected(self, dest_type: str) -> None:
        """The Test button of a "selected for" row: test that uploader as ``dest_type``."""
        if not self.items:
            return
        idx = self.selected_rows[dest_type].get_selected()
        if not 0 <= idx < len(self.items):
            return
        self.listbox.select_row(self.listbox.get_row_at_index(idx))  # ShareX selects the tested uploader
        if dest_type == "TextUploader":
            gtkutil.prompt_text(self, "Text upload test", custom_uploader.TEST_TEXT,
                                lambda text: text is not None and self.run_test(idx, dest_type, text),
                                multiline=True)
        else:
            self.run_test(idx, dest_type)

    def run_test(self, index: int, dest_type: str, text: str | None = None) -> None:
        from .destinations import make_context
        self.save_now()
        item = self.items[index]
        ctx = make_context(self.app)
        self._set_result("Testing…", "", "")

        def call():
            return custom_uploader.test(item, dest_type, ctx, text=text, on_response=self._keep_response)
        self.run_worker(self.test_buttons[dest_type], call, lambda res: self.show_result(dest_type, *res),
                        f"{DEST_TITLES[dest_type]} test")

    def _keep_response(self, info) -> None:
        self.last_response = info

    def show_result(self, dest_type: str, result, summary: str, raw: str) -> None:
        lines = []
        if result.url:
            lines.append(f"URL: {result.url}")
        if result.shortened_url and result.shortened_url != result.url:
            lines.append(f"Shortened URL: {result.shortened_url}")
        if result.thumbnail_url:
            lines.append(f"Thumbnail URL: {result.thumbnail_url}")
        if result.deletion_url:
            lines.append(f"Deletion URL: {result.deletion_url}")
        if result.errors:
            lines.append("Errors:\n" + "\n".join(result.errors))
        if not lines:
            lines.append("No URL returned" if result.is_url_expected else "Done")
        self.last_test = {"dest_type": dest_type, "result": result, "summary": summary, "raw": raw}
        self._set_result("\n".join(lines), summary, raw or result.response)

    def _set_result(self, result: str, request: str, response: str) -> None:
        for key, text in (("result", result), ("request", request), ("response", response)):
            self.result_views[key].get_buffer().set_text(text)

    def result_text(self, key: str) -> str:
        b = self.result_views[key].get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def syntax_test(self, expression: str) -> str:
        """``expression`` parsed against the last test response (ShareX's syntax test form)."""
        from ...uploaders.custom.parser import ResponseInfo, syntax_test
        from .destinations import make_context
        return syntax_test(expression, self.last_response or ResponseInfo(), ctx=make_context(self.app))

    def _syntax_test_dialog(self) -> None:
        item = self.selected_item()
        default = item.url if item else ""

        def done(expr):
            if expr is None:
                return
            gtkutil.prompt_text(self, "Syntax test result", self.syntax_test(expr), lambda _v: None,
                                body="" if self.last_response else "No test response yet: run a Test first.",
                                multiline=True)
        gtkutil.prompt_text(self, "Test URL syntax", default, done,
                            body="Expression to parse against the last test response")

    # ---- saving ----

    def schedule_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._flush)

    def _flush(self) -> bool:
        self._save_source = 0
        self.save_now()
        return GLib.SOURCE_REMOVE

    def save_now(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self._sync_cfg()
        self.app.store.save_uploaders()
        self.app.settings_changed()

    def _on_close(self, *_a) -> bool:
        if self._save_source:
            self.save_now()
        return False
