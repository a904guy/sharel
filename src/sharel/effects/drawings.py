"""Drawings (ShareX.ImageEffectsLib/Drawings): backgrounds, borders, watermarks, particles and text."""
# param() is a dataclass field specifier (it returns dataclasses.field), not a shared default:
# ruff: noqa: RUF009
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import cairo
import numpy as np
from PIL import Image

from .base import ImageEffect, effect, param
from .common import (ints, DASH_STYLES, PLACEMENTS, GradientInfo, default_blue_gradient, default_two_color_gradient,
                     draw_background_image, draw_copy, draw_over, expand_path, fill_with, from_surface,
                     gradient_array, layout_path, load_image, pango_layout, parse_color, parse_font, parse_text,
                     rand_inclusive, rect_contains, rounded_rect_path, set_dash, show_layout,
                     to_surface)
from .common import apply_aspect_ratio as _aspect
from .common import get_position as _position

_RESAMPLE = {
    "HighQualityBicubic": Image.Resampling.BICUBIC,
    "Bicubic": Image.Resampling.BICUBIC,
    "HighQualityBilinear": Image.Resampling.BILINEAR,
    "Bilinear": Image.Resampling.BILINEAR,
    "NearestNeighbor": Image.Resampling.NEAREST,
}

ROTATE_FLIP_TYPES = ["None", "Rotate90", "Rotate180", "Rotate270", "FlipX", "Rotate90FlipX", "FlipY", "Rotate90FlipY"]


def _file_summary(path: str) -> str:
    return os.path.basename(path.replace("\\", "/")) if path else ""


def _color_summary(color: str) -> str:
    r, g, b, _ = parse_color(color)
    return f"{r}, {g}, {b}"


def _truncate(text: str, n: int = 20) -> str:
    return text if len(text) <= n else text[:n] + "..."


def set_brush(cr: cairo.Context, width: int, height: int, color: str, gradient: GradientInfo | None = None,
              rect: tuple[float, float, float, float] | None = None) -> None:
    """Make a solid colour, or a GDI+ linear gradient over ``rect`` (canvas coordinates), the source."""
    if gradient is not None and gradient.is_valid:
        arr = gradient_array(width, height, gradient, rect)
        grad_img = Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), "RGBA")
        surface = to_surface(grad_img)
        cr.set_source_surface(surface, 0, 0)
        return
    r, g, b, a = parse_color(color)
    cr.set_source_rgba(r / 255, g / 255, b / 255, a / 255)


def blank_surface(width: int, height: int) -> cairo.ImageSurface:
    """A clear drawing layer. Effects draw onto it and composite it over the source image, so source
    pixels never go through cairo's premultiplied format (source-over is the same either way)."""
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, width), max(1, height))


def composite_layer(img: Image.Image, surface: cairo.ImageSurface) -> Image.Image:
    return Image.alpha_composite(img, from_surface(surface))


def rotate_flip(img: Image.Image, mode: str) -> Image.Image:
    """GDI+ ``RotateFlip``: rotate clockwise first, then flip."""
    T = Image.Transpose
    rot = {"Rotate90": T.ROTATE_270, "Rotate180": T.ROTATE_180, "Rotate270": T.ROTATE_90,
           "Rotate90FlipX": T.ROTATE_270, "Rotate90FlipY": T.ROTATE_270}.get(mode)
    flip = {"FlipX": T.FLIP_LEFT_RIGHT, "Rotate90FlipX": T.FLIP_LEFT_RIGHT,
            "FlipY": T.FLIP_TOP_BOTTOM, "Rotate90FlipY": T.FLIP_TOP_BOTTOM}.get(mode)
    if rot is not None:
        img = img.transpose(rot)
    if flip is not None:
        img = img.transpose(flip)
    return img


@effect("Drawings", "Background")
@dataclass
class DrawBackground(ImageEffect):
    color: str = param("#FF000000", label="Color", kind="color")
    use_gradient: bool = param(False, label="Use gradient")
    gradient: GradientInfo = param(label="Gradient", kind="gradient", default_factory=default_blue_gradient)

    def apply(self, img: Image.Image) -> Image.Image:
        if self.use_gradient and self.gradient is not None and self.gradient.is_valid:
            fill = gradient_array(img.width, img.height, self.gradient)
        else:
            fill = np.empty((img.height, img.width, 4), np.float32)
            fill[:] = parse_color(self.color)
        return fill_with(img, fill)

    def summary(self) -> str:
        return "" if self.use_gradient else _color_summary(self.color)


