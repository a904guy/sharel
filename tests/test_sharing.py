"""URL sharing services: exact share URLs, Pushbullet request shape, Email via mailto: and real SMTP."""
from __future__ import annotations

import json
import socket
from email import message_from_bytes
from email.policy import default as default_policy
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import responses

from sharel.uploaders import registry
from sharel.uploaders.base import UploadCancelled, UploadContext, UploaderError

URL = "https://i.example.com/a b/shot.png?x=1&y=ü#top"
ENC = "https%3A%2F%2Fi.example.com%2Fa%20b%2Fshot.png%3Fx%3D1%26y%3D%C3%BC%23top"


def make(name, ctx=None, **kw):
    info = registry.get("sharing", name)
    assert info is not None and info.available, name
    settings = info.settings_cls(**kw) if info.settings_cls else None
    return info.cls(settings, ctx or UploadContext())


# ---- template sharers -----------------------------------------------------------------------

TEMPLATES = {
    "Facebook": f"https://www.facebook.com/sharer/sharer.php?u={ENC}",
    "Reddit": f"https://www.reddit.com/submit?url={ENC}",
    "Pinterest": f"https://www.pinterest.com/pin/create/button/?url={ENC}&media={ENC}",
    "Tumblr": f"https://www.tumblr.com/widgets/share/tool?posttype=link&canonicalUrl={ENC}&content={ENC}",
    "LinkedIn": f"https://www.linkedin.com/sharing/share-offsite/?url={ENC}",
    "VK": f"https://vk.com/share.php?url={ENC}",
    "GoogleImageSearch": f"https://lens.google.com/uploadbyurl?url={ENC}",
    "BingVisualSearch": f"https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl={ENC}",
}


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_template_share_url(name):
    out = make(name).share_url(URL)
    assert out == TEMPLATES[name]
    # the encoded URL round-trips: no '&', '?', '#', ' ' leak into the share page's own query
    for values in parse_qs(urlparse(out).query).values():
        assert all(v in (URL, "link", "sbi") for v in values)


@pytest.mark.parametrize("name", ["StumbleUpon", "Delicious"])
def test_defunct_sharing_registered_with_evidence(name):
    info = registry.get("sharing", name)
    assert info is not None and not info.available
    assert info.defunct.startswith("https://") and info.defunct_reason


# ---- Pushbullet -----------------------------------------------------------------------------

PUSH_REPLY = {"active": True, "iden": "ujpah72o0sjAoRtnM0jc", "created": 1760000000.1, "modified": 1760000000.1,
              "type": "link", "dismissed": False, "direction": "self", "sender_iden": "ujpah72o0",
              "title": "ShareL: URL share", "url": URL, "body": URL}


@responses.activate
def test_pushbullet_pushes_link_to_all_devices():
    responses.post("https://api.pushbullet.com/v2/pushes", json=PUSH_REPLY)
    sharer = make("Pushbullet", user_api_key="o.TOKEN")
    assert sharer.validate() == []
    assert sharer.share_url(URL) == ""
    call = responses.calls[0]
    assert call.request.headers["Access-Token"] == "o.TOKEN"
    assert json.loads(call.request.body) == {"type": "link", "title": "ShareL: URL share", "url": URL, "body": URL}


@responses.activate
def test_pushbullet_device_refresh_and_selected_device():
    from sharel.uploaders.services.share_pushbullet import refresh_devices
    responses.get("https://api.pushbullet.com/v2/devices", json={"devices": [
        {"active": True, "iden": "dev1", "nickname": "Pixel", "type": "android", "pushable": True},
        {"active": True, "iden": "dev2", "nickname": "", "type": "chrome"},
        {"active": True, "iden": "dev3", "nickname": "Laptop", "type": "firefox", "pushable": True}]})
    responses.post("https://api.pushbullet.com/v2/pushes", json=PUSH_REPLY)
    saved = []
    ctx = UploadContext(save_settings=lambda: saved.append(1))
    sharer = make("Pushbullet", ctx, user_api_key="o.TOKEN")
    devices = refresh_devices(sharer.settings, ctx)
    assert [(d.key, d.name) for d in devices] == [("dev1", "Pixel"), ("dev3", "Laptop")] and saved == [1]
    assert responses.calls[0].request.headers["Access-Token"] == "o.TOKEN"
    sharer.settings.selected_device = 1
    sharer.share_url(URL)
    assert json.loads(responses.calls[1].request.body)["device_iden"] == "dev3"


