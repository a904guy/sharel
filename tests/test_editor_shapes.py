"""Every shape type drawn through the ShapeManager with synthetic input, checked on output pixels."""
from __future__ import annotations

import numpy as np
import pytest
from editor_helpers import RED, WHITE, blank, click, close, count_color, drag, make, noise, px
from PIL import Image

from sharel.core.config import AnnotationOptions
from sharel.core.enums import ArrowHeadDirection, BorderStyle, RegionCaptureMode, ShapeType, StepType
from sharel.editor import render as R
from sharel.editor import shapes as S
from sharel.editor import stickers as ST
from sharel.editor.manager import PENDING, Callbacks, Mod

# ---- geometry model ---------------------------------------------------------------------


def test_rect_is_inclusive_like_sharex():
    r = S.create_rect((10, 10), (50, 40))
    assert r.as_tuple() == (10, 10, 41, 31)
    assert S.create_rect((50, 40), (10, 10)) == r
    s = S.create_shape(ShapeType.DrawingRectangle)
    s.rect = S.Rect(5, 6, 10, 20)
    assert s.start == (5, 6) and s.end == (14, 25)


def test_every_shape_type_has_a_model():
    for t in ShapeType:
        if t == ShapeType.ToolSelect:
            with pytest.raises(ValueError):
                S.create_shape(t)
            continue
        s = S.create_shape(t)
        assert s.shape_type == t
        c = s.clone()
        assert c.uid == s.uid and type(c) is type(s)
        assert s.duplicate().uid != s.uid


def test_config_round_trip_matches_sharex_targets():
    a = AnnotationOptions(text_border_color="#FF00FF00", step_fill_color="#FF0000FF", border_size=7)
    t = S.create_shape(ShapeType.DrawingTextBackground, a)
    assert t.border_color == "#FF00FF00" and t.fill_color == a.text_fill_color
    step = S.create_shape(ShapeType.DrawingStep, a)
    assert step.fill_color == "#FF0000FF"
    rect = S.create_shape(ShapeType.DrawingRectangle, a)
    assert rect.border_size == 7 and rect.corner_radius == a.drawing_corner_radius
    rect.border_size = 3
    rect.save_config(a)
    assert a.border_size == 3


@pytest.mark.parametrize("n,typ,expected", [
    (1, StepType.Numbers, "1"), (27, StepType.LettersUppercase, "AA"), (3, StepType.LettersLowercase, "c"),
    (1994, StepType.RomanNumeralsUppercase, "MCMXCIV"), (4, StepType.RomanNumeralsLowercase, "iv"),
])
def test_step_labels(n, typ, expected):
    assert S.step_text(n, typ) == expected


# ---- drawing shapes ---------------------------------------------------------------------


def test_rectangle_border_and_no_fill():
    m = make(tool=ShapeType.DrawingRectangle, drawing_corner_radius=0)
    drag(m, (50, 50), (150, 110))
    s = m.shapes[0]
    assert s.rect.as_tuple() == (50, 50, 101, 61)
    out = m.render_output()
    assert close(px(out, 100, 50), RED)  # top edge
    assert close(px(out, 50, 80), RED)  # left edge
    assert close(px(out, 150, 80), RED)  # right edge
    assert px(out, 100, 80) == WHITE  # transparent fill
    assert px(out, 100, 30) == WHITE  # outside


def test_rectangle_fill_color():
    m = make(tool=ShapeType.DrawingRectangle, fill_color="#FF0000FF")
    drag(m, (50, 50), (150, 110))
    out = m.render_output()
    assert close(px(out, 100, 80), (0, 0, 255), 5)
    assert px(out, 200, 80) == WHITE


def test_rectangle_corner_radius_rounds_the_corner():
    sharp = make(tool=ShapeType.DrawingRectangle, drawing_corner_radius=0)
    drag(sharp, (50, 50), (150, 110))
    round_ = make(tool=ShapeType.DrawingRectangle, drawing_corner_radius=20)
    drag(round_, (50, 50), (150, 110))
    assert close(px(sharp.render_output(), 50, 50), RED)
    assert px(round_.render_output(), 50, 50) == WHITE
    assert close(px(round_.render_output(), 100, 50), RED)


