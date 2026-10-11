"""Immich (ShareX Immich.cs), self-hosted photo library.

``POST <server>/api/assets`` (multipart ``assetData`` + ``fileCreatedAt``/``fileModifiedAt``/``filename``; servers
before v3 also need ``deviceAssetId``/``deviceId``, so the server version is checked first) with the
``x-api-key`` header. Optionally add the asset to an album (by ID or name, created when missing), then create
a shared link (``POST /api/shared-links``, type INDIVIDUAL) → ``<server>/share/<key>``; without a shared link
the URL is ``<server>/photos/<id>``.
"""
from __future__ import annotations

import datetime as _dt
import re
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult, json_of
from ..registry import register, setting
from ._file_helpers import combine_url, multipart_body, read_all

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass
class ImmichSettings:
    url: str = setting("", label="Server URL", help="e.g. https://photos.example.com")
    api_key: str = setting("", label="API key", secret=True,
                           help="Account settings > API keys (needs asset.upload, sharedLink.create, album.*)")
    auto_create_shareable_link: bool = setting(True, label="Create a shared link")
    show_metadata: bool = setting(False, label="Shared link shows metadata")
    allow_download: bool = setting(True, label="Shared link allows download")
    shared_link_expire_days: int = setting(0, label="Shared link expires after (days)", help="0 = never")
    shared_link_password: str = setting("", label="Shared link password", secret=True)
    album: str = setting("", label="Album", help="Album name or ID to add uploads to (optional)")
    create_album: bool = setting(True, label="Create the album when missing")


def web_url(url: str) -> str:
    """Immich.GetWebURL: the server root, accepting .../api and .../api/assets."""
    u = url.strip().rstrip("/")
    for suffix in ("/api/assets", "/api"):
        if u.lower().endswith(suffix):
            return u[: -len(suffix)]
    return u


@register("file", "Immich", ImmichSettings, title="Immich", homepage="https://immich.app/")
class ImmichUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if not re.match(r"^https?://", s.url.strip()):
            return ["server URL must start with http:// or https://"]
        if not s.api_key.strip():
            return ["API key must be set"]
        return []

    @property
    def base(self) -> str:
        return web_url(self.settings.url)

    @property
    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "x-api-key": self.settings.api_key.strip()}

    def server_major(self) -> int:
        resp = self.ctx.get(combine_url(self.base, "api/server/version"), headers=self.headers)
        if resp.status_code == 200:
            try:
                return int(resp.json().get("major", 3))
            except (ValueError, TypeError, AttributeError):
                return 3
        return 3

    def upload_asset(self, stream: BinaryIO, file_name: str) -> str:
        stamp = _dt.datetime.now(_dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        fields = {"fileCreatedAt": stamp, "fileModifiedAt": stamp, "filename": file_name, "isFavorite": "false"}
        if self.server_major() < 3:  # v1/v2 require these; v3 removed them
            fields = {"deviceAssetId": f"ShareL-{uuid.uuid4().hex}", "deviceId": "ShareL", **fields}
        body, ctype = multipart_body(fields, "assetData", file_name, read_all(stream))
        resp = self.ctx.post(combine_url(self.base, "api/assets"), data=self.ctx.progress_stream(body),
                             headers={**self.headers, "Content-Type": ctype})
        data = json_of(resp, "Immich upload")
        asset_id = data.get("id") if isinstance(data, dict) else None
        if not asset_id:
            raise UploaderError("Immich upload response has no asset ID", resp.text)
        return asset_id

    def add_to_album(self, asset_id: str) -> None:
        album = self.settings.album.strip()
        if UUID_RE.match(album):
            album_id = album
        else:
            albums = json_of(self.ctx.get(combine_url(self.base, "api/albums"), headers=self.headers),
                             "Immich album list")
            album_id = next((a["id"] for a in albums if a.get("albumName") == album), "")
            if not album_id:
                if not self.settings.create_album:
                    raise UploaderError(f"Immich album '{album}' does not exist")
                json_of(self.ctx.post(combine_url(self.base, "api/albums"), headers=self.headers,
                                      json={"albumName": album, "assetIds": [asset_id]}), "Immich create album")
                return
        resp = self.ctx.put(combine_url(self.base, "api/albums", album_id, "assets"), headers=self.headers,
                            json={"ids": [asset_id]})
        results = json_of(resp, "Immich add to album")
        failed = [r for r in results if isinstance(r, dict) and not r.get("success")
                  and r.get("error") != "duplicate"]
        if failed:
            raise UploaderError(f"Immich could not add the asset to the album: {failed[0].get('error')}", resp.text)

    def create_shared_link(self, asset_id: str) -> str:
        s = self.settings
        body: dict = {"type": "INDIVIDUAL", "assetIds": [asset_id], "allowUpload": False,
                      "allowDownload": s.allow_download, "showMetadata": s.show_metadata}
        if s.shared_link_expire_days > 0:
            expires = _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=s.shared_link_expire_days)
            body["expiresAt"] = expires.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if s.shared_link_password:
            body["password"] = s.shared_link_password
        data = json_of(self.ctx.post(combine_url(self.base, "api/shared-links"), headers=self.headers, json=body),
                       "Immich shared link")
        key = data.get("key") if isinstance(data, dict) else None
        if not key:
            raise UploaderError("Immich shared link response has no key")
        return combine_url(self.base, "share", key)

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        asset_id = self.upload_asset(stream, file_name)
        if self.settings.album.strip():
            self.add_to_album(asset_id)
        if self.settings.auto_create_shareable_link:
            return UploadResult(url=self.create_shared_link(asset_id))
        return UploadResult(url=combine_url(self.base, "photos", asset_id))


def test_connection(settings: ImmichSettings, ctx: UploadContext) -> str:
    up = ImmichUploader(settings, ctx)
    me = json_of(ctx.get(combine_url(up.base, "api/users/me"), headers=up.headers), "Immich API key check")
    return f"Immich server v{up.server_major()}.x; API key belongs to {me.get('email') or me.get('name')}"
