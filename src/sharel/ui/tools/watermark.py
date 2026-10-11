"""Image watermark (ShareX ``ImageWatermarkWindow``): stamp a text or image watermark on many images.

Text (size, colour, optional shadow) or image (scale = % of the canvas the mark may fill), position (nine
anchors), margin, opacity, rotation; output format (same as the source, PNG or JPEG with quality and
background colour), output folder and file name (``$filename_watermarked``). The preview shows the selected
image. Text and position defaults come from the task settings' tool options.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gi.repository import Adw, Gtk
from PIL import Image

from ...core.enums import ContentAlignment
from ...core.jobs import JobContext
from ...effects.common import parse_color
from ...tools import imagetools
from .. import gtkutil
from . import _base
from ._batch import BatchImageTool, open_batch_tool
from .resizer import flatten

TYPES = ["Text", "Image"]
POSITIONS = list(ContentAlignment)
POSITION_LABELS = ["Top left", "Top center", "Top right", "Middle left", "Middle center", "Middle right",
                   "Bottom left", "Bottom center", "Bottom right"]
FORMATS = ["Same as source", "PNG", "JPEG"]


@dataclass
class WatermarkOptions:
    kind: str = "Text"
    text: str = "Watermark"
    text_size: int = 48
    text_color: str = "#FFFFFFFF"
    shadow: bool = False
    image_path: str = ""
    image_scale: int = 25
    position: ContentAlignment = ContentAlignment.BottomRight
    margin: int = 24
    opacity: int = 70
    rotation: int = 0
    fmt: str = "Same as source"
    quality: int = 90
    background: str = "#FFFFFFFF"


def apply_watermark(img: Image.Image, o: WatermarkOptions, mark: Image.Image | None = None) -> Image.Image:
    if o.kind == "Image":
        if mark is None:
            if not o.image_path:
                raise ValueError("choose a watermark image")
            mark = _base.load_image_file(o.image_path)
        m = mark.convert("RGBA")
        if o.rotation % 360:
            m = m.rotate(-o.rotation, expand=True, resample=Image.Resampling.BICUBIC)
        return imagetools.watermark_image(img, m, o.position, o.margin, o.opacity, o.image_scale)
    return imagetools.watermark_text(img, o.text, o.position, o.margin, o.opacity, o.text_size,
                                     parse_color(o.text_color), o.rotation, o.shadow)


class WatermarkWindow(BatchImageTool):
    TITLE = "Image watermark"
    RUN_LABEL = "Apply watermark"
    PATTERN = "$filename_watermarked"

    def __init__(self, app, ctx: JobContext) -> None:
        t = ctx.task_settings.tool_settings
        pos = t.image_watermark_position
        self.opts = WatermarkOptions(text=t.image_watermark_text or "Watermark",
                                     position=pos if isinstance(pos, ContentAlignment) else ContentAlignment(pos))
        super().__init__(app, ctx)

    def build_options(self):
        o = self.opts

        def set_(name, v):
            setattr(o, name, v)
            self._sync_rows()
            self.options_changed()
        wm = Adw.PreferencesGroup(title="Watermark")
        self.type_row = _base.combo_row("Watermark type", TYPES, TYPES.index(o.kind), lambda i: set_("kind", TYPES[i]))
        self.text_row = _base.entry_row("Text", o.text, lambda t: set_("text", t))
        self.size_row = _base.spin_row("Text size", o.text_size, 1, 1000, lambda v: set_("text_size", int(v)))
        self.color_row = _base.color_row("Text color", o.text_color, lambda c: set_("text_color", c))
        self.shadow_row = _base.switch_row("Text shadow", o.shadow, lambda v: set_("shadow", v))
        self.image_row = _base.entry_row("Watermark image", o.image_path, lambda t: set_("image_path", t))
        browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
        browse.add_css_class("flat")
        browse.connect("clicked", lambda *_: gtkutil.open_files(
            self, "Watermark image", lambda ps: ps and self.image_row.set_text(ps[0]),
            patterns=gtkutil.IMAGE_PATTERNS, multiple=False))
        self.image_row.add_suffix(browse)
        self.scale_row = _base.spin_row("Image scale (%)", o.image_scale, 0, 100,
                                        lambda v: set_("image_scale", int(v)), subtitle="0 = original size")
        for r in (self.type_row, self.text_row, self.size_row, self.color_row, self.shadow_row, self.image_row,
                  self.scale_row):
            wm.add(r)
        place = Adw.PreferencesGroup(title="Placement")
        self.position_row = _base.combo_row("Position", POSITION_LABELS, POSITIONS.index(o.position),
                                            lambda i: set_("position", POSITIONS[i]))
        self.margin_row = _base.spin_row("Margin", o.margin, 0, 10000, lambda v: set_("margin", int(v)))
        self.opacity_row = _base.spin_row("Opacity (%)", o.opacity, 0, 100, lambda v: set_("opacity", int(v)))
        self.rotation_row = _base.spin_row("Rotation", o.rotation, -360, 360, lambda v: set_("rotation", int(v)))
        for r in (self.position_row, self.margin_row, self.opacity_row, self.rotation_row):
            place.add(r)
        fmt = Adw.PreferencesGroup(title="Format")
        self.format_row = _base.combo_row("Output format", FORMATS, 0, lambda i: set_("fmt", FORMATS[i]))
        self.quality_row = _base.spin_row("JPEG quality", o.quality, 1, 100, lambda v: set_("quality", int(v)))
        self.background_row = _base.color_row("Background color", o.background, lambda c: set_("background", c))
        for r in (self.format_row, self.quality_row, self.background_row):
            fmt.add(r)
        self._sync_rows()
        return [wm, place, fmt]

    def _sync_rows(self) -> None:
        if not hasattr(self, "background_row"):
            return
        text = self.opts.kind == "Text"
        for r in (self.text_row, self.size_row, self.color_row, self.shadow_row):
            r.set_visible(text)
        for r in (self.image_row, self.scale_row):
            r.set_visible(not text)
        jpeg = self.opts.fmt == "JPEG"
        self.quality_row.set_visible(jpeg)
        self.background_row.set_visible(jpeg)

    def snapshot(self):
        return WatermarkOptions(**vars(self.opts))

    def save_defaults(self, o) -> None:
        if o.kind == "Text":
            self.tools.image_watermark_text = o.text
        self.tools.image_watermark_position = o.position

    def output_ext(self, src, o) -> str:
        if o.fmt == "Same as source":
            ext = Path(src).suffix.lstrip(".").lower()
            return ext if ext in ("png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff") else "png"
        return "jpg" if o.fmt == "JPEG" else "png"

    def render_preview(self, img, o):
        return apply_watermark(img, o)

    def process(self, path, o, cancel):
        out = apply_watermark(_base.load_image_file(path), o)
        dst = self.target_path(path, o)
        if o.fmt == "JPEG":
            out = flatten(out, o.background)
            return imagetools.save_image(out, dst, "JPEG", o.quality)
        return imagetools.save_image(out, dst, None, o.quality)

    def describe_result(self, r) -> str:
        return f"Watermarked → {r.name}"


def open(app, ctx: JobContext) -> WatermarkWindow:  # noqa: A001
    return open_batch_tool(WatermarkWindow, app, ctx)
