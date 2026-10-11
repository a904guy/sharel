"""Zero Width Shortener, zws.im or self-hosted (ShareX ZeroWidthURLShortener).

``POST <request_url>`` (default ``https://api.zws.im``) with JSON ``{"url": ...}`` and, when a token is
set, ``Authorization: Bearer <token>``. 201 returns ``{"short": "<zero-width code>", "url": "<full short
URL>"}`` (https://api.zws.im/openapi.json). As in ShareX, ``url`` is used when present, otherwise
``https://zws.im/`` + ``short``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, short_result

DEFAULT_API = "https://api.zws.im"
PUBLIC_HOST = "https://zws.im"


@dataclass
class ZeroWidthSettings:
    request_url: str = setting(DEFAULT_API, label="API URL", help="Change for a self-hosted instance")
    token: str = setting("", label="Token", secret=True, help="Only for instances that require one")


@register("shortener", "ZeroWidthShortener", ZeroWidthSettings, title="Zero Width Shortener",
          homepage="https://zws.im")
class ZeroWidthShortener(URLShortener):
    settings: ZeroWidthSettings

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("Zero Width Shortener: no URL to shorten")
        endpoint = self.settings.request_url.strip() or DEFAULT_API
        headers = {"Accept": "application/json"}
        if self.settings.token.strip():
            headers["Authorization"] = f"Bearer {self.settings.token.strip()}"
        resp = self.ctx.post(endpoint, json={"url": url}, headers=headers)
        data = api_json(resp, "Zero Width Shortener")
        if not isinstance(data, dict):
            raise UploaderError(f"Zero Width Shortener: unexpected response {resp.text[:300]}", resp.text)
        short = data.get("url") or ""
        if not short and data.get("short"):
            short = PUBLIC_HOST + "/" + str(data["short"]).lstrip("/")
        return short_result(url, short, resp.text, "Zero Width Shortener")
