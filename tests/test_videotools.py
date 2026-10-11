"""FFmpeg argument builders, real runs on a lavfi test clip, thumbnails and the GIF trimmer."""
from __future__ import annotations

import random
import subprocess
import threading

import pytest
from PIL import Image

from sharel.tools._common import OperationCancelled, ToolError
from sharel.tools import videotools as vt


@pytest.fixture(scope="session")
def clip(tmp_path_factory):
    """2 s, 128x96 @ 10 fps H.264 + 440 Hz AAC."""
    p = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=2:size=128x96:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(p)], check=True)
    return p


@pytest.fixture(scope="session")
def long_clip(tmp_path_factory):
    p = tmp_path_factory.mktemp("media") / "long.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=60:size=640x480:rate=30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", str(p)], check=True)
    return p


def opts(tmp_path, codec, **kw):
    return vt.VideoConverterOptions(input_path="/in/clip.mp4", output_folder=str(tmp_path), output_name="out",
                                    codec=codec, **kw)


# ---- probe ---------------------------------------------------------------------------------

def test_probe(clip):
    info = vt.probe(clip)
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert (info.video_codec, info.width, info.height) == ("h264", 128, 96)
    assert info.fps == pytest.approx(10)
    assert info.audio_codec == "aac" and info.has_audio and info.size == clip.stat().st_size
    s = info.summary()
    assert s.splitlines()[0] == "Filename: clip.mp4"
    assert "Video: h264, 128x96, 10 fps" in s and "Duration: 00:00:02" in s
    with pytest.raises(ToolError):
        vt.probe(clip.with_name("missing.mp4"))


# ---- converter argv ------------------------------------------------------------------------

