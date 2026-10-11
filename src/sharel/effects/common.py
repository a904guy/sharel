"""Shared helpers for the image effects: colours, gradients, fonts, paths and pixel primitives.

The effects reproduce ShareX's GDI+ code paths, so the primitives here mirror ShareX helpers:

* colours are ``"#AARRGGBB"`` strings (like the rest of the config); :func:`parse_color` also accepts
  ``#RRGGBB`` and the .NET colour forms ShareX serializes (``"Black"``, ``"R, G, B"``, ``"A, R, G, B"``).
* pixels are processed as straight (non-premultiplied) RGBA ``numpy`` arrays, like GDI+'s 32bppArgb
  bitmaps that ShareX's ``UnsafeBitmap`` loops work on.
* vector work (rounded rectangles, polygons, curves, dashed pens, text) goes through pycairo, whose
  ARGB32 surfaces are premultiplied BGRA; :func:`to_surface` / :func:`from_surface` convert.
"""
from __future__ import annotations

import math
import os
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cairo
import numpy as np
from PIL import Image, ImageColor

# One RNG for the random effects (Slice, TornEdge, DrawParticles); tests seed it.
rng = random.Random()


def rand_inclusive(lo: int, hi: int) -> int:
    """ShareX ``RandomFast.Next(min, max)``: both bounds inclusive, order-tolerant."""
    lo, hi = int(lo), int(hi)
    if lo > hi:
        lo, hi = hi, lo
    return rng.randint(lo, hi)


# --------------------------------------------------------------------------- colours

RGBA = tuple[int, int, int, int]

# .NET named colours are the CSS/X11 set plus Transparent (= ARGB 0, 255, 255, 255).
_NET_NAMES: dict[str, RGBA] = {"transparent": (255, 255, 255, 0)}


def _net_color(name: str) -> RGBA | None:
    key = name.strip().lower().replace(" ", "")
    if key in _NET_NAMES:
        return _NET_NAMES[key]
    # Pillow caches converted names back into ImageColor.colormap as tuples, so ask getrgb.
    if key not in ImageColor.colormap:
        return None
    try:
        r, g, b = ImageColor.getrgb(key)[:3]
    except ValueError:
        return None
    return (r, g, b, 255)


def parse_color(value: str | tuple | list | None, default: RGBA = (0, 0, 0, 255)) -> RGBA:
    """``"#AARRGGBB"`` (or ``#RRGGBB``, ``#ARGB``-less names, ``"R, G, B"``, ``"A, R, G, B"``,
    or an RGB(A) sequence) -> ``(r, g, b, a)``. Unparseable input returns ``default``."""
    if value is None:
        return default
    if isinstance(value, (tuple, list)):
        vals = [max(0, min(255, int(v))) for v in value]
        if len(vals) == 3:
            return (vals[0], vals[1], vals[2], 255)
        if len(vals) == 4:
            return (vals[0], vals[1], vals[2], vals[3])
        return default
    s = str(value).strip()
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) == 8:
                return (int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16), int(h[0:2], 16))
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
            if len(h) == 3:
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 255)
        except ValueError:
            return default
        return default
    if "," in s:
        try:
            parts = [int(float(p)) for p in s.split(",")]
        except ValueError:
            return default
        parts = [max(0, min(255, p)) for p in parts]
        if len(parts) == 3:
            return (parts[0], parts[1], parts[2], 255)
        if len(parts) == 4:  # .NET order: A, R, G, B
            return (parts[1], parts[2], parts[3], parts[0])
        return default
    named = _net_color(s)
    return named if named is not None else default


def format_color(rgba: RGBA | tuple | list) -> str:
    """``(r, g, b[, a])`` -> ``"#AARRGGBB"``."""
    r, g, b, a = parse_color(tuple(rgba))
    return f"#{a:02X}{r:02X}{g:02X}{b:02X}"


def color_to_dotnet(value: str) -> str:
    """``"#AARRGGBB"`` -> the string .NET's ColorConverter writes for an ARGB colour."""
    r, g, b, a = parse_color(value)
    return f"{r}, {g}, {b}" if a == 255 else f"{a}, {r}, {g}, {b}"


def color_from_dotnet(value: str) -> str:
    return format_color(parse_color(value))


# --------------------------------------------------------------------------- gradients

GRADIENT_TYPES = ["Horizontal", "Vertical", "ForwardDiagonal", "BackwardDiagonal"]


