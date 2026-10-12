"""Region capture overlay, editor, pin, after-capture windows, toolbar and small windows — in the sandbox."""
from pathlib import Path

import numpy as np
from PIL import Image
from uihelpers import run_app

from sharel.capture.backends import Capture
from sharel.core.enums import AfterCaptureTasks as ACT
from sharel.core.enums import RegionCaptureMode, ShapeType
from sharel.editor.manager import RegionResult


def gradient(w=800, h=500):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    a[..., 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def test_region_capture_selects_and_crops():
    def body(app):
        cap = Capture(gradient(), (0, 0, 800, 500), 1.0)
        got = {}
        app.run_region(RegionCaptureMode.Default, app.store.task_settings_for(None), lambda o: got.update(o=o),
                       capture=cap)
        yield 0.3
        s = app.region_session
        assert s is not None and len(s.gtk_windows) >= 1 and s.gtk_windows[0].get_mapped()
        m, view = s.manager, s.views[0]
        m.press(100, 50)
        m.motion(300, 200)
        view._finish(m.release(300, 200))
        yield 0.1
        o = got["o"]
        assert o.result.result == RegionResult.Region
        assert o.rect_logical[2:] in ((200, 150), (201, 151))
        assert o.image.size == (o.rect_logical[2], o.rect_logical[3])
        assert o.image.getpixel((0, 0))[:2] == cap.image.getpixel((100, 50))[:2]
        assert app.region_session is None
        return True
    assert run_app(body)


def test_region_capture_cancel_and_color_picker():
    def body(app):
        cap = Capture(gradient(), (0, 0, 800, 500), 1.0)
        got = {}
        app.run_region(RegionCaptureMode.ScreenColorPicker, app.store.task_settings_for(None),
                       lambda o: got.update(o=o), capture=cap)
        yield 0.2
        s = app.region_session
        s.manager.motion(400, 250)
        res = s.manager.press(400, 250)
        res = res or s.manager.release(400, 250)
        s.views[0]._finish(res)
        yield 0.1
        r, g = cap.image.getpixel((400, 250))[:2]
        assert got["o"].color is not None and got["o"].color.upper().endswith(f"{r:02X}{g:02X}00")
        got.clear()
        app.run_region(RegionCaptureMode.Default, app.store.task_settings_for(None), lambda o: got.update(o=o),
                       capture=cap)
        yield 0.2
        s = app.region_session
        s.views[0]._finish(s.manager.key("Escape"))
        yield 0.1
        assert got["o"].result.result == RegionResult.Close and got["o"].image is None
        return True
    assert run_app(body)


def test_annotate_step_runs_through_editor_window():
    from sharel.ui.windows import editor as E

    def body(app):
        ts = app.store.task_settings_for(None)
        ts.after_capture_job = ACT.AnnotateImage | ACT.SaveImageToFile
        t = app.tasks.run_image(Image.new("RGBA", (300, 200), (255, 255, 255, 255)), ts)
        for _ in range(60):
            if E.OPEN_EDITORS:
                break
            yield 0.05
        win = E.OPEN_EDITORS[-1]
        assert win.task_mode and win.get_mapped()
        m = win.manager
        m.current_tool = ShapeType.DrawingRectangle
        m.press(50, 50)
        m.motion(150, 120)
        m.release(150, 120)
        assert len(m.shapes) == 1
        assert m.undo() and len(m.shapes) == 0
        assert m.redo() and len(m.shapes) == 1
        win._on_result(__import__("sharel.editor.manager", fromlist=["CaptureResult"]).CaptureResult(
            RegionResult.AnnotateRunAfterCaptureTasks))
        for _ in range(100):
            if t.done.is_set():
                break
            yield 0.05
        assert t.info.status.name == "Completed", t.info.errors
        with Image.open(t.info.file_path) as out:
            px = out.convert("RGBA").getpixel((50, 85))
        assert px[:3] != (255, 255, 255)  # the rectangle border landed in the saved file
        return True
    assert run_app(body, timeout=40)


def test_editor_cancel_task_stops_it():
    from sharel.editor.manager import CaptureResult
    from sharel.ui.windows import editor as E

    def body(app):
        ts = app.store.task_settings_for(None)
        ts.after_capture_job = ACT.AnnotateImage | ACT.SaveImageToFile
        t = app.tasks.run_image(Image.new("RGBA", (100, 80), "white"), ts)
        for _ in range(60):
            if E.OPEN_EDITORS:
                break
            yield 0.05
        E.OPEN_EDITORS[-1]._on_result(CaptureResult(RegionResult.AnnotateCancelTask))
        for _ in range(60):
            if t.done.is_set():
                break
            yield 0.05
        assert t.info.status.name == "Stopped" and not t.info.file_path
        return True
    assert run_app(body)


def test_pin_zoom_opacity_close_all():
    from sharel.ui.windows import pin as P

    def body(app):
        p = P.pin_image(app, Image.new("RGBA", (120, 80), (0, 128, 255, 255)))
        P.pin_text(app, "hello pinned text")
        yield 0.3
        assert len(P.PINS) == 2 and p.get_mapped()
        w0 = p.area.get_content_width()
        p.set_zoom(2.0)
        assert p.area.get_content_width() > w0 * 1.8
        p.set_pin_opacity(0.4)
        assert p.opacity == 0.4
        assert P.close_all_pins() == 2
        yield 0.2
        assert P.PINS == []
        return True
    assert run_app(body)


def test_after_capture_quick_menu_before_and_after_upload():
    from sharel.core.enums import ImageDestination
    from sharel.core.tasks import TaskInfo
    from sharel.ui.windows import aftercapture as W

    def body(app):
        info = TaskInfo(file_name="shot")
        got = {}
        win = W.open_after_capture(app, info, Image.new("RGBA", (50, 40), "red"),
                                   ACT.SaveImageToFile | ACT.CopyImageToClipboard, lambda r: got.update(ac=r))
        yield 0.2
        win._checks[ACT.CopyImageToClipboard].set_active(False)
        win._checks[ACT.PinToScreen].set_active(True)
        win._name_row.set_text("renamed")
        win._answer(True)
        flags, name = got["ac"]
        assert flags == ACT.SaveImageToFile | ACT.PinToScreen and name == "renamed"
        q = W.open_quick_task_menu(app, info, ACT.SaveImageToFile, lambda r: got.update(q=r))
        yield 0.1
        q._answer(W.QUICK_TASKS[1][1])
        assert got["q"] == ACT.SaveImageToFile | ACT.CopyImageToClipboard
        ts = app.store.task_settings_for(None)
        info.data_type = info.data_type  # image
        b = W.open_before_upload(app, info, ts, lambda ok: got.update(b=ok))
        yield 0.1
        names = [b._combo.get_model().get_string(i) for i in range(b._combo.get_model().get_n_items())]
        b._combo.set_selected(names.index("Chevereto"))
        b._answer(True)
        assert got["b"] is True and ts.image_destination == ImageDestination.Chevereto
        info.result_url = "https://example.com/x.png"
        a = W.open_after_upload(app, info)
        yield 0.2
        assert a.get_mapped()
        return True
    assert run_app(body)


def test_upload_text_drop_window_and_toolbar(monkeypatch):
    from sharel.core.enums import HotkeyType
    from sharel.core.jobs import JobContext
    from sharel.ui.windows import actionstoolbar as T
    from sharel.ui.windows import dropwindow as D
    from sharel.ui.windows import uploadtext as U

    def body(app):
        calls = []
        app.tasks.upload_text = lambda text, ts: calls.append(text)
        ctx = JobContext(HotkeyType.UploadText, app.store.task_settings_for(None))
        w = U.open_upload_text(app, ctx)
        w._view.get_buffer().set_text("some text")
        w._upload.emit("clicked")
        d = D.open_drop_window(app, ctx)
        assert d._on_drop(None, "dropped", 0, 0)
        assert calls == ["some text", "dropped"]
        tb = T.toggle_actions_toolbar(app)
        yield 0.2
        assert tb is not None and tb.get_mapped()
        ed = T.open_toolbar_editor(app, on_saved=tb.rebuild)
        n = len(ed._items)
        ed._picker.set_selected(1)
        ed._add()
        ed._save()
        assert len(app.store.app.actions_toolbar_list) == n + 1
        assert T.toggle_actions_toolbar(app) is None
        return True
    assert run_app(body)


def test_auto_capture_window_runs_captures(monkeypatch):
    from sharel.capture import autocapture as AC
    from sharel.core.enums import HotkeyType
    from sharel.core.jobs import JobContext
    from sharel.ui.windows import autocapture as W

    shots = []
    monkeypatch.setattr(AC.AutoCapture, "_shoot", lambda self: (shots.append(1), setattr(self, "count",
                                                                                         self.count + 1)))

    def body(app):
        ctx = JobContext(HotkeyType.AutoCapture, app.store.task_settings_for(None))
        w = W.open_auto_capture(app, ctx)
        w._full.set_active(True)
        w._repeat.set_value(0.5)
        w._toggle()
        yield 1.3
        assert AC.AUTO.running and len(shots) >= 2
        w._toggle()
        assert not AC.AUTO.running
        assert app.store.app.default_task_settings.capture_settings.auto_capture.capture_fullscreen
        return True
    assert run_app(body)


def test_scrolling_window_result_buttons(monkeypatch):
    from sharel.capture.scrolling import ScrollingCapture, ScrollResult
    from sharel.capture.stitch import ScrollingCaptureStatus
    from sharel.core.enums import HotkeyType
    from sharel.core.jobs import JobContext
    from sharel.ui.windows.scrolling import ScrollingWindow

    tall = Image.new("RGBA", (200, 900), "blue")
    monkeypatch.setattr(ScrollingCapture, "run",
                        lambda self: ScrollResult(tall, ScrollingCaptureStatus.Successful, 5, ["First", "End"]))

    def body(app):
        ctx = JobContext(HotkeyType.ScrollingCapture, app.store.task_settings_for(None))
        w = ScrollingWindow(app, ctx, (0, 0, 200, 300))
        w.present()
        ran = []
        app.tasks.run_image = lambda img, ts, **kw: ran.append(img.size)
        w.start()
        for _ in range(40):
            if w.result is not None:
                break
            yield 0.05
        yield 0.1
        assert w.upload_btn.get_sensitive()
        w.upload_btn.emit("clicked")
        assert ran == [(200, 900)]
        return True
    assert run_app(body)


def test_watch_folder_uploads_new_file(tmp_path):
    from sharel.core.config import WatchFolder

    def body(app):
        ts = app.store.app.default_task_settings
        ts.watch_folder_enabled = True
        ts.watch_folder_list = [WatchFolder(folder_path=str(tmp_path), filter="*.png")]
        up = []
        app.tasks.upload_file = lambda path, ts: up.append(path)
        app.watchers.apply()
        yield 0.2
        Image.new("RGB", (10, 10)).save(tmp_path / "new.png")
        (tmp_path / "skip.txt").write_text("x")
        for _ in range(80):
            if up:
                break
            yield 0.05
        assert [Path(p).name for p in up] == ["new.png"]
        return True
    assert run_app(body)


def _send_keys(view, names):
    """Key presses through the canvas's own key controller (what GTK delivers to it)."""
    from gi.repository import Gdk, Gtk
    ctl = next(c for c in view.observe_controllers() if isinstance(c, Gtk.EventControllerKey))
    for name in names:
        ctl.emit("key-pressed", Gdk.keyval_from_name(name), 0, Gdk.ModifierType(0))


def _draw_text_shape(m, x0=100, y0=100, x1=300, y1=160):
    m.current_tool = ShapeType.DrawingTextBackground
    m.press(x0, y0)
    m.motion(x1, y1)
    m.release(x1, y1)


def _assert_dialog_owns_the_keys(dialogs):
    from gi.repository import Gtk
    assert dialogs.pending == 1
    focus = dialogs.text_dialog.get_focus()
    assert isinstance(focus, Gtk.TextView), focus  # typing goes to the text, not to OK or Cancel


def test_region_capture_text_dialog_keeps_the_keys():
    """Regression: while the text dialog was open, keys reached the overlay (letters switched tools,
    Return finished the capture), so a user typing quickly lost the capture."""
    def body(app):
        cap = Capture(gradient(), (0, 0, 800, 500), 1.0)
        got = {}
        app.run_region(RegionCaptureMode.Annotation, app.store.task_settings_for(None), lambda o: got.update(o=o),
                       capture=cap)
        yield 0.3
        s = app.region_session
        m, view = s.manager, s.views[0]
        _draw_text_shape(m)
        yield 0.3
        _assert_dialog_owns_the_keys(s.dialogs)
        _send_keys(view, ["a", "space", "b", "Return"])
        yield 0.2
        assert m.current_tool == ShapeType.DrawingTextBackground  # "a" did not pick the arrow
        assert not got and app.region_session is s  # Return did not finish the capture
        s.dialogs.text_dialog.get_focus().get_buffer().set_text("a b")
        s.dialogs.text_dialog.emit("response", "ok")
        yield 0.2
        assert s.dialogs.pending == 0
        assert any(getattr(sh, "text", None) == "a b" for sh in m.shapes)
        _send_keys(view, ["a"])  # the dialog is gone: shortcuts work again
        assert m.current_tool == ShapeType.DrawingArrow
        _send_keys(view, ["Escape", "Escape"])
        yield 0.2
        return True
    assert run_app(body)


def test_editor_text_dialog_keeps_the_keys():
    from sharel.ui.windows.editor import open_editor

    def body(app):
        ed = open_editor(app, Image.new("RGBA", (400, 300), "white"), app.store.task_settings_for(None), None,
                         task_mode=False)
        yield 0.5
        m = ed.manager
        _draw_text_shape(m, 50, 50, 250, 110)
        yield 0.3
        _assert_dialog_owns_the_keys(ed.dialogs)
        shapes_before = len(m.shapes)
        _send_keys(ed.canvas, ["a", "space", "b", "Return", "Delete"])
        yield 0.2
        assert m.current_tool == ShapeType.DrawingTextBackground and len(m.shapes) == shapes_before
        assert ed.get_visible()
        ed.dialogs.text_dialog.emit("response", "cancel")
        yield 0.2
        assert ed.dialogs.pending == 0
        _send_keys(ed.canvas, ["a"])
        assert m.current_tool == ShapeType.DrawingArrow
        ed.close()
        return True
    assert run_app(body)


def test_region_toolbar_separators_are_separators_not_buttons():
    """The tool list's separator (before the selection tool) was drawn as a toggle button labelled
    "sep_tools"."""
    from gi.repository import Gtk

    def body(app):
        cap = Capture(gradient(), (0, 0, 800, 500), 1.0)
        app.run_region(RegionCaptureMode.Annotation, app.store.task_settings_for(None), lambda o: None, capture=cap)
        yield 0.3
        s = app.region_session
        assert s.toolbar is not None
        kids, c = [], s.toolbar.get_first_child()
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        labels = [k.get_label() or "" for k in kids if isinstance(k, Gtk.Button)]
        assert not [lb for lb in labels if lb.startswith("sep_")], labels
        tools = [k for k in kids if isinstance(k, Gtk.ToggleButton)]
        assert len(tools) > 5 and any(isinstance(k, Gtk.Separator) for k in kids)
        s.views[0]._finish(s.manager.key("Escape"))
        yield 0.2
        return True
    assert run_app(body)


def test_magnifier_goes_away_when_the_pointer_leaves_the_canvas():
    """Regression: the canvas had no leave handler, so the magnifier (and crosshair) stayed painted where the
    pointer last was after it moved off the canvas, e.g. onto the editor's toolbar."""
    import cairo
    from gi.repository import Gtk

    from sharel.ui.windows.editor import open_editor

    def render(m) -> bytes:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300)
        m.draw(cairo.Context(surf), 1.0, (0, 0), time_s=0.0, view_size=(400, 300))
        surf.flush()
        return bytes(surf.get_data())

    def body(app):
        ed = open_editor(app, gradient(400, 300), app.store.task_settings_for(None), None, task_mode=False)
        yield 0.5
        m = ed.manager
        motion = next(c for c in ed.canvas.observe_controllers() if isinstance(c, Gtk.EventControllerMotion))
        m.pointer = None
        bare = render(m)
        mp = ed.canvas.mapping

        def at(ix, iy):  # image px -> canvas widget px
            return ix * mp.zoom + mp.pan[0], iy * mp.zoom + mp.pan[1]
        motion.emit("motion", *at(200, 150))
        assert m.pointer is not None
        assert render(m) != bare  # the magnifier is drawn at the pointer
        motion.emit("leave")
        assert m.pointer is not None  # still known (paste and nudges use the last position)
        assert render(m) == bare  # but nothing is drawn for it any more
        motion.emit("motion", *at(210, 150))
        assert render(m) != bare  # back on the canvas: drawn again
        ed.close()
        yield 0.2
        return True
    assert run_app(body)
