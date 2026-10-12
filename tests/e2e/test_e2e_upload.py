"""Scenario 12: a custom uploader made in the Custom uploader settings window (real input), pointed
at a local HTTP server; a capture is uploaded there, its URL lands on the clipboard and in history."""
from __future__ import annotations

import email.parser
import email.policy
import http.server
import json
import threading
import time

import pytest
from conftest import found, wait_for

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


class Host(http.server.ThreadingHTTPServer):
    """A tiny image host: multipart POST /upload -> {"data": {"link": ...}}. Nothing leaves the machine."""

    def __init__(self) -> None:
        self.uploads: list[dict] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
        n = len(self.server.uploads) + 1
        link = f"{self.server.url}/i/{n}.png"
        self.server.uploads.append({"path": self.path, "fields": sorted(parts), "auth": self.headers.get("X-Key"),
                                    "file": parts["file"].get_payload(decode=True) if "file" in parts else b"",
                                    "filename": parts["file"].get_filename() if "file" in parts else "",
                                    "link": link})
        out = json.dumps({"data": {"link": link}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        pass


@pytest.fixture(scope="module")
def host():
    srv = Host()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.fixture(scope="module")
def app(session):
    # Only the workflow is seeded; the uploader itself is made through the UI below.
    session.seed_config(app={"default_task_settings": {
        "after_capture_job": ["SaveImageToFile", "UploadImageToHost"],
        "after_upload_job": ["CopyURLToClipboard"],
        "image_destination": "CustomImageUploader"}})
    session.start_app()
    yield session
    session.stop_app()


async def test_s12_custom_uploader_made_in_the_ui(app, host):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        s.sharel("-OpenMainWindow")
        s.wait_window("ShareL")
        time.sleep(1.0)  # let the window's map animation finish before clicking into it
        # Destinations > Custom uploader settings…, by real clicks in the main window
        await call("click", text="Destinations", window="ShareL")
        time.sleep(0.6)
        text, data, _i, _e = await call("find", name="Custom uploader settings…", window="ShareL", limit=3)
        await call("screenshot", window="ShareL", max_width=1000)  # evidence: the Destinations menu open
        assert found(data, "Custom uploader settings…"), text[:600]
        await call("click", text="Custom uploader settings…", window="ShareL")
        win = s.wait_window("Custom uploader settings")
        title = win["title"]

        async def item_of(name, role=None):
            kw = {"role": role} if role else {}
            text, data, _i, _e = await call("find", name=name, window=title, limit=10, **kw)
            hits = [it for it in data["data"] if it.get("name") == name]
            assert hits, f"{name!r} not found: {text[:600]}"
            return hits[0]

        async def ref_of(name, role=None):
            return (await item_of(name, role))["ref"]

        async def scroll_into_view(name, role=None):
            """Wheel-scroll the editor (as a user would) until the field is inside the window."""
            fx, fy, fw, fh = s.ext("GetWindowDetails", f"uint64 {win['id']}")["frame"]
            for _ in range(15):
                it = await item_of(name, role)
                _bx, by, _bw, bh = it.get("bounds") or (0, -1, 0, 0)
                if fy + 60 <= by and by + bh <= fy + fh - 10:
                    return it["ref"]
                await call("scroll", dy=3 if by + bh > fy + fh - 10 else -3, x=fx + fw * 0.65, y=fy + fh / 2)
                time.sleep(0.3)
            raise AssertionError(f"could not scroll {name!r} into view")

        async def fill(name, value):
            await call("click", ref=await scroll_into_view(name, "text"), restore_focus=False)
            await call("key", combo="ctrl+a", window=title, restore_focus=False)
            await call("type_text", text=value, window=title, restore_focus=False)

        await call("click", ref=await ref_of("New", "button"), restore_focus=False)
        await fill("Name", "Local test host")
        await call("click", ref=await scroll_into_view("Image uploader"), restore_focus=False)
        await fill("Request URL", f"{host.url}/upload")
        await fill("File form name", "file")
        await fill("URL", "{json:data.link}")
        time.sleep(1.0)  # the window saves shortly after the last edit
        await call("screenshot", window=title, max_width=1000)  # evidence: the uploader as made in the UI
        saved = json.loads((s.cfg_dir / "UploadersConfig.json").read_text())["custom_uploaders"]
        item = next(it for it in saved if it["name"] == "Local test host")
        assert item["request_url"] == f"{host.url}/upload" and item["file_form_name"] == "file"
        assert item["destination_type"] == ["ImageUploader"] and item["url"] == "{json:data.link}"
        await call("window_close", window=title)

        # a capture goes to it: the server gets the PNG, the URL is copied, history has it
        assert host.uploads == []
        s.sharel("-PrintScreen")
        up = wait_for(lambda: host.uploads, timeout=30, what="upload at the local host")[0]
        assert up["path"] == "/upload" and up["fields"] == ["file"]
        assert up["file"][:8] == b"\x89PNG\r\n\x1a\n" and up["filename"].endswith(".png")
        wait_for(lambda: s.clipboard("text/plain").decode() == up["link"], what="uploaded URL on the clipboard")
        entry = wait_for(lambda: next((h for h in s.history() if h.get("url") == up["link"]), None),
                         what="history entry with the URL")
        assert entry["url"] == up["link"] and entry["type"] == "Image"
        assert entry["host"] == "Custom image uploader", entry  # ShareX's name for this destination
        # ...and the History window shows it
        await call("click", text="History…", window="ShareL", restore_focus=False)
        hist = s.wait_window("History")
        text, data, _i, _e = await call("find", name=up["link"], window=hist["title"], limit=3)
        assert found(data, up["link"]), text[:600]
        await call("screenshot", window=hist["title"], max_width=1000)
