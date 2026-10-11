"""Shared folder (ShareX SharedFolderUploader.cs + LocalhostAccount.cs): copy the file into a local or mounted
folder (a web server's document root, an NFS/SMB mount, a Syncthing or Dropbox folder...) and build its link
from an HTTP homepage, or a ``file://`` URL when no homepage is set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadContext, UploaderError, UploadResult, stream_size
from ..registry import register, setting
from ._file_helpers import (combine_url, data_type_for, parse_pattern, remove_prefixes, url_encode, url_encode_path,
                            without_extension)

REMOTE_PROTOCOLS = ["http", "https", "file", "ftp", "ftps"]
CHUNK = 256 * 1024


@dataclass
class LocalhostAccount:
    name: str = setting("New account", label="Name")
    localhost_root: str = setting("", label="Root folder", kind="folder",
                                  help="e.g. /var/www/html or a mounted share; ~ and $VARS are expanded")
    port: int = setting(80, label="Port", help="Added to the URL when not 80")
    sub_folder_path: str = setting("", label="Sub-folder path",
                                   help="Name-parser patterns allowed, e.g. screenshots/%y/%mo; %host = root folder")
    http_home_path: str = setting("", label="HTTP homepage",
                                  help="e.g. example.com/shots. Empty: a file:// URL of the copied file")
    http_home_path_auto_add_sub_folder_path: bool = setting(True, label="Append sub-folder path to homepage")
    http_home_path_no_extension: bool = setting(False, label="Remove file extension from URL")
    remote_protocol: str = setting("http", label="URL protocol", choices=REMOTE_PROTOCOLS,
                                   help="Used with the homepage; without a homepage the URL is always file://")

    def __str__(self) -> str:
        return f"{self.name} - {self.localhost_root}:{self.port}"


@dataclass
class SharedFolderSettings:
    localhost_account_list: list[LocalhostAccount] = setting(label="Accounts", default_factory=list)  # noqa: RUF009
    localhost_selected_images: int = setting(0, label="Account for images")
    localhost_selected_text: int = setting(0, label="Account for text")
    localhost_selected_files: int = setting(0, label="Account for files")


def expand_root(account: LocalhostAccount) -> str:
    return os.path.expandvars(os.path.expanduser(account.localhost_root.strip()))


def get_sub_folder_path(account: LocalhostAccount) -> str:
    pattern = account.sub_folder_path.replace("%host", expand_root(account)).replace("\\", "/")
    return parse_pattern(pattern, NameParserType.FolderPath).strip("/")


def get_http_home_path(account: LocalhostAccount) -> tuple[str, str | None, bool]:
    """(homepage without scheme, scheme typed into it or None, append sub-folder) — '@' prefix = don't append."""
    home = account.http_home_path.strip()
    auto_add = account.http_home_path_auto_add_sub_folder_path
    if home.startswith("@"):  # ShareX: deprecated '@' prefix turns off appending the sub-folder
        home = home[1:]
        auto_add = False
    scheme = home.split("://", 1)[0] if "://" in home[:16] else None
    home = remove_prefixes(home)
    return parse_pattern(home.replace("%host", expand_root(account)), NameParserType.URL), scheme, auto_add


def get_uri_path(account: LocalhostAccount, file_name: str, sub_folder_path: str | None = None) -> str:
    """LocalhostAccount.GetUriPath."""
    if not account.localhost_root:
        return ""
    if account.http_home_path_no_extension:
        file_name = without_extension(file_name)
    file_name = url_encode(file_name)
    sub = url_encode_path(get_sub_folder_path(account) if sub_folder_path is None else sub_folder_path)
    home, typed_scheme, auto_add = get_http_home_path(account)
    if not home:
        protocol = "file"
        path = Path(expand_root(account)).resolve().as_uri()[len("file://"):]
    else:
        protocol = typed_scheme or account.remote_protocol or "http"
        path = home
    if account.port != 80 and home:
        authority, slash, rest = path.partition("/")
        path = f"{authority}:{account.port}{slash}{rest}"
    if auto_add:
        path = combine_url(path, sub)
    path = combine_url(path, file_name)
    prefix = f"{protocol}://"
    return path if path.startswith(prefix) else prefix + path


def get_localhost_path(account: LocalhostAccount, file_name: str, sub_folder_path: str | None = None) -> Path:
    sub = get_sub_folder_path(account) if sub_folder_path is None else sub_folder_path
    return Path(expand_root(account), sub, file_name)


def select_account(settings: SharedFolderSettings, file_name: str, data_type: object = None) -> LocalhostAccount:
    accounts = settings.localhost_account_list
    if not accounts:
        raise UploaderError("No shared folder account is configured")
    kind = data_type_for(file_name, data_type)
    index = {"Image": settings.localhost_selected_images, "Text": settings.localhost_selected_text}.get(
        kind, settings.localhost_selected_files)
    if not 0 <= index < len(accounts):
        raise UploaderError(f"Shared folder account #{index + 1} does not exist")
    return accounts[index]


@register("file", "SharedFolder", SharedFolderSettings, title="Shared folder")
class SharedFolderUploader(FileUploader):
    data_type: object = None  # EDataType of the task (picks the image/text/file account)

    def validate(self) -> list[str]:
        return [] if self.settings.localhost_account_list else ["add a shared folder account first"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        account = select_account(self.settings, file_name, self.data_type)
        if not account.localhost_root:
            raise UploaderError(f"Shared folder account '{account.name}' has no root folder")
        if Path(file_name).name != file_name or file_name in ("", ".", ".."):
            raise UploaderError(f"Invalid file name: {file_name!r}")
        sub = get_sub_folder_path(account)
        target = get_localhost_path(account, file_name, sub)
        total = stream_size(stream)
        done = 0
        partial = target.with_name(f".{target.name}.sharel-part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(partial, "wb") as out:
                while True:
                    self.ctx.check_cancelled()
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    self.ctx.report(done, total)
            os.replace(partial, target)
        except OSError as e:
            raise UploaderError(f"Copy to {target} failed: {e}") from e
        finally:
            if partial.exists():
                partial.unlink(missing_ok=True)
        return UploadResult(url=get_uri_path(account, file_name, sub))


def test_connection(settings: SharedFolderSettings, ctx: UploadContext) -> str:
    account = select_account(settings, "", "File")
    root = Path(expand_root(account))
    if not root.is_dir():
        raise UploaderError(f"Root folder {root} does not exist (is the share mounted?)")
    if not os.access(root, os.W_OK):
        raise UploaderError(f"Root folder {root} is not writable")
    return f"{root} is writable; example URL: {get_uri_path(account, 'example.png')}"
