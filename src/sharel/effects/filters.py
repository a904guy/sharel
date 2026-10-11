"""Filters (ShareX.ImageEffectsLib/Filters): convolutions, blurs, shadows and edge effects."""
# param() is a dataclass field specifier (it returns dataclasses.field), not a shared default:
# ruff: noqa: RUF009
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .base import ImageEffect, effect, param
from .common import (ints, ALL_SIDES, SIDES, GradientInfo, add_canvas, add_shadow, box_blur, closed_curve_path,
                     color_mask, convolve, default_two_color_gradient, draw_over, from_array, gaussian_blur,
                     gradient_array, parse_color, parse_flags, path_mask, polygon_path, rand_inclusive, to_array)
from .manipulations import add_skew


def _kernel_effect(img: Image.Image, kernel, offset: float = 0.0) -> Image.Image:
    return from_array(convolve(to_array(img), np.array(kernel, float), offset))


@effect("Filters")
@dataclass
class Blur(ImageEffect):
    radius: int = param(15, label="Radius", min=3, max=200, help="Box blur size (odd, at least 3)")

    @property
    def effective_radius(self) -> int:
        r = max(3, self.radius)
        return r + 1 if r % 2 == 0 else r

    def apply(self, img: Image.Image) -> Image.Image:
        return from_array(box_blur(to_array(img), self.effective_radius))

    def summary(self) -> str:
        return str(self.effective_radius)


@effect("Filters", "Color depth")
@dataclass
class ColorDepth(ImageEffect):
    bits_per_channel: int = param(4, label="Bits per channel", min=1, max=8)

    def apply(self, img: Image.Image) -> Image.Image:
        bits = min(max(self.bits_per_channel, 1), 8)
        interval = 255 / (2 ** bits - 1)
        a = to_array(img)
        rgb = a[..., :3].astype(np.float64)
        a[..., :3] = np.rint(np.rint(rgb / interval) * interval).astype(np.uint8)
        return from_array(a)

    def summary(self) -> str:
        bits = min(max(self.bits_per_channel, 1), 8)
        return f"{bits} bit" + ("s" if bits > 1 else "")


