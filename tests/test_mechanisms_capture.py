"""The capture backends after the extension (Mutter ScreenCast, the Screenshot portal, gnome-screenshot),
the DisplayConfig layout with fractional scaling, and degraded mode without the extension. Each desktop
service is a fake on the sandbox's private bus; ShareL's client code is the real one."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
from fakebus import FakeService
from gi.repository import GLib
from PIL import Image

from sharel.capture import backends as B
from sharel.capture import screen
from sharel.shell.client import Monitor, Window


def pattern(w, h, seed=1):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def same(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and np.array_equal(np.asarray(a.convert("RGBA")), np.asarray(b.convert("RGBA")))


@pytest.fixture
def layout(monkeypatch):
    """A 200x100 logical screen; tests set the scale."""
    box = {"scale": 1.0}

    def current():
        return screen.Layout([Monitor(0, 0, 0, 200, 100, box["scale"], True)])
    monkeypatch.setattr(B, "current_layout", current)
    monkeypatch.setattr(screen, "current_layout", current)
    return box


# -- Screenshot portal -----------------------------------------------------------------------------

PORTAL_XML = """<node><interface name="org.freedesktop.portal.Screenshot">
<method name="Screenshot"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="o" direction="out"/></method></interface></node>"""


def portal_service(image_path: Path | None, code: int = 0):
    svc_box = {}

    def handler(method, args, sender):
        _parent, opts = args
        token = opts["handle_token"]
        handle = f"/org/freedesktop/portal/desktop/request/{sender[1:].replace('.', '_')}/{token}"
        results = {"uri": GLib.Variant("s", f"file://{image_path}")} if image_path else {}
        svc = svc_box["svc"]
        svc.later(lambda: svc.signal_now(handle, "org.freedesktop.portal.Request", "Response",
                                         GLib.Variant("(ua{sv})", (code, results))))
        return GLib.Variant("(o)", (handle,))
    svc = FakeService("org.freedesktop.portal.Desktop", {"/org/freedesktop/portal/desktop": (PORTAL_XML, handler)})
    svc_box["svc"] = svc
    return svc


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_portal_backend_screen_and_area(tmp_path, layout, scale):
    layout["scale"] = scale
    full = pattern(int(200 * scale), int(100 * scale))
    shot = tmp_path / "portal.png"
    full.save(shot)
    svc = portal_service(shot)
    try:
        b = B.PortalBackend()
        assert b.available()
        cap = b.area((10, 20, 50, 30), cursor=False)
        assert cap.backend == "Portal" and cap.region == (10, 20, 50, 30) and cap.scale == scale
        s = int(scale)
        assert same(cap.image, full.crop((10 * s, 20 * s, 60 * s, 50 * s)))  # logical rect, physical pixels
        assert not shot.exists()  # the portal's file is ShareL's to delete
        opts = svc.calls[0][2][1]
        assert opts["interactive"] is False and svc.calls[0][1] == "Screenshot"
    finally:
        svc.stop()


def test_portal_cancelled_is_an_error_and_the_chain_moves_on(tmp_path, layout, monkeypatch):
    svc = portal_service(None, code=1)  # user cancelled
    try:
        with pytest.raises(B.CaptureError, match="denied or cancelled"):
            B.PortalBackend().area((0, 0, 10, 10), False)
        # in the chain, the next backend (gnome-screenshot here) takes over
        monkeypatch.setattr(B, "ORDER", ["Portal", "GnomeScreenshot"])
        fake_gnome_screenshot(tmp_path, monkeypatch, pattern(200, 100, 5))
        cap = B.capture_area((0, 0, 20, 10))
        assert cap.backend == "GnomeScreenshot" and same(cap.image, pattern(200, 100, 5).crop((0, 0, 20, 10)))
    finally:
        svc.stop()


# -- gnome-screenshot ------------------------------------------------------------------------------

def fake_gnome_screenshot(tmp_path, monkeypatch, img: Image.Image):
    src = tmp_path / "gs-source.png"
    img.save(src)
    bindir = tmp_path / "gsbin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "gnome-screenshot"
    args_file = tmp_path / "gs-args.txt"
    exe.write_text(f"#!{sys.executable}\nimport shutil, sys\n"
                   f"open({str(args_file)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                   f"shutil.copy({str(src)!r}, sys.argv[sys.argv.index('-f') + 1])\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return args_file


def test_gnome_screenshot_backend(tmp_path, layout, monkeypatch):
    full = pattern(200, 100, 2)
    args = fake_gnome_screenshot(tmp_path, monkeypatch, full)
    b = B.GnomeScreenshotBackend()
    assert b.available()
    cap = b.screen(screen.current_layout(), cursor=True)
    assert same(cap.image, full) and cap.region == (0, 0, 200, 100) and cap.backend == "GnomeScreenshot"
    cap = b.area((100, 50, 40, 20), cursor=False)
    assert same(cap.image, full.crop((100, 50, 140, 70)))
    lines = args.read_text().splitlines()
    assert lines[0].startswith("-p -f ") and lines[1].startswith("-f ")  # -p: include the pointer
    assert not Path(lines[0].split()[-1]).exists()  # its temp file is removed


# -- Mutter ScreenCast (one PipeWire frame) --------------------------------------------------------

SC = "org.gnome.Mutter.ScreenCast"
SC_XML = f"""<node><interface name="{SC}">
<method name="CreateSession"><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/></method>
</interface></node>"""
SESSION_XML = f"""<node><interface name="{SC}.Session">
<method name="RecordArea"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="i" direction="in"/>
<arg type="i" direction="in"/><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/></method>
<method name="RecordWindow"><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Start"/><method name="Stop"/></interface></node>"""
STREAM_XML = f"""<node><interface name="{SC}.Stream"><signal name="PipeWireStreamAdded"><arg type="u"/></signal>
</interface></node>"""