@dataclass
class GradientStop:
    color: str = "#FF000000"
    location: float = 0.0  # 0..100


@dataclass
class GradientInfo:
    """ShareX ``GradientInfo``: a ``LinearGradientMode`` and colour stops at 0..100 %."""
    type: str = "Vertical"  # ShareX property name (LinearGradientMode)
    colors: list[GradientStop] = field(default_factory=list)

    @staticmethod
    def of(type_: str, *stops: tuple[str, float]) -> GradientInfo:
        return GradientInfo(type_, [GradientStop(c, float(loc)) for c, loc in stops])

    @property
    def is_valid(self) -> bool:
        return bool(self.colors)

    @property
    def is_visible(self) -> bool:
        return self.is_valid and any(parse_color(s.color)[3] > 0 for s in self.colors)

    def color_blend(self) -> tuple[np.ndarray, np.ndarray]:
        """``GetColorBlend``: stops sorted by location, padded at 0 and 100 with the end colours.
        Returns (positions 0..1, colours Nx4 float straight RGBA)."""
        stops = sorted(self.colors, key=lambda s: max(0.0, min(100.0, s.location)))
        locs = [max(0.0, min(100.0, s.location)) for s in stops]
        cols = [parse_color(s.color) for s in stops]
        if 0.0 not in locs:
            locs.insert(0, 0.0)
            cols.insert(0, cols[0])
        if 100.0 not in locs:
            locs.append(100.0)
            cols.append(cols[-1])
        return np.array(locs, dtype=np.float64) / 100.0, np.array(cols, dtype=np.float64)


def default_blue_gradient() -> GradientInfo:
    """The 4-stop gradient ShareX's DrawBackground/DrawBorder/DrawText constructors add."""
    return GradientInfo.of("Vertical", ("#FF4478C2", 0), ("#FF0D3A7A", 50), ("#FF06244E", 50), ("#FF1759AE", 100))


def default_two_color_gradient(type_: str = "Horizontal") -> GradientInfo:
    """DrawTextEx/Glow pick one of three random 2-stop gradients; the first one, deterministically."""
    return GradientInfo.of(type_, ("#FF00BB8A", 0), ("#FF0069A3", 100))


def gradient_array(width: int, height: int, gradient: GradientInfo,
                   rect: tuple[float, float, float, float] | None = None) -> np.ndarray:
    """Render a GDI+ ``LinearGradientBrush(rect, mode)`` with ``InterpolationColors`` to a
    ``(height, width, 4)`` float array (straight RGBA 0..255). Diagonal modes interpolate along
    ``(x/w + y/h) / 2`` so the off-diagonal corners are the 50 % colour, as GDI+ does."""
    rx, ry, rw, rh = rect if rect is not None else (0, 0, width, height)
    rw = max(rw, 1e-6)
    rh = max(rh, 1e-6)
    u = ((np.arange(width, dtype=np.float64) + 0.5) - rx) / rw
    v = ((np.arange(height, dtype=np.float64) + 0.5) - ry) / rh
    uu, vv = np.meshgrid(u, v)
    mode = gradient.type
    if mode == "Horizontal":
        t = uu
    elif mode == "ForwardDiagonal":
        t = (uu + vv) / 2
    elif mode == "BackwardDiagonal":
        t = ((1 - uu) + vv) / 2
    else:
        t = vv
    t = np.clip(t, 0.0, 1.0)
    pos, cols = gradient.color_blend()
    n = len(pos)
    if n == 1:
        return np.broadcast_to(cols[0], (height, width, 4)).astype(np.float32)
    idx = np.clip(np.searchsorted(pos, t, side="right") - 1, 0, n - 2)
    p0 = pos[idx]
    p1 = pos[idx + 1]
    span = p1 - p0
    frac = np.where(span > 0, (t - p0) / np.where(span > 0, span, 1), 0.0)
    out = cols[idx] + (cols[idx + 1] - cols[idx]) * frac[..., None]
    return out.astype(np.float32)


def gradient_image(width: int, height: int, gradient: GradientInfo,
                   rect: tuple[float, float, float, float] | None = None) -> Image.Image:
    return from_array(gradient_array(width, height, gradient, rect))


# --------------------------------------------------------------------------- numpy <-> PIL

