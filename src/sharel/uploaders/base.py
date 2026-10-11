"""Uploader base classes, results, and the per-upload context (HTTP session, progress, cancel, prompts).

Every destination is a subclass of one of:

* :class:`FileUploader` (also used for image destinations) — ``upload(stream, file_name)``
* :class:`TextUploader` — ``upload_text(text, file_name)``
* :class:`URLShortener` — ``shorten_url(url)``
* :class:`URLSharer` — ``share_url(url)``: returns the URL to open in the browser, or ``""`` when the
  service did the sharing itself (Email, Pushbullet)

and is registered with :func:`sharel.uploaders.registry.register`.
"""
from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

import requests

USER_AGENT = "ShareL/0.1 (+https://github.com/a904guy/ShareL)"


class UploaderError(Exception):
    """A failed upload with a message meant for the user (and the raw response, if any)."""

    def __init__(self, message: str, response: str = "") -> None:
        super().__init__(message)
        self.response = response


class UploadCancelled(UploaderError):
    def __init__(self) -> None:
        super().__init__("Upload cancelled")


@dataclass
class UploadResult:
    url: str = ""
    thumbnail_url: str = ""
    deletion_url: str = ""
    shortened_url: str = ""
    response: str = ""
    errors: list[str] = field(default_factory=list)
    is_url_expected: bool = True

    @property
    def is_success(self) -> bool:
        return not self.errors and (bool(self.url) or not self.is_url_expected)

    @property
    def result_url(self) -> str:
        return self.shortened_url or self.url


class ProgressReader(io.RawIOBase):
    """Wraps a stream, reporting bytes read and raising UploadCancelled when asked to stop."""

    def __init__(self, stream: BinaryIO, total: int, ctx: UploadContext) -> None:
        self._stream = stream
        self.total = total
        self.done = 0
        self._ctx = ctx

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        self._ctx.check_cancelled()
        chunk = self._stream.read(size)
        self.done += len(chunk)
        self._ctx.report(self.done, self.total)
        return chunk

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def __len__(self) -> int:
        return self.total

    def tell(self) -> int:
        # requests' super_len() subtracts tell() from len(); RawIOBase.tell() raises (not seekable), which makes
        # requests treat the length as 0 and send the body chunked without Content-Length.
        return self.done


class UploadContext:
    """What an uploader may use from its environment. The task layer makes one per upload."""

    def __init__(self, *, proxies: dict[str, str] | None = None, timeout: float = 60.0,
                 progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                 prompt_input: Callable[[str, str], str | None] | None = None,
                 prompt_select: Callable[[str, list[str]], str | None] | None = None,
                 show_output: Callable[[str, str], None] | None = None,
                 open_url: Callable[[str], None] | None = None,
                 save_settings: Callable[[], None] | None = None,
                 session: requests.Session | None = None) -> None:
        self.proxies = proxies
        self.timeout = timeout
        self._progress = progress
        self.cancel_event = cancel or threading.Event()
        self.prompt_input = prompt_input or (lambda _title, default: default)
        self.prompt_select = prompt_select or (lambda _title, options: options[0] if options else None)
        self.show_output = show_output or (lambda _title, _text: None)
        self.open_url = open_url or _default_open_url
        self.save_settings = save_settings or (lambda: None)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        if proxies:
            self.session.proxies.update(proxies)

    def report(self, done: int, total: int) -> None:
        if self._progress:
            self._progress(done, total)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise UploadCancelled()

    # -- HTTP helpers every uploader uses --

    def request(self, method: str, url: str, **kw) -> requests.Response:
        self.check_cancelled()
        kw.setdefault("timeout", self.timeout)
        try:
            return self.session.request(method, url, **kw)
        except requests.RequestException as e:
            raise UploaderError(f"{method} {url} failed: {e}") from e

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw) -> requests.Response:
        return self.request("PUT", url, **kw)

    def progress_stream(self, stream: BinaryIO, total: int | None = None) -> ProgressReader:
        if total is None:
            total = stream_size(stream)
        return ProgressReader(stream, total, self)


def _default_open_url(url: str) -> None:
    import subprocess
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stream_size(stream: BinaryIO) -> int:
    try:
        pos = stream.tell()
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size - pos
    except (OSError, AttributeError):
        return 0


def as_stream(data: bytes | BinaryIO | str | Path) -> BinaryIO:
    if isinstance(data, (bytes, bytearray)):
        return io.BytesIO(bytes(data))
    if isinstance(data, (str, Path)):
        return open(data, "rb")  # noqa: SIM115  (caller closes)
    return data


def check_response(resp: requests.Response, what: str = "Upload") -> str:
    """Raise UploaderError for a non-2xx response, else return its text."""
    text = resp.text
    if not (200 <= resp.status_code < 300):
        raise UploaderError(f"{what} failed: HTTP {resp.status_code} {resp.reason}: {text[:500]}", text)
    return text


def json_of(resp: requests.Response, what: str = "Upload") -> Any:
    text = check_response(resp, what)
    try:
        return json.loads(text)
    except ValueError as e:
        raise UploaderError(f"{what}: response is not JSON: {text[:300]}", text) from e


class Uploader:
    """Common state. ``settings`` is the service's settings dataclass instance."""

    category: ClassVar[str] = ""
    service_name: ClassVar[str] = ""

    def __init__(self, settings: Any, ctx: UploadContext) -> None:
        self.settings = settings
        self.ctx = ctx

    def validate(self) -> list[str]:
        """Problems that make this destination unusable as configured (shown before uploading)."""
        return []


class FileUploader(Uploader):
    category = "file"

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        raise NotImplementedError

    def upload_path(self, path: str | Path, file_name: str | None = None) -> UploadResult:
        with open(path, "rb") as fh:
            return self.upload(fh, file_name or Path(path).name)

    def upload_bytes(self, data: bytes, file_name: str) -> UploadResult:
        return self.upload(io.BytesIO(data), file_name)


class ImageUploader(FileUploader):
    category = "image"


class TextUploader(Uploader):
    category = "text"

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        raise NotImplementedError


class URLShortener(Uploader):
    category = "shortener"

    def shorten_url(self, url: str) -> UploadResult:
        raise NotImplementedError


class URLSharer(Uploader):
    category = "sharing"

    def share_url(self, url: str) -> str:
        raise NotImplementedError


class URLTemplateSharer(URLSharer):
    """A sharing service that is just a URL to open, e.g. ``https://www.reddit.com/submit?url={url}``."""

    template: ClassVar[str] = ""

    def share_url(self, url: str) -> str:
        from urllib.parse import quote
        return self.template.format(url=quote(url, safe=""))
