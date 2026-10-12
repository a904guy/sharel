"""Placing ShareL's own windows through the extension, the pin window's move and shadow, -silent /
-autoclose, and the ScreenCast portal fallback with its restore token (fake portal on the sandbox bus)."""
from __future__ import annotations

import os
import time

import pytest
from fakebus import FakeService
from gi.repository import Gio, GLib
from PIL import Image
from uihelpers import run_app

from sharel.shell.client import Window


class PlacingShell:
    """The extension's view of our windows: finds them by pid + unique title, records what we ask."""
    available = True

    def __init__(self):
        self.calls = []
        self.ids: dict[str, int] = {}  # one window id per title

    def find_windows(self, pid, title):
        assert pid == os.getpid()
        self.calls.append(("find", title))
        wid = self.ids.setdefault(title, 100 + len(self.ids))
        return [Window(id=wid, title=title, wm_class="sharel", pid=pid, frame=(0, 0, 10, 10),
                       buffer=(0, 0, 10, 10), client_rect=(0, 0, 10, 10), stacking=1)]

    def of(self, title):
        wid = self.ids.get(title)
        return [c for c in self.calls if c[0] != "find" and c[1] == wid]

    def position_window(self, wid, x, y):
        self.calls.append(("position", wid, x, y))
        return True

    def set_above(self, wid, above=True):
        self.calls.append(("above", wid, above))
        return True

    def activate(self, wid):
        self.calls.append(("activate", wid))
        return True


@pytest.fixture
def placing(monkeypatch):
    sh = PlacingShell()
    from sharel.ui import placement
    monkeypatch.setattr(placement, "shell", lambda: sh)
    return sh


def test_own_windows_are_positioned_kept_above_and_activated(placing):
    from gi.repository import Gtk

    from sharel.ui import placement

    def body(app):
        win = Gtk.Window(title=placement.unique_title("ShareL toolbar"), application=app)
        assert win.get_title().startswith("ShareL toolbar") and win.get_title() != "ShareL toolbar"
        win.present()
        title = win.get_title()
        placement.keep_above(win, (120, 340), activate=True)
        for _ in range(60):
            if any(c[0] == "activate" for c in placing.of(title)):
                break
            yield 0.05
        wid = placing.ids[title]  # found by its unique title
        assert placing.of(title) == [("position", wid, 120, 340), ("above", wid, True), ("activate", wid)]
        placing.calls.clear()
        placement.position(win, 5, 6)
        for _ in range(60):
            if placing.of(title):
                break
            yield 0.05
        assert placing.of(title) == [("position", wid, 5, 6)]
        win.destroy()
        return True
    assert run_app(body)


def test_pin_window_placement_shadow_and_move(placing, monkeypatch):
    from sharel.ui.windows import pin as P

    def body(app):
        p = P.pin_image(app, Image.new("RGBA", (120, 80), "blue"), position=(300, 200))
        for _ in range(60):
            if any(c[0] == "activate" for c in placing.of(p.get_title())):
                break
            yield 0.05
        pad, wid = P.SHADOW, placing.ids[p.get_title()]
        # placed so the IMAGE (not its shadow margin) lands where it was captured, kept above, focused
        assert placing.of(p.get_title()) == [("position", wid, 300 - pad, 200 - pad), ("above", wid, True),
                                             ("activate", wid)]
        w_shadow = p.get_default_size()[0]
        p.activate_action("pin.toggle-shadow", None)  # the pin menu item
        assert p.shadow is False and p.get_default_size()[0] < w_shadow
        p.activate_action("pin.toggle-shadow", None)  # the pin menu item
        assert p.get_default_size()[0] == w_shadow
        moved = []
        surf = p.get_surface()
        monkeypatch.setattr(type(surf), "begin_move", lambda self, dev, button, x, y, t: moved.append((button, x, y)),
                            raising=False)

        class G:  # a left-button press on the pin
            def get_current_button(self):
                return 1

            def get_current_event_device(self):
                return object()  # (the headless compositor has no seat; begin_move is recorded above)

            def get_current_event_time(self):
                return 0
        p._pressed(G(), 1, 15.0, 25.0)
        assert moved == [(1, 15.0, 25.0)]  # a compositor move: works on Wayland
        P.close_all_pins()
        return True
    assert run_app(body)


