"""Image destinations: request shape and response parsing against mocked APIs (nothing leaves the machine)."""
import io
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from imgtext_helpers import multipart, query
from sharel.core.config import TaskSettings
from sharel.core.enums import EDataType, FileDestination, ImageDestination, TextDestination
from sharel.uploaders import dispatch, oauth1, registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import img_chevereto, img_flickr, img_imageshack, img_imgur, img_vgyme

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def ctx(**kw):
    return UploadContext(**kw)


# ---- Imgur -------------------------------------------------------------------------------------

IMGUR_OK = {"data": {"id": "aBcD123", "deletehash": "DelHash99", "link": "https://i.imgur.com/aBcD123.png",
                     "type": "image/png", "gifv": ""}, "success": True, "status": 200}


@responses.activate
def test_imgur_anonymous_upload_direct_link():
    responses.post(img_imgur.UPLOAD_URL, json=IMGUR_OK)
    s = img_imgur.ImgurSettings(client_id="CID123", thumbnail_type="Medium_Thumbnail")
    up = img_imgur.ImgurUploader(s, ctx())
    assert up.validate() == []
    res = up.upload(io.BytesIO(PNG), "shot.png")
    req = responses.calls[0].request
    assert req.method == "POST" and req.url == "https://api.imgur.com/3/upload"
    assert req.headers["Authorization"] == "Client-ID CID123"
    parts = multipart(req)
    assert parts["image"] == ("shot.png", PNG) and "album" not in parts
    assert res.url == "https://i.imgur.com/aBcD123.png"
    assert res.thumbnail_url == "https://i.imgur.com/aBcD123m.jpg"
    assert res.deletion_url == "https://imgur.com/delete/DelHash99"


@responses.activate
def test_imgur_account_upload_to_album_page_link_and_video_field():
    responses.post(img_imgur.UPLOAD_URL, json={**IMGUR_OK, "data": {**IMGUR_OK["data"],
                                                                   "link": "https://i.imgur.com/aBcD123.mp4."}})
    s = img_imgur.ImgurSettings(account_type="User", client_id="CID", client_secret="SEC",
                                oauth2=OAuth2Info(access_token="ACCESS"), upload_selected_album=True,
                                selected_album_id="Alb1", direct_link=False)
    res = img_imgur.ImgurUploader(s, ctx()).upload(io.BytesIO(b"vid"), "clip.mp4")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer ACCESS"
    parts = multipart(req)
    assert parts["video"] == ("clip.mp4", b"vid") and parts["album"] == (None, b"Alb1")
    assert res.url == "https://imgur.com/aBcD123"


@responses.activate
def test_imgur_direct_link_strips_trailing_dot_and_gifv():
    data = {**IMGUR_OK["data"], "link": "https://i.imgur.com/x.webm.", "gifv": "https://i.imgur.com/x.gifv"}
    responses.post(img_imgur.UPLOAD_URL, json={**IMGUR_OK, "data": data})
    responses.post(img_imgur.UPLOAD_URL, json={**IMGUR_OK, "data": data})
    assert img_imgur.ImgurUploader(img_imgur.ImgurSettings(client_id="C"), ctx()).upload(
        io.BytesIO(b"w"), "a.webm").url == "https://i.imgur.com/x.webm"
    assert img_imgur.ImgurUploader(img_imgur.ImgurSettings(client_id="C", use_gifv=True), ctx()).upload(
        io.BytesIO(b"w"), "a.gif").url == "https://i.imgur.com/x.gifv"


@responses.activate
def test_imgur_refreshes_invalid_token_and_retries():
    responses.post(img_imgur.UPLOAD_URL, status=403, json={"data": {"error": "The access token provided is invalid.",
                                                                    "request": "/3/upload", "method": "POST"},
                                                           "success": False, "status": 403})
    responses.post("https://api.imgur.com/oauth2/token", json={"access_token": "NEW", "refresh_token": "R2",
                                                               "expires_in": 3600})
    responses.post(img_imgur.UPLOAD_URL, json=IMGUR_OK)
    saved = []
    s = img_imgur.ImgurSettings(account_type="User", client_id="CID", client_secret="SEC",
                                oauth2=OAuth2Info(access_token="OLD", refresh_token="R1"))
    res = img_imgur.ImgurUploader(s, ctx(save_settings=lambda: saved.append(1))).upload(io.BytesIO(PNG), "a.png")
    assert res.url == "https://i.imgur.com/aBcD123.png"
    token_body = parse_qs(responses.calls[1].request.body)
    assert token_body["grant_type"] == ["refresh_token"] and token_body["refresh_token"] == ["R1"]
    assert token_body["client_id"] == ["CID"] and token_body["client_secret"] == ["SEC"]
    assert responses.calls[2].request.headers["Authorization"] == "Bearer NEW"
    assert multipart(responses.calls[2].request)["image"][1] == PNG  # stream rewound for the retry
    assert s.oauth2.access_token == "NEW" and saved


