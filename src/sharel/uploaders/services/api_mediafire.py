"""MediaFire (ShareX MediaFire.cs, Core API 1.5: https://www.mediafire.com/developers/core_api/1.5/).

1. ``user/get_session_token.php`` with email, password, ``application_id``, ``token_version=2`` and
   ``signature = sha1(email + password + application_id + api_key)`` → ``session_token``, ``secret_key``,
   ``time``.
2. Every later call is signed: ``signature = md5(str(secret_key % 256) + time + uri)`` where ``uri`` is the
   path plus query string (without the signature). After a response with ``new_key: "yes"`` the key
   rolls forward: ``secret_key = secret_key * 16807 % 2147483647``.
3. ``upload/simple.php?session_token&path&...`` (multipart ``Filedata``) → ``doupload.key``.
4. ``upload/poll_upload.php?key=...`` until ``doupload.status == 99`` → ``quickkey``;
   URL ``https://www.mediafire.com/view/<quickkey>`` (+ ``/<file name>`` with "long link").

ShareX ships its own application ID/key; ShareL has none, so they are settings (create an application at
https://www.mediafire.com/developers/ → My Apps).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import quote, urlencode

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import error_message, multipart_form, parse_pattern, send_body

HOST = "https://www.mediafire.com"
API_PATH = "/api/1.5/"
POLL_INTERVAL = 1.0
MAX_WAIT = 15 * 60.0
STATUS_DONE = 99

_sleep = time.sleep
_monotonic = time.monotonic


@dataclass
class MediaFireSettings:
    application_id: str = setting("", label="Application ID",
                                  help="Create an app at https://www.mediafire.com/developers/ (My Apps)")
    api_key: str = setting("", label="API key", secret=True, help="The application's API key")
    username: str = setting("", label="Email")
    password: str = setting("", label="Password", secret=True)
    path: str = setting("", label="Upload path", help="Folder path, e.g. ShareL/%y-%mo (empty: My Files root)")
    use_long_link: bool = setting(False, label="Use long link (append file name)")


def init_signature(email: str, password: str, app_id: str, api_key: str) -> str:
    return hashlib.sha1((email + password + app_id + api_key).encode()).hexdigest()


def call_signature(secret_key: int, sig_time: str, uri: str) -> str:
    return hashlib.md5(f"{secret_key % 256}{sig_time}{uri}".encode()).hexdigest()


def next_secret_key(secret_key: int) -> int:
    return secret_key * 16807 % 2147483647


class MediaFireSession:
    """A token-version-2 session: signs each call and rolls the secret key."""

    def __init__(self, ctx: UploadContext, settings: MediaFireSettings) -> None:
        self.ctx = ctx
        self.settings = settings
        self.token = ""
        self.time = ""
        self.secret_key = 0

    def login(self) -> None:
        s = self.settings
        data = {"email": s.username, "password": s.password, "application_id": s.application_id,
                "token_version": "2", "response_format": "json",
                "signature": init_signature(s.username, s.password, s.application_id, s.api_key)}
        resp = self.ctx.post(HOST + API_PATH + "user/get_session_token.php", data=data)
        r = self._response(resp, "MediaFire login", roll=False)
        if not r.get("session_token") or r.get("secret_key") in (None, "") or not r.get("time"):
            raise UploaderError("MediaFire login: invalid response", resp.text)
        self.token = r["session_token"]
        self.secret_key = int(r["secret_key"])
        self.time = str(r["time"])

    def signed_url(self, action: str, params: dict[str, Any]) -> str:
        """The full URL with ``session_token`` and a signature over the exact query string sent."""
        query = urlencode({"session_token": self.token, **params, "response_format": "json"})
        uri = f"{API_PATH}{action}?{query}"
        return f"{HOST}{uri}&signature={call_signature(self.secret_key, self.time, uri)}"

    def _response(self, resp, what: str, roll: bool = True) -> dict:
        try:
            r = resp.json()["response"]
        except (ValueError, KeyError, TypeError):
            raise UploaderError(error_message(resp, what), resp.text) from None
        if roll and r.get("new_key") == "yes":
            self.secret_key = next_secret_key(self.secret_key)
        if r.get("result") != "Success":
            raise UploaderError(f"{what} failed: {r.get('message') or 'unknown error'} "
                                f"(error {r.get('error', '?')})", resp.text)
        return r

    def call(self, resp, what: str) -> dict:
        return self._response(resp, what)


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register("file", "MediaFire", MediaFireSettings, title="MediaFire", homepage=HOST)
class MediaFireUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        errors = []
        if not s.application_id or not s.api_key:
            errors.append("MediaFire application ID and API key are required")
        if not s.username or not s.password:
            errors.append("MediaFire email and password are required")
        return errors

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        problems = self.validate()
        if problems:
            raise UploaderError("; ".join(problems))
        session = MediaFireSession(self.ctx, self.settings)
        session.login()
        params: dict[str, str] = {"action_on_duplicate": "keep"}
        path = parse_pattern(self.settings.path, NameParserType.URL).strip("/")
        if path:
            params["path"] = path
        body, ctype = multipart_form(None, "Filedata", file_name, stream)
        resp = send_body(self.ctx, "POST", session.signed_url("upload/simple.php", params), body, ctype)
        r = session.call(resp, "MediaFire upload")
        doupload = r.get("doupload") or {}
        if _int(doupload.get("result")) != 0 or not doupload.get("key"):
            raise UploaderError(f"MediaFire upload failed (result {doupload.get('result')})", resp.text)
        return self._poll(session, doupload["key"], file_name)

    def _poll(self, session: MediaFireSession, key: str, file_name: str) -> UploadResult:
        deadline = _monotonic() + MAX_WAIT
        while True:
            self.ctx.check_cancelled()
            resp = self.ctx.post(session.signed_url("upload/poll_upload.php", {"key": key, "filename": file_name}))
            r = session.call(resp, "MediaFire upload status")
            d = r.get("doupload") or {}
            if d.get("result") is None or d.get("status") is None:
                raise UploaderError("MediaFire upload status: invalid response", resp.text)
            if _int(d.get("result")) != 0 or d.get("fileerror"):
                raise UploaderError(f"MediaFire could not upload the file: "
                                    f"{d.get('description') or d.get('fileerror') or 'unknown error'}", resp.text)
            if _int(d.get("status")) == STATUS_DONE:
                if not d.get("quickkey"):
                    raise UploaderError("MediaFire upload status: no quickkey", resp.text)
                url = f"{HOST}/view/{d['quickkey']}"
                if self.settings.use_long_link:
                    url += "/" + quote(d.get("filename") or file_name, safe="")
                return UploadResult(url=url, response=resp.text)
            if _monotonic() > deadline:
                raise UploaderError("MediaFire is still processing the upload; gave up waiting", resp.text)
            _sleep(POLL_INTERVAL)
