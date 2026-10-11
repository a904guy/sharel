"""Helpers for the tool-window UI tests (generator bodies for uihelpers.run_app)."""
from __future__ import annotations

import time

from sharel.core.enums import HotkeyType
from sharel.core.jobs import JobContext


def ctx_for(app, job: HotkeyType = HotkeyType.HashCheck, files: list[str] | None = None) -> JobContext:
    return JobContext(job, app.store.task_settings_for(None), files=list(files or []))


def wait_until(pred, timeout: float = 15.0, step: float = 0.05):
    """``yield from wait_until(lambda: ...)`` inside a run_app generator body."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"condition not met within {timeout}s")
        yield step
