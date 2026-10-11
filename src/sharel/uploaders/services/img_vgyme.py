"""vgy.me (ShareX ImageUploaders/VgymeUploader.cs): POST https://vgy.me/upload (see https://vgy.me/api)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import ImageUploader, UploaderError, UploadResult, json_of
from ..registry import register, setting

UPLOAD_URL = "https://vgy.me/upload"


@dataclass
class VgymeSettings:
    user_key: str = setting("", label="User key", secret=True,
                            help="Optional: generate one in your vgy.me account so uploads go to your account.")
    title: str = setting("", label="Title")
    description: str = setting("", label="Description")


@register("image", "Vgyme", VgymeSettings, title="vgy.me", homepage="https://vgy.me")
class VgymeUploader(ImageUploader):
    settings: VgymeSettings

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        fields = {k: v for k, v in {"userkey": s.user_key, "title": s.title, "description": s.description}.items() if v}
        resp = self.ctx.post(UPLOAD_URL, data=fields, files={"file": (file_name, self.ctx.progress_stream(stream))})
        body = json_of(resp, "vgy.me upload")
        if not isinstance(body, dict) or body.get("error") or not body.get("image"):
            messages = body.get("messages") if isinstance(body, dict) else None
            raise UploaderError(f"vgy.me upload failed: {messages or resp.text[:300]}", resp.text)
        return UploadResult(url=body["image"], deletion_url=body.get("delete", ""), response=resp.text)
