"""Shared helpers for the HTTPS-API file destinations (``api_*`` modules).

Private module (leading underscore): it registers nothing.

* :class:`StreamingBody` — a file-like that concatenates byte strings and (part of) a stream without
  reading the file into memory, with a known length so requests sends ``Content-Length``.
* :func:`multipart_form` / :func:`multipart_related` — ``multipart/form-data`` (file last) and
  Google's ``multipart/related`` (JSON metadata + media) bodies built on it.
* :func:`send_body` — POST/PUT such a body through ``ctx.progress_stream`` for upload progress.
* :func:`read_exact` / :func:`iter_chunks` — fixed-size chunk reading for upload sessions.
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import secrets
from collections.abc import Iterator
from typing import Any, BinaryIO

import requests

from ...core.nameparser import NameParser, NameParserType
from ..base import UploadContext, UploaderError, stream_size

_EXTRA_TYPES = {".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic", ".jxl": "image/jxl",
                ".mkv": "video/x-matroska", ".webm": "video/webm", ".md": "text/markdown", ".log": "text/plain",
                ".7z": "application/x-7z-compressed", ".mp4": "video/mp4", ".mov": "video/quicktime"}


def mime_type(file_name: str) -> str:
    """MIME type from the extension (ShareX MimeTypes), ``application/octet-stream`` when unknown."""
    ext = os.path.splitext(file_name)[1].lower()
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    guessed, _enc = mimetypes.guess_type("x" + ext) if ext else (None, None)
    return guessed or "application/octet-stream"


def parse_pattern(pattern: str, kind: NameParserType = NameParserType.Default) -> str:
    """Expand a ShareX name-parser pattern (``%y``, ``%mo`` ...)."""
    return NameParser(kind).parse(pattern) if pattern else ""


def basic_auth_header(user: str, password: str) -> dict[str, str]:
    """ShareX RequestHelpers.CreateAuthenticationHeader."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def remaining_size(stream: BinaryIO) -> int:
    """Bytes from the current position to the end (0 if the stream can't tell)."""
    return stream_size(stream)


class StreamingBody(io.RawIOBase):
    """Concatenation of ``bytes`` parts and ``(stream, length)`` parts, read lazily."""

    def __init__(self, parts: list[bytes | tuple[BinaryIO, int]]) -> None:
        self._parts = [p if isinstance(p, tuple) else (io.BytesIO(p), len(p)) for p in parts]
        self._index = 0
        self._left = self._parts[0][1] if self._parts else 0
        self.length = sum(n for _s, n in self._parts)

    def readable(self) -> bool:
        return True

    def __len__(self) -> int:
        return self.length

    def read(self, size: int = -1) -> bytes:
        out = bytearray()
        want = self.length if size is None or size < 0 else size
        while len(out) < want and self._index < len(self._parts):
            stream, _n = self._parts[self._index]
            if self._left <= 0:
                self._next()
                continue
            data = stream.read(min(self._left, want - len(out)))
            if not data:
                raise UploaderError("The file ended before its expected size was read")
            out += data
            self._left -= len(data)
            if self._left <= 0:
                self._next()
        return bytes(out)

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def _next(self) -> None:
        self._index += 1
        self._left = self._parts[self._index][1] if self._index < len(self._parts) else 0


def _quote_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "%22").replace("\r", "").replace("\n", "")


def multipart_form(fields: dict[str, Any] | list[tuple[str, Any]] | None, file_field: str, file_name: str,
                   stream: BinaryIO, size: int | None = None,
                   content_type: str | None = None) -> tuple[StreamingBody, str]:
    """``multipart/form-data``: the text fields first, then the file. Returns (body, Content-Type)."""
    if size is None:
        size = remaining_size(stream)
    boundary = "----ShareL" + secrets.token_hex(16)
    head = io.BytesIO()
    items = list(fields.items()) if isinstance(fields, dict) else list(fields or [])
    for key, value in items:
        head.write(f"--{boundary}\r\n".encode())
        head.write(f'Content-Disposition: form-data; name="{_quote_header(key)}"\r\n\r\n'.encode())
        head.write(str(value).encode())
        head.write(b"\r\n")
    head.write(f"--{boundary}\r\n".encode())
    head.write(f'Content-Disposition: form-data; name="{_quote_header(file_field)}"; '
               f'filename="{_quote_header(file_name)}"\r\n'.encode())
    head.write(f"Content-Type: {content_type or mime_type(file_name)}\r\n\r\n".encode())
    tail = f"\r\n--{boundary}--\r\n".encode()
    return StreamingBody([head.getvalue(), (stream, size), tail]), f"multipart/form-data; boundary={boundary}"


def multipart_related(metadata: dict, stream: BinaryIO, size: int | None,
                      content_type: str) -> tuple[StreamingBody, str]:
    """Google's ``multipart/related`` upload: a JSON metadata part, then the media part."""
    if size is None:
        size = remaining_size(stream)
    boundary = "ShareL" + secrets.token_hex(16)
    head = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return StreamingBody([head, (stream, size), tail]), f"multipart/related; boundary={boundary}"


def send_body(ctx: UploadContext, method: str, url: str, body: StreamingBody, content_type: str,
              headers: dict[str, str] | None = None, **kw) -> requests.Response:
    """Send a streaming body with progress reporting."""
    hdrs = {"Content-Type": content_type, "Content-Length": str(len(body))}
    hdrs.update(headers or {})
    return ctx.request(method, url, data=ctx.progress_stream(body, len(body)), headers=hdrs, **kw)


def read_exact(stream: BinaryIO, n: int) -> bytes:
    """Read exactly ``n`` bytes (fewer only at end of stream)."""
    out = bytearray()
    while len(out) < n:
        data = stream.read(n - len(out))
        if not data:
            break
        out += data
    return bytes(out)


def iter_chunks(stream: BinaryIO, size: int, chunk_size: int) -> Iterator[tuple[int, bytes]]:
    """(offset, data) for consecutive chunks of ``size`` bytes of ``stream``."""
    offset = 0
    while offset < size:
        data = read_exact(stream, min(chunk_size, size - offset))
        if not data:
            raise UploaderError("The file ended before its expected size was read")
        yield offset, data
        offset += len(data)


def error_message(resp: requests.Response, what: str) -> str:
    """A short message for a failed API call, using the JSON error text when there is one."""
    detail = resp.text[:500]
    try:
        js = resp.json()
        err = js.get("error") if isinstance(js, dict) else None
        if isinstance(js, dict) and js.get("error_summary"):  # Dropbox
            detail = js["error_summary"]
        elif isinstance(err, dict):
            detail = err.get("message") or err.get("error_summary") or json.dumps(err)
        elif isinstance(err, str):
            detail = js.get("error_description") or js.get("error_summary") or err
        elif isinstance(js, dict) and js.get("message"):
            detail = js["message"]
    except ValueError:
        pass
    return f"{what} failed: HTTP {resp.status_code}: {detail}"


def ensure_ok(resp: requests.Response, what: str) -> requests.Response:
    if not 200 <= resp.status_code < 300:
        raise UploaderError(error_message(resp, what), resp.text)
    return resp


def json_ok(resp: requests.Response, what: str) -> Any:
    ensure_ok(resp, what)
    try:
        return resp.json()
    except ValueError as e:
        raise UploaderError(f"{what}: response is not JSON: {resp.text[:300]}", resp.text) from e
