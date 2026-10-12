"""Application settings, task settings, debug log and about windows in the sandbox."""
from __future__ import annotations

import datetime as dt
import http.server
import json
import logging
import os
import sys
import threading
import types
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from uihelpers import find, run_app

from sharel.core.enums import (
    AfterCaptureTasks,
    CaptureBackendName,
    EImageFormat,
    HotkeyBackendName,
    ProxyMethod,
    ThemeMode,
)
from sharel.ui.windows._sectioned import find_row


def saved(app, name: str = "ApplicationConfig.json") -> dict:
    return json.loads((app.store.directory / name).read_text())


def auto_answer(monkeypatch, answer: str, calls: list | None = None):
    """Replace the confirmation dialog: answer immediately."""
    from sharel.ui import gtkutil

    def ask(_parent, heading, _body, _responses, on_done, **_kw):
        if calls is not None:
            calls.append(heading)
        on_done(answer)
    monkeypatch.setattr(gtkutil, "ask", ask)


def _suffix_button(row: Gtk.Widget, tooltip: str) -> Gtk.Button:
    b = find(row, lambda w: isinstance(w, Gtk.Button) and w.get_tooltip_text() == tooltip)
    assert b is not None, tooltip
    return b


# ---- application settings -----------------------------------------------------------------------


def test_app_settings_theme_changes_style_manager():
    def body(app):
        from sharel.ui.windows.appsettings import open_app_settings
        win = open_app_settings(app)
        yield 0.2
        assert open_app_settings(app) is win  # opened twice -> same window
        assert set(win.pages) >= {"general", "theme", "paths", "settings", "history", "print", "proxy", "upload",
                                  "clipboard", "advanced"}
        row = find_row(win.pages["theme"], "Appearance", Adw.ComboRow)
        row.set_selected(list(ThemeMode).index(ThemeMode.Dark))
        assert app.store.app.theme_mode == ThemeMode.Dark
        assert Adw.StyleManager.get_default().get_color_scheme() == Adw.ColorScheme.FORCE_DARK
        assert saved(app)["theme_mode"] == "Dark"
        row.set_selected(list(ThemeMode).index(ThemeMode.Light))
        assert Adw.StyleManager.get_default().get_color_scheme() == Adw.ColorScheme.FORCE_LIGHT
        row.set_selected(list(ThemeMode).index(ThemeMode.System))
        assert Adw.StyleManager.get_default().get_color_scheme() == Adw.ColorScheme.DEFAULT
        return True
    assert run_app(body)


def test_app_settings_autostart_tray_language():
    def body(app):
        from sharel.core import autostart
        from sharel.ui.windows.appsettings import open_app_settings
        win = open_app_settings(app)
        yield 0.1
        entry = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart" / "app.sharel.ShareL.desktop"
        assert autostart.entry_path() == entry
        row = find_row(win.pages["general"], "Run ShareL when I log in", Adw.SwitchRow)
        row.set_active(True)
        assert entry.exists() and "-silent" in entry.read_text()
        assert saved(app)["start_with_system"] is True
        row.set_active(False)
        assert not entry.exists()

        tray = find_row(win.pages["general"], "Show tray icon", Adw.SwitchRow)
        tray.set_active(False)
        assert app.tray is None and saved(app)["show_tray"] is False
        tray.set_active(True)
        assert app.tray is not None

        lang = find_row(win.pages["general"], "Language", Adw.ComboRow)
        lang.set_selected(1)
        assert app.store.app.language == "English" and saved(app)["language"] == "English"
        click = find_row(win.pages["general"], "Tray middle click action", Adw.ComboRow)
        click.set_selected(1)
        assert saved(app)["tray_middle_click_action"] == "OpenMainWindow"
        return True
    assert run_app(body)


def test_app_settings_paths_preview(tmp_path):
    def body(app):
        from sharel.ui.windows.appsettings import open_app_settings
        win = open_app_settings(app)
        yield 0.1
        page = win.pages["paths"]
        find_row(page, "Use a custom screenshots folder", Adw.SwitchRow).set_active(True)
        find_row(page, "Screenshots folder", Adw.EntryRow).set_text(str(tmp_path / "shots"))
        find_row(page, "Sub folder pattern", Adw.EntryRow).set_text("%y")
        expected = str(tmp_path / "shots" / str(dt.date.today().year))
        assert win.folder_preview.get_subtitle() == expected
        assert str(app.store.screenshots_folder(None)) == expected
        data = saved(app)
        assert data["custom_screenshots_path"] == str(tmp_path / "shots")
        assert data["save_image_sub_folder_pattern"] == "%y"
        return True
    assert run_app(body)


