"""The recorder must record the ScreenCast stream it asked for, or fail loudly.

Regression: the recorder once held its D-Bus connection only in a local. GLib caches the session bus
weakly, so the connection closed when setup returned, Mutter ended the session for the vanished
caller, and pipewiresrc silently autoconnected to another video node (a webcam)."""
import gc
import os
import threading
import time

import gi
import pytest

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst

from sharel.core.config import FFmpegOptions
from sharel.record.recorder import Recorder, RecordingError

SC = "org.gnome.Mutter.ScreenCast"
XML = f"""<node>
<interface name="{SC}">
  <method name="CreateSession"><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/></method>
</interface>
<interface name="{SC}.Session">
  <method name="RecordArea"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="i" direction="in"/><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/></method>
  <method name="RecordMonitor"><arg type="s" direction="in"/><arg type="a{{sv}}" direction="in"/>
    <arg type="o" direction="out"/></method>
  <method name="Start"/>
  <method name="Stop"/>
  <signal name="Closed"/>
</interface>
<interface name="{SC}.Stream"><signal name="PipeWireStreamAdded"><arg type="u"/></signal></interface>
</node>"""


class FakeMutterScreenCast:
    """Just enough of Mutter's ScreenCast, on its own bus connection and thread. Like Mutter, it ends a
    session when the caller's bus name vanishes."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self._ready = threading.Event()
        self._n = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self._ready.wait(5), "fake ScreenCast service did not start"

    def _run(self) -> None:
        self.ctx = GLib.MainContext.new()
        self.ctx.push_thread_default()
        addr = os.environ["DBUS_SESSION_BUS_ADDRESS"]
        self.conn = Gio.DBusConnection.new_for_address_sync(
            addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.conn.register_object("/org/gnome/Mutter/ScreenCast", self.info.interfaces[0], self._call)
        self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                            GLib.Variant("(su)", (SC, 4)), None, Gio.DBusCallFlags.NONE, 5000, None)
        self.loop = GLib.MainLoop.new(self.ctx, False)
        self._ready.set()
        self.loop.run()

    def _call(self, conn, sender, path, iface, method, params, inv) -> None:
        if method == "CreateSession":
            self._n += 1
            base = "/org/gnome/Mutter/ScreenCast"
            sp, st = f"{base}/Session/u{self._n}", f"{base}/Stream/u{self._n}"
            self.sessions[sp] = {"sender": sender, "alive": True, "stream": st, "started": False}
            conn.register_object(sp, self.info.interfaces[1], self._call)
            Gio.bus_watch_name_on_connection(conn, sender, Gio.BusNameWatcherFlags.NONE, None,
                                             lambda *_a, sp=sp: self._vanished(sp))
            return inv.return_value(GLib.Variant("(o)", (sp,)))
        s = self.sessions[path]
        if method in ("RecordArea", "RecordMonitor"):
            return inv.return_value(GLib.Variant("(o)", (s["stream"],)))
        if method == "Start":
            s["started"] = True
            inv.return_value(None)
            conn.emit_signal(None, s["stream"], SC + ".Stream", "PipeWireStreamAdded", GLib.Variant("(u)", (4242,)))
            return None
        if method == "Stop":
            s["alive"] = False
            return inv.return_value(None)
        return None

    def _vanished(self, sp: str) -> None:
        self.sessions[sp]["alive"] = False  # what Mutter does when the caller goes away

    def close_session(self, sp: str) -> None:
        def go():
            self.sessions[sp]["alive"] = False
            self.conn.emit_signal(None, sp, SC + ".Session", "Closed", None)
            return False
        self.ctx.invoke_full(GLib.PRIORITY_DEFAULT, go)

    def stop(self) -> None:
        """Gone without a word, like a crashed Mutter: its bus name just vanishes."""
        if self.thread.is_alive():
            self.ctx.invoke_full(GLib.PRIORITY_DEFAULT,
                                 lambda: (self.conn.close_sync(None), self.loop.quit(), False)[2])
            self.thread.join(5)


@pytest.fixture
def mutter():
    svc = FakeMutterScreenCast()
    yield svc
    svc.stop()


def spin(pred, timeout=5.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_recorder_holds_its_screencast_connection_for_the_whole_recording(mutter, tmp_path):
    rec = Recorder((10, 10, 400, 300), str(tmp_path / "r.mp4"), FFmpegOptions())
    assert rec._start_screencast() == 4242
    (s,) = mutter.sessions.values()
    assert s["started"]
    gc.collect()
    spin(lambda: False, 0.5)  # let a vanished caller's NameOwnerChanged arrive
    assert s["alive"], "the ScreenCast session died once setup returned: the connection was not kept"
    assert rec._conn is not None and not rec._conn.is_closed()
    assert rec._conn.get_unique_name() == s["sender"]
    rec._stop_screencast()
    assert rec._conn is None and rec._session == ""
    assert not s["alive"]  # stopped explicitly, not dropped


def test_abort_releases_the_connection(mutter, tmp_path):
    rec = Recorder((10, 10, 400, 300), str(tmp_path / "r.mp4"), FFmpegOptions())
    rec._start_screencast()
    assert rec._conn is not None
    rec.abort()
    assert rec._conn is None and rec.state == "aborted"
    assert not next(iter(mutter.sessions.values()))["alive"]


def _fake_source(width, height):
    rec = Recorder((10, 10, 400, 300), "/nonexistent/never.mp4", FFmpegOptions())
    rec.pipe = Gst.parse_launch(f"videotestsrc name=pwsrc is-live=true ! video/x-raw,width={width},height={height}"
                                " ! fakesink")
    rec.pipe.set_state(Gst.State.PLAYING)
    return rec


def test_check_source_refuses_a_stream_of_another_size():
    Gst.init(None)
    rec = _fake_source(640, 480)  # a webcam's size, not the 400x300 region
    with pytest.raises(RecordingError, match="640x480 frames for a 400x300 region"):
        rec._check_source(timeout_s=3)
    assert rec.pipe is None  # torn down: nothing goes on recording it


@pytest.mark.parametrize("size", [(400, 300), (800, 600), (600, 450)])
def test_check_source_accepts_the_region_at_monitor_scales(size):
    Gst.init(None)
    rec = _fake_source(*size)
    try:
        rec._check_source(timeout_s=3)
        assert rec.pipe is not None
    finally:
        rec._teardown()


def test_check_source_reports_a_pipeline_error_at_once():
    Gst.init(None)
    rec = Recorder((10, 10, 400, 300), "/nonexistent/never.mp4", FFmpegOptions())
    rec.pipe = Gst.parse_launch("appsrc name=pwsrc is-live=true ! fakesink")  # running, but no frames yet
    rec.pipe.set_state(Gst.State.PLAYING)
    src = rec.pipe.get_by_name("pwsrc")
    rec.pipe.get_bus().post(Gst.Message.new_error(src, GLib.Error.new_literal(Gst.ResourceError.quark(),
                                                                                "target not found", 3), "dbg"))
    t = time.monotonic()
    with pytest.raises(RecordingError, match="target not found"):
        rec._check_source(timeout_s=5)
    assert time.monotonic() - t < 1.0


def _recording(tmp_path):
    out = tmp_path / "m.mp4"
    rec = Recorder((0, 0, 320, 240), str(out), FFmpegOptions(), fps=15, test_pattern=True)
    failures: list[str] = []
    rec.on_failed = failures.append
    rec.start()
    spin(lambda: False, 1.0)  # running, with frames muxed
    return rec, out, failures


@pytest.mark.parametrize("how", ["error", "eos"])
def test_source_dying_mid_recording_fails_loudly(tmp_path, how):
    rec, out, failures = _recording(tmp_path)
    src = rec.pipe.get_by_name("pwsrc") or rec.pipe.get_by_name("src") or rec.pipe
    if how == "error":
        rec.pipe.get_bus().post(Gst.Message.new_error(src, GLib.Error.new_literal(Gst.ResourceError.quark(),
                                                                                    "stream gone", 1), None))
    else:
        rec.pipe.send_event(Gst.Event.new_eos())  # the source ended on its own, before stop()
    assert spin(lambda: rec.state == "failed"), rec.state
    assert len(failures) == 1 and failures[0] == rec.error
    assert rec.pipe is None  # stopped at once, not left running on whatever else
    assert not out.exists() and not os.path.exists(rec.record_path)  # no partial file left behind
    with pytest.raises(RecordingError, match="the recording failed"):
        rec.stop()


def test_screencast_session_closed_mid_recording_fails(mutter, tmp_path):
    rec, out, failures = _recording(tmp_path)
    rec._start_screencast()  # the session the recording depends on
    (sp, _s), = mutter.sessions.items()
    mutter.close_session(sp)
    assert spin(lambda: rec.state == "failed"), rec.state
    assert failures == ["the ScreenCast session was closed"]
    assert rec.pipe is None and rec._conn is None
    assert not out.exists() and not os.path.exists(rec.record_path)


def test_normal_stop_is_not_a_failure(tmp_path):
    rec, out, failures = _recording(tmp_path)
    time.sleep(0.5)
    # while recording, only the .part file exists: a half-written video never has the real name
    assert not out.exists() and os.path.exists(rec.record_path)
    assert rec.stop() == str(out)
    assert not os.path.exists(rec.record_path)
    spin(lambda: False, 0.3)
    assert failures == [] and rec.state == "done" and out.stat().st_size > 0


def _guarded(launch, stall_s=0.6):
    """A Recorder past its source check, with the mid-recording guards on a stand-in pwsrc."""
    Gst.init(None)
    rec = Recorder((10, 10, 400, 300), "/nonexistent/never.mp4", FFmpegOptions())
    rec.pipe = Gst.parse_launch(launch)
    rec.pipe.set_state(Gst.State.PLAYING)
    if "videotestsrc" in launch:
        rec._check_source(timeout_s=3)
    rec._src_size = rec._src_size or (400, 300)
    rec.stall_timeout_s = stall_s
    failures: list[str] = []
    rec.on_failed = failures.append
    rec._guard_source()
    rec.state = "recording"
    return rec, failures


def test_a_stalled_stream_fails_the_recording():
    # what pipewiresrc does when its node is removed: no error, no EOS, just no more frames
    rec, failures = _guarded("appsrc name=pwsrc is-live=true format=time ! fakesink")
    assert spin(lambda: rec.state == "failed", 3)
    assert failures and "stopped delivering frames" in failures[0]
    assert rec.pipe is None


def test_a_live_stream_is_not_mistaken_for_a_stall():
    rec, failures = _guarded("videotestsrc name=pwsrc is-live=true ! video/x-raw,width=400,height=300,"
                             "framerate=5/1 ! fakesink")
    try:
        spin(lambda: False, 1.5)  # 2.5x the stall timeout
        assert rec.state == "recording" and failures == []
    finally:
        rec._teardown()


def test_a_stream_renegotiated_to_another_size_fails():
    rec, failures = _guarded("videotestsrc name=pwsrc is-live=true ! capsfilter name=cf "
                             "caps=video/x-raw,width=400,height=300 ! fakesink", stall_s=30)
    spin(lambda: False, 0.3)
    assert rec.state == "recording"
    rec.pipe.get_by_name("cf").set_property("caps", Gst.Caps.from_string("video/x-raw,width=640,height=480"))
    assert spin(lambda: rec.state == "failed", 3)
    assert failures and "640x480" in failures[0]


def test_mutter_vanishing_mid_recording_fails(mutter, tmp_path):
    rec, out, failures = _recording(tmp_path)
    rec._start_screencast()
    mutter.stop()  # no Closed signal: the service is simply gone
    assert spin(lambda: rec.state == "failed"), rec.state
    assert failures == ["Mutter's ScreenCast service went away"]
    assert rec.pipe is None and not os.path.exists(rec.record_path) and not out.exists()
