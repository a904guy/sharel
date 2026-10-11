"""Animated GIF trimmer window (ShareX's GIF trimmer tool).

Input GIF (+ Browse, drag and drop) with frame count, size, length and loop count; a preview of the frame under
the slider that moved last, start/end frame sliders and spin fields (with each frame's time), step buttons,
Play to preview the selected range at the GIF's own frame timing, an output file (+ Browse), Trim (Pillow,
off the main thread, every kept frame keeps its delay and the loop count is preserved), then Open / Show in
folder / Upload.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from PIL import Image, ImageSequence

from ...tools import videotools as vt
from ..gtkutil import texture_from_pil
from ...tools._common import OperationCancelled
from ._base import ProgressPanel, ToolWindow, button, file_drop_target, single, spin
from ._videocommon import OutputActions, PathRow, unique_output

GIF_PATTERNS = ["*.gif"]


def load_frames(path: str, cancel=None, max_size: int = 640) -> tuple[vt.GifInfo, list[Image.Image]]:
    info = vt.gif_info(path)
    frames = []
    with Image.open(path) as im:
        for fr in ImageSequence.Iterator(im):
            if cancel is not None and cancel.is_set():
                from ...tools._common import OperationCancelled
                raise OperationCancelled()
            f = fr.convert("RGBA")
            f.thumbnail((max_size, max_size))
            frames.append(f)
    return info, frames


class GifTrimmerWindow(ToolWindow):
    def __init__(self, app, ctx) -> None:
        super().__init__(app, "Animated GIF trimmer", 720, 680, scroll=True)
        self.ctx = ctx
        self.info: vt.GifInfo | None = None
        self.frames: list[Image.Image] = []
        self.current = 0
        self.worker = None
        self.load_worker = None
        self.progress_log: list[tuple[int, int]] = []
        self._play_id = 0
        self._auto_output = ""

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.input_row = PathRow("Input GIF:", "open", patterns=GIF_PATTERNS, filter_name="GIF images",
                                 on_changed=self._input_changed, placeholder="Animated GIF (or drop one here)")
        body.append(self.input_row)
        self.info_label = Gtk.Label(xalign=0)
        self.info_label.add_css_class("dim-label")
        body.append(self.info_label)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=300,
                                   vexpand=True)
        self.picture.add_css_class("card")
        body.append(self.picture)
        self.frame_label = Gtk.Label(xalign=0.5)
        body.append(self.frame_label)

        self.controls: dict[str, tuple[Gtk.Scale, Gtk.SpinButton]] = {}
        for key, title in (("start", "Start frame"), ("end", "End frame")):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=f"{title}:", xalign=0, width_chars=12))
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
            scale.set_hexpand(True)
            scale.set_draw_value(False)
            scale.set_digits(0)
            scale.set_tooltip_text(title)
            field = spin(0, 0, 1, 1, tooltip=title)
            row.append(scale)
            row.append(field)
            row.append(button("◀", lambda k=key: self.step(k, -1), f"Previous frame ({title.lower()})"))
            row.append(button("▶", lambda k=key: self.step(k, 1), f"Next frame ({title.lower()})"))
            scale.connect("value-changed", lambda s, k=key: self.set_frame(k, round(s.get_value())))
            field.connect("value-changed", lambda f, k=key: self.set_frame(k, int(f.get_value())))
            self.controls[key] = (scale, field)
            body.append(row)
        self.range_label = Gtk.Label(xalign=0)
        body.append(self.range_label)
        self.play_button = button("Play", self.toggle_play, "Preview the selected range")
        body.append(self.play_button)

        self.output_row = PathRow("Output file:", "save", patterns=GIF_PATTERNS, suggested=self._suggest_output)
        body.append(self.output_row)
        self.trim_button = button("Trim", self.trim, "Write the trimmed GIF", css="suggested-action")
        self.progress = ProgressPanel()
        self.actions = OutputActions(app, lambda: self.ctx.task_settings)
        bottom = Gtk.Box(spacing=6)
        bottom.append(self.trim_button)
        bottom.append(self.actions)
        body.append(self.progress)
        body.append(bottom)
        self.set_body(body)
        file_drop_target(body, lambda ps: self.input_row.set_path(ps[0]))
        self._update_sensitivity()

    # -- loading --

    def _input_changed(self, path: str) -> None:
        self.stop_play()
        self.info = None
        self.frames = []
        self.picture.set_paintable(None)
        self._update_sensitivity()
        if not path or not Path(path).is_file():
            self.info_label.set_label("")
            return
        if self.load_worker is not None and self.load_worker.running:
            self.load_worker.cancel()

        def done(res):
            if self.input_row.path != path:
                return
            self.set_gif(*res)

        self.info_label.set_label("Loading…")
        self.load_worker = self.run(lambda _p, c: load_frames(path, c), done,
                                    on_error=lambda e: self.info_label.set_label(f"Not a readable GIF: {e}"))

    def set_gif(self, info: vt.GifInfo, frames: list[Image.Image]) -> None:
        self.info = info
        self.frames = frames
        loop = "no loop" if info.loop is None else ("loops forever" if info.loop == 0 else f"loops {info.loop}×")
        self.info_label.set_label(f"{info.frames} frames · {info.size[0]}×{info.size[1]} · "
                                  f"{info.total_duration / 1000:.2f} s · {loop}")
        last = max(0, info.frames - 1)
        for scale, field in self.controls.values():
            scale.get_adjustment().configure(0, 0, last, 1, 5, 0)
            field.get_adjustment().configure(0, 0, last, 1, 5, 0)
        self.set_frame("start", 0)
        self.set_frame("end", last)
        if not self.output_row.path or self.output_row.path == self._auto_output:
            self._auto_output = self._suggest_output()
            self.output_row.set_path(self._auto_output)
        self._update_sensitivity()

    def _suggest_output(self) -> str:
        src = self.input_row.path
        if not src:
            return ""
        p = Path(src)
        return str(unique_output(p.with_name(f"{p.stem}-trimmed.gif")))

    # -- frames --

    @property
    def start_frame(self) -> int:
        return int(self.controls["start"][1].get_value())

    @property
    def end_frame(self) -> int:
        return int(self.controls["end"][1].get_value())

    def frame_time(self, idx: int) -> int:
        return sum(self.info.durations[:idx]) if self.info else 0

    def set_frame(self, key: str, idx: int) -> None:
        if self.info is None:
            return
        idx = max(0, min(self.info.frames - 1, idx))
        scale, field = self.controls[key]
        if round(scale.get_value()) != idx:
            scale.set_value(idx)
        if int(field.get_value()) != idx:
            field.set_value(idx)
        self.show_frame(idx)
        s, e = self.start_frame, self.end_frame
        if s <= e:
            dur = sum(self.info.durations[s:e + 1])
            self.range_label.set_label(f"Keeping frames {s}–{e}: {e - s + 1} frame(s), {dur / 1000:.2f} s")
        else:
            self.range_label.set_label("The start frame is after the end frame.")
        self._update_sensitivity()

    def step(self, key: str, delta: int) -> None:
        self.set_frame(key, int(self.controls[key][1].get_value()) + delta)

    def show_frame(self, idx: int) -> None:
        if 0 <= idx < len(self.frames):
            self.current = idx
            self.picture.set_paintable(texture_from_pil(self.frames[idx]))
            d = self.info.durations[idx] if self.info else 0
            self.frame_label.set_label(f"Frame {idx} · {self.frame_time(idx) / 1000:.2f} s · {d} ms")

    # -- preview playback --

    def toggle_play(self) -> None:
        if self._play_id:
            self.stop_play()
            return
        if self.info is None or self.start_frame > self.end_frame:
            return
        self.play_button.set_label("Stop")
        self._play_next(self.start_frame)

    def _play_next(self, idx: int) -> bool:
        self._play_id = 0
        if self.info is None:
            return GLib.SOURCE_REMOVE
        if idx > self.end_frame or idx < self.start_frame:
            idx = self.start_frame
        self.show_frame(idx)
        delay = max(20, self.info.durations[idx] or 100)
        self._play_id = GLib.timeout_add(delay, self._play_next, idx + 1)
        return GLib.SOURCE_REMOVE

    def stop_play(self) -> None:
        if self._play_id:
            GLib.source_remove(self._play_id)
            self._play_id = 0
        self.play_button.set_label("Play")

    def on_closed(self) -> None:
        self.stop_play()

    # -- trim --

    def _update_sensitivity(self) -> None:
        ok = self.info is not None and self.start_frame <= self.end_frame
        busy = self.worker is not None and self.worker.running
        self.trim_button.set_sensitive(ok and not busy)
        self.play_button.set_sensitive(ok)

    def trim(self) -> None:
        src, dst = self.input_row.path, self.output_row.path
        if not dst:
            self.error("No output file", "Choose where to save the trimmed GIF.")
            return
        if os.path.abspath(src) == os.path.abspath(dst):
            self.error("Output file", "The output file must be different from the input file.")
            return
        s, e = self.start_frame, self.end_frame
        self.progress_log = []
        self.actions.set_outputs([])

        def work(progress, cancel):
            progress(0, 1)
            out = str(vt.trim_gif(src, dst, s, e))
            if cancel.is_set():  # Cancel pressed while Pillow was writing: discard the result
                Path(out).unlink(missing_ok=True)
                raise OperationCancelled()
            progress(1, 1)
            return out

        def done(path):
            self.actions.set_outputs([path])
            self.progress.set_status(f"Saved {os.path.basename(path)}")
            self._auto_output = ""

        self.worker = self.run(work, done, panel=self.progress, status="Trimming…",
                               on_progress=lambda d, t: self.progress_log.append((d, t)),
                               on_finally=self._update_sensitivity)
        self._update_sensitivity()


def open(app, ctx) -> GifTrimmerWindow:  # noqa: A001 - the tool contract's name
    files = list(getattr(ctx, "files", None) or [])
    win = single("giftrimmer", lambda: GifTrimmerWindow(app, ctx))
    if files:
        win.input_row.set_path(files[0])
    return win