def test_settings_export_import_round_trip(tmp_path, monkeypatch):
    def body(app):
        from sharel.core.history import HistoryItem
        from sharel.ui.windows import appsettings
        win = appsettings.open_app_settings(app)
        yield 0.1
        limit = find_row(win.pages["upload"], "Simultaneous uploads", Adw.SpinRow)
        limit.set_value(7)
        assert app.store.app.upload_limit == 7 and app.tasks._sem._initial_value == 7
        app.store.hotkeys.hotkeys[0].task_settings.description = "Backed up"
        app.store.save_hotkeys()
        app.history.add(HistoryItem(file_name="kept.png", url="https://example.com/kept.png"))
        zpath = tmp_path / "backup.zip"
        names = appsettings.export_settings(app, zpath, include_settings=True, include_history=True)
        assert names == ["ApplicationConfig.json", "HotkeysConfig.json", "UploadersConfig.json", "History.json"]

        # change everything, then restore through the window's import path (confirmation answered "import")
        limit.set_value(2)
        app.store.hotkeys.hotkeys[0].task_settings.description = "Changed"
        app.history.clear()
        assert app.store.app.upload_limit == 2
        calls = []
        auto_answer(monkeypatch, "import", calls)
        win.confirm_import(str(zpath))
        assert calls == ["Import settings?"]
        assert app.store.app.upload_limit == 7
        assert saved(app)["upload_limit"] == 7
        assert app.store.hotkeys.hotkeys[0].task_settings.description == "Backed up"
        assert saved(app, "HotkeysConfig.json")["hotkeys"][0]["task_settings"]["description"] == "Backed up"
        assert [i.file_name for i in app.history.items()] == ["kept.png"]
        assert app.tasks._sem._initial_value == 7
        # the rebuilt window shows the restored value
        assert find_row(win.pages["upload"], "Simultaneous uploads", Adw.SpinRow).get_value() == 7

        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        try:
            appsettings.read_backup(bad)
            raise AssertionError("bad backup accepted")
        except ValueError as e:
            assert "Not a ShareL settings backup" in str(e)
        return True
    assert run_app(body)


class _Proxy(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_HEAD(self):
        _Proxy.seen.append(f"{self.command} {self.path}")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_a):
        pass


def test_proxy_test_through_local_proxy():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Proxy.seen.clear()
    try:
        def body(app):
            from sharel.ui.windows.appsettings import open_app_settings
            win = open_app_settings(app)
            yield 0.1
            page = win.pages["proxy"]
            find_row(page, "Proxy", Adw.ComboRow).set_selected(list(ProxyMethod).index(ProxyMethod.Manual))
            find_row(page, "Host", Adw.EntryRow).set_text("127.0.0.1")
            find_row(page, "Port", Adw.SpinRow).set_value(srv.server_address[1])
            assert app.store.app.proxy_settings.method == ProxyMethod.Manual
            assert saved(app)["proxy_settings"]["port"] == srv.server_address[1]
            win.proxy_url.set_text("http://sharel-proxy-test.invalid/probe")
            result = {}
            win.run_proxy_test(lambda ok, msg: result.update(ok=ok, msg=msg))
            for _ in range(100):
                if result:
                    break
                yield 0.05
            assert result["ok"], result
            assert "HTTP 204" in result["msg"]
            assert _Proxy.seen == ["HEAD http://sharel-proxy-test.invalid/probe"]
            assert "HTTP 204" in win.proxy_result.get_subtitle()
            return True
        assert run_app(body)
    finally:
        srv.shutdown()


