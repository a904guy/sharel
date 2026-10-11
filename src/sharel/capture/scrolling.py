"""Scrolling capture (ShareX's ScrollingCaptureManager): scroll a window step by step, capture the
selected area after each step, and stitch the frames (capture/stitch.py).

ScrollMethod: MouseWheel (wheel clicks through Mutter RemoteDesktop), DownArrow (Down key
``scroll_amount`` times), PageDown (Page Down key), ScrollMessage (Windows' WM_VSCROLL has no
Wayland equivalent; it maps to the wheel). ScrollTopMethod: SendHomeKey, ScrollMessage/MouseWheel
(wheel up until the top), None. Manual mode: the user scrolls and a timer captures.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from ..core.config import ScrollingCaptureOptions
from ..core.enums import ScrollingCaptureScrollTopMethod, ScrollMethod
from .stitch import ScrollingCaptureStatus, Stitcher, StitchStatus

log = logging.getLogger("sharel.scrolling")


@dataclass
class ScrollResult:
    image: Image.Image | None
    status: ScrollingCaptureStatus
    frames: int
    statuses: list[str] = field(default_factory=list)
    error: str = ""


class ScrollingCapture:
    def __init__(self, rect: tuple[int, int, int, int], opts: ScrollingCaptureOptions, *,
                 capture: Callable[[tuple[int, int, int, int]], Image.Image],
                 activate_target: Callable[[], None] | None = None,
                 progress: Callable[[int, Image.Image | None, str], None] | None = None,
                 input_factory=None) -> None:
        self.rect = tuple(int(v) for v in rect)
        self.opts = opts
        self.capture = capture
        self.activate_target = activate_target
        self.progress = progress or (lambda *_: None)
        self.input_factory = input_factory
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.result: ScrollResult | None = None

    def start(self, on_done: Callable[[ScrollResult], None]) -> None:
        def run():
            try:
                self.result = self.run()
            except Exception as e:
                log.exception("scrolling capture failed")
                self.result = ScrollResult(None, ScrollingCaptureStatus.Failed, 0, error=str(e))
            on_done(self.result)
        self.thread = threading.Thread(target=run, daemon=True, name="scrolling-capture")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _input(self):
        if self.input_factory is not None:
            return self.input_factory()
        from .remoteinput import RemoteInput
        return RemoteInput()

    def _scroll(self, inp) -> None:
        m = self.opts.scroll_method
        if m in (ScrollMethod.MouseWheel, ScrollMethod.ScrollMessage):
            inp.wheel(max(1, self.opts.scroll_amount))
        elif m == ScrollMethod.DownArrow:
            inp.key("Down", max(1, self.opts.scroll_amount))
        elif m == ScrollMethod.PageDown:
            inp.key("Page_Down", 1)

    def _scroll_to_top(self, inp) -> None:
        t = self.opts.scroll_top_method
        if t == ScrollingCaptureScrollTopMethod.SendHomeKey:
            inp.key("Home")
        elif t in (ScrollingCaptureScrollTopMethod.ScrollMessage, ScrollingCaptureScrollTopMethod.MouseWheel):
            for _ in range(20):
                inp.wheel(-25)

    def run(self) -> ScrollResult:
        o = self.opts
        stitcher = Stitcher(o, append_without_match=o.manual_mode)
        statuses: list[str] = []
        x, y, w, h = self.rect
        time.sleep(max(0, o.start_delay) / 1000)
        if o.manual_mode:
            last = None
            while not self.stop_event.is_set():
                frame = self.capture(self.rect)
                if last is None or frame.tobytes() != last:
                    st = stitcher.add(frame)
                    statuses.append(st.value)
                    self.progress(stitcher.frame_count, stitcher.result(), st.value)
                    last = frame.tobytes()
                if stitcher.frame_count >= o.max_scroll_count:
                    break
                self.stop_event.wait(max(50, o.manual_capture_interval) / 1000)
            return ScrollResult(stitcher.result(), stitcher.status, stitcher.frame_count, statuses)
        with self._input() as inp:
            if self.activate_target:
                self.activate_target()
            inp.move_to(x + w / 2, y + h / 2)
            self._scroll_to_top(inp)
            time.sleep(max(0, o.scroll_delay) / 1000)
            for _ in range(max(1, o.max_scroll_count) + 1):
                if self.stop_event.is_set():
                    break
                frame = self.capture(self.rect)
                st = stitcher.add(frame)
                statuses.append(st.value)
                self.progress(stitcher.frame_count, stitcher.result(), st.value)
                if stitcher.finished or st in (StitchStatus.End, StitchStatus.LimitReached):
                    break
                if st == StitchStatus.NoMatch and not o.auto_detect_scroll_end:
                    break
                self._scroll(inp)
                time.sleep(max(0, o.scroll_delay) / 1000)
        return ScrollResult(stitcher.result(), stitcher.status, stitcher.frame_count, statuses)
