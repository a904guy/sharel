"""transfer.sh (ShareX Transfersh.cs) — any instance of the open-source server.

``PUT <server>/<file name>`` with the raw body (the documented ``curl --upload-file`` call); the response body is
the download URL and the ``X-Url-Delete`` header the deletion URL. Optional ``Max-Days`` / ``Max-Downloads``
headers, and server-side encryption with ``X-Encrypt-Password``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploaderError, UploadResult, check_response
from ..registry import register, setting
from ._file_helpers import fix_prefix, mime_type, url_encode


@dataclass
class TransfershSettings:
    url: str = setting("https://transfer.sh", label="Server URL",
                       help="transfer.sh or your own instance (github.com/dutchcoders/transfer.sh)")
    max_days: int = setting(0, label="Max days", help="0 = server default")
    max_downloads: int = setting(0, label="Max downloads", help="0 = unlimited")
    encrypt_password: str = setting("", label="Encryption password", secret=True,
                                    help="Optional server-side AES-256 encryption (X-Encrypt-Password)")
    basic_auth_user: str = setting("", label="HTTP auth user", help="For instances behind basic auth")
    basic_auth_password: str = setting("", label="HTTP auth password", secret=True)


@register("file", "Transfersh", TransfershSettings, title="transfer.sh", homepage="https://github.com/dutchcoders/transfer.sh")
class TransfershUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.url.strip() else ["server URL is empty"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        headers = {"Content-Type": mime_type(file_name)}
        if s.max_days > 0:
            headers["Max-Days"] = str(s.max_days)
        if s.max_downloads > 0:
            headers["Max-Downloads"] = str(s.max_downloads)
        if s.encrypt_password:
            headers["X-Encrypt-Password"] = s.encrypt_password
        auth = (s.basic_auth_user, s.basic_auth_password) if s.basic_auth_user else None
        url = f"{fix_prefix(s.url.strip().rstrip('/'))}/{url_encode(file_name)}"
        resp = self.ctx.put(url, data=self.ctx.progress_stream(stream), headers=headers, auth=auth)
        text = check_response(resp, "transfer.sh upload").strip()
        if not text.startswith("http"):
            raise UploaderError(f"transfer.sh returned no URL: {text[:200]}", text)
        return UploadResult(url=text, deletion_url=resp.headers.get("X-Url-Delete", ""), response=text)
