"""s-ul (ShareX Sul.cs).

``POST https://s-ul.eu/api/v1/upload`` multipart (``wizard=true``, ``key`` = API key, ``client``, ``file``).
The JSON reply has ``protocol``, ``domain``, ``filename``, ``extension`` (URL = protocol + domain + "/" +
filename + extension) or ``error``. Deletion URL: ``https://s-ul.eu/delete.php?key=<key>&file=<filename>``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import urlencode

from ..base import FileUploader, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import error_message, multipart_form, send_body

BASE_URL = "https://s-ul.eu"
UPLOAD_URL = BASE_URL + "/api/v1/upload"
# The client id ShareX's built-in uploader sends; s-ul keys its native-client handling on it.
CLIENT = "sharex-native"


@dataclass
class SulSettings:
    api_key: str = setting("", label="API key", secret=True, help="From https://s-ul.eu/account/configurations")


@register("file", "Sul", SulSettings, title="s-ul", homepage=BASE_URL)
class SulUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.api_key else ["s-ul API key is empty"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        key = self.settings.api_key
        body, ctype = multipart_form({"wizard": "true", "key": key, "client": CLIENT}, "file", file_name, stream)
        resp = send_body(self.ctx, "POST", UPLOAD_URL, body, ctype)
        try:
            js = resp.json()
        except ValueError:
            raise UploaderError(error_message(resp, "s-ul upload"), resp.text) from None
        if not isinstance(js, dict):
            raise UploaderError(error_message(resp, "s-ul upload"), resp.text)
        error = js.get("error") or (js.get("reason") if js.get("success") is False else "")
        if error or not js.get("protocol"):
            raise UploaderError(f"s-ul upload failed: {error or 'unexpected response, contact s-ul support'}",
                                resp.text)
        name = js.get("filename", "")
        url = f"{js['protocol']}{js.get('domain', '')}/{name}{js.get('extension', '')}"
        deletion = f"{BASE_URL}/delete.php?" + urlencode({"key": key, "file": name})
        return UploadResult(url=url, deletion_url=deletion, response=resp.text)
