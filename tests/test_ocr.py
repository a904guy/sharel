"""OCR via tesseract on text rendered with Pillow."""
from __future__ import annotations

import threading

import pytest
from PIL import Image, ImageDraw, ImageFont

from sharel.tools._common import OperationCancelled, ToolError
from sharel.tools.ocr import list_languages, ocr_file, ocr_image


def render(lines: list[str], size: int = 32, rgba: bool = False) -> Image.Image:
    font = ImageFont.load_default(size=size)
    w = max(int(font.getlength(t)) for t in lines) + 60
    h = len(lines) * int(size * 1.6) + 40
    img = Image.new("RGBA" if rgba else "RGB", (w, h), (255, 255, 255, 0) if rgba else "white")
    d = ImageDraw.Draw(img)
    for i, t in enumerate(lines):
        d.text((30, 20 + i * int(size * 1.6)), t, fill=(0, 0, 0, 255) if rgba else "black", font=font)
    return img


def test_languages():
    langs = list_languages()
    assert "eng" in langs and "osd" not in langs


def test_multi_line_round_trip():
    img = render(["Hello World", "Quick brown fox jumps"])
    text = ocr_image(img, "eng", 2.0)
    low = text.lower()
    for word in ("hello", "world", "quick", "brown", "fox", "jumps"):
        assert word in low, text
    assert len(text.splitlines()) >= 2


def test_single_line_and_small_text_upscaled(tmp_path):
    img = render(["ShareL screenshot tool"], size=14)
    text = ocr_image(img, "eng", scale_factor=3.0, single_line=True)
    assert "\n" not in text
    assert "screenshot" in text.lower()
    p = tmp_path / "t.png"
    render(["Transparent background"], size=28, rgba=True).save(p)
    assert "transparent" in ocr_file(p).lower()


def test_cancel_and_bad_language():
    ev = threading.Event()
    ev.set()
    with pytest.raises(OperationCancelled):
        ocr_image(render(["x"]), cancel=ev)
    with pytest.raises(ToolError, match="tesseract"):
        ocr_image(render(["abc"]), language="zzz_not_installed")
