"""Plik (ShareX Plik.cs), self-hosted temporary file sharing — current HTTP API.

``POST <server>/upload`` (JSON options: ttl, oneShot, removable, comments, login/password; ``X-PlikToken`` for
an account token) → upload ID + upload token; ``POST <server>/file/<uploadID>`` (multipart ``file``, header
``X-UploadToken``) → file ID. URL: ``<downloadURL or server>/file/<uploadID>/<fileID>/<name>``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult, json_of
from ..registry import register, setting
from ._file_helpers import fix_prefix, multipart_body, read_all, url_encode

TTL_UNITS = ["Days", "Hours", "Minutes", "Never"]
_UNIT_SECONDS = {"Days": 86400, "Hours": 3600, "Minutes": 60}


@dataclass
class PlikSettings:
    url: str = setting("", label="Server URL", help="e.g. https://plik.example.com")
    api_key: str = setting("", label="Upload token", secret=True,
                           help="Optional: a token from your Plik account (sent as X-PlikToken)")
    is_secured: bool = setting(False, label="Password protect the upload")
    login: str = setting("", label="Login")
    password: str = setting("", label="Password", secret=True)
    removable: bool = setting(False, label="Removable")
    one_shot: bool = setting(False, label="One shot (deleted after the first download)")
    ttl_unit: str = setting("Minutes", label="Expiration unit", choices=TTL_UNITS)
    ttl: float = setting(30, label="Expire after")
    has_comment: bool = setting(False, label="Add a comment")
    comment: str = setting("", label="Comment", multiline=True)


def ttl_seconds(settings: PlikSettings) -> int:
    """Plik's ttl in seconds; -1 = never expire (ShareX TTLUnit 3)."""
    if settings.ttl_unit == "Never":
        return -1
    return round(float(settings.ttl) * _UNIT_SECONDS.get(settings.ttl_unit, 60))


@register("file", "Plik", PlikSettings, title="Plik", homepage="https://github.com/root-gg/plik")
class PlikUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.url.strip() else ["Plik server URL is empty"]

    @property
    def base(self) -> str:
        return fix_prefix(self.settings.url.strip().rstrip("/"))

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        headers = {"X-PlikToken": s.api_key} if s.api_key else {}
        options: dict = {"ttl": ttl_seconds(s), "removable": s.removable, "oneShot": s.one_shot}
        if s.has_comment and s.comment:
            options["comments"] = s.comment
        if s.is_secured:
            options["login"] = s.login
            options["password"] = s.password
        meta = json_of(self.ctx.post(f"{self.base}/upload", json=options, headers=headers), "Plik create upload")
        upload_id, token = meta.get("id"), meta.get("uploadToken")
        if not upload_id or not token:
            raise UploaderError(f"Plik create upload: unexpected response {meta}")
        body, ctype = multipart_body(None, "file", file_name, read_all(stream))
        resp = self.ctx.post(f"{self.base}/file/{upload_id}", data=self.ctx.progress_stream(body),
                             headers={**headers, "X-UploadToken": token, "Content-Type": ctype})
        info = json_of(resp, "Plik upload")
        file_id = info.get("id")
        if not file_id:
            raise UploaderError(f"Plik upload: no file ID in {info}", resp.text)
        download = (meta.get("downloadURL") or meta.get("downloadDomain") or self.base).rstrip("/")
        name = info.get("fileName") or file_name
        return UploadResult(url=f"{download}/file/{upload_id}/{file_id}/{url_encode(name)}", response=resp.text)


def test_connection(settings: PlikSettings, ctx: UploadContext) -> str:
    up = PlikUploader(settings, ctx)
    cfg = json_of(ctx.get(f"{up.base}/config"), "Plik config")
    size = cfg.get("maxFileSize", 0)
    limit = "unlimited" if size in (-1, 0) else f"{int(size) / 1024 ** 2:.0f} MiB"
    return f"Plik server reachable; max file size {limit}"
