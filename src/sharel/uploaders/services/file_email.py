"""Email (ShareX Email.cs): send the file as an attachment over SMTP.

STARTTLS (port 587, ShareX's default), implicit SSL/TLS (port 465) or plain SMTP. Unless "automatic send" is on,
the recipient, subject and body are asked for (ShareX shows its email window), and the last recipient is
remembered. There is no URL: the result has ``is_url_expected=False``.
"""
from __future__ import annotations

import contextlib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import BinaryIO

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadCancelled, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._file_helpers import mime_type, parse_pattern, read_all

SECURITY = ["STARTTLS", "SSL/TLS", "None"]


@dataclass
class EmailSettings:
    smtp_server: str = setting("smtp.gmail.com", label="SMTP server")
    smtp_port: int = setting(587, label="SMTP port", help="587 STARTTLS, 465 SSL/TLS, 25 plain")
    security: str = setting("STARTTLS", label="Encryption", choices=SECURITY)
    verify_certificate: bool = setting(True, label="Verify server certificate")
    from_address: str = setting("", label="From", help="e.g. me@gmail.com")
    username: str = setting("", label="Username", help="Empty: the From address")
    password: str = setting("", label="Password", secret=True, help="For Gmail, an app password")
    remember_last_to: bool = setting(True, label="Remember last recipient")
    last_to: str = setting("", label="Last recipient")
    default_subject: str = setting("Sending email from ShareL", label="Default subject",
                                   help="Name-parser patterns allowed")
    default_body: str = setting("Screenshot is attached.", label="Default body", multiline=True)
    automatic_send: bool = setting(False, label="Send automatically (don't ask)")
    automatic_send_to: str = setting("", label="Send automatically to")
    attach_file: bool = setting(True, label="Attach the file")


def _recipients(text: str) -> list[str]:
    return [a.strip() for a in text.replace(";", ",").split(",") if a.strip()]


def _connect(s: EmailSettings, ctx: UploadContext) -> smtplib.SMTP:
    tls = ssl.create_default_context()
    if not s.verify_certificate:
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE
    try:
        if s.security == "SSL/TLS":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_server, s.smtp_port, timeout=ctx.timeout, context=tls)
        else:
            smtp = smtplib.SMTP(s.smtp_server, s.smtp_port, timeout=ctx.timeout)
    except (OSError, smtplib.SMTPException) as e:
        raise UploaderError(f"SMTP connection to {s.smtp_server}:{s.smtp_port} failed: {e}") from e
    try:
        smtp.ehlo()
        if s.security == "STARTTLS":
            smtp.starttls(context=tls)
            smtp.ehlo()
        if s.password:
            smtp.login(s.username or s.from_address, s.password)
    except smtplib.SMTPAuthenticationError as e:
        smtp.close()
        raise UploaderError(f"SMTP login failed: {e.smtp_code} {e.smtp_error.decode(errors='replace')}") from e
    except (OSError, smtplib.SMTPException) as e:
        smtp.close()
        raise UploaderError(f"SMTP session with {s.smtp_server}:{s.smtp_port} failed: {e}") from e
    return smtp


def build_message(s: EmailSettings, to: list[str], subject: str, body: str, data: bytes | None,
                  file_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = s.from_address
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(s.from_address.rpartition("@")[2] or None))
    msg.set_content(body)
    if data is not None:
        maintype, _, subtype = mime_type(file_name).partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=file_name)
    return msg


@register("file", "Email", EmailSettings, title="Email")
class EmailUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        if not s.smtp_server or s.smtp_port <= 0:
            return ["SMTP server and port must be set"]
        if not s.from_address:
            return ["From address must be set"]
        return []

    def _ask(self, title: str, default: str) -> str:
        answer = self.ctx.prompt_input(title, default)
        if answer is None:
            raise UploadCancelled()
        return answer

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        subject = parse_pattern(s.default_subject, NameParserType.Text)
        body = parse_pattern(s.default_body, NameParserType.Text)
        if s.automatic_send and s.automatic_send_to:
            to_text = s.automatic_send_to
        else:
            to_text = self._ask("Email to", s.last_to if s.remember_last_to else "")
            subject = self._ask("Email subject", subject)
            body = self._ask("Email body", body)
            if s.remember_last_to and to_text != s.last_to:
                s.last_to = to_text
                self.ctx.save_settings()
        to = _recipients(to_text)
        if not to:
            raise UploaderError("No email recipient")
        data = read_all(stream) if s.attach_file else None
        msg = build_message(s, to, subject, body, data, file_name)
        self.ctx.check_cancelled()
        smtp = _connect(s, self.ctx)
        try:
            refused = smtp.send_message(msg, from_addr=s.from_address, to_addrs=to)
        except smtplib.SMTPException as e:
            raise UploaderError(f"Sending email failed: {e}") from e
        finally:
            with contextlib.suppress(smtplib.SMTPException, OSError):
                smtp.quit()
        size = len(data or b"")
        self.ctx.report(size, size)
        result = UploadResult(is_url_expected=False, response=f"Sent to {', '.join(to)}")
        if refused:
            result.errors.append("Refused recipients: " + ", ".join(refused))
        return result


def test_connection(settings: EmailSettings, ctx: UploadContext) -> str:
    smtp = _connect(settings, ctx)
    try:
        code, _msg = smtp.noop()
    finally:
        with contextlib.suppress(smtplib.SMTPException, OSError):
            smtp.quit()
    auth = "logged in" if settings.password else "no login"
    return f"Connected to {settings.smtp_server}:{settings.smtp_port} ({settings.security}, {auth}); NOOP {code}"
