"""img.fish (ShareX ImgFish.cs; API: https://img.fish/api).

``POST https://img.fish/up`` multipart (``file``, optional ``length`` = file-ID length 8–32) with
``Accept: application/json`` (otherwise the reply is HTML) and an optional ``x-api-key`` header to tie the
upload to an account. The JSON has ``link`` (public URL) and ``destroy`` (deletion URL), or ``error``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import error_message, multipart_form, send_body

UPLOAD_URL = "https://img.fish/up"
MIN_FILE_ID_LENGTH = 8
MAX_FILE_ID_LENGTH = 32


@dataclass
class ImgFishSettings:
    api_key: str = setting("", label="API key", secret=True,
                           help="Optional: from img.fish Profile & settings; empty uploads anonymously")
    file_id_length: int = setting(MIN_FILE_ID_LENGTH, label="File ID length",
                                  help=f"{MIN_FILE_ID_LENGTH}–{MAX_FILE_ID_LENGTH} characters")


@register("file", "ImgFish", ImgFishSettings, title="img.fish", homepage="https://img.fish")
class ImgFishUploader(FileUploader):
    def validate(self) -> list[str]:
        n = self.settings.file_id_length
        if not MIN_FILE_ID_LENGTH <= n <= MAX_FILE_ID_LENGTH:
            return [f"File ID length must be {MIN_FILE_ID_LENGTH}–{MAX_FILE_ID_LENGTH}"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        length = min(max(int(s.file_id_length), MIN_FILE_ID_LENGTH), MAX_FILE_ID_LENGTH)
        headers = {"Accept": "application/json"}
        if s.api_key.strip():
            headers["x-api-key"] = s.api_key.strip()
        body, ctype = multipart_form({"length": length}, "file", file_name, stream)
        resp = send_body(self.ctx, "POST", UPLOAD_URL, body, ctype, headers=headers)
        try:
            js = resp.json()
        except ValueError:
            js = None
        if not isinstance(js, dict):
            raise UploaderError(error_message(resp, "img.fish upload"), resp.text)
        if js.get("error"):
            raise UploaderError(f"img.fish upload failed: {js['error']}", resp.text)
        if not 200 <= resp.status_code < 300 or not js.get("link"):
            raise UploaderError(error_message(resp, "img.fish upload"), resp.text)
        return UploadResult(url=js["link"], deletion_url=js.get("destroy") or "", response=resp.text)
