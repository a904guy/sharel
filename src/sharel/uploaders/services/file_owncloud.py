"""ownCloud (ShareX OwnCloud.cs) — and the shared WebDAV + OCS implementation that Nextcloud uses too.

1. ``PUT <host>/remote.php/dav/files/<user>/<path>/<file>`` with HTTP basic auth (use an app password when
   two-factor authentication is on); missing folders are created with ``MKCOL``.
2. Optionally ``POST <host>/ocs/v{1,2}.php/apps/files_sharing/api/v1/shares`` (shareType 3 = public link,
   permissions 1 = read, optional expireDate) and return the share URL, with ShareX's direct-link
   (``/download``, or ``&download`` for ownCloud < 8.1) and preview-link (``/preview`` for images) options.
   Without sharing the upload has no URL.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import BinaryIO
from xml.etree import ElementTree

import requests

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._file_helpers import combine_url, fix_prefix, is_image_file, mime_type, parse_folder, url_encode, url_encode_path

OCS_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}


@dataclass
class OwnCloudSettings:
    host: str = setting("", label="Host", help="Server URL, e.g. https://cloud.example.com")
    username: str = setting("", label="Username")
    password: str = setting("", label="Password", secret=True, help="An app password is recommended")
    path: str = setting("/", label="Path", help="Folder to upload into, name-parser patterns allowed")
    create_share: bool = setting(True, label="Create a public share link",
                                 help="Without a share link the upload has no URL")
    direct_link: bool = setting(False, label="Direct link (download URL)")
    compatibility_81: bool = setting(True, label="ownCloud 8.1+ compatible links (/download)")
    use_preview_links: bool = setting(False, label="Preview links for images (/preview)")
    append_file_name_to_url: bool = setting(False, label="Append file name to direct links")
    auto_expire: bool = setting(False, label="Auto-expire share")
    expiry_time: int = setting(7, label="Expire after (days)")
    create_missing_directories: bool = setting(True, label="Create missing folders")


class WebDAVCloudUploader(FileUploader):
    """ownCloud / Nextcloud: WebDAV upload + OCS share API."""

    ocs_version = "v1"  # ownCloud's classic endpoint; Nextcloud uses v2
    product = "ownCloud"

    # -- helpers --
    @property
    def base(self) -> str:
        return fix_prefix(self.settings.host.strip().rstrip("/"))

    @property
    def auth(self) -> tuple[str, str]:
        return (self.settings.username, self.settings.password)

    def dav_root(self) -> str:
        return combine_url(self.base, "remote.php/dav/files", url_encode(self.settings.username))

    def folder(self) -> str:
        return "/" + parse_folder(self.settings.path or "/")

    def validate(self) -> list[str]:
        s = self.settings
        if not s.host:
            return [f"{self.product} host is empty"]
        if not s.username or not s.password:
            return [f"{self.product} credentials are empty"]
        if s.create_share and s.auto_expire and s.expiry_time <= 0:
            return ["Auto expire time is invalid"]
        return []

    def _mkcols(self, folder: str) -> None:
        parts = [p for p in folder.split("/") if p]
        for i in range(len(parts)):
            url = combine_url(self.dav_root(), url_encode_path("/".join(parts[: i + 1]))) + "/"
            resp = self.ctx.request("MKCOL", url, auth=self.auth, headers={"OCS-APIRequest": "true"})
            # 201 created, 405 already exists
            if resp.status_code not in (201, 405) and not 200 <= resp.status_code < 300:
                raise UploaderError(f"{self.product}: could not create folder /{'/'.join(parts[: i + 1])}: "
                                    f"HTTP {resp.status_code}", resp.text)

    def put(self, stream: BinaryIO, folder: str, file_name: str) -> None:
        url = combine_url(self.dav_root(), url_encode_path(folder.strip("/")), url_encode(file_name))
        headers = {"OCS-APIRequest": "true", "Content-Type": mime_type(file_name)}
        start = stream.tell()
        resp = self.ctx.put(url, data=self.ctx.progress_stream(stream), auth=self.auth, headers=headers)
        if resp.status_code in (404, 409) and self.settings.create_missing_directories and folder.strip("/"):
            self._mkcols(folder)
            stream.seek(start)
            resp = self.ctx.put(url, data=self.ctx.progress_stream(stream), auth=self.auth, headers=headers)
        if resp.status_code == 401:
            raise UploaderError(f"{self.product}: login failed (wrong username or password)", resp.text)
        if not 200 <= resp.status_code < 300:
            raise UploaderError(f"{self.product} upload failed: HTTP {resp.status_code} {resp.reason}", resp.text)

    def share(self, path: str, file_name: str) -> str:
        """OwnCloud.ShareFileAsync: create a read-only public link and apply the link options."""
        s = self.settings
        data = {"path": path, "shareType": "3", "permissions": "1"}
        if s.auto_expire:
            if s.expiry_time <= 0:
                raise UploaderError("Auto expire time is invalid")
            expire = _dt.datetime.now(_dt.UTC).date() + _dt.timedelta(days=s.expiry_time)
            data["expireDate"] = expire.strftime("%Y-%m-%d")
        url = combine_url(self.base, f"ocs/{self.ocs_version}.php/apps/files_sharing/api/v1/shares") + "?format=json"
        resp = self.ctx.post(url, data=data, auth=self.auth, headers=OCS_HEADERS)
        try:
            ocs = resp.json()["ocs"]
            meta = ocs["meta"]
        except (ValueError, KeyError, TypeError) as e:
            raise UploaderError(f"{self.product} share failed: HTTP {resp.status_code}: {resp.text[:300]}",
                                resp.text) from e
        if meta.get("statuscode") not in (100, 200) or not isinstance(ocs.get("data"), dict):
            raise UploaderError(f"{self.product} share failed: status {meta.get('status')}, code "
                                f"{meta.get('statuscode')}, message {meta.get('message')}", resp.text)
        link = ocs["data"].get("url", "")
        if not link:
            raise UploaderError(f"{self.product} share response has no URL", resp.text)
        if s.use_preview_links and is_image_file(path):
            link += "/preview"
        elif s.direct_link:
            link += "/download" if s.compatibility_81 else "&download"
            if s.append_file_name_to_url:
                link = combine_url(link, url_encode(file_name))
        return link

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        folder = self.folder()
        self.put(stream, folder, file_name)
        if not self.settings.create_share:
            return UploadResult(is_url_expected=False)
        return UploadResult(url=self.share(combine_url(folder, file_name), file_name))


def _propfind(uploader: WebDAVCloudUploader, url: str) -> requests.Response:
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
            '<d:quota-available-bytes/></d:prop></d:propfind>')
    return uploader.ctx.request("PROPFIND", url, auth=uploader.auth, data=body,
                                headers={"Depth": "0", "Content-Type": "application/xml"})


def check_connection(uploader: WebDAVCloudUploader) -> str:
    problems = uploader.validate()
    if problems:
        raise UploaderError("; ".join(problems))
    resp = _propfind(uploader, uploader.dav_root() + "/")
    if resp.status_code == 401:
        raise UploaderError(f"{uploader.product}: login failed (wrong username or password)")
    if resp.status_code != 207:
        raise UploaderError(f"{uploader.product}: WebDAV PROPFIND failed: HTTP {resp.status_code}", resp.text)
    folder = uploader.folder()
    state = "exists"
    if folder.strip("/"):
        r = _propfind(uploader, combine_url(uploader.dav_root(), url_encode_path(folder.strip("/"))) + "/")
        if r.status_code == 404:
            if not uploader.settings.create_missing_directories:
                raise UploaderError(f"{uploader.product}: folder {folder} does not exist")
            state = "will be created on first upload"
        elif r.status_code != 207:
            raise UploaderError(f"{uploader.product}: folder {folder}: HTTP {r.status_code}", r.text)
    quota = ""
    try:
        for el in ElementTree.fromstring(resp.content).iter("{DAV:}quota-available-bytes"):
            if el.text and el.text.lstrip("-").isdigit() and int(el.text) >= 0:
                quota = f"; {int(el.text) / 1024 ** 3:.1f} GiB free"
    except ElementTree.ParseError:
        pass
    return f"Connected to {uploader.base} as {uploader.settings.username}; folder {folder} {state}{quota}"


@register("file", "OwnCloud", OwnCloudSettings, title="ownCloud", homepage="https://owncloud.com/")
class OwnCloudUploader(WebDAVCloudUploader):
    ocs_version = "v1"
    product = "ownCloud"


def test_connection(settings: OwnCloudSettings, ctx: UploadContext) -> str:
    return check_connection(OwnCloudUploader(settings, ctx))
