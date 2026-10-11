"""Image Beautifier (ShareX.MediaLib ImageBeautifier)."""
from __future__ import annotations

import numpy as np
from PIL import Image

from sharel.core.serialize import to_jsonable
from sharel.effects import beautifier
from sharel.effects.beautifier import BeautifierOptions, beautify
from sharel.effects.common import GradientInfo


def busy_image(w: int = 100, h: int = 60) -> Image.Image:
    """No uniform border, so smart padding crops nothing; top-left pixel is (10, 20, 30)."""
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = np.arange(w)[None, :] * 2
    a[..., 1] = np.arange(h)[:, None] * 3
    a[..., 2] = 30
    a[..., 3] = 255
    a[0, 0, :3] = (10, 20, 30)
    return Image.fromarray(a, "RGBA")


def close(a, b, tol) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b, strict=True))


def test_defaults_are_sharex_defaults():
    o = BeautifierOptions()
    assert (o.margin, o.padding, o.smart_padding, o.rounded_corner) == (80, 40, True, 20)
    assert (o.shadow_radius, o.shadow_opacity, o.shadow_distance, o.shadow_angle) == (30, 80, 10, 180)
    assert o.shadow_color == "#FF000000" and o.background_type == "Gradient"
    assert o.background_gradient.type == "ForwardDiagonal"
    assert [(s.color, s.location) for s in o.background_gradient.colors] == [("#FFFF512F", 0.0), ("#FFDD2476", 100.0)]
    assert o.background_color == "#FF222222" and o.background_image_file_path == ""
    assert o.shadow_offset() == (0, 10)  # 180 degrees: straight down
    assert BeautifierOptions(shadow_angle=90).shadow_offset() == (10, 0)


def test_default_render_size_corners_shadow_and_background():
    img = busy_image()
    out = beautify(img, {})
    m, p = 80, 40
    assert out.size == (100 + 2 * (p + m), 60 + 2 * (p + m))
    a = np.array(out).astype(int)
    # background gradient: top-left starts at (255, 81, 47), bottom-right ends at (221, 36, 118)
    assert close(a[0, 0], (255, 81, 47, 255), 2)
    assert close(a[-1, -1], (221, 36, 118, 255), 2)
    # the screenshot itself sits inside margin + padding, unchanged
    assert tuple(a[m + p + 10, m + p + 20]) == tuple(np.array(img)[10, 20])
    # padding is filled with the screenshot's top-left colour
    assert tuple(a[m + 5, m + 60][:3]) == (10, 20, 30) and tuple(a[m + p + 30, m + 3][:3]) == (10, 20, 30)
    # rounded card corner: the corner pixel is not padding colour
    assert tuple(a[m, m][:3]) != (10, 20, 30)
    # drop shadow below the card is darker than the background the same distance above it
    card_bottom = m + 2 * p + 60
    cx = out.width // 2
    below = a[card_bottom + 8, cx][:3].sum()
    above = a[m - 9, cx][:3].sum()
    assert below < above - 60


def test_smart_padding_crops_uniform_border():
    img = Image.new("RGBA", (300, 200), (255, 255, 255, 255))
    img.paste((0, 0, 200, 255), (50, 40, 250, 150))
    out = beautify(img, {"margin": 10, "padding": 5})
    assert out.size == (200 + 30, 110 + 30)
    a = np.array(out)
    assert tuple(a[12, 30][:3]) == (255, 255, 255)  # padding in the original corner colour
    assert tuple(a[60, 100][:3]) == (0, 0, 200)
    no_smart = beautify(img, {"margin": 10, "padding": 5, "smart_padding": False})
    assert no_smart.size == (300 + 30, 200 + 30)


def test_background_types(tmp_path):
    img = busy_image(20, 10)
    base = {"margin": 6, "padding": 0, "rounded_corner": 0, "shadow_opacity": 0}
    color = beautify(img, {**base, "background_type": "Color", "background_color": "#FF112233"})
    assert color.size == (32, 22) and color.getpixel((0, 0)) == (17, 34, 51, 255)
    assert color.getpixel((6, 6)) == img.getpixel((0, 0))
    clear = beautify(img, {**base, "background_type": "Transparent"})
    assert clear.getpixel((0, 0))[3] == 0
    semi = beautify(img, {**base, "background_type": "Color", "background_color": "#80112233"})
    assert semi.getpixel((0, 0))[3] == 0  # ShareX ignores non-opaque background colours
    bg = tmp_path / "bg.png"
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(bg)
    image = beautify(img, {**base, "background_type": "Image", "background_image_file_path": str(bg)})
    assert image.getpixel((0, 0)) == (0, 255, 0, 255) and image.getpixel((31, 21)) == (0, 255, 0, 255)
    grad = GradientInfo.of("Horizontal", ("#FF0000FF", 0), ("#FFFF0000", 100))
    g = beautify(img, BeautifierOptions(margin=6, padding=0, rounded_corner=0, shadow_opacity=0,
                                        background_gradient=grad))
    assert g.getpixel((0, 10))[2] > 200 and g.getpixel((31, 10))[0] > 200


def test_desktop_background_uses_wallpaper(tmp_path, monkeypatch):
    wall = tmp_path / "wall.png"
    Image.new("RGBA", (4, 4), (9, 8, 7, 255)).save(wall)
    monkeypatch.setattr(beautifier, "desktop_wallpaper_path", lambda: str(wall))
    out = beautify(busy_image(20, 10), {"margin": 4, "padding": 0, "shadow_opacity": 0,
                                        "background_type": "Desktop"})
    assert out.getpixel((0, 0)) == (9, 8, 7, 255)


def test_shadow_only_in_margin_and_rounded_corners():
    img = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
    img.putpixel((20, 20), (0, 0, 0, 255))  # keep smart padding from removing everything
    o = {"margin": 20, "padding": 0, "smart_padding": False, "rounded_corner": 10, "shadow_radius": 4,
         "shadow_distance": 0, "shadow_opacity": 100, "background_type": "Transparent"}
    out = np.array(beautify(img, o))
    assert out.shape[:2] == (80, 80)
    assert out[2, 2, 3] == 0  # far corner of the margin: no shadow
    assert out[20, 40, 3] == 255 and tuple(out[20, 40, :3]) == (255, 255, 255)
    assert 0 < out[18, 40, 3] < 255 and tuple(out[18, 40, :3]) == (0, 0, 0)  # soft shadow at the card edge
    # rounded corner: the card's corner pixel is cut away, only faint shadow remains there
    assert out[20, 20, 3] < 64 and tuple(out[20, 20, :3]) == (0, 0, 0)
    assert tuple(out[23, 23]) == (255, 255, 255, 255)


def test_options_serialize_into_task_settings_dict():
    o = BeautifierOptions(margin=5, background_type="Color")
    d = to_jsonable(o)
    assert d["margin"] == 5 and d["background_gradient"]["type"] == "ForwardDiagonal"
    assert beautify(busy_image(10, 10), d).size == beautify(busy_image(10, 10), o).size == (100, 100)
