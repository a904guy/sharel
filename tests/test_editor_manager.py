"""ShapeManager behaviour: selection, handles, clipboard, z-order, undo, modes, results, snapping."""
from __future__ import annotations

import cairo
import numpy as np
import pytest
from PIL import Image
from editor_helpers import RED, WHITE, blank, click, close, drag, make, noise, px, state

from sharel.core.config import RegionCaptureOptions, Size
from sharel.core.enums import RegionCaptureAction, RegionCaptureMode, ShapeType
from sharel.editor import render as R
from sharel.editor import shapes as S
from sharel.editor.manager import (
    BUTTON_MIDDLE,
    BUTTON_RIGHT,
    Callbacks,
    Mod,
    RegionResult,
    ShapeManager,
    parse_pixel_info,
    ruler_info,
)


@pytest.fixture(autouse=True)
def _no_last_region():
    ShapeManager.last_region = None
    yield
    ShapeManager.last_region = None


def rect_mgr():
    m = make(tool=ShapeType.DrawingRectangle)
    drag(m, (50, 50), (150, 110))
    return m, m.shapes[0]


# ---- selection / move / resize ----------------------------------------------------------


def test_created_shape_is_selected_with_eight_nodes():
    m, s = rect_mgr()
    assert m.current_shape is s and m.nodes_visible
    nodes = dict(m.visible_nodes())
    assert nodes[S.NodePosition.TopLeft] == (50, 50)
    assert nodes[S.NodePosition.BottomRight] == (150, 110)
    assert nodes[S.NodePosition.Top] == (100.5, 50)
    assert len(nodes) == 8


def test_select_tool_moves_shape_exactly():
    m, s = rect_mgr()
    m.current_tool = ShapeType.ToolSelect
    m.deselect()
    drag(m, (100, 80), (115, 90))
    assert s.rect.as_tuple() == (65, 60, 101, 61)
    assert m.current_shape is s


def test_click_empty_with_select_tool_deselects():
    m, _s = rect_mgr()
    m.current_tool = ShapeType.ToolSelect
    click(m, (250, 180))
    assert m.current_shape is None and not m.nodes_visible


@pytest.mark.parametrize("node,delta,expected", [
    (S.NodePosition.BottomRight, (20, 10), (50, 50, 121, 71)),
    (S.NodePosition.TopLeft, (-10, -5), (40, 45, 111, 66)),
    (S.NodePosition.Right, (15, 30), (50, 50, 116, 61)),
    (S.NodePosition.Bottom, (40, 12), (50, 50, 101, 73)),
    (S.NodePosition.Left, (10, 99), (60, 50, 91, 61)),
    (S.NodePosition.Top, (-7, 8), (50, 58, 101, 53)),
    (S.NodePosition.TopRight, (5, -5), (50, 45, 106, 66)),
    (S.NodePosition.BottomLeft, (-5, 5), (45, 50, 106, 66)),
])
def test_resize_handles(node, delta, expected):
    m, s = rect_mgr()
    start = dict(m.visible_nodes())[node]
    drag(m, start, (start[0] + delta[0], start[1] + delta[1]))
    assert s.rect.as_tuple() == expected


def test_proportional_corner_resize_keeps_aspect():
    m, s = rect_mgr()  # 101 x 61
    br = dict(m.visible_nodes())[S.NodePosition.BottomRight]
    drag(m, br, (br[0] + 101, br[1] + 10), Mod.SHIFT)
    r = s.rect
    assert r.x == 50 and r.y == 50
    assert abs(r.w / r.h - 101 / 61) < 0.03


def test_shift_creates_square():
    m = make(tool=ShapeType.DrawingRectangle)
    drag(m, (50, 50), (130, 100), Mod.SHIFT)
    r = m.shapes[0].rect
    assert r.w == r.h


def test_ctrl_while_creating_moves_start_corner():
    m = make(tool=ShapeType.DrawingRectangle)
    m.press(50, 50)
    m.motion(100, 100)
    m.motion(110, 105, Mod.CTRL)
    m.release(110, 105, mods=Mod.CTRL)
    assert m.shapes[0].rect.as_tuple() == (60, 55, 51, 51)