def test_converter_args_match_sharex(tmp_path):
    out = str(tmp_path / "out")
    assert vt.build_converter_args(opts(tmp_path, "x264")) == [
        "-i", "/in/clip.mp4", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k", "-y", out + ".mp4"]
    assert vt.build_converter_args(opts(tmp_path, "x265", use_bitrate=True, video_bitrate=2500)) == [
        "-i", "/in/clip.mp4", "-c:v", "libx265", "-preset", "medium", "-b:v", "2500k", "-tag:v", "hvc1",
        "-c:a", "aac", "-b:a", "128k", "-y", out + ".mp4"]
    assert vt.build_converter_args(opts(tmp_path, "vp8", video_quality=1)) == [
        "-i", "/in/clip.mp4", "-c:v", "libvpx", "-crf", "4", "-b:v", "100M", "-c:a", "libvorbis", "-q:a", "3",
        "-y", out + ".webm"]
    assert vt.build_converter_args(opts(tmp_path, "vp9", video_quality=99)) == [
        "-i", "/in/clip.mp4", "-c:v", "libvpx-vp9", "-crf", "63", "-b:v", "0", "-c:a", "libvorbis", "-q:a", "3",
        "-y", out + ".webm"]
    assert vt.build_converter_args(opts(tmp_path, "av1"))[2:6] == ["-c:v", "libsvtav1", "-crf", "23"]
    assert vt.build_converter_args(opts(tmp_path, "av1"))[-4:] == ["-b:a", "128k", "-y", out + ".mkv"]
    assert vt.build_converter_args(opts(tmp_path, "xvid", video_quality=0)) == [
        "-i", "/in/clip.mp4", "-c:v", "libxvid", "-q:v", "1", "-c:a", "libmp3lame", "-q:a", "4", "-y", out + ".avi"]
    assert vt.build_converter_args(opts(tmp_path, "h264_nvenc")) == [
        "-i", "/in/clip.mp4", "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-profile:v", "high",
        "-b:v", "3000k", "-c:a", "aac", "-b:a", "128k", "-y", out + ".mp4"]
    assert vt.build_converter_args(opts(tmp_path, "hevc_amf"))[2:12] == [
        "-c:v", "hevc_amf", "-usage", "transcoding", "-profile", "main", "-quality", "balanced", "-b:v", "3000k"]
    assert vt.build_converter_args(opts(tmp_path, "h264_qsv"))[2:8] == [
        "-c:v", "h264_qsv", "-preset", "medium", "-b:v", "3000k"]
    assert vt.build_converter_args(opts(tmp_path, "gif")) == [
        "-i", "/in/clip.mp4", "-lavfi",
        "palettegen=stats_mode=full[palette],[0:v][palette]paletteuse=dither=sierra2_4a", "-y", out + ".gif"]
    assert vt.build_converter_args(opts(tmp_path, "webp")) == [
        "-i", "/in/clip.mp4", "-c:v", "libwebp", "-lossless", "0", "-preset", "default", "-loop", "0",
        "-y", out + ".webp"]
    assert vt.build_converter_args(opts(tmp_path, "apng")) == [
        "-i", "/in/clip.mp4", "-f", "apng", "-plays", "0", "-y", out + ".apng"]
    assert vt.build_converter_args(opts(tmp_path, "mp3", video_quality=2)) == [
        "-i", "/in/clip.mp4", "-vn", "-c:a", "libmp3lame", "-q:a", "2", "-y", out + ".mp3"]
    assert vt.build_converter_args(opts(tmp_path, "opus", audio_bitrate=96)) == [
        "-i", "/in/clip.mp4", "-vn", "-c:a", "libopus", "-b:a", "96k", "-y", out + ".opus"]


def test_converter_args_scale_animation_and_custom(tmp_path):
    o = opts(tmp_path, "x264", keep_resolution=False, scale_width=101)
    a = vt.build_converter_args(o)
    assert a[a.index("-vf") + 1] == "scale=100:-2"
    o = opts(tmp_path, "webp", keep_resolution=False, scale_percent=50)
    a = vt.build_converter_args(o)
    assert a[a.index("-vf") + 1] == "scale=round(iw*0.5):round(ih*0.5)"
    g = vt.build_converter_args(opts(tmp_path, "gif", keep_resolution=False, scale_height=48))
    assert "-vf" not in g
    assert g[g.index("-lavfi") + 1].startswith("[0:v]scale=-1:48,split[a][b];[a]palettegen")
    anim = vt.VideoConverterOptions(input_path="/x/a.GIF", output_folder=str(tmp_path), output_name="o",
                                    codec="x264")
    assert "-c:a" not in vt.build_converter_args(anim)  # animation-only input: no audio encoder
    custom = opts(tmp_path, "x264", use_custom_arguments=True, custom_arguments='-i "$input" -c copy "$output"')
    assert vt.build_converter_args(custom) == ["-i", "/in/clip.mp4", "-c", "copy", str(tmp_path / "out.mp4")]
    with pytest.raises(ValueError):
        vt.build_converter_args(vt.VideoConverterOptions(input_path="", output_folder="x", output_name="y"))
    assert vt.converter_extension("vp9") == "webm"


# ---- converter runs ------------------------------------------------------------------------

def run_convert(clip, tmp_path, codec, **kw):
    o = vt.VideoConverterOptions(input_path=str(clip), output_folder=str(tmp_path), output_name=f"o_{codec}",
                                 codec=codec, **kw)
    seen = []
    out = vt.convert_video(o, progress=lambda d, t: seen.append((d, t)))
    assert out.exists() and out.stat().st_size > 0
    assert seen and seen[-1][0] == seen[-1][1] == 2000
    return vt.probe(out)


def test_convert_x264_scaled(clip, tmp_path):
    info = run_convert(clip, tmp_path, "x264", keep_resolution=False, scale_width=64)
    assert (info.video_codec, info.width, info.height) == ("h264", 64, 48)
    assert info.audio_codec == "aac" and info.duration == pytest.approx(2.0, abs=0.15)


@pytest.mark.parametrize("codec,vcodec,fmt", [("gif", "gif", "gif"), ("vp9", "vp9", "webm"),
                                              ("webp", "webp", "webp"), ("apng", "apng", "apng"),
                                              ("x265", "hevc", "mov"), ("av1", "av1", "matroska")])
def test_convert_other_video_codecs(clip, tmp_path, codec, vcodec, fmt):
    o = vt.VideoConverterOptions(input_path=str(clip), output_folder=str(tmp_path), output_name=f"o_{codec}",
                                 codec=codec, video_quality=40 if codec in ("vp9", "av1") else 23)
    out = vt.convert_video(o)
    if codec == "webp":
        # FFmpeg's demuxer can't read animated WebP back; check with Pillow instead
        with Image.open(out) as im:
            assert im.format == "WEBP" and im.size == (128, 96) and im.n_frames == 20
        return
    info = vt.probe(out)
    assert info.video_codec == vcodec and (info.width, info.height) == (128, 96)
    assert fmt in info.format_name


@pytest.mark.parametrize("codec,acodec", [("mp3", "mp3"), ("opus", "opus")])
def test_convert_audio_only(clip, tmp_path, codec, acodec):
    info = run_convert(clip, tmp_path, codec)
    assert info.audio_codec == acodec and not info.has_video
    assert info.duration == pytest.approx(2.0, abs=0.15)


def test_convert_cancel_deletes_partial_output(long_clip, tmp_path):
    o = vt.VideoConverterOptions(input_path=str(long_clip), output_folder=str(tmp_path), output_name="slow",
                                 codec="x265", video_quality=10)
    ev = threading.Event()

    def prog(done, total):
        if done > 0:
            ev.set()

    with pytest.raises(OperationCancelled):
        vt.convert_video(o, progress=prog, cancel=ev)
    assert not (tmp_path / "slow.mp4").exists()


def test_ffmpeg_error_is_reported(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    o = vt.VideoConverterOptions(input_path=str(bad), output_folder=str(tmp_path), output_name="x")
    with pytest.raises(ToolError, match="ffmpeg failed"):
        vt.convert_video(o, duration=1)


# ---- trimmer -------------------------------------------------------------------------------

def test_trim_args():
    t = vt.TrimOptions("/a.mp4", "/b.mp4", start=0.5, end=1.75)
    assert vt.build_trim_args(t) == ["-ss", "0.500", "-i", "/a.mp4", "-t", "1.250", "-map", "0", "-c", "copy",
                                     "-avoid_negative_ts", "make_zero", "-y", "/b.mp4"]
    r = vt.build_trim_args(vt.TrimOptions("/a.mp4", "/b.mp4", end=1.0, stream_copy=False))
    assert r[:4] == ["-i", "/a.mp4", "-t", "1.000"] and "libx264" in r
    with pytest.raises(ValueError):
        vt.build_trim_args(vt.TrimOptions("/a", "/b", start=2, end=1))


def test_trim_runs(clip, tmp_path):
    exact = vt.trim_video(vt.TrimOptions(str(clip), str(tmp_path / "re.mp4"), start=0.5, end=1.5,
                                         stream_copy=False))
    info = vt.probe(exact)
    assert info.duration == pytest.approx(1.0, abs=0.12) and info.width == 128
    seen = []
    copy = vt.trim_video(vt.TrimOptions(str(clip), str(tmp_path / "cp.mp4"), start=0.0, end=1.0),
                         progress=lambda d, t: seen.append((d, t)))
    assert vt.probe(copy).duration == pytest.approx(1.0, abs=0.3)
    assert seen[-1] == (1000, 1000)
    tail = vt.trim_video(vt.TrimOptions(str(clip), str(tmp_path / "tail.mp4"), start=1.0, stream_copy=False))
    assert vt.probe(tail).duration == pytest.approx(1.0, abs=0.15)


# ---- thumbnailer ---------------------------------------------------------------------------

def test_thumbnail_times():
    assert vt.thumbnail_times(10, 4) == [2.0, 4.0, 6.0, 8.0]
    rnd = vt.thumbnail_times(10, 4, random_frame=True, rng=random.Random(1))
    assert len(rnd) == 4 and all(0 < t < 10 for t in rnd) and rnd == sorted(rnd)
    assert vt.thumbnail_times(0, 4) == []
    assert vt.format_timestamp(3725.9) == "01:02:05"


def test_thumbnail_sheet(clip, tmp_path):
    o = vt.VideoThumbnailOptions(thumbnail_count=4, column_count=3)
    seen = []
    r = vt.take_thumbnails(clip, o, tmp_path / "th", progress=lambda d, t: seen.append((d, t)))
    assert r.combined == tmp_path / "th" / "clip_Thumbnail.png" and r.files == [r.combined]
    assert seen[-1] == (4, 4)
    assert r.frames == [] and len(list((tmp_path / "th").iterdir())) == 1  # singles removed
    with Image.open(r.combined) as sheet:
        # ShareX layout: padding*2 + 3 thumbs + 2 spacings wide; 2 rows
        assert sheet.width == 10 * 2 + 128 * 3 + 2 * 10
        assert sheet.height > 10 * 3 + 96 * 2 + 10
        assert sheet.getpixel((2, 2)) == (245, 245, 245)  # WhiteSmoke background
        # last cell of row 2 is empty (4 thumbnails in 3 columns)
        assert sheet.getpixel((sheet.width - 60, sheet.height - 40)) == (245, 245, 245)


def test_thumbnail_singles_and_keep(clip, tmp_path):
    o = vt.VideoThumbnailOptions(thumbnail_count=3, combine_screenshots=False, max_thumbnail_width=64,
                                 image_format="jpg")
    r = vt.take_thumbnails(clip, o, tmp_path)
    assert r.combined is None and len(r.files) == 3
    for f in r.files:
        with Image.open(f) as im:
            assert im.size == (64, 48) and im.format == "JPEG"
    keep = vt.take_thumbnails(clip, vt.VideoThumbnailOptions(thumbnail_count=2, keep_screenshots=True,
                                                            add_video_info=False), tmp_path / "k")
    assert len(keep.frames) == 2 and all(p.exists() for _t, p in keep.frames)
    with Image.open(keep.combined) as sheet:
        assert sheet.size == (10 * 2 + 128 * 3 + 20, 10 * 3 + 96)


# ---- GIF trimmer ---------------------------------------------------------------------------

@pytest.fixture
def gif(tmp_path):
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]
    frames = [Image.new("RGB", (20, 10), c) for c in colors]
    p = tmp_path / "a.gif"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=[100, 200, 300, 400, 500, 600], loop=2)
    return p, colors