def test_silent_and_autoclose(tmp_path):
    from sharel.cli import parse_args
    f = tmp_path / "x.txt"
    f.write_text("x")
    reached = []

    def body(app):
        uploads = []
        app.tasks.upload_file = lambda path, ts: uploads.append(path)
        app.main_window = None
        app.handle_request(parse_args(["-silent"]), first=True)
        yield 0.2
        assert app.main_window is None  # silent start: no main window
        app.handle_request(parse_args([]), first=False)  # running already: a plain `sharel` shows it
        yield 0.2
        assert app.main_window is not None and app.main_window.get_mapped()
        app.main_window.destroy()
        app.main_window = None
        app.handle_request(parse_args(["-autoclose", str(f)]), first=False)
        assert uploads == [str(f)]
        yield 10  # -autoclose: quits once nothing is running
        reached.append(True)
        return True
    t = time.monotonic()
    assert run_app(body, timeout=30) is None
    assert not reached and time.monotonic() - t < 8


# -- ScreenCast portal with a restore token ----------------------------------------------------------

SC_PORTAL_XML = """<node><interface name="org.freedesktop.portal.ScreenCast">
<method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="SelectSources"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="o" direction="out"/></method>
<method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="o" direction="out"/></method>
<method name="OpenPipeWireRemote"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="h" direction="out"/></method></interface></node>"""
SESSION_XML = """<node><interface name="org.freedesktop.portal.Session"><method name="Close"/></interface></node>"""


def fake_screencast_portal(tokens_out: list[str]):
    box = {}

    def request(sender, token, results):
        handle = f"/org/freedesktop/portal/desktop/request/{sender[1:].replace('.', '_')}/{token}"
        svc = box["svc"]
        svc.later(lambda: svc.signal_now(handle, "org.freedesktop.portal.Request", "Response",
                                         GLib.Variant("(ua{sv})", (0, results))))
        return GLib.Variant("(o)", (handle,))

    def handler(method, args, sender):
        svc = box["svc"]
        if method == "CreateSession":
            opts = args[0]
            who = sender[1:].replace(".", "_")
            sp = f"/org/freedesktop/portal/desktop/session/{who}/{opts['session_handle_token']}"
            svc.add_object(sp, SESSION_XML, lambda *a: None)
            return request(sender, opts["handle_token"], {"session_handle": GLib.Variant("s", sp)})
        if method == "SelectSources":
            return request(sender, args[1]["handle_token"], {})
        if method == "Start":
            new = f"restore-{len(tokens_out) + 1}"
            tokens_out.append(new)
            stream = (77, {"position": GLib.Variant("(ii)", (0, 0)), "size": GLib.Variant("(ii)", (1920, 1080)),
                           "source_type": GLib.Variant("u", 1)})
            return request(sender, args[2]["handle_token"], {
                "streams": GLib.Variant("a(ua{sv})", [stream]), "restore_token": GLib.Variant("s", new)})
        if method == "OpenPipeWireRemote":
            fds = Gio.UnixFDList.new()
            r, _w = os.pipe()
            idx = fds.append(r)
            return GLib.Variant("(h)", (idx,)), fds
        return None
    svc = FakeService("org.freedesktop.portal.Desktop", {"/org/freedesktop/portal/desktop": (SC_PORTAL_XML, handler)})
    box["svc"] = svc
    return svc


def test_screencast_portal_flow_saves_and_reuses_the_restore_token():
    from sharel.record.portal import PortalScreenCast, TokenStore
    tokens: list[str] = []
    svc = fake_screencast_portal(tokens)
    store = TokenStore()
    try:
        p = PortalScreenCast(store, timeout=10)
        node, fd = p.start(cursor=True)
        assert node == 77 and isinstance(fd, int) and fd >= 0
        assert (p.stream_position, p.stream_size) == ((0, 0), (1920, 1080))
        sel = next(c for c in svc.calls if c[1] == "SelectSources")[2][1]
        assert sel["persist_mode"] == 2 and sel["cursor_mode"] == 2 and "restore_token" not in sel
        assert store.get() == "restore-1"  # remembered: no dialog next time
        p.close()
        p2 = PortalScreenCast(store, timeout=10)
        p2.start(cursor=False)
        sel2 = [c for c in svc.calls if c[1] == "SelectSources"][1][2][1]
        assert sel2["restore_token"] == "restore-1" and sel2["cursor_mode"] == 1
        assert store.get() == "restore-2"  # each grant hands out a new token
        p2.close()
        assert any(c[1] == "Close" for c in svc.calls)
    finally:
        svc.stop()
