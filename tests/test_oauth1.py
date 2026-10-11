"""OAuth 1.0a signing (RFC 5849) and the loopback three-legged flow."""
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from sharel.uploaders import oauth1
from sharel.uploaders.base import UploadContext, UploaderError


def test_rfc5849_section_3_4_1_1_base_string():
    # RFC 5849 3.4.1.1: the example request and its signature base string, verbatim
    url = "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b"
    params = [("c2", ""), ("a3", "2 q"), ("oauth_consumer_key", "9djdj82h48djs9d2"),
              ("oauth_token", "kkk9d7dh3k39sjv7"), ("oauth_signature_method", "HMAC-SHA1"),
              ("oauth_timestamp", "137131201"), ("oauth_nonce", "7d8f3e4a")]
    assert oauth1.signature_base_string("POST", url, params) == (
        "POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D"
        "%26c%2540%3D%26c2%3D%26oauth_consumer_key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a"
        "%26oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk9d7dh3k39sjv7")


def test_rfc5849_section_1_2_signatures():
    # Temporary credential request: oauth_signature="74KNZJeDHnMBp0EMJ9ZHt%2FXKycU%3D"
    params = [("oauth_consumer_key", "dpf43f3p2l4k3l03"), ("oauth_signature_method", "HMAC-SHA1"),
              ("oauth_timestamp", "137131200"), ("oauth_nonce", "wIjqoS"),
              ("oauth_callback", "http://printer.example.com/ready")]
    base = oauth1.signature_base_string("POST", "https://photos.example.net/initiate", params)
    assert oauth1.hmac_sha1_signature(base, "kd94hf93k423kf44") == "74KNZJeDHnMBp0EMJ9ZHt/XKycU="
    # Protected resource request: oauth_signature="MdpQcU8iPSUjWoN%2FUDMsK2sui9I%3D"
    params = [("oauth_consumer_key", "dpf43f3p2l4k3l03"), ("oauth_token", "nnch734d00sl2jdk"),
              ("oauth_signature_method", "HMAC-SHA1"), ("oauth_timestamp", "137131202"),
              ("oauth_nonce", "chapoH")]
    base = oauth1.signature_base_string("GET", "http://photos.example.net/photos?file=vacation.jpg&size=original",
                                        params)
    assert oauth1.hmac_sha1_signature(base, "kd94hf93k423kf44", "pfkkdhi9sl3r4s00") == "MdpQcU8iPSUjWoN/UDMsK2sui9I="


def test_sign_request_matches_twitter_documentation_vector():
    # The worked example of X/Twitter's "Creating a signature" guide (with oauth_version=1.0)
    signed = oauth1.sign_request(
        "POST", "https://api.twitter.com/1.1/statuses/update.json", "xvz1evFS4wEEPTGEFPHBog",
        "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw", "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        params={"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true"},
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp=1318622958)
    assert signed["oauth_signature"] == "hCtSmYh+iHYCEqBWrE7C7hYmtUk="
    assert signed["status"].startswith("Hello") and signed["oauth_version"] == "1.0"
    header = oauth1.authorization_header(signed)
    assert header.startswith("OAuth ") and 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in header
    assert "status=" not in header


def test_base_string_uri_normalization():
    assert oauth1.base_string_uri("HTTP://EXAMPLE.COM:80/r%20v/X?id=123") == "http://example.com/r%20v/X"
    assert oauth1.base_string_uri("https://www.example.net:8080/?q=1") == "https://www.example.net:8080/"
    assert oauth1.percent_encode("a b~*'") == "a%20b~%2A%27"


SPEC = oauth1.OAuth1Spec("https://svc.example/request_token", "https://svc.example/authorize",
                         "https://svc.example/access_token", extra_authorize_params={"perms": "write"})


def _verify(req_url_or_params: dict, url: str, method: str, secret: str, token_secret: str = ""):
    params = [(k, v) for k, v in req_url_or_params.items() if k != "oauth_signature"]
    base = oauth1.signature_base_string(method, url, params)
    assert oauth1.hmac_sha1_signature(base, secret, token_secret) == req_url_or_params["oauth_signature"]


def test_three_legged_flow_with_loopback_callback():
    opened = {}

    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        opened.update(q, url=url)

        def hit():
            time.sleep(0.1)
            requests.get(opened["callback"], params={"oauth_token": q["oauth_token"], "oauth_verifier": "VER"},
                         timeout=5)
        threading.Thread(target=hit).start()

    def request_token(req):
        q = {k: v[0] for k, v in parse_qs(urlparse(req.url).query).items()}
        opened["callback"] = q["oauth_callback"]
        return 200, {}, "oauth_callback_confirmed=true&oauth_token=TEMP&oauth_token_secret=TEMPSEC"

    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.add_callback(responses.GET, "https://svc.example/request_token", callback=request_token)
        rs.get("https://svc.example/access_token",
               body="fullname=Jane&oauth_token=ACC&oauth_token_secret=ACCSEC&user_nsid=12%40N01&username=jane")
        tokens = oauth1.authorize(SPEC, "ckey", "csecret", UploadContext(open_url=fake_browser), timeout=10)
        req1 = {k: v[0] for k, v in parse_qs(urlparse(rs.calls[0].request.url).query).items()}
        req2 = {k: v[0] for k, v in parse_qs(urlparse(rs.calls[-1].request.url).query).items()}

    assert tokens == {"fullname": "Jane", "oauth_token": "ACC", "oauth_token_secret": "ACCSEC",
                      "user_nsid": "12@N01", "username": "jane"}
    assert opened["callback"].startswith("http://127.0.0.1:")
    assert opened["oauth_token"] == "TEMP" and opened["perms"] == "write"
    assert opened["url"].startswith("https://svc.example/authorize?")
    assert req1["oauth_consumer_key"] == "ckey" and "oauth_token" not in req1
    _verify(req1, "https://svc.example/request_token", "GET", "csecret")
    assert req2["oauth_token"] == "TEMP" and req2["oauth_verifier"] == "VER"
    _verify(req2, "https://svc.example/access_token", "GET", "csecret", "TEMPSEC")


@responses.activate
def test_request_token_failure_is_reported():
    responses.get("https://svc.example/request_token", status=401, body="oauth_problem=consumer_key_unknown")
    with pytest.raises(UploaderError, match="Request token failed"):
        oauth1.authorize(SPEC, "bad", "bad", UploadContext(open_url=lambda u: None), timeout=1)


def test_authorize_requires_credentials():
    with pytest.raises(UploaderError, match="API key"):
        oauth1.authorize(SPEC, "", "", UploadContext())
