"""The windows the task pipeline shows: quick task menu, "After capture", "Before upload",
"After upload" (ShareX's QuickTaskMenu, AfterCaptureForm, BeforeUploadForm, AfterUploadForm)."""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from PIL import Image

from ...core import clipboard, desktop, imaging
from ...core.enums import AFTER_CAPTURE_DESCRIPTIONS
from ...core.enums import AfterCaptureTasks as ACT
from ...core.tasks import TaskInfo
from ..gtkutil import no_hyphens, picture, texture_from_pil

# ShareX's QuickTaskInfo.DefaultPresets
QUICK_TASKS: list[tuple[str, ACT]] = [
    ("Save, Upload, Copy URL", ACT.SaveImageToFile | ACT.UploadImageToHost),
    ("Save, Copy image", ACT.SaveImageToFile | ACT.CopyImageToClipboard),
    ("Save, Copy image file", ACT.SaveImageToFile | ACT.CopyFileToClipboard),
    ("Annotate, Save, Upload, Copy URL", ACT.AnnotateImage | ACT.SaveImageToFile | ACT.UploadImageToHost),
    ("Annotate, Save, Copy image", ACT.AnnotateImage | ACT.SaveImageToFile | ACT.CopyImageToClipboard),
    ("Annotate, Save, Copy image file", ACT.AnnotateImage | ACT.SaveImageToFile | ACT.CopyFileToClipboard),
    ("Annotate, Copy image", ACT.AnnotateImage | ACT.CopyImageToClipboard),
    ("Upload, Copy URL", ACT.UploadImageToHost),
    ("Save", ACT.SaveImageToFile),
    ("Copy image", ACT.CopyImageToClipboard),
    ("Annotate", ACT.AnnotateImage),
    ("Pin to screen", ACT.PinToScreen),
]
UI_ONLY = ACT.ShowQuickTaskMenu | ACT.ShowAfterCaptureWindow


def _window(app, title: str, w: int = 520, h: int = 480) -> tuple[Adw.Window, Gtk.Box, Adw.HeaderBar]:
    win = Adw.Window(application=app, title=title, default_width=w, default_height=h)
    header = Adw.HeaderBar()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=12, margin_start=12,
                  margin_end=12)
    tv = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, propagate_natural_height=True))
    tv.add_top_bar(header)
    win.set_content(tv)
    return win, box, header


def _present(win: Gtk.Window) -> None:
    win.present()
    from ..placement import raise_and_focus
    raise_and_focus(win, above=True)


def open_quick_task_menu(app, info: TaskInfo, flags: ACT, done) -> Adw.Window:
    """Pick what to do with this capture. done(flags) or done(None) to cancel."""
    win, box, _h = _window(app, "ShareL: what to do with the capture?", 360, 520)
    state = {"answered": False}

    def answer(value):
        if state["answered"]:
            return
        state["answered"] = True
        win.destroy()
        done(value)

    keep = (flags & ~UI_ONLY) & ~ACT.ShowQuickTaskMenu
    cont = Gtk.Button(label="Continue (current after capture tasks)")
    cont.add_css_class("suggested-action")
    cont.connect("clicked", lambda *_: answer(keep))
    box.append(cont)
    for label, f in QUICK_TASKS:
        b = Gtk.Button(label=label)
        b.connect("clicked", lambda *_a, f=f: answer(f))
        box.append(b)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: answer(None))
    box.append(cancel)
    win.connect("close-request", lambda *_: (answer(None), False)[1])
    _present(win)
    win._answer = answer  # type: ignore[attr-defined]  (tests)
    return win


def open_after_capture(app, info: TaskInfo, img: Image.Image, flags: ACT, done) -> Adw.Window:
    """AfterCaptureForm: preview, which tasks to run (checkboxes) and the file name."""
    win, box, header = _window(app, "ShareL: after capture", 560, 640)
    box.append(picture(img, 480))
    box.append(Gtk.Label(label=f"{img.width} × {img.height}", css_classes=["dim-label"]))
    name_row = Adw.EntryRow(title="File name", text=Path(info.file_name).stem if info.file_name else "")
    names = Adw.PreferencesGroup()
    names.add(name_row)
    box.append(names)
    group = Adw.PreferencesGroup(title="After capture tasks")
    checks: dict[ACT, Adw.SwitchRow] = {}
    for name, label in AFTER_CAPTURE_DESCRIPTIONS.items():
        f = ACT[name]
        if f & UI_ONLY:
            continue
        row = Adw.SwitchRow(title=label, active=bool(flags & f))
        checks[f] = row
        group.add(row)
    box.append(group)
    state = {"answered": False}

    def answer(ok: bool):
        if state["answered"]:
            return
        state["answered"] = True
        result = None
        if ok:
            new = ACT(0)
            for f, row in checks.items():
                if row.get_active():
                    new |= f
            result = (new, name_row.get_text().strip())
        win.destroy()
        done(result)
    go = Gtk.Button(label="Continue")
    go.add_css_class("suggested-action")
    go.connect("clicked", lambda *_: answer(True))
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: answer(False))
    header.pack_end(go)
    header.pack_start(cancel)
    win.connect("close-request", lambda *_: (answer(False), False)[1])
    win._checks = checks  # type: ignore[attr-defined]
    win._name_row = name_row  # type: ignore[attr-defined]
    win._answer = answer  # type: ignore[attr-defined]
    _present(win)
    return win