def to_array(img: Image.Image) -> np.ndarray:
    """RGBA image -> writable ``(h, w, 4)`` uint8 array."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return np.array(img, dtype=np.uint8)


def from_array(arr: np.ndarray, round_values: bool = True) -> Image.Image:
    a = np.asarray(arr)
    if a.dtype != np.uint8:
        a = np.clip(np.rint(a) if round_values else a, 0, 255).astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(a), "RGBA")


def new_image(width: int, height: int, color: str | RGBA = (0, 0, 0, 0)) -> Image.Image:
    rgba = parse_color(color) if isinstance(color, str) else tuple(color)
    return Image.new("RGBA", (max(1, int(width)), max(1, int(height))), rgba)


def draw_over(dst: Image.Image, src: Image.Image, x: int, y: int, opacity: float = 1.0) -> Image.Image:
    """``Graphics.DrawImage(src, x, y)`` with SourceOver, clipped to ``dst``; modifies and returns ``dst``."""
    x, y = int(x), int(y)
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    if opacity < 1.0:
        a = to_array(src)
        a[..., 3] = np.rint(a[..., 3] * max(0.0, opacity)).astype(np.uint8)
        src = from_array(a)
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(src.width - sx0, dst.width - dx0)
    h = min(src.height - sy0, dst.height - dy0)
    if w <= 0 or h <= 0:
        return dst
    part = src.crop((sx0, sy0, sx0 + w, sy0 + h)) if (sx0, sy0, w, h) != (0, 0, src.width, src.height) else src
    dst.alpha_composite(part, (dx0, dy0))
    return dst


def draw_copy(dst: Image.Image, src: Image.Image, x: int, y: int) -> Image.Image:
    """``CompositingMode.SourceCopy``: replace the destination pixels, clipped."""
    x, y = int(x), int(y)
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(src.width - sx0, dst.width - dx0)
    h = min(src.height - sy0, dst.height - dy0)
    if w > 0 and h > 0:
        dst.paste(src.crop((sx0, sy0, sx0 + w, sy0 + h)), (dx0, dy0))
    return dst


def fill_with(img: Image.Image, fill: np.ndarray) -> Image.Image:
    """``FillBackground``: paint ``fill`` (h, w, 4 array) then draw ``img`` over it."""
    bg = from_array(fill)
    return Image.alpha_composite(bg, img)


# --------------------------------------------------------------------------- colour matrix

def color_matrix(img: Image.Image, m: list[list[float]]) -> Image.Image:
    """GDI+ ``ColorMatrix`` (row-vector convention: ``[r g b a 1] @ M`` in 0..1, straight alpha)."""
    a = to_array(img).astype(np.float32) / 255.0
    h, w = a.shape[:2]
    vec = np.concatenate([a, np.ones((h, w, 1), np.float32)], axis=2)
    mat = np.array(m, dtype=np.float32)
    out = vec @ mat
    return from_array(np.clip(out[..., :4], 0, 1) * 255.0)


# --------------------------------------------------------------------------- convolution / blur

def convolve(arr: np.ndarray, kernel: np.ndarray, offset: float = 0.0, consider_alpha: bool = False) -> np.ndarray:
    """ShareX ``ConvolutionMatrix.Apply``: edge-clamped sampling, ``+Offset``, clamp, truncate to byte.
    Alpha is kept from the source unless ``consider_alpha``."""
    kernel = np.asarray(kernel, dtype=np.float64)
    kh, kw = kernel.shape
    oy, ox = (kh - 1) // 2, (kw - 1) // 2
    src = arr.astype(np.float64)
    padded = np.pad(src, ((oy, kh - 1 - oy), (ox, kw - 1 - ox), (0, 0)), mode="edge")
    h, w = arr.shape[:2]
    acc = np.zeros_like(src)
    for fy in range(kh):
        for fx in range(kw):
            k = kernel[fy, fx]
            if k:
                acc += k * padded[fy:fy + h, fx:fx + w]
    acc += offset
    out = np.clip(acc, 0, 255).astype(np.uint8)  # (byte) cast truncates
    if not consider_alpha:
        out[..., 3] = arr[..., 3]
    return out


def _box_pass(a: np.ndarray, half: int, axis: int) -> np.ndarray:
    n = a.shape[axis]
    c = np.cumsum(a.astype(np.int64), axis=axis)
    zero_shape = list(a.shape)
    zero_shape[axis] = 1
    c = np.concatenate([np.zeros(zero_shape, np.int64), c], axis=axis)
    idx = np.arange(n)
    lo = np.clip(idx - half, 0, n)
    hi = np.clip(idx + half + 1, 0, n)
    sums = np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)
    shape = [1] * a.ndim
    shape[axis] = n
    counts = (hi - lo).reshape(shape)
    return (sums // counts).astype(np.uint8)


def box_blur(arr: np.ndarray, size: int) -> np.ndarray:
    """ShareX ``ImageHelpers.BoxBlur``: odd window, H, V, H, V passes over straight RGBA,
    each window averaged over its in-bounds pixels only, truncated per pass. No-op for size <= 1."""
    if size <= 1:
        return arr
    if size % 2 == 0:
        size += 1
    half = size // 2
    out = arr
    for _ in range(2):
        out = _box_pass(out, half, 1)
        out = _box_pass(out, half, 0)
    return out


def gaussian_kernel(radius: int) -> np.ndarray:
    size = radius * 2 + 1
    sigma = radius / 3.0
    xs = np.arange(size) - (size - 1) / 2.0
    k = np.exp(-(xs * xs) / (2 * sigma * sigma)) / (math.sqrt(2 * math.pi) * sigma)
    return k / k.sum()


def gaussian_blur(arr: np.ndarray, radius: int) -> np.ndarray:
    """ShareX ``ImageHelpers.GaussianBlur``: separable kernel of 2r+1 taps, sigma r/3, alpha included."""
    k = gaussian_kernel(max(1, radius))
    horiz = convolve(arr, k[None, :], consider_alpha=True)
    return convolve(horiz, k[:, None], consider_alpha=True)


# --------------------------------------------------------------------------- cairo

def to_surface(img: Image.Image) -> cairo.ImageSurface:
    """RGBA image -> premultiplied ARGB32 cairo surface."""
    a = to_array(img)
    h, w = a.shape[:2]
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = surface.get_stride()
    buf = np.ndarray((h, stride // 4, 4), np.uint8, buffer=surface.get_data())
    alpha = a[..., 3:4].astype(np.uint16)
    prem = ((a[..., :3].astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
    buf[:, :w, 0] = prem[..., 2]
    buf[:, :w, 1] = prem[..., 1]
    buf[:, :w, 2] = prem[..., 0]
    buf[:, :w, 3] = a[..., 3]
    surface.mark_dirty()
    return surface


def surface_array(surface: cairo.ImageSurface) -> np.ndarray:
    """Premultiplied ARGB32 surface -> straight RGBA uint8 array."""
    surface.flush()
    w, h = surface.get_width(), surface.get_height()
    buf = np.ndarray((h, surface.get_stride() // 4, 4), np.uint8, buffer=surface.get_data())[:, :w]
    alpha = buf[..., 3].astype(np.float32)
    out = np.empty((h, w, 4), np.uint8)
    safe = np.where(alpha > 0, alpha, 1.0)
    for i, ch in enumerate((2, 1, 0)):
        out[..., i] = np.clip(np.rint(buf[..., ch].astype(np.float32) * 255.0 / safe), 0, 255).astype(np.uint8)
    out[..., 3] = buf[..., 3]
    out[alpha == 0, :3] = 0
    return out


def from_surface(surface: cairo.ImageSurface) -> Image.Image:
    return from_array(surface_array(surface))


def path_mask(width: int, height: int, build: Callable[[cairo.Context], None], antialias: bool = True) -> np.ndarray:
    """Fill the path ``build(cr)`` makes and return its coverage as a ``(h, w)`` float array 0..1."""
    surface = cairo.ImageSurface(cairo.FORMAT_A8, max(1, width), max(1, height))
    cr = cairo.Context(surface)
    if not antialias:
        cr.set_antialias(cairo.ANTIALIAS_NONE)
    build(cr)
    cr.set_source_rgba(0, 0, 0, 1)
    cr.fill()
    surface.flush()
    buf = np.ndarray((surface.get_height(), surface.get_stride()), np.uint8, buffer=surface.get_data())
    return buf[:height, :width].astype(np.float32) / 255.0


def apply_mask(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Multiply the alpha channel by ``mask`` (TextureBrush fill of a path onto a clear bitmap)."""
    a = to_array(img)
    a[..., 3] = np.rint(a[..., 3].astype(np.float32) * mask).astype(np.uint8)
    return from_array(a)


