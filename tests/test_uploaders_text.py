"""Text destinations: request shape and response parsing against mocked APIs (nothing leaves the machine)."""
import base64
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from imgtext_helpers import form, json_body
from sharel.core.enums import TextDestination
from sharel.uploaders import registry
from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.registry import OAuth2Info
from sharel.uploaders.services import (text_gist, text_hastebin, text_onetimesecret, text_paste2, text_paste_ee,
                                       text_pastebin, text_pastie, text_upaste)


def ctx(**kw):
    return UploadContext(**kw)


# ---- Pastebin ----------------------------------------------------------------------------------

@responses.activate
def test_pastebin_login_then_paste_raw_url():
    responses.post(text_pastebin.LOGIN_URL, body="6c6d3fe13b19bbd6e479b705df0a607f")
    responses.post(text_pastebin.POST_URL, body="https://pastebin.com/UIFdu235s")
    saved = []
    s = text_pastebin.PastebinSettings(api_dev_key="DEV", username="jane", password="pw", exposure="Private",
                                       expiration="M6", title="Log", text_format="python", folder_key="fold",
                                       raw_url=True)
    up = text_pastebin.PastebinUploader(s, ctx(save_settings=lambda: saved.append(1)))
    assert up.validate() == []
    res = up.upload_text("print(1)", "a.py")
    login = form(responses.calls[0].request)
    assert login == {"api_dev_key": "DEV", "api_user_name": "jane", "api_user_password": "pw"}
    paste = form(responses.calls[1].request)
    assert paste == {"api_dev_key": "DEV", "api_option": "paste", "api_paste_code": "print(1)",
                     "api_paste_name": "Log", "api_paste_format": "python", "api_paste_private": "2",
                     "api_paste_expire_date": "6M", "api_user_key": "6c6d3fe13b19bbd6e479b705df0a607f",
                     "api_folder_key": "fold"}
    assert res.url == "https://pastebin.com/raw/UIFdu235s"
    assert s.user_key == "6c6d3fe13b19bbd6e479b705df0a607f" and saved


@responses.activate
def test_pastebin_guest_paste_and_errors():
    responses.post(text_pastebin.POST_URL, body="https://pastebin.com/AbCd1234")
    responses.post(text_pastebin.POST_URL, body="Bad API request, invalid api_dev_key")
    s = text_pastebin.PastebinSettings(api_dev_key="DEV")
    up = text_pastebin.PastebinUploader(s, ctx())
    assert up.upload_text("hi", "a.txt").url == "https://pastebin.com/AbCd1234"
    sent = form(responses.calls[0].request)
    assert sent["api_paste_private"] == "1" and sent["api_paste_expire_date"] == "N" and "api_user_key" not in sent
    with pytest.raises(UploaderError, match="invalid api_dev_key"):
        up.upload_text("hi", "a.txt")
    assert text_pastebin.PastebinUploader(text_pastebin.PastebinSettings(), ctx()).validate()


@responses.activate
def test_pastebin_login_failure():
    responses.post(text_pastebin.LOGIN_URL, body="Bad API request, invalid login")
    s = text_pastebin.PastebinSettings(api_dev_key="DEV", username="u", password="bad")
    with pytest.raises(UploaderError, match="invalid login"):
        text_pastebin.authorize(s, ctx())
    assert s.user_key == ""


# ---- Paste2 ------------------------------------------------------------------------------------

@responses.activate
def test_paste2_follows_redirect_location():
    responses.post(text_paste2.URL, status=302, headers={"Location": "/p/Abc123Xy"}, body="")
    res = text_paste2.Paste2Uploader(text_paste2.Paste2Settings(text_format="python", description="d"),
                                     ctx()).upload_text("print(2)", "a.py")
    assert form(responses.calls[0].request) == {"code": "print(2)", "lang": "python", "description": "d",
                                                "parent": ""}
    assert res.url == "https://paste2.org/p/Abc123Xy"
    assert len(responses.calls) == 1  # the redirect target is not fetched


