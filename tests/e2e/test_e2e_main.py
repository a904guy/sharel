"""Scenarios 1 and 14: every left-menu entry of the main window and every settings window; the tray
icon (left click runs the configured action) and its menu items."""
from __future__ import annotations

import json
import time

import pytest
from conftest import found

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


def wait_for(fn, timeout=20.0, interval=0.25, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def app(session):
    session.start_app()
    yield session
    session.stop_app()


MENUS = {  # left-menu button -> an item that must be in its menu
    "Capture": "Capture region", "Upload": "Upload file", "Workflows": "Capture entire screen",
    "Tools": "Color picker", "After capture tasks": "Copy image to clipboard",
    "After upload tasks": "Copy URL to clipboard", "Destinations": "Image uploaders", "Debug": "Show debug log",
}
WINDOWS = {  # left-menu button -> window title it opens
    "Task settings…": "Task settings", "Application settings…": "Application settings",
    "Hotkey settings…": "Hotkey settings", "History…": "History", "Image history…": "Image history",
}


async def test_s1_every_left_menu_entry_and_settings_window(app):
    s = app
    s.sharel("-OpenMainWindow")
    s.wait_window("ShareL")
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("screenshot", window="ShareL", max_width=1000)
        for button, item in MENUS.items():
            _t, data, _i, _e = await call("find", name=item, window="ShareL", limit=3)
            assert not found(data, item), f"{item!r} visible before opening {button}"
            await call("click", text=button, window="ShareL")
            time.sleep(0.6)
            text, data, _i, _e = await call("find", name=item, window="ShareL", limit=3)
            assert found(data, item), (button, text[:500])
            await call("screenshot", window="ShareL", max_width=1000)
            await call("key", combo="Escape", window="ShareL")
            time.sleep(0.3)
        for button, title in WINDOWS.items():
            await call("click", text=button, window="ShareL")
            win = s.wait_window(title)
            await call("screenshot", window=title, max_width=1000)
            s.ext("Activate", f"uint64 {win['id']}")
            await call("window_close", window=title)
            s.wait_window(title, gone=True)
        # submenu entries that open windows: Destinations, Custom uploaders, Debug log
        for button, item, title in (("Destinations", "Destination settings…", "Destination settings"),
                                    ("Destinations", "Custom uploader settings…", "Custom uploader settings"),
                                    ("Debug", "Show debug log", "Debug log")):
            await call("click", text=button, window="ShareL")
            time.sleep(0.6)
            await call("click", text=item, window="ShareL")
            s.wait_window(title)
            await call("screenshot", window=title, max_width=1000)
            await call("window_close", window=title)
            s.wait_window(title, gone=True)
        # About opens a dialog over the main window: only it shows the version and the license
        from sharel import __version__
        _t, data, _i, _e = await call("find", name=__version__, window="ShareL", limit=3)
        assert not found(data, __version__), "version visible before About was opened?"
        await call("click", text="About…", window="ShareL")
        time.sleep(0.8)
        text, data, _i, _e = await call("find", name=__version__, window="ShareL", limit=3)
        await call("screenshot", window="ShareL", max_width=1000)
        assert found(data, __version__), text[:400]
        await call("key", combo="Escape", window="ShareL")
        time.sleep(0.5)
        _t, data, _i, _e = await call("find", name=__version__, window="ShareL", limit=3)
        assert not found(data, __version__), "About dialog did not close"
        # screenshots folder opens a file manager window on that folder
        folder = s.pictures / "ShareL"
        before = {w["id"] for w in s.windows()}
        await call("click", text="Screenshots folder…", window="ShareL")
        import datetime as dt
        month = f"{dt.date.today():%Y-%m}"
        fm = wait_for(lambda: [w for w in s.windows() if w["id"] not in before and w["pid"] != s.app_proc.pid
                               and (month in w["title"] or folder.name in w["title"])],
                      timeout=30, what=f"file manager window showing {month}")
        await call("screenshot", window=fm[0]["title"], max_width=1000)
        import os
        import signal
        os.kill(fm[0]["pid"], signal.SIGTERM)
        # the checkable after-capture flags change the config
        await call("click", text="After capture tasks", window="ShareL")
        time.sleep(0.5)
        await call("click", text="Pin to screen", window="ShareL")
        time.sleep(0.5)
        cfg = json.loads((s.cfg_dir / "ApplicationConfig.json").read_text())
        assert "PinToScreen" in cfg["default_task_settings"]["after_capture_job"]
        await call("click", text="After capture tasks", window="ShareL")
        time.sleep(0.5)
        await call("click", text="Pin to screen", window="ShareL")
        time.sleep(0.5)
        cfg = json.loads((s.cfg_dir / "ApplicationConfig.json").read_text())
        assert "PinToScreen" not in cfg["default_task_settings"]["after_capture_job"]


async def ocr_click(call, text: str, region, *, hold: bool = False) -> None:
    """Click the menu line that starts with ``text`` (shell menus aren't in the AT-SPI tree, so: OCR).

    ``hold``: press, wait 150 ms, release. Injected clicks are instantaneous, and GNOME Shell closes the
    whole indicator menu instead of expanding a submenu header on a zero-length click; a real mouse
    click always lasts long enough. A zero-distance mouse_drag with hold_ms is still real input."""
    _out, data, _i, _e = await call("ocr", region=list(region))
    lines = data["data"]["lines"]
    hit = next((ln for ln in lines if ln["text"].strip().rstrip(">").strip().lower().startswith(text.lower())), None)
    assert hit is not None, (text, [ln["text"] for ln in lines])
    x, y = hit["center"]
    if hold:
        await call("mouse_drag", from_x=x, from_y=y, to_x=x, to_y=y, steps=1, hold_ms=150, restore_focus=False)
    else:
        await call("mouse_click", x=x, y=y, restore_focus=False)


async def test_s14_tray_icon_and_menu(app):
    s = app
    for w in s.windows():
        if w["pid"] == s.app_proc.pid:
            s.ext("Activate", f"uint64 {w['id']}")
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("mouse_move", x=900, y=700)  # starts input: the screen-sharing indicator appears first
        time.sleep(1.0)
        rect = s.ext("GetTrayIconRect", "app.sharel.ShareL")
        assert rect["found"], rect
        x, y, w, h = rect["rect"]
        await call("screenshot", monitor=0, region=[x - 300, 0, 600, 40])
        # left click: the configured TrayClickAction (Capture region)
        await call("mouse_click", x=x + w / 2, y=y + h / 2, restore_focus=False)
        overlays = wait_for(lambda: [w for w in s.windows() if "region capture" in w["title"]], what="region overlay")
        # the click left keyboard focus in the top bar: give it to the overlay, then cancel
        s.ext("Activate", f"uint64 {next(w for w in overlays if w['monitor'] == 0)['id']}")
        time.sleep(0.3)
        await call("key", combo="Escape", restore_focus=False)
        s.wait_window("region capture", gone=True)
        # the menu opens (real click: right button opens it, it shows ShareX's full menu)
        s.sharel("-ToggleTrayMenu")
        time.sleep(0.8)
        menu_region = (max(0, x - 400), 0, 600, 760)
        _o, data, _i, _e = await call("ocr", region=list(menu_region))
        seen = [ln["text"] for ln in data["data"]["lines"]]
        await call("screenshot", monitor=0, max_width=1200)
        for label in ("Capture", "Upload", "Workflows", "Tools", "Destinations", "History", "Exit"):
            assert any(t.strip().lower().startswith(label.lower()) for t in seen), (label, seen)
        await call("key", combo="Escape", restore_focus=False)
        # Submenu items: injected (zero-length) clicks on a submenu header close GNOME Shell's indicator
        # menu instead of expanding it (a held 150 ms press from a plain RemoteDesktop session does expand
        # it, but RootPilot's per-call handling closes shell popups). So submenu items are activated with
        # the dbusmenu Event the shell sends for a click; top-level items below use real clicks.
        before = s.screenshots()
        assert s.tray_menu_click("Capture", "Capture entire screen")["ok"]
        files = wait_for(lambda: [p for p in s.screenshots() if p not in before], what="tray capture")
        from PIL import Image
        with Image.open(files[0]) as im:
            assert im.size == (3520, 1080)
        cfg = json.loads((s.cfg_dir / "ApplicationConfig.json").read_text())
        assert "ShowInExplorer" not in cfg["default_task_settings"]["after_capture_job"]
        assert s.tray_menu_click("After capture tasks", "Show file in file manager")["ok"]
        time.sleep(0.6)
        cfg = json.loads((s.cfg_dir / "ApplicationConfig.json").read_text())
        assert "ShowInExplorer" in cfg["default_task_settings"]["after_capture_job"]
        # a top-level item: History…
        assert not any("History" in w["title"] for w in s.windows())
        s.sharel("-ToggleTrayMenu")
        time.sleep(0.8)
        await ocr_click(call, "History", menu_region)
        s.wait_window("History")
        await call("window_close", window="History")
        # Show main window (closed first, so the item has to open it)
        if any(w["title"] == "ShareL" for w in s.windows()):
            await call("window_close", window="ShareL")
            wait_for(lambda: not any(w["title"] == "ShareL" for w in s.windows()), what="main window closed")
        s.sharel("-ToggleTrayMenu")
        time.sleep(0.8)
        await ocr_click(call, "Show main window", menu_region)
        s.wait_window("ShareL")