def test_history_page_clear_and_options(monkeypatch):
    def body(app):
        from sharel.core.history import HistoryItem
        from sharel.ui.windows.appsettings import open_app_settings
        app.history.add(HistoryItem(file_name="a.png"))
        win = open_app_settings(app)
        yield 0.1
        page = win.pages["history"]
        find_row(page, "Save tasks in history", Adw.SwitchRow).set_active(False)
        find_row(page, "Max items in the history window", Adw.SpinRow).set_value(50)
        data = saved(app)
        assert data["history_save_tasks"] is False and data["history_settings"]["max_item_count"] == 50
        auto_answer(monkeypatch, "cancel")
        win.clear_history_dialog()
        assert len(app.history.items()) == 1
        auto_answer(monkeypatch, "clear")
        win.clear_history_dialog()
        assert app.history.items() == []
        # print page
        find_row(win.pages["print"], "Don't show the print settings window", Adw.SwitchRow).set_active(True)
        assert saved(app)["dont_show_print_settings_dialog"] is True
        return True
    assert run_app(body)


def test_advanced_backends_and_gsettings_opt_in(monkeypatch):
    def body(app):
        from sharel.shell.hotkeys import HotkeyManager
        from sharel.ui.windows.appsettings import open_app_settings
        removed = []
        monkeypatch.setattr(HotkeyManager, "remove_gsettings_entries", staticmethod(lambda: removed.append(1) or 2))
        registered = []
        orig = app.register_hotkeys
        monkeypatch.setattr(app, "register_hotkeys", lambda: (registered.append(1), orig())[1])
        win = open_app_settings(app)
        yield 0.1
        row = win.hotkey_backend_row
        members = list(HotkeyBackendName)
        auto_answer(monkeypatch, "cancel")
        row.set_selected(members.index(HotkeyBackendName.GSettings))
        assert app.store.app.hotkey_backend == HotkeyBackendName.Auto
        assert members[row.get_selected()] == HotkeyBackendName.Auto  # reverted
        calls = []
        auto_answer(monkeypatch, "use", calls)
        row.set_selected(members.index(HotkeyBackendName.GSettings))
        assert calls == ["Use GNOME custom shortcuts?"]
        assert app.store.app.hotkey_backend == HotkeyBackendName.GSettings
        assert saved(app)["hotkey_backend"] == "GSettings"
        assert app.hotkeys.backend_pref == "GSettings" and registered
        assert not removed
        row.set_selected(members.index(HotkeyBackendName.Extension))
        assert removed == [1]  # leaving GSettings removes ShareL's GNOME entries
        assert app.hotkeys.backend_pref == "Extension"
        assert win.remove_gsettings_entries() == 2
        # libsecret / dev mode / emoji
        page = win.pages["advanced"]
        find_row(page, "Developer mode (debug logging)", Adw.SwitchRow).set_active(True)
        assert logging.getLogger().level == logging.DEBUG
        find_row(page, "Developer mode (debug logging)", Adw.SwitchRow).set_active(False)
        find_row(page, "Keep uploader secrets in the GNOME keyring", Adw.SwitchRow).set_active(False)
        assert saved(app)["use_libsecret"] is False
        retry = find_row(win.pages["upload"], "Retries when an upload fails", Adw.SpinRow)
        retry.set_value(3)
        find_row(win.pages["upload"], "Request timeout (seconds)", Adw.SpinRow).set_value(30)
        assert saved(app)["max_upload_fail_retry"] == 3 and saved(app)["request_timeout_seconds"] == 30
        from sharel.uploaders.services import _file_helpers
        emoji = find_row(win.pages["upload"], "Don't URL-encode emoji", Adw.SwitchRow)
        emoji.set_active(False)
        assert _file_helpers.url_encode("a😀") == "a%F0%9F%98%80"
        emoji.set_active(True)
        assert _file_helpers.url_encode("a b😀") == "a%20b😀"
        return True
    assert run_app(body)


def test_capture_backend_setting_overrides_auto_detect():
    def body(app):
        from sharel.ui.windows.appsettings import open_app_settings
        win = open_app_settings(app)
        yield 0.1
        ts = app.store.task_settings_for(None)
        assert app.backend_pref(ts) == "Auto"
        find_row(win.pages["advanced"], "Capture backend", Adw.ComboRow).set_selected(
            list(CaptureBackendName).index(CaptureBackendName.Portal))
        assert saved(app)["capture_backend"] == "Portal"
        assert app.backend_pref(app.store.task_settings_for(None)) == "Portal"
        ts = app.store.task_settings_for(None)
        ts.capture_settings.capture_backend = CaptureBackendName.MutterScreenCast
        assert app.backend_pref(ts) == "MutterScreenCast"  # task settings win over the app setting
        return True
    assert run_app(body)


