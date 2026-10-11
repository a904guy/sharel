"""OAuth 1.0a (RFC 5849) for destinations that still use it (Flickr): HMAC-SHA1 request signing and
the three-legged flow with a loopback ``oauth_callback`` and the system browser.

    tokens = authorize(spec, consumer_key, consumer_secret, ctx)  # request token -> browser -> access token
    params = sign_request("GET", url, consumer_key, consumer_secret, token, token_secret, params={...})

``sign_request`` returns the full parameter set (the request's own parameters plus the ``oauth_*``
protocol parameters and ``oauth_signature``) so the caller can send them in the query string or a
form body; :func:`authorization_header` builds the ``Authorization: OAuth ...`` header instead.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import secrets
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .base import UploadContext, UploaderError, check_response
from .oauth import DONE_PAGE


@dataclass
class OAuth1Spec:
    request_token_url: str
    authorize_url: str
    access_token_url: str
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    # HTTP method used for the request-token and access-token calls
    token_method: str = "GET"


# ---- signing (RFC 5849 section 3.4) -----------------------------------------------------------

def percent_encode(value: object) -> str:
    """RFC 5849 3.6: UTF-8, then percent-encode everything except the unreserved characters."""
    return quote(str(value).encode("utf-8"), safe="-._~")


def base_string_uri(url: str) -> str:
    """RFC 5849 3.4.1.2: lowercase scheme/host, default ports dropped, no query or fragment."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", "", ""))


def normalize_parameters(params: Iterable[tuple[str, str]]) -> str:
    """RFC 5849 3.4.1.3.2: encode names and values, sort by name then value, join with & and =."""
    encoded = sorted((percent_encode(k), percent_encode(v)) for k, v in params)
    return "&".join(f"{k}={v}" for k, v in encoded)


def signature_base_string(method: str, url: str, params: Iterable[tuple[str, str]]) -> str:
    """RFC 5849 3.4.1: METHOD&uri&params, where params also include the URL's own query."""
    query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    all_params = [(k, v) for k, v in [*query, *params] if k not in ("oauth_signature", "realm")]
    return "&".join((method.upper(), percent_encode(base_string_uri(url)),
                     percent_encode(normalize_parameters(all_params))))


def hmac_sha1_signature(base_string: str, consumer_secret: str, token_secret: str = "") -> str:
    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _pairs(params: Mapping[str, object] | Iterable[tuple[str, object]] | None) -> list[tuple[str, str]]:
    if params is None:
        return []
    items = params.items() if isinstance(params, Mapping) else params
    return [(str(k), "" if v is None else str(v)) for k, v in items]


def sign_request(method: str, url: str, consumer_key: str, consumer_secret: str, token: str = "",
                 token_secret: str = "", params: Mapping[str, object] | Iterable[tuple[str, object]] | None = None,
                 *, oauth_extra: Mapping[str, str] | None = None, nonce: str | None = None,
                 timestamp: str | int | None = None) -> dict[str, str]:
    """The request's parameters plus the signed ``oauth_*`` protocol parameters.

    ``params`` are the query/form parameters that take part in the signature (never a file part).
    ``oauth_extra`` adds protocol parameters such as ``oauth_callback`` or ``oauth_verifier``.
    """
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        oauth["oauth_token"] = token
    if oauth_extra:
        oauth.update(oauth_extra)
    pairs = _pairs(params)
    base = signature_base_string(method, url, [*pairs, *oauth.items()])
    oauth["oauth_signature"] = hmac_sha1_signature(base, consumer_secret, token_secret)
    return {**dict(pairs), **oauth}


def authorization_header(signed: Mapping[str, str], realm: str = "") -> str:
    """``OAuth realm="...", oauth_...="..."`` from the ``oauth_*`` entries of ``signed``."""
    parts = [f'realm="{realm}"'] if realm else []
    parts += [f'{percent_encode(k)}="{percent_encode(v)}"' for k, v in signed.items() if k.startswith("oauth_")]
    return "OAuth " + ", ".join(parts)


# ---- three-legged flow ------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True))
        if "oauth_verifier" in q or "denied" in q or "oauth_problem" in q:
            self.server.result = q  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DONE_PAGE)
            self.server.got.set()  # type: ignore[attr-defined]
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        return


class CallbackServer:
    """Loopback listener on 127.0.0.1 that receives ``oauth_token`` + ``oauth_verifier``."""

    def __init__(self, port: int = 0) -> None:
        self.httpd = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
        self.httpd.result = {}  # type: ignore[attr-defined]
        self.httpd.got = threading.Event()  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="oauth1-loopback")

    @property
    def callback_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def __enter__(self) -> CallbackServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def wait(self, timeout: float, cancel: threading.Event) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.httpd.got.wait(0.2):  # type: ignore[attr-defined]
                return self.httpd.result  # type: ignore[attr-defined]
            if cancel.is_set():
                raise UploaderError("Authorization cancelled")
        raise UploaderError("Authorization timed out")


def _token_call(spec: OAuth1Spec, url: str, consumer_key: str, consumer_secret: str, ctx: UploadContext,
                token: str = "", token_secret: str = "", oauth_extra: dict[str, str] | None = None,
                what: str = "Token request") -> dict[str, str]:
    signed = sign_request(spec.token_method, url, consumer_key, consumer_secret, token, token_secret,
                          oauth_extra=oauth_extra)
    get = spec.token_method.upper() == "GET"
    resp = ctx.get(url, params=signed) if get else ctx.post(url, data=signed)
    text = check_response(resp, what)
    data = dict(parse_qsl(text.strip(), keep_blank_values=True))
    if not data.get("oauth_token") or "oauth_token_secret" not in data:
        raise UploaderError(f"{what} failed: {text[:300]}", text)
    return data


def authorize(spec: OAuth1Spec, consumer_key: str, consumer_secret: str, ctx: UploadContext,
              timeout: float = 300.0, port: int = 0) -> dict[str, str]:
    """Run the OAuth 1.0a flow; returns the access-token response (``oauth_token``,
    ``oauth_token_secret`` and whatever else the service adds, e.g. Flickr's ``user_nsid``)."""
    if not consumer_key or not consumer_secret:
        raise UploaderError("Enter the API key and secret first")
    with CallbackServer(port) as srv:
        temp = _token_call(spec, spec.request_token_url, consumer_key, consumer_secret, ctx,
                           oauth_extra={"oauth_callback": srv.callback_url}, what="Request token")
        if temp.get("oauth_callback_confirmed", "true").lower() != "true":
            raise UploaderError("Request token: the service did not confirm the callback")
        query = {"oauth_token": temp["oauth_token"], **spec.extra_authorize_params}
        sep = "&" if "?" in spec.authorize_url else "?"
        ctx.open_url(spec.authorize_url + sep + urlencode(query))
        result = srv.wait(timeout, ctx.cancel_event)
    if "oauth_verifier" not in result:
        raise UploaderError("Authorization refused")
    if result.get("oauth_token", temp["oauth_token"]) != temp["oauth_token"]:
        raise UploaderError("Authorization failed: token mismatch")
    return _token_call(spec, spec.access_token_url, consumer_key, consumer_secret, ctx,
                       token=temp["oauth_token"], token_secret=temp["oauth_token_secret"],
                       oauth_extra={"oauth_verifier": result["oauth_verifier"]}, what="Access token")
