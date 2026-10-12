"""Scrolling capture runner with a simulated scrollable page."""
import numpy as np
from PIL import Image

from sharel.capture.scrolling import ScrollingCapture
from sharel.core.config import ScrollingCaptureOptions
from sharel.core.enums import ScrollMethod


class FakePage:
    """A tall page seen through a viewport; the fake input device scrolls it."""

    def __init__(self, height=1800, view=400, width=300, step=90, seed=3):
        rng = np.random.default_rng(seed)
        self.page = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
        self.view, self.step, self.offset = view, step, 0
        self.moves, self.keys = [], []

    def frame(self, _rect):
        o = self.offset
        return Image.fromarray(self.page[o:o + self.view], "RGB").convert("RGBA")

    # RemoteInput protocol
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def move_to(self, x, y):
        self.moves.append((x, y))

    def wheel(self, steps):
        self.offset = max(0, min(len(self.page) - self.view, self.offset + steps * self.step // 2))

    def key(self, name, times=1):
        self.keys.append((name, times))
        if name == "Home":
            self.offset = 0
        elif name == "Page_Down":  # GTK keeps 10 % of the view: 40 px of 400
            self.offset = max(0, min(len(self.page) - self.view, self.offset + self.view - 40))
        elif name == "Down":  # one line per press
            self.offset = max(0, min(len(self.page) - self.view, self.offset + 37 * times))


def run(method, **kw):
    page = FakePage(**kw)
    page.offset = 500  # starts scrolled down: the runner must go to the top first
    opts = ScrollingCaptureOptions(start_delay=0, scroll_delay=0, scroll_method=method, scroll_amount=2,
                                   max_scroll_count=100)
    opts.auto_ignore_bottom_edge = False
    sc = ScrollingCapture((10, 20, 300, 400), opts, capture=page.frame, input_factory=lambda: page)
    return sc.run(), page


def test_mouse_wheel_scrolling_reassembles_page():
    res, page = run(ScrollMethod.MouseWheel)
    assert res.image is not None
    out = np.asarray(res.image.convert("RGB"))
    assert out.shape == page.page.shape
    assert np.array_equal(out, page.page)
    assert page.moves == [(160.0, 220.0)]  # pointer over the centre of the area
    assert ("Home", 1) in page.keys and res.statuses[-1] == "End"


def test_page_down_method():
    res, page = run(ScrollMethod.PageDown)
    assert np.array_equal(np.asarray(res.image.convert("RGB")), page.page)
    assert any(k[0] == "Page_Down" for k in page.keys)


def test_down_arrow_method():
    res, page = run(ScrollMethod.DownArrow)
    assert np.array_equal(np.asarray(res.image.convert("RGB")), page.page)
    assert ("Down", 2) in page.keys  # scroll_amount presses per step


def test_scroll_message_method_uses_the_wheel():
    """ShareX's ScrollMessage (WM_VSCROLL) has no Wayland equivalent: it scrolls with wheel clicks."""
    res, page = run(ScrollMethod.ScrollMessage)
    assert np.array_equal(np.asarray(res.image.convert("RGB")), page.page)
    assert not any(k[0] in ("Down", "Page_Down") for k in page.keys)


def test_manual_mode_captures_until_stopped():
    page = FakePage()
    opts = ScrollingCaptureOptions(start_delay=0, manual_mode=True, manual_capture_interval=50, max_scroll_count=50)
    frames = {"n": 0}

    def capture(rect):
        frames["n"] += 1
        if frames["n"] in (3, 6, 9):
            page.wheel(2)  # the user scrolls
        return page.frame(rect)
    sc = ScrollingCapture((0, 0, 300, 400), opts, capture=capture, input_factory=lambda: page)
    import threading
    threading.Timer(0.8, sc.stop).start()
    res = sc.run()
    assert res.frames == 4  # first frame + three scrolls
    assert res.image.height == 400 + 3 * 90
