"""Hostr (Localhostr), img.fish, s-ul, LobFile (Lithiio), Streamable: request shape and URL parsing."""
import base64
import hashlib
import io
import json

import pytest
import responses
from _api_helpers import multipart

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services import api_lithiio, api_streamable


def make(name, ctx=None, **kw):
    info = registry.get("file", name)
    return info.cls(info.settings_cls(**kw), ctx or UploadContext())


def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# ---- Hostr ----

HOSTR_IMAGE = {"added": "2026-10-11T22:00:00.000Z", "downloads": 0, "href": "https://hostr.co/aBcD3fG",
               "id": "aBcD3fG", "name": "shot one.png", "size": 1234, "type": "image", "status": "active",
               "width": 800, "height": 600,
               "direct": {"150x": "https://hostr.co/file/150/aBcD3fG/shot one.png",
                          "970x": "https://hostr.co/file/970/aBcD3fG/shot one.png"}}


@responses.activate(assert_all_requests_are_fired=True)
def test_localhostr_direct_url_for_images():
    responses.post("https://hostr.co/api/file", json=HOSTR_IMAGE, status=201)
    res = make("Localhostr", email="me@x.io", password="pw").upload(io.BytesIO(b"IMG"), "shot one.png")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == basic("me@x.io", "pw")
    assert multipart(req)["file"] == ("shot one.png", b"IMG", "image/png")
    assert res.url == "https://hostr.co/file/aBcD3fG/shot%20one.png"
    assert res.thumbnail_url == HOSTR_IMAGE["direct"]["150x"]


@responses.activate
def test_localhostr_page_url_for_non_images_or_when_off():
    doc = {"href": "https://hostr.co/ZZ9", "id": "ZZ9", "name": "a.zip", "size": 1, "status": "active"}
    responses.post("https://hostr.co/api/file", json=doc, status=201)
    assert make("Localhostr", email="e", password="p").upload(io.BytesIO(b"z"), "a.zip").url == doc["href"]
    responses.replace(responses.POST, "https://hostr.co/api/file", json=HOSTR_IMAGE, status=201)
    res = make("Localhostr", email="e", password="p", direct_url=False).upload(io.BytesIO(b"z"), "s.png")
    assert res.url == "https://hostr.co/aBcD3fG"


@responses.activate
def test_localhostr_auth_failure():
    responses.post("https://hostr.co/api/file", json={"error": {"message": "Incorrect username or password"}},
                   status=401)
    with pytest.raises(UploaderError, match="Incorrect username"):
        make("Localhostr", email="e", password="bad").upload(io.BytesIO(b"z"), "a.zip")


# ---- img.fish ----

@responses.activate(assert_all_requests_are_fired=True)
def test_imgfish_upload_fields_headers_and_links():
    responses.post("https://img.fish/up", json={"link": "https://img.fish/f/Ab3dEf9h.png",
                                                "destroy": "https://img.fish/destroy/" + "t" * 48})
    res = make("ImgFish", api_key="FISHKEY", file_id_length=12).upload(io.BytesIO(b"P"), "a.png")
    req = responses.calls[0].request
    assert req.headers["Accept"] == "application/json" and req.headers["x-api-key"] == "FISHKEY"
    parts = multipart(req)
    assert parts["length"][1] == b"12" and parts["file"] == ("a.png", b"P", "image/png")
    assert res.url == "https://img.fish/f/Ab3dEf9h.png" and res.deletion_url.endswith("t" * 48)


@responses.activate
def test_imgfish_anonymous_and_error():
    responses.post("https://img.fish/up", json={"error": "Unsupported file type"}, status=400)
    up = make("ImgFish")
    assert up.validate() == []
    with pytest.raises(UploaderError, match="Unsupported file type"):
        up.upload(io.BytesIO(b"x"), "a.exe")
    assert "x-api-key" not in responses.calls[0].request.headers
    assert make("ImgFish", file_id_length=40).validate()


# ---- s-ul ----

@responses.activate(assert_all_requests_are_fired=True)
def test_sul_upload_and_url_building():
    responses.post("https://s-ul.eu/api/v1/upload", json={
        "domain": "myname.s-ul.eu", "filename": "aB3dE", "extension": ".png", "protocol": "https://",
        "url": "https://myname.s-ul.eu/aB3dE.png"})
    res = make("Sul", api_key="SULKEY").upload(io.BytesIO(b"P"), "a.png")
    parts = multipart(responses.calls[0].request)
    assert parts["wizard"][1] == b"true" and parts["key"][1] == b"SULKEY"
    assert parts["client"][1] == b"sharex-native" and parts["file"][0] == "a.png"
    assert res.url == "https://myname.s-ul.eu/aB3dE.png"
    assert res.deletion_url == "https://s-ul.eu/delete.php?key=SULKEY&file=aB3dE"


@responses.activate
def test_sul_error():
    responses.post("https://s-ul.eu/api/v1/upload", json={"success": False,
                                                          "reason": "API key error. Error code: 106"}, status=403)
    with pytest.raises(UploaderError, match="API key error"):
        make("Sul", api_key="bad").upload(io.BytesIO(b"P"), "a.png")


