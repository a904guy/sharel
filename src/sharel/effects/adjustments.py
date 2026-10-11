"""Adjustments (ShareX.ImageEffectsLib/Adjustments): colour matrices and per-pixel colour edits.

The matrices are ShareX's ``ColorMatrixManager`` ones, applied like GDI+ ``ImageAttributes``:
``[r g b a 1] @ M`` on straight 0..1 values, clamped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .base import ImageEffect, effect, param
from .common import color_matrix, from_array, parse_color, to_array

# ColorMatrixManager luminance weights
RW, GW, BW = 0.212671, 0.715160, 0.072169


def _fmt(v: float) -> str:
    """C# float.ToString(): ``1`` not ``1.0``."""
    return f"{v:g}"


def m_alpha(value: float, add: float = 0.0) -> list[list[float]]:
    return [[1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, value, 0], [0, 0, 0, add, 1]]


def m_brightness(value: float) -> list[list[float]]:
    return [[1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, 1, 0], [value, value, value, 0, 1]]


def m_contrast(value: float) -> list[list[float]]:
    return [[value, 0, 0, 0, 0], [0, value, 0, 0, 0], [0, 0, value, 0, 0], [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]]


def m_inverse() -> list[list[float]]:
    return [[-1, 0, 0, 0, 0], [0, -1, 0, 0, 0], [0, 0, -1, 0, 0], [0, 0, 0, 1, 0], [1, 1, 1, 0, 1]]


def m_black_white() -> list[list[float]]:
    return [[1.5, 1.5, 1.5, 0, 0], [1.5, 1.5, 1.5, 0, 0], [1.5, 1.5, 1.5, 0, 0], [0, 0, 0, 1, 0], [-1, -1, -1, 0, 1]]


def m_polaroid() -> list[list[float]]:
    return [[1.438, -0.062, -0.062, 0, 0], [-0.122, 1.378, -0.122, 0, 0], [-0.016, -0.016, 1.483, 0, 0],
            [0, 0, 0, 1, 0], [-0.03, 0.05, -0.02, 0, 1]]


def m_grayscale(value: float = 1.0) -> list[list[float]]:
    r, g, b = RW * value, GW * value, BW * value
    return [[r, r, r, 0, 0], [g, g, g, 0, 0], [b, b, b, 0, 0], [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]]


def m_sepia(value: float = 1.0) -> list[list[float]]:
    v = value
    return [[0.393 * v, 0.349 * v, 0.272 * v, 0, 0], [0.769 * v, 0.686 * v, 0.534 * v, 0, 0],
            [0.189 * v, 0.168 * v, 0.131 * v, 0, 0], [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]]


def m_hue(angle: float) -> list[list[float]]:
    a = angle * math.pi / 180
    c, s = math.cos(a), math.sin(a)
    return [
        [(RW + c * (1 - RW)) + s * -RW, (RW + c * -RW) + s * 0.143, (RW + c * -RW) + s * -(1 - RW), 0, 0],
        [(GW + c * -GW) + s * -GW, (GW + c * (1 - GW)) + s * 0.14, (GW + c * -GW) + s * GW, 0, 0],
        [(BW + c * -BW) + s * (1 - BW), (BW + c * -BW) + s * -0.283, (BW + c * (1 - BW)) + s * BW, 0, 0],
        [0, 0, 0, 1, 0],
        [0, 0, 0, 0, 1],
    ]


def m_saturation(value: float) -> list[list[float]]:
    i = 1.0 - value
    return [[i * RW + value, i * RW, i * RW, 0, 0], [i * GW, i * GW + value, i * GW, 0, 0],
            [i * BW, i * BW, i * BW + value, 0, 0], [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]]


def m_colorize(color: str, value: float) -> list[list[float]]:
    cr, cg, cb, _ = parse_color(color)
    r, g, b = cr / 255, cg / 255, cb / 255
    inv = 1 - value
    return [[inv + value * r * RW, value * g * RW, value * b * RW, 0, 0],
            [value * r * GW, inv + value * g * GW, value * b * GW, 0, 0],
            [value * r * BW, value * g * BW, inv + value * b * BW, 0, 0],
            [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]]


