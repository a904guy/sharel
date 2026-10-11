"""Destination settings window: generated pages for every service, saving, Authorize, Test connection,
helpers, defunct pages, current destination and test upload."""
import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from uihelpers import find, find_all, labels, run_app

from sharel.core.enums import FileDestination, ImageDestination, TextDestination
from sharel.uploaders import registry
from sharel.uploaders.registry import OAuth2Info


def _uploaders_json() -> dict:
    return json.loads((Path(os.environ["SHAREL_CONFIG_DIR"]) / "UploadersConfig.json").read_text())


def _wait_idle(win, limit=200):
    for _ in range(limit):
        if win.busy == 0:
            return
        yield 0.05
    raise AssertionError("worker action did not finish")


def test_every_service_gets_a_page_grouped_and_searchable():
    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app)
        yield 0.2
        assert win.get_mapped()
        services = registry.load_all()
        assert set(win.rows) == set(services)
        for key, info in services.items():
            page = win.show_page(key)
            assert isinstance(page, Adw.PreferencesPage)
            assert win.stack.get_visible_child() is page
            assert info.title in labels(page)
            if info.settings_cls is not None:
                # every non-hidden settings field produced a row (or the field type has no editor)
                assert "Settings" in labels(page)
        # category headers
        heads = [r.get_header().get_label() for r in win.rows.values() if r.get_header() is not None]
        assert heads == list(registry.CATEGORY_TITLES.values())
        # search narrows the sidebar
        win.search.set_text("imgur")
        yield 0.3
        assert win.visible_keys() == ["image/Imgur"]
        win.search.set_text("url shorteners")
        yield 0.3
        assert set(win.visible_keys()) == {i.key for i in registry.by_category("shortener")}
        # opening again presents the same window
        assert open_destination_settings(app) is win
        return True
    assert run_app(body, timeout=120)


def test_editing_a_field_changes_settings_and_saves_json():
    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "text/Pastebin")
        page = win.pages["text/Pastebin"]
        entry = find(page, lambda w: isinstance(w, Adw.EntryRow) and w.get_title() == "Title")
        entry.set_text("My paste")
        sw = find(page, lambda w: isinstance(w, Adw.SwitchRow) and w.get_title() == "Use raw URL")
        sw.set_active(True)
        settings = app.store.uploaders.settings_for("text/Pastebin", registry.get("text", "Pastebin").settings_cls)
        assert settings.title == "My paste" and settings.raw_url is True
        yield 0.6
        data = _uploaders_json()
        assert data["services"]["text/Pastebin"]["title"] == "My paste"
        assert data["services"]["text/Pastebin"]["raw_url"] is True
        return True
    assert run_app(body)


