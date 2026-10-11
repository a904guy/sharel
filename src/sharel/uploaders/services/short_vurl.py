"""VURL (ShareX VURLShortener).

``GET https://vurl.com/api.php?url=<url>`` returns the short URL as plain text, or ``Invalid URL``
(unchanged from ShareX, now over https).
"""
from __future__ import annotations

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register
from .short_common import plain_text, short_result

VURL_API_URL = "https://vurl.com/api.php"


@register("shortener", "VURL", None, title="VURL", homepage="https://vurl.com")
class VurlShortener(URLShortener):
    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("VURL: no URL to shorten")
        resp = self.ctx.get(VURL_API_URL, params={"url": url})
        text = plain_text(resp, "VURL")
        if text == "Invalid URL":
            raise UploaderError("VURL: Invalid URL", resp.text)
        return short_result(url, text, resp.text, "VURL")
