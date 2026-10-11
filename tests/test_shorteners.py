"""URL shorteners: request shape (method, URL, query/body, auth) and response parsing, all mocked."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info

LONG = "https://example.com/some path/?a=1&b=two#frag"


def make(name, ctx=None, **kw):
    info = registry.get("shortener", name)
    assert info is not None and info.available, name
    settings = info.settings_cls(**kw) if info.settings_cls else None
    return info.cls(settings, ctx or UploadContext())


def query(call) -> dict[str, list[str]]:
    return parse_qs(urlparse(call.request.url).query)


def body_json(call) -> dict:
    return json.loads(call.request.body)


# ---- bit.ly ---------------------------------------------------------------------------------

@responses.activate
def test_bitly_generic_token_shortens_with_domain_and_group():
    responses.post("https://api-ssl.bitly.com/v4/shorten", status=201, json={
        "created_at": "2026-10-11T10:00:00+0000", "id": "bit.ly/4abcDEF", "link": "https://bit.ly/4abcDEF",
        "custom_bitlinks": [], "long_url": LONG, "archived": False, "tags": [], "deeplinks": [],
        "references": {"group": "https://api-ssl.bitly.com/v4/groups/Bk1"}})
    up = make("BITLY", access_token="GENERIC", domain="j.mp", group_guid="Bk1")
    assert up.validate() == []
    res = up.shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.method == "POST"
    assert call.request.headers["Authorization"] == "Bearer GENERIC"
    assert body_json(call) == {"long_url": LONG, "domain": "j.mp", "group_guid": "Bk1"}
    assert res.shortened_url == "https://bit.ly/4abcDEF" and res.url == LONG and res.is_success


@responses.activate
def test_bitly_oauth_token_used_and_domain_omitted_when_empty():
    responses.post("https://api-ssl.bitly.com/v4/shorten", json={"link": "https://bit.ly/x1"})
    up = make("BITLY", oauth2=OAuth2Info(access_token="OAUTHTOKEN"))
    assert up.shorten_url(LONG).shortened_url == "https://bit.ly/x1"
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer OAUTHTOKEN"
    assert body_json(call) == {"long_url": LONG}


@responses.activate
def test_bitly_error_message_surfaces():
    responses.post("https://api-ssl.bitly.com/v4/shorten", status=400, json={
        "message": "INVALID_ARG_LONG_URL", "resource": "bitlinks", "description": "The value provided is invalid.",
        "errors": [{"field": "long_url", "error_code": "invalid"}]})
    with pytest.raises(UploaderError, match="INVALID_ARG_LONG_URL"):
        make("BITLY", access_token="T").shorten_url("nope")


def test_bitly_validate_requires_credentials():
    assert make("BITLY").validate()


def test_bitly_authorize_runs_loopback_oauth_and_saves():
    from sharel.uploaders.services import short_bitly
    seen = {}

    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q, base=url.split("?")[0])

        def hit():
            time.sleep(0.1)
            requests.get(q["redirect_uri"], params={"code": "C0DE", "state": q["state"]}, timeout=5)
        threading.Thread(target=hit).start()

    saved = []
    settings = short_bitly.BitlySettings(client_id="cid", client_secret="csec")
    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://api-ssl.bitly.com/oauth/access_token", json={"access_token": "BT", "login": "me"})
        ctx = UploadContext(open_url=fake_browser, save_settings=lambda: saved.append(1))
        short_bitly.authorize(settings, ctx)
        token_call = next(c for c in rs.calls if "access_token" in c.request.url)
        form = parse_qs(token_call.request.body)
        assert token_call.request.headers["Accept"] == "application/json"
    assert seen["base"] == "https://bitly.com/oauth/authorize" and seen["client_id"] == "cid"
    assert "code_challenge" not in seen
    assert form["code"] == ["C0DE"] and form["client_secret"] == ["csec"] and form["client_id"] == ["cid"]
    assert settings.oauth2.access_token == "BT" and saved == [1]


# ---- is.gd / v.gd ---------------------------------------------------------------------------

@responses.activate
@pytest.mark.parametrize("name,host", [("ISGD", "is.gd"), ("VGD", "v.gd")])
def test_isgd_vgd_json_create(name, host):
    responses.get(f"https://{host}/create.php", json={"shorturl": f"https://{host}/R709K6"})
    res = make(name, custom_short_url="my_link", log_stats=True).shorten_url(LONG)
    q = query(responses.calls[0])
    assert q == {"format": ["json"], "url": [LONG], "shorturl": ["my_link"], "logstats": ["1"]}
    assert res.shortened_url == f"https://{host}/R709K6"


@responses.activate
def test_isgd_error_code_raises_with_message():
    responses.get("https://is.gd/create.php",
                  json={"errorcode": 2, "errormessage": "Short URL my_link already exists, please try another."})
    with pytest.raises(UploaderError, match=r"already exists.*error 2"):
        make("ISGD", custom_short_url="my_link").shorten_url(LONG)


@responses.activate
def test_isgd_asks_for_custom_short_url():
    responses.get("https://v.gd/create.php", json={"shorturl": "https://v.gd/typed_it"})
    ctx = UploadContext(prompt_input=lambda title, default: "typed_it")
    make("VGD", ctx, ask_custom_short_url=True).shorten_url(LONG)
    assert query(responses.calls[0])["shorturl"] == ["typed_it"]
    assert "logstats" not in query(responses.calls[0])


# ---- TinyURL --------------------------------------------------------------------------------

@responses.activate
def test_tinyurl_api_v1_with_token_alias_domain():
    responses.post("https://api.tinyurl.com/create", json={
        "data": {"domain": "tinyurl.com", "alias": "sharel-test", "deleted": False, "archived": False,
                 "analytics": {"enabled": True, "public": False}, "tags": ["a", "b"],
                 "created_at": "2026-10-11T10:00:00+00:00", "expires_at": None,
                 "tiny_url": "https://tinyurl.com/sharel-test", "url": LONG},
        "code": 0, "errors": []})
    res = make("TINYURL", api_token="TT", alias="sharel-test", tags="a, b", description="d").shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer TT"
    assert body_json(call) == {"url": LONG, "domain": "tinyurl.com", "alias": "sharel-test", "tags": "a,b",
                               "description": "d"}
    assert res.shortened_url == "https://tinyurl.com/sharel-test"


@responses.activate
def test_tinyurl_api_errors_raise():
    responses.post("https://api.tinyurl.com/create", status=422,
                   json={"data": [], "code": 5, "errors": ["Alias is not available."]})
    with pytest.raises(UploaderError, match="Alias is not available"):
        make("TINYURL", api_token="TT", alias="taken").shorten_url(LONG)


@responses.activate
def test_tinyurl_free_fallback_without_token():
    responses.get("https://tinyurl.com/api-create.php", body="https://tinyurl.com/2abc3de")
    res = make("TINYURL").shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.method == "GET" and query(call) == {"url": [LONG]}
    assert "Authorization" not in call.request.headers
    assert res.shortened_url == "https://tinyurl.com/2abc3de"


@responses.activate
def test_tinyurl_free_fallback_error():
    responses.get("https://tinyurl.com/api-create.php", status=400, body="Error")
    with pytest.raises(UploaderError):
        make("TINYURL").shorten_url(LONG)


# ---- turl.ca / VURL -------------------------------------------------------------------------

@responses.activate
def test_turl_api_v1_links():
    responses.post("https://turl.ca/api/v1/links", status=201, json={
        "id": "aB3dE5g", "short_url": "https://turl.ca/aB3dE5g", "preview_url": "https://turl.ca/aB3dE5g+",
        "destination": "https://example.com/", "trust": "open", "trust_reason": "unverified_host",
        "mode": "redirect", "scan_state": "clean", "resolves": "preview", "expires_at": None})
    up = make("TURL", api_key="pbca_live_xyz")
    assert up.validate() == []
    res = up.shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer pbca_live_xyz"
    assert body_json(call) == {"url": LONG, "mode": "redirect"}
    assert res.shortened_url == "https://turl.ca/aB3dE5g"


@responses.activate
def test_turl_error_and_key_required():
    assert make("TURL").validate()
    responses.post("https://turl.ca/api/v1/links", status=400,
                   json={"error": "shortener_destination", "help_url": "https://turl.ca/docs#errors"})
    with pytest.raises(UploaderError, match="shortener_destination"):
        make("TURL", api_key="k", mode="cite").shorten_url("https://bit.ly/x")
    assert body_json(responses.calls[0])["mode"] == "cite"


@responses.activate
def test_vurl_plain_text_api():
    responses.get("https://vurl.com/api.php", body="https://vurl.com/abc12")
    res = make("VURL").shorten_url(LONG)
    assert query(responses.calls[0]) == {"url": [LONG]}
    assert res.shortened_url == "https://vurl.com/abc12"


@responses.activate
def test_vurl_invalid_url_raises():
    responses.get("https://vurl.com/api.php", body="Invalid URL")
    with pytest.raises(UploaderError, match="Invalid URL"):
        make("VURL").shorten_url("x")


# ---- YOURLS ---------------------------------------------------------------------------------

YOURLS_OK = {"url": {"keyword": "kw", "url": LONG, "title": "Example", "date": "2026-10-11 10:00:00",
                     "ip": "127.0.0.1"}, "status": "success", "message": f"{LONG} added to database",
             "title": "Example", "shorturl": "https://sho.rt/kw", "statusCode": 200}


@responses.activate
def test_yourls_signature_keyword_title():
    responses.post("https://sho.rt/yourls-api.php", json=YOURLS_OK)
    up = make("YOURLS", api_url="https://sho.rt/yourls-api.php", signature="abc123", keyword="kw", title="Example")
    assert up.validate() == []
    res = up.shorten_url(LONG)
    form = parse_qs(responses.calls[0].request.body)
    assert form == {"signature": ["abc123"], "action": ["shorturl"], "url": [LONG], "format": ["json"],
                    "keyword": ["kw"], "title": ["Example"]}
    assert res.shortened_url == "https://sho.rt/kw"


@responses.activate
def test_yourls_username_password_and_existing_url_is_success():
    responses.post("https://sho.rt/yourls-api.php", json={
        "status": "fail", "code": "error:url", "url": {"keyword": "old", "url": LONG},
        "message": f"{LONG} already exists in database", "title": "Example",
        "shorturl": "https://sho.rt/old", "statusCode": 200})
    res = make("YOURLS", api_url="https://sho.rt/yourls-api.php", username="u", password="p").shorten_url(LONG)
    form = parse_qs(responses.calls[0].request.body)
    assert form["username"] == ["u"] and form["password"] == ["p"] and "signature" not in form
    assert res.shortened_url == "https://sho.rt/old"


@responses.activate
def test_yourls_time_limited_signature(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1760000000.5)
    responses.post("https://sho.rt/yourls-api.php", json=YOURLS_OK)
    make("YOURLS", api_url="https://sho.rt/yourls-api.php", signature="tok",
         time_limited_signature=True).shorten_url(LONG)
    form = parse_qs(responses.calls[0].request.body)
    assert form["timestamp"] == ["1760000000"] and form["hash"] == ["sha256"]
    assert form["signature"] == [hashlib.sha256(b"1760000000tok").hexdigest()]


@responses.activate
def test_yourls_keyword_taken_raises():
    responses.post("https://sho.rt/yourls-api.php", json={
        "status": "fail", "code": "error:keyword", "message": "Short URL kw already exists in database or is reserved",
        "statusCode": 200})
    with pytest.raises(UploaderError, match="already exists"):
        make("YOURLS", api_url="https://sho.rt/yourls-api.php", signature="s", keyword="kw").shorten_url(LONG)


def test_yourls_validate_needs_credentials():
    assert make("YOURLS", api_url="https://sho.rt/yourls-api.php").validate()


# ---- Polr -----------------------------------------------------------------------------------

@responses.activate
def test_polr_v2_shorten_with_custom_ending_and_secret():
    responses.get("https://polr.example.com/api/v2/action/shorten",
                  json={"action": "shorten", "result": "https://polr.example.com/mine/4d2f"})
    up = make("Polr", api_hostname="polr.example.com", api_key="K", is_secret=True, custom_ending="mine")
    res = up.shorten_url(LONG)
    assert query(responses.calls[0]) == {"key": ["K"], "url": [LONG], "response_type": ["json"],
                                         "is_secret": ["true"], "custom_ending": ["mine"]}
    assert res.shortened_url == "https://polr.example.com/mine/4d2f"


@responses.activate
def test_polr_full_endpoint_kept_and_error_raises():
    responses.get("https://polr.example.com/api/v2/action/shorten", status=400,
                  json={"error": "custom ending already in use", "status_code": 400, "error_code": "CREATION_ERROR"})
    up = make("Polr", api_hostname="https://polr.example.com/api/v2/action/shorten", api_key="K")
    with pytest.raises(UploaderError, match="custom ending already in use"):
        up.shorten_url(LONG)


@responses.activate
def test_polr_api_v1():
    responses.get("https://polr.example.com/api.php", body="https://polr.example.com/5kq")
    res = make("Polr", api_hostname="https://polr.example.com", api_key="K", use_api_v1=True).shorten_url(LONG)
    assert query(responses.calls[0]) == {"apikey": ["K"], "action": ["shorten"], "url": [LONG]}
    assert res.shortened_url == "https://polr.example.com/5kq"


# ---- Kutt -----------------------------------------------------------------------------------

@responses.activate
def test_kutt_v2_links_all_options():
    responses.post("https://kutt.example.org/api/v2/links", status=201, json={
        "id": "6f1c6a2e-4c0a-4d7e-9a46-0b2d6c1f2e3a", "address": "mine", "banned": False, "password": True,
        "expire_in": "2026-10-11T18:00:00.000Z", "target": LONG, "visit_count": 0, "description": "desc",
        "created_at": "2026-10-11T10:00:00.000Z", "updated_at": "2026-10-11T10:00:00.000Z",
        "link": "https://s.example.org/mine"})
    up = make("Kutt", host="kutt.example.org/", api_key="KEY", password="pw1", reuse=True, domain="s.example.org",
              custom_url="mine", expire_in="8h", description="desc")
    res = up.shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.headers["X-API-KEY"] == "KEY"
    assert body_json(call) == {"target": LONG, "password": "pw1", "reuse": True, "domain": "s.example.org",
                               "customurl": "mine", "expire_in": "8h", "description": "desc"}
    assert res.shortened_url == "https://s.example.org/mine"


@responses.activate
def test_kutt_default_host_minimal_body_and_error():
    responses.post("https://kutt.it/api/v2/links", status=400, json={"error": "Custom URL is already in use."})
    up = make("Kutt", api_key="KEY")
    with pytest.raises(UploaderError, match="already in use"):
        up.shorten_url(LONG)
    assert body_json(responses.calls[0]) == {"target": LONG}
    assert make("Kutt").validate()


# ---- Zero Width Shortener -------------------------------------------------------------------

@responses.activate
def test_zws_post_and_url_field():
    responses.post("https://api.zws.im/", status=201,
                   json={"short": "​‌‍", "url": "https://zws.im/​‌‍"})
    res = make("ZeroWidthShortener").shorten_url(LONG)
    call = responses.calls[0]
    assert call.request.method == "POST" and body_json(call) == {"url": LONG}
    assert "Authorization" not in call.request.headers
    assert res.shortened_url == "https://zws.im/​‌‍"


@responses.activate
def test_zws_custom_instance_token_and_short_only():
    responses.post("https://zws.example.net/api", status=201, json={"short": "​​"})
    res = make("ZeroWidthShortener", request_url="https://zws.example.net/api", token="ZT").shorten_url(LONG)
    assert responses.calls[0].request.headers["Authorization"] == "Bearer ZT"
    assert res.shortened_url == "https://zws.im/​​"


@responses.activate
def test_zws_error():
    responses.post("https://api.zws.im/", status=422, json={"statusCode": 422, "message": "url must be a URL",
                                                            "error": "Unprocessable Entity"})
    with pytest.raises(UploaderError, match="url must be a URL"):
        make("ZeroWidthShortener").shorten_url("x")


# ---- defunct --------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["QRnet", "TwoGP", "FirebaseDynamicLinks"])
def test_defunct_shorteners_registered_with_evidence(name):
    info = registry.get("shortener", name)
    assert info is not None and not info.available
    assert info.defunct.startswith("https://") and info.defunct_reason


def test_no_shortener_returns_long_url_as_short():
    """A 200 without a short link must be an error, never the long URL passed off as shortened."""
    with responses.RequestsMock() as rs:
        rs.post("https://api.zws.im/", status=201, json={})
        with pytest.raises(UploaderError, match="no short URL"):
            make("ZeroWidthShortener").shorten_url(LONG)
