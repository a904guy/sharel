"""Google Drive and YouTube: multipart/related, resumable chunking, permissions, links, OAuth spec."""
import io
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from _api_helpers import body_bytes, multipart, query

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import _api_google, api_googledrive, api_youtube

TOKEN = OAuth2Info(access_token="AT", refresh_token="RT", expires_at=time.time() + 3600)
SESSION = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=XYZ"
KIB256 = 256 * 1024


def make(name, ctx=None, **kw):
    info = registry.get("file", name)
    kw.setdefault("oauth2", OAuth2Info(**vars(TOKEN)))
    return info.cls(info.settings_cls(client_id="cid", client_secret="sec", **kw), ctx or UploadContext())


DRIVE_FILE = {"id": "1AbCdEf", "name": "shot.png", "webViewLink": "https://drive.google.com/file/d/1AbCdEf/view?usp=drivesdk",
              "webContentLink": "https://drive.google.com/uc?id=1AbCdEf&export=download"}


@responses.activate(assert_all_requests_are_fired=True)
def test_drive_small_file_multipart_related_public_and_view_link():
    responses.post("https://www.googleapis.com/upload/drive/v3/files", json=DRIVE_FILE)
    responses.post("https://www.googleapis.com/drive/v3/files/1AbCdEf/permissions", json={"id": "anyoneWithLink"})
    res = make("GoogleDrive", use_folder=True, folder_id="FOLDER9").upload(io.BytesIO(b"PNGDATA"), "shot.png")
    up = responses.calls[0].request
    q = query(up)
    assert q["uploadType"] == "multipart" and q["supportsAllDrives"] == "true" and "webContentLink" in q["fields"]
    assert up.headers["Authorization"] == "Bearer AT"
    assert up.headers["Content-Type"].startswith("multipart/related; boundary=")
    parts = list(multipart(up).values())
    assert json.loads(parts[0][1]) == {"name": "shot.png", "parents": ["FOLDER9"]}
    assert parts[0][2] == "application/json" and parts[1][1:] == (b"PNGDATA", "image/png")
    perm = responses.calls[1].request
    assert json.loads(perm.body) == {"role": "reader", "type": "anyone", "allowFileDiscovery": False}
    assert query(perm) == {"supportsAllDrives": "true"}
    assert res.url == DRIVE_FILE["webViewLink"]


@responses.activate(assert_all_requests_are_fired=True)
def test_drive_shared_drive_private_direct_link():
    responses.post("https://www.googleapis.com/upload/drive/v3/files", json=DRIVE_FILE)
    res = make("GoogleDrive", is_public=False, direct_link=True, folder_id="IGNORED", drive_id="0ADrive").upload(
        io.BytesIO(b"x"), "shot.png")
    meta = json.loads(next(iter(multipart(responses.calls[0].request).values()))[1])
    assert meta == {"name": "shot.png", "parents": ["0ADrive"]}  # use_folder off → shared drive root
    assert res.url == "https://drive.google.com/uc?id=1AbCdEf"


