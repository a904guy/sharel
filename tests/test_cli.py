from sharel.cli import parse_args
from sharel.core.config import ConfigStore, HotkeySettings, TaskSettings
from sharel.core.enums import AfterCaptureTasks, HotkeyType
from sharel.core.jobs import all_jobs, handler_name


def test_jobs_case_insensitive_and_flags():
    r = parse_args(["-rectangleregion", "-silent", "-AUTOCLOSE", "-PrintScreen"])
    assert [j for j, _ in r.jobs] == [HotkeyType.RectangleRegion, HotkeyType.PrintScreen]
    assert r.silent and r.autoclose and not r.errors


def test_every_hotkey_type_is_a_flag():
    for job in all_jobs():
        r = parse_args([f"-{job.value}"])
        assert r.jobs == [(job, [])], job
    assert parse_args(["-ExitShareL"]).jobs[0][0] == HotkeyType.ExitShareX


def test_workflow_files_and_urls():
    r = parse_args(["-workflow", "My flow", "/tmp/a b.png", "https://example.com/x.png", "rel.txt"])
    assert r.workflows == ["My flow"]
    assert r.files == ["/tmp/a b.png", "rel.txt"]
    assert r.urls == ["https://example.com/x.png"]


def test_sxcu_files_are_imported_not_uploaded():
    r = parse_args(["a.png", "host.sxcu", "/x/Other.SXCU", "-CustomUploader", "third.sxcu", "-FileUpload", "up.sxcu"])
    assert r.custom_uploaders == ["host.sxcu", "/x/Other.SXCU", "third.sxcu"] and r.has_work
    assert r.files == ["a.png"]
    assert r.jobs == [(HotkeyType.FileUpload, ["up.sxcu"])]  # an explicit upload of the file stays one
    assert parse_args(["-CustomUploader", "notes.txt"]).custom_uploaders == []  # ShareX ignores a non-.sxcu


def test_tools_take_following_files():
    r = parse_args(["-ImageEditor", "a.png", "b.png", "-silent", "c.png"])
    assert r.jobs == [(HotkeyType.ImageEditor, ["a.png", "b.png"])]
    assert r.files == ["c.png"]


def test_errors():
    assert parse_args(["-Nope"]).errors == ["unknown option -Nope"]
    assert parse_args(["--foo"]).errors
    assert parse_args(["-workflow"]).errors


def test_workflow_resolution_with_own_task_settings(tmp_path):
    s = ConfigStore(tmp_path)
    ts = TaskSettings(description="Pin it", job=HotkeyType.RectangleRegion)
    ts.use_default_after_capture_job = False
    ts.after_capture_job = AfterCaptureTasks.PinToScreen
    s.hotkeys.hotkeys.append(HotkeySettings(task_settings=ts))
    wf = s.find_workflow("pin it")
    eff = s.task_settings_for(wf)
    assert wf.job == HotkeyType.RectangleRegion
    assert eff.after_capture_job == AfterCaptureTasks.PinToScreen
    assert s.task_settings_for(None).after_capture_job != AfterCaptureTasks.PinToScreen


def test_handler_names_unique():
    names = [handler_name(j) for j in all_jobs()]
    assert len(names) == len(set(names)) == len(all_jobs())