def test_keyboard_nudge_and_resize():
    m, s = rect_mgr()
    m.key("Right")
    assert s.rect.x == 51
    m.key("Down", Mod.SHIFT)
    assert s.rect.y == 60
    m.key("Right", Mod.CTRL)
    assert s.rect.w == 102 and s.rect.x == 51
    m.key("Left", Mod.ALT)
    assert s.rect.x == 50 and s.rect.w == 103


def test_arrow_keys_without_selection_move_pointer():
    moved = []
    m = make(tool=ShapeType.DrawingRectangle, callbacks=Callbacks(move_pointer=lambda dx, dy: moved.append((dx, dy))))
    m.motion(10, 10)
    m.key("Left", Mod.SHIFT)
    assert moved == [(-10, 0)] and m.pointer == (0, 10)


def test_delete_and_shift_delete():
    m, _s = rect_mgr()
    drag(m, (10, 150), (40, 190))
    assert len(m.shapes) == 2
    m.key("Delete")
    assert len(m.shapes) == 1
    m.key("Delete", Mod.SHIFT)
    assert m.shapes == []


def test_tool_shortcuts_in_annotation_mode():
    m = make(mode=RegionCaptureMode.Annotation)
    for key, tool in (("r", ShapeType.DrawingRectangle), ("e", ShapeType.DrawingEllipse),
                      ("KP_5", ShapeType.DrawingArrow), ("b", ShapeType.EffectBlur), ("m", ShapeType.ToolSelect),
                      ("KP_0", ShapeType.RegionRectangle)):
        m.key(key)
        assert m.current_tool == tool
    m.current_tool = ShapeType.DrawingEllipse
    m.key("Tab")
    assert m.current_tool == ShapeType.RegionRectangle
    m.key("Tab")
    assert m.current_tool == ShapeType.DrawingEllipse


def test_editor_has_no_region_tools_and_has_crop_keys():
    m = make()
    with pytest.raises(ValueError):
        m.current_tool = ShapeType.RegionRectangle
    m.key("c")
    assert m.current_tool == ShapeType.ToolCrop
    m.key("x")
    assert m.current_tool == ShapeType.ToolCutOut


def test_escape_stages():
    m, _s = rect_mgr()
    assert m.key("Escape") is None  # deselect + switch to select tool
    assert m.current_shape is None and m.current_tool == ShapeType.ToolSelect
    r = m.key("Escape")
    assert r.result == RegionResult.Close
    t = make(mode=RegionCaptureMode.TaskEditor, tool=ShapeType.ToolSelect)
    assert t.key("Escape").result == RegionResult.AnnotateCancelTask


def test_exit_confirmation_blocks_close():
    m = make(callbacks=Callbacks(confirm_exit=lambda: False), tool=ShapeType.ToolSelect)
    m.is_image_modified = True
    assert m.key("Escape") is None and m.result is None


# ---- clipboard / duplicate / z-order ------------------------------------------------------


def test_duplicate_centres_copy_on_pointer():
    m, s = rect_mgr()
    m.motion(200, 150)
    m.key("d", Mod.CTRL)
    assert len(m.shapes) == 2
    c = m.shapes[1]
    assert c is not s and c.uid != s.uid and m.current_shape is c
    assert c.rect.center == (200, 150) and c.rect.w == s.rect.w
    assert s.rect.as_tuple() == (50, 50, 101, 61)


def test_copy_paste_shape():
    m, _s = rect_mgr()
    m.key("c", Mod.CTRL)
    m.motion(220, 140)
    m.key("v", Mod.CTRL)
    assert len(m.shapes) == 2 and m.shapes[1].rect.center == (220, 140)
    m.key("v", Mod.CTRL)
    assert len(m.shapes) == 3


