"""Clipboard content viewer (ShareX ClipboardUploadForm "Clipboard content viewer" + ClipboardViewerForm).

``open_clipboard_viewer(app, ctx, upload=True)`` is ShareX's "Clipboard upload with content viewer": the
heading says what is on the clipboard ("Clipboard content: Image (Size: 640x480)", "Text (Length: 12)",
"File (Count: 2)" or "Clipboard is empty or contains unknown data."), the content is previewed (image /
text / file list), and Upload sends exactly that content through ``app.clipboard_upload``; Cancel closes.
"Don't show this window again" switches ``show_clipboard_content_viewer`` off (the job then uploads
directly, like ShareX).

``open(app, ctx)`` (the ClipboardViewer tool) shows the same preview plus ShareX ClipboardViewerForm's
list of clipboard formats (MIME types); selecting a format shows that format's raw data as text or as an
image. Refresh re-reads the clipboard. Reading runs on a worker thread (wl-paste may take a moment).
"""
from __future__ import annotations

import io

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango
from PIL import Image

from ...core import clipboard
from ..gtkutil import human_size, texture_from_pil
from ._base import ToolWindow, button, set_text, single, text_view


def describe(c: clipboard.ClipboardContent) -> str:
    """ShareX's lblQuestion text for the content."""
    if c.kind == "image" and c.image is not None:
        return f"Clipboard content: Image (Size: {c.image.width}x{c.image.height})"
    if c.kind == "text" and c.text:
        return f"Clipboard content: Text (Length: {len(c.text)})"
    if c.kind == "files" and c.files:
        return f"Clipboard content: File (Count: {len(c.files)})"
    return "Clipboard is empty or contains unknown data."


def is_valid(c: clipboard.ClipboardContent | None) -> bool:
    return c is not None and ((c.kind == "image" and c.image is not None) or (c.kind == "text" and bool(c.text))
                              or (c.kind == "files" and bool(c.files)))


