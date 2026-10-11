"""Email destination against a real aiosmtpd server: plain, STARTTLS (required) and implicit TLS, with AUTH."""
from __future__ import annotations

import contextlib
import io
import ssl
from email import policy
from email.parser import BytesParser

import pytest
from _fileproto_servers import free_port, self_signed_cert

from sharel.uploaders.base import UploadCancelled, UploadContext, UploaderError
from sharel.uploaders.services import file_email
from sharel.uploaders.services.file_email import EmailSettings, EmailUploader


@contextlib.contextmanager
def smtpd(tmp_path, mode: str):
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult

    received: list = []
    logins: list = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append(envelope)
            return "250 OK"

    def authenticator(server, session, envelope, mechanism, auth_data):
        ok = auth_data.login == b"mailer" and auth_data.password == b"pw"
        logins.append((mechanism, auth_data.login, ok))
        return AuthResult(success=ok, handled=False)  # handled=True would mean "reply already sent"

    cert, key = self_signed_cert(tmp_path)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(cert), str(key))
    kw: dict = {"authenticator": authenticator}
    ctl_kw: dict = {}
    if mode == "STARTTLS":
        kw.update(tls_context=tls, require_starttls=True, auth_require_tls=True)
    elif mode == "SSL/TLS":
        ctl_kw["ssl_context"] = tls
        kw["auth_require_tls"] = False  # the whole session is TLS; aiosmtpd only tracks STARTTLS
    else:
        kw["auth_require_tls"] = False
    port = free_port()
    ctl = Controller(Handler(), hostname="127.0.0.1", port=port, **ctl_kw, **kw)
    ctl.start()
    try:
        yield port, received, logins, cert
    finally:
        ctl.stop()


def _settings(port, security, cert=None, **kw) -> EmailSettings:
    base = {"smtp_server": "127.0.0.1", "smtp_port": port, "security": security, "from_address": "me@example.org",
            "username": "mailer", "password": "pw", "verify_certificate": False}
    base.update(kw)
    return EmailSettings(**base)


@pytest.mark.parametrize("mode", ["STARTTLS", "SSL/TLS", "None"])
def test_sends_attachment(tmp_path, mode):
    with smtpd(tmp_path, mode) as (port, received, logins, _cert):
        st = _settings(port, mode, automatic_send=True, automatic_send_to="you@example.net; them@example.net",
                       default_subject="Shot %y", default_body="See attached.")
        res = EmailUploader(st, UploadContext()).upload(io.BytesIO(b"\x89PNG data"), "shot.png")
    assert res.is_success and res.url == "" and res.is_url_expected is False
    env = received[0]
    assert env.mail_from == "me@example.org" and env.rcpt_tos == ["you@example.net", "them@example.net"]
    msg = BytesParser(policy=policy.default).parsebytes(env.content)
    import datetime as _dt
    assert msg["Subject"] == f"Shot {_dt.datetime.now().year:04d}"
    assert msg.get_body(("plain",)).get_content().strip() == "See attached."
    att = next(msg.iter_attachments())
    assert att.get_filename() == "shot.png" and att.get_content_type() == "image/png"
    assert att.get_content() == b"\x89PNG data"
    assert logins and logins[-1][1:] == (b"mailer", True)


def test_prompts_and_remembers_recipient(tmp_path):
    asked = []
    saved = []

    def prompt(title, default):
        asked.append((title, default))
        return {"Email to": "friend@example.com", "Email subject": "Hi", "Email body": "Look"}[title]

    with smtpd(tmp_path, "STARTTLS") as (port, received, _l, _c):
        st = _settings(port, "STARTTLS", last_to="old@example.com")
        ctx = UploadContext(prompt_input=prompt, save_settings=lambda: saved.append(True))
        EmailUploader(st, ctx).upload(io.BytesIO(b"t"), "a.txt")
    assert asked[0] == ("Email to", "old@example.com") and st.last_to == "friend@example.com" and saved
    msg = BytesParser(policy=policy.default).parsebytes(received[0].content)
    assert msg["Subject"] == "Hi" and msg["To"] == "friend@example.com"


def test_cancelled_prompt(tmp_path):
    st = _settings(1, "None")
    with pytest.raises(UploadCancelled):
        EmailUploader(st, UploadContext(prompt_input=lambda t, d: None)).upload(io.BytesIO(b"x"), "x.txt")


def test_certificate_verification(tmp_path):
    with smtpd(tmp_path, "STARTTLS") as (port, received, _l, _cert), \
            pytest.raises(UploaderError, match=r"(?i)certificate"):
        EmailUploader(_settings(port, "STARTTLS", verify_certificate=True, automatic_send=True,
                                    automatic_send_to="a@b.c"), UploadContext()).upload(io.BytesIO(b"x"), "x")
    assert not received


def test_bad_login(tmp_path):
    with smtpd(tmp_path, "STARTTLS") as (port, received, logins, _c), \
            pytest.raises(UploaderError, match="login failed"):
        EmailUploader(_settings(port, "STARTTLS", password="wrong", automatic_send=True,
                                    automatic_send_to="a@b.c"), UploadContext(timeout=10)).upload(
                io.BytesIO(b"x"), "x")
    assert logins and logins[0][2] is False and not received


def test_test_connection(tmp_path):
    with smtpd(tmp_path, "SSL/TLS") as (port, _r, logins, _c):
        msg = file_email.test_connection(_settings(port, "SSL/TLS"), UploadContext())
    assert "Connected to 127.0.0.1" in msg and "NOOP 250" in msg and logins[-1][2] is True
