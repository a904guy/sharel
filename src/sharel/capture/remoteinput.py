"""Input injection through org.gnome.Mutter.RemoteDesktop (no prompt for unsandboxed apps on GNOME).

Used by scrolling capture to put the pointer over the area and send wheel / Down / Page Down /
Home. Absolute pointer motion needs a ScreenCast stream, so one RecordMonitor stream per monitor is
linked to the session (nothing consumes them; they are coordinate anchors in physical pixels).

Two Mutter quirks (found by RootPilot, same author): the first key event on a fresh virtual keyboard
is dropped, so a Shift tap goes first; and a session must be quiet for a moment before Stop, or the
shell can touch the freed virtual device.
"""
from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger("sharel.remoteinput")

RD = "org.gnome.Mutter.RemoteDesktop"
RD_PATH = "/org/gnome/Mutter/RemoteDesktop"
SC = "org.gnome.Mutter.ScreenCast"
SC_PATH = "/org/gnome/Mutter/ScreenCast"
DC = "org.gnome.Mutter.DisplayConfig"
DC_PATH = "/org/gnome/Mutter/DisplayConfig"
QUIET_S = 0.3

KEYSYMS = {"Down": 0xFF54, "Up": 0xFF52, "Page_Down": 0xFF56, "Page_Up": 0xFF55, "Home": 0xFF50, "End": 0xFF57,
           "Shift_L": 0xFFE1, "Control_L": 0xFFE3}


class InputUnavailable(RuntimeError):
    pass


def monitors() -> list[dict]:
    """[{connector, x, y, width, height, scale}] in logical coordinates, from DisplayConfig."""
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    _serial, mons, logical, props = conn.call_sync(DC, DC_PATH, DC, "GetCurrentState", None, None,
                                                   Gio.DBusCallFlags.NONE, 5000, None).unpack()
    layout_mode = props.get("layout-mode", 1)
    out = []
    for x, y, scale, _t, _primary, lmons, _p in logical:
        connector = lmons[0][0]
        w = h = 0
        for (c, *_), modes, _mp in mons:
            if c == connector:
                cur = next((m for m in modes if m[6].get("is-current")), None)
                if cur:
                    w, h = cur[1], cur[2]
        if layout_mode == 1:
            w, h = round(w / scale), round(h / scale)
        out.append({"connector": connector, "x": x, "y": y, "width": w, "height": h, "scale": scale})
    return out


class RemoteInput:
    def __init__(self) -> None:
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = ""
        self.streams: dict[str, tuple[str, dict]] = {}
        self._last = 0.0

    def _call(self, path: str, iface: str, method: str, args: GLib.Variant | None = None, dest: str = RD):
        return self.conn.call_sync(dest, path, iface, method, args, None, Gio.DBusCallFlags.NONE, 5000,
                                   None).unpack()

    def __enter__(self) -> RemoteInput:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        try:
            (session,) = self._call(RD_PATH, RD, "CreateSession")
            self.session = session
            sid = self.conn.call_sync(RD, session, "org.freedesktop.DBus.Properties", "Get",
                                      GLib.Variant("(ss)", (RD + ".Session", "SessionId")), None,
                                      Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            link = {"remote-desktop-session-id": GLib.Variant("s", sid)}
            (sc_session,) = self._call(SC_PATH, SC, "CreateSession", GLib.Variant("(a{sv})", (link,)), dest=SC)
            for m in monitors():
                props = {"cursor-mode": GLib.Variant("u", 0)}
                (stream,) = self._call(sc_session, SC + ".Session", "RecordMonitor",
                                       GLib.Variant("(sa{sv})", (m["connector"], props)), dest=SC)
                self.streams[m["connector"]] = (stream, m)
            self._call(session, RD + ".Session", "Start")
            # Spend the dropped first events on harmless ones.
            for pressed in (True, False):
                self._call(session, RD + ".Session", "NotifyKeyboardKeysym",
                           GLib.Variant("(ub)", (KEYSYMS["Shift_L"], pressed)))
            for dx in (1.0, -1.0):
                self._call(session, RD + ".Session", "NotifyPointerMotionRelative", GLib.Variant("(dd)", (dx, 0.0)))
            self._last = time.monotonic()
            time.sleep(0.15)
        except GLib.Error as e:
            self.close()
            raise InputUnavailable(f"Mutter RemoteDesktop failed: {e.message}") from e

    def close(self) -> None:
        if not self.session:
            return
        left = self._last + QUIET_S - time.monotonic()
        if left > 0:
            time.sleep(left)
        try:
            self._call(self.session, RD + ".Session", "Stop")
        except GLib.Error:
            pass
        self.session = ""
        self.streams.clear()

    def _touch(self) -> None:
        self._last = time.monotonic()

    def move_to(self, x: float, y: float) -> None:
        """Pointer to global logical (x, y)."""
        for stream, m in self.streams.values():
            if m["x"] <= x < m["x"] + m["width"] and m["y"] <= y < m["y"] + m["height"]:
                s = m["scale"]
                self._call(self.session, RD + ".Session", "NotifyPointerMotionAbsolute",
                           GLib.Variant("(sdd)", (stream, (x - m["x"]) * s, (y - m["y"]) * s)))
                self._touch()
                return
        raise InputUnavailable(f"({x}, {y}) is not on any monitor")

    def wheel(self, steps: int) -> None:
        """Discrete wheel clicks: positive scrolls down."""
        self._call(self.session, RD + ".Session", "NotifyPointerAxisDiscrete", GLib.Variant("(ui)", (0, int(steps))))
        self._touch()

    def key(self, name: str, times: int = 1) -> None:
        sym = KEYSYMS[name]
        for _ in range(max(1, times)):
            for pressed in (True, False):
                self._call(self.session, RD + ".Session", "NotifyKeyboardKeysym", GLib.Variant("(ub)", (sym, pressed)))
            time.sleep(0.01)
        self._touch()

    def click(self, button: int = 1) -> None:
        code = {1: 0x110, 2: 0x112, 3: 0x111}[button]  # BTN_LEFT / BTN_MIDDLE / BTN_RIGHT
        for pressed in (True, False):
            self._call(self.session, RD + ".Session", "NotifyPointerButton", GLib.Variant("(ib)", (code, pressed)))
        self._touch()