def test_shadow_is_drawn_offset():
    with_shadow = make(tool=ShapeType.DrawingRectangle, shadow=True, shadow_offset_y=4, drawing_corner_radius=0)
    drag(with_shadow, (50, 50), (150, 110))
    without = make(tool=ShapeType.DrawingRectangle, shadow=False, drawing_corner_radius=0)
    drag(without, (50, 50), (150, 110))
    a = px(with_shadow.render_output(), 100, 114)
    b = px(without.render_output(), 100, 114)
    assert b == WHITE
    assert sum(a) < sum(WHITE) - 60  # translucent black shadow


def _hline(style: BorderStyle, tool=ShapeType.DrawingLine, **kw):
    m = make(tool=tool, border_style=style, line_center_point_count=0, **kw)
    drag(m, (20, 100), (220, 100))
    return m.render_output()


def test_dash_pattern_has_gaps():
    solid = _hline(BorderStyle.Solid)
    dash = _hline(BorderStyle.Dash)
    # pen width 4: dash = 12 px on, 4 px off starting at the line start
    for x in (34, 50, 66):
        assert close(px(solid, x, 100), RED)
        assert px(dash, x, 100) == WHITE, x
    for x in (26, 42, 58):
        assert close(px(dash, x, 100), RED), x


def test_dot_pattern():
    dot = _hline(BorderStyle.Dot)
    reds = [close(px(dot, x, 100), RED) for x in range(30, 70)]
    assert 12 <= sum(reds) <= 28  # roughly half the pixels: 4 on, 4 off


def test_arrow_head_pixels():
    line = _hline(BorderStyle.Solid)
    arrow = _hline(BorderStyle.Solid, ShapeType.DrawingArrow, arrow_head_direction=ArrowHeadDirection.End)
    # head: 24 px long, 16 px wide at its base, tip at x=220
    assert close(px(arrow, 200, 105), RED)
    assert px(line, 200, 105) == WHITE
    assert px(arrow, 40, 105) == WHITE  # no head at the start


def test_arrow_both_heads():
    both = _hline(BorderStyle.Solid, ShapeType.DrawingArrow, arrow_head_direction=ArrowHeadDirection.Both)
    assert close(px(both, 200, 105), RED)
    assert close(px(both, 40, 105), RED)
    start = _hline(BorderStyle.Solid, ShapeType.DrawingArrow, arrow_head_direction=ArrowHeadDirection.Start)
    assert close(px(start, 40, 105), RED)
    assert px(start, 200, 105) == WHITE


def test_ellipse_outline():
    m = make(tool=ShapeType.DrawingEllipse)
    drag(m, (50, 50), (150, 110))
    out = m.render_output()
    assert close(px(out, 100, 51), RED)
    assert close(px(out, 51, 80), RED)
    assert px(out, 100, 80) == WHITE
    assert px(out, 54, 54) == WHITE  # corner of the bounding box is outside the ellipse


def test_freehand_follows_pointer():
    m = make(tool=ShapeType.DrawingFreehand)
    m.press(30, 30)
    for p in [(60, 40), (90, 80), (120, 60), (150, 120)]:
        m.motion(*p)
    m.release(150, 120)
    s = m.shapes[0]
    assert s.points[0] == (30, 30) and s.points[-1] == (150, 120)
    out = m.render_output()
    assert close(px(out, 75, 60), RED)  # midway between (60,40) and (90,80)
    assert px(out, 30, 120) == WHITE


def test_freehand_arrow_has_head():
    plain = make(tool=ShapeType.DrawingFreehand)
    drag(plain, (20, 100), (220, 100), steps=20)
    arrow = make(tool=ShapeType.DrawingFreehandArrow)
    drag(arrow, (20, 100), (220, 100), steps=20)
    assert close(px(arrow.render_output(), 200, 105), RED)
    assert px(plain.render_output(), 200, 105) == WHITE