def color_layer(width: int, height: int, color: str, mask: np.ndarray) -> Image.Image:
    """A solid ``color`` layer whose alpha is ``color.a * mask``."""
    r, g, b, a = parse_color(color)
    arr = np.zeros((height, width, 4), np.float32)
    arr[..., 0], arr[..., 1], arr[..., 2] = r, g, b
    arr[..., 3] = a * mask
    return from_array(arr)


def brush_layer(width: int, height: int, mask: np.ndarray, color: str,
                gradient: GradientInfo | None = None,
                rect: tuple[float, float, float, float] | None = None) -> Image.Image:
    """A colour or gradient brush painted through ``mask``."""
    if gradient is not None and gradient.is_valid:
        arr = gradient_array(width, height, gradient, rect)
        arr = arr.copy()
        arr[..., 3] = arr[..., 3] * mask
        return from_array(arr)
    return color_layer(width, height, color, mask)


def rounded_rect_path(cr: cairo.Context, x: float, y: float, w: float, h: float, radius: float) -> None:
    """GDI+ ``AddRoundedRectangle``: plain rectangle for r <= 0, capsule when r >= min(w, h) / 2."""
    cr.new_path()
    if radius <= 0:
        cr.rectangle(x, y, w, h)
        return
    r = min(radius, min(w, h) / 2.0)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def closed_curve_path(cr: cairo.Context, points: list[tuple[float, float]], tension: float = 0.5) -> None:
    """GDI+ ``FillClosedCurve``: closed cardinal spline through ``points`` (Bezier control points
    ``P[i] + t(P[i+1] - P[i-1]) / 3``)."""
    n = len(points)
    cr.new_path()
    if n < 3:
        for i, p in enumerate(points):
            (cr.move_to if i == 0 else cr.line_to)(*p)
        cr.close_path()
        return
    k = tension / 3.0
    cr.move_to(*points[0])
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        c1 = (p1[0] + k * (p2[0] - p0[0]), p1[1] + k * (p2[1] - p0[1]))
        c2 = (p2[0] - k * (p3[0] - p1[0]), p2[1] - k * (p3[1] - p1[1]))
        cr.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    cr.close_path()


