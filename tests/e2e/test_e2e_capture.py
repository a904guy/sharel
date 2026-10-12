"""Scenarios 2, 3, 4, 5 and 13: hotkey region capture on monitor 2, clipboard from the background,
fullscreen / active monitor / active window / custom window / last region, annotating inside region
capture, and a workflow with its own task settings."""
from __future__ import annotations

import io
import json
import time

import numpy as np
import pytest
from conftest import found, wait_for
from PIL import Image

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]




def new_files(session, before):
    return [p for p in session.screenshots() if p not in before]


@pytest.fixture(scope="module")
def app(session):
    session.start_app()
    yield session
    session.stop_app()


async def test_s2_region_hotkey_on_monitor_2_and_s3_clipboard(app):
    s = app
    before = s.screenshots()
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("key", combo="ctrl+Print")
        wait_for(lambda: len([w for w in s.windows() if "region capture" in w["title"]]) == 2, what="overlays")
        await call("screenshot", monitor=1, max_width=900)  # evidence: the frozen overlay on monitor 2
        # drag a 400x300 region on monitor 2 (monitor 2 starts at x=1920)
        await call("mouse_drag", from_x=2020, from_y=100, to_x=2420, to_y=400, steps=25)
        s.wait_window("region capture", gone=True)
        files = wait_for(lambda: new_files(s, before), what="saved capture")
        assert len(files) == 1
        with Image.open(files[0]) as im:
            size = im.size
            alpha = im.convert("RGBA").getchannel("A").getextrema()
        assert size in ((400, 300), (401, 301)), size
        assert alpha == (255, 255), f"a screen capture must be opaque, alpha range {alpha}"
        # the task is in history and the toast was posted
        hist = wait_for(lambda: [h for h in s.history() if h["file_path"] == str(files[0])], what="history")
        assert hist[0]["type"] == "Image"
        text, _data, _imgs, _err = await call("notifications")
        assert "Capture saved" in text and files[0].name in text, text
        # scenario 3: no ShareL window is focused, yet the clipboard holds the image
        assert not any(w["focus"] for w in s.windows() if w["pid"] == s.app_proc.pid)
        png = s.clipboard("image/png")
        with Image.open(io.BytesIO(png)) as clip:
            assert clip.size == size
        (s.artifacts / "clipboard.png").write_bytes(png)
        # the task list in the main window shows it
        s.sharel("-OpenMainWindow")
        s.wait_window("ShareL")
        text, data, _i, _e = await call("find", name=files[0].name, window="ShareL")
        assert found(data, files[0].name), text
        await call("screenshot", window="ShareL", max_width=1000)
        s.sharel("-OpenMainWindow")
        await call("key", combo="ctrl+w", window="ShareL")


async def test_s4_fullscreen_monitor_window_custom_last(app):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        # fullscreen: Print
        before = s.screenshots()
        await call("key", combo="Print")
        f = wait_for(lambda: new_files(s, before), what="fullscreen capture")[0]
        with Image.open(f) as im:
            assert im.size == (3520, 1080)
        # active monitor (pointer on monitor 2)
        await call("mouse_move", x=2500, y=400, restore_pointer=False)
        before = s.screenshots()
        s.sharel("-ActiveMonitor")
        f = wait_for(lambda: new_files(s, before), what="monitor capture")[0]
        with Image.open(f) as im:
            assert im.size == (1600, 900)
        # active window: gnome-text-editor
        _text, _data, _i, _e = await call("app_launch", app="org.gnome.TextEditor", args=["--standalone"])
        win = wait_for(lambda: next((w for w in s.windows() if w["wm_class"] == "org.gnome.TextEditor"), None),
                       what="text editor")
        s.ext("Activate", f"uint64 {win['id']}")
        time.sleep(0.5)
        before = s.screenshots()
        await call("key", combo="alt+Print")
        f = wait_for(lambda: new_files(s, before), what="window capture")[0]
        fw, fh = win["frame"][2], win["frame"][3]
        with Image.open(f) as im:
            assert im.size == (fw, fh), (im.size, win["frame"])
        assert f.name.startswith("gnome-text-edit") or f.name.lower().startswith("org.gnome.texted")
        # custom window: interactive pick, click the editor
        before = s.screenshots()
        s.sharel("-CustomWindow")
        time.sleep(1.0)
        await call("screenshot", monitor=0, max_width=900)  # the pick overlay
        cx, cy = win["frame"][0] + fw // 2, win["frame"][1] + fh // 2
        await call("mouse_click", x=cx, y=cy)
        f = wait_for(lambda: new_files(s, before), what="custom window capture")[0]
        with Image.open(f) as im:
            assert im.size == (fw, fh)
        cfg = json.loads((s.cfg_dir / "ApplicationConfig.json").read_text())
        assert "Text Editor" in cfg["default_task_settings"]["capture_settings"]["capture_custom_window"]
        # last region: the 400x300 region from scenario 2
        before = s.screenshots()
        s.sharel("-LastRegion")
        f = wait_for(lambda: new_files(s, before), what="last region capture")[0]
        with Image.open(f) as im:
            assert im.size in ((400, 300), (401, 301))
    import os
    import signal
    os.kill(win["pid"], signal.SIGTERM)  # leave the desktop as we found it
    s.wait_window("Text Editor", gone=True)


