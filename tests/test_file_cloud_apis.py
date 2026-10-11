"""Azure Storage (local HTTP server that verifies SharedKey), Backblaze B2 and Google Cloud Storage (responses)."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.server
import io
import json
import threading
import time
from email.parser import BytesParser
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import responses

from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import file_b2, file_gcs
from sharel.uploaders.services.file_azure import AzureStorageSettings, AzureStorageUploader
from sharel.uploaders.services.file_b2 import BackblazeB2Settings, BackblazeB2Uploader
from sharel.uploaders.services.file_gcs import GoogleCloudStorageSettings, GoogleCloudStorageUploader

def _body(req) -> bytes:
    body = req.body
    return body.read() if hasattr(body, "read") else (body.encode() if isinstance(body, str) else body)


# ---- Azure ---------------------------------------------------------------------------------------

AZ_KEY = base64.b64encode(b"azure-test-key-0123456789abcdef").decode()


def _server_side_signature(method: str, path: str, headers, account: str) -> str:
    """Independent SharedKey computation, following the Azure Storage REST docs."""
    h = {k.lower(): v for k, v in headers.items()}
    length = h.get("content-length", "")
    std = [method, h.get("content-encoding", ""), h.get("content-language", ""), "" if length == "0" else length,
           h.get("content-md5", ""), h.get("content-type", ""), h.get("date", ""), h.get("if-modified-since", ""),
           h.get("if-match", ""), h.get("if-none-match", ""), h.get("if-unmodified-since", ""), h.get("range", "")]
    xms = "".join(f"{k}:{h[k].strip()}\n" for k in sorted(h) if k.startswith("x-ms-"))
    text = "\n".join(std) + "\n" + xms + f"/{account}{path}"
    mac = hmac.new(base64.b64decode(AZ_KEY), text.encode(), hashlib.sha256).digest()
    return f"SharedKey {account}:{base64.b64encode(mac).decode()}"


@contextlib.contextmanager
def azure_server():
    blobs: dict[str, tuple[dict, bytes]] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            u = urlparse(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            q = parse_qs(u.query)
            if "sig" in q:
                ok = q.get("sp") == ["cw"] and q.get("sig") == ["SASSIG"]
            else:
                ok = self.headers.get("Authorization") == _server_side_signature("PUT", u.path, self.headers, "acct")
            if not ok or self.headers.get("x-ms-blob-type") != "BlockBlob" or not self.headers.get("x-ms-version"):
                self.send_response(403)
                self.send_header("x-ms-error-code", "AuthenticationFailed")
                self.end_headers()
                self.wfile.write(b"<Error><Code>AuthenticationFailed</Code><Message>Server failed to "
                                 b"authenticate the request.</Message></Error>")
                return
            blobs[unquote(u.path)] = (dict(self.headers), body)
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/acct", blobs
    finally:
        srv.shutdown()
        srv.server_close()


def test_azure_shared_key_upload():
    with azure_server() as (endpoint, blobs):
        st = AzureStorageSettings(account_name="acct", account_access_key=AZ_KEY, container="shots",
                                  endpoint=endpoint, upload_path="ShareL/%y", cache_control="max-age=60")
        progress = []
        res = AzureStorageUploader(st, UploadContext(progress=lambda d, t: progress.append((d, t)))).upload(
            io.BytesIO(b"blobdata"), "a b.png")
    import datetime as _dt
    year = f"{_dt.datetime.now().year:04d}"
    headers, body = blobs[f"/acct/shots/ShareL/{year}/a b.png"]
    assert body == b"blobdata" and headers["Content-Type"] == "image/png"
    assert headers["x-ms-blob-cache-control"] == "max-age=60"
    assert res.url == f"{endpoint}/shots/ShareL/{year}/a%20b.png" and progress[-1] == (8, 8)


def test_azure_wrong_key_and_sas():
    with azure_server() as (endpoint, blobs):
        bad = AzureStorageSettings(account_name="acct", account_access_key=base64.b64encode(b"nope").decode(),
                                   container="c", endpoint=endpoint)
        with pytest.raises(UploaderError, match="AuthenticationFailed"):
            AzureStorageUploader(bad, UploadContext()).upload(io.BytesIO(b"x"), "x.txt")
        sas = AzureStorageSettings(account_name="acct", sas_token="?sv=2022-11-02&sp=cw&sig=SASSIG", container="c",
                                   endpoint=endpoint, custom_domain="cdn.example.com")
        res = AzureStorageUploader(sas, UploadContext()).upload(io.BytesIO(b"sas"), "s.txt")
    assert blobs["/acct/c/s.txt"][1] == b"sas" and "Authorization" not in blobs["/acct/c/s.txt"][0]
    assert res.url == "https://cdn.example.com/s.txt"


def test_azure_public_urls():
    st = AzureStorageSettings(account_name="me", account_access_key=AZ_KEY, container="$root")
    up = AzureStorageUploader(st, UploadContext())
    assert up.request_url("x.png") == "https://me.blob.core.windows.net/$root/x.png"
    assert up.result_url("x.png") == "https://me.blob.core.windows.net/x.png"
    st.container, st.environment = "pics", "blob.core.chinacloudapi.cn"
    assert up.result_url("x.png") == "https://me.blob.core.chinacloudapi.cn/pics/x.png"


def test_azure_signature_against_documented_layout():
    """Content-Length 0 is signed as an empty line (service version >= 2015-02-21)."""
    from sharel.uploaders.services.file_azure import string_to_sign
    s = string_to_sign("PUT", {"Content-Length": "0", "Content-Type": "text/plain", "x-ms-version": "v",
                               "x-ms-date": "d", "x-ms-blob-type": "BlockBlob"}, "acct", "/c/a%20b.txt")
    assert s == "PUT\n\n\n\n\ntext/plain\n\n\n\n\n\n\nx-ms-blob-type:BlockBlob\nx-ms-date:d\nx-ms-version:v\n" \
                "/acct/c/a%20b.txt"


# ---- Backblaze B2 --------------------------------------------------------------------------------

API = "https://api005.backblazeb2.com"


def _b2_auth(rs, buckets=None, prefix=None):
    rs.get(file_b2.AUTHORIZE_URL, json={
        "accountId": "acc1", "authorizationToken": "ACCTOKEN",
        "apiInfo": {"storageApi": {"apiUrl": API, "downloadUrl": "https://f005.backblazeb2.com",
                                   "allowed": {"buckets": buckets, "capabilities": ["writeFiles", "listBuckets"],
                                               "namePrefix": prefix}}}})


@responses.activate
def test_b2_native_upload_flow():
    _b2_auth(responses)
    responses.post(f"{API}/b2api/v4/b2_list_buckets", json={"buckets": [{"bucketName": "shots", "bucketId": "B1"}]})
    responses.post(f"{API}/b2api/v4/b2_get_upload_url",
                   json={"bucketId": "B1", "uploadUrl": "https://pod-1.backblaze.com/up/1", "authorizationToken": "UP"})
    responses.post("https://pod-1.backblaze.com/up/1", json={"fileName": "ShareL/x/a b.png", "fileId": "F"})
    st = BackblazeB2Settings(application_key_id="kid", application_key="key", bucket_name="shots",
                             upload_path="ShareL/x")
    res = BackblazeB2Uploader(st, UploadContext()).upload(io.BytesIO(b"pngdata"), "a b.png")
    auth_req = responses.calls[0].request
    assert auth_req.headers["Authorization"] == "Basic " + base64.b64encode(b"kid:key").decode()
    assert json.loads(responses.calls[1].request.body) == {"accountId": "acc1", "bucketName": "shots"}
    assert responses.calls[1].request.headers["Authorization"] == "ACCTOKEN"
    up = responses.calls[3].request
    assert up.headers["Authorization"] == "UP" and up.headers["X-Bz-File-Name"] == "ShareL/x/a%20b.png"
    assert up.headers["X-Bz-Content-Sha1"] == hashlib.sha1(b"pngdata").hexdigest()
    assert up.headers["Content-Type"] == "image/png" and _body(up) == b"pngdata"
    assert unquote(up.headers["X-Bz-Info-b2-content-disposition"]) == 'inline; filename="a b.png"'
    assert res.url == "https://f005.backblazeb2.com/file/shots/ShareL/x/a%20b.png"


@responses.activate
def test_b2_retries_and_custom_url():
    _b2_auth(responses, buckets=[{"id": "B9", "name": "shots"}])
    responses.post(f"{API}/b2api/v4/b2_get_upload_url",
                   json={"uploadUrl": "https://pod/u", "authorizationToken": "T"})
    responses.post("https://pod/u", status=503, json={"status": 503, "code": "service_unavailable", "message": ""})
    responses.post("https://pod/u", status=401, json={"status": 401, "code": "expired_auth_token", "message": ""})
    responses.post("https://pod/u", status=429, json={"status": 429, "code": "too_many_requests", "message": ""})
    responses.post("https://pod/u", json={"fileName": "p/f.txt"})
    up = BackblazeB2Uploader(BackblazeB2Settings(application_key_id="k", application_key="s", bucket_name="shots",
                                                 upload_path="p", use_custom_url=True,
                                                 custom_url="cdn.example.com/b2"), UploadContext())
    sleeps = []
    up.sleep = sleeps.append
    res = up.upload(io.BytesIO(b"t"), "f.txt")
    get_urls = [c for c in responses.calls if c.request.url.endswith("b2_get_upload_url")]
    assert json.loads(get_urls[0].request.body) == {"bucketId": "B9"}
    assert len(get_urls) == 3  # new URL after 503 and after the expired token; same URL after 429
    assert sleeps == [2, 4, 8] and res.url == "https://cdn.example.com/b2/p/f.txt"


@responses.activate
def test_b2_errors():
    _b2_auth(responses, buckets=[{"id": "B9", "name": "other"}])
    st = BackblazeB2Settings(application_key_id="k", application_key="s", bucket_name="shots")
    with pytest.raises(UploaderError, match="restricted to other"):
        BackblazeB2Uploader(st, UploadContext()).upload(io.BytesIO(b"t"), "f.txt")
    responses.reset()
    _b2_auth(responses, buckets=[{"id": "B9", "name": "shots"}], prefix="only/")
    with pytest.raises(UploaderError, match="only/"):
        BackblazeB2Uploader(st, UploadContext()).upload(io.BytesIO(b"t"), "f.txt")
    responses.reset()
    responses.get(file_b2.AUTHORIZE_URL, status=401, json={"status": 401, "code": "unauthorized", "message": "bad"})
    with pytest.raises(UploaderError, match="Could not authenticate"):
        BackblazeB2Uploader(st, UploadContext()).upload(io.BytesIO(b"t"), "f.txt")


# ---- Google Cloud Storage ------------------------------------------------------------------------

@responses.activate
def test_gcs_multipart_upload_public_acl():
    responses.post("https://storage.googleapis.com/upload/storage/v1/b/my-bucket/o",
                   json={"name": "ShareL/x/shot.png"})
    st = GoogleCloudStorageSettings(client_id="cid", client_secret="sec", bucket="my-bucket", object_prefix="ShareL/x",
                                    oauth2=OAuth2Info(access_token="AT", expires_at=time.time() + 3600))
    res = GoogleCloudStorageUploader(st, UploadContext()).upload(io.BytesIO(b"\x89PNG"), "shot.png")
    req = responses.calls[0].request
    q = parse_qs(urlparse(req.url).query)
    assert q == {"uploadType": ["multipart"], "fields": ["name"]}
    assert req.headers["Authorization"] == "Bearer AT"
    assert req.headers["Content-Type"].startswith("multipart/related; boundary=")
    raw = _body(req)
    msg = BytesParser().parsebytes(f"Content-Type: {req.headers['Content-Type']}\r\n\r\n".encode() + raw)
    meta_part, media_part = msg.get_payload()
    meta = json.loads(meta_part.get_payload())
    assert meta == {"name": "ShareL/x/shot.png", "contentType": "image/png",
                    "acl": [{"entity": "allUsers", "role": "READER"}]}
    assert media_part.get_content_type() == "image/png" and media_part.get_payload(decode=True) == b"\x89PNG"
    assert res.url == "https://storage.googleapis.com/my-bucket/ShareL/x/shot.png"


@responses.activate
def test_gcs_refreshes_token_custom_domain_no_ext():
    responses.post("https://oauth2.googleapis.com/token", json={"access_token": "NEW", "expires_in": 3600})
    responses.post("https://storage.googleapis.com/upload/storage/v1/b/b/o", json={"name": "p/clip"})
    saved = []
    st = GoogleCloudStorageSettings(client_id="cid", client_secret="sec", bucket="b", object_prefix="p",
                                    domain="files.example.com", remove_extension_video=True, set_public_acl=False,
                                    oauth2=OAuth2Info(access_token="OLD", refresh_token="RT", expires_at=1.0))
    res = GoogleCloudStorageUploader(st, UploadContext(save_settings=lambda: saved.append(1))).upload(
        io.BytesIO(b"mp4"), "clip.mp4")
    assert parse_qs(responses.calls[0].request.body)["refresh_token"] == ["RT"]
    assert responses.calls[1].request.headers["Authorization"] == "Bearer NEW" and saved
    raw = _body(responses.calls[1].request)
    assert b'"name": "p/clip"' in raw and b'"acl"' not in raw
    assert res.url == "https://files.example.com/p/clip" and st.oauth2.access_token == "NEW"


def test_gcs_authorize_stores_tokens():
    seen = {}

    def browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        import requests
        threading.Thread(target=lambda: requests.get(q["redirect_uri"], params={"code": "C", "state": q["state"]},
                                                     timeout=5)).start()

    st = GoogleCloudStorageSettings(client_id="cid", client_secret="sec")
    saved = []
    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://oauth2.googleapis.com/token", json={"access_token": "A", "refresh_token": "R",
                                                             "expires_in": 3599})
        file_gcs.authorize(st, UploadContext(open_url=browser, save_settings=lambda: saved.append(1)))
    assert st.oauth2.refresh_token == "R" and saved
    assert seen["access_type"] == "offline" and seen["prompt"] == "consent"
    assert "devstorage.read_write" in seen["scope"] and seen["code_challenge_method"] == "S256"