def test_secondary_uploaders_and_clipboard_page():
    def body(app):
        from sharel.ui.windows.appsettings import open_app_settings
        win = open_app_settings(app)
        yield 0.1
        find_row(win.pages["upload"], "Use secondary uploaders when one fails", Adw.SwitchRow).set_active(True)
        ed = win.secondary_editors["secondary_image_uploaders"]
        keys = [k for k, _t in ed.choices]
        ed.add_combo.set_selected(keys.index("image/Imgur"))
        ed.add_selected()
        ed.add_combo.set_selected(keys.index("image/Chevereto"))
        ed.add_selected()
        ed.add_selected()  # no duplicates
        assert app.store.app.secondary_image_uploaders == ["image/Imgur", "image/Chevereto"]
        ed.move(1, -1)
        assert saved(app)["secondary_image_uploaders"] == ["image/Chevereto", "image/Imgur"]
        ed.remove_at(0)
        assert saved(app)["secondary_image_uploaders"] == ["image/Imgur"]
        assert saved(app)["use_secondary_uploaders"] is True
        page = win.pages["clipboard"]
        find_row(page, "Shorten a copied URL", Adw.SwitchRow).set_active(True)
        find_row(page, "Ignore empty text", Adw.SwitchRow).set_active(False)
        data = saved(app)
        assert data["default_task_settings"]["upload_settings"]["clipboard_upload_shorten_url"] is True
        assert data["clipboard_upload_ignore_empty_text"] is False
        return True
    assert run_app(body)


# ---- task settings --------------------------------------------------------------------------------


def test_default_task_settings_edit_and_save():
    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        win = open_task_settings(app)
        yield 0.2
        assert "task" not in win.pages and not win.switches  # defaults: no use-default switches
        fmt = find_row(win.pages["image"], "Image format", Adw.ComboRow)
        fmt.set_selected(list(EImageFormat).index(EImageFormat.JPEG))
        assert app.store.app.default_task_settings.image_settings.image_format == EImageFormat.JPEG
        assert saved(app)["default_task_settings"]["image_settings"]["image_format"] == "JPEG"
        find_row(win.pages["general"], "Play sound after capture", Adw.SwitchRow).set_active(False)
        find_row(win.pages["general"], "Toast window duration", Adw.SpinRow).set_value(5.5)
        general = saved(app)["default_task_settings"]["general_settings"]
        assert general["play_sound_after_capture"] is False and general["toast_window_duration"] == 5.5
        find_row(win.pages["image"], "Thumbnail width", Adw.SpinRow).set_value(320)
        assert saved(app)["default_task_settings"]["image_settings"]["thumbnail_width"] == 320
        find_row(win.pages["capture"], "Show cursor", Adw.SwitchRow).set_active(False)
        assert saved(app)["default_task_settings"]["capture_settings"]["show_cursor"] is False
        # nested sections: region capture / FFmpeg / OCR
        find_row(win.pages["capture"], "Language", Adw.EntryRow).set_text("deu")
        assert saved(app)["default_task_settings"]["capture_settings"]["ocr"]["language"] == "deu"
        find_row(win.pages["capture"], "X264 CRF", Adw.SpinRow).set_value(20)
        assert saved(app)["default_task_settings"]["capture_settings"]["screen_recording"]["ffmpeg"]["x264_crf"] == 20
        find_row(win.pages["tools"], "Indexer max depth", Adw.SpinRow).set_value(3)
        assert app.store.app.default_task_settings.tool_settings.indexer_max_depth == 3
        find_row(win.pages["advanced"], "Result force HTTPS", Adw.SwitchRow).set_active(True)
        assert saved(app)["default_task_settings"]["advanced_settings"]["result_force_https"] is True
        return True
    assert run_app(body)


