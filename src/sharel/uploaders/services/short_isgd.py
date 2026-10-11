"""is.gd and v.gd (ShareX IsgdURLShortener / VgdURLShortener).

``GET https://is.gd/create.php?format=json&url=<url>[&shorturl=<custom>][&logstats=1]`` (v.gd is the
same API on ``https://v.gd/create.php``). Success is ``{"shorturl": "..."}``; failure is
``{"errorcode": N, "errormessage": "..."}`` (with HTTP 200 or 4xx/5xx), where 1 = bad long URL,
2 = bad/taken custom short URL, 3 = rate limited, 4 = other (e.g. maintenance). See
https://is.gd/apishorteningreference.php. ShareX treated a response starting with "Error:" as a
failure; the JSON error object is the same signal in structured form.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..base import UploaderError, UploadResult, URLShortener
from ..registry import register, setting
from .short_common import custom_value, short_result

ERROR_CODES = {
    1: "there is a problem with the long URL",
    2: "the custom short URL is invalid or already taken",
    3: "rate limit exceeded, wait a minute and try again",
    4: "service error (is.gd/v.gd may be under maintenance)",
}


@dataclass
class IsgdSettings:
    custom_short_url: str = setting("", label="Custom short URL",
                                    help="5-30 letters, digits or underscores (empty: random)")
    ask_custom_short_url: bool = setting(False, label="Ask for a custom short URL each time")
    log_stats: bool = setting(False, label="Log detailed statistics",
                              help="Counts double towards the rate limit")


class _GdShortener(URLShortener):
    api_url = ""
    title = ""
    settings: IsgdSettings

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError(f"{self.title}: no URL to shorten")
        params = {"format": "json", "url": url}
        custom = custom_value(self.ctx, "Custom short URL", self.settings.custom_short_url,
                              self.settings.ask_custom_short_url)
        if custom:
            params["shorturl"] = custom
        if self.settings.log_stats:
            params["logstats"] = "1"
        resp = self.ctx.get(self.api_url, params=params)
        text = resp.text
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise UploaderError(f"{self.title} failed: HTTP {resp.status_code}: {text[:300]}", text)
        if "errorcode" in data:
            code = int(data.get("errorcode") or 4)
            msg = data.get("errormessage") or ERROR_CODES.get(code, "unknown error")
            raise UploaderError(f"{self.title}: {msg} (error {code}: {ERROR_CODES.get(code, 'unknown')})", text)
        return short_result(url, data.get("shorturl", ""), text, self.title)


@register("shortener", "ISGD", IsgdSettings, title="is.gd", homepage="https://is.gd")
class IsgdShortener(_GdShortener):
    api_url = "https://is.gd/create.php"
    title = "is.gd"


@register("shortener", "VGD", IsgdSettings, title="v.gd", homepage="https://v.gd")
class VgdShortener(_GdShortener):
    api_url = "https://v.gd/create.php"
    title = "v.gd"
