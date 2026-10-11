"""Destination settings (ShareX's UploadersConfigForm).

A searchable sidebar lists every registered destination, grouped by category (Image / Text / File
uploaders, URL shorteners, URL sharing services). Each page is generated from the service's settings
dataclass with :class:`sharel.ui.forms.Form`, plus the actions the service module offers:

* ``authorize(settings, ctx)`` → "Authorize…" (OAuth 2 loopback, OAuth 1, or a login that stores a token),
* ``test_connection(settings, ctx)`` → "Test connection",
* ``login`` / ``fetch_api_key`` → "Log in" / "Fetch API key",
* ``list_folders`` / ``list_drives`` / ``list_albums`` / ``get_devices`` / ``refresh_devices`` → pickers
  that fill the matching settings field,

and, on every page, "Set as current destination" (the default TaskSettings destination field) and
"Test upload" (a generated sample through ``app.tasks`` with that destination selected).

Network actions run on a worker thread; results come back on the main loop. Edits are saved
(debounced) with ``app.store.save_uploaders()`` followed by ``app.settings_changed()``.
"""
from __future__ import annotations

import copy
import dataclasses
import importlib
import threading
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ...core import desktop
from ...uploaders import registry
from ...uploaders.base import UploadContext
from .. import gtkutil
from ..forms import Form

SAVE_DELAY_MS = 300

# category -> (TaskSettings attribute, title of the "current" destination)
DEST_ATTR = {"image": "image_destination", "text": "text_destination", "file": "file_destination",
             "shortener": "url_shortener_destination", "sharing": "url_sharing_service_destination"}

TEST_URL = "https://github.com/ShareX/ShareX"
TEST_TEXT = "ShareL text upload test"


@dataclass(frozen=True)
class Helper:
    """A settings-page helper a service module may define (looked up by function name)."""
    func: str
    label: str
    # Field filled from the picked entry; "" = no picker (the function stores what it fetched itself)
    target: str = ""
    # "id" stores the entry's id/key, "index" stores its position in the returned list
    store: str = "id"
    # Entry offered first, as (label, value): e.g. "All devices"
    first: tuple[str, Any] | None = None


HELPERS = (
    Helper("login", "Log in"),
    Helper("fetch_api_key", "Fetch API key"),
    Helper("list_drives", "Select shared drive…", target="drive_id"),
    Helper("list_folders", "Select folder…", target="folder_id"),
    Helper("list_albums", "Select album…", target="selected_album_id"),
    Helper("get_devices", "Get device list…", target="device_iden", first=("All devices", "")),
    Helper("refresh_devices", "Get device list…", target="selected_device", store="index",
           first=("All devices", -1)),
)

_windows: dict[int, DestinationSettingsWindow] = {}


def open_destination_settings(app, select: str = "") -> DestinationSettingsWindow:
    """Open (or re-present) the Destination settings window; ``select`` = a service key to show."""
    win = _windows.get(id(app))
    if win is None:
        win = DestinationSettingsWindow(app)
        _windows[id(app)] = win
        win.connect("destroy", lambda *_: _windows.pop(id(app), None))
    win.present()
    if select:
        win.select(select)
    return win


def service_module(info: registry.ServiceInfo):
    """The module that registered ``info`` (where its authorize/test_connection/helpers live)."""
    if info.cls is None:
        return None
    return importlib.import_module(info.cls.__module__)


def make_context(app) -> UploadContext:
    """An UploadContext for settings-page actions: system browser for OAuth, prompts through the GTK host."""
    cfg = app.store.app
    host = app.tasks.host if getattr(app, "tasks", None) is not None else None
    kw: dict[str, Any] = {}
    if host is not None:
        kw = {"prompt_input": host.prompt_input, "prompt_select": host.prompt_select,
              "show_output": host.show_output}
    return UploadContext(proxies=cfg.proxy_settings.requests_proxies(), timeout=cfg.request_timeout_seconds,
                         open_url=lambda url: desktop.open_url(url), save_settings=app.store.save_uploaders, **kw)