@responses.activate
def test_paste2_without_redirect_fails():
    responses.post(text_paste2.URL, status=200, body="<html>form again</html>")
    with pytest.raises(UploaderError, match="without a redirect"):
        text_paste2.Paste2Uploader(text_paste2.Paste2Settings(), ctx()).upload_text("x", "a.txt")
    assert form(responses.calls[0].request)["lang"] == ""


# ---- Paste.ee ----------------------------------------------------------------------------------

@responses.activate
def test_paste_ee_v1():
    responses.post(text_paste_ee.API_URL, status=201, json={"id": "aB3dE", "link": "https://paste.ee/p/aB3dE"})
    s = text_paste_ee.PasteEeSettings(user_key="uKEY", encrypt_paste=True, description="desc", syntax="python")
    res = text_paste_ee.PasteEeUploader(s, ctx()).upload_text("x = 1", "a.py")
    req = responses.calls[0].request
    assert req.headers["X-Auth-Token"] == "uKEY" and req.headers["Content-Type"] == "application/json"
    assert json_body(req) == {"encrypted": True, "description": "desc", "expiration": "never",
                              "sections": [{"name": "a.py", "syntax": "python", "contents": "x = 1"}]}
    assert res.url == "https://paste.ee/p/aB3dE"


@responses.activate
def test_paste_ee_error():
    responses.post(text_paste_ee.API_URL, status=401, json={"errors": [{"code": 1, "message": "Invalid key."}],
                                                            "success": False})
    with pytest.raises(UploaderError, match="Invalid key"):
        text_paste_ee.PasteEeUploader(text_paste_ee.PasteEeSettings(user_key="bad"), ctx()).upload_text("x", "a")
    assert text_paste_ee.PasteEeUploader(text_paste_ee.PasteEeSettings(), ctx()).validate()


# ---- Gist --------------------------------------------------------------------------------------

GIST_OK = {"url": "https://api.github.com/gists/aa5a315d61ae9438b18d", "id": "aa5a315d61ae9438b18d",
           "html_url": "https://gist.github.com/aa5a315d61ae9438b18d", "public": False, "files": {
               "notes.md": {"filename": "notes.md", "type": "text/markdown", "language": "Markdown",
                            "raw_url": "https://gist.githubusercontent.com/jane/aa5a315d61ae9438b18d/raw/"
                                       "1f2e3d/notes.md", "size": 5}}}


@responses.activate
def test_gist_personal_token():
    responses.post("https://api.github.com/gists", status=201, json=GIST_OK)
    s = text_gist.GistSettings(personal_token="ghp_TOKEN", publish_public=True, description="from ShareL")
    res = text_gist.GistUploader(s, ctx()).upload_text("# hi", "notes.md")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer ghp_TOKEN"
    assert req.headers["Accept"] == "application/vnd.github+json"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert json_body(req) == {"description": "from ShareL", "public": True, "files": {"notes.md": {"content": "# hi"}}}
    assert res.url == "https://gist.github.com/aa5a315d61ae9438b18d"


@responses.activate
def test_gist_oauth_token_raw_url_and_enterprise():
    responses.post("https://ghe.example.com/api/v3/gists", status=201, json=GIST_OK)
    s = text_gist.GistSettings(oauth2=OAuth2Info(access_token="gho_OAUTH"), raw_url=True,
                               custom_url="https://ghe.example.com/api/v3/")
    res = text_gist.GistUploader(s, ctx()).upload_text("# hi", "notes.md")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer gho_OAUTH"
    assert json_body(responses.calls[0].request)["public"] is False
    assert res.url.endswith("/raw/1f2e3d/notes.md")
    spec = text_gist._spec(s)
    assert spec.authorize_url == "https://ghe.example.com/login/oauth/authorize"
    assert spec.token_url == "https://ghe.example.com/login/oauth/access_token"
    assert text_gist.GistUploader(text_gist.GistSettings(), ctx()).validate()


