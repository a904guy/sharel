"""Backblaze B2, native API (ShareX BackblazeB2.cs), current version v4.

1. ``b2_authorize_account`` (basic auth with the application key ID + key) → apiUrl, downloadUrl, the key's
   allowed buckets / name prefix / capabilities
2. bucket ID from the key's allowed buckets, else ``b2_list_buckets``
3. ``b2_get_upload_url`` → upload URL + token
4. ``b2_upload_file`` with ``X-Bz-File-Name``, ``X-Bz-Content-Sha1`` and an inline content disposition,
   retried like ShareX (new upload URL on connection failures / 401 expired token / 503, same URL on 408 / 429,
   exponential back-off, 5 attempts)

URL: ``<downloadUrl>/file/<bucket>/<file name>``, or ``<custom URL>/<file name>``.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import BinaryIO

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadContext, UploaderError, UploadResult, stream_size
from ..registry import register, setting
from ._file_helpers import combine_url, fix_prefix, mime_type, parse_pattern, url_encode, url_encode_path

AUTHORIZE_URL = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
API_PATH = "/b2api/v4/"
MAX_TRIES = 5


@dataclass
class BackblazeB2Settings:
    application_key_id: str = setting("", label="Application key ID")
    application_key: str = setting("", label="Application key", secret=True)
    bucket_name: str = setting("", label="Bucket name")
    upload_path: str = setting("ShareL/%y/%mo", label="Upload path", help="Name-parser patterns allowed")
    use_custom_url: bool = setting(False, label="Use custom URL")
    custom_url: str = setting("https://example.com", label="Custom URL",
                              help="e.g. a CDN or Cloudflare domain in front of the bucket")


def _b2_error(resp) -> tuple[str, str]:
    try:
        data = resp.json()
        return str(data.get("code", "")), f"{data.get('status', resp.status_code)} {data.get('code', '')}: " \
                                          f"{data.get('message', '')}"
    except ValueError:
        return "", f"HTTP {resp.status_code}: {resp.text[:200]}"


@register("file", "BackblazeB2", BackblazeB2Settings, title="Backblaze B2", homepage="https://www.backblaze.com/cloud-storage")
class BackblazeB2Uploader(FileUploader):
    sleep = staticmethod(time.sleep)  # back-off between attempts (tests replace it)

    def validate(self) -> list[str]:
        s = self.settings
        if not s.application_key_id.strip() or not s.application_key.strip():
            return ["application key ID and key must be set"]
        if not s.bucket_name.strip():
            return ["bucket name must be set"]
        return []

    # -- API calls --
    def authorize(self) -> dict:
        s = self.settings
        resp = self.ctx.get(AUTHORIZE_URL, auth=(s.application_key_id.strip(), s.application_key.strip()))
        if resp.status_code != 200:
            raise UploaderError(f"Could not authenticate with B2: {_b2_error(resp)[1]}", resp.text)
        return resp.json()

    def api(self, auth: dict, name: str, body: dict):
        storage = auth["apiInfo"]["storageApi"]
        return self.ctx.post(storage["apiUrl"].rstrip("/") + API_PATH + name, json=body,
                             headers={"Authorization": auth["authorizationToken"]})

    def bucket_id(self, auth: dict) -> str:
        name = self.settings.bucket_name.strip()
        allowed = auth["apiInfo"]["storageApi"].get("allowed") or {}
        buckets = allowed.get("buckets")
        if buckets:
            for b in buckets:
                if b.get("name") == name:
                    return b["id"]
            raise UploaderError(f"No permission to upload to bucket {name}: the application key is restricted to "
                                + ", ".join(str(b.get("name") or b.get("id")) for b in buckets))
        resp = self.api(auth, "b2_list_buckets", {"accountId": auth["accountId"], "bucketName": name})
        if resp.status_code != 200:
            raise UploaderError(f"B2 upload failed: {_b2_error(resp)[1]}", resp.text)
        for b in resp.json().get("buckets", []):
            if b.get("bucketName") == name:
                return b["bucketId"]
        raise UploaderError(f"B2 upload failed: couldn't find bucket {name}")

    @staticmethod
    def check_allowed(auth: dict, destination: str) -> None:
        allowed = auth["apiInfo"]["storageApi"].get("allowed") or {}
        caps = allowed.get("capabilities")
        if caps is not None and "writeFiles" not in caps:
            raise UploaderError("B2 application key does not have the writeFiles capability")
        prefix = allowed.get("namePrefix")
        if prefix and not destination.startswith(prefix):
            raise UploaderError(f"B2 application key may only upload to paths starting with '{prefix}', "
                                f"but the upload path is '{destination}'")

    def get_upload_url(self, auth: dict, bucket_id: str) -> dict:
        resp = self.api(auth, "b2_get_upload_url", {"bucketId": bucket_id})
        if resp.status_code != 200:
            raise UploaderError(f"Could not get B2 upload URL: {_b2_error(resp)[1]}", resp.text)
        return resp.json()

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        folder = parse_pattern(s.upload_path, NameParserType.FilePath).strip("/")
        destination = combine_url(folder, file_name)
        auth = self.authorize()
        bucket_id = self.bucket_id(auth)
        self.check_allowed(auth, destination)

        start = stream.tell()
        length = stream_size(stream)
        sha1 = hashlib.sha1()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha1.update(chunk)
        disposition = f'inline; filename="{file_name}"'

        upload_url: dict | None = None
        last_error = ""
        for attempt in range(1, MAX_TRIES + 1):
            self.ctx.check_cancelled()
            if attempt > 1:
                self.sleep(2 ** (attempt - 1))
            if upload_url is None:
                upload_url = self.get_upload_url(auth, bucket_id)
            stream.seek(start)
            headers = {
                "Authorization": upload_url["authorizationToken"],
                "X-Bz-File-Name": url_encode_path(destination),
                "Content-Type": mime_type(destination),
                "Content-Length": str(length),
                "X-Bz-Content-Sha1": sha1.hexdigest(),
                "X-Bz-Info-src_last_modified_millis": str(int(time.time() * 1000)),
                "X-Bz-Info-b2-content-disposition": url_encode(disposition),
            }
            try:
                resp = self.ctx.post(upload_url["uploadUrl"], data=self.ctx.progress_stream(stream, length),
                                     headers=headers)
            except UploaderError as e:
                if e.__class__.__name__ == "UploadCancelled":
                    raise
                last_error = str(e)  # connection failed (or upload caps exceeded): try a new URL
                upload_url = None
                continue
            if resp.status_code == 200:
                uploaded = resp.json()
                encoded = url_encode_path(uploaded.get("fileName", destination))
                if s.use_custom_url:
                    url = fix_prefix(combine_url(s.custom_url, encoded))
                else:
                    download = auth["apiInfo"]["storageApi"]["downloadUrl"]
                    url = combine_url(download, "file", url_encode(s.bucket_name.strip()), encoded)
                return UploadResult(url=url, response=resp.text)
            code, last_error = _b2_error(resp)
            if resp.status_code == 401 and code in ("expired_auth_token", "bad_auth_token"):
                upload_url = None
            elif resp.status_code in (408, 429):
                pass  # same URL
            elif resp.status_code == 503:
                upload_url = None
            else:
                raise UploaderError(f"B2 upload failed: {last_error}", resp.text)
        raise UploaderError(f"B2 upload failed after {MAX_TRIES} attempts: {last_error}")


def test_connection(settings: BackblazeB2Settings, ctx: UploadContext) -> str:
    up = BackblazeB2Uploader(settings, ctx)
    auth = up.authorize()
    bucket_id = up.bucket_id(auth)
    up.get_upload_url(auth, bucket_id)
    return f"Authorized; bucket {settings.bucket_name} ({bucket_id}) accepts uploads"