def test_line_center_point_drag_bends_line():
    m = make(tool=ShapeType.DrawingLine, line_center_point_count=1)
    drag(m, (20, 120), (220, 120))
    line = m.shapes[0]
    assert len(line.points) == 3 and line.points[1] == (120, 120)
    assert not line.center_node_active
    nodes = dict(m.visible_nodes())
    assert nodes[1] == (120, 120)
    drag(m, (120, 120), (120, 50))
    assert line.center_node_active and line.points[1] == (120, 50)
    out = m.render_output()
    assert close(px(out, 120, 50), RED)  # the curve passes through the dragged node
    assert px(out, 120, 120) == WHITE  # the old straight chord is empty
    assert line.rect.y <= 50


def test_text_outline_draws_text_and_outline():
    m = make(blank(color=(128, 128, 128, 255)), tool=ShapeType.DrawingTextOutline,
             callbacks=Callbacks(request_text=lambda s, initial: "Hello"))
    click(m, (150, 100))
    s = m.shapes[0]
    assert s.text == "Hello" and s.rect.w > 40
    out = m.render_output()
    box = tuple(int(v) for v in (s.rect.x, s.rect.y, s.rect.right, s.rect.bottom))
    assert count_color(out, box, WHITE, 30) > 50  # text fill
    assert count_color(out, box, RED, 40) > 50  # outline
    assert count_color(out, (0, 0, 60, 60), WHITE, 30) == 0


def test_text_background_box_and_text():
    m = make(tool=ShapeType.DrawingTextBackground, callbacks=Callbacks(request_text=lambda s, i: "World"))
    click(m, (150, 100))
    s = m.shapes[0]
    out = m.render_output()
    box = tuple(int(v) for v in (s.rect.x + 3, s.rect.y + 3, s.rect.right - 3, s.rect.bottom - 3))
    assert count_color(out, box, RED, 10) > 100  # TextFillColor
    assert count_color(out, box, WHITE, 30) > 30  # white text
    assert s.rect.contains(150, 100)


def test_text_cancel_removes_shape_and_undo_entry():
    m = make(tool=ShapeType.DrawingTextBackground, callbacks=Callbacks(request_text=lambda s, i: None))
    click(m, (150, 100))
    assert m.shapes == [] and not m.can_undo


def test_text_async_pending_then_set_text():
    asked = []
    m = make(tool=ShapeType.DrawingTextOutline,
             callbacks=Callbacks(request_text=lambda s, i: asked.append((s, i)) or PENDING))
    click(m, (150, 100))
    shape, initial = asked[0]
    assert initial == "" and shape in m.shapes
    m.set_text(shape, "Later")
    assert shape.text == "Later" and m.current_shape is shape


def test_double_click_re_edits_text():
    answers = iter(["One", "Two"])
    m = make(tool=ShapeType.DrawingTextBackground, callbacks=Callbacks(request_text=lambda s, i: next(answers)))
    click(m, (150, 100))
    s = m.shapes[0]
    m.double_click(150, 100)
    assert s.text == "Two"
    m.undo()
    assert m.shapes[0].text == "One"


def test_speech_balloon_tail():
    m = make(tool=ShapeType.DrawingSpeechBalloon, callbacks=Callbacks(request_text=lambda s, i: "Hi"))
    click(m, (150, 60))
    s = m.shapes[0]
    r = s.rect
    assert s.tail_position == (r.x, r.bottom + 30)
    out = m.render_output()
    tx, ty = s.tail_position
    assert close(px(out, int(tx) + 2, int(ty) - 6), RED)  # inside the tail near its tip
    assert px(out, int(r.right) + 20, int(ty) - 6) == WHITE
    # the tail tip is the draggable Extra node
    nodes = dict(m.visible_nodes())
    assert nodes[S.NodePosition.Extra] == s.tail_position
    drag(m, s.tail_position, (250, 180))
    assert s.tail_position == (250, 180)


def test_step_numbers_auto_increment_and_draw():
    m = make(tool=ShapeType.DrawingStep)
    for p in [(50, 50), (120, 50), (190, 50)]:
        click(m, p)
    steps = [s for s in m.shapes if isinstance(s, S.DrawingStep)]
    assert [s.number for s in steps] == [1, 2, 3]
    out = m.render_output()
    s = steps[0]
    r = s.rect
    assert r.contains(50, 50)
    box = (int(r.x), int(r.y), int(r.right), int(r.bottom))
    assert count_color(out, box, RED, 10) > 200  # filled circle
    assert count_color(out, box, WHITE, 40) > 15  # the number (white = step border colour)


