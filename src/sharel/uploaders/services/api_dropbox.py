"""Dropbox (ShareX Dropbox.cs, API v2).

* Upload to ``<upload path>/<file name>`` (name-parser pattern, default ``ShareL/%y/%mo``):
  ``POST https://content.dropboxapi.com/2/files/upload`` with the ``Dropbox-API-Arg`` header
  ``{"path", "mode": "overwrite", "autorename": false, "mute": true}`` for files up to 150 MB; larger files
  go through an upload session (``upload_session/start`` → ``append_v2`` → ``finish``) in 8 MiB chunks.
* "Create shareable link": ``sharing/create_shared_link_with_settings`` (public visibility); when the link
  already exists (409 ``shared_link_already_exists``) the existing one is used, from the error's metadata
  or ``sharing/list_shared_links``. Without a link the upload has no URL.
* "Direct link": ShareX's conversion ``www.dropbox.com/scl/fi/...`` → ``dl.dropboxusercontent.com/scl/fi/...``;
  legacy ``/s/`` links get ``raw=1`` instead of ``dl=0``.

OAuth 2 with PKCE, ``token_access_type=offline`` for a refresh token. Dropbox matches redirect URIs
exactly, so the loopback listener uses a fixed port that must be registered on the app.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..oauth import authorize as oauth_authorize
from ..oauth import bearer
from ..registry import OAuth2Info, OAuth2Spec, register, setting
from ._api_common import error_message, iter_chunks, json_ok, parse_pattern, remaining_size

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
SHARE_DIRECT = "https://dl.dropboxusercontent.com/scl/fi/"
SIMPLE_UPLOAD_LIMIT = 150 * 1000 * 1000  # ShareX's limit for /files/upload (Dropbox: 150 MB)
DEFAULT_PORT = 53682

SPEC = OAuth2Spec("https://www.dropbox.com/oauth2/authorize", "https://api.dropboxapi.com/oauth2/token",
                  scopes=["account_info.read", "files.content.write", "sharing.write", "sharing.read"],
                  extra_authorize_params={"token_access_type": "offline"})


@dataclass
class DropboxSettings:
    client_id: str = setting("", label="App key",
                             help="Create a 'Scoped access' app at https://www.dropbox.com/developers/apps with the "
                                  "files.content.write, sharing.write and sharing.read permissions, and add "
                                  f"http://127.0.0.1:{DEFAULT_PORT}/ as a redirect URI")
    client_secret: str = setting("", label="App secret", secret=True, help="Optional: PKCE works without it")
    redirect_port: int = setting(DEFAULT_PORT, label="Redirect port",
                                 help="Port of the http://127.0.0.1:<port>/ redirect URI registered on the app")
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    upload_path: str = setting("ShareL/%y/%mo", label="Upload path", help="Folder in your Dropbox; name-parser "
                                                                        "patterns allowed")
    auto_create_shareable_link: bool = setting(True, label="Create shareable link")
    use_direct_link: bool = setting(False, label="Use direct link")


def authorize(settings: DropboxSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button."""
    settings.oauth2 = oauth_authorize(SPEC, settings.client_id, settings.client_secret, ctx,
                                      port=int(settings.redirect_port or DEFAULT_PORT))
    ctx.save_settings()
    return settings.oauth2


def verify_path(path: str, file_name: str = "") -> str:
    """ShareX Dropbox.VerifyPath: '/'-prefixed, forward slashes, no trailing slash, then the file name."""
    path = (path or "").strip().replace("\\", "/").strip("/")
    if path:
        path = "/" + path
        return f"{path}/{file_name}" if file_name else path
    return "/" + file_name if file_name else ""


def direct_shareable_url(url: str) -> str:
    """ShareX GetDirectShareableURL, plus raw=1 for legacy ``/s/`` links."""
    find = "dropbox.com/scl/fi/"
    index = url.find(find)
    if index > -1:
        rest = url[index + len(find):]
        if rest:
            return SHARE_DIRECT + rest
    parts = urlsplit(url)
    if parts.netloc.endswith("dropbox.com"):
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("dl", "raw")]
        return urlunsplit(parts._replace(query=urlencode([*q, ("raw", "1")])))
    return url


