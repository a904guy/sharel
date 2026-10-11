import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests
import responses

from sharel.uploaders import oauth
from sharel.uploaders.base import UploadContext
from sharel.uploaders.registry import OAuth2Info, OAuth2Spec

SPEC = OAuth2Spec("https://auth.example/authorize", "https://auth.example/token", scopes=["a", "b"])


@dataclass
class S:
    client_id: str = "cid"
    client_secret: str = "sec"
    oauth2: OAuth2Info = field(default_factory=OAuth2Info)


def test_loopback_flow_exchanges_code_with_pkce():
    seen = {}

    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)

        def hit():
            time.sleep(0.1)
            requests.get(q["redirect_uri"], params={"code": "THECODE", "state": q["state"]}, timeout=5)
        threading.Thread(target=hit).start()

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://auth.example/token", json={"access_token": "AT", "refresh_token": "RT",
                                                    "expires_in": 3600})
        ctx = UploadContext(open_url=fake_browser)
        info = oauth.authorize(SPEC, "cid", "sec", ctx, timeout=10)
        body = parse_qs(rs.calls[0].request.body)
    assert info.access_token == "AT" and info.refresh_token == "RT" and info.is_valid
    assert seen["scope"] == "a b" and seen["code_challenge_method"] == "S256"
    assert body["code"] == ["THECODE"] and body["grant_type"] == ["authorization_code"]
    assert body["code_verifier"][0] and body["client_id"] == ["cid"]
    assert body["redirect_uri"][0] == seen["redirect_uri"]


def test_state_mismatch_rejected():
    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        threading.Thread(target=lambda: requests.get(q["redirect_uri"], params={"code": "x", "state": "evil"},
                                                     timeout=5)).start()

    import pytest
    with pytest.raises(Exception, match="state mismatch"):
        oauth.authorize(SPEC, "cid", "", UploadContext(open_url=fake_browser), timeout=10)


@responses.activate
def test_ensure_token_refreshes_and_saves():
    responses.post("https://auth.example/token", json={"access_token": "NEW", "expires_in": 60})
    s = S(oauth2=OAuth2Info(access_token="OLD", refresh_token="RT", expires_at=time.time() - 5))
    saved = []
    tok = oauth.ensure_token(SPEC, s, UploadContext(save_settings=lambda: saved.append(1)))
    assert tok == "NEW" and s.oauth2.refresh_token == "RT" and saved == [1]
    assert oauth.ensure_token(SPEC, s, UploadContext()) == "NEW"  # still valid: no request
    assert len(responses.calls) == 1