def test_gist_authorize_loopback():
    seen = {}

    def fake_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q, url=url)
        threading.Thread(target=lambda: (time.sleep(0.1), requests.get(
            q["redirect_uri"], params={"code": "GHCODE", "state": q["state"]}, timeout=5))).start()

    s = text_gist.GistSettings(client_id="Iv1.cid", client_secret="csec")
    with responses.RequestsMock() as rs:
        rs.add_passthru("http://127.0.0.1")
        rs.post("https://github.com/login/oauth/access_token",
                json={"access_token": "gho_NEW", "token_type": "bearer", "scope": "gist"})
        text_gist.authorize(s, ctx(open_url=fake_browser))
        token_req = rs.calls[0].request
    assert seen["url"].startswith("https://github.com/login/oauth/authorize?") and seen["scope"] == "gist"
    assert seen["redirect_uri"].startswith("http://127.0.0.1:")
    body = parse_qs(token_req.body)
    assert body["code"] == ["GHCODE"] and body["client_secret"] == ["csec"]
    assert token_req.headers["Accept"] == "application/json"
    assert s.oauth2.access_token == "gho_NEW" and s.oauth2.is_valid


# ---- uPaste ------------------------------------------------------------------------------------

UPASTE_OK = {"paste": {"key": "562eb3", "link": "https://upaste.me/562eb3", "raw": "https://upaste.me/r/562eb3",
                       "download": "https://upaste.me/d/562eb3", "expires": 1756785600, "privacy": "unlisted",
                       "anonymous": 0, "burn": 0, "protected": 0}, "status": "success"}


@responses.activate
def test_upaste_v2():
    responses.post(text_upaste.API_URL, status=201, json=UPASTE_OK)
    s = text_upaste.UpasteSettings(user_key="KEY", privacy="Private", syntax="python", expire=60, burn=True,
                                   paste_password="pw")
    res = text_upaste.UpasteUploader(s, ctx()).upload_text("x", "script.py")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer KEY"
    assert form(req) == {"paste": "x", "privacy": "2", "expire": "60", "name": "script.py", "syntax": "python",
                         "burn": "1", "password": "pw"}
    assert res.url == "https://upaste.me/562eb3"


@responses.activate
def test_upaste_raw_and_error():
    responses.post(text_upaste.API_URL, status=201, json=UPASTE_OK)
    responses.post(text_upaste.API_URL, status=401, json={"errorcode": 0, "error": "invalid_key", "status": "error"})
    up = text_upaste.UpasteUploader(text_upaste.UpasteSettings(user_key="K", raw_url=True), ctx())
    assert up.upload_text("x", "a.txt").url == "https://upaste.me/r/562eb3"
    with pytest.raises(UploaderError, match="invalid_key"):
        up.upload_text("x", "a.txt")
    assert text_upaste.UpasteUploader(text_upaste.UpasteSettings(), ctx()).validate()


# ---- Hastebin ----------------------------------------------------------------------------------

@responses.activate
def test_hastebin_toptal_with_token():
    responses.post("https://hastebin.com/documents", json={"key": "aeiou"})
    s = text_hastebin.HastebinSettings(token="TOK")
    up = text_hastebin.HastebinUploader(s, ctx())
    assert up.validate() == []
    res = up.upload_text("print(1)", "a.py")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer TOK" and req.headers["Content-Type"].startswith("text/plain")
    assert req.body == b"print(1)"
    assert res.url == "https://hastebin.com/share/aeiou.py"
    assert text_hastebin.HastebinUploader(text_hastebin.HastebinSettings(), ctx()).validate()


@responses.activate
def test_hastebin_self_hosted_and_raw():
    responses.post("https://haste.example.org/documents", json={"key": "qwerty"})
    responses.post("https://haste.example.org/documents", json={"key": "qwerty"})
    s = text_hastebin.HastebinSettings(custom_domain="haste.example.org/", syntax_highlighting="sh")
    up = text_hastebin.HastebinUploader(s, ctx())
    assert up.validate() == []
    assert up.upload_text("ls", "notes.txt").url == "https://haste.example.org/qwerty.sh"
    assert "Authorization" not in responses.calls[0].request.headers
    s.raw_url = True
    assert up.upload_text("ls", "notes.txt").url == "https://haste.example.org/raw/qwerty"


# ---- OneTimeSecret -----------------------------------------------------------------------------