def test_authorize_calls_module_authorize_on_a_worker_and_refreshes(monkeypatch):
    from sharel.uploaders.services import api_dropbox
    calls = []

    def fake_authorize(settings, ctx):
        import threading
        calls.append((threading.current_thread().name, ctx))
        settings.oauth2 = OAuth2Info(access_token="tok", refresh_token="ref")
        ctx.save_settings()
        return settings.oauth2
    monkeypatch.setattr(api_dropbox, "authorize", fake_authorize)

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "file/Dropbox")
        page = win.pages["file/Dropbox"]
        assert "Not signed in" in labels(page)
        page.buttons["Authorize…"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        assert len(calls) == 1 and calls[0][0].startswith("dest-")
        ctx = calls[0][1]
        from sharel.core import desktop
        opened = []
        monkeypatch.setattr(desktop, "open_url", opened.append)
        ctx.open_url("https://example.invalid/auth")  # the system browser
        assert opened == ["https://example.invalid/auth"]
        new_page = win.pages["file/Dropbox"]
        assert new_page is not page and "Signed in" in labels(new_page)
        assert _uploaders_json()["services"]["file/Dropbox"]["oauth2"]["access_token"] == "tok"
        return True
    assert run_app(body)


def test_test_connection_against_local_ftp_server(tmp_path):
    from _fileproto_servers import pyftpd
    root = tmp_path / "ftproot"
    root.mkdir()

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        from sharel.uploaders.services.file_ftp import FTPAccount
        info = registry.get("file", "FTP")
        s = app.store.uploaders.settings_for(info.key, info.settings_cls)
        s.ftp_account_list = [FTPAccount(name="local", host="127.0.0.1", port=port, username="alice",
                                         password="s3cret", sub_folder_path="shots")]
        win = open_destination_settings(app, "file/FTP")
        alerts = []
        import sharel.ui.gtkutil as gu
        gu_alert = gu.alert
        gu.alert = lambda parent, heading, body="", on_done=None: alerts.append((heading, body))
        try:
            win.pages["file/FTP"].buttons["Test connection"].emit("clicked")
            yield from _wait_idle(win)
            yield 0.1
        finally:
            gu.alert = gu_alert
        assert alerts and alerts[0][0] == f"{registry.get('file', 'FTP').title}: connection OK", alerts
        assert (root / "shots").is_dir()
        assert "USER" in handler.commands_seen and "PASS" in handler.commands_seen
        return True
    with pyftpd(root) as (port, handler):
        assert run_app(body)


def test_test_connection_failure_shows_alert(monkeypatch):
    from sharel.uploaders.services import file_s3
    from sharel.uploaders.base import UploaderError

    def boom(settings, ctx):
        raise UploaderError("bucket missing")
    monkeypatch.setattr(file_s3, "test_connection", boom)
    alerts = []
    import sharel.ui.gtkutil as gu
    monkeypatch.setattr(gu, "alert", lambda parent, heading, body="", on_done=None: alerts.append((heading, body)))

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "file/AmazonS3")
        win.pages["file/AmazonS3"].buttons["Test connection"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        assert alerts == [("Amazon S3 connection test failed", "bucket missing")] or \
            (alerts and alerts[0][1] == "bucket missing")
        return True
    assert run_app(body)


def test_helper_picker_fills_the_target_field(monkeypatch):
    from sharel.uploaders.services import api_box, api_pushbullet, share_pushbullet
    monkeypatch.setattr(api_box, "list_folders", lambda s, ctx: [{"id": "11", "name": "Shots"},
                                                                 {"id": "12", "name": "Docs"}])
    monkeypatch.setattr(api_pushbullet, "get_devices", lambda s, ctx: [{"key": "dev1", "name": "Phone"}])
    monkeypatch.setattr(share_pushbullet, "refresh_devices",
                        lambda s, ctx: [share_pushbullet.PushbulletDevice("a", "Tablet"),
                                        share_pushbullet.PushbulletDevice("b", "Laptop")])
    import sharel.ui.gtkutil as gu
    offered = []

    def fake_choice(parent, heading, options, on_done):
        offered.append((heading, list(options)))
        on_done(options[-1])
    monkeypatch.setattr(gu, "prompt_choice", fake_choice)

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "file/Box")
        win.pages["file/Box"].buttons["Select folder…"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        box = app.store.uploaders.settings_for("file/Box", registry.get("file", "Box").settings_cls)
        assert offered[-1] == ("Select folder", ["Shots", "Docs"])
        assert box.folder_id == "12" and box.folder_name == "Docs"
        assert _uploaders_json()["services"]["file/Box"]["folder_id"] == "12"

        win.select("file/Pushbullet")
        win.pages["file/Pushbullet"].buttons["Get device list…"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        assert offered[-1][1] == ["All devices", "Phone"]
        pb = app.store.uploaders.settings_for("file/Pushbullet", registry.get("file", "Pushbullet").settings_cls)
        assert pb.device_iden == "dev1"

        win.select("sharing/Pushbullet")
        win.pages["sharing/Pushbullet"].buttons["Get device list…"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        sp = app.store.uploaders.settings_for("sharing/Pushbullet",
                                              registry.get("sharing", "Pushbullet").settings_cls)
        assert offered[-1][1] == ["All devices", "Tablet", "Laptop"]
        assert sp.selected_device == 1
        return True
    assert run_app(body)


def test_login_helper_runs_and_rebuilds_page(monkeypatch):
    from sharel.uploaders.services import api_puush

    def fake_login(settings, ctx):
        settings.api_key = "KEY123"
        return "KEY123"
    monkeypatch.setattr(api_puush, "login", fake_login)
    import sharel.ui.gtkutil as gu
    alerts = []
    monkeypatch.setattr(gu, "alert", lambda parent, heading, body="", on_done=None: alerts.append(heading))

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "file/Puush")
        win.pages["file/Puush"].buttons["Log in"].emit("clicked")
        yield from _wait_idle(win)
        yield 0.1
        page = win.pages["file/Puush"]
        entry = find(page, lambda w: isinstance(w, Adw.PasswordEntryRow) and w.get_title() == "API key")
        assert entry.get_text() == "KEY123"
        assert alerts and "Log in done" in alerts[0]
        assert _uploaders_json()["services"]["file/Puush"]["api_key"] == "KEY123"
        return True
    assert run_app(body)


def test_defunct_pages_are_disabled_with_reason_and_evidence():
    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app)
        defunct = [i for i in registry.load_all().values() if i.defunct]
        assert defunct
        for info in defunct:
            page = win.show_page(info.key)
            assert page.defunct
            assert not page.actions_group.get_sensitive()
            assert not page.buttons["Set as current destination"].get_sensitive()
            links = find_all(page, lambda w: isinstance(w, Gtk.LinkButton) and w.get_label() == "Evidence")
            assert links and links[0].get_uri() == info.defunct
            assert links[0].get_sensitive()
            assert info.defunct_reason in labels(page)
            assert "(unavailable)" in win.rows[info.key].get_child().get_label()
        return True
    assert run_app(body)


def test_set_as_current_destination_updates_task_settings():
    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        win = open_destination_settings(app, "image/Chevereto")
        win.pages["image/Chevereto"].buttons["Set as current destination"].emit("clicked")
        ts = app.store.app.default_task_settings
        assert ts.image_destination == ImageDestination.Chevereto
        win.select("file/FTP")
        win.pages["file/FTP"].buttons["Set as current destination"].emit("clicked")
        assert ts.file_destination == FileDestination.FTP
        win.pages["file/FTP"].buttons["Use for text"].emit("clicked")
        assert ts.text_destination == TextDestination.FileUploader and ts.text_file_destination == FileDestination.FTP
        saved = json.loads((Path(os.environ["SHAREL_CONFIG_DIR"]) / "ApplicationConfig.json").read_text())
        assert "FTP" in json.dumps(saved)
        assert win.pages["file/FTP"].current_row.get_subtitle() == "This is the current destination"
        return True
    assert run_app(body)


def test_test_upload_goes_through_tasks_to_shared_folder(tmp_path):
    out = tmp_path / "share"
    out.mkdir()

    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        from sharel.uploaders.services.file_sharedfolder import LocalhostAccount
        info = registry.get("file", "SharedFolder")
        s = app.store.uploaders.settings_for(info.key, info.settings_cls)
        s.localhost_account_list = [LocalhostAccount(name="local", localhost_root=str(out))]
        win = open_destination_settings(app, info.key)
        assert app.store.app.default_task_settings.file_destination != FileDestination.SharedFolder
        task = win.test_upload(info)
        for _ in range(100):
            if task.done.is_set():
                break
            yield 0.05
        assert task.info.status.name == "Completed", task.info.errors if hasattr(task.info, "errors") else ""
        files = list(out.iterdir())
        assert [f.name for f in files] == ["ShareL test file.png"]
        assert files[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # the default destination was not changed by the test upload
        assert app.store.app.default_task_settings.file_destination != FileDestination.SharedFolder
        return True
    assert run_app(body)


def test_test_upload_button_selects_destination_per_category(monkeypatch):
    def body(app):
        from sharel.ui.windows.destinations import open_destination_settings
        seen = []
        app.tasks.upload_data = lambda data, name, ts: seen.append(("data", name, ts))
        app.tasks.upload_text = lambda text, ts: seen.append(("text", text, ts))
        app.tasks.shorten_url = lambda url, ts: seen.append(("short", url, ts))
        app.tasks.share_url = lambda url, ts: seen.append(("share", url, ts))
        win = open_destination_settings(app, "image/Vgyme")
        win.pages["image/Vgyme"].buttons["Test upload"].emit("clicked")
        for key in ("text/Hastebin", "shortener/ISGD", "sharing/Reddit"):
            win.select(key)
            win.pages[key].buttons["Test upload"].emit("clicked")
        assert [s[0] for s in seen] == ["data", "text", "short", "share"]
        assert seen[0][2].image_destination.value == "Vgyme" and seen[0][1].endswith(".png")
        assert seen[1][2].text_destination.value == "Hastebin"
        assert seen[2][2].url_shortener_destination.value == "ISGD"
        assert seen[3][2].url_sharing_service_destination.value == "Reddit"
        return True
    assert run_app(body)
