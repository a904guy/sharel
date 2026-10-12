"""Scenario 15 (screen, window and pin tools): each entry of the main window's Tools menu, opened by
real clicks, performs its primary function. Nothing is uploaded: the seeded after-capture tasks only
save and copy."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time

import numpy as np
import pytest
import qrcode
from conftest import found, wait_for
from PIL import Image

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

QR_TEXT = "ShareL S15 pinned QR 4711"
CLIP_TEXT = "Pinned from the clipboard 0815"
SECOND = 1920  # monitor 1 starts here (1600x900); the main window and its menu live on monitor 0


@pytest.fixture(scope="module")
def app(session):
    session.seed_config(app={"default_task_settings": {
        "after_capture_job": ["SaveImageToFile"], "after_upload_job": []}})
    session.start_app()
    yield session
    session.stop_app()


# -- helpers --------------------------------------------------------------------------------------

def main_window(s):
    return next((w for w in s.windows() if w["title"] == "ShareL"), None)


def pins(s):
    return [w for w in s.windows() if w["title"].startswith("ShareL pin")]


def overlays(s):
    return [w for w in s.windows() if "region capture" in w["title"]]


def area(s, rect, name):
    x, y, w, h = rect
    path = s.artifacts / name
    s.ext("Screenshot", "area", "uint64 0", str(x), str(y), str(w), str(h), "false", str(path))
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def details(s, wid):
    return s.ext("GetWindowDetails", f"uint64 {wid}")


_SET_CLIP = ("import sys, gi\n"
             "from gi.repository import Gio, GLib\n"
             "c = Gio.bus_get_sync(Gio.BusType.SESSION, None)\n"
             "c.call_sync('app.sharel.Shell', '/app/sharel/Shell', 'app.sharel.Shell', 'SetClipboard',"
             " GLib.Variant('(say)', ('text/plain;charset=utf-8', sys.argv[1].encode())), None, 0, 10000, None)\n")


def set_clipboard_text(s, text):
    """Put text on the compositor's clipboard, as another app would (through the shell)."""
    subprocess.run(["/usr/bin/python3", "-c", _SET_CLIP, text], env=s.env, check=True, timeout=20)
    wait_for(lambda: text in (s.clipboard("text/plain;charset=utf-8") or b"").decode(errors="replace"),
             what="clipboard text set")


async def open_tool(s, call, label):
    """Main window -> Tools -> ``label``, by real clicks (scrolling the long menu like a user)."""
    win = main_window(s)
    if win is None:
        s.sharel("-OpenMainWindow")
        win = wait_for(lambda: main_window(s), what="main window")
        time.sleep(1.0)  # let the map animation finish before clicking into it
    s.ext("Activate", f"uint64 {win['id']}")
    time.sleep(0.4)
    await call("click", text="Tools", window="ShareL")
    time.sleep(0.7)
    for _ in range(12):
        text, data, _i, _e = await call("find", name=label, window="ShareL", limit=10)
        hits = [it for it in data["data"] if it.get("name") == label]
        assert hits, f"{label!r} not in the Tools menu: {text[:500]}"
        bx, by, bw, bh = hits[0].get("bounds") or (0, -1, 0, 0)
        if by >= 0 and by + bh <= 1080 - 4:
            await call("click", ref=hits[0]["ref"])
            return
        await call("scroll", dy=3 if by + bh > 1080 - 4 else -3, x=bx + bw / 2, y=540)
        time.sleep(0.3)
    raise AssertionError(f"could not scroll {label!r} into view")


async def click_named(call, name, window, role=None):
    kw = {"role": role} if role else {}
    text, data, _i, _e = await call("find", name=name, window=window, limit=10, **kw)
    hits = [it for it in data["data"] if it.get("name") == name]
    assert hits, f"{name!r} not found in {window!r}: {text[:500]}"
    await call("click", ref=hits[0]["ref"])


def ocr_text(rgb) -> str:
    """Tesseract on a screen grab (upscaled: small UI text reads far better at 2x)."""
    im = Image.fromarray(rgb.astype(np.uint8))
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    out = subprocess.run(["tesseract", "-", "-", "--psm", "6"], input=_png_bytes(im), capture_output=True,
                         timeout=60)
    return out.stdout.decode(errors="replace")


def _png_bytes(im) -> bytes:
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def qr_png(path, text):
    qrcode.make(text, box_size=8, border=4).convert("RGB").save(path)
    with Image.open(path) as im:
        return im.size


# -- screen colour picker and ruler ---------------------------------------------------------------

