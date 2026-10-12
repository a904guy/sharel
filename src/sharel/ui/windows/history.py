"""History (ShareX's HistoryForm) and Image history (ImageHistoryForm).

History: a search box, an advanced search panel (file name, URL, date range, type and host filters,
Reset), the result list (date, file name, type, host, URL), a details pane with a preview, the item count
in the status bar and title, statistics, settings (max item count, remember search text), and the
HistoryItemManager context menu (open / copy in every format / show preview / upload file / edit image /
delete). Image history: a thumbnail grid of image items with search, "filter missing files", max item
count and thumbnail size, double-click opens, the same context menu.

Both follow ``app.history.listeners`` (items finished by tasks appear live).
"""
from __future__ import annotations

import datetime as _dt
import os
import queue
import threading
from collections import Counter
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ...core import clipboard, desktop
from ...core.history import IMAGE_EXTS, HistoryFilter, HistoryItem
from .. import gtkutil

TYPES = ["Image", "Text", "File", "URL"]
TEXT_EXTS = {"txt", "log", "nfo", "c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs", "vb", "html", "htm", "xhtml",
             "xht", "xml", "css", "js", "php", "bat", "java", "lua", "py", "pl", "cfg", "ini", "md", "json", "yml",
             "yaml", "sh", "csv"}

# (section, [(label, action name)]) — HistoryItemManager's context menu
CONTEXT_MENU = [
    ("Open", [("URL", "open-url"), ("Shortened URL", "open-short"), ("Thumbnail URL", "open-thumb"),
              ("Deletion URL", "open-del"), ("File", "open-file"), ("Folder", "open-folder")]),
    ("Copy", [("URL", "copy-url"), ("Shortened URL", "copy-short"), ("Thumbnail URL", "copy-thumb"),
              ("Deletion URL", "copy-del"), ("File", "copy-file"), ("Image", "copy-image"), ("Text", "copy-text"),
              ("HTML link", "copy-html-link"), ("HTML image", "copy-html-image"),
              ("HTML linked image", "copy-html-linked"), ("Forum (BBCode) link", "copy-forum-link"),
              ("Forum (BBCode) image", "copy-forum-image"), ("Forum (BBCode) linked image", "copy-forum-linked"),
              ("Markdown link", "copy-md-link"), ("Markdown image", "copy-md-image"),
              ("Markdown linked image", "copy-md-linked"), ("File path", "copy-path"), ("File name", "copy-name"),
              ("File name with extension", "copy-name-ext"), ("Folder", "copy-folder")]),
    ("", [("Show image preview", "preview"), ("Upload file", "upload"), ("Edit image", "edit-image")]),
    ("", [("Delete from history", "delete"), ("Delete file locally…", "delete-file")]),
]


def _ext(name: str) -> str:
    return Path(name.split("?")[0]).suffix.lower().lstrip(".")


def is_image_url(url: str) -> bool:
    return _ext(url) in IMAGE_EXTS


def has_file(it: HistoryItem) -> bool:
    return bool(it.file_path) and Path(it.file_path).is_file()


def is_image_file(it: HistoryItem) -> bool:
    return has_file(it) and _ext(it.file_path) in IMAGE_EXTS


def is_text_file(it: HistoryItem) -> bool:
    return has_file(it) and _ext(it.file_path) in TEXT_EXTS


def copy_text_for(action: str, items: list[HistoryItem]) -> str:
    """The text a Copy action puts on the clipboard for ``items`` (one line per item, as ShareX)."""
    out: list[str] = []
    for it in items:
        u, t = it.url, it.thumbnail_url
        v = {
            "copy-url": u,
            "copy-short": it.shortened_url,
            "copy-thumb": t,
            "copy-del": it.deletion_url,
            "copy-html-link": u and f'<a href="{u}">{u}</a>',
            "copy-html-image": u and is_image_url(u) and f'<img src="{u}"/>',
            "copy-html-linked": u and t and is_image_url(u) and f'<a href="{u}"><img src="{t}"/></a>',
            "copy-forum-link": u and f"[url]{u}[/url]",
            "copy-forum-image": u and is_image_url(u) and f"[img]{u}[/img]",
            "copy-forum-linked": u and t and is_image_url(u) and f"[url={u}][img]{t}[/img][/url]",
            "copy-md-link": u and f"[{it.file_name}]({u})",
            "copy-md-image": u and is_image_url(u) and f"![{it.file_name}]({u})",
            "copy-md-linked": u and t and is_image_url(u) and f"[![{it.file_name}]({t})]({u})",
            "copy-path": it.file_path,
            "copy-name": Path(it.file_path or it.file_name).stem if (it.file_path or it.file_name) else "",
            "copy-name-ext": Path(it.file_path).name if it.file_path else it.file_name,
            "copy-folder": str(Path(it.file_path).parent) if it.file_path else "",
        }.get(action, "")
        if v:
            out.append(v)
    return "\r\n".join(out)


