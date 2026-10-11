"""Polr, self-hosted (ShareX PolrURLShortener).

Polr 2 API: ``GET <instance>/api/v2/action/shorten?key=<key>&url=<url>[&is_secret=true]
[&custom_ending=<ending>]&response_type=json`` -> ``{"action": "shorten", "result": "<short url>"}``;
errors are ``{"error": "...", "status_code": N}`` with a 4xx status.

``api_hostname`` may be the instance root (``https://polr.example.com``) or, as in ShareX, the full
endpoint URL; ``/api/v2/action/shorten`` is appended when no ``/api`` path is present.
``use_api_v1`` talks to Polr 1.x instead: ``<instance>/api.php?apikey=<key>&action=shorten&url=<url>``,
plain-text reply (no secret/custom endings there).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, custom_value, plain_text, short_result


def _fix_prefix(host: str) -> str:
    host = host.strip()
    if host and "://" not in host:
        host = "https://" + host
    return host


@dataclass
class PolrSettings:
    api_hostname: str = setting("", label="Instance URL", help="e.g. https://polr.example.com")
    api_key: str = setting("", label="API key", secret=True)
    is_secret: bool = setting(False, label="Secret URL")
    custom_ending: str = setting("", label="Custom ending", help="Empty: random")
    ask_custom_ending: bool = setting(False, label="Ask for a custom ending each time")
    use_api_v1: bool = setting(False, label="Use API v1 (Polr 1.x)")


@register("shortener", "Polr", PolrSettings, title="Polr", homepage="https://polrproject.org")
class PolrShortener(URLShortener):
    settings: PolrSettings

    def validate(self) -> list[str]:
        problems = []
        if not self.settings.api_hostname.strip():
            problems.append("instance URL is missing")
        if not self.settings.api_key.strip():
            problems.append("API key is missing")
        return problems

    def endpoint(self) -> str:
        host = _fix_prefix(self.settings.api_hostname)
        if not host:
            raise UploaderError("Polr: instance URL is missing")
        if self.settings.use_api_v1:
            return host if host.rstrip("/").endswith(".php") else host.rstrip("/") + "/api.php"
        if "/api/" in host.split("://", 1)[1]:
            return host
        return host.rstrip("/") + "/api/v2/action/shorten"

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("Polr: no URL to shorten")
        key = self.settings.api_key.strip()
        if self.settings.use_api_v1:
            resp = self.ctx.get(self.endpoint(), params={"apikey": key, "action": "shorten", "url": url})
            return short_result(url, plain_text(resp, "Polr"), resp.text, "Polr")
        params = {"key": key, "url": url, "response_type": "json"}
        if self.settings.is_secret:
            params["is_secret"] = "true"
        ending = custom_value(self.ctx, "Polr custom ending", self.settings.custom_ending,
                              self.settings.ask_custom_ending)
        if ending:
            params["custom_ending"] = ending
        resp = self.ctx.get(self.endpoint(), params=params)
        data = api_json(resp, "Polr")
        short = data.get("result", "") if isinstance(data, dict) else ""
        return short_result(url, short if isinstance(short, str) else "", resp.text, "Polr")
