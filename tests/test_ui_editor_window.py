"""ImageEditorStartMode: how the editor window opens (ShareX's ImageEditorStartMode)."""
from __future__ import annotations

from PIL import Image
from toolhelpers import wait_until
from uihelpers import run_app

from sharel.core.enums import ImageEditorStartMode as M


def _open(app, mode, size=(100, 50)):
    from sharel.ui.windows.editor import open_editor
    ts = app.store.task_settings_for(None)
    ts.capture_settings.surface_options.image_editor_start_mode = mode
    return open_editor(app, Image.new("RGBA", size, "white"), ts, None, task_mode=False)


def test_normal_and_auto_size():
    def body(app):
        win = _open(app, M.Normal)
        assert win.get_default_size() == (1200, 800)
        win.close()
        small = _open(app, M.AutoSize, (100, 50))
        w, h = small.get_default_size()
        # fits the toolbar and the image, at least 700 x 500
        assert w >= 700 and h == 500 and w != 1200
        small.close()
        from sharel.capture.screen import current_layout
        mon = current_layout().primary
        big = _open(app, M.AutoSize, (5000, 4000))
        assert big.get_default_size() == (int(mon.width * 0.95), int(mon.height * 0.9))  # capped by the screen
        big.close()
        yield 0.1
        return True
    assert run_app(body)


def test_maximized_and_fullscreen():
    def body(app):
        win = _open(app, M.Maximized)
        yield from wait_until(win.is_maximized, timeout=5)
        assert not win.is_fullscreen()
        win.close()
        fs = _open(app, M.Fullscreen)
        yield from wait_until(fs.is_fullscreen, timeout=5)
        fs.close()
        return True
    assert run_app(body)


def test_previous_state_restores_the_last_size_and_maximized():
    def body(app):
        o = app.store.app.default_task_settings.capture_settings.surface_options
        first = _open(app, M.Normal)
        first.set_default_size(777, 555)
        yield 0.2
        first.close()  # closing remembers the window state
        yield 0.2
        assert (o.image_editor_window_size.width, o.image_editor_window_size.height) == (777, 555)
        assert o.image_editor_window_maximized is False
        again = _open(app, M.PreviousState)
        assert again.get_default_size() == (777, 555)
        again.maximize()
        yield from wait_until(again.is_maximized, timeout=5)
        again.close()
        yield 0.2
        assert o.image_editor_window_maximized is True
        third = _open(app, M.PreviousState)
        yield from wait_until(third.is_maximized, timeout=5)
        third.close()
        return True
    assert run_app(body)


def test_save_save_as_copy_upload_print_from_the_editor_window(tmp_path, monkeypatch):
    """The editor's shortcuts reach the real handlers: the file on disk, the chosen path, the
    clipboard, an upload task and the print dialog all get the annotated image."""
    from editor_helpers import drag

    from sharel.core import clipboard
    from sharel.core.enums import AfterCaptureTasks as ACT
    from sharel.core.enums import ShapeType
    from sharel.editor.manager import Mod
    from sharel.ui import printing
    from sharel.ui.windows import editor as E
    copied, printed, uploads = [], [], []
    monkeypatch.setattr(clipboard, "set_image", lambda img: copied.append(img.copy()))
    monkeypatch.setattr(printing, "print_image", lambda app, img, settings, parent=None: printed.append(img.copy()))
    target = tmp_path / "out" / "as.png"
    monkeypatch.setattr(E, "save_file", lambda parent, title, suggested, done, patterns: done(str(target)))
    src = tmp_path / "pic.png"
    Image.new("RGBA", (120, 80), "white").save(src)

    def body(app):
        app.tasks.run_image = lambda img, ts, **kw: uploads.append((img.copy(), ts.after_capture_job))
        win = E.open_editor_for_file(app, str(src))
        m = win.manager
        m.current_tool = ShapeType.DrawingRectangle
        m.options.annotation_options.fill_color = "#FFFF0000"  # ShareX ARGB: opaque red
        drag(m, (20, 20), (60, 50))
        m.current_tool = ShapeType.ToolSelect
        m.key("s", Mod.CTRL)  # save over the file
        with Image.open(src) as im:
            assert im.size == (120, 80) and im.getpixel((40, 35))[:3] == (255, 0, 0)
        m.key("s", Mod.CTRL | Mod.SHIFT)  # save as: the chosen path, now the editor's file
        with Image.open(target) as im:
            assert im.getpixel((40, 35))[:3] == (255, 0, 0)
        assert win.file_path == str(target)
        m.deselect()  # with a shape selected, Ctrl+C copies the shape (as in ShareX)
        m.key("c", Mod.CTRL)
        m.key("u", Mod.CTRL)
        m.key("p", Mod.CTRL)
        for got in (copied, printed, [u[0] for u in uploads]):
            assert len(got) == 1 and got[0].getpixel((40, 35))[:3] == (255, 0, 0)
        assert uploads[0][1] == ACT.UploadImageToHost  # the upload task only uploads
        win.close()
        yield 0.1
        return True
    assert run_app(body)


def test_an_editor_closed_before_it_is_shown_leaves_no_busy_idle():
    """Regression: the initial zoom waited in an idle that returned True until the canvas had a width,
    so an editor closed before its first allocation left the main loop spinning forever."""
    from gi.repository import GLib

    def body(app):
        win = _open(app, M.Normal)
        win.close()  # in the same main-loop iteration it was created in
        yield 0.3
        return True
    assert run_app(body)
    ctx = GLib.MainContext.default()
    n = 0
    while ctx.pending() and n < 5000:
        ctx.iteration(False)
        n += 1
    assert n < 5000, "a source kept the main context busy"


def test_editor_zooms_to_fit_a_large_image_once_shown():
    from sharel.ui.windows.editor import open_editor

    def body(app):
        ts = app.store.task_settings_for(None)
        ts.capture_settings.surface_options.image_editor_start_mode = M.Normal
        win = open_editor(app, Image.new("RGBA", (4000, 3000), "white"), ts, None, task_mode=False)
        yield from wait_until(lambda: win.canvas.get_width() > 1, timeout=5)
        yield 0.3
        assert win._initial_done and win.mapping.zoom < 1.0  # fitted into the 1200x800 window
        win.close()
        return True
    assert run_app(body)
