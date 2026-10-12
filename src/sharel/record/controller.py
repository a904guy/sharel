"""The screen recording controller (ShareX's ScreenRecordManager + ScreenRecordForm).

One recording at a time. start() runs the optional countdown, shows the region border (drawn by
the extension just outside the recorded area, so it never ends up in the video), opens a small
control window (Stop / Pause / Abort and the elapsed time) next to the region, and switches the
tray icon to its recording state. stop() finishes the file on a worker thread and hands it to the
task pipeline (save, copy path/file, upload…), with history and toast like a capture.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from ..core.nameparser import NameParser, NameParserType
from .recorder import Recorder, RecordingError

log = logging.getLogger("sharel.record")


class RecordingController:
    def __init__(self) -> None:
        self.recorder: Recorder | None = None
        self.app = None
        self.ctx = None
        self.gif = False
        self.rect: tuple[int, int, int, int] | None = None
        self.control: Gtk.Window | None = None
        self._tick = 0
        self._countdown_win: Gtk.Window | None = None
        self._countdown_src = 0
        self._duration_src = 0
        self.last_output = ""
        self.last_error = ""
        self.listeners: list = []
        self._finishing = threading.Event()

    @property
    def active(self) -> bool:
        return self.recorder is not None or self._countdown_src != 0

    @property
    def paused(self) -> bool:
        return self.recorder is not None and self.recorder.state == "paused"

    # -- start --

    def start(self, app, ctx, rect, *, gif: bool = False, window=None) -> None:
        if self.active:
            return
        self.app, self.ctx, self.gif = app, ctx, gif
        self.rect = tuple(int(v) for v in rect)
        rec_opts = ctx.task_settings.capture_settings.screen_recording
        delay = max(rec_opts.countdown_seconds, int(round(rec_opts.screen_record_start_delay)))
        if delay > 0:
            self._countdown(delay)
        else:
            self._begin()

    def _countdown(self, seconds: int) -> None:
        from ..ui.placement import keep_above, unique_title
        x, y, w, h = self.rect
        win = Gtk.Window(title=unique_title("ShareL countdown"), decorated=False, resizable=False)
        win.set_application(self.app)
        label = Gtk.Label(label=str(seconds), css_classes=["title-1"], margin_top=12, margin_bottom=12,
                          margin_start=24, margin_end=24)
        win.set_child(label)
        win.present()
        keep_above(win, (x + w // 2 - 40, y + h // 2 - 40))
        self._countdown_win = win
        left = {"n": seconds}

        def tick() -> bool:
            left["n"] -= 1
            if left["n"] <= 0:
                self._countdown_src = 0
                win.destroy()
                self._countdown_win = None
                # give the compositor a frame to remove the countdown before recording
                GLib.timeout_add(150, lambda: (self._begin(), False)[1])
                return False
            label.set_label(str(left["n"]))
            return True
        self._countdown_src = GLib.timeout_add(1000, tick)

    def _out_path(self) -> str:
        ts = self.ctx.task_settings
        app = self.app
        folder = app.store.screenshots_folder(ts)
        folder.mkdir(parents=True, exist_ok=True)
        np_ = NameParser(NameParserType.FileName, auto_increment=app.store.app.name_parser_auto_increment_number)
        name = np_.parse(ts.upload_settings.name_format_pattern) or "recording"
        if np_.used_auto_increment:
            app.store.app.name_parser_auto_increment_number = np_.auto_increment
        ext = "gif" if self.gif else self._plan_ext()
        from ..core.imaging import unique_path
        return str(unique_path(folder / f"{name}.{ext}"))

    def _plan_ext(self) -> str:
        from . import pipeline as P
        return P.plan(self.ctx.task_settings.capture_settings.screen_recording.ffmpeg).extension

    def _begin(self) -> None:
        ro = self.ctx.task_settings.capture_settings.screen_recording
        from .portal import TokenStore
        self.recorder = Recorder(self.rect, self._out_path(), ro.ffmpeg, fps=ro.screen_record_fps, gif=self.gif,
                                 gif_fps=ro.gif_fps, cursor=ro.screen_record_show_cursor,
                                 restore_token_store=TokenStore(self.app.store),
                                 test_pattern=bool(os.environ.get("SHAREL_RECORD_TEST_PATTERN")))
        self.recorder.on_failed = lambda err, rec=self.recorder: self._failed(rec, err)
        try:
            self.recorder.start()
        except (RecordingError, GLib.Error) as e:
            self.last_error = str(e)
            self.recorder = None
            self.app.message("Screen recording failed", str(e), urgent=True)
            return
        if self.recorder.ep.fallback_note:
            self.app.message("Screen recording", self.recorder.ep.fallback_note)
        self._show_border(True)
        self._open_controls()
        if ro.screen_record_fixed_duration and ro.screen_record_duration > 0:
            self._duration_src = GLib.timeout_add(int(ro.screen_record_duration * 1000),
                                                  lambda: (self.stop(), False)[1])
        self._emit()

    # -- control window, border, tray --

    def _show_border(self, on: bool) -> None:
        ro = self.ctx.task_settings.capture_settings.screen_recording
        if not ro.show_region_border:
            return
        from ..shell.client import ShellUnavailable, shell
        sh = shell()
        if not sh.available:
            return
        try:
            if on:
                sh.show_border("sharel-recording", self.rect, {"color": "rgba(224,27,36,0.9)", "width": 2})
            else:
                sh.hide_border("sharel-recording")
        except ShellUnavailable:
            pass

    def _open_controls(self) -> None:
        from ..ui.placement import keep_above, unique_title
        win = Gtk.Window(title=unique_title("ShareL recording"), decorated=False, resizable=False)
        win.set_application(self.app)
        box = Gtk.Box(spacing=4, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        dot = Gtk.Image(icon_name="media-record-symbolic")
        self._time_label = Gtk.Label(label="00:00", width_chars=6)
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop recording")
        stop.update_property([Gtk.AccessibleProperty.LABEL], ["Stop recording"])
        pause = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text="Pause recording")
        pause.update_property([Gtk.AccessibleProperty.LABEL], ["Pause recording"])
        abort = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Abort recording")
        abort.update_property([Gtk.AccessibleProperty.LABEL], ["Abort recording"])
        for b in (stop, pause, abort):
            b.add_css_class("flat")
        stop.connect("clicked", lambda *_: self.stop())
        pause.connect("clicked", lambda *_: self.toggle_pause())
        abort.connect("clicked", lambda *_: self.abort())
        for w in (dot, self._time_label, pause, stop, abort):
            box.append(w)
        win.set_child(box)
        self._pause_btn = pause
        win.present()
        x, y, w, h = self.rect
        # Below the region if there is room, else above it: never inside the recorded area.
        from ..capture.screen import current_layout
        layout = current_layout()
        mon = layout.monitor_at(x, y)
        bottom = (mon.y + mon.height) if mon else y + h + 100
        pos_y = y + h + 8 if y + h + 50 < bottom else max(0, y - 50)
        keep_above(win, (x, pos_y))
        self.control = win
        self._tick = GLib.timeout_add(250, self._update_time)

    def _update_time(self) -> bool:
        if self.recorder is None:
            self._tick = 0
            return False
        s = int(self.recorder.elapsed)
        self._time_label.set_label(f"{s // 60:02d}:{s % 60:02d}" + (" ⏸" if self.paused else ""))
        return True

    def _close_controls(self) -> None:
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        if self.control is not None:
            self.control.destroy()
            self.control = None
        if self._duration_src:
            GLib.source_remove(self._duration_src)
            self._duration_src = 0
        self._show_border(False)

    def _emit(self) -> None:
        if self.app is not None and self.app.tray is not None:
            self.app.tray.send_state()
        for cb in list(self.listeners):
            cb(self)

    # -- pause / stop / abort --

    def toggle_pause(self) -> None:
        if self.recorder is None:
            return
        if self.recorder.state == "paused":
            self.recorder.resume()
            self._pause_btn.set_icon_name("media-playback-pause-symbolic")
        else:
            self.recorder.pause()
            self._pause_btn.set_icon_name("media-playback-start-symbolic")
        self._emit()

    def stop(self) -> None:
        if self._countdown_src:
            GLib.source_remove(self._countdown_src)
            self._countdown_src = 0
            if self._countdown_win:
                self._countdown_win.destroy()
            self._emit()
            return
        rec = self.recorder
        if rec is None or self._finishing.is_set():
            return
        self._finishing.set()
        self._close_controls()
        app, ctx = self.app, self.ctx

        def finish():
            try:
                path = rec.stop()
                err = ""
            except RecordingError as e:
                path, err = "", str(e)

            def done():
                # publish the result before anyone waiting on "finishing" can look at it
                if err:
                    self.last_error = err
                else:
                    self.last_output = path
                self.recorder = None
                self._finishing.clear()
                self._emit()
                if err:
                    app.message("Screen recording failed", err, urgent=True)
                    return False
                log.info("recording saved: %s (%.1f s)", path, rec.elapsed)
                app.tasks.run_media_file(path, ctx.task_settings)
                return False
            GLib.idle_add(done)
        threading.Thread(target=finish, daemon=True, name="recording-finish").start()

    def _failed(self, rec: Recorder, err: str) -> None:
        """The recorder died on its own (source gone): drop the controls and say so."""
        if self.recorder is not rec:
            return
        self._close_controls()
        self.last_error = err
        self.recorder = None
        self._emit()
        self.app.message("Screen recording failed", err, urgent=True)

    def abort(self) -> None:
        if self._countdown_src:
            return self.stop()
        rec = self.recorder
        if rec is None:
            return
        self._close_controls()
        rec.abort()
        self.recorder = None
        self._emit()
        if self.app is not None:
            self.app.message("Screen recording aborted", "Nothing was saved.")

    def wait_finished(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._finishing.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        return not self._finishing.is_set()


CONTROLLER = RecordingController()


def output_exists() -> bool:
    return bool(CONTROLLER.last_output) and Path(CONTROLLER.last_output).exists()
