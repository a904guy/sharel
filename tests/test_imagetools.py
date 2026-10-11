"""Image tools: output sizes and pixels."""
from __future__ import annotations

import threading

import numpy as np
import pytest
from PIL import Image

from sharel.core.enums import ContentAlignment
from sharel.tools import imagetools as it
from sharel.tools._common import OperationCancelled


def solid(w, h, c):
    return Image.new("RGBA", (w, h), c)


RED, GREEN, BLUE = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)


# ---- combiner ------------------------------------------------------------------------------

def test_combine_vertical_left_with_space_and_autofill():
    a, b = solid(40, 10, RED), solid(20, 15, GREEN)
    out = it.combine_images([a, b], "Vertical", "LeftOrTop", space=5, auto_fill_background=True)
    assert out.size == (40, 30)
    assert out.getpixel((0, 0)) == RED
    assert out.getpixel((0, 12)) == RED  # the gap is filled with image 1's bottom-right pixel
    assert out.getpixel((5, 20)) == GREEN
    assert out.getpixel((30, 20)) == RED  # right of the narrow image: background fill


def test_combine_horizontal_alignments_and_transparent_bg():
    a, b = solid(10, 40, RED), solid(10, 20, BLUE)
    centre = it.combine_images([a, b], "Horizontal", "Center", auto_fill_background=False)
    assert centre.size == (20, 40)
    assert centre.getpixel((15, 5)) == (0, 0, 0, 0)
    assert centre.getpixel((15, 10)) == BLUE and centre.getpixel((15, 29)) == BLUE
    assert centre.getpixel((15, 30)) == (0, 0, 0, 0)
    bottom = it.combine_images([a, b], "Horizontal", "RightOrBottom", auto_fill_background=False)
    assert bottom.getpixel((15, 19)) == (0, 0, 0, 0) and bottom.getpixel((15, 20)) == BLUE


def test_combine_wrap_after():
    imgs = [solid(10, 10, c) for c in (RED, GREEN, BLUE, RED)]
    out = it.combine_images(imgs, "Horizontal", wrap_after=2, space=2)
    assert out.size == (22, 22)
    assert out.getpixel((15, 15)) == RED and out.getpixel((15, 0)) == GREEN and out.getpixel((0, 15)) == BLUE
    with pytest.raises(ValueError):
        it.combine_images([], "Vertical")


def test_combine_files(tmp_path):
    for i, c in enumerate((RED, GREEN)):
        solid(8, 8, c).save(tmp_path / f"{i}.png")
    out = it.combine_files([tmp_path / "0.png", tmp_path / "1.png"], orientation="Vertical")
    assert out.size == (8, 16) and out.getpixel((0, 12)) == GREEN


# ---- splitter ------------------------------------------------------------------------------

def gradient_img(w, h):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[..., 0] = np.arange(w)[None, :] % 256
    arr[..., 1] = np.arange(h)[:, None] % 256
    return Image.fromarray(arr, "RGB")


def test_split_rows_columns_like_sharex():
    img = gradient_img(101, 61)
    parts = it.split_image(img, 2, 3)
    assert len(parts) == 6 and all(p.size == (33, 30) for p in parts)
    assert parts[4].getpixel((0, 0)) == (33, 30, 0)  # row 2, column 2


def test_split_by_tile_size_and_files(tmp_path):
    img = gradient_img(100, 50)
    grid = it.split_by_tile_size(img, 40, 30)
    assert [[t.size for t in row] for row in grid] == [[(40, 30), (40, 30), (20, 30)], [(40, 20), (40, 20), (20, 20)]]
    src = tmp_path / "pic.png"
    img.save(src)
    files = it.split_to_files(src, tmp_path / "out", rows=2, columns=2)
    assert [f.name for f in files] == ["pic_1_1.png", "pic_1_2.png", "pic_2_1.png", "pic_2_2.png"]
    with Image.open(files[3]) as t:
        assert t.size == (50, 25) and t.getpixel((0, 0)) == (50, 25, 0)
    tiles = it.split_to_files(src, tmp_path / "t", tile_size=(60, 50), fmt="JPEG")
    assert [f.name for f in tiles] == ["pic_1_1.jpg", "pic_1_2.jpg"]


