"""Recording pipelines: builders, and real GStreamer/ffmpeg runs on a live test source."""
import json
import subprocess
import time

import pytest

from sharel.core.config import FFmpegOptions
from sharel.core.enums import FFmpegAudioCodec, FFmpegPaletteUseDither, FFmpegVideoCodec
from sharel.record import pipeline as P
from sharel.record.recorder import Recorder


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def video_stream(info):
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def opts(codec):
    o = FFmpegOptions()
    o.video_codec = FFmpegVideoCodec[codec]
    return o


def test_plan_maps_sharex_codecs():
    assert P.plan(opts("libx264")).gst_video.startswith("x264enc speed-preset=ultrafast tune=zerolatency")
    assert P.plan(opts("libx264")).mux == "mp4mux"
    assert P.plan(opts("libvpx")).mux == "webmmux" and "vp8enc" in P.plan(opts("libvpx")).gst_video
    assert "vp9enc" in P.plan(opts("libvpx_vp9")).gst_video
    gif = P.plan(opts("gif"))
    assert gif.extension == "gif" and gif.record_extension == "mkv"
    webp = P.plan(opts("libwebp"))
    assert webp.post_ffmpeg[:2] == ["-c:v", "libwebp"] and webp.extension == "webp"
    o = opts("libx264")
    o.x264_use_bitrate, o.x264_bitrate = True, 5000
    assert "bitrate=5000" in P.plan(o).gst_video


def test_hardware_fallback_and_detection():
    hw = P.plan(opts("h264_nvenc"), available=lambda el: el == "nvh264enc")
    assert hw.hardware == "nvenc" and hw.gst_video.startswith("nvh264enc")
    fb = P.plan(opts("h264_qsv"), available=lambda el: False)
    assert fb.codec == "libx264" and "not available" in fb.fallback_note
    va = P.plan(opts("hevc_vaapi"), available=lambda el: el == "vah265enc")
    assert va.hardware == "vaapi" and "h265parse" in va.gst_video


def test_audio_chain_and_launch():
    o = opts("libx264")
    o.audio_codec = FFmpegAudioCodec.libopus
    o.audio_source = "alsa_input.test"
    ep = P.plan(o)
    launch = P.build_launch(P.video_source(42), ep, "/tmp/x.mp4", 30, width=640, height=480,
                            audio_source=o.audio_source, o=o)
    assert launch.startswith("pipewiresrc name=pwsrc path=42 do-timestamp=true keepalive-time=1000 resend-last=true")
    assert "video/x-raw,max-framerate=30/1 ! videoconvert" in launch and "videorate" not in launch
    assert 'pulsesrc device="alsa_input.test"' in launch and "opusenc" in launch and "mux." in launch
    assert P.audio_fits("webmmux", "aac") is False


def test_gif_commands_two_pass():
    o = FFmpegOptions()
    o.gif_dither = FFmpegPaletteUseDither.bayer
    o.gif_bayer_scale = 3
    first, second = P.gif_commands("in.mkv", "out.gif", "pal.png", 15, o)
    assert "palettegen=stats_mode=full" in " ".join(first) and first[-1] == "pal.png"
    assert "paletteuse=dither=bayer:bayer_scale=3" in " ".join(second) and second[-1] == "out.gif"
    assert P.even(641) == 640 and P.even(1) == 2


def _record(tmp_path, codec, seconds=2.0, gif=False, ext="mp4"):
    out = str(tmp_path / f"rec.{'gif' if gif else ext}")
    rec = Recorder((0, 0, 321, 241), out, opts(codec), fps=15, gif=gif, gif_fps=10, test_pattern=True)
    rec.start()
    time.sleep(seconds)
    path = rec.stop()
    return path, rec


@pytest.mark.parametrize("codec,ext,expect", [("libx264", "mp4", "h264"), ("libvpx", "webm", "vp8"),
                                              ("libvpx_vp9", "webm", "vp9"), ("libx265", "mp4", "hevc")])
def test_real_recording_each_codec(tmp_path, codec, ext, expect):
    path, rec = _record(tmp_path, codec, 2.0, ext=ext)
    info = probe(path)
    v = video_stream(info)
    assert v["codec_name"] == expect
    assert (v["width"], v["height"]) == (320, 240)  # made even
    assert 1.3 <= float(info["format"]["duration"]) <= 3.5
    assert rec.state == "done"


def test_real_gif_recording(tmp_path):
    path, _rec = _record(tmp_path, "libx264", 2.0, gif=True)
    info = probe(path)
    v = video_stream(info)
    assert v["codec_name"] == "gif" and (v["width"], v["height"]) == (320, 240)
    assert 1.2 <= float(info["format"]["duration"]) <= 3.5


def test_pause_excludes_paused_time_and_abort_deletes(tmp_path):
    out = str(tmp_path / "p.mp4")
    rec = Recorder((0, 0, 320, 240), out, opts("libx264"), fps=15, test_pattern=True)
    rec.start()
    time.sleep(1.0)
    rec.pause()
    time.sleep(1.5)
    rec.resume()
    time.sleep(1.0)
    assert 1.7 <= rec.elapsed <= 2.6
    rec.stop()
    assert float(probe(out)["format"]["duration"]) < 3.2
    out2 = str(tmp_path / "a.mp4")
    rec2 = Recorder((0, 0, 320, 240), out2, opts("libx264"), fps=15, test_pattern=True)
    rec2.start()
    time.sleep(0.5)
    rec2.abort()
    import os
    assert rec2.state == "aborted" and not os.path.exists(out2)


def test_ffmpeg_converted_output(tmp_path):
    path, _rec = _record(tmp_path, "libwebp", 1.5, ext="webp")
    assert path.endswith(".webp")
    with open(path, "rb") as fh:
        head = fh.read(16)
    assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"


@pytest.mark.parametrize("via_resume", [True, False])
def test_stop_while_paused_finishes_promptly(tmp_path, via_resume):
    """Pause drops buffers at a valve; stopping while paused must still get EOS through (no hang until
    the timeout) and leave a valid file of recorded minus paused time."""
    out = str(tmp_path / "sp.mp4")
    rec = Recorder((0, 0, 320, 240), out, opts("libx264"), fps=15, test_pattern=True)
    rec.start()
    time.sleep(1.5)
    rec.pause()
    time.sleep(2.0)
    if not via_resume:
        # the valves alone (no resume first) must forward EOS: their drop mode passes sticky events
        rec.resume = lambda: None
    t = time.monotonic()
    assert rec.stop(timeout=10) == out
    assert time.monotonic() - t < 3.0, "stop() waited for the EOS timeout"
    dur = float(probe(out)["format"]["duration"])
    assert 1.0 <= dur <= 2.2, f"{dur:.2f} s: the 2 s pause must not be in the file"
    assert video_stream(probe(out))["width"] == 320
