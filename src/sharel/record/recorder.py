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
        self.record_path = out_path if self.ep.record_extension == self.ep.extension else \
            tempfile.mktemp(prefix="sharel-rec-", suffix=f".{self.ep.record_extension}")
        self.pipe: Gst.Pipeline | None = None
        self._session = ""
        self._portal = None
        self.started_at = 0.0
        self._paused_total = 0.0
        self._paused_at = 0.0
        self.state = "idle"  # idle | recording | paused | finishing | done | aborted | failed
        self.error = ""

    # -- elapsed --

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        now = self._paused_at if self.state == "paused" else time.monotonic()
        return max(0.0, now - self.started_at - self._paused_total)

    # -- session --

    def _start_screencast(self) -> int:
        from ..capture.backends import _PrivateLoop
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node: dict = {}
        got = threading.Event()
        props = {"cursor-mode": GLib.Variant("u", 1 if self.cursor else 0)}
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
        return node["id"]

    def _stop_screencast(self) -> None:
        if self._session:
            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                conn.call_sync(SC, self._session, SC + ".Session", "Stop", None, None, Gio.DBusCallFlags.NONE, 3000,
                               None)
            except GLib.Error:
                pass
            self._session = ""
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
        x, y, w, h = self.rect
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
        bus.connect("message::error", self._on_error)
        ret = self.pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RecordingError("the recording pipeline could not start")
        self.started_at = time.monotonic()
        self.state = "recording"

    def _on_error(self, _bus, msg) -> None:
        err, dbg = msg.parse_error()
        self.error = f"{err.message} ({dbg})" if dbg else err.message
        log.error("recording error: %s", self.error)
        self.state = "failed"

    def pause(self) -> None:
        if self.state == "recording" and self.pipe is not None:
            self.pipe.set_state(Gst.State.PAUSED)
            self._paused_at = time.monotonic()
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused" and self.pipe is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self.pipe.set_state(Gst.State.PLAYING)
            self.state = "recording"

    def _teardown(self) -> None:
        if self.pipe is not None:
            self.pipe.get_bus().remove_signal_watch()
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None
        self._stop_screencast()

    def stop(self, timeout: float = 20.0) -> str:
        """Finish the file; returns the final path. Blocks until written (call off the main loop)."""
        if self.pipe is None:
            raise RecordingError("not recording")
        if self.state == "paused":
            self.resume()
        self.state = "finishing"
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
        if self.record_path == self.out_path:
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
        for p in {self.record_path, self.out_path}:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        self.state = "aborted"
