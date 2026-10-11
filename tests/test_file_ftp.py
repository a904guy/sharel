"""FTP / FTPS / SFTP destination against real local servers."""
from __future__ import annotations

import datetime as _dt
import io
import threading

import pytest
from _fileproto_servers import MiniFTPS, SFTPTestServer, pyftpd, self_signed_cert

from sharel.uploaders import registry
from sharel.uploaders.base import UploadCancelled, UploadContext, UploaderError
from sharel.uploaders.services import file_ftp
from sharel.uploaders.services.file_ftp import FTPAccount, FTPSettings, FTPUploader, get_uri_path

PAYLOAD = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 600  # ~150 KB, several blocks


def _uploader(*accounts: FTPAccount, **kw) -> tuple[FTPUploader, list[tuple[int, int]]]:
    progress: list[tuple[int, int]] = []
    ctx = UploadContext(progress=lambda d, t: progress.append((d, t)), timeout=10)
    up = FTPUploader(FTPSettings(ftp_account_list=list(accounts), **kw), ctx)
    return up, progress


# ---- URL building (FTPAccount.GetUriPath) -------------------------------------------------------

@pytest.mark.parametrize(("account", "sub", "file_name", "expected"), [
    (FTPAccount(host="ftp.example.com"), "shots/2026", "a b.png", "http://example.com/shots/2026/a%20b.png"),
    (FTPAccount(host="ftp.example.com", http_home_path_auto_add_sub_folder_path=False), "shots", "x.png",
     "http://example.com/x.png"),
    (FTPAccount(host="h", http_home_path="cdn.example.com:8080/i", browser_protocol="https"), "s", "x.png",
     "https://cdn.example.com:8080/i/s/x.png"),
    (FTPAccount(host="h", http_home_path="example.com/view.php?file="), "s", "x.png",
     "http://example.com/view.php?file=/s/x.png"),  # CombineURL("file=", "s", ...)
    (FTPAccount(host="h", http_home_path="example.com/view.php?file=",
                http_home_path_auto_add_sub_folder_path=False), "s", "x.png",
     "http://example.com/view.php?file=x.png"),
    (FTPAccount(host="h", http_home_path="%host/u", http_home_path_no_extension=True), "", "x.tar.png",
     "http://h/u/x.tar"),
    (FTPAccount(host="h", http_home_path="https://files.example.org/"), "d", "x.png",
     "https://files.example.org/d/x.png"),
])
def test_uri_path_matches_sharex(account, sub, file_name, expected):
    assert get_uri_path(account, file_name, sub) == expected


def test_sub_folder_pattern_is_parsed():
    acc = FTPAccount(host="ftp.h", sub_folder_path="up/%y/%mo")
    now = _dt.datetime.now()
    assert file_ftp.get_sub_folder_path(acc) == f"up/{now.year:04d}/{now.month:02d}"


def test_registered_as_file_ftp():
    info = registry.get("file", "FTP")
    assert info is not None and info.cls is FTPUploader and info.settings_cls is FTPSettings


# ---- plain FTP (pyftpdlib) ----------------------------------------------------------------------

def test_ftp_passive_upload_creates_dirs_and_reports_progress(tmp_path):
    with pyftpd(tmp_path) as (port, handler):
        acc = FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret",
                         sub_folder_path="shots/%y", http_home_path="i.example.com", browser_protocol="https")
        up, progress = _uploader(acc)
        res = up.upload(io.BytesIO(PAYLOAD), "shot 1.png")
    year = f"{_dt.datetime.now().year:04d}"
    assert (tmp_path / "shots" / year / "shot 1.png").read_bytes() == PAYLOAD
    assert res.url == f"https://i.example.com/shots/{year}/shot%201.png" and res.is_success
    assert "MKD" in handler.commands_seen and ("PASV" in handler.commands_seen or "EPSV" in handler.commands_seen)
    assert "STOR" in handler.commands_seen
    assert progress and progress[-1][0] == len(PAYLOAD) and progress[-1][1] == len(PAYLOAD)


def test_ftp_active_mode(tmp_path):
    with pyftpd(tmp_path) as (port, handler):
        acc = FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret", is_active=True)
        up, _ = _uploader(acc)
        res = up.upload(io.BytesIO(b"hello"), "a.txt")
    assert (tmp_path / "a.txt").read_bytes() == b"hello"
    assert "PORT" in handler.commands_seen or "EPRT" in handler.commands_seen
    assert res.url == "http://127.0.0.1/a.txt"