def _arg(value: dict) -> str:
    # json.dumps escapes non-ASCII (\\uXXXX), which keeps the header valid for any file name
    return json.dumps(value)


@register("file", "Dropbox", DropboxSettings, title="Dropbox", oauth=SPEC, homepage="https://www.dropbox.com")
class DropboxUploader(FileUploader):
    session_chunk_size = 8 * 1024 * 1024
    simple_upload_limit = SIMPLE_UPLOAD_LIMIT

    def validate(self) -> list[str]:
        o = self.settings.oauth2
        return [] if (o.access_token or o.refresh_token) else ["Dropbox is not authorized"]

    def _auth(self) -> dict[str, str]:
        return bearer(SPEC, self.settings, self.ctx)

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        path = verify_path(parse_pattern(s.upload_path, NameParserType.FolderPath), file_name)
        commit = {"path": path, "mode": "overwrite", "autorename": False, "mute": True}
        size = remaining_size(stream)
        if size <= self.simple_upload_limit:
            headers = {**self._auth(), "Dropbox-API-Arg": _arg(commit), "Content-Type": "application/octet-stream",
                       "Content-Length": str(size)}
            resp = self.ctx.post(f"{CONTENT}/files/upload", data=self.ctx.progress_stream(stream, size),
                                 headers=headers)
        else:
            resp = self._session_upload(stream, size, commit)
        meta = json_ok(resp, "Dropbox upload")
        result = UploadResult(response=resp.text)
        if s.auto_create_shareable_link:
            result.url = self.create_shareable_link(meta.get("path_display") or path, s.use_direct_link)
        else:
            result.is_url_expected = False
        return result

    def _session_upload(self, stream: BinaryIO, size: int, commit: dict):
        session_id = ""
        resp = None
        self.ctx.report(0, size)
        for offset, chunk in iter_chunks(stream, size, self.session_chunk_size):
            last = offset + len(chunk) >= size
            headers = {**self._auth(), "Content-Type": "application/octet-stream"}
            if not session_id:
                headers["Dropbox-API-Arg"] = _arg({"close": False})
                start = json_ok(self.ctx.post(f"{CONTENT}/files/upload_session/start", data=chunk, headers=headers),
                                "Dropbox upload session")
                session_id = start["session_id"]
            elif not last:
                headers["Dropbox-API-Arg"] = _arg({"cursor": {"session_id": session_id, "offset": offset},
                                                   "close": False})
                r = self.ctx.post(f"{CONTENT}/files/upload_session/append_v2", data=chunk, headers=headers)
                if r.status_code != 200:
                    raise UploaderError(error_message(r, "Dropbox upload session"), r.text)
            else:
                headers["Dropbox-API-Arg"] = _arg({"cursor": {"session_id": session_id, "offset": offset},
                                                   "commit": commit})
                resp = self.ctx.post(f"{CONTENT}/files/upload_session/finish", data=chunk, headers=headers)
            self.ctx.report(offset + len(chunk), size)
        if resp is None:
            raise UploaderError("Dropbox upload session ended without a commit")
        return resp

    def create_shareable_link(self, path: str, direct: bool) -> str:
        body = {"path": verify_path(path), "settings": {"requested_visibility": "public"}}
        resp = self.ctx.post(f"{API}/sharing/create_shared_link_with_settings", json=body, headers=self._auth())
        url = ""
        if resp.status_code == 200:
            url = resp.json().get("url", "")
        elif resp.status_code == 409 and "shared_link_already_exists" in resp.text:
            try:
                existing = resp.json()["error"]["shared_link_already_exists"]["metadata"]["url"]
            except (KeyError, TypeError, ValueError):
                existing = ""
            url = existing or self._existing_link(path)
        else:
            raise UploaderError(error_message(resp, "Dropbox shared link"), resp.text)
        if not url:
            raise UploaderError("Dropbox did not return a shared link", resp.text)
        return direct_shareable_url(url) if direct else url

    def _existing_link(self, path: str) -> str:
        resp = self.ctx.post(f"{API}/sharing/list_shared_links", json={"path": verify_path(path), "direct_only": True},
                             headers=self._auth())
        links = json_ok(resp, "Dropbox shared links").get("links") or []
        return links[0].get("url", "") if links else ""
