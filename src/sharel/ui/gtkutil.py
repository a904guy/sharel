"""GTK helpers: main-thread marshalling, PIL <-> Gdk.Texture, file dialogs, simple prompts."""
from __future__ import annotations

import io
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GLib, Gtk
from PIL import Image

# ---- threads --------------------------------------------------------------------------------

_main_thread = threading.main_thread()


def on_main() -> bool:
    return threading.current_thread() is _main_thread


def idle(fn: Callable, *args) -> None:
    """Run fn(*args) on the GTK main loop, soon (fire and forget)."""
    def run():
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


def call_main(fn: Callable[..., Any], *args, timeout: float | None = None) -> Any:
    """Run fn on the main loop and wait for its return value (direct call when already there)."""
    if on_main():
        return fn(*args)
    done = threading.Event()
    box: dict = {}

    def run():
        try:
            box["v"] = fn(*args)
        except BaseException as e:
            box["e"] = e
        done.set()
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)
    done.wait(timeout)
    if "e" in box:
        raise box["e"]
    return box.get("v")


def call_main_async(start: Callable[[Callable[[Any], None]], None], timeout: float | None = None) -> Any:
    """For interactive UI from a worker thread: ``start(done)`` runs on the main loop and opens
    whatever it needs; the worker blocks until ``done(result)`` is called."""
    finished = threading.Event()
    box: dict = {}

    def done(value=None):
        box["v"] = value
        finished.set()

    if on_main():
        # Called on the main loop: spin a nested loop so the caller still gets a value.
        start(done)
        ctx = GLib.MainContext.default()
        while not finished.is_set():
            ctx.iteration(True)
        return box.get("v")
    idle(start, done)
    finished.wait(timeout)
    return box.get("v")


# ---- images ---------------------------------------------------------------------------------

def texture_from_pil(img: Image.Image) -> Gdk.Texture:
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    data = GLib.Bytes.new(rgba.tobytes())
    return Gdk.MemoryTexture.new(rgba.width, rgba.height, Gdk.MemoryFormat.R8G8B8A8, data, rgba.width * 4)


def pil_from_texture(tex: Gdk.Texture) -> Image.Image:
    png = tex.save_to_png_bytes().get_data()
    with Image.open(io.BytesIO(png)) as im:
        im.load()
        return im.convert("RGBA")


def picture(img: Image.Image | None, max_size: int = 0) -> Gtk.Picture:
    pic = Gtk.Picture()
    pic.set_can_shrink(True)
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    if img is not None:
        if max_size:
            img = img.copy()
            img.thumbnail((max_size, max_size))
        pic.set_paintable(texture_from_pil(img))
    return pic


def cairo_surface_from_pil(img: Image.Image):
    import cairo
    import numpy as np
    rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    h, w = rgba.shape[:2]
    a = rgba[..., 3:4].astype(np.uint16)
    pre = (rgba[..., :3].astype(np.uint16) * a // 255).astype(np.uint8)
    bgra = np.empty((h, w, 4), np.uint8)
    bgra[..., 0] = pre[..., 2]
    bgra[..., 1] = pre[..., 1]
    bgra[..., 2] = pre[..., 0]
    bgra[..., 3] = rgba[..., 3]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)  # cairo owns the pixels
    stride = surf.get_stride()
    dst = np.ndarray((h, stride), np.uint8, buffer=surf.get_data())
    dst[:, : w * 4] = bgra.reshape(h, w * 4)
    surf.mark_dirty()
    return surf


# ---- dialogs --------------------------------------------------------------------------------

IMAGE_PATTERNS = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.tif", "*.tiff", "*.webp", "*.ico"]
VIDEO_PATTERNS = ["*.mp4", "*.mkv", "*.webm", "*.mov", "*.avi", "*.gif", "*.m4v"]


def _filters(patterns: list[str] | None, name: str):
    if not patterns:
        return None
    from gi.repository import Gio
    store = Gio.ListStore.new(Gtk.FileFilter)
    f = Gtk.FileFilter()
    f.set_name(name)
    for p in patterns:
        f.add_pattern(p)
    store.append(f)
    allf = Gtk.FileFilter()
    allf.set_name("All files")
    allf.add_pattern("*")
    store.append(allf)
    return store


