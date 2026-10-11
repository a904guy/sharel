"""uPaste (ShareX TextUploaders/Upaste.cs) against API v2 (https://upaste.me/faq/api).

Every v2 request needs the account's API key (``Authorization: Bearer``), minted once in the
uPaste account settings.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import TextUploader, UploaderError, UploadResult
from ..registry import register, setting

API_URL = "https://upaste.me/api/v2/paste"
PRIVACY = {"Public": "0", "Unlisted": "1", "Private": "2"}


@dataclass
class UpasteSettings:
    user_key: str = setting("", label="API key", secret=True, help="Mint one in your uPaste account settings.")
    privacy: str = setting("Unlisted", label="Privacy", choices=list(PRIVACY))
    name: str = setting("", label="Title", help="Up to 64 characters; empty uses the file name.")
    syntax: str = setting("", label="Syntax", help="e.g. php, python, text; empty = plain text.")
    expire: int = setting(0, label="Expire (minutes)", help="0 = never.")
    burn: bool = setting(False, label="Burn after reading")
    paste_password: str = setting("", label="Paste password", secret=True)
    anonymous: bool = setting(False, label="Post as Guest")
    raw_url: bool = setting(False, label="Use raw URL")


@register("text", "Upaste", UpasteSettings, title="uPaste", homepage="https://upaste.me")
class UpasteUploader(TextUploader):
    settings: UpasteSettings

    def validate(self) -> list[str]:
        return [] if self.settings.user_key else ["API key is required (uPaste account settings)"]

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        fields = {"paste": text, "privacy": PRIVACY.get(s.privacy, "1"), "expire": str(int(s.expire or 0))}
        name = (s.name or file_name)[:64]
        if name:
            fields["name"] = name
        if s.syntax:
            fields["syntax"] = s.syntax
        if s.burn:
            fields["burn"] = "1"
        if s.paste_password:
            fields["password"] = s.paste_password
        if s.anonymous:
            fields["anonymous"] = "1"
        resp = self.ctx.post(API_URL, data=fields, headers={"Authorization": f"Bearer {s.user_key}"})
        try:
            body = resp.json()
        except ValueError as e:
            raise UploaderError(f"uPaste upload failed: HTTP {resp.status_code}: {resp.text[:300]}", resp.text) from e
        if not isinstance(body, dict) or str(body.get("status", "")).lower() != "success":
            err = body.get("error") if isinstance(body, dict) else body
            raise UploaderError(f"uPaste upload failed (HTTP {resp.status_code}): {err}", resp.text)
        paste = body.get("paste") or {}
        url = paste.get("raw") if s.raw_url else paste.get("link")
        if not url:
            raise UploaderError("uPaste upload failed: no link in response", resp.text)
        return UploadResult(url=url, response=resp.text)
