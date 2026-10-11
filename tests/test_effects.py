"""Image effects: every ShareX effect changes pixels the way ShareX's implementation does."""
from __future__ import annotations

import dataclasses
import json
import math

import numpy as np
import pytest
from PIL import Image

from sharel.effects import common
from sharel.effects.base import EFFECTS, ImageEffect, ImageEffectPreset, default_preset, default_presets, load_all
from sharel.effects.common import GradientInfo, GradientStop

load_all()


def E(effect_name: str, /, **kw) -> ImageEffect:
    return EFFECTS[effect_name](**kw)


def solid(w: int, h: int, rgba=(100, 150, 200, 255)) -> Image.Image:
    return Image.new("RGBA", (w, h), rgba)


def arr(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGBA")).astype(int)


def close(a, b, tol=1) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b, strict=True))


# --------------------------------------------------------------------------- registry / generic

EXPECTED = {
    "Adjustments": {"Alpha", "BlackWhite", "Brightness", "Colorize", "Contrast", "Gamma", "Grayscale", "Hue",
                    "Inverse", "MatrixColor", "Polaroid", "ReplaceColor", "Saturation", "SelectiveColor", "Sepia"},
    "Drawings": {"DrawBackground", "DrawBackgroundImage", "DrawBorder", "DrawCheckerboard", "DrawImage",
                 "DrawParticles", "DrawText", "DrawTextEx"},
    "Filters": {"Blur", "ColorDepth", "EdgeDetect", "Emboss", "GaussianBlur", "Glow", "MatrixConvolution",
                "MeanRemoval", "Outline", "Pixelate", "RGBSplit", "Reflection", "Shadow", "Sharpen", "Slice",
                "Smooth", "TornEdge", "WaveEdge"},
    "Manipulations": {"AutoCrop", "Canvas", "Crop", "Flip", "ForceProportions", "Resize", "Rotate",
                      "RoundedCorners", "Scale", "Skew"},
}


def test_registry_has_every_sharex_effect():
    for cat, names in EXPECTED.items():
        got = {n for n, c in EFFECTS.items() if c.category == cat}
        assert got == names, cat


def nondefault_value(f: dataclasses.Field, current):
    """A valid value different from the default, by field kind/type."""
    md = f.metadata
    kind = md.get("kind", "")
    if kind == "color":
        return "#80123456"
    if kind == "choice":
        return next(c for c in md["choices"] if c != current)
    if kind == "flags":
        return "Top, Left"
    if kind in ("point", "size"):
        return [3, 4]
    if kind == "padding":
        return [1, 2, 3, 4]
    if kind == "gradient":
        return GradientInfo("BackwardDiagonal", [GradientStop("#FF010203", 0.0), GradientStop("#7F040506", 60.0)])
    if kind == "font":
        return "DejaVu Sans, 14pt, style=Bold, Italic"
    if kind in ("file", "folder"):
        return "/nonexistent/asset.png"
    if isinstance(current, bool):
        return not current
    if isinstance(current, int):
        return current + 2
    if isinstance(current, float):
        return current + 0.25
    return str(current) + " changed"


def nondefault(cls) -> ImageEffect:
    e = cls()
    for f in e.params():
        setattr(e, f.name, nondefault_value(f, getattr(e, f.name)))
    e.enabled = False
    e.name = "Custom name"
    return e


ALL = sorted(EFFECTS)


@pytest.mark.parametrize("name", ALL)
def test_preset_json_round_trip_every_effect(name):
    e = nondefault(EFFECTS[name])
    d = e.to_jsonable()
    for f in e.params():  # every parameter really is non-default
        assert getattr(e, f.name) != getattr(EFFECTS[name](), f.name), f.name
    preset = ImageEffectPreset("P", [e])
    back = ImageEffectPreset.from_jsonable(json.loads(json.dumps(preset.to_jsonable())))
    assert back.name == "P"
    assert back.effects == [e]
    assert d["type"] == name


@pytest.mark.parametrize("name", ALL)
def test_disabled_effect_is_noop(name):
    img = solid(30, 20, (10, 200, 30, 255))
    img.putpixel((3, 4), (250, 0, 0, 128))
    e = nondefault(EFFECTS[name])
    e.enabled = False
    out = e(img)
    assert out.size == img.size
    assert np.array_equal(arr(out), arr(img))
    assert ImageEffectPreset("p", [e]).apply(img).tobytes() == img.tobytes()


@pytest.mark.parametrize("name", ALL)
def test_params_metadata_for_effects_window(name):
    e = EFFECTS[name]()
    for f in e.params():
        assert f.metadata["label"], f.name
        if f.metadata["kind"] in ("choice", "flags"):
            assert getattr(e, f.name) in f.metadata["choices"] or f.metadata["kind"] == "flags"
    assert e.describe().startswith(e.display_name)
    assert e.category in EXPECTED


def test_describe_matches_sharex_summaries():
    assert E("Brightness", value=0.5).describe() == "Brightness: 0.5"
    assert E("DrawBorder").describe() == "Border: 1px"
    assert E("BlackWhite").describe() == "Black & white"
    assert E("Hue", angle=90).describe() == "Hue: 90°"
    assert E("Canvas", margin=[5, 5, 5, 5]).describe() == "Canvas: 5"
    assert E("Canvas", margin=[1, 2, 3, 4]).describe() == "Canvas: 1, 2, 3, 4"
    assert E("ColorDepth", bits_per_channel=1).describe() == "Color depth: 1 bit"
    assert E("Resize", width=250, height=0).describe() == "Resize: 250px, 0"
    assert E("DrawText", text="x" * 25).describe() == "Text watermark: " + "x" * 20 + "..."
    assert E("Blur", radius=4).describe() == "Blur: 5"
    assert E("Alpha", name="Mine").describe() == "Mine"


def test_default_preset_is_sharex_default():
    p = default_preset()
    assert [e.type_name for e in p.effects] == ["Canvas", "DrawText"]
    assert p.effects[0].margin == [0, 0, 0, 30]
    assert p.effects[1].use_gradient is True and p.effects[1].offset == [0, 0]
    out = p.apply(solid(200, 100, (255, 255, 255, 255)))
    assert out.size == (200, 130)
    assert out.getpixel((10, 10)) == (255, 255, 255, 255)
    # the watermark box sits on the new bottom strip, bottom-right, filled with the blue gradient
    a = arr(out)
    strip = a[100:, :]
    assert (strip[..., 3] > 0).any()
    assert len(default_presets()) == 1


