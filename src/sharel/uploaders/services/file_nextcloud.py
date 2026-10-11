"""Nextcloud: WebDAV upload to ``remote.php/dav/files/<user>/...`` and a public link from the OCS v2 share API.

Same behaviour and options as ownCloud (ShareX uses one implementation for both); see file_owncloud.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploadContext
from ..registry import register, setting
from .file_owncloud import WebDAVCloudUploader, check_connection


@dataclass
class NextcloudSettings:
    host: str = setting("", label="Host", help="Server URL, e.g. https://cloud.example.com")
    username: str = setting("", label="Username")
    password: str = setting("", label="App password", secret=True,
                            help="Settings > Security > Devices & sessions > Create new app password")
    path: str = setting("/", label="Path", help="Folder to upload into, name-parser patterns allowed")
    create_share: bool = setting(True, label="Create a public share link",
                                 help="Without a share link the upload has no URL")
    direct_link: bool = setting(False, label="Direct link (download URL)")
    compatibility_81: bool = setting(True, label="Use /download for direct links")
    use_preview_links: bool = setting(False, label="Preview links for images (/preview)")
    append_file_name_to_url: bool = setting(False, label="Append file name to direct links")
    auto_expire: bool = setting(False, label="Auto-expire share")
    expiry_time: int = setting(7, label="Expire after (days)")
    create_missing_directories: bool = setting(True, label="Create missing folders")


@register("file", "Nextcloud", NextcloudSettings, title="Nextcloud", homepage="https://nextcloud.com/")
class NextcloudUploader(WebDAVCloudUploader):
    ocs_version = "v2"
    product = "Nextcloud"


def test_connection(settings: NextcloudSettings, ctx: UploadContext) -> str:
    return check_connection(NextcloudUploader(settings, ctx))
