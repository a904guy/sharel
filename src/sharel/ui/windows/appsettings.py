"""Application settings (ShareX's ApplicationSettingsForm).

Pages: General, Theme, Paths, Settings (backup export/import/reset), Main window, History, Print,
Proxy, Upload, Clipboard upload, Advanced. Every edit saves ApplicationConfig.json at once and
calls ``app.settings_changed()``; edits with side effects apply them right away (theme, tray,
autostart entry, hotkey backend, upload limit, keyring use, log level).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import zipfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ... import __version__
from ...core import paths
from ...core.config import ApplicationConfig, ConfigStore, HotkeysConfig, ProxyInfo
from ...core.enums import HotkeyBackendName, ProxyMethod
from ...core.serialize import from_jsonable, to_jsonable
from .. import gtkutil
from ..forms import Form
from ._sectioned import SectionedWindow, WindowRegistry, button_row, group

log = logging.getLogger("sharel.ui.appsettings")

_REGISTRY = WindowRegistry()

LANGUAGES = ["Automatic", "English"]
BACKUP_FILES = ("ApplicationConfig.json", "HotkeysConfig.json", "UploadersConfig.json")
HISTORY_FILE = "History.json"

HOTKEY_BACKEND_TEXT = {
    "Auto": "Automatic (extension, then the GlobalShortcuts portal)",
    "Extension": "ShareL GNOME Shell extension",
    "GlobalShortcutsPortal": "GlobalShortcuts portal",
    "GSettings": "GNOME custom keyboard shortcuts (edits GNOME's settings)",
}


def open_app_settings(app) -> AppSettingsWindow:
    return _REGISTRY.get_or_create(id(app), lambda: AppSettingsWindow(app))


# ---- backup ---------------------------------------------------------------------------------------


def _load_all_services(cfg) -> None:
    """Turn every stored service dict into its settings object (resolving keyring secrets), so an
    export carries the real values and a save after import moves secrets into the keyring."""
    from ...uploaders import registry
    for key in list(cfg.services):
        cat, _s, name = key.partition("/")
        info = registry.get(cat, name)
        if info is not None and info.settings_cls is not None:
            try:
                cfg.settings_for(key, info.settings_cls)
            except Exception:
                log.warning("cannot load uploader settings %s", key, exc_info=True)


def export_settings(app, path: str | Path, include_settings: bool = True, include_history: bool = False) -> list[str]:
    """Write a backup zip (ShareX's .sxb): the three config files and optionally the history.
    Uploader passwords/tokens are included in plain text (as in ShareX's backup)."""
    store: ConfigStore = app.store
    written: list[str] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if include_settings:
            _load_all_services(store.uploaders)
            for name, obj in zip(BACKUP_FILES, (store.app, store.hotkeys, store.uploaders), strict=True):
                z.writestr(name, json.dumps(to_jsonable(obj), indent=2, ensure_ascii=False))
                written.append(name)
        if include_history and app.history is not None:
            z.writestr(HISTORY_FILE, json.dumps([to_jsonable(i) for i in app.history.items()], indent=2,
                                                ensure_ascii=False))
            written.append(HISTORY_FILE)
        z.writestr("ShareL.txt", f"ShareL {__version__} settings backup\n")
    return written


def read_backup(path: str | Path) -> dict[str, object]:
    """Parse and validate a backup zip; nothing is changed. Raises ValueError for a bad file."""
    from ...uploaders.config import UploadersConfig
    classes = {"ApplicationConfig.json": ApplicationConfig, "HotkeysConfig.json": HotkeysConfig,
               "UploadersConfig.json": UploadersConfig}
    out: dict[str, object] = {}
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for name, cls in classes.items():
                if name in names:
                    out[name] = from_jsonable(cls, json.loads(z.read(name).decode("utf-8")))
            if HISTORY_FILE in names:
                data = json.loads(z.read(HISTORY_FILE).decode("utf-8"))
                if not isinstance(data, list):
                    raise ValueError("History.json is not a list")
                out[HISTORY_FILE] = data
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"Not a ShareL settings backup: {e}") from e
    if not out:
        raise ValueError("The file contains no ShareL settings")
    return out


def import_settings(app, path: str | Path) -> list[str]:
    """Load a backup into the running app: the config objects are replaced on ``app.store`` (which
    every component reads through), saved, and hotkeys / watchers / theme / tray are refreshed."""
    data = read_backup(path)
    store: ConfigStore = app.store
    with store._lock:
        if "ApplicationConfig.json" in data:
            store.app = data["ApplicationConfig.json"]
        if "HotkeysConfig.json" in data:
            store.hotkeys = data["HotkeysConfig.json"]
        if "UploadersConfig.json" in data:
            store.uploaders = data["UploadersConfig.json"]
            _load_all_services(store.uploaders)
    store.save()
    if HISTORY_FILE in data and app.history is not None:
        hp = app.history.path
        hp.parent.mkdir(parents=True, exist_ok=True)
        tmp = hp.with_suffix(".import")
        tmp.write_text(json.dumps(data[HISTORY_FILE], indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(hp)
        app.history.reload()
    apply_side_effects(app)
    return sorted(data)


def apply_side_effects(app) -> None:
    """Make the running app match ApplicationConfig after a bulk change (import/reset)."""
    cfg = app.store.app
    from ...core import autostart
    app.apply_theme()
    autostart.sync(cfg.start_with_system)
    if cfg.show_tray:
        app.start_tray()
    else:
        app.stop_tray()
    if app.tasks is not None:
        app.tasks.set_upload_limit(cfg.upload_limit)
    from ...uploaders.services import _file_helpers
    _file_helpers.URL_ENCODE_IGNORE_EMOJI = cfg.url_encode_ignore_emoji
    logging.getLogger().setLevel(logging.DEBUG if cfg.dev_mode else logging.INFO)
    app.register_hotkeys()
    if app.watchers is not None:
        app.watchers.apply()
    app.settings_changed()


# ---- proxy test -------------------------------------------------------------------------------------


def check_proxy(proxy: ProxyInfo, url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """HEAD ``url`` through the proxy settings; (ok, message)."""
    import requests
    url = url.strip()
    if not url:
        return False, "Enter a URL to test"
    if "://" not in url:
        url = "http://" + url
    proxies = proxy.requests_proxies()
    if proxy.method == ProxyMethod.Manual and not proxies:
        return False, "Enter the proxy host"
    via = proxies.get("http", "") if proxies else "a direct connection"
    if proxies:
        via = via.split("@")[-1]  # never show the credentials
    try:
        r = requests.head(url, proxies=proxies, timeout=timeout,
                          allow_redirects=False)
    except requests.RequestException as e:
        return False, f"Failed via {via}: {e}"
    return r.status_code < 500, f"HTTP {r.status_code} {r.reason or ''} via {via}".strip()


# ---- the window -------------------------------------------------------------------------------------


class AppSettingsWindow(SectionedWindow):
    WATCHED = ("theme_mode", "show_tray", "start_with_system", "upload_limit", "use_libsecret", "dev_mode",
               "url_encode_ignore_emoji", "disable_hotkeys")

    def __init__(self, app) -> None:
        super().__init__(app, "Application settings", 980, 700)
        self.build()

    @property
    def cfg(self) -> ApplicationConfig:
        return self.app.store.app

    def _snapshot(self) -> dict:
        return {k: getattr(self.cfg, k) for k in self.WATCHED}

    def build(self) -> None:
        current = self.stack.get_visible_child_name()
        self.clear_pages()
        self._snap = self._snapshot()
        self.add_page("general", "General", self._general())
        self.add_page("theme", "Theme", self._theme())
        self.add_page("paths", "Paths", self._paths())
        self.add_page("settings", "Settings", self._backup())
        self.add_page("mainwindow", "Main window", self._main_window())
        self.add_page("history", "History", self._history())
        self.add_page("print", "Print", self._print())
        self.add_page("proxy", "Proxy", self._proxy())
        self.add_page("upload", "Upload", self._upload())
        self.add_page("clipboard", "Clipboard upload", self._clipboard())
        self.add_page("advanced", "Advanced", self._advanced())
        if current and current in self.pages:
            self.show_page(current)

    # -- saving --

    def changed(self) -> None:
        store = self.app.store
        store.save_app()
        new = self._snapshot()
        old, self._snap = self._snap, new
        diff = {k for k in new if new[k] != old[k]}
        if "theme_mode" in diff:
            self.app.apply_theme()
        if "show_tray" in diff:
            if new["show_tray"]:
                self.app.start_tray()
            else:
                self.app.stop_tray()
        if "start_with_system" in diff:
            from ...core import autostart
            autostart.sync(new["start_with_system"])
        if "upload_limit" in diff and self.app.tasks is not None:
            self.app.tasks.set_upload_limit(new["upload_limit"])
        if "use_libsecret" in diff:
            store.save_uploaders()  # moves secrets into (or keeps them out of) the keyring
        if "dev_mode" in diff:
            logging.getLogger().setLevel(logging.DEBUG if new["dev_mode"] else logging.INFO)
        if "url_encode_ignore_emoji" in diff:
            from ...uploaders.services import _file_helpers
            _file_helpers.URL_ENCODE_IGNORE_EMOJI = new["url_encode_ignore_emoji"]
        if "disable_hotkeys" in diff:
            self.app.register_hotkeys()
        if hasattr(self, "folder_preview"):
            self._update_folder_preview()
        self.app.settings_changed()

    def form(self, obj, meta: dict | None = None) -> Form:
        return Form(obj, self.changed, parent=self, meta=meta)

    # -- General --

    def _general(self) -> list[Gtk.Widget]:
        cfg = self.cfg
        lang = Adw.ComboRow(title="Language", subtitle="ShareL's interface is currently available in English",
                            model=Gtk.StringList.new(LANGUAGES),
                            selected=LANGUAGES.index(cfg.language) if cfg.language in LANGUAGES else 0)

        def on_lang(r, _p):
            cfg.language = LANGUAGES[r.get_selected()]
            self.changed()
        lang.connect("notify::selected", on_lang)
        f = self.form(cfg, {"show_tray": {"label": "Show tray icon"},
                            "silent_run": {"label": "Start silently (no main window at startup)"},
                            "tray_icon_progress_enabled": {"label": "Show upload progress in the tray icon"},
                            "tray_auto_expand_capture_menu": {"label": "Expand the capture menu in the tray menu"},
                            "show_main_window_tip": {"label": "Show the main window tip"},
                            "start_with_system": {"label": "Run ShareL when I log in"}})
        rows = f.rows(only=["show_tray", "silent_run", "tray_icon_progress_enabled", "tray_auto_expand_capture_menu",
                            "show_main_window_tip"])
        tray_click = f.rows(only=["tray_left_click_action", "tray_left_double_click_action",
                                  "tray_middle_click_action"])
        startup = f.rows(only=["start_with_system"])
        return [group("General", "", [lang, *rows]), group("Tray icon clicks", "", tray_click),
                group("Startup", "Adds an XDG autostart entry (~/.config/autostart).", startup)]

    # -- Theme --

    def _theme(self) -> list[Gtk.Widget]:
        g = self.form(self.cfg, {"theme_mode": {"label": "Appearance"}}).group(
            "Theme", "System follows GNOME's light/dark style.", only=["theme_mode"])
        return [g]

    # -- Paths --

    def _paths(self) -> list[Gtk.Widget]:
        cfg = self.cfg
        f = self.form(cfg, {"custom_screenshots_path": {"kind": "folder", "label": "Screenshots folder"},
                            "use_custom_screenshots_path": {"label": "Use a custom screenshots folder"},
                            "save_image_sub_folder_pattern": {"label": "Sub folder pattern"},
                            "save_image_sub_folder_pattern_window": {
                                "label": "Sub folder pattern for window captures",
                                "help": "Used for window captures when set (e.g. %pn)"}})
        rows = f.rows(only=["use_custom_screenshots_path", "custom_screenshots_path", "save_image_sub_folder_pattern",
                            "save_image_sub_folder_pattern_window"])
        self.folder_preview = Adw.ActionRow(title="Screenshots are saved to")
        self.folder_preview.add_css_class("property")
        self._update_folder_preview()
        folders = group("Folders", "", [
            button_row("Screenshots folder", "Open", self._open_screenshots_folder),
            button_row("Personal folder (settings)", "Open", lambda: self._open(self.app.store.directory),
                       subtitle=str(self.app.store.directory)),
            button_row("Log folder", "Open", lambda: self._open(paths.log_dir()), subtitle=str(paths.log_dir())),
        ])
        return [group("Screenshots folder", "", [*rows, self.folder_preview]), folders]

    def _update_folder_preview(self) -> None:
        try:
            self.folder_preview.set_subtitle(str(self.app.store.screenshots_folder(None)))
        except Exception as e:
            self.folder_preview.set_subtitle(f"(invalid: {e})")

    def _open_screenshots_folder(self) -> None:
        folder = self.app.store.screenshots_folder(None)
        folder.mkdir(parents=True, exist_ok=True)
        self._open(folder)

    @staticmethod
    def _open(folder) -> None:
        from ...core import desktop
        Path(folder).mkdir(parents=True, exist_ok=True)
        desktop.open_folder(str(folder))

    # -- Settings (backup) --

    def _backup(self) -> list[Gtk.Widget]:
        self.export_settings_row = Adw.SwitchRow(title="Export settings",
                                                 subtitle="Application, hotkeys and uploaders (incl. passwords)",
                                                 active=True)
        self.export_history_row = Adw.SwitchRow(title="Export history", active=False)
        export = button_row("Export a backup", "Export…", self.export_dialog, style="suggested-action")
        imp = button_row("Import a backup", "Import…", self.import_dialog)
        reset = button_row("Reset application settings", "Reset…", self.reset_dialog, style="destructive-action",
                           subtitle="Hotkeys and uploader settings are kept")
        return [group("Backup", "A zip file with ShareL's configuration (like ShareX's .sxb backup).",
                      [self.export_settings_row, self.export_history_row, export, imp]), group("Reset", "", [reset])]

    def export_dialog(self) -> None:
        settings, history = self.export_settings_row.get_active(), self.export_history_row.get_active()
        if not (settings or history):
            gtkutil.alert(self, "Nothing to export", "Choose settings and/or history.")
            return
        import datetime as dt
        name = f"ShareL-{__version__}-backup-{dt.date.today():%Y-%m-%d}.zip"

        def done(path):
            if not path:
                return
            try:
                export_settings(self.app, path, settings, history)
            except OSError as e:
                gtkutil.alert(self, "Export failed", str(e))
                return
            self.toast(f"Exported to {path}")
        gtkutil.save_file(self, "Export settings", name, done)

    def import_dialog(self) -> None:
        def chosen(paths_):
            if paths_:
                self.confirm_import(paths_[0])
        gtkutil.open_files(self, "Import settings", chosen, patterns=["*.zip", "*.sxb"], multiple=False)

    def confirm_import(self, path: str) -> None:
        try:
            content = read_backup(path)
        except ValueError as e:
            gtkutil.alert(self, "Import failed", str(e))
            return

        def resp(r):
            if r != "import":
                return
            try:
                import_settings(self.app, path)
            except (ValueError, OSError) as e:
                gtkutil.alert(self, "Import failed", str(e))
                return
            self.build()
            self.toast("Settings imported")
        gtkutil.ask(self, "Import settings?", "These will replace the current ones: " + ", ".join(sorted(content)),
                    [("cancel", "Cancel"), ("import", "Import")], resp, destructive="import")

    def reset_dialog(self) -> None:
        def resp(r):
            if r == "reset":
                self.reset_settings()
        gtkutil.ask(self, "Reset application settings?", "Every application setting goes back to its default.",
                    [("cancel", "Cancel"), ("reset", "Reset")], resp, destructive="reset")

    def reset_settings(self) -> None:
        old = self.cfg
        new = ApplicationConfig()
        new.first_time_run_date = old.first_time_run_date
        new.name_parser_auto_increment_number = old.name_parser_auto_increment_number
        self.app.store.app = new
        self.app.store.save_app()
        apply_side_effects(self.app)
        self.build()

    # -- Main window --

    def _main_window(self) -> list[Gtk.Widget]:
        f = self.form(self.cfg)
        return [f.group("Main window", "Applied when the main window opens next.",
                        only=["show_menu", "task_view_mode", "show_thumbnail_title", "thumbnail_title_location",
                              "thumbnail_size", "show_columns", "workflows_only_show_edited"])]

    # -- History --

    def _history(self) -> list[Gtk.Widget]:
        cfg = self.cfg
        f = self.form(cfg, {"history_save_tasks": {"label": "Save tasks in history"},
                            "history_check_url": {"label": "Check the URL before adding it to history"},
                            "recent_tasks_save": {"label": "Remember recent tasks between runs"},
                            "recent_tasks_max_count": {"label": "Max recent tasks", "min": 1, "max": 100}})
        hs = self.form(cfg.history_settings, {"max_item_count": {"label": "Max items in the history window",
                                                                 "help": "-1 shows every item", "min": -1}})
        ihs = self.form(cfg.image_history_settings, {"max_item_count": {"label": "Max items in image history",
                                                                        "help": "-1 shows every item", "min": -1}})
        clear = button_row("History", "Clear history…", self.clear_history_dialog, style="destructive-action",
                           subtitle=str(self.app.history.path) if self.app.history else "")
        return [
            group("History", "", f.rows(only=["history_save_tasks", "history_check_url"])
                  + hs.rows(only=["max_item_count", "remember_window_state", "remember_search_text"])
                  + ihs.rows(only=["max_item_count", "thumbnail_size", "filter_missing_files"]) + [clear]),
            group("Recent tasks", "", f.rows(only=["recent_tasks_save", "recent_tasks_max_count",
                                                   "recent_tasks_show_in_main_window", "recent_tasks_show_in_tray_menu",
                                                   "recent_tasks_tray_menu_most_recent_first"])),
        ]

    def clear_history_dialog(self) -> None:
        def resp(r):
            if r == "clear" and self.app.history is not None:
                self.app.history.clear()
                self.app.settings_changed()
                self.toast("History cleared")
        gtkutil.ask(self, "Clear history?", "Every history entry is removed. Files on disk are kept.",
                    [("cancel", "Cancel"), ("clear", "Clear")], resp, destructive="clear")

    # -- Print --

    def _print(self) -> list[Gtk.Widget]:
        f = self.form(self.cfg, {"dont_show_print_settings_dialog": {"label": "Don't show the print settings "
                                                                              "window"}})
        p = self.form(self.cfg.print_settings, {"margin": {"min": 0, "max": 500},
                                                "default_printer_override": {"label": "Printer (empty = default)"}})
        return [group("Print", "", f.rows(only=["dont_show_print_settings_dialog"]) + p.rows())]

    # -- Proxy --

    def _proxy(self) -> list[Gtk.Widget]:
        px = self.cfg.proxy_settings
        f = self.form(px, {"method": {"label": "Proxy"}, "type": {"label": "Type"},
                           "port": {"min": 1, "max": 65535}, "password": {"secret": True}})
        rows = f.rows()
        self.proxy_url = Adw.EntryRow(title="Test URL", text="http://example.com/")
        self.proxy_result = Adw.ActionRow(title="Result", subtitle="Not tested")
        self.proxy_test_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self.proxy_test_button.connect("clicked", lambda *_: self.run_proxy_test())
        self.proxy_url.add_suffix(self.proxy_test_button)
        return [group("Proxy", "Automatic uses the system's proxy settings.", rows),
                group("Test", "Sends a HEAD request to the URL through these settings.",
                      [self.proxy_url, self.proxy_result])]

    def run_proxy_test(self, on_done=None) -> threading.Thread:
        px = dataclasses.replace(self.cfg.proxy_settings)
        url = self.proxy_url.get_text()
        self.proxy_test_button.set_sensitive(False)
        self.proxy_result.set_subtitle("Testing…")

        def work():
            ok, msg = check_proxy(px, url)

            def show():
                self.proxy_result.set_subtitle(("✓ " if ok else "✗ ") + msg)
                self.proxy_test_button.set_sensitive(True)
                if on_done:
                    on_done(ok, msg)
            gtkutil.idle(show)
        t = threading.Thread(target=work, name="proxy-test", daemon=True)
        t.start()
        return t

    # -- Upload --

    def _upload(self) -> list[Gtk.Widget]:
        f = self.form(self.cfg, {"upload_limit": {"label": "Simultaneous uploads", "min": 1, "max": 25},
                                 "max_upload_fail_retry": {"label": "Retries when an upload fails", "min": 0,
                                                           "max": 10},
                                 "request_timeout_seconds": {"label": "Request timeout (seconds)", "min": 5,
                                                             "max": 3600},
                                 "url_encode_ignore_emoji": {"label": "Don't URL-encode emoji"},
                                 "use_secondary_uploaders": {"label": "Use secondary uploaders when one fails"}})
        out = [group("Upload", "", f.rows(only=["upload_limit", "max_upload_fail_retry", "request_timeout_seconds",
                                                "url_encode_ignore_emoji"]))]
        sec = Adw.PreferencesGroup(title="Secondary uploaders",
                                   description="Tried in order when the main destination fails.")
        for r in f.rows(only=["use_secondary_uploaders"]):
            sec.add(r)
        from .tasksettings import destination_choices
        self.secondary_editors = {}
        for attr, cat, title in (("secondary_image_uploaders", "image", "Image uploaders"),
                                 ("secondary_text_uploaders", "text", "Text uploaders"),
                                 ("secondary_file_uploaders", "file", "File uploaders")):
            ed = SecondaryList(self, attr, title, destination_choices(cat))
            self.secondary_editors[attr] = ed
            sec.add(ed)
        out.append(sec)
        return out

    # -- Clipboard upload --

    def _clipboard(self) -> list[Gtk.Widget]:
        up = self.cfg.default_task_settings.upload_settings
        f = self.form(up, {"clipboard_upload_url_contents": {"label": "Upload the contents of a copied URL"},
                           "clipboard_upload_shorten_url": {"label": "Shorten a copied URL"},
                           "clipboard_upload_share_url": {"label": "Share a copied URL"},
                           "clipboard_upload_auto_index_folder": {"label": "Index copied folders and upload the "
                                                                           "index"}})
        a = self.form(self.cfg, {"show_clipboard_content_viewer": {
            "label": "Show the content viewer for \"Upload from clipboard with content viewer\""},
            "clipboard_upload_ignore_empty_text": {"label": "Ignore empty text"}})
        return [group("Clipboard upload", "Default task settings (workflows can override them).",
                      f.rows(only=["clipboard_upload_url_contents", "clipboard_upload_shorten_url",
                                   "clipboard_upload_share_url", "clipboard_upload_auto_index_folder"])
                      + a.rows(only=["show_clipboard_content_viewer", "clipboard_upload_ignore_empty_text"]))]

    # -- Advanced --

    def _advanced(self) -> list[Gtk.Widget]:
        cfg = self.cfg
        f = self.form(cfg, {"capture_backend": {"label": "Capture backend",
                                                "help": "Auto tries the extension, Mutter ScreenCast, the portal"},
                            "disable_hotkeys": {"label": "Disable hotkeys"},
                            "disable_hotkeys_on_fullscreen": {"label": "Ignore hotkeys while a fullscreen window "
                                                                       "has focus"},
                            "hotkey_repeat_limit": {"label": "Ignore repeats within (ms)", "min": 0, "max": 5000},
                            "use_libsecret": {"label": "Keep uploader secrets in the GNOME keyring"},
                            "dev_mode": {"label": "Developer mode (debug logging)"},
                            "file_upload_default_directory": {"kind": "folder",
                                                              "label": "Default folder for file uploads"},
                            "actions_toolbar_run_at_startup": {"label": "Show the actions toolbar at startup"}})
        self.hotkey_backend_row = self._hotkey_backend_row()
        remove = button_row("GNOME custom shortcuts", "Remove ShareL's entries", self.remove_gsettings_entries,
                            subtitle="Deletes the shortcuts the GSettings backend added")
        backends = group("Backends", "", [*f.rows(only=["capture_backend"]), self.hotkey_backend_row, remove])
        hot = group("Hotkeys", "", f.rows(only=["disable_hotkeys", "disable_hotkeys_on_fullscreen",
                                                "hotkey_repeat_limit"]))
        other = group("Other", "", f.rows(only=["use_libsecret", "dev_mode", "file_upload_default_directory",
                                                "name_parser_auto_increment_number", "actions_toolbar_run_at_startup",
                                                "actions_toolbar_lock_position", "actions_toolbar_stay_top_most"]))
        return [backends, hot, other]

    def _hotkey_backend_row(self) -> Adw.ComboRow:
        members = list(HotkeyBackendName)
        row = Adw.ComboRow(title="Hotkey backend", model=Gtk.StringList.new([HOTKEY_BACKEND_TEXT.get(m.value, m.value)
                                                                             for m in members]),
                           selected=members.index(self.cfg.hotkey_backend))
        row.members = members  # type: ignore[attr-defined]
        self._reverting = False

        def on_sel(r, _p):
            if self._reverting:
                return
            new = members[r.get_selected()]
            if new == self.cfg.hotkey_backend:
                return
            if new == HotkeyBackendName.GSettings:
                def resp(answer):
                    if answer == "use":
                        self.set_hotkey_backend(new)
                    else:
                        self._reverting = True
                        r.set_selected(members.index(self.cfg.hotkey_backend))
                        self._reverting = False
                gtkutil.ask(self, "Use GNOME custom shortcuts?",
                            "ShareL will add its hotkeys to GNOME's own custom keyboard shortcuts (Settings ▸ "
                            "Keyboard ▸ Custom Shortcuts). They run \"sharel -<task>\" and stay there until you "
                            "switch to another backend or remove them here.",
                            [("cancel", "Cancel"), ("use", "Use GNOME shortcuts")], resp, destructive="use")
            else:
                self.set_hotkey_backend(new)
        row.connect("notify::selected", on_sel)
        return row

    def set_hotkey_backend(self, backend: HotkeyBackendName) -> None:
        old = self.cfg.hotkey_backend
        self.cfg.hotkey_backend = backend
        if old == HotkeyBackendName.GSettings and backend != HotkeyBackendName.GSettings:
            from ...shell.hotkeys import HotkeyManager
            HotkeyManager.remove_gsettings_entries()
        self.changed()
        self.app.register_hotkeys()

    def remove_gsettings_entries(self) -> int:
        from ...shell.hotkeys import HotkeyManager
        n = HotkeyManager.remove_gsettings_entries()
        if self.cfg.hotkey_backend == HotkeyBackendName.GSettings:
            self.toast(f"Removed {n} shortcut(s); they come back while the GSettings backend is selected")
        else:
            self.toast(f"Removed {n} ShareL shortcut(s) from GNOME")
        return n


class SecondaryList(Adw.ExpanderRow):
    """An ordered pick list of destinations ("category/name" keys, the format dispatch reads)."""

    def __init__(self, win: AppSettingsWindow, attr: str, title: str, choices: list[tuple[str, str]]) -> None:
        super().__init__(title=title)
        self.win = win
        self.attr = attr
        self.choices = choices
        self.titles = dict(choices)
        self._rows: list[Gtk.Widget] = []
        self.add_combo = Adw.ComboRow(title="Add uploader", model=Gtk.StringList.new([t for _k, t in choices]))
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.add_selected())
        self.add_combo.add_suffix(add)
        self.add_row(self.add_combo)
        self.rebuild()

    @property
    def items(self) -> list[str]:
        return getattr(self.win.cfg, self.attr)

    def rebuild(self) -> None:
        for r in self._rows:
            self.remove(r)
        self._rows = []
        items = self.items
        for i, key in enumerate(items):
            row = Adw.ActionRow(title=f"{i + 1}. {self.titles.get(key, key)}", subtitle=key)
            for icon, tip, cb in (("go-up-symbolic", "Move up", lambda i=i: self.move(i, -1)),
                                  ("go-down-symbolic", "Move down", lambda i=i: self.move(i, 1)),
                                  ("list-remove-symbolic", "Remove", lambda i=i: self.remove_at(i))):
                b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tip)
                b.add_css_class("flat")
                b.connect("clicked", lambda *_a, cb=cb: cb())
                row.add_suffix(b)
            self.add_row(row)
            self._rows.append(row)
        self.set_subtitle(", ".join(self.titles.get(k, k) for k in items) or "None")

    def add_selected(self) -> None:
        key = self.choices[self.add_combo.get_selected()][0]
        if key not in self.items:
            self.items.append(key)
            self.win.changed()
            self.rebuild()

    def move(self, i: int, delta: int) -> None:
        items = self.items
        j = i + delta
        if 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
            self.win.changed()
            self.rebuild()

    def remove_at(self, i: int) -> None:
        self.items.pop(i)
        self.win.changed()
        self.rebuild()