def test_paste_from_system_clipboard_text_and_image():
    content = {"v": "pasted text"}
    m = make(callbacks=Callbacks(read_clipboard=lambda: content["v"]))
    m.motion(150, 100)
    m.key("v", Mod.CTRL)
    t = m.shapes[-1]
    assert isinstance(t, S.DrawingTextBackground) and t.text == "pasted text" and t.rect.contains(150, 100)
    content["v"] = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
    m.key("v", Mod.CTRL)
    i = m.shapes[-1]
    assert isinstance(i, S.DrawingImage) and i.image.size == (10, 10)


def test_z_order_and_keys():
    m = make(tool=ShapeType.DrawingRectangle)
    for i in range(3):
        drag(m, (10 + 60 * i, 10), (50 + 60 * i, 50))
    a, b, c = m.shapes
    m.select_shape(a)
    m.key("Home")
    assert m.shapes == [b, c, a]
    m.key("Page_Down")
    assert m.shapes == [b, a, c]
    m.key("End")
    assert m.shapes == [a, b, c]
    m.key("Page_Up")
    assert m.shapes == [b, a, c]


def test_step_numbers_follow_z_order():
    m = make(tool=ShapeType.DrawingStep)
    click(m, (50, 50))
    click(m, (150, 50))
    first, second = m.shapes
    m.move_shape_top(first)
    assert (first.number, second.number) == (2, 1)


# ---- undo / redo ------------------------------------------------------------------------


def _ops():
    def create(m):
        m.current_tool = ShapeType.DrawingEllipse
        drag(m, (20, 20), (60, 60))

    def move(m):
        m.current_tool = ShapeType.ToolSelect
        drag(m, (100, 80), (110, 95))

    def resize(m):
        br = dict(m.visible_nodes())[S.NodePosition.BottomRight]
        drag(m, br, (br[0] + 9, br[1] + 4))

    def nudge(m):
        m.key("Up")

    def delete(m):
        m.key("Delete")

    def zorder(m):
        m.key("Home")

    def option(m):
        m.toolbar_model().find("border_size").setter(9)

    def duplicate(m):
        m.key("d", Mod.CTRL)

    def crop(m):
        m.crop_area((10, 10, 200, 150))

    def rotate(m):
        m.rotate(90)

    return [create, move, resize, nudge, delete, zorder, option, duplicate, crop, rotate]


@pytest.mark.parametrize("op", _ops(), ids=lambda f: f.__name__)
def test_undo_redo_restores_exact_state(op):
    m = make(noise(), tool=ShapeType.DrawingRectangle)
    drag(m, (50, 50), (150, 110))
    drag(m, (160, 20), (190, 40))
    m.select_shape(m.shapes[0])
    before = state(m)
    op(m)
    after = state(m)
    assert after != before
    assert m.undo()
    assert state(m) == before
    assert m.redo()
    assert state(m) == after


def test_undo_history_sequence():
    m = make(tool=ShapeType.DrawingRectangle)
    states = [state(m)]
    for i in range(4):
        drag(m, (10 + 40 * i, 10), (40 + 40 * i, 40))
        states.append(state(m))
    for expected in reversed(states[:-1]):
        m.key("z", Mod.CTRL)
        assert state(m) == expected
    assert not m.can_undo
    for expected in states[1:]:
        m.key("y", Mod.CTRL)
        assert state(m) == expected


def test_select_without_moving_adds_no_undo_entry():
    m, _s = rect_mgr()
    m.current_tool = ShapeType.ToolSelect
    n = len(m._undo)
    click(m, (100, 80))
    assert len(m._undo) == n


# ---- right click / middle click actions ---------------------------------------------------


def test_right_click_default_mode_cancels():
    m = make(mode=RegionCaptureMode.Default)
    r = click(m, (50, 50), button=BUTTON_RIGHT)
    assert r.result == RegionResult.Close and m.result is r


def test_right_click_annotation_remove_shape_then_cancel():
    m = make(mode=RegionCaptureMode.Annotation, tool=ShapeType.DrawingRectangle)
    assert m.options.region_capture_action_right_click == RegionCaptureAction.RemoveShapeCancelCapture
    drag(m, (50, 50), (150, 110))
    assert click(m, (100, 80), button=BUTTON_RIGHT) is None
    assert m.shapes == []
    r = click(m, (100, 80), button=BUTTON_RIGHT)
    assert r.result == RegionResult.Close