@effect("Adjustments")
@dataclass
class Alpha(ImageEffect):
    value: float = param(1.0, label="Value", min=0, max=10, step=0.1,
                         help="Pixel alpha = Pixel alpha * Value. Example 0.5 will decrease alpha of pixel 50%")
    addition: float = param(0.0, label="Addition", min=-1, max=1, step=0.1,
                            help="Pixel alpha = Pixel alpha + Addition. Example 0.5 will increase alpha of pixel 127.5")

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_alpha(self.value, self.addition))

    def summary(self) -> str:
        return f"{_fmt(self.value)}, {_fmt(self.addition)}"


@effect("Adjustments", "Black & white")
@dataclass
class BlackWhite(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_black_white())


@effect("Adjustments")
@dataclass
class Brightness(ImageEffect):
    value: float = param(0.0, label="Value", min=-1, max=1, step=0.05,
                         help="Pixel color = Pixel color + Value. Example 0.5 will increase color of pixel 127.5")

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_brightness(self.value))

    def summary(self) -> str:
        return _fmt(self.value)


@effect("Adjustments")
@dataclass
class Colorize(ImageEffect):
    color: str = param("#FFFF0000", label="Color", kind="color")
    value: float = param(0.0, label="Value", min=0, max=1, step=0.05)

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_colorize(self.color, self.value))

    def summary(self) -> str:
        r, g, b, _ = parse_color(self.color)
        return f"{r}, {g}, {b}"


@effect("Adjustments")
@dataclass
class Contrast(ImageEffect):
    value: float = param(1.0, label="Value", min=0, max=5, step=0.05,
                         help="Pixel color = Pixel color * Value. Example 1.5 will increase color of pixel 50%")

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_contrast(self.value))

    def summary(self) -> str:
        return _fmt(self.value)


@effect("Adjustments")
@dataclass
class Gamma(ImageEffect):
    value: float = param(1.0, label="Value", min=0.1, max=5.0, step=0.1, help="Min 0.1, Max 5.0")

    def apply(self, img: Image.Image) -> Image.Image:
        # GDI+ ImageAttributes.SetGamma: each colour channel c (0..1) becomes c ^ gamma, alpha untouched
        # (as Wine's gdiplus implements it and tests against Windows: gamma 3 maps 0x80 to 0x20).
        gamma = min(max(self.value, 0.1), 5.0)
        lut = [round(((i / 255.0) ** gamma) * 255.0) for i in range(256)]
        r, g, b, a = img.split()
        return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

    def summary(self) -> str:
        return _fmt(self.value)


@effect("Adjustments")
@dataclass
class Grayscale(ImageEffect):
    value: float = param(1.0, label="Value", min=0, max=5, step=0.05)

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_grayscale(self.value))

    def summary(self) -> str:
        return _fmt(self.value)


@effect("Adjustments")
@dataclass
class Hue(ImageEffect):
    angle: float = param(0.0, label="Angle", min=0, max=360, step=1, help="From 0 to 360")

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_hue(self.angle))

    def summary(self) -> str:
        return f"{_fmt(self.angle)}°"


@effect("Adjustments")
@dataclass
class Inverse(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_inverse())


@effect("Adjustments", "Color matrix")
@dataclass
class MatrixColor(ImageEffect):
    """Red = (Red * Rr) + (Green * Rg) + (Blue * Rb) + (Alpha * Ra) + Ro, and so on per channel."""
    rr: float = param(1.0, label="Rr", step=0.1,
                      help="Red = (Red * Rr) + (Green * Rg) + (Blue * Rb) + (Alpha * Ra) + Ro")
    rg: float = param(0.0, label="Rg", step=0.1)
    rb: float = param(0.0, label="Rb", step=0.1)
    ra: float = param(0.0, label="Ra", step=0.1)
    ro: float = param(0.0, label="Ro", step=0.1)
    gr: float = param(0.0, label="Gr", step=0.1)
    gg: float = param(1.0, label="Gg", step=0.1)
    gb: float = param(0.0, label="Gb", step=0.1)
    ga: float = param(0.0, label="Ga", step=0.1)
    go: float = param(0.0, label="Go", step=0.1)
    br: float = param(0.0, label="Br", step=0.1)
    bg: float = param(0.0, label="Bg", step=0.1)
    bb: float = param(1.0, label="Bb", step=0.1)
    ba: float = param(0.0, label="Ba", step=0.1)
    bo: float = param(0.0, label="Bo", step=0.1)
    ar: float = param(0.0, label="Ar", step=0.1)
    ag: float = param(0.0, label="Ag", step=0.1)
    ab: float = param(0.0, label="Ab", step=0.1)
    aa: float = param(1.0, label="Aa", step=0.1)
    ao: float = param(0.0, label="Ao", step=0.1)

    def apply(self, img: Image.Image) -> Image.Image:
        m = [[self.rr, self.gr, self.br, self.ar, 0],
             [self.rg, self.gg, self.bg, self.ag, 0],
             [self.rb, self.gb, self.bb, self.ab, 0],
             [self.ra, self.ga, self.ba, self.aa, 0],
             [self.ro, self.go, self.bo, self.ao, 1]]
        return color_matrix(img, m)


