"""QR encode -> zbarimg decode round trips."""
from __future__ import annotations

import pytest
from PIL import Image

from sharel.tools.qr import decode_file, decode_image, decode_symbols, encode

SAMPLES = [
    "https://getsharex.com/",
    "hello world",
    "héllo wörld ✓",
    "日本語のテキスト",
    "emoji 😀 and\nmultiple\nlines",
    "x" * 500,
]


@pytest.mark.parametrize("text", SAMPLES)
def test_round_trip(text):
    img = encode(text, size=400)
    assert img.size == (400, 400) and img.mode == "RGB"
    assert decode_image(img) == [text]


def test_error_correction_border_and_natural_size():
    small = encode("abc", size=0, border=0, error_correction="L")
    assert small.size == (210, 210)  # version 1 = 21 modules * 10 px
    assert small.getpixel((0, 0)) == (0, 0, 0)  # finder pattern touches the edge without a border
    high = encode("abc", size=0, error_correction="H")
    assert high.getpixel((0, 0)) == (255, 255, 255)
    assert decode_image(high) == ["abc"]
    with pytest.raises(ValueError):
        encode("abc", error_correction="Z")
    with pytest.raises(ValueError):
        encode("")


def test_no_code():
    assert decode_image(Image.new("RGB", (100, 100), "white")) == []
    noise = Image.effect_noise((120, 120), 80).convert("RGB")
    assert decode_image(noise) == []


def test_multiple_codes_with_unicode():
    a = encode("first ✓\nline", size=240)
    b = encode("second", size=240)
    c = encode("third 第三", size=240)
    canvas = Image.new("RGB", (800, 300), "white")
    for i, im in enumerate((a, b, c)):
        canvas.paste(im, (20 + i * 260, 30))
    assert sorted(decode_image(canvas)) == sorted(["first ✓\nline", "second", "third 第三"])
    syms = decode_symbols(canvas)
    assert all(s.type == "QR-Code" and len(s.polygon) == 4 for s in syms)


def test_transparent_image_and_file(tmp_path):
    gray = encode("on transparent", size=300).convert("L")
    # black modules on a fully transparent (white, alpha 0) background
    alpha = gray.point(lambda v: 255 if v < 128 else 0)
    qr = Image.merge("RGBA", (gray, gray, gray, alpha))
    path = tmp_path / "qr.png"
    qr.save(path)
    assert decode_file(path) == ["on transparent"]
