"""Background remover (rembg u2netp) on a synthetic subject.

The u2netp model (~4.7 MB) is downloaded once by rembg into a fixed, session-independent cache directory
(``~/.cache/sharel-test-models``) so the per-test XDG_CACHE_HOME from conftest doesn't force re-downloads.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from sharel.tools import bgremove

MODEL_DIR = Path.home() / ".cache" / "sharel-test-models"


def subject_image() -> Image.Image:
    img = Image.new("RGB", (320, 320), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.ellipse((70, 60, 250, 260), fill=(200, 30, 40), outline=(60, 10, 10), width=6)
    d.rectangle((130, 140, 190, 200), fill=(30, 30, 160))
    return img


def test_available_and_default_dir(monkeypatch, tmp_path):
    assert bgremove.is_available()
    monkeypatch.delenv("U2NET_HOME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert bgremove.default_model_dir() == tmp_path / "ShareL" / "rembg"
    monkeypatch.setenv("U2NET_HOME", "/opt/models")
    assert bgremove.default_model_dir() == Path("/opt/models")


def test_remove_background_makes_corners_transparent(tmp_path):
    out = bgremove.remove_background(subject_image(), model_dir=MODEL_DIR)
    assert out.mode == "RGBA" and out.size == (320, 320)
    alpha = np.asarray(out.getchannel("A"))
    for y, x in ((5, 5), (5, 314), (314, 5), (314, 314)):
        assert alpha[y, x] < 30, (x, y, alpha[y, x])
    assert alpha[160, 160] > 200
    assert alpha[100, 160] > 200
    # most of the plain background is gone, most of the subject is kept
    assert (alpha[:30, :] < 30).mean() > 0.95
    assert (alpha[120:220, 110:210] > 200).mean() > 0.9
    rgb = out.convert("RGB").getpixel((160, 160))
    assert all(abs(a - b) <= 3 for a, b in zip(rgb, (30, 30, 160), strict=True))  # subject colours kept
    assert any(p.suffix == ".onnx" for p in MODEL_DIR.rglob("*"))

    mask = bgremove.remove_background(subject_image(), model_dir=MODEL_DIR, only_mask=True)
    assert mask.mode == "L" and np.asarray(mask)[160, 160] > 200

    src = tmp_path / "pic.jpg"
    subject_image().save(src)
    dst = bgremove.remove_background_file(src, model_dir=MODEL_DIR)
    assert dst.name == "pic-nobg.png"
    with Image.open(dst) as im:
        assert im.mode == "RGBA" and im.getpixel((2, 2))[3] < 30
