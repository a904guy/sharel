"""Video trimmer window (ShareX's video trimmer tool).

Input video (+ Browse, drag and drop) with its media info, start and end sliders/fields with a preview frame of
each point (grabbed with FFmpeg off the main thread), "Stream copy" (instant, cuts on key frames) or
re-encode (x264 / x265 / VP9 / AV1 with CRF, frame accurate), output file (+ Browse), Trim with progress
parsed from FFmpeg and Cancel, then Open / Show in folder / Upload.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from ...tools import videotools as vt
from ..gtkutil import VIDEO_PATTERNS, human_size, texture_from_pil
from ._base import ProgressPanel, ToolWindow, button, dropdown, dropdown_value, file_drop_target, single, spin
from ._videocommon import OutputActions, PathRow, format_seconds, grab_frame, unique_output

TRIM_CODECS = ["x264", "x265", "vp9", "av1"]
TRIM_EXT = {"x264": ".mp4", "x265": ".mp4", "vp9": ".webm", "av1": ".mkv"}


class VideoTrimmerWindow(ToolWindow):
    def __init__(self, app, ctx) -> None:
        super().__init__(app, "Video trimmer", 760, 720, scroll=True)
        self.ctx = ctx
        self.media: vt.MediaInfo | None = None
        self.progress_log: list[tuple[int, int]] = []
        self.worker = None
        self.probe_worker = None
        self._preview_workers: dict[str, object] = {}
        self._preview_timers: dict[str, int] = {}
        self._auto_output = ""

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.input_row = PathRow("Input file path:", "open", on_changed=self._input_changed,
                                 placeholder="Video file (or drop one here)")
        body.append(self.input_row)
        self.info_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.info_label.add_css_class("dim-label")
        body.append(self.info_label)

        previews = Gtk.Box(spacing=12, homogeneous=True)
        self.points: dict[str, dict] = {}
        for key, title in (("start", "Start"), ("end", "End")):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=180)
            pic.add_css_class("card")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
            scale.set_draw_value(False)
            scale.set_tooltip_text(f"{title} position")
            field = spin(0, 0, 1, 0.1, 3, tooltip=f"{title} time (seconds)")
            time_label = Gtk.Label(label=format_seconds(0))
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=f"{title}:"))
            row.append(field)
            row.append(Gtk.Label(label="s"))
            row.append(time_label)
            col.append(pic)
            col.append(scale)
            col.append(row)
            previews.append(col)
            self.points[key] = {"picture": pic, "scale": scale, "field": field, "label": time_label}
            scale.connect("value-changed", lambda s, k=key: self._scale_moved(k, s.get_value()))
            field.connect("value-changed", lambda f, k=key: self._field_changed(k, f.get_value()))
        body.append(previews)
        self.duration_label = Gtk.Label(xalign=0)
        body.append(self.duration_label)

        self.stream_copy = Gtk.CheckButton(label="Stream copy (fast, cuts on the nearest key frame)", active=True)
        self.reencode = Gtk.CheckButton(label="Re-encode (frame accurate)", group=self.stream_copy)
        self.stream_copy.connect("toggled", lambda *_: self._mode_changed())
        self.reencode.connect("toggled", lambda *_: self._mode_changed())
        body.append(self.stream_copy)
        enc = Gtk.Box(spacing=6)
        enc.append(self.reencode)
        self.codec_dd = dropdown(TRIM_CODECS, 0, "Video encoder")
        self.codec_dd.connect("notify::selected", lambda *_: self._mode_changed())
        self.crf = spin(23, 0, 63, 1, tooltip="Quality (CRF, lower = better)")
        enc.append(self.codec_dd)
        enc.append(Gtk.Label(label="CRF:"))
        enc.append(self.crf)
        self.encode_options = [self.codec_dd, self.crf]
        body.append(enc)

        self.output_row = PathRow("Output file:", "save", patterns=VIDEO_PATTERNS, suggested=self._suggest_output)
        body.append(self.output_row)

        self.trim_button = button("Trim", self.toggle_trim, "Write the trimmed video", css="suggested-action")
        self.progress = ProgressPanel()
        self.actions = OutputActions(app, lambda: self.ctx.task_settings)
        bottom = Gtk.Box(spacing=6)
        bottom.append(self.trim_button)
        bottom.append(self.actions)
        body.append(self.progress)
        body.append(bottom)
        self.set_body(body)
        file_drop_target(body, lambda ps: self.input_row.set_path(ps[0]))
        self._mode_changed()
        self._update_sensitivity()

    # -- input --

    def _input_changed(self, path: str) -> None:
        self.media = None
        self.info_label.set_label("")
        self._update_sensitivity()
        if not path or not Path(path).is_file():
            return
        if self.probe_worker is not None and self.probe_worker.running:
            self.probe_worker.cancel()

        def work(_p, cancel):
            return vt.probe(path, cancel=cancel)

        def done(info: vt.MediaInfo):
            if self.input_row.path != path:
                return
            self.set_media(info)

        self.probe_worker = self.run(work, done, on_error=lambda e: self.info_label.set_label(f"Cannot read: {e}"))

    def set_media(self, info: vt.MediaInfo) -> None:
        self.media = info
        text = info.summary().replace("\n", "  ·  ")
        if info.size:
            text += f"  ·  {human_size(info.size)}"
        self.info_label.set_label(text)
        d = max(0.001, info.duration)
        for key, p in self.points.items():
            p["scale"].get_adjustment().configure(0, 0, d, 0.01, 1, 0)
            p["field"].get_adjustment().configure(0, 0, d, 0.1, 1, 0)
            self.set_point(key, 0.0 if key == "start" else d)
        self._suggest_output_into_row()
        self._update_sensitivity()

    def _suggest_output(self) -> str:
        src = self.input_row.path
        if not src:
            return ""
        p = Path(src)
        ext = p.suffix if self.stream_copy.get_active() else TRIM_EXT[dropdown_value(self.codec_dd)]
        return str(unique_output(p.with_name(f"{p.stem}-trimmed{ext}")))

    def _suggest_output_into_row(self) -> None:
        # only replace an output path we suggested ourselves
        if not self.output_row.path or self.output_row.path == self._auto_output:
            self._auto_output = self._suggest_output()
            self.output_row.set_path(self._auto_output)

    # -- start / end --

    @property
    def start(self) -> float:
        return float(self.points["start"]["field"].get_value())

    @property
    def end(self) -> float:
        return float(self.points["end"]["field"].get_value())

    def set_point(self, key: str, t: float) -> None:
        p = self.points[key]
        if abs(p["scale"].get_value() - t) > 1e-6:
            p["scale"].set_value(t)
        if abs(p["field"].get_value() - t) > 1e-6:
            p["field"].set_value(t)
        p["label"].set_label(format_seconds(t))
        self.duration_label.set_label(f"Selected duration: {format_seconds(max(0.0, self.end - self.start))}")
        self._queue_preview(key)
        self._update_sensitivity()

    def _scale_moved(self, key: str, value: float) -> None:
        self.set_point(key, value)

    def _field_changed(self, key: str, value: float) -> None:
        self.set_point(key, value)

    def _queue_preview(self, key: str) -> None:
        if self.media is None:
            return
        old = self._preview_timers.pop(key, 0)
        if old:
            GLib.source_remove(old)
        self._preview_timers[key] = GLib.timeout_add(200, self._load_preview, key)

    def _load_preview(self, key: str) -> bool:
        self._preview_timers.pop(key, None)
        media = self.input_row.path
        t = self.start if key == "start" else max(0.0, self.end - 0.04)
        prev = self._preview_workers.get(key)
        if prev is not None and prev.running:
            prev.cancel()

        def done(img):
            self.points[key]["picture"].set_paintable(texture_from_pil(img))
            self.points[key]["image"] = img

        self._preview_workers[key] = self.run(lambda _p, c: grab_frame(media, t, c), done,
                                              on_error=lambda e: self.points[key]["picture"].set_paintable(None))
        return GLib.SOURCE_REMOVE

    def on_closed(self) -> None:
        for tid in self._preview_timers.values():
            GLib.source_remove(tid)
        self._preview_timers.clear()

    # -- options --

    def _mode_changed(self) -> None:
        for w in self.encode_options:
            w.set_sensitive(self.reencode.get_active())
        if self.media is not None:
            self._suggest_output_into_row()

    def _update_sensitivity(self) -> None:
        busy = self.worker is not None and self.worker.running
        ok = self.media is not None and self.end > self.start
        self.trim_button.set_sensitive(busy or ok)

    def options(self) -> vt.TrimOptions:
        end = self.end
        # at the end of the clip: no -t, copy to the end
        end_opt = None if self.media is not None and end >= self.media.duration - 1e-3 else end
        return vt.TrimOptions(self.input_row.path, self.output_row.path, self.start, end_opt,
                              self.stream_copy.get_active(), dropdown_value(self.codec_dd), int(self.crf.get_value()))

    # -- trimming --

    def toggle_trim(self) -> None:
        if self.worker is not None and self.worker.running:
            self.worker.cancel()
            return
        opts = self.options()
        if not opts.output_path:
            self.error("No output file", "Choose where to save the trimmed video.")
            return
        if os.path.abspath(opts.output_path) == os.path.abspath(opts.input_path):
            self.error("Output file", "The output file must be different from the input file.")
            return
        try:
            vt.build_trim_args(opts)
        except ValueError as e:
            self.error("Invalid range", e)
            return
        self.progress_log = []
        self.actions.set_outputs([])
        total = max(0.0, self.media.duration - opts.start) if opts.end is None and self.media is not None else None

        def work(progress, cancel):
            if total is not None:  # skip the second probe: we know the length
                args = vt.build_trim_args(opts)
                Path(opts.output_path).parent.mkdir(parents=True, exist_ok=True)
                vt.run_ffmpeg(args, total, progress, cancel, output=opts.output_path)
                return opts.output_path
            return str(vt.trim_video(opts, progress, cancel))

        def done(path):
            self.actions.set_outputs([path])
            self.progress.set_status(f"Saved {os.path.basename(path)}")
            self._auto_output = ""

        self.trim_button.set_label("Stop")
        self.worker = self.run(work, done, panel=self.progress, status="Trimming…",
                               on_progress=lambda d, t: self.progress_log.append((d, t)),
                               on_finally=self._finished)

    def _finished(self) -> None:
        self.trim_button.set_label("Trim")
        self._update_sensitivity()


def open(app, ctx) -> VideoTrimmerWindow:  # noqa: A001 - the tool contract's name
    files = list(getattr(ctx, "files", None) or [])
    win = single("videotrimmer", lambda: VideoTrimmerWindow(app, ctx))
    if files:
        win.input_row.set_path(files[0])
    return win
