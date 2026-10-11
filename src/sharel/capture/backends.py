"""Getting pixels on GNOME Wayland: a chain of backends, tried in order.

1. **Extension** (``app.sharel.Shell.Screenshot``): in-shell capture, fast, no prompt, optional
   cursor, window content with alpha.
2. **MutterScreenCast** (``org.gnome.Mutter.ScreenCast`` RecordArea/RecordMonitor/RecordWindow,
   one PipeWire frame through GStreamer ``pipewiresrc``): no prompt for unsandboxed apps.
3. **Portal** (``org.freedesktop.portal.Screenshot``): may ask once.
4. **GnomeScreenshot** (``gnome-screenshot -f``).

Every capture returns a :class:`Capture`: an RGBA image plus the logical region it covers;
``scale`` = image pixels per logical pixel (physical vs logical under fractional scaling).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib
from PIL import Image

from ..shell.client import ShellUnavailable, Window, shell
from .screen import Layout, current_layout

log = logging.getLogger("sharel.capture")

SC = "org.gnome.Mutter.ScreenCast"
SC_PATH = "/org/gnome/Mutter/ScreenCast"
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


class CaptureError(RuntimeError):
    pass


@dataclass
class Capture:
    image: Image.Image
    region: tuple[int, int, int, int]  # logical
    scale: float = 1.0
    window: Window | None = None
    backend: str = ""
    notice: str = ""  # e.g. "window capture fell back to region capture"

    def crop_logical(self, rect: tuple[int, int, int, int]) -> Image.Image:
        """Crop a logical rect (global coordinates) out of this capture."""
        x, y, w, h = rect
        ox, oy = self.region[0], self.region[1]
        s = self.scale
        box = (round((x - ox) * s), round((y - oy) * s), round((x - ox + w) * s), round((y - oy + h) * s))
        return self.image.crop(box)


def _tmp_png() -> str:
    fd, path = tempfile.mkstemp(prefix="sharel-", suffix=".png")
    os.close(fd)
    return path


def _load(path: str, delete: bool = True) -> Image.Image:
    try:
        with Image.open(path) as im:
            im.load()
            return im.convert("RGBA")
    finally:
        if delete:
            try:
                os.unlink(path)
            except OSError:
                pass


def _session() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _has_owner(name: str) -> bool:
    try:
        (has,) = _session().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "NameHasOwner", GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE,
                                      2000, None).unpack()
        return bool(has)
    except GLib.Error:
        return False


class _PrivateLoop:
    """Wait for a D-Bus signal from any thread: a private main context, pushed as thread default
    while subscribing and iterated until the signal arrives (no dependency on the app's loop)."""

    def __init__(self) -> None:
        self.ctx = GLib.MainContext.new()

    def __enter__(self) -> _PrivateLoop:
        self.ctx.push_thread_default()
        return self

    def __exit__(self, *exc) -> None:
        while self.ctx.pending():
            self.ctx.iteration(False)
        self.ctx.pop_thread_default()

    def wait(self, done: threading.Event, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while not done.is_set() and time.monotonic() < deadline:
            self.ctx.iteration(False) or time.sleep(0.005)
        return done.is_set()


# ---- backends -----------------------------------------------------------------------------


class Backend:
    name = ""

    def available(self) -> bool:
        raise NotImplementedError

    def area(self, rect, cursor: bool) -> Capture:
        raise NotImplementedError

    def monitor(self, index: int, layout: Layout, cursor: bool) -> Capture:
        m = layout.monitors[index]
        return self.area(m.rect, cursor)

    def screen(self, layout: Layout, cursor: bool) -> Capture:
        return self.area(layout.bounds, cursor)

    def window(self, win: Window, cursor: bool, transparent: bool, shadow: bool) -> Capture:
        raise CaptureError(f"{self.name} cannot capture single windows")


class ExtensionBackend(Backend):
    name = "Extension"

    def available(self) -> bool:
        return shell().available

    def _shoot(self, kind: str, **kw) -> tuple[Image.Image, dict]:
        path = _tmp_png()
        try:
            res = shell().screenshot(kind, path, **kw)
        except ShellUnavailable as e:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise CaptureError(str(e)) from e
        return _load(path), res

    def area(self, rect, cursor: bool) -> Capture:
        img, res = self._shoot("area", rect=rect, cursor=cursor)
        return Capture(img, tuple(res["area"]), res.get("scale", 1.0), backend=self.name)

    def monitor(self, index: int, layout: Layout, cursor: bool) -> Capture:
        img, res = self._shoot("monitor", target=index, cursor=cursor)
        return Capture(img, tuple(res["area"]), res.get("scale", 1.0), backend=self.name)

    def screen(self, layout: Layout, cursor: bool) -> Capture:
        img, res = self._shoot("screen", cursor=cursor)
        return Capture(img, tuple(res["area"]), res.get("scale", 1.0), backend=self.name)

    def window(self, win: Window, cursor: bool, transparent: bool, shadow: bool) -> Capture:
        img, res = self._shoot("window", target=win.id, cursor=cursor)
        scale = res.get("scale", 1.0)
        frame = tuple(res["frame"])
        buffer = tuple(res["buffer"])
        w = Window.of(res.get("window", {})) if res.get("window") else win
        if shadow:
            return Capture(img, buffer, scale, window=w, backend=self.name)
        x, y, cw, ch = res["frame_in_buffer"]
        return Capture(img.crop((x, y, x + cw, y + ch)), frame, scale, window=w, backend=self.name)


class ScreenCastBackend(Backend):
    """One frame from a Mutter ScreenCast stream."""

    name = "MutterScreenCast"
    _gst_ready = False

    def available(self) -> bool:
        if not _has_owner(SC):
            return False
        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            if not ScreenCastBackend._gst_ready:
                Gst.init(None)
                ScreenCastBackend._gst_ready = True
            return Gst.ElementFactory.find("pipewiresrc") is not None
        except (ImportError, ValueError):
            return False

    def _record(self, method: str, sig: str, args: tuple, timeout_s: float = 6.0) -> Image.Image:
        from . import pwframe
        conn = _session()
        node: dict = {}
        got = threading.Event()
        with _PrivateLoop() as loop:
            (session,) = conn.call_sync(SC, SC_PATH, SC, "CreateSession", GLib.Variant("(a{sv})", ({},)), None,
                                        Gio.DBusCallFlags.NONE, 5000, None).unpack()
            sub = 0
            try:
                (stream,) = conn.call_sync(SC, session, SC + ".Session", method, GLib.Variant(sig, args), None,
                                           Gio.DBusCallFlags.NONE, 5000, None).unpack()

                def added(_c, _s, _p, _i, _n, params, _d):
                    node["id"] = params.unpack()[0]
                    got.set()
                sub = conn.signal_subscribe(SC, SC + ".Stream", "PipeWireStreamAdded", stream, None,
                                            Gio.DBusSignalFlags.NONE, added, None)
                conn.call_sync(SC, session, SC + ".Session", "Start", None, None, Gio.DBusCallFlags.NONE, 5000, None)
                if not loop.wait(got, timeout_s):
                    raise CaptureError("Mutter ScreenCast stream never started")
                return pwframe.pull_frame(node["id"], timeout_s)
            except GLib.Error as e:
                raise CaptureError(f"Mutter ScreenCast failed: {e.message}") from e
            finally:
                if sub:
                    conn.signal_unsubscribe(sub)
                try:
                    conn.call_sync(SC, session, SC + ".Session", "Stop", None, None, Gio.DBusCallFlags.NONE, 3000,
                                   None)
                except GLib.Error:
                    pass

    def area(self, rect, cursor: bool) -> Capture:
        x, y, w, h = (int(round(v)) for v in rect)
        img = self._record("RecordArea", "(iiiia{sv})",
                           (x, y, w, h, {"cursor-mode": GLib.Variant("u", 1 if cursor else 0)}))
        return Capture(img, (x, y, w, h), img.width / max(1, w), backend=self.name)

    def window(self, win: Window, cursor: bool, transparent: bool, shadow: bool) -> Capture:
        img = self._record("RecordWindow", "(a{sv})", ({"window-id": GLib.Variant("t", win.id),
                                                        "cursor-mode": GLib.Variant("u", 1 if cursor else 0)},))
        # Mutter draws the window's buffer at the top-left of a stream that can be bigger than it.
        fx, fy, fw, fh = win.frame
        bx, by, bw, bh = win.buffer if any(win.buffer[2:]) else win.frame
        scale = 1.0
        try:
            from .screen import current_layout
            mon = current_layout().monitor_at(fx + fw / 2, fy + fh / 2)
            scale = mon.scale if mon else 1.0
        except Exception:
            scale = 1.0
        if shadow:
            box, region = (0, 0, bw, bh), (bx, by, bw, bh)
        else:
            box, region = (fx - bx, fy - by, fx - bx + fw, fy - by + fh), (fx, fy, fw, fh)
        img = img.crop(tuple(round(v * scale) for v in box))
        return Capture(img, region, scale, window=win, backend=self.name)


class PortalBackend(Backend):
    name = "Portal"

    def available(self) -> bool:
        return _has_owner(PORTAL)

    def _full(self, timeout_s: float = 60.0) -> Image.Image:
        conn = _session()
        token = f"sharel{os.getpid()}{int(time.time() * 1000) & 0xFFFFFF}"
        sender = conn.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        out: dict = {}
        got = threading.Event()
        with _PrivateLoop() as loop:
            def response(_c, _s, _p, _i, _n, params, _d):
                code, results = params.unpack()
                out["code"], out["results"] = code, results
                got.set()
            sub = conn.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", handle, None,
                                        Gio.DBusSignalFlags.NONE, response, None)
            try:
                conn.call_sync(PORTAL, PORTAL_PATH, "org.freedesktop.portal.Screenshot", "Screenshot",
                               GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                                              "interactive": GLib.Variant("b", False)})),
                               None, Gio.DBusCallFlags.NONE, 10000, None)
                if not loop.wait(got, timeout_s):
                    raise CaptureError("screenshot portal did not answer")
            except GLib.Error as e:
                raise CaptureError(f"screenshot portal failed: {e.message}") from e
            finally:
                conn.signal_unsubscribe(sub)
        if out.get("code") != 0:
            raise CaptureError(f"screenshot portal denied or cancelled (code {out.get('code')})")
        path = unquote(urlparse(out["results"]["uri"]).path)
        return _load(path, delete=True)

    def area(self, rect, cursor: bool) -> Capture:
        layout = current_layout()
        full = self.screen(layout, cursor)
        return Capture(full.crop_logical(tuple(rect)), tuple(rect), full.scale, backend=self.name)

    def screen(self, layout: Layout, cursor: bool) -> Capture:
        img = self._full()
        bounds = layout.bounds if layout.monitors else (0, 0, img.width, img.height)
        return Capture(img, bounds, img.width / max(1, bounds[2]), backend=self.name)