def test_ftp_missing_dir_without_create_fails(tmp_path):
    with pyftpd(tmp_path) as (port, _h):
        acc = FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret",
                         sub_folder_path="nope/deeper", create_missing_directories=False)
        up, _ = _uploader(acc)
        with pytest.raises(UploaderError, match="550"):
            up.upload(io.BytesIO(b"x"), "a.txt")
    assert not (tmp_path / "nope").exists()


def test_ftp_bad_password(tmp_path):
    with pyftpd(tmp_path) as (port, _h):
        up, _ = _uploader(FTPAccount(host="127.0.0.1", port=port, username="alice", password="wrong"))
        with pytest.raises(UploaderError, match="530"):
            up.upload(io.BytesIO(b"x"), "a.txt")


def test_ftp_account_selection_by_data_type_and_override(tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    with pyftpd(tmp_path) as (port, _h):
        acc_a = FTPAccount(name="A", host="127.0.0.1", port=port, username="alice", password="s3cret",
                           sub_folder_path="a")
        acc_b = FTPAccount(name="B", host="127.0.0.1", port=port, username="alice", password="s3cret",
                           sub_folder_path="b")
        up, _ = _uploader(acc_a, acc_b, ftp_selected_image=1, ftp_selected_file=0)
        up.upload(io.BytesIO(b"img"), "i.png")  # extension guess: image -> account B
        up.upload(io.BytesIO(b"zip"), "f.zip")  # file -> account A
        up.data_type = "File"
        up.upload(io.BytesIO(b"img2"), "j.png")  # explicit data type wins over the extension
        up.account_index = 1
        up.upload(io.BytesIO(b"zip2"), "g.zip")  # task override
    assert (b_dir / "i.png").read_bytes() == b"img" and (a_dir / "f.zip").read_bytes() == b"zip"
    assert (a_dir / "j.png").read_bytes() == b"img2" and (b_dir / "g.zip").read_bytes() == b"zip2"


def test_dispatch_override_sets_account_index(tmp_path):
    from sharel.core.config import TaskSettings
    from sharel.uploaders.config import UploadersConfig
    from sharel.uploaders.dispatch import Destination, create

    cfg = UploadersConfig()
    cfg.services["file/FTP"] = {"ftp_account_list": [{"host": "a"}, {"host": "b"}]}
    ts = TaskSettings()
    ts.override_ftp = True
    ts.ftp_index = 1
    up = create(Destination("file", "FTP"), cfg, UploadContext(), ts)
    assert up.account_index == 1 and up.settings.ftp_account_list[1].host == "b"


def test_ftp_cancel_aborts(tmp_path):
    cancel = threading.Event()
    with pyftpd(tmp_path) as (port, _h):
        acc = FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret")

        def progress(done, total):
            if done > 0:
                cancel.set()
        ctx = UploadContext(progress=progress, cancel=cancel, timeout=10)
        up = FTPUploader(FTPSettings(ftp_account_list=[acc]), ctx)
        with pytest.raises(UploadCancelled):
            up.upload(io.BytesIO(PAYLOAD * 4), "big.bin")


def test_ftp_test_connection(tmp_path):
    with pyftpd(tmp_path) as (port, _h):
        acc = FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret", sub_folder_path="t/u")
        msg = file_ftp.test_connection(FTPSettings(ftp_account_list=[acc]), UploadContext(timeout=10))
    assert "Connected" in msg and "/t/u" in msg and (tmp_path / "t" / "u").is_dir()


# ---- FTPS (explicit / implicit, certificates) ---------------------------------------------------

@pytest.fixture
def tls_files(tmp_path):
    certs = tmp_path / "certs"
    certs.mkdir()
    return self_signed_cert(certs)


@pytest.mark.parametrize("mode", ["Explicit", "Implicit"])
def test_ftps_upload(tmp_path, tls_files, mode):
    root = tmp_path / "root"
    root.mkdir()
    cert, key = tls_files
    with MiniFTPS(root, cert, key, implicit=mode == "Implicit") as srv:
        acc = FTPAccount(protocol="FTPS", ftps_encryption=mode, host="127.0.0.1", port=srv.port, username="bob",
                         password="pw", sub_folder_path="enc/dir", http_home_path="secure.example.net")
        up, progress = _uploader(acc)
        res = up.upload(io.BytesIO(PAYLOAD), "s.png")
    assert (root / "enc" / "dir" / "s.png").read_bytes() == PAYLOAD
    assert res.url == "http://secure.example.net/enc/dir/s.png"
    assert srv.tls_data_transfers >= 1  # PROT P: the data channel was TLS
    assert ("AUTH" in srv.log) == (mode == "Explicit")
    assert "PBSZ" in srv.log and "PROT" in srv.log and srv.log.count("MKD") == 2
    assert progress[-1][0] == len(PAYLOAD)


def test_ftps_certificate_verification(tmp_path, tls_files):
    root = tmp_path / "root"
    root.mkdir()
    cert, key = tls_files
    with MiniFTPS(root, cert, key) as srv:
        base = {"protocol": "FTPS", "host": "127.0.0.1", "port": srv.port, "username": "bob", "password": "pw"}
        # Verification on, system trust store: the self-signed certificate is rejected
        up, _ = _uploader(FTPAccount(**base, ftps_validate_certificate=True))
        with pytest.raises(UploaderError, match=r"(?i)certificate"):
            up.upload(io.BytesIO(b"x"), "a.txt")
        # Verification on, trusting that certificate: accepted
        up, _ = _uploader(FTPAccount(**base, ftps_validate_certificate=True, ftps_ca_certificate=str(cert)))
        up.upload(io.BytesIO(b"verified"), "b.txt")
    assert (root / "b.txt").read_bytes() == b"verified" and not (root / "a.txt").exists()


def test_ftps_client_certificate(tmp_path, tls_files):
    root = tmp_path / "root"
    root.mkdir()
    cert, key = tls_files
    client_cert, client_key = self_signed_cert(tmp_path / "certs", "client")
    bundle = tmp_path / "client.pem"
    bundle.write_bytes(client_cert.read_bytes() + client_key.read_bytes())
    with MiniFTPS(root, cert, key, client_ca=client_cert) as srv:
        acc = FTPAccount(protocol="FTPS", host="127.0.0.1", port=srv.port, username="bob", password="pw",
                         ftps_certificate_location=str(bundle))
        up, _ = _uploader(acc)
        up.upload(io.BytesIO(b"with client cert"), "c.txt")
    assert (root / "c.txt").read_bytes() == b"with client cert"
    assert srv.client_cert_subjects and "localhost" in srv.client_cert_subjects[0]


def test_ftps_test_connection(tmp_path, tls_files):
    root = tmp_path / "root"
    root.mkdir()
    cert, key = tls_files
    with MiniFTPS(root, cert, key, implicit=True) as srv:
        acc = FTPAccount(protocol="FTPS", ftps_encryption="Implicit", host="127.0.0.1", port=srv.port,
                         username="bob", password="pw", sub_folder_path="x")
        msg = file_ftp.test_connection(FTPSettings(ftp_account_list=[acc]), UploadContext(timeout=10))
    assert msg.startswith("Connected to ftps://127.0.0.1") and (root / "x").is_dir()


# ---- SFTP (paramiko server) ---------------------------------------------------------------------

def test_sftp_password_upload_creates_dirs(tmp_path):
    with SFTPTestServer(tmp_path) as srv:
        acc = FTPAccount(protocol="SFTP", host="127.0.0.1", port=srv.port, username="carol", password="pw",
                         sub_folder_path="pub/%y", http_home_path="files.example.com/~carol",
                         browser_protocol="https")
        up, progress = _uploader(acc)
        res = up.upload(io.BytesIO(PAYLOAD), "s f.png")
    year = f"{_dt.datetime.now().year:04d}"
    assert (tmp_path / "pub" / year / "s f.png").read_bytes() == PAYLOAD
    assert res.url == f"https://files.example.com/~carol/pub/{year}/s%20f.png"
    assert srv.auth_methods == ["password"] and progress[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_sftp_private_key_with_passphrase(tmp_path):
    import paramiko
    key = paramiko.RSAKey.generate(2048)
    key_file = tmp_path / "id_rsa"
    key.write_private_key_file(str(key_file), password="phrase")
    root = tmp_path / "root"
    root.mkdir()
    with SFTPTestServer(root, password=None, authorized_key=key) as srv:
        acc = FTPAccount(protocol="SFTP", host="127.0.0.1", port=srv.port, username="carol",
                         keypath=str(key_file), passphrase="phrase",
                         sftp_host_key_fingerprint=file_ftp.key_fingerprint(srv.host_key))
        up, _ = _uploader(acc)
        res = up.upload(io.BytesIO(b"keyed"), "k.txt")
    assert (root / "k.txt").read_bytes() == b"keyed" and res.url == "http://127.0.0.1/k.txt"
    assert "publickey" in srv.auth_methods


def test_sftp_host_key_pin_mismatch(tmp_path):
    with SFTPTestServer(tmp_path) as srv:
        acc = FTPAccount(protocol="SFTP", host="127.0.0.1", port=srv.port, username="carol", password="pw",
                         sftp_host_key_fingerprint="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        up, _ = _uploader(acc)
        with pytest.raises(UploaderError, match="mismatch"):
            up.upload(io.BytesIO(b"x"), "x.txt")
    assert not (tmp_path / "x.txt").exists()


def test_sftp_bad_password_and_test_connection(tmp_path):
    with SFTPTestServer(tmp_path) as srv:
        bad = FTPAccount(protocol="SFTP", host="127.0.0.1", port=srv.port, username="carol", password="nope")
        up, _ = _uploader(bad)
        with pytest.raises(UploaderError, match=r"(?i)auth"):
            up.upload(io.BytesIO(b"x"), "x.txt")
        good = FTPAccount(protocol="SFTP", host="127.0.0.1", port=srv.port, username="carol", password="pw",
                          sub_folder_path="a/b")
        msg = file_ftp.test_connection(FTPSettings(ftp_account_list=[bad, good]), UploadContext(timeout=10),
                                       account_index=1)
    assert "Connected to sftp://127.0.0.1" in msg and msg.endswith("/a/b") and (tmp_path / "a" / "b").is_dir()


def test_account_passwords_are_keyring_secrets():
    from sharel.uploaders.config import UploadersConfig, _secret_fields, jsonable_with_secrets_hidden

    cfg = UploadersConfig()
    cfg.services["file/FTP"] = FTPSettings(ftp_account_list=[FTPAccount(password="p0"),
                                                             FTPAccount(password="p1", passphrase="k1")])
    paths = [p for p, _n, _o in _secret_fields(cfg.services["file/FTP"])]
    assert paths == ["ftp_account_list.0.password", "ftp_account_list.0.passphrase",
                     "ftp_account_list.1.password", "ftp_account_list.1.passphrase"]
    data = jsonable_with_secrets_hidden(cfg, keyring_ok=True)
    accounts = data["services"]["file/FTP"]["ftp_account_list"]
    assert accounts[0]["password"] == "@secret" and accounts[1]["passphrase"] == "@secret"
    assert accounts[0]["passphrase"] == ""  # empty secrets stay empty


def test_dispatch_passes_data_type(tmp_path):
    from sharel.core.config import ApplicationConfig, TaskSettings
    from sharel.core.enums import EDataType, FileDestination
    from sharel.uploaders.config import UploadersConfig
    from sharel.uploaders.dispatch import upload_file

    (tmp_path / "img").mkdir()
    (tmp_path / "txt").mkdir()
    with pyftpd(tmp_path) as (port, _h):
        cfg = UploadersConfig()
        cfg.services["file/FTP"] = FTPSettings(ftp_account_list=[
            FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret", sub_folder_path="img"),
            FTPAccount(host="127.0.0.1", port=port, username="alice", password="s3cret", sub_folder_path="txt")],
            ftp_selected_image=0, ftp_selected_text=1)
        ts = TaskSettings()
        ts.text_destination = type(ts.text_destination)("FileUploader")
        ts.text_file_destination = FileDestination.FTP
        res, dest = upload_file(io.BytesIO(b"note"), "note.png", EDataType.Text, ts, cfg, ApplicationConfig(),
                                UploadContext(timeout=10))
    assert dest.name == "FTP" and (tmp_path / "txt" / "note.png").read_bytes() == b"note"
    assert res.url == "http://127.0.0.1/txt/note.png"
