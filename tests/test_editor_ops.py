"""Editor-mode image operations: crop, cut out, resize, canvas, rotate, flip, auto crop, insert."""
from __future__ import annotations

import numpy as np
import pytest
from editor_helpers import blank, drag, make, px, state
from PIL import Image

from sharel.core.enums import CutOutEffectType, ImageInsertMethod, RegionCaptureMode, ShapeType
from sharel.editor import render as R
from sharel.editor import shapes as S
from sharel.editor.manager import Callbacks, Mod


def marked(w=200, h=100):
    """White image with coloured marker pixels at known places."""
    img = blank(w, h)
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((w - 1, 0), (0, 255, 0, 255))
    img.putpixel((0, h - 1), (0, 0, 255, 255))
    img.putpixel((w - 1, h - 1), (0, 0, 0, 255))
    img.putpixel((50, 20), (255, 0, 255, 255))
    return img


def test_crop_tool_drag_and_confirm():
    m = make(marked(), tool=ShapeType.ToolCrop)
    drag(m, (40, 10), (139, 69))
    assert [s.shape_type for s in m.shapes] == [ShapeType.ToolCrop]
    buttons = dict(m.tool_buttons())
    assert set(buttons) == {"confirm", "cancel"}
    cx, cy = buttons["confirm"].center
    m.press(cx, cy)
    m.release(cx, cy)
    assert m.canvas.size == (100, 60)
    assert px(m.canvas, 10, 10) == (255, 0, 255)  # (50, 20) -> (10, 10)
    assert m.tool_shapes == []


def test_crop_tool_is_clamped_and_enter_confirms():
    m = make(marked(), tool=ShapeType.ToolCrop)
    drag(m, (150, 50), (400, 300))
    assert m.shapes[0].rect.as_tuple() == (150, 50, 50, 50)
    m.key("Return")
    assert m.canvas.size == (50, 50)
    assert px(m.canvas, 49, 49) == (0, 0, 0)


def test_crop_cancel_button_keeps_image():
    m = make(marked(), tool=ShapeType.ToolCrop)
    drag(m, (40, 10), (139, 69))
    cx, cy = dict(m.tool_buttons())["cancel"].center
    m.press(cx, cy)
    m.release(cx, cy)
    assert m.canvas.size == (200, 100) and m.shapes == []


def test_crop_moves_shapes_with_the_image():
    m = make(marked(), tool=ShapeType.DrawingRectangle)
    drag(m, (60, 30), (80, 50))
    m.crop_area((40, 10, 100, 60))
    assert m.shapes[0].rect.as_tuple() == (20, 20, 21, 21)


def test_cut_out_columns():
    m = make(marked(), tool=ShapeType.ToolCutOut)
    drag(m, (60, 40), (99, 50))  # wider than tall -> remove columns 60..99
    t = m.tool_shapes[0]
    assert t.is_horizontal_trim(m.minimum_size)
    assert m.confirm_tool()
    assert m.canvas.size == (160, 100)
    assert px(m.canvas, 50, 20) == (255, 0, 255)  # before the cut: unchanged
    assert px(m.canvas, 159, 0) == (0, 255, 0)  # right edge moved left by 40


def test_cut_out_rows_and_shape_collapse():
    m = make(marked(), tool=ShapeType.DrawingRectangle)
    drag(m, (10, 70), (30, 90))  # below the cut
    drag(m, (100, 35), (120, 45))  # inside the cut -> deleted
    m.current_tool = ShapeType.ToolCutOut
    drag(m, (80, 30), (90, 59))  # taller than wide -> remove rows 30..59
    m.key("Return")
    assert m.canvas.size == (200, 70)
    assert px(m.canvas, 0, 69) == (0, 0, 255)
    assert len(m.shapes) == 1 and m.shapes[0].rect.as_tuple() == (10, 40, 21, 21)


def test_cut_out_spanning_whole_canvas_is_invalid():
    m = make(marked(), tool=ShapeType.ToolCutOut)
    drag(m, (0, 40), (199, 50))
    assert m.tool_shapes == []
    assert m.canvas.size == (200, 100)


@pytest.mark.parametrize("effect", [CutOutEffectType.ZigZag, CutOutEffectType.TornEdge, CutOutEffectType.Wave])
def test_cut_out_edge_effects(effect):
    m = make(blank(200, 100, (0, 0, 0, 255)), tool=ShapeType.ToolCutOut, cut_out_effect_type=effect,
             cut_out_effect_size=10)
    drag(m, (80, 40), (119, 50))
    m.confirm_tool()
    assert m.canvas.size == (160, 100)
    alpha = np.asarray(m.canvas.getchannel("A"))
    assert (alpha[:, :60] == 255).all()  # far from the cut: intact
    assert (alpha[:, 60:100] < 255).any()  # the cut edges are shaped (transparent bits)
    assert (alpha[:, 100:] == 255).all()