class FakeScreenCast:
    def __init__(self):
        self.n = 0
        self.stopped = []
        self.svc = FakeService(SC, {"/org/gnome/Mutter/ScreenCast": (SC_XML, self._root)})

    def _root(self, method, args, sender):
        self.n += 1
        sp, st = f"/org/gnome/Mutter/ScreenCast/Session/u{self.n}", f"/org/gnome/Mutter/ScreenCast/Stream/u{self.n}"
        self.svc.add_object(st, STREAM_XML, lambda *a: None)

        def session(method, args, sender):
            if method in ("RecordArea", "RecordWindow"):
                return GLib.Variant("(o)", (st,))
            if method == "Start":
                self.svc.later(lambda: self.svc.signal_now(st, SC + ".Stream", "PipeWireStreamAdded",
                                                           GLib.Variant("(u)", (500 + self.n,))))
            if method == "Stop":
                self.stopped.append(sp)
            return None
        self.svc.add_object(sp, SESSION_XML, session)
        return GLib.Variant("(o)", (sp,))


@pytest.fixture
def screencast(monkeypatch):
    fake = FakeScreenCast()
    frames = {}
    from sharel.capture import pwframe
    monkeypatch.setattr(pwframe, "pull_frame", lambda node, timeout_s=5.0: frames[node])
    monkeypatch.setattr(B.ScreenCastBackend, "available", lambda self: True)
    yield fake, frames
    fake.svc.stop()


def test_screencast_backend_area_asks_mutter_for_that_area(screencast, layout):
    fake, frames = screencast
    frames[501] = pattern(50, 30, 3)
    cap = B.ScreenCastBackend().area((10, 20, 50, 30), cursor=True)
    assert same(cap.image, frames[501]) and cap.region == (10, 20, 50, 30) and cap.scale == 1.0
    rec = next(c for c in fake.svc.calls if c[1] == "RecordArea")
    assert rec[2][:4] == (10, 20, 50, 30) and rec[2][4]["cursor-mode"] == 1
    assert fake.stopped == ["/org/gnome/Mutter/ScreenCast/Session/u1"]  # the session is always stopped


