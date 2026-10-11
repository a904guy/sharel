"""Client for the ShareL GNOME Shell extension (app.sharel.Shell).

``ShellClient.get()`` returns the live client when the extension is on the bus, otherwise a
:class:`NullShell` whose methods report "unavailable" (degraded mode: callers fall back, e.g.
window capture becomes region capture with a notice).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger("sharel.shell")

BUS_NAME = "app.sharel.Shell"
OBJECT_PATH = "/app/sharel/Shell"
IFACE = "app.sharel.Shell"


@dataclass
class Monitor:
    index: int
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0
    primary: bool = False

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


@dataclass
class Window:
    id: int
    title: str = ""
    wm_class: str = ""
    app_id: str = ""
    app_name: str = ""
    pid: int = 0
    client: str = "wayland"
    type: str = "NORMAL"
    frame: tuple[int, int, int, int] = (0, 0, 0, 0)
    buffer: tuple[int, int, int, int] = (0, 0, 0, 0)
    client_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    monitor: int = 0
    minimized: bool = False
    maximized: bool = False
    fullscreen: bool = False
    above: bool = False
    focus: bool = False
    stacking: int = -1
    on_current_workspace: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, d: dict) -> Window:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "extra"}
        for k in ("frame", "buffer", "client_rect"):
            if k in known:
                known[k] = tuple(known[k])
        w = cls(**known)
        w.extra = {k: v for k, v in d.items() if k not in cls.__dataclass_fields__}
        return w

    @property
    def process_name(self) -> str:
        if self.pid:
            try:
                return open(f"/proc/{self.pid}/comm").read().strip()
            except OSError:
                pass
        return (self.wm_class or self.app_id).split(".")[-1].lower()


class ShellUnavailable(RuntimeError):
    pass


class NullShell:
    """No extension: everything that needs the shell says so."""

    available = False
    version = ""

    def __getattr__(self, name):
        def unavailable(*_a, **_k):
            raise ShellUnavailable("the ShareL GNOME Shell extension is not running (log out and back in after "
                                   "installing, then enable it with: gnome-extensions enable sharel@hawkins.app)")
        return unavailable

    def get_monitors(self) -> list[Monitor]:
        return []

    def get_windows(self) -> list[Window]:
        return []

    def get_active_window(self) -> Window | None:
        return None

    def connect_accelerator(self, cb) -> int:
        return 0


class ShellClient:
    available = True
    _instance: ShellClient | NullShell | None = None
    _lock = threading.Lock()

    def __init__(self, conn: Gio.DBusConnection) -> None:
        self.conn = conn
        self.version = ""
        try:
            v = conn.call_sync(BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", (IFACE, "Version")), None, Gio.DBusCallFlags.NONE, 2000, None)
            self.version = v.unpack()[0]
        except GLib.Error:
            self.version = ""

    @classmethod
    def get(cls, refresh: bool = False) -> ShellClient | NullShell:
        with cls._lock:
            if cls._instance is None or refresh:
                cls._instance = cls._probe()
            return cls._instance

    @classmethod
    def _probe(cls) -> ShellClient | NullShell:
        if os.environ.get("SHAREL_NO_EXTENSION"):
            return NullShell()
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            (has,) = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                    "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), None,
                                    Gio.DBusCallFlags.NONE, 2000, None).unpack()
        except GLib.Error:
            return NullShell()
        return cls(conn) if has else NullShell()

    # -- plumbing --

    def _call(self, method: str, sig: str | None = None, args: tuple = (), timeout_ms: int = 10000,
              fds: bool = False) -> Any:
        try:
            reply = self.conn.call_sync(BUS_NAME, OBJECT_PATH, IFACE, method,
                                        GLib.Variant(sig, args) if sig else None, None, Gio.DBusCallFlags.NONE,
                                        timeout_ms, None)
        except GLib.Error as e:
            raise ShellUnavailable(f"{method}: {e.message}") from e
        out = reply.unpack()
        return out[0] if len(out) == 1 else out

    def _json(self, method: str, sig: str | None = None, args: tuple = (), timeout_ms: int = 10000) -> Any:
        return json.loads(self._call(method, sig, args, timeout_ms))

    # -- queries --

    def get_monitors(self) -> list[Monitor]:
        return [Monitor(m["index"], m["x"], m["y"], m["width"], m["height"], m.get("scale", 1.0),
                        m.get("primary", False)) for m in self._json("GetMonitors")]

    def get_windows(self) -> list[Window]:
        return [Window.of(d) for d in self._json("GetWindows")]

    def get_active_window(self) -> Window | None:
        d = self._json("GetActiveWindow")
        return Window.of(d) if d else None

    def get_window_details(self, wid: int) -> dict:
        return self._json("GetWindowDetails", "(t)", (wid,))

    def get_pointer(self) -> tuple[int, int, int]:
        return tuple(self._call("GetPointer"))

    def find_windows(self, pid: int = 0, title: str = "") -> list[Window]:
        return [Window.of(d) for d in self._json("FindWindows", "(us)", (pid, title))]

    # -- window ops --

    def activate(self, wid: int) -> bool:
        return self._call("Activate", "(t)", (wid,))

    def set_above(self, wid: int, above: bool = True) -> bool:
        return self._call("SetAbove", "(tb)", (wid, above))

    def toggle_above(self, wid: int) -> bool:
        return self._call("ToggleAbove", "(t)", (wid,))

    def move_resize(self, wid: int, x: int, y: int, w: int, h: int) -> dict:
        return self._json("MoveResize", "(tiiii)", (wid, x, y, w, h))

    def position_window(self, wid: int, x: int, y: int) -> bool:
        return self._call("PositionWindow", "(tii)", (wid, int(x), int(y)))

    def make_borderless(self, wid: int, borderless: bool = True) -> dict:
        return self._json("MakeBorderless", "(tb)", (wid, borderless))

    def make_fullscreen(self, wid: int, fullscreen: bool = True) -> bool:
        return self._call("MakeFullscreen", "(tb)", (wid, fullscreen))

    def set_opacity(self, wid: int, opacity: int) -> bool:
        return self._call("SetOpacity", "(tu)", (wid, max(0, min(255, opacity))))

    def set_sticky(self, wid: int, sticky: bool) -> bool:
        return self._call("SetSticky", "(tb)", (wid, sticky))

    # -- capture --

    def screenshot(self, kind: str, path: str, *, target: int = 0, rect=(0, 0, 0, 0), cursor: bool = False,
                   timeout_ms: int = 20000) -> dict:
        x, y, w, h = (int(round(v)) for v in rect)
        res = self._json("Screenshot", "(stiiiibs)", (kind, int(target), x, y, w, h, bool(cursor), path),
                         timeout_ms)
        if not res.get("ok"):
            raise ShellUnavailable(f"extension screenshot failed: {res.get('error')}")
        return res

    def pick_window(self, hint: str = "") -> Window | None:
        res = self._json("PickWindow", "(s)", (hint,), timeout_ms=600000)
        if res.get("cancelled") or not res.get("window"):
            return None
        return Window.of(res["window"])

    # -- clipboard --

    def set_clipboard(self, mime: str, data: bytes) -> bool:
        return self._call("SetClipboard", "(say)", (mime, data))

    def get_clipboard(self, mime: str) -> bytes:
        return bytes(self._call("GetClipboard", "(s)", (mime,)))

    def clipboard_mimetypes(self) -> list[str]:
        return list(self._call("GetClipboardMimetypes"))

    # -- overlays --

    def mouse_highlighter(self, on: bool, style: dict | None = None) -> bool:
        return self._call("MouseHighlighter", "(bs)", (on, json.dumps(style or {})))

    def show_border(self, tag: str, rect, style: dict | None = None) -> bool:
        x, y, w, h = (int(round(v)) for v in rect)
        return self._call("ShowBorder", "(siiiis)", (tag, x, y, w, h, json.dumps(style or {})))

    def hide_border(self, tag: str) -> bool:
        return self._call("HideBorder", "(s)", (tag,))

    # -- hotkeys --

    def grab_accelerator(self, accel: str, name: str) -> int:
        return self._call("GrabAccelerator", "(ss)", (accel, name))

    def ungrab_accelerator(self, handle: int) -> bool:
        return self._call("UngrabAccelerator", "(u)", (handle,))

    def ungrab_all(self) -> bool:
        return self._call("UngrabAll")

    def list_accelerators(self) -> list[dict]:
        return self._json("ListAccelerators")

    def connect_accelerator(self, cb) -> int:
        """cb(handle, name, timestamp) on the thread running the default GLib main context."""
        def handler(_conn, _sender, _path, _iface, _signal, params, _data):
            handle, name, ts = params.unpack()
            cb(handle, name, ts)
        return self.conn.signal_subscribe(BUS_NAME, IFACE, "AcceleratorActivated", OBJECT_PATH, None,
                                          Gio.DBusSignalFlags.NONE, handler, None)

    def disconnect(self, sub_id: int) -> None:
        if sub_id:
            self.conn.signal_unsubscribe(sub_id)


def shell() -> ShellClient | NullShell:
    return ShellClient.get()
