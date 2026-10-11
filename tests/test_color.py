"""Colour model, ShareX colour picker formats and screen colour picker info tokens."""
from __future__ import annotations

import random

import pytest

from sharel.tools.color import FORMATS, Color, format_color, format_pixel_info, parse


def test_hex_orders():
    c = Color(0x12, 0x34, 0x56, 0x78)
    assert c.to_hex() == "#123456"
    assert c.to_hex("RGBA") == "#12345678"
    assert c.to_hex("ARGB") == "#78123456"
    assert c.to_hex(prefix="", upper=False) == "123456"
    assert Color.from_hex("#78123456") == c  # 8 digits default to ARGB (ShareL config strings)
    assert Color.from_hex("12345678", order="RGBA") == c
    assert Color.from_hex("0xABCDEF") == Color(0xAB, 0xCD, 0xEF)
    assert Color.from_hex("#f0a") == Color(0xFF, 0x00, 0xAA)
    with pytest.raises(ValueError):
        Color.from_hex("#12345")


def test_decimal_like_sharex():
    c = Color(255, 0, 0, 128)
    assert c.to_decimal() == 16711680
    assert c.to_decimal("RGBA") == 0xFF000080
    assert c.to_decimal("ARGB") == 0x80FF0000
    assert Color.from_decimal(16711680) == Color(255, 0, 0)
    assert Color.from_decimal(0x80FF0000, "ARGB") == c
    assert Color.from_decimal(0xFF000080, "RGBA") == c


def test_known_conversions():
    red = Color(255, 0, 0)
    assert red.to_hsb() == (0.0, 100.0, 100.0)
    assert red.to_hsl() == (0.0, 100.0, 50.0)
    assert red.to_cmyk() == (0.0, 100.0, 100.0, 0.0)
    assert Color(0, 0, 0).to_cmyk() == (0.0, 0.0, 0.0, 100.0)
    assert Color.from_hsb(120, 100, 100) == Color(0, 255, 0)
    assert Color.from_hsl(240, 100, 50) == Color(0, 0, 255)
    assert Color.from_cmyk(0, 0, 0, 100) == Color(0, 0, 0)
    assert Color.from_cmyk(100, 0, 100, 0) == Color(0, 255, 0)
    gray = Color(128, 128, 128)
    assert gray.to_hsb().saturation == 0
    assert round(gray.to_cmyk().key, 2) == 49.8


def test_sharex_copy_formats():
    c = Color(255, 128, 0)
    assert format_color(c, "RGB") == "255, 128, 0"
    assert format_color(c, "Hex") == "#FF8000"
    assert format_color(c, "Decimal") == str(0xFF8000)
    assert format_color(c, "HSB") == "30.1°, 100.0%, 100.0%"
    assert format_color(c, "CMYK") == "0.0%, 49.8%, 100.0%, 0.0%"
    all_text = format_color(c, "All")
    assert all_text.splitlines()[0] == "RGBA (Red, Green, Blue, Alpha) = 255, 128, 0, 255"
    assert "Hex (RGB, RGBA, ARGB) = #FF8000, #FF8000FF, #FFFF8000" in all_text
    with pytest.raises(ValueError):
        format_color(c, "Nope")


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_every_format_round_trips(fmt):
    rnd = random.Random(1234 + len(fmt))
    for _ in range(300):
        alpha = 255 if fmt not in ("RGBA", "HexRGBA", "HexARGB", "All") else rnd.randrange(256)
        c = Color(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), alpha)
        back = parse(format_color(c, fmt), hex8_order="RGBA" if fmt == "HexRGBA" else "ARGB")
        assert back is not None, (fmt, c)
        tol = 1 if fmt in ("HSB", "HSL", "CMYK") else 0
        assert all(abs(x - y) <= tol for x, y in zip(back.rgba, c.rgba, strict=True)), (fmt, c, back)


def test_primaries_round_trip_exactly_through_lossy_formats():
    for c in (Color(255, 0, 0), Color(0, 255, 0), Color(0, 0, 255), Color(0, 0, 0), Color(255, 255, 255)):
        for fmt in ("HSB", "HSL", "CMYK"):
            assert parse(format_color(c, fmt)) == c


def test_parse_misc():
    assert parse("rgb(1, 2, 3)") == Color(1, 2, 3)
    assert parse("rgba(1,2,3,0.5)") == Color(1, 2, 3, 128)
    assert parse("rgba(1 2 3 / 50%)") == Color(1, 2, 3, 128)
    assert parse("1 2 3") == Color(1, 2, 3)
    assert parse("hsv(0, 100%, 100%)") == Color(255, 0, 0)
    assert parse("hsl(120deg, 100%, 25%)") == Color(0, 128, 0)
    assert parse("cmyk(0%, 100%, 100%, 0%)") == Color(255, 0, 0)
    assert parse("RebeccaPurple") == Color(0x66, 0x33, 0x99)
    assert parse("  #00ff00 ") == Color(0, 255, 0)
    assert parse("ff8000") == Color(255, 128, 0)
    assert parse("65280") == Color(0, 255, 0)
    for bad in ("", "nope", "300, 0, 0", "#12", "rgb(1,2)", "16777216"):
        assert parse(bad) is None, bad


def test_names():
    assert Color(255, 0, 0).name() == "red"
    assert Color(0, 255, 255).name() == "aqua"
    assert Color(1, 2, 3).name() == ""
    assert Color(255, 0, 0, 10).name() == ""


def test_pixel_info_default_template():
    c = Color(18, 52, 86)
    tpl = "RGB: $r255, $g255, $b255$nHex: $hex$nX: $x Y: $y"
    assert format_pixel_info(tpl, c, 10, 20) == "RGB: 18, 52, 86\nHex: 123456\nX: 10 Y: 20"


def test_pixel_info_all_tokens():
    c = Color(255, 128, 0, 64)
    assert format_pixel_info("$HEX|$hex|$Hex", c) == "FF8000|ff8000|ff8000"
    assert format_pixel_info("$rHEX $gHEX $bHEX $rhex $ghex $bhex", c) == "FF 80 00 ff 80 00"
    assert format_pixel_info("$c100 $m100 $y100 $k100", c) == "0 49.8 100 0"
    assert format_pixel_info("$r1 $g1 $b1", c) == "1 0.502 0"
    assert format_pixel_info("$g1{1} $g1{5} $g1{99}", c) == "0.5 0.50196 0.501960784313725"
    assert format_pixel_info("$r $g $b $a", c) == "255 128 0 64"
    assert format_pixel_info("$a255 $ahex $aHEX $a1", c) == "64 40 40 0.251"
    assert format_pixel_info("$name|$n|$N", Color(255, 0, 0), newline="\r\n") == "red|\r\n|\r\n"
    assert format_pixel_info("$X,$Y", c, 3, 4) == "3,4"
    assert format_pixel_info("", c) == ""
