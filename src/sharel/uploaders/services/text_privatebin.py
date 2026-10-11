"""PrivateBin (ShareX TextUploaders/PrivateBin.cs) with client-side encryption in the PrivateBin v2
paste format, as implemented by PrivateBin's own ``js/privatebin.js`` (CryptTool.cipher):

* a random 256-bit key (shown to the reader base58-encoded in the URL fragment, never sent),
* PBKDF2-HMAC-SHA256, 100000 iterations, 8-byte salt, over ``key || password``,
* AES-256-GCM with a 16-byte IV and a 128-bit tag, the authenticated data being the compact JSON of
  ``adata`` = ``[[iv, salt, 100000, 256, 128, "aes", "gcm", "zlib"], formatter, open_discussion, burn]``,
* the plaintext ``{"paste": text}`` (UTF-8 JSON) compressed with raw deflate (PrivateBin calls it "zlib"),
* request body ``{"v": 2, "adata": ..., "ct": base64(ciphertext||tag), "meta": {"expire": ...}}``
  POSTed as JSON with ``X-Requested-With: JSONHttpRequest``.

ShareX itself sends AES-CBC without compression, which current PrivateBin viewers cannot open;
ShareL follows the format the PrivateBin web client produces.
"""
from __future__ import annotations

import base64
import json
import os
import zlib
from dataclasses import dataclass
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..base import TextUploader, UploaderError, UploadResult
from ..registry import register, setting

ITERATIONS = 100000
KEY_BITS = 256
TAG_BITS = 128
IV_BYTES = 16
SALT_BYTES = 8
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# ShareX PrivateBinExpiration -> PrivateBin "expire" values
EXPIRATION = {"M5": "5min", "M10": "10min", "H1": "1hour", "D1": "1day", "W1": "1week", "M1": "1month",
              "Y1": "1year", "N": "never"}
FORMATS = {"PlainText": "plaintext", "SyntaxHighlighting": "syntaxhighlighting", "Markdown": "markdown"}


@dataclass
class PrivateBinSettings:
    custom_url: str = setting("https://privatebin.net/", label="Instance URL")
    expiration: str = setting("W1", label="Expiration", choices=list(EXPIRATION),
                              help="M5/M10 = 5/10 minutes, H1 = 1 hour, D1 = 1 day, W1 = 1 week, M1 = 1 month, "
                                   "Y1 = 1 year, N = never (the instance may not offer all).")
    format: str = setting("PlainText", label="Format", choices=list(FORMATS))
    burn_after_reading: bool = setting(False, label="Burn after reading")
    open_discussion: bool = setting(False, label="Open discussion")
    paste_password: str = setting("", label="Paste password", secret=True,
                                  help="Readers must enter it in addition to having the link.")
    username: str = setting("", label="HTTP username", help="Only if the instance is behind basic auth.")
    password: str = setting("", label="HTTP password", secret=True)


# ---- crypto ------------------------------------------------------------------------------------

def base58_encode(data: bytes) -> str:
    """Bitcoin-alphabet base58 (as PrivateBin's baseX); each leading 0x00 byte becomes a '1'."""
    num = int.from_bytes(data, "big")
    out = []
    while num:
        num, rem = divmod(num, 58)
        out.append(BASE58_ALPHABET[rem])
    zeros = len(data) - len(data.lstrip(b"\0"))
    return "1" * zeros + "".join(reversed(out))


