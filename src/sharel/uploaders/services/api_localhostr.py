"""Hostr, formerly Localhostr (ShareX Hostr.cs).

``POST https://hostr.co/api/file`` (multipart ``file``) with HTTP basic auth (email + password) → 201 and the
file JSON (``id``, ``name``, ``href``, and for images ``direct: {"150x": ..., "970x": ...}``).

ShareX posted to ``api.hostr.co``; that host now answers 404 and the API is mounted at ``/api`` on the
main site (see the service's source, https://github.com/kudos/hostr, ``app.js``: ``app.route('/api', api)``).
The "direct URL" option returns ``https://hostr.co/file/<id>/<name>`` for images, with the 150px
thumbnail as the thumbnail URL, like ShareX.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from ..base import FileUploader, UploadResult
from ..registry import register, setting
from ._api_common import basic_auth_header, json_ok, multipart_form, send_body

API_URL = "https://hostr.co/api/file"
WEB_URL = "https://hostr.co"


@dataclass
class LocalhostrSettings:
    email: str = setting("", label="Email")
    password: str = setting("", label="Password", secret=True)
    direct_url: bool = setting(True, label="Use direct URL (images)",
                               help="Link to the image file itself instead of the Hostr page")


@register("file", "Localhostr", LocalhostrSettings, title="Hostr", homepage=WEB_URL)
class LocalhostrUploader(FileUploader):
    def validate(self) -> list[str]:
        if not self.settings.email or not self.settings.password:
            return ["Hostr email and password are required"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        body, ctype = multipart_form(None, "file", file_name, stream)
        resp = send_body(self.ctx, "POST", API_URL, body, ctype, headers=basic_auth_header(s.email, s.password))
        js = json_ok(resp, "Hostr upload")
        result = UploadResult(response=resp.text)
        direct = js.get("direct")
        if s.direct_url and isinstance(direct, dict) and js.get("id"):
            result.url = f"{WEB_URL}/file/{js['id']}/{quote(js.get('name') or file_name)}"
            result.thumbnail_url = direct.get("150x") or direct.get("direct_150x") or ""
        else:
            result.url = js.get("href", "")
        return result
