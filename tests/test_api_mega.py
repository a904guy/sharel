"""MEGA: crypto checked against reference implementations written here from the documented algorithms
(MEGA webclient/SDK: a32 word arithmetic, per-block CTR and CBC-MAC loops), plus the API request sequence."""
import base64
import io
import json
import os
import struct

import pytest
import responses
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services import api_mega as mega

API = "https://g.api.mega.co.nz/cs"
UPLOAD_URL = "https://gfs270n123.userstorage.mega.co.nz/ul/AbCdEfGhIjKl"


# ---- reference implementations (independent of the module) ----

def ecb(key, block):
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(block) + e.finalize()


def ecb_dec(key, data):
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def b64u(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def str_to_a32(b):
    b = b + b"\0" * (-len(b) % 4)
    return list(struct.unpack(f">{len(b) // 4}I", b))


def a32_to_str(a):
    return struct.pack(f">{len(a)}I", *a)


def ref_prepare_key(password):
    """webclient prepare_key: arr = str_to_a32(pw); 0x10000 rounds over 4-word slices."""
    arr = str_to_a32(password.encode())
    pkey = [0x93C467E3, 0x7DB0C7A4, 0xD1BE3F81, 0x0152CB56]
    for _ in range(0x10000):
        for j in range(0, len(arr), 4):
            key = [*arr[j:j + 4], 0, 0, 0, 0][:4]
            pkey = str_to_a32(ecb(a32_to_str(key), a32_to_str(pkey)))
    return a32_to_str(pkey)


def ref_stringhash(email, aeskey):
    s32 = str_to_a32(email.encode())
    h32 = [0, 0, 0, 0]
    for i, w in enumerate(s32):
        h32[i % 4] ^= w
    h = a32_to_str(h32)
    for _ in range(0x4000):
        h = ecb(aeskey, h)
    h32 = str_to_a32(h)
    return b64u(a32_to_str([h32[0], h32[2]]))


def ref_encrypt_and_mac(key, nonce, data):
    """Per-16-byte-block CTR and CBC-MAC with MEGA's chunk boundaries (as ShareX's EncryptChunk loop)."""
    out = bytearray()
    file_mac = bytes(16)
    counter = 0
    pos = 0
    for size in mega.chunk_sizes(len(data)):
        chunk = data[pos:pos + size]
        pos += size
        mac = nonce + nonce
        for off in range(0, len(chunk), 16):
            block = chunk[off:off + 16]
            ks = ecb(key, nonce + counter.to_bytes(8, "big"))
            counter += 1
            padded = block + b"\0" * (16 - len(block))
            mac = ecb(key, bytes(a ^ b for a, b in zip(mac, padded, strict=True)))
            out += bytes(a ^ b for a, b in zip(block, ks[:len(block)], strict=False))
        file_mac = ecb(key, bytes(a ^ b for a, b in zip(file_mac, mac, strict=True)))
    m = str_to_a32(file_mac)
    meta = a32_to_str([m[0] ^ m[1], m[2] ^ m[3]])
    k = str_to_a32(key)
    n = str_to_a32(nonce)
    mm = str_to_a32(meta)
    node_key = a32_to_str([k[0] ^ n[0], k[1] ^ n[1], k[2] ^ mm[0], k[3] ^ mm[1], n[0], n[1], mm[0], mm[1]])
    return bytes(out), meta, node_key


def mpi(n):
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return n.bit_length().to_bytes(2, "big") + raw


# ---- crypto ----

def test_chunk_schedule():
    sizes = list(mega.chunk_sizes(5 * 1024 * 1024 + 7))
    assert sizes[:8] == [128 * 1024 * i for i in range(1, 9)]
    assert sizes[8:-1] == [1024 * 1024] * (len(sizes) - 9) and sum(sizes) == 5 * 1024 * 1024 + 7
    assert list(mega.chunk_sizes(0)) == [0] and list(mega.chunk_sizes(10)) == [10]


def test_ctr_encryption_and_condensed_mac_match_reference_and_round_trip():
    key, nonce = os.urandom(16), os.urandom(8)
    data = os.urandom(128 * 1024 + 256 * 1024 + 1000 + 5)  # three chunks, ragged last block
    cipher = mega.FileCipher(key, nonce)
    enc, pos = b"", 0
    for size in mega.chunk_sizes(len(data)):
        enc += cipher.encrypt_chunk(data[pos:pos + size])
        pos += size
    ref_enc, ref_meta, ref_node_key = ref_encrypt_and_mac(key, nonce, data)
    assert enc == ref_enc and cipher.meta_mac == ref_meta and cipher.node_key() == ref_node_key
    dec = Cipher(algorithms.AES(key), modes.CTR(nonce + bytes(8))).decryptor()
    assert dec.update(enc) == data
    assert mega.fold_key(cipher.node_key()) == key  # (k ^ n|m) ^ (n|m)


def test_attributes_round_trip():
    key = os.urandom(16)
    enc = mega.encrypt_attributes({"n": "résumé 1.png"}, key)
    assert len(enc) % 16 == 0
    plain = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor().update(enc)
    assert plain.rstrip(b"\0") == 'MEGA{"n":"résumé 1.png"}'.encode()
    assert mega.decrypt_attributes(enc, key) == {"n": "résumé 1.png"}
    assert mega.decrypt_attributes(enc, os.urandom(16)) is None


def test_v2_key_derivation_matches_pbkdf2_sha512():
    salt = os.urandom(32)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=32, salt=salt, iterations=100000)
    dk = kdf.derive("correct horse ✓".encode())
    key, uh = mega.authentication_info("a@b.c", "correct horse ✓", 2, b64u(salt))
    assert key == dk[:16] and uh == b64u(dk[16:])


