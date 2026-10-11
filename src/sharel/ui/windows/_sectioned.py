"""Shared scaffolding for the settings windows: a sidebar of sections (like ShareX's tab list) and
one Adw.PreferencesPage per section, plus a per-key registry so a window opened twice re-presents."""
from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class SectionedWindow(Adw.Window):
    """An Adw.Window with a header bar, a Gtk.StackSidebar on the left and pages on the right."""

    def __init__(self, app, title: str, width: int = 960, height: int = 680) -> None:
        super().__init__(application=app, title=title, default_width=width, default_height=height)
        self.app = app
        self.pages: dict[str, Adw.PreferencesPage] = {}
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        sidebar = Gtk.StackSidebar(stack=self.stack, width_request=200)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(sidebar)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        box.append(self.stack)
        self.header = Adw.HeaderBar()
        self.toasts = Adw.ToastOverlay(child=box)
        view = Adw.ToolbarView()
        view.add_top_bar(self.header)
        view.set_content(self.toasts)
        self.set_content(view)

    def add_page(self, name: str, title: str, groups: list[Gtk.Widget], icon: str = "") -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=title, icon_name=icon or None)
        for g in groups:
            page.add(g)
        self.stack.add_titled(page, name, title)
        self.pages[name] = page
        return page

    def clear_pages(self) -> None:
        for page in list(self.pages.values()):
            self.stack.remove(page)
        self.pages.clear()

    def show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))


class WindowRegistry:
    """key -> open window; ``get_or_create`` presents the existing one instead of opening another."""

    def __init__(self) -> None:
        self._open: dict = {}

    def get(self, key):
        return self._open.get(key)

    def get_or_create(self, key, factory: Callable[[], Gtk.Window]) -> Gtk.Window:
        win = self._open.get(key)
        if win is not None:
            win.present()
            return win
        win = factory()
        self._open[key] = win
        win.connect("destroy", lambda *_: self._open.pop(key, None))
        win.connect("close-request", lambda *_: (self._open.pop(key, None), False)[1])
        win.present()
        return win


def group(title: str = "", description: str = "", rows: list[Gtk.Widget] | None = None) -> Adw.PreferencesGroup:
    g = Adw.PreferencesGroup(title=title, description=description)
    for r in rows or []:
        g.add(r)
    return g


def button_row(title: str, label: str, on_click: Callable[[], None], subtitle: str = "",
               style: str = "") -> Adw.ActionRow:
    """An ActionRow with one labelled button (the button carries the accessible label)."""
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if style:
        b.add_css_class(style)
    b.connect("clicked", lambda *_: on_click())
    row.add_suffix(b)
    row.button = b  # type: ignore[attr-defined]
    return row


def find_row(root: Gtk.Widget, title: str, cls: type | None = None) -> Gtk.Widget | None:
    """The first PreferencesRow under ``root`` with this title (and class); used by tests and callers."""
    def match(w):
        return isinstance(w, Adw.PreferencesRow) and w.get_title() == title and (cls is None or isinstance(w, cls))

    def walk(w):
        if match(w):
            return w
        c = w.get_first_child()
        while c is not None:
            hit = walk(c)
            if hit is not None:
                return hit
            c = c.get_next_sibling()
        return None
    return walk(root)
