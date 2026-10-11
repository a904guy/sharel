"""Seafile (ShareX Seafile.cs).

Token auth (``Authorization: Token ...``; obtained from username + password via ``api2/auth-token/`` when only
those are set). Upload: ``GET api2/repos/<repo>/upload-link/?p=<dir>`` → ``POST <link>?ret-json=1`` (multipart
``file`` + ``parent_dir``). Share link: ``POST api/v2.1/share-links/`` (repo_id, path, optional password and
expire_days) → ``link``, optionally as a raw/direct link (``?raw=1``). Encrypted libraries can't be shared, so
those uploads have no URL (as in ShareX).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..base import FileUploader, UploadContext, UploaderError, UploadResult, check_response, json_of
from ..registry import register, setting
from ._file_helpers import combine_url, fix_prefix, multipart_body, parse_folder, read_all


@dataclass
class SeafileSettings:
    api_url: str = setting("https://cloud.seafile.com/api2/", label="API URL",
                           help="Your server followed by /api2/, e.g. https://seafile.example.com/api2/")
    auth_token: str = setting("", label="API token", secret=True,
                              help="Leave empty to log in with the username and password below")
    username: str = setting("", label="Username (email)")
    password: str = setting("", label="Password", secret=True)
    repo_id: str = setting("", label="Library (repo) ID", help="Empty: your default library")
    path: str = setting("/", label="Path", help="Folder in the library, name-parser patterns allowed")
    is_library_encrypted: bool = setting(False, label="Library is encrypted")
    encrypted_library_password: str = setting("", label="Library password", secret=True)
    create_shareable_url: bool = setting(True, label="Create a share link")
    create_shareable_url_raw: bool = setting(False, label="Raw (direct) share link")
    share_days_to_expire: int = setting(0, label="Share link expires after (days)", help="0 = never")
    share_password: str = setting("", label="Share link password", secret=True)


def server_root(api_url: str) -> str:
    """https://host/api2/ -> https://host (Seafile can be served below a sub-path too)."""
    u = fix_prefix(api_url.strip()).rstrip("/")
    for suffix in ("/api2", "/api/v2.1"):
        if u.endswith(suffix):
            return u[: -len(suffix)]
    return u


def raw_link(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["raw"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


@register("file", "Seafile", SeafileSettings, title="Seafile", homepage="https://www.seafile.com/")
class SeafileUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if not s.api_url:
            return ["API URL is empty"]
        if not s.auth_token and not (s.username and s.password):
            return ["set an API token, or a username and password"]
        return []

    def api2(self, *parts: str) -> str:
        return combine_url(server_root(self.settings.api_url), "api2", *parts) + "/"

    def token(self) -> str:
        s = self.settings
        if s.auth_token:
            return s.auth_token
        resp = self.ctx.post(self.api2("auth-token"), data={"username": s.username, "password": s.password})
        data = json_of(resp, "Seafile login")
        if not data.get("token"):
            raise UploaderError(f"Seafile login failed: {data}", resp.text)
        s.auth_token = data["token"]
        self.ctx.save_settings()
        return s.auth_token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.token()}", "Accept": "application/json"}

    def repo(self) -> str:
        if self.settings.repo_id:
            return self.settings.repo_id
        data = json_of(self.ctx.get(self.api2("default-repo"), headers=self.headers()), "Seafile default library")
        if not data.get("exists") or not data.get("repo_id"):
            raise UploaderError("Seafile: no default library; set the library ID")
        return data["repo_id"]

    def decrypt(self, repo: str) -> None:
        resp = self.ctx.post(self.api2("repos", repo), data={"password": self.settings.encrypted_library_password},
                             headers=self.headers())
        if check_response(resp, "Seafile library decrypt").strip() != '"success"':
            raise UploaderError("Seafile: wrong library password", resp.text)

    def share(self, repo: str, path: str) -> str:
        s = self.settings
        body: dict = {"repo_id": repo, "path": path}
        if s.share_password:
            body["password"] = s.share_password
        if s.share_days_to_expire > 0:
            body["expire_days"] = s.share_days_to_expire
        url = combine_url(server_root(s.api_url), "api/v2.1/share-links") + "/"
        data = json_of(self.ctx.post(url, json=body, headers=self.headers()), "Seafile share link")
        link = data.get("link") if isinstance(data, dict) else None
        if not link:
            raise UploaderError(f"Seafile share link response has no link: {data}")
        return raw_link(link) if s.create_shareable_url_raw else link

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        folder = "/" + parse_folder(s.path or "/")
        folder = folder if folder.endswith("/") else folder + "/"
        repo = self.repo()
        if s.is_library_encrypted and s.encrypted_library_password:
            self.decrypt(repo)
        resp = self.ctx.get(self.api2("repos", repo, "upload-link"), params={"p": folder}, headers=self.headers())
        link = check_response(resp, "Seafile upload link").strip().strip('"')
        if not link.startswith("http"):
            raise UploaderError(f"Seafile upload link is not a URL: {link[:200]}", resp.text)
        body, ctype = multipart_body({"parent_dir": folder}, "file", file_name, read_all(stream))
        sep = "&" if "?" in link else "?"
        resp = self.ctx.post(f"{link}{sep}ret-json=1", data=self.ctx.progress_stream(body),
                             headers={**self.headers(), "Content-Type": ctype})
        data = json_of(resp, "Seafile upload")
        stored = data[0].get("name", file_name) if isinstance(data, list) and data else file_name
        if s.create_shareable_url and not s.is_library_encrypted:
            return UploadResult(url=self.share(repo, folder + stored), response=resp.text)
        return UploadResult(is_url_expected=False, response=resp.text)


def test_connection(settings: SeafileSettings, ctx: UploadContext) -> str:
    up = SeafileUploader(settings, ctx)
    pong = check_response(ctx.get(up.api2("auth/ping"), headers=up.headers()), "Seafile token check").strip()
    if pong != '"pong"':
        raise UploaderError(f"Seafile token check failed: {pong[:200]}")
    info = json_of(ctx.get(up.api2("account/info"), headers=up.headers()), "Seafile account info")
    repo = up.repo()
    return f"Logged in as {info.get('email')}; library {repo}; used {int(info.get('usage', 0)) / 1024 ** 2:.1f} MiB"
