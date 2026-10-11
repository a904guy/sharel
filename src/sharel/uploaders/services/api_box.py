"""Box (ShareX Box.cs, Box API 2.0).

* Up to 50 MB: ``POST https://upload.box.com/api/2.0/files/content`` multipart with the ``attributes`` JSON part
  (``{"name", "parent": {"id"}}``) before the ``file`` part — the current form of ShareX's ``parent_id``
  field. A 409 name conflict uploads a new version of the existing file instead.
* Larger files: chunked upload session (``POST /files/upload_sessions`` → ``PUT`` parts of ``part_size``
  with ``Content-Range`` and ``Digest: sha=<base64 SHA-1>`` → ``POST commit`` with the part list and the
  whole file's digest; aborted on failure).
* "Share": ``PUT /2.0/files/<id>?fields=shared_link`` ``{"shared_link": {"access": "open"}}`` (access level
  open / company / collaborators); otherwise ``https://app.box.com/file/<id>``.

OAuth 2 authorization-code flow with the client secret (Box has no PKCE for public clients) and an exact
redirect URI, so the loopback listener uses a fixed port.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..oauth import authorize as oauth_authorize
from ..oauth import bearer
from ..registry import OAuth2Info, OAuth2Spec, register, setting
from ._api_common import error_message, iter_chunks, json_ok, multipart_form, remaining_size, send_body

API = "https://api.box.com/2.0"
UPLOAD_API = "https://upload.box.com/api/2.0"
CHUNKED_THRESHOLD = 50 * 1024 * 1024
ACCESS_LEVELS = ["open", "company", "collaborators"]
DEFAULT_PORT = 53683

SPEC = OAuth2Spec("https://account.box.com/api/oauth2/authorize", "https://api.box.com/oauth2/token", pkce=False)

_sleep = time.sleep


@dataclass
class BoxSettings:
    client_id: str = setting("", label="Client ID",
                             help="Create a Custom App with 'User Authentication (OAuth 2.0)' at "
                                  "https://app.box.com/developers/console, enable 'Write all files and folders', "
                                  f"and set the redirect URI to http://127.0.0.1:{DEFAULT_PORT}/")
    client_secret: str = setting("", label="Client secret", secret=True)
    redirect_port: int = setting(DEFAULT_PORT, label="Redirect port",
                                 help="Port of the http://127.0.0.1:<port>/ redirect URI set on the app")
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    folder_id: str = setting("0", label="Folder ID", help="0 is the root folder (All Files)")
    folder_name: str = setting("Root folder", label="Folder", hidden=True)
    share: bool = setting(True, label="Create shared link")
    share_access_level: str = setting("open", label="Shared link access", choices=ACCESS_LEVELS,
                                      help="open: anyone with the link; company: people in your enterprise; "
                                           "collaborators: invited people only")


def authorize(settings: BoxSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button."""
    settings.oauth2 = oauth_authorize(SPEC, settings.client_id, settings.client_secret, ctx,
                                      port=int(settings.redirect_port or DEFAULT_PORT))
    ctx.save_settings()
    return settings.oauth2


def list_folders(settings: BoxSettings, ctx: UploadContext, folder_id: str = "0") -> list[dict]:
    """Sub-folders of a folder: [{"id", "name"}] for a folder picker."""
    out, offset = [], 0
    while True:
        page = json_ok(ctx.get(f"{API}/folders/{folder_id or '0'}/items",
                               params={"fields": "id,name,type", "limit": "1000", "offset": str(offset)},
                               headers=bearer(SPEC, settings, ctx)), "Box folder listing")
        entries = page.get("entries") or []
        out += [{"id": e["id"], "name": e.get("name", "")} for e in entries if e.get("type") == "folder"]
        offset += len(entries)
        if not entries or offset >= int(page.get("total_count") or 0):
            return out


def _digest(data: bytes) -> str:
    return "sha=" + base64.b64encode(hashlib.sha1(data).digest()).decode()


