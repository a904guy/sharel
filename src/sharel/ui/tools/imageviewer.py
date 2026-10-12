"""Image viewer (ShareX ``ImageViewer``).

Opens ``ctx.files`` (or a chooser). One file: every image in its folder is browsable (ShareX); several files:
those files. Previous / Next (Left / Right keys, mouse wheel), zoom in / out / 100 % / fit (Ctrl+wheel,
+ / - / 0 keys), fullscreen (F11), copy, open in the image editor, pin to screen, show in folder. Escape
closes, like ShareX.
"""
from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Gdk, Gtk
from PIL import Image

from ...core import clipboard, desktop
from ...core.jobs import JobContext
from .. import gtkutil
from . import _base

ZOOM_STEPS = (0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0)


def folder_images(path: str) -> list[str]:
    folder = Path(path).parent
    try:
        names = sorted((p for p in folder.iterdir() if p.is_file() and _base.is_image_path(p.name)),
                       key=lambda p: p.name.lower())
    except OSError:
        return [path]
    out = [str(p) for p in names]
    return out if path in out else [path, *out]


def _icon_button(label: str, cb, icon: str) -> Gtk.Button:
    """Icon-only header button; ``label`` stays its tooltip and accessible name. (A dozen icon + text
    buttons made the window wider than a 1920 px screen.)"""
    b = _base.button("", cb, tooltip=label, icon=icon)
    b.update_property([Gtk.AccessibleProperty.LABEL], [label])
    b._label = label  # type: ignore[attr-defined]
    return b


