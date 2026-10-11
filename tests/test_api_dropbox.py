import io
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses
from _api_helpers import body_bytes

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import api_dropbox

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
SCL = "https://www.dropbox.com/scl/fi/abc123xyz/shot.png?rlkey=k3y9&dl=0"


def make(ctx=None, **kw):
    info = registry.get("file", "Dropbox")
    kw.setdefault("oauth2", OAuth2Info(access_token="AT", refresh_token="RT", expires_at=time.time() + 3600))
    return info.cls(info.settings_cls(client_id="appkey", **kw), ctx or UploadContext())


def _meta(path="/ShareL/shot.png"):
    return {".tag": "file", "name": path.rsplit("/", 1)[1], "id": "id:a4ayc_80_OEAAAAAAAAAXw",
            "path_lower": path.lower(), "path_display": path, "rev": "a1c10ce0dd78", "size": 7}


@responses.activate(assert_all_requests_are_fired=True)
def test_dropbox_upload_header_arg_and_shared_link():
    responses.post(f"{CONTENT}/files/upload", json=_meta())
    responses.post(f"{API}/sharing/create_shared_link_with_settings", json={".tag": "file", "url": SCL})
    res = make(upload_path="\\ShareL\\").upload(io.BytesIO(b"PNGDATA"), "shot.png")
    up = responses.calls[0].request
    assert up.headers["Authorization"] == "Bearer AT"
    assert up.headers["Content-Type"] == "application/octet-stream" and body_bytes(up) == b"PNGDATA"
    assert json.loads(up.headers["Dropbox-API-Arg"]) == {"path": "/ShareL/shot.png", "mode": "overwrite",
                                                         "autorename": False, "mute": True}
    link = json.loads(responses.calls[1].request.body)
    assert link == {"path": "/ShareL/shot.png", "settings": {"requested_visibility": "public"}}
    assert res.url == SCL


@responses.activate
def test_dropbox_path_pattern_non_ascii_name_and_no_link():
    responses.post(f"{CONTENT}/files/upload", json=_meta("/ShareL/2026/x.png"))
    res = make(auto_create_shareable_link=False).upload(io.BytesIO(b"x"), "café ✓.png")
    arg = responses.calls[0].request.headers["Dropbox-API-Arg"]
    assert arg.isascii() and json.loads(arg)["path"].startswith("/ShareL/20")
    assert json.loads(arg)["path"].endswith("/café ✓.png")
    assert res.is_success and not res.url and res.is_url_expected is False
    assert api_dropbox.verify_path("", "a.png") == "/a.png"


@responses.activate(assert_all_requests_are_fired=True)
def test_dropbox_existing_link_from_409_and_direct_conversion():
    responses.post(f"{CONTENT}/files/upload", json=_meta())
    responses.post(f"{API}/sharing/create_shared_link_with_settings", status=409, json={
        "error_summary": "shared_link_already_exists/metadata/..",
        "error": {".tag": "shared_link_already_exists",
                  "shared_link_already_exists": {".tag": "metadata", "metadata": {".tag": "file", "url": SCL}}}})
    res = make(use_direct_link=True).upload(io.BytesIO(b"x"), "shot.png")
    assert res.url == "https://dl.dropboxusercontent.com/scl/fi/abc123xyz/shot.png?rlkey=k3y9&dl=0"


@responses.activate(assert_all_requests_are_fired=True)
def test_dropbox_existing_link_via_list_shared_links():
    responses.post(f"{CONTENT}/files/upload", json=_meta())
    responses.post(f"{API}/sharing/create_shared_link_with_settings", status=409,
                   json={"error_summary": "shared_link_already_exists/..", "error": {
                       ".tag": "shared_link_already_exists"}})
    responses.post(f"{API}/sharing/list_shared_links", json={"links": [
        {".tag": "file", "url": "https://www.dropbox.com/s/legacy123/shot.png?dl=0"}], "has_more": False})
    res = make(use_direct_link=True).upload(io.BytesIO(b"x"), "shot.png")
    assert json.loads(responses.calls[2].request.body) == {"path": "/ShareL/shot.png", "direct_only": True}
    assert res.url == "https://www.dropbox.com/s/legacy123/shot.png?raw=1"


@responses.activate(assert_all_requests_are_fired=True)
def test_dropbox_large_file_upload_session_chunks():
    data = b"0123456789" * 3 + b"xy"  # 32 bytes, chunks of 10
    responses.post(f"{CONTENT}/files/upload_session/start", json={"session_id": "SID1"})
    responses.post(f"{CONTENT}/files/upload_session/append_v2", body="null")
    responses.post(f"{CONTENT}/files/upload_session/append_v2", body="null")
    responses.post(f"{CONTENT}/files/upload_session/finish", json=_meta("/ShareL/big.bin"))
    responses.post(f"{API}/sharing/create_shared_link_with_settings", json={"url": SCL})
    up = make(upload_path="ShareL")
    up.simple_upload_limit, up.session_chunk_size = 16, 10
    up.upload(io.BytesIO(data), "big.bin")
    reqs = [c.request for c in responses.calls[:4]]
    args = [json.loads(r.headers["Dropbox-API-Arg"]) for r in reqs]
    assert args[0] == {"close": False}
    assert args[1] == {"cursor": {"session_id": "SID1", "offset": 10}, "close": False}
    assert args[2]["cursor"] == {"session_id": "SID1", "offset": 20}
    assert args[3] == {"cursor": {"session_id": "SID1", "offset": 30},
                       "commit": {"path": "/ShareL/big.bin", "mode": "overwrite", "autorename": False, "mute": True}}
    assert b"".join(body_bytes(r) for r in reqs) == data


@responses.activate
def test_dropbox_api_error_message():
    responses.post(f"{CONTENT}/files/upload", status=409,
                   json={"error_summary": "path/insufficient_space/..", "error": {".tag": "path"}})
    with pytest.raises(UploaderError, match="insufficient_space"):
        make().upload(io.BytesIO(b"x"), "a.png")


def test_dropbox_authorize_uses_fixed_registered_port_and_offline_token():
    seen = {}

    def browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        threading.Thread(target=lambda: requests.get(q["redirect_uri"], params={"code": "C", "state": q["state"]},
                                                     timeout=5)).start()

    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://api.dropboxapi.com/oauth2/token", json={"access_token": "A", "refresh_token": "R",
                                                                 "expires_in": 14400, "token_type": "bearer"})
        info = registry.get("file", "Dropbox")
        s = info.settings_cls(client_id="appkey", redirect_port=0)
        s.redirect_port = _free_port()
        saved = []
        api_dropbox.authorize(s, UploadContext(open_url=browser, save_settings=lambda: saved.append(1)))
    assert seen["redirect_uri"] == f"http://127.0.0.1:{s.redirect_port}/"
    assert seen["token_access_type"] == "offline" and seen["code_challenge_method"] == "S256"
    assert "files.content.write" in seen["scope"].split()
    assert s.oauth2.access_token == "A" and s.oauth2.refresh_token == "R" and saved == [1]


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
