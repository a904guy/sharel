"""Google OAuth 2 and the resumable upload protocol shared by Google Drive and YouTube.

Resumable upload (https://developers.google.com/drive/api/guides/manage-uploads#resumable):

1. ``POST <upload endpoint>?uploadType=resumable`` with the JSON metadata and ``X-Upload-Content-Type`` /
   ``X-Upload-Content-Length`` → 200 with the session URI in ``Location``.
2. ``PUT <session URI>`` chunks (multiples of 256 KiB, except the last) with
   ``Content-Range: bytes <first>-<last>/<total>``; 308 "Resume Incomplete" (its ``Range`` header says how
   much arrived) means send the next part, 200/201 is the finished resource.
3. After a 5xx or a dropped connection, ``PUT`` an empty body with ``Content-Range: bytes */<total>`` to ask
   how much the server has, then resume from there.
"""
from __future__ import annotations

import time
from typing import BinaryIO

import requests

from ..base import UploadCancelled, UploadContext, UploaderError
from ..registry import OAuth2Spec
from ._api_common import ensure_ok, error_message, read_exact

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHUNK_GRANULARITY = 256 * 1024
DEFAULT_CHUNK = 32 * CHUNK_GRANULARITY  # 8 MiB
MAX_RETRIES = 5

CLIENT_HELP = ("Create an OAuth client of type 'Desktop app' in Google Cloud Console → APIs & Services → "
               "Credentials (https://console.cloud.google.com/apis/credentials) and enable the {api} for the "
               "project. Desktop clients accept the loopback redirect on any port.")

_sleep = time.sleep


def google_spec(*scopes: str) -> OAuth2Spec:
    # access_type=offline + prompt=consent: otherwise Google issues no refresh token on re-authorization
    return OAuth2Spec(AUTHORIZE_URL, TOKEN_URL, scopes=list(scopes),
                      extra_authorize_params={"access_type": "offline", "prompt": "consent"})


def _received(resp: requests.Response) -> int:
    """Bytes the server holds after a 308: ``Range: bytes=0-<last>`` (absent = nothing yet)."""
    rng = resp.headers.get("Range", "")
    if rng.startswith("bytes=") and "-" in rng:
        return int(rng.rsplit("-", 1)[1]) + 1
    return 0


def resumable_upload(ctx: UploadContext, init_url: str, auth_headers: dict[str, str], metadata: dict,
                     stream: BinaryIO, size: int, content_type: str,
                     chunk_size: int = DEFAULT_CHUNK) -> requests.Response:
    """Run a resumable upload; returns the final (200/201) response."""
    if chunk_size % CHUNK_GRANULARITY:
        raise ValueError("chunk size must be a multiple of 256 KiB")
    init = ctx.post(init_url, json=metadata, headers={**auth_headers, "X-Upload-Content-Type": content_type,
                                                      "X-Upload-Content-Length": str(size)})
    ensure_ok(init, "Starting the upload")
    session = init.headers.get("Location")
    if not session:
        raise UploaderError("Starting the upload: no upload session URL returned", init.text)
    if size == 0:
        resp = ctx.put(session, data=b"", headers={**auth_headers, "Content-Range": "bytes */0"})
        return ensure_ok(resp, "Upload")

    offset = 0  # bytes the server confirmed
    buf_start, buf = 0, b""  # current chunk held in memory so it can be re-sent
    retries = 0
    ctx.report(0, size)
    while True:
        if offset >= buf_start + len(buf):
            buf_start, buf = offset, read_exact(stream, min(chunk_size, size - offset))
            if not buf:
                raise UploaderError("The file ended before its expected size was read")
        part = buf[offset - buf_start:]
        last = offset + len(part) - 1
        try:
            resp = ctx.put(session, data=part,
                           headers={**auth_headers, "Content-Range": f"bytes {offset}-{last}/{size}"})
        except UploadCancelled:
            raise
        except UploaderError:
            resp = None
        if resp is not None and resp.status_code in (200, 201):
            ctx.report(size, size)
            return resp
        if resp is not None and resp.status_code == 308:
            offset = _received(resp)
            retries = 0
            ctx.report(offset, size)
            continue
        if resp is not None and not (resp.status_code >= 500 or resp.status_code == 429):
            raise UploaderError(error_message(resp, "Upload"), resp.text)
        retries += 1
        if retries > MAX_RETRIES:
            raise UploaderError(error_message(resp, "Upload") if resp is not None else "Upload failed: connection lost")
        _sleep(min(2 ** retries, 30))
        status = ctx.put(session, data=b"", headers={**auth_headers, "Content-Range": f"bytes */{size}"})
        if status.status_code in (200, 201):
            return status
        if status.status_code == 308:
            offset = _received(status)
            if offset < buf_start:  # the server lost data we no longer hold
                raise UploaderError("Upload failed: the server lost part of the file; try again")
        elif status.status_code == 404:
            raise UploaderError("Upload failed: the upload session expired; try again", status.text)
