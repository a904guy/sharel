"""Pin to screen (ShareX's PinToScreenForm): an undecorated always-on-top window showing an image.

Mouse wheel zooms, Ctrl+wheel changes opacity, dragging moves it (a compositor move, so it works on
Wayland), double-click or Esc closes it, right click opens the menu (copy, save, zoom, opacity,
border, shadow, close, close all). It is kept above other windows and placed through the ShareL
extension; without the extension it is a normal window.
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import Image

from ...core import clipboard, imaging
from ..gtkutil import IMAGE_PATTERNS, cairo_surface_from_pil, save_file

log = logging.getLogger("sharel.pin")

PINS: list[PinWindow] = []
SHADOW = 10
CSS = b"window.sharel-pin, window.sharel-pin > * { background: transparent; box-shadow: none; }"
_css_done = False


def _install_css() -> None:
    global _css_done
    if _css_done:
        return
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_done = True


class PinWindow(Gtk.Window):
    def __init__(self, app, img: Image.Image, position: tuple[int, int] | None = None) -> None:
        from ..placement import unique_title
        super().__init__(title=unique_title("ShareL pin"), decorated=False, resizable=False)
        _install_css()
        self.add_css_class("sharel-pin")
        self.set_application(app)
        self.app = app
        self.image = img.convert("RGBA")
        self.zoom = 1.0
        self.opacity = 1.0
        self.border = True
        self.shadow = True
        self.border_color = (0.92, 0.27, 0.27)
        self._surface = cairo_surface_from_pil(self.image)
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self._resize()

        drag = Gtk.GestureClick(button=0)
        drag.connect("pressed", self._pressed)
        self.area.add_controller(drag)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scrolled)
        self.area.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._install_actions()
        PINS.append(self)
        self.connect("close-request", self._closed)
        self.present()
        from ..placement import keep_above
        pos = (position[0] - self._pad(), position[1] - self._pad()) if position else None
        keep_above(self, pos, activate=True)

    def _pad(self) -> int:
        return SHADOW if self.shadow else (1 if self.border else 0)

    def _resize(self) -> None:
        pad = self._pad()
        w = max(16, round(self.image.width * self.zoom)) + 2 * pad
        h = max(16, round(self.image.height * self.zoom)) + 2 * pad
        self.area.set_content_width(w)
        self.area.set_content_height(h)
        self.set_default_size(w, h)
        self.area.queue_draw()

    def _draw(self, _a, cr, w, h) -> None:
        import cairo
        pad = self._pad()
        iw, ih = w - 2 * pad, h - 2 * pad
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.shadow:
            for i in range(SHADOW, 0, -2):
                cr.set_source_rgba(0, 0, 0, 0.05 * self.opacity)
                cr.rectangle(pad - i + 3, pad - i + 3, iw + 2 * i - 3, ih + 2 * i - 3)
                cr.fill()
        cr.save()
        cr.translate(pad, pad)
        cr.scale(iw / self.image.width, ih / self.image.height)
        cr.set_source_surface(self._surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD if self.zoom < 1 else cairo.FILTER_NEAREST)
        cr.paint_with_alpha(self.opacity)
        cr.restore()
        if self.border:
            cr.set_source_rgba(*self.border_color, self.opacity)
            cr.set_line_width(1)
            cr.rectangle(pad - 0.5, pad - 0.5, iw + 1, ih + 1)
            cr.stroke()

    # -- input --

    def _pressed(self, gesture, n_press, x, y) -> None:
        button = gesture.get_current_button()
        if button == 3:
            self._menu(x, y)
            return
        if button == 2 or (button == 1 and n_press == 2):
            self.close()
            return
        if button == 1:
            surf = self.get_surface()
            device = gesture.get_current_event_device()
            if surf is not None and device is not None and hasattr(surf, "begin_move"):
                surf.begin_move(device, button, x, y, gesture.get_current_event_time())

    def _scrolled(self, ctl, _dx, dy) -> bool:
        state = ctl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.set_pin_opacity(self.opacity + (-0.1 if dy > 0 else 0.1))
        else:
            self.set_zoom(self.zoom * (1 / 1.1 if dy > 0 else 1.1))
        return True

    def _key(self, _c, keyval, _code, state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        if name == "Escape":
            self.close()
        elif state & Gdk.ModifierType.CONTROL_MASK and name.lower() == "c":
            clipboard.set_image(self.image)
        elif name in ("plus", "equal", "KP_Add"):
            self.set_zoom(self.zoom * 1.1)
        elif name in ("minus", "KP_Subtract"):
            self.set_zoom(self.zoom / 1.1)
        elif name == "0":
            self.set_zoom(1.0)
        else:
            return False
        return True

    # -- operations --

    def set_zoom(self, z: float) -> None:
        self.zoom = max(0.1, min(10.0, z))
        self._resize()

    def set_pin_opacity(self, o: float) -> None:
        self.opacity = max(0.1, min(1.0, round(o, 2)))
        self.area.queue_draw()

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()

        def add(name, fn):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: fn())
            group.add_action(a)
        add("copy", lambda: clipboard.set_image(self.image))
        add("save", self._save)
        add("upload", lambda: self.app.tasks.run_image(self.image, self._upload_ts()))
        add("edit", self._edit)
        add("zoom-reset", lambda: self.set_zoom(1.0))
        add("zoom-in", lambda: self.set_zoom(self.zoom * 1.25))
        add("zoom-out", lambda: self.set_zoom(self.zoom / 1.25))
        add("opacity-100", lambda: self.set_pin_opacity(1.0))
        add("opacity-75", lambda: self.set_pin_opacity(0.75))
        add("opacity-50", lambda: self.set_pin_opacity(0.5))
        add("toggle-border", lambda: (setattr(self, "border", not self.border), self._resize()))
        add("toggle-shadow", lambda: (setattr(self, "shadow", not self.shadow), self._resize()))
        add("close", self.close)
        add("close-all", close_all_pins)
        self.insert_action_group("pin", group)

    def _upload_ts(self):
        import copy

        from ...core.enums import AfterCaptureTasks
        ts = copy.deepcopy(self.app.store.task_settings_for(None))
        ts.after_capture_job = AfterCaptureTasks.UploadImageToHost
        return ts

    def _save(self) -> None:
        folder = self.app.store.screenshots_folder(self.app.store.app.default_task_settings)

        def chosen(path):
            if path:
                self.image.save(path)
        save_file(self, "Save pinned image", str(folder / "pinned.png"), chosen, IMAGE_PATTERNS)

    def _edit(self) -> None:
        from .editor import open_editor
        open_editor(self.app, self.image, self.app.store.task_settings_for(None), None, task_mode=False,
                    title="Pinned image")

    def _menu(self, x: float, y: float) -> None:
        m = Gio.Menu()
        s1 = Gio.Menu()
        for label, act in (("Copy", "copy"), ("Save as…", "save"), ("Upload", "upload"), ("Edit…", "edit")):
            s1.append(label, f"pin.{act}")
        s2 = Gio.Menu()
        for label, act in (("Zoom in", "zoom-in"), ("Zoom out", "zoom-out"), ("Reset zoom", "zoom-reset"),
                           ("Opacity 100%", "opacity-100"), ("Opacity 75%", "opacity-75"),
                           ("Opacity 50%", "opacity-50"), ("Toggle border", "toggle-border"),
                           ("Toggle shadow", "toggle-shadow")):
            s2.append(label, f"pin.{act}")
        s3 = Gio.Menu()
        s3.append("Close", "pin.close")
        s3.append("Close all pins", "pin.close-all")
        for s in (s1, s2, s3):
            m.append_section(None, s)
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(self.area)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        pop.popup()

    def _closed(self, *_a) -> bool:
        if self in PINS:
            PINS.remove(self)
        return False


def pin_image(app, img: Image.Image, position: tuple[int, int] | None = None) -> PinWindow:
    return PinWindow(app, img, position)


def text_image(text: str, font_size: int = 16) -> Image.Image:
    """Render text for pinning (ShareX pins clipboard text as an image)."""
    import cairo
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    layout = PangoCairo.create_layout(cairo.Context(probe))
    layout.set_font_description(Pango.FontDescription.from_string(f"Sans {font_size}"))
    layout.set_text(text, -1)
    layout.set_width(900 * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    _ink, logical = layout.get_pixel_extents()
    w, h = logical.width + 24, logical.height + 24
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0.1, 0.1, 0.1)
    cr.move_to(12, 12)
    PangoCairo.show_layout(cr, layout)
    surf.flush()
    import numpy as np
    buf = np.frombuffer(surf.get_data(), np.uint8).reshape(h, surf.get_stride())[:, : w * 4].reshape(h, w, 4)
    rgba = buf[..., [2, 1, 0, 3]].copy()
    return Image.fromarray(rgba, "RGBA")


def pin_text(app, text: str) -> PinWindow:
    return pin_image(app, text_image(text))


def close_all_pins() -> int:
    n = len(PINS)
    for p in list(PINS):
        p.close()
    PINS.clear()
    return n


def open_pin_startup(app, ctx) -> None:
    """ShareX's "Pin to screen" choice: from screen, clipboard or file."""
    from gi.repository import Adw
    d = Adw.AlertDialog(heading="Pin to screen", body="Pin an image from:")
    for rid, label in (("cancel", "Cancel"), ("screen", "Screen region"), ("clipboard", "Clipboard"),
                       ("file", "File…")):
        d.add_response(rid, label)
    d.set_default_response("screen")
    d.set_close_response("cancel")

    def chosen(_d, rid):
        if rid == "screen":
            GLib.timeout_add(250, lambda: (app.job_PinToScreenFromScreen(ctx), False)[1])
        elif rid == "clipboard":
            app.job_PinToScreenFromClipboard(ctx)
        elif rid == "file":
            app.job_PinToScreenFromFile(ctx)
    d.connect("response", chosen)
    d.present(app.active_parent())
    return None


def load_and_pin(app, path: str) -> PinWindow:
    return pin_image(app, imaging.load(path))
