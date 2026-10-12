"""A screen recording: Mutter ScreenCast (RecordArea / RecordMonitor) → PipeWire → GStreamer.

    rec = Recorder(rect, out_path, opts, fps=30, gif=False, cursor=True)
    rec.start()          # session + pipeline running
    rec.pause(); rec.resume()
    path = rec.stop()    # EOS, finalize; GIF / WebP / APNG / Xvid converted by ffmpeg afterwards
    rec.abort()          # stop and delete everything

Without Mutter's ScreenCast (a non-GNOME session) the xdg-desktop-portal ScreenCast is used, with
``persist_mode=2`` and a stored restore token so the user is asked once (record/portal.py).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, Gst

from ..capture.pwframe import gst_init
from ..core.config import FFmpegOptions
from . import pipeline as P

log = logging.getLogger("sharel.recorder")

SC = "org.gnome.Mutter.ScreenCast"
SC_PATH = "/org/gnome/Mutter/ScreenCast"


class RecordingError(RuntimeError):
    pass


class Recorder:
    def __init__(self, rect: tuple[int, int, int, int], out_path: str, opts: FFmpegOptions, *, fps: int = 30,
                 gif: bool = False, cursor: bool = True, monitor_connector: str = "", test_pattern: bool = False,
                 gif_fps: int = 15, restore_token_store=None) -> None:
        x, y, w, h = (int(round(v)) for v in rect)
        self.rect = (x, y, P.even(w), P.even(h))
        self.out_path = out_path
        self.opts = opts
        self.fps = fps
        self.gif = gif
        self.gif_fps = gif_fps
        self.cursor = cursor
        self.monitor_connector = monitor_connector
        self.test_pattern = test_pattern
        self.restore_token_store = restore_token_store
        self.ep = P.plan(opts) if not gif else P.EncoderPlan("gif", P.INTERMEDIATE, "matroskamux", "gif", "mkv")
        # Written next to the final file and renamed when complete: a half-written video never shows
        # up under its real name (file managers, watch folders, the after-capture tasks).
        self.record_path = f"{out_path}.part" if self.ep.record_extension == self.ep.extension else \
            tempfile.mktemp(prefix="sharel-rec-", suffix=f".{self.ep.record_extension}")
        self.pipe: Gst.Pipeline | None = None
        self._session = ""
        self._conn: Gio.DBusConnection | None = None
        self._closed_sub = 0
        self._name_watch = 0
        self._watchdog_src = 0
        self._last_buffer = 0.0
        self._src_size: tuple[int, int] | None = None
        # A live ScreenCast node resends its last frame every second (keepalive): this long without
        # one means the stream is gone (pipewiresrc then neither errors nor ends, it just stalls).
        self.stall_timeout_s = 4.0
        # Called (main loop) when the recording dies on its own: the source vanished or the pipeline failed.
        self.on_failed: Callable[[str], None] | None = None
        self._portal = None
        self.started_at = 0.0
        self._paused_total = 0.0
        self._paused_at = 0.0
        self._paused_ns = 0
        self.state = "idle"  # idle | recording | paused | finishing | done | aborted | failed
        self._watching = False
        self._stopped_at = 0.0
        self.error = ""

    # -- elapsed --

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        now = self._paused_at if self.state == "paused" else (self._stopped_at or time.monotonic())
        return max(0.0, now - self.started_at - self._paused_total)

    # -- session --

    def _start_screencast(self) -> int:
        from ..capture.backends import _PrivateLoop
        # GLib caches the session bus only weakly: held in a local, the connection closes when this
        # returns, Mutter sees its caller vanish and ends the session (pipewiresrc then autoconnects
        # to any other video node, e.g. a webcam). Keep it for the whole recording.
        conn = self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node: dict = {}
        got = threading.Event()
        # As GNOME Shell's screencast service asks for it: a recording stream (Mutter then offers the
        # max-framerate the pipeline caps on).
        props = {"cursor-mode": GLib.Variant("u", 1 if self.cursor else 0), "is-recording": GLib.Variant("b", True)}
        with _PrivateLoop() as loop:
            (session,) = conn.call_sync(SC, SC_PATH, SC, "CreateSession", GLib.Variant("(a{sv})", ({},)), None,
                                        Gio.DBusCallFlags.NONE, 5000, None).unpack()
            self._session = session
            if self.monitor_connector:
                (stream,) = conn.call_sync(SC, session, SC + ".Session", "RecordMonitor",
                                           GLib.Variant("(sa{sv})", (self.monitor_connector, props)), None,
                                           Gio.DBusCallFlags.NONE, 5000, None).unpack()
            else:
                x, y, w, h = self.rect
                (stream,) = conn.call_sync(SC, session, SC + ".Session", "RecordArea",
                                           GLib.Variant("(iiiia{sv})", (x, y, w, h, props)), None,
                                           Gio.DBusCallFlags.NONE, 5000, None).unpack()

            def added(_c, _s, _p, _i, _n, params, _d):
                node["id"] = params.unpack()[0]
                got.set()
            sub = conn.signal_subscribe(SC, SC + ".Stream", "PipeWireStreamAdded", stream, None,
                                        Gio.DBusSignalFlags.NONE, added, None)
            try:
                conn.call_sync(SC, session, SC + ".Session", "Start", None, None, Gio.DBusCallFlags.NONE, 5000, None)
                if not loop.wait(got, 6.0):
                    raise RecordingError("the ScreenCast stream never started")
            finally:
                conn.signal_unsubscribe(sub)
        # Subscribed outside the private loop, so the app's main loop dispatches it: if Mutter ends
        # the session (monitor unplugged, shell restart), the recording fails instead of going on.
        self._closed_sub = conn.signal_subscribe(SC, SC + ".Session", "Closed", session, None,
                                                 Gio.DBusSignalFlags.NONE,
                                                 lambda *_a: self._fail("the ScreenCast session was closed"), None)
        # ...and if Mutter itself goes away without closing it (a crash):
        self._name_watch = Gio.bus_watch_name_on_connection(
            conn, SC, Gio.BusNameWatcherFlags.NONE, None,
            lambda *_a: self._fail("Mutter's ScreenCast service went away"))
        return node["id"]

    def _stop_screencast(self) -> None:
        if self._closed_sub and self._conn is not None:
            self._conn.signal_unsubscribe(self._closed_sub)
        self._closed_sub = 0
        if self._name_watch:
            Gio.bus_unwatch_name(self._name_watch)
            self._name_watch = 0
        if self._session:
            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                conn.call_sync(SC, self._session, SC + ".Session", "Stop", None, None, Gio.DBusCallFlags.NONE, 3000,
                               None)
            except GLib.Error:
                pass
            self._session = ""
        self._conn = None
        if self._portal is not None:
            self._portal.close()
            self._portal = None

    # -- control --

    def start(self) -> None:
        gst_init()
        fd = None
        node = None
        if not self.test_pattern:
            try:
                node = self._start_screencast()
            except (GLib.Error, RecordingError) as e:
                log.warning("Mutter ScreenCast failed (%s); trying the ScreenCast portal", e)
                from .portal import PortalScreenCast
                self._portal = PortalScreenCast(self.restore_token_store)
                node, fd = self._portal.start(cursor=self.cursor)
        src = P.video_source(node, fd, self.test_pattern)
        _x, _y, w, h = self.rect
        fps = self.gif_fps if self.gif else self.fps
        audio = "" if self.gif else self.opts.audio_source
        desc = P.build_launch(src, self.ep, self.record_path, fps, width=w, height=h, audio_source=audio, o=self.opts)
        log.info("recording: %s", desc)
        try:
            self.pipe = Gst.parse_launch(desc)
        except GLib.Error as e:
            self._stop_screencast()
            raise RecordingError(f"bad recording pipeline: {e.message}") from e
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        self._watching = True
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)
        ret = self.pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RecordingError("the recording pipeline could not start")
        if not self.test_pattern:
            self._check_source(timeout_s=5.0)
            self._guard_source()
        self.started_at = time.monotonic()
        self.state = "recording"

    def _guard_source(self) -> None:
        """While recording, fail (never go on silently) if the stream stalls or is renegotiated to
        another frame size (a different node)."""
        pad = self.pipe.get_by_name("pwsrc").get_static_pad("src")
        self._last_buffer = time.monotonic()

        def changed(size: tuple[int, int]) -> bool:
            self._fail(f"the stream changed to {size[0]}x{size[1]} frames mid-recording: another source")
            return False

        def probe(_pad, info):
            if info.type & Gst.PadProbeType.BUFFER:
                self._last_buffer = time.monotonic()
            else:
                ev = info.get_event()
                if ev.type == Gst.EventType.CAPS and self._src_size is not None:
                    st = ev.parse_caps().get_structure(0)
                    size = (st.get_value("width"), st.get_value("height"))
                    if size != self._src_size:
                        GLib.idle_add(changed, size)  # streaming thread: fail on the main loop
            return Gst.PadProbeReturn.OK
        pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.EVENT_DOWNSTREAM, probe)

        def watchdog() -> bool:
            if self.state not in ("idle", "recording", "paused"):
                self._watchdog_src = 0
                return False
            if time.monotonic() - self._last_buffer > self.stall_timeout_s:
                self._watchdog_src = 0
                self._fail(f"the screen stream stopped delivering frames for {self.stall_timeout_s:.0f} s")
                return False
            return True
        self._watchdog_src = GLib.timeout_add(250, watchdog)

    def _check_source(self, timeout_s: float) -> None:
        """Make sure we record the ScreenCast stream we asked for: its frames must be the region's size
        (at the monitor scale). Anything else is another PipeWire node: stop, never record it."""
        src = self.pipe.get_by_name("pwsrc") if self.pipe else None
        if src is None:
            return
        pad = src.get_static_pad("src")
        deadline = time.monotonic() + timeout_s
        caps = None
        bus = self.pipe.get_bus()
        while time.monotonic() < deadline and self.state != "failed":
            m = bus.pop_filtered(Gst.MessageType.ERROR)  # start() may run on the loop that dispatches the watch
            if m is not None:
                self._on_error(bus, m)
                break
            caps = pad.get_current_caps()
            if caps is not None:
                break
            time.sleep(0.05)
        if caps is None:
            self._teardown()
            raise RecordingError(f"the ScreenCast stream never delivered a frame ({self.error or 'no caps'})")
        st = caps.get_structure(0)
        w, h = st.get_value("width"), st.get_value("height")
        self._src_size = (w, h)
        _x, _y, rw, rh = self.rect
        ok = any(abs(w - round(rw * s)) <= 2 and abs(h - round(rh * s)) <= 2 for s in (1, 1.25, 1.5, 1.75, 2, 2.5, 3))
        if not ok and not self.monitor_connector:
            self._teardown()
            raise RecordingError(f"PipeWire delivered {w}x{h} frames for a {rw}x{rh} region: not the screen "
                                 "stream, so nothing was recorded")

    def _on_error(self, _bus, msg) -> None:
        err, dbg = msg.parse_error()
        self._fail(f"{err.message} ({dbg})" if dbg else err.message)

    def _on_eos(self, _bus, _msg) -> None:
        # stop() takes its own EOS off the bus after removing the watch: one seen here came early.
        self._fail("the recorded stream ended before the recording was stopped")

    def _fail(self, why: str) -> None:
        """The source or pipeline died mid-recording: end it now, loudly. Never let pipewiresrc go on
        with whatever other node it could find, and never keep a partial file that looks complete."""
        if self.state in ("finishing", "done", "aborted", "failed"):
            return
        starting = self.state == "idle"
        self.error = why
        self.state = "failed"
        log.error("recording failed: %s", why)
        if starting:  # start() is still checking the source and raises with this error
            return
        self._teardown()
        self._remove_files()
        if self.on_failed is not None:
            self.on_failed(why)

    def _valves(self) -> list:
        return [v for v in (self.pipe.get_by_name("vvalve"), self.pipe.get_by_name("avalve")) if v is not None]

    def pause(self) -> None:
        """Live sources must keep PLAYING (a PAUSED pipewiresrc never gets the stop's EOS through):
        drop buffers at the valves instead."""
        if self.state == "recording" and self.pipe is not None:
            for v in self._valves():
                v.set_property("drop", True)
            self._paused_at = time.monotonic()
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused" and self.pipe is not None:
            gap = time.monotonic() - self._paused_at
            self._paused_total += gap
            # wall time, not the pipeline clock: pipewiresrc provides that clock and it does not
            # track real time between frames (a 2 s pause measured as ~1 s)
            self._paused_ns += int(gap * Gst.SECOND)
            for v in self._valves():
                v.get_static_pad("src").set_offset(-self._paused_ns)  # close the gap in running time
                v.set_property("drop", False)
            self.state = "recording"

    def _teardown(self) -> None:
        if self._watchdog_src:
            GLib.source_remove(self._watchdog_src)
            self._watchdog_src = 0
        if self.pipe is not None:
            if self._watching:
                self.pipe.get_bus().remove_signal_watch()
                self._watching = False
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None
        self._stop_screencast()

    def stop(self, timeout: float = 20.0) -> str:
        """Finish the file; returns the final path. Blocks until written (call off the main loop)."""
        if self.state == "failed":
            raise RecordingError(f"the recording failed: {self.error}")
        if self.pipe is None:
            raise RecordingError("not recording")
        if self.state == "paused":
            self.resume()
        self.state = "finishing"
        self._stopped_at = time.monotonic()
        # The bus signal watch (dispatched by the app's main loop) would consume the EOS message
        # before timed_pop_filtered sees it: stop watching first, then wait for EOS here.
        self.pipe.get_bus().remove_signal_watch()
        self._watching = False
        self.pipe.send_event(Gst.Event.new_eos())
        msg = self.pipe.get_bus().timed_pop_filtered(int(timeout * Gst.SECOND),
                                                    Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self.error = err.message
        self._teardown()
        if not os.path.exists(self.record_path) or os.path.getsize(self.record_path) == 0:
            self.state = "failed"
            raise RecordingError(f"no recording was written ({self.error or 'empty file'})")
        try:
            self._postprocess()
        finally:
            if self.record_path != self.out_path and os.path.exists(self.record_path):
                os.unlink(self.record_path)
        self.state = "done"
        return self.out_path

    def _postprocess(self) -> None:
        Path(self.out_path).parent.mkdir(parents=True, exist_ok=True)
        if self.ep.record_extension == self.ep.extension:
            os.replace(self.record_path, self.out_path)
            return
        if self.gif:
            palette = tempfile.mktemp(prefix="sharel-palette-", suffix=".png")
            try:
                for cmd in P.gif_commands(self.record_path, self.out_path, palette, self.gif_fps, self.opts):
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if r.returncode != 0:
                        raise RecordingError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")
            finally:
                if os.path.exists(palette):
                    os.unlink(palette)
            return
        cmd = P.convert_command(self.record_path, self.out_path, self.ep, self.opts)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RecordingError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")

    def abort(self) -> None:
        self._teardown()
        self._remove_files()
        self.state = "aborted"

    def _remove_files(self) -> None:
        for p in {self.record_path, self.out_path}:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
