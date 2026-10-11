"""Borderless window tool (ShareX BorderlessWindowForm + BorderlessWindowManager + its settings form).

ShareX: type (or pick from the window list) a window title, "Make window borderless" toggles the window:
its frame styles are removed and it is moved/resized to fill its monitor (the monitor's working area when
"Exclude taskbar area" is on); pressing it again restores the saved styles and rectangle. Settings: remember
the window title, auto close this window after a successful toggle, exclude taskbar area.

ShareL does the same through the ShareL GNOME Shell extension: ``MakeBorderless`` removes the decorations
(X11 windows: Motif hints, then ``MoveResize`` to the monitor rectangle; Wayland windows can't drop their
client-side decorations, so the extension makes them fullscreen, which is the borderless-fill result ShareX
gives). Restore puts the decorations back and the saved frame rectangle. Without the extension the window
shows what is needed and every action is disabled.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...shell.client import ShellUnavailable, Window, shell
from ._base import stored_tool_settings
from ._base import ToolWindow, button, single

EXTENSION_NEEDED = ("Needs the ShareL GNOME Shell extension: GNOME doesn't let applications list, undecorate or "
                    "move other windows on Wayland. Install and enable it (gnome-extensions enable "
                    "sharel@hawkins.app), log out and back in, then press Refresh.")

# GNOME Shell's top bar (logical px) — what "exclude taskbar area" leaves free on the primary monitor.
TOP_BAR_HEIGHT = 32

# window id -> frame (x, y, w, h) before it was made borderless (ShareX's borderlessWindowList)
_saved: dict[int, tuple[int, int, int, int]] = {}


def extension_banner() -> Adw.Banner:
    return Adw.Banner(title="Needs the ShareL GNOME Shell extension", revealed=not shell().available,
                      tooltip_text=EXTENSION_NEEDED)


def search_window(windows: list[Window], title: str) -> Window | None:
    """ShareX NativeMethods.SearchWindow: an exact title match first, then the first title containing it."""
    t = title.strip()
    if not t:
        return None
    for w in windows:
        if w.title == t:
            return w
    low = t.lower()
    for w in windows:
        if low in w.title.lower():
            return w
    return None


def fill_rect(sh, win: Window, exclude_taskbar: bool) -> tuple[int, int, int, int] | None:
    """The rectangle a borderless window fills: its monitor, minus GNOME's top bar when excluding it."""
    mons = sh.get_monitors()
    mon = next((m for m in mons if m.index == win.monitor), None) or (mons[0] if mons else None)
    if mon is None:
        return None
    x, y, w, h = mon.rect
    if exclude_taskbar and mon.primary:
        y += TOP_BAR_HEIGHT
        h -= TOP_BAR_HEIGHT
    return (x, y, w, h)


def make_borderless(win: Window, exclude_taskbar: bool) -> dict:
    sh = shell()
    _saved[win.id] = tuple(win.frame)
    res = sh.make_borderless(win.id, True)
    if not res.get("ok"):
        _saved.pop(win.id, None)
        raise ShellUnavailable(res.get("error") or "the window could not be made borderless")
    # A fullscreened (Wayland) window already fills its monitor; MoveResize would un-fullscreen it.
    if res.get("method") != "fullscreen":
        rect = fill_rect(sh, win, exclude_taskbar)
        if rect is not None:
            sh.move_resize(win.id, *rect)
    return res


def restore(win: Window) -> dict:
    sh = shell()
    res = sh.make_borderless(win.id, False)
    if not res.get("ok"):
        raise ShellUnavailable(res.get("error") or "the window could not be restored")
    frame = _saved.pop(win.id, None)
    if frame and frame[2] > 0 and frame[3] > 0 and res.get("method") != "fullscreen":
        sh.move_resize(win.id, *frame)
    return res


def is_borderless(win: Window) -> bool:
    if win.id in _saved:
        return True
    try:
        return bool(shell().get_window_details(win.id).get("borderless"))
    except ShellUnavailable:
        return False


