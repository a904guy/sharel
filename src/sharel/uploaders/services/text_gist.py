"""GitHub Gist (ShareX TextUploaders/GitHubGist.cs) against the GitHub REST API (``POST /gists``).

Authentication is either a personal access token (classic with the ``gist`` scope, or fine-grained
with Gists read/write), or an OAuth App's authorization-code flow through a loopback redirect
(GitHub accepts any port on ``http://127.0.0.1`` when the app's callback is ``http://127.0.0.1/``).
``custom_api_url`` points at GitHub Enterprise Server (``https://ghe.example.com/api/v3``); its OAuth
endpoints are then derived from the same host.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from urllib.parse import urlsplit

from .. import oauth
from ..base import TextUploader, UploadContext, UploaderError, UploadResult, json_of
from ..registry import OAuth2Info, OAuth2Spec, register, setting

API_URL = "https://api.github.com"
SPEC = OAuth2Spec(authorize_url="https://github.com/login/oauth/authorize",
                  token_url="https://github.com/login/oauth/access_token", scopes=["gist"], pkce=False)


@dataclass
class GistSettings:
    personal_token: str = setting("", label="Personal access token", secret=True,
                                  help="Token with the gist scope; used instead of the OAuth login when set.")
    client_id: str = setting("", label="OAuth App client ID", help="Only for the browser login.")
    client_secret: str = setting("", label="OAuth App client secret", secret=True)
    oauth2: OAuth2Info = setting(label="GitHub account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    publish_public: bool = setting(False, label="Public gist", help="Secret (unlisted) gists otherwise.")
    raw_url: bool = setting(False, label="Use raw URL", help="Link to the raw file instead of the gist page.")
    custom_url: str = setting("", label="Custom API URL",
                              help="GitHub Enterprise Server API, e.g. https://ghe.example.com/api/v3")
    description: str = setting("", label="Description")


def _spec(settings: GistSettings) -> OAuth2Spec:
    if not settings.custom_url:
        return SPEC
    parts = urlsplit(settings.custom_url if "://" in settings.custom_url else "https://" + settings.custom_url)
    host = f"{parts.scheme}://{parts.netloc}"
    if parts.netloc.lower() == "api.github.com":
        return SPEC
    return dataclasses.replace(SPEC, authorize_url=f"{host}/login/oauth/authorize",
                               token_url=f"{host}/login/oauth/access_token")


def authorize(settings: GistSettings, ctx: UploadContext) -> OAuth2Info:
    """Settings page "Authorize": GitHub OAuth App login in the browser."""
    if not settings.client_id or not settings.client_secret:
        raise UploaderError("Enter the OAuth App client ID and secret first (or use a personal access token)")
    info = oauth.authorize(_spec(settings), settings.client_id, settings.client_secret, ctx)
    settings.oauth2 = info
    ctx.save_settings()
    return info


def api_base(settings: GistSettings) -> str:
    url = (settings.custom_url or API_URL).strip().rstrip("/")
    return url if "://" in url else "https://" + url


@register("text", "Gist", GistSettings, title="GitHub Gist", oauth=SPEC, homepage="https://gist.github.com")
class GistUploader(TextUploader):
    settings: GistSettings

    def validate(self) -> list[str]:
        s = self.settings
        if not s.personal_token and not (s.oauth2.access_token or s.oauth2.refresh_token):
            return ["GitHub login is required: enter a personal access token or click Authorize"]
        return []

    def _token(self) -> str:
        s = self.settings
        if s.personal_token:
            return s.personal_token
        return oauth.ensure_token(_spec(s), s, self.ctx)

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        name = file_name or "paste.txt"
        body = {"description": s.description, "public": bool(s.publish_public), "files": {name: {"content": text}}}
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        resp = self.ctx.post(f"{api_base(s)}/gists", json=body, headers=headers)
        data = json_of(resp, "Gist upload")
        if s.raw_url:
            files = data.get("files") or {}
            entry = files.get(name) or next(iter(files.values()), {})
            url = entry.get("raw_url", "")
        else:
            url = data.get("html_url", "")
        if not url:
            raise UploaderError(f"Gist upload failed: {resp.text[:300]}", resp.text)
        return UploadResult(url=url, response=resp.text)
