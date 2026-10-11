"""Metadata viewer (ShareX ``MetadataWindow``): every metadata entry of a file, grouped and searchable.

Open or drop a file (images show EXIF / GPS / text chunks / XMP / ICC as well as the file entries). Search
filters by group, name or value. "Copy all" copies the visible entries as ``Group: Name: Value`` lines, a row's
copy button copies its value. "Strip metadata…" removes the image metadata in place after a confirmation
(ShareX) and reloads.
"""
from __future__ import annotations

import os
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from ...core import clipboard
from ...core.jobs import JobContext
from ...tools.metadata import Metadata, read_metadata, strip_metadata
from .. import gtkutil
from . import _base


class MetadataWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Metadata", 860, 680)
        self.path = ""
        self.meta: Metadata | None = None
        self.entries: list[tuple[str, str, str]] = []
        self.visible_entries: list[tuple[str, str, str]] = []
        open_btn = _base.button("Open…", self.browse, tooltip="Open a file")
        self.header.pack_start(open_btn)
        self.copy_all_button = _base.button("Copy all", self.copy_all)
        self.strip_button = _base.button("Strip metadata…", self.request_strip)
        self.strip_button.add_css_class("destructive-action")
        self.header.pack_end(self.strip_button)
        self.header.pack_end(self.copy_all_button)
        self.search = Gtk.SearchEntry(placeholder_text="Search metadata", hexpand=True)
        self.search.connect("search-changed", lambda *_: self._fill())
        self.summary = Gtk.Label(label="Open or drop a file to view its metadata", xalign=0)
        self.summary.add_css_class("dim-label")
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6, margin_bottom=12,
                                margin_start=12, margin_end=12)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_start=12,
                      margin_end=12)
        box.append(self.search)
        box.append(self.summary)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(box)
        body.append(_base.scrolled(self.list_box))
        self.set_body(body)
        self.add_controller(_base.image_drop_target(lambda ps: self.load(ps[0])))
        self._sync()

    def browse(self) -> None:
        gtkutil.open_files(self, "Open file", lambda ps: ps and self.load(ps[0]), patterns=None, multiple=False)

    def load(self, path: str) -> None:
        try:
            self.meta = read_metadata(path)
        except OSError as e:
            self.error("Can't read metadata", f"{path}: {e}")
            return
        self.path = os.path.abspath(path)
        self.entries = self.meta.rows()
        self.set_title(f"Metadata — {os.path.basename(path)}")
        self._fill()
        self._sync()

    def _sync(self) -> None:
        self.copy_all_button.set_sensitive(bool(self.visible_entries))
        self.strip_button.set_sensitive(self.meta is not None and self.meta.image is not None)

    def _fill(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        q = self.search.get_text().strip().lower()
        self.visible_entries = [e for e in self.entries if not q or any(q in part.lower() for part in e)]
        groups: dict[str, Adw.PreferencesGroup] = {}
        for section, key, value in self.visible_entries:
            g = groups.get(section)
            if g is None:
                g = groups[section] = Adw.PreferencesGroup(title=section)
                self.list_box.append(g)
            row = Adw.ActionRow(title=GLib.markup_escape_text(key), subtitle=GLib.markup_escape_text(value[:2000]),
                                subtitle_selectable=True)
            row.set_subtitle_lines(4)
            row.entry = (section, key, value)  # type: ignore[attr-defined]
            b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Copy {key}")
            b.add_css_class("flat")
            b.connect("clicked", _copier(value))
            row.add_suffix(b)
            g.add(row)
        total = len(self.entries)
        shown = len(self.visible_entries)
        if self.meta is not None:
            name = os.path.basename(self.path)
            self.summary.set_label(f"{name}: {total} entr{'y' if total == 1 else 'ies'}"
                                   + (f", {shown} shown" if shown != total else ""))
        if self.meta is not None and not self.visible_entries:
            lbl = Gtk.Label(label="No matching metadata", margin_top=24)
            lbl.add_css_class("dim-label")
            self.list_box.append(lbl)
        self._sync()

    def as_text(self) -> str:
        return "\n".join(f"{s}: {k}: {v}" for s, k, v in self.visible_entries)

    def copy_all(self) -> None:
        if self.visible_entries:
            clipboard.set_text(self.as_text())
            self.toast("Metadata copied")

    def request_strip(self) -> None:
        if self.meta is None or self.meta.image is None:
            return
        gtkutil.ask(self, "Strip metadata?", f"Remove the metadata from {os.path.basename(self.path)}? "
                                             "The file is overwritten; this can't be undone.",
                    [("cancel", "Cancel"), ("strip", "Strip metadata")],
                    lambda r: r == "strip" and self.strip(), default="cancel", destructive="strip")

    def strip(self) -> None:
        try:
            strip_metadata(self.path)
        except (OSError, ValueError) as e:
            self.error("Strip failed", str(e))
            return
        self.load(self.path)
        self.toast("Metadata removed")


def _copier(value: str) -> Callable:
    return lambda *_: clipboard.set_text(value)


def open(app, ctx: JobContext) -> MetadataWindow:  # noqa: A001
    win = MetadataWindow.present_single(app, ctx)
    if ctx.files:
        win.load(ctx.files[0])
    return win
