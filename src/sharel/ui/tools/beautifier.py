"""Image Beautifier (ShareX ``ImageBeautifierForm``): live preview of ``beautify`` with every option editable.

Image from ``ctx.files``, a file chooser, the clipboard or drag and drop. Copy / Save (overwrite the opened
file, ShareX) / Save as / Upload, "Reset options" and "Set as default" (stores the options in
``default_task_settings.image_settings.beautifier``, the settings the after-capture "Beautify image" task uses).
"""
from __future__ import annotations

import copy
import dataclasses
import os
import threading
from pathlib import Path

from gi.repository import Adw, Gtk
from PIL import Image

from ...core import clipboard
from ...core.jobs import JobContext
from ...core.serialize import from_jsonable, to_jsonable
from ...effects.beautifier import BeautifierOptions, beautify
from .. import gtkutil
from . import _base
from ._params import param_rows

# ShareX groups its options like this (Shadow is an expandable section).
GROUPS = [("Layout", ["margin", "padding", "smart_padding", "rounded_corner"]),
          ("Shadow", ["shadow_radius", "shadow_opacity", "shadow_distance", "shadow_angle", "shadow_color"]),
          ("Background", ["background_type", "background_gradient", "background_color",
                          "background_image_file_path"])]


class BeautifierWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Image beautifier", 1100, 720)
        self.ctx = ctx
        self.ts = ctx.task_settings
        self.options: BeautifierOptions = from_jsonable(BeautifierOptions, copy.deepcopy(
            self.ts.image_settings.beautifier or {}))
        self.source: Image.Image | None = None
        self.file_path = ""
        self.result: Image.Image | None = None
        self.last_error = ""
        self.preview = _base.LivePreview(self._snapshot, self._render, self._show, self._fail)

        open_btn = _base.button("Open image…", self.open_dialog, tooltip="Open an image file")
        clip_btn = _base.button("From clipboard", self.load_clipboard, tooltip="Use the clipboard image")
        self.header.pack_start(open_btn)
        self.header.pack_start(clip_btn)
        self.reset_button = _base.button("Reset options", self.reset_options)
        self.default_button = _base.button("Set as default", self.set_as_default,
                                           tooltip="Use these options for the after capture task "
                                                   "\"Beautify image\"")
        self.header.pack_end(self.default_button)
        self.header.pack_end(self.reset_button)

        self.options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                                   margin_bottom=12, margin_start=12, margin_end=6)
        self._build_options()
        self.preview_pane = _base.ImagePane("Open an image, paste one from the clipboard or drop a file here")
        self.error_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.error_label.add_css_class("error")
        actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.copy_button = _base.button("Copy", self.copy)
        self.save_button = _base.button("Save", self.save)
        self.save_as_button = _base.button("Save as…", self.save_as_dialog)
        self.upload_button = _base.button("Upload", self.upload, suggested=True)
        for b in (self.copy_button, self.save_button, self.save_as_button, self.upload_button):
            actions.append(b)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12,
                        margin_start=6, margin_end=12)
        right.append(self.preview_pane)
        right.append(self.error_label)
        right.append(actions)
        paned = Gtk.Paned(start_child=_base.scrolled(self.options_box, min_w=360), end_child=right,
                          resize_start_child=False, shrink_start_child=False, position=390)
        self.set_body(paned)
        self.add_controller(_base.image_drop_target(lambda ps: self.load_file(ps[0])))
        self._sync()

    # -- options --

    def _build_options(self) -> None:
        child = self.options_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.options_box.remove(child)
            child = nxt
        fields = {f.name: f for f in dataclasses.fields(self.options)}
        self.rows: dict[str, Gtk.Widget] = {}
        for title, names in GROUPS:
            g = Adw.PreferencesGroup(title=title)
            for row in param_rows(self.options, [fields[n] for n in names], self.options_changed):
                self.rows[row.field_name] = row
                g.add(row)
            self.options_box.append(g)

    def options_changed(self) -> None:
        self._sync()
        self.preview.schedule()

    def _sync(self) -> None:
        bt = self.options.background_type
        if "background_gradient" in self.rows:
            self.rows["background_gradient"].set_visible(bt == "Gradient")
            self.rows["background_color"].set_visible(bt == "Color")
            self.rows["background_image_file_path"].set_visible(bt == "Image")
        has = self.source is not None
        for b in (self.copy_button, self.save_button, self.save_as_button, self.upload_button):
            b.set_sensitive(has)

    def reset_options(self) -> None:
        self.options = BeautifierOptions()
        self._build_options()
        self.options_changed()

    def set_as_default(self) -> None:
        data = to_jsonable(self.options)
        self.app.store.app.default_task_settings.image_settings.beautifier = copy.deepcopy(data)
        if self.ts.image_settings is not self.app.store.app.default_task_settings.image_settings:
            self.ts.image_settings.beautifier = copy.deepcopy(data)
        _base.save_tool_settings(self.app)
        self.toast("Saved as the default beautifier options")

    # -- image --

    def set_image(self, img: Image.Image, path: str = "") -> None:
        self.source = img.convert("RGBA")
        self.file_path = path
        self.set_title(f"Image beautifier — {os.path.basename(path)}" if path else "Image beautifier")
        self._sync()
        self.preview.schedule()

    def load_file(self, path: str) -> None:
        try:
            self.set_image(_base.load_image_file(path), path)
        except OSError as e:
            self.error("Can't open image", f"{path}: {e}")

    def open_dialog(self) -> None:
        gtkutil.open_files(self, "Open image", lambda ps: ps and self.load_file(ps[0]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def load_clipboard(self) -> None:
        got = _base.clipboard_image()
        if got is None:
            self.error("Clipboard", "The clipboard has no image.")
            return
        self.set_image(*got)

    # -- preview --

    def _snapshot(self):
        return (self.source, copy.deepcopy(self.options))

    @staticmethod
    def _render(snap):
        src, opts = snap
        return None if src is None else beautify(src, opts)

    def _show(self, img) -> None:
        self.result = img
        self.last_error = ""
        self.error_label.set_label("")
        self.preview_pane.set_image(img)

    def _fail(self, msg: str) -> None:
        self.last_error = msg
        self.error_label.set_label(msg)

    def _with_result(self, then) -> None:
        if self.source is None:
            return
        if self.result is not None and self.preview.idle.is_set() and not self.last_error:
            then(self.result)
            return
        snap = self._snapshot()

        def work():
            try:
                img = self._render(snap)
            except Exception as e:
                gtkutil.idle(self.error, "Beautify failed", str(e))
                return
            gtkutil.idle(then, img)
        threading.Thread(target=work, daemon=True).start()

    # -- output --

    def copy(self) -> None:
        self._with_result(lambda img: (clipboard.set_image(img), self.toast("Copied to the clipboard")))

    def save(self) -> None:
        if not self.file_path:
            self.save_as_dialog()
            return
        self.save_to(self.file_path)

    def save_as_dialog(self) -> None:
        stem = Path(self.file_path).stem if self.file_path else "beautified"
        folder = str(Path(self.file_path).parent) if self.file_path else str(self.app.store.screenshots_folder(
            self.ts))
        gtkutil.save_file(self, "Save image as", os.path.join(folder, f"{stem}.png"),
                          lambda p: p and self.save_to(p), gtkutil.IMAGE_PATTERNS)

    def save_to(self, path: str) -> None:
        from ...tools.imagetools import save_image

        def write(img):
            try:
                save_image(img, path)
            except (OSError, ValueError) as e:
                self.error("Save failed", str(e))
                return
            self.file_path = path
            self.toast(f"Saved {os.path.basename(path)}")
        self._with_result(write)

    def upload(self) -> None:
        name = Path(self.file_path).stem if self.file_path else "beautified"
        self._with_result(lambda img: (_base.upload_image(self.app, img, name, self.ts),
                                       self.toast("Uploading…")))


def open(app, ctx: JobContext) -> BeautifierWindow:  # noqa: A001
    win = BeautifierWindow.present_single(app, ctx)
    for f in ctx.files:
        if _base.is_image_path(f):
            win.load_file(f)
            break
    return win
