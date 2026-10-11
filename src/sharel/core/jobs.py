"""Every ShareX HotkeyType job, dispatched to the app (ShareX's TaskHelpers.ExecuteJob).

The same entry point serves hotkeys, the CLI (``sharel -RectangleRegion``), the tray menu and the
main window menus: ``dispatch(app, job, workflow)``. Each job maps to a ``job_<Name>`` method on
the app object, which receives a :class:`JobContext`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import HotkeySettings, TaskSettings
from .enums import HotkeyType

log = logging.getLogger("sharel.jobs")


@dataclass
class JobContext:
    job: HotkeyType
    task_settings: TaskSettings
    workflow: HotkeySettings | None = None
    files: list[str] = field(default_factory=list)  # CLI file arguments for tools that take files
    silent: bool = False
    source: str = "menu"  # hotkey | cli | tray | menu | toolbar

    @property
    def workflow_name(self) -> str:
        return self.workflow.name if self.workflow else ""


def handler_name(job: HotkeyType) -> str:
    return f"job_{job.value}"


def all_jobs() -> list[HotkeyType]:
    return [j for j in HotkeyType if j.value != "None"]


def dispatch(app, job: HotkeyType, workflow: HotkeySettings | None = None, *, files: list[str] | None = None,
             silent: bool = False, source: str = "menu") -> None:
    if job.value == "None":
        return
    ts = app.store.task_settings_for(workflow)
    ts.job = job
    ctx = JobContext(job, ts, workflow, list(files or []), silent, source)
    fn = getattr(app, handler_name(job), None)
    if fn is None:
        raise KeyError(f"no handler for job {job.value}")
    log.info("job %s (%s%s)", job.value, source, f", workflow {ctx.workflow_name!r}" if workflow else "")
    fn(ctx)


# Jobs whose capture should be delayed by TaskSettingsCapture.screenshot_delay
CAPTURE_JOBS = {HotkeyType[n] for n in ("PrintScreen", "ActiveWindow", "CustomWindow", "ActiveMonitor",
                                       "RectangleRegion", "CustomRegion", "LastRegion")}
