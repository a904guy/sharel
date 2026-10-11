"""Debug log (ShareX's DebugForm): the live in-memory log plus a System info section.

The log view starts with everything ``MEMORY_HANDLER`` holds and appends new lines as they are
logged (from any thread). Filter text, auto-scroll, copy all, open the log folder, clear view.
"""
from __future__ import annotations

import os
import platform
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ... import __version__
from ...core import paths
from .. import gtkutil
from ..debuglog import MEMORY_HANDLER
from ._sectioned import WindowRegistry

_REGISTRY = WindowRegistry()


def open_debug_log(app) -> DebugLogWindow:
    return _REGISTRY.get_or_create(id(app), lambda: DebugLogWindow(app))


def basic_info(app) -> list[tuple[str, str]]:
    """Facts that need no probing (fast; also used by the About dialog)."""
    import gi as _gi
    from ...shell.client import shell
    sh = shell()
    ext = "not available"
    if sh.available:
        ext = f"running (version {getattr(sh, 'version', '') or '?'})"
    elif os.environ.get("SHAREL_NO_EXTENSION"):
        ext = "disabled (SHAREL_NO_EXTENSION)"
    hk = app.hotkeys
    store = app.store
    pref = store.app.hotkey_backend.value if store else "?"
    active = (hk.active_backend if hk else "") or "none"
    return [
        ("ShareL", __version__),
        ("Python", f"{platform.python_version()} ({sys.executable})"),
        ("PyGObject", getattr(_gi, "__version__", "?")),
        ("GTK", f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"),
        ("libadwaita", f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"),
        ("OS", platform.platform()),
        ("Desktop", f"{os.environ.get('XDG_CURRENT_DESKTOP', '?')} / {os.environ.get('XDG_SESSION_TYPE', '?')}"),
        ("GNOME Shell extension", ext),
        ("Hotkey backend", f"{active} (setting: {pref})"
                           + (", hotkeys disabled" if store and store.app.disable_hotkeys else "")),
        ("Capture backend setting", store.app.capture_backend.value if store else "?"),
        ("Config folder", str(store.directory) if store else str(paths.config_dir())),
        ("Log file", str(app.log_path or paths.log_dir())),
    ]


def capture_backends() -> list[tuple[str, str]]:
    """Probe the capture backends (may take a moment: D-Bus round trips)."""
    from ...capture import backends
    return [(f"Capture: {name}", "available" if ok else "not available") for name, ok in backends.describe()]


def info_text(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in rows)


class DebugLogWindow(Adw.Window):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="Debug log", default_width=980, default_height=640)
        self.app = app
        self.lines: list[str] = list(MEMORY_HANDLER.records)
        self.filter_text = ""

        header = Adw.HeaderBar()
        self.view_switcher_stack = Adw.ViewStack()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.view_switcher_stack,
                                                 policy=Adw.ViewSwitcherPolicy.WIDE))

        # -- log page --
        self.filter_entry = Gtk.SearchEntry(placeholder_text="Filter", hexpand=True)
        self.filter_entry.connect("search-changed", lambda e: self.set_filter(e.get_text()))
        self.autoscroll = Gtk.CheckButton(label="Auto scroll", active=True)
        bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        bar.append(self.filter_entry)
        bar.append(self.autoscroll)
        for text, cb in (("Copy all", self.copy_all), ("Open log folder", self.open_log_folder),
                         ("Clear view", self.clear_view)):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda *_a, cb=cb: cb())
            bar.append(b)
        self.textview = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6,
                                     bottom_margin=6)
        self.buffer = self.textview.get_buffer()
        self.scroller = Gtk.ScrolledWindow(child=self.textview, vexpand=True)
        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        log_box.append(bar)
        log_box.append(self.scroller)
        self.view_switcher_stack.add_titled_with_icon(log_box, "log", "Log", "utilities-terminal-symbolic")

        # -- system info page --
        self.info_group = Adw.PreferencesGroup(title="System info")
        copy_info = Gtk.Button(label="Copy system info", valign=Gtk.Align.CENTER)
        copy_info.connect("clicked", lambda *_: self.copy_info())
        self.info_group.set_header_suffix(copy_info)
        page = Adw.PreferencesPage()
        page.add(self.info_group)
        self.view_switcher_stack.add_titled_with_icon(page, "info", "System info", "computer-symbolic")
        self.info_rows: list[tuple[str, str]] = []
        self._fill_info(basic_info(app))
        threading.Thread(target=self._probe_backends, name="debug-probe", daemon=True).start()

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.view_switcher_stack)
        self.set_content(tv)
        self._render()
        MEMORY_HANDLER.listeners.append(self._on_record)
        self.connect("close-request", self._on_close)

    # -- info --

    def _fill_info(self, rows: list[tuple[str, str]]) -> None:
        for k, v in rows:
            r = Adw.ActionRow(title=k, subtitle=v, subtitle_selectable=True)
            r.add_css_class("property")
            self.info_group.add(r)
        self.info_rows += rows

    def _probe_backends(self) -> None:
        try:
            rows = capture_backends()
        except Exception as e:
            rows = [("Capture backends", f"probe failed: {e}")]
        gtkutil.idle(self._fill_info, rows)

    def copy_info(self) -> None:
        from ...core import clipboard
        clipboard.set_text(info_text(self.info_rows))

    # -- log --

    def _on_record(self, line: str) -> None:
        # Logging can happen on any thread; the view is updated on the main loop.
        GLib.idle_add(self._append, line)

    def _append(self, line: str) -> bool:
        self.lines.append(line)
        if len(self.lines) > MEMORY_HANDLER.records.maxlen:
            del self.lines[0]
        if self._matches(line):
            end = self.buffer.get_end_iter()
            self.buffer.insert(end, ("\n" if self.buffer.get_char_count() else "") + line)
            self._scroll()
        return False

    def _matches(self, line: str) -> bool:
        return not self.filter_text or self.filter_text in line.lower()

    def visible_text(self) -> str:
        return "\n".join(line for line in self.lines if self._matches(line))

    def _render(self) -> None:
        self.buffer.set_text(self.visible_text())
        self._scroll()

    def _scroll(self) -> None:
        if self.autoscroll.get_active():
            mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), False)
            self.textview.scroll_mark_onscreen(mark)
            self.buffer.delete_mark(mark)

    def set_filter(self, text: str) -> None:
        self.filter_text = text.strip().lower()
        self._render()

    def copy_all(self) -> None:
        from ...core import clipboard
        clipboard.set_text(self.visible_text())

    def open_log_folder(self) -> None:
        from ...core import desktop
        d = paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        desktop.open_folder(str(d))

    def clear_view(self) -> None:
        self.lines.clear()
        self._render()

    def _on_close(self, *_a) -> bool:
        if self._on_record in MEMORY_HANDLER.listeners:
            MEMORY_HANDLER.listeners.remove(self._on_record)
        return False