@pytest.mark.parametrize("shadow", [False, True])
def test_screencast_window_is_cropped_from_the_buffer_at_the_stream_top_left(screencast, layout, shadow):
    fake, frames = screencast
    # Mutter draws the window buffer (with its shadow margin) at the stream's top-left
    win = Window(id=7, title="w", wm_class="c", pid=1, frame=(30, 40, 100, 50), buffer=(20, 30, 120, 70),
                 client_rect=(30, 40, 100, 50), stacking=1)
    stream = pattern(160, 90, 4)
    frames[501] = stream
    cap = B.ScreenCastBackend().window(win, cursor=False, transparent=True, shadow=shadow)
    if shadow:
        assert same(cap.image, stream.crop((0, 0, 120, 70))) and cap.region == (20, 30, 120, 70)
    else:
        assert same(cap.image, stream.crop((10, 10, 110, 60))) and cap.region == (30, 40, 100, 50)
    rec = next(c for c in fake.svc.calls if c[1] == "RecordWindow")
    assert rec[2][0]["window-id"] == 7


# -- DisplayConfig: physical vs logical pixels -----------------------------------------------------

DC = "org.gnome.Mutter.DisplayConfig"
DC_XML = f"""<node><interface name="{DC}"><method name="GetCurrentState">
<arg type="u" direction="out"/><arg type="a((ssss)a(siiddada{{sv}})a{{sv}})" direction="out"/>
<arg type="a(iiduba(ssss)a{{sv}})" direction="out"/><arg type="a{{sv}}" direction="out"/>
</method></interface></node>"""


def test_display_config_layout_with_fractional_scale():
    def mode(w, h, current):
        return (f"{w}x{h}", w, h, 60.0, 1.0, [1.0, 1.5, 2.0], {"is-current": GLib.Variant("b", current)})
    monitors = [(("DP-1", "X", "Y", "1"), [mode(2560, 1440, True), mode(1920, 1080, False)], {}),
                (("HDMI-1", "X", "Y", "2"), [mode(1920, 1080, True)], {})]
    logical = [(1707, 0, 1.0, 0, False, [("HDMI-1", "X", "Y", "2")], {}),
               (0, 0, 1.5, 0, True, [("DP-1", "X", "Y", "1")], {})]
    reply = GLib.Variant("(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})",
                         (1, monitors, logical, {"layout-mode": GLib.Variant("u", 1)}))
    svc = FakeService(DC, {"/org/gnome/Mutter/DisplayConfig": (DC_XML, lambda *a: reply)})
    try:
        lay = screen.from_display_config()
    finally:
        svc.stop()
    a, b = lay.monitors
    # the 2560x1440 panel at 150 % is 1707x960 logical pixels; the second monitor sits right of it
    assert (a.x, a.y, a.width, a.height, a.scale, a.primary) == (0, 0, 1707, 960, 1.5, True)
    assert (b.x, b.width, b.height, b.scale) == (1707, 1920, 1080, 1.0)
    assert lay.bounds == (0, 0, 3627, 1080) and lay.max_scale == 1.5
    # a capture taken in physical pixels crops by logical coordinates
    cap = B.Capture(pattern(2560, 1440, 6), (0, 0, 1707, 960), 1.5)
    assert cap.crop_logical((100, 100, 200, 100)).size == (300, 150)


# -- degraded mode ---------------------------------------------------------------------------------

def test_without_the_extension_window_capture_falls_back_with_a_notice(tmp_path, layout, monkeypatch):
    monkeypatch.setattr(B, "ORDER", ["Extension", "GnomeScreenshot"])
    monkeypatch.setattr(B.ExtensionBackend, "available", lambda self: False)
    fake_gnome_screenshot(tmp_path, monkeypatch, pattern(200, 100, 8))
    assert [b.name for b in B.chain()] == ["GnomeScreenshot"]
    cap = B.capture_window(None)  # no extension: no window list, no picked window
    assert cap.backend == "GnomeScreenshot" and "needs the ShareL GNOME Shell extension" in cap.notice
    assert same(cap.image, pattern(200, 100, 8))
    with pytest.raises(B.CaptureError, match="no capture backend worked"):
        monkeypatch.setattr(B, "ORDER", ["Extension"])
        B.capture_area((0, 0, 5, 5))