def polygon_path(cr: cairo.Context, points: list[tuple[float, float]]) -> None:
    cr.new_path()
    for i, p in enumerate(points):
        (cr.move_to if i == 0 else cr.line_to)(*p)
    cr.close_path()


DASH_STYLES = ["Solid", "Dash", "Dot", "DashDot", "DashDotDot"]
_DASHES = {"Dash": [3, 1], "Dot": [1, 1], "DashDot": [3, 1, 1, 1], "DashDotDot": [3, 1, 1, 1, 1, 1]}


def set_dash(cr: cairo.Context, dash_style: str, width: float) -> None:
    """GDI+ dash patterns are in units of the pen width."""
    pattern = _DASHES.get(dash_style)
    if pattern:
        cr.set_dash([d * width for d in pattern])
    else:
        cr.set_dash([])


# --------------------------------------------------------------------------- geometry

PLACEMENTS = ["TopLeft", "TopCenter", "TopRight", "MiddleLeft", "MiddleCenter", "MiddleRight",
              "BottomLeft", "BottomCenter", "BottomRight"]
SIDES = ["Top", "Bottom", "Left", "Right"]
ALL_SIDES = "Top, Bottom, Left, Right"


def ints(value, n: int) -> list[int]:
    """The first ``n`` ints of a point/size/padding list param, zero-filled."""
    vals = [int(v) for v in (value or [])][:n]
    return vals + [0] * (n - len(vals))


def parse_flags(value: str | list | None) -> set[str]:
    if value is None:
        return set()
    items = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return {s.strip() for s in items if s and s.strip() and s.strip() != "None"}


def format_flags(flags: set[str] | list[str], order: list[str] = SIDES) -> str:
    chosen = [s for s in order if s in set(flags)]
    return ", ".join(chosen) if chosen else "None"


