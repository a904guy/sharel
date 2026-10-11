"""Image converter (ShareX ``ImageConverterWindow``): convert many images to PNG, JPEG, WebP, GIF, BMP or TIFF.

Quality for JPEG / WebP, background colour for formats without transparency (JPEG, BMP), WebP lossless, output
folder and file name (``$filename``). The preview shows the selected image as it will be written, with the
encoded size. The default format comes from the task settings' tool options.
"""
from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Adw
from PIL import Image

from ...core.enums import EImageFormat
from ...core.jobs import JobContext
from ...tools import imagetools
from .. import gtkutil
from . import _base
from ._batch import BatchImageTool, open_batch_tool
from .resizer import flatten

FORMATS = ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"]
LABELS = ["PNG", "JPEG", "WebP", "GIF", "BMP", "TIFF"]
EXTS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tif"}


@dataclass
class ConvertOptions:
    fmt: str = "PNG"
    quality: int = 90
    background: str = "#FFFFFFFF"
    webp_lossless: bool = False


def encode(img: Image.Image, o: ConvertOptions) -> bytes:
    """The converted file's bytes."""
    if o.fmt in ("JPEG", "BMP"):
        img = flatten(img, o.background)
    if o.fmt == "WEBP" and o.webp_lossless:
        buf = io.BytesIO()
        img.save(buf, "WEBP", lossless=True)
        return buf.getvalue()
    # save_image picks the per-format options (GIF palette, TIFF compression, quality)
    with tempfile.TemporaryDirectory(prefix="sharel-convert-") as d:
        return imagetools.save_image(img, Path(d) / f"x.{EXTS[o.fmt]}", o.fmt, o.quality).read_bytes()


class ConverterWindow(BatchImageTool):
    TITLE = "Image converter"
    RUN_LABEL = "Convert"
    PATTERN = "$filename"

    def __init__(self, app, ctx: JobContext) -> None:
        fmt = ctx.task_settings.tool_settings.image_converter_format
        fmt = fmt.value if isinstance(fmt, EImageFormat) else str(fmt)
        self.opts = ConvertOptions(fmt if fmt in FORMATS else "PNG")
        super().__init__(app, ctx)

    def build_options(self):
        o = self.opts

        def set_(name, v):
            setattr(o, name, v)
            self._sync_rows()
            self.options_changed()
        g = Adw.PreferencesGroup(title="Format")
        self.format_row = _base.combo_row("Output format", LABELS, FORMATS.index(o.fmt),
                                          lambda i: set_("fmt", FORMATS[i]))
        self.quality_row = _base.spin_row("Quality", o.quality, 1, 100, lambda v: set_("quality", int(v)))
        self.lossless_row = _base.switch_row("WebP lossless", o.webp_lossless, lambda v: set_("webp_lossless", v))
        self.background_row = _base.color_row("Background color", o.background, lambda c: set_("background", c))
        for r in (self.format_row, self.quality_row, self.lossless_row, self.background_row):
            g.add(r)
        self._sync_rows()
        return [g]

    def _sync_rows(self) -> None:
        if hasattr(self, "background_row"):
            f = self.opts.fmt
            self.quality_row.set_visible(f in ("JPEG", "WEBP") and not (f == "WEBP" and self.opts.webp_lossless))
            self.lossless_row.set_visible(f == "WEBP")
            self.background_row.set_visible(f in ("JPEG", "BMP"))

    def snapshot(self):
        return ConvertOptions(**vars(self.opts))

    def save_defaults(self, o) -> None:
        self.tools.image_converter_format = EImageFormat[o.fmt]

    def output_ext(self, src, o) -> str:
        return EXTS[o.fmt]

    def render_preview(self, img, o):
        data = encode(img, o)
        out = Image.open(io.BytesIO(data))
        out.load()
        out = out.convert("RGBA")
        out.info["encoded_size"] = len(data)
        return out

    def _preview_show(self, img) -> None:
        self.preview_image = img
        size = img.info.get("encoded_size", 0)
        self.preview_pane.set_image(img, f"{img.width} × {img.height} — {gtkutil.human_size(size)} as "
                                         f"{LABELS[FORMATS.index(self.opts.fmt)]}")

    def process(self, path, o, cancel):
        data = encode(_base.load_image_file(path), o)
        dst = self.target_path(path, o)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return dst

    def describe_result(self, r) -> str:
        return f"Converted → {r.name}"


def open(app, ctx: JobContext) -> ConverterWindow:  # noqa: A001
    return open_batch_tool(ConverterWindow, app, ctx)