def test_right_click_while_creating_cancels_shape():
    m = make(tool=ShapeType.DrawingRectangle)
    m.press(10, 10)
    m.motion(60, 60)
    m.release(60, 60, BUTTON_RIGHT)
    assert m.shapes == [] and m.result is None and not m.is_creating


def test_right_click_editor_continues_task():
    m = make(mode=RegionCaptureMode.TaskEditor, tool=ShapeType.DrawingRectangle)
    r = click(m, (10, 10), button=BUTTON_RIGHT)
    assert r.result == RegionResult.AnnotateContinueTask


@pytest.mark.parametrize("action,expected", [
    (RegionCaptureAction.CaptureFullscreen, RegionResult.Fullscreen),
    (RegionCaptureAction.CaptureActiveMonitor, RegionResult.ActiveMonitor),
    (RegionCaptureAction.CancelCapture, RegionResult.Close),
])
def test_configurable_click_actions(action, expected):
    opts = RegionCaptureOptions(region_capture_action_middle_click=action)
    m = make(mode=RegionCaptureMode.Annotation, options=opts)
    r = click(m, (10, 10), button=BUTTON_MIDDLE)
    assert r.result == expected


def test_middle_click_swaps_tool_type():
    m = make(mode=RegionCaptureMode.Annotation)
    assert m.current_tool == ShapeType.RegionRectangle
    click(m, (10, 10), button=BUTTON_MIDDLE)
    assert m.current_tool == m.options.last_annotation_tool


def test_last_region_action():
    m = make(mode=RegionCaptureMode.Default)
    drag(m, (10, 10), (59, 39))
    assert ShapeManager.last_region[0] == (10, 10, 50, 30)
    m2 = make(mode=RegionCaptureMode.Default)
    r = m2.run_action(RegionCaptureAction.CaptureLastRegion)
    assert r.result == RegionResult.LastRegion and r.rect == (10, 10, 50, 30)


# ---- regions and results ------------------------------------------------------------------


def test_quick_crop_rectangle_region():
    m = make(mode=RegionCaptureMode.Default)
    r = drag(m, (10, 20), (109, 69))
    assert r.result == RegionResult.Region and r.rect == (10, 20, 100, 50) and r.mask is None
    img = m.result_image()
    assert img.size == (100, 50)


def test_quick_crop_applies_to_region_tools_in_annotation_mode():
    m = make(mode=RegionCaptureMode.Annotation)
    r = drag(m, (10, 20), (109, 69))
    assert r is not None and r.result == RegionResult.Region


def test_minimum_size_discards_tiny_region():
    m = make(mode=RegionCaptureMode.Default)
    assert drag(m, (10, 10), (12, 12)) is None
    assert m.shapes == [] and m.result is None


def test_ellipse_region_mask():
    m = make(mode=RegionCaptureMode.Default, tool=None)
    m.options.last_region_tool = ShapeType.RegionEllipse
    m._current_tool = ShapeType.RegionEllipse
    r = drag(m, (20, 20), (119, 79))
    assert r.rect == (20, 20, 100, 60)
    mask = np.asarray(r.mask)
    assert mask[30, 50] == 255 and mask[0, 0] == 0 and mask[59, 99] == 0
    out = m.result_image()
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((50, 30))[3] == 255


def test_freehand_region_mask():
    m = make(mode=RegionCaptureMode.Default)
    m._current_tool = ShapeType.RegionFreehand
    m.press(20, 20)
    for p in [(120, 20), (120, 120), (20, 20)]:
        m.motion(*p)
    r = m.release(20, 20)
    assert r.rect == (20, 20, 100, 100)
    mask = np.asarray(r.mask)
    assert mask[10, 90] == 255  # above the diagonal (inside the triangle)
    assert mask[90, 10] == 0