def test_workflow_use_default_switch_gates_and_effective_settings():
    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        hk = app.store.hotkeys.hotkeys[0]
        closed = []
        win = open_task_settings(app, hk, on_close=lambda: closed.append(1))
        yield 0.2
        assert win.get_title() == f"Task settings: {hk.name}"
        sw = win.switches["use_default_image_settings"]
        assert sw.get_active()
        gated = win.gates["use_default_image_settings"]
        assert gated and all(not g.get_sensitive() for g in gated)
        sw.set_active(False)
        assert all(g.get_sensitive() for g in gated)
        assert hk.task_settings.use_default_image_settings is False
        find_row(win.pages["image"], "Image JPEG quality", Adw.SpinRow).set_value(55)
        assert app.store.task_settings_for(hk).image_settings.image_jpeg_quality == 55
        assert app.store.task_settings_for(None).image_settings.image_jpeg_quality == 90
        data = saved(app, "HotkeysConfig.json")["hotkeys"][0]["task_settings"]
        assert data["use_default_image_settings"] is False and data["image_settings"]["image_jpeg_quality"] == 55
        sw.set_active(True)
        assert app.store.task_settings_for(hk).image_settings.image_jpeg_quality == 90
        assert all(not g.get_sensitive() for g in gated)

        # Task page: after capture override + flag list, destinations, description
        ov = win.switches["use_default_after_capture_job"]
        assert not ov.get_active() and all(not g.get_sensitive() for g in win.gates["use_default_after_capture_job"])
        ov.set_active(True)
        assert hk.task_settings.use_default_after_capture_job is False
        find_row(win.pages["task"], "Copy Image To Clipboard", Adw.SwitchRow).set_active(False)
        eff = app.store.task_settings_for(hk).after_capture_job
        assert not eff & AfterCaptureTasks.CopyImageToClipboard and eff & AfterCaptureTasks.SaveImageToFile
        assert app.store.task_settings_for(None).after_capture_job & AfterCaptureTasks.CopyImageToClipboard
        folder_sw = win.switches["override_screenshots_folder"]
        folder_sw.set_active(True)
        find_row(win.pages["task"], "Screenshots folder", Adw.EntryRow).set_text("/tmp/sharel-wf")
        assert str(app.store.screenshots_folder(app.store.task_settings_for(hk))) == "/tmp/sharel-wf"
        find_row(win.pages["task"], "Description", Adw.EntryRow).set_text("My region")
        assert win.get_title() == "Task settings: My region"
        assert saved(app, "HotkeysConfig.json")["hotkeys"][0]["task_settings"]["description"] == "My region"
        assert app.store.app.default_task_settings.description == ""
        win.close()
        yield 0.1
        assert closed == [1]
        return True
    assert run_app(body)


def test_name_pattern_preview_and_token_insert():
    def body(app):
        from sharel.ui.windows.tasksettings import TOKEN_HELP, open_task_settings
        win = open_task_settings(app)
        yield 0.1
        entry, label = win.pattern_rows["name_format_pattern"]
        entry.set_text("shot-%y")
        assert label.get_label() == f"shot-{dt.date.today().year}"
        assert app.store.app.default_task_settings.upload_settings.name_format_pattern == "shot-%y"
        entry.set_position(-1)
        win.insert_token(entry, "%mo")
        assert entry.get_text() == "shot-%y%mo"
        assert label.get_label() == f"shot-{dt.date.today().year}{dt.date.today().month:02d}"
        assert saved(app)["default_task_settings"]["upload_settings"]["name_format_pattern"] == "shot-%y%mo"
        assert ("%ra", "Random alphanumeric character (%ra{10})") in TOKEN_HELP
        return True
    assert run_app(body)


