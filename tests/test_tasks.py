from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from sharel.core.config import ExternalProgram, TaskSettings
from sharel.core.enums import (
    AfterCaptureTasks as ACT,
)
from sharel.core.enums import (
    AfterUploadTasks as AUT,
)
from sharel.core.enums import (
    EImageFormat,
    FileDestination,
    FileExistAction,
    ImageDestination,
)
from sharel.core.history import HistoryManager
from sharel.core.nameparser import WindowInfo
from sharel.core.tasks import TaskHost, TaskManager, TaskStatus, format_result
from sharel.uploaders import registry
from sharel.uploaders.base import FileUploader, UploaderError, UploadResult, URLShortener

UPLOADS: list[tuple[str, bytes]] = []


@dataclass
class FakeSettings:
    fail_times: int = 0


class FakeUploader(FileUploader):
    def upload(self, stream, file_name):
        if self.settings.fail_times > 0:
            self.settings.fail_times -= 1
            raise UploaderError("boom")
        data = stream.read()
        self.ctx.report(len(data), len(data))
        UPLOADS.append((file_name, data))
        return UploadResult(url=f"http://fake/{file_name}", deletion_url="http://fake/del")


class FakeShortener(URLShortener):
    def shorten_url(self, url):
        return UploadResult(url=url, shortened_url="http://s/1")


class RecHost(TaskHost):
    def __init__(self):
        self.calls = []

    def copy_image(self, img):
        self.calls.append(("copy_image", img.size))

    def copy_text(self, text):
        self.calls.append(("copy_text", text))

    def pin(self, img):
        self.calls.append(("pin", img.size))

    def notify(self, info, ts, img):
        self.calls.append(("notify", info.status.name))

    def play_sound(self, kind, ts):
        self.calls.append(("sound", kind))

    def open_url(self, url):
        self.calls.append(("open_url", url))

    def annotate(self, img, ts, info):
        self.calls.append(("annotate", img.size))
        out = img.copy()
        out.putpixel((0, 0), (1, 2, 3, 255))
        return out


@pytest.fixture
def mgr(store, tmp_path, monkeypatch):
    UPLOADS.clear()
    registry.load_all()  # real services first, then our fakes over two of them
    monkeypatch.setitem(registry.SERVICES, "file/Dropbox",
                        registry.ServiceInfo("file", "Dropbox", "FakeBox", FakeSettings, FakeUploader))
    monkeypatch.setitem(registry.SERVICES, "shortener/BITLY",
                        registry.ServiceInfo("shortener", "BITLY", "FakeShort", FakeSettings, FakeShortener))
    store.app.default_task_settings.image_destination = ImageDestination.FileUploader
    store.app.default_task_settings.image_file_destination = FileDestination.Dropbox
    store.app.default_task_settings.image_settings.image_auto_use_jpeg = False
    return TaskManager(store, HistoryManager(tmp_path / "History.json"), RecHost())


def ts_with(store, acts, auts=AUT.NONE) -> TaskSettings:
    ts = store.task_settings_for(None)
    ts.after_capture_job = acts
    ts.after_upload_job = auts
    return ts


def img(w=40, h=30, color=(200, 10, 10, 255)):
    return Image.new("RGBA", (w, h), color)


def test_capture_save_copy_upload_history(mgr, store):
    ts = ts_with(store, ACT.CopyImageToClipboard | ACT.SaveImageToFile | ACT.UploadImageToHost,
                 AUT.CopyURLToClipboard)
    t = mgr.run_image(img(), ts, window=WindowInfo("Title", "gedit"))
    assert t.done.wait(10)
    info = t.info
    assert info.status == TaskStatus.Completed, info.errors
    p = Path(info.file_path)
    assert p.exists() and p.suffix == ".png" and p.name.startswith("gedit_")
    with Image.open(p) as im:
        assert im.size == (40, 30)
    assert UPLOADS and UPLOADS[0][0] == p.name and UPLOADS[0][1] == p.read_bytes()
    assert ("copy_image", (40, 30)) in mgr.host.calls
    assert ("copy_text", f"http://fake/{p.name}") in mgr.host.calls
    assert ("sound", "TaskCompleted") in mgr.host.calls and ("notify", "Completed") in mgr.host.calls
    hist = mgr.history.items()
    assert hist[-1].url == f"http://fake/{p.name}" and hist[-1].host == "FakeBox" and hist[-1].type == "Image"
    assert hist[-1].tags == {"WindowTitle": "Title", "ProcessName": "gedit"}


def test_annotate_pin_shorten_open(mgr, store):
    ts = ts_with(store, ACT.AnnotateImage | ACT.PinToScreen | ACT.UploadImageToHost,
                 AUT.UseURLShortener | AUT.OpenURL | AUT.CopyURLToClipboard)
    t = mgr.run_image(img(), ts)
    assert t.done.wait(10) and t.info.status == TaskStatus.Completed, t.info.errors
    assert mgr.host.calls[0][0] == "annotate"
    assert ("pin", (40, 30)) in mgr.host.calls
    assert ("open_url", "http://s/1") in mgr.host.calls and ("copy_text", "http://s/1") in mgr.host.calls
    assert t.image.getpixel((0, 0)) == (1, 2, 3, 255)  # annotated image went on


def test_annotate_cancel_stops_task(mgr, store):
    mgr.host.annotate = lambda *a: None
    t = mgr.run_image(img(), ts_with(store, ACT.AnnotateImage | ACT.SaveImageToFile))
    assert t.done.wait(10) and t.info.status == TaskStatus.Stopped and not t.info.file_path


