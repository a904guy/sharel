"""The main window (ShareX's MainForm): the left menu and the task list.

Left menu: Capture, Upload, Workflows, Tools, After capture tasks (checkable), After upload tasks,
Destinations, Task settings, Application settings, Hotkey settings, Screenshots folder, History,
Image history, Debug, About. Right: the task list (list or thumbnail view) with progress, a context
menu and drag-and-drop upload.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ..core import clipboard, desktop, imaging
from ..core.enums import (
    AfterCaptureTasks,
    AfterUploadTasks,
    FileDestination,
    ImageDestination,
    TaskViewMode,
    TextDestination,
    UrlShortenerType,
    URLSharingServices,
)
from ..core.tasks import TaskInfo, TaskStatus
from . import menus
from .gtkutil import human_size, texture_from_pil


class TaskItem(GObject.Object):
    __gtype_name__ = "ShareLTaskItem"

    def __init__(self, info: TaskInfo) -> None:
        super().__init__()
        self.info = info
        self.thumb: Gdk.Texture | None = None

    @GObject.Property(type=str)
    def title(self) -> str:
        return self.info.file_name or self.info.url or self.info.title

    @GObject.Property(type=str)
    def status(self) -> str:
        i = self.info
        if i.status == TaskStatus.Working and i.bytes_total:
            return f"Uploading {i.progress * 100:.0f}%"
        return i.status.value

    @GObject.Property(type=str)
    def url(self) -> str:
        return self.info.url_result or self.info.file_path

    @GObject.Property(type=str)
    def speed(self) -> str:
        return f"{human_size(self.info.speed)}/s" if self.info.speed else ""

    @GObject.Property(type=str)
    def elapsed(self) -> str:
        i = self.info
        if not i.start_time:
            return ""
        import time
        end = i.end_time or time.time()
        return f"{end - i.start_time:.1f} s"


SIDEBAR = [
    ("Capture", "camera-photo-symbolic", "capture"), ("Upload", "send-to-symbolic", "upload"),
    ("Workflows", "view-list-symbolic", "workflows"), ("Tools", "applications-graphics-symbolic", "tools"),
    (None, None, None),
    ("After capture tasks", "object-select-symbolic", "aftercapture"),
    ("After upload tasks", "object-select-symbolic", "afterupload"),
    ("Destinations", "folder-remote-symbolic", "destinations"),
    ("Task settings…", "preferences-other-symbolic", "action:task-settings"),
    ("Application settings…", "preferences-system-symbolic", "action:app-settings"),
    ("Hotkey settings…", "preferences-desktop-keyboard-shortcuts-symbolic", "action:hotkey-settings"),
    (None, None, None),
    ("Screenshots folder…", "folder-pictures-symbolic", "action:screenshots-folder"),
    ("History…", "document-open-recent-symbolic", "action:history"),
    ("Image history…", "image-x-generic-symbolic", "action:image-history"),
    (None, None, None),
    ("Debug", "dialog-information-symbolic", "debug"),
    ("About…", "help-about-symbolic", "action:about"),
]

TASK_MENU = [
    ("Open", [("URL", "task-open-url"), ("Shortened URL", "task-open-short"), ("Thumbnail URL", "task-open-thumb"),
              ("Deletion URL", "task-open-del"), ("File", "task-open-file"), ("Folder", "task-open-folder")]),
    ("Copy", [("URL", "task-copy-url"), ("Shortened URL", "task-copy-short"), ("Thumbnail URL", "task-copy-thumb"),
              ("Deletion URL", "task-copy-del"), ("File", "task-copy-file"), ("Image", "task-copy-image"),
              ("Text", "task-copy-text"), ("File path", "task-copy-path"), ("File name", "task-copy-name"),
              ("Folder path", "task-copy-folder")]),
    (None, [("Upload", "task-upload"), ("Edit image…", "task-edit"), ("Pin to screen", "task-pin"),
            ("OCR…", "task-ocr"), ("Scan QR code", "task-qr"), ("Show QR code…", "task-show-qr")]),
    (None, [("Stop", "task-stop"), ("Remove from list", "task-remove"), ("Delete file locally…", "task-delete"),
            ("Show response…", "task-response"), ("Show errors…", "task-errors")]),
]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app) -> None:
        cfg = app.store.app
        super().__init__(application=app, title="ShareL", default_width=cfg.main_window_size.width,
                         default_height=cfg.main_window_size.height)
        self.app = app
        self.items: dict[str, TaskItem] = {}
        self.store_model = Gio.ListStore.new(TaskItem)
        self._install_actions()

        header = Adw.HeaderBar()
        self.view_toggle = Gtk.ToggleButton(icon_name="view-grid-symbolic", tooltip_text="Thumbnail view",
                                            active=cfg.task_view_mode == TaskViewMode.ThumbnailView)
        self.view_toggle.connect("toggled", self._on_view_toggled)
        header.pack_end(self.view_toggle)
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Clear finished tasks")
        clear.connect("clicked", lambda *_: self.clear_finished())
        header.pack_end(clear)

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, shrink_start_child=False, position=230)
        split.set_start_child(self._build_sidebar())
        split.set_end_child(self._build_tasks())
        tv = Adw.ToolbarView(content=split)
        tv.add_top_bar(header)
        self.toast_overlay = Adw.ToastOverlay(child=tv)
        self.set_content(self.toast_overlay)
        self._install_drop()
        self.connect("close-request", self._on_close)
        for info in [t.info for t in app.tasks.tasks]:
            self.on_task_event("added", info)
        self._show_view()

    # ---- sidebar --------------------------------------------------------------------------------

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        self.menu_buttons: dict[str, Gtk.Widget] = {}
        for label, icon, target in SIDEBAR:
            if label is None:
                box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
                continue
            content = Adw.ButtonContent(label=label, icon_name=icon, halign=Gtk.Align.START)
            if target.startswith("action:"):
                b = Gtk.Button(child=content, action_name=f"win.{target[7:]}")
            else:
                b = Gtk.MenuButton(child=content, direction=Gtk.ArrowType.RIGHT)
                b.set_create_popup_func(lambda mb, t=target: mb.set_menu_model(self._menu_for(t)))
                b.set_menu_model(self._menu_for(target))
            b.add_css_class("flat")
            b.update_property([Gtk.AccessibleProperty.LABEL], [label.rstrip("…")])
            self.menu_buttons[label.rstrip("…")] = b
            box.append(b)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=210)

    def _menu_for(self, target: str) -> Gio.Menu:
        if target == "capture":
            return menus.capture_menu()
        if target == "upload":
            return menus.upload_menu()
        if target == "workflows":
            return menus.workflows_menu(self.app.store.hotkeys.hotkeys)
        if target == "tools":
            return menus.tools_menu()
        if target == "aftercapture":
            self._sync_flag_states()
            return menus.after_capture_menu()
        if target == "afterupload":
            self._sync_flag_states()
            return menus.after_upload_menu()
        if target == "destinations":
            return menus.destinations_menu()
        if target == "debug":
            return menus.debug_menu()
        return Gio.Menu()

    # ---- actions ----------------------------------------------------------------------------------

    def _add(self, name: str, cb, param: str | None = None) -> None:
        a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
        a.connect("activate", cb)
        self.add_action(a)

    def _install_actions(self) -> None:
        ts = self.app.store.app.default_task_settings
        for flag in AfterCaptureTasks:
            if flag.value:
                a = Gio.SimpleAction.new_stateful(f"ac-{flag.name}", None,
                                                  GLib.Variant("b", bool(ts.after_capture_job & flag)))
                a.connect("change-state", self._on_ac_flag, flag)
                self.add_action(a)
        for flag in AfterUploadTasks:
            if flag.value:
                a = Gio.SimpleAction.new_stateful(f"au-{flag.name}", None,
                                                  GLib.Variant("b", bool(ts.after_upload_job & flag)))
                a.connect("change-state", self._on_au_flag, flag)
                self.add_action(a)
        for name, attr, enum in (("dest-image", "image_destination", ImageDestination),
                                 ("dest-image-file", "image_file_destination", FileDestination),
                                 ("dest-text", "text_destination", TextDestination),
                                 ("dest-file", "file_destination", FileDestination),
                                 ("dest-shortener", "url_shortener_destination", UrlShortenerType),
                                 ("dest-sharing", "url_sharing_service_destination", URLSharingServices)):
            a = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"),
                                              GLib.Variant("s", getattr(ts, attr).value))
            a.connect("change-state", self._on_dest, attr, enum)
            self.add_action(a)
        from .windows import open_named
        for name in ("task-settings", "app-settings", "hotkey-settings", "destination-settings",
                     "custom-uploaders", "history", "image-history", "debug-log", "about"):
            self._add(name, lambda _a, _p, n=name: open_named(self.app, n))
        self._add("screenshots-folder", lambda *_: desktop.open_folder(
            str(self.app.store.screenshots_folder(self.app.store.app.default_task_settings))))
        self._add("debug-folder", lambda *_: desktop.open_folder(str(self.app.log_path.parent)))
        self._add("test-upload", lambda _a, p: self._test_upload(p.unpack()), "s")
        for _section, items in TASK_MENU:
            for _label, action in items:
                self._add(action, lambda _a, _p, act=action: self._task_action(act))

    def _sync_flag_states(self) -> None:
        ts = self.app.store.app.default_task_settings
        for flag in AfterCaptureTasks:
            if flag.value:
                self.lookup_action(f"ac-{flag.name}").set_state(GLib.Variant("b", bool(ts.after_capture_job & flag)))
        for flag in AfterUploadTasks:
            if flag.value:
                self.lookup_action(f"au-{flag.name}").set_state(GLib.Variant("b", bool(ts.after_upload_job & flag)))

    def _on_ac_flag(self, action, value, flag) -> None:
        ts = self.app.store.app.default_task_settings
        ts.after_capture_job = (ts.after_capture_job | flag) if value.unpack() else (ts.after_capture_job & ~flag)
        action.set_state(value)
        self.app.store.save_app()

    def _on_au_flag(self, action, value, flag) -> None:
        ts = self.app.store.app.default_task_settings
        ts.after_upload_job = (ts.after_upload_job | flag) if value.unpack() else (ts.after_upload_job & ~flag)
        action.set_state(value)
        self.app.store.save_app()

    def _on_dest(self, action, value, attr, enum) -> None:
        setattr(self.app.store.app.default_task_settings, attr, enum[value.unpack()])
        action.set_state(value)
        self.app.store.save_app()

    def _test_upload(self, kind: str) -> None:
        """Debug > Test upload: a generated sample to the current destination of that kind."""
        from PIL import Image, ImageDraw
        ts = self.app.store.task_settings_for(None)
        if kind == "image":
            img = Image.new("RGBA", (320, 180), (53, 132, 228, 255))
            ImageDraw.Draw(img).text((20, 80), "ShareL test image", fill="white")
            buf = imaging.png_bytes(img)
            self.app.tasks.upload_data(buf, "ShareL test image.png", ts)
        elif kind == "text":
            self.app.tasks.upload_text("ShareL text upload test", ts)
        elif kind == "file":
            self.app.tasks.upload_data(b"ShareL file upload test\n", "ShareL test file.txt", ts)
        elif kind == "shortener":
            self.app.tasks.shorten_url("https://github.com/ShareX/ShareX", ts)
        elif kind == "sharing":
            self.app.tasks.share_url("https://github.com/ShareX/ShareX", ts)

    # ---- task list -------------------------------------------------------------------------------

    def _build_tasks(self) -> Gtk.Widget:
        self.selection = Gtk.MultiSelection.new(self.store_model)
        cv = Gtk.ColumnView(model=self.selection, show_row_separators=True, reorderable=False)
        for title, prop, expand in (("File name", "title", True), ("Status", "status", False),
                                    ("Speed", "speed", False), ("Elapsed", "elapsed", False), ("URL", "url", True)):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", lambda _f, li: li.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)))
            f.connect("bind", lambda _f, li, p=prop: li.get_child().set_label(li.get_item().get_property(p)))
            col = Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=True)
            cv.append_column(col)
        progress_f = Gtk.SignalListItemFactory()
        progress_f.connect("setup", lambda _f, li: li.set_child(Gtk.ProgressBar(valign=Gtk.Align.CENTER)))
        progress_f.connect("bind", lambda _f, li: li.get_child().set_fraction(li.get_item().info.progress))
        cv.insert_column(2, Gtk.ColumnViewColumn(title="Progress", factory=progress_f, fixed_width=110))
        cv.connect("activate", lambda _cv, pos: self._activate_item(self.store_model.get_item(pos)))
        self.column_view = cv
        self._attach_context(cv)

        gf = Gtk.SignalListItemFactory()
        gf.connect("setup", self._grid_setup)
        gf.connect("bind", self._grid_bind)
        size = self.app.store.app.thumbnail_size
        grid = Gtk.GridView(model=self.selection, factory=gf, max_columns=12, min_columns=1)
        grid.connect("activate", lambda _g, pos: self._activate_item(self.store_model.get_item(pos)))
        self.grid_view = grid
        self._thumb_size = (size.width, size.height)
        self._attach_context(grid)

        self.empty = Adw.StatusPage(title="No tasks yet", icon_name="camera-photo-symbolic",
                                    description="Capture with a hotkey or the Capture menu, or drop files here to "
                                                "upload them.")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(Gtk.ScrolledWindow(child=cv), "list")
        self.stack.add_named(Gtk.ScrolledWindow(child=grid), "grid")
        return self.stack

    def _grid_setup(self, _f, li) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, width_request=self._thumb_size[0],
                          height_request=self._thumb_size[1], can_shrink=True)
        label = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=24)
        bar = Gtk.ProgressBar()
        box.append(label)
        box.append(pic)
        box.append(bar)
        li.set_child(box)

    def _grid_bind(self, _f, li) -> None:
        item: TaskItem = li.get_item()
        box = li.get_child()
        label, pic, bar = box.get_first_child(), box.get_first_child().get_next_sibling(), box.get_last_child()
        label.set_label(item.title if self.app.store.app.show_thumbnail_title else "")
        bar.set_fraction(item.info.progress)
        bar.set_visible(item.info.status == TaskStatus.Working)
        pic.set_paintable(self._thumb(item))

    def _thumb(self, item: TaskItem):
        if item.thumb is None and item.info.file_path and item.info.is_image and Path(item.info.file_path).exists():
            try:
                img = imaging.load(item.info.file_path)
                img.thumbnail(self._thumb_size)
                item.thumb = texture_from_pil(img)
            except OSError:
                item.thumb = None
        if item.thumb is None:
            return Gtk.IconTheme.get_for_display(self.get_display()).lookup_icon(
                "text-x-generic" if item.info.data_type.value == "Text" else "folder-documents-symbolic"
                if not item.info.url else "send-to-symbolic", None, 64, 1, Gtk.TextDirection.NONE, 0)
        return item.thumb

    def _attach_context(self, view: Gtk.Widget) -> None:
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_context, view)
        view.add_controller(click)

    def _on_context(self, gesture, _n, x, y, view) -> None:
        if self.selected_items() == []:
            return
        menu = Gio.Menu()
        for section, items in TASK_MENU:
            sub = Gio.Menu()
            for label, action in items:
                sub.append(label, f"win.{action}")
            if section:
                menu.append_submenu(section, sub)
            else:
                menu.append_section(None, sub)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.set_has_arrow(False)
        pop.popup()

    def selected_items(self) -> list[TaskItem]:
        out = []
        bits = self.selection.get_selection()
        for i in range(bits.get_size()):
            out.append(self.store_model.get_item(bits.get_nth(i)))
        return out

    def _activate_item(self, item: TaskItem) -> None:
        i = item.info
        if i.url_result:
            desktop.open_url(i.url_result)
        elif i.file_path and Path(i.file_path).exists():
            desktop.open_file(i.file_path)

    def _task_action(self, action: str) -> None:
        from .gtkutil import alert, ask
        for item in self.selected_items():
            i = item.info
            p = i.file_path
            exists = bool(p) and Path(p).exists()
            match action:
                case "task-open-url":
                    i.result_url and desktop.open_url(i.result_url)
                case "task-open-short":
                    i.shortened_url and desktop.open_url(i.shortened_url)
                case "task-open-thumb":
                    i.thumbnail_url and desktop.open_url(i.thumbnail_url)
                case "task-open-del":
                    i.deletion_url and desktop.open_url(i.deletion_url)
                case "task-open-file":
                    exists and desktop.open_file(p)
                case "task-open-folder":
                    p and desktop.show_in_folder(p)
                case "task-copy-url":
                    i.result_url and clipboard.set_text(i.result_url)
                case "task-copy-short":
                    i.shortened_url and clipboard.set_text(i.shortened_url)
                case "task-copy-thumb":
                    i.thumbnail_url and clipboard.set_text(i.thumbnail_url)
                case "task-copy-del":
                    i.deletion_url and clipboard.set_text(i.deletion_url)
                case "task-copy-file":
                    exists and clipboard.set_files([p])
                case "task-copy-image":
                    exists and i.is_image and clipboard.set_image(imaging.load(p))
                case "task-copy-text":
                    if exists and not i.is_image:
                        clipboard.set_text(Path(p).read_text(errors="replace"))
                    elif i.text:
                        clipboard.set_text(i.text)
                case "task-copy-path":
                    p and clipboard.set_text(p)
                case "task-copy-name":
                    clipboard.set_text(i.file_name)
                case "task-copy-folder":
                    p and clipboard.set_text(str(Path(p).parent))
                case "task-upload":
                    exists and self.app.tasks.upload_file(p, self.app.store.task_settings_for(None))
                case "task-edit":
                    if exists and i.is_image:
                        from .windows.editor import open_editor_for_file
                        open_editor_for_file(self.app, p)
                case "task-pin":
                    if exists and i.is_image:
                        from .windows.pin import pin_image
                        pin_image(self.app, imaging.load(p))
                case "task-ocr":
                    if exists and i.is_image:
                        from .tools.ocr import open_ocr
                        open_ocr(self.app, imaging.load(p), self.app.store.app.default_task_settings
                                 .capture_settings.ocr)
                case "task-qr":
                    if exists and i.is_image:
                        from ..tools.qr import decode_image
                        from .tools.qrcode import open_qr_results
                        img = imaging.load(p)
                        open_qr_results(self.app, "\n".join(decode_image(img)), img)
                case "task-show-qr":
                    from .tools.qrcode import open_qr_window
                    open_qr_window(self.app, i.url_result or p)
                case "task-stop":
                    t = self.app.tasks.find(i.id)
                    t and t.cancel.set()
                case "task-remove":
                    self.remove_item(i.id)
                case "task-delete":
                    if exists:
                        ask(self, "Delete file?", p, [("cancel", "Cancel"), ("delete", "Delete")],
                            lambda r, p=p: r == "delete" and os.path.exists(p) and os.unlink(p),
                            destructive="delete")
                case "task-response":
                    alert(self, "Response", i.response or "(no response)")
                case "task-errors":
                    alert(self, "Errors", "\n".join(i.errors) or "(no errors)")

    def remove_item(self, task_id: str) -> None:
        item = self.items.pop(task_id, None)
        if item is None:
            return
        ok, pos = self.store_model.find(item)
        if ok:
            self.store_model.remove(pos)
        self.app.tasks.remove(task_id)
        self._show_view()

    def clear_finished(self) -> None:
        for tid, item in list(self.items.items()):
            if item.info.status in (TaskStatus.Completed, TaskStatus.Failed, TaskStatus.Stopped):
                self.remove_item(tid)

    # ---- task events ---------------------------------------------------------------------------

    def on_task_event(self, event: str, info: TaskInfo) -> None:
        item = self.items.get(info.id)
        if item is None:
            item = TaskItem(info)
            self.items[info.id] = item
            self.store_model.insert(0, item)
        else:
            item.thumb = None if event == "finished" else item.thumb
            ok, pos = self.store_model.find(item)
            if ok:
                self.store_model.items_changed(pos, 1, 1)
        self._show_view()
        if event == "finished" and info.status == TaskStatus.Failed and self.get_visible():
            self.toast_overlay.add_toast(Adw.Toast(title=f"Task failed: {info.errors[0] if info.errors else ''}"[:120],
                                                   timeout=5))

    def _show_view(self) -> None:
        if self.store_model.get_n_items() == 0:
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("grid" if self.view_toggle.get_active() else "list")

    def _on_view_toggled(self, btn) -> None:
        self.app.store.app.task_view_mode = TaskViewMode.ThumbnailView if btn.get_active() else TaskViewMode.ListView
        self.app.store.save_app()
        self._show_view()

    # ---- drag and drop ---------------------------------------------------------------------------

    def _install_drop(self) -> None:
        target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList, Gdk.Texture, str])
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _t, value, _x, _y) -> bool:
        return handle_drop(self.app, value)

    def _on_close(self, *_a) -> bool:
        w, h = self.get_default_size()
        self.app.store.app.main_window_size.width, self.app.store.app.main_window_size.height = w, h
        self.app.store.save_app()
        self.app.main_window = None
        return False


def handle_drop(app, value) -> bool:
    ts = app.store.task_settings_for(None)
    if isinstance(value, Gdk.FileList):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        app.upload_paths(paths, ts)
        return bool(paths)
    if isinstance(value, Gdk.Texture):
        from .gtkutil import pil_from_texture
        app.tasks.run_image(pil_from_texture(value), ts)
        return True
    if isinstance(value, str) and value.strip():
        text = value.strip()
        from urllib.parse import urlparse
        if text.startswith("file://"):
            from urllib.parse import unquote
            app.upload_paths([unquote(urlparse(line).path) for line in text.splitlines() if line.strip()], ts)
        elif urlparse(text).scheme in ("http", "https") and "\n" not in text:
            app.tasks.download_upload(text, ts) if ts.upload_settings.clipboard_upload_url_contents else \
                app.tasks.shorten_url(text, ts) if ts.upload_settings.clipboard_upload_shorten_url else \
                app.tasks.upload_text(text, ts)
        else:
            app.tasks.upload_text(text, ts)
        return True
    return False
