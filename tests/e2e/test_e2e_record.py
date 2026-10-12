"""Scenario 10: a 3 s MP4 of a region and a 3 s GIF (hotkeys), checked with ffprobe and against the
screen's content; pause and abort."""
from __future__ import annotations

import json
import subprocess
import time

import numpy as np
import pytest
from conftest import wait_for
from PIL import Image

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


@pytest.fixture(scope="module")
def app(session):
    session.start_app()
    yield session
    session.stop_app()


# The last recorded region: -StartScreenRecorder reuses it (set by the hotkey test, which runs first).
LAST_REGION: list[tuple[int, int, int, int]] = []


def probe(path) -> tuple[float, int, int, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)
    v = next(st for st in info["streams"] if st["codec_type"] == "video")
    return float(info["format"]["duration"]), v["width"], v["height"], v["codec_name"]


def videos(s):
    return [p for p in s.screenshots() if p.suffix in (".mp4", ".gif", ".webm", ".mkv")]


async def record_with_hotkey(s, call, combo, rect, seconds):
    before = videos(s)
    await call("key", combo=combo, restore_focus=False)
    wait_for(lambda: [w for w in s.windows() if "region capture" in w["title"]], what="region overlay")
    time.sleep(0.4)
    x, y, w, h = rect
    await call("mouse_drag", from_x=x, from_y=y, to_x=x + w, to_y=y + h)
    s.wait_window("ShareL recording")  # the Stop / Pause / Abort control window
    t0 = time.monotonic()  # the recording started just before its controls appeared
    # something moving in the recorded area (the stream only carries damage, plus keepalives)
    while time.monotonic() - t0 < seconds:
        await call("mouse_move", x=x + 20 + (time.monotonic() - t0) * 60, y=y + h // 2)
        time.sleep(0.15)
    await call("screenshot", monitor=0, max_width=1000)  # evidence: border + controls while recording
    await call("key", combo=combo, restore_focus=False)  # the same hotkey stops it
    wall = time.monotonic() - t0
    s.wait_window("ShareL recording", gone=True)
    return wait_for(lambda: [p for p in videos(s) if p not in before], timeout=60, what="recording file")[0], wall


def screen_area(s, rect, name):
    x, y, w, h = rect
    path = s.artifacts / name
    s.ext("Screenshot", "area", "uint64 0", str(x), str(y), str(w), str(h), "false", str(path))
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def last_frame(s, video, name):
    path = s.artifacts / name
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.3", "-i", str(video), "-frames:v", "1",
                    str(path)], check=True)
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


async def test_s10_mp4_and_gif_by_hotkey(app):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        # Record over the main window: distinctive content, so a wrong source can't pass for it.
        s.sharel("-OpenMainWindow")
        win = s.wait_window("ShareL")
        fx, fy = win["frame"][:2]
        rect = (fx + 30, fy + 60, 400, 300)
        mp4, wall = await record_with_hotkey(s, call, "shift+Print", rect, 3.0)
        dur, w, h, codec = probe(mp4)
        assert mp4.suffix == ".mp4" and codec == "h264", (mp4, codec)
        assert (w, h) == (400, 300)
        assert dur >= 2.5 and abs(dur - wall) <= 1.0, f"{dur:.2f} s of video for {wall:.2f} s of recording"
        # The recording shows what was on screen there (not another PipeWire source): its last frame
        # matches a screenshot of the same region, and is far from the region next to it.
        frame = last_frame(s, mp4, "mp4-last-frame.png")
        here = screen_area(s, rect, "mp4-region-now.png")
        beside = screen_area(s, (rect[0] + 140, rect[1] + 110, 400, 300), "mp4-region-shifted.png")
        assert frame.shape == here.shape, (frame.shape, here.shape)
        diff, control = float(np.abs(frame - here).mean()), float(np.abs(frame - beside).mean())
        assert diff < 6.0 and control > 3 * diff + 4, f"frame vs region {diff:.2f}, vs shifted region {control:.2f}"
        LAST_REGION[:] = [(rect[0], rect[1], 320, 240)]
        gif, wall = await record_with_hotkey(s, call, "ctrl+shift+Print", LAST_REGION[0], 3.0)
        dur, w, h, codec = probe(gif)
        assert gif.suffix == ".gif" and codec == "gif"
        assert (w, h) == (320, 240)
        assert dur >= 2.5 and abs(dur - wall) <= 1.0, f"{dur:.2f} s of video for {wall:.2f} s of recording"
        (s.artifacts / "ffprobe.txt").write_text(f"{mp4.name}: {probe(mp4)}\n{gif.name}: {probe(gif)}\n"
                                                 f"mp4 last frame vs region: {diff:.2f}, vs shifted: {control:.2f}\n")


async def test_s10_pause_and_abort(app):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        # pause: 1 s recording, 2 s paused, 1 s recording. Each CLI call takes a moment, so time them:
        # the paused span is at least (resume call starts - pause call returned), at most
        # (resume call returned - pause call started).
        assert LAST_REGION, "runs after test_s10_mp4_and_gif_by_hotkey, which sets the region"
        rx, ry, _rw, rh = LAST_REGION[0]
        before = videos(s)
        t_call = time.monotonic()  # recording starts after this, before its controls show
        s.sharel("-StartScreenRecorder")  # the last region (320x240 from the GIF)
        s.wait_window("ShareL recording")
        t_start = time.monotonic()
        marks = {}
        for phase, secs in (("rec", 1.0), ("pause", 2.0), ("rec", 1.0)):
            if phase == "pause":
                marks["p0"] = time.monotonic()
                s.sharel("-PauseScreenRecording")
                marks["p1"] = time.monotonic()
            t0 = time.monotonic()
            while time.monotonic() - t0 < secs:
                # inside the region: the stream only carries frames for damage (plus 1 s keepalives)
                await call("mouse_move", x=rx + 20 + (time.monotonic() - t0) * 80, y=ry + rh // 2)
                time.sleep(0.15)
            if phase == "pause":
                marks["r0"] = time.monotonic()
                s.sharel("-PauseScreenRecording")
                marks["r1"] = time.monotonic()
        s.sharel("-StopScreenRecording")
        t_end = time.monotonic()
        total_min, total_max = t_end - t_start, t_end - t_call
        f = wait_for(lambda: [p for p in videos(s) if p not in before], timeout=60, what="paused recording")[0]
        dur, w, h, _c = probe(f)
        assert (w, h) == (320, 240)
        paused_min, paused_max = marks["r0"] - marks["p1"], marks["r1"] - marks["p0"]
        assert paused_min >= 1.9
        assert total_min - paused_max - 0.6 <= dur <= total_max - paused_min + 0.6, \
            f"{dur:.2f} s of video; wall {total_min:.2f}-{total_max:.2f} s, paused {paused_min:.2f}-{paused_max:.2f} s"
        (s.artifacts / "pause.txt").write_text(f"{f.name}: {dur:.2f} s; wall {total_min:.2f}-{total_max:.2f} s; "
                                               f"paused {paused_min:.2f}-{paused_max:.2f} s\n")
        # abort: nothing is written, the controls go away
        before = videos(s)
        s.sharel("-StartScreenRecorder")
        s.wait_window("ShareL recording")
        time.sleep(1.0)
        s.sharel("-AbortScreenRecording")
        s.wait_window("ShareL recording", gone=True)
        time.sleep(2.0)
        assert [p for p in videos(s) if p not in before] == []