# ---- thumbnails / resize -------------------------------------------------------------------

def test_thumbnail_sizes_and_batch(tmp_path):
    assert it.thumbnail_size((400, 200), 200, 0) == (200, 100)
    assert it.thumbnail_size((400, 200), 0, 50) == (100, 50)
    assert it.thumbnail_size((400, 200), 100, 100) == (100, 50)
    p = tmp_path / "big.png"
    gradient_img(400, 300).save(p)
    seen = []
    out = it.thumbnail_files([p], width=100, quality=80, progress=lambda d, t: seen.append((d, t)))
    assert out == [tmp_path / "big-thumbnail.jpg"]
    with Image.open(out[0]) as t:
        assert t.size == (100, 75) and t.format == "JPEG"
    assert seen == [(0, 1), (1, 1)]


def test_resizer_modes(tmp_path):
    img = gradient_img(200, 100)
    assert it.resize_image(img, percentage=50).size == (100, 50)
    assert it.resize_image(img, width=50).size == (50, 25)
    assert it.resize_image(img, width=50, height=80, keep_aspect=False).size == (50, 80)
    assert it.resize_image(img, width=400, allow_enlarge=False).size == (200, 100)
    near = it.resize_image(img, width=400, resample="Nearest")
    assert near.size == (400, 200) and near.getpixel((3, 0)) == (1, 0, 0)
    with pytest.raises(ValueError):
        it.resize_image(img, width=10, resample="Magic")
    p = tmp_path / "x.png"
    img.save(p)
    [r] = it.resize_files([p], width=20)
    assert r.name == "x-resized.png"
    with Image.open(r) as im:
        assert im.size == (20, 10)


def test_batch_cancel(tmp_path):
    p = tmp_path / "x.png"
    gradient_img(10, 10).save(p)
    ev = threading.Event()
    ev.set()
    with pytest.raises(OperationCancelled):
        it.resize_files([p, p], width=5, cancel=ev)


# ---- converter -----------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,ext,pil", [("PNG", "png", "PNG"), ("JPEG", "jpg", "JPEG"), ("GIF", "gif", "GIF"),
                                         ("BMP", "bmp", "BMP"), ("TIFF", "tif", "TIFF"), ("WEBP", "webp", "WEBP")])
def test_converter_formats(tmp_path, fmt, ext, pil):
    src = tmp_path / "src.png"
    img = solid(30, 20, (10, 200, 30, 255))
    img.paste((0, 0, 0, 0), (0, 0, 10, 20))  # transparent stripe
    img.save(src)
    out = it.convert_file(src, fmt, tmp_path / "out", quality=90)
    assert out.name == f"src.{ext}"
    with Image.open(out) as im:
        assert im.format == pil and im.size == (30, 20)
        px = im.convert("RGBA").getpixel((20, 10))
        assert abs(px[0] - 10) <= 6 and abs(px[1] - 200) <= 6 and abs(px[2] - 30) <= 6
        left = im.convert("RGBA").getpixel((2, 10))
        if pil in ("JPEG", "BMP"):
            assert left[:3] == (255, 255, 255) or min(left[:3]) > 240  # flattened on white
        elif pil in ("PNG", "TIFF", "WEBP", "GIF"):
            assert left[3] == 0
    with pytest.raises(ValueError):
        it.convert_file(src, "XYZ")