def get_position(placement: str, offset: tuple[int, int] | list[int], bg_size: tuple[int, int],
                 obj_size: tuple[int, int]) -> tuple[int, int]:
    """ShareX ``Helpers.GetPosition`` (offsets push inwards from the anchored edge)."""
    bw, bh = bg_size
    ow, oh = obj_size
    ox, oy = int(offset[0]), int(offset[1])
    mid_x = round(bw / 2 - ow / 2)
    mid_y = round(bh / 2 - oh / 2)
    right = bw - ow
    bottom = bh - oh
    return {
        "TopCenter": (mid_x, oy), "TopRight": (right - ox, oy),
        "MiddleLeft": (ox, mid_y), "MiddleCenter": (mid_x, mid_y), "MiddleRight": (right - ox, mid_y),
        "BottomLeft": (ox, bottom - oy), "BottomCenter": (mid_x, bottom - oy), "BottomRight": (right - ox, bottom - oy),
    }.get(placement, (ox, oy))


def apply_aspect_ratio(width: int, height: int, src_w: int, src_h: int) -> tuple[int, int]:
    """ShareX ``ApplyAspectRatio``: a 0 dimension follows the other one."""
    if width == 0:
        return round(height / src_h * src_w), height
    if height == 0:
        return width, round(width / src_w * src_h)
    return width, height


def rect_contains(outer_w: int, outer_h: int, x: int, y: int, w: int, h: int) -> bool:
    return x >= 0 and y >= 0 and x + w <= outer_w and y + h <= outer_h


def add_canvas(img: Image.Image, margin: tuple[int, int, int, int] | list[int],
               color: str = "#00FFFFFF") -> Image.Image | None:
    """ShareX ``AddCanvas(img, Padding, color)``; ``None`` when nothing would change or the result is empty."""
    left, top, right, bottom = (int(v) for v in margin)
    if left == top == right == bottom == 0:
        return None
    w = img.width + left + right
    h = img.height + top + bottom
    if w < 1 or h < 1:
        return None
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_over(out, img, left, top)
    r, g, b, a = parse_color(color)
    if a > 0:
        fill = (r, g, b, a)
        if left > 0:
            out.paste(fill, (0, 0, left, h))
        if top > 0:
            out.paste(fill, (0, 0, w, top))
        if right > 0:
            out.paste(fill, (w - right, 0, w, h))
        if bottom > 0:
            out.paste(fill, (0, h - bottom, w, h))
    return out


def find_auto_crop_rect(img: Image.Image, same_color_crop: bool = False,
                        sides: set[str] | None = None) -> tuple[int, int, int, int]:
    """ShareX ``FindAutoCropRectangle`` -> (x, y, w, h). The reference colour is pixel (0, 0)
    (bottom-right for the right/bottom sides unless ``same_color_crop``); when it is fully
    transparent only alpha is compared."""
    sides = {"Top", "Bottom", "Left", "Right"} if sides is None else sides
    a = to_array(img).view(np.uint32)[..., 0]  # one uint32 per pixel
    h, w = a.shape
    x0, y0, cw, ch = 0, 0, w, h
    if not sides:
        return x0, y0, cw, ch

    def ref(pixel_y: int, pixel_x: int) -> tuple[np.uint32, np.uint32]:
        rgba = to_array(img)[pixel_y, pixel_x]
        mask = np.uint32(0xFFFFFFFF)
        if rgba[3] == 0:
            mask = np.frombuffer(bytes([0, 0, 0, 255]), np.uint32)[0]
        return mask, a[pixel_y, pixel_x] & mask

    mask, check = ref(0, 0)
    diff = (a & mask) != check
    if "Left" in sides:
        cols = np.nonzero(diff.any(axis=0))[0]
        if cols.size == 0:
            return x0, y0, cw, ch
        x0 = int(cols[0])
        cw -= x0
    if "Top" in sides:
        rows = np.nonzero(diff.any(axis=1))[0]
        if rows.size:
            y0 = int(rows[0])
            ch -= y0
    if not same_color_crop:
        mask, check = ref(h - 1, w - 1)
        diff = (a & mask) != check
    if "Right" in sides:
        cols = np.nonzero(diff.any(axis=0))[0]
        if cols.size:
            cw = int(cols[-1]) - x0 + 1
    if "Bottom" in sides:
        rows = np.nonzero(diff.any(axis=1))[0]
        if rows.size:
            ch = int(rows[-1]) - y0 + 1
    return x0, y0, cw, ch