def test_preset_skips_unknown_effects():
    p = ImageEffectPreset.from_jsonable({"name": "x", "effects": [{"type": "Nope"}, {"type": "Inverse"}]})
    assert [e.type_name for e in p.effects] == ["Inverse"]


# --------------------------------------------------------------------------- colour helpers

def test_color_parse_and_format():
    assert common.parse_color("#7D000000") == (0, 0, 0, 125)
    assert common.parse_color("#FF8000") == (255, 128, 0, 255)
    assert common.parse_color("Black") == (0, 0, 0, 255)
    assert common.parse_color("LightGray") == (211, 211, 211, 255)
    assert common.parse_color("Transparent") == (255, 255, 255, 0)
    assert common.parse_color("235, 235, 235") == (235, 235, 235, 255)
    assert common.parse_color("125, 0, 0, 0") == (0, 0, 0, 125)
    assert common.format_color((1, 2, 3, 4)) == "#04010203"
    assert common.color_to_dotnet("#7D000000") == "125, 0, 0, 0"
    assert common.color_to_dotnet("#FFEBEBEB") == "235, 235, 235"
    assert common.parse_color("garbage", (9, 9, 9, 9)) == (9, 9, 9, 9)


def test_gradient_modes_and_hard_stop():
    g = GradientInfo.of("Horizontal", ("#FFFF0000", 0), ("#FF0000FF", 100))
    a = common.gradient_array(100, 10, g)
    assert close(a[5, 0], (254, 0, 1, 255), 2) and close(a[5, 99], (1, 0, 254, 255), 2)
    assert close(a[5, 50], (125, 0, 130, 255), 3)
    v = common.gradient_array(10, 100, GradientInfo.of("Vertical", ("#FFFF0000", 0), ("#FF0000FF", 100)))
    assert v[0, 5, 0] > 250 and v[99, 5, 2] > 250
    d = common.gradient_array(200, 100, GradientInfo.of("ForwardDiagonal", ("#FF000000", 0), ("#FFFFFFFF", 100)))
    # GDI+ diagonal: the off-diagonal corners are the 50 % colour even on a non-square rect
    assert close(d[0, 199][:3], (127, 127, 127), 3) and close(d[99, 0][:3], (127, 127, 127), 3)
    b = common.gradient_array(200, 100, GradientInfo.of("BackwardDiagonal", ("#FF000000", 0), ("#FFFFFFFF", 100)))
    assert b[0, 199, 0] < 5 and b[99, 0, 0] > 250
    hard = common.gradient_array(10, 100, common.default_blue_gradient())
    assert close(hard[48, 0][:3], (13, 58, 122), 2)
    assert close(hard[50, 0][:3], (6, 36, 78), 2)
    # missing 0/100 stops are padded with the end colours
    pad = common.gradient_array(10, 100, GradientInfo.of("Vertical", ("#FF00FF00", 40), ("#FFFF0000", 60)))
    assert tuple(pad[0, 0]) == (0, 255, 0, 255) and tuple(pad[99, 0]) == (255, 0, 0, 255)


def test_font_parse_and_format():
    f = common.parse_font("Arial, 11.25pt")
    assert (f.family, f.size, f.unit, f.bold) == ("Arial", 11.25, "pt", False)
    assert f.size_px == pytest.approx(15.0)
    g = common.parse_font("Segoe UI, 36pt, style=Bold, Italic")
    assert g.family == "Segoe UI" and g.bold and g.italic and not g.underline
    assert common.format_font(g) == "Segoe UI, 36pt, style=Bold, Italic"
    assert common.parse_font("Mono, 20px").size_px == 20


def test_expand_path_sharex_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAREL_DATA_DIR", str(tmp_path))
    assert common.expand_path("%ShareXImageEffects%\\Preset\\a.png") == f"{tmp_path}/ImageEffects/Preset/a.png"
    monkeypatch.setenv("MYDIR", "/opt/x")
    assert common.expand_path("%MYDIR%/b.png") == "/opt/x/b.png"
    assert common.expand_path("$MYDIR/c.png") == "/opt/x/c.png"


def test_cairo_round_trip_keeps_pixels():
    img = solid(5, 3, (10, 20, 30, 255))
    img.putpixel((1, 1), (200, 100, 50, 128))
    back = common.from_surface(common.to_surface(img))
    assert back.getpixel((0, 0)) == (10, 20, 30, 255)
    assert close(back.getpixel((1, 1)), (200, 100, 50, 128), 2)


# --------------------------------------------------------------------------- adjustments

def test_alpha():
    img = solid(4, 4, (100, 150, 200, 200))
    out = E("Alpha", value=0.5)(img)
    assert out.getpixel((0, 0)) == (100, 150, 200, 100)
    out2 = E("Alpha", value=0.5, addition=0.2)(img)
    assert close(out2.getpixel((0, 0)), (100, 150, 200, round((200 / 255 * 0.5 + 0.2) * 255)))
    assert out.size == img.size


def test_black_white():
    img = Image.new("RGBA", (3, 1))
    for x, v in enumerate((50, 100, 200)):
        img.putpixel((x, 0), (v, v, v, 255))
    out = E("BlackWhite")(img)
    assert out.getpixel((0, 0)) == (0, 0, 0, 255)
    assert close(out.getpixel((1, 0)), (195, 195, 195, 255))  # 4.5 * 100 / 255 - 1
    assert out.getpixel((2, 0)) == (255, 255, 255, 255)


def test_brightness():
    out = E("Brightness", value=0.5)(solid(2, 2, (100, 50, 0, 255)))
    assert close(out.getpixel((0, 0)), (228, 178, 128, 255))
    assert E("Brightness", value=-1.0)(solid(2, 2, (100, 50, 0, 77))).getpixel((1, 1)) == (0, 0, 0, 77)


def test_colorize():
    out = E("Colorize", color="#FFFF0000", value=1.0)(solid(2, 2, (100, 200, 50, 255)))
    lum = 0.212671 * 100 + 0.715160 * 200 + 0.072169 * 50
    assert close(out.getpixel((0, 0)), (round(lum), 0, 0, 255))
    assert E("Colorize", value=0.0)(solid(2, 2, (1, 2, 3, 255))).getpixel((0, 0)) == (1, 2, 3, 255)


def test_contrast():
    assert E("Contrast", value=2.0)(solid(2, 2, (60, 100, 200, 255))).getpixel((0, 0)) == (120, 200, 255, 255)


