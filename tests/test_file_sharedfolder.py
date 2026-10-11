"""Shared folder destination: copies into a real directory and builds ShareX-style links."""
from __future__ import annotations

import datetime as _dt
import io
import threading

import pytest

from sharel.uploaders.base import UploadCancelled, UploadContext, UploaderError
from sharel.uploaders.services import file_sharedfolder
from sharel.uploaders.services.file_sharedfolder import LocalhostAccount, SharedFolderSettings, SharedFolderUploader


def test_copy_with_http_homepage(tmp_path):
    root = tmp_path / "www"
    progress = []
    acc = LocalhostAccount(localhost_root=str(root), sub_folder_path="shots/%y", http_home_path="example.com/i",
                           remote_protocol="https")
    up = SharedFolderUploader(SharedFolderSettings(localhost_account_list=[acc]),
                              UploadContext(progress=lambda d, t: progress.append((d, t))))
    data = b"x" * 700_000
    res = up.upload(io.BytesIO(data), "a b.png")
    year = f"{_dt.datetime.now().year:04d}"
    assert (root / "shots" / year / "a b.png").read_bytes() == data
    assert res.url == f"https://example.com/i/shots/{year}/a%20b.png"
    assert progress[-1] == (len(data), len(data))
    assert not list((root / "shots" / year).glob(".*sharel-part"))


def test_file_url_when_no_homepage(tmp_path):
    root = tmp_path / "my share"
    acc = LocalhostAccount(localhost_root=str(root), sub_folder_path="t", http_home_path_no_extension=True)
    res = SharedFolderUploader(SharedFolderSettings(localhost_account_list=[acc]), UploadContext()).upload(
        io.BytesIO(b"t"), "note.txt")
    assert (root / "t" / "note.txt").read_bytes() == b"t"
    assert res.url == (root / "t").resolve().as_uri() + "/note"


def test_port_at_prefix_and_account_by_type(tmp_path):
    a = LocalhostAccount(localhost_root=str(tmp_path / "a"), http_home_path="@lan.box/up", port=8080,
                         sub_folder_path="ignored-in-url")
    b = LocalhostAccount(localhost_root=str(tmp_path / "b"))
    up = SharedFolderUploader(SharedFolderSettings(localhost_account_list=[a, b], localhost_selected_images=0,
                                                   localhost_selected_files=1), UploadContext())
    res = up.upload(io.BytesIO(b"i"), "i.png")
    assert res.url == "http://lan.box:8080/up/i.png" and (tmp_path / "a" / "ignored-in-url" / "i.png").exists()
    up.upload(io.BytesIO(b"z"), "z.zip")
    assert (tmp_path / "b" / "z.zip").read_bytes() == b"z"


def test_rejects_path_traversal_and_cancel(tmp_path):
    acc = LocalhostAccount(localhost_root=str(tmp_path / "r"))
    up = SharedFolderUploader(SharedFolderSettings(localhost_account_list=[acc]), UploadContext())
    with pytest.raises(UploaderError, match="Invalid file name"):
        up.upload(io.BytesIO(b"x"), "../escape.txt")
    cancel = threading.Event()
    cancel.set()
    up = SharedFolderUploader(SharedFolderSettings(localhost_account_list=[acc]), UploadContext(cancel=cancel))
    with pytest.raises(UploadCancelled):
        up.upload(io.BytesIO(b"x"), "c.txt")
    assert not (tmp_path / "r" / "c.txt").exists()


def test_test_connection(tmp_path):
    acc = LocalhostAccount(localhost_root=str(tmp_path), http_home_path="h.example")
    msg = file_sharedfolder.test_connection(SharedFolderSettings(localhost_account_list=[acc]), UploadContext())
    assert "is writable" in msg and "http://h.example/example.png" in msg
    missing = LocalhostAccount(localhost_root=str(tmp_path / "unmounted"))
    with pytest.raises(UploaderError, match="does not exist"):
        file_sharedfolder.test_connection(SharedFolderSettings(localhost_account_list=[missing]), UploadContext())
