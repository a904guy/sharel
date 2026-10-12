"""The actions toolbar (ShareX's ActionsToolbarForm): a small floating, always-on-top bar of job
buttons. The list is configurable (right click > Edit toolbar); drag the grip to move it."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, Gtk

from ...core.enums import HOTKEY_DESCRIPTIONS, HOTKEY_GROUPS, HotkeyType
from ..gtkutil import live
from ...core.jobs import dispatch

JOB_ICONS = {
    "RectangleRegion": "selection-mode-symbolic", "PrintScreen": "video-display-symbolic",
    "ActiveWindow": "window-new-symbolic", "ActiveMonitor": "preferences-desktop-display-symbolic",
    "ScreenRecorder": "media-record-symbolic", "ScreenRecorderGIF": "camera-video-symbolic",
    "FileUpload": "document-send-symbolic", "ClipboardUpload": "edit-paste-symbolic",
    "ClipboardUploadWithContentViewer": "edit-paste-symbolic", "ScreenColorPicker": "color-select-symbolic",
    "ColorPicker": "color-select-symbolic", "OCR": "format-text-plaintext-symbolic", "ImageEditor":
    "document-edit-symbolic", "PinToScreen": "view-pin-symbolic", "UploadText": "insert-text-symbolic",
    "ShortenURL": "insert-link-symbolic", "OpenHistory": "document-open-recent-symbolic",
    "ScrollingCapture": "view-continuous-symbolic", "Ruler": "zoom-fit-best-symbolic",
    "QRCode": "view-grid-symbolic", "OpenScreenshotsFolder": "folder-pictures-symbolic",
}

_state: dict = {}


class ActionsToolbar(Adw.Window):
    def __init__(self, app) -> None:
        from ..placement import unique_title
        super().__init__(application=app, title=unique_title("ShareL actions toolbar"), resizable=False)
        self.app = app
        self.box = Gtk.Box(spacing=2, margin_top=2, margin_bottom=2, margin_start=2, margin_end=2)
        self.set_content(self.box)
        self.rebuild()
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda _g, _n, x, y: self._menu(x, y))
        self.add_controller(click)
        group = Gio.SimpleActionGroup()
        for name, fn in (("edit", self.edit), ("lock", self.toggle_lock), ("topmost", self.toggle_topmost),
                         ("close", self.close)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, fn=fn: fn())
            group.add_action(a)
        self.insert_action_group("toolbar", group)
        self.connect("close-request", self._closed)

    def rebuild(self) -> None:
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt
        grip = Gtk.Image(icon_name="list-drag-handle-symbolic", tooltip_text="Drag to move")
        drag = Gtk.GestureClick(button=1)
        drag.connect("pressed", self._grip_pressed)
        grip.add_controller(drag)
        self.box.append(grip)
        for job in self.app.store.app.actions_toolbar_list:
            if job.value == "None":
                self.box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
                continue
            b = Gtk.Button(icon_name=JOB_ICONS.get(job.value, "system-run-symbolic"),
                           tooltip_text=HOTKEY_DESCRIPTIONS.get(job.value, job.value))
            b.update_property([Gtk.AccessibleProperty.LABEL], [HOTKEY_DESCRIPTIONS.get(job.value, job.value)])
            b.add_css_class("flat")
            b.connect("clicked", lambda *_a, j=job: dispatch(self.app, j, source="toolbar"))
            self.box.append(b)

    def _grip_pressed(self, gesture, _n, x, y) -> None:
        if self.app.store.app.actions_toolbar_lock_position:
            return
        surf = self.get_surface()
        dev = gesture.get_current_event_device()
        if surf is not None and dev is not None and hasattr(surf, "begin_move"):
            surf.begin_move(dev, 1, x, y, gesture.get_current_event_time())

    def _menu(self, x: float, y: float) -> None:
        cfg = self.app.store.app
        m = Gio.Menu()
        m.append("Edit toolbar…", "toolbar.edit")
        m.append("Unlock position" if cfg.actions_toolbar_lock_position else "Lock position", "toolbar.lock")
        m.append("Don't stay on top" if cfg.actions_toolbar_stay_top_most else "Stay on top", "toolbar.topmost")
        m.append("Close", "toolbar.close")
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(self.box)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        pop.popup()

    def toggle_lock(self) -> None:
        cfg = self.app.store.app
        cfg.actions_toolbar_lock_position = not cfg.actions_toolbar_lock_position
        self.app.store.save_app()

    def toggle_topmost(self) -> None:
        cfg = self.app.store.app
        cfg.actions_toolbar_stay_top_most = not cfg.actions_toolbar_stay_top_most
        self.app.store.save_app()
        from ..placement import when_mapped
        from ...shell.client import shell
        when_mapped(self, lambda w: shell().set_above(w.id, cfg.actions_toolbar_stay_top_most))

    def edit(self) -> None:
        open_toolbar_editor(self.app, on_saved=self.rebuild)

    def _closed(self, *_a) -> bool:
        from ..placement import find_own
        w = find_own(self.get_title() or "")
        if w is not None:
            self.app.store.app.actions_toolbar_position_x, self.app.store.app.actions_toolbar_position_y = w.frame[:2]
            self.app.store.save_app()
        _state.pop("win", None)
        return False


def toggle_actions_toolbar(app) -> ActionsToolbar | None:
    win = _state.get("win")
    if live(win):
        win.close()
        _state.pop("win", None)
        return None
    win = ActionsToolbar(app)
    _state["win"] = win
    win.present()
    cfg = app.store.app
    pos = (cfg.actions_toolbar_position_x, cfg.actions_toolbar_position_y) if cfg.actions_toolbar_position_x >= 0 \
        else None
    from ..placement import keep_above, position
    if cfg.actions_toolbar_stay_top_most:
        keep_above(win, pos)
    elif pos:
        position(win, *pos)
    return win


def open_toolbar_editor(app, on_saved=None) -> Adw.Window:
    """Add, remove and reorder the toolbar's jobs (None = separator)."""
    win = Adw.Window(application=app, title="Edit actions toolbar", default_width=480, default_height=560)
    items = list(app.store.app.actions_toolbar_list)
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    listbox.add_css_class("boxed-list")

    def label(job) -> str:
        return "──── separator ────" if job.value == "None" else HOTKEY_DESCRIPTIONS.get(job.value, job.value)

    def refresh(select: int | None = None):
        while (row := listbox.get_row_at_index(0)) is not None:
            listbox.remove(row)
        for job in items:
            listbox.append(Gtk.Label(label=label(job), xalign=0, margin_top=6, margin_bottom=6, margin_start=8))
        if select is not None and 0 <= select < len(items):
            listbox.select_row(listbox.get_row_at_index(select))

    def selected() -> int:
        r = listbox.get_selected_row()
        return r.get_index() if r is not None else -1

    jobs = [j for g in HOTKEY_GROUPS.values() for j in g]
    picker = Gtk.DropDown.new_from_strings(["Separator"] + [HOTKEY_DESCRIPTIONS.get(j, j) for j in jobs])

    def add(*_):
        i = picker.get_selected()
        items.append(HotkeyType["None"] if i == 0 else HotkeyType[jobs[i - 1]])
        refresh(len(items) - 1)

    def remove(*_):
        i = selected()
        if i >= 0:
            items.pop(i)
            refresh(min(i, len(items) - 1))

    def move(delta):
        i = selected()
        j = i + delta
        if 0 <= i < len(items) and 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
            refresh(j)

    def save(*_):
        app.store.app.actions_toolbar_list = list(items)
        app.store.save_app()
        if on_saved:
            on_saved()
        win.destroy()

    bar = Gtk.Box(spacing=6)
    bar.append(picker)
    for lbl, fn in (("Add", add), ("Remove", remove), ("Move up", lambda *_: move(-1)),
                    ("Move down", lambda *_: move(1))):
        b = Gtk.Button(label=lbl)
        b.connect("clicked", fn)
        bar.append(b)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8,
                  margin_end=8)
    box.append(bar)
    box.append(Gtk.ScrolledWindow(child=listbox, vexpand=True))
    header = Adw.HeaderBar()
    ok = Gtk.Button(label="Save")
    ok.add_css_class("suggested-action")
    ok.connect("clicked", save)
    header.pack_end(ok)
    tv = Adw.ToolbarView(content=box)
    tv.add_top_bar(header)
    win.set_content(tv)
    refresh()
    win._items = items  # type: ignore[attr-defined]
    win._add, win._remove, win._move, win._save, win._picker = add, remove, move, save, picker  # type: ignore
    win.present()
    return win