def test_gamma():
    img = Image.new("RGBA", (3, 1))
    img.putpixel((0, 0), (128, 64, 255, 90))
    out = E("Gamma", value=2.0)(img)
    assert out.getpixel((0, 0)) == (64, 16, 255, 90)
    out = E("Gamma", value=0.5)(img)
    assert out.getpixel((0, 0)) == (181, 128, 255, 90)
    assert E("Gamma", value=1.0)(img).getpixel((0, 0)) == (128, 64, 255, 90)
    # Wine's gdiplus test of ImageAttributes.SetGamma(3.0) (checked against Windows): 0x80 -> 0x20
    assert E("Gamma", value=3.0)(img).getpixel((0, 0))[0] == 0x20


def test_grayscale():
    out = E("Grayscale")(solid(2, 2, (255, 0, 0, 255)))
    assert out.getpixel((0, 0)) == (54, 54, 54, 255)


def test_hue():
    assert E("Hue", angle=0)(solid(2, 2, (255, 0, 0, 255))).getpixel((0, 0)) == (255, 0, 0, 255)
    out = E("Hue", angle=180)(solid(2, 2, (255, 0, 0, 255))).getpixel((0, 0))
    assert out[0] == 0 and close(out[1:3], (108, 108)) and out[3] == 255
    grey = E("Hue", angle=77)(solid(2, 2, (90, 90, 90, 255))).getpixel((0, 0))
    assert close(grey, (90, 90, 90, 255))


def test_inverse():
    assert E("Inverse")(solid(2, 2, (10, 20, 30, 40))).getpixel((0, 0)) == (245, 235, 225, 40)


def test_matrix_color():
    swap = E("MatrixColor", rr=0.0, rb=1.0, br=1.0, bb=0.0)
    assert swap(solid(2, 2, (10, 20, 30, 255))).getpixel((0, 0)) == (30, 20, 10, 255)
    off = E("MatrixColor", go=0.2, aa=0.5)
    assert close(off(solid(2, 2, (10, 20, 30, 200))).getpixel((0, 0)), (10, 71, 30, 100))


def test_polaroid():
    assert close(E("Polaroid")(solid(2, 2, (100, 100, 100, 255))).getpixel((0, 0)), (122, 143, 125, 255))


def test_replace_color():
    img = solid(3, 1, (255, 255, 255, 255))
    img.putpixel((1, 0), (250, 250, 245, 255))
    img.putpixel((2, 0), (0, 0, 0, 255))
    exact = E("ReplaceColor", target_color="#FF00FF00")(img)
    assert [exact.getpixel((x, 0)) for x in range(3)] == [(0, 255, 0, 255), (250, 250, 245, 255), (0, 0, 0, 255)]
    near = E("ReplaceColor", target_color="#FF00FF00", threshold=20)(img)
    assert [near.getpixel((x, 0)) for x in range(3)] == [(0, 255, 0, 255), (0, 255, 0, 255), (0, 0, 0, 255)]
    auto = E("ReplaceColor", auto_source_color=True, source_color="#FF123456")(img)
    assert auto.getpixel((0, 0)) == (255, 255, 255, 0)  # default target: .NET Transparent
    assert auto.getpixel((2, 0)) == (0, 0, 0, 255)


def test_saturation():
    assert E("Saturation", value=0.0)(solid(2, 2, (255, 0, 0, 255))).getpixel((0, 0)) == (54, 54, 54, 255)
    assert E("Saturation", value=1.0)(solid(2, 2, (12, 34, 56, 255))).getpixel((0, 0)) == (12, 34, 56, 255)
    boosted = E("Saturation", value=2.0)(solid(2, 2, (150, 100, 100, 255))).getpixel((0, 0))
    assert boosted[0] > 150 and boosted[1] < 100


def test_selective_color():
    img = solid(2, 1)
    img.putpixel((0, 0), (200, 200, 200, 255))
    img.putpixel((1, 0), (30, 30, 30, 99))
    out = E("SelectiveColor", palette_size=2, light_color="#FFFF0000", dark_color="#FF0000FF")(img)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)  # brightness 200 is closer to red's 139 than to blue's 86
    assert out.getpixel((1, 0)) == (0, 0, 255, 99)
    bw = E("SelectiveColor", palette_size=2)(img)
    assert bw.getpixel((0, 0)) == (255, 255, 255, 255) and bw.getpixel((1, 0)) == (0, 0, 0, 99)


def test_sepia():
    assert close(E("Sepia")(solid(2, 2, (100, 100, 100, 255))).getpixel((0, 0)), (135, 120, 94, 255))


# --------------------------------------------------------------------------- drawings

def save(img: Image.Image, path) -> str:
    img.save(path)
    return str(path)


def test_draw_background_color_and_gradient():
    img = solid(10, 10, (0, 0, 0, 0))
    img.putpixel((5, 5), (255, 0, 0, 255))
    out = E("DrawBackground", color="#FF00FF00")(img)
    assert out.getpixel((0, 0)) == (0, 255, 0, 255) and out.getpixel((5, 5)) == (255, 0, 0, 255)
    g = GradientInfo.of("Vertical", ("#FFFF0000", 0), ("#FF0000FF", 100))
    out = E("DrawBackground", use_gradient=True, gradient=g)(solid(10, 100, (0, 0, 0, 0)))
    assert out.getpixel((5, 0))[0] > 250 and out.getpixel((5, 99))[2] > 250
    half = E("DrawBackground", color="#FFFFFFFF")(solid(4, 4, (0, 0, 0, 128)))
    assert close(half.getpixel((0, 0)), (127, 127, 127, 255))


def test_draw_background_image(tmp_path):
    bg = solid(20, 10, (0, 0, 255, 255))
    path = save(bg, tmp_path / "bg.png")
    img = solid(40, 40, (0, 0, 0, 0))
    img.putpixel((1, 1), (255, 0, 0, 255))
    out = E("DrawBackgroundImage", image_file_path=path)(img)
    assert out.size == (40, 40)
    assert out.getpixel((0, 39)) == (0, 0, 255, 255) and out.getpixel((1, 1)) == (255, 0, 0, 255)
    # tiled, not centred: the 2-colour tile repeats from the origin
    tile = solid(4, 4, (0, 255, 0, 255))
    tile.paste((255, 255, 0, 255), (2, 0, 4, 4))
    tpath = save(tile, tmp_path / "tile.png")
    out = E("DrawBackgroundImage", image_file_path=tpath, tile=True, center=False)(solid(10, 4, (0, 0, 0, 0)))
    assert [out.getpixel((x, 0))[0] for x in (0, 2, 4, 6, 8)] == [0, 255, 0, 255, 0]
    # centred tiles: offset ((10 - 4) / 2) % 4 = 3
    out = E("DrawBackgroundImage", image_file_path=tpath, tile=True, center=True)(solid(10, 4, (0, 0, 0, 0)))
    assert out.getpixel((3, 0)) == (0, 255, 0, 255) and out.getpixel((5, 0)) == (255, 255, 0, 255)
    missing = E("DrawBackgroundImage", image_file_path=str(tmp_path / "nope.png"))(img)
    assert missing.tobytes() == img.tobytes()


