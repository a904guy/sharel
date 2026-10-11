"""Helpers for the HTTPS-API destination tests: request bodies and multipart parsing."""
from __future__ import annotations

import json
import re
from email.parser import BytesParser
from email.policy import HTTP
from urllib.parse import parse_qs, urlparse


def body_bytes(req) -> bytes:
    """The request body as bytes (requests keeps file-like bodies as objects)."""
    body = req.body
    if body is None:
        return b""
    if isinstance(body, str):
        return body.encode()
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    cached = getattr(req, "_sharel_body", None)
    if cached is None:
        cached = body.read()
        req._sharel_body = cached
    return cached


def body_json(req):
    return json.loads(body_bytes(req))


def multipart(req) -> dict[str, tuple[str | None, bytes, str]]:
    """{name: (filename, data, content_type)} of a multipart/form-data or multipart/related request."""
    ctype = req.headers["Content-Type"]
    raw = b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body_bytes(req)
    msg = BytesParser(policy=HTTP).parsebytes(raw)
    assert msg.is_multipart(), ctype
    out = {}
    for i, part in enumerate(msg.iter_parts()):
        name = part.get_param("name", header="content-disposition") or f"part{i}"
        out[name] = (part.get_filename(), part.get_payload(decode=True), part.get_content_type())
    return out


def field(req, name: str) -> str:
    return multipart(req)[name][1].decode()


def query(req) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(req.url).query, keep_blank_values=True).items()}


def boundary_ok(req) -> bool:
    return bool(re.search(r"boundary=", req.headers["Content-Type"]))