def test_multi_region_union_and_enter():
    m = make(mode=RegionCaptureMode.Default)
    m.options.quick_crop = False
    drag(m, (10, 10), (49, 49))
    drag(m, (100, 60), (139, 99))
    assert len(m.valid_regions) == 2 and m.result is None
    r = m.key("Return")
    assert r.result == RegionResult.Region and r.rect == (10, 10, 130, 90)
    mask = np.asarray(r.mask)
    assert mask[5, 5] == 255 and mask[70, 110] == 255 and mask[70, 10] == 0


def test_double_click_finishes_multi_region():
    m = make(mode=RegionCaptureMode.Default)
    m.options.quick_crop = False
    drag(m, (10, 10), (49, 49))
    r = m.double_click(30, 30)
    assert r.result == RegionResult.Region and r.rect == (10, 10, 40, 40)


def test_space_captures_fullscreen_and_digit_monitor():
    m = make(mode=RegionCaptureMode.Default)
    assert m.key("space").result == RegionResult.Fullscreen
    m2 = make(mode=RegionCaptureMode.Default)
    m2.set_monitors([(0, 0, 150, 200), (150, 0, 150, 200)])
    r = m2.key("2")
    assert r.result == RegionResult.Monitor and r.rect == (150, 0, 150, 200)
    assert m2.result_image().size == (150, 200)


def test_snapping_hover_and_click_selects_window():
    m = make(mode=RegionCaptureMode.Default)
    m.set_snap_rects([(30, 30, 40, 20), (10, 10, 200, 150)])  # child control first, then its window
    m.motion(40, 35)
    assert m.hover_shape is not None and m.hover_shape.rect.as_tuple() == (30, 30, 40, 20)
    m.motion(150, 120)
    assert m.hover_shape.rect.as_tuple() == (10, 10, 200, 150)
    r = click(m, (150, 120))
    assert r.result == RegionResult.Region and r.rect == (10, 10, 200, 150)


def test_snap_rect_is_clipped_to_screen():
    m = make(mode=RegionCaptureMode.Default)
    m.set_snap_rects([(-50, -50, 100, 100)])
    m.motion(10, 10)
    assert m.hover_shape.rect.as_tuple() == (0, 0, 50, 50)


def test_no_hover_while_dragging_or_with_freehand():
    m = make(mode=RegionCaptureMode.Default)
    m.set_snap_rects([(10, 10, 100, 100)])
    m.press(20, 20)
    m.motion(60, 60)
    assert m.hover_shape is None
    m.release(60, 60)
    m2 = make(mode=RegionCaptureMode.Default)
    m2._current_tool = ShapeType.RegionFreehand
    m2.set_snap_rects([(10, 10, 100, 100)])
    m2.motion(50, 50)
    assert m2.hover_shape is None


def test_fixed_size_region():
    opts = RegionCaptureOptions(is_fixed_size=True, fixed_size=Size(60, 40))
    m = make(mode=RegionCaptureMode.Default, options=opts)
    m.motion(100, 100)
    assert m.hover_shape.rect.as_tuple() == (70, 80, 60, 40)
    r = click(m, (100, 100))
    assert r.rect == (70, 80, 60, 40)


def test_alt_snaps_to_snap_sizes():
    opts = RegionCaptureOptions(quick_crop=False, snap_sizes=[Size(100, 50)])
    m = make(blank(400, 300), mode=RegionCaptureMode.Default, options=opts)
    drag(m, (10, 10), (115, 65), Mod.ALT)
    assert m.shapes[0].rect.as_tuple() == (10, 10, 100, 50)


# ---- tool modes ---------------------------------------------------------------------------


def test_screen_color_picker_returns_colour():
    img = blank()
    img.putpixel((40, 30), (12, 34, 56, 255))
    m = make(img, mode=RegionCaptureMode.ScreenColorPicker)
    assert m.options.background_dim_strength == 0 and not m.options.detect_windows
    m.motion(40, 30)
    assert "Hex: 0C2238" in m.info_text()
    r = m.press(40, 30)
    assert r.result == RegionResult.Region and r.color == "#FF0C2238" and r.point == (40, 30)