def test_cut_out_middle_function():
    img = Image.fromarray(np.arange(10 * 4 * 4, dtype=np.uint8).reshape(4, 10, 4) | 1, "RGBA")
    out = R.cut_out_middle(img, True, 3, 4)
    assert out.size == (6, 4)
    a, b = np.asarray(out), np.asarray(img)
    assert np.array_equal(a[:, :3], b[:, :3]) and np.array_equal(a[:, 3:], b[:, 7:])


def test_resize_image():
    img = blank(200, 100)
    img.paste((255, 0, 0, 255), (0, 0, 4, 4))
    img.paste((0, 0, 0, 255), (196, 96, 200, 100))
    m = make(img)
    assert m.resize_image(100, 50, "NearestNeighbor")
    assert m.canvas.size == (100, 50)
    assert px(m.canvas, 0, 0) == (255, 0, 0) and px(m.canvas, 1, 1) == (255, 0, 0)
    assert px(m.canvas, 99, 49) == (0, 0, 0)
    assert px(m.canvas, 50, 25) == (255, 255, 255)
    m.undo()
    assert m.canvas.size == (200, 100)


def test_resize_canvas_adds_coloured_margins_and_moves_shapes():
    m = make(marked(), tool=ShapeType.DrawingRectangle)
    drag(m, (10, 10), (30, 30))
    assert m.resize_canvas(10, 20, 30, 40, "#FF00FF00")
    assert m.canvas.size == (240, 160)
    assert px(m.canvas, 10, 20) == (255, 0, 0)  # old (0, 0)
    assert px(m.canvas, 5, 5) == (0, 255, 0)  # margin colour
    assert px(m.canvas, 239, 159) == (0, 255, 0)
    assert m.shapes[0].rect.as_tuple() == (20, 30, 21, 21)


def test_resize_canvas_negative_margin_trims():
    m = make(marked())
    m.resize_canvas(-10, 0, 0, -10)
    assert m.canvas.size == (190, 90)
    assert px(m.canvas, 189, 0) == (0, 255, 0)


@pytest.mark.parametrize("deg,size,where", [
    (90, (100, 200), {(99, 0): (255, 0, 0), (0, 0): (0, 0, 255), (99, 199): (0, 255, 0)}),
    (180, (200, 100), {(199, 99): (255, 0, 0), (0, 0): (0, 0, 0)}),
    (270, (100, 200), {(0, 199): (255, 0, 0), (0, 0): (0, 255, 0)}),
])
def test_rotate(deg, size, where):
    m = make(marked())
    m.rotate(deg)
    assert m.canvas.size == size
    for xy, color in where.items():
        assert px(m.canvas, *xy) == color, (deg, xy)


def test_flip():
    m = make(marked())
    m.flip(True)
    assert px(m.canvas, 199, 0) == (255, 0, 0) and px(m.canvas, 149, 20) == (255, 0, 255)
    m.flip(False)
    assert px(m.canvas, 199, 99) == (255, 0, 0)
    m.undo()
    m.undo()
    assert px(m.canvas, 0, 0) == (255, 0, 0)


def test_auto_crop_trims_uniform_border():
    img = blank(120, 80)
    img.paste((10, 20, 30, 255), (30, 20, 70, 50))
    m = make(img)
    crop = m.auto_crop_image()
    assert crop == (30, 20, 40, 30)
    assert isinstance(m.current_shape, S.ToolCrop)  # opened for confirmation, like ShareX
    m.key("Return")
    assert m.canvas.size == (40, 30)
    m2 = make(img)
    assert m2.auto_crop_image(apply=True) == (30, 20, 40, 30)
    assert m2.canvas.size == (40, 30) and px(m2.canvas, 0, 0) == (10, 20, 30)


def test_auto_crop_transparent_border():
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (10, 5, 20, 45))
    assert R.find_auto_crop_rect(img) == (10, 5, 10, 40)


def test_auto_crop_nothing_to_trim():
    m = make(blank(50, 50))
    assert m.auto_crop_image() is None


def _blue(w=40, h=30):
    return Image.new("RGBA", (w, h), (0, 0, 255, 255))


def test_insert_image_center():
    m = make(marked())
    s = m.insert_image(_blue(), ImageInsertMethod.Center)
    assert isinstance(s, S.DrawingImage) and s.rect.as_tuple() == (80, 35, 40, 30)
    assert px(m.render_output(), 100, 50) == (0, 0, 255)


def test_insert_image_expand_down():
    m = make(marked())
    m.insert_image(_blue(250, 30), ImageInsertMethod.CanvasExpandDown)
    assert m.canvas.size == (250, 130)
    assert m.shapes[0].rect.as_tuple() == (0, 100, 250, 30)
    out = m.render_output()
    assert px(out, 10, 120) == (0, 0, 255)
    assert px(out, 0, 0) == (255, 0, 0)


def test_insert_image_expand_right():
    m = make(marked())
    m.insert_image(_blue(30, 150), ImageInsertMethod.CanvasExpandRight)
    assert m.canvas.size == (230, 150)
    assert m.shapes[0].rect.as_tuple() == (200, 0, 30, 150)
    m.undo()
    assert m.canvas.size == (200, 100) and m.shapes == []


