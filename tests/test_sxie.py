"""ShareX .sxie packages and ShareX-format preset JSON."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from sharel.effects import sxie
from sharel.effects.base import EFFECTS, ImageEffectPreset, load_all
from sharel.effects.common import GradientInfo

from test_effects import nondefault

load_all()

# What ShareX 18 writes for a preset (JsonHelpers.SerializeToString + ImageEffectsSerializationBinder),
# plus one effect in the older fully qualified $type form and one type ShareL does not know.
SHAREX_CONFIG = {
    "Name": "Watermarked",
    "Effects": [
        {
            "$type": "Canvas",
            "Margin": "0, 0, 0, 30",
            "MarginMode": "AbsoluteSize",
            "Color": "Transparent",
            "Enabled": True,
        },
        {
            "$type": "ShareX.ImageEffectsLib.DrawText, ShareX.ImageEffectsLib",
            "Text": "Text watermark",
            "Placement": "BottomRight",
            "Offset": "0, 0",
            "AutoHide": False,
            "TextFont": "Arial, 11.25pt",
            "TextRenderingMode": "SystemDefault",
            "TextColor": "235, 235, 235",
            "DrawTextShadow": True,
            "TextShadowColor": "Black",
            "TextShadowOffset": "-1, -1",
            "CornerRadius": 4,
            "Padding": "5, 5, 5, 5",
            "DrawBorder": True,
            "BorderColor": "Black",
            "BorderSize": 1,
            "DrawBackground": True,
            "BackgroundColor": "42, 47, 56",
            "UseGradient": True,
            "Gradient": {
                "Type": "Vertical",
                "Colors": [
                    {"Color": "68, 120, 194", "Location": 0.0},
                    {"Color": "13, 58, 122", "Location": 50.0},
                    {"Color": "6, 36, 78", "Location": 50.0},
                    {"Color": "23, 89, 174", "Location": 100.0},
                ],
            },
            "Enabled": True,
        },
        {"$type": "Shadow", "Opacity": 0.8, "Size": 15, "Darkness": 0.5, "Color": "125, 10, 20, 30",
         "Offset": "3, 4", "AutoResize": False, "Enabled": False, "Name": "Soft shadow"},
        {"$type": "TornEdge", "Depth": 7, "Range": 9, "Sides": "Top, Right", "CurvedEdges": False,
         "Enabled": True},
        {"$type": "SomeFutureEffect", "Value": 1, "Enabled": True},
        {"$type": "Brightness", "Value": 0.25, "Enabled": True},
        {"$type": "Resize", "Width": 300.0, "Height": "oops", "Mode": "ResizeIfBigger", "Enabled": True},
    ],
}


def test_import_handwritten_sharex_config():
    p = sxie.from_sharex_json(json.dumps(SHAREX_CONFIG, indent=2))
    assert p.name == "Watermarked"
    assert [e.type_name for e in p.effects] == ["Canvas", "DrawText", "Shadow", "TornEdge", "Brightness", "Resize"]
    canvas, text, shadow, torn, bright, resize = p.effects
    assert canvas.margin == [0, 0, 0, 30] and canvas.color == "#00FFFFFF"
    assert text.placement == "BottomRight" and text.offset == [0, 0] and text.padding == [5, 5, 5, 5]
    assert text.text_color == "#FFEBEBEB" and text.text_shadow_color == "#FF000000"
    assert text.background_color == "#FF2A2F38" and text.use_gradient is True
    assert text.text_font == "Arial, 11.25pt"
    assert [s.location for s in text.gradient.colors] == [0.0, 50.0, 50.0, 100.0]
    assert text.gradient.colors[1].color == "#FF0D3A7A"
    assert shadow.opacity == 0.8 and shadow.size == 15 and shadow.darkness == 0.5
    assert shadow.color == "#7D0A141E" and shadow.offset == [3, 4] and shadow.auto_resize is False
    assert shadow.enabled is False and shadow.name == "Soft shadow" and shadow.describe() == "Soft shadow"
    assert torn.depth == 7 and torn.range == 9 and torn.sides == "Top, Right" and torn.curved_edges is False
    assert bright.value == 0.25
    assert resize.width == 300 and resize.height == 0 and resize.mode == "ResizeIfBigger"  # bad value -> default
    out = p.apply(Image.new("RGBA", (120, 60), (255, 255, 255, 255)))
    assert out.size == (120, 90)


def test_export_json_layout_matches_sharex():
    p = sxie.from_sharex_json(SHAREX_CONFIG)
    doc = json.loads(sxie.to_sharex_json(p))
    assert doc["Name"] == "Watermarked"
    first = doc["Effects"][0]
    assert next(iter(first)) == "$type" and first["$type"] == "Canvas"
    assert first == {"$type": "Canvas", "Margin": "0, 0, 0, 30", "MarginMode": "AbsoluteSize",
                     "Color": "0, 255, 255, 255", "Enabled": True}
    text = doc["Effects"][1]
    assert text["$type"] == "DrawText" and text["TextColor"] == "235, 235, 235" and text["Offset"] == "0, 0"
    assert text["Gradient"]["Colors"][0] == {"Color": "68, 120, 194", "Location": 0.0}
    assert doc["Effects"][2]["Name"] == "Soft shadow" and doc["Effects"][2]["Color"] == "125, 10, 20, 30"
    assert isinstance(doc["Effects"][4]["Value"], float)
    long = json.loads(sxie.to_sharex_json(p, long_type_names=True))
    assert long["Effects"][0]["$type"] == "ShareX.ImageEffectsLib.Canvas, ShareX.ImageEffectsLib"
    # property names: overrides and PascalCase
    assert "X0Y0" in sxie.effect_to_sharex(EFFECTS["MatrixConvolution"]())
    assert "Rr" in sxie.effect_to_sharex(EFFECTS["MatrixColor"]())
    assert "Type" in sxie.effect_to_sharex(EFFECTS["DrawBorder"]())
    assert "OffsetRed" in sxie.effect_to_sharex(EFFECTS["RGBSplit"]())


@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_sharex_json_round_trip_every_effect(name):
    e = nondefault(EFFECTS[name])
    e.enabled = True
    preset = ImageEffectPreset("All", [e])
    back = sxie.from_sharex_json(sxie.to_sharex_json(preset))
    assert back.effects == [e]
    assert back.name == "All"


def test_from_sharex_json_accepts_our_format():
    p = ImageEffectPreset("Ours", [EFFECTS["Inverse"](), EFFECTS["Blur"](radius=5)])
    back = sxie.from_sharex_json(json.dumps(p.to_jsonable()))
    assert back.to_jsonable() == p.to_jsonable()


def make_assets(tmp_path: Path) -> tuple[str, str]:
    logo = tmp_path / "src" / "logo.png"
    logo.parent.mkdir(parents=True)
    Image.new("RGBA", (6, 4), (255, 0, 0, 255)).save(logo)
    particles = tmp_path / "src" / "stars"
    particles.mkdir()
    Image.new("RGBA", (3, 3), (0, 255, 0, 255)).save(particles / "star.png")
    Image.new("RGB", (2, 2), (0, 0, 255)).save(particles / "dot.jpg")
    return str(logo), str(particles)


def test_sxie_export_import_round_trip_with_assets(tmp_path):
    logo, particles = make_assets(tmp_path)
    preset = ImageEffectPreset("My logo", [
        EFFECTS["DrawImage"](image_location=logo, placement="BottomRight", offset=[1, 1]),
        EFFECTS["DrawBackgroundImage"](image_file_path=logo),
        EFFECTS["DrawParticles"](image_folder=particles, image_count=3),
        EFFECTS["Sepia"](value=0.5),
    ])
    pkg = sxie.export_sxie(preset, tmp_path / "out" / "My logo.sxie")
    with zipfile.ZipFile(pkg) as zf:
        names = sorted(zf.namelist())
        cfg = json.loads(zf.read("Config.json"))
    assert names == ["Config.json", "My logo/logo.png", "My logo/stars/dot.jpg", "My logo/stars/star.png"]
    assert cfg["Effects"][0]["ImageLocation"] == "%ShareXImageEffects%\\My logo\\logo.png"
    assert cfg["Effects"][1]["ImageFilePath"] == "%ShareXImageEffects%\\My logo\\logo.png"
    assert cfg["Effects"][2]["ImageFolder"] == "%ShareXImageEffects%\\My logo\\stars"

    assets = tmp_path / "assets"
    back = sxie.import_sxie(pkg, assets)
    assert back.name == "My logo"
    draw, bg, parts, sepia = back.effects
    assert draw.image_location == str(assets / "My logo" / "logo.png")
    assert bg.image_file_path == str(assets / "My logo" / "logo.png")
    assert parts.image_folder == str(assets / "My logo" / "stars")
    assert (assets / "My logo" / "stars" / "star.png").is_file()
    assert sepia.value == 0.5 and draw.placement == "BottomRight" and draw.offset == [1, 1]
    with Image.open(draw.image_location) as im:
        assert im.size == (6, 4)
    out = back.effects[0](Image.new("RGBA", (20, 20), (0, 0, 0, 255)))
    assert out.getpixel((18, 18)) == (255, 0, 0, 255)  # the imported asset really is used


def test_import_default_assets_dir_resolves_sharex_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAREL_DATA_DIR", str(tmp_path / "data"))
    logo, _ = make_assets(tmp_path)
    pkg = sxie.export_sxie(ImageEffectPreset("P", [EFFECTS["DrawImage"](image_location=logo)]), tmp_path / "p.sxie")
    back = sxie.import_sxie(pkg)
    expected = tmp_path / "data" / "ImageEffects" / "P" / "logo.png"
    assert back.effects[0].image_location == str(expected) and expected.is_file()


def test_import_real_sharex_style_package(tmp_path):
    """A package as ShareX builds it: absolute Windows paths and the bare-name $type."""
    pkg = tmp_path / "Frame.sxie"
    config = {"Name": "", "Effects": [
        {"$type": "DrawImage", "ImageLocation": "C:\\Users\\me\\Documents\\ShareX\\ImageEffects\\Frame\\frame.png",
         "Placement": "TopLeft", "Offset": "0, 0", "SizeMode": "DontResize", "Size": "0, 0", "RotateFlip": "None",
         "Tile": False, "AutoHide": False, "InterpolationMode": "HighQualityBicubic",
         "CompositingMode": "SourceOver", "Opacity": 100, "Enabled": True},
        {"$type": "DrawBorder", "Type": "Inside", "Size": 2, "DashStyle": "Dot", "Color": "Red",
         "UseGradient": False, "Gradient": {"Type": "Horizontal", "Colors": []}, "Enabled": True},
    ]}
    buf = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
    frame = tmp_path / "frame.png"
    buf.save(frame)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("Config.json", "\ufeff" + json.dumps(config))
        zf.write(frame, "Frame/frame.png")
        zf.writestr("Frame/readme.txt", "not extracted")
        zf.writestr("../escape.png", b"evil")
    assets = tmp_path / "assets"
    p = sxie.import_sxie(pkg, assets)
    assert p.name == "Frame"  # empty Name -> file name
    assert p.effects[0].image_location == str(assets / "Frame" / "frame.png")
    assert not (assets / "Frame" / "readme.txt").exists()
    assert not (tmp_path / "escape.png").exists()
    border = p.effects[1]
    assert border.border_type == "Inside" and border.dash_style == "Dot" and border.color == "#FFFF0000"
    assert border.gradient == GradientInfo("Horizontal", [])


def test_import_rejects_package_without_config(tmp_path):
    pkg = tmp_path / "bad.sxie"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("x/y.png", b"")
    with pytest.raises(ValueError):
        sxie.import_sxie(pkg, tmp_path / "a")


def test_export_keeps_missing_paths(tmp_path):
    p = ImageEffectPreset("M", [EFFECTS["DrawImage"](image_location="/does/not/exist.png")])
    pkg = sxie.export_sxie(p, tmp_path / "m.sxie")
    with zipfile.ZipFile(pkg) as zf:
        assert zf.namelist() == ["Config.json"]
        assert json.loads(zf.read("Config.json"))["Effects"][0]["ImageLocation"] == "/does/not/exist.png"
    assert sxie.read_sxie_config(pkg).startswith("{")
