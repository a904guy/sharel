"""ImageShack (ShareX ImageUploaders/ImageShackUploader.cs) against API v2 (https://api.imageshack.com/v2/).

Every non-GET call needs an ImageShack API key (ShareX ships its own; request one at
https://imageshack.com/contact/api) and uploads need an ``auth_token``. ``login`` exchanges the
username and password for one; ImageShack now sometimes demands a captcha on that endpoint, in which
case the token can be pasted in directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import ImageUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting

API = "https://api.imageshack.com/v2/"
LOGIN_URL = API + "user/login"
UPLOAD_URL = API + "images"


@dataclass
class ImageShackSettings:
    api_key: str = setting("", label="API key", secret=True, help="Request one at https://imageshack.com/contact/api")
    username: str = setting("", label="Username or email")
    password: str = setting("", label="Password", secret=True)
    auth_token: str = setting("", label="Auth token", secret=True,
                              help="Filled by Authorize (login); can also be pasted if login asks for a captcha.")
    is_public: bool = setting(False, label="Public uploads")
    thumbnail_width: int = setting(256, label="Thumbnail width")
    thumbnail_height: int = setting(0, label="Thumbnail height", help="0 keeps the aspect ratio.")


def _error(body: dict) -> str:
    err = body.get("error") or {}
    if isinstance(err, dict):
        return f"Error message: {err.get('error_message')}\nError code: {err.get('error_code')}"
    return str(err)


def login(settings: ImageShackSettings, ctx: UploadContext) -> str:
    """POST user/login with ``user``/``password``; stores and returns the auth token."""
    if not settings.username or not settings.password:
        raise UploaderError("Enter the ImageShack username and password first")
    resp = ctx.post(LOGIN_URL, files={"user": (None, settings.username), "password": (None, settings.password),
                                      **({"api_key": (None, settings.api_key)} if settings.api_key else {})})
    try:
        body = resp.json()
    except ValueError as e:
        raise UploaderError(f"ImageShack login failed: HTTP {resp.status_code}: {resp.text[:300]}", resp.text) from e
    result = body.get("result") if isinstance(body, dict) else None
    if not (isinstance(result, dict) and result.get("auth_token")):
        message = _error(body) if isinstance(body, dict) else str(body)
        if "capcha" in message.lower() or "captcha" in message.lower():
            message += ("\nImageShack wants a captcha for this login: sign in at imageshack.com and paste "
                        "your auth token into the Auth token field instead.")
        raise UploaderError(f"ImageShack login failed: {message}", resp.text)
    settings.auth_token = result["auth_token"]
    ctx.save_settings()
    return settings.auth_token


def authorize(settings: ImageShackSettings, ctx: UploadContext) -> str:
    return login(settings, ctx)


@register("image", "ImageShack", ImageShackSettings, title="ImageShack", homepage="https://imageshack.com")
class ImageShackUploader(ImageUploader):
    settings: ImageShackSettings

    def validate(self) -> list[str]:
        problems = []
        if not self.settings.api_key:
            problems.append("API key is required")
        if not self.settings.auth_token and not (self.settings.username and self.settings.password):
            problems.append("Log in (or paste an auth token) in Destination settings")
        return problems

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        if not s.auth_token:
            login(s, self.ctx)
        fields = {"api_key": s.api_key, "auth_token": s.auth_token, "public": "y" if s.is_public else "n"}
        resp = self.ctx.post(UPLOAD_URL, data=fields, files={"file": (file_name, self.ctx.progress_stream(stream))})
        text = resp.text
        try:
            body = resp.json()
        except ValueError as e:
            raise UploaderError(f"ImageShack upload failed: HTTP {resp.status_code}: {text[:300]}", text) from e
        if not (isinstance(body, dict) and body.get("success")):
            raise UploaderError(_error(body) if isinstance(body, dict) else str(body), text)
        images = (body.get("result") or {}).get("images") or []
        if not images:
            raise UploaderError("ImageShack upload failed: no image in response", text)
        image = images[0]
        result = UploadResult(response=text)
        result.url = f"https://imagizer.imageshack.com/a/img{image['server']}/{image['bucket']}/{image['filename']}"
        result.thumbnail_url = (f"https://imagizer.imageshack.us/v2/{s.thumbnail_width}x{s.thumbnail_height}q90/"
                                f"{image['server']}/{image['filename']}")
        return result