@effect("Adjustments")
@dataclass
class Polaroid(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_polaroid())


@effect("Adjustments", "Replace color")
@dataclass
class ReplaceColor(ImageEffect):
    source_color: str = param("#FFFFFFFF", label="Source color", kind="color")
    auto_source_color: bool = param(False, label="Auto source color",
                                    help="Use the top-left pixel as the source colour")
    target_color: str = param("#00FFFFFF", label="Target color", kind="color")
    threshold: int = param(0, label="Threshold", min=0, max=765,
                           help="0 = exact ARGB match, else |dR| + |dG| + |dB| <= threshold")

    def apply(self, img: Image.Image) -> Image.Image:
        a = to_array(img)
        src = np.array(parse_color(self.source_color), np.int16)
        if self.auto_source_color:
            src = a[0, 0].astype(np.int16)
        tgt = np.array(parse_color(self.target_color), np.uint8)
        if self.threshold == 0:
            hit = np.all(a.astype(np.int16) == src, axis=2)
        else:
            diff = np.abs(a[..., :3].astype(np.int16) - src[:3]).sum(axis=2)
            hit = diff <= self.threshold
        a[hit] = tgt
        return from_array(a)


@effect("Adjustments")
@dataclass
class Saturation(ImageEffect):
    value: float = param(1.0, label="Value", min=-5, max=5, step=0.05)

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_saturation(self.value))

    def summary(self) -> str:
        return _fmt(self.value)


def perceived_brightness(r, g, b):
    """ShareX ``ColorHelpers.PerceivedBrightness`` (truncated)."""
    return np.sqrt(r * r * 0.299 + g * g * 0.587 + b * b * 0.114).astype(np.int32)


@effect("Adjustments", "Selective color")
@dataclass
class SelectiveColor(ImageEffect):
    light_color: str = param("#FFFFFFFF", label="Light color", kind="color")
    dark_color: str = param("#FF000000", label="Dark color", kind="color")
    palette_size: int = param(10, label="Palette size", min=2, max=100)

    def apply(self, img: Image.Image) -> Image.Image:
        size = min(max(self.palette_size, 2), 100)
        lr, lg, lb, _ = parse_color(self.light_color)
        dr, dg, db, _ = parse_color(self.dark_color)
        palette: dict[int, tuple[int, int, int]] = {}
        for i in range(size):
            t = i / (size - 1)
            c = (int(lr + (dr - lr) * t), int(lg + (dg - lg) * t), int(lb + (db - lb) * t))
            pb = int(perceived_brightness(np.float64(c[0]), np.float64(c[1]), np.float64(c[2])))
            palette.setdefault(pb, c)
        keys = np.array(list(palette.keys()), np.int32)
        cols = np.array(list(palette.values()), np.uint8)
        a = to_array(img)
        pbs = perceived_brightness(a[..., 0].astype(np.float64), a[..., 1].astype(np.float64),
                                   a[..., 2].astype(np.float64))
        dist = np.abs(pbs[..., None] - keys[None, None, :])
        # Aggregate keeps the current entry only when strictly closer, so ties go to the later one.
        rev = dist[..., ::-1]
        idx = len(keys) - 1 - np.argmin(rev, axis=2)
        a[..., :3] = cols[idx]
        return from_array(a)


@effect("Adjustments")
@dataclass
class Sepia(ImageEffect):
    value: float = param(1.0, label="Value", min=0, max=5, step=0.05)

    def apply(self, img: Image.Image) -> Image.Image:
        return color_matrix(img, m_sepia(self.value))

    def summary(self) -> str:
        return _fmt(self.value)