class GnomeScreenshotBackend(Backend):
    name = "GnomeScreenshot"

    def available(self) -> bool:
        return shutil.which("gnome-screenshot") is not None

    def _run(self, args: list[str]) -> Image.Image:
        path = _tmp_png()
        os.unlink(path)
        try:
            subprocess.run(["gnome-screenshot", *args, "-f", path], timeout=30, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError) as e:
            raise CaptureError(f"gnome-screenshot failed: {e}") from e
        if not Path(path).exists():
            raise CaptureError("gnome-screenshot produced no file")
        return _load(path)

    def screen(self, layout: Layout, cursor: bool) -> Capture:
        img = self._run(["-p"] if cursor else [])
        bounds = layout.bounds if layout.monitors else (0, 0, img.width, img.height)
        return Capture(img, bounds, img.width / max(1, bounds[2]), backend=self.name)

    def area(self, rect, cursor: bool) -> Capture:
        full = self.screen(current_layout(), cursor)
        return Capture(full.crop_logical(tuple(rect)), tuple(rect), full.scale, backend=self.name)


BACKENDS: dict[str, type[Backend]] = {b.name: b for b in (ExtensionBackend, ScreenCastBackend, PortalBackend,
                                                          GnomeScreenshotBackend)}
ORDER = ["Extension", "MutterScreenCast", "Portal", "GnomeScreenshot"]


