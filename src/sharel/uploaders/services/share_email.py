"""Email URL sharing (ShareX EmailSharingService).

Two modes:

* **SMTP** (when ``smtp_server`` and ``from_email`` are set): the URL is mailed directly with
  :mod:`smtplib`, like ShareX's ``Email.Send``. With ``automatic_send`` and ``automatic_send_to`` it goes
  there without asking (ShareX's automatic send); otherwise the user is asked for the recipient,
  pre-filled with the last one when ``remember_last_to`` is on (ShareX's email window). ``share_url``
  returns ``""`` because the message was already sent.
* **mailto:** (no SMTP account configured): returns a ``mailto:`` link with the subject and the body
  containing the URL, which the app opens in the desktop's mail client.

``body`` is a template where ``{url}`` is replaced by the shared URL (ShareX sends just the URL).
``smtp_encryption``: STARTTLS (ShareX's ``EnableSsl`` on port 587), SSL/TLS (implicit TLS, port 465)
or None (plain SMTP, e.g. a local relay).
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import quote

from ..base import UploadCancelled, UploaderError, URLSharer
from ..registry import register, setting

ENCRYPTIONS = ["STARTTLS", "SSL/TLS", "None"]


@dataclass
class EmailSettings:
    smtp_server: str = setting("", label="SMTP server", help="Empty: open a mailto: link in your mail app instead")
    smtp_port: int = setting(587, label="SMTP port")
    smtp_encryption: str = setting("STARTTLS", label="Encryption", choices=ENCRYPTIONS)
    from_email: str = setting("", label="From (email address / login)")
    password: str = setting("", label="Password", secret=True, help="Empty: send without logging in")
    remember_last_to: bool = setting(True, label="Remember last recipient")
    last_to: str = setting("", label="Last recipient", hidden=True)
    default_subject: str = setting("Sending email from ShareL", label="Subject")
    body: str = setting("{url}", label="Body", multiline=True, help="{url} is replaced by the shared URL")
    automatic_send: bool = setting(False, label="Send automatically (no prompt)")
    automatic_send_to: str = setting("", label="Automatically send to")


def mailto_url(to: str, subject: str, body: str) -> str:
    """A RFC 6068 mailto: URL (spaces as %20, not '+', which mail clients would show literally)."""
    query = f"subject={quote(subject, safe='')}&body={quote(body, safe='')}"
    return f"mailto:{quote(to, safe='@,')}?{query}"


@register("sharing", "Email", EmailSettings, title="Email")
class EmailSharer(URLSharer):
    settings: EmailSettings

    @property
    def smtp_configured(self) -> bool:
        return bool(self.settings.smtp_server.strip() and self.settings.from_email.strip())

    def validate(self) -> list[str]:
        s = self.settings
        if self.smtp_configured and not 0 < int(s.smtp_port) < 65536:
            return ["SMTP port is invalid"]
        return []

    def _body(self, url: str) -> str:
        template = self.settings.body or "{url}"
        return template.replace("{url}", url) if "{url}" in template else f"{template}\n\n{url}"

    def share_url(self, url: str) -> str:
        s = self.settings
        auto_to = s.automatic_send_to.strip() if s.automatic_send else ""
        remembered = s.last_to if s.remember_last_to else ""
        body = self._body(url)
        if not self.smtp_configured:
            return mailto_url(auto_to or remembered, s.default_subject, body)

        to = auto_to
        if not to:
            answer = self.ctx.prompt_input("Send email to", remembered)
            if answer is None:
                raise UploadCancelled()
            to = answer.strip()
            if not to:
                raise UploaderError("Email: no recipient")
            if s.remember_last_to:
                s.last_to = to
                self.ctx.save_settings()
        self.send(to, s.default_subject, body)
        return ""

    def send(self, to: str, subject: str, body: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.from_email.strip()
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=s.from_email.partition("@")[2] or None)
        msg.set_content(body)
        self.ctx.check_cancelled()
        host, port, timeout = s.smtp_server.strip(), int(s.smtp_port), self.ctx.timeout
        tls = ssl.create_default_context()
        try:
            if s.smtp_encryption == "SSL/TLS":
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=tls)
            else:
                smtp = smtplib.SMTP(host, port, timeout=timeout)
            with smtp:
                smtp.ehlo()
                if s.smtp_encryption == "STARTTLS":
                    smtp.starttls(context=tls)
                    smtp.ehlo()
                if s.password:
                    smtp.login(s.from_email.strip(), s.password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise UploaderError(f"Email: sending through {host}:{port} failed: {e}") from e
