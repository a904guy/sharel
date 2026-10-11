"""Chevereto (ShareX ImageUploaders/Chevereto.cs) against the Chevereto API V1.1 (``/api/1/upload``).

The instance URL may be the site root (``https://img.example.com``) or the full upload endpoint; the
API key goes in the ``X-API-Key`` header as the current docs prefer (https://v4-docs.chevereto.com/api/1/file-upload.html).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import ImageUploader, UploaderError, UploadResult, json_of
from ..registry import register, setting

UPLOAD_PATH = "/api/1/upload"


@dataclass
class CheveretoSettings:
    upload_url: str = setting("", label="Upload URL",
                              help="Your Chevereto site, e.g. https://img.example.com (or its /api/1/upload URL).")
    api_key: str = setting("", label="API key", secret=True, help="From your account's API settings page.")
    direct_url: bool = setting(True, label="Use direct URL", help="Link to the image file instead of the viewer page.")
    album_id: str = setting("", label="Album ID", help="Encoded album id; the album must belong to the API key user.")
    title: str = setting("", label="Title")
    description: str = setting("", label="Description")
    tags: str = setting("", label="Tags", help="Comma-separated.")
    category_id: str = setting("", label="Category ID")
    width: str = setting("", label="Resize width", help="Target width in pixels (height follows).")
    expiration: str = setting("", label="Auto delete", help="ISO 8601 interval, e.g. PT5M or P3D. Empty keeps forever.")
    nsfw: bool = setting(False, label="NSFW")


def endpoint(upload_url: str) -> str:
    url = upload_url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    url = url.rstrip("/")
    if "/api/1/upload" not in url:
        url += UPLOAD_PATH
    return url


@register("image", "Chevereto", CheveretoSettings, title="Chevereto", homepage="https://chevereto.com")
class CheveretoUploader(ImageUploader):
    settings: CheveretoSettings

    def validate(self) -> list[str]:
        problems = []
        if not self.settings.upload_url:
            problems.append("Upload URL is required")
        if not self.settings.api_key:
            problems.append("API key is required")
        return problems

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        fields = {"format": "json"}
        for name in ("album_id", "title", "description", "tags", "category_id", "width", "expiration"):
            value = getattr(s, name)
            if value:
                fields[name] = str(value)
        if s.nsfw:
            fields["nsfw"] = "1"
        resp = self.ctx.post(endpoint(s.upload_url), headers={"X-API-Key": s.api_key}, data=fields,
                             files={"source": (file_name, self.ctx.progress_stream(stream))})
        try:
            body = json_of(resp, "Chevereto upload")
        except UploaderError as e:
            # Chevereto errors are JSON too: {"status_code":400,"error":{"message":...}}
            try:
                msg = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                raise e from None
            raise UploaderError(f"Chevereto upload failed: {msg}", resp.text) from None
        image = body.get("image") if isinstance(body, dict) else None
        if not image:
            raise UploaderError(f"Chevereto upload failed: {resp.text[:300]}", resp.text)
        result = UploadResult(response=resp.text)
        result.url = image.get("url") if s.direct_url else image.get("url_viewer") or image.get("url")
        result.thumbnail_url = (image.get("thumb") or {}).get("url", "")
        result.deletion_url = image.get("delete_url", "") or ""
        return result
