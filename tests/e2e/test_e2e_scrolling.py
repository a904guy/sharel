"""Scenario 11: scrolling capture of a long gnome-text-editor document. The stitched image is taller
than the window, and OCR of its numbered lines shows every line once, in order (no duplicated or
missing seams)."""
from __future__ import annotations

import itertools
import os
import re
import signal
import subprocess
import time

import pytest
from conftest import wait_for
from PIL import Image

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

N = 120
LINES = [f"Line {i:03d} of the ShareL scrolling test" for i in range(1, N + 1)]


@pytest.fixture(scope="module")
def app(session):
    session.start_app()
    yield session
    session.stop_app()


def ocr_line_numbers(path) -> list[int]:
    """An independent OCR (tesseract CLI, not ShareL's): the 'Line NNN' numbers, top to bottom."""
    big = path.with_suffix(".x2.png")
    with Image.open(path) as im:
        im.convert("L").resize((im.width * 2, im.height * 2), Image.LANCZOS).save(big)
    out = subprocess.run(["tesseract", str(big), "-", "--psm", "6"], capture_output=True, text=True, check=True)
    return [int(n) for n in re.findall(r"Line\s+(\d{3})\b", out.stdout)]


async def test_s11_scrolling_capture_of_a_long_document(app):
    s = app
    doc = s.state / "long-document.txt"
    doc.write_text("\n".join(LINES) + "\n")
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("app_launch", app="org.gnome.TextEditor", args=["--standalone", str(doc)])
        ed = wait_for(lambda: next((w for w in s.windows() if w["wm_class"] == "org.gnome.TextEditor"
                                    and "long-document" in w["title"]), None), what="text editor with the document")
        s.ext("MoveResize", f"uint64 {ed['id']}", "120", "60", "760", "560")
        time.sleep(1.5)
        x, y, w, h = s.ext("GetWindowDetails", f"uint64 {ed['id']}")["frame"]
        # the text area: below the header bar, clear of the overlay scrollbar on the right
        rect = (x + 8, y + 50, w - 48, h - 76)
        await call("screenshot", window=ed["title"], max_width=1000)  # evidence: the document before
        before = s.screenshots()

        s.sharel("-ScrollingCapture")
        wait_for(lambda: [o for o in s.windows() if "region capture" in o["title"]], what="overlay")
        time.sleep(0.5)
        rx, ry, rw, rh = rect
        await call("mouse_drag", from_x=rx, from_y=ry, to_x=rx + rw, to_y=ry + rh, restore_focus=False)
        sw = s.wait_window("scrolling capture")
        time.sleep(1.0)
        await call("click", text="Start capture", window=sw["title"])
        # the window hides while it scrolls the area, and comes back with the result
        s.wait_window("scrolling capture", gone=True, timeout=10)
        sw = s.wait_window("scrolling capture", timeout=120)
        time.sleep(1.0)
        await call("screenshot", window=sw["title"], max_width=1000)  # evidence: the stitched preview
        await call("click", text="Upload / run after capture tasks", window=sw["title"])
        shot = wait_for(lambda: [p for p in s.screenshots() if p not in before and p.suffix == ".png"],
                        timeout=30, what="saved scrolling capture")[0]
        time.sleep(0.5)
        with Image.open(shot) as im:
            size = im.size
        (s.artifacts / "scrolling-capture.png").write_bytes(shot.read_bytes())

        # taller than the window: several screens of the document stitched together
        assert size[1] >= 3 * rh, (size, rh)
        nums = ocr_line_numbers(s.artifacts / "scrolling-capture.png")
        (s.artifacts / "ocr-line-numbers.txt").write_text(" ".join(map(str, nums)) + "\n")
        assert len(nums) == len(set(nums)), f"duplicated lines at a seam: {nums}"
        assert nums == sorted(nums), f"lines out of order: {nums}"
        assert nums[0] <= 2 and nums[-1] == N, (nums[:3], nums[-3:])
        # OCR may miss a scattered line; a lost seam drops a block of consecutive lines
        missing = sorted(set(range(1, N + 1)) - set(nums))
        assert len(missing) <= 6, f"{len(missing)} lines missing: {missing}"
        assert not any(b - a == 1 for a, b in itertools.pairwise(missing)), f"a block of lines is missing: {missing}"
        os.kill(ed["pid"], signal.SIGTERM)
