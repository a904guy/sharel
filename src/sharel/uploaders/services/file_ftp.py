"""FTP / FTPS / SFTP (ShareX FTP.cs, SFTP.cs, FTPAccount.cs).

One destination with a list of accounts. Each account picks its protocol:

* FTP — :mod:`ftplib`, passive (default) or active data connections
* FTPS — explicit (``AUTH TLS`` on the normal port) or implicit (TLS from the first byte, usually port 990),
  data channel protected (``PROT P``) with TLS session reuse; certificate checking is optional (ShareX accepts
  any certificate) and a client certificate (PEM, certificate + key) can be presented
* SFTP — :mod:`paramiko`, password or private key (+ passphrase); the server key is checked against
  ``~/.ssh/known_hosts`` and an optional pinned SHA256 fingerprint

The remote path is ``<remote directory pattern>/<file name>`` (name-parser patterns such as ``%y/%mo``) and the
URL is built from the HTTP homepage exactly like ShareX's ``FTPAccount.GetUriPath``.
"""
from __future__ import annotations

import base64
import contextlib
import ftplib
import hashlib
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadCancelled, UploadContext, UploaderError, UploadResult, stream_size
from ..registry import register, setting
from ._file_helpers import (combine_url, data_type_for, parse_pattern, remove_prefixes, url_encode, url_encode_path,
                            without_extension)

FTP_PROTOCOLS = ["FTP", "FTPS", "SFTP"]
FTPS_ENCRYPTIONS = ["Explicit", "Implicit"]
BROWSER_PROTOCOLS = ["http", "https", "ftp", "ftps", "file"]
DEFAULT_PORTS = {"FTP": 21, "FTPS": 21, "SFTP": 22}
BLOCK_SIZE = 64 * 1024


@dataclass
class FTPAccount:
    name: str = setting("New account", label="Name")
    protocol: str = setting("FTP", label="Protocol", choices=FTP_PROTOCOLS)
    host: str = setting("", label="Host", help="e.g. ftp.example.com")
    port: int = setting(21, label="Port", help="21 for FTP / explicit FTPS, 990 for implicit FTPS, 22 for SFTP")
    username: str = setting("", label="Username")
    password: str = setting("", label="Password", secret=True)
    is_active: bool = setting(False, label="Active mode", help="Unchecked: passive mode (works behind NAT)")
    sub_folder_path: str = setting("", label="Remote directory",
                                   help="Path on the server, name-parser patterns allowed, e.g. screenshots/%y/%mo")
    browser_protocol: str = setting("http", label="URL protocol", choices=BROWSER_PROTOCOLS)
    http_home_path: str = setting("", label="HTTP homepage",
                                  help="e.g. example.com/files or example.com/view.php?file= ; %host = host. "
                                       "Empty: the host (without a leading 'ftp.') is used")
    http_home_path_auto_add_sub_folder_path: bool = setting(True, label="Append remote directory to homepage")
    http_home_path_no_extension: bool = setting(False, label="Remove file extension from URL")
    create_missing_directories: bool = setting(True, label="Create missing directories")
    ftps_encryption: str = setting("Explicit", label="FTPS encryption", choices=FTPS_ENCRYPTIONS)
    ftps_validate_certificate: bool = setting(False, label="FTPS: verify server certificate",
                                              help="Off (ShareX default): any certificate is accepted")
    ftps_ca_certificate: str = setting("", label="FTPS: trusted CA / server certificate (PEM)", kind="path",
                                       help="Used when verifying; empty = system trust store")
    ftps_certificate_location: str = setting("", label="FTPS: client certificate (PEM with key)", kind="path")
    keypath: str = setting("", label="SFTP: private key file", kind="path")
    passphrase: str = setting("", label="SFTP: key passphrase", secret=True)
    sftp_host_key_fingerprint: str = setting("", label="SFTP: server key fingerprint",
                                             help="Optional SHA256:... pin; known_hosts is always checked")

    @property
    def ftp_address(self) -> str:
        if not self.host:
            return ""
        return f"{self.protocol.lower()}://{self.host}:{self.port}"

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"


@dataclass
class FTPSettings:
    ftp_account_list: list[FTPAccount] = setting(label="Accounts", default_factory=list)  # noqa: RUF009
    ftp_selected_image: int = setting(0, label="Account for images")
    ftp_selected_text: int = setting(0, label="Account for text")
    ftp_selected_file: int = setting(0, label="Account for files")


