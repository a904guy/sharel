"""Scrolling-capture stitching on synthetic frames with known offsets."""
from __future__ import annotations

import itertools
from pathlib import Path

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


# -- an editor-like document: text lines, then the blank scroll-past-end space --

def _editor_doc(view: int, n: int = 120, pitch: int = 21, width: int = 713) -> Image.Image:
    from PIL import ImageDraw, ImageFont
    im = Image.new("RGBA", (width, n * pitch + view - pitch), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=15)
    for i in range(n):
        d.text((4, i * pitch + 2), f"Line {i + 1:03d} of the ShareL scrolling test", fill="black", font=font)
    return im


@pytest.mark.parametrize("view", [400, 470])
@pytest.mark.parametrize("overlap", [20, 40, 49, 50, 60, 120, 235, 350])  # below ~20 px it can be all blank
def test_editor_document_with_blank_tail_is_stitched_exactly(view, overlap):
    """Every scroll step from GTK's Page Down (10 % kept: 40 px of 400) to small wheel steps, with a short
    last scroll into blank space: the result is the document, pixel for pixel."""
    doc = _editor_doc(view)
    end = doc.height - view
    offsets = [*range(0, end, view - overlap), end, end]  # the last scroll is short; then it stops moving
    st = Stitcher(ScrollingCaptureOptions())
    for y in offsets:
        if st.add(doc.crop((0, y, doc.width, y + view))) == StitchStatus.End:
            break
    assert st.status == ScrollingCaptureStatus.Successful
    out = st.result()
    assert out.size == doc.size
    assert np.array_equal(np.asarray(out), np.asarray(doc))


def test_blank_rows_alone_are_not_a_match():
    """Two frames that differ only by where blank space starts: a run of identical blank rows fits any
    offset, so the true one (where the text continues) must win."""
    doc = _editor_doc(470)
    end = doc.height - 470
    st = Stitcher(ScrollingCaptureOptions())
    st.add(doc.crop((0, end - 99, doc.width, end - 99 + 470)))
    assert st.add(doc.crop((0, end, doc.width, end + 470))) == StitchStatus.Added
    assert st.last.added_rows == 99
    assert np.array_equal(np.asarray(st.result()), np.asarray(doc.crop((0, end - 99, doc.width, doc.height))))


# -- real frames: gnome-text-editor scrolled by the mouse wheel (GTK scrolls ~121.5 px per step, so the
#    same text is antialiased a level or two differently in each frame and rows are never byte-identical) --

GTK_FRAMES = sorted((Path(__file__).parent / "data" / "scroll_gtk").glob("f*.png"))


def _true_shift(a: np.ndarray, b: np.ndarray) -> int:
    """Brute force, independent of the stitcher: the shift with the least mean difference."""
    h = a.shape[0]
    errs = [np.abs(a[d:, 50:-50].astype(int) - b[:h - d, 50:-50]).mean() for d in range(h - 20)]
    return int(np.argmin(errs))


def _ocr_lines(im: Image.Image) -> list[int]:
    import re
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im.convert("L").resize((im.width * 2, im.height * 2), Image.LANCZOS).save(f.name)
        out = subprocess.run(["tesseract", f.name, "-", "--psm", "6"], capture_output=True, text=True, check=True)
    return [int(n) for n in re.findall(r"Line\s+(\d{3})\b", out.stdout)]


@pytest.mark.parametrize("names", [["f000", "f001", "f002", "f003", "f004"],
                                   ["f018", "f019", "f020", "f021", "f022"]], ids=["top", "end"])
def test_real_gtk_frames_stitch_to_the_document(names):
    frames = [Image.open(p).convert("RGBA") for p in GTK_FRAMES if p.stem in names]
    assert len(frames) == 5
    arrs = [np.asarray(f) for f in frames]
    shifts = [_true_shift(a, b) for a, b in itertools.pairwise(arrs)]
    assert shifts[0] > 0 and shifts[1] > 0  # these really scrolled (at the end it then stops: shift 0)
    st = Stitcher(ScrollingCaptureOptions())
    statuses = [st.add(f) for f in frames]
    assert StitchStatus.BestGuess not in statuses and st.status == ScrollingCaptureStatus.Successful, statuses
    out = st.result()
    assert out.height == frames[0].height + sum(shifts)
    nums = _ocr_lines(out)
    # from the first frame's first line to the last frame's last line, each once, in order
    first, last = _ocr_lines(frames[0])[0], _ocr_lines(frames[-1])[-1]
    assert nums == list(range(first, last + 1)), nums
    if names[0] == "f000":
        assert first == 1  # the top of the document
    else:
        assert last == 120  # its last line, after the short last scroll into blank space


@pytest.mark.parametrize("overlap", [40, 120, 300])
def test_noisy_frames_are_stitched_within_the_noise(overlap):
    """±3 per channel of independent noise in every frame (sensor-like): no row is byte-identical, the
    shifts must still be found exactly."""
    view = 470
    doc = _editor_doc(view)
    ref = np.asarray(doc).astype(int)
    end = doc.height - view
    rng = np.random.default_rng(7)
    st = Stitcher(ScrollingCaptureOptions())
    for y in [*range(0, end, view - overlap), end, end]:
        f = ref[y:y + view].copy()
        f[..., :3] = np.clip(f[..., :3] + rng.integers(-3, 4, f[..., :3].shape), 0, 255)
        if st.add(Image.fromarray(f.astype(np.uint8), "RGBA")) == StitchStatus.End:
            break
    assert st.status == ScrollingCaptureStatus.Successful
    out = np.asarray(st.result()).astype(int)
    assert out.shape == ref.shape
    assert np.abs(out - ref).max() <= 3


def test_tolerance_never_matches_a_blank_overlap():
    """Noisy frames whose only overlap is blank space: no shift can be told, so nothing is invented."""
    view = 470
    doc = _editor_doc(view)
    ref = np.asarray(doc).astype(int)
    end = doc.height - view
    rng = np.random.default_rng(3)

    def noisy(y):
        f = ref[y:y + view].copy()
        f[..., :3] = np.clip(f[..., :3] + rng.integers(-3, 4, f[..., :3].shape), 0, 255)
        return Image.fromarray(f.astype(np.uint8), "RGBA")
    st = Stitcher(ScrollingCaptureOptions())
    st.add(noisy(end - 99))
    # the true shift (99) is found through the text above the blank tail...
    assert st.add(noisy(end)) == StitchStatus.Added and st.last.added_rows == 99
    # ...but two frames that overlap only in blank rows give no match at all
    blank = Stitcher(ScrollingCaptureOptions())
    tail = ref[-view:].copy()
    tail[..., :3] = 255
    f1 = tail.copy()
    f2 = tail.copy()
    f1[:20, :, :3] = 0  # different content at the top of each, blank below
    f2[-20:, :, :3] = 0
    blank.add(Image.fromarray(f1.astype(np.uint8), "RGBA"))
    assert blank.add(Image.fromarray(f2.astype(np.uint8), "RGBA")) == StitchStatus.NoMatch