def stats_text(items: list[HistoryItem]) -> str:
    """HistoryHelpers.OutputStats: totals per type, host and file extension."""
    lines = [f"Total: {len(items):,}", ""]
    for title, counter in (("Types", Counter(i.type for i in items if i.type)),
                           ("Hosts", Counter(i.host for i in items if i.host)),
                           ("File extensions", Counter(_ext(i.file_name or i.file_path) for i in items
                                                       if _ext(i.file_name or i.file_path)))):
        lines.append(f"{title}:")
        lines += [f"  {k}: {n:,}" for k, n in counter.most_common()] or ["  (none)"]
        lines.append("")
    return "\n".join(lines).rstrip()


class HistoryRow(GObject.Object):
    def __init__(self, item: HistoryItem) -> None:
        super().__init__()
        self.item = item


class HistoryActionsMixin:
    """The context menu actions on the selected history items (``self.selected_items()``)."""

    app: object

    def _install_actions(self, widget: Gtk.Widget) -> None:
        group = Gio.SimpleActionGroup()
        for _section, entries in CONTEXT_MENU:
            for _label, name in entries:
                a = Gio.SimpleAction.new(name, None)
                a.connect("activate", lambda _a, _p, n=name: self.run_action(n))
                group.add_action(a)
        widget.insert_action_group("hist", group)
        self.action_group = group

    def context_menu_model(self) -> Gio.Menu:
        m = Gio.Menu()
        for section, entries in CONTEXT_MENU:
            sec = Gio.Menu()
            for label, name in entries:
                sec.append(label, f"hist.{name}")
            if section:
                sub = Gio.Menu()
                sub.append_submenu(section, sec)
                m.append_section(None, sub)
            else:
                m.append_section(None, sec)
        return m

    def update_action_states(self) -> None:
        items = self.selected_items()
        one = items[0] if items else None
        enabled = {
            "open-url": bool(one and one.url), "open-short": bool(one and one.shortened_url),
            "open-thumb": bool(one and one.thumbnail_url), "open-del": bool(one and one.deletion_url),
            "open-file": bool(one and has_file(one)), "open-folder": bool(one and has_file(one)),
            "copy-file": any(has_file(i) for i in items), "copy-image": bool(one and is_image_file(one)),
            "copy-text": bool(one and is_text_file(one)), "preview": bool(one and is_image_file(one)),
            "upload": any(has_file(i) for i in items), "edit-image": bool(one and is_image_file(one)),
            "delete": bool(items), "delete-file": any(has_file(i) for i in items),
        }
        for _s, entries in CONTEXT_MENU:
            for _l, name in entries:
                if name.startswith("copy-") and name not in enabled:
                    on = bool(copy_text_for(name, items))
                else:
                    on = enabled.get(name, bool(items))
                self.action_group.lookup_action(name).set_enabled(on)

    def run_action(self, name: str) -> None:
        items = self.selected_items()
        if not items:
            return
        one = items[0]
        if name.startswith("open-"):
            if name == "open-file" and has_file(one):
                desktop.open_file(one.file_path)
            elif name == "open-folder" and has_file(one):
                desktop.show_in_folder(one.file_path)
            else:
                url = {"open-url": one.url, "open-short": one.shortened_url, "open-thumb": one.thumbnail_url,
                       "open-del": one.deletion_url}.get(name, "")
                if url:
                    desktop.open_url(url)
        elif name == "copy-file":
            files = [i.file_path for i in items if has_file(i)]
            if files:
                clipboard.set_files(files)
        elif name == "copy-image":
            if is_image_file(one):
                from ...core import imaging
                clipboard.set_image(imaging.load(one.file_path))
        elif name == "copy-text":
            if is_text_file(one):
                clipboard.set_text(Path(one.file_path).read_text(encoding="utf-8", errors="replace"))
        elif name.startswith("copy-"):
            text = copy_text_for(name, items)
            if text:
                clipboard.set_text(text)
        elif name == "preview":
            if is_image_file(one):
                show_image_preview(self.app, one.file_path, parent=self)
        elif name == "upload":
            paths = [i.file_path for i in items if has_file(i)]
            if paths:
                self.app.upload_paths(paths, self.app.store.task_settings_for(None))
        elif name == "edit-image":
            if is_image_file(one):
                from ...core.enums import HotkeyType
                from ...core.jobs import dispatch
                dispatch(self.app, HotkeyType["ImageEditor"], files=[one.file_path])
        elif name == "delete":
            self.delete_items(items)
        elif name == "delete-file":
            targets = [i for i in items if has_file(i)]
            if targets:
                gtkutil.ask(self, "Delete file locally?",
                            "\n".join(i.file_path for i in targets[:10]) + ("\n…" if len(targets) > 10 else ""),
                            [("cancel", "Cancel"), ("delete", "Delete")],
                            lambda r: r == "delete" and self.delete_files(targets), destructive="delete")

    def delete_items(self, items: list[HistoryItem]) -> None:
        for it in items:
            self.app.history.remove(it.id)
        self.refresh()

    def delete_files(self, items: list[HistoryItem]) -> None:
        for it in items:
            try:
                os.remove(it.file_path)
            except OSError as e:
                gtkutil.alert(self, "Could not delete the file", f"{it.file_path}: {e.strerror}")
                continue
            self.app.history.remove(it.id)
        self.refresh()

    def _attach_context(self, view: Gtk.Widget) -> None:
        self.popover = Gtk.PopoverMenu(menu_model=self.context_menu_model(), has_arrow=False)
        self.popover.set_parent(view)
        view.connect("unrealize", lambda *_: self.popover.get_parent() is not None and self.popover.unparent())
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_context_press, view)
        view.add_controller(click)

    def _on_context_press(self, _g, _n, x, y, view) -> None:
        self._select_at(view, x, y)
        self.update_action_states()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def _select_at(self, view, x, y) -> None:
        """Right-clicking an unselected item selects it (as a list view does)."""
        picked = view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not view:
            row = getattr(picked, "_history_row", None)
            if row is not None:
                for i in range(self.model.get_n_items()):
                    if self.model.get_item(i) is row:
                        if not self.selection.is_selected(i):
                            self.selection.select_item(i, True)
                        return
            picked = picked.get_parent()

    def _listen(self) -> None:
        def on_item(_item):
            gtkutil.idle(self._live_refresh)
        self._listener = on_item
        self.app.history.listeners.append(on_item)
        self.connect("unrealize", lambda *_: self._unlisten())

    def _unlisten(self) -> None:
        try:
            self.app.history.listeners.remove(self._listener)
        except ValueError:
            pass

    def _live_refresh(self) -> None:
        if self.get_root() is not None:
            self.refresh()