def test_one_click_returns_point_and_window():
    m = make(mode=RegionCaptureMode.OneClick)
    assert not m.options.show_magnifier
    m.set_snap_rects([(20, 20, 50, 50)])
    r = m.press(30, 40)
    assert r.point == (30, 40) and r.window_rect == (20, 20, 50, 50)


def test_ruler_mode_readout():
    m = make(mode=RegionCaptureMode.Ruler)
    assert not m.options.quick_crop
    drag(m, (10, 10), (40, 50))
    s = m.shapes[0]
    info = ruler_info(s.rect)
    assert info["width"] == 31 and info["height"] == 41
    assert info["distance"] == pytest.approx(50.0)
    assert info["angle"] == pytest.approx(53.13, abs=0.01)
    text = m.area_text(s.rect)
    assert "Width: 31 px | Height: 41 px | Area: 1271 px | Perimeter: 144 px" in text
    assert "Distance: 50.00 px | Angle: 53.13°" in text


def test_info_text_custom_tokens():
    img = blank()
    img.putpixel((7, 9), (255, 128, 0, 255))
    opts = RegionCaptureOptions(use_custom_info_text=True,
                                custom_info_text="X: $x, Y: $y$nR: $r, G: $g, B: $b$nHex: $hex")
    m = ShapeManager(img, RegionCaptureMode.Default, opts, screen_origin=(100, 0))
    m.motion(7, 9)
    assert m.info_text() == "X: 107, Y: 9\nR: 255, G: 128, B: 0\nHex: ff8000"


def test_pixel_info_tokens():
    s = parse_pixel_info("$r255 $g255 $b255|$HEX|$rhex$ghex$bhex|$r1{2}|$c100 $m100 $y100 $k100|$name|$x,$y",
                         "#FFFF0000", (3, 4))
    assert s == "255 0 0|FF0000|ff0000|1|0 100 100 0|Red|3,4"
    assert parse_pixel_info("$b1", "#FF000080", (0, 0)) == "0.502"


def test_editor_info_text_and_area_text():
    m = make()
    m.motion(12.4, 7.6)
    assert m.info_text() == "X: 12 Y: 8"
    assert m.area_text(S.Rect(1, 2, 30, 40)) == "X: 1 Y: 2 W: 30 H: 40"


def test_magnifier_grid():
    img = blank()
    img.putpixel((50, 50), (0, 0, 255, 255))
    m = make(img, mode=RegionCaptureMode.Default)
    m.motion(50, 50)
    mag = m.magnifier()
    assert mag.count == 15 and mag.pixel_size == 10
    assert mag.pixels.getpixel((7, 7))[:3] == (0, 0, 255)
    assert mag.center_color == "#FF0000FF"
    bmp = mag.render()
    assert bmp.size == (149, 149)
    assert bmp.getpixel((74, 74))[:3] == (0, 0, 255)  # centre of the centre cell
    m.scroll(0, -1)
    assert m.magnifier().count == 17
    edge = m.magnifier(0, 0)
    assert edge.pixels.getpixel((0, 0))[:3] == (200, 200, 200)  # outside the canvas


def test_scroll_down_hides_magnifier_at_minimum():
    m = make(mode=RegionCaptureMode.Default)
    m.options.magnifier_pixel_count = 5
    m.scroll(0, 1)
    assert m.options.magnifier_pixel_count == 3 and m.options.show_magnifier
    m.scroll(0, 1)
    assert not m.options.show_magnifier


# ---- toolbar model ------------------------------------------------------------------------


def test_toolbar_lists_tools_per_mode():
    ed = make().toolbar_model()
    types = [t.value for t in ed.tools if t.kind == "tool"]
    assert ShapeType.RegionRectangle not in types and ShapeType.ToolCrop in types and ShapeType.ToolSelect in types
    an = make(mode=RegionCaptureMode.Annotation).toolbar_model()
    types = [t.value for t in an.tools if t.kind == "tool"]
    assert ShapeType.RegionRectangle in types and ShapeType.ToolCrop not in types
    image_ids = {i.id for i in ed.image_menu}
    assert image_ids >= {"image_size", "canvas_size", "rotate_cw", "flip_vertical", "auto_crop_image"}
    assert an.image_menu == [] and an.capture_menu


