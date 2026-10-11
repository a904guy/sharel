"""Streamable (ShareX Streamable.cs).

1. ``POST https://api.streamable.com/upload`` multipart ``file`` with HTTP basic auth (email + password; the
   API rejects unauthenticated uploads today, but ShareX sent them when no account was set, so does this) →
   ``{"shortcode": "abc12", "status": 1}``.
2. Poll ``GET https://api.streamable.com/videos/<shortcode>`` until ``status`` is 2 (ready); > 2 is an
   error with ``message``. ``percent`` reports transcoding progress.
3. URL: the video's ``url`` (scheme-less, so ``https://`` is prefixed), i.e. ``https://streamable.com/<code>``;
   with "use direct URL" the transcoded MP4 ``files.mp4.url`` instead.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import basic_auth_header, json_ok, multipart_form, send_body

API = "https://api.streamable.com"
POLL_INTERVAL = 1.0
MAX_WAIT = 30 * 60.0  # transcoding is normally a minute or two; give up after half an hour

_sleep = time.sleep
_monotonic = time.monotonic


@dataclass
class StreamableSettings:
    username: str = setting("", label="Email", help="Your Streamable account email")
    password: str = setting("", label="Password", secret=True)
    use_direct_url: bool = setting(False, label="Use direct (raw MP4) URL")


def force_prefix(url: str, prefix: str = "https://") -> str:
    """ShareX URLHelpers.ForcePrefix: replace or add the scheme."""
    if not url:
        return url
    _scheme, sep, rest = url.partition("://")
    if sep:
        return prefix + rest
    if url.startswith("//"):
        return prefix + url[2:]
    return prefix + url


@register("file", "Streamable", StreamableSettings, title="Streamable", homepage="https://streamable.com")
class StreamableUploader(FileUploader):
    def validate(self) -> list[str]:
        if not self.settings.username or not self.settings.password:
            return ["Streamable requires an account: enter the email and password"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        headers = basic_auth_header(s.username, s.password) if s.username and s.password else {}
        body, ctype = multipart_form(None, "file", file_name, stream)
        resp = send_body(self.ctx, "POST", f"{API}/upload", body, ctype, headers=headers)
        if resp.status_code == 401:
            raise UploaderError("Streamable upload failed: login required or wrong email/password", resp.text)
        js = json_ok(resp, "Streamable upload")
        code = js.get("shortcode") or js.get("Shortcode")
        if not code:
            raise UploaderError("Streamable could not create the video", resp.text)
        return self._wait_for_video(code, headers)

    def _wait_for_video(self, code: str, headers: dict[str, str]) -> UploadResult:
        deadline = _monotonic() + MAX_WAIT
        while True:
            self.ctx.check_cancelled()
            resp = self.ctx.get(f"{API}/videos/{code}", headers=headers)
            js = json_ok(resp, "Streamable status")
            status = int(js.get("status") or 0)
            if status > 2:
                raise UploaderError(f"Streamable processing failed: {js.get('message') or status}", resp.text)
            if status == 2:
                self.ctx.report(100, 100)
                mp4 = (js.get("files") or {}).get("mp4") or {}
                if self.settings.use_direct_url and mp4.get("url"):
                    url = force_prefix(mp4["url"])
                else:
                    url = force_prefix(js.get("url") or f"streamable.com/{code}")
                return UploadResult(url=url, thumbnail_url=force_prefix(js.get("thumbnail_url") or ""),
                                    response=resp.text)
            self.ctx.report(int(js.get("percent") or 0), 100)
            if _monotonic() > deadline:
                raise UploaderError(f"Streamable is still processing {code}; gave up waiting", resp.text)
            _sleep(POLL_INTERVAL)