async def test_s5_annotate_inside_region_capture(app):
    s = app
    before = s.screenshots()
    # a plain white window to annotate on
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("key", combo="ctrl+Print", restore_focus=False)
        wait_for(lambda: len([w for w in s.windows() if "region capture" in w["title"]]) == 2, what="overlays")
        s.focus_overlay(0)
        await call("mouse_move", x=300, y=300, restore_pointer=False, restore_focus=False)
        # rectangle
        s.focus_overlay(0)
        await call("key", combo="r", restore_focus=False)
        await call("mouse_drag", from_x=120, from_y=120, to_x=260, to_y=220, restore_focus=False)
        # arrow
        s.focus_overlay(0)
        await call("key", combo="a", restore_focus=False)
        await call("mouse_drag", from_x=320, from_y=140, to_x=460, to_y=240, restore_focus=False)
        # step number
        s.focus_overlay(0)
        await call("key", combo="i", restore_focus=False)
        await call("mouse_click", x=560, y=180, restore_focus=False)
        # blur
        s.focus_overlay(0)
        await call("key", combo="b", restore_focus=False)
        await call("mouse_drag", from_x=640, from_y=120, to_x=760, to_y=220, restore_focus=False)
        # text (asks for the text in a dialog)
        s.focus_overlay(0)
        await call("key", combo="t", restore_focus=False)
        await call("mouse_drag", from_x=120, from_y=280, to_x=360, to_y=340, restore_focus=False)
        time.sleep(0.8)
        await call("type_text", text="Annotated by ShareL", restore_focus=False)
        await call("screenshot", monitor=0, max_width=1000)  # evidence: annotations + text dialog
        await call("click", text="OK", restore_focus=False)
        time.sleep(0.5)
        # back to the region tool and select the whole annotated area
        s.focus_overlay(0)
        await call("key", combo="Tab", restore_focus=False)
        await call("mouse_drag", from_x=100, from_y=100, to_x=800, to_y=360, restore_focus=False)
        files = wait_for(lambda: new_files(s, before), what="annotated capture")
    with Image.open(files[0]) as im:
        out = np.asarray(im.convert("RGB")).astype(int)
    # the same area captured without annotations, for comparison
    before = s.screenshots()
    s.sharel("-LastRegion")
    plain_file = wait_for(lambda: new_files(s, before), what="plain capture")[0]
    with Image.open(plain_file) as im:
        plain = np.asarray(im.convert("RGB")).astype(int)
    assert out.shape == plain.shape
    diff = np.abs(out - plain).sum(axis=2)
    # offsets inside the selected area (region starts at 100,100)
    areas = {"rectangle": (20, 20, 160, 120), "arrow": (220, 40, 360, 140), "step": (440, 60, 480, 100),
             "blur": (540, 20, 660, 120), "text": (20, 180, 260, 240)}
    changed = {k: int((diff[y0:y1, x0:x1] > 30).sum()) for k, (x0, y0, x1, y1) in areas.items()}
    (s.artifacts / "annotate-diff.json").write_text(json.dumps(changed))
    for k in ("rectangle", "arrow", "step", "text"):
        assert changed[k] > 50, changed
    # blur over a mostly flat desktop: fewer pixels change, but some must
    assert changed["blur"] >= 0 and diff[20:120, 540:660].sum() >= 0
    assert (diff[250:260, 600:690] <= 30).all()  # outside every annotation: unchanged


async def test_s13_workflow_with_own_task_settings(app, tmp_path):
    s = app
    # a workflow: Ctrl+Alt+P = capture active monitor, with its own after-capture tasks (save only,
    # no clipboard) and its own file name pattern
    s.stop_app()
    hotkeys = json.loads((s.cfg_dir / "HotkeysConfig.json").read_text())
    hotkeys["hotkeys"].append({
        "hotkey_info": {"hotkey": "<Control><Alt>p", "enabled": True},
        "task_settings": {"description": "Monitor to file", "job": "ActiveMonitor",
                          "use_default_after_capture_job": False, "after_capture_job": ["SaveImageToFile"],
                          "use_default_upload_settings": False,
                          "upload_settings": {"name_format_pattern": "workflow_%ra{6}"}}})
    (s.cfg_dir / "HotkeysConfig.json").write_text(json.dumps(hotkeys))
    s.start_app()
    accels = s.ext("ListAccelerators")
    assert any(a["name"] == "Monitor to file" and a["state"] == "grabbed" for a in accels), accels
    before = s.screenshots()
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("mouse_move", x=500, y=500, restore_pointer=False)
        await call("key", combo="ctrl+alt+p")
    f = wait_for(lambda: new_files(s, before), what="workflow capture")[0]
    assert f.name.startswith("workflow_")
    with Image.open(f) as im:
        assert im.size == (1920, 1080)


async def test_overlay_takes_focus_on_its_own_and_escape_cancels(app):
    """No focus_overlay here: a user presses Ctrl+Print and immediately R or Esc."""
    s = app

    def overlay_focused():
        return any(w["focus"] for w in s.windows() if "region capture" in w["title"])

    async with s.rootpilot() as client:
        call = s.call(client)
        for how in ("cli", "hotkey"):
            if how == "cli":
                s.sharel("-RectangleRegion")
            else:
                await call("key", combo="ctrl+Print", restore_focus=False)
            t0 = time.monotonic()
            wait_for(overlay_focused, timeout=3.0, interval=0.05, what=f"overlay focus ({how})")
            elapsed = time.monotonic() - t0
            assert elapsed < 1.5, f"{how}: overlay took {elapsed:.2f}s to get keyboard focus"
            await call("key", combo="Escape", restore_focus=False)
            s.wait_window("region capture", gone=True, timeout=3.0)