def open_files(parent: Gtk.Window | None, title: str, on_done: Callable[[list[str]], None], *,
               patterns: list[str] | None = None, filter_name: str = "Images", multiple: bool = True,
               folder: str = "") -> None:
    from gi.repository import Gio
    dlg = Gtk.FileDialog(title=title, modal=True)
    flt = _filters(patterns, filter_name)
    if flt is not None:
        dlg.set_filters(flt)
    if folder and Path(folder).is_dir():
        dlg.set_initial_folder(Gio.File.new_for_path(folder))

    def finish(d, res):
        try:
            if multiple:
                model = d.open_multiple_finish(res)
                paths = [model.get_item(i).get_path() for i in range(model.get_n_items())]
            else:
                f = d.open_finish(res)
                paths = [f.get_path()] if f else []
        except GLib.Error:
            paths = []
        on_done([p for p in paths if p])

    if multiple:
        dlg.open_multiple(parent, None, finish)
    else:
        dlg.open(parent, None, finish)


def select_folder(parent: Gtk.Window | None, title: str, on_done: Callable[[str], None]) -> None:
    dlg = Gtk.FileDialog(title=title, modal=True)

    def finish(d, res):
        try:
            f = d.select_folder_finish(res)
            on_done(f.get_path() if f else "")
        except GLib.Error:
            on_done("")
    dlg.select_folder(parent, None, finish)


def save_file(parent: Gtk.Window | None, title: str, suggested: str, on_done: Callable[[str], None],
              patterns: list[str] | None = None) -> None:
    from gi.repository import Gio
    dlg = Gtk.FileDialog(title=title, modal=True)
    p = Path(suggested)
    if p.parent.is_dir():
        dlg.set_initial_folder(Gio.File.new_for_path(str(p.parent)))
    dlg.set_initial_name(p.name)
    flt = _filters(patterns, "Files")
    if flt is not None:
        dlg.set_filters(flt)

    def finish(d, res):
        try:
            f = d.save_finish(res)
            on_done(f.get_path() if f else "")
        except GLib.Error:
            on_done("")
    dlg.save(parent, None, finish)


def alert(parent: Gtk.Widget | None, heading: str, body: str = "", on_done: Callable[[], None] | None = None) -> None:
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("ok", "OK")

    def resp(_d, _r):
        if on_done:
            on_done()
    d.connect("response", resp)
    d.present(parent)


def ask(parent: Gtk.Widget | None, heading: str, body: str, responses: list[tuple[str, str]],
        on_done: Callable[[str], None], default: str = "", destructive: str = "") -> None:
    d = Adw.AlertDialog(heading=heading, body=body)
    for rid, label in responses:
        d.add_response(rid, label)
    if default:
        d.set_default_response(default)
    d.set_close_response(responses[0][0])
    if destructive:
        d.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    d.connect("response", lambda _d, r: on_done(r))
    d.present(parent)


def prompt_text(parent: Gtk.Widget | None, heading: str, default: str, on_done: Callable[[str | None], None],
                body: str = "", multiline: bool = False) -> None:
    d = Adw.AlertDialog(heading=heading, body=body)
    if multiline:
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
        view.get_buffer().set_text(default)
        sw = Gtk.ScrolledWindow(min_content_height=200, min_content_width=420, child=view)
        d.set_extra_child(sw)

        def value():
            b = view.get_buffer()
            return b.get_text(b.get_start_iter(), b.get_end_iter(), False)
    else:
        entry = Gtk.Entry(text=default, activates_default=True)
        d.set_extra_child(entry)

        def value():
            return entry.get_text()
    d.add_response("cancel", "Cancel")
    d.add_response("ok", "OK")
    d.set_default_response("ok")
    d.set_close_response("cancel")
    d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    d.connect("response", lambda _d, r: on_done(value() if r == "ok" else None))
    d.present(parent)


def prompt_choice(parent: Gtk.Widget | None, heading: str, options: list[str],
                  on_done: Callable[[str | None], None]) -> None:
    d = Adw.AlertDialog(heading=heading)
    dd = Gtk.DropDown.new_from_strings(options)
    d.set_extra_child(dd)
    d.add_response("cancel", "Cancel")
    d.add_response("ok", "OK")
    d.set_default_response("ok")
    d.set_close_response("cancel")
    d.connect("response", lambda _d, r: on_done(options[dd.get_selected()] if r == "ok" and options else None))
    d.present(parent)


def copy_button(text_fn: Callable[[], str], label: str = "Copy") -> Gtk.Button:
    from ..core import clipboard
    b = Gtk.Button(label=label)
    b.connect("clicked", lambda *_: clipboard.set_text(text_fn()))
    return b


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