def test_gif_info_and_trim(gif, tmp_path):
    p, colors = gif
    info = vt.gif_info(p)
    assert info.frames == 6 and info.durations == [100, 200, 300, 400, 500, 600] and info.loop == 2
    out = vt.trim_gif(p, tmp_path / "t.gif", 1, 3)
    t = vt.gif_info(out)
    assert t.frames == 3 and t.durations == [200, 300, 400] and t.loop == 2 and t.size == (20, 10)
    with Image.open(out) as im:
        got = []
        for i in range(im.n_frames):
            im.seek(i)
            got.append(im.convert("RGB").getpixel((5, 5)))
    assert got == colors[1:4]
    one = vt.gif_info(vt.trim_gif(p, tmp_path / "one.gif", 5, 5))
    assert one.frames == 1 and one.durations == [600]
    with pytest.raises(ValueError):
        vt.trim_gif(p, tmp_path / "x.gif", 4, 9)


def test_gif_trim_by_time_and_no_loop(gif, tmp_path):
    p, _colors = gif
    out = vt.trim_gif_time(p, tmp_path / "t.gif", 250, 1000)  # frames start at 0,100,300,600,1000,1500
    assert vt.gif_info(out).durations == [300, 400]
    once = tmp_path / "once.gif"
    Image.new("RGB", (4, 4), "red").save(once, save_all=True,
                                          append_images=[Image.new("RGB", (4, 4), "blue")], duration=50)
    assert vt.gif_info(once).loop is None
    assert vt.gif_info(vt.trim_gif(once, tmp_path / "o2.gif", 0, 1)).loop is None
    with pytest.raises(ValueError):
        vt.trim_gif_time(p, tmp_path / "n.gif", 5000, 6000)
