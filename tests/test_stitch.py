"""Scrolling-capture stitching on synthetic frames with known offsets."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from sharel.capture.stitch import ScrollingCaptureStatus, Stitcher, StitchStatus, combine
from sharel.core.config import ScrollingCaptureOptions

W = 320


def rand_rgba(h: int, w: int = W, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(h, w, 4), dtype=np.uint8)
    arr[..., 3] = 255
    return arr


def img(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(a), "RGBA")


def windows(total: int, h: int, step: int) -> list[int]:
    ys = list(range(0, total - h + 1, step))
    if ys[-1] != total - h:
        ys.append(total - h)  # partial last scroll
    return ys


def test_random_tall_image_reassembles_exactly():
    page = rand_rgba(2000, seed=1)
    h = 400
    ys = windows(2000, h, 150)
    assert ys[-1] - ys[-2] == 100  # the last scroll is a short one
    st = Stitcher()
    statuses = [st.add(img(page[y:y + h])) for y in ys]
    assert statuses[0] == StitchStatus.First
    assert all(s == StitchStatus.Added for s in statuses[1:])
    assert st.last.added_rows == 100
    # scrolling no further: same frame again -> end detected, nothing added
    assert st.add(img(page[ys[-1]:ys[-1] + h])) == StitchStatus.End
    assert st.finished
    out = np.asarray(st.result())
    assert out.shape == page.shape
    assert np.array_equal(out, page)
    assert st.status == ScrollingCaptureStatus.Successful
    assert st.frame_count == len(ys)


def test_combine_helper_and_rgb_frames():
    page = rand_rgba(900, seed=2)
    frames = [img(page[y:y + 300]).convert("RGB") for y in windows(900, 300, 120)]
    frames.append(frames[-1].copy())
    out = combine(frames)
    assert np.array_equal(np.asarray(out), page)


@pytest.mark.parametrize("mode", ["auto", "fixed"])
def test_sticky_footer_appears_once(mode):
    content = rand_rgba(1500, seed=3)
    footer = rand_rgba(70, seed=4)
    ch = 330
    opts = ScrollingCaptureOptions(auto_ignore_bottom_edge=(mode == "auto"),
                                   ignore_last_rows=0 if mode == "auto" else 70)
    st = Stitcher(opts)
    ys = windows(1500, ch, 140)
    for y in ys:
        frame = np.concatenate([content[y:y + ch], footer])
        assert st.add(img(frame)) in (StitchStatus.First, StitchStatus.Added)
    expected = np.concatenate([content, footer])
    assert np.array_equal(np.asarray(st.result()), expected)
    assert st.last.ignore_bottom >= 70


def test_sticky_header_stays_on_top():
    content = rand_rgba(1200, seed=5)
    header = rand_rgba(60, seed=6)
    ch = 340
    st = Stitcher()
    for y in windows(1200, ch, 100):
        st.add(img(np.concatenate([header, content[y:y + ch]])))
    expected = np.concatenate([header, content])
    assert np.array_equal(np.asarray(st.result()), expected)


def test_no_overlap_first_time_fails_then_best_guess():
    page = rand_rgba(1000, seed=7)
    h = 300
    st = Stitcher()
    st.add(img(page[0:h]))
    unrelated = rand_rgba(h, seed=99)
    assert st.add(img(unrelated)) == StitchStatus.NoMatch
    assert st.status == ScrollingCaptureStatus.Failed
    assert np.asarray(st.result()).shape[0] == h  # unchanged

    st = Stitcher()
    st.add(img(page[0:h]))
    assert st.add(img(page[100:100 + h])) == StitchStatus.Added
    # page row 249 (last row above the 50 ignored ones) sits at row 149 of the scrolled frame
    assert (st.last.match_index, st.last.ignore_bottom, st.last.added_rows) == (149, 50, 100)
    before = np.asarray(st.result()).shape[0]
    assert st.add(img(rand_rgba(h, seed=100))) == StitchStatus.BestGuess
    assert st.status == ScrollingCaptureStatus.PartiallySuccessful
    after = np.asarray(st.result())
    assert after.shape[0] == before - 50 + (h - 149 - 1)


def test_append_without_match_for_manual_mode():
    st = Stitcher(append_without_match=True)
    a, b = rand_rgba(200, seed=8), rand_rgba(200, seed=9)
    st.add(img(a))
    assert st.add(img(b)) == StitchStatus.BestGuess
    assert np.array_equal(np.asarray(st.result()), np.concatenate([a, b]))


def test_end_detection_can_be_disabled_and_limits():
    page = rand_rgba(600, seed=10)
    st = Stitcher(ScrollingCaptureOptions(auto_detect_scroll_end=False))
    st.add(img(page[0:300]))
    assert st.add(img(page[0:300])) == StitchStatus.Added  # duplicate re-adds the trimmed bottom edge only
    assert np.array_equal(np.asarray(st.result()), page[0:300])
    lim = Stitcher(ScrollingCaptureOptions(max_scroll_count=1))
    lim.add(img(page[0:300]))
    assert lim.add(img(page[100:400])) == StitchStatus.Added
    assert lim.add(img(page[200:500])) == StitchStatus.LimitReached
    assert lim.finished
    with pytest.raises(ValueError):  # every frame must have the first frame's size
        st.add(img(page[0:200]))