@effect("Drawings", "Background image")
@dataclass
class DrawBackgroundImage(ImageEffect):
    image_file_path: str = param("", label="Image file path", kind="file")
    center: bool = param(True, label="Center")
    tile: bool = param(False, label="Tile")

    def apply(self, img: Image.Image) -> Image.Image:
        return draw_background_image(img, load_image(self.image_file_path), self.center, self.tile)

    def summary(self) -> str:
        return _file_summary(self.image_file_path)


@effect("Drawings", "Border")
@dataclass
class DrawBorder(ImageEffect):
    # "type" is the effect's own key in our preset JSON, so the ShareX ``Type`` property is renamed here.
    border_type: str = param("Outside", label="Type", kind="choice", choices=["Outside", "Inside"], sharex="Type")
    size: int = param(1, label="Size", min=1, max=1000)
    dash_style: str = param("Solid", label="Dash style", kind="choice", choices=DASH_STYLES)
    color: str = param("#FF000000", label="Color", kind="color")
    use_gradient: bool = param(False, label="Use gradient")
    gradient: GradientInfo = param(label="Gradient", kind="gradient", default_factory=default_blue_gradient)

    def apply(self, img: Image.Image) -> Image.Image:
        size = max(1, self.size)
        if self.border_type == "Inside":
            out = img.copy()
        else:
            out = Image.new("RGBA", (img.width + size * 2, img.height + size * 2), (0, 0, 0, 0))
        w, h = out.size
        surface = blank_surface(w, h)
        cr = cairo.Context(surface)
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        grad = self.gradient if self.use_gradient else None
        set_brush(cr, w, h, self.color, grad)
        # Inset pen: the band covers the outermost ``size`` pixels.
        cr.rectangle(size / 2, size / 2, w - size, h - size)
        cr.set_line_width(size)
        cr.set_line_join(cairo.LINE_JOIN_MITER)
        set_dash(cr, self.dash_style, size)
        cr.stroke()
        out = composite_layer(out, surface)
        if self.border_type != "Inside":
            draw_over(out, img, size, size)
        return out

    def summary(self) -> str:
        return f"{max(1, self.size)}px"


