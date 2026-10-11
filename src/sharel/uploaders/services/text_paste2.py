"""Paste2 (ShareX TextUploaders/Paste2.cs): the paste2.org web form (no JSON API). The new paste's URL
is where the form submission redirects to."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from ..base import TextUploader, UploaderError, UploadResult
from ..registry import register, setting

URL = "https://paste2.org/"
# A selection of paste2.org's Pygments lexer names ("" is plain text); any lexer name it lists works
LANGUAGES = ["", "text", "bash", "c", "cpp", "csharp", "css", "diff", "go", "html", "ini", "java", "javascript",
             "json", "kotlin", "lua", "make", "markdown", "nginx", "perl", "php", "powershell", "python", "ruby",
             "rust", "sql", "swift", "typescript", "xml", "yaml"]


@dataclass
class Paste2Settings:
    text_format: str = setting("", label="Language", choices=LANGUAGES, help="Empty = plain text.")
    description: str = setting("", label="Description")


@register("text", "Paste2", Paste2Settings, title="Paste2", homepage="https://paste2.org")
class Paste2Uploader(TextUploader):
    settings: Paste2Settings

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        lang = "" if s.text_format == "text" else s.text_format
        resp = self.ctx.post(URL, data={"code": text, "lang": lang, "description": s.description, "parent": ""},
                             allow_redirects=False)
        if resp.is_redirect and resp.headers.get("Location"):
            url = urljoin(URL, resp.headers["Location"])
        else:
            raise UploaderError(f"Paste2 upload failed: HTTP {resp.status_code} without a redirect to the paste",
                                resp.text)
        if url.rstrip("/") == URL.rstrip("/"):
            raise UploaderError("Paste2 rejected the paste (redirected back to the form)", resp.text)
        return UploadResult(url=url, response=resp.text)
