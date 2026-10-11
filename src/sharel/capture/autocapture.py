"""Auto capture (ShareX's AutoCaptureForm): capture a region or the screen every N seconds."""
from __future__ import annotations

import logging
import time

from gi.repository import GLib

log = logging.getLogger("sharel.autocapture")


class AutoCapture:
    def __init__(self) -> None:
        self.source = 0
        self.rect: tuple[int, int, int, int] | None = None
        self.count = 0
        self.started_at = 0.0
        self.next_at = 0.0
        self.listeners: list = []
        self._app = None
        self._ctx = None

    @property
    def running(self) -> bool:
        return bool(self.source)

    def start(self, app, ctx, rect: tuple[int, int, int, int] | None) -> None:
        self.stop()
        self._app, self._ctx, self.rect = app, ctx, (tuple(rect) if rect else None)
        self.count = 0
        self.started_at = time.monotonic()
        interval = max(0.5, float(ctx.task_settings.capture_settings.auto_capture.repeat_time))
        self._shoot()
        self.next_at = time.monotonic() + interval
        self.source = GLib.timeout_add(int(interval * 1000), self._tick, interval)
        self._emit()

    def _tick(self, interval: float) -> bool:
        if not self.source:
            return False
        self._shoot()
        self.next_at = time.monotonic() + interval
        self._emit()
        return True

    def _shoot(self) -> None:
        from . import backends
        app, ctx = self._app, self._ctx
        cs = ctx.task_settings.capture_settings
        try:
            if self.rect is None:
                cap = backends.capture_screen(cs.show_cursor, app.backend_pref(ctx.task_settings))
            else:
                cap = backends.capture_area(self.rect, cs.show_cursor, app.backend_pref(ctx.task_settings))
        except backends.CaptureError as e:
            log.warning("auto capture failed: %s", e)
            return
        self.count += 1
        app.finish_capture(ctx, cap, None, region=self.rect)

    def stop(self) -> None:
        if self.source:
            GLib.source_remove(self.source)
        self.source = 0
        self._emit()

    def _emit(self) -> None:
        for cb in list(self.listeners):
            cb(self)


AUTO = AutoCapture()
