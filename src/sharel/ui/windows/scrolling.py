"""Scrolling capture window (ShareX's ScrollingCaptureForm): select the area, set the options,
capture, then preview the stitched result and run the after-capture tasks on it."""
from __future__ import annotations

import copy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ...capture import backends
from ...capture.scrolling import ScrollingCapture, ScrollResult
from ...core import clipboard
from ...core.enums import RegionCaptureMode
from ...editor.manager import RegionResult
from ..forms import Form
from ..gtkutil import IMAGE_PATTERNS, save_file, texture_from_pil

_state: dict = {}


def start_scrolling_capture(app, ctx) -> None:
    """Select the scrolling area (window snapping helps), then show the options."""
    def outcome(o):
        if o.result.result == RegionResult.Close or not o.rect_logical:
            return
        ScrollingWindow(app, ctx, o.rect_logical, o.window).present()
    app.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)


class ScrollingWindow(Adw.ApplicationWindow):
    def __init__(self, app, ctx, rect, window=None) -> None:
        super().__init__(application=app, title="ShareL: scrolling capture", default_width=620, default_height=720)
        self.app, self.ctx, self.rect, self.target = app, ctx, tuple(rect), window
        self.opts = copy.deepcopy(ctx.task_settings.capture_settings.scrolling_capture)
        self.run: ScrollingCapture | None = None
        self.result: ScrollResult | None = None
        header = Adw.HeaderBar()
        self.start_btn = Gtk.Button(label="Start capture")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda *_: self.start())
        header.pack_end(self.start_btn)
        self.stack = Adw.ViewStack()
        # options page
        page = Adw.PreferencesPage()
        info = Adw.PreferencesGroup(title="Area",
                                    description=f"{rect[2]} × {rect[3]} at {rect[0]}, {rect[1]}" +
                                    (f" — {window.title}" if window else ""))
        page.add(info)
        page.add(Form(self.opts, self._opts_changed).group("Options"))
        save_default = Gtk.Button(label="Save as default", halign=Gtk.Align.START, margin_top=6)
        save_default.connect("clicked", lambda *_: self._save_default())
        g = Adw.PreferencesGroup()
        g.add(save_default)
        page.add(g)
        self.stack.add_titled_with_icon(page, "options", "Options", "preferences-system-symbolic")
        # progress / result page
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
        self.status = Gtk.Label(label="Not started", xalign=0)
        box.append(self.status)
        self.preview = Gtk.Picture(can_shrink=False, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        box.append(Gtk.ScrolledWindow(child=self.preview, vexpand=True))
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.connect("clicked", lambda *_: self.run and self.run.stop())
        self.upload_btn = Gtk.Button(label="Upload / run after capture tasks")
        self.upload_btn.add_css_class("suggested-action")
        self.upload_btn.connect("clicked", lambda *_: self._process())
        self.copy_btn = Gtk.Button(label="Copy")
        self.copy_btn.connect("clicked", lambda *_: self.result and self.result.image and
                              clipboard.set_image(self.result.image))
        self.save_btn = Gtk.Button(label="Save as…")
        self.save_btn.connect("clicked", lambda *_: self._save_as())
        self.edit_btn = Gtk.Button(label="Edit…")
        self.edit_btn.connect("clicked", lambda *_: self._edit())
        self.retry_btn = Gtk.Button(label="Retry")
        self.retry_btn.connect("clicked", lambda *_: self.start())
        for b in (self.stop_btn, self.retry_btn, self.edit_btn, self.save_btn, self.copy_btn, self.upload_btn):
            buttons.append(b)
        box.append(buttons)
        self.stack.add_titled_with_icon(box, "result", "Result", "image-x-generic-symbolic")
        self._result_buttons(False)
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        tv = Adw.ToolbarView(content=self.stack)
        tv.add_top_bar(header)
        self.set_content(tv)
        _state["win"] = self

    def _opts_changed(self) -> None:
        pass

    def _save_default(self) -> None:
        self.app.store.app.default_task_settings.capture_settings.scrolling_capture = copy.deepcopy(self.opts)
        self.app.store.save_app()

    def _result_buttons(self, have_result: bool) -> None:
        for b in (self.upload_btn, self.copy_btn, self.save_btn, self.edit_btn):
            b.set_sensitive(have_result)

    def _capture(self, rect):
        return backends.capture_area(rect, False, self.app.backend_pref(self.ctx.task_settings)).image

    def _activate_target(self) -> None:
        from ...shell.client import ShellUnavailable, shell
        sh = shell()
        if self.target is not None and sh.available:
            try:
                sh.activate(self.target.id)
            except ShellUnavailable:
                pass

    def start(self) -> None:
        self.stack.set_visible_child_name("result")
        self.status.set_label("Capturing… (Stop ends it)")
        self.stop_btn.set_sensitive(True)
        self._result_buttons(False)
        self.start_btn.set_sensitive(False)
        if not self.opts.manual_mode:
            self.set_visible(False)  # never cover the area being scrolled

        def progress(n, img, status):
            GLib.idle_add(self._show_progress, n, img, status)
        self.run = ScrollingCapture(self.rect, self.opts, capture=self._capture, activate_target=self._activate_target,
                                    progress=progress)
        self.run.start(lambda res: GLib.idle_add(self._done, res))

    def _show_progress(self, n, img, status) -> bool:
        h = img.height if img is not None else 0
        self.status.set_label(f"{n} frame(s), {h} px tall ({status})")
        return False

    def _done(self, res: ScrollResult) -> bool:
        self.result = res
        self.set_visible(True)
        self.present()
        self.start_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        if res.image is None:
            self.status.set_label(f"Failed: {res.error or res.status.value}")
            return False
        self.preview.set_paintable(texture_from_pil(res.image))
        self.status.set_label(f"{res.status.value}: {res.frames} frame(s), {res.image.width} × {res.image.height}")
        self._result_buttons(True)
        if self.opts.auto_upload:
            self._process()
        return False

    def _process(self) -> None:
        if self.result and self.result.image is not None:
            self.app.tasks.run_image(self.result.image, self.ctx.task_settings, region=self.rect)

    def _save_as(self) -> None:
        if not (self.result and self.result.image):
            return
        folder = self.app.store.screenshots_folder(self.ctx.task_settings)
        save_file(self, "Save scrolling capture", str(folder / "scrolling.png"),
                  lambda p: p and self.result.image.save(p), IMAGE_PATTERNS)

    def _edit(self) -> None:
        if self.result and self.result.image is not None:
            from .editor import open_editor
            open_editor(self.app, self.result.image, self.ctx.task_settings, None, task_mode=False,
                        title="Scrolling capture")