@effect("Filters", "Edge detect")
@dataclass
class EdgeDetect(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return _kernel_effect(img, [[-1, -1, -1], [0, 0, 0], [1, 1, 1]], 127)


@effect("Filters")
@dataclass
class Emboss(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return _kernel_effect(img, [[-1, 0, -1], [0, 4, 0], [-1, 0, -1]], 127)


@effect("Filters", "Gaussian blur")
@dataclass
class GaussianBlur(ImageEffect):
    radius: int = param(15, label="Radius", min=1, max=200)

    def apply(self, img: Image.Image) -> Image.Image:
        return from_array(gaussian_blur(to_array(img), max(1, self.radius)))

    def summary(self) -> str:
        return str(max(1, self.radius))


def gradient_mask(img: Image.Image, gradient: GradientInfo, opacity: float) -> Image.Image:
    """ShareX ``CreateGradientMask``: the gradient's colours with alpha = source alpha * gradient alpha * opacity."""
    a = to_array(img)
    grad = gradient_array(img.width, img.height, gradient)
    out = np.empty_like(a)
    out[..., :3] = np.clip(np.rint(grad[..., :3]), 0, 255).astype(np.uint8)
    alpha = a[..., 3].astype(np.float32) * (np.rint(grad[..., 3]) / 255.0) * opacity
    out[..., 3] = np.minimum(255, alpha).astype(np.uint8)
    if opacity <= 0:
        out[:] = 0
    return from_array(out)


@effect("Filters")
@dataclass
class Glow(ImageEffect):
    size: int = param(20, label="Size", min=0, max=500)
    strength: float = param(1.0, label="Strength", min=0.1, max=100, step=0.1)
    color: str = param("#FFFFFFFF", label="Color", kind="color")
    use_gradient: bool = param(False, label="Use gradient")
    gradient: GradientInfo = param(label="Gradient", kind="gradient",
                                   default_factory=lambda: default_two_color_gradient("ForwardDiagonal"))
    offset: list[int] = param(label="Offset", kind="point", default_factory=lambda: [0, 0])

    def apply(self, img: Image.Image) -> Image.Image:
        size = max(0, self.size)
        strength = max(0.1, self.strength)
        ox, oy = ints(self.offset, 2)
        if size > 0:
            blur = add_canvas(img, [size] * 4)
            blur = from_array(box_blur(to_array(blur), size))
        else:
            blur = img
        if self.use_gradient and self.gradient is not None and self.gradient.is_valid:
            mask = gradient_mask(blur, self.gradient, strength)
        else:
            mask = color_mask(blur, strength, self.color)
        out = Image.new("RGBA", (mask.width + abs(ox), mask.height + abs(oy)), (0, 0, 0, 0))
        draw_over(out, mask, max(0, ox), max(0, oy))
        draw_over(out, img, max(size, -ox + size), max(size, -oy + size))
        return out

    def summary(self) -> str:
        return str(self.size)


@effect("Filters", "Convolution matrix")
@dataclass
class MatrixConvolution(ImageEffect):
    x0y0: int = param(0, label="X0Y0", sharex="X0Y0")
    x1y0: int = param(0, label="X1Y0", sharex="X1Y0")
    x2y0: int = param(0, label="X2Y0", sharex="X2Y0")
    x0y1: int = param(0, label="X0Y1", sharex="X0Y1")
    x1y1: int = param(1, label="X1Y1", sharex="X1Y1")
    x2y1: int = param(0, label="X2Y1", sharex="X2Y1")
    x0y2: int = param(0, label="X0Y2", sharex="X0Y2")
    x1y2: int = param(0, label="X1Y2", sharex="X1Y2")
    x2y2: int = param(0, label="X2Y2", sharex="X2Y2")
    factor: float = param(1.0, label="Factor", step=0.1)
    offset: int = param(0, label="Offset", min=0, max=255)

    def apply(self, img: Image.Image) -> Image.Image:
        f = self.factor if self.factor != 0 else 1.0
        k = np.array([[self.x0y0, self.x1y0, self.x2y0],
                      [self.x0y1, self.x1y1, self.x2y1],
                      [self.x0y2, self.x1y2, self.x2y2]], float) / f
        return _kernel_effect(img, k, min(max(int(self.offset), 0), 255))


@effect("Filters", "Mean removal")
@dataclass
class MeanRemoval(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return _kernel_effect(img, [[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])


def make_outline(img: Image.Image, min_radius: int, max_radius: int, color: str) -> Image.Image:
    """ShareX ``MakeOutline``: pixels whose distance to the nearest fully opaque pixel lies in
    (min_radius, max_radius), anti-aliased over the last pixel at each end."""
    a = to_array(img)
    h, w = a.shape[:2]
    opaque = a[..., 3] >= 255
    big = max_radius * max_radius + 1
    dist2 = np.full((h, w), big, np.int64)
    r = max_radius
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d2 = dx * dx + dy * dy
            if d2 >= big:
                continue
            shifted = np.zeros((h, w), bool)
            ys, ye = max(0, -dy), min(h, h - dy)
            xs, xe = max(0, -dx), min(w, w - dx)
            if ys >= ye or xs >= xe:
                continue
            shifted[ys:ye, xs:xe] = opaque[ys + dy:ye + dy, xs + dx:xe + dx]
            np.minimum(dist2, np.where(shifted, d2, big), out=dist2)
    dist = np.sqrt(dist2.astype(np.float64))
    inside = (dist > min_radius) & (dist < max_radius)
    alpha = np.where(inside, 255.0, 0.0)
    near_min = inside & (dist - min_radius < 1)
    near_max = inside & ~near_min & (max_radius - dist < 1)
    alpha = np.where(near_min, 255 * (dist - min_radius), alpha)
    alpha = np.where(near_max, 255 * (max_radius - dist), alpha)
    r_, g_, b_, _ = parse_color(color)
    out = np.zeros_like(a)
    out[inside, 0], out[inside, 1], out[inside, 2] = r_, g_, b_
    out[..., 3] = alpha.astype(np.uint8)
    return from_array(out)


@effect("Filters")
@dataclass
class Outline(ImageEffect):
    size: int = param(1, label="Size", min=1, max=100)
    padding: int = param(0, label="Padding", min=0, max=100)
    color: str = param("#FF000000", label="Color", kind="color")
    outline_only: bool = param(False, label="Outline only")

    def apply(self, img: Image.Image) -> Image.Image:
        size, padding = max(1, self.size), max(0, self.padding)
        outline = make_outline(img, padding, padding + size + 1, self.color)
        if self.outline_only:
            return outline
        return draw_over(img.copy(), outline, 0, 0)

    def summary(self) -> str:
        return str(self.size)


def pixelate(img: Image.Image, size: int) -> np.ndarray:
    """ShareX ``ImageHelpers.Pixelate``: alpha-weighted block average (alpha itself = sum(a*a/255)/n)."""
    a = to_array(img).astype(np.float64)
    h, w = a.shape[:2]
    if size <= 1:
        return a.astype(np.uint8)
    ph, pw = -(-h // size) * size, -(-w // size) * size
    pad = np.zeros((ph, pw, 4))
    pad[:h, :w] = a
    valid = np.zeros((ph, pw))
    valid[:h, :w] = 1
    bh, bw = ph // size, pw // size
    blocks = pad.reshape(bh, size, bw, size, 4)
    weight = blocks[..., 3] / 255.0
    count = valid.reshape(bh, size, bw, size).sum(axis=(1, 3))
    wsum = weight.sum(axis=(1, 3))
    col = (blocks[..., :3] * weight[..., None]).sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        rgb = np.where(wsum[..., None] > 0, col / np.where(wsum > 0, wsum, 1)[..., None], 0)
    alpha = (blocks[..., 3] * weight).sum(axis=(1, 3)) / count
    avg = np.concatenate([rgb, alpha[..., None]], axis=2).astype(np.uint8)  # (byte) truncation
    full = np.repeat(np.repeat(avg, size, axis=0), size, axis=1)
    return full[:h, :w]


@effect("Filters")
@dataclass
class Pixelate(ImageEffect):
    size: int = param(10, label="Size", min=2, max=1000)
    border_size: int = param(0, label="Border size", min=0, max=100)
    border_color: str = param("#FF000000", label="Border color", kind="color")

    def apply(self, img: Image.Image) -> Image.Image:
        size = max(2, self.size)
        out = from_array(pixelate(img, size))
        bsize = max(0, self.border_size)
        if bsize > 0 and parse_color(self.border_color)[3] > 0:
            ys = np.arange(img.height) % size
            xs = np.arange(img.width) % size
            band_y = (ys < bsize) | (ys >= size - bsize)
            band_x = (xs < bsize) | (xs >= size - bsize)
            grid = (band_y[:, None] | band_x[None, :]).astype(np.float32)
            r, g, b, ca = parse_color(self.border_color)
            layer = np.zeros((img.height, img.width, 4), np.float32)
            layer[..., 0], layer[..., 1], layer[..., 2] = r, g, b
            layer[..., 3] = grid * ca
            out = Image.alpha_composite(out, from_array(layer))
        return out

    def summary(self) -> str:
        return str(max(2, self.size))


def add_reflection(img: Image.Image, percentage: int, max_alpha: int, min_alpha: int) -> Image.Image | None:
    percentage = min(max(percentage, 1), 100)
    max_alpha = min(max(max_alpha, 0), 255)
    min_alpha = min(max(min_alpha, 0), 255)
    rh = int(img.height * (percentage / 100))
    if rh < 1:
        return None
    flipped = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    a = to_array(flipped.crop((0, 0, img.width, rh)))
    span = rh - 1
    ys = np.arange(rh, dtype=np.float64)
    ramp = (max_alpha - (max_alpha - min_alpha) * (ys / span if span > 0 else 0 * ys)).astype(np.uint8)
    a[..., 3] = np.minimum(a[..., 3], ramp[:, None])
    return from_array(a)


@effect("Filters")
@dataclass
class Reflection(ImageEffect):
    percentage: int = param(20, label="Percentage", min=1, max=100,
                            help="Reflection height size relative to screenshot height.")
    max_alpha: int = param(255, label="Max alpha", min=0, max=255,
                           help="Reflection transparency start from this value to MinAlpha.")
    min_alpha: int = param(0, label="Min alpha", min=0, max=255,
                           help="Reflection transparency start from MaxAlpha to this value.")
    offset: int = param(0, label="Offset", min=-10000, max=10000,
                        help="Reflection start position will be: Screenshot height + Offset")
    skew: bool = param(False, label="Skew", help="Adding skew to reflection from bottom left to bottom right.")
    skew_size: int = param(25, label="Skew size", min=-10000, max=10000, help="How much pixel skew left to right.")

    def apply(self, img: Image.Image) -> Image.Image:
        refl = add_reflection(img, self.percentage, self.max_alpha, self.min_alpha)
        if refl is None:
            return img
        if self.skew and self.skew_size != 0:
            refl = add_skew(refl, self.skew_size, 0)
        out = Image.new("RGBA", (refl.width, max(1, img.height + refl.height + self.offset)), (0, 0, 0, 0))
        draw_over(out, img, 0, 0)
        draw_over(out, refl, 0, img.height + self.offset)
        return out

    def summary(self) -> str:
        return str(self.percentage)


@effect("Filters", "RGB split")
@dataclass
class RGBSplit(ImageEffect):
    offset_red: list[int] = param(label="Offset red", kind="point", default_factory=lambda: [-5, 0])
    offset_green: list[int] = param(label="Offset green", kind="point", default_factory=lambda: [0, 0])
    offset_blue: list[int] = param(label="Offset blue", kind="point", default_factory=lambda: [5, 0])

    def apply(self, img: Image.Image) -> Image.Image:
        a = to_array(img).astype(np.int32)
        h, w = a.shape[:2]
        ys = np.arange(h)[:, None]
        xs = np.arange(w)[None, :]

        def sample(off):
            ox, oy = ints(off, 2)
            return a[np.clip(ys - oy, 0, h - 1), np.clip(xs - ox, 0, w - 1)]

        cr, cg, cb = sample(self.offset_red), sample(self.offset_green), sample(self.offset_blue)
        out = np.empty((h, w, 4), np.uint8)
        out[..., 0] = cr[..., 0] * cr[..., 3] // 255
        out[..., 1] = cg[..., 1] * cg[..., 3] // 255
        out[..., 2] = cb[..., 2] * cb[..., 3] // 255
        out[..., 3] = (cr[..., 3] + cg[..., 3] + cb[..., 3]) // 3
        return from_array(out)


@effect("Filters")
@dataclass
class Shadow(ImageEffect):
    opacity: float = param(0.6, label="Opacity", min=0.1, max=1.0, step=0.05, help="Choose a value between 0.1 and 1.0")
    size: int = param(10, label="Size", min=0, max=500)
    darkness: float = param(0.0, label="Darkness", min=0, max=100, step=0.1)
    color: str = param("#FF000000", label="Color", kind="color")
    offset: list[int] = param(label="Offset", kind="point", default_factory=lambda: [0, 0])
    auto_resize: bool = param(True, label="Auto resize")

    def apply(self, img: Image.Image) -> Image.Image:
        opacity = min(max(self.opacity, 0.1), 1.0)
        return add_shadow(img, opacity, max(0, self.size), self.darkness + 1, self.color,
                          ints(self.offset, 2), self.auto_resize)

    def summary(self) -> str:
        return str(self.size)


@effect("Filters")
@dataclass
class Sharpen(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        f = 11 - 8
        return _kernel_effect(img, [[0, -2 / f, 0], [-2 / f, 11 / f, -2 / f], [0, -2 / f, 0]])


@effect("Filters", "Slice")
@dataclass
class Slice(ImageEffect):
    min_slice_height: int = param(10, label="Min slice height", min=1, max=10000)
    max_slice_height: int = param(100, label="Max slice height", min=1, max=10000)
    min_slice_shift: int = param(0, label="Min slice shift", min=-10000, max=10000)
    max_slice_shift: int = param(10, label="Max slice shift", min=-10000, max=10000)

    def apply(self, img: Image.Image) -> Image.Image:
        lo_h = max(1, min(self.min_slice_height, self.max_slice_height))
        hi_h = max(1, max(self.min_slice_height, self.max_slice_height))
        lo_s = min(self.min_slice_shift, self.max_slice_shift)
        hi_s = max(self.min_slice_shift, self.max_slice_shift)
        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        y = 0
        while y < img.height:
            sh = rand_inclusive(lo_h, hi_h)
            shift_left = rand_inclusive(0, 1) == 0
            x = rand_inclusive(-hi_s, -lo_s) if shift_left else rand_inclusive(lo_s, hi_s)
            strip = img.crop((0, y, img.width, min(img.height, y + sh)))
            draw_over(out, strip, x, y)
            y += sh
        return out

    def summary(self) -> str:
        return f"{self.min_slice_height}, {self.max_slice_height}"


@effect("Filters")
@dataclass
class Smooth(ImageEffect):
    def apply(self, img: Image.Image) -> Image.Image:
        return _kernel_effect(img, np.full((3, 3), 1 / 9))


def torn_edge_points(w: int, h: int, depth: int, rng_range: int, sides: set[str]) -> list[tuple[int, int]] | None:
    """The polygon ShareX ``ImageHelpers.TornEdges`` fills (random depths, inclusive)."""
    hcount = w // rng_range
    vcount = h // rng_range
    if hcount < 2 and vcount < 2:
        return None
    pts: list[tuple[int, int]] = []

    def rnd() -> int:
        return rand_inclusive(0, depth)

    if "Top" in sides and hcount > 1:
        start = depth if ("Left" in sides and vcount > 1) else 0
        end = w - depth if ("Right" in sides and vcount > 1) else w
        for x in range(start, end, rng_range):
            pts.append((x, rnd()))
    else:
        pts += [(0, 0), (w, 0)]
    if "Right" in sides and vcount > 1:
        start = depth if ("Top" in sides and hcount > 1) else 0
        end = h - depth if ("Bottom" in sides and hcount > 1) else h
        for y in range(start, end, rng_range):
            pts.append((w - depth + rnd(), y))
    else:
        pts += [(w, 0), (w, h)]
    if "Bottom" in sides and hcount > 1:
        x = w - depth if ("Right" in sides and vcount > 1) else w
        end = depth if ("Left" in sides and vcount > 1) else 0
        while x >= end:
            pts.append((x, h - depth + rnd()))
            x = (x // rng_range - 1) * rng_range
    else:
        pts += [(w, h), (0, h)]
    if "Left" in sides and vcount > 1:
        y = h - depth if ("Bottom" in sides and hcount > 1) else h
        end = depth if ("Top" in sides and hcount > 1) else 0
        while y >= end:
            pts.append((rnd(), y))
            y = (y // rng_range - 1) * rng_range
    else:
        pts += [(0, h), (0, 0)]
    unique: list[tuple[int, int]] = []
    seen = set()
    for p in pts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


@effect("Filters", "Torn edge")
@dataclass
class TornEdge(ImageEffect):
    depth: int = param(15, label="Depth", min=0, max=1000)
    range: int = param(20, label="Range", min=1, max=1000)  # ShareX property name
    sides: str = param(ALL_SIDES, label="Sides", kind="flags", choices=SIDES)
    curved_edges: bool = param(True, label="Curved edges")

    def apply(self, img: Image.Image) -> Image.Image:
        sides = parse_flags(self.sides)
        if self.depth < 1 or self.range < 1 or not sides:
            return img
        pts = torn_edge_points(img.width, img.height, self.depth, self.range, sides)
        if pts is None:
            return img
        fpts = [(float(x), float(y)) for x, y in pts]
        builder = (lambda cr: closed_curve_path(cr, fpts)) if self.curved_edges else (lambda cr: polygon_path(cr, fpts))
        mask = path_mask(img.width, img.height, builder)
        a = to_array(img)
        a[..., 3] = np.rint(a[..., 3] * mask).astype(np.uint8)
        return from_array(a)

    def summary(self) -> str:
        return f"{self.depth}, {self.range}"


def wave_edge_points(w: int, h: int, depth: int, wave_range: int, sides: set[str]) -> list[tuple[int, int]]:
    """The polygon ShareX ``ImageHelpers.WavyEdges`` fills."""
    hcount = max(2, (w // wave_range + 1) // 2 * 2) - 1
    vcount = max(2, (h // wave_range + 1) // 2 * 2) - 1
    hrange = w // hcount
    vrange = h // vcount
    step = min(max(1, wave_range // depth), 10)

    def wave(t: int, mx: int) -> int:
        return int((1 - math.cos(t * math.pi / mx)) * depth / 2)

    pts: list[tuple[int, int]] = []
    if "Top" in sides:
        sx = depth if "Left" in sides else 0
        ex = w - depth if "Right" in sides else w
        for x in range(sx, ex, step):
            pts.append((x, wave(x, hrange)))
        pts.append((ex, wave(ex, hrange)))
    else:
        pts.append((0, 0))
    if "Right" in sides:
        sy = depth if "Top" in sides else 0
        ey = h - depth if "Bottom" in sides else h
        for y in range(sy, ey, step):
            pts.append((w - depth + wave(y, vrange), y))
        pts.append((w - depth + wave(ey, vrange), ey))
    else:
        pts.append((w, pts[-1][1]))
    if "Bottom" in sides:
        sx = w - depth if "Right" in sides else w
        ex = depth if "Left" in sides else 0
        for x in range(sx, ex - 1, -step):
            pts.append((x, h - depth + wave(x, hrange)))
        pts.append((ex, h - depth + wave(ex, hrange)))
    else:
        pts.append((pts[-1][0], h))
    if "Left" in sides:
        sy = h - depth if "Bottom" in sides else h
        ey = depth if "Top" in sides else 0
        for y in range(sy, ey - 1, -step):
            pts.append((wave(y, vrange), y))
        pts.append((wave(ey, vrange), ey))
    else:
        pts.append((0, pts[-1][1]))
    if "Top" not in sides:
        pts[0] = (pts[-1][0], 0)
    return pts


@effect("Filters", "Wave edge")
@dataclass
class WaveEdge(ImageEffect):
    depth: int = param(15, label="Depth", min=0, max=1000)
    range: int = param(20, label="Range", min=1, max=1000)  # ShareX property name
    sides: str = param(ALL_SIDES, label="Sides", kind="flags", choices=SIDES)

    def apply(self, img: Image.Image) -> Image.Image:
        sides = parse_flags(self.sides)
        if self.depth < 1 or self.range < 1 or not sides:
            return img
        pts = [(float(x), float(y)) for x, y in wave_edge_points(img.width, img.height, self.depth, self.range, sides)]
        mask = path_mask(img.width, img.height, lambda cr: polygon_path(cr, pts))
        a = to_array(img)
        a[..., 3] = np.rint(a[..., 3] * mask).astype(np.uint8)
        return from_array(a)

    def summary(self) -> str:
        return f"{self.depth}, {self.range}"
