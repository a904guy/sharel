"""Monitor test patterns: pixel checks."""
from __future__ import annotations

import pytest

from sharel.tools import monitortest as mt


def test_solid_and_gray():
    im = mt.solid((10, 5), (1, 2, 3))
    assert im.size == (10, 5) and im.mode == "RGB"
    assert im.tobytes() == bytes((1, 2, 3)) * 50
    assert mt.grayscale((4, 4), 300).tobytes() == b"\xff" * 48
    with pytest.raises(ValueError):
        mt.solid((0, 5), (0, 0, 0))


def test_gradients():
    h = mt.gradient((256, 4), (0, 0, 0), (255, 255, 255), "Horizontal")
    assert [h.getpixel((x, 2))[0] for x in (0, 1, 128, 255)] == [0, 1, 128, 255]
    assert h.getpixel((10, 0)) == h.getpixel((10, 3))
    v = mt.gradient((3, 101), (255, 0, 0), (0, 0, 255), "Vertical")
    assert v.getpixel((0, 0)) == (255, 0, 0)
    assert v.getpixel((2, 100)) == (0, 0, 255)
    assert v.getpixel((1, 50)) == (128, 0, 128)
    f = mt.gradient((50, 30), (0, 0, 0), (255, 255, 255), "ForwardDiagonal")
    assert f.getpixel((0, 0)) == (0, 0, 0) and f.getpixel((49, 29)) == (255, 255, 255)
    b = mt.gradient((50, 30), (0, 0, 0), (255, 255, 255), "BackwardDiagonal")
    assert b.getpixel((49, 0)) == (0, 0, 0) and b.getpixel((0, 29)) == (255, 255, 255)
    with pytest.raises(ValueError):
        mt.gradient((5, 5), direction="Sideways")


def test_gradient_bands():
    im = mt.gradient_bands((256, 40))
    assert im.getpixel((255, 5)) == (255, 0, 0)
    assert im.getpixel((255, 15)) == (0, 255, 0)
    assert im.getpixel((255, 25)) == (0, 0, 255)
    assert im.getpixel((255, 35)) == (255, 255, 255)
    assert im.getpixel((100, 35)) == (100, 100, 100)
    assert all(im.getpixel((0, y)) == (0, 0, 0) for y in range(40))


def test_patterns():
    hl = mt.pattern((20, 20), "HorizontalLines", 5)
    assert hl.getpixel((7, 0)) == (0, 0, 0) and hl.getpixel((7, 4)) == (0, 0, 0)
    assert hl.getpixel((7, 5)) == (255, 255, 255) and hl.getpixel((7, 10)) == (0, 0, 0)
    vl = mt.pattern((20, 20), "VerticalLines", 3)
    assert vl.getpixel((2, 9)) == (0, 0, 0) and vl.getpixel((3, 9)) == (255, 255, 255)
    cb = mt.pattern((20, 20), "Checkerboard", 4)
    assert cb.getpixel((0, 0)) == (0, 0, 0)
    assert cb.getpixel((4, 0)) == (255, 255, 255)
    assert cb.getpixel((4, 4)) == (0, 0, 0)
    assert cb.getpixel((0, 4)) == (255, 255, 255)
    hist = mt.pattern((16, 16), "Checkerboard", 4).convert("L").histogram()
    assert hist[0] == hist[255] == 128


def test_grid():
    g = mt.grid((101, 51), spacing=25, line_width=1)
    for x in (0, 25, 50, 75, 100):
        assert g.getpixel((x, 13)) == (255, 255, 255)
    for y in (0, 25, 50):
        assert g.getpixel((13, y)) == (255, 255, 255)
    assert g.getpixel((13, 13)) == (0, 0, 0)


def test_motion_frames_move_bars():
    f0 = mt.motion_frame((300, 10), 0, fps=50, speed=500)
    # offset 0 -> start = -50: black on [-50,0), [50,100), [150,200) ...
    assert f0.getpixel((0, 5)) == (255, 255, 255)
    assert f0.getpixel((50, 5)) == (0, 0, 0) and f0.getpixel((99, 5)) == (0, 0, 0)
    assert f0.getpixel((100, 5)) == (255, 255, 255)
    f1 = mt.motion_frame((300, 10), 1, fps=50, speed=500)  # moved 10 px
    assert f1.getpixel((60, 5)) == (0, 0, 0) and f1.getpixel((59, 5)) == (255, 255, 255)
    f10 = mt.motion_frame((300, 10), 10, fps=50, speed=500)  # moved a full period
    assert f10.tobytes() == f0.tobytes()
    hb = mt.motion_frame((10, 200), 0, direction="HorizontalBars")
    assert hb.getpixel((3, 60)) == (0, 0, 0) and hb.getpixel((3, 110)) == (255, 255, 255)
    frames = list(mt.motion_frames((40, 40), 3))
    assert len(frames) == 3


def test_render_dispatch():
    s = mt.MonitorTestSettings(mode="Rgb", red=10, green=20, blue=30)
    assert mt.render(s, (4, 4)).getpixel((0, 0)) == (10, 20, 30)
    s.mode = "Motion"
    assert mt.render(s, (200, 4), frame=3).size == (200, 4)
    for mode in mt.MODES:
        s.mode = mode
        assert mt.render(s, (64, 32)).size == (64, 32)
    s.mode = "Bogus"
    with pytest.raises(ValueError):
        mt.render(s, (4, 4))
