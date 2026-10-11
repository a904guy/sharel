"""Pastie (ShareX TextUploaders/Pastie.cs). pastie.org now runs pastie_express
(https://github.com/joshgoebel/pastie_express): ``POST /pastes/create`` with ``content`` and
``language`` answers with a redirect to ``/p/<key>``; pastes expire after 24 hours. The site only
serves plain HTTP (port 443 is closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from ..base import TextUploader, UploaderError, UploadResult
from ..registry import register, setting

BASE = "http://pastie.org"
LANGUAGES = ["plaintext", "apache", "bash", "cpp", "csharp", "coffeescript", "css", "diff", "elixir", "go", "html",
             "ini", "java", "javascript", "json", "kotlin", "lua", "less", "makefile", "markdown", "nginx",
             "objectivec", "properties", "perl", "python", "php", "rust", "ruby", "sql", "swift", "scss",
             "typescript", "xml", "yaml"]


@dataclass
class PastieSettings:
    language: str = setting("plaintext", label="Language", choices=LANGUAGES)
    raw_url: bool = setting(False, label="Use raw URL", help="/p/<key>/raw instead of the highlighted page.")


@register("text", "Pastie", PastieSettings, title="Pastie", homepage="http://pastie.org")
class PastieUploader(TextUploader):
    settings: PastieSettings

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text.strip():
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        resp = self.ctx.post(f"{BASE}/pastes/create", data={"content": text, "language": s.language or "plaintext"},
                             allow_redirects=False)
        location = resp.headers.get("Location", "")
        if not (resp.is_redirect and "/p/" in location):
            raise UploaderError(f"Pastie upload failed: HTTP {resp.status_code}: {resp.text[:300]}", resp.text)
        url = urljoin(BASE + "/", location)
        if s.raw_url:
            url = url.rstrip("/") + "/raw"
        return UploadResult(url=url, response=resp.text)