@responses.activate
def test_imgur_error_message():
    responses.post(img_imgur.UPLOAD_URL, status=400, json={"data": {"error": {"code": 1003,
                                                                              "message": "File type invalid"}},
                                                           "success": False, "status": 400})
    with pytest.raises(UploaderError, match="File type invalid"):
        img_imgur.ImgurUploader(img_imgur.ImgurSettings(client_id="C"), ctx()).upload(io.BytesIO(b"x"), "a.png")


def test_imgur_validate():
    assert img_imgur.ImgurUploader(img_imgur.ImgurSettings(), ctx()).validate()
    assert img_imgur.ImgurUploader(img_imgur.ImgurSettings(client_id="C", account_type="User"), ctx()).validate()


def test_imgur_authorize_uses_fixed_callback_port():
    seen = {}

    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        threading.Thread(target=lambda: (time.sleep(0.1), requests.get(
            q["redirect_uri"], params={"code": "C0DE", "state": q["state"]}, timeout=5))).start()

    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    s = img_imgur.ImgurSettings(client_id="CID", client_secret="SEC", redirect_port=port)
    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://api.imgur.com/oauth2/token", json={"access_token": "AT", "refresh_token": "RT",
                                                            "expires_in": 315360000, "account_username": "me"})
        info = img_imgur.authorize(s, ctx(open_url=fake_browser))
        body = parse_qs(rs.calls[0].request.body)
    assert seen["redirect_uri"] == f"http://127.0.0.1:{port}/" and seen["response_type"] == "code"
    assert "code_challenge" not in seen
    assert body["code"] == ["C0DE"] and body["client_secret"] == ["SEC"]
    assert info.access_token == "AT" and s.oauth2.refresh_token == "RT" and s.account_type == "User"
    assert info.extra["account_username"] == "me"


@responses.activate
def test_imgur_list_albums():
    responses.get("https://api.imgur.com/3/account/me/albums",
                  json={"data": [{"id": "A1", "title": "Shots"}], "success": True, "status": 200})
    s = img_imgur.ImgurSettings(client_id="C", oauth2=OAuth2Info(access_token="AT"))
    albums = img_imgur.list_albums(s, ctx())
    assert albums == [{"id": "A1", "title": "Shots"}]
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer AT" and query(req) == {"page": "0", "perPage": "100"}


# ---- ImageShack --------------------------------------------------------------------------------

@responses.activate
def test_imageshack_upload():
    responses.post(img_imageshack.UPLOAD_URL, json={"success": True, "process_time": 100, "result": {
        "max_filesize": 26214400, "passed": 1, "failed": 0, "total": 1, "images": [{
            "id": "newx3Kp", "server": 921, "bucket": 2, "filename": "newx3Kp.png",
            "original_filename": "shot.png", "direct_link": "imageshack.com/a/img921/2/newx3Kp.png"}]}})
    s = img_imageshack.ImageShackSettings(api_key="KEY", auth_token="TOK", is_public=True, thumbnail_width=256)
    res = img_imageshack.ImageShackUploader(s, ctx()).upload(io.BytesIO(PNG), "shot.png")
    parts = multipart(responses.calls[0].request)
    assert responses.calls[0].request.url == "https://api.imageshack.com/v2/images"
    assert parts["file"] == ("shot.png", PNG)
    assert parts["api_key"] == (None, b"KEY") and parts["auth_token"] == (None, b"TOK")
    assert parts["public"] == (None, b"y")
    assert res.url == "https://imagizer.imageshack.com/a/img921/2/newx3Kp.png"
    assert res.thumbnail_url == "https://imagizer.imageshack.us/v2/256x0q90/921/newx3Kp.png"