def test_draw_border_outside_inside_dash_gradient():
    img = solid(10, 8, (100, 100, 100, 255))
    out = E("DrawBorder", size=3, color="#FFFF0000")(img)
    assert out.size == (16, 14)
    a = arr(out)
    assert tuple(a[0, 0]) == (255, 0, 0, 255) and tuple(a[2, 2]) == (255, 0, 0, 255)
    assert tuple(a[3, 3]) == (100, 100, 100, 255) and tuple(a[13, 15]) == (255, 0, 0, 255)
    inside = E("DrawBorder", border_type="Inside", size=2, color="#FF00FF00")(img)
    assert inside.size == (10, 8)
    assert inside.getpixel((0, 0)) == (0, 255, 0, 255) and inside.getpixel((1, 7)) == (0, 255, 0, 255)
    assert inside.getpixel((2, 2)) == (100, 100, 100, 255)
    dashed = arr(E("DrawBorder", border_type="Inside", size=1, dash_style="Dash", color="#FF00FF00")(solid(40, 20)))
    top = (dashed[0, :, 1] == 255).astype(int)
    assert 0 < top.sum() < 40 and top[:3].tolist() == [1, 1, 1] and top[3] == 0
    g = GradientInfo.of("Horizontal", ("#FFFF0000", 0), ("#FF0000FF", 100))
    grad = E("DrawBorder", size=2, use_gradient=True, gradient=g)(solid(50, 10))
    assert grad.getpixel((0, 5))[0] > 200 and grad.getpixel((53, 5))[2] > 200


def test_draw_checkerboard():
    img = solid(20, 10, (0, 0, 0, 0))
    img.putpixel((7, 7), (1, 2, 3, 255))
    out = E("DrawCheckerboard", size=5, color="#FFAA0000", color2="#FF00BB00")(img)
    assert out.getpixel((0, 0)) == (170, 0, 0, 255) and out.getpixel((5, 0)) == (0, 187, 0, 255)
    assert out.getpixel((5, 5)) == (170, 0, 0, 255) and out.getpixel((0, 5)) == (0, 187, 0, 255)
    assert out.getpixel((7, 7)) == (1, 2, 3, 255)
    default = E("DrawCheckerboard")(solid(20, 20, (0, 0, 0, 0)))
    assert default.getpixel((0, 0)) == (211, 211, 211, 255) and default.getpixel((10, 0)) == (255, 255, 255, 255)


def test_draw_image_placement_sizes_opacity(tmp_path):
    wm = solid(10, 10, (255, 0, 0, 255))
    path = save(wm, tmp_path / "wm.png")
    base = solid(50, 40, (0, 0, 255, 255))
    out = E("DrawImage", image_location=path, placement="BottomRight", offset=[2, 2])(base)
    assert out.size == (50, 40)
    assert out.getpixel((38, 28)) == (255, 0, 0, 255) and out.getpixel((47, 37)) == (255, 0, 0, 255)
    assert out.getpixel((48, 38)) == (0, 0, 255, 255) and out.getpixel((37, 27)) == (0, 0, 255, 255)
    half = E("DrawImage", image_location=path, opacity=50)(base)
    assert close(half.getpixel((0, 0)), (128, 0, 127, 255))
    absolute = E("DrawImage", image_location=path, size_mode="AbsoluteSize", size=[20, 0])(base)
    assert absolute.getpixel((19, 19))[0] > 250 and absolute.getpixel((21, 21)) == (0, 0, 255, 255)
    canvas_pct = E("DrawImage", image_location=path, size_mode="PercentageOfCanvas", size=[100, 100])(base)
    assert canvas_pct.getpixel((49, 39))[0] > 250
    full = E("DrawImage", image_location=path, size_mode="AbsoluteSize", size=[-1, 20], placement="MiddleCenter")
    full_out = full(base)  # -1 = canvas width -> 50x20, centred at y = 10
    assert full_out.getpixel((0, 10))[0] > 250 and full_out.getpixel((49, 29))[0] > 250
    assert full_out.getpixel((0, 9)) == (0, 0, 255, 255) and full_out.getpixel((0, 30)) == (0, 0, 255, 255)
    both = E("DrawImage", image_location=path, size_mode="AbsoluteSize", size=[-1, -1])(base)
    assert both.tobytes() == base.tobytes()  # ShareX skips when neither size is positive
    hidden = E("DrawImage", image_location=path, size_mode="AbsoluteSize", size=[60, 60], auto_hide=True)(base)
    assert hidden.tobytes() == base.tobytes()
    tiled = E("DrawImage", image_location=save(Image.new("RGBA", (2, 1), (0, 255, 0, 255)), tmp_path / "t.png"),
              size_mode="AbsoluteSize", size=[8, 4], tile=True)
    tiled_out = tiled(base)
    assert tiled_out.getpixel((7, 3)) == (0, 255, 0, 255) and tiled_out.getpixel((8, 3)) == (0, 0, 255, 255)
    clear = save(Image.new("RGBA", (4, 4), (0, 0, 0, 0)), tmp_path / "clear.png")
    copy = E("DrawImage", image_location=clear, compositing_mode="SourceCopy")(base)
    assert copy.getpixel((1, 1)) == (0, 0, 0, 0) and copy.getpixel((5, 5)) == (0, 0, 255, 255)
    asym = solid(4, 2, (0, 0, 0, 255))
    asym.putpixel((0, 0), (255, 255, 255, 255))
    rot = E("DrawImage", image_location=save(asym, tmp_path / "asym.png"), rotate_flip="Rotate90")(base)
    assert rot.getpixel((1, 0)) == (255, 255, 255, 255) and rot.getpixel((0, 3)) == (0, 0, 0, 255)
    assert E("DrawImage", image_location=str(tmp_path / "missing.png"))(base).tobytes() == base.tobytes()


