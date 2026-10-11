"""Cairo rendering of editor shapes and the image algorithms behind them.

* ``draw_shape(cr, shape, ctx)`` draws one shape in image coordinates (the caller sets up any
  view transform). GDI+ puts pixel centres on integer coordinates, cairo on ``n + 0.5``; shape
  geometry is therefore drawn under a ``translate(0.5, 0.5)`` so ShareX's pixel maths carries over.
* ``render_output(base, shapes)`` produces the final annotated PIL image the way ShareX's
  ``RenderOutputImage`` does: effect shapes are applied in z-order to the progressively modified
  output, then drawing shapes are drawn in z-order; region and tool shapes are not drawn.
* Effects (box blur, pixelate, highlight), cut-out edge effects (sawtooth, torn, wave) and the
  auto-crop search are numpy / Pillow ports of ShareX's ``ImageHelpers``.

Colours are ShareX-style ``"#AARRGGBB"`` strings (``"#RRGGBB"`` is accepted as opaque).
"""
from __future__ import annotations

import math
import random
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

import cairo
import gi
import numpy as np
from PIL import Image, ImageDraw

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from ..core.config import TextDrawingOptions
from ..core.enums import ArrowHeadDirection, BorderStyle, CutOutEffectType, ImageInterpolationMode, ShapeType
from . import shapes as S

RGBA = tuple[int, int, int, int]

# ---- colours ----------------------------------------------------------------------------


def parse_color(color: str) -> RGBA:
    """"#AARRGGBB" / "#RRGGBB" -> (r, g, b, a) 0..255."""
    s = color.strip().lstrip("#")
    if len(s) == 6:
        s = "FF" + s
    if len(s) != 8:
        raise ValueError(f"bad colour {color!r}, expected #AARRGGBB")
    v = int(s, 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255, (v >> 24) & 255)


def color_to_str(r: int, g: int, b: int, a: int = 255) -> str:
    return f"#{a:02X}{r:02X}{g:02X}{b:02X}"


def set_source(cr: cairo.Context, color: str | RGBA) -> None:
    r, g, b, a = parse_color(color) if isinstance(color, str) else color
    cr.set_source_rgba(r / 255, g / 255, b / 255, a / 255)


# ---- PIL <-> cairo ----------------------------------------------------------------------


def pil_to_surface(img: Image.Image) -> cairo.ImageSurface:
    """RGBA PIL image -> premultiplied ARGB32 cairo surface."""
    img = img.convert("RGBA") if img.mode != "RGBA" else img
    w, h = img.size
    arr = np.asarray(img, dtype=np.uint16)
    a = arr[..., 3:4]
    rgb = (arr[..., :3] * a + 127) // 255
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[..., 0] = rgb[..., 2]
    bgra[..., 1] = rgb[..., 1]
    bgra[..., 2] = rgb[..., 0]
    bgra[..., 3] = a[..., 0]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = surf.get_stride()
    buf = np.ndarray((h, stride), dtype=np.uint8, buffer=surf.get_data())
    buf[:, : w * 4] = bgra.reshape(h, w * 4)
    surf.mark_dirty()
    return surf


def surface_to_pil(surf: cairo.ImageSurface) -> Image.Image:
    """Premultiplied ARGB32 surface -> straight-alpha RGBA PIL image."""
    surf.flush()
    w, h = surf.get_width(), surf.get_height()
    stride = surf.get_stride()
    buf = np.ndarray((h, stride), dtype=np.uint8, buffer=surf.get_data())
    bgra = buf[:, : w * 4].reshape(h, w, 4).astype(np.uint16)
    a = bgra[..., 3]
    out = np.empty((h, w, 4), dtype=np.uint8)
    safe = np.where(a == 0, 1, a)
    for dst, src in ((0, 2), (1, 1), (2, 0)):
        out[..., dst] = np.where(a == 0, 0, np.minimum(255, (bgra[..., src] * 255 + safe // 2) // safe))
    out[..., 3] = a
    return Image.fromarray(out, "RGBA")


def new_surface(w: int, h: int) -> cairo.ImageSurface:
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(w)), max(1, int(h)))


# ---- text -------------------------------------------------------------------------------


def font_description(family: str, size: float, bold: bool = False, italic: bool = False) -> Pango.FontDescription:
    fd = Pango.FontDescription()
    fd.set_family(family or "Sans")
    fd.set_size(max(1, round(size * Pango.SCALE)))
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_style(Pango.Style.ITALIC if italic else Pango.Style.NORMAL)
    return fd


def make_layout(cr: cairo.Context, text: str, family: str, size: float, bold=False, italic=False, underline=False,
                width: float | None = None, align: str = "Left") -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_resolution(layout.get_context(), 96.0)
    layout.context_changed()
    layout.set_font_description(font_description(family, size, bold, italic))
    layout.set_text(text, -1)
    if underline:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
        layout.set_attributes(attrs)
    if width is not None:
        layout.set_width(max(1, int(width * Pango.SCALE)))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_alignment({"Left": Pango.Alignment.LEFT, "Near": Pango.Alignment.LEFT,
                          "Center": Pango.Alignment.CENTER, "Right": Pango.Alignment.RIGHT,
                          "Far": Pango.Alignment.RIGHT}.get(align, Pango.Alignment.LEFT))
    return layout


_measure_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)


def measure_text(text: str, family: str = "Sans", size: float = 9, bold: bool = False, italic: bool = False,
                 underline: bool = False) -> tuple[float, float]:
    """Logical size in px of (multi-line) text, like GDI's MeasureString."""
    cr = cairo.Context(_measure_surface)
    layout = make_layout(cr, text, family, size, bold, italic, underline)
    _ink, logical = layout.get_pixel_extents()
    return float(logical.width), float(logical.height)


def _text_options_layout(cr, text: str, o: TextDrawingOptions, rect: S.Rect) -> tuple[Pango.Layout, float, float]:
    """Layout wrapped to ``rect`` with ShareX's horizontal/vertical alignment; returns the layout and
    its top-left position."""
    layout = make_layout(cr, text, o.font, o.size, o.bold, o.italic, o.underline, width=rect.w,
                         align=o.alignment_horizontal)
    _ink, logical = layout.get_pixel_extents()
    if o.alignment_vertical in ("Center", "Middle"):
        y = rect.y + (rect.h - logical.height) / 2
    elif o.alignment_vertical in ("Bottom", "Far"):
        y = rect.y + rect.h - logical.height
    else:
        y = rect.y
    return layout, rect.x, y


