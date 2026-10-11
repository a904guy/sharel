"""ownCloud / Nextcloud against a real WebDAV server (wsgidav on cheroot) with an OCS share endpoint."""
from __future__ import annotations

import datetime as _dt
import io

import pytest
from _fileproto_servers import CloudServer

from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services import file_nextcloud, file_owncloud
from sharel.uploaders.services.file_nextcloud import NextcloudSettings, NextcloudUploader
from sharel.uploaders.services.file_owncloud import OwnCloudSettings, OwnCloudUploader


@pytest.fixture
def cloud(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    with CloudServer(root) as srv:
        yield srv


def test_nextcloud_upload_creates_folders_and_shares(cloud):
    progress = []
    st = NextcloudSettings(host=cloud.url, username="alice", password="app-pass", path="/Shots/%y")
    res = NextcloudUploader(st, UploadContext(progress=lambda d, t: progress.append((d, t)))).upload(
        io.BytesIO(b"PNGDATA" * 1000), "my shot.png")
    year = f"{_dt.datetime.now().year:04d}"
    assert (cloud.root / "Shots" / year / "my shot.png").read_bytes() == b"PNGDATA" * 1000
    assert ("MKCOL", "/remote.php/dav/files/alice/Shots/") in cloud.methods
    share = cloud.shares[-1]
    assert share["path"] == f"/Shots/{year}/my shot.png" and share["shareType"] == "3"
    assert share["permissions"] == "1" and share["_version"] == 2 and share["_ocs_api_request"] == "true"
    assert "expireDate" not in share and share["_query"] == "format=json"
    assert res.url == f"{cloud.url}/s/tok1" and progress[-1][0] == 7000


def test_owncloud_direct_link_expiry_v1(cloud):
    st = OwnCloudSettings(host=cloud.url, username="alice", password="app-pass", path="/", direct_link=True,
                          append_file_name_to_url=True, auto_expire=True, expiry_time=3)
    res = OwnCloudUploader(st, UploadContext()).upload(io.BytesIO(b"zip"), "a b.zip")
    share = cloud.shares[-1]
    expected = (_dt.datetime.now(_dt.UTC).date() + _dt.timedelta(days=3)).strftime("%Y-%m-%d")
    assert share["_version"] == 1 and share["expireDate"] == expected and share["path"] == "/a b.zip"
    assert res.url == f"{cloud.url}/s/tok1/download/a%20b.zip"
    assert (cloud.root / "a b.zip").read_bytes() == b"zip"


def test_preview_link_for_images_and_legacy_direct(cloud):
    st = NextcloudSettings(host=cloud.url, username="alice", password="app-pass", use_preview_links=True,
                           direct_link=True, compatibility_81=False)
    up = NextcloudUploader(st, UploadContext())
    assert up.upload(io.BytesIO(b"i"), "i.png").url.endswith("/s/tok1/preview")
    assert up.upload(io.BytesIO(b"t"), "t.txt").url.endswith("/s/tok2&download")


def test_without_share_no_url_expected(cloud):
    st = NextcloudSettings(host=cloud.url, username="alice", password="app-pass", create_share=False)
    res = NextcloudUploader(st, UploadContext()).upload(io.BytesIO(b"x"), "x.bin")
    assert res.is_success and not res.url and res.is_url_expected is False and not cloud.shares
    assert (cloud.root / "x.bin").exists()


def test_bad_password(cloud):
    st = NextcloudSettings(host=cloud.url, username="alice", password="wrong")
    with pytest.raises(UploaderError, match="login failed"):
        NextcloudUploader(st, UploadContext()).upload(io.BytesIO(b"x"), "x.bin")


def test_missing_folder_without_create(cloud):
    st = NextcloudSettings(host=cloud.url, username="alice", password="app-pass", path="/nope",
                           create_missing_directories=False)
    with pytest.raises(UploaderError, match="HTTP"):
        NextcloudUploader(st, UploadContext()).upload(io.BytesIO(b"x"), "x.bin")


def test_test_connection(cloud):
    msg = file_nextcloud.test_connection(NextcloudSettings(host=cloud.url, username="alice", password="app-pass",
                                                           path="/later"), UploadContext())
    assert "Connected" in msg and "will be created" in msg
    (cloud.root / "there").mkdir()
    msg = file_owncloud.test_connection(OwnCloudSettings(host=cloud.url, username="alice", password="app-pass",
                                                         path="there"), UploadContext())
    assert "folder /there exists" in msg
    with pytest.raises(UploaderError, match="login failed"):
        file_owncloud.test_connection(OwnCloudSettings(host=cloud.url, username="alice", password="no"),
                                      UploadContext())
