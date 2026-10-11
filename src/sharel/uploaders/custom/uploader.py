"""The custom uploader engine: one .sxcu item as an image/text/file uploader, URL shortener or sharing service.

Mirrors ShareX's CustomImageUploader / CustomTextUploader / CustomFileUploader / CustomURLShortener /
CustomURLSharer and CustomUploaderItem:

* Request URL: syntax-parsed with ``{input}``/``{filename}`` URL-encoded, ``https://`` added when it has no
  scheme, then Parameters appended as a query string (``key=value`` form-encoded, bare ``key`` when empty).
* Parameters, Headers and Arguments: name parser (``%y``, ``%mo``...) then the syntax parser.
* Data (JSON / XML body): name parser, then ``{input}`` and ``{filename}`` replaced with JSON- or
  XML-escaped text. Data is not run through the syntax parser (ShareX doesn't either).
* Bodies per destination (anything else is "Unsupported request format"):

  =================  =====================================================================
  Image / File       MultipartFormData (file in FileFormName + Arguments), Binary
  Text               None, MultipartFormData (text as a file, or Arguments only when
                     FileFormName is empty), FormURLEncoded, JSON, XML, Binary
  URL shortener /    None, MultipartFormData (Arguments only), FormURLEncoded, JSON, XML
  URL sharing
  =================  =====================================================================

* Response: 2xx parses URL (the whole response when URL is empty), ThumbnailURL and DeletionURL;
  otherwise ErrorMessage, when set, becomes the first error.

File bodies stream from the caller's stream through ``ctx.progress_stream`` (progress + cancellation).
"""
from __future__ import annotations

import codecs
import io
import mimetypes
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import requests

from ..base import (
    FileUploader,
    ProgressReader,
    UploadCancelled,
    UploadContext,
    UploaderError,
    UploadResult,
    stream_size,
)
from .model import DESTINATION_TYPES, CustomUploaderItem
from .parser import (
    CustomSyntaxError,
    CustomUploaderSyntaxParser,
    InvalidJSONResponse,
    ResponseInfo,
    append_query_string,
    create_query_string,
    fix_prefix,
    host_name,
    json_encode,
    name_parse,
    xml_encode,
)

CONTENT_TYPES = {
    "MultipartFormData": "multipart/form-data",
    "FormURLEncoded": "application/x-www-form-urlencoded",
    "JSON": "application/json",
    "XML": "application/xml",
    "Binary": "application/octet-stream",
}

SUPPORTED_BODIES = {
    "ImageUploader": ("MultipartFormData", "Binary"),
    "FileUploader": ("MultipartFormData", "Binary"),
    "TextUploader": ("None", "MultipartFormData", "FormURLEncoded", "JSON", "XML", "Binary"),
    "URLShortener": ("None", "MultipartFormData", "FormURLEncoded", "JSON", "XML"),
    "URLSharingService": ("None", "MultipartFormData", "FormURLEncoded", "JSON", "XML"),
}


def mime_type(file_name: str) -> str:
    """MimeTypes.GetMimeTypeFromFileName: by extension, ``application/octet-stream`` when unknown."""
    return mimetypes.guess_type(file_name or "", strict=False)[0] or "application/octet-stream"


# ---- request bodies -------------------------------------------------------------------------------------