def test_step_letters_and_starting_value():
    m = make(tool=ShapeType.DrawingStep, step_type=StepType.LettersUppercase)
    m.starting_step_number = 3
    click(m, (50, 50))
    click(m, (120, 50))
    assert [s.text for s in m.shapes] == ["C", "D"]


def test_step_font_size_changes_circle():
    small = make(tool=ShapeType.DrawingStep, step_font_size=12)
    click(small, (60, 60))
    big = make(tool=ShapeType.DrawingStep, step_font_size=40)
    click(big, (60, 60))
    assert big.shapes[0].rect.w > small.shapes[0].rect.w + 10


def test_magnify_enlarges_underlying_image():
    img = blank(200, 200)
    img.paste((0, 0, 0, 255), (100, 100, 102, 102))
    m = make(img, tool=ShapeType.DrawingMagnify, border_size=0, magnifier_strength=200)
    drag(m, (61, 61), (141, 141))
    s = m.shapes[0]
    assert s.rect.w == s.rect.h  # forced proportional
    out = m.render_output()
    black = count_color(out, (60, 60, 145, 145), (0, 0, 0), 30)
    assert black >= 12  # 2x2 black block shown at 2x -> ~4x4
    assert count_color(img, (60, 60, 145, 145), (0, 0, 0), 30) == 4


def test_image_from_file(tmp_path):
    f = tmp_path / "blue.png"
    Image.new("RGBA", (20, 10), (0, 0, 255, 255)).save(f)
    m = make(tool=ShapeType.DrawingImage, callbacks=Callbacks(request_image_file=lambda: str(f)))
    click(m, (100, 100))
    s = m.shapes[0]
    assert s.rect.as_tuple() == (90, 95, 20, 10)
    out = m.render_output()
    assert px(out, 95, 98) == (0, 0, 255)
    assert px(out, 85, 98) == WHITE
    # Ctrl+click reuses the last file without asking
    m2 = make(tool=ShapeType.DrawingImage, callbacks=Callbacks(request_image_file=lambda: None))
    m2.annotation.last_image_file_path = str(f)
    click(m2, (50, 50), Mod.CTRL)
    assert len(m2.shapes) == 1 and m2.shapes[0].image.size == (20, 10)


def test_image_file_cancel_creates_nothing():
    m = make(tool=ShapeType.DrawingImage, callbacks=Callbacks(request_image_file=lambda: None))
    click(m, (100, 100))
    assert m.shapes == []


def test_image_screen_copies_area_and_moves():
    img = blank(200, 150)
    img.paste((0, 200, 0, 255), (20, 20, 40, 40))
    m = make(img, tool=ShapeType.DrawingImageScreen)
    drag(m, (10, 10), (49, 49))
    s = m.shapes[0]
    assert s.image is not None and s.image.size == (40, 40)
    m.current_tool = ShapeType.ToolSelect
    drag(m, (30, 30), (130, 80))
    out = m.render_output()
    assert px(out, 130, 80) == (0, 200, 0)  # moved copy
    assert px(out, 30, 30) == (0, 200, 0)  # original canvas untouched


def test_sticker_from_pack():
    pack = ST.list_packs()
    assert "Twemoji" in pack
    sticker = ST.list_stickers("Twemoji")[0]
    m = make(tool=ShapeType.DrawingSticker, sticker_size=48,
             callbacks=Callbacks(request_sticker=lambda: str(sticker)))
    click(m, (100, 100))
    s = m.shapes[0]
    assert s.image.size == (48, 48)
    assert s.rect.as_tuple() == (76, 76, 48, 48)
    out = m.render_output()
    non_white = 48 * 48 - count_color(out, (76, 76, 124, 124), WHITE, 5)
    assert non_white > 500
    assert m.annotation.last_sticker_path == str(sticker)


def test_cursor_default_arrow_bitmap():
    m = make(blank(color=(0, 128, 0, 255)), tool=ShapeType.DrawingCursor)
    click(m, (100, 100))
    s = m.shapes[0]
    assert s.rect.x == 99 and s.rect.y == 99  # hotspot (1, 1) on the click
    out = m.render_output()
    assert count_color(out, (99, 99, 113, 120), WHITE, 30) > 30
    assert count_color(out, (99, 99, 113, 120), (0, 0, 0), 30) > 20