def test_retry_then_success(mgr, store):
    store.app.max_upload_fail_retry = 2
    store.uploaders.settings_for("file/Dropbox", FakeSettings).fail_times = 2
    t = mgr.run_image(img(), ts_with(store, ACT.UploadImageToHost))
    assert t.done.wait(10) and t.info.status == TaskStatus.Completed, t.info.errors


def test_upload_failure_reports_error(mgr, store):
    store.app.max_upload_fail_retry = 0
    store.uploaders.settings_for("file/Dropbox", FakeSettings).fail_times = 5
    t = mgr.run_image(img(), ts_with(store, ACT.UploadImageToHost))
    assert t.done.wait(10) and t.info.status == TaskStatus.Failed and "boom" in t.info.errors[0]
    assert ("sound", "Error") in mgr.host.calls


def test_size_fallback_format(mgr, store):
    import numpy as np
    noise = Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 300, 4), dtype=np.uint8), "RGBA")
    ts = ts_with(store, ACT.SaveImageToFile)
    ts.image_settings.image_auto_use_jpeg = True
    ts.image_settings.image_auto_use_jpeg_size = 10  # KB
    t = mgr.run_image(noise, ts)
    assert t.done.wait(10)
    assert Path(t.info.file_path).suffix == ".jpg"


def test_file_exist_actions(mgr, store):
    ts = ts_with(store, ACT.SaveImageToFile)
    ts.image_settings.file_exist_action = FileExistAction.UniqueName
    a = mgr.run_image(img(), ts, file_name="same")
    a.done.wait(10)
    b = mgr.run_image(img(), ts, file_name="same")
    b.done.wait(10)
    assert Path(a.info.file_path).name == "same.png" and Path(b.info.file_path).name == "same (1).png"
    ts.image_settings.file_exist_action = FileExistAction.Cancel
    c = mgr.run_image(img(), ts, file_name="same")
    c.done.wait(10)
    assert c.info.file_path == ""


def test_formats(mgr, store):
    for fmt, ext in ((EImageFormat.WEBP, ".webp"), (EImageFormat.BMP, ".bmp"), (EImageFormat.TIFF, ".tif"),
                     (EImageFormat.GIF, ".gif"), (EImageFormat.JPEG, ".jpg")):
        ts = ts_with(store, ACT.SaveImageToFile)
        ts.image_settings.image_format = fmt
        t = mgr.run_image(img(), ts)
        t.done.wait(10)
        p = Path(t.info.file_path)
        assert p.suffix == ext
        with Image.open(p) as im:
            assert im.size == (40, 30)


def test_thumbnail_and_delete(mgr, store):
    ts = ts_with(store, ACT.SaveImageToFile | ACT.SaveThumbnailImageToFile)
    ts.image_settings.thumbnail_width = 20
    t = mgr.run_image(img(), ts, file_name="pic")
    t.done.wait(10)
    thumb = Path(t.info.file_path).with_name("pic-thumbnail.png")
    with Image.open(thumb) as im:
        assert im.size == (20, 15)
    ts2 = ts_with(store, ACT.SaveImageToFile | ACT.UploadImageToHost | ACT.DeleteFile)
    t2 = mgr.run_image(img(), ts2, file_name="gone")
    t2.done.wait(10)
    assert t2.info.status == TaskStatus.Completed and not (thumb.parent / "gone.png").exists()


def test_perform_actions_external_program(mgr, store, tmp_path):
    script = tmp_path / "conv.sh"
    script.write_text('#!/bin/sh\ncp "$1" "$2"\n')
    script.chmod(0o755)
    ts = ts_with(store, ACT.SaveImageToFile | ACT.PerformActions)
    ts.external_programs = [ExternalProgram(is_active=True, name="copy", path=str(script),
                                            args='"%input" "%output"', output_extension="bak.png",
                                            extensions="png")]
    t = mgr.run_image(img(), ts, file_name="act")
    t.done.wait(10)
    assert t.info.file_path.endswith("act.bak.png") and Path(t.info.file_path).exists()


def test_text_upload_and_shorten(mgr, store):
    store.app.default_task_settings.text_destination = __import__(
        "sharel.core.enums", fromlist=["TextDestination"]).TextDestination.FileUploader
    store.app.default_task_settings.text_file_destination = FileDestination.Dropbox
    ts = store.task_settings_for(None)
    t = mgr.upload_text("hello world", ts)
    t.done.wait(10)
    assert t.info.status == TaskStatus.Completed, t.info.errors
    assert UPLOADS[-1][1] == b"hello world" and UPLOADS[-1][0].endswith(".txt")
    s = mgr.shorten_url("https://example.com/long", ts)
    s.done.wait(10)
    assert s.info.shortened_url == "http://s/1" and mgr.history.items()[-1].type == "URL"


def test_format_result_tokens():
    from sharel.core.tasks import TaskInfo
    info = TaskInfo(file_path="/a/b/c.png", file_name="c.png", result_url="http://u", shortened_url="http://s")
    assert format_result("$result|$url|$filenamenoext|$foldername|$filepath$n", info) == \
        "http://s|http://u|c|b|/a/b/c.png\n"


def test_stop_all_cancels(mgr, store):
    import threading
    gate = threading.Event()
    mgr.host.annotate = lambda img, ts, info: (gate.wait(5), img)[1]
    t = mgr.run_image(img(), ts_with(store, ACT.AnnotateImage | ACT.SaveImageToFile))
    assert mgr.stop_all() == 1
    gate.set()
    t.done.wait(10)
    assert t.info.status == TaskStatus.Stopped
