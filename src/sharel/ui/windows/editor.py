"""The image editor (ShareX's RegionCaptureForm in Editor / TaskEditor mode).

``open_editor(app, img, ts, done, task_mode=True)`` is the after-capture "Open in image editor"
step: Enter / "Run after capture tasks" continues the task with the annotated image, closing the
window continues it too (like ShareX), "Cancel task" stops it. ``open_editor_for_file`` edits a
file; ``open_editor_startup`` is the ImageEditor job's start dialog (file, clipboard, new, screen).
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from PIL import Image

from ...core import clipboard, imaging
from ...core.config import TaskSettings
from ...core.enums import AfterCaptureTasks, ImageEditorStartMode, RegionCaptureMode
from ...editor.manager import Callbacks, CaptureResult, RegionResult, ShapeManager
from ..annotate import CanvasView, DialogBridge, Toolbar, ZoomMapping
from ..gtkutil import IMAGE_PATTERNS, alert, ask, call_main_async, save_file

log = logging.getLogger("sharel.editor")

OPEN_EDITORS: list[EditorWindow] = []


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app, img: Image.Image, ts: TaskSettings, *, done=None, task_mode: bool = False,
                 file_path: str = "", title: str = "") -> None:
        super().__init__(application=app, title=f"ShareL image editor - {title or Path(file_path).name or 'Untitled'}")
        self.app = app
        self.ts = ts
        self.done = done
        self.task_mode = task_mode
        self.file_path = file_path
        self._finished = False
        opts = copy.deepcopy(ts.capture_settings.surface_options)
        self.dialogs = DialogBridge(lambda: self.manager, lambda: self, self._redraw)
        cb = Callbacks(request_text=self.dialogs.request_text, request_image_file=self.dialogs.request_image_file,
                       request_sticker=self.dialogs.request_sticker, capture_screen_image=self._capture_screen,
                       apply_effects=self._apply_effects, confirm_exit=self._confirm_exit,
                       copy_image=clipboard.set_image, copy_text=clipboard.set_text,
                       read_clipboard=self._read_clipboard,
                       save_image=self._save, save_image_as=self._save_as, upload_image=self._upload,
                       print_image=self._print, zoom=lambda d: self.canvas.zoom_step(d), on_result=self._on_result,
                       on_change=self._changed)
        mode = RegionCaptureMode.TaskEditor if task_mode else RegionCaptureMode.Editor
        self.manager = ShapeManager(img, mode, opts, cb)
        self.mapping = ZoomMapping()
        self.canvas = CanvasView(self.manager, self.mapping, on_result=self._on_result, editor=True)
        self.toolbar = Toolbar(self.manager, self.dialogs)
        self.status = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"], margin_start=8, margin_end=8,
                                margin_top=2, margin_bottom=2)
        header = Adw.HeaderBar()
        zoom_out = Gtk.Button(icon_name="zoom-out-symbolic", tooltip_text="Zoom out (Ctrl+-)")
        zoom_out.connect("clicked", lambda *_: self.canvas.zoom_step(-1))
        zoom_in = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text="Zoom in (Ctrl++)")
        zoom_in.connect("clicked", lambda *_: self.canvas.zoom_step(1))
        fit = Gtk.Button(icon_name="zoom-fit-best-symbolic", tooltip_text="Zoom to fit")
        fit.connect("clicked", lambda *_: self.canvas.zoom_to_fit())
        for b in (fit, zoom_in, zoom_out):
            header.pack_end(b)
        if task_mode:
            cont = Gtk.Button(label="Run after capture tasks", tooltip_text="Continue the task (Enter)")
            cont.add_css_class("suggested-action")
            run = CaptureResult(RegionResult.AnnotateRunAfterCaptureTasks)
            cont.connect("clicked", lambda *_: self._on_result(run))
            cancel = Gtk.Button(label="Cancel task", tooltip_text="Stop the task (Esc)")
            cancel.connect("clicked", lambda *_: self._on_result(CaptureResult(RegionResult.AnnotateCancelTask)))
            header.pack_start(cont)
            header.pack_start(cancel)
        tb_scroll = Gtk.ScrolledWindow(child=self.toolbar, vscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(tb_scroll)
        box.append(Gtk.Separator())
        box.append(self.canvas)
        box.append(self.status)
        tv = Adw.ToolbarView(content=box)
        tv.add_top_bar(header)
        self.set_content(tv)
        self.connect("close-request", self._on_close_request)
        self._apply_start_mode(img)
        GLib.idle_add(self._initial_view)
        OPEN_EDITORS.append(self)
        self._changed()

    # -- window sizing (ImageEditorStartMode) --

    def _apply_start_mode(self, img: Image.Image) -> None:
        o = self.ts.capture_settings.surface_options
        mode = o.image_editor_start_mode
        if mode == ImageEditorStartMode.PreviousState:
            self.set_default_size(o.image_editor_window_size.width, o.image_editor_window_size.height)
            if o.image_editor_window_maximized:
                self.maximize()
        elif mode == ImageEditorStartMode.Maximized:
            self.maximize()
        elif mode == ImageEditorStartMode.Fullscreen:
            self.fullscreen()
        elif mode == ImageEditorStartMode.Normal:
            self.set_default_size(1200, 800)
        else:  # AutoSize: fit the image, within the screen
            from ...capture.screen import current_layout
            layout = current_layout()
            mon = layout.primary
            mw, mh = (mon.width, mon.height) if mon else (1600, 1000)
            w = min(max(700, img.width + 80), int(mw * 0.9))
            h = min(max(500, img.height + 180), int(mh * 0.9))
            self.set_default_size(w, h)

    def _initial_view(self) -> bool:
        if self.canvas.get_width() <= 1:
            return True
        iw, ih = self.manager.canvas.size
        if self.ts.capture_settings.surface_options.zoom_to_fit_on_open or iw > self.canvas.get_width() or \
                ih > self.canvas.get_height():
            self.canvas.zoom_to_fit()
        else:
            self.canvas.center()
        self.canvas.grab_focus()
        return False

    # -- manager callbacks --

    def _redraw(self) -> None:
        self.canvas.queue_draw()

    def _changed(self) -> None:
        self.canvas.queue_draw()
        self.toolbar.schedule_rebuild()
        w, h = self.manager.canvas.size
        mod = " (modified)" if self.manager.is_image_modified else ""
        self.status.set_label(f"{w} × {h}{mod}   Zoom {self.mapping.zoom * 100:.0f}%   "
                              f"{len(self.manager.shapes)} shape(s)")

    def _read_clipboard(self):
        c = clipboard.read()
        if c.kind == "image":
            return c.image
        if c.kind == "files" and c.files:
            return c.files[0]
        if c.kind == "text":
            return c.text
        return None

    def _capture_screen(self):
        from ...capture import backends
        try:
            return backends.capture_screen(False).image
        except backends.CaptureError as e:
            alert(self, "Capture failed", str(e))
            return None

    def _apply_effects(self, img: Image.Image):
        from .effects import open_effects_window
        return call_main_async(lambda done: open_effects_window(self.app, img, self.ts, done))

    def _confirm_exit(self) -> bool:
        res = call_main_async(lambda done: ask(self, "Close the editor?", "The image has unsaved changes.",
                                               [("cancel", "Cancel"), ("close", "Close without saving")], done,
                                               destructive="close"))
        return res == "close"

    def output_image(self) -> Image.Image:
        return self.manager.render_output()

    def _save(self, img: Image.Image) -> None:
        if self.file_path:
            data = imaging.encode(img, _format_for(self.file_path), self.ts.image_settings)
            Path(self.file_path).write_bytes(data)
            self.manager.is_image_modified = False
            self._changed()
            self.app.message("Image saved", self.file_path)
        else:
            self._save_as(img)

    def _save_as(self, img: Image.Image) -> None:
        folder = self.app.store.screenshots_folder(self.ts)
        name = Path(self.file_path).name if self.file_path else "image.png"

        def chosen(path: str) -> None:
            if not path:
                return
            data = imaging.encode(img, _format_for(path), self.ts.image_settings)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
            self.file_path = path
            self.set_title(f"ShareL image editor - {Path(path).name}")
            self.manager.is_image_modified = False
            self._changed()
        save_file(self, "Save image as", str(folder / name), chosen, IMAGE_PATTERNS)

    def _upload(self, img: Image.Image) -> None:
        ts = copy.deepcopy(self.ts)
        ts.after_capture_job = AfterCaptureTasks.UploadImageToHost
        self.app.tasks.run_image(img, ts)

    def _print(self, img: Image.Image) -> None:
        from ..printing import print_image
        print_image(self.app, img, self.app.store.app.print_settings, parent=self)

    # -- results --

    def _on_result(self, res: CaptureResult) -> None:
        r = res.result
        if r in (RegionResult.AnnotateRunAfterCaptureTasks, RegionResult.AnnotateContinueTask):
            self._finish(self.output_image())
        elif r in (RegionResult.AnnotateCancelTask,):
            self._finish(None)
        elif r == RegionResult.Close:
            if self.task_mode:
                self._finish(self.output_image())
            else:
                self._finish(None)

    def _finish(self, img: Image.Image | None) -> None:
        if self._finished:
            return
        self._finished = True
        o = self.app.store.app.default_task_settings.capture_settings.surface_options
        w, h = self.get_default_size()
        o.image_editor_window_size.width, o.image_editor_window_size.height = w, h
        o.image_editor_window_maximized = self.is_maximized()
        if img is not None and self.ts.capture_settings.surface_options.editor_auto_copy_image:
            clipboard.set_image(img)
        if self in OPEN_EDITORS:
            OPEN_EDITORS.remove(self)
        done = self.done
        self.done = None
        self.destroy()
        if done:
            done(img)

    def _on_close_request(self, *_a) -> bool:
        if self._finished:
            return False
        if not self.task_mode and self.manager.is_image_modified and not self._confirm_exit():
            return True
        self._on_result(CaptureResult(RegionResult.Close))
        return True


def _format_for(path: str):
    from ...core.enums import EImageFormat
    ext = Path(path).suffix.lower().lstrip(".")
    return {"jpg": EImageFormat.JPEG, "jpeg": EImageFormat.JPEG, "gif": EImageFormat.GIF, "bmp": EImageFormat.BMP,
            "tif": EImageFormat.TIFF, "tiff": EImageFormat.TIFF, "webp": EImageFormat.WEBP}.get(ext, EImageFormat.PNG)


def open_editor(app, img: Image.Image, ts: TaskSettings, done, *, task_mode: bool = True, title: str = "",
                file_path: str = "") -> EditorWindow:
    win = EditorWindow(app, img, ts, done=done, task_mode=task_mode, title=title, file_path=file_path)
    win.present()
    from ..placement import raise_and_focus
    raise_and_focus(win)
    return win


def open_editor_for_file(app, path: str) -> EditorWindow | None:
    try:
        img = imaging.load(path)
    except OSError as e:
        alert(app.active_parent(), "Cannot open image", f"{path}: {e}")
        return None
    return open_editor(app, img, app.store.task_settings_for(None), None, task_mode=False, file_path=path)


def open_editor_startup(app, ctx) -> None:
    """ShareX's image editor startup choices: open a file, load from clipboard, new image, capture screen."""
    d = Adw.AlertDialog(heading="Image editor", body="What do you want to edit?")
    for rid, label in (("cancel", "Cancel"), ("file", "Open image file…"), ("clipboard", "Load from clipboard"),
                       ("new", "Create new image"), ("screen", "Capture screen")):
        d.add_response(rid, label)
    d.set_default_response("file")
    d.set_close_response("cancel")
    ts = ctx.task_settings

    def chosen(_d, rid: str) -> None:
        if rid == "file":
            from ..gtkutil import open_files
            open_files(app.active_parent(), "Open image", lambda ps: [open_editor_for_file(app, p) for p in ps],
                       patterns=IMAGE_PATTERNS)
        elif rid == "clipboard":
            c = clipboard.read()
            img = c.image if c.kind == "image" else (imaging.load(c.files[0]) if c.kind == "files" else None)
            if img is None:
                alert(app.active_parent(), "Clipboard", "The clipboard has no image.")
                return
            open_editor(app, img, ts, None, task_mode=False, title="Clipboard")
        elif rid == "new":
            o = ts.capture_settings.surface_options
            from ...effects.common import parse_color
            color = (0, 0, 0, 0) if o.editor_new_image_transparent else parse_color(o.editor_new_image_background_color)
            img = Image.new("RGBA", (o.editor_new_image_size.width, o.editor_new_image_size.height), color)
            open_editor(app, img, ts, None, task_mode=False, title="New image")
        elif rid == "screen":
            from ...capture import backends
            try:
                img = backends.capture_screen(False).image
            except backends.CaptureError as e:
                alert(app.active_parent(), "Capture failed", str(e))
                return
            open_editor(app, img, ts, None, task_mode=False, title="Screen")
    d.connect("response", chosen)
    d.present(app.active_parent())