def show_image_preview(app, path: str, parent=None) -> Adw.Window:
    from ...core import imaging
    img = imaging.load(path)
    win = Adw.Window(application=app, title=Path(path).name, default_width=min(img.width + 40, 1200),
                     default_height=min(img.height + 80, 900), transient_for=parent)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    pic = gtkutil.picture(img)
    tv.set_content(pic)
    win.set_content(tv)
    win.present()
    return win


def _date_entry(placeholder: str) -> tuple[Gtk.Box, Gtk.Entry]:
    """An ISO date entry with a calendar popover."""
    box = Gtk.Box(spacing=0)
    box.add_css_class("linked")
    entry = Gtk.Entry(placeholder_text="YYYY-MM-DD", width_chars=11, tooltip_text=placeholder)
    cal = Gtk.Calendar()
    pop = Gtk.Popover(child=cal)
    btn = Gtk.MenuButton(icon_name="x-office-calendar-symbolic", popover=pop, tooltip_text=f"Pick {placeholder}")

    def picked(c):
        d = c.get_date()
        entry.set_text(f"{d.get_year():04d}-{d.get_month():02d}-{d.get_day_of_month():02d}")
        pop.popdown()
    cal.connect("day-selected", picked)
    box.append(entry)
    box.append(btn)
    return box, entry


