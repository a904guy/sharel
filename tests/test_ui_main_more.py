"""Main window parts test_ui_main.py does not drive: after-upload flags, task progress, the task
context menu, drops of files/images/URLs, and what the after-upload window shows."""
from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk
from PIL import Image
from uihelpers import find, find_all, run_app

from sharel.core.enums import AfterUploadTasks, EDataType
from sharel.core.tasks import TaskInfo, TaskStatus


def test_after_upload_flags_toggle_and_are_saved():
    def body(app):
        app.show_main_window()
        yield 0.2
        win = app.main_window
        ts = app.store.app.default_task_settings
        for flag in (AfterUploadTasks.UseURLShortener, AfterUploadTasks.OpenURL):
            before = bool(ts.after_upload_job & flag)
            win.activate_action(f"win.au-{flag.name}", None)  # a check item: activating toggles it
            assert bool(ts.after_upload_job & flag) is (not before)
            assert win.lookup_action(f"au-{flag.name}").get_state().unpack() is (not before)
            saved = json.loads((app.store.directory / "ApplicationConfig.json").read_text())
            assert (flag.name in saved["default_task_settings"]["after_upload_job"]) is (not before)
        return True
    assert run_app(body)


def _item_info(tmp_path, **kw):
    p = tmp_path / "shot.png"
    Image.new("RGBA", (40, 30), "red").save(p)
    base = dict(file_name="shot.png", file_path=str(p), data_type=EDataType.Image, status=TaskStatus.Completed,
                result_url="https://h.example/shot.png", shortened_url="https://s.example/1",
                deletion_url="https://h.example/del", errors=["first error"])
    base.update(kw)
    return TaskInfo(**base)


def test_task_progress_is_shown_in_the_list(tmp_path):
    def body(app):
        app.show_main_window()
        win = app.main_window
        win.view_toggle.set_active(False)  # list view
        info = _item_info(tmp_path, status=TaskStatus.Working, result_url="", progress=0.0, bytes_total=100)
        win.on_task_event("added", info)
        yield 0.2
        info.bytes_done, info.progress = 40, 0.4
        win.on_task_event("progress", info)
        yield 0.3
        item = win.items[info.id]
        assert item.status == "Uploading 40%"
        bars = find_all(win.column_view, lambda w: isinstance(w, Gtk.ProgressBar))
        assert [round(b.get_fraction(), 2) for b in bars] == [0.4]
        texts = [w.get_label() for w in find_all(win.column_view, lambda w: isinstance(w, Gtk.Label))]
        assert "Uploading 40%" in texts and "shot.png" in texts
        win.view_toggle.set_active(True)  # thumbnail view: its bar follows too
        yield 0.3
        info.bytes_done, info.progress = 75, 0.75
        win.on_task_event("progress", info)
        yield 0.3
        gbars = find_all(win.grid_view, lambda w: isinstance(w, Gtk.ProgressBar))
        assert [(round(b.get_fraction(), 2), b.get_visible()) for b in gbars] == [(0.75, True)]
        info.status, info.progress = TaskStatus.Completed, 1.0
        win.on_task_event("finished", info)
        yield 0.3
        assert [b.get_visible() for b in find_all(win.grid_view, lambda w: isinstance(w, Gtk.ProgressBar))] == [False]
        return True
    assert run_app(body)


