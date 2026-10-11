import base64
import hashlib
import io
import json
import time

import pytest
import responses
from _api_helpers import body_bytes, multipart, query

from sharel.uploaders import oauth, registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import api_box

API = "https://api.box.com/2.0"
UP = "https://upload.box.com/api/2.0"
FILE = {"type": "file", "id": "12345", "name": "shot.png", "parent": {"type": "folder", "id": "678"}}


def make(ctx=None, **kw):
    info = registry.get("file", "Box")
    kw.setdefault("oauth2", OAuth2Info(access_token="AT", refresh_token="RT", expires_at=time.time() + 3600))
    return info.cls(info.settings_cls(client_id="cid", client_secret="sec", **kw), ctx or UploadContext())


def sha(data):
    return "sha=" + base64.b64encode(hashlib.sha1(data).digest()).decode()


@responses.activate(assert_all_requests_are_fired=True)
def test_box_upload_attributes_before_file_and_open_shared_link():
    responses.post(f"{UP}/files/content", status=201, json={"total_count": 1, "entries": [FILE]})
    responses.put(f"{API}/files/12345", json={"type": "file", "id": "12345", "shared_link": {
        "url": "https://app.box.com/s/abcdef123", "access": "open"}})
    res = make(folder_id="678").upload(io.BytesIO(b"PNG"), "shot.png")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer AT"
    parts = multipart(req)
    assert list(parts) == ["attributes", "file"]
    assert json.loads(parts["attributes"][1]) == {"name": "shot.png", "parent": {"id": "678"}}
    assert parts["file"] == ("shot.png", b"PNG", "image/png")
    share = responses.calls[1].request
    assert query(share) == {"fields": "shared_link"}
    assert json.loads(share.body) == {"shared_link": {"access": "open"}}
    assert res.url == "https://app.box.com/s/abcdef123"


@responses.activate(assert_all_requests_are_fired=True)
def test_box_name_conflict_uploads_new_version_and_no_share_url():
    responses.post(f"{UP}/files/content", status=409, json={
        "type": "error", "status": 409, "code": "item_name_in_use",
        "context_info": {"conflicts": {"type": "file", "id": "999", "name": "shot.png"}}})
    responses.post(f"{UP}/files/999/content", json={"entries": [dict(FILE, id="999")]})
    res = make(share=False).upload(io.BytesIO(b"PNG"), "shot.png")
    parts = multipart(responses.calls[1].request)
    assert json.loads(parts["attributes"][1]) == {"name": "shot.png"} and parts["file"][1] == b"PNG"
    assert json.loads(multipart(responses.calls[0].request)["attributes"][1])["parent"] == {"id": "0"}
    assert res.url == "https://app.box.com/file/999"


@responses.activate(assert_all_requests_are_fired=True)
def test_box_chunked_upload_session_digests_and_commit(monkeypatch):
    monkeypatch.setattr(api_box, "_sleep", lambda s: None)
    data = b"A" * 8 + b"B" * 8 + b"C" * 3
    sess = f"{UP}/files/upload_sessions/F971964745A5CD0C001BBE4E58196BFD"
    responses.post(f"{UP}/files/upload_sessions", status=201, json={
        "id": "F971964745A5CD0C001BBE4E58196BFD", "part_size": 8, "total_parts": 3, "num_parts_processed": 0,
        "session_endpoints": {"upload_part": sess, "commit": f"{sess}/commit", "abort": sess,
                              "list_parts": f"{sess}/parts", "status": sess}})
    for i, (off, n) in enumerate([(0, 8), (8, 8), (16, 3)]):
        responses.put(sess, json={"part": {"part_id": f"P{i}", "offset": off, "size": n, "sha1": "x"}})
    responses.post(f"{sess}/commit", status=202, headers={"Retry-After": "1"})
    responses.post(f"{sess}/commit", status=201, json={"entries": [FILE]})
    responses.put(f"{API}/files/12345", json={"shared_link": {"url": "https://app.box.com/s/zz"}})
    up = make(share_access_level="company")
    up.chunked_threshold = 10
    res = up.upload(io.BytesIO(data), "big.bin")
    assert json.loads(responses.calls[0].request.body) == {"folder_id": "0", "file_size": 19, "file_name": "big.bin"}
    puts = [c.request for c in responses.calls[1:4]]
    assert [p.headers["Content-Range"] for p in puts] == ["bytes 0-7/19", "bytes 8-15/19", "bytes 16-18/19"]
    assert [p.headers["Digest"] for p in puts] == [sha(b"A" * 8), sha(b"B" * 8), sha(b"C" * 3)]
    assert b"".join(body_bytes(p) for p in puts) == data
    commit = responses.calls[5].request
    assert commit.headers["Digest"] == sha(data)
    assert [p["part_id"] for p in json.loads(commit.body)["parts"]] == ["P0", "P1", "P2"]
    assert json.loads(responses.calls[6].request.body) == {"shared_link": {"access": "company"}}
    assert res.url == "https://app.box.com/s/zz"


@responses.activate
def test_box_chunked_failure_aborts_session():
    sess = f"{UP}/files/upload_sessions/S1"
    responses.post(f"{UP}/files/upload_sessions", json={"id": "S1", "part_size": 8,
                                                        "session_endpoints": {"upload_part": sess, "abort": sess}})
    responses.put(sess, status=416, json={"code": "range_not_satisfiable", "message": "Bad range"})
    responses.delete(sess, status=204)
    up = make()
    up.chunked_threshold = 1
    with pytest.raises(UploaderError, match="Bad range"):
        up.upload(io.BytesIO(b"0123456789"), "a.bin")
    assert responses.calls[-1].request.method == "DELETE"


@responses.activate
def test_box_folder_listing_and_auth_url():
    responses.get(f"{API}/folders/0/items", json={"total_count": 2, "entries": [
        {"type": "folder", "id": "11", "name": "Screens"}, {"type": "file", "id": "12", "name": "a.png"}]})
    assert api_box.list_folders(make().settings, UploadContext()) == [{"id": "11", "name": "Screens"}]
    url = oauth.authorize_url(api_box.SPEC, "cid", "http://127.0.0.1:53683/", "st")
    assert url.startswith("https://account.box.com/api/oauth2/authorize?") and "code_challenge" not in url
