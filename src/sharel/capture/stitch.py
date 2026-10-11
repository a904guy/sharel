"""Scrolling-capture stitching, ported from ShareX's ScrollingCaptureManager.CombineImages.

How it works (per new frame, exactly as ShareX):

1. *End detection* — if the new frame is pixel-identical to the previous frame the page did not scroll any more:
   status ``End`` (when ``auto_detect_scroll_end``), the frame is not added.
2. *Bottom edge* — ``ignore_bottom = max(50, h/10)`` rows at the bottom of the combined image are not used for
   matching and are replaced by the new frame's rows. With ``auto_ignore_bottom_edge`` the rows that are
   identical at the bottom of the combined image and of the new frame (a sticky footer / status bar) are added
   to it, and the largest value seen so far is kept. ShareL's ``ignore_last_rows`` (a fixed sticky-footer
   height) is an extra floor for that value. The result is capped at ``h/3``.
3. *Overlap search* — rows are compared byte-exactly, ignoring ``max(50, w/20)`` (≤ ``w/3``) columns on each side
   (scroll bars, shadows). For every candidate row ``cy`` of the new frame (bottom → top) count how many
   consecutive rows going up equal the combined image's rows going up from its last non-ignored row; the
   longest run wins, the search stops once a run reaches ``h/2``.
4. *Combine* — combined[: H - ignore_bottom] + frame[match_index + 1 :]. The new frame's bottom edge (footer)
   therefore appears once, at the very bottom of the result; a sticky header stays only at the top, because
   only rows below the matched overlap are appended; a short last scroll appends just the new rows.
5. *Fallback* — when no row matches, the best match seen so far (offset and bottom edge) is reused as a guess
   (status ``BestGuess``, overall ``PartiallySuccessful``). With no earlier match the frame is rejected
   (status ``NoMatch``, overall ``Failed``) unless ``append_without_match=True``, which appends the whole
   frame below the result (useful for manual mode).

API
---
``StitchStatus``   per-frame outcome: ``First``, ``Added``, ``BestGuess``, ``End``, ``NoMatch``, ``NoNewRows``,
                   ``LimitReached``.
``ScrollingCaptureStatus``  overall: ``Failed``, ``PartiallySuccessful``, ``Successful`` (ShareX's enum).
``Stitcher(options=ScrollingCaptureOptions(), *, append_without_match=False)``
    ``add(frame: PIL.Image) -> StitchStatus``  feed the next screenshot (all frames must have the same size).
    ``result() -> PIL.Image | None``           the combined image so far (RGBA, a copy).
    ``status``                                 overall ``ScrollingCaptureStatus``.
    ``finished``                               True once ``End``/``LimitReached`` was returned.
    ``last``                                   ``StitchInfo`` about the last frame (match index/count, ignored
                                               bottom rows, ``added_rows`` = how many rows the result grew by).
    ``frame_count``                            frames accepted into the result.
``combine(frames, options=None) -> PIL.Image``  stitch a list of frames in one go (stops at ``End``).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from PIL import Image

from ..core.config import ScrollingCaptureOptions

__all__ = ["ScrollingCaptureStatus", "StitchInfo", "StitchStatus", "Stitcher", "combine"]


class StitchStatus(StrEnum):
    First = "First"
    Added = "Added"
    BestGuess = "BestGuess"
    End = "End"
    NoMatch = "NoMatch"
    NoNewRows = "NoNewRows"
    LimitReached = "LimitReached"


class ScrollingCaptureStatus(StrEnum):
    Failed = "Failed"
    PartiallySuccessful = "PartiallySuccessful"
    Successful = "Successful"


@dataclass(frozen=True)
class StitchInfo:
    status: StitchStatus
    match_index: int = -1
    match_count: int = 0
    ignore_bottom: int = 0
    added_rows: int = 0


def _to_array(frame: Image.Image) -> np.ndarray:
    im = frame if frame.mode == "RGBA" else frame.convert("RGBA")
    return np.asarray(im, dtype=np.uint8)


def _row_ids(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map every row of two (rows × bytes) uint8 arrays to an int so that equal rows get equal ids."""
    both = np.ascontiguousarray(np.concatenate([a, b], axis=0))
    if both.shape[1] == 0:
        ids = np.zeros(both.shape[0], dtype=np.int64)
    else:
        view = both.view(np.dtype((np.void, both.shape[1]))).ravel()
        _, ids = np.unique(view, return_inverse=True)
        ids = ids.ravel()
    return ids[: a.shape[0]], ids[a.shape[0]:]


