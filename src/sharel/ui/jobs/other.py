"""The "Other" jobs: hotkey toggle, windows, folders, toolbar, tray menu, exit."""
from __future__ import annotations

from ...core import desktop
from ...core.jobs import JobContext


class OtherJobs:
    def job_DisableHotkeys(self, ctx: JobContext) -> None:
        self.store.app.disable_hotkeys = not self.store.app.disable_hotkeys
        self.store.save_app()
        # The DisableHotkeys hotkey itself must keep working, so keep the registrations and only
        # ignore activations (on_hotkey checks the flag) — re-register to refresh states.
        state = "disabled" if self.store.app.disable_hotkeys else "enabled"
        self.message("Hotkeys " + state, "Press the Disable/Enable hotkeys hotkey again to toggle.")
        if self.tray:
            self.tray.send_state()

    def job_OpenMainWindow(self, ctx: JobContext) -> None:
        self.show_main_window()

    def job_OpenScreenshotsFolder(self, ctx: JobContext) -> None:
        desktop.open_folder(str(self.store.screenshots_folder(ctx.task_settings)))

    def job_OpenHistory(self, ctx: JobContext) -> None:
        from ..windows.history import open_history
        open_history(self)

    def job_OpenImageHistory(self, ctx: JobContext) -> None:
        from ..windows.history import open_image_history
        open_image_history(self)

    def job_ToggleActionsToolbar(self, ctx: JobContext | None) -> None:
        from ..windows.actionstoolbar import toggle_actions_toolbar
        toggle_actions_toolbar(self)

    def job_ToggleTrayMenu(self, ctx: JobContext) -> None:
        if self.tray is None:
            self.start_tray()
        self.tray.send("menu")

    def job_ExitShareX(self, ctx: JobContext) -> None:
        self.quit_app()
