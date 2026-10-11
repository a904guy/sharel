import hashlib
import io
import json
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from _api_helpers import multipart, query

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services import api_mediafire, api_pushbullet


def make(name, ctx=None, **kw):
    info = registry.get("file", name)
    return info.cls(info.settings_cls(**kw), ctx or UploadContext())


# ---- Pushbullet ----

UPLOAD_REQ = {"file_name": "shot.png", "file_type": "image/png",
              "file_url": "https://dl2.pushbulletusercontent.com/abc123/shot.png",
              "upload_url": "https://upload.pushbullet.com/upload/abc123"}


@responses.activate(assert_all_requests_are_fired=True)
def test_pushbullet_upload_request_upload_then_push_to_device():
    responses.post("https://api.pushbullet.com/v2/upload-request", json=UPLOAD_REQ)
    responses.post("https://upload.pushbullet.com/upload/abc123", status=204)
    responses.post("https://api.pushbullet.com/v2/pushes", json={"iden": "ujpah72o0sjAoRtnM0jc", "type": "file",
                                                                 "active": True})
    res = make("Pushbullet", user_api_key="o.TOKEN", device_iden="ujpah72o0sjAsoeMFETvcy").upload(
        io.BytesIO(b"PNG"), "shot.png")
    c = responses.calls
    assert c[0].request.headers["Access-Token"] == "o.TOKEN"
    assert json.loads(c[0].request.body) == {"file_name": "shot.png", "file_type": "image/png"}
    assert "Access-Token" not in c[1].request.headers  # the upload URL is pre-signed
    assert multipart(c[1].request)["file"] == ("shot.png", b"PNG", "image/png")
    push = json.loads(c[2].request.body)
    assert push == {"type": "file", "file_name": "shot.png", "file_type": "image/png",
                    "file_url": UPLOAD_REQ["file_url"], "body": "Sent via ShareL",
                    "device_iden": "ujpah72o0sjAsoeMFETvcy"}
    assert res.url == "https://www.pushbullet.com/pushes?push_iden=ujpah72o0sjAoRtnM0jc"


@responses.activate
def test_pushbullet_all_devices_and_deprecated_s3_fields():
    req = dict(UPLOAD_REQ, data={"awsaccesskeyid": "AKIA", "acl": "public-read", "key": "abc/shot.png",
                                 "signature": "SIG", "policy": "POL", "content-type": "image/png"})
    responses.post("https://api.pushbullet.com/v2/upload-request", json=req)
    responses.post("https://upload.pushbullet.com/upload/abc123", status=204)
    responses.post("https://api.pushbullet.com/v2/pushes", json={"iden": "P1"})
    make("Pushbullet", user_api_key="T", body="").upload(io.BytesIO(b"PNG"), "shot.png")
    parts = multipart(responses.calls[1].request)
    assert parts["policy"][1] == b"POL" and parts["awsaccesskeyid"][1] == b"AKIA"
    assert list(parts)[-1] == "file"  # S3 requires the file last
    push = json.loads(responses.calls[2].request.body)
    assert "device_iden" not in push and "body" not in push


@responses.activate
def test_pushbullet_device_list_and_bad_token():
    responses.get("https://api.pushbullet.com/v2/devices", json={"devices": [
        {"iden": "d1", "nickname": "Pixel", "active": True}, {"iden": "d2", "active": True},
        {"iden": "d3", "nickname": "Old", "active": False}]})
    s = registry.get("file", "Pushbullet").settings_cls(user_api_key="T", device_iden="gone")
    devices = api_pushbullet.get_devices(s, UploadContext())
    assert devices == [{"key": "d1", "name": "Pixel"}] and s.device_iden == ""
    assert api_pushbullet.device_choices(s) == [("", "All devices"), ("d1", "Pixel")]
    assert query(responses.calls[0].request) == {"active": "true"}
    responses.post("https://api.pushbullet.com/v2/upload-request", status=401, json={
        "error": {"code": "invalid_access_token", "message": "Access token is missing or invalid."}})
    with pytest.raises(UploaderError, match="Access token is missing or invalid"):
        make("Pushbullet", user_api_key="bad").upload(io.BytesIO(b"x"), "a.txt")


# ---- MediaFire ----

def _mf(**r):
    return {"response": {"result": "Success", "current_api_version": "1.5", **r}}


def _check_signature(req, key: int, sig_time: str):
    """Recompute md5(key % 256 + time + uri) from the URL actually sent (uri = path?query sans signature)."""
    u = urlparse(req.url)
    q, _, sig = u.query.rpartition("&signature=")
    uri = f"{u.path}?{q}"
    assert sig == hashlib.md5(f"{key % 256}{sig_time}{uri}".encode()).hexdigest()


@responses.activate(assert_all_requests_are_fired=True)
def test_mediafire_session_signed_upload_and_poll(monkeypatch):
    monkeypatch.setattr(api_mediafire, "_sleep", lambda s: None)
    base = "https://www.mediafire.com/api/1.5"
    responses.post(f"{base}/user/get_session_token.php",
                   json=_mf(session_token="SESS", secret_key="9316931", time="1359061000.8125", pkey="x"))
    responses.post(f"{base}/upload/simple.php", json=_mf(doupload={"result": "0", "key": "upkey1"}, new_key="yes"))
    responses.post(f"{base}/upload/poll_upload.php", json=_mf(doupload={"result": "0", "status": "17",
                                                                        "description": "Verifying"}))
    responses.post(f"{base}/upload/poll_upload.php", json=_mf(new_key="yes", doupload={
        "result": "0", "status": "99", "description": "No more requests for this key", "quickkey": "qk123abc",
        "filename": "my shot.png", "fileerror": ""}))
    up = make("MediaFire", application_id="42", api_key="APPKEY", username="me@x.io", password="pw",
              path="ShareL/%y", use_long_link=True)
    res = up.upload(io.BytesIO(b"PNGDATA"), "my shot.png")
    c = responses.calls
    login = parse_qs(c[0].request.body)
    assert login["email"] == ["me@x.io"] and login["application_id"] == ["42"] and login["token_version"] == ["2"]
    assert login["signature"] == [hashlib.sha1(b"me@x.iopw42APPKEY").hexdigest()]
    up_q = query(c[1].request)
    assert up_q["session_token"] == "SESS" and up_q["response_format"] == "json"
    assert up_q["path"].startswith("ShareL/20") and up_q["action_on_duplicate"] == "keep"
    _check_signature(c[1].request, 9316931, "1359061000.8125")
    assert multipart(c[1].request)["Filedata"] == ("my shot.png", b"PNGDATA", "image/png")
    rolled = 9316931 * 16807 % 2147483647  # new_key=yes on the upload response
    assert query(c[2].request)["key"] == "upkey1" and query(c[2].request)["filename"] == "my shot.png"
    _check_signature(c[2].request, rolled, "1359061000.8125")
    _check_signature(c[3].request, rolled, "1359061000.8125")  # the first poll had no new_key
    assert res.url == "https://www.mediafire.com/view/qk123abc/my%20shot.png"


@responses.activate
def test_mediafire_errors():
    base = "https://www.mediafire.com/api/1.5"
    responses.post(f"{base}/user/get_session_token.php", json={"response": {
        "result": "Error", "error": 107, "message": "The Credentials you entered are invalid"}})
    with pytest.raises(UploaderError, match="Credentials you entered are invalid"):
        make("MediaFire", application_id="1", api_key="k", username="u", password="p").upload(io.BytesIO(b"x"), "a")
    assert make("MediaFire").validate()
    assert api_mediafire.next_secret_key(1) == 16807