def parse_date(text: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


# ---- History ----------------------------------------------------------------------------------------

_history_windows: dict[int, HistoryWindow] = {}
_image_windows: dict[int, ImageHistoryWindow] = {}


def open_history(app) -> HistoryWindow:
    win = _history_windows.get(id(app))
    if win is None:
        win = HistoryWindow(app)
        _history_windows[id(app)] = win
        win.connect("unrealize", lambda w: _history_windows.get(id(app)) is w and _history_windows.pop(id(app), None))
    win.present()
    return win


def open_image_history(app) -> ImageHistoryWindow:
    win = _image_windows.get(id(app))
    if win is None:
        win = ImageHistoryWindow(app)
        _image_windows[id(app)] = win
        win.connect("unrealize", lambda w: _image_windows.get(id(app)) is w and _image_windows.pop(id(app), None))
    win.present()
    return win


class HistoryWindow(HistoryActionsMixin, Adw.Window):
    TITLE = "ShareL - History"

    def __init__(self, app) -> None:
        super().__init__(application=app, title=self.TITLE, default_width=1100, default_height=700)
        self.app = app
        self.settings = app.store.app.history_settings
        self._busy = False
        self.all_items: list[HistoryItem] = []
        self.filtered: list[HistoryItem] = []

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.search = Gtk.SearchEntry(placeholder_text="Search history", width_chars=30,
                                      tooltip_text="Search (file name, URL, host, path, window title)")
        self.search.connect("changed", lambda *_: self.refresh())
        header.set_title_widget(self.search)
        self.advanced_toggle = Gtk.ToggleButton(icon_name="edit-find-replace-symbolic",
                                                tooltip_text="Advanced search")
        header.pack_start(self.advanced_toggle)
        stats = Gtk.Button(icon_name="view-list-bullet-symbolic", tooltip_text="Show stats")
        stats.connect("clicked", lambda *_: self.show_stats())
        header.pack_end(stats)
        header.pack_end(self._settings_button())
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_start(refresh)
        tv.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.revealer = Gtk.Revealer(child=self._advanced_panel())
        self.advanced_toggle.bind_property("active", self.revealer, "reveal-child",
                                           GObject.BindingFlags.SYNC_CREATE)
        box.append(self.revealer)

        self.model = Gio.ListStore.new(HistoryRow)
        self.selection = Gtk.MultiSelection.new(self.model)
        self.selection.connect("selection-changed", lambda *_: self.update_details())
        cv = Gtk.ColumnView(model=self.selection, show_row_separators=True, hexpand=True, vexpand=True)
        for title, fn, expand in (("Date", lambda i: i.when.strftime("%Y-%m-%d %H:%M:%S")
                                   if i.when != _dt.datetime.min else i.date_time, False),
                                  ("File name", lambda i: i.file_name, True), ("Type", lambda i: i.type, False),
                                  ("Host", lambda i: i.host, False), ("URL", lambda i: i.result_url, True)):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", lambda _f, li: li.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)))

            def bind(_f, li, fn=fn):
                lbl = li.get_child()
                lbl.set_label(fn(li.get_item().item) or "")
                lbl._history_row = li.get_item()
            f.connect("bind", bind)
            cv.append_column(Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=True))
        cv.connect("activate", lambda _cv, pos: self.try_open(self.model.get_item(pos).item))
        self.column_view = cv
        self._install_actions(self)
        self._attach_context(cv)

        paned = Gtk.Paned(start_child=Gtk.ScrolledWindow(child=cv), end_child=self._details_pane(),
                          resize_end_child=False, shrink_end_child=False, vexpand=True)
        paned.set_position(760)
        box.append(paned)
        self.status = Gtk.Label(xalign=0, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.status.add_css_class("dim-label")
        box.append(self.status)
        tv.set_content(box)
        self.set_content(tv)

        if self.settings.remember_search_text and self.settings.search_text:
            self.search.set_text(self.settings.search_text)
        self.connect("close-request", self._on_close)
        self._listen()
        self.refresh()

    # ---- panels ----

    def _advanced_panel(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin_start=8, margin_end=8, margin_top=6,
                        margin_bottom=6)
        self.filename_filter = Gtk.Entry(placeholder_text="File name contains", hexpand=True,
                                         tooltip_text="File name filter")
        self.url_filter = Gtk.Entry(placeholder_text="URL contains", hexpand=True, tooltip_text="URL filter")
        for e in (self.filename_filter, self.url_filter):
            e.connect("changed", lambda *_: self.refresh())
        grid.attach(Gtk.Label(label="File name:", xalign=1), 0, 0, 1, 1)
        grid.attach(self.filename_filter, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="URL:", xalign=1), 2, 0, 1, 1)
        grid.attach(self.url_filter, 3, 0, 1, 1)

        self.date_check = Gtk.CheckButton(label="Date", tooltip_text="Filter by date")
        from_box, self.date_from = _date_entry("from date")
        to_box, self.date_to = _date_entry("to date")
        today = _dt.date.today().isoformat()
        self.date_from.set_text(today)
        self.date_to.set_text(today)
        for w in (self.date_check,):
            w.connect("toggled", lambda *_: self.refresh())
        for e in (self.date_from, self.date_to):
            e.connect("changed", lambda *_: self.refresh())
        dbox = Gtk.Box(spacing=6)
        dbox.append(Gtk.Label(label="From"))
        dbox.append(from_box)
        dbox.append(Gtk.Label(label="To"))
        dbox.append(to_box)
        grid.attach(self.date_check, 0, 1, 1, 1)
        grid.attach(dbox, 1, 1, 1, 1)

        self.type_check = Gtk.CheckButton(label="Type", tooltip_text="Filter by type")
        self.type_model = Gtk.StringList.new([])
        self.type_dd = Gtk.DropDown(model=self.type_model, tooltip_text="Type")
        self.host_check = Gtk.CheckButton(label="Host", tooltip_text="Filter by host")
        self.host_model = Gtk.StringList.new([])
        self.host_dd = Gtk.DropDown(model=self.host_model, tooltip_text="Host", enable_search=True)
        for c in (self.type_check, self.host_check):
            c.connect("toggled", lambda *_: self.refresh())
        for d in (self.type_dd, self.host_dd):
            d.connect("notify::selected", lambda *_: self._busy or self.refresh())
        tb = Gtk.Box(spacing=6)
        tb.append(self.type_check)
        tb.append(self.type_dd)
        tb.append(self.host_check)
        tb.append(self.host_dd)
        grid.attach(tb, 2, 1, 2, 1)
        reset = Gtk.Button(label="Reset", tooltip_text="Reset filters")
        reset.connect("clicked", lambda *_: self.reset_filters())
        grid.attach(reset, 4, 0, 1, 2)
        return grid

    def _settings_button(self) -> Gtk.MenuButton:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=8)
        self.remember_switch = Gtk.CheckButton(label="Remember search text",
                                               active=self.settings.remember_search_text)
        self.remember_switch.connect("toggled", lambda c: self._set_setting("remember_search_text", c.get_active()))
        box.append(self.remember_switch)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Max item count (-1 = all)"))
        self.max_spin = Gtk.SpinButton.new_with_range(-1, 1_000_000, 1)
        self.max_spin.set_value(self.settings.max_item_count)
        self.max_spin.connect("value-changed", lambda s: (self._set_setting("max_item_count", int(s.get_value())),
                                                          self.refresh()))
        row.append(self.max_spin)
        box.append(row)
        return Gtk.MenuButton(icon_name="emblem-system-symbolic", tooltip_text="History settings",
                              popover=Gtk.Popover(child=box))

    def _set_setting(self, name, value) -> None:
        setattr(self.settings, name, value)
        self.app.store.save_app()

    def _details_pane(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=8, width_request=300)
        self.preview = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=200)
        box.append(self.preview)
        self.details_grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        self.detail_labels: dict[str, Gtk.Label] = {}
        for i, (key, title) in enumerate((("file_name", "File name"), ("file_path", "File path"),
                                          ("date_time", "Date"), ("type", "Type"), ("host", "Host"),
                                          ("url", "URL"), ("shortened_url", "Shortened URL"),
                                          ("thumbnail_url", "Thumbnail URL"), ("deletion_url", "Deletion URL"),
                                          ("tags", "Window / process"))):
            t = Gtk.Label(label=title, xalign=1, yalign=0)
            t.add_css_class("dim-label")
            v = gtkutil.no_hyphens(Gtk.Label(xalign=0, selectable=True, hexpand=True, max_width_chars=40))
            self.details_grid.attach(t, 0, i, 1, 1)
            self.details_grid.attach(v, 1, i, 1, 1)
            self.detail_labels[key] = v
        box.append(self.details_grid)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    # ---- filtering ----

    def current_filter(self) -> HistoryFilter:
        flt = HistoryFilter(text=self.search.get_text().strip())
        if self.date_check.get_active():
            flt.date_from = parse_date(self.date_from.get_text())
            flt.date_to = parse_date(self.date_to.get_text())
        if self.type_check.get_active():
            flt.type = self._dd_value(self.type_dd, self.type_model)
        if self.host_check.get_active():
            flt.host = self._dd_value(self.host_dd, self.host_model)
        return flt

    @staticmethod
    def _dd_value(dd: Gtk.DropDown, model: Gtk.StringList) -> str:
        i = dd.get_selected()
        return model.get_string(i) if i != Gtk.INVALID_LIST_POSITION and i < model.get_n_items() else ""

    def matches(self, flt: HistoryFilter, it: HistoryItem) -> bool:
        if not flt.matches(it):
            return False
        fn = self.filename_filter.get_text().strip().lower()
        if fn and fn not in (it.file_name or "").lower():
            return False
        uf = self.url_filter.get_text().strip().lower()
        return not (uf and uf not in f"{it.url} {it.shortened_url}".lower())

    def refresh(self) -> None:
        if self._busy:
            return
        self.all_items = self.app.history.items()
        self._sync_choices(self.type_dd, self.type_model, sorted({i.type for i in self.all_items if i.type},
                                                                 key=lambda t: (TYPES.index(t) if t in TYPES
                                                                                else 99, t)))
        self._sync_choices(self.host_dd, self.host_model, self.app.history.hosts())
        flt = self.current_filter()
        res = [i for i in self.all_items if self.matches(flt, i)]
        res.sort(key=lambda i: i.date_time, reverse=True)
        if self.settings.max_item_count > 0:
            res = res[: self.settings.max_item_count]
        self.filtered = res
        self.model.splice(0, self.model.get_n_items(), [HistoryRow(i) for i in res])
        self._update_title()
        self.update_details()

    def _sync_choices(self, dd: Gtk.DropDown, model: Gtk.StringList, values: list[str]) -> None:
        cur = [model.get_string(i) for i in range(model.get_n_items())]
        if cur == values:
            return
        keep = self._dd_value(dd, model)
        self._busy = True
        try:
            model.splice(0, model.get_n_items(), values)
            dd.set_selected(values.index(keep) if keep in values else 0)
        finally:
            self._busy = False

    def set_type_filter(self, value: str) -> None:
        values = [self.type_model.get_string(i) for i in range(self.type_model.get_n_items())]
        self.type_dd.set_selected(values.index(value))
        self.type_check.set_active(True)

    def set_host_filter(self, value: str) -> None:
        values = [self.host_model.get_string(i) for i in range(self.host_model.get_n_items())]
        self.host_dd.set_selected(values.index(value))
        self.host_check.set_active(True)

    def reset_filters(self) -> None:
        self._busy = True
        try:
            self.search.set_text("")
            self.filename_filter.set_text("")
            self.url_filter.set_text("")
            self.date_check.set_active(False)
            today = _dt.date.today().isoformat()
            self.date_from.set_text(today)
            self.date_to.set_text(today)
            self.type_check.set_active(False)
            self.host_check.set_active(False)
            if self.type_model.get_n_items():
                self.type_dd.set_selected(0)
        finally:
            self._busy = False
        self.refresh()

    def _update_title(self) -> None:
        total, shown = len(self.all_items), len(self.filtered)
        parts = [f"Total: {total:,}"]
        if total > shown:
            parts.append(f"Filtered: {shown:,}")
        for t, n in Counter(i.type for i in self.filtered).most_common():
            parts.append(f"{t}: {n:,}")
        text = " - ".join(parts)
        self.status.set_label(text)
        self.set_title(f"{self.TITLE} ({text})")

    def item_count_text(self) -> str:
        return self.status.get_label()

    # ---- selection / details ----

    def selected_items(self) -> list[HistoryItem]:
        bits = self.selection.get_selection()
        out = []
        for k in range(bits.get_size()):
            row = self.model.get_item(bits.get_nth(k))
            if row is not None:
                out.append(row.item)
        return out

    def select_index(self, i: int) -> None:
        self.selection.select_item(i, True)

    def update_details(self) -> None:
        self.update_action_states()
        items = self.selected_items()
        it = items[0] if items else None
        for key, lbl in self.detail_labels.items():
            if it is None:
                lbl.set_label("")
            elif key == "tags":
                lbl.set_label(" / ".join(v for v in it.tags.values() if v))
            else:
                lbl.set_label(str(getattr(it, key) or ""))
        self.preview.set_paintable(None)
        if it is not None and is_image_file(it):
            try:
                from ...core import imaging
                img = imaging.load(it.file_path)
                img.thumbnail((400, 400))
                self.preview.set_paintable(gtkutil.texture_from_pil(img))
            except Exception:
                self.preview.set_paintable(None)

    def try_open(self, it: HistoryItem) -> None:
        """Double-click (HistoryItemManager.TryOpen): shortened URL, URL, else the file."""
        if it.shortened_url:
            desktop.open_url(it.shortened_url)
        elif it.url:
            desktop.open_url(it.url)
        elif has_file(it):
            desktop.open_file(it.file_path)

    def show_stats(self) -> None:
        gtkutil.alert(self, "History stats", stats_text(self.all_items))

    def _on_close(self, *_a) -> bool:
        if self.settings.remember_search_text:
            self.settings.search_text = self.search.get_text()
        else:
            self.settings.search_text = ""
        self.app.store.save_app()
        return False


