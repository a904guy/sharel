"""Shared helpers for the file/storage destinations (ShareX's URLHelpers / FileHelpers / MimeTypes).

Private module (leading underscore): it registers nothing, the ``file_*`` service modules import it.
"""
from __future__ import annotations

import io
import mimetypes
import os
import secrets
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import quote

from ...core.nameparser import NameParser, NameParserType

# ShareX FileHelpers extension lists (used for "remove extension" options and data-type fallbacks)
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "ico", "tif", "tiff", "webp", "avif", "heic", "heif", "jxl"}
TEXT_EXTENSIONS = {"txt", "log", "nfo", "c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs", "vb", "html", "htm",
                   "xhtml", "xht", "xml", "css", "js", "php", "bat", "java", "lua", "py", "pl", "cfg", "ini",
                   "dart", "go", "gohtml", "json", "md", "rs", "sql", "sh", "yaml", "yml", "toml", "csv", "ts"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mkv", "avi", "vob", "ogv", "flv", "mov", "mpg", "mpeg", "m4v", "wmv", "3gp"}

_EXTRA_TYPES = {".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic", ".jxl": "image/jxl",
                ".mkv": "video/x-matroska", ".webm": "video/webm", ".md": "text/markdown", ".log": "text/plain",
                ".7z": "application/x-7z-compressed"}


def extension(file_name: str) -> str:
    return PurePosixPath(file_name).suffix.lower().lstrip(".")


def is_image_file(file_name: str) -> bool:
    return extension(file_name) in IMAGE_EXTENSIONS


def is_text_file(file_name: str) -> bool:
    return extension(file_name) in TEXT_EXTENSIONS


def is_video_file(file_name: str) -> bool:
    return extension(file_name) in VIDEO_EXTENSIONS


def without_extension(file_name: str) -> str:
    stem, dot, _ext = file_name.rpartition(".")
    return stem if dot and stem else file_name


def mime_type(file_name: str) -> str:
    """ShareX MimeTypes.GetMimeTypeFromFileName: by extension, application/octet-stream otherwise."""
    ext = os.path.splitext(file_name)[1].lower()
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    guessed, _enc = mimetypes.guess_type("x" + ext) if ext else (None, None)
    return guessed or "application/octet-stream"


def combine_url(*parts: str) -> str:
    """ShareX URLHelpers.CombineURL: join with exactly one '/' between non-empty parts."""
    out = ""
    for part in parts:
        if part is None or part == "":
            continue
        if not out:
            out = part
        elif out.endswith("/") and part.startswith("/"):
            out = out + part.lstrip("/")
        elif out.endswith("/") or part.startswith("/"):
            out = out + part
        else:
            out = out + "/" + part
    return out


# ApplicationConfig.url_encode_ignore_emoji (set by the app at startup and by Application settings).
URL_ENCODE_IGNORE_EMOJI = False


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2300 <= o <= 0x23FF or 0x2B00 <= o <= 0x2BFF
            or o in (0x200D, 0xFE0F, 0x20E3))


def _encode(text: str, safe: str) -> str:
    if not URL_ENCODE_IGNORE_EMOJI:
        return quote(text, safe=safe)
    return "".join(ch if _is_emoji(ch) else quote(ch, safe=safe) for ch in text)


def url_encode(text: str) -> str:
    """URLHelpers.URLEncode: everything but RFC 3986 unreserved characters is percent-encoded
    (emoji kept as they are when "ignore emoji" is on)."""
    return _encode(text, "")


def url_encode_path(text: str) -> str:
    """URLHelpers.URLEncode(text, isPath: true): like url_encode but keeps '/'."""
    return _encode(text, "/")


def has_prefix(url: str) -> bool:
    return "://" in url[:16]


def fix_prefix(url: str, prefix: str = "https://") -> str:
    """URLHelpers.FixPrefix: add https:// when the URL has no scheme."""
    if url and not has_prefix(url):
        return prefix + url
    return url


def remove_prefixes(url: str) -> str:
    _scheme, sep, rest = url.partition("://")
    return rest if sep else url


def parse_pattern(pattern: str, kind: NameParserType = NameParserType.FolderPath) -> str:
    """Expand a ShareX name-parser pattern (%y, %mo, %rn ...) once."""
    if not pattern:
        return ""
    return NameParser(kind).parse(pattern)


def parse_folder(pattern: str) -> str:
    """A folder path pattern, expanded, with backslashes normalised and no surrounding slashes."""
    return parse_pattern(pattern.replace("\\", "/").strip("/"), NameParserType.FolderPath).strip("/")


def data_type_for(file_name: str, data_type: object = None) -> str:
    """'Image' | 'Text' | 'File' — the task's data type, or a guess from the extension."""
    if data_type is not None:
        name = getattr(data_type, "name", None) or getattr(data_type, "value", None) or str(data_type)
        if name in ("Image", "Text", "File"):
            return name
        return "File"
    if is_image_file(file_name):
        return "Image"
    if is_text_file(file_name):
        return "Text"
    return "File"


def read_all(stream: BinaryIO) -> bytes:
    data = stream.read()
    return data if isinstance(data, bytes) else bytes(data)


def multipart_body(fields: dict[str, str] | list[tuple[str, str]] | None, file_field: str | None, file_name: str,
                   data: bytes, content_type: str | None = None) -> tuple[io.BytesIO, str]:
    """A multipart/form-data body (fields first, then the file) and its Content-Type header value.

    Built in memory so the caller can stream it through ``ctx.progress_stream`` for upload progress.
    """
    boundary = "----ShareL" + secrets.token_hex(16)
    out = io.BytesIO()
    items = list(fields.items()) if isinstance(fields, dict) else list(fields or [])
    for key, value in items:
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{_q(key)}"\r\n\r\n'.encode())
        out.write(str(value).encode("utf-8"))
        out.write(b"\r\n")
    if file_field is not None:
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{_q(file_field)}"; filename="{_q(file_name)}"\r\n'
                  .encode())
        out.write(f"Content-Type: {content_type or mime_type(file_name)}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    out.seek(0)
    return out, f"multipart/form-data; boundary={boundary}"


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "%22").replace("\r", "").replace("\n", "")
