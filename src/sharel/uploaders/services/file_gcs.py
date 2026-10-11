"""Google Cloud Storage (ShareX GoogleCloudStorage.cs): OAuth 2 (loopback + PKCE) and the JSON API multipart
upload ``POST https://storage.googleapis.com/upload/storage/v1/b/<bucket>/o?uploadType=multipart`` with the
object metadata (name, content type, and an ``allUsers: READER`` ACL when "public" is on).

URL: ``https://storage.googleapis.com/<bucket>/<object>``, or ``<domain>/<object>`` with a custom domain.
Buckets with uniform bucket-level access reject object ACLs: turn "set public ACL" off for those and grant
``allUsers`` the Storage Object Viewer role on the bucket instead.
"""
from __future__ import annotations

import io
import json
import secrets
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from .. import oauth
from ..base import FileUploader, UploadContext, UploaderError, UploadResult, json_of
from ..registry import OAuth2Info, OAuth2Spec, register, setting
from . import _file_helpers as fh

UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
OAUTH = OAuth2Spec(
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=["https://www.googleapis.com/auth/devstorage.read_write",
            "https://www.googleapis.com/auth/userinfo.profile"],
    extra_authorize_params={"access_type": "offline", "prompt": "consent"},
)


@dataclass
class GoogleCloudStorageSettings:
    client_id: str = setting("", label="OAuth client ID",
                             help="Google Cloud console > APIs & Services > Credentials > OAuth client (Desktop app)")
    client_secret: str = setting("", label="OAuth client secret", secret=True)
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    bucket: str = setting("", label="Bucket")
    domain: str = setting("", label="Custom domain", help="Empty: storage.googleapis.com/<bucket>")
    object_prefix: str = setting("ShareL/%y/%mo", label="Object prefix", help="Name-parser patterns allowed")
    remove_extension_image: bool = setting(False, label="Remove extension from image URLs")
    remove_extension_video: bool = setting(False, label="Remove extension from video URLs")
    remove_extension_text: bool = setting(False, label="Remove extension from text URLs")
    set_public_acl: bool = setting(True, label="Set public ACL (allUsers: READER)")


def upload_path(settings: GoogleCloudStorageSettings, file_name: str) -> str:
    prefix = fh.parse_folder(settings.object_prefix)
    if ((settings.remove_extension_image and fh.is_image_file(file_name))
            or (settings.remove_extension_text and fh.is_text_file(file_name))
            or (settings.remove_extension_video and fh.is_video_file(file_name))):
        file_name = fh.without_extension(file_name)
    return fh.combine_url(prefix, file_name)


def generate_url(settings: GoogleCloudStorageSettings, name: str) -> str:
    if not settings.bucket:
        return ""
    domain = settings.domain or fh.combine_url("storage.googleapis.com", settings.bucket)
    return fh.fix_prefix(fh.combine_url(domain, fh.url_encode_path(name)))


def related_body(metadata: dict, data: bytes, content_type: str) -> tuple[bytes, str]:
    """A multipart/related body: JSON metadata part, then the media part."""
    boundary = "sharel_" + secrets.token_hex(16)
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(metadata).encode("utf-8"),
        f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/related; boundary={boundary}"


@register("file", "GoogleCloudStorage", GoogleCloudStorageSettings, title="Google Cloud Storage", oauth=OAUTH,
          homepage="https://cloud.google.com/storage")
class GoogleCloudStorageUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if not s.oauth2.access_token and not s.oauth2.refresh_token:
            return ["not authorized: click Authorize in Destination settings"]
        if not s.bucket:
            return ["bucket must be set"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        name = upload_path(s, file_name)
        content_type = fh.mime_type(file_name)
        metadata: dict = {"name": name, "contentType": content_type}
        if s.set_public_acl:
            metadata["acl"] = [{"entity": "allUsers", "role": "READER"}]
        body, ctype = related_body(metadata, fh.read_all(stream), content_type)
        headers = {**oauth.bearer(OAUTH, s, self.ctx), "Content-Type": ctype}
        resp = self.ctx.post(UPLOAD_URL.format(bucket=quote(s.bucket, safe="")),
                             params={"uploadType": "multipart", "fields": "name"},
                             data=self.ctx.progress_stream(io.BytesIO(body), len(body)), headers=headers)
        data = json_of(resp, "Google Cloud Storage upload")
        if "name" not in data:
            raise UploaderError("Google Cloud Storage response has no object name", resp.text)
        return UploadResult(url=generate_url(s, data["name"]), response=resp.text)


def authorize(settings: GoogleCloudStorageSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button: browser consent, store the tokens, save."""
    if not settings.client_secret:
        raise UploaderError("Google requires the OAuth client secret too (Desktop app clients have one)")
    info = oauth.authorize(OAUTH, settings.client_id, settings.client_secret, ctx)
    settings.oauth2 = info
    ctx.save_settings()
    return info
