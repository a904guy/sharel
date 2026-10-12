"""ShapeManager: the toolkit-independent controller of region capture and the image editor.

In ShareX the region-capture overlay *is* the annotation editor (``RegionCaptureForm`` +
``ShapeManager``); the image editor is the same thing in Editor mode. This class owns the
canvas image, the shapes, the current tool, undo history and the result, and is driven by:

* pointer events in **image coordinates**: :meth:`press`, :meth:`motion`, :meth:`release`,
  :meth:`double_click`, :meth:`scroll`; keys: :meth:`key` (GDK key names such as ``"Escape"``,
  ``"Return"``, ``"Left"``, ``"r"``, ``"KP_1"``, ``"Delete"``) with :class:`Mod` flags;
* :meth:`draw` paints everything (canvas, shapes, handles, hover rectangle, dimming, magnifier,
  crosshair, info text) onto a cairo context with the caller's view transform;
* :meth:`toolbar_model` describes ShareX's toolbar/menus with current values and setters.

Anything that needs a dialog goes through :class:`Callbacks` (text input, image file, sticker,
image effects, exit confirmation, clipboard...). A callback may answer synchronously or return
:data:`PENDING` and complete later through the matching ``set_*`` method.

When the capture finishes (quick crop, Enter, double-click, right-click cancel, colour picked...)
``result`` is set, ``callbacks.on_result`` fires and the event method returns the
:class:`CaptureResult`.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntFlag
from pathlib import Path
from typing import Any

import cairo
import numpy as np
from PIL import Image, ImageColor

from ..core import imaging
from ..core.config import AnnotationOptions, RegionCaptureOptions, Size
from ..core.enums import (
    ArrowHeadDirection,
    BorderStyle,
    CutOutEffectType,
    ImageEditorStartMode,
    ImageInsertMethod,
    ImageInterpolationMode,
    RegionCaptureAction,
    RegionCaptureMode,
    ShapeType,
    StepType,
)
from ..core.enums import StrEnum as _StrEnum
from . import render as R
from . import shapes as S
from . import stickers as ST

PENDING = object()
"""Returned by a dialog callback that will answer later through the matching ``set_*`` call."""


class Mod(IntFlag):
    NONE = 0
    SHIFT = 1
    CTRL = 2
    ALT = 4


# GDK button numbers
BUTTON_LEFT, BUTTON_MIDDLE, BUTTON_RIGHT, BUTTON_X1, BUTTON_X2 = 1, 2, 3, 8, 9


class RegionResult(_StrEnum):
    """ShareX's RegionResult."""
    Close = "Close"
    Region = "Region"
    LastRegion = "LastRegion"
    Fullscreen = "Fullscreen"
    Monitor = "Monitor"
    ActiveMonitor = "ActiveMonitor"
    AnnotateRunAfterCaptureTasks = "AnnotateRunAfterCaptureTasks"
    AnnotateContinueTask = "AnnotateContinueTask"
    AnnotateCancelTask = "AnnotateCancelTask"


@dataclass
class CaptureResult:
    result: RegionResult
    #: Selected area in image coordinates (x, y, w, h), when there is one.
    rect: tuple[int, int, int, int] | None = None
    #: "L" mask of ``rect`` for non-rectangular / multiple regions (255 = inside).
    mask: Image.Image | None = None
    #: Click position (OneClick / ScreenColorPicker), image coordinates.
    point: tuple[int, int] | None = None
    #: Picked colour "#AARRGGBB" (ScreenColorPicker).
    color: str | None = None
    #: Snap rectangle under the click (OneClick "selected window").
    window_rect: tuple[int, int, int, int] | None = None
    monitor_index: int | None = None


@dataclass
class Callbacks:
    """Hooks into the UI. Every one is optional."""
    #: (shape, initial text) -> text | None (cancelled) | PENDING (answer with set_text).
    request_text: Callable[[S.Shape, str], Any] | None = None
    #: () -> image file path | None | PENDING (answer with set_shape_image_file).
    request_image_file: Callable[[], Any] | None = None
    #: () -> sticker file path | None | PENDING (answer with set_shape_sticker).
    request_sticker: Callable[[], Any] | None = None
    #: () -> (PIL cursor image, (hotspot x, y)) used by the Cursor tool; default arrow if None.
    cursor_image: Callable[[], tuple[Image.Image, tuple[int, int]]] | None = None
    #: () -> PIL image of a screen region (Insert image from screen).
    capture_screen_image: Callable[[], Image.Image | None] | None = None
    #: (canvas) -> new image | None (Add image effects).
    apply_effects: Callable[[Image.Image], Image.Image | None] | None = None
    #: () -> bool, asked before closing a modified editor.
    confirm_exit: Callable[[], bool] | None = None
    copy_image: Callable[[Image.Image], None] | None = None
    copy_text: Callable[[str], None] | None = None
    #: () -> PIL image | str (text or file path) | None: the system clipboard (Ctrl+V).
    read_clipboard: Callable[[], Any] | None = None
    save_image: Callable[[Image.Image], None] | None = None
    save_image_as: Callable[[Image.Image], None] | None = None
    upload_image: Callable[[Image.Image], None] | None = None
    print_image: Callable[[Image.Image], None] | None = None
    #: (dx, dy) image px: arrow keys with nothing selected move the pointer (ShareX).
    move_pointer: Callable[[int, int], None] | None = None
    #: (+1 | -1): Ctrl+wheel / Ctrl+plus/minus zoom in the editor (view concern).
    zoom: Callable[[int], None] | None = None
    on_result: Callable[[CaptureResult], None] | None = None
    #: Something visible changed (redraw; re-query toolbar_model()).
    on_change: Callable[[], None] | None = None


@dataclass
class Memento:
    shapes: list[S.Shape]
    canvas: Image.Image
    canvas_version: int


@dataclass
class MagnifierData:
    """Pixels around the cursor for the magnifier (ShareX RegionCaptureForm.Magnifier)."""
    pixels: Image.Image  # count x count RGBA, out-of-canvas pixels = canvas background colour
    count: int
    pixel_size: int
    center_color: str  # "#AARRGGBB" of the pixel under the cursor

    def render(self) -> Image.Image:
        """The magnifier bitmap: zoomed pixels, translucent crosshair, grid, centre box."""
        ps, n = self.pixel_size, self.count
        w = h = n * ps
        img = self.pixels.resize((w, h), Image.Resampling.NEAREST)
        arr = np.array(img, dtype=np.float64)
        blue = np.array([173, 216, 230], dtype=np.float64)  # LightBlue, alpha 125
        a = 125 / 255
        c0, c1 = (w - ps) // 2, (w + ps) // 2
        r0, r1 = (h - ps) // 2, (h + ps) // 2
        for ys, xs in ((slice(r0, r0 + ps), slice(0, c0)), (slice(r0, r0 + ps), slice(c1, w)),
                       (slice(0, r0), slice(c0, c0 + ps)), (slice(r1, h), slice(c0, c0 + ps))):
            arr[ys, xs, :3] = arr[ys, xs, :3] * (1 - a) + blue * a
        g = 75 / 255
        for i in range(1, n):
            arr[:, i * ps - 1, :3] *= 1 - g
            arr[i * ps - 1, :, :3] *= 1 - g
        out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA").copy()
        px = out.load()
        bx, by = (w - ps) // 2 - 1, (h - ps) // 2 - 1
        for k in range(ps + 1):
            for (x, y) in ((bx + k, by), (bx + k, by + ps), (bx, by + k), (bx + ps, by + k)):
                if 0 <= x < w and 0 <= y < h:
                    px[x, y] = (0, 0, 0, 255)
        if ps >= 6:
            wx, wy = (w - ps) // 2, (h - ps) // 2
            for k in range(ps - 1):
                for (x, y) in ((wx + k, wy), (wx + k, wy + ps - 2), (wx, wy + k), (wx + ps - 2, wy + k)):
                    px[x, y] = (255, 255, 255, 255)
        return out.crop((0, 0, w - 1, h - 1))


@dataclass
class ToolbarItem:
    """One toolbar/menu entry. ``kind``: tool | action | toggle | int | color | choice | size |
    dialog | separator | submenu. ``setter(value)`` applies int/color/choice/toggle/size values;
    ``activate(**params)`` runs actions and dialogs (``dialog`` names the dialog the UI shows and
    ``params`` the values it should collect)."""
    id: str
    label: str = ""
    kind: str = "action"
    icon: str = ""
    value: Any = None
    visible: bool = True
    enabled: bool = True
    checked: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[tuple[str, str]] | None = None  # (value, label)
    shortcut: str = ""
    dialog: str = ""
    params: list[str] = field(default_factory=list)
    items: list[ToolbarItem] = field(default_factory=list)
    setter: Callable[[Any], None] | None = None
    activate: Callable[..., Any] | None = None


@dataclass
class ToolbarModel:
    """ShareX's ShapeManagerMenu, top to bottom: main buttons (editor), tool buttons, colour
    buttons, the "Tool options" drop-down, Edit / Image / Capture / Options menus."""
    main: list[ToolbarItem]
    tools: list[ToolbarItem]
    colors: list[ToolbarItem]
    tool_options: list[ToolbarItem]
    edit_menu: list[ToolbarItem]
    image_menu: list[ToolbarItem]
    capture_menu: list[ToolbarItem]
    options_menu: list[ToolbarItem]
    tool_options_visible: bool = True

    def all_items(self) -> list[ToolbarItem]:
        out: list[ToolbarItem] = []

        def walk(items):
            for it in items:
                out.append(it)
                walk(it.items)
        for group in (self.main, self.tools, self.colors, self.tool_options, self.edit_menu, self.image_menu,
                      self.capture_menu, self.options_menu):
            walk(group)
        return out

    def find(self, item_id: str) -> ToolbarItem:
        for it in self.all_items():
            if it.id == item_id:
                return it
        raise KeyError(item_id)


TOOL_ICONS = {
    ShapeType.RegionRectangle: "layer-shape-region", ShapeType.RegionEllipse: "layer-shape-ellipse-region",
    ShapeType.RegionFreehand: "layer-shape-polygon", ShapeType.ToolSelect: "cursor",
    ShapeType.DrawingRectangle: "layer-shape", ShapeType.DrawingEllipse: "layer-shape-ellipse",
    ShapeType.DrawingFreehand: "pencil", ShapeType.DrawingFreehandArrow: "pencil-arrow",
    ShapeType.DrawingLine: "layer-shape-line", ShapeType.DrawingArrow: "layer-shape-arrow",
    ShapeType.DrawingTextOutline: "edit-outline", ShapeType.DrawingTextBackground: "edit-shade",
    ShapeType.DrawingSpeechBalloon: "balloon-box-left", ShapeType.DrawingStep: "counter-reset",
    ShapeType.DrawingMagnify: "magnifier-zoom", ShapeType.DrawingImage: "folder-open-image",
    ShapeType.DrawingImageScreen: "monitor-image", ShapeType.DrawingSticker: "smiley-cool",
    ShapeType.DrawingCursor: "stamp-cursor", ShapeType.DrawingSmartEraser: "eraser",
    ShapeType.EffectBlur: "layer-shade", ShapeType.EffectPixelate: "grid",
    ShapeType.EffectHighlight: "highlighter-text", ShapeType.ToolCrop: "image-crop",
    ShapeType.ToolCutOut: "table-delete-column",
}

#: Tools that never hover a shape/window (ShareX CheckHover).
_NO_HOVER_TOOLS = frozenset({
    ShapeType.RegionFreehand, ShapeType.DrawingFreehand, ShapeType.DrawingFreehandArrow, ShapeType.DrawingLine,
    ShapeType.DrawingArrow, ShapeType.DrawingTextOutline, ShapeType.DrawingTextBackground,
    ShapeType.DrawingSpeechBalloon, ShapeType.DrawingStep, ShapeType.DrawingImage, ShapeType.DrawingSticker,
    ShapeType.DrawingCursor, ShapeType.ToolSelect})

_TOOL_KEYS = {
    "m": ShapeType.ToolSelect, "r": ShapeType.DrawingRectangle, "KP_1": ShapeType.DrawingRectangle,
    "e": ShapeType.DrawingEllipse, "KP_2": ShapeType.DrawingEllipse, "f": ShapeType.DrawingFreehand,
    "KP_3": ShapeType.DrawingFreehand, "l": ShapeType.DrawingLine, "KP_4": ShapeType.DrawingLine,
    "a": ShapeType.DrawingArrow, "KP_5": ShapeType.DrawingArrow, "o": ShapeType.DrawingTextOutline,
    "KP_6": ShapeType.DrawingTextOutline, "t": ShapeType.DrawingTextBackground,
    "s": ShapeType.DrawingSpeechBalloon, "i": ShapeType.DrawingStep, "KP_7": ShapeType.DrawingStep,
    "b": ShapeType.EffectBlur, "KP_8": ShapeType.EffectBlur, "p": ShapeType.EffectPixelate,
    "KP_9": ShapeType.EffectPixelate, "h": ShapeType.EffectHighlight,
}

MAGNIFIER_PIXEL_COUNT_MIN, MAGNIFIER_PIXEL_COUNT_MAX = 3, 35
MOVE_SPEED_MIN, MOVE_SPEED_MAX = 1, 10
SNAP_DISTANCE = 30
NODE_SIZE = 13

# RegionCaptureForm colours (default theme)
CANVAS_BACKGROUND = (200, 200, 200, 255)
TEXT_BACKGROUND = (42, 131, 199, 200)
TEXT_OUTER_BORDER = (255, 255, 255, 200)
TEXT_INNER_BORDER = (0, 81, 145, 200)
BUTTON_SIZE = (80, 40)
BUTTON_OFFSET = 15


def _key_norm(name: str) -> str:
    return name.lower() if len(name) == 1 else name


