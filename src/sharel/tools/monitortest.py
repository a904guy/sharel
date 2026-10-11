"""Monitor test patterns (ShareX MonitorTest window), rendered to Pillow images.

ShareX's modes are Grayscale, Rgb, Gradient, Pattern and Motion; ShareL adds a grid and RGB channel gradient
bands. Every function returns a new ``RGB`` image of ``size`` = (width, height).

API
---
``solid(size, color)``                                     fill with an (r, g, b) colour.
``grayscale(size, value)``                                 solid gray 0..255.
``gradient(size, start, end, direction="Vertical")``       linear gradient; directions ``Horizontal``,
                                                           ``Vertical``, ``ForwardDiagonal`` (top-left →
                                                           bottom-right), ``BackwardDiagonal`` (top-right →
                                                           bottom-left).
``gradient_bands(size, direction="Horizontal")``           four bands black→red, black→green, black→blue,
                                                           black→white (gray ramp) for banding checks.
``pattern(size, kind="Checkerboard", pattern_size=5)``     black on white ``HorizontalLines`` (rows of
                                                           ``pattern_size`` px), ``VerticalLines`` or
                                                           ``Checkerboard`` cells, tiled from (0, 0).
``grid(size, spacing=50, line_width=1, line=(255,255,255), background=(0,0,0))``  lines at x, y = 0, s, 2s...
                                                           plus the last row/column (so the border is closed).
``motion_frame(size, n, fps=60, speed=500, direction="VerticalBars", bar_size=50)``
    frame ``n`` of the tearing test: black bars of ``bar_size`` px with period ``2*bar_size`` on white, moved by
    ``n / fps * speed`` px (ShareX: speed clamped to 100..2000 px/s). ``motion_frames(...)`` yields frames.
``MonitorTestSettings`` + ``render(settings, size, frame=0)``  one-call renderer for the window's current mode.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from PIL import Image

__all__ = ["GRADIENT_DIRECTIONS", "MODES", "MOTION_DIRECTIONS", "PATTERNS", "MonitorTestSettings", "gradient",
           "gradient_bands", "grayscale", "grid", "motion_frame", "motion_frames", "pattern", "render", "solid"]

RGB = tuple[int, int, int]
MODES = ("Grayscale", "Rgb", "Gradient", "Pattern", "Motion", "Grid", "GradientBands")
GRADIENT_DIRECTIONS = ("Horizontal", "Vertical", "ForwardDiagonal", "BackwardDiagonal")
PATTERNS = ("HorizontalLines", "VerticalLines", "Checkerboard")
MOTION_DIRECTIONS = ("VerticalBars", "HorizontalBars")


def _check_size(size: tuple[int, int]) -> tuple[int, int]:
    w, h = int(size[0]), int(size[1])
    if w < 1 or h < 1:
        raise ValueError(f"invalid size {size!r}")
    return w, h


def _byte(v: float) -> int:
    return round(min(255.0, max(0.0, float(v))))


def solid(size: tuple[int, int], color: RGB) -> Image.Image:
    return Image.new("RGB", _check_size(size), tuple(_byte(c) for c in color[:3]))


def grayscale(size: tuple[int, int], value: float) -> Image.Image:
    v = _byte(value)
    return solid(size, (v, v, v))


def _ramp(w: int, h: int, direction: str) -> np.ndarray:
    """0..1 parameter per pixel along the gradient direction (float64, shape h×w)."""
    xs = np.arange(w, dtype=np.float64)
    ys = np.arange(h, dtype=np.float64)
    if direction == "Horizontal":
        t = np.broadcast_to(xs / max(1, w - 1), (h, w))
    elif direction == "Vertical":
        t = np.broadcast_to((ys / max(1, h - 1))[:, None], (h, w))
    elif direction in ("ForwardDiagonal", "BackwardDiagonal"):
        xx = xs if direction == "ForwardDiagonal" else (w - 1 - xs)
        # project onto the diagonal from the start corner to the opposite corner
        dx, dy = max(1, w - 1), max(1, h - 1)
        t = (xx[None, :] * dx + ys[:, None] * dy) / (dx * dx + dy * dy)
    else:
        raise ValueError(f"unknown gradient direction {direction!r}")
    return np.clip(t, 0.0, 1.0)


def gradient(size: tuple[int, int], start: RGB = (255, 255, 255), end: RGB = (0, 0, 0),
             direction: str = "Vertical") -> Image.Image:
    w, h = _check_size(size)
    t = _ramp(w, h, direction)[..., None]
    s = np.array(start[:3], dtype=np.float64)
    e = np.array(end[:3], dtype=np.float64)
    arr = np.rint(s + (e - s) * t).clip(0, 255).astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(arr), "RGB")


def gradient_bands(size: tuple[int, int], direction: str = "Horizontal") -> Image.Image:
    """Four stacked bands (or side-by-side for a vertical ramp) of black→red/green/blue/white."""
    w, h = _check_size(size)
    out = Image.new("RGB", (w, h))
    ends = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    for i, e in enumerate(ends):
        if direction == "Horizontal":
            y0, y1 = h * i // 4, h * (i + 1) // 4
            if y1 > y0:
                out.paste(gradient((w, y1 - y0), (0, 0, 0), e, "Horizontal"), (0, y0))
        elif direction == "Vertical":
            x0, x1 = w * i // 4, w * (i + 1) // 4
            if x1 > x0:
                out.paste(gradient((x1 - x0, h), (0, 0, 0), e, "Vertical"), (x0, 0))
        else:
            raise ValueError("gradient bands support Horizontal or Vertical")
    return out


def pattern(size: tuple[int, int], kind: str = "Checkerboard", pattern_size: float = 5) -> Image.Image:
    w, h = _check_size(size)
    s = max(1, round(float(pattern_size)))
    ys = (np.arange(h) // s) % 2
    xs = (np.arange(w) // s) % 2
    if kind == "HorizontalLines":
        black = np.broadcast_to((ys == 0)[:, None], (h, w))
    elif kind == "VerticalLines":
        black = np.broadcast_to((xs == 0)[None, :], (h, w))
    elif kind == "Checkerboard":
        black = (ys[:, None] ^ xs[None, :]) == 0
    else:
        raise ValueError(f"unknown pattern {kind!r}")
    gray = np.where(black, 0, 255).astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(gray), "L").convert("RGB")


def grid(size: tuple[int, int], spacing: int = 50, line_width: int = 1, line: RGB = (255, 255, 255),
         background: RGB = (0, 0, 0)) -> Image.Image:
    w, h = _check_size(size)
    if spacing < 1 or line_width < 1:
        raise ValueError("spacing and line width must be >= 1")
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:] = background[:3]
    mask_x = (np.arange(w) % spacing) < line_width
    mask_y = (np.arange(h) % spacing) < line_width
    mask_x[max(0, w - line_width):] = True
    mask_y[max(0, h - line_width):] = True
    arr[:, mask_x] = line[:3]
    arr[mask_y, :] = line[:3]
    return Image.fromarray(arr, "RGB")


def motion_frame(size: tuple[int, int], n: int, fps: float = 60.0, speed: float = 500.0,
                 direction: str = "VerticalBars", bar_size: int = 50) -> Image.Image:
    w, h = _check_size(size)
    if fps <= 0:
        raise ValueError("fps must be positive")
    if direction not in MOTION_DIRECTIONS:
        raise ValueError(f"unknown motion direction {direction!r}")
    speed = min(2000.0, max(100.0, float(speed)))
    period = bar_size * 2
    offset = n / fps * speed
    start = offset % period - bar_size  # in [-bar_size, bar_size)
    length = w if direction == "VerticalBars" else h
    pos = np.arange(length, dtype=np.float64)
    # pixel p is black when it lies inside [start + k*period, start + k*period + bar_size)
    black = ((pos - start) % period) < bar_size
    line = np.where(black, 0, 255).astype(np.uint8)
    if direction == "VerticalBars":
        gray = np.broadcast_to(line[None, :], (h, w))
    else:
        gray = np.broadcast_to(line[:, None], (h, w))
    return Image.fromarray(np.ascontiguousarray(gray), "L").convert("RGB")


def motion_frames(size: tuple[int, int], count: int, fps: float = 60.0, speed: float = 500.0,
                  direction: str = "VerticalBars", bar_size: int = 50) -> Iterator[Image.Image]:
    for n in range(count):
        yield motion_frame(size, n, fps, speed, direction, bar_size)


@dataclass
class MonitorTestSettings:
    """Everything the Monitor test window lets the user pick (ShareX defaults)."""

    mode: str = "Grayscale"
    gray_value: int = 255
    red: int = 255
    green: int = 0
    blue: int = 0
    gradient_start: RGB = (255, 255, 255)
    gradient_end: RGB = (0, 0, 0)
    gradient_direction: str = "Vertical"
    pattern: str = "HorizontalLines"
    pattern_size: int = 5
    motion_direction: str = "VerticalBars"
    motion_speed: float = 500.0
    fps: float = 60.0
    grid_spacing: int = 50


def render(settings: MonitorTestSettings, size: tuple[int, int], frame: int = 0) -> Image.Image:
    m = settings.mode
    if m == "Grayscale":
        return grayscale(size, settings.gray_value)
    if m == "Rgb":
        return solid(size, (settings.red, settings.green, settings.blue))
    if m == "Gradient":
        return gradient(size, settings.gradient_start, settings.gradient_end, settings.gradient_direction)
    if m == "Pattern":
        return pattern(size, settings.pattern, settings.pattern_size)
    if m == "Motion":
        return motion_frame(size, frame, settings.fps, settings.motion_speed, settings.motion_direction)
    if m == "Grid":
        return grid(size, settings.grid_spacing)
    if m == "GradientBands":
        return gradient_bands(size, "Horizontal" if settings.gradient_direction != "Vertical" else "Vertical")
    raise ValueError(f"unknown monitor test mode {m!r}")