def test_draw_particles(tmp_path):
    folder = tmp_path / "particles"
    folder.mkdir()
    save(solid(4, 4, (255, 0, 0, 255)), folder / "p.png")
    (folder / "notes.txt").write_text("ignored")
    common.rng.seed(1234)
    base = solid(40, 30, (0, 0, 255, 255))
    out = E("DrawParticles", image_folder=str(folder), image_count=5)(base)
    a = arr(out)
    red = (a[..., 0] == 255) & (a[..., 2] == 0)
    assert out.size == (40, 30)
    assert 16 <= red.sum() <= 5 * 16
    common.rng.seed(7)
    no_overlap = arr(E("DrawParticles", image_folder=str(folder), image_count=4, no_overlap=True)(base))
    assert ((no_overlap[..., 0] == 255) & (no_overlap[..., 2] == 0)).sum() == 4 * 16
    common.rng.seed(1)
    behind = E("DrawParticles", image_folder=str(folder), image_count=5, background=True)(base)
    assert behind.tobytes() == base.tobytes()  # drawn under an opaque image
    clear = solid(40, 30, (0, 0, 0, 0))
    common.rng.seed(1)
    under = arr(E("DrawParticles", image_folder=str(folder), image_count=3, background=True)(clear))
    assert (under[..., 3] == 255).sum() >= 16
    assert E("DrawParticles", image_folder=str(tmp_path / "none"))(base).tobytes() == base.tobytes()


def test_drawing_keeps_untouched_semi_transparent_pixels():
    img = solid(60, 40, (200, 100, 50, 3))  # would drift through a premultiplied round trip
    for e in (E("DrawText", text="x", placement="TopLeft"), E("DrawTextEx", text="x"),
              E("DrawBorder", border_type="Inside")):
        out = e(img)
        assert out.getpixel((30, 35)) == (200, 100, 50, 3), e.type_name


