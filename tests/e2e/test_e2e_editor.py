"""Scenarios 6, 7 and 8: the image editor (two effects, a crop, undo/redo), pin to screen from a
region (stays on top, close all), and the screen colour picker and ruler."""
from __future__ import annotations

import os
import re
import signal
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


def bounds(text: str, name: str) -> tuple[int, int, int, int]:
    m = re.search(rf'"{re.escape(name)}"[^\n]*?@(-?\d+),(-?\d+) (\d+)x(\d+)', text)
    assert m, (name, text[:600])
    return tuple(int(v) for v in m.groups())


def colorful(w=400, h=300) -> Image.Image:
    a = np.zeros((h, w, 3), np.uint8)
    a[..., 0] = np.linspace(20, 235, w, dtype=np.uint8)[None, :]
    a[..., 1] = 40
    a[..., 2] = np.linspace(200, 30, h, dtype=np.uint8)[:, None]
    return Image.fromarray(a, "RGB")


def load(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).astype(int)


async def test_s6_editor_effects_crop_undo_redo(app):
    s = app
    path = s.state / "edit-me.png"
    colorful().save(path)
    s.sharel("-ImageEditor", str(path))
    win = s.wait_window("image editor")
    title = win["title"]
    async with s.rootpilot() as client:
        call = s.call(client)
        # two effects through Image > Add image effects…
        _text, data, _i, _e = await call("find", name="Image", role="toggle button", window=title, limit=3)
        ref = next(it["ref"] for it in data["data"] if it.get("name") == "Image")
        await call("click", ref=ref)
        time.sleep(0.5)
        await call("click", text="Add image effects…", window=title)
        fx = s.wait_window("Image effects")
        # a new, empty preset (the default one is ShareX's: a 30 px canvas and a watermark)
        _t, data, _i, _e = await call("find", name="New", role="button", window=fx["title"], limit=3)
        await call("click", ref=next(it["ref"] for it in data["data"] if it.get("name") == "New"))
        time.sleep(0.4)
        for category, effect in (("Adjustments", "Grayscale"), ("Adjustments", "Inverse")):
            await call("click", text="Add effect", window=fx["title"])
            time.sleep(0.4)
            await call("click", text=category, window=fx["title"])
            time.sleep(0.4)
            await call("click", text=effect, window=fx["title"])
            time.sleep(0.6)
        await call("screenshot", window=fx["title"], max_width=1200)  # evidence: effect list + preview
        await call("click", text="OK", window=fx["title"])
        s.wait_window("Image effects", gone=True)
        # crop with the crop tool: drag inside the image, Enter confirms
        _text, data, _i, _e = await call("find", name="Image canvas", window=title, limit=2)
        canvas = next(it for it in data["data"] if it.get("name") == "Image canvas")
        cx, cy, cw, ch = canvas["bounds"]
        ix, iy = cx + (cw - 400) // 2, cy + (ch - 300) // 2  # image centred at 100 %
        _text, data, _i, _e = await call("find", name="Crop image (C)", window=title, limit=3)
        await call("click", ref=next(it["ref"] for it in data["data"] if it.get("name") == "Crop image (C)"))
        await call("mouse_drag", from_x=ix + 50, from_y=iy + 40, to_x=ix + 250, to_y=iy + 190)
        await call("key", combo="Return", window=title)
        await call("screenshot", window=title, max_width=1000)
        await call("key", combo="ctrl+s", window=title)
        time.sleep(0.8)
        out = load(path)
        h, w = out.shape[:2]
        assert (w, h) in ((200, 150), (201, 151)), (w, h)
        assert (out[..., 0] == out[..., 1]).all() and (out[..., 1] == out[..., 2]).all()  # grayscale
        src = np.asarray(colorful().convert("L")).astype(int)
        # inverted: the left (dark red) side of the source became light
        assert out[5, 5, 0] > 255 - src[45, 55] - 25 and out[5, 5, 0] > out[5, w - 6, 0]
        # undo the crop, save: full size again (effects stay)
        await call("key", combo="ctrl+z", window=title)
        await call("key", combo="ctrl+s", window=title)
        time.sleep(0.8)
        undone = load(path)
        assert undone.shape[:2] == (300, 400)
        assert (undone[..., 0] == undone[..., 2]).all()
        # redo, save: cropped again
        await call("key", combo="ctrl+y", window=title)
        await call("key", combo="ctrl+s", window=title)
        time.sleep(0.8)
        assert load(path).shape[:2] in ((150, 200), (151, 201))
        await call("window_close", window=title)


