"""YOURLS, self-hosted (ShareX YourlsURLShortener).

``POST <api_url>`` (``.../yourls-api.php``) with ``action=shorturl``, ``url``, optional ``keyword`` and
``title``, ``format=json`` and one of:

* ``signature=<token>`` (the passwordless API token from the YOURLS Tools page);
* ``timestamp=<unix>&signature=sha256(timestamp + token)&hash=sha256`` when ``time_limited_signature``
  is on (YOURLS' time-limited signature);
* ``username`` + ``password``.

The JSON reply carries ``status`` ("success"/"fail"), ``code`` and ``shorturl``. When the long URL is
already known (``code: "error:url"``, with unique URLs on) YOURLS still returns the existing
``shorturl``, which is a usable result.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import api_json, custom_value, error_text, short_result


@dataclass
class YourlsSettings:
    api_url: str = setting("http://yoursite.com/yourls-api.php", label="API URL")
    signature: str = setting("", label="Signature token", secret=True, help="Tools > Secure passwordless API call")
    time_limited_signature: bool = setting(False, label="Use time-limited signature")
    username: str = setting("", label="Username", help="Used when no signature token is set")
    password: str = setting("", label="Password", secret=True)
    keyword: str = setting("", label="Keyword", help="Custom short URL keyword (empty: automatic)")
    ask_keyword: bool = setting(False, label="Ask for a keyword each time")
    title: str = setting("", label="Title", help="Empty: YOURLS fetches the page title")


@register("shortener", "YOURLS", YourlsSettings, title="YOURLS", homepage="https://yourls.org")
class YourlsShortener(URLShortener):
    settings: YourlsSettings

    def validate(self) -> list[str]:
        s = self.settings
        problems = []
        if not s.api_url.strip():
            problems.append("API URL is missing")
        if not s.signature.strip() and not (s.username.strip() and s.password):
            problems.append("enter a signature token, or a username and password")
        return problems

    def _auth(self) -> dict[str, str]:
        s = self.settings
        token = s.signature.strip()
        if token:
            if s.time_limited_signature:
                ts = str(int(time.time()))
                return {"timestamp": ts, "signature": hashlib.sha256((ts + token).encode()).hexdigest(),
                        "hash": "sha256"}
            return {"signature": token}
        if s.username.strip() and s.password:
            return {"username": s.username.strip(), "password": s.password}
        raise UploaderError("YOURLS: credentials are missing (signature token or username/password)")

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("YOURLS: no URL to shorten")
        data = self._auth()
        data.update({"action": "shorturl", "url": url, "format": "json"})
        keyword = custom_value(self.ctx, "YOURLS keyword", self.settings.keyword, self.settings.ask_keyword)
        if keyword:
            data["keyword"] = keyword
        if self.settings.title.strip():
            data["title"] = self.settings.title.strip()
        resp = self.ctx.post(self.settings.api_url.strip(), data=data, headers={"Accept": "application/json"})
        body = api_json(resp, "YOURLS")
        if not isinstance(body, dict):
            raise UploaderError(f"YOURLS: unexpected response {resp.text[:300]}", resp.text)
        short = body.get("shorturl") or ""
        if not short:
            msg = error_text(body) or body.get("code") or "no short URL returned"
            raise UploaderError(f"YOURLS: {msg}", resp.text)
        return short_result(url, short, resp.text, "YOURLS")