class ClipboardViewerWindow(ToolWindow):
    def __init__(self, app, ctx, upload: bool) -> None:
        super().__init__(app, "Clipboard content viewer" if upload else "Clipboard viewer",
                         620 if upload else 820, 480 if upload else 560)
        self.ctx = ctx
        self.upload_mode = upload
        self.content: clipboard.ClipboardContent | None = None
        self.formats: list[str] = []
        self.refresh_worker = None

        self.heading = Gtk.Label(label="Reading the clipboard…", xalign=0, wrap=True)
        self.heading.add_css_class("heading")

        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, vexpand=True,
                                   hexpand=True)
        self.text, self.text_sw = text_view(editable=False, monospace=True, height=200)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], ["Clipboard text"])
        self.text.set_tooltip_text("Clipboard text")
        self.file_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.file_list.add_css_class("boxed-list")
        files_sw = Gtk.ScrolledWindow(child=self.file_list, vexpand=True)
        self.preview = Gtk.Stack(vexpand=True, hexpand=True)
        self.preview.add_named(self.picture, "image")
        self.preview.add_named(self.text_sw, "text")
        self.preview.add_named(files_sw, "files")
        self.preview.add_named(Gtk.Label(label=""), "empty")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(self.heading)

        self.refresh_button = button("Refresh", self.refresh, "Read the clipboard again", icon="view-refresh-symbolic")
        self.header.pack_start(self.refresh_button)

        if upload:
            body.append(self.preview)
        else:
            # ShareX ClipboardViewerForm: formats on the left, the selected format's data on the right
            self.format_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.format_list.connect("row-selected", self._format_selected)
            fl_sw = Gtk.ScrolledWindow(child=self.format_list, min_content_width=220, vexpand=True)
            self.raw_text, raw_sw = text_view(editable=False, monospace=True)
            self.raw_text.update_property([Gtk.AccessibleProperty.LABEL], ["Format data"])
            self.raw_picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
            self.raw_info = Gtk.Label(xalign=0)
            self.raw_info.add_css_class("dim-label")
            self.raw_stack = Gtk.Stack(vexpand=True, hexpand=True)
            self.raw_stack.add_named(raw_sw, "text")
            self.raw_stack.add_named(self.raw_picture, "image")
            raw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            raw_box.append(self.raw_info)
            raw_box.append(self.raw_stack)
            fpane = Gtk.Paned(start_child=fl_sw, end_child=raw_box, position=240, vexpand=True)
            tabs = Gtk.Notebook(vexpand=True)
            tabs.append_page(self.preview, Gtk.Label(label="Content"))
            tabs.append_page(fpane, Gtk.Label(label="Formats"))
            body.append(tabs)

        actions = Gtk.Box(spacing=8)
        if upload:
            self.dont_show = Gtk.CheckButton(label="Don't show this window again",
                                             active=not app.store.app.show_clipboard_content_viewer)
            self.dont_show.connect("toggled", self._dont_show_toggled)
            actions.append(self.dont_show)
            actions.append(Gtk.Box(hexpand=True))
            self.cancel_button = button("Cancel", self.close, "Close without uploading")
            self.upload_button = button("Upload", self.upload, "Upload the clipboard content",
                                        css="suggested-action")
            self.upload_button.set_sensitive(False)
            actions.append(self.cancel_button)
            actions.append(self.upload_button)
        else:
            actions.append(Gtk.Box(hexpand=True))
            actions.append(button("Upload", self.upload, "Upload the clipboard content (Clipboard upload)"))
            actions.append(button("Close", self.close))
        body.append(actions)
        self.set_body(body)
        self.refresh()

    # -- reading --

    def refresh(self) -> None:
        if self.refresh_worker is not None and self.refresh_worker.running:
            return
        self.heading.set_label("Reading the clipboard…")
        want_formats = not self.upload_mode

        def work(_progress, _cancel):
            content = clipboard.read()
            formats = clipboard.mimetypes() if want_formats else []
            return content, formats

        self.refresh_worker = self.run(work, lambda res: self.show_content(*res),
                                       on_error=lambda e: self.heading.set_label(f"Could not read the clipboard: {e}"))

    def show_content(self, content: clipboard.ClipboardContent, formats: list[str] | None = None) -> None:
        self.content = content
        self.heading.set_label(describe(content))
        if content.kind == "image" and content.image is not None:
            self.picture.set_paintable(texture_from_pil(content.image))
            self.preview.set_visible_child_name("image")
        elif content.kind == "text" and content.text:
            set_text(self.text, content.text)
            self.preview.set_visible_child_name("text")
        elif content.kind == "files" and content.files:
            child = self.file_list.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.file_list.remove(child)
                child = nxt
            for f in content.files:
                self.file_list.append(Gtk.Label(label=f, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                                                margin_top=4, margin_bottom=4, margin_start=8, margin_end=8))
            self.preview.set_visible_child_name("files")
        else:
            self.preview.set_visible_child_name("empty")
        if self.upload_mode:
            self.upload_button.set_sensitive(is_valid(content))
        else:
            self._set_formats(formats or [])

    def _set_formats(self, formats: list[str]) -> None:
        self.formats = list(formats)
        self.format_list.remove_all()
        for f in self.formats:
            self.format_list.append(Gtk.Label(label=f, xalign=0, margin_top=4, margin_bottom=4, margin_start=8))
        if self.formats:
            self.format_list.select_row(self.format_list.get_row_at_index(0))
        else:
            self.raw_info.set_label("No formats")
            set_text(self.raw_text, "")
            self.raw_stack.set_visible_child_name("text")

    def _format_selected(self, _lb, row) -> None:
        if row is None:
            return
        mime = self.formats[row.get_index()]
        self.raw_info.set_label(f"{mime}: reading…")

        def work(_p, _c):
            return clipboard.get_data(mime)
        self.run(work, lambda data: self.show_format(mime, data))

    def show_format(self, mime: str, data: bytes | None) -> None:
        if not data:
            self.raw_info.set_label(f"{mime}: no data")
            set_text(self.raw_text, "")
            self.raw_stack.set_visible_child_name("text")
            return
        self.raw_info.set_label(f"{mime}: {human_size(len(data))}")
        if mime.startswith("image/"):
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    self.raw_picture.set_paintable(texture_from_pil(im.convert("RGBA")))
                self.raw_stack.set_visible_child_name("image")
                return
            except OSError:
                pass
        text = data.decode("utf-8", errors="replace")
        if "\x00" in text:
            text = data[:4096].hex(" ", 1)
        set_text(self.raw_text, text)
        self.raw_stack.set_visible_child_name("text")

    # -- actions --

    def _dont_show_toggled(self, cb: Gtk.CheckButton) -> None:
        self.app.store.app.show_clipboard_content_viewer = not cb.get_active()
        self.app.store.save_app()
        self.app.settings_changed()

    def upload(self) -> None:
        if not is_valid(self.content):
            self.error("Nothing to upload", "The clipboard is empty or contains unknown data.")
            return
        self.app.clipboard_upload(self.ctx.task_settings, self.content)
        self.close()


def open_clipboard_viewer(app, ctx, upload: bool) -> ClipboardViewerWindow:
    key = "clipboardviewer-upload" if upload else "clipboardviewer"
    win = single(key, lambda: ClipboardViewerWindow(app, ctx, upload))
    win.ctx = ctx
    return win


def open(app, ctx) -> ClipboardViewerWindow:  # noqa: A001 - the tool contract's name
    win = open_clipboard_viewer(app, ctx, upload=False)
    win.refresh()
    return win
