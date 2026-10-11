import io

import pytest
import responses

from sharel.uploaders import registry
from sharel.uploaders.base import USER_AGENT, UploadContext, UploaderError
from sharel.uploaders.services import api_puush
from _api_helpers import body_bytes, multipart


def _uploader(**kw):
    info = registry.get("file", "Puush")
    return info.cls(info.settings_cls(**kw), UploadContext())


@responses.activate(assert_all_requests_are_fired=True)
def test_puush_upload_multipart_and_url():
    responses.post("https://puush.me/api/up", body="0,https://puu.sh/Abc12/x.png,123456,2048")
    res = _uploader(api_key="KEY").upload(io.BytesIO(b"PNGDATA"), "shot.png")
    req = responses.calls[0].request
    parts = multipart(req)
    assert parts["k"][1] == b"KEY" and parts["z"][1] == USER_AGENT.encode()
    assert parts["f"] == ("shot.png", b"PNGDATA", "image/png")
    assert req.headers["Content-Length"] == str(len(body_bytes(req)))
    assert res.url == "https://puu.sh/Abc12/x.png" and res.is_success


@responses.activate
def test_puush_error_status():
    responses.post("https://puush.me/api/up", body="-1")
    with pytest.raises(UploaderError, match="Authentication failure"):
        _uploader(api_key="BAD").upload(io.BytesIO(b"x"), "a.txt")


@responses.activate(assert_all_requests_are_fired=True)
def test_puush_login_fetches_key_before_upload():
    responses.post("https://puush.me/api/auth", body="1,APIKEY99,,0")
    responses.post("https://puush.me/api/up", body="0,https://puu.sh/Z/a.txt,1,1")
    saved = []
    info = registry.get("file", "Puush")
    s = info.settings_cls(email="me@example.com", password="pw")
    up = info.cls(s, UploadContext(save_settings=lambda: saved.append(1)))
    assert up.validate() == []
    res = up.upload(io.BytesIO(b"x"), "a.txt")
    assert "e=me%40example.com" in responses.calls[0].request.body and "p=pw" in responses.calls[0].request.body
    assert s.api_key == "APIKEY99" and saved == [1]
    assert multipart(responses.calls[1].request)["k"][1] == b"APIKEY99"
    assert res.url == "https://puu.sh/Z/a.txt"
    assert api_puush.ERRORS[-4].startswith("Insufficient")
