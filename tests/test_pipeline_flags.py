"""The after-capture / after-upload flags not covered in test_tasks.py, and the print layout."""
from pathlib import Path

import pytest
from PIL import Image

from sharel.core.config import PrintSettings
from sharel.core.enums import AfterCaptureTasks as ACT
from sharel.core.enums import AfterUploadTasks as AUT
from sharel.core.enums import FileDestination, FileExistAction, ImageDestination, URLSharingServices
from sharel.core.history import HistoryManager
from sharel.core.tasks import TaskHost, TaskManager, TaskStatus
from sharel.ui.printing import layout
from sharel.uploaders import registry
from sharel.uploaders.base import FileUploader, UploadResult, URLSharer


class Up(FileUploader):
    def upload(self, stream, file_name):
        return UploadResult(url=f"http://up/{file_name}")


class Share(URLSharer):
    def share_url(self, url):
        return f"http://share/?u={url}"


class Host(TaskHost):
    def __init__(self):
        self.calls = []
        self.answers = {}

    def __getattribute__(self, name):
        if name in ("calls", "answers") or name.startswith("__"):
            return object.__getattribute__(self, name)
        base = object.__getattribute__(self, name)
        if not callable(base):
            return base

        def wrapped(*a, **k):
            self.calls.append((name, a))
            if name in self.answers:
                v = self.answers[name]
                return v(*a) if callable(v) else v
            return base(*a, **k)
        return wrapped


@pytest.fixture
def mgr(store, tmp_path, monkeypatch):
    registry.load_all()
    monkeypatch.setitem(registry.SERVICES, "file/Dropbox", registry.ServiceInfo("file", "Dropbox", "Up", None, Up))
    monkeypatch.setitem(registry.SERVICES, "sharing/Email",
                        registry.ServiceInfo("sharing", "Email", "Share", None, Share))
    d = store.app.default_task_settings
    d.image_destination = ImageDestination.FileUploader
    d.image_file_destination = FileDestination.Dropbox
    d.url_sharing_service_destination = URLSharingServices.Email
    d.image_settings.image_auto_use_jpeg = False
    return TaskManager(store, HistoryManager(tmp_path / "h.json"), Host())


def run(mgr, store, acts, auts=AUT.NONE, name=""):
    ts = store.task_settings_for(None)
    ts.after_capture_job, ts.after_upload_job = acts, auts
    t = mgr.run_image(Image.new("RGBA", (30, 20), "red"), ts, file_name=name)
    assert t.done.wait(10)
    return t


def names(host):
    return [c[0] for c in host.calls]


def test_print_and_copy_flags(mgr, store):
    t = run(mgr, store, ACT.SaveImageToFile | ACT.SendImageToPrinter | ACT.CopyFileToClipboard | ACT.ShowInExplorer)
    h = mgr.host
    assert ("print_image" in names(h)) and ("copy_files", ([t.info.file_path],)) in h.calls
    assert ("show_in_folder", (t.info.file_path,)) in h.calls
    t = run(mgr, store, ACT.SaveImageToFile | ACT.CopyFilePathToClipboard)
    assert ("copy_text", (t.info.file_path,)) in mgr.host.calls
    t = run(mgr, store, ACT.SaveImageToFile | ACT.CopyFolderPathToClipboard)
    assert ("copy_text", (str(Path(t.info.file_path).parent),)) in mgr.host.calls


def test_save_with_dialog(mgr, store, tmp_path):
    target = tmp_path / "chosen" / "picked.png"
    mgr.host.answers["save_dialog"] = lambda suggested: target
    t = run(mgr, store, ACT.SaveImageToFileWithDialog)
    assert t.info.file_path == str(target) and target.exists()
    with Image.open(target) as im:
        assert im.size == (30, 20)


def test_quick_task_menu_and_after_capture_window(mgr, store):
    mgr.host.answers["quick_task_menu"] = lambda info, flags: ACT.SaveImageToFile | ACT.CopyImageToClipboard
    t = run(mgr, store, ACT.ShowQuickTaskMenu | ACT.UploadImageToHost)
    assert t.info.file_path and "copy_image" in names(mgr.host) and not t.info.uploaded
    mgr.host.answers["quick_task_menu"] = None  # cancel
    t = run(mgr, store, ACT.ShowQuickTaskMenu | ACT.SaveImageToFile)
    assert t.info.status == TaskStatus.Stopped and not t.info.file_path
    mgr.host.answers["after_capture_window"] = lambda info, img, flags: (ACT.SaveImageToFile, "from_window")
    t = run(mgr, store, ACT.ShowAfterCaptureWindow | ACT.UploadImageToHost)
    assert Path(t.info.file_path).name == "from_window.png" and not t.info.uploaded


def test_before_upload_window_accept_and_decline(mgr, store):
    mgr.host.answers["before_upload"] = False
    t = run(mgr, store, ACT.ShowBeforeUploadWindow | ACT.UploadImageToHost)
    assert not t.info.uploaded and t.info.status == TaskStatus.Completed
    mgr.host.answers["before_upload"] = True
    t = run(mgr, store, ACT.ShowBeforeUploadWindow | ACT.UploadImageToHost, AUT.ShowAfterUploadWindow)
    assert t.info.uploaded and t.info.result_url.startswith("http://up/")
    assert "after_upload_window" in names(mgr.host)


def test_share_url_after_upload(mgr, store):
    t = run(mgr, store, ACT.UploadImageToHost, AUT.ShareURL)
    assert ("open_url", (f"http://share/?u={t.info.result_url}",)) in mgr.host.calls


def test_file_exist_ask_and_overwrite(mgr, store):
    ts = store.app.default_task_settings
    ts.image_settings.file_exist_action = FileExistAction.Overwrite
    a = run(mgr, store, ACT.SaveImageToFile, name="same")
    b = run(mgr, store, ACT.SaveImageToFile, name="same")
    assert a.info.file_path == b.info.file_path
    ts.image_settings.file_exist_action = FileExistAction.Ask
    mgr.host.answers["ask_file_exists"] = FileExistAction.UniqueName
    c = run(mgr, store, ACT.SaveImageToFile, name="same")
    assert Path(c.info.file_path).name == "same (1).png" and "ask_file_exists" in names(mgr.host)
    mgr.host.answers["ask_file_exists"] = FileExistAction.Cancel
    d = run(mgr, store, ACT.SaveImageToFile, name="same")
    assert d.info.file_path == ""


def test_print_layout_settings():
    s = PrintSettings(margin=10, auto_rotate_image=True, auto_scale_image=True, allow_enlarge_image=False,
                      center_image=True)
    # landscape image on a portrait page is rotated, scaled down to fit, centred
    x, y, w, h, rot = layout(1000, 500, 600, 800, s)
    assert rot and w <= 580 and h <= 780 and abs(x - (600 - w) / 2) < 1 and abs(y - (800 - h) / 2) < 1
    # small image, no enlarge: actual size; with enlarge: fills the width
    _x, _y, w, h, _r = layout(100, 100, 600, 800, s)
    assert (w, h) == (100, 100)
    s.allow_enlarge_image = True
    _x, _y, w, h, _r = layout(100, 100, 600, 800, s)
    assert round(w) == 580
    s.center_image, s.auto_rotate_image = False, False
    x, y, *_ = layout(100, 100, 600, 800, s)
    assert (x, y) == (10, 10)
