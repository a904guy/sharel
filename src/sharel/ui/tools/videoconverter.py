"""Video converter window (ShareX VideoConverterForm).

ShareX's controls: input file (+ Browse, drag and drop), output folder (+ Browse), output file name (the
extension follows the encoder), video encoder, quality slider (CRF/q, "lower quality/size ← → higher
quality/size", hidden for codecs without one), "Use bitrate" + bitrate kbit/s (the only setting for the
hardware encoders), "Open folder after encoding", "Use custom arguments" + the FFmpeg arguments box (shows the
generated arguments, editable when custom), Encode / Stop encoding with a progress bar.
ShareL adds: Cancel next to the progress bar, audio bitrate (Opus), resize options, the full command line,
Open / Show in folder / Upload of the result.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from ...tools import videotools as vt
from ._base import ProgressPanel, ToolWindow, button, dropdown, file_drop_target, set_text, single, spin, text_of, \
    text_view
from ._videocommon import OutputActions, PathRow, codec_from_setting

CODECS = list(vt.ConverterCodec)
CODEC_LABELS = {
    "x264": "H.264 / x264", "x265": "H.265 / x265", "h264_nvenc": "H.264 / NVENC", "hevc_nvenc": "H.265 / NVENC",
    "h264_amf": "H.264 / AMF", "hevc_amf": "H.265 / AMF", "h264_qsv": "H.264 / Quick Sync",
    "hevc_qsv": "H.265 / Quick Sync", "vp8": "VP8", "vp9": "VP9", "av1": "AV1", "xvid": "MPEG-4 / Xvid",
    "gif": "GIF", "webp": "WebP", "apng": "APNG", "mp3": "MP3 (audio only)", "opus": "Opus (audio only)"}
QUALITY_CODECS = {"x264", "x265", "vp8", "vp9", "av1", "xvid", "mp3"}
BITRATE_ONLY = {"h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv"}


class VideoConverterWindow(ToolWindow):
    def __init__(self, app, ctx) -> None:
        super().__init__(app, "Video converter", 720, 760, scroll=True)
        self.ctx = ctx
        tool = ctx.task_settings.tool_settings
        self.opts = vt.VideoConverterOptions(codec=codec_from_setting(tool.video_converter_codec),
                                             video_quality=tool.video_converter_crf)
        self.auto_open_folder = True
        self.progress_log: list[tuple[int, int]] = []
        self.worker = None
        self.output_path = ""
        self._ready = False

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.input_row = PathRow("Input file path:", "open", on_changed=self._input_changed,
                                 placeholder="Video file (or drop one here)")
        self.folder_row = PathRow("Output folder:", "folder", on_changed=lambda _p: self.update_options())
        self.name_row = PathRow("Output file name:", "save", on_changed=lambda _p: self.update_options())
        self.name_row.remove(self.name_row.browse)  # a name, not a path: no browse button (like ShareX)
        self.ext_label = Gtk.Label(xalign=0)
        self.ext_label.add_css_class("dim-label")
        self.name_row.append(self.ext_label)
        for r in (self.input_row, self.folder_row, self.name_row):
            body.append(r)

        enc = Gtk.Box(spacing=6)
        enc.append(Gtk.Label(label="Video encoder:", xalign=0, width_chars=16))
        self.codec_dd = dropdown([CODEC_LABELS.get(c.value, c.value) for c in CODECS], CODECS.index(self.opts.codec),
                                 "Video encoder")
        self.codec_dd.connect("notify::selected", lambda *_: self.update_options())
        enc.append(self.codec_dd)
        body.append(enc)

        # quality
        self.quality_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        qrow = Gtk.Box(spacing=6)
        qrow.append(Gtk.Label(label="Video quality:", xalign=0, width_chars=16))
        self.quality = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 51, 1)
        self.quality.set_hexpand(True)
        self.quality.set_inverted(True)  # left = higher CRF = lower quality/size, as in ShareX
        self.quality.set_draw_value(False)
        self.quality.set_tooltip_text("Video quality (CRF)")
        self.quality.set_value(self.opts.video_quality)
        self.quality.connect("value-changed", lambda *_: self.update_options())
        self.quality_value = Gtk.Label(width_chars=4)
        qrow.append(self.quality)
        qrow.append(self.quality_value)
        self.quality_box.append(qrow)
        hints = Gtk.Box(spacing=6)
        self.lower_label = Gtk.Label(xalign=0, hexpand=True)
        self.higher_label = Gtk.Label(xalign=1)
        for lab in (self.lower_label, self.higher_label):
            lab.add_css_class("dim-label")
            hints.append(lab)
        self.quality_box.append(hints)
        body.append(self.quality_box)

        brow = Gtk.Box(spacing=6)
        self.use_bitrate = Gtk.CheckButton(label="Use bitrate", active=self.opts.use_bitrate)
        self.use_bitrate.connect("toggled", lambda *_: self.update_options())
        self.bitrate = spin(self.opts.video_bitrate, 1, 1_000_000, 100, tooltip="Video bitrate (kbit/s)")
        self.bitrate.connect("value-changed", lambda *_: self.update_options())
        self.bitrate_hint = Gtk.Label(label="kbit/s")
        brow.append(self.use_bitrate)
        brow.append(self.bitrate)
        brow.append(self.bitrate_hint)
        body.append(brow)

        arow = Gtk.Box(spacing=6)
        self.audio_label = Gtk.Label(label="Audio bitrate:", xalign=0, width_chars=16)
        self.audio_bitrate = spin(self.opts.audio_bitrate, 6, 512, 8, tooltip="Audio bitrate (kbit/s)")
        self.audio_bitrate.connect("value-changed", lambda *_: self.update_options())
        arow.append(self.audio_label)
        arow.append(self.audio_bitrate)
        arow.append(Gtk.Label(label="kbit/s"))
        self.audio_row = arow
        body.append(arow)

        # resize
        exp = Gtk.Expander(label="Resize")
        rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        self.keep_res = Gtk.CheckButton(label="Keep resolution", active=True)
        self.keep_res.connect("toggled", lambda *_: self.update_options())
        rbox.append(self.keep_res)
        srow = Gtk.Box(spacing=6)
        self.scale_w = spin(0, 0, 16384, 2, tooltip="Width (0 = keep aspect ratio)")
        self.scale_h = spin(0, 0, 16384, 2, tooltip="Height (0 = keep aspect ratio)")
        self.scale_pct = spin(0, 0, 1000, 5, tooltip="Scale percentage (0 = use width/height)")
        for lab, w in (("Width:", self.scale_w), ("Height:", self.scale_h), ("or %:", self.scale_pct)):
            srow.append(Gtk.Label(label=lab))
            srow.append(w)
            w.connect("value-changed", lambda *_: self.update_options())
        self.size_row = srow
        rbox.append(srow)
        exp.set_child(rbox)
        body.append(exp)

        self.auto_open = Gtk.CheckButton(label="Open folder after encoding", active=self.auto_open_folder)
        self.auto_open.connect("toggled", lambda b: setattr(self, "auto_open_folder", b.get_active()))
        body.append(self.auto_open)
        self.use_custom = Gtk.CheckButton(label="Use custom arguments")
        self.use_custom.connect("toggled", lambda *_: self._custom_toggled())
        body.append(self.use_custom)
        self.args_view, sw = text_view(monospace=True, height=80)
        self.args_view.set_tooltip_text("FFmpeg arguments ($input and $output are replaced)")
        self.args_view.get_buffer().connect("changed", lambda *_: self._args_edited())
        body.append(sw)
        self.command_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.command_label.add_css_class("monospace")
        self.command_label.add_css_class("dim-label")
        body.append(self.command_label)

        self.encode_button = button("Encode", self.toggle_encode, "Start encoding", css="suggested-action")
        self.progress = ProgressPanel()
        self.actions = OutputActions(app, lambda: self.ctx.task_settings)
        bottom = Gtk.Box(spacing=6)
        bottom.append(self.encode_button)
        bottom.append(self.actions)
        body.append(self.progress)
        body.append(bottom)
        self.set_body(body)
        file_drop_target(body, lambda ps: self.set_input(ps[0]))
        self._ready = True
        self.update_options()

    # -- options --

    @property
    def codec(self) -> vt.ConverterCodec:
        return CODECS[self.codec_dd.get_selected()]

    def set_input(self, path: str) -> None:
        self.input_row.set_path(path)

    def _input_changed(self, path: str) -> None:
        # ShareX UpdateInputFilePathTextBox: fill the output folder/name when empty
        if path:
            if not self.folder_row.path:
                self.folder_row.set_path(os.path.dirname(os.path.abspath(path)))
            if not self.name_row.path:
                self.name_row.set_path(Path(path).stem + "-output")
        self.update_options()

    def _custom_toggled(self) -> None:
        if self.use_custom.get_active() and not self.opts.custom_arguments:
            # start from the generated arguments, with placeholders for the paths
            text = text_of(self.args_view)
            if self.opts.input_path:
                text = text.replace(shlex.quote(self.opts.input_path), "$input")
            if self.opts.output_path:
                text = text.replace(shlex.quote(self.opts.output_path), "$output")
            self.opts.custom_arguments = text
            set_text(self.args_view, text)
        self.update_options()

    def _args_edited(self) -> None:
        if self._ready and self.use_custom.get_active():
            self.opts.custom_arguments = text_of(self.args_view)
            self._refresh_command()

    def update_options(self) -> None:
        if not self._ready:
            return
        o = self.opts
        o.input_path = self.input_row.path
        o.output_folder = self.folder_row.path
        o.output_name = self.name_row.path
        o.codec = self.codec
        o.use_bitrate = self.use_bitrate.get_active()
        o.video_bitrate = int(self.bitrate.get_value())
        o.audio_bitrate = int(self.audio_bitrate.get_value())
        o.keep_resolution = self.keep_res.get_active()
        o.scale_width = int(self.scale_w.get_value())
        o.scale_height = int(self.scale_h.get_value())
        o.scale_percent = float(self.scale_pct.get_value())
        o.use_custom_arguments = self.use_custom.get_active()
        c = o.codec.value
        self.ext_label.set_label("" if os.path.splitext(o.output_name)[1] else "." + vt.converter_extension(c))

        has_quality = c in QUALITY_CODECS
        self.use_bitrate.set_visible(has_quality)
        self.quality_box.set_visible(has_quality and not o.use_bitrate)
        show_bitrate = c in BITRATE_ONLY or (has_quality and o.use_bitrate)
        self.bitrate.set_visible(show_bitrate)
        self.bitrate_hint.set_visible(show_bitrate)
        self.audio_row.set_visible(c == "opus")
        self.size_row.set_sensitive(not o.keep_resolution)
        if has_quality:
            lo, hi = vt.QUALITY_RANGES[c]
            adj = self.quality.get_adjustment()
            if (adj.get_lower(), adj.get_upper()) != (lo, hi):
                adj.set_lower(lo)
                adj.set_upper(hi)
                self.quality.set_value(max(lo, min(hi, o.video_quality)))
            o.video_quality = int(self.quality.get_value())
            self.quality_value.set_label(str(o.video_quality))
            self.lower_label.set_label(f"{hi}   ← Lower quality/size")
            self.higher_label.set_label(f"Higher quality/size →   {lo}")

        self.args_view.set_editable(o.use_custom_arguments)
        self.args_view.set_sensitive(o.use_custom_arguments)
        if not o.use_custom_arguments:
            try:
                set_text(self.args_view, shlex.join(vt.build_converter_args(o)))
            except ValueError:
                set_text(self.args_view, "")
        self._refresh_command()
        busy = self.worker is not None and self.worker.running
        self.encode_button.set_sensitive(busy or bool(o.input_path and o.output_folder and o.output_name))

    def _refresh_command(self) -> None:
        try:
            self.command_label.set_label("ffmpeg " + shlex.join(vt.build_converter_args(self.opts)))
        except ValueError:
            self.command_label.set_label("")

    # -- encoding --

    def toggle_encode(self) -> None:
        if self.worker is not None and self.worker.running:
            self.worker.cancel()
            return
        self.update_options()
        o = self.opts
        if not Path(o.input_path).is_file():
            self.error("Input file not found", o.input_path)
            return
        try:
            vt.build_converter_args(o)
        except ValueError as e:
            self.error("Invalid options", e)
            return
        self._remember()
        self.output_path = o.output_path
        self.progress_log = []
        self.actions.set_outputs([])
        import copy
        opts = copy.deepcopy(o)

        def work(progress, cancel):
            return str(vt.convert_video(opts, progress, cancel))

        def done(path):
            self.actions.set_outputs([path])
            self.progress.set_status(f"Saved {os.path.basename(path)}")
            if self.auto_open_folder:
                from ...core import desktop
                desktop.show_in_folder(path)

        self.encode_button.set_label("Stop encoding")
        self.worker = self.run(work, done, panel=self.progress, status="Encoding…",
                               on_progress=lambda d, t: self.progress_log.append((d, t)),
                               on_cancelled=lambda: self.progress.set_fraction(0.0),
                               on_finally=self._finished)

    def _finished(self) -> None:
        self.encode_button.set_label("Encode")
        self.update_options()

    def _remember(self) -> None:
        """Keep the encoder and quality for next time (ShareX keeps VideoConverterOptions)."""
        tool = self.app.store.app.default_task_settings.tool_settings
        tool.video_converter_codec = self.opts.codec.value
        tool.video_converter_crf = int(self.opts.video_quality)
        self.app.store.save_app()


def open(app, ctx) -> VideoConverterWindow:  # noqa: A001 - the tool contract's name
    files = list(getattr(ctx, "files", None) or [])
    win = single("videoconverter", lambda: VideoConverterWindow(app, ctx))
    if files:
        win.set_input(files[0])
    return win
