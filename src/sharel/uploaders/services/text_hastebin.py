"""Hastebin (ShareX TextUploaders/Hastebin.cs).

``POST <server>/documents`` with the text as the body answers ``{"key": "..."}``. The public service
(https://www.toptal.com/developers/hastebin/documentation) needs ``Authorization: Bearer <token>``
(generate one after signing in with GitHub) and shows documents at ``/share/<key>``; a self-hosted
haste-server shows them at ``/<key>.<ext>``. Raw text is at ``/raw/<key>`` on both.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..base import TextUploader, UploaderError, UploadResult, json_of
from ..registry import register, setting

DEFAULT_SERVER = "https://hastebin.com"
TOPTAL_HOSTS = {"hastebin.com", "www.hastebin.com"}


@dataclass
class HastebinSettings:
    custom_domain: str = setting("", label="Server URL", help=f"Empty = {DEFAULT_SERVER} (needs a token).")
    token: str = setting("", label="API token", secret=True,
                         help="Required by hastebin.com: toptal.com/developers/hastebin > generate token.")
    syntax_highlighting: str = setting("", label="Syntax highlighting", help="Extension added to the URL, e.g. py.")
    use_file_extension: bool = setting(True, label="Use the file's extension for highlighting")
    raw_url: bool = setting(False, label="Use raw URL")


def _server(settings: HastebinSettings) -> str:
    url = (settings.custom_domain or DEFAULT_SERVER).strip().rstrip("/")
    return url if "://" in url else "https://" + url


def _is_toptal(server: str) -> bool:
    return (urlsplit(server).hostname or "").lower() in TOPTAL_HOSTS


@register("text", "Hastebin", HastebinSettings, title="Hastebin", homepage="https://www.toptal.com/developers/hastebin")
class HastebinUploader(TextUploader):
    settings: HastebinSettings

    def validate(self) -> list[str]:
        if _is_toptal(_server(self.settings)) and not self.settings.token:
            return ["hastebin.com needs an API token (toptal.com/developers/hastebin)"]
        return []

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        server = _server(s)
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if s.token:
            headers["Authorization"] = f"Bearer {s.token}"
        resp = self.ctx.post(f"{server}/documents", data=text.encode("utf-8"), headers=headers)
        body = json_of(resp, "Hastebin upload")
        key = body.get("key") if isinstance(body, dict) else None
        if not key:
            raise UploaderError(f"Hastebin upload failed: {resp.text[:300]}", resp.text)
        if s.raw_url:
            return UploadResult(url=f"{server}/raw/{key}", response=resp.text)
        syntax = s.syntax_highlighting
        if s.use_file_extension:
            ext = Path(file_name).suffix.lstrip(".").lower()
            if ext and ext != "txt":
                syntax = ext
        url = f"{server}/share/{key}" if _is_toptal(server) else f"{server}/{key}"
        if syntax:
            url += "." + syntax
        return UploadResult(url=url, response=resp.text)
