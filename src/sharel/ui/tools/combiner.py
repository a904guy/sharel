"""Image combiner (ShareX ``ImageCombinerForm``): join images vertically or horizontally.

Ordered input list (Add, Remove, Move up / down, drag and drop), orientation, alignment, space between images,
wrap after N images, auto fill background (the first image's bottom-right pixel). Live preview. "Combine" runs
the result through the after-capture tasks like a capture (ShareX ``RunImageTask``); "Save as…" and "Copy"
are also offered. The options are remembered in the task settings' tool options.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from gi.repository import Adw, Gtk
from PIL import Image

from ...core import clipboard
from ...core.jobs import JobContext
from ...tools import imagetools
from .. import gtkutil
from . import _base

ORIENTATIONS = ["Vertical", "Horizontal"]
ALIGNMENTS = ["LeftOrTop", "Center", "RightOrBottom"]
ALIGNMENT_LABELS = ["Left / top", "Center", "Right / bottom"]


@dataclass
class CombineOptions:
    orientation: str = "Vertical"
    alignment: str = "LeftOrTop"
    space: int = 0
    wrap_after: int = 0
    auto_fill_background: bool = True


def combine(paths: list[str], o: CombineOptions) -> Image.Image:
    return imagetools.combine_images([_base.load_image_file(p) for p in paths], o.orientation, o.alignment,
                                     o.space, o.wrap_after, o.auto_fill_background)


class CombinerWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Image combiner", 1100, 720)
        self.ctx = ctx
        self.tools = _base.ToolSettingsWriter(app, ctx)
        t = ctx.task_settings.tool_settings
        self.opts = CombineOptions(t.image_combiner_orientation if t.image_combiner_orientation in ORIENTATIONS
                                   else "Vertical",
                                   t.image_combiner_align if t.image_combiner_align in ALIGNMENTS else "LeftOrTop",
                                   t.image_combiner_space, 0, t.image_combiner_auto_fill_background)
        self.result: Image.Image | None = None
        self.files = _base.FileList("Images", on_change=self._changed, reorder=True)
        self.files.set_size_request(280, -1)
        o = self.opts

        def set_(name, v):
            setattr(o, name, v)
            self._changed()
        g = Adw.PreferencesGroup(title="Options")
        self.orientation_row = _base.combo_row("Orientation", ORIENTATIONS, ORIENTATIONS.index(o.orientation),
                                               lambda i: set_("orientation", ORIENTATIONS[i]))
        self.alignment_row = _base.combo_row("Alignment", ALIGNMENT_LABELS, ALIGNMENTS.index(o.alignment),
                                             lambda i: set_("alignment", ALIGNMENTS[i]))
        self.space_row = _base.spin_row("Space", o.space, 0, 10000, lambda v: set_("space", int(v)))
        self.wrap_row = _base.spin_row("Wrap after", o.wrap_after, 0, 1000, lambda v: set_("wrap_after", int(v)),
                                       subtitle="Start a new column / row after this many images (0 = never)")
        self.autofill_row = _base.switch_row("Auto fill background", o.auto_fill_background,
                                             lambda v: set_("auto_fill_background", v))
        for r in (self.orientation_row, self.alignment_row, self.space_row, self.wrap_row, self.autofill_row):
            g.add(r)
        options = _base.prefs_box(g)
        options.set_size_request(340, -1)
        self.preview_pane = _base.ImagePane("Add two or more images")
        self.preview = _base.LivePreview(lambda: (list(self.files.paths), CombineOptions(**vars(self.opts))),
                                         self._render, self._show, self._fail)
        center = Gtk.Box(spacing=10, margin_top=10, margin_start=10, margin_end=10, vexpand=True)
        center.append(self.files)
        center.append(options)
        center.append(self.preview_pane)
        self.combine_button = _base.button("Combine", self.combine, suggested=True,
                                           tooltip="Run the after capture tasks on the combined image")
        self.save_button = _base.button("Save as…", self.save_as_dialog)
        self.copy_button = _base.button("Copy", self.copy)
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=10, margin_start=10, margin_end=10,
                         halign=Gtk.Align.END)
        for b in (self.copy_button, self.save_button, self.combine_button):
            bottom.append(b)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(center)
        body.append(bottom)
        self.set_body(body)
        self._sync()

    def _changed(self) -> None:
        self._sync()
        self.preview.schedule()

    def _sync(self) -> None:
        ok = len(self.files.paths) >= 1
        for b in (self.combine_button, self.save_button, self.copy_button):
            b.set_sensitive(ok)

    @staticmethod
    def _render(snap):
        paths, o = snap
        return combine(paths, o) if paths else None

    def _show(self, img) -> None:
        self.result = img
        self.preview_pane.set_image(img)

    def _fail(self, msg: str) -> None:
        self.result = None
        self.preview_pane.set_image(None, f"Can't combine: {msg}")

    def _remember(self) -> None:
        o = self.opts
        self.tools.image_combiner_orientation = o.orientation
        self.tools.image_combiner_align = o.alignment
        self.tools.image_combiner_space = o.space
        self.tools.image_combiner_auto_fill_background = o.auto_fill_background
        self.tools.save()

    def _with_result(self, then) -> None:
        if not self.files.paths:
            return
        if self.result is not None and self.preview.idle.is_set():
            then(self.result)
            return
        paths, o = list(self.files.paths), CombineOptions(**vars(self.opts))

        def work():
            try:
                img = combine(paths, o)
            except (OSError, ValueError) as e:
                gtkutil.idle(self.error, "Can't combine images", str(e))
                return
            gtkutil.idle(then, img)
        threading.Thread(target=work, daemon=True).start()

    def combine(self) -> None:
        self._remember()

        def run(img):
            self.last_task = self.app.tasks.run_image(img, self.ctx.task_settings)
            self.toast("Combined image sent to the after capture tasks")
        self._with_result(run)

    def copy(self) -> None:
        self._with_result(lambda img: (clipboard.set_image(img), self.toast("Copied to the clipboard")))

    def save_as_dialog(self) -> None:
        first = self.files.paths[0] if self.files.paths else ""
        folder = os.path.dirname(first) if first else str(self.app.store.screenshots_folder(self.ctx.task_settings))
        gtkutil.save_file(self, "Save combined image", os.path.join(folder, "combined.png"),
                          lambda p: p and self.save_to(p), gtkutil.IMAGE_PATTERNS)

    def save_to(self, path: str) -> None:
        self._remember()

        def write(img):
            try:
                imagetools.save_image(img, path)
            except (OSError, ValueError) as e:
                self.error("Save failed", str(e))
                return
            self.toast(f"Saved {os.path.basename(path)}")
        self._with_result(write)


def open(app, ctx: JobContext) -> CombinerWindow:  # noqa: A001
    win = CombinerWindow.present_single(app, ctx)
    if ctx.files:
        win.files.add_paths(ctx.files)
    return win