class BorderlessWindow(ToolWindow):
    def __init__(self, app, ctx=None) -> None:
        super().__init__(app, "Borderless window", 560, 520)
        self.tool, self._save_tool = stored_tool_settings(app, ctx)
        self.windows: list[Window] = []
        self.remember_title = True
        self.auto_close = False

        self.banner = extension_banner()
        self.toolbar.add_top_bar(self.banner)
        refresh = button("", self.refresh, "Refresh the window list", icon="view-refresh-symbolic")
        self.header.pack_start(refresh)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(Gtk.Label(label="Window title:", xalign=0))
        self.title_entry = Gtk.Entry(hexpand=True, tooltip_text="Window title",
                                     placeholder_text="Title of the window (or choose it below)")
        self.title_entry.connect("changed", lambda *_: self._update_buttons())
        self.title_entry.connect("activate", lambda *_: self.toggle_clicked())
        body.append(self.title_entry)

        self.filter_entry = Gtk.SearchEntry(placeholder_text="Filter windows", tooltip_text="Filter windows")
        self.filter_entry.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        body.append(self.filter_entry)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        self.listbox.connect("row-selected", self._row_selected)
        self.listbox.connect("row-activated", lambda *_: self.toggle_clicked())
        body.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True, min_content_height=180))

        opts = Adw.PreferencesGroup(title="Settings")
        self.remember_row = Adw.SwitchRow(title="Remember window title", active=self.remember_title)
        self.remember_row.connect("notify::active", lambda r, _p: setattr(self, "remember_title", r.get_active()))
        self.autoclose_row = Adw.SwitchRow(title="Auto close this window", active=self.auto_close,
                                           subtitle="After the window was made borderless or restored")
        self.autoclose_row.connect("notify::active", lambda r, _p: setattr(self, "auto_close", r.get_active()))
        self.exclude_row = Adw.SwitchRow(title="Exclude taskbar area", active=self.tool.border_window_exclude_taskbar,
                                         subtitle="Leave GNOME's top bar visible")
        self.exclude_row.connect("notify::active", lambda r, _p: self._set_exclude(r.get_active()))
        for r in (self.remember_row, self.autoclose_row, self.exclude_row):
            opts.add(r)
        body.append(opts)

        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.restore_button = button("Restore", self.restore_clicked, "Give the window its frame back")
        self.toggle_button = button("Make window borderless", self.toggle_clicked,
                                    "Remove the frame and fill the monitor (press again to restore)",
                                    css="suggested-action")
        btns.append(self.restore_button)
        btns.append(self.toggle_button)
        body.append(btns)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
        body.append(self.status)
        self.set_body(body)

        if self.tool.border_window_title:
            self.title_entry.set_text(self.tool.border_window_title)
        self.refresh()

    # -- list --

    def refresh(self) -> None:
        sh = shell()
        self.banner.set_revealed(not sh.available)
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        self.windows = []
        if sh.available:
            try:
                self.windows = sorted((w for w in sh.get_windows() if w.title and w.type in ("NORMAL", "DIALOG")),
                                      key=lambda w: w.title.lower())
            except ShellUnavailable as e:
                self.status.set_label(str(e))
        for w in self.windows:
            row = Adw.ActionRow(title=w.title[:80], subtitle=w.app_name or w.wm_class or w.app_id,
                                activatable=True)
            row.window = w  # type: ignore[attr-defined]
            if w.id in _saved:
                row.add_suffix(Gtk.Label(label="Borderless"))
            self.listbox.append(row)
        self.status.set_label(EXTENSION_NEEDED if not sh.available else f"{len(self.windows)} window(s)")
        self._update_buttons()

    def _filter(self, row) -> bool:
        q = self.filter_entry.get_text().strip().lower()
        w = getattr(row, "window", None)
        if w is None or not q:
            return True
        return q in w.title.lower() or q in (w.app_name or "").lower() or q in (w.wm_class or "").lower()

    def _row_selected(self, _lb, row) -> None:
        if row is not None and hasattr(row, "window"):
            self.title_entry.set_text(row.window.title)

    def _set_exclude(self, on: bool) -> None:
        self.tool.border_window_exclude_taskbar = on
        self._save_tool()

    def _update_buttons(self) -> None:
        ok = shell().available and bool(self.title_entry.get_text().strip())
        self.toggle_button.set_sensitive(ok)
        self.restore_button.set_sensitive(ok)

    # -- actions --

    def target(self) -> Window | None:
        title = self.title_entry.get_text()
        try:
            wins = shell().get_windows()
        except ShellUnavailable:
            wins = self.windows
        w = search_window(wins, title)
        if w is None:
            self.error("Borderless window", "Unable to find a window with the specified window title.")
        return w

    def _remember(self) -> None:
        self.tool.border_window_title = self.title_entry.get_text() if self.remember_title else ""
        self._save_tool()

    def toggle_clicked(self) -> None:
        if not shell().available:
            self.error("Borderless window", EXTENSION_NEEDED)
            return
        self._remember()
        w = self.target()
        if w is None:
            return
        try:
            if is_borderless(w):
                restore(w)
                self.status.set_label(f"Restored: {w.title}")
            else:
                make_borderless(w, self.exclude_row.get_active())
                self.status.set_label(f"Borderless: {w.title}")
        except ShellUnavailable as e:
            self.error("Borderless window", e)
            return
        self._done()

    def restore_clicked(self) -> None:
        if not shell().available:
            self.error("Borderless window", EXTENSION_NEEDED)
            return
        w = self.target()
        if w is None:
            return
        try:
            restore(w)
        except ShellUnavailable as e:
            self.error("Borderless window", e)
            return
        self.status.set_label(f"Restored: {w.title}")
        self._done()

    def _done(self) -> None:
        from .. import sounds
        sounds.play("ActionCompleted", self.app.store.app.default_task_settings.general_settings)
        if self.auto_close:
            self.close()
        else:
            self.refresh()


def open(app, ctx) -> BorderlessWindow:  # noqa: A001 - the tool contract's name
    win = single("borderless", lambda: BorderlessWindow(app, ctx))
    win.refresh()
    return win
