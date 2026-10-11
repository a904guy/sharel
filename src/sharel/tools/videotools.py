"""Video tools (ShareX VideoConverter, VideoThumbnailer, video trimmer, animated GIF trimmer) on FFmpeg/ffprobe.

Argument builders are pure functions returning argv lists (no ``ffmpeg`` binary, no progress flags) so the UI
can show them; runners add ``ffmpeg -nostdin -hide_banner -loglevel error -progress pipe:1 -nostats`` in front,
parse the progress stream for ``progress(done_ms, total_ms)`` and kill FFmpeg when ``cancel`` is set (the
partial output file is deleted, ``OperationCancelled`` is raised). A non-zero exit raises ``ToolError`` with
FFmpeg's error text.

Media info
    ``probe(path) -> MediaInfo`` (ffprobe JSON): duration, size, bitrate, container, video codec/size/fps,
    audio codec/sample rate/channels; ``MediaInfo.summary()`` is the header text of the thumbnail sheet.
Converter (ShareX VideoConverterOptions.GetFFmpegArgs, same arguments per codec)
    ``ConverterCodec``: x264, x265, h264_nvenc, hevc_nvenc, h264_amf, hevc_amf, h264_qsv, hevc_qsv, vp8, vp9,
    av1, xvid, gif, webp, apng — plus ShareL's audio-only mp3 and opus.
    ``VideoConverterOptions`` (input_path, output_folder, output_name, codec, video_quality = CRF/q, use_bitrate,
    video_bitrate kb/s, audio_bitrate kb/s for opus/mp3-with-bitrate, keep_resolution / scale_width /
    scale_height / scale_percent, use_custom_arguments + custom_arguments).
    ``build_converter_args(opts) -> list[str]``, ``opts.output_path``, ``converter_extension(codec)``,
    ``convert_video(opts, progress=None, cancel=None) -> Path``.
Trimmer
    ``TrimOptions(input_path, output_path, start=0.0, end=None, stream_copy=True, codec="x264", crf=23)``;
    ``build_trim_args(opts)``; ``trim_video(opts, progress, cancel) -> Path``. Stream copy is instant but cuts on
    key frames; re-encoding is frame accurate.
Thumbnailer (ShareX VideoThumbnailer)
    ``VideoThumbnailOptions`` (ShareX defaults: 9 thumbnails, "_Thumbnail" suffix, max width 512, combined sheet
    with padding 10, spacing 10, 3 columns, video info header, timestamps, shadow, border).
    ``thumbnail_times(duration, count, random_frame=False) -> list[float]`` — evenly spaced inside the clip
    (ShareX uses whole seconds ``int(duration / count) * (i + 1)``, which collapses for clips shorter than
    ``count`` seconds and can seek past the end; ShareL uses ``duration * (i + 1) / (count + 1)``).
    ``build_frame_args(media, t, out_path)``; ``take_thumbnails(media, options, output_dir=None, progress, cancel)
    -> ThumbnailResult(files, combined, frames)``; ``combine_thumbnails(images, timestamps, info_text, options)``.
Animated GIF trimmer (Pillow)
    ``gif_info(path) -> GifInfo(frames, durations, loop, size)``;
    ``trim_gif(src, dst, start_frame, end_frame)`` keeps frames start..end inclusive with their own durations and
    the loop count; ``trim_gif_time(src, dst, start_ms, end_ms)`` picks frames by their start time.
"""
from __future__ import annotations

import json
import os
import random
import shlex
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

from ._common import (
    OperationCancelled,
    ProgressFn,
    ToolError,
    check_cancel,
    load_font,
    require_binary,
    run_cancelable,
)

__all__ = ["ConverterCodec", "GifInfo", "MediaInfo", "ThumbnailResult", "TrimOptions", "VideoConverterOptions",
           "VideoThumbnailOptions", "build_converter_args", "build_frame_args", "build_trim_args",
           "combine_thumbnails", "convert_video", "converter_extension", "format_timestamp", "gif_info", "probe",
           "run_ffmpeg", "take_thumbnails", "thumbnail_times", "trim_gif", "trim_gif_time", "trim_video"]

