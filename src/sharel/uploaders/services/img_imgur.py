"""Imgur (ShareX ImageUploaders/Imgur.cs) against Imgur API v3.

* Anonymous uploads: ``Authorization: Client-ID <client_id>`` (the user registers an app at
  https://api.imgur.com/oauth2/addclient and pastes its Client ID).
* Account uploads: OAuth 2 authorization-code flow through a loopback redirect (Imgur's PIN flow,
  which ShareX uses, is deprecated). Imgur matches the redirect against the app's registered
  callback, so the app must be registered with ``http://127.0.0.1:<redirect_port>/``.
* Album, thumbnail size, direct link vs page link, GIFV, deletion URL from ``deletehash``.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .. import oauth
from ..base import ImageUploader, UploadContext, UploaderError, UploadResult, json_of
from ..registry import OAuth2Info, OAuth2Spec, register, setting

API = "https://api.imgur.com"
UPLOAD_URL = f"{API}/3/upload"

ACCOUNT_TYPES = ["Anonymous", "User"]
# ShareX ImgurThumbnailType -> Imgur size suffix
THUMBNAIL_SUFFIX = {"Small_Square": "s", "Big_Square": "b", "Small_Thumbnail": "t", "Medium_Thumbnail": "m",
                    "Large_Thumbnail": "l", "Huge_Thumbnail": "h"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mkv", "avi", "mov", "m4v", "flv", "wmv", "mpeg", "mpg", "3gp"}

SPEC = OAuth2Spec(authorize_url=f"{API}/oauth2/authorize", token_url=f"{API}/oauth2/token", pkce=False)


@dataclass
class ImgurSettings:
    account_type: str = setting("Anonymous", label="Account type", choices=ACCOUNT_TYPES,
                                help="Anonymous uploads use only the Client ID; User uploads go to your account.")
    client_id: str = setting("", label="Client ID", secret=True,
                             help="From https://api.imgur.com/oauth2/addclient")
    client_secret: str = setting("", label="Client secret", secret=True, help="Needed for account (User) uploads.")
    redirect_port: int = setting(53682, label="Callback port",
                                 help="Register http://127.0.0.1:<port>/ as the app's callback URL.")
    oauth2: OAuth2Info = setting(label="Imgur account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    upload_selected_album: bool = setting(False, label="Upload to album")
    selected_album_id: str = setting("", label="Album ID", help="Album id (from the album URL imgur.com/a/<id>).")
    direct_link: bool = setting(True, label="Use direct link", help="i.imgur.com file link instead of the page.")
    use_gifv: bool = setting(False, label="Use GIFV link for animated uploads")
    thumbnail_type: str = setting("Large_Thumbnail", label="Thumbnail type", choices=list(THUMBNAIL_SUFFIX))


def _spec(settings: ImgurSettings) -> OAuth2Spec:
    return dataclasses.replace(SPEC, redirect_port=int(settings.redirect_port or 0))


def authorize(settings: ImgurSettings, ctx: UploadContext) -> OAuth2Info:
    """Settings page "Authorize": log in to Imgur in the browser and store the tokens."""
    if not settings.client_secret:
        raise UploaderError("Enter the Client ID and Client secret of your Imgur app first")
    info = oauth.authorize(_spec(settings), settings.client_id, settings.client_secret, ctx)
    settings.oauth2 = info
    settings.account_type = "User"
    ctx.save_settings()
    return info


def list_albums(settings: ImgurSettings, ctx: UploadContext, max_pages: int = 10,
                per_page: int = 100) -> list[dict]:
    """The account's albums (``id``, ``title``, ...), as ShareX's album list (GET /3/account/me/albums)."""
    headers = oauth.bearer(_spec(settings), settings, ctx)
    albums: list[dict] = []
    for page in range(max_pages):
        data = _data(json_of(ctx.get(f"{API}/3/account/me/albums", params={"page": page, "perPage": per_page},
                                     headers=headers), "Imgur albums"))
        if not isinstance(data, list) or not data:
            break
        albums.extend(data)
        if len(data) < per_page:
            break
    return albums


def _error_text(body: dict) -> str:
    data = body.get("data")
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
    return str(body.get("errors") or body)


def _data(body):
    if not isinstance(body, dict) or not body.get("success"):
        raise UploaderError(f"Imgur error: {_error_text(body) if isinstance(body, dict) else body}", str(body))
    return body.get("data")


@register("image", "Imgur", ImgurSettings, title="Imgur", oauth=SPEC, homepage="https://imgur.com")
class ImgurUploader(ImageUploader):
    settings: ImgurSettings

    def validate(self) -> list[str]:
        s = self.settings
        if not s.client_id:
            return ["Client ID is required (register an app at https://api.imgur.com/oauth2/addclient)"]
        if s.account_type == "User" and not (s.oauth2.access_token or s.oauth2.refresh_token):
            return ["Imgur login is required: click Authorize in Destination settings"]
        return []

    def _headers(self, force_refresh: bool = False) -> dict[str, str]:
        s = self.settings
        if s.account_type == "User":
            if force_refresh:
                s.oauth2 = oauth.refresh(_spec(s), s.client_id, s.client_secret, s.oauth2, self.ctx)
                self.ctx.save_settings()
            return oauth.bearer(_spec(s), s, self.ctx)
        return {"Authorization": f"Client-ID {s.client_id}"}

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        return self._upload(stream, file_name, retry_on_invalid_token=True)

    def _upload(self, stream: BinaryIO, file_name: str, retry_on_invalid_token: bool,
                force_refresh: bool = False) -> UploadResult:
        s = self.settings
        start = stream.tell() if stream.seekable() else 0
        fields: dict[str, str] = {}
        if s.account_type == "User" and s.upload_selected_album and s.selected_album_id:
            fields["album"] = s.selected_album_id
        ext = Path(file_name).suffix.lower().lstrip(".")
        form_name = "video" if ext in VIDEO_EXTENSIONS else "image"
        resp = self.ctx.post(UPLOAD_URL, headers=self._headers(force_refresh), data=fields,
                             files={form_name: (file_name, self.ctx.progress_stream(stream))})
        text = resp.text
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise UploaderError(f"Imgur upload failed: HTTP {resp.status_code}: {text[:300]}", text)
        if not (body.get("success") and resp.status_code == 200):
            message = _error_text(body)
            if (retry_on_invalid_token and s.account_type == "User" and s.oauth2.refresh_token
                    and (resp.status_code in (401, 403) or "access token" in message.lower())):
                stream.seek(start)
                return self._upload(stream, file_name, retry_on_invalid_token=False, force_refresh=True)
            raise UploaderError(f"Imgur upload failed ({body.get('status', resp.status_code)}): {message}", text)
        image = body.get("data") or {}
        if not image.get("link"):
            raise UploaderError("Imgur upload failed: no link in response", text)
        result = UploadResult(response=text)
        if s.direct_link:
            # webm uploads return the link with a dot at the end
            result.url = image["gifv"] if s.use_gifv and image.get("gifv") else image["link"].rstrip(".")
        else:
            result.url = f"https://imgur.com/{image['id']}"
        suffix = THUMBNAIL_SUFFIX.get(s.thumbnail_type, "l")
        result.thumbnail_url = f"https://i.imgur.com/{image['id']}{suffix}.jpg"  # Imgur thumbnails are always jpg
        if image.get("deletehash"):
            result.deletion_url = f"https://imgur.com/delete/{image['deletehash']}"
        return result