class BodyStream(io.RawIOBase):
    """A request body made of byte strings and (at most a few) progress-reporting file streams.

    It has a length (``len()``) so requests sends a Content-Length instead of chunking, and it can be
    rewound to the start (requests does that when it follows a 307/308 redirect).
    """

    def __init__(self, parts: list[bytes | ProgressReader]) -> None:
        super().__init__()
        self._parts = parts
        self._starts = [self._stream_pos(p) for p in parts]
        self._total = sum(len(p) for p in parts)
        self._index = 0
        self._offset = 0  # within the current bytes part
        self._pos = 0

    @staticmethod
    def _stream_pos(part: bytes | ProgressReader) -> int | None:
        if isinstance(part, ProgressReader):
            try:
                return part._stream.tell()
            except (OSError, AttributeError):
                return None
        return None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return all(s is not None for p, s in zip(self._parts, self._starts, strict=True)
                   if isinstance(p, ProgressReader))

    def __len__(self) -> int:
        return self._total

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence != io.SEEK_SET or offset != 0:
            if whence == io.SEEK_CUR and offset == 0:
                return self._pos
            raise io.UnsupportedOperation("BodyStream can only be rewound to the start")
        for part, start in zip(self._parts, self._starts, strict=True):
            if isinstance(part, ProgressReader):
                if start is None:
                    raise io.UnsupportedOperation("the file stream cannot be rewound")
                part._stream.seek(start)
                part.done = 0
        self._index = self._offset = self._pos = 0
        return 0

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            size = self._total - self._pos
        chunks: list[bytes] = []
        want = size
        while want > 0 and self._index < len(self._parts):
            part = self._parts[self._index]
            if isinstance(part, ProgressReader):
                remaining = len(part) - part.done
                chunk = part.read(min(want, remaining)) if remaining > 0 else b""
                if not chunk:
                    self._index += 1
                    continue
            else:
                chunk = part[self._offset:self._offset + want]
                self._offset += len(chunk)
                if self._offset >= len(part):
                    self._index += 1
                    self._offset = 0
                if not chunk:
                    continue
            chunks.append(chunk)
            want -= len(chunk)
        data = b"".join(chunks)
        self._pos += len(data)
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def _header_value(value: str) -> str | bytes:
    """http.client can only send Latin-1 header text; anything beyond goes out as UTF-8 bytes
    (e.g. a ``{filename}`` header for ``日本.png``) instead of failing the upload."""
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return value.encode("utf-8")
    return value


def _quote_header_value(value: str) -> str:
    """Uploader.QuoteHeaderValue: ``"value"`` with ``\\`` and ``"`` backslash-escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_boundary() -> str:
    """RequestHelpers.CreateBoundary: 20 dashes and a GUID's 32 hex digits."""
    return "-" * 20 + uuid.uuid4().hex


def multipart_parts(boundary: str, args: Mapping[str, str] | None, file_form_name: str | None = None,
                    file_name: str = "", file_body: bytes | ProgressReader | None = None,
                    file_type: str = "") -> list[bytes | ProgressReader]:
    """multipart/form-data as .NET's MultipartFormDataContent writes it: each argument as a part with only
    a Content-Disposition (empty names skipped), then the file part with its Content-Type."""
    b = boundary.encode("ascii")
    parts: list[bytes | ProgressReader] = []
    head = b"--" + b
    for key, value in (args or {}).items():
        if not key:
            continue
        parts.append(head + b"\r\nContent-Disposition: form-data; name=" + _quote_header_value(key).encode("utf-8")
                     + b"\r\n\r\n" + (value or "").encode("utf-8") + b"\r\n")
    if file_body is not None and file_form_name is not None:
        parts.append(head + b"\r\nContent-Type: " + file_type.encode("ascii") + b"\r\nContent-Disposition: form-data; "
                     b"name=" + _quote_header_value(file_form_name).encode("utf-8") + b"; filename="
                     + _quote_header_value(file_name).encode("utf-8") + b"\r\n\r\n")
        parts.append(file_body)
        parts.append(b"\r\n")
    parts.append(head + b"--\r\n")
    return parts


# ---- request / response records (for the editor's Test button and error reports) -----------------------

@dataclass
class RequestSummary:
    method: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body_type: str = "None"
    content_type: str = ""
    fields: dict[str, str] = field(default_factory=dict)  # multipart / form arguments
    file_field: str = ""
    file_name: str = ""
    file_type: str = ""
    file_size: int = 0
    data: str = ""  # JSON / XML text

    def __str__(self) -> str:
        lines = [f"{self.method} {self.url}"]
        if self.content_type:
            lines.append(f"Content-Type: {self.content_type}")
        lines += [f"{k}: {v}" for k, v in self.headers.items()]
        lines.append("")
        if self.body_type == "MultipartFormData":
            lines += [f"{k}: {v}" for k, v in self.fields.items()]
            if self.file_field:
                lines.append(f"{self.file_field}: {self.file_name} ({self.file_type}, {self.file_size} bytes)")
        elif self.body_type == "FormURLEncoded":
            lines.append(create_query_string(self.fields))
        elif self.body_type in ("JSON", "XML"):
            lines.append(self.data)
        elif self.body_type == "Binary":
            lines.append(f"<{self.file_size} bytes of {self.file_type}>")
        return "\n".join(lines).rstrip("\n")