# ---- Image history -----------------------------------------------------------------------------------

class ThumbnailLoader:
    """Loads thumbnails on one worker thread; ``callback(path, texture_or_None)`` runs on the main loop."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.cache: dict[str, Gdk.Texture | None] = {}
        self.pending: set[str] = set()
        self.q: queue.Queue = queue.Queue()
        self.listeners: list = []
        self.thread = threading.Thread(target=self._run, daemon=True, name="image-history-thumbs")
        self.thread.start()

    def request(self, path: str) -> Gdk.Texture | None:
        if path in self.cache:
            return self.cache[path]
        if path not in self.pending:
            self.pending.add(path)
            self.q.put((path, self.size))
        return None

    def reset(self, size: int) -> None:
        self.size = size
        self.cache.clear()
        self.pending.clear()

    def stop(self) -> None:
        self.q.put(None)

    def _run(self) -> None:
        from ...core import imaging
        while True:
            job = self.q.get()
            if job is None:
                return
            path, size = job
            try:
                img = imaging.load(path)
                img.thumbnail((size, size))
                rgba = img.convert("RGBA")
                data, w, h = rgba.tobytes(), rgba.width, rgba.height
            except Exception:
                data, w, h = None, 0, 0
            gtkutil.idle(self._done, path, size, data, w, h)

    def _done(self, path, size, data, w, h) -> None:
        if size != self.size:
            return
        self.pending.discard(path)
        tex = None
        if data is not None:
            tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), w * 4)
        self.cache[path] = tex
        for cb in list(self.listeners):
            cb(path, tex)


class ImageHistoryWindow(HistoryActionsMixin, Adw.Window):
    TITLE = "ShareL - Image history"

    def __init__(self, app) -> None:
        super().__init__(application=app, title=self.TITLE, default_width=1000, default_height=720)
        self.app = app
        self.settings = app.store.app.image_history_settings
        self.items: list[HistoryItem] = []
        self.thumbs = ThumbnailLoader(self.settings.thumbnail_size)
        self.thumbs.listeners.append(self._thumb_ready)
        self._bound: dict[str, list[Gtk.Picture]] = {}

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.search = Gtk.SearchEntry(placeholder_text="Search image history", width_chars=30,
                                      tooltip_text="Search")
        self.search.connect("changed", lambda *_: self.refresh())
        header.set_title_widget(self.search)
        header.pack_end(self._settings_button())
        stats = Gtk.Button(icon_name="view-list-bullet-symbolic", tooltip_text="Show stats")
        stats.connect("clicked", lambda *_: gtkutil.alert(self, "Image history stats",
                                                          stats_text(self.items)))
        header.pack_end(stats)
        tv.add_top_bar(header)

        self.model = Gio.ListStore.new(HistoryRow)
        self.selection = Gtk.MultiSelection.new(self.model)
        self.selection.connect("selection-changed", lambda *_: self.update_action_states())
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        f.connect("unbind", self._unbind)
        self.grid = Gtk.GridView(model=self.selection, factory=f, max_columns=30, min_columns=1)
        self.grid.connect("activate", lambda _g, pos: self.open_item(self.model.get_item(pos).item))
        self._install_actions(self)
        self._attach_context(self.grid)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.ScrolledWindow(child=self.grid, vexpand=True))
        self.status = Gtk.Label(xalign=0, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.status.add_css_class("dim-label")
        box.append(self.status)
        tv.set_content(box)
        self.set_content(tv)
        if self.settings.remember_search_text and self.settings.search_text:
            self.search.set_text(self.settings.search_text)
        self.connect("close-request", self._on_close)
        self.connect("unrealize", lambda *_: self.thumbs.stop())
        self._listen()
        self.refresh()

    def _settings_button(self) -> Gtk.MenuButton:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=8)
        self.missing_check = Gtk.CheckButton(label="Filter missing files", active=self.settings.filter_missing_files)
        self.missing_check.connect("toggled", lambda c: self._set_setting("filter_missing_files", c.get_active()))
        box.append(self.missing_check)
        self.remember_check = Gtk.CheckButton(label="Remember search text",
                                              active=self.settings.remember_search_text)
        self.remember_check.connect("toggled", lambda c: self._set_setting("remember_search_text", c.get_active(),
                                                                           refresh=False))
        box.append(self.remember_check)
        for label, attr, lo, hi in (("Max item count (0 = all)", "max_item_count", 0, 100_000),
                                    ("Thumbnail size", "thumbnail_size", 50, 500)):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=label, hexpand=True, xalign=0))
            spin = Gtk.SpinButton.new_with_range(lo, hi, 1 if attr == "max_item_count" else 10)
            spin.set_value(getattr(self.settings, attr))
            spin.set_tooltip_text(label)
            spin.connect("value-changed", lambda s, a=attr: self._set_setting(a, int(s.get_value())))
            row.append(spin)
            box.append(row)
            setattr(self, f"{attr}_spin", spin)
        return Gtk.MenuButton(icon_name="emblem-system-symbolic", tooltip_text="Image history settings",
                              popover=Gtk.Popover(child=box))

    def _set_setting(self, name, value, refresh: bool = True) -> None:
        setattr(self.settings, name, value)
        self.app.store.save_app()
        if name == "thumbnail_size":
            self.thumbs.reset(value)
        if refresh:
            self.refresh()

    # ---- grid ----

    def _setup(self, _f, li) -> None:
        size = self.settings.thumbnail_size
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=4, margin_end=4,
                      margin_top=4, margin_bottom=4)
        pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, width_request=size,
                          height_request=size)
        lbl = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=max(8, size // 9))
        lbl.add_css_class("caption")
        box.append(pic)
        box.append(lbl)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        row = li.get_item()
        it = row.item
        box._history_row = row
        pic = box.get_first_child()
        lbl = pic.get_next_sibling()
        size = self.settings.thumbnail_size
        pic.set_size_request(size, size)
        lbl.set_label(it.file_name or Path(it.file_path).name)
        box.set_tooltip_text(f"{it.file_name}\n{it.date_time}\n{it.result_url or it.file_path}")
        pic._path = it.file_path
        tex = self.thumbs.request(it.file_path) if it.file_path else None
        pic.set_paintable(tex)
        self._bound.setdefault(it.file_path, []).append(pic)

    def _unbind(self, _f, li) -> None:
        pic = li.get_child().get_first_child()
        lst = self._bound.get(getattr(pic, "_path", ""), [])
        if pic in lst:
            lst.remove(pic)

    def _thumb_ready(self, path: str, tex) -> None:
        for pic in self._bound.get(path, []):
            if getattr(pic, "_path", "") == path:
                pic.set_paintable(tex)

    # ---- data ----

    def refresh(self) -> None:
        s = self.settings
        flt = HistoryFilter(text=self.search.get_text().strip())
        res = []
        for it in self.app.history.search(flt, newest_first=True):
            # ImageHistoryForm: items with an image file path (and, optionally, the file still there)
            if not it.file_path or _ext(it.file_path) not in IMAGE_EXTS:
                continue
            if s.filter_missing_files and not Path(it.file_path).is_file():
                continue
            res.append(it)
            if s.max_item_count > 0 and len(res) >= s.max_item_count:
                break
        self.items = res
        self._bound.clear()
        self.model.splice(0, self.model.get_n_items(), [HistoryRow(i) for i in res])
        self.status.set_label(f"Images: {len(res):,}")
        self.set_title(f"{self.TITLE} ({len(res):,})")

    def selected_items(self) -> list[HistoryItem]:
        bits = self.selection.get_selection()
        return [self.model.get_item(bits.get_nth(k)).item for k in range(bits.get_size())]

    def select_index(self, i: int) -> None:
        self.selection.select_item(i, True)

    def open_item(self, it: HistoryItem) -> None:
        """Double-click: the image file, or its URL when the file is gone."""
        if has_file(it):
            desktop.open_file(it.file_path)
        elif it.result_url:
            desktop.open_url(it.result_url)

    def _on_close(self, *_a) -> bool:
        self.settings.search_text = self.search.get_text() if self.settings.remember_search_text else ""
        self.app.store.save_app()
        return False
