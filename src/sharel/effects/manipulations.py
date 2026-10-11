"""Manipulations (ShareX.ImageEffectsLib/Manipulations): size, crop and geometry changes."""
# param() is a dataclass field specifier (it returns dataclasses.field), not a shared default:
# ruff: noqa: RUF009
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .base import ImageEffect, effect, param
from .common import (ints, ALL_SIDES, SIDES, add_canvas, apply_aspect_ratio, crop_bitmap, draw_over,
                     find_auto_crop_rect, parse_color, parse_flags, rounded_corners)

_BICUBIC = Image.Resampling.BICUBIC


def _padding_summary(m: list[int]) -> str:
    """ShareX Padding.All is -1 unless all four sides are equal."""
    return str(m[0]) if len(set(m)) == 1 else f"{m[0]}, {m[1]}, {m[2]}, {m[3]}"


def premul_transform(img: Image.Image, size: tuple[int, int], data) -> Image.Image:
    """Affine resample in premultiplied space (GDI+ blends like this; straight RGBA would fringe)."""
    out = img.convert("RGBa").transform(size, Image.Transform.AFFINE, data, resample=_BICUBIC,
                                        fillcolor=(0, 0, 0, 0))
    return out.convert("RGBA")


def premul_resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.convert("RGBa").resize(size, _BICUBIC).convert("RGBA")


def resize_image(img: Image.Image, width: int, height: int) -> Image.Image:
    """ShareX ``ResizeImage(bmp, width, height)`` (HighQualityBicubic); unchanged when a side < 1."""
    if width < 1 or height < 1 or (img.width == width and img.height == height):
        return img
    return premul_resize(img, (width, height))


@effect("Manipulations", "Auto crop")
@dataclass
class AutoCrop(ImageEffect):
    sides: str = param(ALL_SIDES, label="Sides", kind="flags", choices=SIDES)
    padding: int = param(0, label="Padding", min=0, max=1000)

    def apply(self, img: Image.Image) -> Image.Image:
        sides = parse_flags(self.sides)
        rect = find_auto_crop_rect(img, True, sides)
        if rect == (0, 0, img.width, img.height):
            return img
        cropped = crop_bitmap(img, *rect)
        if cropped is None:
            return img
        if self.padding > 0:
            r, g, b, a = img.getpixel((0, 0))
            out = add_canvas(cropped, [self.padding] * 4, f"#{a:02X}{r:02X}{g:02X}{b:02X}")
            return out if out is not None else cropped
        return cropped

    def summary(self) -> str:
        return str(self.padding) if self.padding > 0 else ""


@effect("Manipulations")
@dataclass
class Canvas(ImageEffect):
    margin: list[int] = param(label="Margin", kind="padding", default_factory=lambda: [0, 0, 0, 0],
                              help="Left, top, right, bottom")
    margin_mode: str = param("AbsoluteSize", label="Margin mode", kind="choice",
                             choices=["AbsoluteSize", "PercentageOfCanvas"],
                             help="How the margin around the canvas will be calculated.")
    color: str = param("#00FFFFFF", label="Color", kind="color")

    def apply(self, img: Image.Image) -> Image.Image:
        left, top, right, bottom = ints(self.margin, 4)
        if self.margin_mode == "PercentageOfCanvas":
            left = round(left / 100 * img.width)
            right = round(right / 100 * img.width)
            top = round(top / 100 * img.height)
            bottom = round(bottom / 100 * img.height)
        out = add_canvas(img, [left, top, right, bottom], self.color)
        return img if out is None else out

    def summary(self) -> str:
        return _padding_summary(ints(self.margin, 4))


@effect("Manipulations")
@dataclass
class Crop(ImageEffect):
    margin: list[int] = param(label="Margin", kind="padding", min=0, default_factory=lambda: [0, 0, 0, 0],
                              help="Left, top, right, bottom")

    def apply(self, img: Image.Image) -> Image.Image:
        left, top, right, bottom = ints(self.margin, 4)
        if min(left, top, right, bottom) < 0 or left == top == right == bottom == 0:
            return img
        out = crop_bitmap(img, left, top, img.width - left - right, img.height - top - bottom)
        return img if out is None else out

    def summary(self) -> str:
        return _padding_summary(ints(self.margin, 4))