def format_response(info: ResponseInfo | None) -> str:
    """Status line, headers and body: the "raw response" the editor shows."""
    if info is None:
        return ""
    lines = [f"HTTP {info.status_code} {info.status_description}".rstrip()]
    if info.response_url:
        lines.append(f"Response URL: {info.response_url}")
    lines += [f"{k}: {v}" for k, v in info.headers.items()]
    return "\n".join(lines) + "\n\n" + (info.response_text or "")


_CHARSET = re.compile(r"charset\s*=\s*\"?([^\s;\"]+)", re.I)


def response_text(resp: requests.Response) -> str:
    """The body decoded as HttpContent.ReadAsStringAsync does: Content-Type charset, else BOM, else UTF-8."""
    raw = resp.content or b""
    m = _CHARSET.search(resp.headers.get("Content-Type", ""))
    for bom, enc in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                     (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(enc, errors="replace")
    if m:
        try:
            return raw.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


def response_info(resp: requests.Response) -> ResponseInfo:
    return ResponseInfo(status_code=resp.status_code, status_description=resp.reason or "",
                        response_url=resp.url, headers=dict(resp.headers), response_text=response_text(resp))


# ---- the uploader --------------------------------------------------------------------------------------

class CustomUploader(FileUploader):
    """A custom uploader item acting as ``dest_type`` (ImageUploader, TextUploader, FileUploader,
    URLShortener or URLSharingService)."""

    service_name = "Custom uploader"

    def __init__(self, item: CustomUploaderItem, ctx: UploadContext, dest_type: str = "FileUploader") -> None:
        if dest_type not in DESTINATION_TYPES:
            raise ValueError(f"Unknown custom uploader destination type {dest_type!r}")
        super().__init__(item, ctx)
        self.item = item
        self.dest_type = dest_type
        self.last_request: RequestSummary | None = None
        self.last_response: ResponseInfo | None = None

    # -- configuration --

    def validate(self) -> list[str]:
        problems = []
        if not self.item.request_url:
            problems.append("Request URL must be configured.")
        if self.item.body not in SUPPORTED_BODIES[self.dest_type]:
            problems.append(self._unsupported())
        if self.item.body == "MultipartFormData" and not self.item.file_form_name and \
                self.dest_type in ("ImageUploader", "FileUploader"):
            problems.append("File form name must be configured.")
        return problems

    def _unsupported(self) -> str:
        return f"Unsupported request format: {self.item.body}"

    def _host(self) -> str:
        return host_name(self.item.request_url)

    def _parser(self, file_name: str, input_text: str | None, **kw: Any) -> CustomUploaderSyntaxParser:
        return CustomUploaderSyntaxParser(file_name=file_name, input_text=input_text, ctx=self.ctx, **kw)

    def _request_url(self, file_name: str, input_text: str) -> str:
        """CustomUploaderItem.GetRequestURL."""
        if not self.item.request_url:
            raise UploaderError("Request URL must be configured.")
        url = self._parser(file_name, input_text, url_encode=True).parse(self.item.request_url)
        url = fix_prefix(url)
        return append_query_string(url, self._parsed_map(self.item.parameters, file_name, input_text))

    def _parsed_map(self, values: Mapping[str, str], file_name: str, input_text: str) -> dict[str, str]:
        """GetParameters / GetArguments / GetHeaders: name parser, then syntax, for every value."""
        parser = self._parser(file_name, input_text, use_name_parser=True)
        return {k: parser.parse(v) for k, v in (values or {}).items()}

    def _headers(self, file_name: str, input_text: str) -> dict[str, str]:
        return {k: v for k, v in self._parsed_map(self.item.headers, file_name, input_text).items() if k}

    def _file_form_name(self) -> str:
        if not self.item.file_form_name:
            raise UploaderError("File form name must be configured.")
        return self.item.file_form_name

    def _data(self, file_name: str, input_text: str) -> str:
        """CustomUploaderItem.GetData: name parser, then {input}/{filename} escaped for the body type."""
        result = name_parse(self.item.data)
        encode = json_encode if self.item.body == "JSON" else xml_encode if self.item.body == "XML" else None

        def enc(v: str) -> str:
            return encode(v) if encode and v else (v or "")

        values = {"{input}": enc(input_text), "{filename}": enc(file_name)}
        return re.sub(r"\{input\}|\{filename\}", lambda m: values[m.group(0).lower()], result, flags=re.I)

    # -- sending --

    def _send(self, method: str, url: str, headers: dict[str, str], content_type: str | None,
              body: bytes | BodyStream | None, summary: RequestSummary) -> ResponseInfo:
        send_headers: dict[str, str | bytes] = {}
        if content_type:
            send_headers["Content-Type"] = content_type
        # Custom headers win over the defaults, Content-Type included (ShareX docs: they can be overridden).
        for k, v in headers.items():
            for existing in [h for h in send_headers if h.lower() == k.lower()]:
                del send_headers[existing]
            send_headers[k] = _header_value(v)
        summary.method, summary.url, summary.headers = method, url, dict(headers)
        summary.content_type = content_type or ""
        self.last_request = summary
        self.last_response = None
        self.ctx.check_cancelled()
        resp = self.ctx.request(method, url, headers=send_headers, data=body, allow_redirects=True)
        info = response_info(resp)
        self.last_response = info
        return info

    def _stream_body(self, stream: BinaryIO) -> tuple[ProgressReader, int]:
        total = stream_size(stream)
        return self.ctx.progress_stream(stream, total), total

    def _send_multipart(self, url: str, args: dict[str, str], headers: dict[str, str], summary: RequestSummary,
                        file_form_name: str | None = None, file_name: str = "",
                        stream: BinaryIO | None = None) -> ResponseInfo:
        boundary = create_boundary()
        file_body = None
        file_type = ""
        if stream is not None and file_form_name is not None:
            file_body, size = self._stream_body(stream)
            file_type = mime_type(file_name)
            summary.file_field, summary.file_name, summary.file_type, summary.file_size = (
                file_form_name, file_name, file_type, size)
        summary.body_type, summary.fields = "MultipartFormData", dict(args)
        body = BodyStream(multipart_parts(boundary, args, file_form_name, file_name, file_body, file_type))
        return self._send(self.item.request_method, url, headers, f"multipart/form-data; boundary=\"{boundary}\"",
                          body, summary)

    def _send_binary(self, url: str, headers: dict[str, str], file_name: str, stream: BinaryIO,
                     summary: RequestSummary) -> ResponseInfo:
        reader, size = self._stream_body(stream)
        file_type = mime_type(file_name)
        summary.body_type, summary.file_name = "Binary", file_name
        summary.file_type, summary.file_size = file_type, size
        return self._send(self.item.request_method, url, headers, file_type, BodyStream([reader]), summary)

    def _send_form(self, url: str, args: dict[str, str], headers: dict[str, str],
                   summary: RequestSummary) -> ResponseInfo:
        summary.body_type, summary.fields = "FormURLEncoded", dict(args)
        body = create_query_string(args).encode("utf-8")
        return self._send(self.item.request_method, url, headers, CONTENT_TYPES["FormURLEncoded"], body, summary)

    def _send_data(self, url: str, data: str, headers: dict[str, str], summary: RequestSummary) -> ResponseInfo:
        summary.body_type, summary.data = self.item.body, data
        return self._send(self.item.request_method, url, headers, CONTENT_TYPES[self.item.body],
                          data.encode("utf-8"), summary)

    def _send_none(self, url: str, headers: dict[str, str], summary: RequestSummary) -> ResponseInfo:
        summary.body_type = "None"
        return self._send(self.item.request_method, url, headers, None, None, summary)

    def _run(self, build) -> tuple[UploadResult, ResponseInfo | None]:
        """Build and send the request; HTTP-level failures become result errors as in ShareX's Uploader."""
        result = UploadResult()
        try:
            info = build()
        except UploadCancelled:
            raise
        except CustomSyntaxError as e:
            raise UploaderError(f"Custom uploader ({self.item.display_name}): {e}") from e
        except UploaderError as e:
            if self.last_request is None:
                raise  # the request could not even be built (missing URL, unsupported body...)
            result.errors.append(str(e))
            return result, None
        if not info.is_success:
            result.errors.append(f"Response status code does not indicate success: {info.status_code} "
                                 f"({info.status_description}).\nRequest URL: {self.last_request.url}\n"
                                 f"Response: {info.response_text[:2000]}".rstrip())
        result.response = info.response_text
        return result, info

    def _parse_response(self, result: UploadResult, info: ResponseInfo | None, file_name: str,
                        is_shortened_url: bool = False) -> UploadResult:
        """CustomUploaderItem.TryParseResponse."""
        if info is None:
            return result
        try:
            self._parse_response_core(result, info, file_name, is_shortened_url)
        except InvalidJSONResponse as e:
            result.errors.insert(0, f"Custom uploader error. Invalid response content is returned from host "
                                    f"({self._host()}), expected response content is JSON.\n\n{e}")
        except CustomSyntaxError as e:
            result.errors.insert(0, f"Custom uploader error. Unable to parse response content returned from host "
                                    f"({self._host()}).\n\n{e}")
        return result

    def _parse_response_core(self, result: UploadResult, info: ResponseInfo, file_name: str,
                             is_shortened_url: bool) -> None:
        item = self.item
        parser = self._parser(file_name, None, response=info, url_encode=True)
        if info.is_success:
            if item.url:
                url = parser.parse(item.url)
                lowered = item.url.lower()
                if not url and ("{output:" in lowered or "{outputbox:" in lowered):
                    result.is_url_expected = False
            else:
                url = info.response_text
            if is_shortened_url:
                result.shortened_url = url
            else:
                result.url = url
            result.thumbnail_url = parser.parse(item.thumbnail_url)
            result.deletion_url = parser.parse(item.deletion_url)
        elif item.error_message:
            message = parser.parse(item.error_message)
            if message:
                result.errors.insert(0, message)

    def _require_body(self) -> str:
        body = self.item.body
        if body not in SUPPORTED_BODIES[self.dest_type]:
            raise UploaderError(self._unsupported())
        return body

    # -- the four entry points --

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        """Image / file upload (CustomImageUploader / CustomFileUploader)."""
        if self.dest_type == "TextUploader":
            return self.upload_text(stream.read().decode("utf-8", errors="replace"), file_name)
        if self.dest_type not in ("ImageUploader", "FileUploader"):
            raise UploaderError(f"A custom {self.dest_type} cannot upload files")
        self.ctx.check_cancelled()
        self.last_request = None
        body = self._require_body()
        inp = ""
        summary = RequestSummary()

        def build() -> ResponseInfo:
            if body == "MultipartFormData":
                url = self._request_url(file_name, inp)
                form_name = self._file_form_name()
                args = self._parsed_map(self.item.arguments, file_name, inp)
                headers = self._headers(file_name, inp)
                return self._send_multipart(url, args, headers, summary, form_name, file_name, stream)
            url = self._request_url(file_name, inp)
            headers = self._headers(file_name, inp)
            return self._send_binary(url, headers, file_name, stream, summary)

        result, info = self._run(build)
        return self._parse_response(result, info, file_name)

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        """Text upload (CustomTextUploader): ``{input}`` is the text. A custom image/file uploader
        uploads the text as a UTF-8 file instead."""
        if self.dest_type in ("ImageUploader", "FileUploader"):
            return self.upload(io.BytesIO(text.encode("utf-8")), file_name)
        if self.dest_type != "TextUploader":
            raise UploaderError(f"A custom {self.dest_type} cannot upload text")
        self.ctx.check_cancelled()
        self.last_request = None
        body = self._require_body()
        summary = RequestSummary()

        def build() -> ResponseInfo:
            if body == "None":
                return self._send_none(self._request_url(file_name, text), self._headers(file_name, text), summary)
            if body == "MultipartFormData":
                if not self.item.file_form_name:
                    url = self._request_url(file_name, text)
                    args = self._parsed_map(self.item.arguments, file_name, text)
                    return self._send_multipart(url, args, self._headers(file_name, text), summary)
                url = self._request_url(file_name, text)
                form_name = self._file_form_name()
                args = self._parsed_map(self.item.arguments, file_name, text)
                headers = self._headers(file_name, text)
                return self._send_multipart(url, args, headers, summary, form_name, file_name,
                                            io.BytesIO(text.encode("utf-8")))
            if body == "FormURLEncoded":
                url = self._request_url(file_name, text)
                args = self._parsed_map(self.item.arguments, file_name, text)
                return self._send_form(url, args, self._headers(file_name, text), summary)
            if body in ("JSON", "XML"):
                url = self._request_url(file_name, text)
                data = self._data(file_name, text)
                return self._send_data(url, data, self._headers(file_name, text), summary)
            url = self._request_url(file_name, text)
            headers = self._headers(file_name, text)
            return self._send_binary(url, headers, file_name, io.BytesIO(text.encode("utf-8")), summary)

        result, info = self._run(build)
        return self._parse_response(result, info, file_name)

    def _url_request(self, url: str, summary: RequestSummary) -> ResponseInfo:
        """The request CustomURLShortener and CustomURLSharer make: ``{input}`` is the URL, no file name."""
        body = self._require_body()
        if body == "None":
            return self._send_none(self._request_url("", url), self._headers("", url), summary)
        if body == "MultipartFormData":
            req_url = self._request_url("", url)
            args = self._parsed_map(self.item.arguments, "", url)
            return self._send_multipart(req_url, args, self._headers("", url), summary)
        if body == "FormURLEncoded":
            req_url = self._request_url("", url)
            args = self._parsed_map(self.item.arguments, "", url)
            return self._send_form(req_url, args, self._headers("", url), summary)
        req_url = self._request_url("", url)
        data = self._data("", url)
        return self._send_data(req_url, data, self._headers("", url), summary)

    def shorten_url(self, url: str) -> UploadResult:
        """URL shortening (CustomURLShortener): result.url is the original, shortened_url the parsed URL."""
        if self.dest_type not in ("URLShortener", "URLSharingService"):
            raise UploaderError(f"A custom {self.dest_type} cannot shorten URLs")
        self.ctx.check_cancelled()
        self.last_request = None
        self._require_body()
        summary = RequestSummary()
        result, info = self._run(lambda: self._url_request(url, summary))
        result.url = url
        return self._parse_response(result, info, "", is_shortened_url=True)

    def share_url_result(self, url: str) -> UploadResult:
        """URL sharing (CustomURLSharer) with the full result; no URL is expected back."""
        if self.dest_type not in ("URLSharingService", "URLShortener"):
            raise UploaderError(f"A custom {self.dest_type} cannot share URLs")
        self.ctx.check_cancelled()
        self.last_request = None
        self._require_body()
        summary = RequestSummary()
        result, info = self._run(lambda: self._url_request(url, summary))
        result.url = url
        result.is_url_expected = False
        return self._parse_response(result, info, "")

    def share_url(self, url: str) -> str:
        """Share ``url``; the request itself is the share, so there is nothing to open: returns ``""``.
        Raises UploaderError when the service reports a failure."""
        result = self.share_url_result(url)
        if result.errors:
            raise UploaderError("\n".join(result.errors), result.response)
        return ""


# ---- the editor's Test button ---------------------------------------------------------------------------

TEST_URL = "https://getsharex.com"
TEST_TEXT = "ShareL custom uploader test"


def sample_png() -> bytes:
    """A small generated PNG to test-upload (ShareX uploads its logo as Test.png)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 59, 59), fill=(41, 128, 185, 255))
    draw.ellipse((20, 20, 43, 43), fill=(236, 240, 241, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test(item: CustomUploaderItem, dest_type: str, ctx: UploadContext,
         text: str | None = None) -> tuple[UploadResult, str, str]:
    """Run ``item`` as ``dest_type`` on generated sample data, as ShareX's custom uploader Test button does.

    Image / file: a generated ``Test.png``; text: ``text`` (or a sample sentence) as ``Test.txt``;
    URL shortener / sharing: ``https://getsharex.com``. Returns (result, request summary, raw response).
    Failures come back as ``result.errors``; only cancellation raises.
    """
    up = CustomUploader(item, ctx, dest_type)
    try:
        if dest_type in ("ImageUploader", "FileUploader"):
            result = up.upload(io.BytesIO(sample_png()), "Test.png")
        elif dest_type == "TextUploader":
            result = up.upload_text(text or TEST_TEXT, "Test.txt")
        elif dest_type == "URLShortener":
            result = up.shorten_url(TEST_URL)
        else:
            result = up.share_url_result(TEST_URL)
    except UploadCancelled:
        raise
    except UploaderError as e:
        result = UploadResult(errors=[str(e)], response=e.response)
    summary = str(up.last_request) if up.last_request else ""
    return result, summary, format_response(up.last_response)


test.__test__ = False  # not a pytest test function
