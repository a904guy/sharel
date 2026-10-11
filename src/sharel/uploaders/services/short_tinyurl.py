"""TinyURL (ShareX TinyURLShortener).

With an API token (tinyurl.com > Account > Developer > API), uses API v1:
``POST https://api.tinyurl.com/create`` with ``Authorization: Bearer <token>`` and a JSON body
``{"url", "domain", "alias", "tags", "expires_at", "description"}``; the reply is the usual TinyURL
envelope ``{"data": {"tiny_url": ...}, "code": 0, "errors": []}``.

Without a token it falls back to the free, keyless endpoint ShareX always used,
``GET https://tinyurl.com/api-create.php?url=<url>[&alias=<alias>]``, which answers with the short URL
as plain text (or "Error" with a 4xx status). The free endpoint cannot pick a domain, tags,
expiry or description.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, custom_value, plain_text, short_result

API_CREATE_URL = "https://api.tinyurl.com/create"
FREE_CREATE_URL = "https://tinyurl.com/api-create.php"


@dataclass
class TinyURLSettings:
    api_token: str = setting("", label="API token", secret=True,
                             help="Empty: use the free api-create.php endpoint (alias only)")
    domain: str = setting("tinyurl.com", label="Domain", help="tinyurl.com or a branded domain on your account")
    alias: str = setting("", label="Alias", help="Custom ending (empty: random)")
    ask_alias: bool = setting(False, label="Ask for an alias each time")
    tags: str = setting("", label="Tags", help="Comma separated (API token only)")
    expires_at: str = setting("", label="Expires at", help="e.g. 2027-01-01 00:00:00 (API token only, paid plans)")
    description: str = setting("", label="Description", help="API token only")


@register("shortener", "TINYURL", TinyURLSettings, title="TinyURL", homepage="https://tinyurl.com")
class TinyURLShortener(URLShortener):
    settings: TinyURLSettings

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("TinyURL: no URL to shorten")
        alias = custom_value(self.ctx, "TinyURL alias", self.settings.alias, self.settings.ask_alias)
        token = self.settings.api_token.strip()
        if not token:
            params = {"url": url}
            if alias:
                params["alias"] = alias
            resp = self.ctx.get(FREE_CREATE_URL, params=params)
            return short_result(url, plain_text(resp, "TinyURL"), resp.text, "TinyURL")

        body: dict[str, object] = {"url": url}
        if self.settings.domain.strip():
            body["domain"] = self.settings.domain.strip()
        if alias:
            body["alias"] = alias
        tags = [t.strip() for t in self.settings.tags.split(",") if t.strip()]
        if tags:
            body["tags"] = ",".join(tags)
        if self.settings.expires_at.strip():
            body["expires_at"] = self.settings.expires_at.strip()
        if self.settings.description.strip():
            body["description"] = self.settings.description.strip()
        resp = self.ctx.post(API_CREATE_URL, json=body,
                             headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        data = api_json(resp, "TinyURL")
        if not isinstance(data, dict):
            raise UploaderError(f"TinyURL: unexpected response {resp.text[:300]}", resp.text)
        if data.get("errors"):
            raise UploaderError("TinyURL: " + "; ".join(map(str, data["errors"])), resp.text)
        info = data.get("data") if isinstance(data.get("data"), dict) else {}
        return short_result(url, info.get("tiny_url", ""), resp.text, "TinyURL")
