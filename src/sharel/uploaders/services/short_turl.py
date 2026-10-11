"""turl.ca (ShareX TurlURLShortener).

turl.ca relaunched with a new REST API; ShareX's old ``http://turl.ca/api.php`` now returns 404.
Current API (https://turl.ca/api/v1/openapi.json): ``POST https://turl.ca/api/v1/links`` with JSON
``{"url": ..., "mode": "redirect"|"cite"}`` and ``Authorization: Bearer <key>``; 201 returns
``{"short_url", "preview_url", "resolves", "scan_state", ...}``, errors are ``{"error": ...}``.
Keys (``pbca_live_...``) are minted on pastebin.ca for the turl.ca audience. Anonymous creates need a
browser Turnstile token, so an API key is required here.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, short_result

TURL_LINKS_URL = "https://turl.ca/api/v1/links"


@dataclass
class TurlSettings:
    api_key: str = setting("", label="API key", secret=True,
                           help="Create one on pastebin.ca (account > API keys, audience turl.ca)")
    mode: str = setting("redirect", label="Link mode", choices=["redirect", "cite"],
                        help="cite keeps citation evidence and always shows a preview page")


@register("shortener", "TURL", TurlSettings, title="turl.ca", homepage="https://turl.ca")
class TurlShortener(URLShortener):
    settings: TurlSettings

    def validate(self) -> list[str]:
        return [] if self.settings.api_key.strip() else ["an API key is required (anonymous API use is blocked)"]

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("turl.ca: no URL to shorten")
        body = {"url": url, "mode": self.settings.mode if self.settings.mode in ("redirect", "cite") else "redirect"}
        resp = self.ctx.post(TURL_LINKS_URL, json=body,
                             headers={"Authorization": f"Bearer {self.settings.api_key.strip()}",
                                      "Accept": "application/json"})
        data = api_json(resp, "turl.ca")
        return short_result(url, data.get("short_url", "") if isinstance(data, dict) else "", resp.text, "turl.ca")