# ---- paths ------------------------------------------------------------------------------


def path_rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, radius: float) -> None:
    """GraphicsPath.AddRoundedRectangle (a capsule when the radius reaches half the short side)."""
    if w <= 0 or h <= 0:
        return
    if radius <= 0:
        cr.rectangle(x, y, w, h)
        return
    radius = min(radius, min(w, h) / 2)
    cr.new_sub_path()
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def path_ellipse(cr: cairo.Context, x: float, y: float, w: float, h: float) -> None:
    if w <= 0 or h <= 0:
        return
    cr.save()
    cr.translate(x + w / 2, y + h / 2)
    cr.scale(w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.close_path()
    cr.restore()


def cardinal_spline_points(points: Sequence[S.Point], tension: float = 0.5, steps: int = 24) -> list[S.Point]:
    """Flatten GDI+ DrawCurve (cardinal spline, tension 0.5) into a polyline."""
    pts = list(points)
    if len(pts) < 3:
        return pts
    out: list[S.Point] = [pts[0]]
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3, p1[1] + (p2[1] - p0[1]) * tension / 3)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3, p2[1] - (p3[1] - p1[1]) * tension / 3)
        for k in range(1, steps + 1):
            t = k / steps
            mt = 1 - t
            out.append((mt ** 3 * p1[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * p2[0],
                        mt ** 3 * p1[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * p2[1]))
    return out


def _dedupe(points: Sequence[S.Point]) -> list[S.Point]:
    out: list[S.Point] = []
    for p in points:
        if not out or (abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9):
            out.append(p)
    return out


def _trim_polyline(points: list[S.Point], length: float, from_end: bool) -> list[S.Point]:
    """Remove ``length`` px of polyline from one end (GDI CustomLineCap.BaseInset)."""
    pts = list(reversed(points)) if from_end else list(points)
    remaining = length
    while len(pts) >= 2 and remaining > 0:
        seg = S.distance(pts[0], pts[1])
        if seg > remaining:
            t = remaining / seg
            pts[0] = (S.lerp(pts[0][0], pts[1][0], t), S.lerp(pts[0][1], pts[1][1], t))
            remaining = 0
        else:
            remaining -= seg
            pts.pop(0)
    if len(pts) < 2:
        pts = [pts[0], pts[0]] if pts else []
    return list(reversed(pts)) if from_end else pts


def _end_direction(points: list[S.Point], at_end: bool, min_dist: float) -> tuple[float, float] | None:
    """Unit direction pointing out of the line at one end (looks back ``min_dist`` px)."""
    pts = list(reversed(points)) if not at_end else list(points)
    tip = pts[-1]
    for p in reversed(pts[:-1]):
        d = S.distance(p, tip)
        if d >= min_dist or p is pts[0]:
            if d == 0:
                return None
            return ((tip[0] - p[0]) / d, (tip[1] - p[1]) / d)
    return None


DASH_PATTERNS: dict[str, list[float]] = {
    "Dash": [3, 1], "Dot": [1, 1], "DashDot": [3, 1, 1, 1], "DashDotDot": [3, 1, 1, 1, 1, 1],
}


def set_pen(cr: cairo.Context, width: float, style: BorderStyle | str, *, round_caps: bool = False,
            round_join: bool = False) -> bool:
    """Set width/dash/cap/join like a GDI+ Pen. Returns True when dashed."""
    cr.set_line_width(width)
    pattern = DASH_PATTERNS.get(str(style))
    if pattern:
        cr.set_dash([v * width for v in pattern], 0)
        cr.set_line_cap(cairo.LINE_CAP_BUTT)  # GDI+ DashCap.Flat
    else:
        cr.set_dash([], 0)
        cr.set_line_cap(cairo.LINE_CAP_ROUND if round_caps else cairo.LINE_CAP_BUTT)
    cr.set_line_join(cairo.LINE_JOIN_ROUND if round_join else cairo.LINE_JOIN_MITER)
    return bool(pattern)


# ---- render context / effect cache --------------------------------------------------------


class EffectCache:
    """Effect results keyed by (uid, rect, params, canvas version)."""

    def __init__(self, max_entries: int = 64) -> None:
        self._d: OrderedDict = OrderedDict()
        self.max_entries = max_entries
        self.computed = 0

    def get(self, key):
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key, value) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


@dataclass
class RenderContext:
    """What shapes need from their surroundings while drawing."""
    canvas: Image.Image  # the base image (ShareX Form.Canvas) used by magnify / effects previews
    canvas_surface: cairo.ImageSurface | None = None
    canvas_version: int = 0
    is_output: bool = False
    dimmed: bool = False  # region capture background is dimmed (smart eraser uses the dimmed colour)
    current_tool: ShapeType | None = None
    minimum_size: int = 5
    #: uids of effect shapes being moved/resized: draw the overlay instead of the effect.
    effect_overlay_uids: set[int] = field(default_factory=set)
    effect_cache: EffectCache | None = None

    def surface(self) -> cairo.ImageSurface:
        if self.canvas_surface is None:
            self.canvas_surface = pil_to_surface(self.canvas)
        return self.canvas_surface

    @property
    def canvas_rect(self) -> S.Rect:
        return S.Rect(0, 0, self.canvas.width, self.canvas.height)


_FILTERS = {
    ImageInterpolationMode.NearestNeighbor: cairo.FILTER_NEAREST,
    ImageInterpolationMode.Bilinear: cairo.FILTER_BILINEAR,
    ImageInterpolationMode.HighQualityBilinear: cairo.FILTER_GOOD,
    ImageInterpolationMode.Bicubic: cairo.FILTER_GOOD,
    ImageInterpolationMode.HighQualityBicubic: cairo.FILTER_BEST,
}

# ---- shape drawing ----------------------------------------------------------------------


def draw_shape(cr: cairo.Context, shape: S.Shape, ctx: RenderContext) -> None:
    """Draw one drawing / effect shape (regions and tools are overlays drawn by the manager)."""
    fn = _DRAWERS.get(type(shape))
    if fn is None:
        for cls, f in _DRAWERS.items():
            if isinstance(shape, cls):
                fn = f
                break
    if fn is None:
        return
    cr.save()
    try:
        fn(cr, shape, ctx)
    finally:
        cr.restore()


def _rectangle(cr, border: str, size: int, style, fill: str, r: S.Rect, radius: int) -> None:
    """ShareX RectangleDrawingShape.DrawRectangle (GDI path is rect shrunk by 1 px)."""
    fill_visible = parse_color(fill)[3] > 0
    border_visible = size > 0 and parse_color(border)[3] > 0
    if not fill_visible and not border_visible:
        return
    cr.save()
    cr.translate(0.5, 0.5)
    cr.set_antialias(cairo.ANTIALIAS_DEFAULT if radius > 0 else cairo.ANTIALIAS_NONE)
    w, h = r.w - 1, r.h - 1
    if w > 0 and h > 0:
        if fill_visible:
            path_rounded_rect(cr, r.x, r.y, w, h, radius)
            set_source(cr, fill)
            cr.fill()
        if border_visible:
            path_rounded_rect(cr, r.x, r.y, w, h, radius)
            set_pen(cr, size, style)
            set_source(cr, border)
            cr.stroke()
    cr.restore()


def _draw_rectangle_shape(cr, s: S.DrawingRectangle, ctx) -> None:
    r = s.rect
    if s.shadow:
        sr = r.offset(*s.shadow_offset)
        if s.is_border_visible:
            _rectangle(cr, s.shadow_color, s.border_size, s.border_style, "#00000000", sr, s.corner_radius)
        elif parse_color(s.fill_color)[3] == 255:
            _rectangle(cr, "#00000000", 0, s.border_style, s.shadow_color, sr, s.corner_radius)
    _rectangle(cr, s.border_color, s.border_size, s.border_style, s.fill_color, r, s.corner_radius)


def _ellipse(cr, border: str, size: int, style, fill: str, r: S.Rect) -> None:
    cr.save()
    cr.translate(0.5, 0.5)
    if parse_color(fill)[3] > 0:
        path_ellipse(cr, r.x, r.y, r.w, r.h)
        set_source(cr, fill)
        cr.fill()
    if size > 0 and parse_color(border)[3] > 0:
        path_ellipse(cr, r.x, r.y, r.w, r.h)
        set_pen(cr, size, style)
        set_source(cr, border)
        cr.stroke()
    cr.restore()


def _draw_ellipse_with_shadow(cr, s: S.DrawingShape, r: S.Rect) -> None:
    if s.shadow:
        sr = r.offset(*s.shadow_offset)
        if s.is_border_visible:
            _ellipse(cr, s.shadow_color, s.border_size, s.border_style, "#00000000", sr)
        elif parse_color(s.fill_color)[3] == 255:
            _ellipse(cr, "#00000000", 0, s.border_style, s.shadow_color, sr)
    _ellipse(cr, s.border_color, s.border_size, s.border_style, s.fill_color, r)


def _draw_ellipse_shape(cr, s: S.DrawingEllipse, ctx) -> None:
    _draw_ellipse_with_shadow(cr, s, s.rect)


def _arrow_head(cr, tip: S.Point, direction: tuple[float, float], width: float) -> None:
    """GDI CustomLineCap used by ShareX: tip at the end point, 6w long, 4w wide, slightly
    concave base (curve through (0, -5w))."""
    dx, dy = direction
    px, py = -dy, dx  # perpendicular

    def at(along: float, across: float) -> S.Point:
        return (tip[0] + dx * along + px * across, tip[1] + dy * along + py * across)

    w = width
    cr.move_to(*at(0, 0))
    cr.line_to(*at(-6 * w, -2 * w))
    c = at(-5 * w, 0)
    b = at(-6 * w, 2 * w)
    a = at(-6 * w, -2 * w)
    # quadratic through c approximated with a cubic whose control points pull towards c
    cr.curve_to(a[0] + (c[0] - a[0]) * 4 / 3 * 0.5, a[1] + (c[1] - a[1]) * 4 / 3 * 0.5,
                b[0] + (c[0] - b[0]) * 4 / 3 * 0.5, b[1] + (c[1] - b[1]) * 4 / 3 * 0.5, b[0], b[1])
    cr.close_path()
    cr.fill()


def _stroke_polyline(cr, color: str, width: int, style, points: list[S.Point],
                     arrow: ArrowHeadDirection | None = None) -> None:
    """A round-capped/joined polyline with optional ShareX arrow caps."""
    pts = _dedupe(points)
    if not pts or width <= 0 or parse_color(color)[3] == 0:
        return
    cr.save()
    cr.translate(0.5, 0.5)
    set_source(cr, color)
    if len(pts) == 1:
        # ShareX draws a dot (FillEllipse of border size) for a single freehand point.
        x, y = pts[0]
        cr.arc(x, y, width / 2, 0, 2 * math.pi)
        cr.fill()
        cr.restore()
        return
    heads: list[tuple[S.Point, tuple[float, float]]] = []
    if arrow is not None:
        inset = 5 * width  # BaseInset = arrowHeight - arrowCurve
        total = sum(S.distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        want_end = arrow in (ArrowHeadDirection.End, ArrowHeadDirection.Both)
        want_start = arrow == ArrowHeadDirection.Start or (
            arrow == ArrowHeadDirection.Both and S.distance(pts[0], pts[-1]) > 6 * width * 2)
        look = max(1.0, min(total / 2, 6 * width))
        if want_end:
            d = _end_direction(pts, True, look)
            if d:
                heads.append((pts[-1], d))
        if want_start:
            d = _end_direction(pts, False, look)
            if d:
                heads.append((pts[0], d))
        if want_end:
            pts = _trim_polyline(pts, min(inset, total * 0.999), True)
        if want_start:
            pts = _trim_polyline(pts, min(inset, total * 0.999), False)
    dashed = set_pen(cr, width, style, round_caps=arrow is None, round_join=True)
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.stroke()
    if dashed and arrow is None:
        for p in (pts[0], pts[-1]):  # GDI+ keeps round start/end caps on dashed pens
            cr.arc(p[0], p[1], width / 2, 0, 2 * math.pi)
            cr.fill()
    cr.set_dash([], 0)
    for tip, d in heads:
        _arrow_head(cr, tip, d, width)
    cr.restore()


def line_geometry(s: S.DrawingLine) -> list[S.Point]:
    if s.is_curved:
        return cardinal_spline_points(s.points)
    return [s.points[0], s.points[-1]]


def _draw_line_shape(cr, s: S.DrawingLine, ctx) -> None:
    width = max(s.border_size, 1)
    pts = line_geometry(s)
    arrow = s.arrow_head_direction if isinstance(s, S.DrawingArrow) else None
    if s.shadow:
        ox, oy = s.shadow_offset
        _stroke_polyline(cr, s.shadow_color, width, s.border_style, [(x + ox, y + oy) for x, y in pts], arrow)
    _stroke_polyline(cr, s.border_color, width, s.border_style, pts, arrow)


def _draw_freehand_shape(cr, s: S.DrawingFreehand, ctx) -> None:
    width = max(s.border_size, 1)
    arrow = s.arrow_head_direction if isinstance(s, S.DrawingFreehandArrow) else None
    if s.shadow:
        ox, oy = s.shadow_offset
        _stroke_polyline(cr, s.shadow_color, width, s.border_style, [(x + ox, y + oy) for x, y in s.points], arrow)
    _stroke_polyline(cr, s.border_color, width, s.border_style, list(s.points), arrow)


def _draw_text(cr, text: str, color: str, o: TextDrawingOptions, r: S.Rect) -> None:
    if not text or r.w <= 10 or r.h <= 10:
        return
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    layout, x, y = _text_options_layout(cr, text, o, r)
    cr.move_to(x, y)
    set_source(cr, color)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


def _draw_text_background_shape(cr, s: S.DrawingTextBackground, ctx) -> None:
    _draw_rectangle_shape(cr, s, ctx)
    r = s.rect
    if s.shadow:
        _draw_text(cr, s.text, s.shadow_color, s.text_options, r.offset(*s.shadow_offset))
    _draw_text(cr, s.text, s.text_options.color, s.text_options, r)


def _draw_text_outline_shape(cr, s: S.DrawingTextOutline, ctx) -> None:
    r = s.rect
    if not s.text or r.w <= 10 or r.h <= 10:
        return
    o = s.text_options
    layout, x, y = _text_options_layout(cr, s.text, o, r)
    border_visible = s.is_border_visible

    def text_path(dx=0.0, dy=0.0):
        cr.new_path()
        cr.move_to(x + dx, y + dy)
        PangoCairo.layout_path(cr, layout)

    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if s.shadow:
        ox, oy = s.shadow_offset
        text_path(ox, oy)
        set_source(cr, s.shadow_color)
        if border_visible:
            cr.set_line_width(s.border_size)
            cr.stroke()
        else:
            cr.fill()
    if border_visible:
        text_path()
        cr.set_line_width(s.border_size)
        set_source(cr, s.border_color)
        cr.stroke()
    text_path()
    if o.gradient:
        _x1, y1, _x2, y2 = cr.fill_extents()
        grad = cairo.LinearGradient(0, y1, 0, y2 + 1)
        c1, c2 = parse_color(o.color), parse_color(o.color2)
        grad.add_color_stop_rgba(0, c1[0] / 255, c1[1] / 255, c1[2] / 255, c1[3] / 255)
        grad.add_color_stop_rgba(1, c2[0] / 255, c2[1] / 255, c2[2] / 255, c2[3] / 255)
        cr.set_source(grad)
    else:
        set_source(cr, o.color)
    cr.fill()


def tail_polygon(rect: S.Rect, tail: S.Point, multiplier: float) -> list[S.Point]:
    """ShareX CreateTailPath: an isosceles triangle from the rect centre to the tail point."""
    cx, cy = rect.center
    avg = (rect.w + rect.h) / 2
    tail_width = min(multiplier * avg, rect.w, rect.h)
    half = tail_width / 2
    length = S.distance((cx, cy), tail)
    ang = math.atan2(tail[1] - cy, tail[0] - cx)
    ca, sa = math.cos(ang), math.sin(ang)

    def tr(px: float, py: float) -> S.Point:
        return (cx + px * ca - py * sa, cy + px * sa + py * ca)

    return [tr(0, -half), tr(0, half), tr(length, 0)]


def _poly(cr, pts: list[S.Point]) -> None:
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.close_path()


def _exclude_clip(cr, x, y, w, h) -> None:
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.rectangle(-1e6, -1e6, 2e6, 2e6)
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)


def _speech_balloon(cr, s: S.DrawingSpeechBalloon, border: str, size: int, fill: str, r: S.Rect,
                    tail: S.Point) -> None:
    tail_pts = tail_polygon(r, tail, s.TAIL_WIDTH_MULTIPLIER) if not r.contains(*tail) else None
    fill_on = parse_color(fill)[3] > 0
    border_on = size > 0 and parse_color(border)[3] > 0
    if fill_on:
        cr.save()
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.rectangle(r.x, r.y, r.w, r.h)
        set_source(cr, fill)
        cr.fill()
        cr.restore()
    if tail_pts:
        if fill_on:
            cr.save()
            _exclude_clip(cr, r.x, r.y, r.w, r.h)
            cr.translate(0.5, 0.5)
            _poly(cr, tail_pts)
            set_source(cr, fill)
            cr.fill()
            cr.restore()
        if border_on:
            cr.save()
            _exclude_clip(cr, r.x + 1, r.y + 1, r.w - 2, r.h - 2)
            cr.translate(0.5, 0.5)
            _poly(cr, tail_pts)
            cr.set_line_width(size)
            set_source(cr, border)
            cr.stroke()
            cr.restore()
    if border_on:
        cr.save()
        if tail_pts:
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.rectangle(-1e6, -1e6, 2e6, 2e6)
            cr.save()
            cr.translate(0.5, 0.5)
            _poly(cr, tail_pts)
            cr.restore()
            cr.clip()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        # Inset pen on the rect grown by (size - 1): the band lies just outside the box edge.
        outer = r.inflate(size - 1)
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.rectangle(outer.x + size / 2, outer.y + size / 2, outer.w - size, outer.h - size)
        cr.set_line_width(size)
        set_source(cr, border)
        cr.stroke()
        cr.restore()


def _draw_speech_balloon_shape(cr, s: S.DrawingSpeechBalloon, ctx) -> None:
    r = s.rect
    if r.w <= 10 or r.h <= 10:
        return
    if s.shadow:
        ox, oy = s.shadow_offset
        tail = (s.tail_position[0] + ox, s.tail_position[1] + oy)
        if s.is_border_visible:
            _speech_balloon(cr, s, s.shadow_color, s.border_size, "#00000000", r.offset(ox, oy), tail)
        elif parse_color(s.fill_color)[3] == 255:
            _speech_balloon(cr, s, "#00000000", 0, s.shadow_color, r.offset(ox, oy), tail)
    _speech_balloon(cr, s, s.border_color, s.border_size, s.fill_color, r, s.tail_position)
    if s.shadow:
        _draw_text(cr, s.text, s.shadow_color, s.text_options, r.offset(*s.shadow_offset))
    _draw_text(cr, s.text, s.text_options.color, s.text_options, r)


def _draw_step_shape(cr, s: S.DrawingStep, ctx) -> None:
    s.update_size(measure_text)
    r = s.rect
    if s.shadow:
        sr = r.offset(*s.shadow_offset)
        if s.is_border_visible:
            _ellipse(cr, s.shadow_color, s.border_size, s.border_style, "#00000000", sr)
        elif parse_color(s.fill_color)[3] == 255:
            _ellipse(cr, "#00000000", 0, s.border_style, s.shadow_color, sr)
    if s.is_tail_active and s.tail_visible:
        inner = r.inflate(s.border_size // 2)
        if s.shadow:
            ox, oy = s.shadow_offset
            cr.save()
            cr.translate(0.5, 0.5)
            _poly(cr, tail_polygon(inner.offset(ox, oy), (s.tail_position[0] + ox, s.tail_position[1] + oy),
                                   s.TAIL_WIDTH_MULTIPLIER))
            set_source(cr, s.shadow_color)
            cr.fill()
            cr.restore()
        cr.save()
        cr.translate(0.5, 0.5)
        _poly(cr, tail_polygon(inner, s.tail_position, s.TAIL_WIDTH_MULTIPLIER))
        set_source(cr, s.border_color if s.is_border_visible else s.fill_color)
        cr.fill()
        cr.restore()
    _ellipse(cr, s.border_color, s.border_size, s.border_style, s.fill_color, r)
    text = s.text

    def number(color: str, rr: S.Rect) -> None:
        layout = make_layout(cr, text, "Sans", s.font_size, True)
        _ink, logical = layout.get_pixel_extents()
        cr.move_to(rr.x + (rr.w - logical.width) / 2, rr.y + 1 + (rr.h - logical.height) / 2)
        set_source(cr, color)
        PangoCairo.show_layout(cr, layout)

    if s.shadow:
        number(s.shadow_color, r.offset(*s.shadow_offset))
    number(s.border_color, r)


def _draw_magnify_shape(cr, s: S.DrawingMagnify, ctx) -> None:
    r = s.rect
    if r.w > 0 and r.h > 0:
        magnify = max(s.magnify_strength, 100) / 100
        nw, nh = int(r.w / magnify), int(r.h / magnify)
        sx = r.x + r.w / 2 - nw / 2
        sy = r.y + r.h / 2 - nh / 2
        if nw > 0 and nh > 0:
            cr.save()
            cr.translate(0.5, 0.5)
            path_ellipse(cr, r.x, r.y, r.w, r.h)
            cr.restore()
            cr.save()
            cr.clip()
            cr.translate(r.x, r.y)
            cr.scale(r.w / nw, r.h / nh)
            cr.translate(-sx, -sy)
            cr.set_source_surface(ctx.surface(), 0, 0)
            pat = cr.get_source()
            pat.set_filter(_FILTERS.get(s.interpolation, cairo.FILTER_NEAREST))
            pat.set_extend(cairo.EXTEND_NONE)
            cr.paint()
            cr.restore()
    _draw_ellipse_with_shadow(cr, s, r)


_surface_cache: dict[int, tuple[Image.Image, cairo.ImageSurface]] = {}


def image_surface(img: Image.Image) -> cairo.ImageSurface:
    """Cairo surface for a (never mutated) PIL image, cached by identity."""
    hit = _surface_cache.get(id(img))
    if hit is not None and hit[0] is img:
        return hit[1]
    surf = pil_to_surface(img)
    if len(_surface_cache) > 64:
        _surface_cache.clear()
    _surface_cache[id(img)] = (img, surf)
    return surf


def _draw_image_shape(cr, s: S.DrawingImage, ctx) -> None:
    if s.image is None:
        return
    r = s.rect
    iw, ih = s.image.size
    if r.w <= 0 or r.h <= 0 or iw <= 0 or ih <= 0:
        return
    cr.save()
    cr.translate(r.x, r.y)
    cr.scale(r.w / iw, r.h / ih)
    cr.set_source_surface(image_surface(s.image), 0, 0)
    pat = cr.get_source()
    pat.set_filter(_FILTERS.get(s.interpolation, cairo.FILTER_NEAREST))
    pat.set_extend(cairo.EXTEND_PAD if (r.w, r.h) != (iw, ih) else cairo.EXTEND_NONE)
    cr.rectangle(0, 0, iw, ih)
    cr.fill()
    cr.restore()


def _draw_smart_eraser_shape(cr, s: S.DrawingSmartEraser, ctx) -> None:
    color = s.eraser_dimmed_color if (not ctx.is_output and ctx.dimmed and s.eraser_dimmed_color) else s.eraser_color
    r = s.rect
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.set_operator(cairo.OPERATOR_SOURCE if parse_color(color)[3] < 255 else cairo.OPERATOR_OVER)
    set_source(cr, color)
    cr.fill()


def _draw_effect_shape(cr, s: S.EffectShape, ctx) -> None:
    r = s.rect
    if ctx.is_output:
        return
    if s.uid in ctx.effect_overlay_uids:
        draw_effect_overlay(cr, r, s.overlay_text)
        return
    inside = r.intersect(ctx.canvas_rect)
    if inside.is_empty:
        return
    surf = effect_preview_surface(s, ctx)
    if surf is None:
        return
    x, y, _w, _h = _crop_box(inside, ctx.canvas.size)
    cr.set_source_surface(surf, x, y)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()


def draw_effect_overlay(cr, r: S.Rect, text: str) -> None:
    """ShareX OnDrawOverlay: dark box, white corner lines, centred caption."""
    cr.save()
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.set_source_rgba(0, 0, 0, 150 / 255)
    cr.fill()
    cr.translate(0.5, 0.5)
    corner_lines(cr, r.inflate(1), 25)
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(1)
    cr.stroke()
    cr.restore()
    w, h = measure_text(text, "Sans", 12)
    if r.w > w and r.h > h:
        layout = make_layout(cr, text, "Sans", 12)
        cr.move_to(r.x + (r.w - w) / 2, r.y + (r.h - h) / 2)
        cr.set_source_rgb(1, 1, 1)
        PangoCairo.show_layout(cr, layout)


def corner_lines(cr, r: S.Rect, size: float) -> None:
    """Path of GraphicsExtensions.DrawCornerLines."""
    x1, y1, x2, y2 = r.x, r.y, r.right - 1, r.bottom - 1
    if r.w <= size * 2:
        cr.move_to(x1, y1)
        cr.line_to(x2, y1)
        cr.move_to(x1, y2)
        cr.line_to(x2, y2)
    else:
        for (ax, ay, bx) in ((x1, y1, x1 + size), (x2, y1, x2 - size), (x1, y2, x1 + size), (x2, y2, x2 - size)):
            cr.move_to(ax, ay)
            cr.line_to(bx, ay)
    if r.h <= size * 2:
        cr.move_to(x1, y1)
        cr.line_to(x1, y2)
        cr.move_to(x2, y1)
        cr.line_to(x2, y2)
    else:
        for (ax, ay, by) in ((x1, y1, y1 + size), (x1, y2, y2 - size), (x2, y1, y1 + size), (x2, y2, y2 - size)):
            cr.move_to(ax, ay)
            cr.line_to(ax, by)


_DRAWERS = {
    S.DrawingSpeechBalloon: _draw_speech_balloon_shape,
    S.DrawingTextOutline: _draw_text_outline_shape,
    S.DrawingTextBackground: _draw_text_background_shape,
    S.DrawingRectangle: _draw_rectangle_shape,
    S.DrawingStep: _draw_step_shape,
    S.DrawingMagnify: _draw_magnify_shape,
    S.DrawingEllipse: _draw_ellipse_shape,
    S.DrawingArrow: _draw_line_shape,
    S.DrawingLine: _draw_line_shape,
    S.DrawingFreehandArrow: _draw_freehand_shape,
    S.DrawingFreehand: _draw_freehand_shape,
    S.DrawingCursor: _draw_image_shape,
    S.DrawingSticker: _draw_image_shape,
    S.DrawingImageScreen: _draw_image_shape,
    S.DrawingImage: _draw_image_shape,
    S.DrawingSmartEraser: _draw_smart_eraser_shape,
    S.EffectBlur: _draw_effect_shape,
    S.EffectPixelate: _draw_effect_shape,
    S.EffectHighlight: _draw_effect_shape,
}

# ---- effects (ShareX ImageHelpers ports) --------------------------------------------------


def _box_blur_axis(a: np.ndarray, rng: int, axis: int) -> np.ndarray:
    """Running average over ``rng`` pixels centred on each pixel, counting only in-bounds pixels
    (ShareX's accumulator BoxBlurHorizontal/Vertical)."""
    half = rng // 2
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (1, 0)
    cs = np.cumsum(np.pad(a, pad), axis=axis)
    idx = np.arange(n)
    hi = np.minimum(idx + half, n - 1) + 1
    lo = np.maximum(idx - half, 0)
    count = (hi - lo).astype(np.float64)
    upper = np.take(cs, hi, axis=axis)
    lower = np.take(cs, lo, axis=axis)
    shape = [1] * a.ndim
    shape[axis] = n
    return (upper - lower) / count.reshape(shape)


def box_blur(img: Image.Image, blur_range: int) -> Image.Image:
    """ImageHelpers.BoxBlur: odd range, horizontal+vertical box passes applied twice."""
    if blur_range <= 1:
        return img.copy()
    if blur_range % 2 == 0:
        blur_range += 1
    a = np.asarray(img.convert("RGBA"), dtype=np.float64)
    for _ in range(2):
        a = np.floor(_box_blur_axis(a, blur_range, 1))
        a = np.floor(_box_blur_axis(a, blur_range, 0))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


def pixelate(img: Image.Image, pixel_size: int) -> Image.Image:
    """ImageHelpers.Pixelate: alpha-weighted block average, blocks anchored at the top-left."""
    if pixel_size <= 1:
        return img.copy()
    a = np.asarray(img.convert("RGBA"), dtype=np.float64)
    h, w = a.shape[:2]
    out = np.empty_like(a)
    for y in range(0, h, pixel_size):
        for x in range(0, w, pixel_size):
            blk = a[y:y + pixel_size, x:x + pixel_size]
            wgt = blk[..., 3] / 255.0
            wsum = wgt.sum()
            count = blk.shape[0] * blk.shape[1]
            rgb = (blk[..., :3] * wgt[..., None]).sum(axis=(0, 1)) / wsum if wsum > 0 else np.zeros(3)
            alpha = (blk[..., 3] * wgt).sum() / count
            out[y:y + pixel_size, x:x + pixel_size, :3] = np.floor(rgb)
            out[y:y + pixel_size, x:x + pixel_size, 3] = np.floor(alpha)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def highlight(img: Image.Image, color: str) -> Image.Image:
    """ImageHelpers.HighlightImage: each channel = min(channel, highlight channel)."""
    r, g, b, _a = parse_color(color)
    a = np.array(img.convert("RGBA"), dtype=np.uint8)
    a[..., 0] = np.minimum(a[..., 0], r)
    a[..., 1] = np.minimum(a[..., 1], g)
    a[..., 2] = np.minimum(a[..., 2], b)
    return Image.fromarray(a, "RGBA")


def apply_effect(shape: S.EffectShape, img: Image.Image) -> Image.Image:
    if isinstance(shape, S.EffectBlur):
        return box_blur(img, shape.blur_radius)
    if isinstance(shape, S.EffectPixelate):
        return pixelate(img, shape.pixel_size)
    if isinstance(shape, S.EffectHighlight):
        return highlight(img, shape.highlight_color)
    return img.copy()


def _crop_box(r: S.Rect, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """``Rectangle.Intersect(bounds, Rectangle.Round())`` as (x, y, w, h)."""
    x, y, w, h = r.rounded()
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(size[0], x + w), min(size[1], y + h)
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def effect_preview_surface(s: S.EffectShape, ctx: RenderContext) -> cairo.ImageSurface | None:
    x, y, w, h = _crop_box(s.rect.intersect(ctx.canvas_rect), ctx.canvas.size)
    if w <= 0 or h <= 0:
        return None
    key = (s.uid, type(s).__name__, x, y, w, h, s.effect_key(), ctx.canvas_version, id(ctx.canvas))
    cache = ctx.effect_cache
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    result = apply_effect(s, ctx.canvas.crop((x, y, x + w, y + h)))
    surf = pil_to_surface(result)
    if cache is not None:
        cache.computed += 1
        cache.put(key, surf)
    return surf


# ---- final output -----------------------------------------------------------------------


def render_output(base: Image.Image, shapes: Sequence[S.Shape], offset: tuple[float, float] = (0, 0),
                  source: Image.Image | None = None, canvas_version: int = 0) -> Image.Image:
    """ShareX RenderOutputImage: effects (z-order, cumulative) then drawings (z-order).

    ``base`` is the image to draw on; ``offset`` is where ``base``'s origin lies in canvas
    coordinates (region capture crops the canvas first); ``source`` is the full canvas that the
    magnify shape reads from (defaults to ``base``).
    """
    out = base.convert("RGBA") if base.mode != "RGBA" else base.copy()
    ox, oy = offset
    effects = [s for s in shapes if isinstance(s, S.EffectShape)]
    drawings = [s for s in shapes if isinstance(s, S.DrawingShape)]
    for s in effects:
        if isinstance(s, S.EffectBlur) and s.blur_radius <= 1:
            continue
        if isinstance(s, S.EffectPixelate) and s.pixel_size <= 1:
            continue
        x, y, w, h = _crop_box(s.rect.offset(-ox, -oy), out.size)
        if w <= 0 or h <= 0:
            continue
        part = apply_effect(s, out.crop((x, y, x + w, y + h)))
        out.paste(part, (x, y))
    if not drawings:
        return out
    surf = pil_to_surface(out)
    cr = cairo.Context(surf)
    cr.translate(-ox, -oy)
    ctx = RenderContext(canvas=source if source is not None else base, is_output=True, canvas_version=canvas_version)
    for s in drawings:
        draw_shape(cr, s, ctx)
    del cr
    return surface_to_pil(surf)


# ---- cut out (ImageHelpers.CutOutBitmapMiddle and edge effects) ---------------------------

LEFT, TOP, RIGHT, BOTTOM = 1, 2, 4, 8
_SUPERSAMPLE = 4


def _polygon_fill(img: Image.Image, points: list[tuple[float, float]], background: str) -> Image.Image:
    """Fill ``points`` with ``img`` as texture over a ``background`` canvas (anti-aliased)."""
    w, h = img.size
    mask = Image.new("L", (w * _SUPERSAMPLE, h * _SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).polygon([(x * _SUPERSAMPLE, y * _SUPERSAMPLE) for x, y in points], fill=255)
    mask = mask.resize((w, h), Image.Resampling.BOX)
    r, g, b, a = parse_color(background)
    result = Image.new("RGBA", (w, h), (r, g, b, a) if a > 0 else (0, 0, 0, 0))
    src = img.convert("RGBA")
    src_alpha = np.asarray(src.getchannel("A"), dtype=np.uint16)
    m = np.asarray(mask, dtype=np.uint16)
    layer = src.copy()
    layer.putalpha(Image.fromarray(((src_alpha * m + 127) // 255).astype(np.uint8), "L"))
    return Image.alpha_composite(result, layer)


def torn_edges(img: Image.Image, depth: int, rng: int, sides: int, curved: bool = False, rand: bool = True,
               background: str = "#00000000", rnd: random.Random | None = None) -> Image.Image:
    """ImageHelpers.TornEdges (``rand=False`` gives the regular sawtooth used by ZigZag)."""
    if depth < 1 or rng < 1 or not sides:
        return img
    rnd = rnd or random.Random()
    w, h = img.size
    hcount, vcount = w // rng, h // rng
    if hcount < 2 and vcount < 2:
        return img
    pts: list[tuple[int, int]] = []

    def jag(v: int) -> int:
        return rnd.randrange(0, depth) if rand else ((v // rng) & 1) * depth

    if sides & TOP and hcount > 1:
        sx = depth if (sides & LEFT and vcount > 1) else 0
        ex = w - depth if (sides & RIGHT and vcount > 1) else w
        pts += [(x, jag(x)) for x in range(sx, ex, rng)]
    else:
        pts += [(0, 0), (w, 0)]
    if sides & RIGHT and vcount > 1:
        sy = depth if (sides & TOP and hcount > 1) else 0
        ey = h - depth if (sides & BOTTOM and hcount > 1) else h
        pts += [(w - depth + jag(y), y) for y in range(sy, ey, rng)]
    else:
        pts += [(w, 0), (w, h)]
    if sides & BOTTOM and hcount > 1:
        sx = w - depth if (sides & RIGHT and vcount > 1) else w
        ex = depth if (sides & LEFT and vcount > 1) else 0
        x = sx
        while x >= ex:
            pts.append((x, h - depth + jag(x)))
            x = (x // rng - 1) * rng
    else:
        pts += [(w, h), (0, h)]
    if sides & LEFT and vcount > 1:
        sy = h - depth if (sides & BOTTOM and hcount > 1) else h
        ey = depth if (sides & TOP and hcount > 1) else 0
        y = sy
        while y >= ey:
            pts.append((jag(y), y))
            y = (y // rng - 1) * rng
    else:
        pts += [(0, h), (0, 0)]
    unique: list[tuple[int, int]] = []
    for p in pts:
        if p not in unique:
            unique.append(p)
    if curved and len(unique) >= 3:
        flat = cardinal_spline_points([*unique, unique[0]])
        return _polygon_fill(img, flat, background)
    return _polygon_fill(img, [(float(x), float(y)) for x, y in unique], background)


def wavy_edges(img: Image.Image, depth: int, rng: int, sides: int, background: str = "#00000000") -> Image.Image:
    """ImageHelpers.WavyEdges."""
    if depth < 1 or rng < 1 or not sides:
        return img
    w, h = img.size
    hwc = max(2, (w // rng + 1) // 2 * 2) - 1
    vwc = max(2, (h // rng + 1) // 2 * 2) - 1
    hwr = max(1, w // hwc)
    vwr = max(1, h // vwc)
    step = min(max(1, rng // depth), 10)

    def wave(t: int, mx: int) -> int:
        return int((1 - math.cos(t * math.pi / mx)) * depth / 2)

    pts: list[list[int]] = []
    if sides & TOP:
        sx = depth if sides & LEFT else 0
        ex = w - depth if sides & RIGHT else w
        pts += [[x, wave(x, hwr)] for x in range(sx, ex, step)]
        pts.append([ex, wave(ex, hwr)])
    else:
        pts.append([0, 0])
    if sides & RIGHT:
        sy = depth if sides & TOP else 0
        ey = h - depth if sides & BOTTOM else h
        pts += [[w - depth + wave(y, vwr), y] for y in range(sy, ey, step)]
        pts.append([w - depth + wave(ey, vwr), ey])
    else:
        pts.append([w, pts[-1][1]])
    if sides & BOTTOM:
        sx = w - depth if sides & RIGHT else w
        ex = depth if sides & LEFT else 0
        pts += [[x, h - depth + wave(x, hwr)] for x in range(sx, ex - 1, -step)]
        pts.append([ex, h - depth + wave(ex, hwr)])
    else:
        pts.append([pts[-1][0], h])
    if sides & LEFT:
        sy = h - depth if sides & BOTTOM else h
        ey = depth if sides & TOP else 0
        pts += [[wave(y, vwr), y] for y in range(sy, ey - 1, -step)]
        pts.append([wave(ey, vwr), ey])
    else:
        pts.append([0, pts[-1][1]])
    if not sides & TOP:
        pts[0] = [pts[-1][0], 0]
    return _polygon_fill(img, [(float(x), float(y)) for x, y in pts], background)


def apply_cut_out_effect(img: Image.Image, edge: int, effect: CutOutEffectType, size: int,
                         background: str) -> Image.Image:
    if effect == CutOutEffectType.ZigZag:
        return torn_edges(img, size, size, edge, False, False, background)
    if effect == CutOutEffectType.TornEdge:
        return torn_edges(img, size, size * 2, edge, False, True, background)
    if effect == CutOutEffectType.Wave:
        return wavy_edges(img, size, size * 5, edge, background)
    return img


def combine_images(images: list[Image.Image], horizontal: bool) -> Image.Image:
    """ImageHelpers.CombineImages (no spacing, top/left aligned, transparent background)."""
    if horizontal:
        w = sum(i.width for i in images)
        h = max(i.height for i in images)
    else:
        w = max(i.width for i in images)
        h = sum(i.height for i in images)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pos = 0
    for i in images:
        out.paste(i.convert("RGBA"), (pos, 0) if horizontal else (0, pos))
        pos += i.width if horizontal else i.height
    return out


def cut_out_middle(img: Image.Image, horizontal: bool, start: int, size: int,
                   effect: CutOutEffectType = CutOutEffectType["None"], effect_size: int = 10,
                   background: str = "#00000000") -> Image.Image:
    """ImageHelpers.CutOutBitmapMiddle: remove ``size`` columns (horizontal) or rows (vertical)
    starting at ``start`` and join the two remaining parts, applying the edge effect to the cut
    edges."""
    if size <= 0:
        return img
    w, h = img.size
    first = second = None
    if start > 0:
        box = (0, 0, min(start, w), h) if horizontal else (0, 0, w, min(start, h))
        first = apply_cut_out_effect(img.crop(box), RIGHT if horizontal else BOTTOM, effect, effect_size, background)
    dim = w if horizontal else h
    if start + size < dim:
        end = max(start + size, 0)
        box = (end, 0, w, h) if horizontal else (0, end, w, h)
        second = apply_cut_out_effect(img.crop(box), LEFT if horizontal else TOP, effect, effect_size, background)
    if first is not None and second is not None:
        return combine_images([first, second], horizontal)
    if first is not None:
        return first.convert("RGBA")
    if second is not None:
        return second.convert("RGBA")
    return img


def find_auto_crop_rect(img: Image.Image) -> tuple[int, int, int, int]:
    """ImageHelpers.FindAutoCropRectangle: trim uniform borders. Left/top compare against the
    top-left pixel, right/bottom against the bottom-right pixel; a transparent reference pixel
    matches any fully transparent pixel."""
    a = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    h, w = a.shape[:2]

    def differs(ref: np.ndarray) -> np.ndarray:
        if ref[3] == 0:
            return a[..., 3] != 0
        return np.any(a != ref, axis=2)

    d1 = differs(a[0, 0])
    cols = np.nonzero(d1.any(axis=0))[0]
    if cols.size == 0:
        return (0, 0, w, h)
    x = int(cols[0])
    rows = np.nonzero(d1.any(axis=1))[0]
    y = int(rows[0])
    d2 = differs(a[h - 1, w - 1])
    cols2 = np.nonzero(d2.any(axis=0))[0]
    rows2 = np.nonzero(d2.any(axis=1))[0]
    right = int(cols2[-1]) if cols2.size else w - 1
    bottom = int(rows2[-1]) if rows2.size else h - 1
    return (x, y, max(0, right - x + 1), max(0, bottom - y + 1))


# ---- misc bitmaps -----------------------------------------------------------------------

ARROW_CURSOR_POINTS = [(0, 0), (0, 16), (4, 12), (7, 18), (9, 17), (6, 11), (11, 11)]
#: Hotspot of :func:`default_cursor_image` (the arrow tip, 1 px in from the outline).
DEFAULT_CURSOR_HOTSPOT = (1, 1)


def default_cursor_image() -> Image.Image:
    """A standard arrow pointer (white with a black outline), hotspot at (0, 0)."""
    surf = new_surface(14, 21)
    cr = cairo.Context(surf)
    cr.translate(1, 1)
    cr.move_to(*ARROW_CURSOR_POINTS[0])
    for p in ARROW_CURSOR_POINTS[1:]:
        cr.line_to(*p)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgb(0, 0, 0)
    cr.set_line_width(1.2)
    cr.set_line_join(cairo.LINE_JOIN_MITER)
    cr.stroke()
    del cr
    return surface_to_pil(surf)


def checker_pattern(cell: int, c1: RGBA, c2: RGBA) -> cairo.SurfacePattern:
    surf = new_surface(cell * 2, cell * 2)
    cr = cairo.Context(surf)
    for (x, y, c) in ((0, 0, c1), (cell, 0, c2), (0, cell, c2), (cell, cell, c1)):
        cr.rectangle(x, y, cell, cell)
        cr.set_source_rgba(c[0] / 255, c[1] / 255, c[2] / 255, c[3] / 255)
        cr.fill()
    pat = cairo.SurfacePattern(surf)
    pat.set_extend(cairo.EXTEND_REPEAT)
    pat.set_filter(cairo.FILTER_NEAREST)
    return pat
