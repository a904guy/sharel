"""Inspect window tool (ShareX InspectWindowForm).

ShareX: "Inspect window" lets you click a window (the window list button picks one by title), then shows
its handle, title, class name, process name/file, PID, window and client rectangles and styles; Refresh
re-reads it; "Top most" and "Opacity (%)" change it. ShareL shows everything the ShareL GNOME Shell
extension's ``GetWindowDetails`` reports (ids, title, WM class, app ids, process, geometry, client rect,
state flags, opacity, layer, role, borderless state…), plus Copy all and a move/resize editor.
Picking runs on a worker thread (the extension's PickWindow waits for the user's click).
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...core import clipboard
from ...shell.client import ShellUnavailable, Window, shell
from ._base import ToolWindow, button, existing, single, spin

EXTENSION_NEEDED = ("Needs the ShareL GNOME Shell extension: on Wayland only GNOME Shell knows other windows' "
                    "details. Install and enable it (gnome-extensions enable sharel@hawkins.app), log out and "
                    "back in, then press Refresh.")


def _rect(r) -> str:
    try:
        x, y, w, h = (int(v) for v in r)
    except (TypeError, ValueError):
        return str(r)
    return f"X: {x}, Y: {y}, Width: {w}, Height: {h}"


def _yes(v) -> str:
    return "Yes" if v else "No"


def process_file_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def process_name(pid: int, d: dict) -> str:
    if pid:
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            pass
    return (d.get("wm_class") or d.get("app_id") or "").split(".")[-1].lower()


STATE_FLAGS = (("minimized", "Minimized"), ("maximized", "Maximized"), ("fullscreen", "Fullscreen"),
               ("above", "Always on top"), ("focus", "Focused"), ("sticky", "On all workspaces"),
               ("skip_taskbar", "Skip taskbar"), ("decorated", "Decorated"), ("resizeable", "Resizable"),
               ("on_current_workspace", "On current workspace"))

# key -> label, formatter; in display order (ShareX's order first)
FIELDS: list[tuple[str, str, object]] = [
    ("id", "Window ID", lambda v: f"{int(v):08X} ({int(v)})"),
    ("title", "Window title", str),
    ("wm_class", "Class name", str),
    ("wm_class_instance", "Class instance", str),
    ("app_id", "Application ID", str),
    ("app_name", "Application name", str),
    ("gtk_app_id", "GTK application ID", str),
    ("sandboxed_app_id", "Sandboxed application ID", str),
    ("_process_name", "Process name", str),
    ("_process_file", "Process file name", str),
    ("pid", "Process identifier", lambda v: str(int(v))),
    ("client", "Client type", lambda v: "X11" if v == "x11" else "Wayland"),
    ("type", "Window type", str),
    ("frame", "Window rectangle", _rect),
    ("buffer", "Buffer rectangle", _rect),
    ("client_rect", "Client rectangle", _rect),
    ("monitor", "Monitor", str),
    ("workspace", "Workspace", lambda v: "All" if int(v) < 0 else str(int(v) + 1)),
    ("stacking", "Stacking order", str),
    ("_state", "Window state", str),
    ("opacity", "Opacity", lambda v: f"{round(int(v) / 255 * 100)}% ({int(v)}/255)"),
    ("layer", "Layer", str),
    ("role", "Role", str),
    ("description", "Description", str),
    ("startup_id", "Startup ID", str),
    ("transient_for", "Transient for", lambda v: f"{int(v):08X}" if int(v) else ""),
    ("user_time", "User time", str),
    ("borderless", "Borderless", lambda v: f"Yes ({v})" if v else "No"),
]


def describe(d: dict) -> list[tuple[str, str]]:
    """(label, value) pairs for every detail, ShareX's AddInfo skipping empty values."""
    d = dict(d)
    pid = int(d.get("pid") or 0)
    d["_process_name"] = process_name(pid, d)
    d["_process_file"] = process_file_name(pid)
    d["_state"] = "\n".join(f"{label}: {_yes(d[k])}" for k, label in STATE_FLAGS if k in d)
    out = []
    known = {k for k, _l, _f in FIELDS} | {k for k, _l in STATE_FLAGS}
    for key, label, fmt in FIELDS:
        if key not in d or d[key] is None or d[key] == "":
            continue
        try:
            value = fmt(d[key])  # type: ignore[operator]
        except (TypeError, ValueError):
            value = str(d[key])
        if value:
            out.append((label, value))
    for key in sorted(k for k in d if k not in known):
        if d[key] not in (None, ""):
            out.append((key.replace("_", " ").capitalize(), str(d[key])))
    return out


