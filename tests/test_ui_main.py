"""The main window in the sandbox: left menu entries, flags, destinations, task list, drop upload."""
from pathlib import Path

from PIL import Image
from uihelpers import button_with_label, labels, run_app

from sharel.core.enums import AfterCaptureTasks, ImageDestination
from sharel.ui.main_window import SIDEBAR


def test_sidebar_has_every_sharex_entry():
    def body(app):
        app.show_main_window()
        yield 0.3
        win = app.main_window
        assert win.get_mapped()
        names = [lbl for lbl, _i, _t in SIDEBAR if lbl]
        assert names == ["Capture", "Upload", "Workflows", "Tools", "After capture tasks", "After upload tasks",
                         "Destinations", "Task settings…", "Application settings…", "Hotkey settings…",
                         "Screenshots folder…", "History…", "Image history…", "Debug", "About…"]
        texts = labels(win)
        for n in names:
            assert n in texts, n
        return True
    assert run_app(body)


def test_after_capture_flag_action_toggles_config():
    def body(app):
        app.show_main_window()
        yield 0.2
        win = app.main_window
        ts = app.store.app.default_task_settings
        before = bool(ts.after_capture_job & AfterCaptureTasks.PinToScreen)
        win.activate_action("win.ac-PinToScreen", None)
        assert bool(ts.after_capture_job & AfterCaptureTasks.PinToScreen) != before
        import gi
        from gi.repository import GLib
        win.activate_action("win.dest-image", GLib.Variant("s", "Chevereto"))
        assert ts.image_destination == ImageDestination.Chevereto
        return True
    assert run_app(body)


def test_task_list_shows_tasks_and_thumbnail_toggle(tmp_path):
    def body(app):
        app.show_main_window()
        ts = app.store.task_settings_for(None)
        ts.after_capture_job = AfterCaptureTasks.SaveImageToFile
        t = app.tasks.run_image(Image.new("RGBA", (64, 48), (255, 0, 0, 255)), ts)
        for _ in range(50):
            if t.done.is_set():
                break
            yield 0.05
        yield 0.2
        win = app.main_window
        assert t.info.status.name == "Completed" and Path(t.info.file_path).exists()
        assert win.store_model.get_n_items() == 1
        assert win.store_model.get_item(0).info.id == t.info.id
        win.view_toggle.set_active(False)
        assert win.stack.get_visible_child_name() == "list"
        win.view_toggle.set_active(True)
        assert win.stack.get_visible_child_name() == "grid"
        return True
    assert run_app(body)


def test_drop_text_uploads_and_buttons_exist():
    def body(app):
        app.show_main_window()
        yield 0.2
        from sharel.ui.main_window import handle_drop
        calls = []
        app.tasks.upload_text = lambda text, ts: calls.append(text)
        assert handle_drop(app, "dropped text")
        assert calls == ["dropped text"]
        assert button_with_label(app.main_window, "Task settings…") is not None
        return True
    assert run_app(body)