def test_actions_and_uploader_filter_editors_add_rows():
    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        win = open_task_settings(app)
        yield 0.1
        ts = app.store.app.default_task_settings
        _suffix_button(win.actions_row, "Add").emit("clicked")
        assert len(ts.external_programs) == 1
        find_row(win.actions_row, "Name", Adw.EntryRow).set_text("Optimize")
        find_row(win.actions_row, "Program", Adw.EntryRow).set_text("/usr/bin/optipng")
        find_row(win.actions_row, "Arguments", Adw.EntryRow).set_text('"%input"')
        find_row(win.actions_row, "Enabled", Adw.SwitchRow).set_active(True)
        prog = saved(app)["default_task_settings"]["external_programs"][0]
        assert prog["name"] == "Optimize" and prog["path"] == "/usr/bin/optipng" and prog["is_active"] is True
        _suffix_button(win.actions_row, "Add").emit("clicked")
        assert len(saved(app)["default_task_settings"]["external_programs"]) == 2

        win.add_filter_button.emit("clicked")
        assert len(ts.upload_settings.uploader_filters) == 1
        exp = win.filter_rows[0]
        find_row(exp, "Extensions", Adw.EntryRow).set_text("mp4 webm")
        dest = find_row(exp, "Destination", Adw.ComboRow)
        from sharel.ui.windows.tasksettings import destination_choices
        keys = [k for c in ("image", "text", "file") for k, _t in destination_choices(c)]
        dest.set_selected(keys.index("file/Streamable"))
        flt = saved(app)["default_task_settings"]["upload_settings"]["uploader_filters"][0]
        assert flt == {"enabled": True, "extensions": "mp4 webm", "category": "file", "destination": "Streamable"}
        from sharel.core.enums import EDataType
        from sharel.uploaders.dispatch import destination_for
        d = destination_for(EDataType.File, app.store.task_settings_for(None), "clip.mp4")
        assert (d.category, d.name) == ("file", "Streamable")
        return True
    assert run_app(body)


def _wait_for(cond, seconds: float):
    for _ in range(int(seconds / 0.1)):
        if cond():
            return
        yield 0.1


def test_watch_folder_editor_uploads_dropped_file(tmp_path):
    watched = tmp_path / "watched"

    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        uploads = []
        app.tasks.upload_file = lambda path, ts: uploads.append((path, ts))
        win = open_task_settings(app)
        yield 0.1
        win.watch_enable_row.set_active(True)
        win.add_watch_button.emit("clicked")
        row = win.watch_rows[0]
        row.folder_entry.set_text(str(watched))
        row.folder_entry.emit("apply")
        row.filter_entry.set_text("*.png")
        row.filter_entry.emit("apply")
        wf = app.store.app.default_task_settings.watch_folder_list[0]
        assert wf.folder_path == str(watched) and wf.filter == "*.png"
        assert saved(app)["default_task_settings"]["watch_folder_enabled"] is True
        assert any(m[1] is wf for m in app.watchers.monitors)
        (watched / "ignored.txt").write_text("x")
        (watched / "dropped.png").write_bytes(b"\x89PNG fake")
        yield from _wait_for(lambda: uploads, 6)
        assert [Path(p).name for p, _ts in uploads] == ["dropped.png"]
        # removing the folder stops watching
        _suffix_button(row, "Remove").emit("clicked")
        assert app.store.app.default_task_settings.watch_folder_list == []
        assert app.watchers.monitors == []
        return True
    assert run_app(body, timeout=40)


def test_workflow_watch_folder_uploads_with_its_settings(tmp_path):
    watched = tmp_path / "wf"

    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        uploads = []
        app.tasks.upload_file = lambda path, ts: uploads.append((path, ts))
        hk = app.store.hotkeys.hotkeys[1]
        assert hk.task_settings.is_using_defaults  # watch folders still count for such a workflow
        win = open_task_settings(app, hk)
        yield 0.1
        win.watch_enable_row.set_active(True)
        win.add_watch_button.emit("clicked")
        win.watch_rows[0].folder_entry.set_text(str(watched))
        win.watch_rows[0].folder_entry.emit("apply")
        assert saved(app, "HotkeysConfig.json")["hotkeys"][1]["task_settings"]["watch_folder_list"][0][
            "folder_path"] == str(watched)
        (watched / "new.jpg").write_bytes(b"jpeg")
        yield from _wait_for(lambda: uploads, 6)
        assert len(uploads) == 1 and uploads[0][0].endswith("new.jpg")
        return True
    assert run_app(body, timeout=40)


