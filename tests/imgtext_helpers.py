"""Helpers for the image/text uploader tests: decode what a mocked request actually sent."""
from __future__ import annotations

import email
import email.policy
import json
from urllib.parse import parse_qs, urlsplit


def _body_bytes(request) -> bytes:
    body = request.body
    if body is None:
        return b""
    if hasattr(body, "read"):
        body = body.read()
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def multipart(request) -> dict[str, tuple[str | None, bytes]]:
    """``{field: (filename, content)}`` from a multipart/form-data request."""
    ctype = request.headers["Content-Type"]
    assert ctype.startswith("multipart/form-data"), ctype
    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + _body_bytes(request),
                                   policy=email.policy.HTTP)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        out[name] = (part.get_filename(), part.get_payload(decode=True))
    return out


def form(request) -> dict[str, str]:
    """Fields of an application/x-www-form-urlencoded body (single values)."""
    assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    return {k: v[0] for k, v in parse_qs(_body_bytes(request).decode(), keep_blank_values=True).items()}


def json_body(request):
    return json.loads(_body_bytes(request).decode("utf-8"))


def query(request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(request.url).query, keep_blank_values=True).items()}