class Stitcher:
    def __init__(self, options: ScrollingCaptureOptions | None = None, *, append_without_match: bool = False) -> None:
        self.options = options or ScrollingCaptureOptions()
        self.append_without_match = append_without_match
        self._result: np.ndarray | None = None
        self._previous: np.ndarray | None = None
        self._best_match_count = 0
        self._best_match_index = 0
        self._best_ignore_bottom = 0
        self.status = ScrollingCaptureStatus.Failed
        self.finished = False
        self.frame_count = 0
        self.last = StitchInfo(StitchStatus.First)

    # ---- public ---------------------------------------------------------------------------
    def result(self) -> Image.Image | None:
        if self._result is None:
            return None
        return Image.fromarray(self._result.copy(), "RGBA")

    def add(self, frame: Image.Image) -> StitchStatus:
        cur = _to_array(frame)
        if cur.ndim != 3 or cur.shape[0] < 1 or cur.shape[1] < 1:
            raise ValueError("empty frame")
        if self._result is None:
            self._result = cur.copy()
            self._previous = cur
            self.frame_count = 1
            self.status = ScrollingCaptureStatus.Successful
            return self._set(StitchInfo(StitchStatus.First, added_rows=cur.shape[0]))
        if cur.shape != self._previous.shape:
            raise ValueError(f"frame size {cur.shape[1]}x{cur.shape[0]} differs from the first frame "
                             f"{self._previous.shape[1]}x{self._previous.shape[0]}")
        if self.finished:
            return self.last.status
        if self.options.auto_detect_scroll_end and np.array_equal(cur, self._previous):
            self.finished = True
            return self._set(StitchInfo(StitchStatus.End))
        max_scrolls = self.options.max_scroll_count
        if max_scrolls and max_scrolls > 0 and self.frame_count > max_scrolls:
            self.finished = True
            return self._set(StitchInfo(StitchStatus.LimitReached))
        info = self._combine(cur)
        if info.status in (StitchStatus.Added, StitchStatus.BestGuess):
            self._previous = cur
            self.frame_count += 1
        return self._set(info)

    # ---- internals ------------------------------------------------------------------------
    def _set(self, info: StitchInfo) -> StitchStatus:
        self.last = info
        return info.status

    def _combine(self, cur: np.ndarray) -> StitchInfo:
        result = self._result
        assert result is not None
        h, w = cur.shape[:2]
        rh = result.shape[0]

        side = max(50, w // 20)
        side = min(side, w // 3)
        # rows as bytes, without the side columns; only the last h rows of the result can take part
        avail = min(rh, h)
        tail = result[rh - avail:, side:w - side].reshape(avail, -1)
        cur_rows = cur[:, side:w - side].reshape(h, -1)
        res_ids, cur_ids = _row_ids(tail, cur_rows)
        if avail < h:  # result shorter than a frame (after odd frames): pad the top with never-matching rows
            res_ids = np.concatenate([-1 - np.arange(h - avail), res_ids])

        match_limit = h // 2
        ignore_max = h // 3
        ignore_bottom = max(50, h // 10)
        if self.options.ignore_last_rows > 0:
            ignore_bottom = max(ignore_bottom, int(self.options.ignore_last_rows))
        if self.options.auto_ignore_bottom_edge:
            # identical rows at the very bottom of both images = sticky footer
            for i in range(ignore_max + 1):
                if res_ids[h - 1 - i] != cur_ids[h - 1 - i]:
                    ignore_bottom += i
                    break
            ignore_bottom = max(ignore_bottom, self._best_ignore_bottom)
        ignore_bottom = min(ignore_bottom, ignore_max)

        rect_bottom = h - ignore_bottom - 1  # last compared row of the result, in tail coordinates
        match_count = 0
        match_index = 0
        cy = h - 1
        while cy >= 0 and match_count < match_limit:
            k = min(cy + 1, match_limit, rect_bottom + 1)
            if k > 0:
                a = res_ids[rect_bottom - k + 1: rect_bottom + 1]
                b = cur_ids[cy - k + 1: cy + 1]
                eq = (a == b)[::-1]  # from the bottom row going up
                run = k if eq.all() else int(np.argmin(eq))
                if run > match_count:
                    match_count = run
                    match_index = cy
            cy -= 1

        best_guess = False
        if match_count == 0 and self._best_match_count > 0:
            match_count = self._best_match_count
            match_index = self._best_match_index
            ignore_bottom = self._best_ignore_bottom
            best_guess = True

        if match_count == 0:
            if self.append_without_match:
                self._result = np.concatenate([result, cur], axis=0)
                if self.status == ScrollingCaptureStatus.Successful:
                    self.status = ScrollingCaptureStatus.PartiallySuccessful
                return StitchInfo(StitchStatus.BestGuess, -1, 0, 0, h)
            self.status = ScrollingCaptureStatus.Failed
            return StitchInfo(StitchStatus.NoMatch, ignore_bottom=ignore_bottom)

        match_height = h - match_index - 1
        if match_height <= 0:
            return StitchInfo(StitchStatus.NoNewRows, match_index, match_count, ignore_bottom)

        if match_count > self._best_match_count:
            self._best_match_count = match_count
            self._best_match_index = match_index
            self._best_ignore_bottom = ignore_bottom

        self._result = np.concatenate([result[: rh - ignore_bottom], cur[match_index + 1:]], axis=0)
        if best_guess:
            self.status = ScrollingCaptureStatus.PartiallySuccessful
        elif self.status != ScrollingCaptureStatus.PartiallySuccessful:
            self.status = ScrollingCaptureStatus.Successful
        return StitchInfo(StitchStatus.BestGuess if best_guess else StitchStatus.Added, match_index, match_count,
                          ignore_bottom, match_height - ignore_bottom)


def combine(frames: Iterable[Image.Image], options: ScrollingCaptureOptions | None = None) -> Image.Image:
    st = Stitcher(options)
    for f in frames:
        if st.add(f) in (StitchStatus.End, StitchStatus.LimitReached):
            break
    out = st.result()
    if out is None:
        raise ValueError("no frames")
    return out
