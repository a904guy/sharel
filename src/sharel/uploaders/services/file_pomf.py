"""Pomf-compatible hosts (ShareX Pomf.cs + PomfUploader.cs): multipart ``files[]`` to the instance's upload URL,
JSON ``{"success": true, "files": [{"url": ...}]}``; a relative URL is joined to the result URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._file_helpers import combine_url, fix_prefix, has_prefix, multipart_body, read_all

# Instances checked alive at the time of writing (see docs/featuremap/uploaders_file.md).
KNOWN_INSTANCES = ["https://pomf.lain.la/upload.php", "https://uguu.se/upload"]


@dataclass
class PomfSettings:
    upload_url: str = setting("https://pomf.lain.la/upload.php", label="Upload URL",
                              help="Any pomf-compatible instance, e.g. " + ", ".join(KNOWN_INSTANCES))
    result_url: str = setting("", label="Result URL", help="Prefix for instances that return a bare file name")


def pomf_upload(ctx: UploadContext, upload_url: str, result_url: str, stream: BinaryIO, file_name: str,
                what: str = "Pomf") -> UploadResult:
    body, ctype = multipart_body(None, "files[]", file_name, read_all(stream))
    resp = ctx.post(upload_url, data=ctx.progress_stream(body), headers={"Content-Type": ctype,
                                                                         "Accept": "application/json"})
    try:
        data = resp.json()
    except ValueError as e:
        raise UploaderError(f"{what} upload failed: HTTP {resp.status_code}: {resp.text[:300]}", resp.text) from e
    if not resp.ok or not data.get("success") or not data.get("files"):
        detail = data.get("description") or data.get("error") or resp.reason
        raise UploaderError(f"{what} upload failed: {detail}", resp.text)
    url = data["files"][0].get("url", "")
    if url and not has_prefix(url) and result_url:
        url = combine_url(fix_prefix(result_url), url)
    return UploadResult(url=url, response=resp.text)


@register("file", "Pomf", PomfSettings, title="Pomf", homepage="https://github.com/pomf/pomf")
class PomfUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.upload_url.strip() else ["upload URL is empty"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        return pomf_upload(self.ctx, self.settings.upload_url.strip(), self.settings.result_url, stream, file_name)
