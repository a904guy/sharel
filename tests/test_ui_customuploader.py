"""Custom uploader editor: list management, every field through the widgets, .sxcu export/import,
the Test button against a local HTTP server, and dispatch to custom uploaders for every destination type."""
import contextlib
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from uihelpers import run_app

from sharel.uploaders.custom import sxcu
from sharel.uploaders.custom.model import CustomUploaderItem


@contextlib.contextmanager
def local_server():
    """Answers every request with JSON naming the uploaded thing; records method, path, headers, body."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
            port = self.server.server_address[1]
            out = json.dumps({"data": {"link": f"http://127.0.0.1:{port}/f/abc.png",
                                       "thumb": f"http://127.0.0.1:{port}/t/abc.png",
                                       "delete": f"http://127.0.0.1:{port}/d/abc"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_POST = do_GET = do_PUT = _answer

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()


def _wait(win, limit=200):
    for _ in range(limit):
        if win.busy == 0:
            return
        yield 0.05
    raise AssertionError("worker did not finish")


def _saved() -> dict:
    return json.loads((Path(os.environ["SHAREL_CONFIG_DIR"]) / "UploadersConfig.json").read_text())


def _fill(win, url):
    """Create an uploader through the widgets and set every field."""
    win.buttons["New"].emit("clicked")
    win.entries["name"].set_text("Local host")
    for t in ("ImageUploader", "FileUploader", "TextUploader"):
        win.type_checks[t].set_active(True)
    from sharel.uploaders.custom.model import BODY_TYPES, METHODS
    win.method_row.set_selected(METHODS.index("POST"))
    win.entries["request_url"].set_text(url + "/upload")
    k, v = win.params_table.add_row("", "")
    k.set_text("expires")
    v.set_text("1h")
    k, v = win.headers_table.add_row("", "")
    k.set_text("Authorization")
    v.set_text("Bearer xyz")
    win.body_row.set_selected(BODY_TYPES.index("MultipartFormData"))
    k, v = win.args_table.add_row("", "")
    k.set_text("title")
    v.set_text("hello")
    win.entries["file_form_name"].set_text("file")
    win.entries["url"].set_text("{json:data.link}")
    win.entries["thumbnail_url"].set_text("{json:data.thumb}")
    win.entries["deletion_url"].set_text("{json:data.delete}")
    win.entries["error_message"].set_text("{json:error}")


def test_add_edit_export_reimport(tmp_path):
    def body(app):
        from sharel.ui.windows.customuploader import open_custom_uploaders
        win = open_custom_uploaders(app)
        yield 0.2
        assert win.get_mapped()
        _fill(win, "https://example.invalid")
        item = win.selected_item()
        assert item.name == "Local host"
        assert item.destination_type == ["ImageUploader", "TextUploader", "FileUploader"]
        assert item.request_method == "POST" and item.request_url == "https://example.invalid/upload"
        assert item.parameters == {"expires": "1h"} and item.headers == {"Authorization": "Bearer xyz"}
        assert item.body == "MultipartFormData" and item.arguments == {"title": "hello"}
        assert item.file_form_name == "file" and item.url == "{json:data.link}"
        assert item.thumbnail_url == "{json:data.thumb}" and item.deletion_url == "{json:data.delete}"
        assert item.error_message == "{json:error}"
        assert win.listbox.get_row_at_index(0).get_child().get_label() == "Local host"
        # body switch shows the Data editor and hides arguments
        win.body_row.set_selected(3)  # JSON
        assert win.data_box.get_visible() and not win.args_box.get_visible()
        win.data_view.get_buffer().set_text('{"a": 1, "b": [1, 2]}')
        win.minify_btn.emit("clicked")
        assert item.data == '{"a":1,"b":[1,2]}'
        win.beautify_btn.emit("clicked")
        assert item.data.startswith("{\n  \"a\": 1")
        win.body_row.set_selected(1)  # back to multipart
        yield 0.5
        saved = _saved()["custom_uploaders"]
        assert saved[0]["name"] == "Local host" and saved[0]["headers"] == {"Authorization": "Bearer xyz"}

        out = win.export_to(str(tmp_path / "local"))
        assert out.endswith("local.sxcu")
        exported = json.loads(Path(out).read_text(encoding="utf-8"))
        assert exported["RequestURL"] == "https://example.invalid/upload"
        assert exported["Arguments"] == {"title": "hello"}
        # remove then re-import
        win.buttons["Remove"].emit("clicked")
        assert win.items == []
        win.import_paths([out])
        assert len(win.items) == 1
        re = win.items[0]
        assert (re.name, re.request_url, re.parameters, re.headers, re.arguments, re.url, re.thumbnail_url) == \
            ("Local host", "https://example.invalid/upload", {"expires": "1h"}, {"Authorization": "Bearer xyz"},
             {"title": "hello"}, "{json:data.link}", "{json:data.thumb}")
        assert win.entries["name"].get_text() == "Local host"
        # export all
        win.duplicate_selected()
        paths = win.export_all_to(str(tmp_path / "all"))
        assert len(paths) == 2 and all(Path(p).exists() for p in paths)
        return True
    assert run_app(body)


def test_list_management_and_selected_for(monkeypatch):
    def body(app):
        from sharel.ui.windows.customuploader import open_custom_uploaders
        win = open_custom_uploaders(app)
        for name in ("A", "B", "C"):
            win.add_new()
            win.entries["name"].set_text(name)
        cfg = app.store.uploaders
        assert [i.name for i in cfg.custom_uploaders] == ["A", "B", "C"]
        win.selected_rows["ImageUploader"].set_selected(2)
        win.selected_rows["TextUploader"].set_selected(1)
        assert cfg.custom_image_uploader_selected == 2 and cfg.custom_text_uploader_selected == 1
        # move C up: the image selection follows it
        assert win.selected_index() == 2
        win.buttons["Move up"].emit("clicked")
        assert [i.name for i in win.items] == ["A", "C", "B"]
        assert cfg.custom_image_uploader_selected == 1 and cfg.custom_text_uploader_selected == 2
        win.buttons["Move down"].emit("clicked")
        assert [i.name for i in win.items] == ["A", "B", "C"]
        # remove B (the text selection) -> reset to the last item; image (C, index 2) shifts down
        win.listbox.select_row(win.listbox.get_row_at_index(1))
        win.buttons["Remove"].emit("clicked")
        assert [i.name for i in win.items] == ["A", "C"]
        assert cfg.custom_image_uploader_selected == 1 and cfg.custom_text_uploader_selected == 1
        win.buttons["Duplicate"].emit("clicked")
        assert [i.name for i in win.items] == ["A", "C", "C"] and win.items[1] is not win.items[2]
        yield 0.5
        data = _saved()
        assert data["custom_image_uploader_selected"] == 1 and len(data["custom_uploaders"]) == 3

        # clipboard JSON import (an array of two uploaders)
        from sharel.core import clipboard
        text = json.dumps([{"Name": "Clip1", "RequestURL": "https://a.example/up", "DestinationType":
                            "ImageUploader", "Body": "MultipartFormData", "FileFormName": "f"},
                           {"Name": "Clip2", "RequestURL": "https://b.example/up", "DestinationType":
                            "URLShortener", "RequestMethod": "GET"}])
        monkeypatch.setattr(clipboard, "read", lambda: clipboard.ClipboardContent("text", text=text))
        win.action_group.activate_action("import-clipboard", None)
        assert [i.name for i in win.items][-2:] == ["Clip1", "Clip2"]
        assert win.items[-1].destination_type == ["URLShortener"]
        win.clear_all()
        assert win.items == [] and cfg.custom_uploaders == []
        return True
    assert run_app(body)


def test_syntax_help_inserts_into_focused_field():
    def body(app):
        from sharel.ui.windows.customuploader import open_custom_uploaders
        win = open_custom_uploaders(app)
        win.add_new()
        yield 0.2
        win.entries["url"].grab_focus()
        yield 0.1
        win.insert_syntax("{json:data.link}")
        assert win.selected_item().url == "{json:data.link}"
        win.data_view.grab_focus()
        yield 0.1
        win.insert_syntax("{input}")
        assert win.selected_item().data == "{input}"
        return True
    assert run_app(body)


def test_test_button_against_local_server_shows_parsed_url():
    def body(app):
        from sharel.ui.windows.customuploader import open_custom_uploaders
        with local_server() as (url, seen):
            win = open_custom_uploaders(app)
            _fill(win, url)
            win.selected_rows["ImageUploader"].set_selected(0)
            win.test_buttons["ImageUploader"].emit("clicked")
            yield from _wait(win)
            yield 0.1
            res = win.last_test["result"]
            assert res.url == f"{url}/f/abc.png" and res.thumbnail_url == f"{url}/t/abc.png"
            assert res.deletion_url == f"{url}/d/abc"
            assert f"URL: {url}/f/abc.png" in win.result_text("result")
            assert win.result_text("request").startswith(f"POST {url}/upload?expires=1h")
            assert "Authorization: Bearer xyz" in win.result_text("request")
            assert win.result_text("response").startswith("HTTP 200")
            assert seen[0]["method"] == "POST" and seen[0]["path"] == "/upload?expires=1h"
            assert seen[0]["headers"]["Authorization"] == "Bearer xyz"
            assert b'name="title"' in seen[0]["body"] and b'filename="Test.png"' in seen[0]["body"]
            # the syntax tester works on the last response
            assert win.syntax_test("{json:data.thumb}") == f"{url}/t/abc.png"
        return True
    assert run_app(body)


def test_import_from_url_local_server(tmp_path):
    item = CustomUploaderItem(name="Remote", request_url="https://x.example/u", destination_type=["FileUploader"],
                              file_form_name="file")
    payload = sxcu.dumps(item).encode()

    def body(app):
        from http.server import SimpleHTTPRequestHandler
        (tmp_path / "web").mkdir()
        (tmp_path / "web" / "r.sxcu").write_bytes(payload)

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(tmp_path / "web"), **kw)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            from sharel.ui.windows.customuploader import open_custom_uploaders
            win = open_custom_uploaders(app)
            got = []
            win.import_url(f"http://127.0.0.1:{srv.server_address[1]}/r.sxcu", got.append)
            yield from _wait(win)
            yield 0.1
            assert got and got[0][0].name == "Remote"
            assert win.items[-1].request_url == "https://x.example/u"
        finally:
            srv.shutdown()
            srv.server_close()
        return True
    assert run_app(body)


def test_gtk_prompts_for_select_and_inputbox(monkeypatch):
    """{select:} / {inputbox:} go through GtkTaskHost.prompt_select/prompt_input -> gtkutil dialogs."""
    import sharel.ui.gtkutil as gu
    asked = []

    def fake_choice(parent, heading, options, on_done):
        asked.append(("choice", heading, list(options)))
        on_done(options[1])

    def fake_text(parent, heading, default, on_done, body="", multiline=False):
        asked.append(("text", heading, default))
        on_done("typed value")
    monkeypatch.setattr(gu, "prompt_choice", fake_choice)
    monkeypatch.setattr(gu, "prompt_text", fake_text)

    def body(app):
        from sharel.ui.host import GtkTaskHost
        from sharel.uploaders.base import UploadContext
        from sharel.uploaders.custom.parser import CustomUploaderSyntaxParser
        host = GtkTaskHost(app)
        assert host.prompt_select("Pick", ["a", "b"]) == "b"
        assert host.prompt_input("Name", "x") == "typed value"
        # from a worker thread too, as a real upload does
        ctx = UploadContext(prompt_input=host.prompt_input, prompt_select=host.prompt_select)
        box = {}

        def work():
            p = CustomUploaderSyntaxParser(file_name="a.png", ctx=ctx)
            box["v"] = p.parse("{select:one|two}-{inputbox:Title|def}")
        t = threading.Thread(target=work)
        t.start()
        for _ in range(100):
            if not t.is_alive():
                break
            yield 0.05
        assert box["v"] == "two-typed value"
        assert ("choice", "Select", ["one", "two"]) in asked and ("text", "Title", "def") in asked
        return True
    assert run_app(body)


def test_dispatch_to_custom_uploaders_for_every_destination_type():
    """CustomImageUploader / CustomTextUploader / CustomFileUploader / CustomURLShortener /
    CustomURLSharingService through sharel.uploaders.dispatch to a local server."""
    from sharel.core.config import ApplicationConfig, TaskSettings
    from sharel.core.enums import (EDataType, FileDestination, ImageDestination, TextDestination,
                                   UrlShortenerType, URLSharingServices)
    from sharel.uploaders import dispatch
    from sharel.uploaders.base import UploadContext
    from sharel.uploaders.config import UploadersConfig

    with local_server() as (url, seen):
        other = CustomUploaderItem(name="Other", request_url="http://127.0.0.1:9/never",
                                   destination_type=list(("ImageUploader",)))
        item = CustomUploaderItem(name="Local", request_url=url + "/up", body="MultipartFormData",
                                  file_form_name="file", url="{json:data.link}",
                                  destination_type=["ImageUploader", "TextUploader", "FileUploader", "URLShortener",
                                                    "URLSharingService"])
        short = CustomUploaderItem(name="Short", request_url=url + "/s", request_method="GET",
                                   parameters={"u": "{input}"}, body="None", url="{json:data.link}",
                                   destination_type=["URLShortener", "URLSharingService"])
        cfg = UploadersConfig(custom_uploaders=[other, item, short], custom_image_uploader_selected=1,
                              custom_text_uploader_selected=1, custom_file_uploader_selected=1,
                              custom_url_shortener_selected=2, custom_url_sharing_service_selected=2)
        ts = TaskSettings(image_destination=ImageDestination.CustomImageUploader,
                          text_destination=TextDestination.CustomTextUploader,
                          file_destination=FileDestination.CustomFileUploader,
                          url_shortener_destination=UrlShortenerType.CustomURLShortener,
                          url_sharing_service_destination=URLSharingServices.CustomURLSharingService)
        app = ApplicationConfig()
        ctx = UploadContext()
        res, dest = dispatch.upload_file(io.BytesIO(b"\x89PNG fake"), "a.png", EDataType.Image, ts, cfg, app, ctx)
        assert dest.name == "CustomImageUploader" and res.url == f"{url}/f/abc.png"
        assert seen[-1]["path"] == "/up" and b'filename="a.png"' in seen[-1]["body"]
        res, dest = dispatch.upload_file(io.BytesIO(b"bin"), "a.bin", EDataType.File, ts, cfg, app, ctx)
        assert dest.name == "CustomFileUploader" and res.url == f"{url}/f/abc.png"
        res, dest = dispatch.upload_text("hello text", "t.txt", ts, cfg, app, ctx)
        assert dest.name == "CustomTextUploader" and b"hello text" in seen[-1]["body"]
        res, dest = dispatch.shorten_url("https://long.example/x", ts, cfg, app, ctx)
        assert dest.name == "CustomURLShortener" and res.shortened_url == f"{url}/f/abc.png"
        assert seen[-1]["method"] == "GET" and seen[-1]["path"].startswith("/s?u=https%3A%2F%2Flong.example")
        shared, dest = dispatch.share_url("https://long.example/y", ts, cfg, ctx)
        assert dest.name == "CustomURLSharingService" and shared == ""
        assert seen[-1]["path"].startswith("/s?u=https%3A%2F%2Flong.example%2Fy")
        assert len(seen) == 5
