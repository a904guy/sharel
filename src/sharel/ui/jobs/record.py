"""Screen recording jobs (ShareX's ScreenRecordForm hotkeys): a hotkey that starts a recording
stops it when pressed again."""
from __future__ import annotations

from ...core.enums import RegionCaptureMode
from ...core.jobs import JobContext
from ...editor.manager import RegionResult


class RecordJobs:
    @property
    def recorder(self):
        from ...record.controller import CONTROLLER
        return CONTROLLER

    def _record_select(self, ctx: JobContext, gif: bool) -> None:
        if self.recorder.active:
            return self.recorder.stop()

        def outcome(o):
            if o.result.result == RegionResult.Close or not o.rect_logical:
                return
            from .capture import CaptureJobs
            CaptureJobs.last_region = o.rect_logical
            self.recorder.start(self, ctx, o.rect_logical, gif=gif)
        self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)

    def _record_active_window(self, ctx: JobContext, gif: bool) -> None:
        if self.recorder.active:
            return self.recorder.stop()
        win = self.active_window()
        if win is None:
            self.message("Screen recording", "No active window (the ShareL extension is needed); select a region.")
            return self._record_select(ctx, gif)
        self.recorder.start(self, ctx, tuple(win.frame), gif=gif, window=win)

    def _record_custom_region(self, ctx: JobContext, gif: bool) -> None:
        if self.recorder.active:
            return self.recorder.stop()
        r = ctx.task_settings.capture_settings.capture_custom_region
        if r.empty:
            return self._record_select(ctx, gif)
        self.recorder.start(self, ctx, r.as_tuple(), gif=gif)

    def _record_last_region(self, ctx: JobContext, gif: bool) -> None:
        if self.recorder.active:
            return self.recorder.stop()
        from .capture import CaptureJobs
        if CaptureJobs.last_region is None:
            return self._record_select(ctx, gif)
        self.recorder.start(self, ctx, CaptureJobs.last_region, gif=gif)

    def job_ScreenRecorder(self, ctx: JobContext) -> None:
        self._record_select(ctx, gif=False)

    def job_ScreenRecorderActiveWindow(self, ctx: JobContext) -> None:
        self._record_active_window(ctx, gif=False)

    def job_ScreenRecorderCustomRegion(self, ctx: JobContext) -> None:
        self._record_custom_region(ctx, gif=False)

    def job_StartScreenRecorder(self, ctx: JobContext) -> None:
        self._record_last_region(ctx, gif=False)

    def job_ScreenRecorderGIF(self, ctx: JobContext) -> None:
        self._record_select(ctx, gif=True)

    def job_ScreenRecorderGIFActiveWindow(self, ctx: JobContext) -> None:
        self._record_active_window(ctx, gif=True)

    def job_ScreenRecorderGIFCustomRegion(self, ctx: JobContext) -> None:
        self._record_custom_region(ctx, gif=True)

    def job_StartScreenRecorderGIF(self, ctx: JobContext) -> None:
        self._record_last_region(ctx, gif=True)

    def job_StopScreenRecording(self, ctx: JobContext) -> None:
        self.recorder.stop()

    def job_PauseScreenRecording(self, ctx: JobContext) -> None:
        self.recorder.toggle_pause()

    def job_AbortScreenRecording(self, ctx: JobContext) -> None:
        self.recorder.abort()