# ---- LobFile (Lithiio) ----

@responses.activate(assert_all_requests_are_fired=True)
def test_lithiio_upload_with_key_header_and_sha256():
    responses.post("https://lobfile.com/api/v3/upload",
                   json={"success": True, "url": "https://lobfile.com/image/AbCdE.png"})
    data = b"some image bytes"
    res = make("Lithiio", user_api_key="LOBKEY").upload(io.BytesIO(data), "x.png")
    req = responses.calls[0].request
    assert req.headers["X-API-Key"] == "LOBKEY"
    parts = multipart(req)
    assert parts["sha_256"][1].decode() == hashlib.sha256(data).hexdigest()
    assert parts["file"] == ("x.png", data, "image/png")
    assert res.url == "https://lobfile.com/image/AbCdE.png"


@responses.activate
def test_lithiio_error_and_fetch_key():
    responses.post("https://lobfile.com/api/v3/upload", json={"success": False, "error": "Invalid API key"},
                   status=401)
    with pytest.raises(UploaderError, match="Invalid API key"):
        make("Lithiio", user_api_key="bad").upload(io.BytesIO(b"x"), "x.png")
    responses.post("https://lobfile.com/api/v3/fetch-api-key", json={"success": True, "api_key": "NEWKEY"})
    s = registry.get("file", "Lithiio").settings_cls(email="a@b.c", password="pw")
    assert api_lithiio.fetch_api_key(s, UploadContext()) == "NEWKEY" and s.user_api_key == "NEWKEY"
    sent = responses.calls[1].request
    assert b'name="email"' in sent.body and b"a@b.c" in sent.body and b'name="password"' in sent.body


# ---- Streamable ----

def _video(status, percent=0, **kw):
    doc = {"status": status, "percent": percent, "url": "streamable.com/x1y2z", "message": None,
           "thumbnail_url": "//cdn-cf-east.streamable.com/image/x1y2z.jpg", "title": "clip",
           "files": {"mp4": {"status": 2, "url": "//cdn-cf-east.streamable.com/video/mp4/x1y2z.mp4?Expires=1",
                             "width": 1280, "height": 720}}}
    doc.update(kw)
    return doc


@responses.activate(assert_all_requests_are_fired=True)
def test_streamable_upload_polls_until_ready(monkeypatch):
    sleeps = []
    monkeypatch.setattr(api_streamable, "_sleep", sleeps.append)
    responses.post("https://api.streamable.com/upload", json={"shortcode": "x1y2z", "status": 1})
    responses.get("https://api.streamable.com/videos/x1y2z", json=_video(1, 40))
    responses.get("https://api.streamable.com/videos/x1y2z", json=_video(2, 100))
    progress = []
    up = make("Streamable", ctx=UploadContext(progress=lambda d, t: progress.append((d, t))),
              username="me@x.io", password="pw")
    res = up.upload(io.BytesIO(b"MP4DATA"), "clip.mp4")
    up_req = responses.calls[0].request
    assert up_req.headers["Authorization"] == basic("me@x.io", "pw")
    assert multipart(up_req)["file"] == ("clip.mp4", b"MP4DATA", "video/mp4")
    assert responses.calls[1].request.headers["Authorization"] == basic("me@x.io", "pw")
    assert res.url == "https://streamable.com/x1y2z"
    assert res.thumbnail_url == "https://cdn-cf-east.streamable.com/image/x1y2z.jpg"
    assert sleeps == [1.0] and (40, 100) in progress and progress[-1] == (100, 100)


@responses.activate
def test_streamable_direct_url_and_processing_error(monkeypatch):
    monkeypatch.setattr(api_streamable, "_sleep", lambda s: None)
    responses.post("https://api.streamable.com/upload", json={"shortcode": "x1y2z", "status": 1})
    responses.get("https://api.streamable.com/videos/x1y2z", json=_video(2, 100))
    res = make("Streamable", username="u", password="p", use_direct_url=True).upload(io.BytesIO(b"v"), "a.mp4")
    assert res.url == "https://cdn-cf-east.streamable.com/video/mp4/x1y2z.mp4?Expires=1"
    responses.replace(responses.GET, "https://api.streamable.com/videos/x1y2z",
                      json=_video(3, 0, message="Video could not be processed"))
    with pytest.raises(UploaderError, match="could not be processed"):
        make("Streamable", username="u", password="p").upload(io.BytesIO(b"v"), "a.mp4")


@responses.activate
def test_streamable_anonymous_is_rejected_cleanly():
    responses.post("https://api.streamable.com/upload", body="Unauthorized", status=401)
    up = make("Streamable")
    assert up.validate()
    with pytest.raises(UploaderError, match="login required"):
        up.upload(io.BytesIO(b"v"), "a.mp4")
    assert "Authorization" not in responses.calls[0].request.headers
    assert json.loads(json.dumps(_video(2)))["url"] == "streamable.com/x1y2z"


def test_defunct_file_hosts_registered_with_evidence():
    for name in ("Lambda", "SendSpace"):
        info = registry.get("file", name)
        assert info is not None and not info.available
        assert info.defunct.startswith("https://") and info.defunct_reason