@responses.activate
def test_imageshack_login_and_captcha_error():
    responses.post(img_imageshack.LOGIN_URL, json={"success": True, "result": {"auth_token": "NEWTOK",
                                                                               "username": "jane"}})
    responses.post(img_imageshack.LOGIN_URL, json={"success": False, "result": False, "error": {
        "error_code": 2, "error_message": "Missing capcha response"}})
    s = img_imageshack.ImageShackSettings(api_key="K", username="jane", password="pw")
    assert img_imageshack.authorize(s, ctx()) == "NEWTOK" and s.auth_token == "NEWTOK"
    parts = multipart(responses.calls[0].request)
    assert parts["user"] == (None, b"jane") and parts["password"] == (None, b"pw")
    with pytest.raises(UploaderError, match="captcha"):
        img_imageshack.login(img_imageshack.ImageShackSettings(api_key="K", username="j", password="p"), ctx())


@responses.activate
def test_imageshack_error():
    responses.post(img_imageshack.UPLOAD_URL, status=400, json={"success": False, "error": {
        "error_code": 0, "error_message": "A valid API key is required for this request."}})
    with pytest.raises(UploaderError, match="valid API key"):
        img_imageshack.ImageShackUploader(img_imageshack.ImageShackSettings(api_key="x", auth_token="t"),
                                          ctx()).upload(io.BytesIO(b"x"), "a.png")
    assert img_imageshack.ImageShackUploader(img_imageshack.ImageShackSettings(), ctx()).validate()


# ---- Flickr ------------------------------------------------------------------------------------

FLICKR_SIZES = {"sizes": {"canblog": 0, "canprint": 0, "candownload": 1, "size": [
    {"label": "Square", "width": 75, "height": 75, "source": "https://live.staticflickr.com/65535/5432_ab_s.jpg",
     "url": "https://www.flickr.com/photos/jane/5432/sizes/sq/", "media": "photo"},
    {"label": "Small", "width": 240, "height": 180, "source": "https://live.staticflickr.com/65535/5432_ab_m.jpg",
     "url": "https://www.flickr.com/photos/jane/5432/sizes/s/", "media": "photo"},
    {"label": "Original", "width": 1920, "height": 1080,
     "source": "https://live.staticflickr.com/65535/5432_cd_o.png",
     "url": "https://www.flickr.com/photos/jane/5432/sizes/o/", "media": "photo"}]}, "stat": "ok"}
FLICKR_INFO = {"photo": {"id": "5432", "owner": {"nsid": "12@N01", "username": "jane"}, "urls": {"url": [
    {"type": "photopage", "_content": "https://www.flickr.com/photos/jane/5432/"}]}}, "stat": "ok"}


def _flickr_settings(**kw):
    return img_flickr.FlickrSettings(api_key="ck", api_secret="cs", oauth_token="tok", oauth_token_secret="ts", **kw)


def _assert_signed(fields: dict, url: str, method: str):
    params = [(k, v) for k, v in fields.items() if k != "oauth_signature"]
    base = oauth1.signature_base_string(method, url, params)
    assert oauth1.hmac_sha1_signature(base, "cs", "ts") == fields["oauth_signature"]


@responses.activate
def test_flickr_upload_direct_link():
    responses.post(img_flickr.UPLOAD_URL, body='<?xml version="1.0" encoding="utf-8" ?>\n'
                                                '<rsp stat="ok">\n<photoid>5432</photoid>\n</rsp>\n')
    responses.get(img_flickr.REST_URL, json=FLICKR_SIZES)
    res = img_flickr.FlickrUploader(_flickr_settings(title="Shot", is_public="0", content_type="2"), ctx()).upload(
        io.BytesIO(PNG), "shot.png")
    up = responses.calls[0].request
    parts = multipart(up)
    assert parts["photo"] == ("shot.png", PNG)
    fields = {k: v.decode() for k, (fn, v) in parts.items() if k != "photo"}
    assert fields["title"] == "Shot" and fields["is_public"] == "0" and fields["content_type"] == "2"
    assert "description" not in fields
    assert fields["oauth_consumer_key"] == "ck" and fields["oauth_token"] == "tok"
    _assert_signed(fields, img_flickr.UPLOAD_URL, "POST")  # signed without the photo part
    q = query(responses.calls[1].request)
    assert q["method"] == "flickr.photos.getSizes" and q["photo_id"] == "5432" and q["format"] == "json"
    _assert_signed(q, img_flickr.REST_URL, "GET")
    assert res.url == "https://live.staticflickr.com/65535/5432_cd_o.png"
    assert res.thumbnail_url == "https://live.staticflickr.com/65535/5432_ab_m.jpg"
    assert len(responses.calls) == 2