def crop_bitmap(img: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image | None:
    """ShareX ``CropBitmap``: ``None`` unless the rectangle is non-empty and inside the image."""
    if x >= 0 and y >= 0 and w > 0 and h > 0 and rect_contains(img.width, img.height, x, y, w, h):
        return img.crop((x, y, x + w, y + h))
    return None


def rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """ShareX ``ImageHelpers.RoundedCorners``: clip to an anti-aliased rounded rectangle."""
    w, h = img.size
    mask = path_mask(w, h, lambda cr: rounded_rect_path(cr, 0, 0, w, h, radius))
    return apply_mask(img, mask)


def color_mask(img: Image.Image, opacity: float, color: str) -> Image.Image:
    """``ColorMatrixManager.Mask(opacity, color)`` drawn onto a clear bitmap: RGB = colour,
    alpha = source alpha * colour alpha * opacity; fully transparent source pixels stay (0,0,0,0)."""
    a = to_array(img)
    r, g, b, ca = parse_color(color)
    out = np.zeros_like(a)
    alpha = np.clip(np.rint(a[..., 3].astype(np.float32) * (ca / 255.0) * opacity), 0, 255).astype(np.uint8)
    keep = alpha > 0
    out[keep, 0], out[keep, 1], out[keep, 2] = r, g, b
    out[..., 3] = alpha
    return from_array(out)


def add_shadow(img: Image.Image, opacity: float, size: int, darkness: float, color: str,
               offset: tuple[int, int] | list[int], auto_resize: bool = True) -> Image.Image:
    """ShareX ``ImageHelpers.AddShadow``."""
    size = max(0, int(size))
    ox, oy = int(offset[0]), int(offset[1])
    w, h = img.size
    shadow = Image.new("RGBA", (w + size * 2, h + size * 2), (0, 0, 0, 0))
    draw_over(shadow, color_mask(img, opacity, color), size, size)
    arr = to_array(shadow)
    if size > 0:
        arr = box_blur(arr, size)
    if darkness > 1:
        arr = arr.copy()
        arr[..., 3] = np.clip(np.rint(arr[..., 3].astype(np.float32) * darkness), 0, 255).astype(np.uint8)
    shadow = from_array(arr)
    if auto_resize:
        out = Image.new("RGBA", (shadow.width + abs(ox), shadow.height + abs(oy)), (0, 0, 0, 0))
        draw_over(out, shadow, max(0, ox), max(0, oy))
        draw_over(out, img, max(size, -ox + size), max(size, -oy + size))
    else:
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_over(out, shadow, -size + ox, -size + oy)
        draw_over(out, img, 0, 0)
    return out


def draw_background_image(img: Image.Image, background: Image.Image | None, center: bool = True,
                          tile: bool = False) -> Image.Image:
    """ShareX ``ImageHelpers.DrawBackgroundImage``: tile, or cover-scale the background, then draw
    the image on top."""
    if background is None:
        return img
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bw, bh = background.size
    if tile:
        tx = ty = 0
        if center:
            tx = _cs_mod(_cs_div(w - bw, 2), bw)
            ty = _cs_mod(_cs_div(h - bh, 2), bh)
        # start one tile up/left so a positive translation still covers the origin
        sx = tx - bw if tx > 0 else tx
        sy = ty - bh if ty > 0 else ty
        for yy in range(sy, h, bh):
            for xx in range(sx, w, bw):
                draw_over(out, background, xx, yy)
    else:
        aspect = bw / bh
        width = w
        height = int(width / aspect)
        if height < h:
            height = h
            width = int(height * aspect)
        x = y = 0
        if center:
            x = _cs_div(w - width, 2)
            y = _cs_div(h - height, 2)
        scaled = background.resize((max(1, width), max(1, height)), Image.Resampling.BICUBIC)
        draw_over(out, scaled, x, y)
    draw_over(out, img, 0, 0)
    return out


def _cs_div(a: int, b: int) -> int:
    """C# integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _cs_mod(a: int, b: int) -> int:
    """C# ``%`` (sign follows the dividend)."""
    return a - _cs_div(a, b) * b


# --------------------------------------------------------------------------- files

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico")


def image_effects_dir() -> Path:
    """ShareL's equivalent of ShareX's ``%ShareXImageEffects%`` folder (assets of imported packages)."""
    from ..core import paths
    return paths.data_dir() / "ImageEffects"


_VAR_RE = re.compile(r"%([^%/\\]+)%")


def expand_path(path: str) -> str:
    """ShareX ``FileHelpers.ExpandFolderVariables`` for Linux: ``%ShareXImageEffects%``, ``%VAR%``
    and ``$VAR`` environment variables and ``~``; Windows separators become ``/``."""
    if not path:
        return ""

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key.lower() == "shareximageeffects":
            return str(image_effects_dir())
        if key.lower() in ("sharex", "sharexpersonal"):
            from ..core import paths
            return str(paths.data_dir())
        return os.environ.get(key, m.group(0))

    out = _VAR_RE.sub(repl, path)
    out = os.path.expandvars(os.path.expanduser(out))
    if "\\" in out and os.sep == "/":
        out = out.replace("\\", "/")
    return out


def load_image(path: str) -> Image.Image | None:
    p = expand_path(path)
    if not p or not os.path.isfile(p):
        return None
    try:
        with Image.open(p) as im:
            im.load()
            return im.convert("RGBA")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- fonts

@dataclass
class FontSpec:
    family: str = "Arial"
    size: float = 11.25
    unit: str = "pt"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False

    @property
    def size_px(self) -> float:
        """Em size in pixels at 96 DPI (``g.DpiY * SizeInPoints / 72``)."""
        if self.unit == "px":
            return self.size
        if self.unit == "in":
            return self.size * 96
        if self.unit == "mm":
            return self.size * 96 / 25.4
        return self.size * 96 / 72


_FONT_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(pt|px|world|in|mm|point|pixel)?\s*$", re.I)


def parse_font(value: str, default: str = "Arial, 11.25pt") -> FontSpec:
    """Parse .NET ``FontConverter`` text: ``"Family, 12pt[, style=Bold, Italic]"``."""
    text = (value or default).strip()
    style_part = ""
    m = re.search(r",\s*style\s*=\s*(.*)$", text, re.I)
    if m:
        style_part = m.group(1)
        text = text[:m.start()]
    parts = [p.strip() for p in text.split(",") if p.strip()]
    spec = FontSpec()
    if parts:
        spec.family = parts[0]
    if len(parts) > 1:
        sm = _FONT_SIZE_RE.match(parts[1])
        if sm:
            spec.size = float(sm.group(1))
            unit = (sm.group(2) or "pt").lower()
            spec.unit = {"point": "pt", "pixel": "px", "world": "px"}.get(unit, unit)
    styles = {s.strip().lower() for s in style_part.split(",")}
    spec.bold = "bold" in styles
    spec.italic = "italic" in styles
    spec.underline = "underline" in styles
    spec.strikeout = "strikeout" in styles
    return spec


def format_font(spec: FontSpec) -> str:
    size = f"{spec.size:g}{spec.unit}"
    styles = [n for n, on in (("Bold", spec.bold), ("Italic", spec.italic), ("Underline", spec.underline),
                              ("Strikeout", spec.strikeout)) if on]
    out = f"{spec.family}, {size}"
    return out + (", style=" + ", ".join(styles) if styles else "")


def pango_layout(cr: cairo.Context, text: str, spec: FontSpec):
    """A PangoCairo layout for ``text`` in ``spec`` at 96 DPI (fonts are resolved by family name)."""
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_resolution(layout.get_context(), 96.0)
    layout.context_changed()
    desc = Pango.FontDescription()
    desc.set_family(spec.family)
    desc.set_absolute_size(spec.size_px * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if spec.bold else Pango.Weight.NORMAL)
    desc.set_style(Pango.Style.ITALIC if spec.italic else Pango.Style.NORMAL)
    layout.set_font_description(desc)
    attrs = Pango.AttrList()
    if spec.underline:
        attrs.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
    if spec.strikeout:
        attrs.insert(Pango.attr_strikethrough_new(True))
    layout.set_attributes(attrs)
    layout.set_text(text, -1)
    return layout


def layout_path(cr: cairo.Context, layout) -> None:
    from gi.repository import PangoCairo
    PangoCairo.layout_path(cr, layout)


def show_layout(cr: cairo.Context, layout) -> None:
    from gi.repository import PangoCairo
    PangoCairo.show_layout(cr, layout)


def parse_text(text: str, width: int, height: int) -> str:
    """Run a watermark through the name parser (``NameParserType.Text`` with the image size)."""
    from ..core.nameparser import NameParser, NameParserType
    try:
        return NameParser(NameParserType.Text, image_width=width, image_height=height).parse(text)
    except Exception:
        return text
