"""Uguu (ShareX Uguu.cs): temporary hosting at uguu.se, pomf-compatible API.

Current API (https://uguu.se/api): ``POST https://uguu.se/upload`` with ``files[]``, JSON response by default.
(ShareX still posts to the older ``upload.php?output=text``.)
"""
from __future__ import annotations

from typing import BinaryIO

from ..base import FileUploader, UploadResult
from ..registry import register
from .file_pomf import pomf_upload

UPLOAD_URL = "https://uguu.se/upload"


@register("file", "Uguu", None, title="Uguu", homepage="https://uguu.se/")
class UguuUploader(FileUploader):
    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        return pomf_upload(self.ctx, UPLOAD_URL, "", stream, file_name, "Uguu")