class ImageViewerWindow(_base.ToolWindow):
    def __init__(self, app) -> None:
        super().__init__(app, "Image viewer", 1000, 720)
        self.images: list[str] = []
        self.index = -1
        self.image: Image.Image | None = None
        self.zoom: float | None = None  # None = fit to window
        self.support_wrap = False

        self.prev_button = _icon_button("Previous image", lambda: self.navigate(-1), icon="go-previous-symbolic")
        self.next_button = _icon_button("Next image", lambda: self.navigate(1), icon="go-next-symbolic")
        open_btn = _icon_button("Open image", self.open_dialog, icon="document-open-symbolic")
        for b in (open_btn, self.prev_button, self.next_button):
            self.header.pack_start(b)
        self.zoom_out_button = _icon_button("Zoom out", lambda: self.step_zoom(-1), icon="zoom-out-symbolic")
        self.zoom_in_button = _icon_button("Zoom in", lambda: self.step_zoom(1), icon="zoom-in-symbolic")
        self.actual_button = _icon_button("Actual size", lambda: self.set_zoom(1.0), icon="zoom-original-symbolic")
        self.fit_button = _icon_button("Fit to window", lambda: self.set_zoom(None), icon="zoom-fit-best-symbolic")
        menu = Gtk.Box(spacing=0)
        menu.add_css_class("linked")
        for b in (self.zoom_out_button, self.actual_button, self.fit_button, self.zoom_in_button):
            menu.append(b)
        self.header.pack_end(self._actions_box())
        self.header.pack_end(menu)
        self.status = Gtk.Label(label="")
        self.status.add_css_class("dim-label")
        self.header.set_title_widget(self.status)

        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.SCALE_DOWN, hexpand=True,
                                   vexpand=True)
        self.scroller = Gtk.ScrolledWindow(child=self.picture, hexpand=True, vexpand=True)
        self.empty = Gtk.Label(label="Open an image or drop one here")
        self.empty.add_css_class("dim-label")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.scroller, "image")
        self.set_body(self.stack)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_scroll)
        self.picture.add_controller(wheel)
        self.add_controller(_base.image_drop_target(self.open_paths))
        self._sync()

    def _actions_box(self) -> Gtk.Box:
        box = Gtk.Box(spacing=4)
        self.copy_button = _icon_button("Copy image", self.copy, icon="edit-copy-symbolic")
        self.edit_button = _icon_button("Open in image editor", self.edit, icon="document-edit-symbolic")
        self.pin_button = _icon_button("Pin to screen", self.pin, icon="view-pin-symbolic")
        self.folder_button = _icon_button("Show in folder", self.show_in_folder, icon="folder-symbolic")
        self.fullscreen_button = _icon_button("Fullscreen", self.toggle_fullscreen,
                                              icon="view-fullscreen-symbolic")
        for b in (self.copy_button, self.edit_button, self.pin_button, self.folder_button, self.fullscreen_button):
            box.append(b)
        return box

    # -- loading --

    @property
    def path(self) -> str:
        return self.images[self.index] if 0 <= self.index < len(self.images) else ""

    def open_paths(self, paths: list[str]) -> None:
        paths = [os.path.abspath(p) for p in paths if os.path.isfile(p) and _base.is_image_path(p)]
        if not paths:
            return
        self.images = folder_images(paths[0]) if len(paths) == 1 else paths
        self.index = self.images.index(paths[0])
        self.load_current()

    def open_dialog(self) -> None:
        gtkutil.open_files(self, "Open image", self.open_paths, patterns=gtkutil.IMAGE_PATTERNS)

    def load_current(self) -> None:
        path = self.path
        try:
            self.image = _base.load_image_file(path) if path else None
        except OSError as e:
            self.image = None
            self.error("Can't open image", f"{path}: {e}")
        self._show()
        self._sync()

    def navigate(self, delta: int) -> None:
        n = len(self.images)
        if n < 2:
            return
        j = self.index + delta
        if self.support_wrap:
            j %= n
        if not 0 <= j < n:
            return
        self.index = j
        self.load_current()

    # -- zoom --

    def set_zoom(self, zoom: float | None) -> None:
        self.zoom = None if zoom is None else max(ZOOM_STEPS[0], min(ZOOM_STEPS[-1], zoom))
        self._show()
        self._sync()

    def effective_zoom(self) -> float:
        if self.zoom is not None or self.image is None:
            return self.zoom or 1.0
        w = max(1, self.scroller.get_width())
        h = max(1, self.scroller.get_height())
        return min(1.0, w / self.image.width, h / self.image.height)

    def step_zoom(self, direction: int) -> None:
        cur = self.effective_zoom()
        if direction > 0:
            nxt = next((z for z in ZOOM_STEPS if z > cur + 1e-6), ZOOM_STEPS[-1])
        else:
            nxt = next((z for z in reversed(ZOOM_STEPS) if z < cur - 1e-6), ZOOM_STEPS[0])
        self.set_zoom(nxt)

    def _show(self) -> None:
        img = self.image
        if img is None:
            self.picture.set_paintable(None)
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("image")
        self.picture.set_paintable(gtkutil.texture_from_pil(img))
        if self.zoom is None:
            self.picture.set_can_shrink(True)
            self.picture.set_content_fit(Gtk.ContentFit.SCALE_DOWN)
            self.picture.set_size_request(-1, -1)
        else:
            self.picture.set_can_shrink(False)
            self.picture.set_content_fit(Gtk.ContentFit.FILL)
            self.picture.set_size_request(max(1, round(img.width * self.zoom)), max(1, round(img.height * self.zoom)))

    def _sync(self) -> None:
        has = self.image is not None
        n = len(self.images)
        can = n > 1
        self.prev_button.set_sensitive(can and (self.support_wrap or self.index > 0))
        self.next_button.set_sensitive(can and (self.support_wrap or self.index < n - 1))
        for b in (self.copy_button, self.edit_button, self.pin_button, self.folder_button, self.zoom_in_button,
                  self.zoom_out_button, self.actual_button, self.fit_button):
            b.set_sensitive(has)
        if has:
            zoom = "Fit" if self.zoom is None else f"{self.zoom * 100:.0f}%"
            text = f"{os.path.basename(self.path)} — {self.image.width} × {self.image.height} — {zoom}"
            if n > 1:
                text += f" — {self.index + 1} / {n}"
            self.status.set_label(text)
            self.set_title(os.path.basename(self.path))
        else:
            self.status.set_label("")

    # -- actions --

    def copy(self) -> None:
        if self.image is not None:
            clipboard.set_image(self.image)
            self.toast("Copied to the clipboard")

    def edit(self) -> None:
        if self.path:
            from ..windows.editor import open_editor_for_file
            open_editor_for_file(self.app, self.path)

    def pin(self) -> None:
        if self.image is not None:
            from ..windows.pin import pin_image
            pin_image(self.app, self.image)

    def show_in_folder(self) -> None:
        if self.path:
            desktop.show_in_folder(self.path)

    def toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_key(self, _c, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
            self.navigate(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down):
            self.navigate(1)
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.step_zoom(1)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.step_zoom(-1)
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self.set_zoom(None)
        elif keyval == Gdk.KEY_1:
            self.set_zoom(1.0)
        elif keyval == Gdk.KEY_F11:
            self.toggle_fullscreen()
        elif ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy()
        elif keyval == Gdk.KEY_Escape:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.close()
        else:
            return False
        return True

    def _on_scroll(self, ctrl, _dx, dy) -> bool:
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.step_zoom(-1 if dy > 0 else 1)
        elif self.zoom is None:
            self.navigate(1 if dy > 0 else -1)
        else:
            return False  # zoomed in: the wheel scrolls the image
        return True


def open(app, ctx: JobContext) -> ImageViewerWindow:  # noqa: A001
    win = ImageViewerWindow.present_single(app)
    files = [f for f in ctx.files if _base.is_image_path(f)]
    if files:
        win.open_paths(files)
    elif win.image is None:
        win.open_dialog()
    return win
