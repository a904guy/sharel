"""OAuth 2 for destinations: loopback redirect + the system browser (RFC 8252), with PKCE.

    info = authorize(spec, client_id, client_secret, ctx)   # opens the browser, waits for the redirect
    token = ensure_token(spec, settings, ctx)                # refreshes when expired, saves settings

The loopback listener binds 127.0.0.1 on a free port; the redirect URI is
``http://127.0.0.1:<port>/``. ``state`` is checked, the code exchanged at ``spec.token_url``.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

from .base import UploadContext, UploaderError, json_of
from .registry import OAuth2Info, OAuth2Spec

DONE_PAGE = (b"<!doctype html><meta charset=utf-8><title>ShareL</title>"
             b"<body style='font-family:sans-serif;text-align:center;margin-top:20vh'>"
             b"<h2>ShareL is authorized.</h2><p>You can close this tab.</p></body>")


class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        if "code" in q or "error" in q:
            self.server.result = {k: v[0] for k, v in q.items()}  # type: ignore[attr-defined]
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


class LoopbackServer:
    def __init__(self, port: int = 0) -> None:
        self.httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
        self.httpd.result = {}  # type: ignore[attr-defined]
        self.httpd.got = threading.Event()  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="oauth-loopback")

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def __enter__(self) -> LoopbackServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def wait(self, timeout: float, cancel: threading.Event) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.httpd.got.wait(0.2):  # type: ignore[attr-defined]
                return self.httpd.result  # type: ignore[attr-defined]
            if cancel.is_set():
                raise UploaderError("Authorization cancelled")
        raise UploaderError("Authorization timed out")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(spec: OAuth2Spec, client_id: str, redirect_uri: str, state: str, challenge: str = "") -> str:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    if spec.scopes:
        params["scope"] = spec.scope_separator.join(spec.scopes)
    if spec.pkce and challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    params.update(spec.extra_authorize_params)
    sep = "&" if "?" in spec.authorize_url else "?"
    return spec.authorize_url + sep + urlencode(params)


def _token_request(spec: OAuth2Spec, client_id: str, client_secret: str, data: dict, ctx: UploadContext) -> dict:
    auth = None
    if spec.token_auth_basic:
        auth = (client_id, client_secret)
    else:
        data["client_id"] = client_id
        if client_secret:
            data["client_secret"] = client_secret
    resp = ctx.post(spec.token_url, data=data, auth=auth, headers={"Accept": "application/json"})
    tok = json_of(resp, "Token request")
    if "access_token" not in tok:
        raise UploaderError(f"Token request failed: {tok}")
    return tok


def _info_from(tok: dict, old: OAuth2Info | None = None) -> OAuth2Info:
    expires_in = tok.get("expires_in")
    extra = {k: str(v) for k, v in tok.items()
             if k not in ("access_token", "refresh_token", "token_type", "expires_in", "scope")}
    return OAuth2Info(access_token=tok["access_token"],
                      refresh_token=tok.get("refresh_token") or (old.refresh_token if old else ""),
                      token_type=tok.get("token_type", "Bearer"),
                      expires_at=time.time() + float(expires_in) if expires_in else 0.0,
                      scope=tok.get("scope", ""), extra=extra)


def authorize(spec: OAuth2Spec, client_id: str, client_secret: str, ctx: UploadContext,
              timeout: float = 300.0) -> OAuth2Info:
    if not client_id:
        raise UploaderError("Enter a client ID first")
    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce_pair() if spec.pkce else ("", "")
    with LoopbackServer(spec.redirect_port) as srv:
        ctx.open_url(authorize_url(spec, client_id, srv.redirect_uri, state, challenge))
        result = srv.wait(timeout, ctx.cancel_event)
        redirect = srv.redirect_uri
    if "error" in result:
        raise UploaderError(f"Authorization refused: {result.get('error_description') or result['error']}")
    if result.get("state") != state:
        raise UploaderError("Authorization failed: state mismatch")
    data = {"grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect}
    if verifier:
        data["code_verifier"] = verifier
    return _info_from(_token_request(spec, client_id, client_secret, data, ctx))


def refresh(spec: OAuth2Spec, client_id: str, client_secret: str, info: OAuth2Info,
            ctx: UploadContext) -> OAuth2Info:
    if not info.refresh_token:
        raise UploaderError("Login expired: authorize again in Destination settings")
    tok = _token_request(spec, client_id, client_secret,
                         {"grant_type": "refresh_token", "refresh_token": info.refresh_token}, ctx)
    return _info_from(tok, info)


def ensure_token(spec: OAuth2Spec, settings, ctx: UploadContext) -> str:
    """A valid access token from ``settings.<spec.token_field>``, refreshing (and saving) if needed."""
    info: OAuth2Info = getattr(settings, spec.token_field)
    if info.is_valid:
        return info.access_token
    if not info.access_token and not info.refresh_token:
        raise UploaderError("Not authorized: open Destination settings and click Authorize")
    new = refresh(spec, getattr(settings, spec.client_id_field, ""), getattr(settings, spec.client_secret_field, ""),
                  info, ctx)
    setattr(settings, spec.token_field, new)
    ctx.save_settings()
    return new.access_token


def bearer(spec: OAuth2Spec, settings, ctx: UploadContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_token(spec, settings, ctx)}"}