def chain(preferred: str = "Auto") -> list[Backend]:
    """Available backends, the preferred one first (``Auto`` keeps the standard order)."""
    names = list(ORDER)
    if preferred and preferred != "Auto" and preferred in names:
        names.remove(preferred)
        names.insert(0, preferred)
    out = []
    for n in names:
        b = BACKENDS[n]()
        try:
            if b.available():
                out.append(b)
        except Exception:
            continue
    return out


def _run_chain(preferred: str, fn) -> Capture:
    errors = []
    for b in chain(preferred):
        try:
            cap = fn(b)
            cap.backend = cap.backend or b.name
            return cap
        except CaptureError as e:
            log.warning("capture backend %s failed: %s", b.name, e)
            errors.append(f"{b.name}: {e}")
    raise CaptureError("no capture backend worked: " + ("; ".join(errors) or "none available"))


def capture_area(rect, cursor: bool = False, preferred: str = "Auto") -> Capture:
    return _run_chain(preferred, lambda b: b.area(tuple(int(round(v)) for v in rect), cursor))


def capture_screen(cursor: bool = False, preferred: str = "Auto") -> Capture:
    layout = current_layout()
    return _run_chain(preferred, lambda b: b.screen(layout, cursor))


def capture_monitor(index: int, cursor: bool = False, preferred: str = "Auto") -> Capture:
    layout = current_layout()
    if not 0 <= index < len(layout.monitors):
        index = layout.monitors.index(layout.primary) if layout.primary else 0
    return _run_chain(preferred, lambda b: b.monitor(index, layout, cursor))


