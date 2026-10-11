"""Hash checker: known digests, streaming, progress, cancel and comparisons."""
from __future__ import annotations

import shutil
import subprocess
import threading

import pytest

from sharel.tools._common import OperationCancelled
from sharel.tools.hashcheck import (
    HashType,
    compare_files,
    detect_hash_type,
    hash_bytes,
    hash_file,
    hash_file_multi,
    hash_text,
    hashes_match,
)

# Known test vectors for b"abc" (FIPS 180 / RFC 1321) and the classic CRC-32 check value.
ABC = {
    HashType.MD5: "900150983cd24fb0d6963f7d28e17f72",
    HashType.SHA1: "a9993e364706816aba3e25717850c26c9cd0d89d",
    HashType.SHA256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    HashType.SHA384: "cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed"
                     "8086072ba1e7cc2358baeca134c825a7",
    HashType.SHA512: "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
                     "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
    HashType.CRC32: "352441c2",
}


@pytest.mark.parametrize("ht", list(HashType))
def test_known_vectors(ht):
    assert hash_bytes(b"abc", ht) == ABC[ht]
    assert hash_text("abc", ht) == ABC[ht]
    assert hash_text("abc", ht.value.lower()) == ABC[ht]


def test_crc32_check_value_and_empty():
    assert hash_bytes(b"123456789", HashType.CRC32) == "cbf43926"
    assert hash_bytes(b"", HashType.CRC32) == "00000000"
    assert hash_bytes(b"", HashType.MD5) == "d41d8cd98f00b204e9800998ecf8427e"


def test_unicode_text_hashes_utf8_bytes():
    assert hash_text("héllo ✓", HashType.SHA256) == hash_bytes("héllo ✓".encode(), HashType.SHA256)


def test_file_streaming_matches_sha256sum(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(bytes(range(256)) * 20000)  # 5 MB -> several chunks
    calls = []
    digest = hash_file(p, HashType.SHA256, progress=lambda d, t: calls.append((d, t)), chunk_size=1 << 20)
    if shutil.which("sha256sum"):
        assert digest == subprocess.check_output(["sha256sum", str(p)]).split()[0].decode()
    assert calls[0] == (0, p.stat().st_size)
    assert calls[-1] == (p.stat().st_size, p.stat().st_size)
    assert len(calls) >= 5
    multi = hash_file_multi(p, list(HashType))
    assert multi[HashType.SHA256] == digest
    assert multi[HashType.MD5] == hash_bytes(p.read_bytes(), HashType.MD5)
    assert multi[HashType.CRC32] == hash_bytes(p.read_bytes(), HashType.CRC32)


def test_cancel(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"x" * (3 << 20))
    ev = threading.Event()

    def prog(done, total):
        if done >= 1 << 20:
            ev.set()

    with pytest.raises(OperationCancelled):
        hash_file(p, HashType.MD5, progress=prog, cancel=ev, chunk_size=1 << 20)


def test_compare_with_expected():
    assert hashes_match(ABC[HashType.MD5], "  900150983CD24FB0 D6963F7D28E17F72\n")
    assert hashes_match(ABC[HashType.CRC32], "0x352441C2")
    assert not hashes_match(ABC[HashType.MD5], ABC[HashType.SHA1])
    assert not hashes_match("", "")


def test_compare_two_files(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    c.write_bytes(b"other content")
    seen = []
    r = compare_files(a, b, HashType.SHA1, progress=lambda d, t: seen.append((d, t)))
    assert r.match and r.hash_a == r.hash_b == hash_bytes(b"same content", HashType.SHA1)
    assert seen[-1] == (24, 24)
    assert not compare_files(a, c).match


def test_detect_hash_type():
    assert detect_hash_type(ABC[HashType.SHA256]) == [HashType.SHA256]
    assert detect_hash_type(ABC[HashType.CRC32].upper()) == [HashType.CRC32]
    assert detect_hash_type("zz") == []