OTS_OK = {"user_id": "ur1abc", "shrimp": "", "record": {
    "receipt": {"identifier": "rcpt7xk2", "key": "rcpt7xk2", "shortid": "rcpt7x", "state": "new"},
    "secret": {"identifier": "sec9q8w7", "key": "sec9q8w7", "shortid": "sec9q8", "state": "new"},
    "share_domain": None}, "details": {"kind": "conceal"}}


@responses.activate
def test_onetimesecret_authenticated():
    responses.post("https://eu.onetimesecret.com/api/v2/secret/conceal", json=OTS_OK)
    s = text_onetimesecret.OneTimeSecretSettings(api_username="ur1abc", api_key="APIKEY",
                                                 region="https://eu.onetimesecret.com", ttl="1 hour",
                                                 passphrase="pp", recipient="bob@example.com")
    res = text_onetimesecret.OneTimeSecretUploader(s, ctx()).upload_text("the secret", "a.txt")
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"ur1abc:APIKEY").decode()
    assert json_body(req) == {"secret": {"kind": "conceal", "secret": "the secret", "ttl": 3600, "passphrase": "pp",
                                         "recipient": "bob@example.com"}}
    assert res.url == "https://eu.onetimesecret.com/secret/sec9q8w7"
    assert res.deletion_url == "https://eu.onetimesecret.com/receipt/rcpt7xk2"


@responses.activate
def test_onetimesecret_anonymous_and_error():
    responses.post("https://us.onetimesecret.com/api/v2/guest/secret/conceal", json=OTS_OK)
    responses.post("https://secrets.example.com/api/v2/guest/secret/conceal", status=403,
                   json={"message": "Guest routes are disabled"})
    up = text_onetimesecret.OneTimeSecretUploader(text_onetimesecret.OneTimeSecretSettings(), ctx())
    res = up.upload_text("s", "a.txt")
    req = responses.calls[0].request
    assert "Authorization" not in req.headers
    assert json_body(req) == {"secret": {"kind": "conceal", "secret": "s", "ttl": 604800}}
    assert res.url == "https://us.onetimesecret.com/secret/sec9q8w7"
    self_hosted = text_onetimesecret.OneTimeSecretSettings(custom_url="secrets.example.com")
    with pytest.raises(UploaderError, match="Guest routes are disabled"):
        text_onetimesecret.OneTimeSecretUploader(self_hosted, ctx()).upload_text("s", "a.txt")


# ---- Pastie ------------------------------------------------------------------------------------

@responses.activate
def test_pastie_create_redirect():
    responses.post("http://pastie.org/pastes/create", status=302, headers={"Location": "/p/1a2B3c4D5e6F"},
                   body="Found. Redirecting to /p/1a2B3c4D5e6F")
    responses.post("http://pastie.org/pastes/create", status=302, headers={"Location": "/p/1a2B3c4D5e6F"})
    up = text_pastie.PastieUploader(text_pastie.PastieSettings(language="python"), ctx())
    assert up.upload_text("print(3)", "a.py").url == "http://pastie.org/p/1a2B3c4D5e6F"
    assert form(responses.calls[0].request) == {"content": "print(3)", "language": "python"}
    up.settings.raw_url = True
    assert up.upload_text("x", "a.py").url == "http://pastie.org/p/1a2B3c4D5e6F/raw"


@responses.activate
def test_pastie_empty_content_redirects_home():
    responses.post("http://pastie.org/pastes/create", status=302, headers={"Location": "/"})
    with pytest.raises(UploaderError, match="Pastie upload failed"):
        text_pastie.PastieUploader(text_pastie.PastieSettings(), ctx()).upload_text("x", "a.txt")
    with pytest.raises(UploaderError, match="empty"):
        text_pastie.PastieUploader(text_pastie.PastieSettings(), ctx()).upload_text("   ", "a.txt")


# ---- registry ----------------------------------------------------------------------------------

def test_text_services_registered():
    reg = registry.load_all()
    for name in ("Pastebin", "Paste2", "Paste_ee", "Gist", "Upaste", "Hastebin", "OneTimeSecret", "Pastie",
                 "PrivateBin"):
        assert reg[f"text/{name}"].available and TextDestination[name].value == name
    slexy = reg["text/Slexy"]
    assert not slexy.available and slexy.defunct.startswith("https://forum.infinityfree.com/")
