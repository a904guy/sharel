"""LobFile, formerly Lithiio (ShareX LobFile.cs; API v3.4: https://lobfile.com/api-docs).

``POST https://lobfile.com/api/v3/upload`` multipart (``file``, optional ``sha_256`` the server verifies)
with the ``X-API-Key`` header → ``{"success": true, "url": ...}`` or ``{"success": false, "error": ...}``.
The key can be fetched from email + password with ``POST /api/v3/fetch-api-key`` (ShareX's login button).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import error_message, multipart_form, send_body

API = "https://lobfile.com/api/v3"
UPLOAD_URL = API + "/upload"
FETCH_KEY_URL = API + "/fetch-api-key"


@dataclass
class LithiioSettings:
    user_api_key: str = setting("", label="API key", secret=True,
                                help="From the Account info section of the LobFile dashboard, or fetch it below")
    email: str = setting("", label="Email", help="Only used to fetch the API key")
    password: str = setting("", label="Password", secret=True, help="Only used to fetch the API key")


def fetch_api_key(settings: LithiioSettings, ctx: UploadContext) -> str:
    """Fetch the API key for email + password, store it in the settings and return it."""
    if not settings.email or not settings.password:
        raise UploaderError("Enter the LobFile email and password first")
    resp = ctx.post(FETCH_KEY_URL, files={"email": (None, settings.email), "password": (None, settings.password)})
    try:
        js = resp.json()
    except ValueError:
        raise UploaderError(error_message(resp, "LobFile API key request"), resp.text) from None
    # ShareX reads this case-insensitively (Newtonsoft); accept api_key / API_Key / apiKey
    js = {str(k).lower().replace("_", ""): v for k, v in js.items()} if isinstance(js, dict) else {}
    if not js.get("success") or not js.get("apikey"):
        raise UploaderError(f"LobFile API key request failed: {js.get('error') or resp.text[:200]}", resp.text)
    settings.user_api_key = js["apikey"]
    ctx.save_settings()
    return settings.user_api_key


def _sha256_of(stream: BinaryIO) -> str | None:
    """Hex SHA-256 of the rest of a seekable stream (position restored), None if it can't seek."""
    try:
        pos = stream.tell()
    except (OSError, AttributeError):
        return None
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b""):
        h.update(block)
    stream.seek(pos)
    return h.hexdigest()


@register("file", "Lithiio", LithiioSettings, title="LobFile", homepage="https://lobfile.com")
class LithiioUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.user_api_key else ["LobFile API key is empty"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        key = self.settings.user_api_key
        if not key:
            raise UploaderError("LobFile API key is empty")
        fields = {}
        digest = _sha256_of(stream)
        if digest:
            fields["sha_256"] = digest
        body, ctype = multipart_form(fields, "file", file_name, stream)
        resp = send_body(self.ctx, "POST", UPLOAD_URL, body, ctype, headers={"X-API-Key": key})
        try:
            js = resp.json()
        except ValueError:
            raise UploaderError(error_message(resp, "LobFile upload"), resp.text) from None
        if not isinstance(js, dict) or not js.get("success") or not js.get("url"):
            err = js.get("error") if isinstance(js, dict) else None
            raise UploaderError(f"LobFile upload failed: {err or resp.text[:200]}", resp.text)
        return UploadResult(url=js["url"], response=resp.text)