def destination_enum(category: str):
    from ...core.config import TaskSettings
    return typing.get_type_hints(TaskSettings)[DEST_ATTR[category]]


def can_be_current(info: registry.ServiceInfo) -> bool:
    return info.available and info.name in destination_enum(info.category).__members__


def entry_label_value(entry: Any) -> tuple[str, str]:
    """(label, id) of a picker entry: dicts with id/key + name/title/nickname, or objects with key/name."""
    get = entry.get if isinstance(entry, dict) else (lambda k, d=None: getattr(entry, k, d))
    ident = get("id") if get("id") is not None else get("key", "")
    name = get("name") or get("title") or get("nickname") or ""
    return str(name or ident or "(unnamed)"), str(ident or "")


def _sample_png() -> bytes:
    from PIL import Image, ImageDraw

    from ...core import imaging
    img = Image.new("RGBA", (320, 180), (53, 132, 228, 255))
    ImageDraw.Draw(img).text((20, 80), "ShareL test image", fill="white")
    return imaging.png_bytes(img)


class DestinationSettingsWindow(Adw.Window):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="Destination settings", default_width=1000, default_height=700)
        self.app = app
        self.cfg = app.store.uploaders
        self.pages: dict[str, Gtk.Widget] = {}
        self.busy = 0  # running worker actions (tests wait for 0)
        self._save_source = 0
        self.services = self._services()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        custom = Gtk.Button(label="Custom uploader settings…", tooltip_text="Open the custom uploader editor")
        custom.connect("clicked", lambda *_: self._open_custom())
        header.pack_end(custom)
        toolbar.add_top_bar(header)

        # sidebar
        self.search = Gtk.SearchEntry(placeholder_text="Search destinations", margin_start=6, margin_end=6,
                                      margin_top=6, margin_bottom=6)
        self.search.set_tooltip_text("Search destinations")
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.rows: dict[str, Gtk.ListBoxRow] = {}
        for info in self.services:
            row = Gtk.ListBoxRow()
            row.info = info  # type: ignore[attr-defined]
            lbl = Gtk.Label(label=info.title + (" (unavailable)" if info.defunct else ""), xalign=0,
                            margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            if info.defunct:
                lbl.add_css_class("dim-label")
            row.set_child(lbl)
            self.listbox.append(row)
            self.rows[info.key] = row
        self.listbox.set_header_func(self._header)
        self.listbox.set_filter_func(self._filter)
        self.listbox.connect("row-selected", self._on_row)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=250)
        side.append(self.search)
        side.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.stack.add_named(Adw.StatusPage(title="Destination settings", icon_name="folder-remote-symbolic",
                                            description="Pick a destination on the left to configure it."), "empty")
        paned = Gtk.Paned(start_child=side, end_child=self.stack, position=260, shrink_start_child=False)
        toolbar.set_content(paned)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close)
        if self.services:
            self.select(self._initial_key())

    # ---- sidebar ----

    def _services(self) -> list[registry.ServiceInfo]:
        registry.load_all()
        order = {c: i for i, c in enumerate(registry.CATEGORIES)}
        return sorted(registry.SERVICES.values(),
                      key=lambda s: (order[s.category], bool(s.defunct), s.title.lower()))

    def _initial_key(self) -> str:
        ts = self.app.store.app.default_task_settings
        key = f"image/{ts.image_destination.value}"
        return key if key in self.rows else self.services[0].key

    def _header(self, row, before) -> None:
        if before is None or before.info.category != row.info.category:
            lbl = Gtk.Label(label=registry.CATEGORY_TITLES[row.info.category], xalign=0, margin_start=6,
                            margin_top=10, margin_bottom=2)
            lbl.add_css_class("heading")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def _filter(self, row) -> bool:
        needle = self.search.get_text().strip().lower()
        if not needle:
            return True
        info = row.info
        hay = f"{info.title} {info.name} {registry.CATEGORY_TITLES[info.category]}".lower()
        return needle in hay

    def visible_keys(self) -> list[str]:
        return [k for k, r in self.rows.items() if self._filter(r)]

    def select(self, key: str) -> None:
        row = self.rows.get(key)
        if row is not None:
            self.listbox.select_row(row)
            self.show_page(key)

    def _on_row(self, _lb, row) -> None:
        if row is not None:
            self.show_page(row.info.key)

    def show_page(self, key: str) -> Gtk.Widget:
        page = self.pages.get(key)
        if page is None:
            page = self._build_page(registry.SERVICES[key])
            self.pages[key] = page
            self.stack.add_named(page, key)
        self.stack.set_visible_child(page)
        return page

    def rebuild_page(self, key: str) -> Gtk.Widget:
        """Rebuild a page so its rows show values a worker action changed (tokens, fetched keys)."""
        old = self.pages.pop(key, None)
        shown = old is not None and self.stack.get_visible_child() is old
        if old is not None:
            self.stack.remove(old)
        page = self._build_page(registry.SERVICES[key])
        self.pages[key] = page
        self.stack.add_named(page, key)
        if shown or old is None:
            self.stack.set_visible_child(page)
        return page

    # ---- pages ----

    def settings_of(self, info: registry.ServiceInfo):
        if info.settings_cls is None:
            return None
        return self.cfg.settings_for(info.key, info.settings_cls)

    def _build_page(self, info: registry.ServiceInfo) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=info.title)
        page.info = info  # type: ignore[attr-defined]
        page.buttons = {}  # type: ignore[attr-defined]  label -> Gtk.Button (tests drive these)
        head = Adw.PreferencesGroup(title=GLib.markup_escape_text(info.title),
                                    description=GLib.markup_escape_text(info.description or
                                                                        registry.CATEGORY_TITLES[info.category]))
        if info.homepage:
            row = Adw.ActionRow(title="Website", subtitle=info.homepage, use_markup=False)
            link = Gtk.LinkButton(uri=info.homepage, label="Open", valign=Gtk.Align.CENTER)
            row.add_suffix(link)
            head.add(row)
        page.add(head)

        if info.defunct:
            page.defunct = True  # type: ignore[attr-defined]
            g = Adw.PreferencesGroup(title="No longer available")
            row = Adw.ActionRow(title=f"{info.title} has shut down or removed its API",
                                subtitle=info.defunct_reason or "This service is no longer available.",
                                use_markup=False)
            row.set_subtitle_lines(0)
            ev = Gtk.LinkButton(uri=info.defunct, label="Evidence", valign=Gtk.Align.CENTER,
                                tooltip_text=info.defunct)
            row.add_suffix(ev)
            g.add(row)
            page.add(g)
        else:
            page.defunct = False  # type: ignore[attr-defined]

        settings = self.settings_of(info)
        if settings is not None:
            form = Form(settings, self.schedule_save, parent=self)
            rows = form.rows()
            g = Adw.PreferencesGroup(title="Settings")
            for r in rows:
                g.add(r)
            if not rows:
                g.add(Adw.ActionRow(title="This destination has no settings"))
            page.add(g)
        elif not info.defunct:
            g = Adw.PreferencesGroup(title="Settings")
            g.add(Adw.ActionRow(title="This destination has no settings"))
            page.add(g)

        actions = Adw.PreferencesGroup(title="Actions")
        mod = service_module(info)
        if mod is not None and settings is not None:
            if hasattr(mod, "authorize"):
                self._action_row(page, actions, "Authorize…", "Sign in in the browser (or log in) and store "
                                 "the token", lambda: self._authorize(info, mod))
            for h in HELPERS:
                if hasattr(mod, h.func) and (not h.target or self._has_field(settings, h.target)):
                    self._action_row(page, actions, h.label, "", lambda h=h: self._helper(info, mod, h))
            if hasattr(mod, "test_connection"):
                self._action_row(page, actions, "Test connection", "Connect with these settings",
                                 lambda: self._test_connection(info, mod))
        cur = self._action_row(page, actions, "Set as current destination", self._current_text(info),
                               lambda: self.set_current(info))
        page.current_row = cur  # type: ignore[attr-defined]
        if info.category == "file":
            for label, attr in (("Use for images", "image"), ("Use for text", "text")):
                b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda *_a, a=attr: self.set_current(info, via_file=a))
                b.set_sensitive(can_be_current(info))
                cur.add_suffix(b)
                page.buttons[label] = b
        page.buttons["Set as current destination"].set_sensitive(can_be_current(info))
        self._action_row(page, actions, "Test upload", "Upload a generated sample to this destination",
                         lambda: self.test_upload(info))
        page.buttons["Test upload"].set_sensitive(info.available)
        actions.set_sensitive(info.available)
        page.actions_group = actions  # type: ignore[attr-defined]
        page.add(actions)
        return page

    @staticmethod
    def _has_field(obj, name: str) -> bool:
        return any(f.name == name for f in dataclasses.fields(obj))

    def _action_row(self, page, group, label: str, subtitle: str, cb: Callable[[], None]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=label.rstrip("…"), subtitle=subtitle, use_markup=False)
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: cb())
        row.add_suffix(b)
        group.add(row)
        page.buttons[label] = b
        return row

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
        self.app.store.save_uploaders()
        self.app.settings_changed()

    def _on_close(self, *_a) -> bool:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
            self.save_now()
        return False

    # ---- worker actions ----

    def run_worker(self, button: Gtk.Button | None, fn: Callable[[], Any],
                   on_done: Callable[[Any], None], what: str) -> None:
        """Run ``fn`` on a thread; ``on_done(result)`` on the main loop, errors in an alert."""
        label = button.get_label() if button else ""
        if button is not None:
            button.set_sensitive(False)
            button.set_label("Working…")
        self.busy += 1

        def finish(ok: bool, value: Any) -> None:
            self.busy -= 1
            if button is not None:
                button.set_label(label)
                button.set_sensitive(True)
            if ok:
                on_done(value)
            else:
                gtkutil.alert(self, f"{what} failed", str(value) or value.__class__.__name__)

        def work() -> None:
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - every failure is shown to the user
                gtkutil.idle(finish, False, e)
                return
            gtkutil.idle(finish, True, res)
        threading.Thread(target=work, daemon=True, name=f"dest-{what}").start()

    def _button(self, info, label) -> Gtk.Button | None:
        page = self.pages.get(info.key)
        return page.buttons.get(label) if page is not None else None

    def _authorize(self, info, mod) -> None:
        settings = self.settings_of(info)
        ctx = make_context(self.app)

        def done(_res):
            self.save_now()
            self.rebuild_page(info.key)
            self.app.message(f"{info.title}: authorized", "The account is linked; uploads will use it.")
        self.run_worker(self._button(info, "Authorize…"), lambda: mod.authorize(settings, ctx), done,
                        f"{info.title} authorization")

    def _test_connection(self, info, mod) -> None:
        settings = self.settings_of(info)
        ctx = make_context(self.app)
        self.run_worker(self._button(info, "Test connection"), lambda: mod.test_connection(settings, ctx),
                        lambda res: gtkutil.alert(self, f"{info.title}: connection OK", str(res or "Connected.")),
                        f"{info.title} connection test")

    def _helper(self, info, mod, h: Helper) -> None:
        settings = self.settings_of(info)
        ctx = make_context(self.app)
        fn = getattr(mod, h.func)

        def call():
            if h.func == "list_folders" and self._has_field(settings, "drive_id"):
                return fn(settings, ctx, settings.drive_id)
            return fn(settings, ctx)

        def done(res):
            if not h.target:
                self.save_now()
                self.rebuild_page(info.key)
                gtkutil.alert(self, f"{info.title}: {h.label.rstrip('…')} done", "The settings were updated.")
                return
            self.pick(info, h, list(res or []))
        self.run_worker(self._button(info, h.label), call, done, f"{info.title} {h.label.rstrip('…').lower()}")

    def pick(self, info, h: Helper, entries: list) -> None:
        """Offer ``entries`` in a chooser and store the picked one in ``h.target``."""
        options: list[tuple[str, Any, str]] = []  # (label, value, name)
        if h.first is not None:
            options.append((h.first[0], h.first[1], ""))
        for i, e in enumerate(entries):
            name, ident = entry_label_value(e)
            value = i if h.store == "index" else ident
            options.append((name, value, name))
        if not options:
            gtkutil.alert(self, f"{info.title}: nothing found", "The service returned an empty list.")
            return
        labels: list[str] = []
        for name, _v, _n in options:
            lbl, n = name, 2
            while lbl in labels:
                lbl, n = f"{name} ({n})", n + 1
            labels.append(lbl)

        def chosen(label: str | None) -> None:
            if label is None:
                return
            _name, value, picked_name = options[labels.index(label)]
            self.apply_pick(info, h, value, picked_name)
        gtkutil.prompt_choice(self, h.label.rstrip("…"), labels, chosen)

    def apply_pick(self, info, h: Helper, value: Any, name: str = "") -> None:
        settings = self.settings_of(info)
        setattr(settings, h.target, value)
        if h.target == "folder_id" and self._has_field(settings, "folder_name"):
            settings.folder_name = name
        self.save_now()
        self.rebuild_page(info.key)

    # ---- current destination / test upload ----

    def _current_text(self, info) -> str:
        if not can_be_current(info):
            return "Not available as a destination"
        ts = self.app.store.app.default_task_settings
        cur = getattr(ts, DEST_ATTR[info.category]).value
        return "This is the current destination" if cur == info.name else \
            f"Current: {registry.get(info.category, cur).title if registry.get(info.category, cur) else cur}"

    def set_current(self, info, via_file: str = "") -> None:
        """Make ``info`` the default destination of its kind (``via_file``: image/text through a file host)."""
        if not can_be_current(info):
            return
        ts = self.app.store.app.default_task_settings
        enum = destination_enum(info.category)
        if via_file == "image":
            ts.image_destination = destination_enum("image")["FileUploader"]
            ts.image_file_destination = enum[info.name]
        elif via_file == "text":
            ts.text_destination = destination_enum("text")["FileUploader"]
            ts.text_file_destination = enum[info.name]
        else:
            setattr(ts, DEST_ATTR[info.category], enum[info.name])
        self.app.store.save_app()
        self.app.settings_changed()
        for page in self.pages.values():
            page.current_row.set_subtitle(self._current_text(page.info))

    def test_settings(self, info):
        ts = copy.deepcopy(self.app.store.task_settings_for(None))
        enum = destination_enum(info.category)
        if info.name in enum.__members__:
            setattr(ts, DEST_ATTR[info.category], enum[info.name])
        ts.use_default_destinations = False
        return ts

    def test_upload(self, info):
        """Upload a generated sample through the task pipeline with this destination selected."""
        if not info.available:
            return None
        self.save_now()
        ts = self.test_settings(info)
        tasks = self.app.tasks
        if info.category == "image":
            ts.advanced_settings.upload_images_to_image_host = True
            return tasks.upload_data(_sample_png(), "ShareL test image.png", ts)
        if info.category == "text":
            return tasks.upload_text(TEST_TEXT, ts)
        if info.category == "file":
            ts.advanced_settings.upload_images_to_image_host = False
            ts.advanced_settings.upload_text_files_to_text_host = False
            return tasks.upload_data(_sample_png(), "ShareL test file.png", ts)
        if info.category == "shortener":
            return tasks.shorten_url(TEST_URL, ts)
        return tasks.share_url(TEST_URL, ts)

    def _open_custom(self) -> None:
        from .customuploader import open_custom_uploaders
        open_custom_uploaders(self.app)
