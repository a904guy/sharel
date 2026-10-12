"""Every job dispatched the way a hotkey/CLI/tray would (core.jobs.dispatch), inside the sandbox.

The desktop is simulated: captures come from synthetic images, the GNOME Shell extension is a fake
that records calls, recordings use GStreamer's test source. Each test asserts what the job did.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from uihelpers import run_app

from sharel.capture import backends
from sharel.capture.backends import Capture
from sharel.core import clipboard as clip
from sharel.core.config import Rect
from sharel.core.enums import AfterCaptureTasks as ACT
from sharel.core.enums import HotkeyType as H
from sharel.core.jobs import dispatch
from sharel.shell.client import Window

SCREEN = (0, 0, 1600, 1000)


def desktop_image(w=1600, h=1000):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (np.arange(w) % 256)[None, :]
    a[..., 1] = (np.arange(h) % 256)[:, None]
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


DESK = desktop_image()
EDITOR = Window(id=42, title="Notes - Text Editor", wm_class="org.gnome.TextEditor", pid=1, frame=(100, 120, 300, 200),
                buffer=(90, 110, 320, 220), client_rect=(100, 120, 300, 200), stacking=1)


class FakeShell:
    available = True

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
            return {"get_window_details": {"borderless": ""}, "toggle_above": True, "make_borderless": {"ok": True},
                    "find_windows": [], "get_windows": [EDITOR], "get_active_window": EDITOR,
                    "get_pointer": (50, 50, 0), "pick_window": EDITOR, "toggle_tray_menu": True,
                    "list_accelerators": [], "get_monitors": [], "clipboard_mimetypes": []}.get(name, True)
        return rec


@pytest.fixture
def fake_desktop(monkeypatch):
    shell = FakeShell()
    import sharel.shell.client as sc
    for mod in ("sharel.shell.client", "sharel.ui.jobs.capture", "sharel.ui.jobs.tools", "sharel.capture.backends",
                "sharel.capture.region", "sharel.ui.placement", "sharel.record.controller", "sharel.core.clipboard"):
        m = __import__(mod, fromlist=["shell"])
        if hasattr(m, "shell"):
            monkeypatch.setattr(m, "shell", lambda: shell)
    monkeypatch.setattr(sc.ShellClient, "_instance", shell)

    def area(rect, cursor=False, preferred="Auto"):
        x, y, w, h = rect
        return Capture(DESK.crop((x, y, x + w, y + h)), tuple(rect), 1.0, backend="test")
    monkeypatch.setattr(backends, "capture_screen", lambda cursor=False, preferred="Auto": Capture(DESK.copy(), SCREEN,
                                                                                                   1.0))
    monkeypatch.setattr(backends, "capture_area", area)
    monkeypatch.setattr(backends, "capture_monitor", lambda i, cursor=False, preferred="Auto":
                        Capture(DESK.crop((0, 0, 800, 1000)), (0, 0, 800, 1000), 1.0))
    monkeypatch.setattr(backends, "capture_window", lambda win, *a, **k:
                        area(win.frame) if win else Capture(DESK.copy(), SCREEN, 1.0))
    copied = {"text": [], "image": [], "files": []}
    monkeypatch.setattr(clip, "set_text", lambda t: copied["text"].append(t) or "test")
    monkeypatch.setattr(clip, "set_image", lambda i: copied["image"].append(i.size) or "test")
    monkeypatch.setattr(clip, "set_files", lambda p: copied["files"].append(list(p)) or "test")
    shell.copied = copied
    return shell


def save_only(app):
    ts = app.store.app.default_task_settings
    ts.after_capture_job = ACT.SaveImageToFile
    ts.image_settings.image_auto_use_jpeg = False


def wait_files(app, n, timeout=10):
    root = app.store.screenshots_folder(app.store.app.default_task_settings).parent
    for _ in range(int(timeout / 0.05)):
        files = sorted(p for p in root.rglob("*") if p.is_file())
        if len(files) >= n:
            return files
        yield 0.05
    raise AssertionError(f"expected {n} file(s) in {root}")


def drive_region(app, x0, y0, x1, y1):
    s = app.region_session
    assert s is not None, "region capture did not open"
    s.manager.press(x0, y0)
    s.manager.motion(x1, y1)
    s.views[0]._finish(s.manager.release(x1, y1))


def sizes(paths):
    out = []
    for p in paths:
        with Image.open(p) as im:
            out.append(im.size)
    return out


def test_capture_jobs(fake_desktop):
    def body(app):
        save_only(app)
        dispatch(app, H.PrintScreen, source="hotkey")
        files = yield from wait_files(app, 1)
        dispatch(app, H.ActiveMonitor)
        files = yield from wait_files(app, 2)
        dispatch(app, H.ActiveWindow)
        files = yield from wait_files(app, 3)
        assert sorted(sizes(files)) == sorted([(1600, 1000), (800, 1000), (300, 200)])
        assert any(p.name.startswith("gnome-text-editor") or p.name.startswith("org.gnome.texteditor")
                   or "_" in p.name for p in files)
        # region capture: the overlay, then LastRegion repeats it
        dispatch(app, H.RectangleRegion)
        yield 0.2
        drive_region(app, 200, 150, 450, 350)
        files = yield from wait_files(app, 4)
        dispatch(app, H.LastRegion)
        files = yield from wait_files(app, 5)
        assert sizes(files).count((250, 200)) + sizes(files).count((251, 201)) == 2
        # custom region: first selection is remembered, then captured directly
        dispatch(app, H.CustomRegion)
        yield 0.2
        drive_region(app, 10, 20, 110, 70)
        files = yield from wait_files(app, 6)  # the selection is stored, then captured once
        assert not app.store.app.default_task_settings.capture_settings.capture_custom_region.empty
        assert sizes(files).count((100, 50)) + sizes(files).count((101, 51)) == 1
        # custom window: picks a window through the extension and remembers its title
        dispatch(app, H.CustomWindow)
        files = yield from wait_files(app, 7)
        assert app.store.app.default_task_settings.capture_settings.capture_custom_window == EDITOR.title
        assert ("pick_window", ("Click the window to capture. Esc to cancel.",)) in fake_desktop.calls
        return True
    assert run_app(body, timeout=60)


def test_auto_capture_start_stop(fake_desktop):
    def body(app):
        save_only(app)
        o = app.store.app.default_task_settings.capture_settings.auto_capture
        o.capture_fullscreen, o.repeat_time = True, 0.5
        dispatch(app, H.StartAutoCapture)
        yield 1.3
        dispatch(app, H.StopAutoCapture)
        from sharel.capture.autocapture import AUTO
        assert not AUTO.running and AUTO.count >= 2
        files = yield from wait_files(app, 2)
        assert all(s == (1600, 1000) for s in sizes(files))
        dispatch(app, H.AutoCapture)
        yield 0.2
        from sharel.ui.windows.autocapture import _state
        assert _state["win"].get_mapped()
        return True
    assert run_app(body, timeout=30)


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_recording_jobs(fake_desktop, monkeypatch):
    monkeypatch.setenv("SHAREL_RECORD_TEST_PATTERN", "1")
    from sharel.record.controller import CONTROLLER

    def finished():
        for _ in range(400):
            if not CONTROLLER.active and not CONTROLLER._finishing.is_set():
                return
            yield 0.05
        raise AssertionError("the recording did not finish within 20 s")

    def body(app):
        ts = app.store.app.default_task_settings
        ts.after_capture_job = ACT.SaveImageToFile | ACT.CopyFilePathToClipboard
        ts.capture_settings.screen_recording.screen_record_fps = 15
        # ScreenRecorder: select a region, record, the same hotkey stops it
        dispatch(app, H.ScreenRecorder)
        yield 0.2
        drive_region(app, 0, 0, 320, 240)
        yield 1.6
        assert CONTROLLER.active and CONTROLLER.control is not None
        assert any(c[0] == "show_border" for c in fake_desktop.calls)
        dispatch(app, H.ScreenRecorder)
        yield from finished()
        mp4 = Path(CONTROLLER.last_output)
        info = ffprobe(mp4)
        assert mp4.suffix == ".mp4" and 1.0 <= float(info["format"]["duration"]) <= 3.0
        assert any(c[0] == "hide_border" for c in fake_desktop.calls)
        for _ in range(100):
            if str(mp4) in fake_desktop.copied["text"]:
                break
            yield 0.05
        assert str(mp4) in fake_desktop.copied["text"]  # CopyFilePathToClipboard on the recording
        assert any(h.file_path == str(mp4) for h in app.history.items())
        # GIF for the active window, pause in the middle
        dispatch(app, H.ScreenRecorderGIFActiveWindow)
        yield 0.8
        dispatch(app, H.PauseScreenRecording)
        assert CONTROLLER.paused
        yield 0.8
        dispatch(app, H.PauseScreenRecording)
        yield 0.8
        dispatch(app, H.StopScreenRecording)
        yield from finished()
        gif = Path(CONTROLLER.last_output)
        g = ffprobe(gif)
        v = next(s for s in g["streams"] if s["codec_type"] == "video")
        assert gif.suffix == ".gif" and (v["width"], v["height"]) == (300, 200)
        # custom region + last region variants, and abort
        ts.capture_settings.capture_custom_region = Rect(0, 0, 200, 100)
        dispatch(app, H.ScreenRecorderCustomRegion)
        yield 0.8
        dispatch(app, H.AbortScreenRecording)
        yield 0.2
        assert not CONTROLLER.active
        dispatch(app, H.StartScreenRecorder)  # last region (320x240 from above)
        yield 1.2
        dispatch(app, H.StartScreenRecorder)
        yield from finished()
        last = ffprobe(CONTROLLER.last_output)
        v = next(s for s in last["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (320, 240)
        dispatch(app, H.ScreenRecorderGIFCustomRegion)
        yield 0.8
        dispatch(app, H.StopScreenRecording)
        yield from finished()
        v = next(s for s in ffprobe(CONTROLLER.last_output)["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (200, 100)
        dispatch(app, H.StartScreenRecorderGIF)
        yield 0.8
        dispatch(app, H.StartScreenRecorderGIF)
        yield from finished()
        assert CONTROLLER.last_output.endswith(".gif")
        dispatch(app, H.ScreenRecorderActiveWindow)
        yield 0.8
        dispatch(app, H.ScreenRecorderActiveWindow)
        yield from finished()
        v = next(s for s in ffprobe(CONTROLLER.last_output)["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (300, 200)
        dispatch(app, H.ScreenRecorderGIF)
        yield 0.2
        drive_region(app, 0, 0, 160, 120)
        yield 0.8
        dispatch(app, H.ScreenRecorderGIF)
        yield from finished()
        assert CONTROLLER.last_output.endswith(".gif")
        return True
    assert run_app(body, timeout=120)


def test_upload_jobs(fake_desktop, tmp_path):
    def body(app):
        calls = []
        app.tasks.upload_file = lambda path, ts: calls.append(("file", Path(path).name))
        app.tasks.upload_text = lambda text, ts: calls.append(("text", text))
        app.tasks.upload_data = lambda data, name, ts: calls.append(("data", name))
        app.tasks.shorten_url = lambda url, ts: calls.append(("shorten", url))
        app.tasks.share_url = lambda url, ts: calls.append(("share", url))
        app.tasks.download_upload = lambda url, ts: calls.append(("download", url))
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.png").write_bytes(b"x")
        dispatch(app, H.FileUpload, files=[str(tmp_path / "a.txt")], source="cli")
        dispatch(app, H.FolderUpload, files=[str(tmp_path)])
        assert calls[:3] == [("file", "a.txt"), ("file", "a.txt"), ("file", "b.png")]
        calls.clear()
        ts = app.store.task_settings_for(None)
        # clipboard upload: each content kind goes to the right place
        for content, expect in ((clip.ClipboardContent("text", text="hello"), ("text", "hello")),
                                (clip.ClipboardContent("files", files=[str(tmp_path / "a.txt")]), ("file", "a.txt")),
                                (clip.ClipboardContent("image", image=Image.new("RGBA", (5, 5))), "data")):
            calls.clear()
            app.clipboard_upload(ts, content)
            assert calls and (calls[0] == expect or calls[0][0] == expect), (content.kind, calls)
        calls.clear()
        url = clip.ClipboardContent("text", text="https://example.com/x.png")
        ts.upload_settings.clipboard_upload_shorten_url = True
        app.clipboard_upload(ts, url)
        ts.upload_settings.clipboard_upload_shorten_url = False
        ts.upload_settings.clipboard_upload_url_contents = True
        app.clipboard_upload(ts, url)
        ts.upload_settings.clipboard_upload_url_contents = False
        ts.upload_settings.clipboard_upload_share_url = True
        app.clipboard_upload(ts, url)
        assert calls == [("shorten", "https://example.com/x.png"), ("download", "https://example.com/x.png"),
                         ("share", "https://example.com/x.png")]
        # the job reads the real clipboard module
        import sharel.ui.jobs.upload as up
        up.clipboard.read = lambda: clip.ClipboardContent("text", text="from job")
        calls.clear()
        dispatch(app, H.ClipboardUpload)
        assert calls == [("text", "from job")]
        # UploadText / DragDropUpload windows
        dispatch(app, H.UploadText)
        from sharel.ui.windows import dropwindow, uploadtext
        w = uploadtext._open["win"]
        w._view.get_buffer().set_text("typed")
        w._upload.emit("clicked")
        dispatch(app, H.DragDropUpload)
        assert dropwindow._open["win"]._on_drop(None, "dropped", 0, 0)
        assert calls[-2:] == [("text", "typed"), ("text", "dropped")]
        # StopUploads cancels running tasks
        dispatch(app, H.StopUploads)
        yield 0.1
        return True
    assert run_app(body)


def test_url_jobs_prompt_and_run(fake_desktop, monkeypatch):
    import sharel.ui.gtkutil as gu

    def body(app):
        calls = []
        app.tasks.shorten_url = lambda url, ts: calls.append(("shorten", url))
        app.tasks.download_upload = lambda url, ts: calls.append(("download", url))
        monkeypatch.setattr(gu, "prompt_text", lambda parent, heading, default, done, **kw: done(
            "https://example.com/long" if "Shorten" in heading else "https://example.com/file.zip"))
        dispatch(app, H.ShortenURL)
        dispatch(app, H.UploadURL)
        assert calls == [("shorten", "https://example.com/long"), ("download", "https://example.com/file.zip")]
        yield 0
        return True
    assert run_app(body)


def test_tool_and_window_jobs(fake_desktop, tmp_path, monkeypatch):
    from sharel.core import desktop
    opened = []
    monkeypatch.setattr(desktop, "open_folder", lambda p: opened.append(p))
    import sharel.ui.jobs.other as other
    monkeypatch.setattr(other.desktop, "open_folder", lambda p: opened.append(p))

    def body(app):
        # pin from screen / clipboard / file, then close all
        from sharel.ui.windows import pin as P
        dispatch(app, H.PinToScreenFromScreen)
        yield 0.2
        drive_region(app, 0, 0, 120, 80)
        yield 0.3
        import sharel.ui.jobs.tools as T
        T.clipboard.read = lambda: clip.ClipboardContent("image", image=Image.new("RGBA", (30, 20), "red"))
        dispatch(app, H.PinToScreenFromClipboard)
        img = tmp_path / "p.png"
        Image.new("RGBA", (40, 30), "blue").save(img)
        dispatch(app, H.PinToScreenFromFile, files=[str(img)])
        yield 0.3
        assert sorted(p.image.size for p in P.PINS) == [(30, 20), (40, 30), (120, 80)] or \
            sorted(p.image.size for p in P.PINS) == [(30, 20), (40, 30), (121, 81)]
        dispatch(app, H.PinToScreenCloseAll)
        assert P.PINS == []
        dispatch(app, H.PinToScreen)  # the startup chooser opens
        yield 0.2
        # screen colour picker: click copies the formatted colour
        dispatch(app, H.ScreenColorPicker)
        yield 0.2
        s = app.region_session
        s.manager.motion(300, 200)
        res = s.manager.press(300, 200) or s.manager.release(300, 200)
        s.views[0]._finish(res)
        r, g, b, _a = DESK.getpixel((300, 200))
        from sharel.tools.color import Color, format_pixel_info
        expected = format_pixel_info(app.store.app.default_task_settings.tool_settings.screen_color_picker_format,
                                     Color(r, g, b), 300, 200)
        assert fake_desktop.copied["text"][-1] == expected, (fake_desktop.copied["text"], expected)
        # ruler opens and closes with Escape
        dispatch(app, H.Ruler)
        yield 0.2
        assert app.region_session is not None
        app.region_session.views[0]._finish(app.region_session.manager.key("Escape"))
        assert app.region_session is None
        # window tools through the extension
        dispatch(app, H.ActiveWindowTopMost)
        dispatch(app, H.ActiveWindowBorderless)
        dispatch(app, H.MouseHighlighter)
        dispatch(app, H.MouseHighlighter)
        names = [c[0] for c in fake_desktop.calls]
        assert "toggle_above" in names and "make_borderless" in names and names.count("mouse_highlighter") == 2
        # image editor with a file
        from sharel.ui.windows import editor as E
        dispatch(app, H.ImageEditor, files=[str(img)])
        yield 0.3
        assert E.OPEN_EDITORS and E.OPEN_EDITORS[-1].file_path == str(img)
        # other jobs
        dispatch(app, H.OpenMainWindow)
        yield 0.2
        assert app.main_window.get_mapped()
        dispatch(app, H.OpenScreenshotsFolder)
        assert opened and opened[-1].endswith(app.store.screenshots_folder(app.store.app.default_task_settings).name)
        dispatch(app, H.ToggleActionsToolbar)
        from sharel.ui.windows.actionstoolbar import _state
        yield 0.2
        assert _state.get("win") is not None
        dispatch(app, H.ToggleActionsToolbar)
        assert _state.get("win") is None
        app.start_tray()
        dispatch(app, H.ToggleTrayMenu)
        assert ("toggle_tray_menu", ("app.sharel.ShareL",)) in fake_desktop.calls
        dispatch(app, H.ScrollingCapture)  # opens the region overlay to select the scrolling area
        yield 0.2
        assert app.region_session is not None
        app.region_session.views[0]._finish(app.region_session.manager.key("Escape"))
        dispatch(app, H.ExitShareX)
        return True
    assert run_app(body, timeout=40)
