"""Hash checker (ShareX HashChecker): MD5, SHA1, SHA256, SHA384, SHA512 and CRC32 of files or text.

API
---
``hash_bytes(data, hash_type) -> str``                 lowercase hex digest of bytes.
``hash_text(text, hash_type, encoding="utf-8") -> str`` digest of a string's encoded bytes.
``hash_file(path, hash_type, progress=None, cancel=None, chunk_size=1 MiB) -> str``
    streams the file; ``progress(done_bytes, total_bytes)`` is called after every chunk; setting ``cancel``
    (a ``threading.Event``) raises ``OperationCancelled``.
``hash_file_multi(path, hash_types, progress=None, cancel=None) -> dict[HashType, str]``
    every requested digest in one read of the file.
``normalize_hash(text) -> str``                        strip whitespace, a leading "0x" and lower-case it.
``hashes_match(actual, expected) -> bool``             case- and whitespace-insensitive comparison.
``compare_files(path_a, path_b, hash_type=SHA256, progress=None, cancel=None) -> FileComparison``
    hashes both (progress counts bytes across both files) and says whether they match.
``detect_hash_type(text) -> list[HashType]``           algorithms whose digest length matches ``text``.

CRC32 digests are the 8-digit zero-padded big-endian hex of zlib's CRC-32 (as ``crc32``/7-Zip show it).
"""
from __future__ import annotations

import hashlib
import os
import threading
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from ..core.enums import HashType
from ._common import ProgressFn, check_cancel

__all__ = ["FileComparison", "HashType", "compare_files", "detect_hash_type", "hash_bytes", "hash_file",
           "hash_file_multi", "hash_text", "hashes_match", "normalize_hash"]

DEFAULT_CHUNK = 1024 * 1024

_DIGEST_HEX_LENGTH = {"CRC32": 8, "MD5": 32, "SHA1": 40, "SHA256": 64, "SHA384": 96, "SHA512": 128}


class _Crc32:
    def __init__(self) -> None:
        self._v = 0

    def update(self, data: bytes) -> None:
        self._v = zlib.crc32(data, self._v)

    def hexdigest(self) -> str:
        return f"{self._v & 0xFFFFFFFF:08x}"


def _coerce(hash_type: HashType | str) -> HashType:
    if isinstance(hash_type, HashType):
        return hash_type
    try:
        return HashType[str(hash_type).upper()]
    except KeyError:
        raise ValueError(f"unknown hash type {hash_type!r}") from None


def _new(hash_type: HashType | str):
    name = _coerce(hash_type).value
    if name == "CRC32":
        return _Crc32()
    return hashlib.new(name.lower())


def hash_bytes(data: bytes, hash_type: HashType | str) -> str:
    h = _new(hash_type)
    h.update(data)
    return h.hexdigest()


def hash_text(text: str, hash_type: HashType | str, encoding: str = "utf-8") -> str:
    return hash_bytes(text.encode(encoding), hash_type)


def hash_file_multi(path: str | os.PathLike, hash_types: Iterable[HashType | str], progress: ProgressFn | None = None,
                    cancel: threading.Event | None = None, chunk_size: int = DEFAULT_CHUNK) -> dict[HashType, str]:
    types = [_coerce(t) for t in hash_types]
    if not types:
        raise ValueError("no hash types requested")
    hashers = {t: _new(t) for t in types}
    total = os.path.getsize(path)
    done = 0
    if progress:
        progress(0, total)
    with open(path, "rb") as f:
        while True:
            check_cancel(cancel)
            chunk = f.read(chunk_size)
            if not chunk:
                break
            for h in hashers.values():
                h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    check_cancel(cancel)
    return {t: h.hexdigest() for t, h in hashers.items()}


def hash_file(path: str | os.PathLike, hash_type: HashType | str, progress: ProgressFn | None = None,
              cancel: threading.Event | None = None, chunk_size: int = DEFAULT_CHUNK) -> str:
    t = _coerce(hash_type)
    return hash_file_multi(path, [t], progress, cancel, chunk_size)[t]


def normalize_hash(text: str) -> str:
    s = "".join(text.split()).lower()
    if s.startswith("0x"):
        s = s[2:]
    return s


def hashes_match(actual: str, expected: str) -> bool:
    a, e = normalize_hash(actual), normalize_hash(expected)
    return bool(a) and a == e


def detect_hash_type(text: str) -> list[HashType]:
    s = normalize_hash(text)
    if not s or any(c not in "0123456789abcdef" for c in s):
        return []
    return [HashType[n] for n, length in _DIGEST_HEX_LENGTH.items() if length == len(s)]


@dataclass(frozen=True)
class FileComparison:
    hash_type: HashType
    hash_a: str
    hash_b: str

    @property
    def match(self) -> bool:
        return self.hash_a == self.hash_b


def compare_files(path_a: str | os.PathLike, path_b: str | os.PathLike, hash_type: HashType | str = HashType.SHA256,
                  progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> FileComparison:
    t = _coerce(hash_type)
    size_a = os.path.getsize(path_a)
    total = size_a + os.path.getsize(path_b)

    def prog_a(done: int, _total: int) -> None:
        if progress:
            progress(done, total)

    def prog_b(done: int, _total: int) -> None:
        if progress:
            progress(size_a + done, total)

    ha = hash_file(path_a, t, prog_a, cancel)
    hb = hash_file(path_b, t, prog_b, cancel)
    return FileComparison(t, ha, hb)