@effect("Drawings", "Checkerboard")
@dataclass
class DrawCheckerboard(ImageEffect):
    size: int = param(10, label="Size", min=1, max=1000)
    color: str = param("#FFD3D3D3", label="Color", kind="color")
    color2: str = param("#FFFFFFFF", label="Color 2", kind="color")

    def apply(self, img: Image.Image) -> Image.Image:
        size = max(1, self.size)
        ys = (np.arange(img.height) // size) % 2
        xs = (np.arange(img.width) // size) % 2
        second = (ys[:, None] ^ xs[None, :]).astype(bool)
        fill = np.empty((img.height, img.width, 4), np.float32)
        fill[:] = parse_color(self.color)
        fill[second] = parse_color(self.color2)
        return fill_with(img, fill)

    def summary(self) -> str:
        return f"{self.size}x{self.size}"


@effect("Drawings", "Image")
@dataclass
class DrawImage(ImageEffect):
    image_location: str = param("", label="Image location", kind="file")
    placement: str = param("TopLeft", label="Placement", kind="choice", choices=PLACEMENTS)
    offset: list[int] = param(label="Offset", kind="point", default_factory=lambda: [0, 0])
    size_mode: str = param("DontResize", label="Size mode", kind="choice",
                           choices=["DontResize", "AbsoluteSize", "PercentageOfWatermark", "PercentageOfCanvas"],
                           help="How the image watermark should be rescaled, if at all.")
    size: list[int] = param(label="Size", kind="size", default_factory=lambda: [0, 0],
                            help="-1 = canvas size (absolute mode); 0 keeps the aspect ratio")
    rotate_flip: str = param("None", label="Rotate flip", kind="choice", choices=ROTATE_FLIP_TYPES)
    tile: bool = param(False, label="Tile")
    auto_hide: bool = param(False, label="Auto hide",
                            help="If image watermark size bigger than source image then don't draw it.")
    interpolation_mode: str = param("HighQualityBicubic", label="Interpolation mode", kind="choice",
                                    choices=list(_RESAMPLE))
    compositing_mode: str = param("SourceOver", label="Compositing mode", kind="choice",
                                  choices=["SourceOver", "SourceCopy"])
    opacity: int = param(100, label="Opacity", min=0, max=100)

    def apply(self, img: Image.Image) -> Image.Image:
        opacity = min(max(self.opacity, 0), 100)
        sw, sh = ints(self.size, 2)
        if opacity < 1 or (self.size_mode != "DontResize" and sw <= 0 and sh <= 0):
            return img
        wm = load_image(self.image_location)
        if wm is None:
            return img
        if self.rotate_flip != "None":
            wm = rotate_flip(wm, self.rotate_flip)
        if self.size_mode == "AbsoluteSize":
            w = img.width if sw == -1 else sw
            h = img.height if sh == -1 else sh
            size = _aspect(w, h, wm.width, wm.height)
        elif self.size_mode == "PercentageOfWatermark":
            size = _aspect(round(sw / 100 * wm.width), round(sh / 100 * wm.height), wm.width, wm.height)
        elif self.size_mode == "PercentageOfCanvas":
            size = _aspect(round(sw / 100 * img.width), round(sh / 100 * img.height), wm.width, wm.height)
        else:
            size = wm.size
        if size[0] < 1 or size[1] < 1:
            return img
        x, y = _position(self.placement, self.offset, img.size, size)
        if self.auto_hide and not rect_contains(img.width, img.height, x, y, size[0], size[1]):
            return img
        out = img.copy()
        if self.tile:
            # TextureBrush of the unscaled watermark, translated to the rectangle origin.
            layer = Image.new("RGBA", size, (0, 0, 0, 0))
            for ty in range(0, size[1], wm.height):
                for tx in range(0, size[0], wm.width):
                    layer.paste(wm, (tx, ty))
        else:
            layer = wm if wm.size == tuple(size) else wm.convert("RGBa").resize(
                size, _RESAMPLE.get(self.interpolation_mode, Image.Resampling.BICUBIC)).convert("RGBA")
            if opacity < 100:
                arr = np.array(layer)
                arr[..., 3] = np.rint(arr[..., 3] * (opacity / 100)).astype(np.uint8)
                layer = Image.fromarray(arr, "RGBA")
        if self.compositing_mode == "SourceCopy":
            return draw_copy(out, layer, x, y)
        return draw_over(out, layer, x, y)

    def summary(self) -> str:
        return _file_summary(self.image_location)


@effect("Drawings", "Particles")
@dataclass
class DrawParticles(ImageEffect):
    image_folder: str = param("", label="Image folder", kind="folder")
    image_count: int = param(1, label="Image count", min=1, max=1000)
    background: bool = param(False, label="Background")
    random_size: bool = param(False, label="Random size")
    random_size_min: int = param(64, label="Random size min", min=1, max=10000)
    random_size_max: int = param(128, label="Random size max", min=1, max=10000)
    random_angle: bool = param(False, label="Random angle")
    random_angle_min: int = param(0, label="Random angle min", min=-360, max=360)
    random_angle_max: int = param(360, label="Random angle max", min=-360, max=360)
    random_opacity: bool = param(False, label="Random opacity")
    random_opacity_min: int = param(0, label="Random opacity min", min=0, max=100)
    random_opacity_max: int = param(100, label="Random opacity max", min=0, max=100)
    no_overlap: bool = param(False, label="No overlap")
    no_overlap_offset: int = param(0, label="No overlap offset", min=-1000, max=1000)
    edge_overlap: bool = param(False, label="Edge overlap")

    def _files(self) -> list[str]:
        folder = expand_path(self.image_folder)
        if not folder or not os.path.isdir(folder):
            return []
        return sorted(str(p) for p in Path(folder).iterdir()
                      if p.is_file() and p.suffix.lower() in (".png", ".jpg"))

    def apply(self, img: Image.Image) -> Image.Image:
        files = self._files()
        if self.background:
            canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
            canvas = self._draw_particles(canvas, files)
            return draw_over(canvas, img, 0, 0)
        return self._draw_particles(img.copy(), files)

    def _draw_particles(self, canvas: Image.Image, files: list[str]) -> Image.Image:
        if not files:
            return canvas
        cache: dict[str, Image.Image | None] = {}
        rects: list[tuple[int, int, int, int]] = []
        surface = blank_surface(*canvas.size)
        cr = cairo.Context(surface)
        count = min(max(self.image_count, 1), 1000)
        for _ in range(count):
            path = files[rand_inclusive(0, len(files) - 1)]
            if path not in cache:
                cache[path] = load_image(path)
            particle = cache[path]
            if particle is not None:
                self._draw_one(cr, canvas.size, particle, rects)
        return composite_layer(canvas, surface)

    def _draw_one(self, cr: cairo.Context, canvas_size: tuple[int, int], particle: Image.Image,
                  rects: list[tuple[int, int, int, int]]) -> None:
        if self.random_size:
            size = rand_inclusive(self.random_size_min, self.random_size_max)
            width = height = size
            if particle.width > particle.height:
                height = round(size * (particle.height / particle.width))
            elif particle.width < particle.height:
                width = round(size * (particle.width / particle.height))
        else:
            width, height = particle.size
        if width < 1 or height < 1:
            return
        cw, ch = canvas_size
        min_x = -width + 1 if self.edge_overlap else 0
        min_y = -height + 1 if self.edge_overlap else 0
        max_x = cw - (0 if self.edge_overlap else width) - 1
        max_y = ch - (0 if self.edge_overlap else height) - 1
        attempts = 0
        while True:
            attempts += 1
            if attempts > 1000:
                return
            x = rand_inclusive(min_x, max_x)
            y = rand_inclusive(min_y, max_y)
            o = self.no_overlap_offset
            test = (x - o, y - o, width + 2 * o, height + 2 * o)
            if not self.no_overlap or not any(_intersects(r, test) for r in rects):
                break
        rects.append((x, y, width, height))
        cr.save()
        if self.random_angle:
            cx, cy = x + width / 2, y + height / 2
            angle = rand_inclusive(self.random_angle_min, self.random_angle_max)
            cr.translate(cx, cy)
            cr.rotate(math.radians(angle))
            cr.translate(-cx, -cy)
        cr.translate(x, y)
        cr.scale(width / particle.width, height / particle.height)
        cr.set_source_surface(to_surface(particle), 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BEST)
        if self.random_opacity:
            opacity = min(max(rand_inclusive(self.random_opacity_min, self.random_opacity_max), 0), 100) / 100
            cr.paint_with_alpha(opacity)
        else:
            cr.paint()
        cr.restore()

    def summary(self) -> str:
        return _file_summary(self.image_folder.rstrip("/\\"))


def _intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@effect("Drawings", "Text watermark")
@dataclass
class DrawText(ImageEffect):
    text: str = param("Text watermark", label="Text", kind="text")
    placement: str = param("BottomRight", label="Placement", kind="choice", choices=PLACEMENTS)
    offset: list[int] = param(label="Offset", kind="point", default_factory=lambda: [5, 5])
    auto_hide: bool = param(False, label="Auto hide",
                            help="If text watermark size bigger than source image then don't draw it.")
    text_font: str = param("Arial, 11.25pt", label="Text font", kind="font")
    text_rendering_mode: str = param("SystemDefault", label="Text rendering mode", kind="choice",
                                     choices=["SystemDefault", "SingleBitPerPixelGridFit", "SingleBitPerPixel",
                                              "AntiAliasGridFit", "AntiAlias", "ClearTypeGridFit"])
    text_color: str = param("#FFEBEBEB", label="Text color", kind="color")
    draw_text_shadow: bool = param(True, label="Draw text shadow")
    text_shadow_color: str = param("#FF000000", label="Text shadow color", kind="color")
    text_shadow_offset: list[int] = param(label="Text shadow offset", kind="point", default_factory=lambda: [-1, -1])
    corner_radius: int = param(4, label="Corner radius", min=0, max=1000)
    padding: list[int] = param(label="Padding", kind="padding", default_factory=lambda: [5, 5, 5, 5])
    draw_border: bool = param(True, label="Draw border")
    border_color: str = param("#FF000000", label="Border color", kind="color")
    border_size: int = param(1, label="Border size", min=1, max=100)
    draw_background: bool = param(True, label="Draw background")
    background_color: str = param("#FF2A2F38", label="Background color", kind="color")
    use_gradient: bool = param(False, label="Use gradient")
    gradient: GradientInfo = param(label="Gradient", kind="gradient", default_factory=default_blue_gradient)

    def _antialias(self) -> cairo.Antialias:
        if self.text_rendering_mode in ("SingleBitPerPixelGridFit", "SingleBitPerPixel"):
            return cairo.ANTIALIAS_NONE
        return cairo.ANTIALIAS_GRAY

    def watermark_rect(self, img_size: tuple[int, int], text: str) -> tuple[int, int, int, int] | None:
        """(x, y, w, h) of the watermark box, or None when there is nothing to draw."""
        spec = parse_font(self.text_font)
        if not text or spec.size < 1:
            return None
        scratch = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        tw, th = pango_layout(scratch, text, spec).get_pixel_size()
        pl, pt, pr, pb = ints(self.padding, 4)
        size = (pl + tw + pr, pt + th + pb)
        x, y = _position(self.placement, self.offset, img_size, size)
        return x, y, size[0], size[1]

    def apply(self, img: Image.Image) -> Image.Image:
        if not self.text:
            return img
        text = parse_text(self.text, img.width, img.height)
        rect = self.watermark_rect(img.size, text)
        if rect is None:
            return img
        x, y, w, h = rect
        if self.auto_hide and not rect_contains(img.width, img.height, x, y, w, h):
            return img
        spec = parse_font(self.text_font)
        surface = blank_surface(*img.size)
        cr = cairo.Context(surface)
        # GDI+ PixelOffsetMode.Default: integer coordinates are pixel centres (cairo: +0.5). Paths are
        # built in that shifted space; the brush stays in device space.
        # AddRoundedRectangleProper(rect, radius) with the default 1px pen shrinks the rect by 1.
        if self.draw_background:
            cr.save()
            cr.translate(0.5, 0.5)
            rounded_rect_path(cr, x, y, w - 1, h - 1, self.corner_radius)
            cr.restore()
            grad = self.gradient if self.use_gradient else None
            set_brush(cr, img.width, img.height, self.background_color, grad, (x, y, w, h))
            cr.fill()
        if self.draw_border:
            bsize = max(1, self.border_size)
            cr.save()
            if bsize % 2 == 1:  # even pens use PixelOffsetMode.Half (no shift)
                cr.translate(0.5, 0.5)
            rounded_rect_path(cr, x, y, w - 1, h - 1, self.corner_radius)
            cr.restore()
            r, g, b, a = parse_color(self.border_color)
            cr.set_source_rgba(r / 255, g / 255, b / 255, a / 255)
            cr.set_line_width(bsize)
            cr.stroke()
        pl, pt = ints(self.padding, 2)
        font_options = cairo.FontOptions()
        font_options.set_antialias(self._antialias())
        cr.set_font_options(font_options)
        layout = pango_layout(cr, text, spec)
        if self.draw_text_shadow:
            sx, sy = ints(self.text_shadow_offset, 2)
            r, g, b, a = parse_color(self.text_shadow_color)
            cr.set_source_rgba(r / 255, g / 255, b / 255, a / 255)
            cr.move_to(x + pl + sx, y + pt + sy)
            show_layout(cr, layout)
        r, g, b, a = parse_color(self.text_color)
        cr.set_source_rgba(r / 255, g / 255, b / 255, a / 255)
        cr.move_to(x + pl, y + pt)
        show_layout(cr, layout)
        return composite_layer(img, surface)

    def summary(self) -> str:
        return _truncate(self.text) if self.text else ""


def _inflate(rect: tuple[float, float, float, float], n: float) -> tuple[float, float, float, float]:
    x, y, w, h = rect
    return (x - n, y - n, w + 2 * n, h + 2 * n)


@effect("Drawings", "Text")
@dataclass
class DrawTextEx(ImageEffect):
    text: str = param("Text", label="Text", kind="text")
    placement: str = param("TopLeft", label="Placement", kind="choice", choices=PLACEMENTS)
    offset: list[int] = param(label="Offset", kind="point", default_factory=lambda: [0, 0])
    angle: int = param(0, label="Angle", min=-360, max=360)
    auto_hide: bool = param(False, label="Auto hide", help="If text size bigger than source image then don't draw it.")
    font: str = param("Arial, 36pt", label="Font", kind="font")
    color: str = param("#FFEBEBEB", label="Color", kind="color")
    use_gradient: bool = param(False, label="Use gradient")
    gradient: GradientInfo = param(label="Gradient", kind="gradient",
                                   default_factory=lambda: default_two_color_gradient("Horizontal"))
    outline: bool = param(False, label="Outline")
    outline_size: int = param(5, label="Outline size", min=0, max=500)
    outline_color: str = param("#FFEB0000", label="Outline color", kind="color")
    outline_use_gradient: bool = param(False, label="Outline use gradient")
    outline_gradient: GradientInfo = param(label="Outline gradient", kind="gradient",
                                           default_factory=lambda: default_two_color_gradient("Horizontal"))
    shadow: bool = param(False, label="Shadow")
    shadow_offset: list[int] = param(label="Shadow offset", kind="point", default_factory=lambda: [0, 5])
    shadow_color: str = param("#7D000000", label="Shadow color", kind="color")
    shadow_use_gradient: bool = param(False, label="Shadow use gradient")
    shadow_gradient: GradientInfo = param(label="Shadow gradient", kind="gradient",
                                          default_factory=lambda: default_two_color_gradient("Horizontal"))

    def text_path(self, text: str):
        """The glyph outlines rotated by ``angle``; returns (cairo path, (x1, y1, x2, y2)) or None."""
        spec = parse_font(self.font)
        if spec.size < 1:
            return None
        scratch = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        layout = pango_layout(scratch, text, spec)
        scratch.save()
        if self.angle:
            scratch.rotate(math.radians(self.angle))
        layout_path(scratch, layout)
        scratch.restore()
        x1, y1, x2, y2 = scratch.path_extents()
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None
        return scratch.copy_path(), (x1, y1, x2, y2)

    def apply(self, img: Image.Image) -> Image.Image:
        if not self.text:
            return img
        text = parse_text(self.text, img.width, img.height)
        res = self.text_path(text)
        if res is None:
            return img
        path, (x1, y1, x2, y2) = res
        size = (int(x2 - x1) + 1, int(y2 - y1) + 1)
        tx, ty = _position(self.placement, self.offset, img.size, size)
        if self.auto_hide and not rect_contains(img.width, img.height, tx, ty, size[0], size[1]):
            return img
        trect = (tx, ty, size[0], size[1])
        W, H = img.size
        surface = blank_surface(*img.size)
        cr = cairo.Context(surface)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        dx, dy = tx - x1, ty - y1
        outline = self.outline and self.outline_size > 0

        def draw(offset_x: float, offset_y: float, stroke: bool, color: str, grad: GradientInfo | None,
                 brush_rect: tuple[float, float, float, float]) -> None:
            cr.save()
            set_brush(cr, W, H, color, grad, brush_rect)
            cr.translate(dx + offset_x, dy + offset_y)
            cr.new_path()
            cr.append_path(path)
            if stroke:
                cr.set_line_width(self.outline_size)
                cr.stroke()
            else:
                cr.fill()
            cr.restore()

        if self.shadow:
            sgrad = self.shadow_gradient if self.shadow_use_gradient else None
            visible = (sgrad.is_visible if sgrad is not None else parse_color(self.shadow_color)[3] > 0)
            if visible:
                sx, sy = ints(self.shadow_offset, 2)
                if outline:
                    r = _inflate(trect, self.outline_size + 1)
                    draw(sx, sy, True, self.shadow_color, sgrad, (r[0] + sx, r[1] + sy, r[2], r[3]))
                else:
                    r = _inflate(trect, 1)
                    draw(sx, sy, False, self.shadow_color, sgrad, (r[0] + sx, r[1] + sy, r[2], r[3]))
        if outline:
            if self.outline_use_gradient:
                if self.outline_gradient.is_visible:
                    draw(0, 0, True, self.outline_color, self.outline_gradient, _inflate(trect, self.outline_size + 1))
            elif parse_color(self.outline_color)[3] > 0:
                draw(0, 0, True, self.outline_color, None, trect)
        if self.use_gradient:
            if self.gradient.is_visible:
                draw(0, 0, False, self.color, self.gradient, _inflate(trect, 1))
        elif parse_color(self.color)[3] > 0:
            draw(0, 0, False, self.color, None, trect)
        return composite_layer(img, surface)

    def summary(self) -> str:
        return _truncate(self.text) if self.text else ""

