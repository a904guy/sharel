"""Hotkey settings window: job picker, key capture, conflicts, list editing; DisableHotkeys."""
from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk
from uihelpers import run_app

from sharel.core.config import HotkeyInfo, HotkeySettings, TaskSettings
from sharel.core.enums import HotkeyType


def saved_hotkeys(app) -> list[dict]:
    return json.loads((app.store.directory / "HotkeysConfig.json").read_text())["hotkeys"]


def press(button, keyval, mods=0):
    """Feed a key press to the capture button's EventControllerKey handler."""
    return button._on_key_pressed(button.key_controller, keyval, 0, Gdk.ModifierType(mods))


def test_add_hotkey_via_job_picker_and_capture_accelerator():
    def body(app):
        from sharel.ui.windows.hotkeysettings import open_hotkey_settings
        win = open_hotkey_settings(app)
        yield 0.2
        assert open_hotkey_settings(app) is win
        n = len(app.store.hotkeys.hotkeys)
        assert len(win.rows) == n
        dlg = win.open_job_picker()
        yield 0.1
        groups = {r.get_subtitle() for r, _k in dlg.rows}
        assert {"RectangleRegion", "ScreenColorPicker", "DisableHotkeys", "OCR"} <= groups
        dlg.search.set_text("color picker")
        dlg.search.emit("search-changed")
        visible = [r.get_subtitle() for r, _k in dlg.rows if r.get_visible()]
        assert "ScreenColorPicker" in visible and "RectangleRegion" not in visible
        row = next(r for r, _k in dlg.rows if r.get_subtitle() == "ScreenColorPicker")
        row.emit("activated")
        yield 0.1
        assert len(app.store.hotkeys.hotkeys) == n + 1
        hk = app.store.hotkeys.hotkeys[-1]
        assert hk.job == HotkeyType.ScreenColorPicker and hk.hotkey_info.hotkey == ""
        assert saved_hotkeys(app)[-1]["task_settings"]["job"] == "ScreenColorPicker"
        new_row = win.rows[-1]
        assert win.selected_row() is new_row and new_row.state == "unset"

        btn = new_row.hotkey_button
        btn.emit("clicked")
        assert btn.capturing and btn.get_label() == "Press a key…"
        assert press(btn, Gdk.KEY_Control_L, Gdk.ModifierType.CONTROL_MASK)  # modifier alone: keep waiting
        assert btn.capturing
        press(btn, Gdk.KEY_F9, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        assert not btn.capturing
        assert hk.hotkey_info.hotkey == "<Shift><Control>F9"
        assert saved_hotkeys(app)[-1]["hotkey_info"]["hotkey"] == "<Shift><Control>F9"
        assert btn.get_label() == "Shift+Ctrl+F9"
        # Escape cancels (no change), Backspace clears
        btn.emit("clicked")
        press(btn, Gdk.KEY_Escape)
        assert hk.hotkey_info.hotkey == "<Shift><Control>F9" and not btn.capturing
        btn.emit("clicked")
        press(btn, Gdk.KEY_BackSpace)
        assert hk.hotkey_info.hotkey == "" and saved_hotkeys(app)[-1]["hotkey_info"]["hotkey"] == ""
        assert btn.get_label() == "None"
        # letters are stored lower-case, as GTK accelerators
        btn.emit("clicked")
        press(btn, Gdk.KEY_K, Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK)
        assert hk.hotkey_info.hotkey == "<Shift><Super>k"
        return True
    assert run_app(body)


def test_duplicate_accelerator_shows_conflict():
    def body(app):
        from sharel.ui.windows.hotkeysettings import open_hotkey_settings
        win = open_hotkey_settings(app)
        yield 0.1
        a = win.add_hotkey(HotkeyType.OCR)
        b = win.add_hotkey(HotkeyType.QRCode)
        ra, rb = win.rows[-2], win.rows[-1]
        assert ra.hk is a and rb.hk is b
        for row in (ra, rb):
            row.hotkey_button.emit("clicked")
            press(row.hotkey_button, Gdk.KEY_F8, Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        assert a.hotkey_info.hotkey == b.hotkey_info.hotkey == "<Alt><Super>F8"
        assert ra.state == rb.state == "conflict"
        assert 'also used by "QR code"' in ra.detail and 'also used by "OCR"' in rb.detail
        assert "also used by" in rb.subtitle.get_label()
        assert rb.status_icon.get_icon_name() == "dialog-warning-symbolic"
        # disabling one of them resolves the conflict
        rb.enabled.set_active(False)
        assert saved_hotkeys(app)[-1]["hotkey_info"]["enabled"] is False
        assert ra.state != "conflict" and rb.state == "disabled"
        return True
    assert run_app(body)


def test_remove_duplicate_reorder_rename_reset(monkeypatch):
    def body(app):
        from sharel.ui import gtkutil
        from sharel.ui.windows.hotkeysettings import open_hotkey_settings
        hks = app.store.hotkeys.hotkeys
        hks[0].task_settings.watch_folder_enabled = True
        win = open_hotkey_settings(app)
        yield 0.1
        jobs = lambda: [h.job.value for h in hks]  # noqa: E731
        saved_jobs = lambda: [h["task_settings"]["job"] for h in saved_hotkeys(app)]  # noqa: E731
        start = jobs()
        assert start == ["RectangleRegion", "PrintScreen", "ActiveWindow", "ScreenRecorder", "ScreenRecorderGIF"]
        win.select(0)
        win.move_selected(1)
        assert jobs()[:2] == ["PrintScreen", "RectangleRegion"] and saved_jobs() == jobs()
        assert win.selected_row().hk.job == HotkeyType.RectangleRegion
        win.select(0)
        win.move_selected(-1)  # wraps to the end, like ShareX
        assert jobs()[-1] == "PrintScreen" and saved_jobs() == jobs()

        win.select(0)
        first = win.selected_row().hk
        dup = win.duplicate_selected()
        assert hks[1] is dup and dup.job == first.job and dup is not first
        assert dup.hotkey_info.hotkey == "" and first.hotkey_info.hotkey == "<Control>Print"
        assert dup.task_settings is not first.task_settings
        assert dup.task_settings.watch_folder_enabled is False and first.task_settings.watch_folder_enabled
        assert len(saved_hotkeys(app)) == 6

        monkeypatch.setattr(gtkutil, "prompt_text", lambda _p, _h, _d, on_done, **_k: on_done("  Copy of region "))
        win.select(1)
        win.rename_selected()
        assert dup.task_settings.description == "Copy of region"
        assert win.rows[1].title.get_label() == "Copy of region"
        assert saved_hotkeys(app)[1]["task_settings"]["description"] == "Copy of region"

        win.select(1)
        win.remove_selected()
        assert dup not in hks and len(saved_hotkeys(app)) == 5 and len(win.rows) == 5

        # change task via the picker
        win.select(0)
        dlg = win.change_task_selected()
        next(r for r, _k in dlg.rows if r.get_subtitle() == "OCR").emit("activated")
        assert hks[0].job == HotkeyType.OCR and saved_jobs()[0] == "OCR"

        monkeypatch.setattr(gtkutil, "ask", lambda _p, _h, _b, _r, on_done, **_k: on_done("cancel"))
        win.reset_defaults()
        assert hks[0].job == HotkeyType.OCR
        monkeypatch.setattr(gtkutil, "ask", lambda _p, _h, _b, _r, on_done, **_k: on_done("reset"))
        win.reset_defaults()
        assert jobs() == start and saved_jobs() == start and len(win.rows) == 5
        assert hks is app.store.hotkeys.hotkeys  # edited in place
        return True
    assert run_app(body)


def test_edit_opens_task_settings_for_workflow():
    def body(app):
        from sharel.ui.windows.hotkeysettings import open_hotkey_settings
        win = open_hotkey_settings(app)
        yield 0.1
        row = win.rows[2]
        ts_win = win.edit(row)
        yield 0.2
        assert ts_win.hotkey is row.hk and ts_win.get_mapped()
        from sharel.ui.windows._sectioned import find_row
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        find_row(ts_win.pages["task"], "Description", Adw.EntryRow).set_text("Window shot")
        ts_win.close()
        yield 0.1
        assert row.title.get_label() == "Window shot"
        assert saved_hotkeys(app)[2]["task_settings"]["description"] == "Window shot"
        return True
    assert run_app(body)


def test_status_comes_from_hotkey_manager():
    def body(app):
        from sharel.shell.hotkeys import HotkeyStatus
        from sharel.ui.windows.hotkeysettings import open_hotkey_settings
        win = open_hotkey_settings(app)
        yield 0.1
        hks = app.store.hotkeys.hotkeys
        hks[3].hotkey_info.hotkey = "<Control><Alt>F11"
        app.hotkeys.status = [HotkeyStatus(hks[3], "failed", "the shell refused the shortcut"),
                              HotkeyStatus(hks[4], "registered", "")]
        hks[4].hotkey_info.hotkey = "<Control><Alt>F12"
        win.refresh_status()
        assert win.rows[3].state == "failed" and "refused" in win.rows[3].detail
        assert win.rows[4].state == "registered"
        assert win.rows[4].status_icon.get_icon_name() == "emblem-ok-symbolic"
        app.store.app.disable_hotkeys = True
        win.refresh_status()
        assert win.banner.get_revealed()
        win.enable_hotkeys()
        assert app.store.app.disable_hotkeys is False and not win.banner.get_revealed()
        return True
    assert run_app(body)


def test_disable_hotkeys_job_toggles_and_on_hotkey_ignores():
    def body(app):
        import sharel.app as appmod
        from sharel.core.jobs import dispatch
        app.store.app.hotkey_repeat_limit = 0
        app.stop_tray()  # the tray icon state is not under test here
        dispatch(app, HotkeyType.DisableHotkeys, source="hotkey")
        assert app.store.app.disable_hotkeys is True
        on_disk = json.loads((app.store.directory / "ApplicationConfig.json").read_text())
        assert on_disk["disable_hotkeys"] is True
        calls = []
        real = appmod.dispatch
        appmod.dispatch = lambda _app, job, hk=None, **_k: calls.append(job)
        try:
            region = HotkeySettings(HotkeyInfo("<Control>F1"), TaskSettings(job=HotkeyType.RectangleRegion))
            toggle = HotkeySettings(HotkeyInfo("<Control>F2"), TaskSettings(job=HotkeyType.DisableHotkeys))
            app.on_hotkey(region)
            yield 0.1
            assert calls == []  # ignored while disabled
            app.on_hotkey(toggle)  # the toggle itself keeps working
            yield 0.1
            assert calls == [HotkeyType.DisableHotkeys]
            appmod.dispatch = real
            dispatch(app, HotkeyType.DisableHotkeys, source="hotkey")
            assert app.store.app.disable_hotkeys is False
            appmod.dispatch = lambda _app, job, hk=None, **_k: calls.append(job)
            app.on_hotkey(region)
            yield 0.1
            assert calls[-1] == HotkeyType.RectangleRegion
            # repeat limit: a second activation within the limit is dropped
            app.store.app.hotkey_repeat_limit = 10_000
            calls.clear()
            screen = HotkeySettings(HotkeyInfo("<Control>F3"), TaskSettings(job=HotkeyType.PrintScreen))
            app.on_hotkey(screen)
            app.on_hotkey(screen)
            yield 0.1
            assert calls == [HotkeyType.PrintScreen]
            # fullscreen check (no extension in the sandbox -> not fullscreen)
            app.store.app.hotkey_repeat_limit = 0
            app.store.app.disable_hotkeys_on_fullscreen = True
            app._focused_window_fullscreen = lambda: True
            app.on_hotkey(region)
            yield 0.1
            assert calls == [HotkeyType.PrintScreen]
        finally:
            appmod.dispatch = real
        return True
    assert run_app(body)
