"""The ShareL application: one Adw.Application (app.sharel.ShareL), single instance.

A second ``sharel ...`` forwards its command line here (GApplication command-line handling). The
app keeps running in the background (tray), like ShareX. Jobs (``job_<HotkeyType>``) come from the
mixins in :mod:`sharel.ui.jobs`.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from . import __version__
from .cli import CliRequest, parse_args
from .core import clipboard, paths
from .core.config import ConfigStore, HotkeySettings
from .core.enums import HotkeyType, ThemeMode, ToastClickAction
from .core.history import HistoryManager
from .core.jobs import dispatch
from .core.tasks import TaskInfo, TaskManager
from .ui.jobs import AllJobs

log = logging.getLogger("sharel")


def setup_logging(dev: bool = False) -> Path:
    d = paths.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    import datetime as dt
    path = d / f"ShareL-Log-{dt.date.today():%Y-%m}.txt"
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        sh.setLevel(logging.DEBUG if dev else logging.WARNING)
        root.addHandler(sh)
    # The Debug log window's in-memory copy, independent of other file handlers (e.g. pytest's).
    from .ui.debuglog import MEMORY_HANDLER
    if MEMORY_HANDLER not in root.handlers:
        root.addHandler(MEMORY_HANDLER)
    root.setLevel(logging.DEBUG if dev else logging.INFO)
    return path


class ShareLApp(AllJobs, Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=paths.APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store: ConfigStore | None = None
        self.history: HistoryManager | None = None
        self.tasks: TaskManager | None = None
        self.main_window = None
        self.notifier = None
        self.hotkeys = None
        self.tray = None
        self.watchers = None
        self._held = False
        self._autoclose_pending = 0
        self.log_path: Path | None = None
        self.started_silent = False
        self.init_jobs()

    # ---- lifecycle ----------------------------------------------------------------------

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.log_path = setup_logging(bool(os.environ.get("SHAREL_DEV")))
        log.info("ShareL %s starting (pid %d)", __version__, os.getpid())
        self.store = ConfigStore()
        from .core import i18n
        i18n.set_language(self.store.app.language)
        self.history = HistoryManager(paths.history_path())
        from .ui.host import GtkTaskHost
        self.tasks = TaskManager(self.store, self.history, GtkTaskHost(self))
        self.tasks.add_listener(self._on_task_event)
        self.apply_theme()
        from .uploaders.services import _file_helpers
        _file_helpers.URL_ENCODE_IGNORE_EMOJI = self.store.app.url_encode_ignore_emoji
        self._install_gtk_clipboard()
        from .ui.notify import Notifier
        self.notifier = Notifier(self.on_toast_action)
        self._add_actions()
        self.register_hotkeys()
        from .core.watchfolders import WatchFolderManager
        self.watchers = WatchFolderManager(self)
        self.watchers.apply()
        from .core import autostart
        autostart.sync(self.store.app.start_with_system)
        if self.store.app.show_tray:
            self.start_tray()
        if self.store.app.actions_toolbar_run_at_startup:
            GLib.timeout_add(500, lambda: (self.job_ToggleActionsToolbar(None), False)[1])
        self.hold()
        self._held = True
        self.store.save()

    def do_shutdown(self) -> None:
        log.info("ShareL shutting down")
        try:
            if self.hotkeys:
                self.hotkeys.unregister()
            if self.tray:
                self.tray.stop()
            if self.watchers:
                self.watchers.stop()
            self.shutdown_jobs()
            if self.store:
                self.store.save()
        finally:
            Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        self.show_main_window()

    def do_command_line(self, cl: Gio.ApplicationCommandLine) -> int:
        argv = cl.get_arguments()[1:]
        req = parse_args(argv)
        cwd = cl.get_cwd() or os.getcwd()
        if req.errors:
            for e in req.errors:
                cl.printerr(f"sharel: {e}\n")
            return 2
        first = not cl.get_is_remote()
        self.handle_request(req, cwd, first)
        return 0

    def handle_request(self, req: CliRequest, cwd: str = "", first: bool = False) -> None:
        def absolute(p: str) -> str:
            return p if os.path.isabs(p) else os.path.join(cwd or os.getcwd(), p)

        if first:
            self.started_silent = req.silent or self.store.app.silent_run
        if not req.has_work:
            if not (first and self.started_silent):
                self.show_main_window()
            return
        if req.autoclose:
            self._autoclose_pending = 1
        for job, files in req.jobs:
            dispatch(self, job, files=[absolute(f) for f in files], source="cli")
        for name in req.workflows:
            wf = self.store.find_workflow(name)
            if wf is None:
                self.message("Workflow not found", f"No workflow named \"{name}\".", urgent=True)
                continue
            dispatch(self, wf.job, wf, source="cli")
        if req.files:
            dispatch(self, HotkeyType.FileUpload, files=[absolute(f) for f in req.files], source="cli")
        for url in req.urls:
            self.tasks.download_upload(url, self.store.task_settings_for(None))
        if req.autoclose:
            GLib.timeout_add(500, self._check_autoclose)

    def _check_autoclose(self) -> bool:
        if (self.tasks and self.tasks.running) or self.busy_interactive():
            return True
        self.quit_app()
        return False

    def quit_app(self) -> None:
        if self._held:
            self.release()
            self._held = False
        self.quit()

    # ---- windows ----------------------------------------------------------------------------

    def show_main_window(self) -> None:
        if self.main_window is None:
            from .ui.main_window import MainWindow
            self.main_window = MainWindow(self)
        self.main_window.present()

    def active_parent(self) -> Gtk.Window | None:
        w = self.get_active_window()
        if w is not None and w.get_visible():
            return w
        return self.main_window if self.main_window and self.main_window.get_visible() else None

    def apply_theme(self) -> None:
        sm = Adw.StyleManager.get_default()
        mode = self.store.app.theme_mode
        sm.set_color_scheme({ThemeMode.Light: Adw.ColorScheme.FORCE_LIGHT,
                             ThemeMode.Dark: Adw.ColorScheme.FORCE_DARK}.get(mode, Adw.ColorScheme.DEFAULT))

    def _install_gtk_clipboard(self) -> None:
        """The last link of the clipboard chain: GTK's clipboard, usable while we have focus."""
        def writer(mime: str, data: bytes) -> bool:
            from .ui.gtkutil import call_main
            from gi.repository import Gdk

            def run():
                if not self.get_active_window():
                    return False
                display = Gdk.Display.get_default()
                if display is None:
                    return False
                provider = Gdk.ContentProvider.new_for_bytes(mime, GLib.Bytes.new(data))
                display.get_clipboard().set_content(provider)
                return True
            return bool(call_main(run, timeout=5))
        clipboard.gtk_writer = writer

    # ---- actions (tray, menus, notifications) -------------------------------------------------

    def _add_actions(self) -> None:
        def add(name: str, cb, param: str | None = None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)

        # shared by the tray menu and the main window's menus
        add("job", lambda _a, p: dispatch(self, HotkeyType[p.unpack()], source="menu"), "s")
        add("workflow", lambda _a, p: self._run_workflow_index(p.unpack()), "i")
        add("show", lambda *_: self.show_main_window())
        add("quit", lambda *_: self.quit_app())
        add("open-recent", lambda _a, p: self._open_recent(p.unpack()), "s")
        add("tray-click", lambda _a, p: self.on_tray_click(p.unpack()), "s")
        add("toggle-ac", lambda _a, p: self._toggle_flag("ac", p.unpack()), "s")
        add("toggle-au", lambda _a, p: self._toggle_flag("au", p.unpack()), "s")
        add("set-dest", lambda _a, p: self._set_destination(p.unpack()), "s")
        from .ui.windows import open_named
        add("open-window", lambda _a, p: open_named(self, p.unpack()), "s")
        add("native", lambda _a, p: self.on_native_message(p.unpack()), "s")
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def on_native_message(self, payload: str) -> str:
        """A browser extension request (ShareX's NativeMessagingAction) from sharel-native-host."""
        import json
        from .core.enums import NativeMessagingAction as N
        msg = json.loads(payload)
        action = N[msg.get("Action", "None")]
        url, text = msg.get("URL", ""), msg.get("Text", "")
        ts = self.store.task_settings_for(None)
        if action == N.UploadImage and ts.advanced_settings.process_images_during_browser_upload:
            self._download_image_then_process(url, ts)
        elif action in (N.UploadImage, N.UploadVideo, N.UploadAudio):
            self.tasks.download_upload(url, ts)
        elif action == N.UploadText:
            self.tasks.upload_text(text, ts)
        elif action == N.ShortenURL:
            self.tasks.shorten_url(url, ts)
        log.info("native message: %s", action.value)
        return action.value

    def _download_image_then_process(self, url: str, ts) -> None:
        """Browser image upload with image processing: download it, then run the after-capture tasks."""
        import io
        import threading

        import requests
        from PIL import Image

        def work():
            try:
                r = requests.get(url, timeout=self.store.app.request_timeout_seconds,
                                 proxies=self.store.app.proxy_settings.requests_proxies())
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            except Exception as e:
                err = str(e)  # `e` is unbound once the except block ends
                GLib.idle_add(lambda: (self.message("Browser upload failed", err, urgent=True), False)[1])
                return
            GLib.idle_add(lambda: (self.tasks.run_image(img, ts), False)[1])
        threading.Thread(target=work, daemon=True, name="browser-image").start()

    def _toggle_flag(self, kind: str, name: str) -> None:
        from .core.enums import AfterCaptureTasks, AfterUploadTasks
        ts = self.store.app.default_task_settings
        if kind == "ac" and name in AfterCaptureTasks.__members__:
            ts.after_capture_job ^= AfterCaptureTasks[name]
        elif kind == "au" and name in AfterUploadTasks.__members__:
            ts.after_upload_job ^= AfterUploadTasks[name]
        self.store.save_app()
        self.settings_changed()

    def _set_destination(self, spec: str) -> None:
        import typing

        from .core.config import TaskSettings
        attr, _, value = spec.partition("=")
        enum_cls = typing.get_type_hints(TaskSettings).get(attr)
        if enum_cls is None or value not in enum_cls.__members__:
            return
        setattr(self.store.app.default_task_settings, attr, enum_cls[value])
        self.store.save_app()
        self.settings_changed()

    def settings_changed(self) -> None:
        """Config edited somewhere (settings window, tray, main window): refresh what shows it."""
        if self.tray:
            self.tray.send_menu()
        if self.main_window is not None:
            self.main_window._sync_flag_states()

    def _run_workflow_index(self, idx: int) -> None:
        hks = self.store.hotkeys.hotkeys
        if 0 <= idx < len(hks):
            dispatch(self, hks[idx].job, hks[idx], source="menu")

    def _open_recent(self, item_id: str) -> None:
        for it in self.history.items():
            if it.id == item_id:
                from .core import desktop
                if it.result_url:
                    desktop.open_url(it.result_url)
                elif it.file_path:
                    desktop.open_file(it.file_path)
                return

    def on_tray_click(self, which: str) -> None:
        from .core.enums import TrayClickAction
        cfg = self.store.app
        action = {"left": cfg.tray_left_click_action, "double": cfg.tray_left_double_click_action,
                  "middle": cfg.tray_middle_click_action}.get(which, TrayClickAction.OpenMainWindow)
        if action.value == "None":
            return
        if action.value == "OpenTrayMenu":
            if self.tray:
                self.tray.send("menu")
            return
        dispatch(self, HotkeyType[action.value], source="tray")

    # ---- hotkeys ------------------------------------------------------------------------------

    def register_hotkeys(self) -> None:
        from .shell.hotkeys import HotkeyManager
        if self.hotkeys is None:
            self.hotkeys = HotkeyManager(self.on_hotkey, self.store.app.hotkey_backend.value)
        self.hotkeys.backend_pref = self.store.app.hotkey_backend.value
        # "Disable hotkeys" keeps the grabs (so the DisableHotkeys hotkey can switch them back on);
        # on_hotkey ignores the other activations while the flag is set.
        self.hotkeys.disabled = bool(os.environ.get("SHAREL_DISABLE_HOTKEYS"))
        self.hotkeys.register(self.store.hotkeys.hotkeys)
        GLib.timeout_add_seconds(3, self._refresh_hotkey_states)

    def _refresh_hotkey_states(self) -> bool:
        if self.hotkeys:
            self.hotkeys.refresh_states()
        return GLib.SOURCE_REMOVE

    def on_hotkey(self, hk: HotkeySettings) -> None:
        cfg = self.store.app
        if cfg.disable_hotkeys and hk.job != HotkeyType.DisableHotkeys:
            return
        # Application settings > Advanced: ignore repeats faster than hotkey_repeat_limit ms, and
        # (optionally) every hotkey while the focused window is fullscreen (games, presentations).
        import time
        now = time.monotonic()
        last = getattr(self, "_hotkey_last", {})
        self._hotkey_last = last
        if cfg.hotkey_repeat_limit > 0 and now - last.get(id(hk), -1e9) < cfg.hotkey_repeat_limit / 1000:
            return
        last[id(hk)] = now
        if cfg.disable_hotkeys_on_fullscreen and self._focused_window_fullscreen():
            return
        GLib.idle_add(lambda: (dispatch(self, hk.job, hk, source="hotkey"), False)[1])

    @staticmethod
    def _focused_window_fullscreen() -> bool:
        from .shell.client import ShellUnavailable, shell
        sh = shell()
        if not sh.available:
            return False
        try:
            w = sh.get_active_window()
        except ShellUnavailable:
            return False
        return bool(w and w.fullscreen)

    # ---- tasks ----------------------------------------------------------------------------------

    def _on_task_event(self, event: str, info: TaskInfo) -> None:
        def ui():
            if self.main_window is not None:
                self.main_window.on_task_event(event, info)
            if self.tray is not None and event == "finished":
                self.tray.send_recent(self.history)
            return False
        GLib.idle_add(ui)

    def post_toast(self, info: TaskInfo, ts) -> None:
        if ts.general_settings.show_toast_notification_after_task_completed and self.notifier:
            self.notifier.notify(info, ts.general_settings)

    def message(self, summary: str, body: str = "", urgent: bool = False) -> None:
        if self.notifier:
            self.notifier.message(summary, body, urgent)

    def on_toast_action(self, action: ToastClickAction, info: TaskInfo) -> None:
        from .ui.toastactions import run_toast_action
        run_toast_action(self, action, info)

    # ---- tray -------------------------------------------------------------------------------------

    def start_tray(self) -> None:
        from .ui.tray import TrayClient
        if self.tray is None:
            self.tray = TrayClient(self)
            self.tray.start()

    def stop_tray(self) -> None:
        if self.tray:
            self.tray.stop()
            self.tray = None


def run(argv: list[str]) -> int:
    app = ShareLApp()
    return app.run(argv)