async def test_s7_pin_from_region_stays_on_top_then_close_all(app):
    s = app
    s.sharel("-PinToScreenFromScreen")
    wait_for(lambda: [w for w in s.windows() if "region capture" in w["title"]], what="overlay")
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("mouse_drag", from_x=300, from_y=300, to_x=600, to_y=500)
        pin = s.wait_window("ShareL pin")
        assert pin["above"], pin
        await call("screenshot", monitor=0, max_width=1000)
        # another app takes focus: the pin stays above it
        _t, _d, _i, _e = await call("app_launch", app="org.gnome.TextEditor", args=["--standalone"])
        editor = wait_for(lambda: next((w for w in s.windows() if w["wm_class"] == "org.gnome.TextEditor"), None),
                          what="text editor")
        s.ext("Activate", f"uint64 {editor['id']}")
        time.sleep(0.8)
        wins = {w["id"]: w for w in s.windows()}
        pin_now = wins[pin["id"]]
        assert wins[editor["id"]]["focus"] and pin_now["above"]
        assert pin_now["stacking"] > wins[editor["id"]]["stacking"]
        fw, fh = pin_now["frame"][2], pin_now["frame"][3]
        assert fw >= 300 and fh >= 200  # the 300x200 image (plus its shadow margin)
        await call("screenshot", monitor=0, max_width=1000)
        os.kill(editor["pid"], signal.SIGTERM)
    s.sharel("-PinToScreenCloseAll")
    s.wait_window("ShareL pin", gone=True)


async def test_s8_screen_color_picker_and_ruler(app):
    s = app
    probe = s.state / "probe.png"
    s.ext("Screenshot", "area", "uint64 0", "640", "400", "1", "1", "false", str(probe))
    with Image.open(probe) as im:
        r, g, b = im.convert("RGB").getpixel((0, 0))
    s.sharel("-ScreenColorPicker")
    wait_for(lambda: [w for w in s.windows() if "region capture" in w["title"]], what="picker overlay")
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("mouse_move", x=640, y=400, restore_pointer=False)
        await call("screenshot", monitor=0, max_width=1000)  # evidence: magnifier + colour info
        await call("mouse_click", x=640, y=400)
        s.wait_window("region capture", gone=True)
        text = s.clipboard("text/plain;charset=utf-8").decode() or s.clipboard("text/plain").decode()
        assert text.strip().upper() == f"{r:02X}{g:02X}{b:02X}", (text, (r, g, b))
        # ruler: measure 300 x 200 and read the readout
        s.sharel("-Ruler")
        overlays = wait_for(lambda: [w for w in s.windows() if "region capture" in w["title"]], what="ruler")
        s.ext("Activate", f"uint64 {next(w for w in overlays if w['monitor'] == 0)['id']}")
        time.sleep(0.6)  # let the freshly mapped overlay take input
        await call("mouse_drag", from_x=200, from_y=200, to_x=500, to_y=400, restore_focus=False,
                   restore_pointer=False)
        time.sleep(0.5)
        assert [w for w in s.windows() if "region capture" in w["title"]], "the ruler must stay open after a drag"
        readout = ""
        for _attempt in range(4):  # the readout box sits just above the measured area
            _t, data, _i, _e = await call("ocr", region=[180, 120, 560, 90])
            readout = " ".join(ln["text"] for ln in data["data"]["lines"])
            if "Width" in readout:
                break
            time.sleep(0.5)
        await call("screenshot", monitor=0, max_width=1000)
        # ShareX's ruler readout: "Width: 301 px | Height: 201 px | ... Distance: 360.56 px" (edges inclusive)
        assert re.search(r"Width:\s*30[01]\s*px.*Height:\s*20[01]\s*px", readout), readout
        assert re.search(r"Distance:\s*36[01]\.\d+", readout), readout
        await call("key", combo="Escape", restore_focus=False)
        s.wait_window("region capture", gone=True)
