"""MEGA (ShareX Mega.cs) — a self-contained client for MEGA's JSON API at ``https://g.api.mega.co.nz/cs``.

Login (from Destination settings, or automatically before the first upload):

* ``us0`` tells the account version. v2: ``PBKDF2-HMAC-SHA512(password, salt, 100000, 32)`` → the first 16
  bytes are the password key, the last 16 (base64url) the user hash ``uh``. v1 (legacy): the password key
  is 65536 rounds of AES-ECB over a fixed constant keyed by each 16-byte block of the password, and ``uh``
  folds the e-mail into 16 bytes, AES-encrypts it 16384 times with that key and keeps bytes 0–3 and 8–11.
* ``us`` returns the master key (AES-ECB encrypted with the password key) and either ``csid`` — the
  session id RSA-encrypted to the account key; ``privk`` (p, q, d, u as MPIs, ECB-encrypted with the master
  key) decrypts it and the first 43 bytes are the sid — or a 32-byte ``tsid`` checked against the master key.
  ``-26`` asks for a two-factor code (``mfa``).
* A ``402`` reply with an ``X-Hashcash`` challenge is answered with the proof-of-work token.

Upload:

1. ``u`` (``ssl: 2``, ``v: 3``) → an upload URL. The file is encrypted with AES-128-CTR (random 128-bit key,
   64-bit nonce, 64-bit block counter) and POSTed to ``<url>/<offset>`` in MEGA's chunk sizes (128 KiB,
   256 KiB, … growing by 128 KiB up to 1 MiB, then 1 MiB). Each chunk's MAC is the CBC-MAC of its
   zero-padded plaintext with IV ``nonce||nonce``; the file MAC folds them (``mac = E(mac ^ chunk_mac)``) and
   is condensed to the 8-byte meta-MAC. The last chunk's reply is the 36-byte completion handle.
2. ``p`` creates the node in the chosen folder (Cloud Drive root by default) with the attributes
   ``"MEGA" + {"n": name}`` AES-CBC-encrypted with the file key and the 32-byte node key
   ``(key ^ nonce|metamac) || nonce || metamac`` ECB-encrypted with the master key.
3. ``l`` exports it; the link is ``https://mega.nz/file/<public handle>#<base64url node key>``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import read_exact, remaining_size

API_URL = "https://g.api.mega.co.nz/cs"
PUBLIC_FILE_URL = "https://mega.nz/file/"
INITIAL_CHUNK = 128 * 1024
MAX_CHUNK = 1024 * 1024
UPLOAD_TOKEN_LENGTH = 36
LEGACY_KEY_SEED = bytes.fromhex("93C467E37DB0C7A4D1BE3F810152CB56")

ERRORS = {-1: "internal error", -2: "invalid arguments", -3: "try again", -4: "rate limit exceeded",
          -5: "upload failed", -6: "too many concurrent connections", -7: "out of range", -8: "expired",
          -9: "not found", -10: "circular linkage", -11: "access denied", -12: "already exists",
          -13: "incomplete", -14: "invalid key or decryption error", -15: "invalid or expired session",
          -16: "account blocked", -17: "over quota", -18: "temporarily unavailable",
          -19: "too many connections", -20: "write error", -21: "read error", -22: "invalid application key",
          -24: "SSL verification failed", -25: "not enough quota", -26: "two-factor authentication required"}
EAGAIN, ESID, EMFAREQUIRED = -3, -15, -26

_sleep = time.sleep


class MegaApiError(UploaderError):
    def __init__(self, code: int) -> None:
        super().__init__(f"MEGA error {code}: {ERRORS.get(code, 'unknown error')}")
        self.code = code


@dataclass
class MegaSettings:
    email: str = setting("", label="Email")
    password: str = setting("", label="Password", secret=True,
                            help="Used to log in; the session is kept, so it can be cleared after logging in")
    session_id: str = setting("", label="Session ID", secret=True, hidden=True)
    master_key: str = setting("", label="Master key", secret=True, hidden=True)
    folder_id: str = setting("", label="Folder ID", help="Node handle of the target folder; empty = Cloud Drive")
    folder_name: str = setting("", label="Folder", hidden=True)


# ---- encoding and crypto primitives ------------------------------------------------------------------

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    if not text or not text.strip():
        raise UploaderError("Invalid MEGA base64 value")
    text = text.strip().replace("+", "-").replace("/", "_").replace(",", "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise UploaderError("Invalid MEGA AES block data")
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise UploaderError("Invalid MEGA AES block data")
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data) + dec.finalize()


def aes_cbc_encrypt(key: bytes, data: bytes, iv: bytes = bytes(16)) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def aes_cbc_decrypt(key: bytes, data: bytes, iv: bytes = bytes(16)) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def pad16(data: bytes) -> bytes:
    return data + bytes(-len(data) % 16)


def password_bytes(password: str) -> bytes:
    """The password as UTF-8, zero-padded to a multiple of 4 bytes (MEGA's a32 conversion)."""
    raw = password.encode()
    return raw + bytes(-len(raw) % 4)


def prepare_legacy_key(password: str) -> bytes:
    """v1 accounts: 65536 rounds of AES-ECB over the seed, keyed by each 16-byte password block."""
    pw = password_bytes(password)
    encryptors = [Cipher(algorithms.AES(pad16(pw[i:i + 16])), modes.ECB()).encryptor()
                  for i in range(0, max(len(pw), 1), 16)]
    key = LEGACY_KEY_SEED
    for _ in range(65536):
        for enc in encryptors:
            key = enc.update(key)
    return key


def legacy_user_hash(email: str, password_key: bytes) -> str:
    h = bytearray(16)
    for i, b in enumerate(email.lower().encode()):
        h[i % 16] ^= b
    enc = Cipher(algorithms.AES(password_key), modes.ECB()).encryptor()
    block = bytes(h)
    for _ in range(16384):
        block = enc.update(block)
    return b64url_encode(block[0:4] + block[8:12])


def derive_v2(password: str, salt: bytes) -> tuple[bytes, str]:
    """v2 accounts: (password key, user hash) from PBKDF2-HMAC-SHA512."""
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100000, 32)
    return dk[:16], b64url_encode(dk[16:])