@responses.activate(assert_all_requests_are_fired=True)
def test_drive_large_file_resumable_in_256k_multiples(monkeypatch):
    monkeypatch.setattr(api_googledrive, "MULTIPART_LIMIT", 10)
    data = bytes(range(256)) * (KIB256 * 2 // 256) + b"tail!"  # 2 chunks of 256 KiB + 5 bytes
    responses.post("https://www.googleapis.com/upload/drive/v3/files", status=200, headers={"Location": SESSION})
    responses.put(SESSION, status=308, headers={"Range": f"bytes=0-{KIB256 - 1}"})
    responses.put(SESSION, status=308, headers={"Range": f"bytes=0-{2 * KIB256 - 1}"})
    responses.put(SESSION, status=200, json=DRIVE_FILE)
    responses.post("https://www.googleapis.com/drive/v3/files/1AbCdEf/permissions", json={})
    progress = []
    up = make("GoogleDrive", ctx=UploadContext(progress=lambda d, t: progress.append((d, t))))
    up.chunk_size = KIB256
    res = up.upload(io.BytesIO(data), "big.bin")
    init = responses.calls[0].request
    assert query(init)["uploadType"] == "resumable"
    assert init.headers["X-Upload-Content-Length"] == str(len(data))
    assert init.headers["X-Upload-Content-Type"] == "application/octet-stream"
    assert json.loads(init.body) == {"name": "big.bin"}
    puts = [c.request for c in responses.calls[1:4]]
    ranges = [p.headers["Content-Range"] for p in puts]
    total = len(data)
    assert ranges == [f"bytes 0-{KIB256 - 1}/{total}", f"bytes {KIB256}-{2 * KIB256 - 1}/{total}",
                      f"bytes {2 * KIB256}-{total - 1}/{total}"]
    assert b"".join(body_bytes(p) for p in puts) == data
    assert all(p.headers["Authorization"] == "Bearer AT" for p in puts)
    assert progress[-1] == (total, total) and (KIB256, total) in progress
    assert res.url == DRIVE_FILE["webViewLink"]


@responses.activate
def test_resumable_resends_unacknowledged_bytes_and_recovers_from_5xx(monkeypatch):
    monkeypatch.setattr(_api_google, "_sleep", lambda s: None)
    data = b"A" * KIB256 + b"B" * 100
    responses.post("https://up.example/init", headers={"Location": "https://up.example/s"})
    responses.put("https://up.example/s", status=308, headers={"Range": "bytes=0-99"})  # only 100 bytes landed
    responses.put("https://up.example/s", status=503)
    responses.put("https://up.example/s", status=308, headers={"Range": f"bytes=0-{KIB256 - 1}"})  # status query
    responses.put("https://up.example/s", status=201, json={"id": "done"})
    resp = _api_google.resumable_upload(UploadContext(), "https://up.example/init", {}, {"name": "x"},
                                        io.BytesIO(data), len(data), "text/plain", KIB256)
    assert resp.json() == {"id": "done"}
    puts = [c.request for c in responses.calls[1:]]
    assert puts[0].headers["Content-Range"] == f"bytes 0-{KIB256 - 1}/{len(data)}"
    assert puts[1].headers["Content-Range"] == f"bytes 100-{KIB256 - 1}/{len(data)}"
    assert body_bytes(puts[1]) == b"A" * (KIB256 - 100)
    assert puts[2].headers["Content-Range"] == f"bytes */{len(data)}" and body_bytes(puts[2]) == b""
    assert puts[3].headers["Content-Range"] == f"bytes {KIB256}-{len(data) - 1}/{len(data)}"
    assert body_bytes(puts[3]) == b"B" * 100


@responses.activate
def test_resumable_client_error_is_reported():
    responses.post("https://up.example/init", headers={"Location": "https://up.example/s"})
    responses.put("https://up.example/s", status=403, json={"error": {"code": 403, "message": "quotaExceeded"}})
    with pytest.raises(UploaderError, match="quotaExceeded"):
        _api_google.resumable_upload(UploadContext(), "https://up.example/init", {}, {}, io.BytesIO(b"x"), 1,
                                     "text/plain")


@responses.activate
def test_drive_listings_paginate():
    responses.get("https://www.googleapis.com/drive/v3/files", json={"files": [{"id": "f1", "name": "A"}],
                                                                     "nextPageToken": "P2"})
    responses.get("https://www.googleapis.com/drive/v3/files", json={"files": [{"id": "f2", "name": "B"}]})
    s = make("GoogleDrive").settings
    assert [f["id"] for f in api_googledrive.list_folders(s, UploadContext(), drive_id="0AD")] == ["f1", "f2"]
    q1, q2 = query(responses.calls[0].request), query(responses.calls[1].request)
    assert "'me' in writers" not in q1["q"] and q1["corpora"] == "drive" and q1["driveId"] == "0AD"
    assert "pageToken" not in q1 and q2["pageToken"] == "P2"
    responses.get("https://www.googleapis.com/drive/v3/drives", json={"drives": [{"id": "0AD", "name": "Team"}]})
    assert api_googledrive.list_drives(s, UploadContext())[1] == {"id": "0AD", "name": "Team"}


def test_google_oauth_spec_requests_offline_access():
    url = __import__("sharel.uploaders.oauth", fromlist=["x"]).authorize_url(
        api_googledrive.SPEC, "cid", "http://127.0.0.1:5/", "st", "ch")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["code_challenge"] == "ch"
    assert "https://www.googleapis.com/auth/drive.file" in q["scope"].split()
    assert registry.get("file", "GoogleDrive").oauth is api_googledrive.SPEC
    assert "youtube.upload" in api_youtube.SPEC.scopes[0]


# ---- YouTube ----

YT_SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=YT1"


@responses.activate(assert_all_requests_are_fired=True)
def test_youtube_resumable_insert_metadata_and_short_link():
    responses.post("https://www.googleapis.com/upload/youtube/v3/videos", headers={"Location": YT_SESSION})
    responses.put(YT_SESSION, json={"kind": "youtube#video", "id": "dQw4w9WgXcQ",
                                    "snippet": {"title": "my <clip>"}, "status": {"uploadStatus": "uploaded",
                                                                                  "privacyStatus": "unlisted"}})
    res = make("YouTube", privacy_type="unlisted", description="desc", category_id="20").upload(
        io.BytesIO(b"MP4"), "my <clip>.mp4")
    init = responses.calls[0].request
    assert query(init) == {"uploadType": "resumable", "part": "snippet,status"}
    assert init.headers["X-Upload-Content-Type"] == "video/mp4" and init.headers["X-Upload-Content-Length"] == "3"
    assert json.loads(init.body) == {"snippet": {"title": "my clip", "description": "desc", "categoryId": "20"},
                                     "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}}
    assert responses.calls[1].request.headers["Content-Range"] == "bytes 0-2/3"
    assert res.url == "https://youtu.be/dQw4w9WgXcQ"


@responses.activate
def test_youtube_dialog_long_link_and_rejection():
    responses.post("https://www.googleapis.com/upload/youtube/v3/videos", headers={"Location": YT_SESSION})
    responses.put(YT_SESSION, json={"id": "vid1", "status": {"uploadStatus": "uploaded"}})
    answers = iter(["Custom title", "Long description"])
    ctx = UploadContext(prompt_input=lambda title, default: next(answers),
                        prompt_select=lambda title, options: "private")
    res = make("YouTube", ctx=ctx, show_dialog=True, use_shortened_link=False).upload(io.BytesIO(b"v"), "a.mkv")
    meta = json.loads(responses.calls[0].request.body)
    assert meta["snippet"]["title"] == "Custom title" and meta["snippet"]["description"] == "Long description"
    assert meta["status"]["privacyStatus"] == "private"
    assert res.url == "https://www.youtube.com/watch?v=vid1"
    responses.replace(responses.PUT, YT_SESSION, json={"id": "vid2", "status": {
        "uploadStatus": "rejected", "rejectionReason": "duplicate"}})
    with pytest.raises(UploaderError, match="rejected: duplicate"):
        make("YouTube").upload(io.BytesIO(b"v"), "a.mp4")


@responses.activate
def test_expired_google_token_is_refreshed_and_saved():
    responses.post("https://oauth2.googleapis.com/token", json={"access_token": "NEWAT", "expires_in": 3599})
    responses.post("https://www.googleapis.com/upload/youtube/v3/videos", headers={"Location": YT_SESSION})
    responses.put(YT_SESSION, json={"id": "v", "status": {"uploadStatus": "uploaded"}})
    saved = []
    up = make("YouTube", ctx=UploadContext(save_settings=lambda: saved.append(1)),
              oauth2=OAuth2Info(access_token="OLD", refresh_token="RT", expires_at=time.time() - 10))
    up.upload(io.BytesIO(b"v"), "a.mp4")
    tok = parse_qs(responses.calls[0].request.body)
    assert tok["grant_type"] == ["refresh_token"] and tok["client_secret"] == ["sec"]
    assert responses.calls[1].request.headers["Authorization"] == "Bearer NEWAT" and saved
