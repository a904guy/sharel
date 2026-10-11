"""Image resizer (ShareX ``ImageResizerWindow``).

Width × height with ShareX's resize modes: Fill (scale and crop to cover), Fit (scale to fit inside, centred on
a canvas of exactly that size; transparent, or the background colour for JPEG) and Stretch. A width or height
of 0 keeps the aspect ratio from the other side. Output PNG or JPEG (quality, background colour), output
folder and file name (``$filename_resized``). Defaults from the task settings' tool options.
"""
from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Adw
from PIL import Image, ImageOps

from ...core.jobs import JobContext
from ...effects.common import parse_color
from ...tools import imagetools
from . import _base
from ._batch import BatchImageTool, open_batch_tool

MODES = ["Fill", "Fit", "Stretch"]
FORMATS = ["PNG", "JPEG"]
RESAMPLING = ["Lanczos", "Bicubic", "Bilinear", "Nearest"]


@dataclass
class ResizeOptions:
    width: int = 1280
    height: int = 0
    mode: str = "Fit"
    fmt: str = "PNG"
    quality: int = 90
    background: str = "#FFFFFFFF"
    resample: str = "Lanczos"


def resize(img: Image.Image, o: ResizeOptions) -> Image.Image:
    img = img.convert("RGBA")
    rs = getattr(Image.Resampling, o.resample.upper())
    w, h = o.width, o.height
    if w <= 0 and h <= 0:
        return img.copy()
    if w <= 0 or h <= 0:  # proportional from the given side
        size = imagetools.thumbnail_size(img.size, w, h)
        return img.resize(size, rs)
    if o.mode == "Stretch":
        return img.resize((w, h), rs)
    if o.mode == "Fill":
        return ImageOps.fit(img, (w, h), rs)
    fw, fh = imagetools.thumbnail_size(img.size, w, h)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(img.resize((fw, fh), rs), ((w - fw) // 2, (h - fh) // 2))
    return canvas


def flatten(img: Image.Image, color: str) -> Image.Image:
    bg = Image.new("RGBA", img.size, (*parse_color(color)[:3], 255))
    bg.alpha_composite(img.convert("RGBA"))
    return bg.convert("RGB")


class ResizerWindow(BatchImageTool):
    TITLE = "Image resizer"
    RUN_LABEL = "Resize"
    PATTERN = "$filename_resized"

    def __init__(self, app, ctx: JobContext) -> None:
        t = ctx.task_settings.tool_settings
        self.opts = ResizeOptions(t.image_resizer_width, t.image_resizer_height,
                                  "Fit" if t.image_resizer_keep_aspect else "Stretch")
        super().__init__(app, ctx)

    def build_options(self):
        o = self.opts

        def setter(name, conv=int):
            def set_(v):
                setattr(o, name, conv(v))
                self._sync_rows()
                self.options_changed()
            return set_
        size = Adw.PreferencesGroup(title="Size")
        self.width_row = _base.spin_row("Width", o.width, 0, 20000, setter("width"), subtitle="0 = keep aspect ratio")
        self.height_row = _base.spin_row("Height", o.height, 0, 20000, setter("height"),
                                         subtitle="0 = keep aspect ratio")
        self.mode_row = _base.combo_row("Resize mode", MODES, MODES.index(o.mode),
                                        lambda i: setter("mode", lambda v: v)(MODES[i]))
        self.resample_row = _base.combo_row("Resampling", RESAMPLING, 0,
                                            lambda i: setter("resample", lambda v: v)(RESAMPLING[i]))
        for r in (self.width_row, self.height_row, self.mode_row, self.resample_row):
            size.add(r)
        fmt = Adw.PreferencesGroup(title="Format")
        self.format_row = _base.combo_row("Output format", FORMATS, 0,
                                          lambda i: setter("fmt", lambda v: v)(FORMATS[i]))
        self.quality_row = _base.spin_row("JPEG quality", o.quality, 1, 100, setter("quality"))
        self.background_row = _base.color_row("Background color", o.background,
                                              lambda c: setter("background", str)(c))
        for r in (self.format_row, self.quality_row, self.background_row):
            fmt.add(r)
        self._sync_rows()
        return [size, fmt]

    def _sync_rows(self) -> None:
        if hasattr(self, "background_row"):
            jpeg = self.opts.fmt == "JPEG"
            self.quality_row.set_visible(jpeg)
            self.background_row.set_visible(jpeg)

    def snapshot(self):
        return ResizeOptions(**vars(self.opts))

    def save_defaults(self, o) -> None:
        self.tools.image_resizer_width = o.width
        self.tools.image_resizer_height = o.height
        self.tools.image_resizer_keep_aspect = o.mode != "Stretch"

    def output_ext(self, src, o) -> str:
        return "jpg" if o.fmt == "JPEG" else "png"

    def render_preview(self, img, o):
        out = resize(img, o)
        return flatten(out, o.background) if o.fmt == "JPEG" else out

    def process(self, path, o, cancel):
        out = resize(_base.load_image_file(path), o)
        if o.fmt == "JPEG":
            out = flatten(out, o.background)
        return imagetools.save_image(out, self.target_path(path, o), o.fmt, o.quality)

    def describe_result(self, r) -> str:
        return f"Resized → {r.name}"


def open(app, ctx: JobContext) -> ResizerWindow:  # noqa: A001
    return open_batch_tool(ResizerWindow, app, ctx)
