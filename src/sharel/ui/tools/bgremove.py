"""Background remover (ShareX ``BackgroundRemoverWindow``) using rembg.

Choose or drop an image, pick the model (``u2netp`` is the small default; the others are downloaded by rembg on
first use into the models folder), optional alpha matting, then "Remove background" (on a thread, with Cancel).
Source and result are shown side by side; Save writes ``<name>-nobg.png`` next to the source, Save as asks,
Copy puts the result on the clipboard, "Open models folder" shows where the models live. Without rembg /
onnxruntime installed the window says so and the remove button stays disabled.
"""
from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Adw, Gtk
from PIL import Image

from ...core import clipboard, desktop
from ...core.jobs import JobContext
from ...tools import bgremove
from ...tools.imagetools import save_image
from .. import gtkutil
from . import _base

MODELS = ["u2netp", "u2net", "isnet-general-use", "silueta", "u2net_human_seg", "isnet-anime"]


class BackgroundRemoverWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Background remover", 1000, 640)
        self.path = ""
        self.source: Image.Image | None = None
        self.result: Image.Image | None = None
        self.model = bgremove.DEFAULT_MODEL
        self.alpha_matting = False
        self.available = bgremove.is_available()

        self.banner = Adw.Banner(title="The background remover needs the rembg and onnxruntime Python packages "
                                       "(pip install 'sharel[rembg]').", revealed=not self.available)
        opts = Adw.PreferencesGroup()
        self.source_row = Adw.ActionRow(title="Image", subtitle="Choose or drop an image")
        browse = _base.button("Browse…", self.browse)
        browse.set_valign(Gtk.Align.CENTER)
        self.source_row.add_suffix(browse)
        self.model_row = _base.combo_row("Model", MODELS, MODELS.index(self.model), self._set_model)
        self.matting_row = _base.switch_row("Alpha matting", False, lambda v: setattr(self, "alpha_matting", v),
                                            subtitle="Smoother edges, slower")
        models = _base.button("Open models folder", self.open_models_folder)
        models.set_valign(Gtk.Align.CENTER)
        self.model_row.add_suffix(models)
        for r in (self.source_row, self.model_row, self.matting_row):
            opts.add(r)
        self.source_pane = _base.ImagePane("Source", 280, 240)
        self.result_pane = _base.ImagePane("Result", 280, 240)
        panes = Gtk.Box(spacing=10, homogeneous=True, vexpand=True)
        panes.append(self.source_pane)
        panes.append(self.result_pane)
        self.batch = _base.BatchBar()
        self.batch.on_busy = lambda _b: self._sync()
        self.remove_button = _base.button("Remove background", self.remove, suggested=True)
        self.save_button = _base.button("Save", self.save)
        self.save_as_button = _base.button("Save as…", self.save_as_dialog)
        self.copy_button = _base.button("Copy", self.copy)
        bottom = Gtk.Box(spacing=8)
        bottom.append(self.batch)
        for b in (self.copy_button, self.save_as_button, self.save_button, self.remove_button):
            bottom.append(b)
        self.status = Gtk.Label(label="", xalign=0, selectable=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_bottom=10,
                      margin_start=10, margin_end=10)
        for w in (opts, panes, self.status, bottom):
            box.append(w)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.banner)
        outer.append(box)
        self.set_body(outer)
        self.add_controller(_base.image_drop_target(lambda ps: self.load(ps[0])))
        self._sync()

    def _set_model(self, i: int) -> None:
        self.model = MODELS[i]

    def _sync(self) -> None:
        busy = self.batch.busy
        self.remove_button.set_sensitive(self.available and self.source is not None and not busy)
        for b in (self.save_button, self.save_as_button, self.copy_button):
            b.set_sensitive(self.result is not None and not busy)

    def browse(self) -> None:
        gtkutil.open_files(self, "Choose image", lambda ps: ps and self.load(ps[0]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def load(self, path: str) -> None:
        try:
            self.source = _base.load_image_file(path)
        except OSError as e:
            self.error("Can't open image", f"{path}: {e}")
            return
        self.path = os.path.abspath(path)
        self.result = None
        self.source_row.set_subtitle(f"{os.path.basename(path)} — {self.source.width} × {self.source.height}")
        self.source_pane.set_image(self.source)
        self.result_pane.set_image(None)
        self._sync()

    def set_image(self, img: Image.Image, path: str = "") -> None:
        self.source = img.convert("RGBA")
        self.path = path
        self.result = None
        self.source_pane.set_image(self.source)
        self.result_pane.set_image(None)
        self._sync()

    def open_models_folder(self) -> None:
        d = bgremove.default_model_dir()
        d.mkdir(parents=True, exist_ok=True)
        desktop.open_folder(str(d))

    def remove(self) -> None:
        if self.source is None or not self.available:
            return
        src, model, matting = self.source, self.model, self.alpha_matting

        def work(_item, _cancel):
            return bgremove.remove_background(src, model=model, alpha_matting=matting)

        def done(results, errors, cancelled):
            if results and not cancelled:
                self.result = results[0]
                self.result_pane.set_image(self.result)
                self.status.set_label("Background removed")
            elif errors:
                self.error("Background removal failed", errors[0][1])
            self._sync()
        self.status.set_label(f"Removing the background with {model}… (the model is downloaded on first use)")
        self.batch.run([None], work, done, label=lambda _i: model)

    def default_output(self) -> Path:
        if self.path:
            p = Path(self.path)
            return p.with_name(f"{p.stem}-nobg.png")
        return Path(os.path.expanduser("~")) / "image-nobg.png"

    def save(self) -> None:
        if self.path:
            self.save_to(str(self.default_output()))
        else:
            self.save_as_dialog()

    def save_as_dialog(self) -> None:
        gtkutil.save_file(self, "Save image", str(self.default_output()), lambda p: p and self.save_to(p),
                          ["*.png", "*.webp", "*.tif"])

    def save_to(self, path: str) -> None:
        if self.result is None:
            return
        try:
            out = save_image(self.result, path)
        except (OSError, ValueError) as e:
            self.error("Save failed", str(e))
            return
        self.status.set_label(f"Saved {out}")

    def copy(self) -> None:
        if self.result is not None:
            clipboard.set_image(self.result)
            self.toast("Copied to the clipboard")


def open(app, ctx: JobContext) -> BackgroundRemoverWindow:  # noqa: A001
    win = BackgroundRemoverWindow.present_single(app, ctx)
    for f in ctx.files:
        if _base.is_image_path(f):
            win.load(f)
            break
    return win