# ---- paths and URLs ------------------------------------------------------------------------------

def get_sub_folder_path(account: FTPAccount, kind: NameParserType = NameParserType.FilePath) -> str:
    """FTPAccount.GetSubFolderPath: the remote directory pattern with %host and name-parser tokens expanded."""
    pattern = (account.sub_folder_path or "").replace("%host", account.host).replace("\\", "/")
    return parse_pattern(pattern, kind)


def get_http_home_path(account: FTPAccount) -> str:
    homepage = (account.http_home_path or "").replace("%host", account.host)
    return parse_pattern(homepage, NameParserType.URL)


def get_uri_path(account: FTPAccount, file_name: str, sub_folder_path: str | None = None) -> str:
    """FTPAccount.GetUriPath: the public URL of an uploaded file."""
    if not account.host:
        return ""
    if account.http_home_path_no_extension:
        file_name = without_extension(file_name)
    file_name = url_encode(file_name)
    if sub_folder_path is None:
        sub_folder_path = get_sub_folder_path(account)
    sub = url_encode_path(sub_folder_path)
    scheme = account.browser_protocol or "http"
    home = get_http_home_path(account)

    if not home:
        url = account.host
        if url.startswith("ftp."):
            url = url[4:]
        if account.http_home_path_auto_add_sub_folder_path:
            url = combine_url(url, sub)
        url = combine_url(url, file_name)
        return f"{scheme}://{url}"

    if "://" in home[:16]:  # a scheme typed into the homepage wins over the protocol setting
        scheme = home.split("://", 1)[0]
        home = remove_prefixes(home)
    first_slash = home.find("/")
    authority = home[:first_slash] if first_slash >= 0 else home
    path_and_query = home[first_slash + 1:] if first_slash >= 0 else ""
    q = path_and_query.rfind("?")
    path = path_and_query[:q] if q >= 0 else path_and_query
    query = path_and_query[q + 1:] if q >= 0 else ""

    if query.endswith("="):
        if account.http_home_path_auto_add_sub_folder_path:
            query = combine_url(query, sub, file_name)
        else:
            query = query + file_name
    else:
        if account.http_home_path_auto_add_sub_folder_path:
            path = combine_url(path, sub)
        path = combine_url(path, file_name)
    url = f"{scheme}://{authority}/{path.lstrip('/')}"
    if query:
        url += "?" + query
    return url


def _parent_dirs(remote_path: str) -> list[str]:
    """URLHelpers.GetPaths(GetDirectoryPath(path)): 'a/b/c.png' -> ['a', 'a/b']."""
    lead = "/" if remote_path.startswith("/") else ""
    parts = [p for p in remote_path.split("/")[:-1] if p]
    return [lead + "/".join(parts[: i + 1]) for i in range(len(parts))]


# ---- FTP / FTPS ----------------------------------------------------------------------------------

def _ssl_context(account: FTPAccount) -> ssl.SSLContext:
    if account.ftps_validate_certificate:
        ctx = ssl.create_default_context(cafile=account.ftps_ca_certificate or None)
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if account.ftps_certificate_location:
        cert = Path(account.ftps_certificate_location).expanduser()
        if not cert.is_file():
            raise UploaderError(f"FTPS client certificate not found: {cert}")
        ctx.load_cert_chain(str(cert))
    return ctx


class _FTPTLS(ftplib.FTP_TLS):
    """FTP_TLS that reuses the control connection's TLS session on data connections (vsftpd's
    ``require_ssl_reuse``, FileZilla Server) and can do implicit FTPS."""

    implicit = False

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if not self.implicit:
            return super().connect(host, port, timeout, source_address)
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        sys_sock = socket.create_connection((self.host, self.port), self.timeout, source_address=self.source_address)
        self.af = sys_sock.family
        self.sock = self.context.wrap_socket(sys_sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def login(self, user="", passwd="", acct="", secure=True):
        if self.implicit:  # already encrypted: no AUTH TLS
            return ftplib.FTP.login(self, user, passwd, acct)
        return super().login(user, passwd, acct, secure)

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = getattr(self.sock, "session", None)
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=session)
        return conn, size


