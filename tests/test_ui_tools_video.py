"""Video tool windows: converter, trimmer, thumbnailer, GIF trimmer — real FFmpeg on a 1 s lavfi clip."""
import subprocess

import pytest
from PIL import Image
from toolhelpers import ctx_for, wait_until
from uihelpers import button_with_label, run_app

from sharel.core.enums import HotkeyType


@pytest.fixture
def clip(tmp_path):
    out = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=1:size=320x240:rate=25", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-y", str(out)],
                   check=True, timeout=60)
    return out


def _reached_100(log):
    assert log, "no progress reported"
    done, total = log[-1]
    assert total > 0 and done == total, log
    return True


def test_video_converter_encodes_with_progress(clip, tmp_path, monkeypatch):
    from sharel.core import desktop
    shown = []
    monkeypatch.setattr(desktop, "show_in_folder", lambda p: shown.append(p))
    uploads = []

    def body(app):
        from sharel.ui.tools import videoconverter as vc
        monkeypatch.setattr(app.tasks, "upload_file", lambda p, ts: uploads.append(p))
        win = vc.open(app, ctx_for(app, HotkeyType.VideoConverter, [str(clip)]))
        assert vc.open(app, ctx_for(app, HotkeyType.VideoConverter)) is win
        yield 0.1
        # ShareX fills the output folder and "<name>-output"
        assert win.folder_row.path == str(tmp_path) and win.name_row.path == "clip-output"
        assert win.codec.value == "x264" and win.opts.video_quality == 23
        assert "libx264" in win.command_label.get_label() and "-crf 23" in win.command_label.get_label()
        # hardware encoder: bitrate only
        win.codec_dd.set_selected(vc.CODECS.index(vc.vt.ConverterCodec.h264_nvenc))
        assert not win.quality_box.get_visible() and win.bitrate.get_visible()
        # VP9 with a quality of 40
        win.codec_dd.set_selected(vc.CODECS.index(vc.vt.ConverterCodec.vp9))
        assert win.quality_box.get_visible() and win.quality.get_adjustment().get_upper() == 63
        win.quality.set_value(40)
        assert "-crf 40" in win.command_label.get_label() and win.ext_label.get_label() == ".webm"
        win.use_bitrate.set_active(True)
        assert not win.quality_box.get_visible() and "-b:v 3000k" in win.command_label.get_label()
        win.use_bitrate.set_active(False)
        win.codec_dd.set_selected(vc.CODECS.index(vc.vt.ConverterCodec.x264))
        win.keep_res.set_active(False)
        win.scale_w.set_value(160)
        assert "scale=160:-2" in win.command_label.get_label()
        assert win.encode_button.get_sensitive()
        win.encode_button.emit("clicked")
        assert win.encode_button.get_label() == "Stop encoding"
        yield from wait_until(lambda: not win.worker.running, 60)
        out = tmp_path / "clip-output.mp4"
        assert win.worker.error is None and out.is_file() and out.stat().st_size > 0
        assert _reached_100(win.progress_log) and win.progress.fraction == 1.0
        assert shown == [str(out)]  # "Open folder after encoding"
        assert win.encode_button.get_label() == "Encode"
        assert app.store.app.default_task_settings.tool_settings.video_converter_codec == "x264"
        button_with_label(win, "Upload").emit("clicked")
        assert uploads == [str(out)]
        from sharel.tools.videotools import probe
        assert probe(out).width == 160
        return True
    assert run_app(body, timeout=90)


def test_video_converter_custom_arguments_and_cancel(clip, tmp_path, monkeypatch):
    def body(app):
        from sharel.ui.tools import videoconverter as vc
        win = vc.open(app, ctx_for(app, HotkeyType.VideoConverter, [str(clip)]))
        win.auto_open.set_active(False)
        win.use_custom.set_active(True)
        text = vc.text_of(win.args_view)
        assert "$input" in text and "$output" in text
        win.args_view.get_buffer().set_text("-i $input -c:v libx264 -preset ultrafast -an -y $output")
        assert win.command_label.get_label().startswith("ffmpeg -i " + str(clip))
        win.encode_button.emit("clicked")
        yield from wait_until(lambda: not win.worker.running, 60)
        assert (tmp_path / "clip-output.mp4").is_file() and _reached_100(win.progress_log)
        # cancel a slow encode: the partial output is removed
        win.use_custom.set_active(False)
        win.name_row.set_path("slow")
        win.codec_dd.set_selected(vc.CODECS.index(vc.vt.ConverterCodec.av1))
        win.encode_button.emit("clicked")
        yield 0.05
        win.encode_button.emit("clicked")  # Stop encoding
        yield from wait_until(lambda: not win.worker.running, 30)
        assert win.worker.cancelled and not (tmp_path / "slow.mkv").exists()
        return True
    assert run_app(body, timeout=90)