async def test_screen_color_picker_and_ruler_from_the_menu(app):
    s = app
    px, py = SECOND + 800, 700  # wallpaper on monitor 1, away from every window
    r, g, b = area(s, (px, py, 1, 1), "picker-probe.png")[0, 0]
    want = f"{r:02X}{g:02X}{b:02X}"
    set_clipboard_text(s, "before the pick")
    async with s.rootpilot() as client:
        call = s.call(client)
        await open_tool(s, call, "Screen color picker")
        wait_for(lambda: overlays(s), what="picker overlay")
        time.sleep(0.6)
        await call("mouse_move", x=px, y=py, restore_pointer=False)
        await call("screenshot", monitor=1, max_width=1000)  # evidence: magnifier and colour readout
        await call("mouse_click", x=px, y=py)
        s.wait_window("region capture", gone=True)
        text = wait_for(lambda: s.clipboard("text/plain;charset=utf-8").decode().strip()
                        if want in s.clipboard("text/plain;charset=utf-8").decode().upper() else "",
                        what="picked colour on the clipboard")
        assert text.upper() == want, (text, want)

        # ruler: measure 300 x 200 on monitor 1 and read the readout
        await open_tool(s, call, "Ruler")
        wait_for(lambda: overlays(s), what="ruler overlay")
        time.sleep(0.6)
        x0, y0 = SECOND + 300, 300
        await call("mouse_drag", from_x=x0, from_y=y0, to_x=x0 + 300, to_y=y0 + 200, restore_focus=False,
                   restore_pointer=False)
        time.sleep(0.5)
        assert overlays(s), "the ruler must stay open after a drag"
        readout = ""
        for _attempt in range(4):  # the readout box sits just above the measured area
            _t, data, _i, _e = await call("ocr", region=[x0 - 20, y0 - 80, 380, 90])
            readout = " ".join(ln["text"] for ln in data["data"]["lines"])
            if "Width" in readout:
                break
            time.sleep(0.5)
        await call("screenshot", monitor=1, max_width=1000)
        assert re.search(r"Width:\s*30[01]\s*px.*Height:\s*20[01]\s*px", readout), readout
        await call("key", combo="Escape", restore_focus=False)
        s.wait_window("region capture", gone=True)


# -- mouse highlighter ----------------------------------------------------------------------------

async def test_mouse_highlighter_from_the_menu(app):
    s = app
    px, py = SECOND + 800, 450
    box = (px - 60, py - 60, 120, 120)
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("mouse_move", x=px, y=py, restore_pointer=False)
        time.sleep(0.3)
        off = area(s, box, "highlighter-off.png")
        await open_tool(s, call, "Mouse highlighter")
        await call("mouse_move", x=px, y=py, restore_pointer=False)
        time.sleep(0.5)
        on = area(s, box, "highlighter-on.png")
        await call("screenshot", monitor=1, max_width=1000)  # evidence: the ring around the pointer
        changed = (np.abs(on - off).sum(axis=2) > 60).mean()
        assert changed > 0.03, f"no highlight drawn around the pointer ({changed:.3f} of the area changed)"
        # the second click turns it off again: the area is back to what it was
        await open_tool(s, call, "Mouse highlighter")
        await call("mouse_move", x=px, y=py, restore_pointer=False)
        time.sleep(0.5)
        again = area(s, box, "highlighter-off-again.png")
        assert (np.abs(again - off).sum(axis=2) > 60).mean() < 0.005, "the highlight stayed after turning it off"


# -- pin to screen, QR from screen and region -----------------------------------------------------

