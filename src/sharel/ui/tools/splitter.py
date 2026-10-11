"""Image splitter (ShareX ``ImageSplitterWindow``): cut an image into a grid of tiles.

Split by rows × columns (equal cells, ShareX) or by a fixed tile width × height (edge tiles take the rest).
The preview draws the cut lines and the cell size; tiles are written as ``<name>_<row>_<column>.<ext>`` into the
output folder (default: the image's folder). "Open output folder" after splitting. Rows / columns defaults come
from the task settings' tool options.
"""
from __future__ import annotations

import os

from gi.repository import Adw, Gtk
from PIL import Image, ImageDraw

from ...core.jobs import JobContext
from ...tools import imagetools
from .. import gtkutil
from . import _base

MODES = ["Rows and columns", "Tile size"]


class SplitterWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Image splitter", 960, 640)
        self.ctx = ctx
        self.tools = _base.ToolSettingsWriter(app, ctx)
        t = ctx.task_settings.tool_settings
        self.mode = 0
        self.rows_n = max(1, t.image_splitter_rows)
        self.columns_n = max(1, t.image_splitter_columns)
        self.tile_w = 256
        self.tile_h = 256
        self.image: Image.Image | None = None
        self.path = ""
        self.results: list = []

        src = Adw.PreferencesGroup(title="Image")
        self.path_row = Adw.EntryRow(title="Image file", text="")
        self.path_row.connect("apply", lambda r: self.load(r.get_text()))
        self.path_row.set_show_apply_button(True)
        browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
        browse.add_css_class("flat")
        browse.connect("clicked", lambda *_: gtkutil.open_files(self, "Image to split",
                                                                lambda ps: ps and self.load(ps[0]),
                                                                patterns=gtkutil.IMAGE_PATTERNS, multiple=False))
        self.path_row.add_suffix(browse)
        src.add(self.path_row)
        grid = Adw.PreferencesGroup(title="Split")
        self.mode_row = _base.combo_row("Split by", MODES, 0, self._set_mode)
        self.rows_row = _base.spin_row("Rows", self.rows_n, 1, 1000, lambda v: self._set("rows_n", int(v)))
        self.columns_row = _base.spin_row("Columns", self.columns_n, 1, 1000,
                                          lambda v: self._set("columns_n", int(v)))
        self.tile_w_row = _base.spin_row("Tile width", self.tile_w, 1, 100000, lambda v: self._set("tile_w", int(v)))
        self.tile_h_row = _base.spin_row("Tile height", self.tile_h, 1, 100000,
                                         lambda v: self._set("tile_h", int(v)))
        self.size_label = Adw.ActionRow(title="Cell size", subtitle="")
        for r in (self.mode_row, self.rows_row, self.columns_row, self.tile_w_row, self.tile_h_row, self.size_label):
            grid.add(r)
        out = Adw.PreferencesGroup(title="Output")
        self.output = _base.OutputFolder(title="Output folder (empty = the image's folder)")
        out.add(self.output)
        options = _base.prefs_box(src, grid, out)
        options.set_size_request(380, -1)
        self.preview_pane = _base.ImagePane("Choose or drop an image")
        center = Gtk.Box(spacing=10, margin_top=10, margin_start=10, margin_end=10, vexpand=True)
        center.append(options)
        center.append(self.preview_pane)
        self.status = Gtk.Label(label="", xalign=0, margin_start=10)
        self.batch = _base.BatchBar()
        self.split_button = _base.button("Split image", self.split, suggested=True)
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=10, margin_start=10, margin_end=10)
        bottom.append(self.batch)
        bottom.append(self.split_button)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(center)
        body.append(self.status)
        body.append(bottom)
        self.set_body(body)
        self.add_controller(_base.image_drop_target(lambda ps: self.load(ps[0])))
        self._update()

    def _set(self, name, v) -> None:
        setattr(self, name, v)
        self._update()

    def _set_mode(self, i: int) -> None:
        self.mode = i
        self._update()

    def load(self, path: str) -> None:
        try:
            self.image = _base.load_image_file(path)
        except OSError as e:
            self.error("Can't open image", f"{path}: {e}")
            return
        self.path = os.path.abspath(path)
        if self.path_row.get_text() != self.path:
            self.path_row.set_text(self.path)
        self._update()

    def grid(self) -> tuple[int, int, int, int]:
        """(columns, rows, cell width, cell height) for the current options."""
        if self.image is None:
            return 0, 0, 0, 0
        w, h = self.image.size
        if self.mode == 0:
            return self.columns_n, self.rows_n, w // self.columns_n, h // self.rows_n
        return -(-w // self.tile_w), -(-h // self.tile_h), min(self.tile_w, w), min(self.tile_h, h)

    def _update(self) -> None:
        by_grid = self.mode == 0
        self.rows_row.set_visible(by_grid)
        self.columns_row.set_visible(by_grid)
        self.tile_w_row.set_visible(not by_grid)
        self.tile_h_row.set_visible(not by_grid)
        cols, rows, cw, ch = self.grid()
        valid = self.image is not None and cw >= 1 and ch >= 1
        self.size_label.set_subtitle(f"{cw} × {ch} px, {cols * rows} tiles" if valid else
                                     ("The image is too small" if self.image is not None else ""))
        self.split_button.set_sensitive(valid and not self.batch.busy)
        if self.image is None:
            self.preview_pane.set_image(None)
            return
        prev = self.image.convert("RGBA").copy()
        if valid:
            d = ImageDraw.Draw(prev)
            lw = max(1, max(prev.size) // 400)
            xs = [c * cw for c in range(1, cols)] if by_grid else list(range(self.tile_w, prev.width, self.tile_w))
            ys = [r * ch for r in range(1, rows)] if by_grid else list(range(self.tile_h, prev.height, self.tile_h))
            for x in xs:
                d.line((x, 0, x, prev.height), fill=(255, 0, 0, 255), width=lw)
            for y in ys:
                d.line((0, y, prev.width, y), fill=(255, 0, 0, 255), width=lw)
        self.preview_pane.set_image(prev, f"{self.image.width} × {self.image.height}")

    def split(self) -> None:
        if self.image is None:
            return
        folder = self.output.path
        if folder and not os.path.isdir(folder):
            self.error("Output folder", f"{folder} does not exist.")
            return
        if self.mode == 0:
            self.tools.image_splitter_rows = self.rows_n
            self.tools.image_splitter_columns = self.columns_n
            self.tools.save()
        path, mode = self.path, self.mode
        rows, cols, tile = self.rows_n, self.columns_n, (self.tile_w, self.tile_h)

        def work(_item, _cancel):
            if mode == 0:
                return imagetools.split_to_files(path, folder or None, rows=rows, columns=cols)
            return imagetools.split_to_files(path, folder or None, tile_size=tile)

        def done(results, errors, cancelled):
            self.results = results[0] if results else []
            if self.results:
                self.output.last_output = str(self.results[0].parent)
                self.status.set_label(f"Saved {len(self.results)} tiles to {self.results[0].parent}")
            if errors:
                self.error("Split failed", errors[0][1])
            self._update()
        self.batch.run([path], work, done)


def open(app, ctx: JobContext) -> SplitterWindow:  # noqa: A001
    win = SplitterWindow.present_single(app, ctx)
    for f in ctx.files:
        if _base.is_image_path(f):
            win.load(f)
            break
    return win