def as_text(pairs: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{label}\n{value}" for label, value in pairs)


class InspectWindow(ToolWindow):
    def __init__(self, app) -> None:
        super().__init__(app, "Inspect window", 560, 720)
        self.selected: Window | None = None
        self.details: dict = {}
        self._updating = False
        self.pick_worker = None

        self.banner = Adw.Banner(title="Needs the ShareL GNOME Shell extension", tooltip_text=EXTENSION_NEEDED)
        self.toolbar.add_top_bar(self.banner)

        top = Gtk.Box(spacing=6)
        self.pick_button = button("Inspect window…", self.pick, "Click a window to inspect it",
                                  icon="find-location-symbolic", css="suggested-action")
        self.list_button = Gtk.MenuButton(label="Window list", tooltip_text="Choose a window from the list")
        self.list_popover = Gtk.Popover()
        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.connect("row-activated", lambda _lb, row: self._choose(row.window))
        self.list_popover.set_child(Gtk.ScrolledWindow(child=self.list_box, min_content_height=300,
                                                       min_content_width=360, propagate_natural_height=True))
        self.list_button.set_popover(self.list_popover)
        self.list_popover.connect("show", lambda *_: self.fill_window_list())
        self.refresh_button = button("Refresh", self.refresh, "Read the window's details again",
                                     icon="view-refresh-symbolic")
        self.copy_button = button("Copy all", self.copy_all, "Copy every detail as text", icon="edit-copy-symbolic")
        for w in (self.pick_button, self.list_button, self.refresh_button, self.copy_button):
            top.append(w)

        self.info = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.info.add_css_class("boxed-list")
        self.placeholder = Gtk.Label(label="No window selected.", margin_top=24, margin_bottom=24)
        self.placeholder.add_css_class("dim-label")
        self.info.set_placeholder(self.placeholder)

        # actions
        acts = Adw.PreferencesGroup(title="Window")
        self.top_most = Adw.SwitchRow(title="Top most", subtitle="Keep the window above others")
        self.top_most.connect("notify::active", lambda *_: self._top_most_changed())
        self.opacity_row = Adw.SpinRow(title="Opacity (%)", subtitle="100 = opaque",
                                       adjustment=Gtk.Adjustment(value=100, lower=1, upper=100, step_increment=1,
                                                                 page_increment=10))
        self.opacity_row.connect("notify::value", lambda *_: self._opacity_changed())
        acts.add(self.top_most)
        acts.add(self.opacity_row)
        geo = Adw.ActionRow(title="Position and size")
        self.x_spin = spin(0, -100000, 100000, tooltip="X")
        self.y_spin = spin(0, -100000, 100000, tooltip="Y")
        self.w_spin = spin(100, 1, 100000, tooltip="Width")
        self.h_spin = spin(100, 1, 100000, tooltip="Height")
        gbox = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        for lab, s in (("X", self.x_spin), ("Y", self.y_spin), ("W", self.w_spin), ("H", self.h_spin)):
            gbox.append(Gtk.Label(label=lab))
            gbox.append(s)
        self.apply_geo = button("Apply", self.apply_geometry, "Move and resize the window")
        gbox.append(self.apply_geo)
        geo.add_suffix(gbox)
        acts.add(geo)
        self.actions_group = acts

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(top)
        body.append(Gtk.ScrolledWindow(child=self.info, vexpand=True, min_content_height=260))
        body.append(acts)
        self.set_body(body)
        self.update_ui()

    # -- selection --

    def pick(self) -> None:
        sh = shell()
        if not sh.available:
            self.update_ui()
            self.error("Inspect window", EXTENSION_NEEDED)
            return
        if self.pick_worker is not None and self.pick_worker.running:
            return
        self.pick_button.set_sensitive(False)

        def work(_progress, _cancel):
            return sh.pick_window("Click the window to inspect (Esc cancels)")

        def done(win):
            if win is not None:
                self._choose(win)

        self.pick_worker = self.run(work, done, on_error=lambda e: self.error("Inspect window", e),
                                    on_finally=self.update_ui)

    def fill_window_list(self) -> None:
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)
        try:
            wins = sorted((w for w in shell().get_windows() if w.title), key=lambda w: w.title.lower())
        except ShellUnavailable:
            wins = []
        for w in wins:
            row = Adw.ActionRow(title=w.title[:50] + ("..." if len(w.title) > 50 else ""),
                                subtitle=w.app_name or w.wm_class, activatable=True)
            row.window = w  # type: ignore[attr-defined]
            self.list_box.append(row)

    def _choose(self, win: Window) -> None:
        self.list_popover.popdown()
        self.selected = win
        self.refresh()

    def refresh(self) -> None:
        if self.selected is None:
            self.update_ui()
            return
        try:
            d = shell().get_window_details(self.selected.id)
        except ShellUnavailable as e:
            self.error("Inspect window", e)
            d = {}
        if not d:
            self.details = {}
            self.toast("The window is gone")
        else:
            self.details = d
            self.selected = Window.of(d)
        self.update_ui()

    # -- display --

    def update_ui(self) -> None:
        avail = shell().available
        self.banner.set_revealed(not avail)
        self._updating = True
        self.pick_button.set_sensitive(avail and not (self.pick_worker is not None and self.pick_worker.running))
        self.list_button.set_sensitive(avail)
        have = avail and bool(self.details)
        self.refresh_button.set_sensitive(avail and self.selected is not None)
        self.copy_button.set_sensitive(bool(self.details))
        self.actions_group.set_sensitive(have)
        while (row := self.info.get_row_at_index(0)) is not None:
            self.info.remove(row)
        if not avail:
            self.placeholder.set_label(EXTENSION_NEEDED)
            self.placeholder.set_wrap(True)
        else:
            self.placeholder.set_label("No window selected. Press \"Inspect window…\" and click a window.")
        for label, value in describe(self.details) if self.details else []:
            row = Adw.ActionRow(title=label, subtitle=value, subtitle_selectable=True)
            row.add_css_class("property")
            self.info.append(row)
        if have:
            d = self.details
            self.top_most.set_active(bool(d.get("above")))
            self.opacity_row.set_value(max(1, round(int(d.get("opacity", 255)) / 255 * 100)))
            fr = d.get("frame") or (0, 0, 100, 100)
            self.x_spin.set_value(fr[0])
            self.y_spin.set_value(fr[1])
            self.w_spin.set_value(max(1, fr[2]))
            self.h_spin.set_value(max(1, fr[3]))
        self._updating = False

    def text(self) -> str:
        return as_text(describe(self.details)) if self.details else ""

    def copy_all(self) -> None:
        t = self.text()
        if t:
            clipboard.set_text(t)
            self.toast("Copied")

    # -- actions --

    def _top_most_changed(self) -> None:
        if self._updating or self.selected is None:
            return
        try:
            shell().set_above(self.selected.id, self.top_most.get_active())
        except ShellUnavailable as e:
            self.error("Inspect window", e)
        self.refresh()

    def _opacity_changed(self) -> None:
        if self._updating or self.selected is None:
            return
        try:
            shell().set_opacity(self.selected.id, round(self.opacity_row.get_value() / 100 * 255))
        except ShellUnavailable as e:
            self.error("Inspect window", e)
        self.refresh()

    def apply_geometry(self) -> None:
        if self.selected is None:
            return
        try:
            res = shell().move_resize(self.selected.id, int(self.x_spin.get_value()), int(self.y_spin.get_value()),
                                      int(self.w_spin.get_value()), int(self.h_spin.get_value()))
            if isinstance(res, dict) and res.get("ok") is False:
                self.error("Inspect window", res.get("error", "Move/resize failed"))
        except ShellUnavailable as e:
            self.error("Inspect window", e)
        self.refresh()


def open(app, ctx) -> InspectWindow:  # noqa: A001 - the tool contract's name
    fresh = existing("inspectwindow") is None
    win = single("inspectwindow", lambda: InspectWindow(app))
    if fresh and shell().available:
        win.pick()  # ShareX starts by selecting a window
    return win