async def test_pins_and_qr_tools_from_the_menu(app, tmp_path):
    s = app
    qr_file = s.state / "s15-qr.png"
    qw, qh = qr_png(qr_file, QR_TEXT)
    async with s.rootpilot() as client:
        call = s.call(client)
        assert pins(s) == []

        # Pin to screen (From file): the file chooser, then the image pinned at its own size
        await open_tool(s, call, "Pin to screen (From file)")
        chooser = wait_for(lambda: next((w for w in s.windows() if "Pin image to screen" in w["title"]), None),
                           timeout=20, what="file chooser")
        time.sleep(1.0)
        s.ext("Activate", f"uint64 {chooser['id']}")
        await call("screenshot", window=chooser["title"], max_width=1000)  # evidence: the file chooser
        await call("key", combo="ctrl+l", window=chooser["title"])
        await call("type_text", text=str(qr_file), window=chooser["title"])
        time.sleep(0.5)
        await call("screenshot", window=chooser["title"], max_width=1000)  # evidence: the typed path
        await call("key", combo="Return", window=chooser["title"])  # goes to the file and selects it
        time.sleep(1.0)
        await click_named(call, "Open", chooser["title"], role="button")
        p1 = wait_for(lambda: pins(s), what="pin from file")[0]
        fw, fh = p1["frame"][2:4]
        assert abs(fw - qw) <= 24 and abs(fh - qh) <= 24, (p1["frame"], (qw, qh))  # plus its shadow margin
        s.ext("PositionWindow", f"uint64 {p1['id']}", str(SECOND + 100), "120")
        time.sleep(0.6)
        await call("screenshot", monitor=1, max_width=1000)  # evidence: the pinned QR image

        # QR code (Scan screen): finds the pinned code
        set_clipboard_text(s, "before the scan")
        await open_tool(s, call, "QR code (Scan screen)")
        res = s.wait_window("QR code scan", timeout=30)
        time.sleep(0.8)
        await call("screenshot", window=res["title"], max_width=1000)
        await click_named(call, "Copy all", res["title"], role="button")
        text = wait_for(lambda: s.clipboard("text/plain;charset=utf-8").decode()
                        if QR_TEXT in s.clipboard("text/plain;charset=utf-8").decode() else "",
                        what="decoded text on the clipboard")
        assert text.strip() == QR_TEXT, text
        await call("window_close", window=res["title"])

        # Pin to screen (From screen): a region around the first pin becomes a second pin
        p1 = next(w for w in s.windows() if w["id"] == p1["id"])
        x, y, w, h = p1["frame"]
        await open_tool(s, call, "Pin to screen (From screen)")
        wait_for(lambda: overlays(s), what="region overlay")
        time.sleep(0.6)
        await call("mouse_drag", from_x=x - 6, from_y=y - 6, to_x=x + w + 6, to_y=y + h + 6)
        p2 = wait_for(lambda: [p for p in pins(s) if p["id"] != p1["id"]], what="pin from screen")[0]
        assert p2["above"], p2
        s.ext("PositionWindow", f"uint64 {p2['id']}", str(SECOND + 700), "120")
        time.sleep(0.6)
        p2 = next(w for w in s.windows() if w["id"] == p2["id"])

        # QR code (Scan region): the second pin holds the captured code, so it decodes there too
        set_clipboard_text(s, "before the region scan")
        await open_tool(s, call, "QR code (Scan region)")
        wait_for(lambda: overlays(s), what="region overlay")
        time.sleep(0.6)
        x, y, w, h = p2["frame"]
        await call("mouse_drag", from_x=x - 4, from_y=y - 4, to_x=x + w + 4, to_y=y + h + 4)
        res = s.wait_window("QR code scan", timeout=30)
        time.sleep(0.8)
        await call("screenshot", window=res["title"], max_width=1000)
        await click_named(call, "Copy all", res["title"], role="button")
        text = wait_for(lambda: s.clipboard("text/plain;charset=utf-8").decode()
                        if QR_TEXT in s.clipboard("text/plain;charset=utf-8").decode() else "",
                        what="region-decoded text on the clipboard")
        assert text.strip() == QR_TEXT, text
        await call("window_close", window=res["title"])

        # Pin to screen (From clipboard): clipboard text becomes a pin showing it
        set_clipboard_text(s, CLIP_TEXT)
        before = {p["id"] for p in pins(s)}
        await open_tool(s, call, "Pin to screen (From clipboard)")
        p3 = wait_for(lambda: [p for p in pins(s) if p["id"] not in before], what="pin from clipboard")[0]
        s.ext("PositionWindow", f"uint64 {p3['id']}", str(SECOND + 100), "650")
        time.sleep(0.6)
        p3 = next(w for w in s.windows() if w["id"] == p3["id"])
        x, y, w, h = p3["frame"]
        shot = area(s, (x, y, w, h), "pin-text.png")  # evidence: the pinned clipboard text
        seen = ocr_text(shot)
        assert "clipboard" in seen and "0815" in seen, seen

        # Pin to screen: ShareX's source choice; "Clipboard" pins it again
        before = {p["id"] for p in pins(s)}
        await open_tool(s, call, "Pin to screen")
        time.sleep(0.8)
        await call("screenshot", window="ShareL", max_width=1000)  # evidence: the source choice
        await click_named(call, "Clipboard", "ShareL", role="button")
        wait_for(lambda: [p for p in pins(s) if p["id"] not in before], what="pin from the choice dialog")
        await call("screenshot", monitor=1, max_width=1000)  # evidence: all the pins

        # Pin to screen (Close all)
        assert len(pins(s)) == 4
        await open_tool(s, call, "Pin to screen (Close all)")
        wait_for(lambda: not pins(s), what="all pins closed")


# -- window tools ---------------------------------------------------------------------------------

def _monitor_rect(s, mon_index):
    m = next(m for m in s.ext("GetMonitors") if m["index"] == mon_index)
    return [m["x"], m["y"], m["width"], m["height"]]


