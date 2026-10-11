"""Real local servers for the file-protocol destination tests (all bound to 127.0.0.1 on free ports).

* :func:`pyftpd` — pyftpdlib FTP server (plain FTP, passive and active)
* :class:`MiniFTPS` — a small threaded FTPS server on the standard library ``ssl`` module, explicit (AUTH TLS)
  or implicit, with PROT P data channels. (pyftpdlib's TLS handler needs pyOpenSSL, which is not installed.)
* :class:`SFTPTestServer` — paramiko SSH server with the SFTP subsystem over a real socket
* :func:`self_signed_cert` — a throwaway certificate/key made with ``cryptography``
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import ipaddress
import os
import socket
import socketserver
import ssl
import threading
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def self_signed_cert(directory: Path, name: str = "server") -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = _dt.datetime.now(_dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(minutes=5))
            .not_valid_after(now + _dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path = directory / f"{name}.crt"
    key_path = directory / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


# ---- pyftpdlib ----------------------------------------------------------------------------------

@contextlib.contextmanager
def pyftpd(root: Path, user: str = "alice", password: str = "s3cret"):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer

    auth = DummyAuthorizer()
    auth.add_user(user, password, str(root), perm="elradfmwMT")

    commands_seen: list[str] = []

    class Handler(FTPHandler):
        def pre_process_command(self, line, cmd, arg):  # record the command stream for assertions
            commands_seen.append(cmd)
            return super().pre_process_command(line, cmd, arg)

    Handler.authorizer = auth
    Handler.commands_seen = commands_seen  # type: ignore[attr-defined]
    server = ThreadedFTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True)
    thread.start()
    try:
        yield server.address[1], Handler
    finally:
        server.close_all()
        thread.join(5)


# ---- a minimal FTPS server ----------------------------------------------------------------------

class MiniFTPS:
    """Threaded FTPS server. ``implicit=True`` speaks TLS from the first byte; otherwise AUTH TLS is required
    before USER. STOR into a missing directory answers 550 like real servers do."""

    def __init__(self, root: Path, cert: Path, key: Path, user: str = "bob", password: str = "pw",
                 implicit: bool = False, client_ca: Path | None = None) -> None:
        self.root = root
        self.user = user
        self.password = password
        self.implicit = implicit
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert), str(key))
        if client_ca is not None:
            self.ctx.verify_mode = ssl.CERT_REQUIRED
            self.ctx.load_verify_locations(str(client_ca))
        self.log: list[str] = []
        self.tls_data_transfers = 0
        self.client_cert_subjects: list[str] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer._session(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> MiniFTPS:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _path(self, cwd: str, arg: str) -> Path:
        p = arg if arg.startswith("/") else f"{cwd.rstrip('/')}/{arg}"
        full = (self.root / p.lstrip("/")).resolve()
        if not str(full).startswith(str(self.root.resolve())):
            raise PermissionError(arg)
        return full

    def _session(self, sock: socket.socket) -> None:
        conn = sock
        if self.implicit:
            conn = self.ctx.wrap_socket(sock, server_side=True)
            self._note_peer(conn)
        rfile = conn.makefile("rb")

        def send(line: str) -> None:
            conn.sendall((line + "\r\n").encode())

        send("220 MiniFTPS ready")
        authed = user_ok = prot_p = False
        cwd = "/"
        pasv: socket.socket | None = None
        while True:
            raw = rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8").rstrip("\r\n")
            cmd, _, arg = line.partition(" ")
            cmd = cmd.upper()
            self.log.append(cmd if cmd != "PASS" else "PASS ***")
            if cmd == "AUTH" and arg.upper() in ("TLS", "SSL") and not self.implicit:
                send("234 AUTH TLS ok")
                conn = self.ctx.wrap_socket(sock, server_side=True)
                self._note_peer(conn)
                rfile = conn.makefile("rb")
            elif cmd == "USER":
                if not isinstance(conn, ssl.SSLSocket):
                    send("530 TLS required")
                    continue
                user_ok = arg == self.user
                send("331 password please")
            elif cmd == "PASS":
                authed = user_ok and arg == self.password
                send("230 logged in" if authed else "530 login incorrect")
            elif not authed and cmd not in ("QUIT", "FEAT", "SYST"):
                send("530 not logged in")
            elif cmd == "PBSZ":
                send("200 PBSZ=0")
            elif cmd == "PROT":
                prot_p = arg.upper() == "P"
                send("200 PROT ok")
            elif cmd == "TYPE":
                send("200 type set")
            elif cmd == "PWD":
                send(f'257 "{cwd}"')
            elif cmd == "CWD":
                target = self._path(cwd, arg)
                if target.is_dir():
                    rel = target.relative_to(self.root.resolve()).as_posix()
                    cwd = "/" if rel == "." else "/" + rel
                    send("250 ok")
                else:
                    send("550 no such directory")
            elif cmd == "MKD":
                target = self._path(cwd, arg)
                try:
                    target.mkdir()
                    send(f'257 "{arg}" created')
                except OSError:
                    send("550 cannot create")
            elif cmd in ("PASV", "EPSV"):
                pasv = socket.socket()
                pasv.bind(("127.0.0.1", 0))
                pasv.listen(1)
                p = pasv.getsockname()[1]
                if cmd == "PASV":
                    send(f"227 Entering Passive Mode (127,0,0,1,{p >> 8},{p & 255})")
                else:
                    send(f"229 Entering Extended Passive Mode (|||{p}|)")
            elif cmd == "STOR":
                target = self._path(cwd, arg)
                if not target.parent.is_dir():
                    send("550 directory does not exist")
                    if pasv:
                        pasv.close()
                        pasv = None
                    continue
                if pasv is None:
                    send("425 use PASV first")
                    continue
                send("150 ok to send data")
                data_sock, _ = pasv.accept()
                pasv.close()
                pasv = None
                if prot_p:
                    data_sock = self.ctx.wrap_socket(data_sock, server_side=True)
                    self.tls_data_transfers += 1
                chunks = []
                while True:
                    chunk = data_sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                if isinstance(data_sock, ssl.SSLSocket):
                    with contextlib.suppress(OSError, ValueError):
                        data_sock = data_sock.unwrap()
                data_sock.close()
                target.write_bytes(b"".join(chunks))
                send("226 transfer complete")
            elif cmd == "QUIT":
                send("221 bye")
                return
            else:
                send(f"502 {cmd} not implemented")

    def _note_peer(self, conn: ssl.SSLSocket) -> None:
        peer = conn.getpeercert()
        if peer:
            self.client_cert_subjects.append(str(peer.get("subject")))


# ---- paramiko SFTP ------------------------------------------------------------------------------

class SFTPTestServer:
    """SSH server on 127.0.0.1 with an SFTP subsystem rooted at ``root`` (relative paths = the root)."""

    def __init__(self, root: Path, user: str = "carol", password: str | None = "pw",
                 authorized_key=None) -> None:
        import paramiko

        self.root = root
        self.host_key = paramiko.RSAKey.generate(2048)
        self.user = user
        self.password = password
        self.authorized_key = authorized_key
        self.auth_methods: list[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.transports: list = []

    def __enter__(self) -> SFTPTestServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self.sock.close()
        for t in self.transports:
            t.close()

    def _serve(self) -> None:
        import paramiko

        outer = self

        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                outer.auth_methods.append("password")
                if outer.password is not None and username == outer.user and password == outer.password:
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def check_auth_publickey(self, username, key):
                outer.auth_methods.append("publickey")
                if outer.authorized_key is not None and username == outer.user and key == outer.authorized_key:
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "password,publickey"

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else \
                    paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        root = self.root

        class Handle(paramiko.SFTPHandle):
            def stat(self):
                try:
                    return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def chattr(self, attr):
                return paramiko.SFTP_OK

        class SI(paramiko.SFTPServerInterface):
            def canonicalize(self, path):
                return "/" + os.path.normpath("/" + path).lstrip("/") if path not in ("", ".") else "/"

            def _real(self, path):
                return str(root / self.canonicalize(path).lstrip("/"))

            def list_folder(self, path):
                try:
                    out = []
                    for name in os.listdir(self._real(path)):
                        attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._real(path), name)))
                        attr.filename = name
                        out.append(attr)
                    return out
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def stat(self, path):
                try:
                    return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            lstat = stat

            def open(self, path, flags, attr):
                real = self._real(path)
                try:
                    fd = os.open(real, flags, 0o644)
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)
                mode = "wb" if flags & os.O_WRONLY else ("r+b" if flags & os.O_RDWR else "rb")
                if flags & os.O_APPEND:
                    mode = "ab"
                f = os.fdopen(fd, mode)
                h = Handle(flags)
                h.filename = real
                h.readfile = f
                h.writefile = f
                return h

            def mkdir(self, path, attr):
                try:
                    os.mkdir(self._real(path))
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)
                return paramiko.SFTP_OK

            def remove(self, path):
                try:
                    os.remove(self._real(path))
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)
                return paramiko.SFTP_OK

            def chattr(self, path, attr):
                return paramiko.SFTP_OK

        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, SI)
            self.transports.append(t)
            try:
                t.start_server(server=Server())
            except (paramiko.SSHException, EOFError, OSError):
                continue