@register("file", "Box", BoxSettings, title="Box", oauth=SPEC, homepage="https://www.box.com")
class BoxUploader(FileUploader):
    chunked_threshold = CHUNKED_THRESHOLD

    def validate(self) -> list[str]:
        o = self.settings.oauth2
        return [] if (o.access_token or o.refresh_token) else ["Box is not authorized"]

    def _auth(self) -> dict[str, str]:
        return bearer(SPEC, self.settings, self.ctx)

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        folder = s.folder_id.strip() or "0"
        size = remaining_size(stream)
        if size >= self.chunked_threshold:
            entry = self._chunked_upload(stream, size, file_name, folder)
        else:
            entry = self._simple_upload(stream, size, file_name, folder)
        result = UploadResult(response=json.dumps(entry))
        if s.share:
            result.url = self.create_shared_link(entry["id"], s.share_access_level)
        else:
            result.url = f"https://app.box.com/file/{entry['id']}"
        return result

    def _simple_upload(self, stream: BinaryIO, size: int, file_name: str, folder: str) -> dict:
        start = stream.tell()
        attrs = {"name": file_name, "parent": {"id": folder}}
        body, ctype = multipart_form({"attributes": json.dumps(attrs)}, "file", file_name, stream, size)
        resp = send_body(self.ctx, "POST", f"{UPLOAD_API}/files/content", body, ctype, headers=self._auth())
        if resp.status_code == 409:
            conflict = ((resp.json().get("context_info") or {}).get("conflicts") or {})
            if isinstance(conflict, list):
                conflict = conflict[0] if conflict else {}
            if conflict.get("id") and conflict.get("type", "file") == "file":
                stream.seek(start)
                body, ctype = multipart_form({"attributes": json.dumps({"name": file_name})}, "file", file_name,
                                             stream, size)
                resp = send_body(self.ctx, "POST", f"{UPLOAD_API}/files/{conflict['id']}/content", body, ctype,
                                 headers=self._auth())
        return self._first_entry(json_ok(resp, "Box upload"), resp.text)

    @staticmethod
    def _first_entry(js: dict, text: str) -> dict:
        entries = js.get("entries") or []
        if not entries or not entries[0].get("id"):
            raise UploaderError("Box upload: no file in the response", text)
        return entries[0]

    def _chunked_upload(self, stream: BinaryIO, size: int, file_name: str, folder: str) -> dict:
        session = json_ok(self.ctx.post(f"{UPLOAD_API}/files/upload_sessions", headers=self._auth(),
                                        json={"folder_id": folder, "file_size": size, "file_name": file_name}),
                          "Box upload session")
        endpoints = session.get("session_endpoints") or {}
        part_size = int(session["part_size"])
        upload_part = endpoints.get("upload_part") or f"{UPLOAD_API}/files/upload_sessions/{session['id']}"
        commit = endpoints.get("commit") or f"{upload_part}/commit"
        abort = endpoints.get("abort") or upload_part
        whole = hashlib.sha1()
        parts = []
        try:
            self.ctx.report(0, size)
            for offset, chunk in iter_chunks(stream, size, part_size):
                whole.update(chunk)
                end = offset + len(chunk) - 1
                resp = self.ctx.put(upload_part, data=chunk, headers={
                    **self._auth(), "Content-Type": "application/octet-stream", "Digest": _digest(chunk),
                    "Content-Range": f"bytes {offset}-{end}/{size}"})
                parts.append(json_ok(resp, "Box upload part")["part"])
                self.ctx.report(end + 1, size)
            digest = "sha=" + base64.b64encode(whole.digest()).decode()
            for _attempt in range(30):
                resp = self.ctx.post(commit, json={"parts": parts}, headers={**self._auth(), "Digest": digest})
                if resp.status_code != 202:  # 202: parts still being processed, retry after Retry-After
                    break
                _sleep(float(resp.headers.get("Retry-After") or 1))
            if resp.status_code not in (200, 201):
                raise UploaderError(error_message(resp, "Box upload commit"), resp.text)
            return self._first_entry(resp.json(), resp.text)
        except UploaderError:
            try:
                self.ctx.request("DELETE", abort, headers=self._auth())
            except UploaderError:
                pass
            raise

    def create_shared_link(self, file_id: str, access: str) -> str:
        access = access if access in ACCESS_LEVELS else "open"
        resp = self.ctx.put(f"{API}/files/{file_id}", params={"fields": "shared_link"},
                            json={"shared_link": {"access": access}}, headers=self._auth())
        link = (json_ok(resp, "Box shared link").get("shared_link") or {}).get("url")
        if not link:
            raise UploaderError("Box did not return a shared link", resp.text)
        return link