def test_task_context_menu_actions(tmp_path, monkeypatch):
    import sharel.ui.main_window as MW
    texts, urls, alerts, shown = [], [], [], []
    monkeypatch.setattr(MW.clipboard, "set_text", lambda t: texts.append(t))
    monkeypatch.setattr(MW.desktop, "open_url", lambda u: urls.append(u))
    monkeypatch.setattr(MW.desktop, "show_in_folder", lambda p: shown.append(p))
    import sharel.ui.gtkutil as gu
    monkeypatch.setattr(gu, "alert", lambda parent, title, body: alerts.append((title, body)))

    def body(app):
        app.show_main_window()
        win = app.main_window
        info = _item_info(tmp_path)
        win.on_task_event("added", info)
        yield 0.2
        assert win.selected_items() == []
        win._on_context(None, 1, 5, 5, win.column_view)  # no selection: no menu
        assert find(win.column_view, lambda w: isinstance(w, Gtk.PopoverMenu)) is None
        win.selection.select_item(0, True)
        win._on_context(None, 1, 5, 5, win.column_view)  # right click on the selection: the menu
        pop = find(win.column_view, lambda w: isinstance(w, Gtk.PopoverMenu))
        assert pop is not None and pop.get_visible()
        model = pop.get_menu_model()
        labels = [model.get_item_attribute_value(i, "label").unpack() for i in range(model.get_n_items())
                  if model.get_item_attribute_value(i, "label") is not None]
        assert labels[:2] == ["Open", "Copy"]
        pop.popdown()
        for action in ("task-copy-url", "task-copy-short", "task-copy-del", "task-copy-path", "task-copy-name",
                       "task-copy-folder", "task-open-url", "task-open-short", "task-open-folder", "task-errors"):
            win.activate_action(f"win.{action}", None)
        assert texts == ["https://h.example/shot.png", "https://s.example/1", "https://h.example/del",
                         info.file_path, "shot.png", str(Path(info.file_path).parent)]
        assert urls == ["https://h.example/shot.png", "https://s.example/1"]
        assert shown == [info.file_path] and alerts == [("Errors", "first error")]
        win.activate_action("win.task-remove", None)
        assert win.store_model.get_n_items() == 0 and info.id not in win.items
        return True
    assert run_app(body)


def test_drops_of_files_images_and_urls(tmp_path):
    from sharel.ui.main_window import handle_drop

    def body(app):
        calls = []
        app.upload_paths = lambda paths, ts: calls.append(("files", [Path(p).name for p in paths]))
        app.tasks.run_image = lambda img, ts, **kw: calls.append(("image", img.size))
        app.tasks.shorten_url = lambda url, ts: calls.append(("shorten", url))
        app.tasks.download_upload = lambda url, ts: calls.append(("download", url))
        app.tasks.upload_text = lambda text, ts: calls.append(("text", text))
        a, b = tmp_path / "a.txt", tmp_path / "b.png"
        a.write_text("a")
        Image.new("RGBA", (12, 8), "blue").save(b)
        files = Gdk.FileList.new_from_array([Gio.File.new_for_path(str(a)), Gio.File.new_for_path(str(b))])
        assert handle_drop(app, files)
        tex = Gdk.Texture.new_from_filename(str(b))
        assert handle_drop(app, tex)
        assert handle_drop(app, f"file://{a}")
        ts = app.store.app.default_task_settings
        ts.upload_settings.clipboard_upload_shorten_url = True
        assert handle_drop(app, "https://example.com/page")
        ts.upload_settings.clipboard_upload_shorten_url = False
        ts.upload_settings.clipboard_upload_url_contents = True
        assert handle_drop(app, "https://example.com/pic.png")
        assert not handle_drop(app, "   ")
        assert calls == [("files", ["a.txt", "b.png"]), ("image", (12, 8)), ("files", ["a.txt"]),
                         ("shorten", "https://example.com/page"), ("download", "https://example.com/pic.png")]
        yield 0
        return True
    assert run_app(body)


def test_after_upload_window_shows_the_links_and_copies_them(tmp_path, monkeypatch):
    from sharel.ui.windows import aftercapture as W
    copied = []
    monkeypatch.setattr(W.clipboard, "set_text", lambda t: copied.append(t))

    def body(app):
        info = _item_info(tmp_path)
        win = W.open_after_upload(app, info)
        yield 0.2
        rows = find_all(win, lambda w: isinstance(w, gi.repository.Adw.ActionRow))
        got = {r.get_title(): r.get_subtitle() for r in rows}
        assert got == {"URL": "https://h.example/shot.png", "Shortened URL": "https://s.example/1",
                       "Deletion URL": "https://h.example/del", "File": info.file_path}
        copy_url = find(win, lambda w: isinstance(w, Gtk.Button) and w.get_tooltip_text() == "Copy Shortened URL")
        copy_url.emit("clicked")
        assert copied == ["https://s.example/1"]
        assert find(win, lambda w: isinstance(w, Gtk.Picture)) is not None  # the image preview
        win.destroy()
        return True
    assert run_app(body)