def test_toolbar_option_visibility_matrix():
    m = make(tool=ShapeType.DrawingRectangle)
    tb = m.toolbar_model()
    vis = {i.id for i in tb.tool_options + tb.colors if i.visible}
    assert {"border_color", "fill_color", "border_size", "corner_radius", "border_style", "shadow"} <= vis
    assert "blur_radius" not in vis
    m.current_tool = ShapeType.EffectBlur
    tb = m.toolbar_model()
    vis = {i.id for i in tb.tool_options + tb.colors if i.visible}
    assert vis == {"blur_radius"}
    m.current_tool = ShapeType.DrawingArrow
    vis = {i.id for i in m.toolbar_model().tool_options if i.visible}
    assert {"center_points", "arrow_head_direction"} <= vis


def test_toolbar_setter_updates_selected_shape():
    m, s = rect_mgr()
    item = m.toolbar_model().find("border_size")
    assert item.value == 4
    item.setter(10)
    assert s.border_size == 10 and m.annotation.border_size == 10
    m.undo()
    assert m.shapes[0].border_size == 4


def test_selecting_shape_loads_its_options_into_toolbar():
    m, s = rect_mgr()
    s.border_size = 13
    m.current_tool = ShapeType.ToolSelect
    m.deselect()
    click(m, (100, 80))
    assert m.toolbar_model().find("border_size").value == 13


def test_text_tool_border_targets_text_options():
    m = make(tool=ShapeType.DrawingTextOutline)
    item = m.toolbar_model().find("border_color")
    item.setter("#FF00FF00")
    assert m.annotation.text_outline_border_color == "#FF00FF00"
    assert m.annotation.border_color == "#FFF03C3C"


def test_toolbar_actions_run():
    m, _s = rect_mgr()
    tb = m.toolbar_model()
    tb.find("delete").activate()
    assert m.shapes == []
    m.toolbar_model().find("undo").activate()
    assert len(m.shapes) == 1
    m.toolbar_model().find("rotate_cw").activate()
    assert m.canvas.size == (200, 300)


# ---- drawing the overlay ------------------------------------------------------------------


def _draw(m, w=None, h=None, **kw):
    w = w or m.canvas.width
    h = h or m.canvas.height
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    m.draw(cairo.Context(surf), **kw)
    return R.surface_to_pil(surf)


def test_draw_dims_outside_regions():
    m = make(blank(), mode=RegionCaptureMode.Default)
    m.options.quick_crop = False
    m.options.show_magnifier = m.options.show_info = False
    drag(m, (20, 20), (119, 99))
    shown = _draw(m)
    inside, outside = px(shown, 60, 60), px(shown, 200, 150)
    assert inside == WHITE
    assert outside[0] < 240 and outside == (229, 229, 229)  # 10 % black


def test_draw_view_transform_and_nodes():
    m, _s = rect_mgr()
    m.options.show_magnifier = m.options.show_info = False
    shown = _draw(m, 600, 400, view_scale=2.0, view_offset=(0, 0))
    assert close(px(shown, 150, 100), RED)  # (75, 50) top edge at 2x
    ring = [px(shown, 301, y) for y in range(222, 232)]  # below the stroke, through the BR node
    assert any(sum(c) < 400 for c in ring)  # node outline at (150,110)*2, 13 widget px wide
    assert px(shown, 301, 240) == WHITE
    assert px(shown, 500, 350) == WHITE


def test_draw_crosshair_and_cursor_info():
    m = make(mode=RegionCaptureMode.Default)
    m.options.show_crosshair = True
    m.motion(100, 100)
    shown = _draw(m)
    assert px(shown, 20, 100) != WHITE  # crosshair line across the screen
    assert px(shown, 100, 180) != WHITE
    assert px(shown, 150, 150) != WHITE  # magnifier next to the cursor
