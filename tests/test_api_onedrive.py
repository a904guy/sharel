import io
import json
import time

import pytest
import responses
from _api_helpers import body_bytes

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import api_onedrive

GRAPH = "https://graph.microsoft.com/v1.0"
UPLOAD_URL = "https://my.microsoftpersonalcontent.com/up/AbCdEf?cid=123"
ITEM = {"id": "01BYE5RZ6QN3ZWBTUFOFD3GSPGOHDJD36K", "name": "shot one.png",
        "webUrl": "https://onedrive.live.com/?cid=123&id=ITEM", "size": 7}


def make(ctx=None, **kw):
    info = registry.get("file", "OneDrive")
    kw.setdefault("oauth2", OAuth2Info(access_token="AT", refresh_token="RT", expires_at=time.time() + 3600))
    return info.cls(info.settings_cls(client_id="cid", **kw), ctx or UploadContext())


@responses.activate(assert_all_requests_are_fired=True)
def test_onedrive_simple_upload_into_folder_and_view_link():
    responses.put(f"{GRAPH}/me/drive/items/FOLDER!12:/shot%20one.png:/content", json=ITEM, status=201)
    responses.post(f"{GRAPH}/me/drive/items/{ITEM['id']}/createLink", json={
        "id": "perm1", "roles": ["read"], "link": {"type": "view", "scope": "anonymous",
                                                   "webUrl": "https://1drv.ms/i/s!AbCdEfG"}}, status=201)
    res = make(folder_id="FOLDER!12").upload(io.BytesIO(b"PNGDATA"), "shot one.png")
    put = responses.calls[0].request
    assert put.url.endswith(":/content?@microsoft.graph.conflictBehavior=replace")
    assert put.headers["Authorization"] == "Bearer AT" and body_bytes(put) == b"PNGDATA"
    assert json.loads(responses.calls[1].request.body) == {"type": "view", "scope": "anonymous"}
    assert res.url == "https://1drv.ms/i/s!AbCdEfG"


@responses.activate
def test_onedrive_root_web_url_without_link():
    responses.put(f"{GRAPH}/me/drive/root:/a.txt:/content", json=ITEM)
    res = make(auto_create_shareable_link=False).upload(io.BytesIO(b"x"), "a.txt")
    assert res.url == ITEM["webUrl"]


@responses.activate(assert_all_requests_are_fired=True)
def test_onedrive_upload_session_fragments_and_embed_link():
    responses.post(f"{GRAPH}/me/drive/root:/big.bin:/createUploadSession",
                   json={"uploadUrl": UPLOAD_URL, "expirationDateTime": "2026-10-12T00:00:00Z"})
    responses.put(UPLOAD_URL, status=202, json={"nextExpectedRanges": ["327680-"]})
    responses.put(UPLOAD_URL, status=202, json={"nextExpectedRanges": ["655360-"]})
    responses.put(UPLOAD_URL, status=201, json=ITEM)
    responses.post(f"{GRAPH}/me/drive/items/{ITEM['id']}/createLink",
                   json={"link": {"type": "embed", "webUrl": "https://onedrive.live.com/embed?resid=X&authkey=Y"}})
    frag = api_onedrive.FRAGMENT_GRANULARITY
    data = b"a" * frag + b"b" * frag + b"c" * 10
    up = make(use_direct_link=True)
    up.simple_upload_limit, up.fragment_size = 100, frag
    res = up.upload(io.BytesIO(data), "big.bin")
    sess = responses.calls[0].request
    assert json.loads(sess.body) == {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
    puts = [c.request for c in responses.calls[1:4]]
    n = len(data)
    assert [p.headers["Content-Range"] for p in puts] == [
        f"bytes 0-{frag - 1}/{n}", f"bytes {frag}-{2 * frag - 1}/{n}", f"bytes {2 * frag}-{n - 1}/{n}"]
    assert all("Authorization" not in p.headers for p in puts)
    assert b"".join(body_bytes(p) for p in puts) == data
    assert json.loads(responses.calls[4].request.body) == {"type": "embed", "scope": "anonymous"}
    assert res.url == "https://onedrive.live.com/embed?resid=X&authkey=Y"


@responses.activate
def test_onedrive_failed_fragment_deletes_session():
    responses.post(f"{GRAPH}/me/drive/root:/big.bin:/createUploadSession", json={"uploadUrl": UPLOAD_URL})
    responses.put(UPLOAD_URL, status=416, json={"error": {"code": "invalidRange", "message": "Bad range"}})
    responses.delete(UPLOAD_URL, status=204)
    up = make()
    up.simple_upload_limit = 1
    with pytest.raises(UploaderError, match="Bad range"):
        up.upload(io.BytesIO(b"abc"), "big.bin")
    assert responses.calls[-1].request.method == "DELETE"


@responses.activate
def test_onedrive_business_embed_falls_back_to_view_and_folder_listing():
    responses.put(f"{GRAPH}/me/drive/root:/a.png:/content", json=ITEM)
    link_url = f"{GRAPH}/me/drive/items/{ITEM['id']}/createLink"
    responses.post(link_url, status=400, json={"error": {"code": "invalidRequest", "message": "embed"}})
    responses.post(link_url, json={"link": {"webUrl": "https://contoso.sharepoint.com/:i:/g/abc"}})
    assert make(use_direct_link=True).upload(io.BytesIO(b"x"), "a.png").url.endswith("/:i:/g/abc")
    responses.get(f"{GRAPH}/me/drive/root/children", json={
        "value": [{"id": "F1", "name": "Pics", "folder": {"childCount": 2}}, {"id": "X", "name": "a.txt"}],
        "@odata.nextLink": f"{GRAPH}/me/drive/root/children?$skiptoken=2"})
    responses.get(f"{GRAPH}/me/drive/root/children", json={"value": [{"id": "F2", "name": "Docs", "folder": {}}]})
    folders = api_onedrive.list_folders(make().settings, UploadContext())
    assert folders == [{"id": "F1", "name": "Pics"}, {"id": "F2", "name": "Docs"}]


def test_onedrive_spec():
    spec = registry.get("file", "OneDrive").oauth
    assert spec.scopes == ["offline_access", "Files.ReadWrite"] and spec.pkce
    assert spec.token_url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