def _ftp_connect(account: FTPAccount, ctx: UploadContext) -> ftplib.FTP:
    port = account.port or DEFAULT_PORTS.get(account.protocol, 21)
    try:
        if account.protocol == "FTPS":
            ftp: ftplib.FTP = _FTPTLS(context=_ssl_context(account), timeout=ctx.timeout)
            ftp.implicit = account.ftps_encryption == "Implicit"  # type: ignore[attr-defined]
        else:
            ftp = ftplib.FTP(timeout=ctx.timeout)
        ftp.encoding = "utf-8"
        ftp.connect(account.host, port)
        ftp.login(account.username or "anonymous", account.password)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.set_pasv(not account.is_active)
    except (OSError, ftplib.Error, EOFError) as e:
        raise UploaderError(f"FTP connection to {account.host}:{port} failed: {e}") from e
    return ftp


def _ftp_mkdirs(ftp: ftplib.FTP, remote_path: str) -> None:
    for d in _parent_dirs(remote_path):
        with contextlib.suppress(ftplib.error_perm):
            ftp.mkd(d)


def _ftp_upload(account: FTPAccount, stream: BinaryIO, remote_path: str, ctx: UploadContext) -> None:
    ftp = _ftp_connect(account, ctx)
    start = stream.tell() if stream.seekable() else 0
    try:
        try:
            ftp.storbinary(f"STOR {remote_path}", ctx.progress_stream(stream), BLOCK_SIZE)
        except ftplib.error_perm as e:
            # Probably the directory does not exist: create it and retry (ShareX: 550 / 553)
            if not (str(e)[:3] in ("550", "553") and account.create_missing_directories and "/" in remote_path):
                raise
            _ftp_mkdirs(ftp, remote_path)
            stream.seek(start)
            ftp.storbinary(f"STOR {remote_path}", ctx.progress_stream(stream), BLOCK_SIZE)
    except UploadCancelled:
        with contextlib.suppress(Exception):
            ftp.close()
        raise
    except (OSError, ftplib.Error, EOFError) as e:
        raise UploaderError(f"FTP upload of {remote_path} failed: {e}") from e
    finally:
        with contextlib.suppress(Exception):
            ftp.quit()
        with contextlib.suppress(Exception):
            ftp.close()


# ---- SFTP ----------------------------------------------------------------------------------------

