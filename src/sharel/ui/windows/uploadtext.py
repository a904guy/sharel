"""UploadText (ShareX's TextUploadForm): type or paste text, then upload it to the text host."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...core import clipboard

_open: dict = {}


def open_upload_text(app, ctx) -> Adw.Window:
    if _open.get("win") is not None:
        _open["win"].present()
        return _open["win"]
    win = Adw.Window(application=app, title="ShareL: upload text", default_width=640, default_height=480)
    view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                        left_margin=8, right_margin=8)
    view.update_property([Gtk.AccessibleProperty.LABEL], ["Text to upload"])
    c = clipboard.read()
    if c.kind == "text":
        view.get_buffer().set_text(c.text)
    header = Adw.HeaderBar()
    upload = Gtk.Button(label="Upload")
    upload.add_css_class("suggested-action")
    cancel = Gtk.Button(label="Cancel")

    def text() -> str:
        b = view.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def do_upload(*_):
        t = text()
        if t.strip():
            app.tasks.upload_text(t, ctx.task_settings)
        win.destroy()
    upload.connect("clicked", do_upload)
    cancel.connect("clicked", lambda *_: win.destroy())
    header.pack_end(upload)
    header.pack_start(cancel)
    tv = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=view))
    tv.add_top_bar(header)
    win.set_content(tv)
    win.connect("destroy", lambda *_: _open.pop("win", None))
    _open["win"] = win
    win._view = view  # type: ignore[attr-defined]
    win._upload = upload  # type: ignore[attr-defined]
    win.present()
    return win
