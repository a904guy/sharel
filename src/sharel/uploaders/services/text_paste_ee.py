"""Paste.ee (ShareX TextUploaders/Paste_ee.cs) against API v1 (https://pastee.github.io/docs/).

ShareX falls back to its own application key; here the user supplies a key (an application key or a
user key from the paste.ee account API page) since ShareL has none to ship.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import TextUploader, UploaderError, UploadResult, json_of
from ..registry import register, setting

API_URL = "https://api.paste.ee/v1/pastes"
EXPIRATIONS = ["never", "3600", "86400", "604800", "2592000"]


@dataclass
class PasteEeSettings:
    user_key: str = setting("", label="API key", secret=True,
                            help="An application or user key from your paste.ee account (API section).")
    encrypt_paste: bool = setting(False, label="Encrypted paste")
    description: str = setting("", label="Description")
    expiration: str = setting("never", label="Expiration", choices=EXPIRATIONS,
                              help="never, or lifetime in seconds.")
    syntax: str = setting("autodetect", label="Syntax", help="A paste.ee syntax short name, e.g. text, python.")
    use_file_name: bool = setting(True, label="Use the file name as the section name")


@register("text", "Paste_ee", PasteEeSettings, title="Paste.ee", homepage="https://paste.ee")
class PasteEeUploader(TextUploader):
    settings: PasteEeSettings

    def validate(self) -> list[str]:
        return [] if self.settings.user_key else ["API key is missing"]

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        body = {
            "encrypted": s.encrypt_paste,
            "description": s.description,
            "expiration": s.expiration or "never",
            "sections": [{"name": file_name if s.use_file_name else "", "syntax": s.syntax or "autodetect",
                          "contents": text}],
        }
        resp = self.ctx.post(API_URL, json=body, headers={"X-Auth-Token": s.user_key})
        try:
            data = json_of(resp, "Paste.ee upload")
        except UploaderError:
            try:
                errors = resp.json().get("errors")
            except (ValueError, AttributeError):
                raise
            raise UploaderError(f"Paste.ee upload failed: {errors}", resp.text) from None
        if not data.get("link"):
            raise UploaderError(f"Paste.ee upload failed: {data.get('errors') or resp.text[:300]}", resp.text)
        return UploadResult(url=data["link"], response=resp.text)