def key_fingerprint(key) -> str:
    """OpenSSH-style 'SHA256:<base64>' fingerprint of a paramiko key."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _check_host_key(account: FTPAccount, port: int, key) -> None:
    pin = account.sftp_host_key_fingerprint.strip()
    if pin:
        want = pin if pin.startswith("SHA256:") else "SHA256:" + pin
        if want.rstrip("=") != key_fingerprint(key):
            raise UploaderError(f"SFTP server key mismatch for {account.host}: got {key_fingerprint(key)}, "
                                f"expected {want}")
        return
    import paramiko
    known = Path("~/.ssh/known_hosts").expanduser()
    if not known.is_file():
        return
    try:
        hostkeys = paramiko.HostKeys(str(known))
    except (OSError, paramiko.SSHException):
        return
    names = [account.host] if port == 22 else [f"[{account.host}]:{port}"]
    for name in names:
        entry = hostkeys.lookup(name)
        if entry and key.get_name() in entry and entry[key.get_name()] != key:
            raise UploaderError(f"SFTP server key for {name} does not match ~/.ssh/known_hosts "
                                f"(got {key_fingerprint(key)}); refusing to connect")


def _sftp_connect(account: FTPAccount, ctx: UploadContext):
    import paramiko
    port = account.port or 22
    try:
        sock = socket.create_connection((account.host, port), ctx.timeout)
    except OSError as e:
        raise UploaderError(f"SFTP connection to {account.host}:{port} failed: {e}") from e
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=ctx.timeout)
        _check_host_key(account, port, transport.get_remote_server_key())
        if account.keypath:
            key_path = Path(account.keypath).expanduser()
            if not key_path.is_file():
                raise UploaderError(f"SFTP key file not found: {key_path}")
            pkey = paramiko.PKey.from_path(key_path, account.passphrase.encode() if account.passphrase else None)
            transport.auth_publickey(account.username, pkey)
        elif account.password:
            transport.auth_password(account.username, account.password)
        else:
            raise UploaderError("SFTP account has neither a password nor a private key")
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise UploaderError("SFTP subsystem is not available on the server")
        return transport, sftp
    except UploaderError:
        transport.close()
        raise
    except (paramiko.SSHException, OSError, ValueError) as e:
        transport.close()
        raise UploaderError(f"SFTP login to {account.host}:{port} failed: {e}") from e


def _sftp_mkdirs(sftp, remote_path: str) -> None:
    for d in _parent_dirs(remote_path):
        try:
            sftp.stat(d)
        except OSError:
            with contextlib.suppress(OSError):
                sftp.mkdir(d)


def _sftp_upload(account: FTPAccount, stream: BinaryIO, remote_path: str, ctx: UploadContext) -> None:
    transport, sftp = _sftp_connect(account, ctx)
    start = stream.tell() if stream.seekable() else 0
    size = stream_size(stream)

    def progress(done: int, total: int) -> None:
        ctx.report(done, total)
        ctx.check_cancelled()

    try:
        try:
            sftp.putfo(stream, remote_path, size, callback=progress)
        except FileNotFoundError:
            if not (account.create_missing_directories and "/" in remote_path):
                raise
            _sftp_mkdirs(sftp, remote_path)
            stream.seek(start)
            sftp.putfo(stream, remote_path, size, callback=progress)
    except UploadCancelled:
        raise
    except OSError as e:
        raise UploaderError(f"SFTP upload of {remote_path} failed: {e}") from e
    finally:
        with contextlib.suppress(Exception):
            sftp.close()
        transport.close()


# ---- the destination -----------------------------------------------------------------------------

def select_account(settings: FTPSettings, file_name: str = "", data_type: object = None,
                   account_index: int | None = None) -> FTPAccount:
    accounts = settings.ftp_account_list
    if not accounts:
        raise UploaderError("No FTP account is configured")
    if account_index is not None:  # task override: FTPIndex.BetweenOrDefault(0, count - 1)
        index = account_index if 0 <= account_index < len(accounts) else 0
    else:
        kind = data_type_for(file_name, data_type)
        index = {"Image": settings.ftp_selected_image, "Text": settings.ftp_selected_text}.get(
            kind, settings.ftp_selected_file)
        if not 0 <= index < len(accounts):
            raise UploaderError(f"FTP account #{index + 1} does not exist")
    return accounts[index]


@register("file", "FTP", FTPSettings, title="FTP / FTPS / SFTP", homepage="https://getsharex.com/docs/ftp")
class FTPUploader(FileUploader):
    account_index: int | None = None  # set by dispatch when the task overrides the FTP account
    data_type: object = None  # EDataType of the task (picks the image/text/file account)

    def validate(self) -> list[str]:
        if not self.settings.ftp_account_list:
            return ["add an FTP account first"]
        return []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        account = select_account(self.settings, file_name, self.data_type, self.account_index)
        if not account.host:
            raise UploaderError(f"FTP account '{account.name}' has no host")
        sub = get_sub_folder_path(account, NameParserType.FilePath)
        remote_path = combine_url(sub, file_name)
        url = get_uri_path(account, file_name, sub)
        if account.protocol == "SFTP":
            _sftp_upload(account, stream, remote_path, self.ctx)
        else:
            _ftp_upload(account, stream, remote_path, self.ctx)
        return UploadResult(url=url)


def test_connection(settings: FTPSettings, ctx: UploadContext, account_index: int | None = None) -> str:
    """Connect and log in with one account (default: the file account); create the remote directory if
    allowed. Returns a message for the settings page; raises UploaderError on failure."""
    account = select_account(settings, data_type="File", account_index=account_index)
    sub = get_sub_folder_path(account)
    if account.protocol == "SFTP":
        transport, sftp = _sftp_connect(account, ctx)
        try:
            if sub:
                if account.create_missing_directories:
                    _sftp_mkdirs(sftp, combine_url(sub, "x"))
                sftp.stat(sub)
            cwd = sftp.normalize(sub or ".")
        except OSError as e:
            raise UploaderError(f"Remote directory '{sub}': {e}") from e
        finally:
            sftp.close()
            transport.close()
        return f"Connected to {account.ftp_address} as {account.username}; remote directory {cwd}"
    ftp = _ftp_connect(account, ctx)
    try:
        if sub:
            if account.create_missing_directories:
                _ftp_mkdirs(ftp, combine_url(sub, "x"))
            ftp.cwd(sub)
        cwd = ftp.pwd()
    except ftplib.Error as e:
        raise UploaderError(f"Remote directory '{sub}': {e}") from e
    finally:
        with contextlib.suppress(Exception):
            ftp.quit()
        ftp.close()
    return f"Connected to {account.ftp_address} as {account.username or 'anonymous'}; remote directory {cwd}"
