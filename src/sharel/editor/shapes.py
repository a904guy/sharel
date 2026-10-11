"""Shape model of the region capture / annotation editor (ShareX.ScreenCaptureLib/Shapes/**).

Pure state and geometry: no cairo, no reference back to the manager (ShareX nulls
``BaseShape.Manager`` before copying a shape for the same reason). Rendering lives in
:mod:`sharel.editor.render`, input handling in :mod:`sharel.editor.manager`.

Coordinates are image (canvas) pixels as floats. Like ShareX, a shape keeps a start and an end
position and its rectangle is *inclusive*: dragging from (10, 10) to (50, 40) gives
``Rect(10, 10, 41, 31)`` (``CaptureHelpers.CreateRectangle``).

Shape properties are loaded from / saved to :class:`sharel.core.config.AnnotationOptions` by
``load_config`` / ``save_config`` exactly where ShareX's ``OnConfigLoad`` / ``OnConfigSave`` do,
so the toolbar edits the options and the selected shape picks them up.
"""
from __future__ import annotations

import copy
import itertools
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

from ..core.config import AnnotationOptions, TextDrawingOptions
from ..core.enums import ArrowHeadDirection, BorderStyle, ImageInterpolationMode, ShapeType, StepType
from ..core.enums import StrEnum as _StrEnum

Point = tuple[float, float]

# A text measuring function: (text, font family, size in pt, bold, italic, underline) -> (w, h) px.
MeasureText = Callable[[str, str, float, bool, bool, bool], tuple[float, float]]


class ShapeCategory(_StrEnum):
    Region = "Region"
    Drawing = "Drawing"
    Effect = "Effect"
    Tool = "Tool"


class NodePosition:
    """ShareX's NodePosition: index into the manager's 9 resize nodes."""
    TopLeft, Top, TopRight, Right, BottomRight, Bottom, BottomLeft, Left, Extra = range(9)