def test_convert_files_batch(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        solid(5, 5, RED).save(p)
        paths.append(p)
    out = it.convert_files(paths, "JPEG", tmp_path / "j")
    assert sorted(o.name for o in out) == ["0.jpg", "1.jpg", "2.jpg"]


# ---- watermark -----------------------------------------------------------------------------

def _bbox_of_changes(a, b):
    return it.compare_images(a, b).bbox


def test_text_watermark_positions_and_opacity():
    base = solid(300, 200, (0, 0, 0, 255))
    br = it.watermark_text(base, "ShareL", ContentAlignment.BottomRight, margin=10, font_size=30)
    x0, y0, x1, y1 = _bbox_of_changes(base, br)
    assert x1 <= 290 and y1 <= 190 and x1 > 250 and y1 > 160
    tl = it.watermark_text(base, "ShareL", "TopLeft", margin=10, font_size=30)
    x0, y0, x1, y1 = _bbox_of_changes(base, tl)
    assert x0 >= 10 and y0 >= 10 and x0 < 30 and y0 < 30
    mc = it.watermark_text(base, "ShareL", ContentAlignment.MiddleCenter, font_size=30)
    x0, y0, x1, y1 = _bbox_of_changes(base, mc)
    assert abs((x0 + x1) / 2 - 150) < 8 and abs((y0 + y1) / 2 - 100) < 8
    full = np.asarray(it.watermark_text(base, "█", "MiddleCenter", font_size=60, opacity=100))[..., 0].max()
    half = np.asarray(it.watermark_text(base, "█", "MiddleCenter", font_size=60, opacity=50))[..., 0].max()
    assert full == 255 and 120 <= half <= 135
    assert it.watermark_text(base, "   ").tobytes() == base.tobytes()


def test_image_watermark_scale_and_position():
    base = solid(400, 200, (255, 255, 255, 255))
    logo = solid(100, 100, BLUE)
    out = it.watermark_image(base, logo, ContentAlignment.TopRight, margin=5, scale=50)
    # 50% of 400x200 -> fits in 200x100 -> logo becomes 100x100
    assert _bbox_of_changes(base, out) == (295, 5, 395, 105)
    assert out.getpixel((300, 50)) == BLUE
    ghost = it.watermark_image(base, logo, "BottomLeft", margin=0, opacity=50, scale=0)
    r, _g, b, _a = ghost.getpixel((50, 150))
    assert b == 255 and 120 <= r <= 135
    huge = it.watermark_image(base, solid(1000, 1000, RED), "MiddleCenter", scale=0)
    assert huge.size == (400, 200) and huge.getpixel((0, 0)) == RED


# ---- comparer ------------------------------------------------------------------------------

def test_compare_identical_and_changes():
    a = gradient_img(50, 40)
    same = it.compare_images(a, a.copy())
    assert same.identical and same.changed_pixels == 0 and same.bbox is None and same.similarity_percent == 100
    b = a.copy()
    for x in range(10, 20):
        for y in range(5, 8):
            b.putpixel((x, y), (255, 255, 255))
    r = it.compare_images(a, b)
    assert not r.identical
    assert r.changed_pixels == 30 and r.total_pixels == 2000
    assert r.changed_percent == pytest.approx(1.5)
    assert r.bbox == (10, 5, 20, 8)
    assert r.diff_image.getpixel((12, 6)) == (255, 255, 255) and r.diff_image.getpixel((0, 0)) == (0, 0, 0)
    hl = it.highlight_differences(a, b)
    assert hl.getpixel((12, 6)) == (255, 0, 0) and hl.getpixel((0, 0)) != (255, 0, 0)


def test_compare_different_sizes():
    a = solid(10, 10, RED)
    b = solid(12, 10, RED)
    r = it.compare_images(a, b)
    assert r.total_pixels == 120 and r.changed_pixels == 20 and r.bbox == (10, 0, 12, 10)


# ---- icons ---------------------------------------------------------------------------------

def test_png_to_ico_and_back(tmp_path):
    src = gradient_img(300, 200).convert("RGBA")
    ico = it.image_to_ico(src, tmp_path / "app.ico")
    with Image.open(ico) as im:
        assert im.format == "ICO"
        assert sorted(im.info["sizes"]) == [(s, s) for s in it.ICON_SIZES]
    frames = it.ico_frames(ico)
    assert [f.size for f in frames] == [(s, s) for s in it.ICON_SIZES]
    # non-square input is centred on a transparent square
    assert frames[-1].getpixel((128, 2))[3] == 0 and frames[-1].getpixel((128, 128))[3] == 255
    png = it.ico_to_png(ico, tmp_path / "app.png")
    with Image.open(png) as p:
        assert p.size == (256, 256) and p.format == "PNG"
    small = it.image_to_ico(src, tmp_path / "s.ico", sizes=[16, 32])
    assert [f.size for f in it.ico_frames(small)] == [(16, 16), (32, 32)]
    with pytest.raises(ValueError):
        it.image_to_ico(src, tmp_path / "x.ico", sizes=[20])
