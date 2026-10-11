"""OneDrive (ShareX OneDrive.cs, Microsoft Graph v1.0).

* Files up to 4 MB: ``PUT /me/drive/items/<folder>:/<name>:/content`` (``/me/drive/root:...`` for the root).
* Larger files: ``POST .../createUploadSession`` (conflict behaviour "replace") then ``PUT`` the
  pre-authenticated ``uploadUrl`` in 10 MiB fragments (Graph wants multiples of 320 KiB) with
  ``Content-Range``; 202 means "send the next range", 200/201 is the driveItem. The session is deleted
  when a fragment fails.
* "Create shareable link": ``POST /me/drive/items/<id>/createLink`` ``{"type": "view", "scope": "anonymous"}``;
  with "direct link" the ``embed`` type, as ShareX does (OneDrive for Business has no embed links, so it
  falls back to ``view`` there). Otherwise the item's ``webUrl``.

OAuth 2 (PKCE) against the ``common`` endpoint with ``offline_access Files.ReadWrite``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..oauth import authorize as oauth_authorize
from ..oauth import bearer
from ..registry import OAuth2Info, OAuth2Spec, register, setting
from ._api_common import error_message, iter_chunks, json_ok, remaining_size

GRAPH = "https://graph.microsoft.com/v1.0"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
FRAGMENT_GRANULARITY = 320 * 1024

SPEC = OAuth2Spec("https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                  "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                  scopes=["offline_access", "Files.ReadWrite"])


@dataclass
class OneDriveSettings:
    client_id: str = setting("", label="Application (client) ID",
                             help="Register an app at https://entra.microsoft.com → App registrations (accounts in "
                                  "any organizational directory and personal Microsoft accounts), add the platform "
                                  "'Mobile and desktop applications' with the redirect URI http://127.0.0.1 "
                                  "(loopback ports are not matched) and allow public client flows")
    client_secret: str = setting("", label="Client secret", secret=True, help="Leave empty for a public client")
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    folder_id: str = setting("", label="Folder ID", help="Empty uploads to the root folder")
    folder_name: str = setting("", label="Folder", hidden=True)
    auto_create_shareable_link: bool = setting(True, label="Create shareable link")
    use_direct_link: bool = setting(False, label="Use direct (embed) link")


def authorize(settings: OneDriveSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button."""
    settings.oauth2 = oauth_authorize(SPEC, settings.client_id, settings.client_secret, ctx)
    ctx.save_settings()
    return settings.oauth2


def folder_path(folder_id: str) -> str:
    return f"me/drive/items/{quote(folder_id, safe='!')}" if folder_id else "me/drive/root"


def list_folders(settings: OneDriveSettings, ctx: UploadContext, folder_id: str = "") -> list[dict]:
    """Sub-folders of a folder (root when empty): [{"id", "name"}] for a folder picker."""
    url = f"{GRAPH}/{folder_path(folder_id)}/children?$select=id,name,folder&$top=200"
    out = []
    while url:
        page = json_ok(ctx.get(url, headers=bearer(SPEC, settings, ctx)), "OneDrive folder listing")
        out += [{"id": i["id"], "name": i.get("name", "")} for i in page.get("value", []) if "folder" in i]
        url = page.get("@odata.nextLink", "")
    return out


@register("file", "OneDrive", OneDriveSettings, title="OneDrive", oauth=SPEC, homepage="https://onedrive.live.com")
class OneDriveUploader(FileUploader):
    fragment_size = 32 * FRAGMENT_GRANULARITY  # 10 MiB
    simple_upload_limit = SIMPLE_UPLOAD_LIMIT

    def validate(self) -> list[str]:
        o = self.settings.oauth2
        return [] if (o.access_token or o.refresh_token) else ["OneDrive is not authorized"]

    def _auth(self) -> dict[str, str]:
        return bearer(SPEC, self.settings, self.ctx)

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        target = f"{GRAPH}/{folder_path(self.settings.folder_id.strip())}:/{quote(file_name, safe='')}:"
        size = remaining_size(stream)
        if size <= self.simple_upload_limit:
            resp = self.ctx.put(f"{target}/content?@microsoft.graph.conflictBehavior=replace",
                                data=self.ctx.progress_stream(stream, size),
                                headers={**self._auth(), "Content-Type": "application/octet-stream",
                                         "Content-Length": str(size)})
        else:
            resp = self._session_upload(target, stream, size)
        item = json_ok(resp, "OneDrive upload")
        result = UploadResult(response=resp.text)
        if self.settings.auto_create_shareable_link:
            result.url = self.create_link(item["id"], "embed" if self.settings.use_direct_link else "view")
        else:
            result.url = item.get("webUrl", "")
        return result

    def _session_upload(self, target: str, stream: BinaryIO, size: int):
        session = json_ok(self.ctx.post(f"{target}/createUploadSession", headers=self._auth(),
                                        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}),
                          "OneDrive upload session")
        upload_url = session.get("uploadUrl")
        if not upload_url:
            raise UploaderError("OneDrive upload session: no upload URL", str(session))
        self.ctx.report(0, size)
        resp = None
        try:
            for offset, chunk in iter_chunks(stream, size, self.fragment_size):
                end = offset + len(chunk) - 1
                # the upload URL is pre-authenticated: sending the Authorization header is an error
                resp = self.ctx.put(upload_url, data=chunk, headers={"Content-Range": f"bytes {offset}-{end}/{size}",
                                                                     "Content-Length": str(len(chunk))})
                if resp.status_code not in (200, 201, 202):
                    raise UploaderError(error_message(resp, "OneDrive upload"), resp.text)
                self.ctx.report(end + 1, size)
        except UploaderError:
            try:
                self.ctx.request("DELETE", upload_url)
            except UploaderError:
                pass
            raise
        if resp is None or resp.status_code not in (200, 201):
            raise UploaderError("OneDrive upload did not complete", resp.text if resp is not None else "")
        return resp

    def create_link(self, item_id: str, link_type: str) -> str:
        url = f"{GRAPH}/me/drive/items/{quote(item_id, safe='!')}/createLink"
        resp = self.ctx.post(url, json={"type": link_type, "scope": "anonymous"}, headers=self._auth())
        if link_type == "embed" and resp.status_code in (400, 403, 501):
            resp = self.ctx.post(url, json={"type": "view", "scope": "anonymous"}, headers=self._auth())
        js = json_ok(resp, "OneDrive shareable link")
        link = (js.get("link") or {}).get("webUrl")
        if not link:
            raise UploaderError("OneDrive did not return a link", resp.text)
        return link