@responses.activate
def test_flickr_upload_photo_page():
    responses.post(img_flickr.UPLOAD_URL, body='<rsp stat="ok"><photoid>5432</photoid></rsp>')
    responses.get(img_flickr.REST_URL, json=FLICKR_SIZES)
    responses.get(img_flickr.REST_URL, json=FLICKR_INFO)
    res = img_flickr.FlickrUploader(_flickr_settings(direct_link=False), ctx()).upload(io.BytesIO(PNG), "a.png")
    assert query(responses.calls[2].request)["method"] == "flickr.photos.getInfo"
    assert res.url == "https://www.flickr.com/photos/jane/5432/"


@responses.activate
def test_flickr_upload_error():
    responses.post(img_flickr.UPLOAD_URL, body='<rsp stat="fail"><err code="98" msg="Invalid auth token" /></rsp>')
    with pytest.raises(UploaderError, match="Invalid auth token"):
        img_flickr.FlickrUploader(_flickr_settings(), ctx()).upload(io.BytesIO(PNG), "a.png")
    assert img_flickr.FlickrUploader(img_flickr.FlickrSettings(api_key="a", api_secret="b"), ctx()).validate()


def test_flickr_authorize_stores_tokens(monkeypatch):
    calls = {}

    def fake(spec, key, secret, c):
        calls.update(spec=spec, key=key, secret=secret)
        return {"oauth_token": "T", "oauth_token_secret": "S", "user_nsid": "1@N0", "username": "jane"}

    monkeypatch.setattr(oauth1, "authorize", fake)
    saved = []
    s = img_flickr.FlickrSettings(api_key="ck", api_secret="cs")
    img_flickr.authorize(s, ctx(save_settings=lambda: saved.append(1)))
    assert calls["spec"].extra_authorize_params == {"perms": "write"}
    assert calls["spec"].request_token_url == "https://www.flickr.com/services/oauth/request_token"
    assert (s.oauth_token, s.oauth_token_secret, s.user_nsid, s.username) == ("T", "S", "1@N0", "jane") and saved


# ---- Chevereto ---------------------------------------------------------------------------------

CHEVERETO_OK = {"status_code": 200, "success": {"message": "file uploaded", "code": 200}, "image": {
    "name": "shot", "extension": "png", "url": "https://img.example.com/images/2026/10/11/shot.png",
    "url_viewer": "https://img.example.com/image/AbC1", "url_short": "https://img.example.com/image/AbC1",
    "display_url": "https://img.example.com/images/2026/10/11/shot.md.png",
    "thumb": {"url": "https://img.example.com/images/2026/10/11/shot.th.png"},
    "medium": {"url": "https://img.example.com/images/2026/10/11/shot.md.png"},
    "delete_url": "https://img.example.com/image/AbC1/delete/3f2a"}, "status_txt": "OK"}


@responses.activate
def test_chevereto_upload():
    responses.post("https://img.example.com/api/1/upload", json=CHEVERETO_OK)
    s = img_chevereto.CheveretoSettings(upload_url="img.example.com/", api_key="chv_KEY", album_id="Xy",
                                        expiration="P3D", nsfw=True)
    res = img_chevereto.CheveretoUploader(s, ctx()).upload(io.BytesIO(PNG), "shot.png")
    req = responses.calls[0].request
    assert req.headers["X-API-Key"] == "chv_KEY"
    parts = multipart(req)
    assert parts["source"] == ("shot.png", PNG) and parts["format"] == (None, b"json")
    assert parts["album_id"] == (None, b"Xy") and parts["expiration"] == (None, b"P3D")
    assert parts["nsfw"] == (None, b"1") and "title" not in parts
    assert res.url == "https://img.example.com/images/2026/10/11/shot.png"
    assert res.thumbnail_url.endswith("shot.th.png")
    assert res.deletion_url == "https://img.example.com/image/AbC1/delete/3f2a"


@responses.activate
def test_chevereto_viewer_url_and_error():
    responses.post("https://img.example.com/api/1/upload", json=CHEVERETO_OK)
    responses.post("https://img.example.com/api/1/upload", status=400,
                   json={"status_code": 400, "error": {"message": "Invalid API key", "code": 100},
                         "status_txt": "Bad Request"})
    s = img_chevereto.CheveretoSettings(upload_url="https://img.example.com/api/1/upload", api_key="k",
                                        direct_url=False)
    assert img_chevereto.CheveretoUploader(s, ctx()).upload(io.BytesIO(PNG), "a.png").url == \
        "https://img.example.com/image/AbC1"
    with pytest.raises(UploaderError, match="Invalid API key"):
        img_chevereto.CheveretoUploader(s, ctx()).upload(io.BytesIO(PNG), "a.png")
    assert len(img_chevereto.CheveretoUploader(img_chevereto.CheveretoSettings(), ctx()).validate()) == 2


