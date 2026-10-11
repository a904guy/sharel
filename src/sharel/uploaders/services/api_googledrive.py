"""Google Drive (ShareX GoogleDrive.cs, Drive API v3).

* Files up to 5 MB: one ``multipart/related`` request (``uploadType=multipart``); larger files use the
  resumable protocol in 8 MiB chunks (see ``_api_google``).
* Metadata ``{"name", "parents": [folder or shared-drive id]}``; ``supportsAllDrives=true`` everywhere so
  shared drives work (a shared drive's id is also its root folder's id).
* "Public": ``POST files/<id>/permissions`` ``{"role": "reader", "type": "anyone", "allowFileDiscovery": false}``.
* URL: ``webViewLink``, or with "direct link" the ``webContentLink`` without its ``export`` parameter
  (``https://drive.google.com/uc?id=<id>``), as ShareX does.

Scope ``drive.file`` (only files this app created) — the same ShareX asks for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..base import FileUploader, UploadContext, UploadResult
from ..oauth import authorize as oauth_authorize
from ..oauth import bearer
from ..registry import OAuth2Info, register, setting
from ._api_common import json_ok, mime_type, multipart_related, remaining_size, send_body
from ._api_google import CLIENT_HELP, google_spec, resumable_upload

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FIELDS = "id,name,webViewLink,webContentLink"
MULTIPART_LIMIT = 5 * 1024 * 1024

SPEC = google_spec("https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/userinfo.profile")


@dataclass
class GoogleDriveSettings:
    client_id: str = setting("", label="Client ID", help=CLIENT_HELP.format(api="Google Drive API"))
    client_secret: str = setting("", label="Client secret", secret=True)
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    is_public: bool = setting(True, label="Share as public (anyone with the link can view)")
    direct_link: bool = setting(False, label="Use direct link (download URL)")
    use_folder: bool = setting(False, label="Upload into a folder")
    folder_id: str = setting("", label="Folder ID", help="The id at the end of the folder's drive.google.com URL")
    drive_id: str = setting("", label="Shared drive ID", help="Empty for My Drive")


def authorize(settings: GoogleDriveSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button."""
    settings.oauth2 = oauth_authorize(SPEC, settings.client_id, settings.client_secret, ctx)
    ctx.save_settings()
    return settings.oauth2


def _paged(ctx: UploadContext, settings, url: str, params: dict, key: str) -> list[dict]:
    out, token = [], ""
    while True:
        page = json_ok(ctx.get(url, params={**params, **({"pageToken": token} if token else {})},
                               headers=bearer(SPEC, settings, ctx)), "Google Drive listing")
        out.extend(page.get(key) or [])
        token = page.get("nextPageToken") or ""
        if not token:
            return out


def list_drives(settings: GoogleDriveSettings, ctx: UploadContext) -> list[dict]:
    """Shared drives the user can see: [{"id", "name"}] (My Drive is id "")."""
    return [{"id": "", "name": "My Drive"}, *_paged(ctx, settings, f"{API}/drives", {"pageSize": "100"}, "drives")]


def list_folders(settings: GoogleDriveSettings, ctx: UploadContext, drive_id: str = "") -> list[dict]:
    """Writable, non-trashed folders (ShareX GetFoldersAsync): [{"id", "name", "description"}]."""
    q = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    params = {"fields": "nextPageToken,files(id,name,description)"}
    if drive_id:
        params.update(driveId=drive_id, corpora="drive", supportsAllDrives="true", includeItemsFromAllDrives="true")
    else:
        q += " and 'me' in writers"
    params["q"] = q
    return _paged(ctx, settings, f"{API}/files", params, "files")


def direct_link(web_content_link: str) -> str:
    """ShareX: the webContentLink with its ``export`` query parameter removed."""
    parts = urlsplit(web_content_link)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "export"]
    return urlunsplit(parts._replace(query=urlencode(q)))


@register("file", "GoogleDrive", GoogleDriveSettings, title="Google Drive", oauth=SPEC,
          homepage="https://drive.google.com")
class GoogleDriveUploader(FileUploader):
    chunk_size = 32 * 256 * 1024

    def validate(self) -> list[str]:
        o = self.settings.oauth2
        return [] if (o.access_token or o.refresh_token) else ["Google Drive is not authorized"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        auth = bearer(SPEC, s, self.ctx)
        parent = (s.folder_id.strip() if s.use_folder else "") or s.drive_id.strip()
        metadata: dict = {"name": file_name}
        if parent:
            metadata["parents"] = [parent]
        size = remaining_size(stream)
        ctype = mime_type(file_name)
        query = f"fields={FIELDS}&supportsAllDrives=true"
        if size <= MULTIPART_LIMIT:
            body, body_type = multipart_related(metadata, stream, size, ctype)
            resp = send_body(self.ctx, "POST", f"{UPLOAD_API}?uploadType=multipart&{query}", body, body_type,
                             headers=auth)
        else:
            resp = resumable_upload(self.ctx, f"{UPLOAD_API}?uploadType=resumable&{query}", auth, metadata,
                                    stream, size, ctype, self.chunk_size)
        info = json_ok(resp, "Google Drive upload")
        result = UploadResult(response=resp.text)
        if s.is_public:
            json_ok(self.ctx.post(f"{API}/files/{info['id']}/permissions", params={"supportsAllDrives": "true"},
                                  json={"role": "reader", "type": "anyone", "allowFileDiscovery": False},
                                  headers=bearer(SPEC, s, self.ctx)), "Google Drive sharing")
        if s.direct_link and info.get("webContentLink"):
            result.url = direct_link(info["webContentLink"])
        else:
            result.url = info.get("webViewLink") or f"https://drive.google.com/file/d/{info['id']}/view"
        return result
