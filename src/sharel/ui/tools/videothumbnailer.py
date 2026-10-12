"""Video thumbnailer window (ShareX VideoThumbnailerForm + VideoThumbnailOptions).

Media path (+ Browse, drag and drop), every VideoThumbnailOptions setting in a property list (count, file
name suffix, random frame, keep screenshots, max width, combine, padding, spacing, columns, video info,
timestamp, shadow, border, image format, font size) plus ShareX's output location (parent folder of the
video, the screenshots folder, or a custom folder) and "Upload thumbnails". Start runs FFmpeg off the main
thread with a progress bar (one step per frame) and Cancel; the result sheet (or the first frame) is shown, and
Open / Show in folder / Upload / Copy image act on it.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from PIL import Image

from ...core import clipboard
from ...tools import videotools as vt
from ..forms import Form
from ..gtkutil import no_hyphens, texture_from_pil
from ._base import stored_tool_settings
from ._base import ProgressPanel, ToolWindow, button, dropdown, dropdown_value, file_drop_target, select_value, single
from ._videocommon import OutputActions, PathRow

OUTPUT_LOCATIONS = ["Parent folder of the video", "Screenshots folder", "Custom folder"]
IMAGE_FORMATS = ["png", "jpg"]


class VideoThumbnailerWindow(ToolWindow):
    def __init__(self, app, ctx) -> None:
        super().__init__(app, "Video thumbnailer", 760, 780)
        self.ctx = ctx
        tool = ctx.task_settings.tool_settings
        self.options = vt.VideoThumbnailOptions(thumbnail_count=tool.video_thumbnailer_count,
                                                column_count=tool.video_thumbnailer_columns,
                                                max_thumbnail_width=tool.video_thumbnailer_width)
        self.progress_log: list[tuple[int, int]] = []
        self.result: vt.ThumbnailResult | None = None
        self.preview_image: Image.Image | None = None
        self.worker = None

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=330, wide_handle=True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=6)
        self.input_row = PathRow("Media path:", "open", on_changed=lambda _p: self._update_sensitivity(),
                                 placeholder="Video file (or drop one here)")
        self.input_row.get_first_child().set_width_chars(10)
        left.append(self.input_row)

        prefs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        prefs.append(Form(self.options).group("Thumbnails", skip=["image_format"]))
        out = Adw.PreferencesGroup(title="Output")
        fmt_row = Adw.ActionRow(title="Image format")
        self.format_dd = dropdown(IMAGE_FORMATS, 0, "Image format")
        self.format_dd.set_valign(Gtk.Align.CENTER)
        self.format_dd.connect("notify::selected",
                               lambda *_: setattr(self.options, "image_format", dropdown_value(self.format_dd)))
        fmt_row.add_suffix(self.format_dd)
        out.add(fmt_row)
        loc_row = Adw.ActionRow(title="Output location")
        self.location_dd = dropdown(OUTPUT_LOCATIONS, 0, "Output location")
        self.location_dd.set_valign(Gtk.Align.CENTER)
        self.location_dd.connect("notify::selected", lambda *_: self._location_changed())
        loc_row.add_suffix(self.location_dd)
        out.add(loc_row)
        self.custom_folder = Adw.EntryRow(title="Custom output folder")
        out.add(self.custom_folder)
        self.upload_switch = Adw.SwitchRow(title="Upload thumbnails", active=True,
                                           subtitle="Upload the result after the thumbnails are taken")
        out.add(self.upload_switch)
        self.open_folder_switch = Adw.SwitchRow(title="Open folder after taking thumbnails", active=False)
        out.add(self.open_folder_switch)
        prefs.append(out)
        left.append(Gtk.ScrolledWindow(child=prefs, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

        self.start_button = button("Start", self.toggle_start, "Take the thumbnails", css="suggested-action")
        left.append(self.start_button)
        paned.set_start_child(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                        margin_start=6, margin_end=12)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, vexpand=True, hexpand=True)
        self.picture.add_css_class("card")
        self.picture.set_tooltip_text("Thumbnail preview")
        right.append(self.picture)
        self.files_label = no_hyphens(Gtk.Label(xalign=0, selectable=True))
        self.files_label.add_css_class("dim-label")
        right.append(self.files_label)
        self.actions = OutputActions(app, lambda: self.ctx.task_settings)
        self.actions.append(button("Copy image", self.copy_image, "Copy the thumbnail image"))
        right.append(self.actions)
        paned.set_end_child(right)

        self.progress = ProgressPanel()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(paned)
        pbox = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=12)
        self.progress.set_hexpand(True)
        pbox.append(self.progress)
        box.append(pbox)
        paned.set_vexpand(True)
        self.set_body(box)
        file_drop_target(box, lambda ps: self.input_row.set_path(ps[0]))
        self._location_changed()
        self._update_sensitivity()

    def _location_changed(self) -> None:
        self.custom_folder.set_sensitive(self.location_dd.get_selected() == 2)

    def _update_sensitivity(self) -> None:
        busy = self.worker is not None and self.worker.running
        self.start_button.set_sensitive(busy or bool(self.input_row.path))

    def output_dir(self, media: str) -> str:
        sel = self.location_dd.get_selected()
        if sel == 1:
            return str(self.app.store.screenshots_folder(self.ctx.task_settings))
        if sel == 2 and self.custom_folder.get_text().strip():
            return os.path.expanduser(self.custom_folder.get_text().strip())
        return str(Path(media).parent)

    def toggle_start(self) -> None:
        if self.worker is not None and self.worker.running:
            self.worker.cancel()
            return
        media = self.input_row.path
        if not Path(media).is_file():
            self.error("Media file not found", media)
            return
        select_value(self.format_dd, self.options.image_format)
        import copy
        opts = copy.deepcopy(self.options)
        out_dir = self.output_dir(media)
        self.progress_log = []
        self.actions.set_outputs([])
        self._remember()

        def work(progress, cancel):
            res = vt.take_thumbnails(media, opts, out_dir, progress, cancel)
            with Image.open(res.files[0]) as im:
                im.load()
                preview = im.convert("RGBA")
            return res, preview

        def done(res_preview):
            res, preview = res_preview
            self.result = res
            self.preview_image = preview
            self.picture.set_paintable(texture_from_pil(preview))
            files = [str(p) for p in res.files]
            self.actions.set_outputs(files)
            self.files_label.set_label("\n".join(files))
            self.progress.set_status(f"{len(files)} file(s)")
            if self.upload_switch.get_active():
                self.actions.upload_output()
            if self.open_folder_switch.get_active():
                from ...core import desktop
                desktop.show_in_folder(files[0])

        self.start_button.set_label("Stop")
        self.worker = self.run(work, done, panel=self.progress, status="Taking thumbnails…",
                               on_progress=lambda d, t: self.progress_log.append((d, t)),
                               on_finally=self._finished)

    def _finished(self) -> None:
        self.start_button.set_label("Start")
        self._update_sensitivity()

    def copy_image(self) -> None:
        if self.preview_image is not None:
            clipboard.set_image(self.preview_image)
            self.toast("Image copied")

    def _remember(self) -> None:
        stored, save = stored_tool_settings(self.app, self.ctx)
        for tool in (stored, self.ctx.task_settings.tool_settings):
            tool.video_thumbnailer_count = self.options.thumbnail_count
            tool.video_thumbnailer_columns = self.options.column_count
            tool.video_thumbnailer_width = self.options.max_thumbnail_width
        save()


def open(app, ctx) -> VideoThumbnailerWindow:  # noqa: A001 - the tool contract's name
    files = list(getattr(ctx, "files", None) or [])
    win = single("videothumbnailer", lambda: VideoThumbnailerWindow(app, ctx))
    if files:
        win.input_row.set_path(files[0])
    return win
