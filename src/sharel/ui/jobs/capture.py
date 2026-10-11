"""Screen capture jobs (ShareX's CaptureTaskHelpers)."""
from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import GLib

from ...capture import backends
from ...capture.region import RegionCaptureSession, RegionOutcome
from ...core.config import Rect, TaskSettings
from ...core.enums import RegionCaptureMode
from ...core.jobs import JobContext
from ...core.nameparser import WindowInfo
from ...editor.manager import RegionResult
from ...shell.client import ShellUnavailable, Window, shell

log = logging.getLogger("sharel.capture.jobs")


def window_info(w: Window | None) -> WindowInfo:
    if w is None:
        return WindowInfo()
    return WindowInfo(w.title, w.process_name)


class CaptureJobs:
    last_region: tuple[int, int, int, int] | None = None
    region_session: RegionCaptureSession | None = None

    # -- helpers --

    def active_window(self) -> Window | None:
        sh = shell()
        if not sh.available:
            return None
        try:
            return sh.get_active_window()
        except ShellUnavailable:
            return None

    def backend_pref(self, ts: TaskSettings) -> str:
        pref = ts.capture_settings.capture_backend.value
        return pref if pref != "Auto" else self.store.app.capture_backend.value

    def after_delay(self, ts: TaskSettings, fn: Callable[[], None]) -> None:
        delay = max(0.0, ts.capture_settings.screenshot_delay)
        if delay > 0:
            GLib.timeout_add(int(delay * 1000), lambda: (fn(), False)[1])
        else:
            fn()

    def capture_error(self, e: Exception) -> None:
        log.error("capture failed: %s", e)
        from ..sounds import play
        play("Error", self.store.app.default_task_settings.general_settings)
        self.message("Capture failed", str(e), urgent=True)

    def finish_capture(self, ctx: JobContext, cap: backends.Capture, window: Window | None = None,
                       region=None) -> None:
        from ..sounds import play
        play("Capture", ctx.task_settings.general_settings)
        if cap.notice:
            self.message("ShareL", cap.notice)
        self.tasks.run_image(cap.image, ctx.task_settings, window=window_info(window or cap.window),
                             region=region or cap.region, workflow_name=ctx.workflow_name)

    def run_region(self, mode: RegionCaptureMode, ts: TaskSettings, on_outcome: Callable[[RegionOutcome], None],
                   capture: backends.Capture | None = None) -> None:
        """Freeze the desktop and run the region overlay in ``mode``."""
        if self.region_session is not None:
            return
        try:
            cap = capture or backends.capture_screen(ts.capture_settings.show_cursor, self.backend_pref(ts))
        except backends.CaptureError as e:
            self.capture_error(e)
            return
        import copy
        opts = copy.deepcopy(ts.capture_settings.surface_options)
        if mode == RegionCaptureMode.ScreenColorPicker and not opts.screen_color_picker_info_text:
            opts.screen_color_picker_info_text = ts.tool_settings.screen_color_picker_info_text

        def done(outcome: RegionOutcome) -> None:
            self.region_session = None
            on_outcome(outcome)
        self.region_session = RegionCaptureSession(self, cap, mode, opts, done)

    # -- jobs --

    def job_PrintScreen(self, ctx: JobContext) -> None:
        win = self.active_window()

        def go():
            try:
                cap = backends.capture_screen(ctx.task_settings.capture_settings.show_cursor,
                                              self.backend_pref(ctx.task_settings))
            except backends.CaptureError as e:
                return self.capture_error(e)
            self.finish_capture(ctx, cap, win)
        self.after_delay(ctx.task_settings, go)

    def active_monitor_index(self) -> int:
        from ...capture.screen import current_layout
        layout = current_layout()
        sh = shell()
        pt = None
        if sh.available:
            try:
                x, y, _ = sh.get_pointer()
                pt = (x, y)
            except ShellUnavailable:
                pt = None
        if pt is None:
            return -1
        m = layout.monitor_at(*pt)
        return layout.monitors.index(m) if m else -1

    def job_ActiveMonitor(self, ctx: JobContext) -> None:
        win = self.active_window()

        def go():
            try:
                cap = backends.capture_monitor(self.active_monitor_index(), ctx.task_settings.capture_settings.show_cursor,
                                               self.backend_pref(ctx.task_settings))
            except backends.CaptureError as e:
                return self.capture_error(e)
            self.finish_capture(ctx, cap, win)
        self.after_delay(ctx.task_settings, go)

    def _capture_window(self, ctx: JobContext, win: Window | None) -> None:
        cs = ctx.task_settings.capture_settings
        try:
            cap = backends.capture_window(win, cs.show_cursor, cs.capture_transparent, cs.capture_shadow,
                                          cs.capture_client_area, self.backend_pref(ctx.task_settings))
        except backends.CaptureError as e:
            return self.capture_error(e)
        self.finish_capture(ctx, cap, win)

    def job_ActiveWindow(self, ctx: JobContext) -> None:
        win = self.active_window()
        self.after_delay(ctx.task_settings, lambda: self._capture_window(ctx, win))

    def job_CustomWindow(self, ctx: JobContext) -> None:
        """ShareX's "pre configured window": the window whose title is set in Capture settings;
        if none is set (or it's not open), pick one interactively and remember its title."""
        cs = ctx.task_settings.capture_settings
        sh = shell()
        if not sh.available:
            return self._capture_window(ctx, None)
        target = None
        if cs.capture_custom_window:
            want = cs.capture_custom_window.lower()
            for w in sh.get_windows():
                if not w.minimized and (want in w.title.lower() or want == w.wm_class.lower()):
                    target = w
                    break
        if target is not None:
            self.after_delay(ctx.task_settings, lambda: self._capture_window(ctx, target))
            return

        def pick_worker():
            # PickWindow blocks until the user clicks, so it runs on a worker thread (GDBus
            # connections are thread-safe); the capture goes back onto the main loop.
            try:
                w = sh.pick_window("Click the window to capture. Esc to cancel.")
            except ShellUnavailable as e:
                GLib.idle_add(lambda: (self.capture_error(e), False)[1])
                return
            if w is None:
                return

            def after() -> bool:
                self.store.app.default_task_settings.capture_settings.capture_custom_window = w.title
                self.store.save_app()
                self.after_delay(ctx.task_settings, lambda: self._capture_window(ctx, w))
                return False
            GLib.timeout_add(150, after)
        import threading
        threading.Thread(target=pick_worker, daemon=True, name="pick-window").start()

    def _region_mode(self, ts: TaskSettings) -> RegionCaptureMode:
        return RegionCaptureMode.Default if ts.advanced_settings.region_capture_disable_annotation else \
            RegionCaptureMode.Annotation

    def job_RectangleRegion(self, ctx: JobContext) -> None:
        win = self.active_window()

        def go():
            def outcome(o: RegionOutcome) -> None:
                r = o.result.result
                if r in (RegionResult.Close, RegionResult.AnnotateCancelTask) or o.image is None:
                    return
                if r == RegionResult.Fullscreen:
                    return self.job_PrintScreen(ctx)
                if r in (RegionResult.ActiveMonitor, RegionResult.Monitor):
                    return self.job_ActiveMonitor(ctx)
                if o.rect_logical:
                    CaptureJobs.last_region = o.rect_logical
                from ..sounds import play
                play("Capture", ctx.task_settings.general_settings)
                self.tasks.run_image(o.image, ctx.task_settings, window=window_info(o.window or win),
                                     region=o.rect_logical, workflow_name=ctx.workflow_name)
            self.run_region(self._region_mode(ctx.task_settings), ctx.task_settings, outcome)
        self.after_delay(ctx.task_settings, go)

    def job_CustomRegion(self, ctx: JobContext) -> None:
        r = ctx.task_settings.capture_settings.capture_custom_region
        if r.empty:
            # Not configured yet: select it once, then it's remembered (Capture settings).
            def outcome(o: RegionOutcome) -> None:
                if o.rect_logical:
                    rect = Rect(*o.rect_logical)
                    self.store.app.default_task_settings.capture_settings.capture_custom_region = rect
                    if ctx.workflow is not None:
                        ctx.workflow.task_settings.capture_settings.capture_custom_region = rect
                    self.store.save()
                    ctx.task_settings.capture_settings.capture_custom_region = rect
                    self.job_CustomRegion(ctx)
            self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)
            return
        self._capture_rect(ctx, r.as_tuple())

    def _capture_rect(self, ctx: JobContext, rect) -> None:
        win = self.active_window()

        def go():
            try:
                cap = backends.capture_area(rect, ctx.task_settings.capture_settings.show_cursor,
                                            self.backend_pref(ctx.task_settings))
            except backends.CaptureError as e:
                return self.capture_error(e)
            self.finish_capture(ctx, cap, win, region=tuple(rect))
        self.after_delay(ctx.task_settings, go)

    def job_LastRegion(self, ctx: JobContext) -> None:
        if CaptureJobs.last_region is None:
            return self.job_RectangleRegion(ctx)
        self._capture_rect(ctx, CaptureJobs.last_region)

    # ---- scrolling / auto capture ---------------------------------------------------------------

    def job_ScrollingCapture(self, ctx: JobContext) -> None:
        from ..windows.scrolling import start_scrolling_capture
        start_scrolling_capture(self, ctx)

    def job_AutoCapture(self, ctx: JobContext) -> None:
        from ..windows.autocapture import open_auto_capture
        open_auto_capture(self, ctx)

    def job_StartAutoCapture(self, ctx: JobContext) -> None:
        from ...capture.autocapture import AUTO
        opts = ctx.task_settings.capture_settings.auto_capture
        rect = None if opts.capture_fullscreen else (CaptureJobs.last_region or
                                                     (opts.region.as_tuple() if not opts.region.empty else None))
        if rect is None and not opts.capture_fullscreen:
            # ShareX: "Start auto capture using last region" falls back to selecting one.
            def outcome(o: RegionOutcome) -> None:
                if o.rect_logical:
                    CaptureJobs.last_region = o.rect_logical
                    AUTO.start(self, ctx, o.rect_logical)
            self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)
            return
        AUTO.start(self, ctx, rect)

    def job_StopAutoCapture(self, ctx: JobContext) -> None:
        from ...capture.autocapture import AUTO
        AUTO.stop()