@effect("Manipulations")
@dataclass
class Flip(ImageEffect):
    horizontally: bool = param(False, label="Horizontally")
    vertically: bool = param(False, label="Vertically")

    def apply(self, img: Image.Image) -> Image.Image:
        if self.horizontally:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.vertically:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return img

    def summary(self) -> str:
        return f"{self.horizontally}, {self.vertically}"


@effect("Manipulations", "Force proportions")
@dataclass
class ForceProportions(ImageEffect):
    proportional_width: int = param(1, label="Proportional width", min=1, max=10000)
    proportional_height: int = param(1, label="Proportional height", min=1, max=10000)
    method: str = param("Grow", label="Method", kind="choice", choices=["Grow", "Crop"])
    grow_fill_color: str = param("#00FFFFFF", label="Grow fill color", kind="color")

    def apply(self, img: Image.Image) -> Image.Image:
        pw = max(1, self.proportional_width)
        ph = max(1, self.proportional_height)
        current = img.width / img.height
        target = pw / ph
        wider = target > current
        tw, th = img.width, img.height
        if self.method == "Crop":
            ml = mt = 0
            if wider:
                th = round(img.width / target)
                mt = (img.height - th) // 2
            else:
                tw = round(img.height * target)
                ml = (img.width - tw) // 2
            out = crop_bitmap(img, ml, mt, tw, th)
            return img if out is None else out
        if wider:
            tw = round(img.height * target)
        else:
            th = round(img.width / target)
        # ResizeImage(bmp, w, h, allowEnlarge: false, centerImage: true, GrowFillColor)
        out = Image.new("RGBA", (tw, th), parse_color(self.grow_fill_color) if parse_color(self.grow_fill_color)[3] > 0
                        else (0, 0, 0, 0))
        if img.width <= tw and img.height <= th:
            ratio = 1.0
            nw, nh = img.width, img.height
            src = img
        else:
            ratio = min(tw / img.width, th / img.height)
            nw, nh = int(img.width * ratio), int(img.height * ratio)
            src = premul_resize(img, (max(1, nw), max(1, nh)))
        x = int((tw - img.width * ratio) / 2)
        y = int((th - img.height * ratio) / 2)
        return draw_over(out, src, x, y)

    def summary(self) -> str:
        return f"{self.proportional_width}, {self.proportional_height}"


@effect("Manipulations")
@dataclass
class Resize(ImageEffect):
    width: int = param(250, label="Width", min=0, max=100000,
                       help="Use width as 0 to automatically adjust width to maintain aspect ratio.")
    height: int = param(0, label="Height", min=0, max=100000,
                        help="Use height as 0 to automatically adjust height to maintain aspect ratio.")
    mode: str = param("ResizeAll", label="Mode", kind="choice",
                      choices=["ResizeAll", "ResizeIfBigger", "ResizeIfSmaller"])

    def apply(self, img: Image.Image) -> Image.Image:
        if self.width <= 0 and self.height <= 0:
            return img
        w, h = apply_aspect_ratio(self.width, self.height, img.width, img.height)
        if self.mode == "ResizeIfBigger" and img.width <= w and img.height <= h:
            return img
        if self.mode == "ResizeIfSmaller" and img.width >= w and img.height >= h:
            return img
        return resize_image(img, w, h)

    def summary(self) -> str:
        w = f"{self.width}px" if self.width > 0 else str(self.width)
        h = f"{self.height}px" if self.height > 0 else str(self.height)
        return f"{w}, {h}"


def rotate_image(img: Image.Image, angle: float, upsize: bool, clip: bool) -> Image.Image:
    """ShareX ``ImageHelpers.RotateImage`` (positive angles turn clockwise):
    upsize -> bigger canvas, no clipping; not upsize + clip -> same size, clipped;
    neither -> same size, image scaled down to fit."""
    if angle == 0:
        return img.copy()
    w, h = img.size
    nw, nh = w, h
    scale = 1.0
    if upsize or not clip:
        rad = angle * math.pi / 180
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        nw = round(w * c + h * s)
        nh = round(w * s + h * c)
    if not upsize and not clip:
        scale = min(w / nw, h / nh)
        nw, nh = w, h
    rad = angle * math.pi / 180
    cos, sin = math.cos(rad), math.sin(rad)
    cx1, cy1 = w / 2, h / 2
    cx2, cy2 = nw / 2, nh / 2
    # inverse of: p = C2 + scale * R(angle) * (q - C1)
    a, b = cos / scale, sin / scale
    d, e = -sin / scale, cos / scale
    cc = cx1 - (a * cx2 + b * cy2)
    f = cy1 - (d * cx2 + e * cy2)
    return premul_transform(img, (nw, nh), (a, b, cc, d, e, f))