async def _text_editor(s, call):
    _t, _d, _i, _e = await call("app_launch", app="org.gnome.TextEditor", args=["--standalone"])
    ed = wait_for(lambda: next((w for w in s.windows() if w["wm_class"] == "org.gnome.TextEditor"), None),
                  what="text editor")
    time.sleep(1.0)
    return ed


async def test_window_tools_from_the_menu(app):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        ed = await _text_editor(s, call)
        s.ext("MoveResize", f"uint64 {ed['id']}", str(SECOND + 200), "150", "700", "500")
        time.sleep(0.8)
        ed = next(w for w in s.windows() if w["id"] == ed["id"])
        start = list(ed["frame"])
        mon = _monitor_rect(s, ed["monitor"])
        assert start != mon and not details(s, ed["id"]).get("borderless")

        # Borderless window: type the target's title, make it borderless (it fills its monitor), restore
        await open_tool(s, call, "Borderless window")
        bw = s.wait_window("Borderless window")
        time.sleep(0.8)
        await click_named(call, "Window title", bw["title"])
        await call("type_text", text=ed["title"], window=bw["title"])
        await click_named(call, "Make window borderless", bw["title"], role="button")
        wait_for(lambda: details(s, ed["id"]).get("borderless"), what="borderless target")
        filled = wait_for(lambda: next(w for w in s.windows() if w["id"] == ed["id"])["frame"]
                          if next(w for w in s.windows() if w["id"] == ed["id"])["frame"] == mon else None,
                          what="target filling its monitor")
        assert filled == mon
        await call("screenshot", monitor=1, max_width=1000)  # evidence: the borderless editor
        await click_named(call, "Restore", bw["title"], role="button")
        wait_for(lambda: not details(s, ed["id"]).get("borderless"), what="restored target")
        back = wait_for(lambda: next(w for w in s.windows() if w["id"] == ed["id"])["frame"]
                        if next(w for w in s.windows() if w["id"] == ed["id"])["frame"] != mon else None,
                        what="target back to its own size")
        assert abs(back[2] - start[2]) <= 8 and abs(back[3] - start[3]) <= 8, (back, start)
        await call("window_close", window=bw["title"])

        # Inspect window: pick the editor by clicking it; the details show its real pid and title
        await open_tool(s, call, "Inspect window")
        iw = s.wait_window("Inspect window")
        # keep the tool off the target, so the pick click lands on the editor (monitor 1)
        s.ext("PositionWindow", f"uint64 {iw['id']}", "60", "80")
        time.sleep(0.8)
        _t, data, _i, _e = await call("find", name=str(ed["pid"]), window=iw["title"], limit=3)
        assert not found(data, str(ed["pid"])), "pid shown before a window was picked"
        await click_named(call, "Inspect window…", iw["title"], role="button")
        time.sleep(0.8)
        x, y, w, h = next(w for w in s.windows() if w["id"] == ed["id"])["frame"]
        await call("screenshot", monitor=1, max_width=1000)  # evidence: picking the editor
        await call("mouse_click", x=x + w // 2, y=y + h // 2, restore_focus=False)
        text = ""
        for _ in range(20):
            text, data, _i, _e = await call("find", name=str(ed["pid"]), window=iw["title"], limit=3)
            if found(data, str(ed["pid"])):
                break
            time.sleep(0.5)
        assert found(data, str(ed["pid"])), text[:500]
        await call("screenshot", window=iw["title"], max_width=1000)
        await call("window_close", window=iw["title"])
        os.kill(ed["pid"], signal.SIGTERM)

        # Make active window top most / borderless: from the menu, the active window is ShareL's own
        main = main_window(s)
        assert not main["above"]
        await open_tool(s, call, "Make active window top most")
        wait_for(lambda: main_window(s)["above"], what="main window kept on top")
        await open_tool(s, call, "Make active window top most")
        wait_for(lambda: not main_window(s)["above"], what="main window no longer on top")

        mon0 = _monitor_rect(s, main["monitor"])
        assert main_window(s)["frame"] != mon0 and not details(s, main["id"]).get("borderless")
        await open_tool(s, call, "Make active window borderless")
        wait_for(lambda: details(s, main["id"]).get("borderless"), what="main window borderless")
        wait_for(lambda: main_window(s)["frame"] == mon0, what="main window filling its monitor")
        await call("screenshot", monitor=0, max_width=1000)
        await open_tool(s, call, "Make active window borderless")
        wait_for(lambda: not details(s, main["id"]).get("borderless"), what="main window restored")
        wait_for(lambda: main_window(s)["frame"] != mon0, what="main window back to its size")
