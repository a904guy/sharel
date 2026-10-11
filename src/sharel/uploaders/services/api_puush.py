"""puush (ShareX Puush.cs).

``POST https://puush.me/api/up`` (multipart: ``k`` = API key, ``z`` = client id, ``f`` = file). The reply is
plain text ``status,url,id,size``; a negative status is an error (-1 authentication, -2 connection,
-3 checksum, -4 insufficient storage). The API key can be fetched from email + password with
``POST /api/auth`` (``e``, ``p``, ``z`` → ``status,apikey,expiry,usage``), as ShareX's login button does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import USER_AGENT, FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import ensure_ok, multipart_form, send_body

PUUSH_URL = "https://puush.me"
API_AUTH = PUUSH_URL + "/api/auth"
API_UPLOAD = PUUSH_URL + "/api/up"
API_DELETE = PUUSH_URL + "/api/del"

ERRORS = {-1: "Authentication failure (check the API key)", -2: "Connection error", -3: "Checksum error",
          -4: "Insufficient account storage"}


@dataclass
class PuushSettings:
    api_key: str = setting("", label="API key", secret=True,
                           help="From https://puush.me/account/settings, or log in below to fetch it")
    email: str = setting("", label="Email", help="Only used to fetch the API key")
    password: str = setting("", label="Password", secret=True, help="Only used to fetch the API key")


def login(settings: PuushSettings, ctx: UploadContext) -> str:
    """Fetch the API key for email + password (``/api/auth``), store it in the settings and return it."""
    if not settings.email or not settings.password:
        raise UploaderError("Enter the puush email and password first")
    resp = ctx.post(API_AUTH, data={"e": settings.email, "p": settings.password, "z": USER_AGENT})
    text = ensure_ok(resp, "puush login").text.strip()
    values = text.split(",")
    try:
        status = int(values[0])
    except ValueError:
        status = -2
    if status < 0 or len(values) < 2 or not values[1]:
        raise UploaderError(f"puush login failed: {ERRORS.get(status, text)}", text)
    settings.api_key = values[1]
    ctx.save_settings()
    return settings.api_key


def delete_file(settings: PuushSettings, ctx: UploadContext, file_id: str) -> bool:
    resp = ctx.post(API_DELETE, data={"k": settings.api_key, "i": file_id, "z": USER_AGENT})
    first = ensure_ok(resp, "puush delete").text.strip().splitlines()[:1]
    try:
        return bool(first) and int(first[0]) >= 0
    except ValueError:
        return False


@register("file", "Puush", PuushSettings, title="puush", homepage=PUUSH_URL)
class PuushUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if not s.api_key and not (s.email and s.password):
            return ["puush API key is empty"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        if not self.settings.api_key:
            login(self.settings, self.ctx)
        body, ctype = multipart_form({"k": self.settings.api_key, "z": USER_AGENT}, "f", file_name, stream)
        resp = send_body(self.ctx, "POST", API_UPLOAD, body, ctype)
        text = ensure_ok(resp, "puush upload").text.strip()
        values = text.split(",")
        try:
            status = int(values[0])
        except ValueError:
            status = -2
        if status < 0:
            raise UploaderError(f"puush upload failed: {ERRORS.get(status, ERRORS[-2])}", text)
        if len(values) < 2 or not values[1]:
            raise UploaderError(f"puush upload: unexpected response {text[:200]}", text)
        return UploadResult(url=values[1], response=text)
