"""Bitly (ShareX BitlyURLShortener), API v4: ``POST https://api-ssl.bitly.com/v4/shorten``.

Two ways to authenticate:

* a **generic access token** (Bitly > Settings > API > "Generate token"): pasted into ``access_token``,
  takes precedence when set; or
* **OAuth 2** web flow with your own Bitly app (``client_id``/``client_secret``): the Authorize button
  calls :func:`authorize`, which opens ``https://bitly.com/oauth/authorize`` and exchanges the code at
  ``https://api-ssl.bitly.com/oauth/access_token`` (``Accept: application/json`` makes Bitly answer in
  JSON). Bitly tokens do not expire and there is no refresh token.

``domain`` (custom/branded domain, e.g. ``bit.ly`` or ``j.mp``) and ``group_guid`` are optional; when
empty Bitly uses the account's default group and domain.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import oauth
from ..base import UploadContext, UploaderError, UploadResult, URLShortener
from ..registry import OAuth2Info, OAuth2Spec, register, setting
from .short_common import api_json, short_result

API_URL = "https://api-ssl.bitly.com/"
SHORTEN_URL = API_URL + "v4/shorten"

BITLY_OAUTH = OAuth2Spec(
    authorize_url="https://bitly.com/oauth/authorize",
    token_url=API_URL + "oauth/access_token",
    scopes=[],
    pkce=False,  # Bitly's OAuth 2 is the classic confidential-client code flow
)


@dataclass
class BitlySettings:
    access_token: str = setting("", label="Generic access token", secret=True,
                                help="Bitly > Settings > API. Used instead of OAuth when set.")
    client_id: str = setting("", label="OAuth client ID", help="From your app at bitly.com > Settings > Developer")
    client_secret: str = setting("", label="OAuth client secret", secret=True)
    oauth2: OAuth2Info = setting(default_factory=OAuth2Info, label="Authorization", kind="oauth")  # noqa: RUF009
    domain: str = setting("", label="Domain", help="Custom or branded short domain (empty: account default)")
    group_guid: str = setting("", label="Group GUID", help="Empty: the account's default group")


def authorize(settings: BitlySettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button: run the loopback OAuth flow and store the token."""
    info = oauth.authorize(BITLY_OAUTH, settings.client_id, settings.client_secret, ctx)
    settings.oauth2 = info
    ctx.save_settings()
    return info


@register("shortener", "BITLY", BitlySettings, title="bit.ly", oauth=BITLY_OAUTH, homepage="https://bitly.com")
class BitlyShortener(URLShortener):
    settings: BitlySettings

    def validate(self) -> list[str]:
        s = self.settings
        if s.access_token.strip() or s.oauth2.access_token or s.oauth2.refresh_token:
            return []
        return ["enter a generic access token, or authorize with OAuth"]

    def _token(self) -> str:
        if self.settings.access_token.strip():
            return self.settings.access_token.strip()
        return oauth.ensure_token(BITLY_OAUTH, self.settings, self.ctx)

    def shorten_url(self, url: str) -> UploadResult:
        if not url:
            raise UploaderError("bit.ly: no URL to shorten")
        body: dict[str, str] = {"long_url": url}
        if self.settings.domain.strip():
            body["domain"] = self.settings.domain.strip()
        if self.settings.group_guid.strip():
            body["group_guid"] = self.settings.group_guid.strip()
        resp = self.ctx.post(SHORTEN_URL, json=body, headers={"Authorization": f"Bearer {self._token()}"})
        data = api_json(resp, "bit.ly")
        return short_result(url, data.get("link", "") if isinstance(data, dict) else "", resp.text, "bit.ly")