def base58_decode(text: str) -> bytes:
    num = 0
    for ch in text:
        num = num * 58 + BASE58_ALPHABET.index(ch)
    zeros = len(text) - len(text.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\0" * zeros + body


def js_binary_string(text: str) -> bytes:
    """What PrivateBin's ``stringToArraybuffer`` makes of a password: one byte per character, the
    low 8 bits of its (first) UTF-16 code unit. Identical to UTF-8/Latin-1 for ASCII passwords."""
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:  # astral characters: charCodeAt(0) is the high surrogate
            code = 0xD800 + ((code - 0x10000) >> 10)
        out.append(code & 0xFF)
    return bytes(out)


def derive_key(key: bytes, password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    material = key + js_binary_string(password) if password else key
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_BITS // 8, salt=salt, iterations=iterations)
    return kdf.derive(material)


def compact_json(value: object) -> str:
    """JSON exactly as JavaScript's ``JSON.stringify`` writes these values (no spaces, raw Unicode)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def raw_deflate(data: bytes) -> bytes:
    comp = zlib.compressobj(level=7, wbits=-15)  # PrivateBin: deflate level 7, no zlib header
    return comp.compress(data) + comp.flush()


def encrypt_paste(text: str, *, formatter: str = "plaintext", open_discussion: bool = False,
                  burn_after_reading: bool = False, password: str = "", key: bytes | None = None,
                  iv: bytes | None = None, salt: bytes | None = None) -> tuple[dict, bytes]:
    """The v2 ``adata`` and ``ct`` for ``text``; returns ``(fields, key)`` where fields has
    ``adata`` and ``ct``. ``key``/``iv``/``salt`` are random unless given (tests)."""
    key = key if key is not None else os.urandom(KEY_BITS // 8)
    iv = iv if iv is not None else os.urandom(IV_BYTES)
    salt = salt if salt is not None else os.urandom(SALT_BYTES)
    spec = [base64.b64encode(iv).decode("ascii"), base64.b64encode(salt).decode("ascii"), ITERATIONS, KEY_BITS,
            TAG_BITS, "aes", "gcm", "zlib"]
    adata = [spec, formatter, 1 if open_discussion else 0, 1 if burn_after_reading else 0]
    plaintext = raw_deflate(compact_json({"paste": text}).encode("utf-8"))
    aes = AESGCM(derive_key(key, password, salt))
    ct = aes.encrypt(iv, plaintext, compact_json(adata).encode("utf-8"))  # ciphertext || 16-byte tag
    return {"adata": adata, "ct": base64.b64encode(ct).decode("ascii")}, key


def instance_url(settings: PrivateBinSettings) -> str:
    url = (settings.custom_url or "https://privatebin.net/").strip()
    if "://" not in url:
        url = "https://" + url
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url if url.endswith("/") else url + "/"


@register("text", "PrivateBin", PrivateBinSettings, title="PrivateBin", homepage="https://privatebin.info")
class PrivateBinUploader(TextUploader):
    settings: PrivateBinSettings

    def validate(self) -> list[str]:
        problems = []
        if not self.settings.custom_url:
            problems.append("Instance URL is required")
        if self.settings.burn_after_reading and self.settings.open_discussion:
            problems.append("Burn after reading and open discussion cannot both be enabled")
        return problems

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        fields, key = encrypt_paste(text, formatter=FORMATS.get(s.format, "plaintext"),
                                    open_discussion=s.open_discussion, burn_after_reading=s.burn_after_reading,
                                    password=s.paste_password)
        payload = {"v": 2, "adata": fields["adata"], "ct": fields["ct"],
                   "meta": {"expire": EXPIRATION.get(s.expiration, "1week")}}
        base = instance_url(s)
        auth = (s.username, s.password) if s.username and s.password else None
        resp = self.ctx.post(base, data=compact_json(payload).encode("utf-8"), auth=auth,
                             headers={"Content-Type": "application/json", "X-Requested-With": "JSONHttpRequest",
                                      "Accept": "application/json"})
        if resp.status_code == 401:
            raise UploaderError("PrivateBin: the server requires authorization (HTTP username/password)", resp.text)
        try:
            body = resp.json()
        except ValueError as e:
            raise UploaderError(f"PrivateBin upload failed: HTTP {resp.status_code}: {resp.text[:300]}",
                                resp.text) from e
        if not isinstance(body, dict) or body.get("status") != 0 or not body.get("id"):
            msg = body.get("message") if isinstance(body, dict) else body
            raise UploaderError(f"PrivateBin upload failed: {msg}", resp.text)
        paste_id = body["id"]
        result = UploadResult(url=f"{base}?{paste_id}#{base58_encode(key)}", response=resp.text)
        if body.get("deletetoken"):
            result.deletion_url = f"{base}?{urlencode({'pasteid': paste_id, 'deletetoken': body['deletetoken']})}"
        return result