# ShareX FFmpegCLIManager quality ranges
QUALITY_RANGES = {"x264": (0, 51), "x265": (0, 51), "vp8": (4, 63), "vp9": (0, 63), "av1": (0, 63),
                  "xvid": (1, 31), "mp3": (0, 9)}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


# ---- probing -------------------------------------------------------------------------------------


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0  # seconds
    size: int = 0  # bytes
    bitrate: int = 0  # kb/s
    format_name: str = ""
    video_codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frames: int = 0
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0

    @property
    def has_video(self) -> bool:
        return bool(self.video_codec)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    def summary(self) -> str:
        lines = [f"Filename: {os.path.basename(self.path)}",
                 f"Duration: {format_timestamp(self.duration)}, Bitrate: {self.bitrate} kb/s"]
        if self.video_codec:
            fps = f"{self.fps:.2f}".rstrip("0").rstrip(".")  # .NET "0.##"
            lines.append(f"Video: {self.video_codec}, {self.width}x{self.height}, {fps} fps")
        if self.audio_codec:
            lines.append(f"Audio: {self.audio_codec}"
                         + (f", {self.sample_rate} Hz" if self.sample_rate else "")
                         + (f", {self.channels} ch" if self.channels else ""))
        return "\n".join(lines)


def _rate(text: str | None) -> float:
    if not text or text in ("0/0", "N/A"):
        return 0.0
    if "/" in text:
        n, d = text.split("/", 1)
        try:
            return float(n) / float(d) if float(d) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def probe(path: str | os.PathLike, timeout: float = 60, cancel: threading.Event | None = None) -> MediaInfo:
    exe = require_binary("ffprobe")
    p = os.fspath(path)
    proc = run_cancelable([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", p],
                          cancel, timeout=timeout)
    if proc.returncode != 0:
        raise ToolError(f"ffprobe failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    fmt = data.get("format", {})
    info = MediaInfo(path=p, format_name=fmt.get("format_name", ""))
    try:
        info.duration = float(fmt.get("duration") or 0)
    except ValueError:
        info.duration = 0.0
    info.size = int(fmt.get("size") or 0)
    info.bitrate = int(int(fmt.get("bit_rate") or 0) / 1000)
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind == "video" and not info.video_codec and not (s.get("disposition") or {}).get("attached_pic"):
            info.video_codec = s.get("codec_name", "")
            info.width, info.height = int(s.get("width") or 0), int(s.get("height") or 0)
            info.fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
            info.frames = int(s.get("nb_frames") or 0) if str(s.get("nb_frames", "")).isdigit() else 0
            if not info.duration:
                info.duration = float(s.get("duration") or 0)
        elif kind == "audio" and not info.audio_codec:
            info.audio_codec = s.get("codec_name", "")
            info.sample_rate = int(s.get("sample_rate") or 0)
            info.channels = int(s.get("channels") or 0)
    return info


# ---- runner --------------------------------------------------------------------------------------


def run_ffmpeg(args: list[str], duration: float | None = None, progress: ProgressFn | None = None,
               cancel: threading.Event | None = None, output: str | os.PathLike | None = None,
               timeout: float | None = None) -> None:
    """Run ``ffmpeg <args>`` with progress parsing. ``duration`` (seconds) is the expected output length."""
    exe = require_binary("ffmpeg")
    total_ms = int((duration or 0) * 1000)
    state = {"last": -1}

    def on_line(line: str) -> None:
        if not progress:
            return
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ("out_time_us", "out_time_ms"):  # both are microseconds (FFmpeg quirk)
            try:
                done = max(0, int(value.strip()) // 1000)
            except ValueError:
                return
            if total_ms:
                done = min(done, total_ms)
            if done != state["last"]:
                state["last"] = done
                progress(done, total_ms)
        elif key == "progress" and value.strip() == "end":
            progress(total_ms or max(0, state["last"]), total_ms or max(0, state["last"]))

    argv = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    try:
        proc = run_cancelable(argv, cancel, timeout=timeout, on_stdout_line=on_line)
    except OperationCancelled:
        if output and os.path.exists(output):
            try:
                os.unlink(output)
            except OSError:
                pass
        raise
    if proc.returncode != 0:
        if output and os.path.exists(output):
            try:
                os.unlink(output)
            except OSError:
                pass
        raise ToolError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}")


# ---- converter -----------------------------------------------------------------------------------


class ConverterCodec(StrEnum):
    x264 = "x264"
    x265 = "x265"
    h264_nvenc = "h264_nvenc"
    hevc_nvenc = "hevc_nvenc"
    h264_amf = "h264_amf"
    hevc_amf = "hevc_amf"
    h264_qsv = "h264_qsv"
    hevc_qsv = "hevc_qsv"
    vp8 = "vp8"
    vp9 = "vp9"
    av1 = "av1"
    xvid = "xvid"
    gif = "gif"
    webp = "webp"
    apng = "apng"
    mp3 = "mp3"
    opus = "opus"


_H264_FAMILY = {"x264", "x265", "h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv"}
_HEVC = {"x265", "hevc_nvenc", "hevc_amf", "hevc_qsv"}
_ANIMATION_INPUTS = ("gif", "webp", "png", "apng")


def converter_extension(codec: ConverterCodec | str) -> str:
    c = ConverterCodec(codec).value
    if c in _H264_FAMILY:
        return "mp4"
    return {"vp8": "webm", "vp9": "webm", "av1": "mkv", "xvid": "avi", "gif": "gif", "webp": "webp",
            "apng": "apng", "mp3": "mp3", "opus": "opus"}[c]


@dataclass
class VideoConverterOptions:
    input_path: str = ""
    output_folder: str = ""
    output_name: str = ""
    codec: ConverterCodec = ConverterCodec.x264
    video_quality: int = 23  # CRF / q:v / mp3 VBR quality
    use_bitrate: bool = False
    video_bitrate: int = 3000  # kb/s (also used as the audio bitrate for mp3 when use_bitrate)
    audio_bitrate: int = 128  # kb/s, opus
    keep_resolution: bool = True
    scale_width: int = 0  # 0 = keep aspect from the other side
    scale_height: int = 0
    scale_percent: float = 0.0  # > 0 overrides width/height
    use_custom_arguments: bool = False
    custom_arguments: str = ""

    @property
    def is_input_animation_only(self) -> bool:
        return self.input_path.lower().endswith(tuple("." + x for x in _ANIMATION_INPUTS))

    @property
    def output_path(self) -> str:
        if not self.output_folder or not self.output_name:
            return ""
        path = os.path.join(self.output_folder, self.output_name)
        if not os.path.splitext(self.output_name)[1]:
            path += "." + converter_extension(self.codec)
        return path

    def scale_filter(self) -> str:
        """``scale=W:H`` (or "" when keeping the resolution). Codecs with 4:2:0 output need even sizes."""
        if self.keep_resolution:
            return ""
        even = ConverterCodec(self.codec).value in _H264_FAMILY or self.codec in ("vp8", "vp9", "av1", "xvid")
        auto = "-2" if even else "-1"
        if self.scale_percent and self.scale_percent > 0:
            f = self.scale_percent / 100
            expr_w = f"trunc(iw*{f:g}/2)*2" if even else f"round(iw*{f:g})"
            expr_h = f"trunc(ih*{f:g}/2)*2" if even else f"round(ih*{f:g})"
            return f"scale={expr_w}:{expr_h}"
        w, h = int(self.scale_width), int(self.scale_height)
        if w <= 0 and h <= 0:
            return ""
        if even:
            w = w - w % 2 if w > 0 else 0
            h = h - h % 2 if h > 0 else 0
        return f"scale={w if w > 0 else auto}:{h if h > 0 else auto}"


def build_converter_args(opts: VideoConverterOptions) -> list[str]:
    out = opts.output_path
    if not opts.input_path:
        raise ValueError("no input file")
    if not out:
        raise ValueError("no output folder / name")
    if opts.use_custom_arguments:
        args = shlex.split(opts.custom_arguments)
        return [a.replace("$input", opts.input_path).replace("$output", out) for a in args]
    c = ConverterCodec(opts.codec).value
    q = opts.video_quality
    br = f"{opts.video_bitrate}k"
    a: list[str] = ["-i", opts.input_path]
    scale = opts.scale_filter()
    if c == "x264":
        a += ["-c:v", "libx264", "-preset", "medium"]
        a += ["-b:v", br] if opts.use_bitrate else ["-crf", str(_clamp(q, *QUALITY_RANGES["x264"]))]
        a += ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    elif c == "x265":
        a += ["-c:v", "libx265", "-preset", "medium"]
        a += ["-b:v", br] if opts.use_bitrate else ["-crf", str(_clamp(q, *QUALITY_RANGES["x265"]))]
    elif c in ("h264_nvenc", "hevc_nvenc"):
        a += ["-c:v", c, "-preset", "p4", "-tune", "hq", "-profile:v", "high" if c == "h264_nvenc" else "main",
              "-b:v", br]
    elif c in ("h264_amf", "hevc_amf"):
        a += ["-c:v", c, "-usage", "transcoding", "-profile", "main", "-quality", "balanced", "-b:v", br]
    elif c in ("h264_qsv", "hevc_qsv"):
        a += ["-c:v", c, "-preset", "medium", "-b:v", br]
    elif c == "vp8":
        a += ["-c:v", "libvpx"]
        a += ["-b:v", br] if opts.use_bitrate else ["-crf", str(_clamp(q, *QUALITY_RANGES["vp8"])), "-b:v", "100M"]
    elif c == "vp9":
        a += ["-c:v", "libvpx-vp9"]
        a += ["-b:v", br] if opts.use_bitrate else ["-crf", str(_clamp(q, *QUALITY_RANGES["vp9"])), "-b:v", "0"]
    elif c == "av1":
        a += ["-c:v", "libsvtav1"]
        a += ["-b:v", br] if opts.use_bitrate else ["-crf", str(_clamp(q, *QUALITY_RANGES["av1"]))]
    elif c == "xvid":
        a += ["-c:v", "libxvid"]
        a += ["-b:v", br] if opts.use_bitrate else ["-q:v", str(_clamp(q, *QUALITY_RANGES["xvid"]))]
    elif c == "gif":
        pre = f"{scale}," if scale else ""
        if scale:
            graph = (f"[0:v]{pre}split[a][b];[a]palettegen=stats_mode=full[palette];"
                     "[b][palette]paletteuse=dither=sierra2_4a")
        else:
            graph = "palettegen=stats_mode=full[palette],[0:v][palette]paletteuse=dither=sierra2_4a"
        a += ["-lavfi", graph]
        scale = ""  # already inside the graph
    elif c == "webp":
        a += ["-c:v", "libwebp", "-lossless", "0", "-preset", "default", "-loop", "0"]
    elif c == "apng":
        a += ["-f", "apng", "-plays", "0"]
    elif c == "mp3":
        a += ["-vn", "-c:a", "libmp3lame"]
        a += ["-b:a", br] if opts.use_bitrate else ["-q:a", str(_clamp(q, *QUALITY_RANGES["mp3"]))]
    elif c == "opus":
        a += ["-vn", "-c:a", "libopus", "-b:a", f"{opts.audio_bitrate}k"]
    if scale and c not in ("mp3", "opus"):
        a += ["-vf", scale]
    if c in _HEVC:
        a += ["-tag:v", "hvc1"]
    if not opts.is_input_animation_only:
        if c in _H264_FAMILY:
            a += ["-c:a", "aac", "-b:a", "128k"]
        elif c in ("vp8", "vp9"):
            a += ["-c:a", "libvorbis", "-q:a", "3"]
        elif c == "av1":
            a += ["-c:a", "libopus", "-b:a", "128k"]
        elif c == "xvid":
            a += ["-c:a", "libmp3lame", "-q:a", "4"]
    a += ["-y", out]
    return a


def convert_video(opts: VideoConverterOptions, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None, duration: float | None = None) -> Path:
    args = build_converter_args(opts)
    if duration is None:
        try:
            duration = probe(opts.input_path, cancel=cancel).duration
        except ToolError:
            duration = None
    Path(opts.output_path).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, duration, progress, cancel, output=opts.output_path)
    return Path(opts.output_path)


# ---- trimmer -------------------------------------------------------------------------------------


@dataclass
class TrimOptions:
    input_path: str
    output_path: str
    start: float = 0.0  # seconds
    end: float | None = None  # seconds; None = to the end
    stream_copy: bool = True
    codec: str = "x264"  # re-encode codec: x264, x265, vp9, av1
    crf: int = 23

    @property
    def duration(self) -> float | None:
        return None if self.end is None else max(0.0, self.end - self.start)


def _ts_arg(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


def build_trim_args(opts: TrimOptions) -> list[str]:
    if opts.end is not None and opts.end <= opts.start:
        raise ValueError("the end must be after the start")
    a: list[str] = []
    if opts.start > 0:
        a += ["-ss", _ts_arg(opts.start)]
    a += ["-i", opts.input_path]
    if opts.end is not None:
        a += ["-t", _ts_arg(opts.end - opts.start)]
    if opts.stream_copy:
        a += ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        codec = opts.codec
        if codec == "x264":
            a += ["-c:v", "libx264", "-preset", "medium", "-crf", str(_clamp(opts.crf, 0, 51)), "-pix_fmt",
                  "yuv420p", "-c:a", "aac", "-b:a", "128k"]
        elif codec == "x265":
            a += ["-c:v", "libx265", "-preset", "medium", "-crf", str(_clamp(opts.crf, 0, 51)), "-tag:v", "hvc1",
                  "-c:a", "aac", "-b:a", "128k"]
        elif codec == "vp9":
            a += ["-c:v", "libvpx-vp9", "-crf", str(_clamp(opts.crf, 0, 63)), "-b:v", "0", "-c:a", "libopus",
                  "-b:a", "128k"]
        elif codec == "av1":
            a += ["-c:v", "libsvtav1", "-crf", str(_clamp(opts.crf, 0, 63)), "-c:a", "libopus", "-b:a", "128k"]
        else:
            raise ValueError(f"unsupported trim codec {codec!r}")
    a += ["-y", opts.output_path]
    return a


def trim_video(opts: TrimOptions, progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> Path:
    args = build_trim_args(opts)
    dur = opts.duration
    if dur is None:
        try:
            dur = max(0.0, probe(opts.input_path, cancel=cancel).duration - opts.start)
        except ToolError:
            dur = None
    Path(opts.output_path).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, dur, progress, cancel, output=opts.output_path)
    return Path(opts.output_path)


# ---- thumbnailer ---------------------------------------------------------------------------------


@dataclass
class VideoThumbnailOptions:
    thumbnail_count: int = 9
    filename_suffix: str = "_Thumbnail"
    random_frame: bool = False
    keep_screenshots: bool = False
    max_thumbnail_width: int = 512  # 0 = don't resize
    combine_screenshots: bool = True
    padding: int = 10
    spacing: int = 10
    column_count: int = 3
    add_video_info: bool = True
    add_timestamp: bool = True
    draw_shadow: bool = True
    draw_border: bool = True
    image_format: str = "png"  # png / jpg
    font_size: int = 14


@dataclass
class ThumbnailResult:
    files: list[Path]  # what to show/upload: the sheet, or the single frames
    combined: Path | None
    frames: list[tuple[float, Path]] = field(default_factory=list)  # kept single frames (time, path)


def format_timestamp(seconds: float) -> str:
    """.NET ``TimeSpan.ToString()`` for whole seconds: ``hh:mm:ss``."""
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def thumbnail_times(duration: float, count: int, random_frame: bool = False,
                    rng: random.Random | None = None) -> list[float]:
    if count < 1 or duration <= 0:
        return []
    step = duration / (count + 1)
    if not random_frame:
        return [step * (i + 1) for i in range(count)]
    r = rng or random.Random()
    # one random time inside each of ``count`` equal slices (ShareX GetRandomTimeSlice)
    return [step * (i + 0.5) + r.random() * step for i in range(count)]


def build_frame_args(media: str, t: float, out_path: str) -> list[str]:
    """ShareX: ``-ss T -i media -f image2 -vframes 1 -y out`` (fast input seeking)."""
    return ["-ss", _ts_arg(t), "-i", media, "-f", "image2", "-frames:v", "1", "-y", out_path]


def combine_thumbnails(images: list[Image.Image], timestamps: list[float], info_text: str,
                       options: VideoThumbnailOptions) -> Image.Image:
    """ShareX's CombineScreenshots layout on a WhiteSmoke canvas."""
    if not images:
        raise ValueError("no thumbnails")
    o = options
    font = load_font(o.font_size)
    info_h = 0
    if o.add_video_info and info_text:
        probe_d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        info_h = probe_d.multiline_textbbox((0, 0), info_text, font=font)[3]
    cols = max(1, o.column_count)
    tw, th = images[0].size
    rows = -(-len(images) // cols)
    width = o.padding * 2 + tw * cols + (cols - 1) * o.spacing
    height = o.padding * 3 + info_h + th * rows + (rows - 1) * o.spacing
    sheet = Image.new("RGB", (width, height), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    if info_h:
        d.multiline_text((o.padding, o.padding), info_text, font=font, fill=(0, 0, 0))
    i = 0
    y = o.padding * 2 + info_h
    for _r in range(rows):
        x = o.padding
        for _c in range(cols):
            if i >= len(images):
                break
            if o.draw_shadow:
                d.rectangle((x + 3, y + 3, x + 3 + tw - 1, y + 3 + th - 1), fill=(160, 160, 160))
            im = images[i] if images[i].size == (tw, th) else images[i].resize((tw, th), Image.Resampling.LANCZOS)
            sheet.paste(im.convert("RGB"), (x, y))
            if o.draw_border:
                d.rectangle((x, y, x + tw - 1, y + th - 1), outline=(0, 0, 0))
            if o.add_timestamp and i < len(timestamps):
                text = format_timestamp(timestamps[i])
                d.text((x + 11, y + 11), text, font=font, fill=(0, 0, 0))
                d.text((x + 10, y + 10), text, font=font, fill=(255, 255, 255))
            i += 1
            x += tw + o.spacing
        y += th + o.spacing
    return sheet


def take_thumbnails(media: str | os.PathLike, options: VideoThumbnailOptions | None = None,
                    output_dir: str | os.PathLike | None = None, progress: ProgressFn | None = None,
                    cancel: threading.Event | None = None, rng: random.Random | None = None) -> ThumbnailResult:
    o = options or VideoThumbnailOptions()
    media_s = os.fspath(media)
    info = probe(media_s, cancel=cancel)
    if info.duration <= 0 or not info.has_video:
        raise ToolError(f"{os.path.basename(media_s)} has no video stream with a duration")
    out_dir = Path(output_dir) if output_dir else Path(media_s).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if o.image_format.lower() in ("jpg", "jpeg") else "png"
    stem = Path(media_s).stem
    times = thumbnail_times(info.duration, o.thumbnail_count, o.random_frame, rng)
    frames: list[tuple[float, Path]] = []
    total = len(times)
    if progress:
        progress(0, total)
    for i, t in enumerate(times):
        check_cancel(cancel)
        path = out_dir / f"{stem}-{t:.0f}s-{i + 1}.{ext}"
        run_ffmpeg(build_frame_args(media_s, t, str(path)), None, None, cancel, output=path, timeout=60)
        if path.exists():
            frames.append((t, path))
        if progress:
            progress(i + 1, total)
    if not frames:
        raise ToolError("FFmpeg did not produce any thumbnails")

    def load(p: Path) -> Image.Image:
        with Image.open(p) as im:
            im.load()
            im = im.convert("RGB")
        if o.max_thumbnail_width > 0 and im.width > o.max_thumbnail_width:
            h = int(o.max_thumbnail_width / im.width * im.height)
            im = im.resize((o.max_thumbnail_width, max(1, h)), Image.Resampling.LANCZOS)
        return im

    if not o.combine_screenshots:
        if o.max_thumbnail_width > 0:
            for _t, p in frames:
                im = load(p)
                im.save(p)
        return ThumbnailResult([p for _t, p in frames], None, frames)
    images = [load(p) for _t, p in frames]
    sheet = combine_thumbnails(images, [t for t, _p in frames], info.summary(), o)
    combined = out_dir / f"{stem}{o.filename_suffix}.{ext}"
    sheet.save(combined, "JPEG" if ext == "jpg" else "PNG", **({"quality": 90} if ext == "jpg" else {}))
    if not o.keep_screenshots:
        for _t, p in frames:
            try:
                p.unlink()
            except OSError:
                pass
        frames = []
    return ThumbnailResult([combined], combined, frames)


# ---- animated GIF trimmer ------------------------------------------------------------------------


@dataclass
class GifInfo:
    frames: int
    durations: list[int]  # ms per frame
    loop: int | None  # None = no loop extension (plays once); 0 = forever
    size: tuple[int, int]

    @property
    def total_duration(self) -> int:
        return sum(self.durations)


def gif_info(path: str | os.PathLike) -> GifInfo:
    with Image.open(path) as im:
        durations = []
        for fr in ImageSequence.Iterator(im):
            durations.append(int(fr.info.get("duration", 100) or 0))
        loop = im.info.get("loop")
        return GifInfo(len(durations), durations, None if loop is None else int(loop), im.size)


def trim_gif(src: str | os.PathLike, dst: str | os.PathLike, start_frame: int, end_frame: int) -> Path:
    """Keep frames ``start_frame..end_frame`` (0-based, inclusive). Each frame keeps its own duration; the loop
    count is preserved. Note: GIF encoders merge identical consecutive frames (adding their durations)."""
    with Image.open(src) as im:
        n = getattr(im, "n_frames", 1)
        if not 0 <= start_frame <= end_frame < n:
            raise ValueError(f"frame range {start_frame}..{end_frame} is outside 0..{n - 1}")
        loop = im.info.get("loop")
        frames, durations = [], []
        for idx, fr in enumerate(ImageSequence.Iterator(im)):
            if idx < start_frame:
                continue
            if idx > end_frame:
                break
            durations.append(int(fr.info.get("duration", 100) or 0))
            frames.append(fr.convert("RGBA"))
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    kw: dict = {"save_all": True, "append_images": frames[1:], "duration": durations, "disposal": 2,
                "optimize": False}
    if loop is not None:
        kw["loop"] = int(loop)
    if len(frames) == 1:
        kw.pop("append_images")
        kw["duration"] = durations[0]
    frames[0].save(out, "GIF", **kw)
    return out


def trim_gif_time(src: str | os.PathLike, dst: str | os.PathLike, start_ms: int, end_ms: int) -> Path:
    """Keep the frames whose start time lies in ``[start_ms, end_ms)``."""
    info = gif_info(src)
    t = 0
    keep = []
    for i, d in enumerate(info.durations):
        if start_ms <= t < end_ms:
            keep.append(i)
        t += d
    if not keep:
        raise ValueError("no frames start inside that time range")
    return trim_gif(src, dst, keep[0], keep[-1])