def test_draw_text_watermark_box():
    base = solid(220, 100, (128, 128, 128, 255))
    e = E("DrawText", text="Hello", corner_radius=0)
    x, y, w, h = e.watermark_rect(base.size, "Hello")
    assert x + w == 220 - 5 and y + h == 100 - 5  # BottomRight, offset 5
    out = e(base)
    a = arr(out)
    assert out.size == base.size
    assert tuple(a[y + h // 2, x + 2][:3]) == (42, 47, 56)  # background colour inside the padding
    assert tuple(a[y + h // 2, x][:3]) == (0, 0, 0)  # 1 px border
    assert tuple(a[y - 2, x - 2]) == (128, 128, 128, 255)  # outside untouched
    text_area = a[y + 5:y + h - 5, x + 5:x + w - 5]
    assert (text_area[..., 0] > 200).sum() > 10  # light text pixels
    # the name parser runs on the text: %width is the image width
    assert E("DrawText", text="%width").watermark_rect(base.size, "220") == \
        E("DrawText", text="220").watermark_rect(base.size, "220")
    parsed = arr(E("DrawText", text="%width", draw_background=False, draw_border=False,
                   draw_text_shadow=False)(base))
    plain = arr(E("DrawText", text="220", draw_background=False, draw_border=False, draw_text_shadow=False)(base))
    assert np.array_equal(parsed, plain)
    hidden = E("DrawText", text="W" * 80, auto_hide=True)(base)
    assert hidden.tobytes() == base.tobytes()
    grad = arr(E("DrawText", text="Hi", use_gradient=True, placement="TopLeft", offset=[0, 0])(base))
    assert tuple(grad[2, 2][:3]) != (42, 47, 56)


def test_draw_text_ex_fill_outline_angle():
    base = solid(300, 200, (0, 0, 0, 255))
    out = arr(E("DrawTextEx", text="Hello", font="DejaVu Sans, 30pt", color="#FFFF0000")(base))
    red = (out[..., 0] > 200) & (out[..., 1] < 50)
    ys, xs = np.nonzero(red)
    assert red.sum() > 100
    assert xs.min() <= 2 and ys.min() <= 2  # TopLeft, offset 0: the glyph bounds start at the origin
    wide = xs.max() - xs.min()
    tall = ys.max() - ys.min()
    assert wide > tall
    rot = arr(E("DrawTextEx", text="Hello", font="DejaVu Sans, 30pt", color="#FFFF0000", angle=90)(base))
    rys, rxs = np.nonzero((rot[..., 0] > 200) & (rot[..., 1] < 50))
    assert (rys.max() - rys.min()) > (rxs.max() - rxs.min())
    outl = arr(E("DrawTextEx", text="Hi", font="DejaVu Sans, 30pt", outline=True, outline_size=4,
                 outline_color="#FF00FF00", placement="MiddleCenter")(base))
    assert ((outl[..., 1] > 200) & (outl[..., 0] < 50)).sum() > 50
    sh = arr(E("DrawTextEx", text="Hi", font="DejaVu Sans, 30pt", shadow=True, shadow_color="#FF0000FF",
               shadow_offset=[0, 6])(base))
    assert ((sh[..., 2] > 200) & (sh[..., 0] < 50)).sum() > 20
    grad = arr(E("DrawTextEx", text="HHHH", font="DejaVu Sans, 40pt", use_gradient=True,
                 gradient=GradientInfo.of("Horizontal", ("#FFFF0000", 0), ("#FF0000FF", 100)))(base))
    lit = np.nonzero(grad[..., 3] > 0)
    gx = np.nonzero((grad[..., 0] + grad[..., 2]) > 150)[1]
    assert grad[lit].shape[0] > 0
    left = grad[:, gx.min():gx.min() + 10]
    right = grad[:, gx.max() - 10:gx.max() + 1]
    assert left[..., 0].max() > left[..., 2].max() and right[..., 2].max() > right[..., 0].max()
    assert E("DrawTextEx", text="")(base).tobytes() == base.tobytes()


# --------------------------------------------------------------------------- filters

def ref_box_pass(a: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Straightforward port of ShareX BoxBlurHorizontal/Vertical."""
    half = size // 2
    out = np.zeros_like(a)
    n = a.shape[axis]
    for i in range(n):
        lo, hi = max(0, i - half), min(n - 1, i + half)
        win = a.take(range(lo, hi + 1), axis=axis).astype(int)
        val = win.sum(axis=axis) // (hi - lo + 1)
        idx = [slice(None)] * a.ndim
        idx[axis] = i
        out[tuple(idx)] = val
    return out


def test_blur_matches_sharex_box_blur():
    rnd = np.random.default_rng(5)
    a = rnd.integers(0, 256, (9, 13, 4), dtype=np.uint8)
    img = Image.fromarray(a, "RGBA")
    out = arr(E("Blur", radius=4)(img))  # even radius -> 5
    ref = a
    for _ in range(2):
        ref = ref_box_pass(ref, 5, 1)
        ref = ref_box_pass(ref, 5, 0)
    assert np.array_equal(out, ref)
    assert E("Blur", radius=1).effective_radius == 3


def test_color_depth():
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (100, 200, 17, 50))
    img.putpixel((1, 0), (127, 128, 0, 255))
    one = E("ColorDepth", bits_per_channel=1)(img)
    assert one.getpixel((0, 0)) == (0, 255, 0, 50) and one.getpixel((1, 0)) == (0, 255, 0, 255)
    four = E("ColorDepth", bits_per_channel=4)(img)
    assert four.getpixel((0, 0)) == (102, 204, 17, 50)


def test_edge_detect():
    flat = E("EdgeDetect")(solid(5, 5, (80, 80, 80, 255)))
    assert flat.getpixel((2, 2)) == (127, 127, 127, 255)
    img = solid(5, 6, (0, 0, 0, 255))
    img.paste((255, 255, 255, 255), (0, 3, 5, 6))
    out = E("EdgeDetect")(img)
    assert out.getpixel((2, 2)) == (255, 255, 255, 255)  # dark above, bright below
    assert out.getpixel((2, 0)) == (127, 127, 127, 255)


def test_emboss():
    assert E("Emboss")(solid(5, 5, (80, 30, 200, 99))).getpixel((2, 2)) == (127, 127, 127, 99)
    img = solid(5, 5, (0, 0, 0, 255))
    img.putpixel((2, 2), (40, 40, 40, 255))
    assert E("Emboss")(img).getpixel((2, 2)) == (255, 255, 255, 255)  # 4 * 40 + 127


def test_gaussian_blur():
    flat = arr(E("GaussianBlur", radius=3)(solid(9, 9, (200, 100, 50, 255))))
    assert np.all(np.abs(flat - np.array([200, 100, 50, 255])) <= 1)
    img = solid(15, 15, (0, 0, 0, 0))
    img.putpixel((7, 7), (255, 255, 255, 255))
    out = arr(E("GaussianBlur", radius=3)(img))
    assert out[7, 7, 3] < 255 and out[7, 8, 3] > 0 and out[7, 11, 3] == 0
    assert out[7, 6, 3] == out[7, 8, 3] == out[6, 7, 3]
    assert out.shape[:2] == (15, 15)


def test_glow():
    img = solid(20, 20, (255, 0, 0, 255))
    out = E("Glow", size=5, color="#FF00FF00")(img)
    assert out.size == (30, 30)
    assert out.getpixel((5, 5)) == (255, 0, 0, 255) and out.getpixel((24, 24)) == (255, 0, 0, 255)
    edge = out.getpixel((3, 15))
    assert edge[:3] == (0, 255, 0) and 0 < edge[3] < 255
    assert out.getpixel((0, 0))[3] < edge[3]
    moved = E("Glow", size=5, offset=[4, -2])(img)
    assert moved.size == (34, 32)
    assert moved.getpixel((5, 7)) == (255, 0, 0, 255)
    g = GradientInfo.of("Horizontal", ("#FF0000FF", 0), ("#FF0000FF", 100))
    blue = E("Glow", size=5, use_gradient=True, gradient=g)(img).getpixel((3, 15))
    assert blue[:3] == (0, 0, 255) and blue[3] > 0


def test_matrix_convolution():
    img = Image.fromarray(np.random.default_rng(2).integers(0, 256, (6, 7, 4), dtype=np.uint8), "RGBA")
    assert E("MatrixConvolution")(img).tobytes() == img.tobytes()
    assert E("MatrixConvolution", x1y1=2, factor=2.0)(img).tobytes() == img.tobytes()
    shift = arr(E("MatrixConvolution", x1y1=0, x0y1=1)(img))  # take the left neighbour
    src = arr(img)
    assert np.array_equal(shift[:, 1:, :3], src[:, :-1, :3]) and np.array_equal(shift[..., 3], src[..., 3])
    offs = E("MatrixConvolution", x1y1=0, offset=40)(img)
    assert np.all(arr(offs)[..., :3] == 40)


def test_mean_removal():
    assert E("MeanRemoval")(solid(5, 5, (90, 10, 200, 255))).getpixel((2, 2)) == (90, 10, 200, 255)
    img = solid(5, 5, (10, 10, 10, 255))
    img.putpixel((2, 2), (20, 20, 20, 255))
    assert E("MeanRemoval")(img).getpixel((2, 2)) == (100, 100, 100, 255)  # 9 * 20 - 8 * 10


def test_outline():
    img = solid(20, 20, (0, 0, 0, 0))
    img.paste((255, 255, 255, 255), (7, 7, 13, 13))
    out = E("Outline", size=2, color="#FFFF0000")(img)
    assert out.size == (20, 20)
    assert out.getpixel((10, 10)) == (255, 255, 255, 255)
    assert out.getpixel((6, 10)) == (255, 0, 0, 255) and out.getpixel((5, 10)) == (255, 0, 0, 255)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((3, 10))[3] == 0
    only = E("Outline", size=2, color="#FFFF0000", outline_only=True)(img)
    assert only.getpixel((10, 10))[3] == 0 and only.getpixel((6, 10)) == (255, 0, 0, 255)
    padded = E("Outline", size=1, padding=2, color="#FF00FF00")(img)
    assert padded.getpixel((6, 10))[3] == 0 and padded.getpixel((4, 10)) == (0, 255, 0, 255)


def test_pixelate():
    img = Image.new("RGBA", (4, 2))
    img.putpixel((0, 0), (0, 0, 0, 255))
    img.putpixel((1, 0), (100, 100, 100, 255))
    img.putpixel((0, 1), (200, 200, 200, 255))
    img.putpixel((1, 1), (100, 100, 100, 255))
    img.paste((255, 0, 0, 255), (2, 0, 4, 2))
    img.putpixel((3, 1), (0, 0, 255, 0))
    out = E("Pixelate", size=2)(img)
    assert out.getpixel((0, 0)) == (100, 100, 100, 255) and out.getpixel((1, 1)) == (100, 100, 100, 255)
    # transparent pixels carry no colour weight; alpha = sum(a * a / 255) / count
    assert out.getpixel((3, 1)) == (255, 0, 0, 191)
    bordered = E("Pixelate", size=4, border_size=1, border_color="#FF00FF00")(solid(8, 8))
    assert bordered.getpixel((0, 0)) == (0, 255, 0, 255) and bordered.getpixel((3, 1)) == (0, 255, 0, 255)
    assert bordered.getpixel((1, 1)) == (100, 150, 200, 255)


def test_reflection():
    img = solid(10, 10, (0, 0, 255, 255))
    img.paste((255, 0, 0, 255), (0, 9, 10, 10))  # bottom row red
    out = E("Reflection", percentage=50)(img)
    assert out.size == (10, 15)
    assert out.getpixel((0, 10)) == (255, 0, 0, 255)  # flipped: bottom row first, alpha max
    assert out.getpixel((0, 14))[3] == 0  # fades to min alpha
    assert 0 < out.getpixel((0, 12))[3] < 255
    offset = E("Reflection", percentage=50, offset=2, max_alpha=100)(img)
    assert offset.size == (10, 17)
    assert offset.getpixel((0, 11))[3] == 0 and offset.getpixel((0, 12)) == (255, 0, 0, 100)
    skewed = E("Reflection", percentage=50, skew=True, skew_size=25)(img)
    assert skewed.size == (35, 15)


def test_rgb_split():
    img = solid(20, 3, (0, 0, 0, 255))
    img.putpixel((10, 1), (255, 255, 255, 255))
    out = arr(E("RGBSplit")(img))
    assert out[1, 5, 0] == 255 and out[1, 5, 1] == 0  # red moved 5 px left
    assert out[1, 10, 1] == 255 and out[1, 10, 0] == 0
    assert out[1, 15, 2] == 255 and out[1, 15, 1] == 0
    half = Image.new("RGBA", (3, 1), (200, 100, 50, 102))
    h = E("RGBSplit", offset_red=[0, 0], offset_blue=[0, 0])(half).getpixel((1, 0))
    assert h == (80, 40, 20, 102)  # channels are premultiplied by alpha, as ShareX does


def test_shadow():
    img = solid(10, 10, (255, 255, 255, 255))
    out = E("Shadow", size=4, offset=[3, 2], opacity=1.0)(img)
    assert out.size == (21, 20)
    assert out.getpixel((4, 4)) == (255, 255, 255, 255) and out.getpixel((13, 13)) == (255, 255, 255, 255)
    assert out.getpixel((15, 13))[3] > 0 and out.getpixel((15, 13))[:3] == (0, 0, 0)  # shadow bottom-right
    assert out.getpixel((1, 1))[3] < out.getpixel((15, 13))[3]
    left = E("Shadow", size=4, offset=[-3, 0])(img)
    assert left.size == (21, 18) and left.getpixel((7, 4)) == (255, 255, 255, 255)
    same = E("Shadow", size=4, auto_resize=False)(img)
    assert same.size == (10, 10)
    dark = E("Shadow", size=4, darkness=1.0, opacity=0.5)(img).getpixel((1, 9))[3]
    light = E("Shadow", size=4, darkness=0.0, opacity=0.5)(img).getpixel((1, 9))[3]
    assert dark > light


def test_sharpen():
    # flat images survive up to the (byte) truncation of the double sum, as in ShareX
    assert close(E("Sharpen")(solid(5, 5, (90, 10, 200, 255))).getpixel((2, 2)), (90, 10, 200, 255))
    img = solid(5, 5, (30, 30, 30, 255))
    img.putpixel((2, 2), (60, 60, 60, 255))
    assert E("Sharpen")(img).getpixel((2, 2)) == (140, 140, 140, 255)  # 11/3 * 60 - 4 * 2/3 * 30


def row_shifted_by(row: np.ndarray, src: np.ndarray, s: int) -> bool:
    """``row`` is ``src`` drawn ``s`` px to the right (negative: left), uncovered pixels transparent."""
    n = len(src)
    if s >= 0:
        return np.array_equal(row[s:, :3], src[:n - s, :3]) and bool(np.all(row[:s, 3] == 0))
    return np.array_equal(row[:n + s, :3], src[-s:, :3]) and bool(np.all(row[n + s:, 3] == 0))


def test_slice():
    rnd = np.random.default_rng(3)
    a = rnd.integers(0, 256, (60, 30, 4), dtype=np.uint8)
    a[..., 3] = 255
    img = Image.fromarray(a, "RGBA")
    common.rng.seed(42)
    out = arr(E("Slice", min_slice_height=5, max_slice_height=10, min_slice_shift=2, max_slice_shift=6)(img))
    assert out.shape == a.shape
    shifts = set()
    for y in range(60):
        row = out[y]
        found = next((s for s in range(-6, 7) if row_shifted_by(row, a[y], s)), None)
        assert found is not None and 2 <= abs(found) <= 6, y
        shifts.add(found)
    assert len(shifts) > 1


def test_smooth():
    assert close(E("Smooth")(solid(5, 5, (90, 10, 200, 255))).getpixel((2, 2)), (90, 10, 200, 255))
    img = solid(5, 5, (0, 0, 0, 255))
    img.putpixel((2, 2), (90, 90, 90, 255))
    out = E("Smooth")(img)
    assert out.getpixel((2, 2)) == (10, 10, 10, 255) and out.getpixel((1, 1)) == (10, 10, 10, 255)


def test_torn_edge():
    common.rng.seed(9)
    img = solid(100, 80, (10, 20, 30, 255))
    out = arr(E("TornEdge", depth=10, range=20)(img))
    assert out.shape[:2] == (80, 100)
    assert out[40, 50, 3] == 255 and tuple(out[40, 50, :3]) == (10, 20, 30)
    assert out[0, 0, 3] < 128 and out[79, 99, 3] < 128
    edge_alpha = np.concatenate([out[0, :, 3], out[-1, :, 3], out[:, 0, 3], out[:, -1, 3]])
    assert (edge_alpha < 255).sum() > 50
    top_only = arr(E("TornEdge", depth=10, range=20, sides="Top", curved_edges=False)(img))
    assert top_only[79, 0, 3] == 255 and top_only[79, 99, 3] == 255 and top_only[40, 0, 3] == 255
    assert E("TornEdge", range=500)(img).tobytes() == img.tobytes()


def test_wave_edge():
    img = solid(100, 80, (10, 20, 30, 255))
    out = arr(E("WaveEdge", depth=10, range=20)(img))
    assert out.shape[:2] == (80, 100)
    assert out[40, 50, 3] == 255
    assert out[0, 0, 3] == 0 and out[79, 99, 3] == 0
    assert out[40, 0, 3] < 255 or out[41, 0, 3] < 255
    bottom = arr(E("WaveEdge", depth=10, range=20, sides="Bottom")(img))
    assert bottom[0, 0, 3] == 255 and bottom[0, 99, 3] == 255 and bottom[79, 50, 3] < 255


# --------------------------------------------------------------------------- manipulations

def test_auto_crop():
    img = solid(30, 20, (255, 255, 255, 255))
    img.paste((0, 0, 0, 255), (8, 6, 18, 11))
    out = E("AutoCrop")(img)
    assert out.size == (10, 5) and out.getpixel((0, 0)) == (0, 0, 0, 255)
    padded = E("AutoCrop", padding=3)(img)
    assert padded.size == (16, 11)
    assert padded.getpixel((0, 0)) == (255, 255, 255, 255) and padded.getpixel((3, 3)) == (0, 0, 0, 255)
    left_only = E("AutoCrop", sides="Left")(img)
    assert left_only.size == (22, 20)
    clear = solid(10, 10, (0, 0, 0, 0))
    clear.putpixel((4, 5), (9, 9, 9, 10))
    assert E("AutoCrop")(clear).size == (1, 1)
    assert E("AutoCrop")(solid(5, 5)).size == (5, 5)


def test_canvas():
    img = solid(10, 8)
    out = E("Canvas", margin=[1, 2, 3, 4], color="#FFFF0000")(img)
    assert out.size == (14, 14)
    assert out.getpixel((0, 5)) == (255, 0, 0, 255) and out.getpixel((1, 2)) == (100, 150, 200, 255)
    assert out.getpixel((13, 13)) == (255, 0, 0, 255) and out.getpixel((10, 9)) == (100, 150, 200, 255)
    clear = E("Canvas", margin=[2, 2, 2, 2])(img)
    assert clear.size == (14, 12) and clear.getpixel((0, 0))[3] == 0
    pct = E("Canvas", margin=[10, 25, 0, 0], margin_mode="PercentageOfCanvas")(img)
    assert pct.size == (11, 10)
    assert E("Canvas")(img) is img or E("Canvas")(img).tobytes() == img.tobytes()
    neg = E("Canvas", margin=[-2, 0, 0, 0])(img)
    assert neg.size == (8, 8)


def test_crop():
    img = Image.fromarray(np.arange(10 * 8 * 4, dtype=np.uint8).reshape(8, 10, 4), "RGBA")
    out = E("Crop", margin=[1, 2, 3, 0])(img)
    assert out.size == (6, 6)
    assert out.getpixel((0, 0)) == img.getpixel((1, 2))
    assert E("Crop", margin=[5, 0, 5, 0])(img).size == (10, 8)  # empty rect: unchanged
    assert E("Crop", margin=[-1, 0, 0, 0])(img).size == (10, 8)


def test_flip():
    img = solid(4, 3, (0, 0, 0, 255))
    img.putpixel((0, 0), (255, 0, 0, 255))
    assert E("Flip", horizontally=True)(img).getpixel((3, 0)) == (255, 0, 0, 255)
    assert E("Flip", vertically=True)(img).getpixel((0, 2)) == (255, 0, 0, 255)
    assert E("Flip", horizontally=True, vertically=True)(img).getpixel((3, 2)) == (255, 0, 0, 255)
    assert E("Flip")(img).getpixel((0, 0)) == (255, 0, 0, 255)


def test_force_proportions():
    img = solid(100, 50, (0, 0, 255, 255))
    grow = E("ForceProportions", grow_fill_color="#FFFF0000")(img)
    assert grow.size == (100, 100)
    assert grow.getpixel((50, 10)) == (255, 0, 0, 255) and grow.getpixel((50, 50)) == (0, 0, 255, 255)
    assert grow.getpixel((50, 24)) == (255, 0, 0, 255) and grow.getpixel((50, 25)) == (0, 0, 255, 255)
    crop = E("ForceProportions", method="Crop")(img)
    assert crop.size == (50, 50)
    wide = E("ForceProportions", proportional_width=16, proportional_height=9)(solid(90, 90))
    assert wide.size == (160, 90) and wide.getpixel((0, 0))[3] == 0


def test_resize():
    img = solid(100, 50)
    assert E("Resize", width=50, height=0)(img).size == (50, 25)
    assert E("Resize", width=0, height=10)(img).size == (20, 10)
    assert E("Resize", width=30, height=40)(img).size == (30, 40)
    assert E("Resize", width=200, mode="ResizeIfBigger")(img).size == (100, 50)
    assert E("Resize", width=50, mode="ResizeIfBigger")(img).size == (50, 25)
    assert E("Resize", width=50, mode="ResizeIfSmaller")(img).size == (100, 50)
    assert E("Resize", width=0, height=0)(img).size == (100, 50)
    assert E("Resize", width=50)(img).getpixel((10, 10)) == (100, 150, 200, 255)


def test_rotate():
    img = solid(40, 20, (0, 0, 255, 255))
    img.paste((255, 0, 0, 255), (0, 0, 10, 10))  # red top-left block
    out = E("Rotate", angle=90)(img)
    assert out.size == (20, 40)
    assert out.getpixel((15, 5))[0] > 200 and out.getpixel((5, 5))[2] > 200  # clockwise: top-left -> top-right
    clip = E("Rotate", angle=45, upsize=False, clip=True)(img)
    assert clip.size == (40, 20) and clip.getpixel((20, 10))[3] == 255
    shrink = E("Rotate", angle=90, upsize=False, clip=False)(img)
    assert shrink.size == (40, 20)
    a = arr(shrink)
    assert a[10, 20, 3] == 255 and a[10, 2, 3] == 0  # scaled to fit, sides empty
    up = E("Rotate", angle=30)(img)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    assert up.size == (round(40 * c + 20 * s), round(40 * s + 20 * c))
    assert E("Rotate", angle=0)(img) is img


def test_rounded_corners():
    out = E("RoundedCorners", corner_radius=6)(solid(30, 20, (5, 6, 7, 255)))
    assert out.size == (30, 20)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((29, 19))[3] == 0
    assert out.getpixel((15, 10)) == (5, 6, 7, 255) and out.getpixel((15, 0)) == (5, 6, 7, 255)
    assert 0 < out.getpixel((1, 1))[3] < 255 or out.getpixel((1, 1))[3] == 0
    assert E("RoundedCorners", corner_radius=0)(solid(5, 5)).getpixel((0, 0)) == (100, 150, 200, 255)


def test_scale():
    img = solid(100, 50)
    assert E("Scale", width_percentage=50)(img).size == (50, 25)
    assert E("Scale", width_percentage=0, height_percentage=200)(img).size == (200, 100)
    assert E("Scale", width_percentage=10, height_percentage=50)(img).size == (10, 25)
    assert E("Scale", width_percentage=0, height_percentage=0)(img).size == (100, 50)


def test_skew():
    img = solid(20, 10, (255, 0, 0, 255))
    out = E("Skew", horizontally=10)(img)
    assert out.size == (30, 10)
    a = arr(out)
    assert a[0, 2, 3] > 200 and a[8, 2, 3] < 50  # top row starts at x=0, bottom rows are shifted right
    assert a[8, 25, 3] > 200 and a[0, 27, 3] < 50
    v = E("Skew", vertically=-6)(img)
    assert v.size == (20, 16)
    assert E("Skew")(img) is img
