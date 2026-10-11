"""Helpers shared by the URL shortener and sharing service modules (registers nothing itself)."""
from __future__ import annotations

import json
from typing import Any

import requests

from ..base import UploadContext, UploaderError, UploadResult

# Fields APIs commonly use for a human-readable error, in the order we prefer them
_ERROR_KEYS = ("errormessage", "message", "error", "errors", "description", "error_description")


def error_text(data: Any) -> str:
    """The most useful error string in a decoded JSON error body (or "")."""
    if isinstance(data, dict):
        for key in _ERROR_KEYS:
            val = data.get(key)
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val if v)
            if isinstance(val, dict):
                val = "; ".join(f"{k}: {v}" for k, v in val.items())
            if val:
                return str(val)
    return ""


def api_json(resp: requests.Response, what: str) -> Any:
    """Decode a JSON API response; on a non-2xx status raise UploaderError with the API's own message."""
    text = resp.text
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    if not 200 <= resp.status_code < 300:
        msg = error_text(data) or text[:300].strip() or resp.reason
        raise UploaderError(f"{what} failed: HTTP {resp.status_code}: {msg}", text)
    if data is None:
        raise UploaderError(f"{what}: response is not JSON: {text[:300]}", text)
    return data


def plain_text(resp: requests.Response, what: str) -> str:
    """A plain-text API body (stripped); raises on a non-2xx status."""
    text = resp.text
    if not 200 <= resp.status_code < 300:
        raise UploaderError(f"{what} failed: HTTP {resp.status_code} {resp.reason}: {text[:300].strip()}", text)
    return text.strip()


def short_result(long_url: str, short_url: str, response: str, what: str) -> UploadResult:
    """The UploadResult for a shortened link; raises when the service gave no short URL.

    ``url`` keeps the original long URL (as ShareX does) and ``shortened_url`` the short link, so the
    dispatcher's "fall back to url" can never present the long URL as if it had been shortened.
    """
    short_url = (short_url or "").strip()
    if not short_url.lower().startswith(("http://", "https://")):
        raise UploaderError(f"{what} returned no short URL: {response[:300]}", response)
    return UploadResult(url=long_url, shortened_url=short_url, response=response)


def custom_value(ctx: UploadContext, title: str, value: str, ask: bool) -> str:
    """A custom alias/keyword: the configured value, or (when ``ask``) what the user types, defaulting to it.

    Cancelling the prompt keeps the configured value.
    """
    if ask:
        answer = ctx.prompt_input(title, value)
        if answer is not None:
            value = answer
    return (value or "").strip()