@responses.activate
def test_pushbullet_error_and_missing_key():
    assert make("Pushbullet").validate()
    responses.post("https://api.pushbullet.com/v2/pushes", status=401, json={
        "error": {"code": "invalid_access_token", "type": "invalid_request",
                  "message": "Access token is missing or invalid.", "cat": "(=^‥^=)"},
        "error_code": "invalid_access_token"})
    with pytest.raises(UploaderError, match="Access token is missing or invalid"):
        make("Pushbullet", user_api_key="bad").share_url(URL)


# ---- Email ----------------------------------------------------------------------------------

def test_email_mailto_without_smtp():
    out = make("Email", last_to="friend@example.org", default_subject="Look at this & that").share_url(URL)
    parsed = urlparse(out)
    assert parsed.scheme == "mailto" and parsed.path == "friend@example.org"
    assert out == ("mailto:friend@example.org?subject=Look%20at%20this%20%26%20that&body="
                   + ENC)
    q = parse_qs(parsed.query)
    assert q["body"] == [URL] and q["subject"] == ["Look at this & that"]


def test_email_mailto_body_template_and_no_remember():
    out = make("Email", last_to="x@example.org", remember_last_to=False,
               body="Here: {url}\nbye").share_url(URL)
    assert out.startswith("mailto:?subject=")
    assert unquote(out.split("body=", 1)[1]) == f"Here: {URL}\nbye"


@pytest.fixture
def smtp_server():
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult, LoginPassword

    class Handler:
        def __init__(self):
            self.messages = []
            self.logins = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope)
            return "250 OK"

    def authenticator(server, session, envelope, mechanism, auth_data):
        if isinstance(auth_data, LoginPassword):
            handler.logins.append((auth_data.login.decode(), auth_data.password.decode()))
            ok = auth_data.password == b"s3cret"
            # handled=False: let aiosmtpd send the 235/535 reply itself
            return AuthResult(success=ok, handled=ok)
        return AuthResult(success=False, handled=False)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = Handler()
    ctl = Controller(handler, hostname="127.0.0.1", port=port, authenticator=authenticator,
                     auth_require_tls=False)
    ctl.start()
    try:
        yield handler, port
    finally:
        ctl.stop()


def _smtp_settings(port, **kw):
    base = dict(smtp_server="127.0.0.1", smtp_port=port, smtp_encryption="None", from_email="me@example.com",
                password="s3cret", default_subject="Shared from ShareL")
    base.update(kw)
    return base


def test_email_smtp_prompts_recipient_remembers_and_sends(smtp_server):
    handler, port = smtp_server
    asked = []
    saved = []

    def prompt(title, default):
        asked.append((title, default))
        return "friend@example.org"

    ctx = UploadContext(prompt_input=prompt, save_settings=lambda: saved.append(1))
    sharer = make("Email", ctx, **_smtp_settings(port, last_to="old@example.org"))
    assert sharer.share_url(URL) == ""
    assert asked == [("Send email to", "old@example.org")]
    assert sharer.settings.last_to == "friend@example.org" and saved == [1]
    assert handler.logins == [("me@example.com", "s3cret")]
    env = handler.messages[0]
    assert env.mail_from == "me@example.com" and env.rcpt_tos == ["friend@example.org"]
    msg = message_from_bytes(env.original_content, policy=default_policy)
    assert msg["Subject"] == "Shared from ShareL" and msg["To"] == "friend@example.org"
    assert msg.get_content().strip() == URL


def test_email_smtp_automatic_send_no_prompt_no_login(smtp_server):
    handler, port = smtp_server

    def prompt(title, default):
        raise AssertionError("automatic send must not prompt")

    sharer = make("Email", UploadContext(prompt_input=prompt),
                  **_smtp_settings(port, password="", automatic_send=True, automatic_send_to="auto@example.org"))
    assert sharer.share_url(URL) == ""
    assert handler.logins == []
    assert handler.messages[0].rcpt_tos == ["auto@example.org"]


def test_email_smtp_cancelled_prompt_and_bad_login(smtp_server):
    _handler, port = smtp_server
    with pytest.raises(UploadCancelled):
        make("Email", UploadContext(prompt_input=lambda t, d: None), **_smtp_settings(port)).share_url(URL)
    with pytest.raises(UploaderError, match="sending through"):
        make("Email", UploadContext(prompt_input=lambda t, d: "a@example.org"),
             **_smtp_settings(port, password="wrong")).share_url(URL)
