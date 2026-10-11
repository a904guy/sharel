"""Widgets shared by the video tool windows (converter, trimmer, thumbnailer, GIF trimmer): a file row with
Browse, an output-actions bar (Open / Show in folder / Upload), time formatting, frame grabbing."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from PIL import Image

from ...core import desktop
from ...tools import videotools as vt
from ..gtkutil import VIDEO_PATTERNS, open_files, save_file, select_folder
from ._base import button, file_drop_target

# ShareL's FFmpeg recording codec names (TaskSettingsTools.video_converter_codec) -> converter codecs
FFMPEG_CODEC_ALIASES = {"libx264": "x264", "libx265": "x265", "libvpx": "vp8", "libvpx-vp9": "vp9",
                        "libsvtav1": "av1", "libaom-av1": "av1", "librav1e": "av1", "libxvid": "xvid",
                        "h264_vaapi": "x264", "hevc_vaapi": "x265", "libwebp": "webp", "libmp3lame": "mp3",
                        "libopus": "opus"}


def codec_from_setting(name: str) -> vt.ConverterCodec:
    n = FFMPEG_CODEC_ALIASES.get(name, name)
    try:
        return vt.ConverterCodec(n)
    except ValueError:
        return vt.ConverterCodec.x264


def format_seconds(t: float) -> str:
    """``h:mm:ss.mmm`` (hours only when needed)."""
    t = max(0.0, t)
    ms = round(t * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return (f"{h}:{m:02d}:{s:02d}.{ms:03d}" if h else f"{m:02d}:{s:02d}.{ms:03d}")


class PathRow(Gtk.Box):
    """Label + entry + Browse. ``mode``: "open" (a file), "folder", or "save"."""

    def __init__(self, label: str, mode: str = "open", patterns: list[str] | None = None,
                 filter_name: str = "Videos", placeholder: str = "", on_changed: Callable[[str], None] | None = None,
                 suggested: Callable[[], str] | None = None) -> None:
        super().__init__(spacing=6)
        self.mode = mode
        self.patterns = patterns
        self.filter_name = filter_name
        self.suggested = suggested
        self.title = label.rstrip(":")
        lab = Gtk.Label(label=label, xalign=0, width_chars=16)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text=placeholder, tooltip_text=self.title)
        lab.set_mnemonic_widget(self.entry)
        self.browse = button("Browse…", self._browse, f"Browse for {self.title.lower()}")
        self.append(lab)
        self.append(self.entry)
        self.append(self.browse)
        if on_changed:
            self.entry.connect("changed", lambda e: on_changed(e.get_text().strip()))
        if mode == "open":
            file_drop_target(self.entry, lambda ps: self.entry.set_text(ps[0]))

    @property
    def path(self) -> str:
        return os.path.expanduser(self.entry.get_text().strip())

    def set_path(self, p: str) -> None:
        self.entry.set_text(p)

    def _root(self):
        r = self.get_root()
        return r if isinstance(r, Gtk.Window) else None

    def _browse(self) -> None:
        if self.mode == "folder":
            select_folder(self._root(), self.title, lambda p: p and self.set_path(p))
        elif self.mode == "save":
            sug = self.path or (self.suggested() if self.suggested else "")
            save_file(self._root(), self.title, sug, lambda p: p and self.set_path(p), self.patterns)
        else:
            open_files(self._root(), self.title, lambda ps: ps and self.set_path(ps[0]),
                       patterns=self.patterns if self.patterns is not None else VIDEO_PATTERNS,
                       filter_name=self.filter_name, multiple=False)


class OutputActions(Gtk.Box):
    """Open / Show in folder / Upload for the produced file(s); insensitive until ``set_outputs``."""

    def __init__(self, app, ts_fn: Callable[[], object], upload: bool = True) -> None:
        super().__init__(spacing=6)
        self.app = app
        self.ts_fn = ts_fn
        self.outputs: list[str] = []
        self.open_button = button("Open", self.open_output, "Open the output file")
        self.folder_button = button("Show in folder", self.show_output, "Show the output file in the file manager")
        self.append(self.open_button)
        self.append(self.folder_button)
        self.upload_button: Gtk.Button | None = None
        if upload:
            self.upload_button = button("Upload", self.upload_output, "Upload the output file")
            self.append(self.upload_button)
        self.set_outputs([])

    def set_outputs(self, paths: list[str]) -> None:
        self.outputs = [str(p) for p in paths]
        ok = bool(self.outputs)
        for b in (self.open_button, self.folder_button, self.upload_button):
            if b is not None:
                b.set_sensitive(ok)

    def open_output(self) -> None:
        if self.outputs:
            desktop.open_file(self.outputs[0])

    def show_output(self) -> None:
        if self.outputs:
            desktop.show_in_folder(self.outputs[0])

    def upload_output(self) -> None:
        ts = self.ts_fn()
        for p in self.outputs:
            if Path(p).is_file():
                self.app.tasks.upload_file(p, ts)


def grab_frame(media: str, t: float, cancel=None, max_size: int = 480) -> Image.Image:
    """One frame of ``media`` at ``t`` seconds (ffmpeg fast seek), scaled down to ``max_size``."""
    with tempfile.TemporaryDirectory(prefix="sharel-frame-") as tmp:
        out = os.path.join(tmp, "frame.png")
        vt.run_ffmpeg(vt.build_frame_args(media, t, out), None, None, cancel, output=out, timeout=60)
        if not os.path.exists(out):  # seeking at/after the end gives no frame: step back a little
            vt.run_ffmpeg(vt.build_frame_args(media, max(0.0, t - 0.5), out), None, None, cancel, output=out,
                          timeout=60)
        with Image.open(out) as im:
            im.load()
            im = im.convert("RGBA")
    im.thumbnail((max_size, max_size))
    return im


def unique_output(path: Path) -> Path:
    """``name.ext`` -> ``name (1).ext`` ... when it exists."""
    if not path.exists():
        return path
    i = 1
    while True:
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
        i += 1
