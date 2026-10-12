"""Jobs whose effect test_ui_jobs.py only triggered: StopUploads, PinToScreen, ExitShareX,
ScrollingCapture and Ruler, each dispatched as a hotkey would and checked for what it did."""
from __future__ import annotations

import threading
import time

import responses
from PIL import Image
from test_ui_jobs import drive_region, fake_desktop  # noqa: F401  (the fixture)
from uihelpers import run_app

from sharel.core import clipboard as clip
from sharel.core.enums import HotkeyType as H
from sharel.core.enums import RegionCaptureMode, TextDestination
from sharel.core.jobs import dispatch
from sharel.core.serialize import to_jsonable
from sharel.core.tasks import TaskStatus
from sharel.uploaders.custom.model import CustomUploaderItem


def test_stop_uploads_stops_running_and_queued_tasks(fake_desktop):  # noqa: F811
    release = threading.Event()
    started = threading.Event()

    def slow(request):  # the host holds the first upload until the test lets it go
        started.set()
        release.wait(10)
        return 200, {}, '{"u": "https://up.example/t"}'

    def body(app):
        messages = []
        app.message = lambda summary, body="", urgent=False: messages.append((summary, body))
        app.store.uploaders.custom_uploaders = [to_jsonable(CustomUploaderItem(
            name="slow", destination_type=["TextUploader"], request_url="https://up.example/upload",
            body="MultipartFormData", file_form_name="file", url="{json:u}"))]
        app.tasks.set_upload_limit(1)
        ts = app.store.task_settings_for(None)
        ts.text_destination = TextDestination.CustomTextUploader
        first = app.tasks.upload_text("one", ts)
        second = app.tasks.upload_text("two", ts)  # queued behind the upload limit
        assert started.wait(5)
        assert second.info.status not in (TaskStatus.Stopped, TaskStatus.Completed)
        dispatch(app, H.StopUploads, source="hotkey")
        assert messages[-1] == ("Uploads stopped", "2 task(s) stopped.")
        release.set()
        for _ in range(200):
            if first.done.is_set() and second.done.is_set():
                break
            yield 0.05
        assert second.info.status == TaskStatus.Stopped  # never started
        assert first.info.status == TaskStatus.Stopped and first.info.result_url == ""  # its answer is dropped
        return True
    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "https://up.example/upload", callback=slow)
        assert run_app(body)


def test_pin_to_screen_offers_the_sources_and_pins_the_choice(fake_desktop, monkeypatch):  # noqa: F811
    from gi.repository import Adw
    shown = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent=None: shown.append(self))

    def body(app):
        from sharel.ui.windows import pin as P
        import sharel.ui.jobs.tools as T
        T.clipboard.read = lambda: clip.ClipboardContent("image", image=Image.new("RGBA", (33, 22), "green"))
        dispatch(app, H.PinToScreen)
        assert len(shown) == 1
        d = shown[0]
        assert [d.get_response_label(r) for r in ("screen", "clipboard", "file", "cancel")] == \
            ["Screen region", "Clipboard", "File…", "Cancel"]
        assert P.PINS == []
        d.emit("response", "clipboard")
        yield 0.3
        assert [p.image.size for p in P.PINS] == [(33, 22)]
        P.close_all_pins()
        return True
    assert run_app(body)


def test_exit_sharex_quits_the_app(fake_desktop):  # noqa: F811
    reached = []

    def body(app):
        dispatch(app, H.ExitShareX, source="hotkey")
        yield 20  # the app is gone long before this
        reached.append(True)
        return True
    t = time.monotonic()
    assert run_app(body, timeout=30) is None  # quit before the body finished
    assert not reached and time.monotonic() - t < 10


def test_scrolling_capture_job_opens_the_capture_window_for_the_selection(fake_desktop):  # noqa: F811
    def body(app):
        dispatch(app, H.ScrollingCapture)
        yield 0.2
        assert app.region_session is not None
        drive_region(app, 100, 120, 400, 420)
        yield 0.3
        from sharel.ui.windows.scrolling import _state
        win = _state["win"]
        assert win.get_mapped() and win.rect[2:] in ((300, 300), (301, 301)) and win.rect[:2] == (100, 120)
        win.destroy()
        return True
    assert run_app(body)


def test_ruler_job_opens_the_ruler_overlay(fake_desktop):  # noqa: F811
    def body(app):
        assert app.region_session is None
        dispatch(app, H.Ruler)
        yield 0.2
        s = app.region_session
        assert s is not None and s.manager.mode == RegionCaptureMode.Ruler
        s.manager.press(10, 10)
        s.manager.motion(40, 50)
        s.manager.release(40, 50)
        assert "Distance: 50.00 px" in s.manager.area_text(s.manager.shapes[0].rect)
        s.views[0]._finish(s.manager.key("Escape"))
        assert app.region_session is None
        return True
    assert run_app(body)


def test_custom_region_recording_and_abort(fake_desktop, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SHAREL_RECORD_TEST_PATTERN", "1")
    from sharel.core.config import Rect
    from sharel.record.controller import CONTROLLER
    from test_ui_jobs import ffprobe

    def settle():
        for _ in range(400):
            if not CONTROLLER.active and not CONTROLLER._finishing.is_set():
                return
            yield 0.05
        raise AssertionError("the recording did not finish")

    def body(app):
        ts = app.store.app.default_task_settings
        ts.capture_settings.screen_recording.screen_record_fps = 15
        ts.capture_settings.capture_custom_region = Rect(0, 0, 200, 100)
        folder = app.store.screenshots_folder(ts)
        dispatch(app, H.ScreenRecorderCustomRegion)  # straight to the stored region: no overlay
        yield 1.0
        assert CONTROLLER.active and app.region_session is None
        dispatch(app, H.StopScreenRecording)
        yield from settle()
        v = next(s for s in ffprobe(CONTROLLER.last_output)["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (200, 100)
        before = set(folder.rglob("*")) if folder.exists() else set()
        dispatch(app, H.ScreenRecorderCustomRegion)
        yield 1.0
        assert CONTROLLER.active
        dispatch(app, H.AbortScreenRecording)
        yield 1.0
        assert not CONTROLLER.active and CONTROLLER.control is None
        after = set(folder.rglob("*")) if folder.exists() else set()
        assert after == before  # aborted: nothing written, no .part left behind
        return True
    assert run_app(body, timeout=60)