# ---- geometry helpers -------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    """System.Drawing.RectangleF semantics (``contains`` is half-open)."""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> Point:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def contains_rect(self, r: Rect) -> bool:
        return self.x <= r.x and r.right <= self.right and self.y <= r.y and r.bottom <= self.bottom

    def intersects(self, r: Rect) -> bool:
        return r.x < self.right and self.x < r.right and r.y < self.bottom and self.y < r.bottom

    def intersect(self, r: Rect) -> Rect:
        x1, y1 = max(self.x, r.x), max(self.y, r.y)
        x2, y2 = min(self.right, r.right), min(self.bottom, r.bottom)
        if x2 < x1 or y2 < y1:
            return Rect()
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def union(self, r: Rect) -> Rect:
        if self.is_empty:
            return r
        if r.is_empty:
            return self
        x1, y1 = min(self.x, r.x), min(self.y, r.y)
        return Rect(x1, y1, max(self.right, r.right) - x1, max(self.bottom, r.bottom) - y1)

    def offset(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def inflate(self, d: float) -> Rect:
        return Rect(self.x - d, self.y - d, self.w + 2 * d, self.h + 2 * d)

    def rounded(self) -> tuple[int, int, int, int]:
        """``Rectangle.Round``: each component rounded independently (away from zero at .5)."""
        return (_round(self.x), _round(self.y), _round(self.w), _round(self.h))

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


def _round(v: float) -> int:
    return math.floor(v + 0.5) if v >= 0 else -math.floor(-v + 0.5)


def create_rect(p1: Point, p2: Point) -> Rect:
    """``CaptureHelpers.CreateRectangle``: inclusive of both corner pixels."""
    x, y = p1
    x2, y2 = p2
    if x <= x2:
        w = x2 - x + 1
    else:
        w = x - x2 + 1
        x = x2
    if y <= y2:
        h = y2 - y + 1
    else:
        h = y - y2 + 1
        y = y2
    return Rect(x, y, w, h)


def points_rect(points: Iterable[Point]) -> Rect:
    """``Points.CreateRectangle``: inclusive bounding rectangle of points."""
    pts = list(points)
    if not pts:
        return Rect()
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return create_rect((min(xs), min(ys)), (max(xs), max(ys)))


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def look_at_degree(a: Point, b: Point) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def snap_position_to_degree(pos: Point, pos2: Point, degree: float, start_degree: float) -> Point:
    """``CaptureHelpers.SnapPositionToDegree`` (Shift while drawing)."""
    angle = math.atan2(pos2[1] - pos[1], pos2[0] - pos[0])
    start = math.radians(start_degree)
    snap = math.radians(degree)
    new_angle = round((angle + start) / snap) * snap - start
    d = distance(pos, pos2)
    return (round(pos[0] + math.cos(new_angle) * d), round(pos[1] + math.sin(new_angle) * d))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ---- step labels (Helpers.NumberToLetters / NumberToRomanNumeral) -------------------------


def number_to_letters(num: int) -> str:
    result = ""
    while True:
        num -= 1
        if num < 0:
            break
        result = chr(ord("A") + num % 26) + result
        num //= 26
    return result


_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def number_to_roman(num: int) -> str:
    out = []
    for value, numeral in _ROMAN:
        if num >= value:
            out.append(numeral * (num // value))
            num %= value
    return "".join(out)


def step_text(number: int, step_type: StepType) -> str:
    if step_type == StepType.LettersUppercase:
        return number_to_letters(number)
    if step_type == StepType.LettersLowercase:
        return number_to_letters(number).lower()
    if step_type == StepType.RomanNumeralsUppercase:
        return number_to_roman(number)
    if step_type == StepType.RomanNumeralsLowercase:
        return number_to_roman(number).lower()
    return str(number)


# ---- base shape -------------------------------------------------------------------------

_uid = itertools.count(1)


class Shape:
    """ShareX's ``BaseShape``."""

    shape_type: ClassVar[ShapeType]
    category: ClassVar[ShapeCategory]
    #: Rectangle is clamped to the canvas (crop / cut out tools).
    limit_to_canvas: ClassVar[bool] = False
    #: Magnify keeps a 1:1 aspect while drawing.
    force_proportional: ClassVar[bool] = False
    #: Shape is created by a click (positioned, then moved while the button is held), not dragged.
    click_to_create: ClassVar[bool] = False
    #: ShowNodes() is a no-op (freehand, sticker, cursor, cut out).
    shows_nodes: ClassVar[bool] = True

    def __init__(self) -> None:
        self.uid = next(_uid)
        self._start: Point = (0.0, 0.0)
        self._end: Point = (0.0, 0.0)
        self.initial_size: tuple[float, float] = (0.0, 0.0)

    # -- position / rectangle (start/end are inclusive corner pixels) --

    @property
    def start(self) -> Point:
        return self._start

    @start.setter
    def start(self, p: Point) -> None:
        self._start = (float(p[0]), float(p[1]))

    @property
    def end(self) -> Point:
        return self._end

    @end.setter
    def end(self, p: Point) -> None:
        self._end = (float(p[0]), float(p[1]))

    @property
    def rect(self) -> Rect:
        return create_rect(self._start, self._end)

    @rect.setter
    def rect(self, r: Rect) -> None:
        self._start = (float(r.x), float(r.y))
        self._end = (float(r.x + r.w - 1), float(r.y + r.h - 1))

    @property
    def is_handled_by_select_tool(self) -> bool:
        return self.category in (ShapeCategory.Drawing, ShapeCategory.Effect)

    def is_valid(self, minimum_size: float) -> bool:
        r = self.rect
        return not r.is_empty and r.w >= minimum_size and r.h >= minimum_size

    def is_selectable(self, current_tool: ShapeType) -> bool:
        return current_tool == self.shape_type or current_tool == ShapeType.ToolSelect

    def intersects(self, x: float, y: float) -> bool:
        return self.rect.contains(x, y)

    # -- creation (OnCreating / OnUpdate while creating) --

    def begin_create(self, pos: Point) -> None:
        self.rect = Rect(pos[0], pos[1], 1, 1)

    def update_create(self, pos: Point, *, proportional: bool, corner_move: Point | None = None) -> None:
        """Pointer moved while creating: ``corner_move`` is the pointer delta when Ctrl is held
        (ShareX "IsCornerMoving": the start corner follows the pointer)."""
        if corner_move is not None:
            self.start = (self._start[0] + corner_move[0], self._start[1] + corner_move[1])
        if proportional or self.force_proportional:
            if self.shape_type in (ShapeType.DrawingLine, ShapeType.DrawingArrow):
                pos = snap_position_to_degree(self._start, pos, 45, 0)
            else:
                pos = snap_position_to_degree(self._start, pos, 90, 45)
        self.end = pos

    def on_created(self) -> None:
        r = self.rect
        self.initial_size = (r.w, r.h)

    # -- move / resize --

    def move(self, dx: float, dy: float) -> None:
        self._start = (self._start[0] + dx, self._start[1] + dy)
        self._end = (self._end[0] + dx, self._end[1] + dy)

    def move_absolute(self, x: float, y: float, center: bool = False) -> None:
        r = self.rect
        if center:
            x -= r.w / 2
            y -= r.h / 2
        self.move(x - r.x, y - r.y)

    def resize(self, dx: float, dy: float, from_bottom_right: bool) -> None:
        """Keyboard resize (Ctrl+arrows from bottom-right, Alt+arrows from top-left)."""
        r = self.rect
        if from_bottom_right:
            self.rect = Rect(r.x, r.y, r.w + dx, r.h + dy)
        else:
            self.rect = Rect(r.x + dx, r.y + dy, r.w - dx, r.h - dy)

    # -- resize nodes --

    def node_positions(self) -> list[Point | None]:
        """Positions of the 9 nodes (None = hidden), ShareX's OnNodePositionUpdate."""
        r = self.rect
        xs, xm, xe = r.x, r.x + r.w / 2, r.x + r.w - 1
        ys, ym, ye = r.y, r.y + r.h / 2, r.y + r.h - 1
        return [(xs, ys), (xm, ys), (xe, ys), (xe, ym), (xe, ye), (xm, ye), (xs, ye), (xs, ym), None]

    def begin_node_drag(self, index: int) -> dict:
        """Remember the state at the start of a node drag (ShareX's temp* fields)."""
        r = self.rect
        nodes = self.node_positions()
        return {"node": nodes[index], "start": (r.x, r.y), "end": (r.x + r.w - 1, r.y + r.h - 1), "rect": r}

    def drag_node(self, index: int, pos: Point, state: dict, proportional: bool) -> None:
        """ShareX's BaseShape.OnNodeUpdate for the 8 rectangle nodes."""
        if index >= 8:
            return
        node = state["node"]
        sx, sy = state["start"]
        ex, ey = state["end"]
        dx = pos[0] - node[0]
        dy = pos[1] - node[1]
        if index in (NodePosition.TopLeft, NodePosition.Left, NodePosition.BottomLeft):
            sx += dx
        elif index in (NodePosition.TopRight, NodePosition.Right, NodePosition.BottomRight):
            ex += dx
        if index in (NodePosition.TopLeft, NodePosition.Top, NodePosition.TopRight):
            sy += dy
        elif index in (NodePosition.BottomLeft, NodePosition.Bottom, NodePosition.BottomRight):
            ey += dy
        self._start = (sx, sy)
        self._end = (ex, ey)
        iw, ih = self.initial_size
        if proportional and iw > 0 and ih > 0 and index in (NodePosition.TopLeft, NodePosition.TopRight,
                                                              NodePosition.BottomRight, NodePosition.BottomLeft):
            r = self.rect
            ratio = min(r.w / iw, r.h / ih)
            nw, nh = round(iw * ratio), round(ih * ratio)
            tr: Rect = state["rect"]
            ax = tr.right - 1 if index in (NodePosition.TopLeft, NodePosition.BottomLeft) else tr.x
            ay = tr.bottom - 1 if index in (NodePosition.TopLeft, NodePosition.TopRight) else tr.y
            nx = r.right - nw if pos[0] < ax else r.x
            ny = r.bottom - nh if pos[1] < ay else r.y
            self.rect = Rect(nx, ny, nw, nh)

    # -- copies --

    def clone(self) -> Shape:
        """Snapshot copy for undo: lists and option objects are copied, images are shared
        (images are never mutated in place)."""
        c = copy.copy(self)
        for k, v in list(vars(c).items()):
            if isinstance(v, list):
                setattr(c, k, list(v))
            elif isinstance(v, TextDrawingOptions):
                setattr(c, k, copy.copy(v))
        return c

    def duplicate(self) -> Shape:
        """A new independent shape (new uid), ShareX's Duplicate."""
        c = self.clone()
        c.uid = next(_uid)
        return c

    # -- config --

    def load_config(self, opts: AnnotationOptions) -> None:
        pass

    def save_config(self, opts: AnnotationOptions) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} #{self.uid} {self.rect.as_tuple()}>"


# ---- regions ----------------------------------------------------------------------------


class RegionShape(Shape):
    category = ShapeCategory.Region


class RegionRectangle(RegionShape):
    shape_type = ShapeType.RegionRectangle

    def __init__(self) -> None:
        super().__init__()
        self.corner_radius = 0

    def load_config(self, opts):
        self.corner_radius = opts.region_corner_radius

    def save_config(self, opts):
        opts.region_corner_radius = self.corner_radius


class RegionEllipse(RegionShape):
    shape_type = ShapeType.RegionEllipse


class _PointsMixin:
    """Freehand creation shared by RegionFreehand and DrawingFreehand (Shift = polygon mode)."""

    points: list[Point]
    _polygon_mode: bool

    def _add_freehand_point(self, pos: Point, proportional: bool) -> None:
        pts = self.points
        if not pts or (not proportional and pts[-1] != pos):
            pts.append(pos)
        if proportional:
            if not self._polygon_mode:
                pts.append(pos)
            pts[-1] = pos
        self._polygon_mode = proportional

    def begin_create(self, pos: Point) -> None:
        self.points = []
        self._polygon_mode = False
        self.update_create(pos, proportional=False)

    def update_create(self, pos: Point, *, proportional: bool, corner_move: Point | None = None) -> None:
        if corner_move is not None:
            self.move(*corner_move)
            return
        self._add_freehand_point((float(pos[0]), float(pos[1])), proportional)
        Shape.rect.fset(self, points_rect(self.points))

    def move(self, dx: float, dy: float) -> None:
        self.points = [(x + dx, y + dy) for x, y in self.points]
        Shape.move(self, dx, dy)

    def resize(self, dx: float, dy: float, from_bottom_right: bool) -> None:
        self.move(dx, dy)

    def path_bounds(self) -> Rect:
        """GraphicsPath.GetBounds of the polygon (exclusive extent)."""
        if not self.points:
            return Rect()
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


class RegionFreehand(_PointsMixin, RegionShape):
    shape_type = ShapeType.RegionFreehand

    def __init__(self) -> None:
        super().__init__()
        self.points: list[Point] = []
        self._polygon_mode = False

    def node_positions(self):
        nodes: list[Point | None] = [None] * 9
        if self.points:
            nodes[NodePosition.TopLeft] = self.points[-1]
        return nodes

    def drag_node(self, index, pos, state, proportional):
        # ShareX: dragging the last-point node continues the freehand drawing.
        if index == NodePosition.TopLeft:
            self.update_create(pos, proportional=proportional)


# ---- drawings ---------------------------------------------------------------------------


class DrawingShape(Shape):
    """ShareX's BaseDrawingShape."""

    category = ShapeCategory.Drawing

    def __init__(self) -> None:
        super().__init__()
        self.border_color = "#FFF03C3C"
        self.border_size = 4
        self.border_style = BorderStyle.Solid
        self.fill_color = "#00000000"
        self.shadow = True
        self.shadow_color = "#7D000000"
        self.shadow_offset: Point = (0, 1)

    @property
    def is_border_visible(self) -> bool:
        return self.border_size > 0 and alpha_of(self.border_color) > 0

    @property
    def is_fill_visible(self) -> bool:
        return alpha_of(self.fill_color) > 0

    def _load_shadow(self, opts: AnnotationOptions) -> None:
        self.shadow = opts.shadow
        self.shadow_color = opts.shadow_color
        self.shadow_offset = (opts.shadow_offset_x, opts.shadow_offset_y)

    def _save_shadow(self, opts: AnnotationOptions) -> None:
        opts.shadow = self.shadow
        opts.shadow_color = self.shadow_color
        opts.shadow_offset_x, opts.shadow_offset_y = int(self.shadow_offset[0]), int(self.shadow_offset[1])

    def load_config(self, opts):
        self.border_color = opts.border_color
        self.border_size = opts.border_size
        self.border_style = opts.border_style
        self.fill_color = opts.fill_color
        self._load_shadow(opts)

    def save_config(self, opts):
        opts.border_color = self.border_color
        opts.border_size = self.border_size
        opts.border_style = self.border_style
        opts.fill_color = self.fill_color
        self._save_shadow(opts)


def alpha_of(color: str) -> int:
    """Alpha of a "#AARRGGBB" (or "#RRGGBB" = opaque) colour."""
    s = color.lstrip("#")
    return int(s[0:2], 16) if len(s) == 8 else 255


class DrawingRectangle(DrawingShape):
    shape_type = ShapeType.DrawingRectangle

    def __init__(self) -> None:
        super().__init__()
        self.corner_radius = 3

    def load_config(self, opts):
        super().load_config(opts)
        self.corner_radius = opts.drawing_corner_radius

    def save_config(self, opts):
        super().save_config(opts)
        opts.drawing_corner_radius = self.corner_radius


class DrawingEllipse(DrawingShape):
    shape_type = ShapeType.DrawingEllipse


class DrawingFreehand(_PointsMixin, DrawingShape):
    shape_type = ShapeType.DrawingFreehand
    shows_nodes = False

    def __init__(self) -> None:
        super().__init__()
        self.points: list[Point] = []
        self._polygon_mode = False

    def is_valid(self, minimum_size):
        return len(self.points) > 0

    def is_selectable(self, current_tool):
        return current_tool == ShapeType.ToolSelect

    def node_positions(self):
        return [None] * 9

    def drag_node(self, index, pos, state, proportional):
        return


class DrawingFreehandArrow(DrawingFreehand):
    shape_type = ShapeType.DrawingFreehandArrow

    def __init__(self) -> None:
        super().__init__()
        self.arrow_head_direction = ArrowHeadDirection.End

    def load_config(self, opts):
        super().load_config(opts)
        self.arrow_head_direction = opts.arrow_head_direction

    def save_config(self, opts):
        super().save_config(opts)
        opts.arrow_head_direction = self.arrow_head_direction


class DrawingLine(DrawingShape):
    """Straight line through optional centre points; dragging a centre node bends it into a
    cardinal spline (GDI+ DrawCurve)."""

    shape_type = ShapeType.DrawingLine
    MAXIMUM_CENTER_POINT_COUNT = 5
    MINIMUM_COLLISION_SIZE = 10

    def __init__(self) -> None:
        super().__init__()
        self.center_point_count = 0
        self.points: list[Point] = [(0.0, 0.0), (0.0, 0.0)]
        self.center_node_active = False

    def is_valid(self, minimum_size):
        r = self.rect
        return r.w >= minimum_size or r.h >= minimum_size

    def _adjust_points(self, count: int) -> None:
        first, last = self.points[0], self.points[-1]
        self.points = [first] + [(0.0, 0.0)] * count + [last]

    def auto_position_center_points(self) -> None:
        if self.center_node_active:
            return
        p0, pn = self.points[0], self.points[-1]
        n = len(self.points) - 2
        for i in range(1, n + 1):
            t = i / (n + 1)
            self.points[i] = (lerp(p0[0], pn[0], t), lerp(p0[1], pn[1], t))

    def recalc_rect(self) -> None:
        """ShareX's CalculateRectangle: points' bounds grown to at least 10 px for hit testing."""
        r = points_rect(self.points)
        m = self.MINIMUM_COLLISION_SIZE
        if r.w < m:
            r = Rect(r.x - m / 2, r.y, r.w + m, r.h)
        if r.h < m:
            r = Rect(r.x, r.y - m / 2, r.w, r.h + m)
        self.rect = r

    def load_config(self, opts):
        super().load_config(opts)
        count = max(0, min(opts.line_center_point_count, self.MAXIMUM_CENTER_POINT_COUNT))
        if count != self.center_point_count or len(self.points) != count + 2:
            self.center_point_count = count
            self._adjust_points(count)
            self.center_node_active = False
            self.auto_position_center_points()
            if self.points[0] != self.points[-1]:
                self.recalc_rect()

    def save_config(self, opts):
        super().save_config(opts)
        opts.line_center_point_count = self.center_point_count

    def begin_create(self, pos):
        super().begin_create(pos)
        self.points[0] = self.points[-1] = (float(pos[0]), float(pos[1]))
        self.auto_position_center_points()

    def update_create(self, pos, *, proportional, corner_move=None):
        super().update_create(pos, proportional=proportional, corner_move=corner_move)
        self.points[0] = self.start
        self.points[-1] = self.end
        self.auto_position_center_points()

    def on_created(self):
        self.auto_position_center_points()
        self.recalc_rect()
        super().on_created()

    def move(self, dx, dy):
        super().move(dx, dy)
        self.points = [(x + dx, y + dy) for x, y in self.points]

    def resize(self, dx, dy, from_bottom_right):
        i = -1 if from_bottom_right else 0
        x, y = self.points[i]
        self.points[i] = (x + dx, y + dy)
        self.auto_position_center_points()
        self.recalc_rect()

    def node_positions(self):
        nodes: list[Point | None] = [None] * 9
        for i, p in enumerate(self.points[:9]):
            nodes[i] = p
        return nodes

    def begin_node_drag(self, index):
        return {}

    def drag_node(self, index, pos, state, proportional):
        if index >= len(self.points):
            return
        if 0 < index < len(self.points) - 1:
            self.center_node_active = True
        self.points[index] = (float(pos[0]), float(pos[1]))
        self.auto_position_center_points()
        self.recalc_rect()

    @property
    def is_curved(self) -> bool:
        return self.center_node_active and len(self.points) > 2


class DrawingArrow(DrawingLine):
    shape_type = ShapeType.DrawingArrow

    def __init__(self) -> None:
        super().__init__()
        self.arrow_head_direction = ArrowHeadDirection.End

    def load_config(self, opts):
        super().load_config(opts)
        self.arrow_head_direction = opts.arrow_head_direction

    def save_config(self, opts):
        super().save_config(opts)
        opts.arrow_head_direction = self.arrow_head_direction


class DrawingTextBackground(DrawingRectangle):
    """ShareX's TextDrawingShape: text in a (rounded) rectangle."""

    shape_type = ShapeType.DrawingTextBackground
    click_to_create = True
    supports_gradient: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.text_options = TextDrawingOptions()

    def load_config(self, opts):
        self.text_options = copy.copy(opts.text_options)
        self.border_color = opts.text_border_color
        self.border_size = opts.text_border_size
        self.fill_color = opts.text_fill_color
        self.corner_radius = opts.drawing_corner_radius
        self._load_shadow(opts)

    def save_config(self, opts):
        opts.text_options = copy.copy(self.text_options)
        opts.text_border_color = self.border_color
        opts.text_border_size = self.border_size
        opts.text_fill_color = self.fill_color
        opts.drawing_corner_radius = self.corner_radius
        self._save_shadow(opts)

    def auto_size(self, measure: MeasureText, center: bool) -> None:
        """Size the box to the text + (15, 20) px (ShareX's AutoSize); 100x60 when empty."""
        if self.text:
            o = self.text_options
            w, h = measure(self.text, o.font, o.size, o.bold, o.italic, o.underline)
            size = (math.ceil(w) + 15, math.ceil(h) + 20)
        else:
            size = (100, 60)
        r = self.rect
        loc = (r.x - size[0] // 2, r.y - size[1] // 2) if center else (r.x, r.y)
        self.rect = Rect(loc[0], loc[1], size[0], size[1])


class DrawingTextOutline(DrawingTextBackground):
    shape_type = ShapeType.DrawingTextOutline
    supports_gradient = True

    def load_config(self, opts):
        self.text_options = copy.copy(opts.text_outline_options)
        self.border_color = opts.text_outline_border_color
        self.border_size = opts.text_outline_border_size
        self._load_shadow(opts)

    def save_config(self, opts):
        opts.text_outline_options = copy.copy(self.text_options)
        opts.text_outline_border_color = self.border_color
        opts.text_outline_border_size = self.border_size
        self._save_shadow(opts)


class DrawingSpeechBalloon(DrawingTextBackground):
    """Text box with a tail whose tip is the draggable Extra node."""

    shape_type = ShapeType.DrawingSpeechBalloon
    TAIL_WIDTH_MULTIPLIER = 0.3

    def __init__(self) -> None:
        super().__init__()
        self.tail_position: Point = (0.0, 0.0)

    @property
    def tail_visible(self) -> bool:
        return not self.rect.contains(*self.tail_position)

    def place_tail(self) -> None:
        """ShareX OnCreated: tail 30 px below the bottom-left corner."""
        r = self.rect
        self.tail_position = (r.x, r.y + r.h + 30)

    def move(self, dx, dy):
        super().move(dx, dy)
        self.tail_position = (self.tail_position[0] + dx, self.tail_position[1] + dy)

    def node_positions(self):
        nodes = super().node_positions()
        nodes[NodePosition.Extra] = self.tail_position
        return nodes

    def begin_node_drag(self, index):
        if index == NodePosition.Extra:
            return {}
        return super().begin_node_drag(index)

    def drag_node(self, index, pos, state, proportional):
        if index == NodePosition.Extra:
            self.tail_position = (float(pos[0]), float(pos[1]))
        else:
            super().drag_node(index, pos, state, proportional)


class DrawingStep(DrawingEllipse):
    """Numbered marker (numbers, letters, roman numerals); numbered in z-order by the manager."""

    shape_type = ShapeType.DrawingStep
    DEFAULT_SIZE = 30
    TAIL_WIDTH_MULTIPLIER = 1.0
    click_to_create = True

    def __init__(self) -> None:
        super().__init__()
        self.font_size = 18
        self.number = 1
        self.step_type = StepType.Numbers
        self.is_tail_active = False
        self.tail_position: Point = (0.0, 0.0)
        self.rect = Rect(0, 0, self.DEFAULT_SIZE, self.DEFAULT_SIZE)

    @property
    def text(self) -> str:
        return step_text(self.number, self.step_type)

    @property
    def tail_visible(self) -> bool:
        return not self.rect.contains(*self.tail_position)

    def load_config(self, opts):
        self.border_color = opts.step_border_color
        self.border_size = opts.step_border_size
        self.fill_color = opts.step_fill_color
        self._load_shadow(opts)
        self.font_size = opts.step_font_size
        self.step_type = opts.step_type

    def save_config(self, opts):
        opts.step_border_color = self.border_color
        opts.step_border_size = self.border_size
        opts.step_fill_color = self.fill_color
        self._save_shadow(opts)
        opts.step_font_size = self.font_size
        opts.step_type = self.step_type

    def begin_create(self, pos):
        r = self.rect
        self.rect = Rect(pos[0] - r.w / 2, pos[1] - r.h / 2, r.w, r.h)
        r = self.rect
        offset = 5
        self.tail_position = (r.x + r.w + offset, r.y + r.h + offset)

    def update_size(self, measure: MeasureText) -> None:
        """Square around the measured label + 3 px padding, kept centred (ShareX DrawNumber)."""
        w, h = measure(self.text, "Sans", self.font_size, True, False, False)
        max_size = int(max(w, h))
        padding = 3
        cx, cy = self.rect.center
        self.rect = Rect(cx - max_size / 2 - padding, cy - max_size / 2 - padding,
                         max_size + padding * 2, max_size + padding * 2)

    def move(self, dx, dy):
        super().move(dx, dy)
        self.tail_position = (self.tail_position[0] + dx, self.tail_position[1] + dy)

    def resize(self, dx, dy, from_bottom_right):
        self.move(dx, dy)

    def node_positions(self):
        nodes: list[Point | None] = [None] * 9
        nodes[NodePosition.Extra] = self.tail_position
        return nodes

    def begin_node_drag(self, index):
        return {}

    def drag_node(self, index, pos, state, proportional):
        if index == NodePosition.Extra:
            self.is_tail_active = True
            self.tail_position = (float(pos[0]), float(pos[1]))


class DrawingMagnify(DrawingEllipse):
    shape_type = ShapeType.DrawingMagnify
    force_proportional = True

    def __init__(self) -> None:
        super().__init__()
        self.magnify_strength = 200
        self.interpolation = ImageInterpolationMode.NearestNeighbor

    def load_config(self, opts):
        super().load_config(opts)
        self.magnify_strength = opts.magnifier_strength
        self.interpolation = opts.image_interpolation_mode

    def save_config(self, opts):
        super().save_config(opts)
        opts.magnifier_strength = self.magnify_strength
        opts.image_interpolation_mode = self.interpolation


class DrawingImage(DrawingShape):
    """An image placed on the canvas (ShareX ImageDrawingShape / ImageFileDrawingShape)."""

    shape_type = ShapeType.DrawingImage
    click_to_create = True

    def __init__(self) -> None:
        super().__init__()
        self.image: Image.Image | None = None
        self.interpolation = ImageInterpolationMode.NearestNeighbor
        self.file_path = ""

    def load_config(self, opts):
        self.interpolation = opts.image_interpolation_mode

    def save_config(self, opts):
        opts.image_interpolation_mode = self.interpolation

    def set_image(self, img: Image.Image | None, center: bool) -> None:
        self.image = img.convert("RGBA") if img is not None and img.mode != "RGBA" else img
        if self.image is None:
            return
        w, h = self.image.size
        r = self.rect
        loc = (r.x - w // 2, r.y - h // 2) if center else (r.x, r.y)
        self.rect = Rect(loc[0], loc[1], w, h)


class DrawingImageScreen(DrawingImage):
    """Drag a rectangle; on release it becomes an image of that area (taken from the screen /
    canvas through the manager)."""

    shape_type = ShapeType.DrawingImageScreen
    click_to_create = False


class DrawingSticker(DrawingImage):
    shape_type = ShapeType.DrawingSticker
    shows_nodes = False

    def load_config(self, opts):
        self.interpolation = ImageInterpolationMode.NearestNeighbor

    def save_config(self, opts):
        return

    def resize(self, dx, dy, from_bottom_right):
        self.move(dx, dy)


class DrawingCursor(DrawingImage):
    shape_type = ShapeType.DrawingCursor
    shows_nodes = False

    def __init__(self) -> None:
        super().__init__()
        self.hotspot: Point = (0, 0)

    def load_config(self, opts):
        return

    def save_config(self, opts):
        return

    def update_cursor(self, img: Image.Image, position: Point, hotspot: Point = (0, 0)) -> None:
        """Place the cursor bitmap so its hotspot is at ``position``."""
        self.image = img.convert("RGBA") if img.mode != "RGBA" else img
        self.hotspot = hotspot
        w, h = self.image.size
        self.rect = Rect(round(position[0] - hotspot[0]), round(position[1] - hotspot[1]), w, h)

    def resize(self, dx, dy, from_bottom_right):
        self.move(dx, dy)


class DrawingSmartEraser(DrawingShape):
    """Rectangle filled with the canvas colour sampled under the start point."""

    shape_type = ShapeType.DrawingSmartEraser

    def __init__(self) -> None:
        super().__init__()
        self.eraser_color = "#FFFFFFFF"
        self.eraser_dimmed_color = ""

    def load_config(self, opts):
        return

    def save_config(self, opts):
        return


# ---- effects ----------------------------------------------------------------------------


class EffectShape(Shape):
    category = ShapeCategory.Effect

    def effect_key(self) -> tuple:
        """Parameters that change the effect output (for caching)."""
        return ()

    @property
    def overlay_text(self) -> str:
        return ""


class EffectBlur(EffectShape):
    shape_type = ShapeType.EffectBlur

    def __init__(self) -> None:
        super().__init__()
        self.blur_radius = 35

    def load_config(self, opts):
        self.blur_radius = opts.blur_radius

    def save_config(self, opts):
        opts.blur_radius = self.blur_radius

    def effect_key(self):
        return (self.blur_radius,)

    @property
    def overlay_text(self):
        return f"Blur [{self.blur_radius}]"


class EffectPixelate(EffectShape):
    shape_type = ShapeType.EffectPixelate

    def __init__(self) -> None:
        super().__init__()
        self.pixel_size = 15

    def load_config(self, opts):
        self.pixel_size = opts.pixelate_size

    def save_config(self, opts):
        opts.pixelate_size = self.pixel_size

    def effect_key(self):
        return (self.pixel_size,)

    @property
    def overlay_text(self):
        return f"Pixelate [{self.pixel_size}]"


class EffectHighlight(EffectShape):
    shape_type = ShapeType.EffectHighlight

    def __init__(self) -> None:
        super().__init__()
        self.highlight_color = "#FFFFFF00"

    def load_config(self, opts):
        self.highlight_color = opts.highlight_color

    def save_config(self, opts):
        opts.highlight_color = self.highlight_color

    def effect_key(self):
        return (self.highlight_color,)

    @property
    def overlay_text(self):
        return "Highlight"


# ---- tools ------------------------------------------------------------------------------


class ToolShape(Shape):
    category = ShapeCategory.Tool
    limit_to_canvas = True


class ToolCrop(ToolShape):
    shape_type = ShapeType.ToolCrop


class ToolCutOut(ToolShape):
    """Removes a horizontal band (wider than tall: cut columns) or a vertical band (cut rows)."""

    shape_type = ShapeType.ToolCutOut
    shows_nodes = False

    def is_horizontal_trim(self, minimum_size: float) -> bool:
        r = self.rect
        return r.w >= minimum_size and r.w > r.h

    def is_vertical_trim(self, minimum_size: float) -> bool:
        r = self.rect
        return r.h >= minimum_size and r.h >= r.w

    def is_valid_cut(self, minimum_size: float, canvas: Rect) -> bool:
        r = self.rect
        h = self.is_horizontal_trim(minimum_size)
        v = self.is_vertical_trim(minimum_size)
        if not h and not v:
            return False
        if h and r.x <= canvas.x and r.right >= canvas.right:
            return False
        return not (v and r.y <= canvas.y and r.bottom >= canvas.bottom)

    def cut_out_rect(self, minimum_size: float, canvas: Rect) -> Rect:
        r = self.rect
        if self.is_horizontal_trim(minimum_size):
            return Rect(r.x, canvas.y, r.w, canvas.h)
        if self.is_vertical_trim(minimum_size):
            return Rect(canvas.x, r.y, canvas.w, r.h)
        return Rect()


# ---- registry ---------------------------------------------------------------------------

SHAPE_CLASSES: dict[ShapeType, type[Shape]] = {cls.shape_type: cls for cls in (
    RegionRectangle, RegionEllipse, RegionFreehand,
    DrawingRectangle, DrawingEllipse, DrawingFreehand, DrawingFreehandArrow, DrawingLine, DrawingArrow,
    DrawingTextOutline, DrawingTextBackground, DrawingSpeechBalloon, DrawingStep, DrawingMagnify,
    DrawingImage, DrawingImageScreen, DrawingSticker, DrawingCursor, DrawingSmartEraser,
    EffectBlur, EffectPixelate, EffectHighlight, ToolCrop, ToolCutOut)}

REGION_TYPES = frozenset({ShapeType.RegionRectangle, ShapeType.RegionEllipse, ShapeType.RegionFreehand})

#: ShareX's localized ShapeType descriptions (toolbar tooltips).
SHAPE_TYPE_LABELS: dict[ShapeType, str] = {
    ShapeType.RegionRectangle: "Rectangle region", ShapeType.RegionEllipse: "Ellipse region",
    ShapeType.RegionFreehand: "Freehand region", ShapeType.ToolSelect: "Select and move (M)",
    ShapeType.DrawingRectangle: "Rectangle (R)", ShapeType.DrawingEllipse: "Ellipse (E)",
    ShapeType.DrawingFreehand: "Freehand (F)", ShapeType.DrawingFreehandArrow: "Freehand arrow",
    ShapeType.DrawingLine: "Line (L)", ShapeType.DrawingArrow: "Arrow (A)",
    ShapeType.DrawingTextOutline: "Text (Outline) (O)", ShapeType.DrawingTextBackground: "Text (Background) (T)",
    ShapeType.DrawingSpeechBalloon: "Speech balloon (S)", ShapeType.DrawingStep: "Step (I)",
    ShapeType.DrawingMagnify: "Magnify", ShapeType.DrawingImage: "Image (File)",
    ShapeType.DrawingImageScreen: "Image (Screen)", ShapeType.DrawingSticker: "Sticker",
    ShapeType.DrawingCursor: "Cursor", ShapeType.DrawingSmartEraser: "Smart eraser",
    ShapeType.EffectBlur: "Blur (B)", ShapeType.EffectPixelate: "Pixelate (P)",
    ShapeType.EffectHighlight: "Highlight (H)", ShapeType.ToolCrop: "Crop image (C)",
    ShapeType.ToolCutOut: "Cut out (X)",
}


def is_region_type(t: ShapeType) -> bool:
    return t in REGION_TYPES


def create_shape(shape_type: ShapeType, opts: AnnotationOptions | None = None) -> Shape:
    """A new shape of ``shape_type`` with its properties loaded from ``opts``."""
    if shape_type == ShapeType.ToolSelect:
        raise ValueError("ToolSelect is the selection tool, it creates no shape")
    shape = SHAPE_CLASSES[shape_type]()
    shape.load_config(opts if opts is not None else AnnotationOptions())
    return shape
