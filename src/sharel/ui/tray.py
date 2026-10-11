"""The main app's side of the tray: starts the GTK 3 helper (tray_helper.py), sends it ShareX's tray
menu and the icon state (recording / paused / hotkeys disabled / upload progress), and handles
left / double / middle clicks reported by the ShareL extension (TrayClicked)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from gi.repository import Gio, GLib

from ..core.enums import AFTER_CAPTURE_DESCRIPTIONS, AFTER_UPLOAD_DESCRIPTIONS, HOTKEY_DESCRIPTIONS
from ..core.enums import AfterCaptureTasks as ACT
from ..core.enums import AfterUploadTasks as AUT
from ..core.history import HistoryFilter
from . import menus

log = logging.getLogger("sharel.tray")

INDICATOR_ID = "app.sharel.ShareL"


def _job(job: str) -> dict:
    return {"label": HOTKEY_DESCRIPTIONS.get(job, job), "action": "app.job", "target": job}


def _jobs(names: list[str]) -> list[dict]:
    return [_job(n) for n in names]


def build_menu(app) -> list[dict]:
    ts = app.store.app.default_task_settings
    sep = {"separator": True}
    capture = [*_jobs(menus.CAPTURE), {"label": "More", "children": _jobs(menus.CAPTURE_MORE)}]
    tools: list[dict] = []
    for i, sec in enumerate(menus.TOOL_SECTIONS):
        if i:
            tools.append(sep)
        tools += _jobs(sec)
    workflows = [{"label": hk.name, "action": "app.workflow", "target": i}
                 for i, hk in enumerate(app.store.hotkeys.hotkeys)] or [{"label": "(none)", "sensitive": False}]
    ac = [{"label": label, "action": "app.toggle-ac", "target": name, "check": True,
           "checked": bool(ts.after_capture_job & ACT[name])} for name, label in AFTER_CAPTURE_DESCRIPTIONS.items()]
    au = [{"label": label, "action": "app.toggle-au", "target": name, "check": True,
           "checked": bool(ts.after_upload_job & AUT[name])} for name, label in AFTER_UPLOAD_DESCRIPTIONS.items()]
    dests = _destination_items(app)
    recent = []
    if app.store.app.recent_tasks_show_in_tray_menu:
        items = app.history.search(HistoryFilter(), newest_first=True, limit=app.store.app.recent_tasks_max_count)
        if not app.store.app.recent_tasks_tray_menu_most_recent_first:
            items = list(reversed(items))
        recent = [{"label": (it.result_url or it.file_name or it.file_path)[:80], "action": "app.open-recent",
                   "target": it.id} for it in items]
    hotkeys_label = "Enable hotkeys" if app.store.app.disable_hotkeys else "Disable hotkeys"
    menu = [
        {"label": "Capture", "children": capture},
        {"label": "Upload", "children": _jobs(menus.UPLOAD)},
        {"label": "Workflows", "children": workflows},
        {"label": "Tools", "children": tools},
        sep,
        {"label": "After capture tasks", "children": ac},
        {"label": "After upload tasks", "children": au},
        {"label": "Destinations", "children": dests},
        {"label": "Task settings…", "action": "app.open-window", "target": "task-settings"},
        {"label": "Application settings…", "action": "app.open-window", "target": "app-settings"},
        {"label": "Hotkey settings…", "action": "app.open-window", "target": "hotkey-settings"},
        sep,
        {"label": "Screenshots folder…", "action": "app.job", "target": "OpenScreenshotsFolder"},
        {"label": "History…", "action": "app.job", "target": "OpenHistory"},
        {"label": "Image history…", "action": "app.job", "target": "OpenImageHistory"},
    ]
    if recent:
        menu.append({"label": "Recent items", "children": recent})
    menu += [
        sep,
        {"label": "Show main window", "action": "app.show"},
        {"label": "Toggle actions toolbar", "action": "app.job", "target": "ToggleActionsToolbar"},
        {"label": hotkeys_label, "action": "app.job", "target": "DisableHotkeys"},
        {"label": "About…", "action": "app.open-window", "target": "about"},
        sep,
        {"label": "Exit", "action": "app.quit"},
    ]
    return menu


def _destination_items(app) -> list[dict]:
    from ..core.enums import FileDestination, ImageDestination, TextDestination, UrlShortenerType, URLSharingServices
    from ..uploaders import registry
    ts = app.store.app.default_task_settings
    out = []
    for label, attr, category, enum in (("Image uploaders", "image_destination", "image", ImageDestination),
                                        ("Text uploaders", "text_destination", "text", TextDestination),
                                        ("File uploaders", "file_destination", "file", FileDestination),
                                        ("URL shorteners", "url_shortener_destination", "shortener", UrlShortenerType),
                                        ("URL sharing services", "url_sharing_service_destination", "sharing",
                                         URLSharingServices)):
        cur = getattr(ts, attr).value
        children = []
        for v in enum:
            if v.value.startswith("Custom") or v.value == "FileUploader":
                title = "Custom uploader" if v.value.startswith("Custom") else "File uploader"
            else:
                info = registry.get(category, v.value)
                if info is None or not info.available:
                    continue
                title = info.title
            children.append({"label": title, "action": "app.set-dest", "target": f"{attr}={v.value}", "check": True,
                             "checked": v.value == cur})
        out.append({"label": label, "children": children})
    out.append({"separator": True})
    out.append({"label": "Destination settings…", "action": "app.open-window", "target": "destination-settings"})
    out.append({"label": "Custom uploader settings…", "action": "app.open-window", "target": "custom-uploaders"})
    return out


class TrayClient:
    def __init__(self, app) -> None:
        self.app = app
        self.proc: subprocess.Popen | None = None
        self._sub = 0
        self._last_state = None

    def start(self) -> None:
        if os.environ.get("SHAREL_NO_TRAY"):
            return
        try:
            self.proc = subprocess.Popen([sys.executable, "-m", "sharel.ui.tray_helper"], stdin=subprocess.PIPE,
                                         text=True, bufsize=1)
        except OSError as e:
            log.warning("tray helper failed to start: %s", e)
            self.proc = None
            return
        self.send_menu()
        self.send_state()
        self._hook_extension()

    def stop(self) -> None:
        if self._sub:
            from ..shell.client import shell
            sh = shell()
            if sh.available:
                sh.conn.signal_unsubscribe(self._sub)
            self._sub = 0
        if self.proc and self.proc.poll() is None:
            self._write({"cmd": "quit"})
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _write(self, msg: dict) -> bool:
        if not self.proc or self.proc.poll() is not None or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def send(self, what: str) -> None:
        if what == "menu":
            # ToggleTrayMenu: the extension opens the indicator's menu.
            from ..shell.client import ShellUnavailable, shell
            sh = shell()
            if sh.available:
                try:
                    if sh.toggle_tray_menu(INDICATOR_ID):
                        return
                except ShellUnavailable as e:
                    log.info("ToggleTrayMenu: %s", e)
            self.app.message("ShareL", "Opening the tray menu needs the ShareL GNOME Shell extension.")

    def send_menu(self) -> None:
        self._write({"cmd": "menu", "menu": build_menu(self.app)})

    def send_recent(self, _history) -> None:
        self.send_menu()

    def send_state(self, icon: str | None = None, tooltip: str = "ShareL") -> None:
        if icon is None:
            from ..record.controller import CONTROLLER
            if CONTROLLER.active:
                icon = "paused" if CONTROLLER.paused else "recording"
                tooltip = "ShareL: recording (paused)" if CONTROLLER.paused else "ShareL: recording"
            elif self.app.store.app.disable_hotkeys:
                icon, tooltip = "disabled", "ShareL: hotkeys disabled"
            else:
                icon = ""
        state = (icon, tooltip)
        if state != self._last_state:
            self._last_state = state
            self._write({"cmd": "state", "icon": icon, "tooltip": tooltip})
        self.send_menu()

    def progress(self, fraction: float | None) -> None:
        if not self.app.store.app.tray_icon_progress_enabled:
            return
        if fraction is None:
            return self.send_state()
        step = min(75, int(fraction * 4) * 25)
        self._write({"cmd": "state", "icon": f"progress-{step}", "tooltip": f"ShareL: {fraction * 100:.0f}%"})
        self._last_state = None

    def _hook_extension(self) -> None:
        """Left / double / middle click run the TrayClickActions (the extension intercepts them)."""
        from ..shell.client import IFACE, OBJECT_PATH, ShellUnavailable, shell
        from ..shell.client import BUS_NAME as SHELL_BUS
        sh = shell()
        if not sh.available:
            return
        left_menu = self.app.store.app.tray_left_click_action.value == "OpenTrayMenu"
        try:
            sh.configure_tray(INDICATOR_ID, left_menu)
        except ShellUnavailable as e:
            log.info("ConfigureTray: %s", e)
            return

        def on_click(_c, _s, _p, _i, _n, params, _d):
            (which,) = params.unpack()
            GLib.idle_add(lambda: (self.app.on_tray_click(which), False)[1])
        self._sub = sh.conn.signal_subscribe(SHELL_BUS, IFACE, "TrayClicked", OBJECT_PATH, None,
                                             Gio.DBusSignalFlags.NONE, on_click, None)