def test_cursor_from_callback_image():
    cur = Image.new("RGBA", (8, 8), (255, 0, 255, 255))
    m = make(tool=ShapeType.DrawingCursor, callbacks=Callbacks(cursor_image=lambda: (cur, (4, 4))))
    click(m, (50, 50))
    assert m.shapes[0].rect.as_tuple() == (46, 46, 8, 8)
    assert px(m.render_output(), 47, 47) == (255, 0, 255)


def test_smart_eraser_fills_with_start_colour():
    img = blank(200, 150, (128, 128, 128, 255))
    img.paste((255, 0, 0, 255), (60, 60, 90, 90))
    m = make(img, tool=ShapeType.DrawingSmartEraser)
    drag(m, (50, 50), (100, 100))
    assert m.shapes[0].eraser_color == "#FF808080"
    out = m.render_output()
    assert px(out, 75, 75) == (128, 128, 128)
    assert px(img, 75, 75) == (255, 0, 0)


# ---- effects ----------------------------------------------------------------------------


def test_blur_reduces_variance_only_inside():
    img = noise()
    m = make(img, tool=ShapeType.EffectBlur, blur_radius=15)
    drag(m, (50, 40), (149, 109))
    out = np.asarray(m.render_output().convert("RGB"), dtype=np.float64)
    src = np.asarray(img.convert("RGB"), dtype=np.float64)
    inside = (slice(50, 110), slice(60, 140))
    assert out[inside].std() < src[inside].std() / 3
    assert np.array_equal(out[:30], src[:30])
    assert np.array_equal(out[:, :40], src[:, :40])


def test_pixelate_makes_uniform_blocks():
    img = noise()
    m = make(img, tool=ShapeType.EffectPixelate, pixelate_size=10)
    drag(m, (40, 30), (139, 109))
    out = np.asarray(m.render_output().convert("RGB"))
    block = out[30:40, 40:50]
    assert (block == block[0, 0]).all()
    nxt = out[30:40, 50:60]
    assert (nxt == nxt[0, 0]).all() and not (nxt[0, 0] == block[0, 0]).all()
    src = np.asarray(img.convert("RGB"))
    assert np.array_equal(out[120:], src[120:])


def test_highlight_tints_like_sharex_min():
    img = blank(200, 100)
    img.paste((0, 0, 0, 255), (0, 50, 200, 100))
    m = make(img, tool=ShapeType.EffectHighlight)
    drag(m, (20, 20), (119, 79))
    out = m.render_output()
    assert px(out, 50, 30) == (255, 255, 0)  # white -> yellow
    assert px(out, 50, 70) == (0, 0, 0)  # min() keeps black black
    assert px(out, 150, 30) == WHITE


def test_effect_preview_is_cached_and_drawn():
    import cairo

    img = noise()
    m = make(img, mode=RegionCaptureMode.Editor, tool=ShapeType.EffectPixelate, pixelate_size=10)
    drag(m, (40, 30), (139, 109))
    m.options.show_magnifier = m.options.show_info = False
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 150)
    m.draw(cairo.Context(surf))
    m.draw(cairo.Context(surf))
    assert m.effect_cache.computed == 1
    shown = R.surface_to_pil(surf)
    expected = m.render_output()
    assert px(shown, 60, 60) == px(expected, 60, 60)
    assert px(shown, 60, 60) != px(img, 60, 60)


def test_effects_apply_before_drawings_in_output():
    img = noise()
    m = make(img, tool=ShapeType.DrawingRectangle, fill_color="#FF0000FF", border_size=0)
    drag(m, (50, 50), (100, 100))
    m.current_tool = ShapeType.EffectBlur
    drag(m, (40, 40), (110, 110))
    out = m.render_output()
    assert px(out, 75, 75) == (0, 0, 255)  # the drawing stays on top of the later blur


def test_regions_and_tools_are_not_rendered():
    m = make(mode=RegionCaptureMode.Annotation)
    m.options.quick_crop = False
    drag(m, (10, 10), (100, 100))
    assert len(m.regions) == 1
    assert m.render_output().tobytes() == blank().tobytes()