# ---- vgy.me ------------------------------------------------------------------------------------

@responses.activate
def test_vgyme_upload():
    responses.post(img_vgyme.UPLOAD_URL, json={"error": False, "filesize": 123456, "filename": "abc123",
                                               "ext": "png", "url": "https://vgy.me/u/abc123",
                                               "image": "https://i.vgy.me/abc123.png",
                                               "delete": "https://vgy.me/delete/DelKey"})
    res = img_vgyme.VgymeUploader(img_vgyme.VgymeSettings(user_key="UK", title="T"), ctx()).upload(
        io.BytesIO(PNG), "shot.png")
    parts = multipart(responses.calls[0].request)
    assert parts["file"] == ("shot.png", PNG) and parts["userkey"] == (None, b"UK") and parts["title"] == (None, b"T")
    assert "description" not in parts
    assert res.url == "https://i.vgy.me/abc123.png" and res.deletion_url == "https://vgy.me/delete/DelKey"


@responses.activate
def test_vgyme_anonymous_and_error():
    responses.post(img_vgyme.UPLOAD_URL, json={"error": True, "messages": {"Unsupported": "File type"}})
    with pytest.raises(UploaderError, match="Unsupported"):
        img_vgyme.VgymeUploader(img_vgyme.VgymeSettings(), ctx()).upload(io.BytesIO(PNG), "a.png")
    assert multipart(responses.calls[0].request).keys() == {"file"}


# ---- registry / dispatch -----------------------------------------------------------------------

def test_image_services_registered():
    reg = registry.load_all()
    for name in ("Imgur", "ImageShack", "Flickr", "Chevereto", "Vgyme"):
        assert reg[f"image/{name}"].available and ImageDestination[name].value == name
    pb = reg["image/Photobucket"]
    assert not pb.available and pb.defunct == "https://web.archive.org/web/20260516202928/http://developer.photobucket.com/"
    for mod in (img_imgur, img_flickr, img_imageshack):
        assert callable(mod.authorize)


def test_dispatch_file_uploader_redirects_image_and_text_to_file_destination():
    ts = TaskSettings()
    ts.image_destination = ImageDestination.FileUploader
    ts.image_file_destination = FileDestination.Pomf
    ts.text_destination = TextDestination.FileUploader
    ts.text_file_destination = FileDestination.FTP
    assert dispatch.destination_for(EDataType.Image, ts, "a.png") == dispatch.Destination("file", "Pomf")
    assert dispatch.destination_for(EDataType.Text, ts, "a.txt") == dispatch.Destination("file", "FTP")
    ts.image_destination = ImageDestination.Imgur
    ts.text_destination = TextDestination.PrivateBin
    assert dispatch.destination_for(EDataType.Image, ts, "a.png") == dispatch.Destination("image", "Imgur")
    assert dispatch.destination_for(EDataType.Text, ts, "a.txt") == dispatch.Destination("text", "PrivateBin")


def test_settings_round_trip_through_uploaders_config():
    import dataclasses

    from sharel.core.serialize import from_jsonable, to_jsonable
    from sharel.uploaders.config import UploadersConfig

    for info in registry.load_all().values():
        if info.category not in ("image", "text") or not info.available:
            continue
        cls = info.settings_cls
        assert dataclasses.is_dataclass(cls), info.key
        for f in dataclasses.fields(cls):
            assert f.metadata.get("label"), f"{info.key}.{f.name} has no label"
            if f.metadata.get("choices"):
                assert f.default in f.metadata["choices"], f"{info.key}.{f.name} default not in choices"
        obj = cls()
        again = from_jsonable(cls, to_jsonable(obj))
        assert again == obj, info.key
        cfg = UploadersConfig(services={info.key: to_jsonable(obj)})
        assert cfg.settings_for(info.key, cls) == obj
    s = from_jsonable(img_imgur.ImgurSettings, {"client_id": "C", "oauth2": {"access_token": "A", "expires_at": 5.0}})
    assert isinstance(s.oauth2, OAuth2Info) and s.oauth2.access_token == "A"


def test_imgur_authorize_port_in_use():
    import socket
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        s = img_imgur.ImgurSettings(client_id="C", client_secret="S", redirect_port=busy.getsockname()[1])
        with pytest.raises(UploaderError, match="in use"):
            img_imgur.authorize(s, ctx(open_url=lambda u: None))