def test_video_trimmer_trims_with_preview(clip, tmp_path):
    def body(app):
        from sharel.ui.tools import videotrimmer as vtw
        win = vtw.open(app, ctx_for(app, HotkeyType.VideoTrimmer, [str(clip)]))
        yield from wait_until(lambda: win.media is not None, 30)
        assert abs(win.end - win.media.duration) < 1e-6 and win.start == 0
        assert win.output_row.path == str(tmp_path / "clip-trimmed.mp4")
        win.points["start"]["scale"].set_value(0.2)
        win.points["end"]["field"].set_value(0.8)
        assert abs(win.start - 0.2) < 1e-6 and abs(win.end - 0.8) < 1e-6
        yield from wait_until(lambda: "image" in win.points["start"] and "image" in win.points["end"], 30)
        assert win.points["start"]["picture"].get_paintable() is not None
        # re-encode for a frame-accurate cut
        win.reencode.set_active(True)
        assert win.codec_dd.get_sensitive()
        win.trim_button.emit("clicked")
        yield from wait_until(lambda: not win.worker.running, 60)
        out = tmp_path / "clip-trimmed.mp4"
        assert win.worker.error is None and out.is_file()
        assert _reached_100(win.progress_log)
        from sharel.tools.videotools import probe
        assert 0.4 < probe(out).duration < 0.8
        assert win.actions.outputs == [str(out)] and win.actions.open_button.get_sensitive()
        # stream copy to the end of the clip (no end given)
        win.stream_copy.set_active(True)
        win.points["end"]["field"].set_value(win.media.duration)
        win.output_row.set_path(str(tmp_path / "copy.mp4"))
        win.trim_button.emit("clicked")
        yield from wait_until(lambda: not win.worker.running, 60)
        assert (tmp_path / "copy.mp4").is_file() and _reached_100(win.progress_log)
        return True
    assert run_app(body, timeout=90)


def test_video_thumbnailer_makes_sheet_and_uploads(clip, tmp_path, monkeypatch):
    uploads = []
    copied = []
    from sharel.core import clipboard
    monkeypatch.setattr(clipboard, "set_image", lambda img: copied.append(img.size))

    def body(app):
        from sharel.ui.tools import videothumbnailer as vth
        monkeypatch.setattr(app.tasks, "upload_file", lambda p, ts: uploads.append(p))
        win = vth.open(app, ctx_for(app, HotkeyType.VideoThumbnailer, [str(clip)]))
        assert win.options.thumbnail_count == 9 and win.options.column_count == 3
        win.options.thumbnail_count = 4
        win.options.column_count = 2
        win.options.max_thumbnail_width = 160
        win.location_dd.set_selected(2)
        out_dir = tmp_path / "thumbs"
        win.custom_folder.set_text(str(out_dir))
        win.start_button.emit("clicked")
        yield from wait_until(lambda: not win.worker.running, 60)
        assert win.worker.error is None, win.worker.error
        sheet = out_dir / "clip_Thumbnail.png"
        assert sheet.is_file()
        assert win.progress_log[-1] == (4, 4)
        assert win.picture.get_paintable() is not None
        with Image.open(sheet) as im:
            assert win.preview_image.size == im.size
            assert im.width == 10 * 2 + 160 * 2 + 10  # padding, 2 columns, spacing
        assert uploads == [str(sheet)]  # "Upload thumbnails" is on by default (ShareX)
        button_with_label(win, "Copy image").emit("clicked")
        assert copied == [win.preview_image.size]
        assert app.store.app.default_task_settings.tool_settings.video_thumbnailer_count == 4
        return True
    assert run_app(body, timeout=90)


def _make_gif(path, n=6):
    frames = [Image.new("RGB", (40, 30), (i * 40, 255 - i * 40, 0)) for i in range(n)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100 + 10 * i for i in range(n)], loop=0)


def test_gif_trimmer_trims_frames(tmp_path):
    src = tmp_path / "anim.gif"
    _make_gif(src)

    def body(app):
        from sharel.tools.videotools import gif_info
        from sharel.ui.tools import giftrimmer as gt
        win = gt.open(app, ctx_for(app, HotkeyType.AnimatedGifTrimmer, [str(src)]))
        yield from wait_until(lambda: win.info is not None, 15)
        assert len(win.frames) == 6 and win.end_frame == 5 and win.start_frame == 0
        assert "6 frames" in win.info_label.get_label()
        win.controls["start"][0].set_value(2)
        assert win.start_frame == 2 and win.current == 2
        win.step("end", -1)
        assert win.end_frame == 4 and win.current == 4
        assert "3 frame(s)" in win.range_label.get_label()
        win.play_button.emit("clicked")
        yield 0.35
        assert win.play_button.get_label() == "Stop" and 2 <= win.current <= 4
        win.play_button.emit("clicked")
        assert win._play_id == 0
        assert win.output_row.path == str(tmp_path / "anim-trimmed.gif")
        win.trim_button.emit("clicked")
        yield from wait_until(lambda: not win.worker.running, 15)
        out = tmp_path / "anim-trimmed.gif"
        info = gif_info(out)
        assert info.frames == 3 and info.durations == [120, 130, 140] and info.loop == 0
        assert win.progress_log[-1] == (1, 1)
        # start after end: Trim is disabled
        win.controls["start"][1].set_value(5)
        win.controls["end"][1].set_value(1)
        assert not win.trim_button.get_sensitive()
        return True
    assert run_app(body, timeout=60)