def test_v1_legacy_key_and_user_hash_match_a32_reference():
    pw = "a password that is longer than 16 bytes"
    key = mega.prepare_legacy_key(pw)
    assert key == ref_prepare_key(pw)
    assert mega.legacy_user_hash("User@Example.COM", key) == ref_stringhash("user@example.com", key)
    k2, uh2 = mega.authentication_info("User@Example.COM", pw, 1, None)
    assert (k2, uh2) == (key, ref_stringhash("user@example.com", key))
    with pytest.raises(UploaderError, match="version 3"):
        mega.authentication_info("a", "b", 3, None)


def _rsa_account(master_key):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = priv.private_numbers()
    privk = mpi(nums.p) + mpi(nums.q) + mpi(nums.d) + mpi(nums.iqmp)
    privk_enc = ecb(master_key, privk + b"\0" * (-len(privk) % 16))
    sid = os.urandom(43)
    n = nums.public_numbers.n
    m = int.from_bytes(b"\x01" + sid + os.urandom(150), "big")  # sid is the leading 43 bytes of the plaintext
    sid = (b"\x01" + sid)[:43]
    c = pow(m, nums.public_numbers.e, n)
    return b64u(privk_enc), b64u(mpi(c)), b64u(sid)


def test_rsa_session_id_decryption():
    master = os.urandom(16)
    privk, csid, sid = _rsa_account(master)
    assert mega.decrypt_session_id(csid, privk, master) == sid


def test_temporary_session_check():
    master = os.urandom(16)
    challenge = os.urandom(16)
    tsid = b64u(challenge + ecb(master, challenge))
    assert mega.valid_temporary_session(tsid, master)
    assert not mega.valid_temporary_session(b64u(os.urandom(32)), master)


def test_hashcash_token_meets_threshold():
    import hashlib
    token = os.urandom(48)
    easiness = 192 + 63  # (127 << 24): about half of all nonces pass
    challenge = f"1:{easiness}:1700000000:{b64u(token)}"
    answer = mega.hashcash_token(challenge)
    prefix_b64 = answer.split(":")[2]
    assert answer.startswith(f"1:{b64u(token)}:")
    digest = hashlib.sha256(unb64u(prefix_b64) + token * 262144).digest()
    assert int.from_bytes(digest[:4], "big") <= 127 << 24
    with pytest.raises(UploaderError):
        mega.hashcash_token("2:1:2:3")


# ---- API sequence ----

def _cmd(call):
    return json.loads(call.request.body)[0]


@responses.activate(assert_all_requests_are_fired=True)
def test_login_v2_then_upload_into_root_and_export_link():
    email, password = "me@example.com", "hunter2!"
    salt = os.urandom(32)
    pw_key, uh = mega.derive_v2(password, salt)
    master = os.urandom(16)
    privk, csid, sid = _rsa_account(master)
    data = os.urandom(128 * 1024 + 50000)
    token = os.urandom(36)
    uploaded = []

    responses.post(API, json=[{"v": 2, "s": b64u(salt)}])
    responses.post(API, json=[{"k": b64u(ecb(pw_key, master)), "csid": csid, "privk": privk, "u": "UserHndl"}])
    responses.post(API, json=[{"f": [{"h": "RootHndl", "t": 2, "p": "", "a": "", "k": ""},
                                     {"h": "InboxHnd", "t": 3}]}])
    responses.post(API, json=[{"p": UPLOAD_URL + "/"}])

    def chunk_cb(request):
        uploaded.append((request.url.rsplit("/", 1)[1], request.body))
        last = sum(len(b) for _o, b in uploaded) == len(data)
        return 200, {}, token if last else b""

    responses.add_callback(responses.POST, f"{UPLOAD_URL}/0", callback=chunk_cb)
    responses.add_callback(responses.POST, f"{UPLOAD_URL}/{128 * 1024}", callback=chunk_cb)
    responses.post(API, json=[{"f": [{"h": "NodeHndl", "t": 0, "p": "RootHndl"}]}])
    responses.post(API, json=["PubHndl1"])

    saved = []
    ctx = UploadContext(save_settings=lambda: saved.append(1))
    info = registry.get("file", "Mega")
    s = info.settings_cls(email=email, password=password)
    res = info.cls(s, ctx).upload(io.BytesIO(data), "clip.mp4")

    calls = responses.calls
    assert _cmd(calls[0]) == {"a": "us0", "user": email} and "sid" not in calls[0].request.url
    assert _cmd(calls[1]) == {"a": "us", "user": email, "uh": uh}
    assert s.session_id == sid and unb64u(s.master_key) == master and saved
    assert f"sid={sid}" in calls[2].request.url and _cmd(calls[2]) == {"a": "f", "c": 1}
    assert _cmd(calls[3]) == {"a": "u", "ssl": 2, "v": 3, "s": len(data)}
    assert [o for o, _b in uploaded] == ["0", str(128 * 1024)]
    ids = [int(c.request.url.split("id=")[1].split("&")[0]) for c in calls if c.request.url.startswith(API)]
    assert ids == list(range(ids[0], ids[0] + len(ids)))  # sequence numbers increment

    p = _cmd(calls[6])
    assert p["a"] == "p" and p["t"] == "RootHndl" and p["n"][0]["h"] == b64u(token) and p["n"][0]["t"] == 0
    assert _cmd(calls[7]) == {"a": "l", "n": "NodeHndl"}

    # the link: https://mega.nz/file/<handle>#<node key>; decrypt everything with it
    assert res.url.startswith("https://mega.nz/file/PubHndl1#")
    node_key = unb64u(res.url.split("#", 1)[1])
    assert len(node_key) == 32
    assert ecb_dec(master, unb64u(p["n"][0]["k"])) == node_key
    file_key = bytes(a ^ b for a, b in zip(node_key[:16], node_key[16:], strict=True))
    nonce, meta = node_key[16:24], node_key[24:32]
    enc = b"".join(b for _o, b in uploaded)
    assert Cipher(algorithms.AES(file_key), modes.CTR(nonce + bytes(8))).decryptor().update(enc) == data
    assert ref_encrypt_and_mac(file_key, nonce, data)[1] == meta
    assert mega.decrypt_attributes(unb64u(p["n"][0]["a"]), file_key) == {"n": "clip.mp4"}