def capture_window(win: Window | None, cursor: bool = False, transparent: bool = True, shadow: bool = False,
                   client_area: bool = False, preferred: str = "Auto") -> Capture:
    """A window. Without the extension (no window list) this degrades to the active monitor with a
    notice, as the plan prescribes for degraded mode."""
    if win is None:
        cap = capture_monitor(-1, cursor, preferred)
        cap.notice = ("Window capture needs the ShareL GNOME Shell extension; captured the screen instead. "
                      "Log out and back in after installing to enable it.")
        return cap
    if not transparent:
        # Like ShareX's non-transparent capture: the screen area of the window (activated first).
        try:
            shell().activate(win.id)
            time.sleep(0.15)
        except ShellUnavailable:
            pass
        rect = win.client_rect if client_area and any(win.client_rect[2:]) else win.frame
        cap = capture_area(rect, cursor, preferred)
        cap.window = win
        return cap
    errors = []
    for b in chain(preferred):
        try:
            cap = b.window(win, cursor, transparent, shadow)
            if client_area and any(win.client_rect[2:]) and tuple(win.client_rect) != tuple(cap.region):
                cap = Capture(cap.crop_logical(tuple(win.client_rect)), tuple(win.client_rect), cap.scale,
                              window=win, backend=cap.backend)
            return cap
        except CaptureError as e:
            errors.append(f"{b.name}: {e}")
    raise CaptureError("window capture failed: " + "; ".join(errors))


def describe() -> list[tuple[str, bool]]:
    out = []
    for n in ORDER:
        try:
            out.append((n, BACKENDS[n]().available()))
        except Exception:
            out.append((n, False))
    return out