def open_before_upload(app, info: TaskInfo, ts, done) -> Adw.Window:
    """BeforeUploadForm: confirm the upload and pick the destination for this task."""
    from ...core.enums import EDataType, FileDestination, ImageDestination, TextDestination
    from ...uploaders import dispatch, registry
    win, box, header = _window(app, "ShareL: before upload", 460, 560)
    if info.file_path and Path(info.file_path).exists() and info.is_image:
        box.append(picture(imaging.load(info.file_path), 400))
    box.append(no_hyphens(Gtk.Label(label=info.file_name)))
    dt = info.data_type
    if dt == EDataType.Image:
        attr, category, enum = "image_destination", "image", ImageDestination
    elif dt == EDataType.Text:
        attr, category, enum = "text_destination", "text", TextDestination
    else:
        attr, category, enum = "file_destination", "file", FileDestination
    group = Adw.PreferencesGroup(title="Destination")
    choices = []
    for v in enum:
        if v.value in dispatch.CUSTOM_NAMES:
            title = "Custom uploader"
        elif v.value == "FileUploader":
            title = "File uploader"
        else:
            info_ = registry.get(category, v.value)
            if info_ is None or not info_.available:
                continue
            title = info_.title
        choices.append((v, title))
    current = getattr(ts, attr)
    combo = Adw.ComboRow(title="Upload to", model=Gtk.StringList.new([t for _v, t in choices]),
                         selected=next((i for i, (v, _t) in enumerate(choices) if v == current), 0))
    group.add(combo)
    box.append(group)
    state = {"answered": False}

    def answer(ok: bool):
        if state["answered"]:
            return
        state["answered"] = True
        if ok and choices:
            setattr(ts, attr, choices[combo.get_selected()][0])
        win.destroy()
        done(ok)
    up = Gtk.Button(label="Upload")
    up.add_css_class("suggested-action")
    up.connect("clicked", lambda *_: answer(True))
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: answer(False))
    header.pack_end(up)
    header.pack_start(cancel)
    win.connect("close-request", lambda *_: (answer(False), False)[1])
    win._combo = combo  # type: ignore[attr-defined]
    win._answer = answer  # type: ignore[attr-defined]
    _present(win)
    return win


def open_after_upload(app, info: TaskInfo) -> Adw.Window:
    """AfterUploadForm: the result URLs with copy/open buttons, a preview and an optional QR code."""
    win, box, _header = _window(app, "ShareL: upload completed", 560, 620)
    if info.file_path and Path(info.file_path).exists() and info.is_image:
        box.append(picture(imaging.load(info.file_path), 420))
    group = Adw.PreferencesGroup(title="Links")
    for label, value in (("URL", info.result_url), ("Shortened URL", info.shortened_url),
                         ("Thumbnail URL", info.thumbnail_url), ("Deletion URL", info.deletion_url),
                         ("File", info.file_path)):
        if not value:
            continue
        row = Adw.ActionRow(title=label, subtitle=value, subtitle_selectable=True)
        copy_b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Copy {label}")
        copy_b.add_css_class("flat")
        copy_b.connect("clicked", lambda *_a, v=value: clipboard.set_text(v))
        open_b = Gtk.Button(icon_name="send-to-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Open {label}")
        open_b.add_css_class("flat")
        open_b.connect("clicked", lambda *_a, v=value, f=(label == "File"):
                       desktop.open_file(v) if f else desktop.open_url(v))
        row.add_suffix(copy_b)
        row.add_suffix(open_b)
        group.add(row)
    box.append(group)
    if info.url_result and app.store.app.default_task_settings.advanced_settings.after_upload_form_show_qr_code:
        from ...tools.qr import encode
        qr = Gtk.Picture(paintable=texture_from_pil(encode(info.url_result)), can_shrink=True,
                         content_fit=Gtk.ContentFit.CONTAIN, height_request=180)
        box.append(qr)
    buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
    if info.file_path:
        folder = Gtk.Button(label="Show in folder")
        folder.connect("clicked", lambda *_: desktop.show_in_folder(info.file_path))
        buttons.append(folder)
    close = Gtk.Button(label="Close")
    close.connect("clicked", lambda *_: win.destroy())
    buttons.append(close)
    box.append(buttons)
    if app.store.app.default_task_settings.advanced_settings.auto_close_after_upload_form:
        GLib.timeout_add_seconds(60, lambda: (win.destroy(), False)[1])
    _present(win)
    return win
