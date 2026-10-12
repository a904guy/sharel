"""Recording pipelines: ShareX's FFmpegOptions mapped to GStreamer (live capture) and ffmpeg
(final conversion), using hardware encoders (NVENC, VA-API, QSV) when present.

``plan(opts)`` decides the encoder chain for a ``FFmpegVideoCodec``. Codecs GStreamer can encode
live (x264, x265, vp8, vp9, AV1, NVENC/VA-API H.264/H.265/AV1, MPEG-4) are encoded during
recording; the rest (GIF, WebP, APNG, Xvid) are recorded to a lossless-ish intermediate and
converted by ffmpeg afterwards, exactly like ShareX's FFmpeg command lines.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from ..core.config import FFmpegOptions
from ..core.enums import FFmpegAudioCodec, FFmpegVideoCodec


def element_available(name: str) -> bool:
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        from ..capture.pwframe import gst_init
        gst_init()
        return Gst.ElementFactory.find(name) is not None
    except (ImportError, ValueError):
        return False


@dataclass
class EncoderPlan:
    codec: str  # the requested FFmpegVideoCodec value
    gst_video: str  # GStreamer encoder chain (with parser), "" when ffmpeg converts afterwards
    mux: str  # GStreamer muxer for the recorded file
    extension: str  # final file extension
    record_extension: str  # extension of what GStreamer writes
    post_ffmpeg: list[str] = field(default_factory=list)  # ffmpeg output args for the conversion step
    hardware: str = ""  # "nvenc" | "vaapi" | "qsv" | ""
    fallback_note: str = ""
    raw_format: str = "I420"


# Software x264 settings shared by the intermediate recordings.
INTERMEDIATE = "x264enc speed-preset=ultrafast tune=zerolatency pass=quant quantizer=0 ! h264parse"


def _x264(o: FFmpegOptions) -> str:
    rate = f"bitrate={o.x264_bitrate}" if o.x264_use_bitrate else f"pass=quant quantizer={o.x264_crf}"
    return (f"x264enc speed-preset={o.x264_preset.value} tune={o.x264_tune.value} {rate} key-int-max=60 "
            f"! video/x-h264,profile=high ! h264parse")


def _x265(o: FFmpegOptions) -> str:
    rate = f"bitrate={o.x264_bitrate}" if o.x264_use_bitrate else f"option-string=crf={o.x264_crf}"
    tune = o.x264_tune.value if o.x264_tune.value in ("psnr", "ssim", "grain", "zerolatency", "fastdecode") else \
        "zerolatency"
    return f"x265enc speed-preset={o.x264_preset.value} tune={tune} {rate} ! h265parse"


def _vpx(enc: str, o: FFmpegOptions) -> str:
    return (f"{enc} deadline=1 cpu-used=8 threads=8 end-usage=cq cq-level={o.vpx_crf} "
            f"target-bitrate={o.vpx_bitrate * 1000}")


def plan(o: FFmpegOptions, *, available=element_available) -> EncoderPlan:
    c = o.video_codec.value
    if c == "libx264":
        return EncoderPlan(c, _x264(o), "mp4mux", "mp4", "mp4")
    if c == "libx265":
        if available("x265enc"):
            return EncoderPlan(c, _x265(o), "mp4mux", "mp4", "mp4")
        return EncoderPlan("libx264", _x264(o), "mp4mux", "mp4", "mp4", fallback_note="x265enc missing: used x264")
    if c == "libvpx":
        return EncoderPlan(c, _vpx("vp8enc", o), "webmmux", "webm", "webm")
    if c == "libvpx_vp9":
        return EncoderPlan(c, _vpx("vp9enc", o) + " row-mt=true", "webmmux", "webm", "webm")
    if c in ("libaom_av1", "libsvtav1"):
        for el, chain in (("svtav1enc", f"svtav1enc preset=12 crf={min(63, o.vpx_crf)} ! av1parse"),
                          ("av1enc", f"av1enc cpu-used=8 end-usage=q cq-level={min(63, o.vpx_crf)} ! av1parse")):
            if available(el):
                return EncoderPlan(c, chain, "matroskamux", "mkv", "mkv")
        return EncoderPlan("libvpx_vp9", _vpx("vp9enc", o), "webmmux", "webm", "webm",
                           fallback_note="no AV1 encoder: used VP9")
    hw = {
        "h264_nvenc": ("nvh264enc", f"nvh264enc preset=low-latency-hq bitrate={o.nvenc_bitrate} ! h264parse",
                       "mp4mux", "mp4", "nvenc"),
        "hevc_nvenc": ("nvh265enc", f"nvh265enc preset=low-latency-hq bitrate={o.nvenc_bitrate} ! h265parse",
                       "mp4mux", "mp4", "nvenc"),
        "av1_nvenc": ("nvav1enc", f"nvav1enc bitrate={o.nvenc_bitrate} ! av1parse", "matroskamux", "mkv", "nvenc"),
        "h264_vaapi": ("vah264enc", f"vah264enc rate-control=cqp qpi={o.vaapi_qp} ! h264parse", "mp4mux", "mp4",
                       "vaapi"),
        "hevc_vaapi": ("vah265enc", f"vah265enc rate-control=cqp qpi={o.vaapi_qp} ! h265parse", "mp4mux", "mp4",
                       "vaapi"),
        "av1_vaapi": ("vaav1enc", f"vaav1enc rate-control=cqp qpi={o.vaapi_qp} ! av1parse", "matroskamux", "mkv",
                      "vaapi"),
        "h264_qsv": ("qsvh264enc", f"qsvh264enc bitrate={o.qsv_bitrate} ! h264parse", "mp4mux", "mp4", "qsv"),
        "hevc_qsv": ("qsvh265enc", f"qsvh265enc bitrate={o.qsv_bitrate} ! h265parse", "mp4mux", "mp4", "qsv"),
        "av1_qsv": ("qsvav1enc", f"qsvav1enc bitrate={o.qsv_bitrate} ! av1parse", "matroskamux", "mkv", "qsv"),
    }
    if c in hw:
        el, chain, mux, ext, kind = hw[c]
        if available(el):
            return EncoderPlan(c, chain, mux, ext, ext, hardware=kind, raw_format="NV12")
        return EncoderPlan("libx264", _x264(o), "mp4mux", "mp4", "mp4",
                           fallback_note=f"{el} (hardware) not available: used x264")
    if c == "mpeg4":
        return EncoderPlan(c, f"avenc_mpeg4 bitrate={o.x264_bitrate * 1000} ! mpeg4videoparse", "matroskamux",
                           "mkv", "mkv")
    # ffmpeg-converted outputs: record an intermediate, then ShareX's encoder args.
    post = {
        "libxvid": (["-c:v", "libxvid", "-qscale:v", str(o.xvid_qscale)], "avi"),
        "libwebp": (["-c:v", "libwebp", "-lossless", "0", "-qscale:v", "75", "-loop", "0"], "webp"),
        "apng": (["-c:v", "apng", "-plays", "0", "-f", "apng"], "png"),
        "gif": ([], "gif"),
    }
    args, ext = post.get(c, (["-c:v", "libx264"], "mp4"))
    if c == "libxvid" and not ffmpeg_has_encoder("libxvid"):
        args = ["-c:v", "mpeg4", "-qscale:v", str(o.xvid_qscale)]
    return EncoderPlan(c, INTERMEDIATE, "matroskamux", ext, "mkv", post_ffmpeg=args)


def ffmpeg_has_encoder(name: str) -> bool:
    import subprocess
    if not shutil.which("ffmpeg"):
        return False
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
        return any(line.split()[1:2] == [name] for line in out.stdout.splitlines() if len(line.split()) > 1)
    except (subprocess.SubprocessError, OSError):
        return False


def audio_chain(o: FFmpegOptions) -> tuple[str, str]:
    """(GStreamer audio encoder chain, short name) for FFmpegAudioCodec."""
    a = o.audio_codec
    if a == FFmpegAudioCodec.libopus:
        return f"opusenc bitrate={o.opus_bitrate * 1000}", "opus"
    if a == FFmpegAudioCodec.libvorbis:
        return f"vorbisenc quality={min(1.0, o.vorbis_qscale / 10):.1f}", "vorbis"
    if a == FFmpegAudioCodec.libmp3lame:
        return f"lamemp3enc quality={o.mp3_qscale}", "mp3"
    enc = "avenc_aac" if element_available("avenc_aac") or not element_available("voaacenc") else "voaacenc"
    return f"{enc} bitrate=128000 ! aacparse", "aac"


def audio_fits(mux: str, short: str) -> bool:
    ok = {"mp4mux": {"aac", "mp3", "opus"}, "webmmux": {"opus", "vorbis"},
          "matroskamux": {"aac", "mp3", "opus", "vorbis"}}
    return short in ok.get(mux, set())


def video_source(node_id: int | None = None, fd: int | None = None, test_pattern: bool = False) -> str:
    """The live source. For PipeWire this mirrors GNOME Shell's own screencast service
    (``pipewiresrc path=<node id> do-timestamp=true keepalive-time=1000 resend-last=true``). If the
    node is gone (its ScreenCast session ended), pipewiresrc connects to the default video source
    instead (a camera): the Recorder keeps the session alive for the whole recording, checks the
    frame size before recording, and fails if the source ends early."""
    if test_pattern:
        return "videotestsrc is-live=true pattern=ball"
    parts = ["pipewiresrc name=pwsrc"]
    if fd is not None:
        parts.append(f"fd={fd}")
    if node_id is not None:
        parts.append(f"path={node_id}")
    parts += ["do-timestamp=true", "keepalive-time=1000", "resend-last=true"]
    return " ".join(parts)


def build_launch(source: str, ep: EncoderPlan, out_path: str, fps: int, *, width: int = 0, height: int = 0,
                 audio_source: str = "", o: FFmpegOptions | None = None) -> str:
    """The gst-launch description for a recording."""
    loc = out_path.replace('"', '\\"')
    if source.startswith("pipewiresrc"):
        # GNOME's screencast caps: a ScreenCast stream has a variable frame rate and refuses a fixed one,
        # so only cap the maximum; the stream's own size is the region's (physical pixels).
        raw = f"video/x-raw,max-framerate={fps}/1 ! videoconvert ! video/x-raw,format={ep.raw_format}"
    else:
        size = f",width={width},height={height}" if width and height else ""
        raw = f"videoconvert ! videoscale ! videorate ! video/x-raw,format={ep.raw_format},framerate={fps}/1{size}"
    # Pause drops buffers at the valves (a live pipeline must keep PLAYING); the Recorder then
    # offsets the valves' src pads by the paused time, so the gap is not in the file.
    # drop-mode forwards sticky events, EOS among them: stopping while paused still finishes the file.
    valve = "drop=false drop-mode=forward-sticky-events"
    video = (f'{source} ! {raw} ! queue ! valve name=vvalve {valve} ! {ep.gst_video} ! queue ! '
             f'{ep.mux} name=mux ! filesink location="{loc}"')
    if audio_source and o is not None:
        chain, short = audio_chain(o)
        if audio_fits(ep.mux, short):
            dev = "" if audio_source == "default" else f' device="{audio_source}"'
            video += (f" pulsesrc{dev} do-timestamp=true ! queue ! valve name=avalve {valve} ! audioconvert !"
                      f" audioresample ! {chain} ! queue ! mux.")
    return video


def gif_commands(src: str, dst: str, palette: str, fps: int, o: FFmpegOptions, scale_width: int = 0) -> list[list[str]]:
    """ShareX's two-pass GIF: palettegen, then paletteuse (ffmpeg)."""
    scale = f",scale={scale_width}:-1:flags=lanczos" if scale_width else ""
    stats = o.gif_stats_mode.value
    dither = o.gif_dither.value
    use = f"paletteuse=dither={dither}"
    if dither == "bayer":
        use += f":bayer_scale={o.gif_bayer_scale}"
    return [
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vf",
         f"fps={fps}{scale},palettegen=stats_mode={stats}", palette],
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-i", palette, "-lavfi",
         f"fps={fps}{scale} [x]; [x][1:v] {use}", "-loop", "0", dst],
    ]


def convert_command(src: str, dst: str, ep: EncoderPlan, o: FFmpegOptions) -> list[str]:
    extra = o.user_args.split() if o.user_args else []
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, *ep.post_ffmpeg, *extra, dst]


def even(v: int) -> int:
    """Encoders want even frame sizes (4:2:0 chroma)."""
    return max(2, v - (v % 2))


__all__ = ["EncoderPlan", "FFmpegVideoCodec", "audio_chain", "build_launch", "convert_command", "even",
           "gif_commands", "plan", "video_source"]