class ShapeManager:
    """See the module docstring."""

    #: ShareX's static RegionCaptureForm.LastRegionFillPath: (rect, mask) of the last region.
    last_region: tuple[tuple[int, int, int, int], Image.Image | None] | None = None

    def __init__(self, image: Image.Image, mode: RegionCaptureMode = RegionCaptureMode.Default,
                 options: RegionCaptureOptions | None = None, callbacks: Callbacks | None = None,
                 *, screen_origin: tuple[int, int] = (0, 0)) -> None:
        self.mode = RegionCaptureMode(mode)
        self.options = options if options is not None else RegionCaptureOptions()
        self.callbacks = callbacks if callbacks is not None else Callbacks()
        self.screen_origin = screen_origin
        self._apply_mode_overrides()
        self.canvas: Image.Image = image.convert("RGBA") if image.mode != "RGBA" else image
        self.canvas_version = 0
        self._canvas_surface: cairo.ImageSurface | None = None
        self._dimmed_surface: cairo.ImageSurface | None = None
        self.effect_cache = R.EffectCache()

        self.shapes: list[S.Shape] = []
        self._current_shape: S.Shape | None = None
        self._current_tool: ShapeType = ShapeType.RegionRectangle
        self.nodes_visible = False
        self.is_creating = False
        self.is_moving = False
        self.is_resizing = False
        self._drag_node: int | None = None
        self._drag_state: dict = {}
        self._pending_memento: Memento | None = None
        self._undo: list[Memento] = []
        self._redo: list[Memento] = []
        self.starting_step_number = 1
        self.is_image_modified = False
        self.snap_rects: list[tuple[float, float, float, float]] = []
        self.monitors: list[tuple[int, int, int, int]] = []
        self.hover_shape: S.Shape | None = None
        self.pointer: tuple[float, float] | None = None
        self.pointer_inside = True  # False once the pointer has left the view: no magnifier or crosshair then
        self.mods = Mod.NONE
        self.view_scale = 1.0
        self.result: CaptureResult | None = None
        self.shape_clipboard: list[S.Shape] = []
        self._pending_dialog_shape: S.Shape | None = None
        self._started = time.monotonic()
        self._key_allowed = False
        self.client_area: S.Rect | None = None

        if self.mode == RegionCaptureMode.Annotation:
            self._current_tool = self.options.last_region_tool
        elif self.is_editor_mode:
            self._current_tool = self.options.last_editor_tool
        else:
            self._current_tool = ShapeType.RegionRectangle

    # ------------------------------------------------------------------ properties

    def _apply_mode_overrides(self) -> None:
        """RegionCaptureTasks: each tool mode runs with some options forced."""
        o = self.options
        if self.mode == RegionCaptureMode.ScreenColorPicker:
            o.detect_windows = False
            o.background_dim_strength = 0
        elif self.mode == RegionCaptureMode.OneClick:
            o.background_dim_strength = 0
            o.show_magnifier = False
        elif self.mode == RegionCaptureMode.Ruler:
            o.quick_crop = False
            o.use_light_resize_nodes = True

    @property
    def annotation(self) -> AnnotationOptions:
        return self.options.annotation_options

    @property
    def is_editor_mode(self) -> bool:
        return self.mode in (RegionCaptureMode.Editor, RegionCaptureMode.TaskEditor)

    @property
    def is_annotation_mode(self) -> bool:
        return self.mode == RegionCaptureMode.Annotation or self.is_editor_mode

    @property
    def canvas_rect(self) -> S.Rect:
        return S.Rect(0, 0, self.canvas.width, self.canvas.height)

    @property
    def area(self) -> S.Rect:
        """ShareX ClientArea (the visible area); the canvas unless the UI set one."""
        return self.client_area if self.client_area is not None else self.canvas_rect

    @property
    def minimum_size(self) -> int:
        return self.options.minimum_size

    @property
    def current_shape(self) -> S.Shape | None:
        return self._current_shape

    def _set_current_shape(self, shape: S.Shape | None) -> None:
        self._current_shape = shape
        if shape is not None:
            shape.save_config(self.annotation)  # ShareX CurrentShape setter: OnConfigSave

    @property
    def current_tool(self) -> ShapeType:
        return self._current_tool

    @current_tool.setter
    def current_tool(self, value: ShapeType) -> None:
        value = ShapeType(value)
        if value == self._current_tool:
            return
        if self.is_editor_mode and S.is_region_type(value):
            raise ValueError("region tools are not available in the image editor")
        if not self.is_annotation_mode and not S.is_region_type(value):
            raise ValueError(f"{value} needs Annotation or Editor mode")
        self._current_tool = value
        if self.is_annotation_mode:
            if S.is_region_type(value):
                self.options.last_region_tool = value
            elif self.is_editor_mode:
                self.options.last_editor_tool = value
            else:
                self.options.last_annotation_tool = value
            self._clear_tools()
        cs = self._current_shape
        if cs is not None:
            if value == ShapeType.ToolSelect:
                if not cs.is_handled_by_select_tool:
                    self._deselect()
            elif cs.shape_type != value:
                self._deselect()
        self._changed()

    @property
    def current_shape_tool(self) -> ShapeType:
        """The tool whose options the toolbar shows (the selected shape's type under Select)."""
        if self._current_tool == ShapeType.ToolSelect and self._current_shape is not None:
            return self._current_shape.shape_type
        return self._current_tool

    @property
    def is_current_tool_region(self) -> bool:
        return S.is_region_type(self._current_tool)

    @property
    def regions(self) -> list[S.Shape]:
        return [s for s in self.shapes if isinstance(s, S.RegionShape)]

    @property
    def valid_regions(self) -> list[S.Shape]:
        return [s for s in self.regions if s.is_valid(self.minimum_size)]

    @property
    def drawing_shapes(self) -> list[S.Shape]:
        return [s for s in self.shapes if isinstance(s, S.DrawingShape)]

    @property
    def effect_shapes(self) -> list[S.Shape]:
        return [s for s in self.shapes if isinstance(s, S.EffectShape)]

    @property
    def tool_shapes(self) -> list[S.Shape]:
        return [s for s in self.shapes if isinstance(s, S.ToolShape)]

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # ------------------------------------------------------------------ setup from the UI

    def set_snap_rects(self, rects) -> None:
        """Window / control rectangles (image coords) for hover-snapping, topmost first."""
        self.snap_rects = [tuple(map(float, r)) for r in rects]
        self._update_hover()
        self._changed()

    def set_monitors(self, rects) -> None:
        """Monitor rectangles in image coordinates (Capture monitor / active monitor)."""
        self.monitors = [tuple(int(v) for v in r) for r in rects]

    def set_client_area(self, rect: tuple[float, float, float, float] | None) -> None:
        self.client_area = S.Rect(*rect) if rect is not None else None

    # ------------------------------------------------------------------ history

    def _snapshot(self) -> Memento:
        return Memento([s.clone() for s in self.shapes], self.canvas, self.canvas_version)

    def _checkpoint(self) -> None:
        """ShareX CreateShapesMemento / CreateCanvasMemento (the canvas is an immutable reference,
        so one memento type covers both)."""
        self._undo.append(self._snapshot())
        self._redo.clear()
        if len(self._undo) > 200:
            self._undo.pop(0)

    def _pend_checkpoint(self) -> None:
        """A checkpoint taken only if the following drag actually changes something."""
        self._pending_memento = self._snapshot()

    def _commit_pending(self) -> None:
        if self._pending_memento is not None:
            self._undo.append(self._pending_memento)
            self._redo.clear()
            self._pending_memento = None

    def _drop_last_checkpoint(self) -> None:
        if self._undo:
            self._undo.pop()

    def _restore(self, m: Memento) -> None:
        self.shapes = [s.clone() for s in m.shapes]
        if m.canvas is not self.canvas:
            self._set_canvas(m.canvas, version=m.canvas_version)
        self._current_shape = None
        self.nodes_visible = False
        self.is_creating = self.is_moving = self.is_resizing = False
        self._drag_node = None
        self._pending_memento = None
        self._clear_tools()
        self._on_image_modified()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    # ------------------------------------------------------------------ notifications

    def _changed(self) -> None:
        if self.callbacks.on_change:
            self.callbacks.on_change()

    def _on_image_modified(self) -> None:
        self.order_step_shapes()
        self.is_image_modified = True
        if self.options.editor_auto_copy_image and self.is_editor_mode and self.callbacks.copy_image \
                and self.result is None:
            self.callbacks.copy_image(self.render_output())
        self._changed()

    def order_step_shapes(self) -> None:
        """Steps are numbered in z-order from ``starting_step_number``."""
        n = self.starting_step_number
        for s in self.shapes:
            if isinstance(s, S.DrawingStep):
                s.number = n
                n += 1

    def _finish(self, result: CaptureResult) -> CaptureResult:
        if result.result in (RegionResult.Region,) and result.rect is not None:
            ShapeManager.last_region = (result.rect, result.mask)
        self.result = result
        if self.callbacks.on_result:
            self.callbacks.on_result(result)
        self._changed()
        return result

    def close(self, result: RegionResult = RegionResult.Close) -> CaptureResult:
        """End the session with ``result`` (Region/Fullscreen/... fill in their geometry)."""
        if result == RegionResult.Region:
            return self._finish(self.region_result())
        if result == RegionResult.Fullscreen:
            x, y, w, h = self.canvas_rect.rounded()
            return self._finish(CaptureResult(result, rect=(x, y, w, h)))
        if result == RegionResult.ActiveMonitor:
            return self._finish(CaptureResult(result, rect=self._active_monitor()))
        if result == RegionResult.LastRegion:
            rect, mask = ShapeManager.last_region if ShapeManager.last_region else (None, None)
            return self._finish(CaptureResult(result, rect=rect, mask=mask))
        return self._finish(CaptureResult(RegionResult(result)))

    def _active_monitor(self) -> tuple[int, int, int, int]:
        p = self.pointer or (0, 0)
        for m in self.monitors:
            if m[0] <= p[0] < m[0] + m[2] and m[1] <= p[1] < m[1] + m[3]:
                return m
        return self.canvas_rect.rounded()

    def capture_monitor(self, index: int) -> CaptureResult:
        rect = self.monitors[index] if 0 <= index < len(self.monitors) else None
        return self._finish(CaptureResult(RegionResult.Monitor, rect=rect, monitor_index=index))

    # ------------------------------------------------------------------ shapes list ops

    def create_shape(self, shape_type: ShapeType) -> S.Shape:
        return S.create_shape(shape_type, self.annotation)

    def _add_shape(self, shape: S.Shape, checkpoint: bool = True) -> None:
        if checkpoint:
            self._checkpoint()
        self.shapes.append(shape)
        self._set_current_shape(shape)

    def delete_shape(self, shape: S.Shape | None) -> None:
        if shape is None or shape not in self.shapes:
            return
        self._checkpoint()
        self.shapes.remove(shape)
        if shape is self._current_shape:
            self._deselect()
        if shape.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
            self._on_image_modified()
        self._changed()

    def delete_current_shape(self) -> None:
        self.delete_shape(self._current_shape)

    def delete_all_shapes(self, checkpoint: bool = True) -> None:
        if not self.shapes:
            return
        if checkpoint:
            self._checkpoint()
        self.shapes.clear()
        self._deselect()
        self._on_image_modified()

    def _discard_shape(self, shape: S.Shape) -> None:
        """Remove a shape whose creation was abandoned, together with its checkpoint."""
        if shape in self.shapes:
            self.shapes.remove(shape)
            self._drop_last_checkpoint()
        if shape is self._current_shape:
            self._deselect()
        self._changed()

    def _clear_tools(self) -> None:
        for t in self.tool_shapes:
            self.shapes.remove(t)
            if t is self._current_shape:
                self._current_shape = None
                self.nodes_visible = False

    def _select(self, shape: S.Shape | None) -> None:
        if shape is None:
            return
        self._set_current_shape(shape)
        self.nodes_visible = shape.shows_nodes
        if self.options.switch_to_drawing_tool_after_selection and self._current_tool == ShapeType.ToolSelect:
            self._current_tool = shape.shape_type
        self._changed()

    def select_shape(self, shape: S.Shape) -> None:
        """Select a shape programmatically (as clicking it with the select tool does)."""
        self._select(shape)

    def _deselect(self) -> None:
        self._current_shape = None
        self.nodes_visible = False

    def deselect(self) -> None:
        self._deselect()
        self._changed()

    def _move_in_list(self, shape: S.Shape | None, new_index: Callable[[int], int]) -> None:
        if shape is None or shape not in self.shapes:
            return
        i = self.shapes.index(shape)
        j = max(0, min(len(self.shapes) - 1, new_index(i)))
        if i == j:
            return
        self._checkpoint()
        self.shapes.insert(j, self.shapes.pop(i))
        self._on_image_modified()

    def move_shape_top(self, shape=None) -> None:
        """Bring to front (Home)."""
        self._move_in_list(shape or self._current_shape, lambda i: len(self.shapes) - 1)

    def move_shape_bottom(self, shape=None) -> None:
        """Send to back (End)."""
        self._move_in_list(shape or self._current_shape, lambda i: 0)

    def move_shape_up(self, shape=None) -> None:
        """Bring forward (Page Up)."""
        self._move_in_list(shape or self._current_shape, lambda i: i + 1)

    def move_shape_down(self, shape=None) -> None:
        """Send backward (Page Down)."""
        self._move_in_list(shape or self._current_shape, lambda i: i - 1)

    def move_all(self, dx: float, dy: float) -> None:
        if dx or dy:
            for s in self.shapes:
                s.move(dx, dy)

    def get_intersect_shape(self, x: float, y: float) -> S.Shape | None:
        if self.mods & Mod.CTRL:
            return None
        for s in reversed(self.shapes):
            if s.is_selectable(self._current_tool) and s.intersects(x, y):
                return s
        return None

    def duplicate_current_shape(self, at_pointer: bool = True) -> S.Shape | None:
        """Ctrl+D: a copy centred on the pointer (or offset by 10 px)."""
        s = self._current_shape
        if s is None or not s.is_handled_by_select_tool:
            return None
        return self._insert_copy(s, at_pointer)

    def _insert_copy(self, s: S.Shape, at_pointer: bool) -> S.Shape:
        c = s.duplicate()
        if at_pointer and self.pointer is not None:
            c.move_absolute(self.pointer[0], self.pointer[1], center=True)
        else:
            c.move(10, 10)
        self._add_shape(c)
        self._select(c)
        if c.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
            self._on_image_modified()
        return c

    def copy_current_shape(self) -> bool:
        """Copy the selected shape to the internal shape clipboard."""
        s = self._current_shape
        if s is None or not s.is_handled_by_select_tool:
            return False
        self.shape_clipboard = [s.clone()]
        return True

    def paste_shape(self, at_pointer: bool = True) -> S.Shape | None:
        if not self.shape_clipboard:
            return None
        return self._insert_copy(self.shape_clipboard[0], at_pointer)

    def clipboard_changed(self) -> None:
        """The system clipboard got new content: Ctrl+V pastes that instead of a copied shape."""
        self.shape_clipboard = []

    def paste(self, at_pointer: bool = True) -> S.Shape | None:
        """Ctrl+V: a copied shape, else the system clipboard's image / image file / text."""
        if self.shape_clipboard:
            return self.paste_shape(at_pointer)
        if self.callbacks.read_clipboard is None:
            return None
        content = self.callbacks.read_clipboard()
        if isinstance(content, Image.Image):
            return self.insert_image(content)
        if isinstance(content, (list, tuple)):
            for f in content:
                if Path(str(f)).suffix.lower() in ST.STICKER_EXTENSIONS and Path(str(f)).is_file():
                    return self.insert_image(imaging.load(f))
            return None
        if isinstance(content, str) and content:
            p = Path(content.strip())
            if p.suffix.lower() in ST.STICKER_EXTENSIONS and p.is_file():
                return self.insert_image(imaging.load(p))
            return self.paste_text(content, at_pointer)
        return None

    def paste_text(self, text: str, at_pointer: bool = True) -> S.Shape:
        """ShareX PasteFromClipboard (text): a Text (Background) shape."""
        pos = self.pointer if at_pointer and self.pointer is not None else self.area.center
        self.current_tool = ShapeType.DrawingTextBackground
        shape = self.create_shape(ShapeType.DrawingTextBackground)
        shape.rect = S.Rect(pos[0], pos[1], 1, 1)
        shape.text = text.strip()
        shape.auto_size(R.measure_text, True)
        shape.on_created()
        self._add_shape(shape)
        self._select(shape)
        self._on_image_modified()
        return shape

    def add_cursor(self, img: Image.Image | None = None, position: tuple[float, float] = (0, 0),
                   hotspot: tuple[int, int] | None = None) -> S.Shape:
        """ShareX AddCursor: the captured mouse cursor as a movable Cursor shape."""
        if img is None:
            img, hotspot = R.default_cursor_image(), R.DEFAULT_CURSOR_HOTSPOT
        shape = self.create_shape(ShapeType.DrawingCursor)
        shape.update_cursor(img, position, hotspot or (0, 0))
        shape.on_created()
        self.shapes.append(shape)
        self._on_image_modified()
        return shape

    def update_current_shape(self) -> None:
        """Toolbar option changed: the selected shape reloads its properties (undoable)."""
        s = self._current_shape
        if s is None:
            self._changed()
            return
        self._checkpoint()
        s.load_config(self.annotation)
        if isinstance(s, S.DrawingLine) and s.points[0] != s.points[-1]:
            s.recalc_rect()
        self._on_image_modified()

    # ------------------------------------------------------------------ hover / snapping

    def _update_hover(self) -> None:
        self.hover_shape = self._check_hover()

    def _check_hover(self) -> S.Shape | None:
        if self.pointer is None or self.is_creating or self.is_moving or self.is_resizing:
            return None
        if self._node_at(*self.pointer) is not None or self._button_at(*self.pointer) is not None:
            return None
        x, y = self.pointer
        shape = self.get_intersect_shape(x, y)
        if shape is not None and shape.is_valid(self.minimum_size):
            return shape
        if shape is not None or self._current_tool in _NO_HOVER_TOOLS:
            return None
        if self.options.is_fixed_size and self.is_current_tool_region:
            fs = self.options.fixed_size
            r = S.create_shape(ShapeType.RegionRectangle, self.annotation)
            r.rect = S.Rect(x - fs.width // 2, y - fs.height // 2, fs.width, fs.height)
            return r
        rect = self.find_snap_rect(x, y)
        if rect is not None:
            r = S.create_shape(ShapeType.RegionRectangle, self.annotation)
            r.rect = self.area.intersect(S.Rect(*rect))
            return r
        return None

    def find_snap_rect(self, x: float, y: float) -> tuple[float, float, float, float] | None:
        """The first snap rectangle containing the point (ShareX FindSelectedWindow)."""
        if self.is_editor_mode or not (self.options.detect_windows or self.mode == RegionCaptureMode.OneClick):
            return None
        for r in self.snap_rects:
            if r[2] > 0 and r[3] > 0 and S.Rect(*r).contains(x, y):
                return r
        return None

    def _snap_position(self, start: S.Point, pos: S.Point) -> S.Point:
        """Alt while drawing: snap to the nearest ``snap_sizes`` entry within 30 px."""
        cur = S.create_rect(start, pos)
        best: Size | None = None
        best_d = None
        for size in self.options.snap_sizes:
            d = math.hypot(cur.w - size.width, cur.h - size.height)
            if 0 < d < SNAP_DISTANCE and (best_d is None or d < best_d):
                best, best_d = size, d
        if best is None:
            return pos
        sx, sy = start
        nx = sx + best.width - 1 if pos[0] > sx else sx - best.width + 1
        ny = sy + best.height - 1 if pos[1] > sy else sy - best.height + 1
        if self.area.contains_rect(S.create_rect(start, (nx, ny))):
            return (nx, ny)
        return pos

    # ------------------------------------------------------------------ nodes and buttons

    def visible_nodes(self) -> list[tuple[int, S.Point]]:
        """(index, position) of the resize nodes currently shown."""
        s = self._current_shape
        if s is None or not self.nodes_visible or self.mods & Mod.CTRL:
            return []
        pos = s.node_positions()
        size = NODE_SIZE / self.view_scale
        vis = [p is not None for p in pos]

        def nrect(i):
            p = pos[i]
            return S.Rect(p[0] - (size - 1) / 2, p[1] - (size - 1) / 2, size, size)

        if isinstance(s, S.DrawingLine):
            last = len(s.points) - 1
            for i in range(last):
                if vis[i] and nrect(i).intersects(nrect(last)):
                    vis[i] = False
        elif all(vis[:8]):
            P = S.NodePosition
            br = nrect(P.BottomRight)
            if nrect(P.Right).intersects(br):
                vis[P.Left] = vis[P.Right] = False
            if nrect(P.Bottom).intersects(br):
                vis[P.Top] = vis[P.Bottom] = False
            if nrect(P.TopRight).intersects(br):
                vis[P.TopLeft] = vis[P.Top] = vis[P.TopRight] = False
            if nrect(P.BottomLeft).intersects(br):
                vis[P.TopLeft] = vis[P.Left] = vis[P.BottomLeft] = False
        return [(i, pos[i]) for i in range(9) if vis[i] and pos[i] is not None]

    def _node_at(self, x: float, y: float) -> int | None:
        nodes = self.visible_nodes()
        size = NODE_SIZE / self.view_scale
        order = sorted(nodes, key=lambda n: 0 if n[0] == S.NodePosition.BottomRight else 1)
        for i, p in order:
            if S.Rect(p[0] - (size - 1) / 2, p[1] - (size - 1) / 2, size, size).contains(x, y):
                return i
        return None

    def tool_buttons(self) -> list[tuple[str, S.Rect]]:
        """Confirm / cancel buttons of an active crop or cut-out tool (ShareX ImageEditorButton)."""
        tools = [t for t in self.tool_shapes if t.is_valid(self.minimum_size) or isinstance(t, S.ToolCutOut)]
        if not tools or self.is_creating:
            return []
        t = tools[-1]
        r = t.rect
        bw, bh = BUTTON_SIZE[0] / self.view_scale, BUTTON_SIZE[1] / self.view_scale
        off = BUTTON_OFFSET / self.view_scale
        ca = self.area
        if isinstance(t, S.ToolCrop):
            if (r.bottom + off + bh > ca.bottom and r.w > bw * 2 + off * 3 and r.h > bh + off * 2):
                confirm = S.Rect(r.right - off * 2 - bw * 2, r.bottom - off - bh, bw, bh)
                cancel = S.Rect(r.right - off - bw, r.bottom - off - bh, bw, bh)
            else:
                confirm = S.Rect(r.right - bw * 2 - off, r.bottom + off, bw, bh)
                cancel = S.Rect(r.right - bw, r.bottom + off, bw, bh)
        else:
            if not t.is_valid_cut(self.minimum_size, self.canvas_rect):
                return []
            if t.is_vertical_trim(self.minimum_size):
                below = ca.bottom - r.bottom >= bh + 2 * off
                top = r.bottom + off if below else r.y - off - bh
                left = r.x + r.w / 2 - (2 * bw + off) / 2
                right = left + 2 * bw + off
                if left < ca.x + off and not right >= ca.right - off:
                    left = ca.x + off
                elif right >= ca.right - off and not left < ca.x + off:
                    left = ca.right - off - 2 * bw - off
                confirm = S.Rect(left, top, bw, bh)
                cancel = confirm.offset(bw + off, 0)
            else:
                right_side = ca.right - r.right >= bw + 2 * off
                left = r.right + off if right_side else r.x - off - bw
                top = r.y + r.h / 2 - (2 * bh + off) / 2
                bottom = top + 2 * bh + off
                if top < ca.y + off and not bottom >= ca.bottom - off:
                    top = ca.y + off
                elif bottom >= ca.bottom - off and not top < ca.y + off:
                    top = ca.bottom - off - 2 * bh - off
                confirm = S.Rect(left, top, bw, bh)
                cancel = confirm.offset(0, bh + off)
        return [("confirm", confirm), ("cancel", cancel)]

    def _button_at(self, x: float, y: float) -> str | None:
        for name, r in self.tool_buttons():
            if r.contains(x, y):
                return name
        return None

    def confirm_tool(self) -> bool:
        """Apply the active crop / cut-out tool (its ✔ button)."""
        tools = self.tool_shapes
        if not tools:
            return False
        t = tools[-1]
        if isinstance(t, S.ToolCrop):
            rect = t.rect
            self._remove_tool(t)
            self.crop_area(rect)
        elif isinstance(t, S.ToolCutOut):
            if not t.is_valid_cut(self.minimum_size, self.canvas_rect):
                return False
            rect = t.rect
            self._remove_tool(t)
            self.cut_out(rect)
        return True

    def cancel_tool(self) -> bool:
        """Discard the active crop / cut-out tool (its ✖ button)."""
        tools = self.tool_shapes
        if not tools:
            return False
        self._remove_tool(tools[-1])
        return True

    def _remove_tool(self, t: S.Shape) -> None:
        if t in self.shapes:
            self.shapes.remove(t)
        if t is self._current_shape:
            self._deselect()
        if self.options.switch_to_selection_tool_after_drawing:
            self._current_tool = ShapeType.ToolSelect
        self._changed()

    # ------------------------------------------------------------------ pointer input

    def press(self, x: float, y: float, button: int = BUTTON_LEFT, mods: int = Mod.NONE) -> CaptureResult | None:
        """Mouse button pressed at image coordinates (x, y)."""
        if self.result is not None:
            return self.result
        self.mods = Mod(mods)
        self._set_pointer(x, y)
        if button != BUTTON_LEFT:
            return None
        if self.mode in (RegionCaptureMode.OneClick, RegionCaptureMode.ScreenColorPicker):
            return self._one_click_result(x, y)
        btn = self._button_at(x, y)
        if btn is not None:
            if btn == "confirm":
                self.confirm_tool()
            else:
                self.cancel_tool()
            return None
        node = self._node_at(x, y)
        if node is not None and self._current_shape is not None:
            self._pend_checkpoint()
            self._drag_node = node
            self._drag_state = self._current_shape.begin_node_drag(node)
            self.is_resizing = True
            return None
        if not self.is_creating:
            return self.start_region_selection()
        return None

    def _one_click_result(self, x: float, y: float) -> CaptureResult:
        px, py = math.floor(x), math.floor(y)
        if self.mode == RegionCaptureMode.ScreenColorPicker:
            return self._finish(CaptureResult(RegionResult.Region, point=(px, py), color=self.color_at(px, py)))
        win = self.find_snap_rect(x, y)
        return self._finish(CaptureResult(RegionResult.Region, point=(px, py),
                                          window_rect=tuple(int(v) for v in win) if win else None))

    def motion(self, x: float, y: float, mods: int = Mod.NONE) -> None:
        """Pointer moved (with or without a button held)."""
        if self.result is not None:
            return
        self.mods = Mod(mods)
        prev = self.pointer if self.pointer is not None else (x, y)
        dx, dy = x - prev[0], y - prev[1]
        self._set_pointer(x, y)
        s = self._current_shape
        if self._drag_node is not None and s is not None:
            if dx or dy:
                self._commit_pending()
            s.drag_node(self._drag_node, (x, y), self._drag_state, bool(self.mods & Mod.SHIFT))
            if s.limit_to_canvas:
                s.rect = s.rect.intersect(self.canvas_rect)
        elif self.is_creating and s is not None:
            pos = (x, y)
            corner = (dx, dy) if self.mods & Mod.CTRL else None
            prop = bool(self.mods & Mod.SHIFT)
            if not prop and not s.force_proportional and self.mods & Mod.ALT \
                    and not isinstance(s, (S.RegionFreehand, S.DrawingFreehand)):
                pos = self._snap_position(s.start, pos)
            s.update_create(pos, proportional=prop, corner_move=corner)
            self._limit_to_canvas(s)
        elif self.is_moving and s is not None:
            if dx or dy:
                self._commit_pending()
                s.move(dx, dy)
                self._limit_to_canvas(s)
        self._update_hover()
        self._changed()

    def _limit_to_canvas(self, s: S.Shape) -> None:
        if s.limit_to_canvas:
            c = self.canvas_rect

            def clamp(p):
                return (min(max(p[0], c.x), c.right - 1), min(max(p[1], c.y), c.bottom - 1))
            s.start, s.end = clamp(s.start), clamp(s.end)

    def _set_pointer(self, x: float, y: float) -> None:
        self.pointer = (x, y)
        self.pointer_inside = True

    def pointer_left(self) -> None:
        """The pointer left the view. ``pointer`` keeps the last position (paste and nudges use it); only
        the cursor graphics stop, so the magnifier isn't left painted where the pointer last was."""
        self.pointer_inside = False

    def release(self, x: float, y: float, button: int = BUTTON_LEFT, mods: int = Mod.NONE) -> CaptureResult | None:
        """Mouse button released."""
        if self.result is not None:
            return self.result
        self.mods = Mod(mods)
        if self.pointer != (x, y):
            self.motion(x, y, mods)
        if button == BUTTON_LEFT:
            if self._drag_node is not None:
                self._drag_node = None
                self.is_resizing = False
                self._pending_memento = None
                s = self._current_shape
                if s is not None and s.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
                    self._on_image_modified()
                self._changed()
                return None
            if self.is_moving or self.is_creating:
                return self.end_region_selection()
            return None
        if button == BUTTON_RIGHT:
            if self.is_creating:
                s = self._current_shape
                self.is_creating = False
                if s is not None:
                    self._discard_shape(s)
                self._update_hover()
                return None
            if self.is_annotation_mode:
                return self.run_action(self.options.region_capture_action_right_click)
            target = self.get_intersect_shape(x, y)
            if target is not None:
                self.delete_shape(target)
                return None
            return self._finish(CaptureResult(RegionResult.Close))
        if button == BUTTON_MIDDLE:
            if self.is_editor_mode:
                return None  # middle-drag pans the editor view (handled by the view)
            return self.run_action(self.options.region_capture_action_middle_click)
        if button == BUTTON_X1:
            return self.run_action(self.options.region_capture_action_x1_click)
        if button == BUTTON_X2:
            return self.run_action(self.options.region_capture_action_x2_click)
        return None

    def double_click(self, x: float, y: float, button: int = BUTTON_LEFT, mods: int = Mod.NONE) -> CaptureResult | None:
        """Second click of a double click (after its press/release pair)."""
        if self.result is not None or button != BUTTON_LEFT:
            return self.result
        self.mods = Mod(mods)
        self._set_pointer(x, y)
        if self.is_current_tool_region and self.valid_regions:
            return self._finish(self.region_result())
        s = self._current_shape
        if s is not None and not self.is_creating:
            self._on_double_clicked(s)
        return None

    def scroll(self, dx: float, dy: float, mods: int = Mod.NONE) -> None:
        """Wheel: magnifier pixel count (no modifier); Ctrl zooms the editor view."""
        mods = Mod(mods)
        o = self.options
        if mods == Mod.NONE:
            if dy < 0:
                if o.show_magnifier:
                    o.magnifier_pixel_count = min(o.magnifier_pixel_count + 2, MAGNIFIER_PIXEL_COUNT_MAX)
                else:
                    o.show_magnifier = True
            elif dy > 0:
                n = o.magnifier_pixel_count - 2
                if n < MAGNIFIER_PIXEL_COUNT_MIN:
                    n = MAGNIFIER_PIXEL_COUNT_MIN
                    o.show_magnifier = False
                o.magnifier_pixel_count = n
            self._changed()
        elif mods == Mod.CTRL and self.is_editor_mode and self.callbacks.zoom and dy:
            self.callbacks.zoom(1 if dy < 0 else -1)

    # ------------------------------------------------------------------ region selection

    def start_region_selection(self) -> CaptureResult | None:
        """ShareX StartRegionSelection: select the shape under the pointer or start a new one."""
        if self.pointer is None:
            return None
        x, y = self.pointer
        if self._button_at(x, y) is not None:
            return None
        shape = self.get_intersect_shape(x, y)
        if shape is not None and shape.is_selectable(self._current_tool):
            self._deselect()
            self._pend_checkpoint()
            self.is_moving = True
            self._set_current_shape(shape)
            self._select(shape)
        elif shape is None and self._current_tool == ShapeType.ToolSelect:
            self._clear_tools()
            self._deselect()
        elif not self.is_creating and self._current_tool != ShapeType.ToolSelect:
            self._clear_tools()
            self._deselect()
            shape = self.create_shape(self._current_tool)
            self._add_shape(shape)
            self._on_creating(shape, (x, y))
        self._update_hover()
        self._changed()
        return None

    def _on_creating(self, s: S.Shape, pos: S.Point) -> None:
        """ShareX OnCreating per shape type."""
        ctrl = bool(self.mods & Mod.CTRL)
        a = self.annotation
        if isinstance(s, S.RegionShape) and self.options.is_fixed_size:
            fs = self.options.fixed_size
            s.rect = S.Rect(pos[0] - fs.width // 2, pos[1] - fs.height // 2, fs.width, fs.height)
            self.is_moving = True
            s.on_created()
            return
        if isinstance(s, S.DrawingTextBackground):
            s.begin_create(pos)
            self._ask_text(s, "", creating=True)
            return
        if isinstance(s, S.DrawingStep):
            s.begin_create(pos)
            s.update_size(R.measure_text)
            self.is_moving = True
            self._shape_created(s)
            return
        if isinstance(s, S.DrawingCursor):
            if self.callbacks.cursor_image is not None:
                img, hotspot = self.callbacks.cursor_image()
            else:
                img, hotspot = R.default_cursor_image(), R.DEFAULT_CURSOR_HOTSPOT
            s.update_cursor(img, (round(pos[0]), round(pos[1])), hotspot)
            self.is_moving = True
            self._shape_created(s)
            return
        if isinstance(s, S.DrawingSticker):
            s.begin_create(pos)
            if ctrl and a.last_sticker_path and self._load_sticker(s, a.last_sticker_path):
                self.is_moving = True
                self._shape_created(s)
            else:
                self._ask_file(s, self.callbacks.request_sticker, creating=True)
            return
        if isinstance(s, S.DrawingImage) and not isinstance(s, S.DrawingImageScreen):
            s.begin_create(pos)
            if ctrl and a.last_image_file_path and self._load_image_file(s, a.last_image_file_path, True):
                self.is_moving = True
                self._shape_created(s)
            else:
                self._ask_file(s, self.callbacks.request_image_file, creating=True)
            return
        if isinstance(s, S.DrawingSmartEraser):
            s.begin_create(pos)
            self.is_creating = True
            px, py = math.floor(pos[0]), math.floor(pos[1])
            col = self.color_at(px, py)
            s.eraser_color = col if col is not None else "#FFFFFFFF"
            if self._dimming_active and col is not None:
                s.eraser_dimmed_color = R.color_to_str(*self._dim_rgba(R.parse_color(col)))
            return
        s.begin_create(pos)
        self.is_creating = True

    def _shape_created(self, s: S.Shape) -> None:
        """OnCreated for click-created shapes (they never pass through end_region_selection
        as 'creating')."""
        if isinstance(s, S.DrawingSpeechBalloon):
            s.auto_size(R.measure_text, True)
            s.place_tail()
        elif isinstance(s, S.DrawingTextBackground):
            s.auto_size(R.measure_text, True)
        s.on_created()
        if s.shows_nodes:
            self._select(s)
        else:
            self._set_current_shape(s)
        if s.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
            self._on_image_modified()

    def end_region_selection(self) -> CaptureResult | None:
        """ShareX EndRegionSelection."""
        was_creating, was_moving = self.is_creating, self.is_moving
        self.is_creating = self.is_moving = False
        moved = was_moving and self._pending_memento is None
        self._pending_memento = None
        s = self._current_shape
        if s is not None:
            if not self._shape_valid(s):
                saved_rect = s.rect
                s.rect = S.Rect()  # ShareX: an invalid shape is emptied before the hover check
                self._update_hover()
                hover = self.hover_shape
                if hover is not None and hover is not s and hover.is_valid(self.minimum_size) and was_creating:
                    s.rect = hover.rect
                    if isinstance(s, S.DrawingLine):
                        s.points[0], s.points[-1] = s.start, s.end
                        s.auto_position_center_points()
                else:
                    if was_creating:
                        self._discard_shape(s)
                    else:
                        s.rect = saved_rect
                    s = None
            if s is not None:
                if self.options.quick_crop and self.is_current_tool_region and isinstance(s, S.RegionShape):
                    return self._finish(self.region_result())
                if was_creating:
                    self._on_created(s)
                    if self.options.switch_to_selection_tool_after_drawing and s.is_handled_by_select_tool:
                        self._current_tool = ShapeType.ToolSelect
                elif was_moving:
                    self._select(s)
                    if moved and s.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
                        self._on_image_modified()
        self._update_hover()
        self._changed()
        return None

    def _shape_valid(self, s: S.Shape) -> bool:
        if isinstance(s, S.ToolCutOut):
            return s.is_valid_cut(self.minimum_size, self.canvas_rect)
        return s.is_valid(self.minimum_size)

    def _on_created(self, s: S.Shape) -> None:
        if isinstance(s, S.DrawingImageScreen):
            x, y, w, h = R._crop_box(s.rect.intersect(self.canvas_rect), self.canvas.size)
            if w <= 0 or h <= 0:
                self._discard_shape(s)
                return
            s.rect = S.Rect(x, y, w, h)
            s.image = self.canvas.crop((x, y, x + w, y + h))
        s.on_created()
        self._select(s)
        if s.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
            self._on_image_modified()

    # ------------------------------------------------------------------ dialogs (text, files)

    def _ask_text(self, s: S.DrawingTextBackground, initial: str, creating: bool) -> None:
        cb = self.callbacks.request_text
        answer = cb(s, initial) if cb is not None else initial
        if answer is PENDING:
            self._pending_dialog_shape = s
            self._pending_creating = creating
            return
        self._apply_text(s, answer, creating)

    def _apply_text(self, s: S.DrawingTextBackground, text: str | None, creating: bool) -> None:
        if text is None:
            if creating:
                self._discard_shape(s)
            return
        if not creating:
            self._checkpoint()
        s.text = text
        s.save_config(self.annotation)
        if creating:
            self._shape_created(s)
        else:
            self._on_image_modified()

    def set_text(self, shape: S.Shape, text: str | None) -> None:
        """Answer a pending ``request_text`` (None = cancelled), or set a text shape's text."""
        if not isinstance(shape, S.DrawingTextBackground):
            raise TypeError("not a text shape")
        if shape is self._pending_dialog_shape:
            self._pending_dialog_shape = None
            self._apply_text(shape, text, self._pending_creating)
        else:
            self._apply_text(shape, text, False)

    def _ask_file(self, s: S.DrawingImage, cb, creating: bool) -> None:
        answer = cb() if cb is not None else None
        if answer is PENDING:
            self._pending_dialog_shape = s
            self._pending_creating = creating
            return
        self._apply_file(s, answer, creating)

    def _apply_file(self, s: S.DrawingImage, path, creating: bool) -> None:
        ok = False
        if path:
            if not creating:
                self._checkpoint()
            if isinstance(s, S.DrawingSticker):
                ok = self._load_sticker(s, path)
            else:
                ok = self._load_image_file(s, path, creating)
            if not ok and not creating:
                self._drop_last_checkpoint()
        if creating:
            if ok:
                self._shape_created(s)
            else:
                self._discard_shape(s)
        elif ok:
            self._on_image_modified()

    def set_shape_image_file(self, shape: S.Shape, path: str | None) -> None:
        """Answer a pending ``request_image_file`` (None = cancelled) / replace an image shape's file."""
        self._answer_file(shape, path)

    def set_shape_sticker(self, shape: S.Shape, path: str | None) -> None:
        """Answer a pending ``request_sticker`` (None = cancelled) / replace a sticker."""
        self._answer_file(shape, path)

    def _answer_file(self, shape, path) -> None:
        if not isinstance(shape, S.DrawingImage):
            raise TypeError("not an image shape")
        if shape is self._pending_dialog_shape:
            self._pending_dialog_shape = None
            self._apply_file(shape, path, self._pending_creating)
        else:
            self._apply_file(shape, path, False)

    def _load_image_file(self, s: S.DrawingImage, path, center: bool) -> bool:
        try:
            img = imaging.load(path)
        except (OSError, ValueError):
            return False
        self.annotation.last_image_file_path = str(path)
        s.file_path = str(path)
        if not center:
            # re-opening keeps the top-left corner
            s.set_image(img, False)
        else:
            s.set_image(img, True)
        return True

    def _load_sticker(self, s: S.DrawingSticker, path) -> bool:
        try:
            img = ST.load_sticker(path, self.annotation.sticker_size)
        except (OSError, ValueError):
            return False
        self.annotation.last_sticker_path = str(path)
        s.file_path = str(path)
        r = s.rect
        cx, cy = (r.x, r.y) if r.w <= 1 and r.h <= 1 else r.center
        s.rect = S.Rect(cx, cy, 1, 1)
        s.set_image(img, True)
        return True

    def _on_double_clicked(self, s: S.Shape) -> None:
        if isinstance(s, S.DrawingTextBackground):
            self._ask_text(s, s.text, creating=False)
        elif isinstance(s, S.DrawingSticker):
            self._ask_file(s, self.callbacks.request_sticker, creating=False)
        elif isinstance(s, S.DrawingImage) and not isinstance(s, (S.DrawingImageScreen, S.DrawingCursor)):
            self._ask_file(s, self.callbacks.request_image_file, creating=False)

    # ------------------------------------------------------------------ actions / keys

    def run_action(self, action: RegionCaptureAction) -> CaptureResult | None:
        """ShareX RunAction (right / middle / X1 / X2 click)."""
        action = RegionCaptureAction(action)
        if action == RegionCaptureAction.CancelCapture:
            return self._cancel()
        if action == RegionCaptureAction.RemoveShapeCancelCapture:
            target = self.get_intersect_shape(*self.pointer) if self.pointer else None
            if target is not None:
                self.delete_shape(target)
                return None
            return self._cancel()
        if action == RegionCaptureAction.RemoveShape:
            target = self.get_intersect_shape(*self.pointer) if self.pointer else None
            self.delete_shape(target)
            return None
        if action == RegionCaptureAction.SwapToolType:
            self.swap_tool_type()
            return None
        if action == RegionCaptureAction.CaptureFullscreen:
            return self.close(RegionResult.Fullscreen)
        if action == RegionCaptureAction.CaptureActiveMonitor:
            return self.close(RegionResult.ActiveMonitor)
        if action == RegionCaptureAction.CaptureLastRegion:
            if ShapeManager.last_region is not None:
                return self.close(RegionResult.LastRegion)
            return None
        if action == RegionCaptureAction.OpenOptionsMenu:
            return None  # the UI opens its options menu from toolbar_model().options_menu
        return None

    def _cancel(self) -> CaptureResult | None:
        if self.is_editor_mode:
            if self._confirm_exit():
                return self._finish(CaptureResult(RegionResult.AnnotateContinueTask))
            return None
        return self._finish(CaptureResult(RegionResult.Close))

    def _confirm_exit(self) -> bool:
        if not self.is_image_modified or self.callbacks.confirm_exit is None:
            return True
        return bool(self.callbacks.confirm_exit())

    def swap_tool_type(self) -> None:
        """Annotation mode: toggle between the last region tool and the last drawing tool."""
        if self.mode != RegionCaptureMode.Annotation:
            return
        if self.is_current_tool_region:
            self.current_tool = self.options.last_annotation_tool
        else:
            self.current_tool = self.options.last_region_tool

    def handle_escape(self) -> bool:
        """ShareX HandleEscape: deselect, then switch to the select tool; False = close."""
        if self.is_current_tool_region:
            return False
        if self._current_shape is not None:
            self._clear_tools()
            self._deselect()
        if self._current_tool != ShapeType.ToolSelect:
            self.current_tool = ShapeType.ToolSelect
            return True
        self._changed()
        return False

    def key(self, name: str, mods: int = Mod.NONE) -> CaptureResult | None:
        """Key pressed. ``name`` is a GDK key name ("Escape", "Return", "Delete", "Left", "r",
        "KP_1", "Page_Up", "Home", "Tab", "space", "grave", "0"...)."""
        if self.result is not None:
            return self.result
        mods = Mod(mods)
        self.mods = mods
        k = _key_norm(name)
        if k == "Escape":
            if self._pending_dialog_shape is not None:
                return None
            if self.cancel_tool():
                return None
            if self.handle_escape():
                return None
            if not self.is_editor_mode:
                return self._finish(CaptureResult(RegionResult.Close))
            if self._confirm_exit():
                res = RegionResult.AnnotateCancelTask if self.mode == RegionCaptureMode.TaskEditor \
                    else RegionResult.Close
                return self._finish(CaptureResult(res))
            return None
        if not self._key_allowed and (time.monotonic() - self._started) * 1000 < self.options.input_delay:
            return None
        self._key_allowed = True
        none = mods == Mod.NONE
        ctrl = mods == Mod.CTRL
        if k in ("Return", "KP_Enter") and none:
            if self.tool_shapes:
                self.confirm_tool()
                return None
            if self.is_editor_mode:
                return self._finish(CaptureResult(RegionResult.AnnotateRunAfterCaptureTasks))
            if self.is_current_tool_region:
                self.start_region_selection()
                self.end_region_selection()
                if self.result is not None:
                    return self.result
            return self._finish(self.region_result())
        if k == "space" and none:
            if self.mode == RegionCaptureMode.TaskEditor:
                return self._finish(CaptureResult(RegionResult.AnnotateContinueTask))
            if not self.is_editor_mode:
                return self.close(RegionResult.Fullscreen)
            return None
        if k == "grave" and none and not self.is_editor_mode:
            return self.close(RegionResult.ActiveMonitor)
        if self.is_editor_mode:
            if k in ("plus", "equal", "KP_Add") and ctrl and self.callbacks.zoom:
                self.callbacks.zoom(1)
            elif k in ("minus", "KP_Subtract") and ctrl and self.callbacks.zoom:
                self.callbacks.zoom(-1)
        elif none and len(k) == 1 and k.isdigit():
            index = int(k) or 10
            return self.capture_monitor(index - 1)
        if k == "c" and ctrl:
            if self.copy_current_shape():
                return None
            if self.is_editor_mode:
                if self.callbacks.copy_image:
                    self.callbacks.copy_image(self.render_output())
            elif self.callbacks.copy_text:
                cs = self._current_shape
                if cs is not None and cs.is_valid(self.minimum_size):
                    self.callbacks.copy_text(self.area_text(cs.rect))
                else:
                    self.callbacks.copy_text(self.info_text())
            return None
        if k == "Insert" and none:
            if self.is_creating:
                return self.end_region_selection()
            return self.start_region_selection()
        if k == "Delete":
            if mods == Mod.SHIFT:
                self.delete_all_shapes()
            elif none:
                self.delete_current_shape()
                if self.is_creating:
                    return self.end_region_selection()
            return None
        if k == "z" and ctrl:
            self.undo()
            return None
        if (k == "y" and ctrl) or (k == "z" and mods == Mod.CTRL | Mod.SHIFT):
            self.redo()
            return None
        if k in ("Left", "Right", "Up", "Down"):
            return self._nudge(k, mods)
        if self.is_creating:
            return None
        if self.mode == RegionCaptureMode.Annotation and none:
            if k == "Tab":
                self.swap_tool_type()
                return None
            if k == "KP_0":
                self.current_tool = ShapeType.RegionRectangle
                return None
        if self.is_annotation_mode:
            if none and k in _TOOL_KEYS:
                self.current_tool = _TOOL_KEYS[k]
                return None
            if ctrl and k == "d":
                self.duplicate_current_shape(True)
            elif ctrl and k == "v":
                self.paste(True)
            elif none and k == "Home":
                self.move_shape_top()
            elif none and k == "End":
                self.move_shape_bottom()
            elif none and k == "Page_Up":
                self.move_shape_up()
            elif none and k == "Page_Down":
                self.move_shape_down()
        if self.is_editor_mode:
            cb = self.callbacks
            if none and k == "c":
                self.current_tool = ShapeType.ToolCrop
            elif none and k == "x":
                self.current_tool = ShapeType.ToolCutOut
            elif ctrl and k == "s" and cb.save_image:
                cb.save_image(self.render_output())
            elif mods == Mod.CTRL | Mod.SHIFT and k == "s" and cb.save_image_as:
                cb.save_image_as(self.render_output())
            elif ctrl and k == "u" and cb.upload_image:
                cb.upload_image(self.render_output())
            elif ctrl and k == "p" and cb.print_image:
                cb.print_image(self.render_output())
        return None

    def _nudge(self, k: str, mods: Mod) -> None:
        speed = MOVE_SPEED_MAX if mods & Mod.SHIFT else MOVE_SPEED_MIN
        dx = {"Left": -speed, "Right": speed}.get(k, 0)
        dy = {"Up": -speed, "Down": speed}.get(k, 0)
        s = self._current_shape
        if s is None or self.is_creating:
            if self.pointer is not None:
                px, py = self.pointer
                self.motion(px + dx, py + dy, mods & ~Mod.SHIFT)
            if self.callbacks.move_pointer:
                self.callbacks.move_pointer(dx, dy)
            return
        self._checkpoint()
        if mods & Mod.CTRL:
            s.resize(dx, dy, True)
        elif mods & Mod.ALT:
            s.resize(dx, dy, False)
        else:
            s.move(dx, dy)
        self._limit_to_canvas(s)
        if s.category in (S.ShapeCategory.Drawing, S.ShapeCategory.Effect):
            self._on_image_modified()
        self._changed()

    # ------------------------------------------------------------------ info / magnifier

    def color_at(self, x: int, y: int) -> str | None:
        """Canvas colour "#AARRGGBB" at a pixel, None outside the canvas."""
        if 0 <= x < self.canvas.width and 0 <= y < self.canvas.height:
            r, g, b, a = self.canvas.getpixel((x, y))
            return R.color_to_str(r, g, b, a)
        return None

    def screen_position(self) -> tuple[int, int]:
        p = self.pointer or (0, 0)
        return (math.floor(p[0]) + self.screen_origin[0], math.floor(p[1]) + self.screen_origin[1])

    def info_text(self) -> str:
        """The text next to the cursor (ShareX GetInfoText)."""
        p = self.pointer or (0, 0)
        if self.is_editor_mode:
            return f"X: {_round(p[0])} Y: {_round(p[1])}"
        pos = self.screen_position()
        if self.mode == RegionCaptureMode.ScreenColorPicker or self.options.use_custom_info_text:
            color = self.color_at(math.floor(p[0]), math.floor(p[1])) or "#00000000"
            if self.mode == RegionCaptureMode.ScreenColorPicker:
                if self.options.screen_color_picker_info_text:
                    return parse_pixel_info(self.options.screen_color_picker_info_text, color, pos)
            elif self.options.custom_info_text:
                return parse_pixel_info(self.options.custom_info_text, color, pos)
            r, g, b, _a = R.parse_color(color)
            return f"RGB: {r}, {g}, {b}\nHex: {r:02X}{g:02X}{b:02X}\nX: {pos[0]} Y: {pos[1]}"
        return f"X: {pos[0]} Y: {pos[1]}"

    def area_text(self, rect: S.Rect) -> str:
        """Position/size label of a region (ShareX GetAreaText; three lines in Ruler mode)."""
        if self.mode == RegionCaptureMode.Ruler:
            info = ruler_info(rect)
            return (f"X: {_fmt(rect.x)} | Y: {_fmt(rect.y)} | Right: {_fmt(info['right'])} | "
                    f"Bottom: {_fmt(info['bottom'])}\n"
                    f"Width: {_fmt(rect.w)} px | Height: {_fmt(rect.h)} px | Area: {_fmt(info['area'])} px | "
                    f"Perimeter: {_fmt(info['perimeter'])} px\n"
                    f"Distance: {info['distance']:.2f} px | Angle: {info['angle']:.2f}°")
        x, y, w, h = rect.rounded()
        return f"X: {x} Y: {y} W: {w} H: {h}"

    def magnifier(self, x: float | None = None, y: float | None = None) -> MagnifierData:
        """Pixel grid around (x, y) (default: the pointer)."""
        if x is None or y is None:
            x, y = self.pointer or (0, 0)
        o = self.options
        n = max(1, min(101, o.magnifier_pixel_count | 1))
        ps = max(1, min(1000, o.magnifier_pixel_size))
        ca = self.area
        if n * ps > ca.w or n * ps > ca.h:
            n, ps = 15, 10
        sx = _round(x - n // 2)
        sy = _round(y - n // 2)
        bg = Image.new("RGBA", (n, n), CANVAS_BACKGROUND)
        part = self.canvas.crop((sx, sy, sx + n, sy + n))
        bg.alpha_composite(part)
        inside = (sx >= 0 and sy >= 0 and sx + n <= self.canvas.width and sy + n <= self.canvas.height)
        pixels = part if inside else bg
        cx, cy = math.floor(x), math.floor(y)
        return MagnifierData(pixels, n, ps, self.color_at(cx, cy) or R.color_to_str(*CANVAS_BACKGROUND))

    # ------------------------------------------------------------------ results

    def _region_path(self, cr: cairo.Context, s: S.Shape, size_offset: float = 0) -> None:
        r = s.rect
        if size_offset:
            r = S.Rect(r.x, r.y, r.w + size_offset, r.h + size_offset)
        if isinstance(s, S.RegionEllipse):
            R.path_ellipse(cr, r.x, r.y, r.w, r.h)
        elif isinstance(s, S.RegionFreehand):
            if len(s.points) > 2:
                R._poly(cr, s.points)
            elif len(s.points) == 2:
                cr.move_to(*s.points[0])
                cr.line_to(*s.points[1])
        else:
            R.path_rounded_rect(cr, r.x, r.y, r.w, r.h, getattr(s, "corner_radius", 0))

    def region_bounds(self) -> S.Rect:
        b = S.Rect()
        for s in self.valid_regions:
            b = b.union(s.path_bounds() if isinstance(s, S.RegionFreehand) else s.rect)
        return b

    def region_result(self) -> CaptureResult:
        """Union of the valid region shapes: bounding rectangle (clipped to the canvas) and a mask
        when the area is not a single plain rectangle."""
        regions = self.valid_regions
        if not regions:
            return CaptureResult(RegionResult.Region)
        x, y, w, h = self.region_bounds().rounded()
        bounds = S.Rect(x, y, w, h).intersect(self.canvas_rect)
        x, y, w, h = bounds.rounded()
        if w <= 0 or h <= 0:
            return CaptureResult(RegionResult.Region)
        plain = (len(regions) == 1 and isinstance(regions[0], S.RegionRectangle)
                 and regions[0].corner_radius <= 0)
        mask = None
        if not plain:
            surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
            cr = cairo.Context(surf)
            cr.translate(-x, -y)
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
            for s in regions:
                self._region_path(cr, s)
            cr.set_source_rgba(0, 0, 0, 1)
            cr.fill()
            surf.flush()
            stride = surf.get_stride()
            arr = np.ndarray((h, stride), dtype=np.uint8, buffer=surf.get_data())[:, :w].copy()
            mask = Image.fromarray(arr, "L")
        return CaptureResult(RegionResult.Region, rect=(x, y, w, h), mask=mask)

    def render_output(self) -> Image.Image:
        """The full canvas with effects and drawings applied (regions/tools not drawn)."""
        return R.render_output(self.canvas, self.shapes, canvas_version=self.canvas_version)

    def result_image(self, result: CaptureResult | None = None) -> Image.Image | None:
        """ShareX GetResultImage for ``result`` (default: the session result)."""
        res = result or self.result
        if self.is_editor_mode:
            return self.render_output()
        if res is None:
            return None
        if res.result in (RegionResult.Region, RegionResult.LastRegion):
            if res.rect is None:
                return None
            x, y, w, h = res.rect
            part = self.canvas.crop((x, y, x + w, y + h))
            if res.mask is not None:
                alpha = np.asarray(part.getchannel("A"), dtype=np.uint16)
                m = np.asarray(res.mask, dtype=np.uint16)
                part.putalpha(Image.fromarray(((alpha * m + 127) // 255).astype(np.uint8), "L"))
            return R.render_output(part, self.shapes, offset=(x, y), source=self.canvas,
                                   canvas_version=self.canvas_version)
        if res.result in (RegionResult.Fullscreen,):
            return self.render_output()
        if res.result in (RegionResult.Monitor, RegionResult.ActiveMonitor) and res.rect is not None:
            x, y, w, h = res.rect
            return self.render_output().crop((x, y, x + w, y + h))
        return None

    # ------------------------------------------------------------------ canvas / image ops

    def _set_canvas(self, img: Image.Image, version: int | None = None) -> None:
        self.canvas = img.convert("RGBA") if img.mode != "RGBA" else img
        self.canvas_version = version if version is not None else self.canvas_version + 1
        self._canvas_surface = None
        self._dimmed_surface = None
        self.effect_cache.clear()

    def update_canvas(self, img: Image.Image) -> None:
        """Replace the canvas (undoable)."""
        self._checkpoint()
        self._set_canvas(img)
        self._on_image_modified()

    def crop_image(self, rect: S.Rect, only_if_size_different: bool = False) -> Image.Image | None:
        x, y, w, h = R._crop_box(rect, self.canvas.size)
        if w <= 0 or h <= 0:
            return None
        if only_if_size_different and (w, h) == self.canvas.size:
            return None
        return self.canvas.crop((x, y, x + w, y + h))

    def crop_area(self, rect: S.Rect | tuple) -> bool:
        """Crop the canvas to ``rect``; shapes keep their place on the image."""
        rect = rect if isinstance(rect, S.Rect) else S.Rect(*rect)
        img = self.crop_image(rect, True)
        if img is None:
            return False
        x, y, _w, _h = R._crop_box(rect, self.canvas.size)
        self._checkpoint()
        self.move_all(-x, -y)
        self._set_canvas(img)
        self._on_image_modified()
        return True

    def cut_out(self, rect: S.Rect | tuple) -> bool:
        """Remove the columns (wider than tall) or rows covered by ``rect`` and close the gap,
        with the configured cut-out edge effect; shapes collapse with the image."""
        rect = rect if isinstance(rect, S.Rect) else S.Rect(*rect)
        horizontal = rect.w > rect.h
        x, y, w, h = R._crop_box(rect, self.canvas.size)
        a = self.annotation
        if horizontal and w > 0:
            self._checkpoint()
            self._collapse(rect.x, rect.w, True)
            img = R.cut_out_middle(self.canvas, True, x, w, a.cut_out_effect_type, a.cut_out_effect_size,
                                   a.cut_out_background_color)
        elif not horizontal and h > 0:
            self._checkpoint()
            self._collapse(rect.y, rect.h, False)
            img = R.cut_out_middle(self.canvas, False, y, h, a.cut_out_effect_type, a.cut_out_effect_size,
                                   a.cut_out_background_color)
        else:
            return False
        self._set_canvas(img)
        self._on_image_modified()
        return True

    def _collapse(self, start: float, size: float, horizontal: bool) -> None:
        """ShareX CollapseAllHorizontal / CollapseAllVertical; point shapes collapse per point."""
        end = start + size
        if size <= 0:
            return

        def cp(v: float) -> float:
            return v if v < start else (start if v < end else v - size)

        to_delete = []
        for s in self.shapes:
            r = s.rect
            lo, hi = (r.x, r.right) if horizontal else (r.y, r.bottom)
            if start <= lo and hi <= end:
                to_delete.append(s)
                continue
            pts = getattr(s, "points", None)
            if pts is not None:
                s.points = [(cp(px), py) if horizontal else (px, cp(py)) for px, py in pts]
                if isinstance(s, S.DrawingLine):
                    s.recalc_rect()
                else:
                    s.rect = S.points_rect(s.points)
                continue
            if lo < start:
                if hi <= start:
                    new = (lo, hi - lo)
                elif hi < end:
                    new = (lo, start - lo)
                else:
                    new = (lo, hi - lo - size)
            elif lo < end:
                new = (start, hi - end)
            else:
                new = (lo - size, hi - lo)
            s.rect = S.Rect(new[0], r.y, new[1], r.h) if horizontal else S.Rect(r.x, new[0], r.w, new[1])
            for attr in ("tail_position",):
                tp = getattr(s, attr, None)
                if tp is not None:
                    setattr(s, attr, (cp(tp[0]), tp[1]) if horizontal else (tp[0], cp(tp[1])))
        for s in to_delete:
            self.shapes.remove(s)
            if s is self._current_shape:
                self._deselect()

    def resize_image(self, width: int, height: int, interpolation: str | None = None) -> bool:
        """Image size...: scale the canvas (shapes are not scaled, as in ShareX)."""
        if (width, height) == self.canvas.size or width <= 0 or height <= 0:
            return False
        mode = interpolation or self.options.image_editor_resize_interpolation
        self.options.image_editor_resize_interpolation = str(mode)
        res = {"NearestNeighbor": Image.Resampling.NEAREST, "Bilinear": Image.Resampling.BILINEAR,
               "HighQualityBilinear": Image.Resampling.BILINEAR, "Bicubic": Image.Resampling.BICUBIC,
               "HighQualityBicubic": Image.Resampling.LANCZOS}.get(str(mode), Image.Resampling.BICUBIC)
        self.update_canvas(self.canvas.resize((int(width), int(height)), res))
        return True

    def resize_canvas(self, left: int, top: int, right: int, bottom: int, color: str | None = None) -> bool:
        """Canvas size...: add (or with negative values remove) margins; shapes move with the
        image content."""
        color = color if color is not None else self.options.editor_canvas_color
        self.options.editor_canvas_color = color
        w = self.canvas.width + left + right
        h = self.canvas.height + top + bottom
        if w <= 0 or h <= 0 or (left, top, right, bottom) == (0, 0, 0, 0):
            return False
        img = Image.new("RGBA", (w, h), R.parse_color(color))
        img.paste(self.canvas, (left, top))
        self._checkpoint()
        self.move_all(left, top)
        self._set_canvas(img)
        self._on_image_modified()
        return True

    def rotate(self, degrees: int) -> None:
        """Rotate the canvas clockwise by 90 / 180 / 270 degrees."""
        ops = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
        if degrees % 360 not in ops:
            raise ValueError("rotation must be 90, 180 or 270")
        self.update_canvas(self.canvas.transpose(ops[degrees % 360]))

    def flip(self, horizontal: bool) -> None:
        self.update_canvas(self.canvas.transpose(Image.Transpose.FLIP_LEFT_RIGHT if horizontal
                                                 else Image.Transpose.FLIP_TOP_BOTTOM))

    def auto_crop_image(self, apply: bool = False) -> tuple[int, int, int, int] | None:
        """Find the rectangle without uniform borders (in the rendered image). ShareX opens it as
        a crop tool to confirm; ``apply=True`` crops right away."""
        crop = R.find_auto_crop_rect(self.render_output())
        if crop == (0, 0, self.canvas.width, self.canvas.height) or crop[2] <= 0 or crop[3] <= 0:
            return None
        if apply:
            self.crop_area(S.Rect(*crop))
            return crop
        self._current_tool = ShapeType.ToolCrop
        self._clear_tools()
        tool = self.create_shape(ShapeType.ToolCrop)
        tool.rect = S.Rect(*crop)
        tool.on_created()
        self.shapes.append(tool)
        self._select(tool)
        return crop

    def add_crop_tool(self) -> S.Shape:
        """Crop image...: a crop tool covering the whole canvas to adjust and confirm."""
        self._current_tool = ShapeType.ToolCrop
        self._clear_tools()
        tool = self.create_shape(ShapeType.ToolCrop)
        tool.rect = self.canvas_rect
        tool.on_created()
        self.shapes.append(tool)
        self._select(tool)
        return tool

    def insert_image(self, img: Image.Image, method: ImageInsertMethod | None = None) -> S.Shape | None:
        """Insert an image as an Image shape (ShareX InsertImage)."""
        if img is None:
            return None
        method = ImageInsertMethod(method or self.options.image_insert_method)
        if not self.is_editor_mode:
            method = ImageInsertMethod.Center
        c = self.canvas_rect
        expanded = method in (ImageInsertMethod.CanvasExpandDown, ImageInsertMethod.CanvasExpandRight)
        if method == ImageInsertMethod.CanvasExpandDown:
            self._expand_canvas(0, 0, max(0, img.width - self.canvas.width), img.height)
            pos, center = (c.x, c.bottom), False
        elif method == ImageInsertMethod.CanvasExpandRight:
            self._expand_canvas(0, 0, img.width, max(0, img.height - self.canvas.height))
            pos, center = (c.right, c.y), False
        else:
            pos, center = c.center, True
        if self.is_annotation_mode:
            self._current_tool = ShapeType.DrawingImage
        shape = self.create_shape(ShapeType.DrawingImage)
        shape.rect = S.Rect(pos[0], pos[1], 1, 1)
        shape.set_image(img, center)
        shape.on_created()
        self._add_shape(shape, checkpoint=not expanded)  # one undo step for expand + insert
        self._select(shape)
        self._on_image_modified()
        return shape

    def _expand_canvas(self, left: int, top: int, right: int, bottom: int) -> None:
        img = Image.new("RGBA", (self.canvas.width + left + right, self.canvas.height + top + bottom),
                        R.parse_color(self.options.editor_canvas_color))
        img.paste(self.canvas, (left, top))
        self._checkpoint()
        self.move_all(left, top)
        self._set_canvas(img)

    def insert_image_file(self, path, method: ImageInsertMethod | None = None) -> S.Shape | None:
        return self.insert_image(imaging.load(path), method)

    def insert_image_from_screen(self, method: ImageInsertMethod | None = None) -> S.Shape | None:
        cb = self.callbacks.capture_screen_image
        img = cb() if cb is not None else None
        return self.insert_image(img, method) if img is not None else None

    def add_image_effects(self) -> bool:
        cb = self.callbacks.apply_effects
        if cb is None:
            return False
        img = cb(self.canvas)
        if img is None:
            return False
        self.update_canvas(img)
        return True

    def new_image(self, width: int | None = None, height: int | None = None, color: str | None = None,
                  transparent: bool | None = None) -> None:
        """New image...: replace the canvas with a blank image and remove all shapes."""
        o = self.options
        width = width or o.editor_new_image_size.width
        height = height or o.editor_new_image_size.height
        o.editor_new_image_size = Size(int(width), int(height))
        if transparent is not None:
            o.editor_new_image_transparent = transparent
        if color is not None:
            o.editor_new_image_background_color = color
        fill = (0, 0, 0, 0) if o.editor_new_image_transparent else R.parse_color(o.editor_new_image_background_color)
        self._load_new_canvas(Image.new("RGBA", (int(width), int(height)), fill))

    def load_image(self, image: Image.Image | str | Path) -> None:
        """Open image file...: replace the canvas and remove all shapes."""
        img = imaging.load(image) if not isinstance(image, Image.Image) else image
        self._load_new_canvas(img)

    def _load_new_canvas(self, img: Image.Image) -> None:
        self._checkpoint()
        self.shapes.clear()
        self._deselect()
        self._set_canvas(img)
        self._on_image_modified()

    # ------------------------------------------------------------------ drawing

    @property
    def _dimming_active(self) -> bool:
        o = self.options
        return not self.is_editor_mode and o.use_dimming and o.background_dim_strength > 0

    def _dim_rgba(self, c: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        a = round(255 * self.options.background_dim_strength / 100) / 255
        return (round(c[0] * (1 - a)), round(c[1] * (1 - a)), round(c[2] * (1 - a)), c[3])

    def canvas_surface(self) -> cairo.ImageSurface:
        if self._canvas_surface is None:
            self._canvas_surface = R.pil_to_surface(self.canvas)
        return self._canvas_surface

    def _dimmed(self) -> cairo.ImageSurface:
        if self._dimmed_surface is None:
            surf = R.new_surface(self.canvas.width, self.canvas.height)
            cr = cairo.Context(surf)
            cr.set_source_surface(self.canvas_surface(), 0, 0)
            cr.paint()
            alpha = round(255 * self.options.background_dim_strength / 100)
            cr.set_source_rgba(0, 0, 0, alpha / 255)
            cr.paint()
            self._dimmed_surface = surf
        return self._dimmed_surface

    def render_context(self) -> R.RenderContext:
        overlay = set()
        if (self.is_moving or self.is_resizing or self.is_creating) and self._current_shape is not None:
            overlay.add(self._current_shape.uid)
        return R.RenderContext(canvas=self.canvas, canvas_surface=self.canvas_surface(),
                               canvas_version=self.canvas_version, dimmed=self._dimming_active,
                               current_tool=self._current_tool, minimum_size=self.minimum_size,
                               effect_overlay_uids=overlay, effect_cache=self.effect_cache)

    def draw(self, cr: cairo.Context, view_scale: float = 1.0, view_offset: tuple[float, float] = (0, 0),
             *, time_s: float | None = None, view_size: tuple[float, float] | None = None) -> None:
        """Paint the canvas, shapes and overlays: image pixel (x, y) lands on
        ``view_offset + (x, y) * view_scale``. ``time_s`` animates the dashed borders;
        ``view_size`` (widget px) bounds the crosshair and cursor info."""
        self.view_scale = view_scale or 1.0
        sc = self.view_scale
        cr.save()
        cr.translate(*view_offset)
        cr.scale(sc, sc)
        dash_offset = -(time_s or 0.0) * 15 if self.options.enable_animations else 0.0
        self._draw_background(cr)
        ctx = self.render_context()
        o = self.options

        if self.is_creating and self.mods & Mod.ALT and self._current_shape is not None \
                and not isinstance(self._current_shape, (S.RegionFreehand, S.DrawingFreehand)):
            s = self._current_shape
            cr.save()
            cr.translate(0.5, 0.5)
            for size in o.snap_sizes:
                end = self._snap_end(s.start, s.end, size)
                rr = S.create_rect(s.start, end)
                cr.rectangle(rr.x, rr.y, rr.w - 1, rr.h - 1)
            cr.set_source_rgba(1, 0, 0, 200 / 255)
            cr.set_line_width(1 / sc)
            cr.stroke()
            cr.restore()

        areas = self.valid_regions
        if areas:
            if self._dimming_active:
                cr.save()
                for s in areas:
                    self._region_path(cr, s)
                cr.clip()
                cr.set_source_surface(self.canvas_surface(), 0, 0)
                cr.get_source().set_filter(cairo.FILTER_NEAREST)
                cr.paint()
                cr.restore()
            cr.save()
            cr.translate(0.5, 0.5)
            for s in areas:
                self._region_path(cr, s, -1)
            self._stroke_region_pens(cr, sc, None)
            cr.restore()

        for s in self.effect_shapes:
            R.draw_shape(cr, s, ctx)
        for s in self.drawing_shapes:
            R.draw_shape(cr, s, ctx)
            if isinstance(s, S.DrawingCursor) and self._current_tool == ShapeType.DrawingCursor:
                self._draw_region_area(cr, S.Rect(*s.rect.rounded()), sc, None)
            if isinstance(s, S.DrawingImageScreen) and s.image is None and s.is_valid(self.minimum_size):
                self._draw_region_area(cr, s.rect.intersect(self.canvas_rect), sc, dash_offset)
        for t in self.tool_shapes:
            if isinstance(t, S.ToolCrop) and t.is_valid(self.minimum_size):
                self._draw_region_area(cr, t.rect, sc, dash_offset)
                if o.show_info:
                    self._draw_area_text(cr, self.area_text(t.rect), t.rect, sc)
                cx, cy = t.rect.center
                cr.save()
                cr.translate(0.5, 0.5)
                cr.move_to(cx - 10, cy)
                cr.line_to(cx + 10, cy)
                cr.move_to(cx, cy - 10)
                cr.line_to(cx, cy + 10)
                cr.set_source_rgb(0, 0, 0)
                cr.set_line_width(1 / sc)
                cr.stroke()
                cr.restore()
            elif isinstance(t, S.ToolCutOut):
                cut = t.cut_out_rect(self.minimum_size, self.canvas_rect)
                if not cut.is_empty:
                    cr.save()
                    cr.rectangle(cut.x, cut.y, cut.w, cut.h)
                    cr.set_source(R.checker_pattern(1, (211, 211, 211, 128), (128, 128, 128, 128)))
                    cr.fill()
                    cr.restore()

        hover = self.hover_shape
        if hover is not None and hover.is_valid(self.minimum_size):
            cr.save()
            cr.translate(0.5, 0.5)
            self._region_path(cr, hover, -1)
            self._stroke_region_pens(cr, sc, dash_offset)
            cr.restore()

        cs = self._current_shape
        if self.is_current_tool_region and cs is not None and cs.is_valid(self.minimum_size):
            r = cs.rect
            if self.mode == RegionCaptureMode.Ruler:
                cr.save()
                cr.rectangle(r.x, r.y, r.w, r.h)
                cr.set_source_rgba(1, 1, 1, 50 / 255)
                cr.fill()
                cr.translate(0.5, 0.5)
                self._ruler_ticks(cr, r, 5, 10)
                self._ruler_ticks(cr, r, 15, 100)
                cx, cy = r.center
                cr.move_to(cx - 10, cy)
                cr.line_to(cx + 10, cy)
                cr.move_to(cx, cy - 10)
                cr.line_to(cx, cy + 10)
                cr.set_source_rgb(0, 0, 0)
                cr.set_line_width(1 / sc)
                cr.stroke()
                cr.restore()
            self._draw_region_area(cr, r, sc, dash_offset)

        if o.show_info:
            infos = list(areas)
            if self.is_current_tool_region and hover is not None and hover.is_valid(self.minimum_size) \
                    and all(a.rect != hover.rect for a in infos):
                infos.append(hover)
            for s in infos:
                if not s.rect.is_empty:
                    self._draw_area_text(cr, self.area_text(s.rect), s.rect, sc)

        self._draw_nodes(cr, sc)
        self._draw_buttons(cr, sc)
        cr.restore()

        cursor = self.pointer is not None and self.pointer_inside
        if cursor and (o.show_magnifier or o.show_info) and self.result is None:
            self._draw_cursor_graphics(cr, sc, view_offset, view_size)
        if o.show_crosshair and cursor:
            self._draw_crosshair(cr, sc, view_offset, view_size, dash_offset)

    def _snap_end(self, start: S.Point, cur: S.Point, size: Size) -> S.Point:
        sx, sy = start
        nx = sx + size.width - 1 if cur[0] > sx else sx - size.width + 1
        ny = sy + size.height - 1 if cur[1] > sy else sy - size.height + 1
        return (nx, ny)

    def _draw_background(self, cr: cairo.Context) -> None:
        w, h = self.canvas.size
        if self.is_editor_mode:
            cr.save()
            cr.rectangle(0, 0, w, h)
            cr.set_source(R.checker_pattern(10, (255, 255, 255, 255), (230, 230, 230, 255)))
            cr.fill()
            cr.restore()
        cr.save()
        cr.rectangle(0, 0, w, h)
        cr.clip()
        cr.set_source_surface(self._dimmed() if self._dimming_active else self.canvas_surface(), 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST if self.view_scale >= 1 else cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

    def _stroke_region_pens(self, cr: cairo.Context, sc: float, dash_offset: float | None) -> None:
        """Black 1 px pen, then a white [5, 5] dashed pen (animated when ``dash_offset``)."""
        cr.set_line_width(1 / sc)
        cr.set_dash([], 0)
        cr.set_source_rgb(0, 0, 0)
        cr.stroke_preserve()
        cr.set_dash([5 / sc, 5 / sc], (dash_offset or 0.0) / sc)
        cr.set_source_rgb(1, 1, 1)
        cr.stroke()
        cr.set_dash([], 0)

    def _draw_region_area(self, cr, r: S.Rect, sc: float, dash_offset: float | None) -> None:
        """ShareX DrawRegionArea (DrawRectangleProper with 1 px pens)."""
        if r.w - 1 <= 0 or r.h - 1 <= 0:
            return
        cr.save()
        cr.translate(0.5, 0.5)
        cr.rectangle(r.x, r.y, r.w - 1, r.h - 1)
        self._stroke_region_pens(cr, sc, dash_offset)
        cr.restore()

    def _ruler_ticks(self, cr, r: S.Rect, size: float, width: float) -> None:
        if r.w >= size and r.h >= size:
            for i in range(1, int(r.w // width) + 1):
                x = r.x + i * width
                cr.move_to(x, r.y)
                cr.line_to(x, r.y + size)
                cr.move_to(x, r.bottom)
                cr.line_to(x, r.bottom - size)
            for i in range(1, int(r.h // width) + 1):
                y = r.y + i * width
                cr.move_to(r.x, y)
                cr.line_to(r.x + size, y)
                cr.move_to(r.right, y)
                cr.line_to(r.right - size, y)

    def _info_box(self, cr, text: str, x: float, y: float, padding: float, sc: float,
                  extra_left: float = 0) -> tuple[float, float]:
        """ShareX DrawInfoText (blue box, two borders, shadowed white text) at screen-ish scale."""
        fs = 9 / sc
        tw, th = R.measure_text(text, "Sans", 9)
        tw, th = tw / sc, th / sc
        pad = padding / sc
        w, h = tw + pad * 2 + extra_left, th + pad * 2
        cr.save()
        cr.rectangle(x + 2 / sc, y + 2 / sc, w - 4 / sc, h - 4 / sc)
        cr.set_source_rgba(*(c / 255 for c in TEXT_BACKGROUND))
        cr.fill()
        cr.set_line_width(1 / sc)
        cr.rectangle(x + 1.5 / sc, y + 1.5 / sc, w - 3 / sc, h - 3 / sc)
        cr.set_source_rgba(*(c / 255 for c in TEXT_INNER_BORDER))
        cr.stroke()
        cr.rectangle(x + 0.5 / sc, y + 0.5 / sc, w - 1 / sc, h - 1 / sc)
        cr.set_source_rgba(*(c / 255 for c in TEXT_OUTER_BORDER))
        cr.stroke()
        layout = R.make_layout(cr, text, "Sans", fs)
        cr.move_to(x + pad + extra_left, y + pad + 1 / sc)
        cr.set_source_rgb(0, 0, 0)
        R.PangoCairo.show_layout(cr, layout)
        cr.move_to(x + pad + extra_left, y + pad)
        cr.set_source_rgb(1, 1, 1)
        R.PangoCairo.show_layout(cr, layout)
        cr.restore()
        return w, h

    def _draw_area_text(self, cr, text: str, area: S.Rect, sc: float) -> None:
        """ShareX DrawAreaText: label above the area, inside it when there is no room."""
        offset, bp = 6 / sc, 3 / sc
        tw, th = R.measure_text(text, "Sans", 9)
        tw, th = tw / sc, th / sc
        ca = self.area
        if area.y - offset - th - bp * 2 < ca.y:
            tx, ty = area.x + offset + bp, area.y + offset + bp
        else:
            tx, ty = area.x + bp, area.y - offset - bp - th
        if tx + tw + bp >= ca.right:
            tx = ca.right - tw - bp
        self._info_box(cr, text, tx - bp, ty - bp, 3, sc)

    def _draw_nodes(self, cr, sc: float) -> None:
        size = NODE_SIZE / sc
        light = self.options.use_light_resize_nodes
        line = isinstance(self._current_shape, (S.DrawingLine, S.RegionFreehand))
        for i, (x, y) in self.visible_nodes():
            cr.save()
            if light and not (line or i == S.NodePosition.Extra):
                cr.translate(0.5, 0.5)
                h = (size - 1) / 2
                cr.rectangle(x - h - 1 / sc, y - h - 1 / sc, size - 1 / sc, size - 1 / sc)
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(1 / sc)
                cr.stroke()
                cr.rectangle(x - h, y - h, size - 2 / sc, size - 2 / sc)
                cr.set_source_rgb(0, 0, 0)
                cr.stroke()
            else:
                rad = (size - 1) / 2
                cr.arc(x + 0.5, y + 0.5, rad, 0, 2 * math.pi)
                cr.set_source_rgb(1, 1, 1)
                cr.fill_preserve()
                cr.set_source_rgb(0.2, 0.2, 0.2)
                cr.set_line_width(1.5 / sc)
                cr.stroke()
            cr.restore()

    def _draw_buttons(self, cr, sc: float) -> None:
        for name, r in self.tool_buttons():
            color = (34 / 255, 139 / 255, 34 / 255) if name == "confirm" else (227 / 255, 45 / 255, 45 / 255)
            cr.save()
            R.path_rounded_rect(cr, r.x, r.y, r.w, r.h, 6 / sc)
            cr.set_source_rgb(*color)
            cr.fill()
            text = "✔" if name == "confirm" else "✖"
            tw, th = R.measure_text(text, "Sans", 16, True)
            layout = R.make_layout(cr, text, "Sans", 16 / sc, True)
            cr.move_to(r.x + (r.w - tw / sc) / 2, r.y + (r.h - th / sc) / 2)
            cr.set_source_rgb(1, 1, 1)
            R.PangoCairo.show_layout(cr, layout)
            cr.restore()

    def _draw_cursor_graphics(self, cr, sc: float, view_offset, view_size) -> None:
        """Magnifier and info text next to the cursor, in widget pixels (ShareX DrawCursorGraphics)."""
        o = self.options
        px, py = self.pointer
        mx, my = view_offset[0] + px * sc, view_offset[1] + py * sc
        gap, off = 10, 10
        total_w = total_h = 0.0
        mag_img = None
        if o.show_magnifier:
            mag_img = self.magnifier().render()
            total_w = max(total_w, mag_img.width)
            total_h += mag_img.height
        info = ""
        info_w = info_h = 0.0
        color_box = self.mode == RegionCaptureMode.ScreenColorPicker
        if o.show_info:
            if mag_img is not None:
                total_h += gap
            info = self.info_text()
            tw, th = R.measure_text(info, "Sans", 9)
            info_w, info_h = tw + 6, th + 6
            if color_box:
                info_w += info_h - 4 + 4
            total_w = max(total_w, info_w)
            total_h += info_h
        bound_w, bound_h = view_size if view_size else (view_offset[0] + self.canvas.width * sc,
                                                        view_offset[1] + self.canvas.height * sc)
        x = mx + off
        if x + total_w > bound_w:
            x = mx - off - total_w
        y = my + off
        if y + total_h > bound_h:
            y = my - off - total_h
        cy = y
        if mag_img is not None:
            surf = R.pil_to_surface(mag_img)
            cr.save()
            if o.use_square_magnifier:
                cr.rectangle(x, cy, mag_img.width, mag_img.height)
                cr.set_source_surface(surf, x, cy)
                cr.fill()
                cr.set_line_width(1)
                cr.rectangle(x - 0.5, cy - 0.5, mag_img.width + 1, mag_img.height + 1)
                cr.set_source_rgb(1, 1, 1)
                cr.stroke()
                cr.rectangle(x + 0.5, cy + 0.5, mag_img.width - 1, mag_img.height - 1)
                cr.set_source_rgb(0, 0, 0)
                cr.stroke()
            else:
                R.path_ellipse(cr, x, cy, mag_img.width, mag_img.height)
                cr.set_source_surface(surf, x, cy)
                cr.fill()
                cr.set_line_width(1)
                R.path_ellipse(cr, x - 0.5, cy - 0.5, mag_img.width + 1, mag_img.height + 1)
                cr.set_source_rgb(1, 1, 1)
                cr.stroke()
                R.path_ellipse(cr, x + 0.5, cy + 0.5, mag_img.width - 1, mag_img.height - 1)
                cr.set_source_rgb(0, 0, 0)
                cr.stroke()
            cr.restore()
            cy += mag_img.height + gap
        if o.show_info:
            bx = x + total_w / 2 - info_w / 2
            extra = (info_h - 4 + 4) if color_box else 0
            self._info_box(cr, info, bx, cy, 3, 1.0, extra_left=extra)
            if color_box:
                col = self.color_at(math.floor(px), math.floor(py)) or "#FF000000"
                size = info_h - 4
                cr.save()
                cr.rectangle(bx + 2, cy + 2, size, size)
                R.set_source(cr, col)
                cr.fill()
                cr.move_to(bx + 2 + size + 0.5, cy + 2)
                cr.line_to(bx + 2 + size + 0.5, cy + 2 + size)
                cr.set_source_rgba(*(c / 255 for c in TEXT_INNER_BORDER))
                cr.set_line_width(1)
                cr.stroke()
                cr.restore()

    def _draw_crosshair(self, cr, sc, view_offset, view_size, dash_offset) -> None:
        px, py = self.pointer
        mx, my = view_offset[0] + px * sc + 0.5, view_offset[1] + py * sc + 0.5
        w, h = view_size if view_size else (view_offset[0] + self.canvas.width * sc,
                                            view_offset[1] + self.canvas.height * sc)
        o = 5
        cr.save()
        segs = []
        if mx - o > 10:
            segs.append(((mx - o, my), (0, my)))
        if w - 1 - (mx + o) > 10:
            segs.append(((mx + o, my), (w - 1, my)))
        if my - o > 10:
            segs.append(((mx, my - o), (mx, 0)))
        if h - 1 - (my + o) > 10:
            segs.append(((mx, my + o), (mx, h - 1)))
        for a, b in segs:
            cr.move_to(*a)
            cr.line_to(*b)
        self._stroke_region_pens(cr, 1.0, dash_offset)
        cr.restore()

    # ------------------------------------------------------------------ toolbar model

    def _border_target(self, t: ShapeType) -> tuple[str, str]:
        if t in (ShapeType.DrawingTextBackground, ShapeType.DrawingSpeechBalloon):
            return "text_border_color", "text_border_size"
        if t == ShapeType.DrawingTextOutline:
            return "text_outline_border_color", "text_outline_border_size"
        if t == ShapeType.DrawingStep:
            return "step_border_color", "step_border_size"
        return "border_color", "border_size"

    def _fill_target(self, t: ShapeType) -> str:
        if t in (ShapeType.DrawingTextBackground, ShapeType.DrawingSpeechBalloon):
            return "text_fill_color"
        if t == ShapeType.DrawingStep:
            return "step_fill_color"
        return "fill_color"

    def _opt_setter(self, attr: str, conv=lambda v: v) -> Callable[[Any], None]:
        def setter(value):
            setattr(self.annotation, attr, conv(value))
            self.update_current_shape()
        return setter

    def _rc_setter(self, attr: str, conv=lambda v: v) -> Callable[[Any], None]:
        def setter(value):
            setattr(self.options, attr, conv(value))
            self._update_hover()
            self._changed()
        return setter

    def set_tool(self, tool: ShapeType) -> None:
        self.current_tool = tool

    def text_options_target(self) -> str | None:
        """Which AnnotationOptions text options the current text tool uses."""
        t = self.current_shape_tool
        if t == ShapeType.DrawingTextOutline:
            return "text_outline_options"
        if t in (ShapeType.DrawingTextBackground, ShapeType.DrawingSpeechBalloon):
            return "text_options"
        return None

    def set_text_option(self, name: str, value: Any) -> None:
        """Font family/size/bold/italic/underline/color/alignment of the current text tool
        (ShareX's text input box edits these)."""
        target = self.text_options_target()
        if target is None:
            raise ValueError("current tool has no text options")
        opts = getattr(self.annotation, target)
        if not hasattr(opts, name):
            raise AttributeError(name)
        setattr(opts, name, value)
        self.update_current_shape()

    def toolbar_model(self) -> ToolbarModel:
        """Everything ShareX's toolbar shows for the current mode/tool/selection."""
        a = self.annotation
        o = self.options
        t = self.current_shape_tool
        editor = self.is_editor_mode
        cb = self.callbacks

        main: list[ToolbarItem] = []
        if editor:
            label = ("Run after capture tasks (Enter)" if self.mode == RegionCaptureMode.Editor
                     else "Apply changes & continue task (Enter)")
            main.append(ToolbarItem("complete_edit", label, icon="tick",
                                    activate=lambda: self.close(RegionResult.AnnotateRunAfterCaptureTasks)))
            if self.mode == RegionCaptureMode.TaskEditor:
                main.append(ToolbarItem("continue_task", "Continue task (Space or right click)", icon="control",
                                        activate=lambda: self.close(RegionResult.AnnotateContinueTask)))
                main.append(ToolbarItem("cancel_task", "Cancel task (Esc)", icon="cross",
                                        activate=lambda: self.close(RegionResult.AnnotateCancelTask)))
            main.append(ToolbarItem("sep_main", kind="separator"))
            for item_id, label, icon, fn in (
                    ("save_image", "Save image (Ctrl + S)", "disk-black", cb.save_image),
                    ("save_image_as", "Save image as... (Ctrl + Shift + S)", "disks-black", cb.save_image_as),
                    ("copy_image", "Copy image to clipboard (Ctrl + C)", "clipboard", cb.copy_image),
                    ("upload_image", "Upload image (Ctrl + U)", "drive-globe", cb.upload_image),
                    ("print_image", "Print image... (Ctrl + P)", "printer", cb.print_image)):
                main.append(ToolbarItem(item_id, label, icon=icon, enabled=fn is not None,
                                        activate=(lambda f=fn: f(self.render_output())) if fn else None))
        else:
            main.append(ToolbarItem("close", "Close (Esc)", icon="cross",
                                    activate=lambda: self.close(RegionResult.Close)))

        tools: list[ToolbarItem] = []
        if self.is_annotation_mode:
            for st in ShapeType:
                if editor and S.is_region_type(st):
                    continue
                if not editor and st == ShapeType.ToolSelect:
                    tools.append(ToolbarItem("sep_tools", kind="separator"))
                if not editor and st in (ShapeType.ToolCrop, ShapeType.ToolCutOut):
                    continue
                tools.append(ToolbarItem(f"tool_{st.value}", S.SHAPE_TYPE_LABELS[st], kind="tool",
                                         icon=TOOL_ICONS[st], value=st, checked=st == self._current_tool,
                                         activate=lambda st=st: self.set_tool(st)))

        border_attr, size_attr = self._border_target(t)
        fill_attr = self._fill_target(t)
        drawing_border = t in (ShapeType.DrawingRectangle, ShapeType.DrawingEllipse, ShapeType.DrawingFreehand,
                               ShapeType.DrawingFreehandArrow, ShapeType.DrawingLine, ShapeType.DrawingArrow,
                               ShapeType.DrawingTextOutline, ShapeType.DrawingTextBackground,
                               ShapeType.DrawingSpeechBalloon, ShapeType.DrawingStep, ShapeType.DrawingMagnify)
        fill_visible = t in (ShapeType.DrawingRectangle, ShapeType.DrawingEllipse, ShapeType.DrawingTextBackground,
                             ShapeType.DrawingSpeechBalloon, ShapeType.DrawingStep, ShapeType.DrawingMagnify)
        colors = [
            ToolbarItem("border_color", "Border color...", kind="color", value=getattr(a, border_attr),
                        visible=drawing_border, setter=self._opt_setter(border_attr)),
            ToolbarItem("fill_color", "Fill color...", kind="color", value=getattr(a, fill_attr),
                        visible=fill_visible, setter=self._opt_setter(fill_attr)),
            ToolbarItem("highlight_color", "Highlight color...", kind="color", value=a.highlight_color,
                        visible=t == ShapeType.EffectHighlight, setter=self._opt_setter("highlight_color")),
        ]
        if not self.is_annotation_mode:
            for c in colors:
                c.visible = False

        corner_attr = "region_corner_radius" if t == ShapeType.RegionRectangle else "drawing_corner_radius"
        corner_visible = t in (ShapeType.RegionRectangle, ShapeType.DrawingRectangle,
                               ShapeType.DrawingTextBackground)

        def choices(enum_cls, labels=None):
            return [(m.value, (labels or {}).get(m.value, m.value)) for m in enum_cls]

        def set_start_step(v):
            self.starting_step_number = int(v)
            self.update_current_shape()
            self._on_image_modified()

        text_target = self.text_options_target()
        text_opts = getattr(a, text_target) if text_target else None
        tool_options = [
            ToolbarItem("magnify_strength", "Magnify strength:", kind="int", value=a.magnifier_strength, min=100,
                        max=1000, step=100, visible=t == ShapeType.DrawingMagnify,
                        setter=self._opt_setter("magnifier_strength", int)),
            ToolbarItem("border_size", "Border size:", kind="int", value=getattr(a, size_attr), min=0, max=20,
                        visible=drawing_border, setter=self._opt_setter(size_attr, int)),
            ToolbarItem("corner_radius", "Corner radius:", kind="int", value=getattr(a, corner_attr) if corner_visible
                        else 0, min=0, max=150, visible=corner_visible, setter=self._opt_setter(corner_attr, int)),
            ToolbarItem("border_style", "Border style:", kind="choice", value=a.border_style.value,
                        choices=choices(BorderStyle), visible=t in (
                            ShapeType.DrawingRectangle, ShapeType.DrawingEllipse, ShapeType.DrawingFreehand,
                            ShapeType.DrawingFreehandArrow, ShapeType.DrawingLine, ShapeType.DrawingArrow,
                            ShapeType.DrawingMagnify), setter=self._opt_setter("border_style", BorderStyle)),
            ToolbarItem("interpolation_mode", "Interpolation mode:", kind="choice",
                        value=a.image_interpolation_mode.value, choices=choices(ImageInterpolationMode),
                        visible=t in (ShapeType.DrawingImage, ShapeType.DrawingImageScreen, ShapeType.DrawingMagnify),
                        setter=self._opt_setter("image_interpolation_mode", ImageInterpolationMode)),
            ToolbarItem("blur_radius", "Blur strength:", kind="int", value=a.blur_radius, min=3, max=199, step=2,
                        visible=t == ShapeType.EffectBlur, setter=self._opt_setter("blur_radius", int)),
            ToolbarItem("pixelate_size", "Pixel size:", kind="int", value=a.pixelate_size, min=2, max=10000,
                        visible=t == ShapeType.EffectPixelate, setter=self._opt_setter("pixelate_size", int)),
            ToolbarItem("center_points", "Center points:", kind="int", value=a.line_center_point_count, min=0,
                        max=S.DrawingLine.MAXIMUM_CENTER_POINT_COUNT,
                        visible=t in (ShapeType.DrawingLine, ShapeType.DrawingArrow),
                        setter=self._opt_setter("line_center_point_count", int)),
            ToolbarItem("arrow_head_direction", "Arrow head direction:", kind="choice",
                        value=a.arrow_head_direction.value, choices=choices(ArrowHeadDirection),
                        visible=t in (ShapeType.DrawingArrow, ShapeType.DrawingFreehandArrow),
                        setter=self._opt_setter("arrow_head_direction", ArrowHeadDirection)),
            ToolbarItem("step_font_size", "Font size:", kind="int", value=a.step_font_size, min=10, max=300,
                        visible=t == ShapeType.DrawingStep, setter=self._opt_setter("step_font_size", int)),
            ToolbarItem("starting_step_value", "Value of first step:", kind="int", value=self.starting_step_number,
                        min=0, max=10000, visible=t == ShapeType.DrawingStep, setter=set_start_step),
            ToolbarItem("step_type", "Step type:", kind="choice", value=a.step_type.value,
                        choices=choices(StepType, {"LettersUppercase": "Letters (Uppercase)",
                                                   "LettersLowercase": "Letters (Lowercase)",
                                                   "RomanNumeralsUppercase": "Roman numerals (Uppercase)",
                                                   "RomanNumeralsLowercase": "Roman numerals (Lowercase)"}),
                        visible=t == ShapeType.DrawingStep, setter=self._opt_setter("step_type", StepType)),
            ToolbarItem("font", "Font:", kind="font", value=(text_opts.font, text_opts.size, text_opts.bold,
                                                              text_opts.italic, text_opts.underline)
                        if text_opts else None, visible=text_opts is not None,
                        setter=self._font_setter if text_opts else None),
            ToolbarItem("text_color", "Text color...", kind="color", value=text_opts.color if text_opts else None,
                        visible=text_opts is not None,
                        setter=(lambda v: self.set_text_option("color", v)) if text_opts else None),
            ToolbarItem("text_alignment", "Text alignment:", kind="choice",
                        value=(f"{text_opts.alignment_vertical}{text_opts.alignment_horizontal}"
                               if text_opts else None),
                        choices=[(f"{v}{h}", f"{v} {h}") for v in ("Top", "Center", "Bottom")
                                 for h in ("Left", "Center", "Right")],
                        visible=text_opts is not None, setter=self._alignment_setter if text_opts else None),
            ToolbarItem("sticker_pack", "Sticker pack:", kind="choice", value=a.sticker_pack,
                        choices=[(p, p) for p in ST.list_packs()], visible=t == ShapeType.DrawingSticker,
                        setter=lambda v: setattr(a, "sticker_pack", str(v))),
            ToolbarItem("sticker_size", "Sticker size:", kind="int", value=a.sticker_size, min=16, max=512,
                        visible=t == ShapeType.DrawingSticker, setter=lambda v: setattr(a, "sticker_size", int(v))),
            ToolbarItem("shadow", "Drop shadow", kind="toggle", value=a.shadow, checked=a.shadow,
                        visible=drawing_border, setter=self._opt_setter("shadow", bool)),
            ToolbarItem("shadow_color", "Drop shadow color...", kind="color", value=a.shadow_color,
                        visible=drawing_border, setter=self._opt_setter("shadow_color")),
            ToolbarItem("cut_out_effect_type", "Cut out effect:", kind="choice", value=a.cut_out_effect_type.value,
                        choices=choices(CutOutEffectType, {"None": "No effect", "ZigZag": "Sawtooth",
                                                           "TornEdge": "Torn edges", "Wave": "Wave"}),
                        visible=t == ShapeType.ToolCutOut, setter=self._opt_setter("cut_out_effect_type",
                                                                                   CutOutEffectType)),
            ToolbarItem("cut_out_effect_size", "Cut out effect size:", kind="int", value=a.cut_out_effect_size,
                        min=3, max=100, visible=t == ShapeType.ToolCutOut,
                        setter=self._opt_setter("cut_out_effect_size", int)),
            ToolbarItem("cut_out_background_color", "Cut out background color...", kind="color",
                        value=a.cut_out_background_color, visible=t == ShapeType.ToolCutOut,
                        setter=self._opt_setter("cut_out_background_color")),
        ]
        options_visible = self.is_annotation_mode and t in (
            ShapeType.RegionRectangle, ShapeType.DrawingRectangle, ShapeType.DrawingEllipse, ShapeType.DrawingFreehand,
            ShapeType.DrawingFreehandArrow, ShapeType.DrawingLine, ShapeType.DrawingArrow, ShapeType.DrawingTextOutline,
            ShapeType.DrawingTextBackground, ShapeType.DrawingSpeechBalloon, ShapeType.DrawingStep,
            ShapeType.DrawingMagnify, ShapeType.DrawingImage, ShapeType.DrawingImageScreen, ShapeType.DrawingCursor,
            ShapeType.DrawingSticker, ShapeType.EffectBlur, ShapeType.EffectPixelate, ShapeType.ToolCutOut)

        has_shape = self._current_shape is not None
        edit_menu = [
            ToolbarItem("undo", "Undo", icon="arrow-circle-225-left", shortcut="Ctrl+Z", enabled=self.can_undo,
                        activate=self.undo),
            ToolbarItem("redo", "Redo", icon="arrow-circle-315", shortcut="Ctrl+Y", enabled=self.can_redo,
                        activate=self.redo),
            ToolbarItem("paste", "Paste image/text", icon="clipboard", shortcut="Ctrl+V",
                        activate=lambda: self.paste(False)),
            ToolbarItem("duplicate", "Duplicate", icon="document-copy", shortcut="Ctrl+D", enabled=has_shape,
                        activate=lambda: self.duplicate_current_shape(False)),
            ToolbarItem("sep_edit1", kind="separator"),
            ToolbarItem("delete", "Delete", icon="layer-minus", shortcut="Del", enabled=has_shape,
                        activate=self.delete_current_shape),
            ToolbarItem("delete_all", "Delete all", icon="eraser", shortcut="Shift+Del", enabled=bool(self.shapes),
                        activate=self.delete_all_shapes),
            ToolbarItem("sep_edit2", kind="separator"),
            ToolbarItem("move_top", "Bring to front", icon="layers-stack-arrange", shortcut="Home",
                        enabled=has_shape, activate=self.move_shape_top),
            ToolbarItem("move_up", "Bring forward", icon="layers-arrange", shortcut="Page Up", enabled=has_shape,
                        activate=self.move_shape_up),
            ToolbarItem("move_down", "Send backward", icon="layers-arrange-back", shortcut="Page Down",
                        enabled=has_shape, activate=self.move_shape_down),
            ToolbarItem("move_bottom", "Send to back", icon="layers-stack-arrange-back", shortcut="End",
                        enabled=has_shape, activate=self.move_shape_bottom),
        ]

        image_menu: list[ToolbarItem] = []
        if editor:
            image_menu = [
                ToolbarItem("new_image", "New image...", kind="dialog", icon="image-empty", dialog="new_image",
                            params=["width", "height", "color", "transparent"], activate=self.new_image,
                            value={"width": o.editor_new_image_size.width, "height": o.editor_new_image_size.height,
                                   "color": o.editor_new_image_background_color,
                                   "transparent": o.editor_new_image_transparent}),
                ToolbarItem("open_image", "Open image file...", kind="dialog", icon="folder-open-image",
                            dialog="open_file", params=["image"], activate=self.load_image),
                ToolbarItem("insert_image_file", "Insert image file...", kind="dialog", icon="image-plus",
                            dialog="open_file", params=["path", "method"], activate=self.insert_image_file),
                ToolbarItem("insert_image_screen", "Insert image from screen...", icon="camera",
                            enabled=cb.capture_screen_image is not None, activate=self.insert_image_from_screen),
                ToolbarItem("sep_image1", kind="separator"),
                ToolbarItem("image_size", "Image size...", kind="dialog", icon="image-select", dialog="image_size",
                            params=["width", "height", "interpolation"], activate=self.resize_image,
                            value={"width": self.canvas.width, "height": self.canvas.height,
                                   "interpolation": o.image_editor_resize_interpolation}),
                ToolbarItem("canvas_size", "Canvas size...", kind="dialog", icon="image-resize", dialog="canvas_size",
                            params=["left", "top", "right", "bottom", "color"], activate=self.resize_canvas,
                            value={"color": o.editor_canvas_color}),
                ToolbarItem("crop_image", "Crop image...", icon="image-crop", activate=self.add_crop_tool),
                ToolbarItem("auto_crop_image", "Auto crop image...", icon="image-resize-actual",
                            activate=self.auto_crop_image),
                ToolbarItem("sep_image2", kind="separator"),
                ToolbarItem("rotate_cw", "Rotate 90° clockwise", icon="arrow-circle", activate=lambda: self.rotate(90)),
                ToolbarItem("rotate_ccw", "Rotate 90° counter clockwise", icon="arrow-circle-135-left",
                            activate=lambda: self.rotate(270)),
                ToolbarItem("rotate_180", "Rotate 180°", icon="arrow-circle-double", activate=lambda: self.rotate(180)),
                ToolbarItem("sep_image3", kind="separator"),
                ToolbarItem("flip_horizontal", "Flip horizontal", icon="layer-flip", activate=lambda: self.flip(True)),
                ToolbarItem("flip_vertical", "Flip vertical", icon="layer-flip-vertical",
                            activate=lambda: self.flip(False)),
                ToolbarItem("sep_image4", kind="separator"),
                ToolbarItem("image_effects", "Add image effects...", icon="image-saturation",
                            enabled=cb.apply_effects is not None, activate=self.add_image_effects),
            ]

        capture_menu: list[ToolbarItem] = []
        if not editor:
            capture_menu = [
                ToolbarItem("capture_regions", "Capture regions", icon="layer", shortcut="Enter",
                            visible=not o.quick_crop and bool(self.valid_regions),
                            activate=lambda: self.close(RegionResult.Region)),
                ToolbarItem("capture_last_region", "Capture last region", icon="layers",
                            visible=ShapeManager.last_region is not None,
                            activate=lambda: self.close(RegionResult.LastRegion)),
                ToolbarItem("capture_fullscreen", "Capture fullscreen", icon="layer-fullscreen", shortcut="Space",
                            activate=lambda: self.close(RegionResult.Fullscreen)),
                ToolbarItem("capture_active_monitor", "Capture active monitor", icon="monitor", shortcut="~",
                            activate=lambda: self.close(RegionResult.ActiveMonitor)),
                ToolbarItem("capture_monitor", "Capture monitor", kind="submenu", icon="monitor-window",
                            visible=bool(self.monitors), items=[
                                ToolbarItem(f"capture_monitor_{i}", f"{m[2]}x{m[3]}", shortcut=str(i + 1),
                                            activate=lambda i=i: self.capture_monitor(i))
                                for i, m in enumerate(self.monitors)]),
            ]

        def toggle(item_id, label, attr, visible=True):
            v = getattr(o, attr)
            return ToolbarItem(item_id, label, kind="toggle", value=v, checked=v, visible=visible,
                               setter=self._rc_setter(attr, bool))

        options_menu: list[ToolbarItem] = []
        if editor:
            options_menu += [
                ToolbarItem("editor_start_mode", "Editor start mode:", kind="choice",
                            value=o.image_editor_start_mode.value, choices=choices(ImageEditorStartMode),
                            setter=self._rc_setter("image_editor_start_mode", ImageEditorStartMode)),
                toggle("zoom_to_fit_on_open", "Zoom to fit on open", "zoom_to_fit_on_open"),
                toggle("editor_auto_copy_image", "Auto copy image to clipboard", "editor_auto_copy_image"),
                toggle("auto_close_editor_on_task", "Auto close editor on task", "auto_close_editor_on_task"),
                ToolbarItem("sep_opt0", kind="separator"),
            ]
        else:
            options_menu.append(ToolbarItem("multi_region_mode", "Multi region mode", kind="toggle",
                                            value=not o.quick_crop, checked=not o.quick_crop,
                                            setter=self._rc_setter("quick_crop", lambda v: not bool(v))))
        options_menu += [
            toggle("show_info", "Show position and size info", "show_info"),
            toggle("show_magnifier", "Show magnifier", "show_magnifier"),
            toggle("use_square_magnifier", "Square shape magnifier", "use_square_magnifier"),
            ToolbarItem("magnifier_pixel_count", "Magnifier pixel count:", kind="int", value=o.magnifier_pixel_count,
                        min=MAGNIFIER_PIXEL_COUNT_MIN, max=MAGNIFIER_PIXEL_COUNT_MAX, step=2,
                        setter=self._rc_setter("magnifier_pixel_count", int)),
            ToolbarItem("magnifier_pixel_size", "Magnifier pixel size:", kind="int", value=o.magnifier_pixel_size,
                        min=2, max=30, setter=self._rc_setter("magnifier_pixel_size", int)),
            toggle("show_crosshair", "Show screen wide crosshair", "show_crosshair"),
            toggle("use_light_resize_nodes", "Use light resize nodes", "use_light_resize_nodes"),
            toggle("enable_animations", "Enable animations", "enable_animations"),
        ]
        if not editor:
            options_menu += [
                toggle("is_fixed_size", "Fixed size region mode", "is_fixed_size"),
                ToolbarItem("fixed_size", "Width: / Height:", kind="size",
                            value=(o.fixed_size.width, o.fixed_size.height), min=3, max=10000,
                            setter=self._rc_setter("fixed_size", lambda v: Size(int(v[0]), int(v[1])))),
            ]
        options_menu += [
            toggle("show_fps", "Show FPS", "show_fps"),
            ToolbarItem("fps_limit", "FPS limit:", kind="int", value=o.fps_limit, min=0, max=300,
                        setter=self._rc_setter("fps_limit", int)),
            toggle("switch_to_drawing_tool_after_selection", "Switch to drawing tool after shape selection",
                   "switch_to_drawing_tool_after_selection"),
            toggle("switch_to_selection_tool_after_drawing", "Switch to selection tool after shape drawing",
                   "switch_to_selection_tool_after_drawing"),
            ToolbarItem("menu_icon_size", "Menu icon size:", kind="int", value=o.menu_icon_size, min=0, max=64,
                        step=16, setter=self._rc_setter("menu_icon_size", int)),
        ]
        if not editor:
            options_menu += [toggle("menu_locked", "Lock menu", "menu_locked"),
                             toggle("remember_menu_state", "Remember menu state", "remember_menu_state")]
        return ToolbarModel(main, tools, colors, tool_options, edit_menu if self.is_annotation_mode else [],
                            image_menu, capture_menu, options_menu, options_visible)

    def _font_setter(self, value) -> None:
        family, size, bold, italic, underline = value
        target = self.text_options_target()
        opts = getattr(self.annotation, target)
        opts.font, opts.size, opts.bold, opts.italic, opts.underline = str(family), int(size), bool(bold), \
            bool(italic), bool(underline)
        self.update_current_shape()

    def _alignment_setter(self, value: str) -> None:
        target = self.text_options_target()
        opts = getattr(self.annotation, target)
        for v in ("Top", "Center", "Bottom"):
            if value.startswith(v):
                opts.alignment_vertical = v
                opts.alignment_horizontal = value[len(v):]
                break
        self.update_current_shape()


# ---- helpers ----------------------------------------------------------------------------


def _round(v: float) -> int:
    return math.floor(v + 0.5) if v >= 0 else -math.floor(-v + 0.5)


def _fmt(v: float) -> str:
    """C# float.ToString(): integers without decimals."""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def ruler_info(rect: S.Rect) -> dict[str, float]:
    """Ruler mode readout (ShareX GetAreaText in Ruler mode)."""
    right, bottom = rect.right - 1, rect.bottom - 1
    return {"x": rect.x, "y": rect.y, "right": right, "bottom": bottom, "width": rect.w, "height": rect.h,
            "area": rect.w * rect.h, "perimeter": 2 * (rect.w + rect.h),
            "distance": S.distance((rect.x, rect.y), (right, bottom)),
            "angle": S.look_at_degree((rect.x, rect.y), (right, bottom))}


_COLOR_NAMES: dict[tuple[int, int, int], str] = {}


def color_name(r: int, g: int, b: int) -> str:
    """Name of a CSS/.NET web colour matching exactly, else the hex value."""
    if not _COLOR_NAMES:
        # Pillow caches converted entries back into colormap as tuples: resolve through getrgb.
        for name in list(ImageColor.colormap):
            try:
                rgb = ImageColor.getrgb(name)[:3]
            except ValueError:
                continue
            _COLOR_NAMES.setdefault(rgb, name[0].upper() + name[1:])
    return _COLOR_NAMES.get((r, g, b), f"{r:02X}{g:02X}{b:02X}")


def _num(v: float) -> str:
    s = f"{v:.15f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _cmyk100(r: int, g: int, b: int) -> tuple[float, float, float, float]:
    rf, gf, bf = r / 255, g / 255, b / 255
    k = 1 - max(rf, gf, bf)
    if k >= 1:
        return (0.0, 0.0, 0.0, 100.0)
    return ((1 - rf - k) / (1 - k) * 100, (1 - gf - k) / (1 - k) * 100, (1 - bf - k) / (1 - k) * 100, k * 100)


def _round_away(v: float, digits: int) -> float:
    q = 10 ** digits
    return math.floor(abs(v) * q + 0.5) / q * (1 if v >= 0 else -1)


def parse_pixel_info(text: str, color: str, position: tuple[int, int]) -> str:
    """ShareX CodeMenuEntryPixelInfo.Parse: $r255 $g255 $b255 $r1{n} $g1 $b1 $hex $HEX $rhex $ghex
    $bhex $rHEX $gHEX $bHEX $c100 $m100 $y100 $k100 $name $x $y $n (and legacy $r $g $b)."""
    import re

    r, g, b, _a = R.parse_color(color)
    hexv = f"{r:02X}{g:02X}{b:02X}"
    c, m, y, k = _cmyk100(r, g, b)

    def rep(s: str, token: str, value: str, ignore_case: bool = True) -> str:
        if ignore_case:
            return re.sub(re.escape(token), lambda _m: value, s, flags=re.IGNORECASE)
        return s.replace(token, value)

    s = text
    s = rep(s, "$r255", str(r))
    s = rep(s, "$g255", str(g))
    s = rep(s, "$b255", str(b))
    s = rep(s, "$rHEX", f"{r:02X}", False)
    s = rep(s, "$gHEX", f"{g:02X}", False)
    s = rep(s, "$bHEX", f"{b:02X}", False)
    s = rep(s, "$rhex", f"{r:02x}")
    s = rep(s, "$ghex", f"{g:02x}")
    s = rep(s, "$bhex", f"{b:02x}")
    s = rep(s, "$HEX", hexv, False)
    s = rep(s, "$hex", hexv.lower())
    s = rep(s, "$c100", _num(_round_away(c, 2)))
    s = rep(s, "$m100", _num(_round_away(m, 2)))
    s = rep(s, "$y100", _num(_round_away(y, 2)))
    s = rep(s, "$k100", _num(_round_away(k, 2)))
    s = rep(s, "$name", color_name(r, g, b))
    s = rep(s, "$x", str(position[0]))
    s = rep(s, "$y", str(position[1]))
    s = rep(s, "$n", "\n")
    for token, v in (("$r1", r), ("$g1", g), ("$b1", b)):
        def sub(mt, v=v):
            digits = max(0, min(15, int(mt.group(1)) if mt.group(1).lstrip("-").isdigit() else 0))
            return _num(_round_away(v / 255, digits))
        s = re.sub(re.escape(token) + r"\{([^}]*)\}", sub, s, flags=re.IGNORECASE)
    s = rep(s, "$r1", _num(_round_away(r / 255, 3)))
    s = rep(s, "$g1", _num(_round_away(g / 255, 3)))
    s = rep(s, "$b1", _num(_round_away(b / 255, 3)))
    s = rep(s, "$r", str(r))
    s = rep(s, "$g", str(g))
    s = rep(s, "$b", str(b))
    return s
