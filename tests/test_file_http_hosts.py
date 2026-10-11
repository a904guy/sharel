"""Immich, Seafile, Pomf, Uguu (responses) and Plik, transfer.sh (local HTTP servers)."""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import responses

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services.file_immich import ImmichSettings, ImmichUploader
from sharel.uploaders.services.file_plik import PlikSettings, PlikUploader, ttl_seconds
from sharel.uploaders.services.file_pomf import PomfSettings, PomfUploader
from sharel.uploaders.services.file_seafile import SeafileSettings, SeafileUploader
from sharel.uploaders.services.file_transfersh import TransfershSettings, TransfershUploader
from sharel.uploaders.services.file_uguu import UguuUploader


def _body(req) -> bytes:
    body = req.body
    return body.read() if hasattr(body, "read") else (body.encode() if isinstance(body, str) else body)


def _form(content_type: str, raw: bytes) -> dict:
    """multipart/form-data -> {name: (filename, content_type, bytes)}"""
    msg = BytesParser(policy=email_policy).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + raw)
    out = {}
    for part in msg.iter_parts():
        out[part.get_param("name", header="content-disposition")] = (
            part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
    return out


# ---- Immich --------------------------------------------------------------------------------------

IMMICH = "https://photos.example.com"
ASSET = "0b5c3f5e-1111-4abc-8def-1234567890ab"
ALBUM = "9f1c3f5e-2222-4abc-8def-1234567890ab"


@responses.activate
def test_immich_v3_upload_album_and_shared_link():
    responses.get(f"{IMMICH}/api/server/version", json={"major": 3, "minor": 3, "patch": 1})
    responses.post(f"{IMMICH}/api/assets", json={"id": ASSET, "status": "created"}, status=201)
    responses.get(f"{IMMICH}/api/albums", json=[{"id": ALBUM, "albumName": "Screenshots"}])
    responses.put(f"{IMMICH}/api/albums/{ALBUM}/assets", json=[{"id": ASSET, "success": True}])
    responses.post(f"{IMMICH}/api/shared-links", json={"key": "SHAREKEY", "id": "l1"}, status=201)
    st = ImmichSettings(url=IMMICH + "/api", api_key="KEY", album="Screenshots", shared_link_expire_days=2,
                        shared_link_password="pw")
    res = ImmichUploader(st, UploadContext()).upload(io.BytesIO(b"PNG"), "s.png")
    up = responses.calls[1].request
    assert up.headers["x-api-key"] == "KEY"
    form = _form(up.headers["Content-Type"], _body(up))
    assert form["assetData"] == ("s.png", "image/png", b"PNG")
    assert form["filename"][2] == b"s.png" and form["isFavorite"][2] == b"false"
    assert form["fileCreatedAt"][2].endswith(b"Z") and "deviceAssetId" not in form
    assert json.loads(responses.calls[3].request.body) == {"ids": [ASSET]}
    link = json.loads(responses.calls[4].request.body)
    assert link["type"] == "INDIVIDUAL" and link["assetIds"] == [ASSET] and link["allowUpload"] is False
    assert link["showMetadata"] is False and link["password"] == "pw" and link["expiresAt"].endswith("Z")
    assert res.url == f"{IMMICH}/share/SHAREKEY"


@responses.activate
def test_immich_v2_device_fields_new_album_no_link():
    responses.get(f"{IMMICH}/api/server/version", json={"major": 2, "minor": 7, "patch": 5})
    responses.post(f"{IMMICH}/api/assets", json={"id": ASSET, "status": "duplicate"}, status=200)
    responses.get(f"{IMMICH}/api/albums", json=[])
    responses.post(f"{IMMICH}/api/albums", json={"id": ALBUM}, status=201)
    st = ImmichSettings(url=IMMICH, api_key="KEY", album="New", auto_create_shareable_link=False)
    res = ImmichUploader(st, UploadContext()).upload(io.BytesIO(b"x"), "v.mp4")
    form = _form(responses.calls[1].request.headers["Content-Type"], _body(responses.calls[1].request))
    assert form["deviceId"][2] == b"ShareL" and form["deviceAssetId"][2].startswith(b"ShareL-")
    assert json.loads(responses.calls[3].request.body) == {"albumName": "New", "assetIds": [ASSET]}
    assert res.url == f"{IMMICH}/photos/{ASSET}"


@responses.activate
def test_immich_rejected_key():
    responses.get(f"{IMMICH}/api/server/version", status=401, json={"message": "Invalid API key"})
    responses.post(f"{IMMICH}/api/assets", status=401, json={"message": "Invalid API key"})
    with pytest.raises(UploaderError, match="401"):
        ImmichUploader(ImmichSettings(url=IMMICH, api_key="bad"), UploadContext()).upload(io.BytesIO(b"x"), "a.png")


# ---- Seafile -------------------------------------------------------------------------------------

SEA = "https://sea.example.com"


@responses.activate
def test_seafile_login_upload_share_raw():
    responses.post(f"{SEA}/api2/auth-token/", json={"token": "TOK"})
    responses.get(f"{SEA}/api2/default-repo/", json={"repo_id": "R1", "exists": True})
    responses.get(f"{SEA}/api2/repos/R1/upload-link/", body='"https://sea.example.com/seafhttp/upload-api/abc"')
    responses.post("https://sea.example.com/seafhttp/upload-api/abc",
                   json=[{"name": "shot (1).png", "id": "x", "size": 3}])
    responses.post(f"{SEA}/api/v2.1/share-links/", json={"link": f"{SEA}/f/abcdef/", "token": "abcdef"})
    saved = []
    st = SeafileSettings(api_url=f"{SEA}/api2/", username="me@x", password="pw", path="/ShareL",
                         create_shareable_url_raw=True, share_days_to_expire=5, share_password="spw")
    res = SeafileUploader(st, UploadContext(save_settings=lambda: saved.append(1))).upload(
        io.BytesIO(b"png"), "shot.png")
    assert parse_qs(responses.calls[0].request.body) == {"username": ["me@x"], "password": ["pw"]}
    assert st.auth_token == "TOK" and saved
    assert responses.calls[1].request.headers["Authorization"] == "Token TOK"
    assert parse_qs(urlparse(responses.calls[2].request.url).query) == {"p": ["/ShareL/"]}
    up = responses.calls[3].request
    assert urlparse(up.url).query == "ret-json=1"
    form = _form(up.headers["Content-Type"], _body(up))
    assert form["file"] == ("shot.png", "image/png", b"png") and form["parent_dir"][2] == b"/ShareL/"
    share = json.loads(responses.calls[4].request.body)
    assert share == {"repo_id": "R1", "path": "/ShareL/shot (1).png", "password": "spw", "expire_days": 5}
    assert res.url == f"{SEA}/f/abcdef/?raw=1"


@responses.activate
def test_seafile_encrypted_library_no_share():
    responses.post(f"{SEA}/api2/repos/R2/", body='"success"')
    responses.get(f"{SEA}/api2/repos/R2/upload-link/", body='"https://sea.example.com/up/1"')
    responses.post("https://sea.example.com/up/1", json=[{"name": "a.txt"}])
    st = SeafileSettings(api_url=f"{SEA}/api2", auth_token="T", repo_id="R2", is_library_encrypted=True,
                         encrypted_library_password="libpw")
    res = SeafileUploader(st, UploadContext()).upload(io.BytesIO(b"a"), "a.txt")
    assert parse_qs(responses.calls[0].request.body) == {"password": ["libpw"]}
    assert res.is_success and res.url == "" and res.is_url_expected is False and len(responses.calls) == 3


# ---- Pomf / Uguu ---------------------------------------------------------------------------------

@responses.activate
def test_pomf_default_instance_and_relative_url():
    responses.post("https://pomf.lain.la/upload.php", json={"success": True, "files": [
        {"hash": "h", "name": "a.png", "url": "https://pomf2.lain.la/f/abc.png", "size": 3}]})
    res = PomfUploader(PomfSettings(), UploadContext()).upload(io.BytesIO(b"png"), "a.png")
    req = responses.calls[0].request
    assert _form(req.headers["Content-Type"], _body(req))["files[]"] == ("a.png", "image/png", b"png")
    assert res.url == "https://pomf2.lain.la/f/abc.png"
    responses.post("https://my.pomf/upload.php", json={"success": True, "files": [{"url": "xyz.png"}]})
    res = PomfUploader(PomfSettings(upload_url="https://my.pomf/upload.php", result_url="files.my.pomf"),
                       UploadContext()).upload(io.BytesIO(b"p"), "b.png")
    assert res.url == "https://files.my.pomf/xyz.png"


@responses.activate
def test_pomf_error_and_uguu():
    responses.post("https://pomf.lain.la/upload.php", status=413,
                   json={"success": False, "errorcode": 413, "description": "File too large"})
    with pytest.raises(UploaderError, match="File too large"):
        PomfUploader(PomfSettings(), UploadContext()).upload(io.BytesIO(b"x"), "x.bin")
    responses.post("https://uguu.se/upload", json={"success": True, "files": [
        {"hash": "h", "filename": "q.png", "url": "https://h.uguu.se/QwErTy.png", "size": 1}]})
    res = UguuUploader(None, UploadContext()).upload(io.BytesIO(b"q"), "q.png")
    assert res.url == "https://h.uguu.se/QwErTy.png"
    assert registry.get("file", "Uguu").settings_cls is None


# ---- local HTTP servers: Plik and transfer.sh ----------------------------------------------------

@contextlib.contextmanager
def http_stub(handler_fn):
    seen: list[dict] = []

    class H(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            req = {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
            seen.append(req)
            status, headers, out = handler_fn(req, f"http://127.0.0.1:{self.server.server_address[1]}")
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = do_PUT = _handle

        def log_message(self, *a):
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()


def _plik(req, base):
    if req["method"] == "POST" and req["path"] == "/upload":
        if req["headers"].get("X-PlikToken") != "acct-token":
            return 403, {}, b'{"message":"invalid token"}'
        return 200, {"Content-Type": "application/json"}, json.dumps(
            {"id": "UPL1", "uploadToken": "UT", "files": []}).encode()
    if req["method"] == "POST" and req["path"] == "/file/UPL1":
        if req["headers"].get("X-UploadToken") != "UT":
            return 401, {}, b"{}"
        form = _form(req["headers"]["Content-Type"], req["body"])
        name = form["file"][0]
        return 200, {"Content-Type": "application/json"}, json.dumps(
            {"id": "F1", "fileName": name, "status": "uploaded"}).encode()
    return 404, {}, b"{}"


def test_plik_create_upload_then_file():
    with http_stub(_plik) as (base, seen):
        st = PlikSettings(url=base, api_key="acct-token", one_shot=True, removable=True, ttl_unit="Hours", ttl=2,
                          has_comment=True, comment="hi", is_secured=True, login="u", password="p")
        res = PlikUploader(st, UploadContext()).upload(io.BytesIO(b"data"), "a b.txt")
    opts = json.loads(seen[0]["body"])
    assert opts == {"ttl": 7200, "removable": True, "oneShot": True, "comments": "hi", "login": "u", "password": "p"}
    form = _form(seen[1]["headers"]["Content-Type"], seen[1]["body"])
    assert form["file"] == ("a b.txt", "text/plain", b"data")
    assert res.url == f"{base}/file/UPL1/F1/a%20b.txt"


def test_plik_ttl_and_errors():
    assert ttl_seconds(PlikSettings(ttl_unit="Never")) == -1
    assert ttl_seconds(PlikSettings()) == 30 * 60 and ttl_seconds(PlikSettings(ttl_unit="Days", ttl=1)) == 86400
    with http_stub(_plik) as (base, _seen), pytest.raises(UploaderError, match="403"):
        PlikUploader(PlikSettings(url=base, api_key="wrong"), UploadContext()).upload(io.BytesIO(b"x"), "x")


def _transfer(req, base):
    if req["method"] != "PUT":
        return 405, {}, b""
    name = unquote(req["path"].lstrip("/"))
    url = f"{base}/Ab12/{req['path'].lstrip('/')}"
    return 200, {"X-Url-Delete": f"{url}/DeLeTe", "Content-Type": "text/plain"}, (url + "\n").encode() \
        if name else b"no name"


def test_transfersh_put_headers_and_delete_url():
    with http_stub(_transfer) as (base, seen):
        st = TransfershSettings(url=base, max_days=3, max_downloads=1, encrypt_password="enc")
        progress = []
        res = TransfershUploader(st, UploadContext(progress=lambda d, t: progress.append((d, t)))).upload(
            io.BytesIO(b"hello"), "my file.txt")
    req = seen[0]
    assert req["method"] == "PUT" and req["path"] == "/my%20file.txt" and req["body"] == b"hello"
    # progress-wrapped bodies go out with Content-Length, not chunked (ProgressReader.tell)
    assert req["headers"]["Content-Length"] == "5" and "Transfer-Encoding" not in req["headers"]
    assert req["headers"]["Max-Days"] == "3" and req["headers"]["Max-Downloads"] == "1"
    assert req["headers"]["X-Encrypt-Password"] == "enc"
    assert res.url == f"{base}/Ab12/my%20file.txt" and res.deletion_url == f"{base}/Ab12/my%20file.txt/DeLeTe"
    assert progress[-1] == (5, 5)