def authentication_info(email: str, password: str, version: int, salt: str | None) -> tuple[bytes, str]:
    if version == 2 and salt:
        return derive_v2(password, b64url_decode(salt))
    if version == 1:
        key = prepare_legacy_key(password)
        return key, legacy_user_hash(email, key)
    raise UploaderError(f"MEGA account version {version} is not supported")


def read_mpi(data: bytes, offset: int) -> tuple[int, int]:
    """A multi-precision integer: 2-byte big-endian bit length, then the bytes. Returns (value, new offset)."""
    if offset + 2 > len(data):
        raise UploaderError("Invalid MEGA MPI value")
    bits = int.from_bytes(data[offset:offset + 2], "big")
    n = (bits + 7) // 8
    offset += 2
    if n == 0 or offset + n > len(data):
        raise UploaderError("Invalid MEGA MPI value")
    return int.from_bytes(data[offset:offset + n], "big"), offset + n


def decrypt_session_id(csid: str, privk: str, master_key: bytes) -> str:
    """RSA-decrypt ``csid`` with the account private key (``privk``: p, q, d, u MPIs, ECB with the master key)."""
    key = aes_ecb_decrypt(master_key, pad16(b64url_decode(privk)))
    p, off = read_mpi(key, 0)
    q, off = read_mpi(key, off)
    d, off = read_mpi(key, off)
    c, _ = read_mpi(b64url_decode(csid), 0)
    m = pow(c, d, p * q)
    sid = m.to_bytes((m.bit_length() + 7) // 8, "big")
    if len(sid) < 43:
        raise UploaderError("MEGA returned an invalid encrypted session")
    return b64url_encode(sid[:43])


def valid_temporary_session(tsid: str, master_key: bytes) -> bool:
    raw = b64url_decode(tsid)
    return len(raw) == 32 and secrets.compare_digest(aes_ecb_decrypt(master_key, raw[16:]), raw[:16])


def hashcash_token(challenge: str, cancel=None) -> str:
    """Solve MEGA's ``X-Hashcash`` proof of work: ``1:<easiness>:<ts>:<48-byte token>``."""
    parts = (challenge or "").split(":")
    if len(parts) < 4 or parts[0] != "1" or not parts[1].isdigit() or not 0 <= int(parts[1]) <= 255:
        raise UploaderError("MEGA returned an invalid proof-of-work challenge")
    easiness = int(parts[1])
    token = b64url_decode(parts[3])
    if len(token) != 48:
        raise UploaderError("MEGA returned an invalid proof-of-work token")
    threshold = ((((easiness & 63) << 1) + 1) << ((easiness >> 6) * 7 + 3)) & 0xFFFFFFFF
    body = token * 262144
    nonce = 0
    while True:
        if cancel is not None and nonce % 64 == 0:
            cancel()
        prefix = nonce.to_bytes(4, "big")
        h = hashlib.sha256(prefix)
        h.update(body)
        if int.from_bytes(h.digest()[:4], "big") <= threshold:
            return f"1:{parts[3]}:{b64url_encode(prefix)}"
        nonce = (nonce + 1) & 0xFFFFFFFF


def chunk_sizes(size: int) -> Iterator[int]:
    """MEGA's chunk boundaries: 128 KiB, 256 KiB, … +128 KiB each up to 1 MiB, then 1 MiB."""
    if size == 0:
        yield 0
        return
    chunk, left = INITIAL_CHUNK, size
    while left > 0:
        n = min(chunk, left)
        yield n
        left -= n
        chunk = min(chunk + INITIAL_CHUNK, MAX_CHUNK)


class FileCipher:
    """AES-128-CTR encryption with MEGA's per-chunk CBC-MACs and condensed meta-MAC."""

    def __init__(self, key: bytes | None = None, nonce: bytes | None = None) -> None:
        self.key = key or secrets.token_bytes(16)
        self.nonce = nonce or secrets.token_bytes(8)
        self._ctr = Cipher(algorithms.AES(self.key), modes.CTR(self.nonce + bytes(8))).encryptor()
        self._ecb = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        self.file_mac = bytes(16)

    def chunk_mac(self, plain: bytes) -> bytes:
        """CBC-MAC of the zero-padded chunk with IV nonce||nonce = the last CBC ciphertext block."""
        return aes_cbc_encrypt(self.key, pad16(plain), self.nonce + self.nonce)[-16:]

    def encrypt_chunk(self, plain: bytes) -> bytes:
        """Encrypt one chunk (chunks must arrive in order; all but the last are multiples of 16 bytes)."""
        self.file_mac = self._ecb.update(_xor(self.file_mac, self.chunk_mac(plain)))
        return self._ctr.update(plain)

    @property
    def meta_mac(self) -> bytes:
        m = self.file_mac
        return _xor(m[0:4], m[4:8]) + _xor(m[8:12], m[12:16])

    def node_key(self) -> bytes:
        """The 32-byte key stored on the node and put in the link."""
        meta = self.meta_mac
        return _xor(self.key[:8], self.nonce) + _xor(self.key[8:], meta) + self.nonce + meta


def encrypt_attributes(attrs: dict, key: bytes) -> bytes:
    data = b"MEGA" + json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode()
    return aes_cbc_encrypt(key, pad16(data))


def decrypt_attributes(data: bytes, key: bytes) -> dict | None:
    if not data or len(data) % 16:
        return None
    text = aes_cbc_decrypt(key, data).rstrip(b"\0")
    if not text.startswith(b"MEGA"):
        return None
    try:
        return json.loads(text[4:].decode())
    except (ValueError, UnicodeDecodeError):
        return None


def fold_key(key: bytes) -> bytes:
    """A file node's 32-byte key → the 16-byte AES key (folders already have 16 bytes)."""
    return _xor(key[:16], key[16:32]) if len(key) == 32 else key


# ---- API client ----------------------------------------------------------------------------------------

class MegaClient:
    def __init__(self, ctx: UploadContext, session_id: str = "", master_key: bytes = b"") -> None:
        self.ctx = ctx
        self.session_id = session_id
        self.master_key = master_key
        self.seq = secrets.randbelow(2**31)

    def request(self, command: dict, include_session: bool = True) -> Any:
        params = {"id": str(self.seq)}
        self.seq += 1
        if include_session and self.session_id:
            params["sid"] = self.session_id
        body = json.dumps([command], separators=(",", ":"))
        hashcash = ""
        for attempt in range(6):
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if hashcash:
                headers["X-Hashcash"] = hashcash
            resp = self.ctx.post(API_URL, params=params, data=body, headers=headers)
            if resp.status_code == 402:
                challenge = resp.headers.get("X-Hashcash", "")
                if hashcash or not challenge:
                    raise UploaderError("MEGA proof-of-work verification failed", resp.text)
                hashcash = hashcash_token(challenge, self.ctx.check_cancelled)
                continue
            if not 200 <= resp.status_code < 300:
                raise UploaderError(f"MEGA API request failed: HTTP {resp.status_code}", resp.text)
            try:
                parsed = json.loads(resp.text)
            except ValueError:
                kind = "HTML" if resp.text.lstrip().startswith("<") else "a non-JSON reply"
                raise UploaderError(f"MEGA returned {kind} for API command '{command.get('a')}'",
                                    resp.text) from None
            result = parsed[0] if isinstance(parsed, list) and parsed else parsed
            if isinstance(parsed, list) and not parsed:
                raise UploaderError("MEGA returned an empty API response", resp.text)
            if isinstance(result, dict) and "result" in result and len(result) == 1:
                result = result["result"]
            if isinstance(result, int) and not isinstance(result, bool) and result < 0:
                if result == EAGAIN and attempt < 5:
                    _sleep(min(2 ** attempt * 0.5, 8))
                    continue
                raise MegaApiError(result)
            return result
        raise MegaApiError(EAGAIN)

    # -- session --

    def login(self, email: str, password: str, mfa: str = "") -> None:
        email = email.strip()
        self.session_id, self.master_key = "", b""
        pre = self.request({"a": "us0", "user": email}, include_session=False)
        version = int(pre.get("v") or 0) if isinstance(pre, dict) else 0
        password_key, user_hash = authentication_info(email, password, version, pre.get("s"))
        cmd = {"a": "us", "user": email, "uh": user_hash}
        if mfa:
            cmd["mfa"] = mfa.strip()
        res = self.request(cmd, include_session=False)
        if not isinstance(res, dict) or "k" not in res:
            raise UploaderError("MEGA login: unexpected response", json.dumps(res))
        self.master_key = aes_ecb_decrypt(password_key, b64url_decode(res["k"]))
        if res.get("csid"):
            self.session_id = decrypt_session_id(res["csid"], res["privk"], self.master_key)
        elif valid_temporary_session(res.get("tsid", ""), self.master_key):
            self.session_id = res["tsid"]
        else:
            raise UploaderError("MEGA returned an invalid login session")

    # -- nodes --

    def nodes(self) -> list[dict]:
        res = self.request({"a": "f", "c": 1})
        return list(res.get("f") or []) if isinstance(res, dict) else []

    def root_id(self, nodes: list[dict] | None = None) -> str:
        for node in nodes if nodes is not None else self.nodes():
            if node.get("t") == 2:
                return node["h"]
        raise UploaderError("MEGA did not return the Cloud Drive root folder")

    def node_name(self, node: dict) -> str | None:
        attrs, keys = node.get("a"), node.get("k")
        if not attrs or not keys:
            return None
        for entry in keys.split("/"):
            enc = entry.split(":", 1)[-1]
            try:
                key = fold_key(aes_ecb_decrypt(self.master_key, b64url_decode(enc)))
                decoded = decrypt_attributes(b64url_decode(attrs), key)
            except (UploaderError, ValueError):
                continue
            if decoded and "n" in decoded:
                return decoded["n"]
        return None

    def folders(self, parent_id: str = "") -> list[dict]:
        nodes = self.nodes()
        parent = parent_id or self.root_id(nodes)
        out = [{"id": n["h"], "name": self.node_name(n) or n["h"]} for n in nodes
               if n.get("t") == 1 and n.get("p") == parent and n.get("h")]
        return sorted(out, key=lambda f: f["name"].casefold())

    # -- upload --

    def upload(self, stream: BinaryIO, file_name: str, folder_id: str = "") -> tuple[str, str]:
        """Encrypt and upload; returns (public link, node handle)."""
        size = remaining_size(stream)
        target = folder_id or self.root_id()
        res = self.request({"a": "u", "ssl": 2, "v": 3, "s": size})
        url = (res.get("p") if isinstance(res, dict) else "") or ""
        if not url:
            raise UploaderError("MEGA did not return an upload URL")
        url = url.rstrip("/")
        cipher = FileCipher()
        handle, offset = "", 0
        self.ctx.report(0, size)
        for n in chunk_sizes(size):
            plain = read_exact(stream, n)
            if len(plain) != n:
                raise UploaderError("The file ended before its expected size was read")
            resp = self.ctx.post(f"{url}/{offset}", data=cipher.encrypt_chunk(plain) if n else b"",
                                 headers={"Content-Type": "application/octet-stream"})
            if not 200 <= resp.status_code < 300:
                raise UploaderError(f"MEGA rejected the upload: HTTP {resp.status_code}", resp.text)
            content = resp.content
            if len(content) == UPLOAD_TOKEN_LENGTH:
                handle = b64url_encode(content)
            elif re.fullmatch(rb"\s*-\d+\s*", content):
                raise MegaApiError(int(content))
            elif content:
                raise UploaderError(f"MEGA returned an invalid upload token ({len(content)} bytes)")
            offset += n
            self.ctx.report(offset, size)
        if not handle:
            raise UploaderError("MEGA did not return an upload completion handle")
        node_key = cipher.node_key()
        created = self.request({"a": "p", "t": target, "n": [{
            "h": handle, "t": 0, "a": b64url_encode(encrypt_attributes({"n": file_name}, cipher.key)),
            "k": b64url_encode(aes_ecb_encrypt(self.master_key, node_key))}]})
        try:
            node_id = created["f"][0]["h"]
        except (KeyError, IndexError, TypeError):
            raise UploaderError("MEGA created the file but returned no node handle", json.dumps(created)) from None
        public = self.request({"a": "l", "n": node_id})
        if not isinstance(public, str) or not public:
            raise UploaderError("MEGA did not return a public file handle", json.dumps(public))
        return f"{PUBLIC_FILE_URL}{public}#{b64url_encode(node_key)}", node_id


# ---- settings-page actions -----------------------------------------------------------------------------

def login(settings: MegaSettings, ctx: UploadContext) -> MegaClient:
    """Log in with email + password (asking for a 2FA code when the account needs one) and keep the session."""
    if not settings.email or not settings.password:
        raise UploaderError("Enter the MEGA email and password first")
    client = MegaClient(ctx)
    try:
        client.login(settings.email, settings.password)
    except MegaApiError as e:
        if e.code != EMFAREQUIRED:
            raise
        code = ctx.prompt_input("MEGA two-factor authentication code", "")
        if not code:
            raise UploaderError("MEGA login needs a two-factor authentication code") from None
        client.login(settings.email, settings.password, code)
    settings.session_id = client.session_id
    settings.master_key = b64url_encode(client.master_key)
    ctx.save_settings()
    return client


def list_folders(settings: MegaSettings, ctx: UploadContext, parent_id: str = "") -> list[dict]:
    """Folders directly under ``parent_id`` (Cloud Drive root when empty): [{"id", "name"}]."""
    return _client(settings, ctx).folders(parent_id)


def _client(settings: MegaSettings, ctx: UploadContext) -> MegaClient:
    if settings.session_id and settings.master_key:
        key = b64url_decode(settings.master_key)
        if len(key) == 16:
            return MegaClient(ctx, settings.session_id, key)
    return login(settings, ctx)


@register("file", "Mega", MegaSettings, title="MEGA", homepage="https://mega.nz")
class MegaUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if (s.session_id and s.master_key) or (s.email and s.password):
            return []
        return ["MEGA login is required: enter the email and password and log in"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        client = _client(s, self.ctx)
        start = stream.tell()
        try:
            url, node = client.upload(stream, file_name, s.folder_id.strip())
        except MegaApiError as e:
            if e.code != ESID or not (s.email and s.password):
                raise
            stream.seek(start)  # the stored session expired: log in again once
            client = login(s, self.ctx)
            url, node = client.upload(stream, file_name, s.folder_id.strip())
        return UploadResult(url=url, response=json.dumps({"h": node}))