@effect("Manipulations")
@dataclass
class Rotate(ImageEffect):
    angle: float = param(0.0, label="Angle", min=-360, max=360, step=1, help="Choose a value between -360 and 360.")
    upsize: bool = param(True, label="Upsize",
                         help="If true, output image will be larger than the input and no clipping will occur.")
    clip: bool = param(False, label="Clip",
                       help="Upsize must be false for this setting to work. If true, clipping will occur or else "
                            "image size will be reduced.")

    def apply(self, img: Image.Image) -> Image.Image:
        if self.angle == 0:
            return img
        return rotate_image(img, self.angle, self.upsize, self.clip)

    def summary(self) -> str:
        return f"{self.angle:g}°"


@effect("Manipulations", "Rounded corners")
@dataclass
class RoundedCorners(ImageEffect):
    corner_radius: int = param(20, label="Corner radius", min=0, max=1000)

    def apply(self, img: Image.Image) -> Image.Image:
        return rounded_corners(img, max(0, self.corner_radius))

    def summary(self) -> str:
        return str(self.corner_radius)


@effect("Manipulations")
@dataclass
class Scale(ImageEffect):
    width_percentage: float = param(100.0, label="Width percentage", min=0, max=10000, step=1,
                                    help="Use width percentage as 0 to maintain aspect ratio by automatically "
                                         "adjusting width.")
    height_percentage: float = param(0.0, label="Height percentage", min=0, max=10000, step=1,
                                     help="Use height percentage as 0 to maintain aspect ratio by automatically "
                                          "adjusting height.")

    def apply(self, img: Image.Image) -> Image.Image:
        if self.width_percentage <= 0 and self.height_percentage <= 0:
            return img
        w = round(self.width_percentage / 100 * img.width)
        h = round(self.height_percentage / 100 * img.height)
        w, h = apply_aspect_ratio(w, h, img.width, img.height)
        return resize_image(img, w, h)

    def summary(self) -> str:
        w = f"{self.width_percentage:g}%" if self.width_percentage > 0 else f"{self.width_percentage:g}"
        h = f"{self.height_percentage:g}%" if self.height_percentage > 0 else f"{self.height_percentage:g}"
        return f"{w}, {h}"


def add_skew(img: Image.Image, x: int, y: int) -> Image.Image:
    """ShareX ``ImageHelpers.AddSkew``: draw into a parallelogram on a canvas grown by |x|, |y|."""
    w, h = img.size
    start_x, start_y = -min(0, x), -min(0, y)
    end_x, end_y = max(0, x), max(0, y)
    p0 = np.array([start_x, start_y], float)
    p1 = np.array([start_x + w - 1, end_y], float)
    p2 = np.array([end_x, start_y + h - 1], float)
    # forward: p = p0 + A @ q with A columns (p1 - p0) / w and (p2 - p0) / h
    fwd = np.column_stack([(p1 - p0) / w, (p2 - p0) / h])
    inv = np.linalg.inv(fwd)
    off = -inv @ p0
    data = (inv[0, 0], inv[0, 1], off[0], inv[1, 0], inv[1, 1], off[1])
    return premul_transform(img, (w + abs(x), h + abs(y)), data)


@effect("Manipulations")
@dataclass
class Skew(ImageEffect):
    horizontally: int = param(0, label="Horizontally", min=-10000, max=10000, help="How much pixel skew left to right.")
    vertically: int = param(0, label="Vertically", min=-10000, max=10000, help="How much pixel skew top to bottom.")

    def apply(self, img: Image.Image) -> Image.Image:
        if self.horizontally == 0 and self.vertically == 0:
            return img
        return add_skew(img, self.horizontally, self.vertically)

    def summary(self) -> str:
        return f"{self.horizontally}px, {self.vertically}px"
