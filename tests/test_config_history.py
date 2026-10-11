import datetime as dt
import json

from sharel.core.config import (
    ApplicationConfig,
    ConfigStore,
    HotkeySettings,
    HotkeysConfig,
    TaskSettings,
    effective_task_settings,
)
from sharel.core.enums import AfterCaptureTasks, AfterUploadTasks, EImageFormat, HotkeyType, ImageDestination
from sharel.core.history import HistoryFilter, HistoryItem, HistoryManager
from sharel.core.serialize import dumps, loads


def test_application_config_round_trip(tmp_path):
    cfg = ApplicationConfig()
    cfg.default_task_settings.after_capture_job = AfterCaptureTasks.SaveImageToFile | AfterCaptureTasks.DoOCR
    cfg.default_task_settings.image_settings.image_format = EImageFormat.WEBP
    cfg.default_task_settings.capture_settings.surface_options.annotation_options.border_size = 9
    cfg.upload_limit = 3
    text = dumps(cfg)
    data = json.loads(text)
    assert data["default_task_settings"]["after_capture_job"] == ["SaveImageToFile", "DoOCR"]
    assert data["default_task_settings"]["image_settings"]["image_format"] == "WEBP"
    back = loads(ApplicationConfig, text)
    assert back == cfg


def test_unknown_and_bad_keys_tolerated():
    back = loads(ApplicationConfig, json.dumps({"upload_limit": "x", "nope": 1, "show_tray": False}))
    assert back.upload_limit == 5 and back.show_tray is False


def test_flags_accept_sharex_comma_string():
    ts = loads(TaskSettings, json.dumps({"after_upload_job": "CopyURLToClipboard, OpenURL"}))
    assert ts.after_upload_job == AfterUploadTasks.CopyURLToClipboard | AfterUploadTasks.OpenURL


def test_store_files_and_names(tmp_path):
    s = ConfigStore(tmp_path)
    s.app.upload_limit = 2
    s.hotkeys.hotkeys.append(HotkeySettings(task_settings=TaskSettings(description="My flow",
                                                                       job=HotkeyType.ActiveMonitor)))
    s.save()
    assert {p.name for p in tmp_path.iterdir()} >= {"ApplicationConfig.json", "HotkeysConfig.json",
                                                    "UploadersConfig.json"}
    s2 = ConfigStore(tmp_path)
    assert s2.app.upload_limit == 2
    assert s2.find_workflow("my flow").job == HotkeyType.ActiveMonitor
    assert s2.find_workflow("RectangleRegion").hotkey_info.hotkey == "<Control>Print"
    assert s2.find_workflow("Capture region").job == HotkeyType.RectangleRegion
    assert s2.find_workflow("missing") is None


def test_corrupt_file_falls_back_to_backup(tmp_path):
    s = ConfigStore(tmp_path)
    s.app.upload_limit = 4
    s.save()
    s.app.upload_limit = 7
    s.save()  # previous file becomes .bak
    (tmp_path / "ApplicationConfig.json").write_text("{broken")
    assert ConfigStore(tmp_path).app.upload_limit == 4


def test_default_hotkeys_match_sharex():
    hk = {h.job.value: h.hotkey_info.hotkey for h in HotkeysConfig().hotkeys}
    assert hk == {"RectangleRegion": "<Control>Print", "PrintScreen": "Print", "ActiveWindow": "<Alt>Print",
                  "ScreenRecorder": "<Shift>Print", "ScreenRecorderGIF": "<Control><Shift>Print"}


def test_effective_task_settings_sections():
    default = TaskSettings()
    default.image_destination = ImageDestination.Imgur
    default.image_settings.image_format = EImageFormat.JPEG
    wf = TaskSettings(job=HotkeyType.RectangleRegion)
    wf.use_default_after_capture_job = False
    wf.after_capture_job = AfterCaptureTasks.PinToScreen
    wf.use_default_destinations = False
    wf.image_destination = ImageDestination.CustomImageUploader
    wf.image_settings.image_format = EImageFormat.GIF  # ignored: use_default_image_settings is on
    eff = effective_task_settings(wf, default)
    assert eff.after_capture_job == AfterCaptureTasks.PinToScreen
    assert eff.image_destination == ImageDestination.CustomImageUploader
    assert eff.image_settings.image_format == EImageFormat.JPEG
    assert eff.job == HotkeyType.RectangleRegion
    eff.image_settings.image_format = EImageFormat.BMP
    assert default.image_settings.image_format == EImageFormat.JPEG  # deep copies


def test_screenshots_folder_monthly(store, monkeypatch, tmp_path):
    folder = store.screenshots_folder(store.app.default_task_settings)
    now = dt.datetime.now()
    assert folder.name == f"{now.year}-{now.month:02d}"
    assert folder.parent.name == "ShareL"


def test_history_round_trip_and_filter(tmp_path):
    h = HistoryManager(tmp_path / "History.json")
    h.add(HistoryItem(file_name="a.png", file_path="/x/a.png", type="Image", host="Imgur",
                      url="https://i/a.png", date_time="2026-01-02T10:00:00", tags={"WindowTitle": "Editor"}))
    h.add(HistoryItem(file_name="b.txt", type="Text", host="Pastebin", url="https://p/b",
                      date_time="2026-02-03T10:00:00"))
    h2 = HistoryManager(tmp_path / "History.json")
    items = h2.items()
    assert [i.file_name for i in items] == ["a.png", "b.txt"]
    assert h2.search(HistoryFilter(type="Image"))[0].host == "Imgur"
    assert h2.search(HistoryFilter(text="editor"))[0].file_name == "a.png"
    assert [i.file_name for i in h2.search(HistoryFilter(date_from=dt.date(2026, 2, 1)))] == ["b.txt"]
    assert [i.file_name for i in h2.search(HistoryFilter(images_only=True))] == ["a.png"]
    assert h2.hosts() == ["Imgur", "Pastebin"]
    assert h2.remove(items[0].id) and len(HistoryManager(tmp_path / "History.json").items()) == 1