def test_image_effects_button_uses_effects_window(monkeypatch):
    calls = []
    fake = types.ModuleType("sharel.ui.windows.effects")

    def open_effects_window(app, img, ts, done):
        calls.append((img.size, ts))
        ts.image_settings.image_effect_presets.append({"name": "Fancy", "effects": [{"type": "x"}]})
        done(None)
    fake.open_effects_window = open_effects_window
    monkeypatch.setitem(sys.modules, "sharel.ui.windows.effects", fake)

    def body(app):
        from sharel.ui.windows.tasksettings import open_task_settings
        win = open_task_settings(app)
        yield 0.1
        assert "No presets" in win.effects_summary.get_subtitle()
        win.open_effects()
        assert calls and calls[0][1] is app.store.app.default_task_settings
        assert "Fancy (1 effect)" in win.effects_summary.get_subtitle()
        assert saved(app)["default_task_settings"]["image_settings"]["image_effect_presets"][0]["name"] == "Fancy"
        return True
    assert run_app(body)


# ---- debug log / about ----------------------------------------------------------------------------


def test_debug_log_shows_logged_line_and_system_info(monkeypatch):
    def body(app):
        from sharel.core import clipboard
        from sharel.ui.windows.debuglog import open_debug_log
        copied = []
        monkeypatch.setattr(clipboard, "set_text", lambda t: copied.append(t))
        win = open_debug_log(app)
        yield 0.1
        logging.getLogger("sharel.test").warning("hello-debug-xyz")
        yield 0.1

        def text():
            b = win.buffer
            return b.get_text(b.get_start_iter(), b.get_end_iter(), False)
        assert "hello-debug-xyz" in text()
        win.set_filter("HELLO-DEBUG")
        assert text().strip() and all("hello-debug" in ln for ln in text().splitlines())
        win.copy_all()
        assert "hello-debug-xyz" in copied[-1]
        win.set_filter("")
        win.clear_view()
        assert text() == ""
        logging.getLogger("sharel.test").warning("after-clear")
        yield 0.1
        assert "after-clear" in text()
        keys = [k for k, _v in win.info_rows]
        assert {"ShareL", "Python", "GTK", "libadwaita", "Hotkey backend", "GNOME Shell extension"} <= set(keys)
        yield from _wait_for(lambda: any(k.startswith("Capture:") for k, _v in win.info_rows), 10)
        assert any(k.startswith("Capture:") for k, _v in win.info_rows)
        win.copy_info()
        assert "ShareL: " in copied[-1]
        win.close()
        yield 0.05
        from sharel.ui.debuglog import MEMORY_HANDLER
        assert win._on_record not in MEMORY_HANDLER.listeners
        return True
    assert run_app(body, timeout=40)


def test_about_dialog():
    def body(app):
        from sharel import __version__
        from sharel.ui.windows.about import open_about
        dlg = open_about(app)
        yield 0.2
        assert isinstance(dlg, Adw.AboutDialog)
        assert dlg.get_application_name() == "ShareL" and dlg.get_version() == __version__
        assert dlg.get_license_type() == Gtk.License.GPL_3_0
        assert "ShareL" in dlg.get_debug_info() and "GTK" in dlg.get_debug_info()
        dlg.close()
        return True
    assert run_app(body)


def test_fresh_config_never_writes_an_autostart_entry(tmp_path, monkeypatch):
    """start_with_system defaults to off: a first run, opening and editing settings, and a settings import
    leave no autostart entry (and no autostart directory)."""
    xdg = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    cfg_dir = Path(os.environ["SHAREL_CONFIG_DIR"])
    assert not (cfg_dir / "ApplicationConfig.json").exists()  # a fresh configuration

    def body(app):
        from sharel.core import autostart
        from sharel.ui.windows.appsettings import apply_side_effects, open_app_settings
        assert app.store.app.start_with_system is False
        assert autostart.entry_path() == xdg / "autostart" / "app.sharel.ShareL.desktop"
        win = open_app_settings(app)
        yield 0.2
        tray = find_row(win.pages["general"], "Show tray icon", Adw.SwitchRow)
        tray.set_active(not tray.get_active())  # an unrelated change is saved
        yield 0.1
        assert saved(app)["start_with_system"] is False
        apply_side_effects(app)  # what an import or reset runs
        win.close()
        yield 0.1
        assert not autostart.is_enabled()
        return True
    assert run_app(body)
    assert not (xdg / "autostart").exists(), list(xdg.rglob("*"))
    assert json.loads((cfg_dir / "ApplicationConfig.json").read_text())["start_with_system"] is False