@responses.activate
def test_stored_session_folder_target_and_api_errors():
    master = os.urandom(16)
    s = registry.get("file", "Mega").settings_cls(session_id="SESSIONID", master_key=b64u(master),
                                                    folder_id="FoldHndl")
    responses.post(API, json=[{"p": UPLOAD_URL}])
    responses.post(f"{UPLOAD_URL}/0", body=os.urandom(36))
    responses.post(API, json=[{"f": [{"h": "N1", "t": 0}]}])
    responses.post(API, json=["PH"])
    res = registry.get("file", "Mega").cls(s, UploadContext()).upload(io.BytesIO(b"tiny"), "a.txt")
    assert _cmd(responses.calls[0])["a"] == "u"  # no login and no node listing with a folder chosen
    assert _cmd(responses.calls[2])["t"] == "FoldHndl" and res.url.startswith("https://mega.nz/file/PH#")
    responses.replace(responses.POST, API, json=-17)
    with pytest.raises(UploaderError, match="over quota"):
        registry.get("file", "Mega").cls(s, UploadContext()).upload(io.BytesIO(b"x"), "a.txt")


@responses.activate
def test_hashcash_402_retry_and_eagain(monkeypatch):
    monkeypatch.setattr(mega, "_sleep", lambda s: None)
    monkeypatch.setattr(mega, "hashcash_token", lambda ch, cancel=None: "1:TOKEN:AAAAAA")
    responses.post(API, status=402, headers={"X-Hashcash": "1:255:1700000000:" + "A" * 64})
    responses.post(API, json=[-3])
    responses.post(API, json=[{"v": 1}])
    client = mega.MegaClient(UploadContext())
    assert client.request({"a": "us0", "user": "x"}, include_session=False) == {"v": 1}
    assert "X-Hashcash" not in responses.calls[0].request.headers
    assert responses.calls[1].request.headers["X-Hashcash"] == "1:TOKEN:AAAAAA"


@responses.activate
def test_two_factor_prompt_and_folder_listing():
    email, password = "me@example.com", "pw"
    salt = os.urandom(32)
    pw_key, _uh = mega.derive_v2(password, salt)
    master = os.urandom(16)
    challenge = os.urandom(16)
    tsid = b64u(challenge + ecb(master, challenge))
    responses.post(API, json=[{"v": 2, "s": b64u(salt)}])
    responses.post(API, json=[-26])
    responses.post(API, json=[{"v": 2, "s": b64u(salt)}])
    responses.post(API, json=[{"k": b64u(ecb(pw_key, master)), "tsid": tsid}])
    fkey = os.urandom(16)
    responses.post(API, json=[{"f": [
        {"h": "Root", "t": 2},
        {"h": "F1", "t": 1, "p": "Root", "k": f"UserHndl:{b64u(ecb(master, fkey))}",
         "a": b64u(mega.encrypt_attributes({"n": "Screens"}, fkey))},
        {"h": "F2", "t": 1, "p": "Other", "k": "x:y", "a": "z"}]}])
    s = registry.get("file", "Mega").settings_cls(email=email, password=password)
    ctx = UploadContext(prompt_input=lambda title, default: "123456")
    assert mega.list_folders(s, ctx) == [{"id": "F1", "name": "Screens"}]
    assert _cmd(responses.calls[3])["mfa"] == "123456" and s.session_id == tsid
