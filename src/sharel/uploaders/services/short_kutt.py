"""Kutt, kutt.it or self-hosted (ShareX KuttURLShortener).

``POST <host>/api/v2/links`` with ``X-API-KEY: <key>`` and JSON ``{"target", "description",
"expire_in", "password", "customurl", "reuse", "domain"}`` (only ``target`` is required; the others are
sent when set). 201 returns the link object whose ``link`` is the full short URL (200 when ``reuse``
returned an existing one); errors are ``{"error": "..."}`` with a 4xx status. ``expire_in`` is a
duration such as ``2 minutes``, ``8h`` or ``42 days``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, custom_value, short_result

DEFAULT_HOST = "https://kutt.it"


@dataclass
class KuttSettings:
    host: str = setting(DEFAULT_HOST, label="Instance URL")
    api_key: str = setting("", label="API key", secret=True, help="Settings > API on your Kutt instance")
    password: str = setting("", label="Link password", secret=True, help="Visitors must enter it (3-64 chars)")
    reuse: bool = setting(False, label="Reuse existing link for the same target")
    domain: str = setting("", label="Domain", help="A custom domain added to your account (empty: default)")
    custom_url: str = setting("", label="Custom URL", help="Custom ending (empty: random)")
    ask_custom_url: bool = setting(False, label="Ask for a custom URL each time")
    expire_in: str = setting("", label="Expire in", help="e.g. 8h, 42 days (empty: never)")
    description: str = setting("", label="Description")


@register("shortener", "Kutt", KuttSettings, title="Kutt", homepage="https://kutt.it")
class KuttShortener(URLShortener):
    settings: KuttSettings

    def validate(self) -> list[str]:
        return [] if self.settings.api_key.strip() else ["API key is missing"]

    def links_url(self) -> str:
        host = self.settings.host.strip() or DEFAULT_HOST
        if "://" not in host:
            host = "https://" + host
        return host.rstrip("/") + "/api/v2/links"

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("Kutt: no URL to shorten")
        s = self.settings
        body: dict[str, object] = {"target": url}
        if s.password:
            body["password"] = s.password
        if s.reuse:
            body["reuse"] = True
        if s.domain.strip():
            body["domain"] = s.domain.strip()
        custom = custom_value(self.ctx, "Kutt custom URL", s.custom_url, s.ask_custom_url)
        if custom:
            body["customurl"] = custom
        if s.expire_in.strip():
            body["expire_in"] = s.expire_in.strip()
        if s.description.strip():
            body["description"] = s.description.strip()
        resp = self.ctx.post(self.links_url(), json=body,
                             headers={"X-API-KEY": s.api_key.strip(), "Accept": "application/json"})
        data = api_json(resp, "Kutt")
        return short_result(url, data.get("link", "") if isinstance(data, dict) else "", resp.text, "Kutt")
