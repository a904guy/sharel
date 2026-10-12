"""Base window for the batch image tools (thumbnailer, resizer, converter, watermark, metadata stripper).

Layout (ShareX 18 tool windows): the input list on the left (Add / Remove / Clear, drag and drop), options,
output folder (+ "Open output folder") and output file name pattern (``$filename`` = source name) on the
right, a preview of the selected image when the tool has one, then progress + Cancel and the run button.
Subclasses implement ``build_options()``, ``snapshot()`` (main loop) and ``process(path, opts, cancel)``
(worker thread, returns the written path); ``render_preview(img, opts)`` enables the preview.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from gi.repository import Adw, Gtk
from PIL import Image

from ...core.jobs import JobContext
from ..gtkutil import no_hyphens
from . import _base


class BatchImageTool(_base.ToolWindow):
    TITLE = "Image tool"
    RUN_LABEL = "Run"
    PATTERN = "$filename"
    PREVIEW = True
    SIZE = (1100, 720)

    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, self.TITLE, *self.SIZE)
        self.ctx = ctx
        self.ts = ctx.task_settings
        self.tools = _base.ToolSettingsWriter(app, ctx)
        self.results: list[Any] = []
        self.errors: list[tuple[str, str]] = []
        self.cancelled = False
        self._out_spec = ("", self.PATTERN)

        self.files = _base.FileList("Images", on_change=self._files_changed)
        self.files.on_select = self._schedule_preview
        self.files.set_size_request(280, -1)
        self.output = _base.OutputFolder()
        self.output.connect("changed", lambda *_: self._sync())
        self.preview_image: Image.Image | None = None
        self.name_row = _base.entry_row("Output file name ($filename = source file name)", self.PATTERN,
                                        lambda _t: self._sync())
        out_group = Adw.PreferencesGroup(title="Output")
        out_group.add(self.output)
        if self.PATTERN:
            out_group.add(self.name_row)
        groups = [*self.build_options(), out_group]
        options = _base.prefs_box(*groups)
        options.set_size_request(380, -1)

        self.batch = _base.BatchBar()
        self.batch.on_busy = lambda _b: self._sync()
        self.run_button = _base.button(self.RUN_LABEL, self.run, suggested=True)
        self.status = no_hyphens(Gtk.Label(label="", xalign=0, selectable=True))  # output paths
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=10, margin_start=10, margin_end=10)
        bottom.append(self.batch)
        bottom.append(self.run_button)

        center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=10, margin_start=10,
                         margin_end=10, vexpand=True)
        center.append(self.files)
        center.append(options)
        self.preview_pane: _base.ImagePane | None = None
        self.preview: _base.LivePreview | None = None
        if self.PREVIEW:
            self.preview_pane = _base.ImagePane("Select an image to preview", 300, 240)
            center.append(self.preview_pane)
            self.preview = _base.LivePreview(self._preview_snapshot, self._preview_render, self._preview_show,
                                             self._preview_fail)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(center)
        body.append(self.status)
        self.status.set_margin_start(10)
        self.status.set_margin_end(10)
        body.append(bottom)
        self.set_body(body)
        self._sync()

    # -- to override --

    def build_options(self) -> list[Adw.PreferencesGroup]:
        return []

    def snapshot(self) -> Any:
        return None

    def save_defaults(self, opts: Any) -> None:
        """Store the options used for a run as the tool's defaults (task settings → tools)."""

    def output_ext(self, src: str, opts: Any) -> str:
        return Path(src).suffix.lstrip(".") or "png"

    def process(self, path: str, opts: Any, cancel: threading.Event) -> Any:
        raise RuntimeError("process() is defined by each tool")

    def render_preview(self, img: Image.Image, opts: Any) -> Image.Image:
        return img

    def describe_result(self, result: Any) -> str:
        return str(result)

    # -- shared --

    def options_changed(self) -> None:
        self._schedule_preview()

    def target_path(self, src: str, opts: Any) -> Path:
        """Where ``src`` is written (output folder and name pattern as they were when the run started)."""
        folder, pattern = self._out_spec
        return _base.output_path(src, folder, pattern, self.output_ext(src, opts))

    def _files_changed(self) -> None:
        self._sync()
        self._schedule_preview()

    def _sync(self) -> None:
        folder_ok = not self.output.path or os.path.isdir(self.output.path)
        name_ok = not self.PATTERN or bool(self.name_row.get_text().strip())
        self.run_button.set_sensitive(bool(self.files.paths) and not self.batch.busy and folder_ok and name_ok)

    def run(self) -> None:
        if not self.files.paths or self.batch.busy:
            return
        if self.output.path and not os.path.isdir(self.output.path):
            self.error("Output folder", f"{self.output.path} does not exist.")
            return
        opts = self.snapshot()
        self._out_spec = (self.output.path, self.name_row.get_text() if self.PATTERN else "$filename")
        self.save_defaults(opts)
        self.tools.save()
        self.status.set_label("")
        self.batch.run(self.files.paths, lambda p, c: self.process(p, opts, c), self._done)

    def _done(self, results, errors, cancelled) -> None:
        self.results, self.errors, self.cancelled = results, errors, cancelled
        if results:
            first = results[0]
            path = first if isinstance(first, (str, Path)) else getattr(first, "path", "")
            if path:
                self.output.last_output = str(Path(path).parent)
        lines = [self.describe_result(r) for r in results]
        lines += [f"{os.path.basename(str(p))}: {msg}" for p, msg in errors]
        if cancelled:
            lines.append("Cancelled.")
        self.status.set_label("\n".join(lines[:12]) + ("\n…" if len(lines) > 12 else ""))
        if errors:
            self.error(f"{len(errors)} file(s) failed", "\n".join(f"{os.path.basename(str(p))}: {m}"
                                                                  for p, m in errors[:10]))
        self._sync()

    # -- preview --

    def _schedule_preview(self) -> None:
        if self.preview is not None:
            self.preview.schedule()

    def _preview_snapshot(self):
        return self.files.selected_path(), self.snapshot()

    def _preview_render(self, snap):
        path, opts = snap
        if not path:
            return None
        return self.render_preview(_base.load_image_file(path), opts)

    def _preview_show(self, img) -> None:
        self.preview_image = img
        self.preview_pane.set_image(img)

    def _preview_fail(self, msg: str) -> None:
        self.preview_pane.set_image(None, f"Preview failed: {msg}")

    def add_files(self, paths: list[str]) -> None:
        self.files.add_paths(paths)


def open_batch_tool(cls, app, ctx: JobContext):
    win = cls.present_single(app, ctx)
    if ctx.files:
        win.add_files(ctx.files)
    return win
