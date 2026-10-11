"""DragDropUpload (ShareX's DropForm): a small always-on-top target; files, images or text dropped
on it are uploaded."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GObject, Gtk

_open: dict = {}


def open_drop_window(app, ctx) -> Gtk.Window:
    if _open.get("win") is not None:
        _open["win"].present()
        return _open["win"]
    from ..placement import keep_above, unique_title
    win = Adw.Window(application=app, title=unique_title("ShareL drop"), default_width=220, default_height=220)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    icon = Gtk.Image(icon_name="folder-drag-accept-symbolic", pixel_size=72)
    box.append(icon)
    box.append(Gtk.Label(label="Drop files here\nto upload", justify=Gtk.Justification.CENTER))
    count = Gtk.Label(css_classes=["dim-label"])
    box.append(count)
    close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close", halign=Gtk.Align.CENTER)
    close.add_css_class("flat")
    close.connect("clicked", lambda *_: win.destroy())
    box.append(close)
    frame = Gtk.Frame(child=box, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
    win.set_content(frame)
    state = {"n": 0}

    def on_drop(_t, value, _x, _y) -> bool:
        from ..main_window import handle_drop
        ok = handle_drop(app, value)
        if ok:
            state["n"] += 1
            count.set_label(f"{state['n']} drop(s) uploaded")
        return ok
    target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
    target.set_gtypes([Gdk.FileList, Gdk.Texture, str])
    target.connect("drop", on_drop)
    win.add_controller(target)
    win._on_drop = on_drop  # type: ignore[attr-defined]
    win.connect("destroy", lambda *_: _open.pop("win", None))
    _open["win"] = win
    win.present()
    keep_above(win)
    return win