def test_insert_image_file_and_from_screen(tmp_path):
    f = tmp_path / "i.png"
    _blue(12, 12).save(f)
    m = make(marked(), callbacks=Callbacks(capture_screen_image=lambda: _blue(20, 20)))
    m.insert_image_file(f)
    m.insert_image_from_screen()
    assert [s.image.size for s in m.shapes] == [(12, 12), (20, 20)]


def test_add_image_effects_callback():
    m = make(marked(), callbacks=Callbacks(apply_effects=lambda img: img.convert("L").convert("RGBA")))
    assert m.add_image_effects()
    r, g, b = px(m.canvas, 0, 0)
    assert r == g == b
    m.undo()
    assert px(m.canvas, 0, 0) == (255, 0, 0)
    none = make(marked(), callbacks=Callbacks(apply_effects=lambda img: None))
    assert not none.add_image_effects() and not none.can_undo


def test_new_image_and_load_image(tmp_path):
    m = make(marked(), tool=ShapeType.DrawingRectangle)
    drag(m, (10, 10), (30, 30))
    m.new_image(64, 32, "#FF112233")
    assert m.canvas.size == (64, 32) and px(m.canvas, 5, 5) == (0x11, 0x22, 0x33) and m.shapes == []
    m.new_image(10, 10, transparent=True)
    assert m.canvas.getpixel((0, 0))[3] == 0
    f = tmp_path / "x.png"
    _blue(7, 9).save(f)
    m.load_image(f)
    assert m.canvas.size == (7, 9)
    m.undo()
    m.undo()
    m.undo()
    assert m.canvas.size == (200, 100) and len(m.shapes) == 1


@pytest.mark.parametrize("op", [
    lambda m: m.crop_area((10, 10, 50, 50)),
    lambda m: m.cut_out((60, 40, 40, 10)),
    lambda m: m.resize_image(50, 25),
    lambda m: m.resize_canvas(5, 5, 5, 5),
    lambda m: m.rotate(90),
    lambda m: m.flip(False),
    lambda m: m.auto_crop_image(apply=True),
    lambda m: m.insert_image(_blue(), ImageInsertMethod.CanvasExpandDown),
    lambda m: m.new_image(30, 30),
], ids=["crop", "cut_out", "resize", "canvas", "rotate", "flip", "auto_crop", "insert", "new"])
def test_image_ops_are_undoable(op):
    img = blank(200, 100)
    img.paste((10, 20, 30, 255), (30, 20, 70, 50))
    m = make(img, tool=ShapeType.DrawingEllipse)
    drag(m, (100, 60), (150, 90))
    before = state(m)
    op(m)
    after = state(m)
    assert after != before
    m.undo()
    assert state(m) == before
    m.redo()
    assert state(m) == after


def test_editor_save_copy_upload_print_shortcuts():
    got = []
    cb = Callbacks(save_image=lambda i: got.append(("save", i.size)),
                   save_image_as=lambda i: got.append(("as", i.size)),
                   copy_image=lambda i: got.append(("copy", i.size)), upload_image=lambda i: got.append(("up", i.size)),
                   print_image=lambda i: got.append(("print", i.size)))
    m = make(marked(), callbacks=cb, tool=ShapeType.ToolSelect)
    m.key("s", Mod.CTRL)
    m.key("s", Mod.CTRL | Mod.SHIFT)
    m.key("c", Mod.CTRL)
    m.key("u", Mod.CTRL)
    m.key("p", Mod.CTRL)
    assert got == [("save", (200, 100)), ("as", (200, 100)), ("copy", (200, 100)), ("up", (200, 100)),
                   ("print", (200, 100))]


def test_editor_auto_copy_on_modify():
    copies = []
    m = make(marked(), callbacks=Callbacks(copy_image=copies.append), tool=ShapeType.DrawingRectangle)
    m.options.editor_auto_copy_image = True
    drag(m, (10, 10), (40, 40))
    assert copies and copies[-1].size == (200, 100)


def test_render_output_and_region_result_image_with_annotations():
    m = make(blank(200, 100), mode=RegionCaptureMode.Annotation, tool=ShapeType.DrawingRectangle,
             fill_color="#FF0000FF", border_size=0)
    drag(m, (20, 20), (40, 40))
    m.current_tool = ShapeType.RegionRectangle
    r = drag(m, (10, 10), (59, 59))
    img = m.result_image(r)
    assert img.size == (50, 50)
    assert px(img, 15, 15) == (0, 0, 255)  # the drawing at (25, 25) lands at (15, 15) of the crop
    assert px(img, 45, 45) == (255, 255, 255)


def test_add_cursor_api():
    m = make(blank(60, 60, (0, 128, 0, 255)), mode=RegionCaptureMode.Default)
    s = m.add_cursor(position=(10, 10))
    assert isinstance(s, S.DrawingCursor) and s.rect.x == 9
    out = m.render_output()
    assert px(out, 10, 12) != (0, 128, 0)
