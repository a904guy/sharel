"""CustomUploaderItem: one custom uploader, field for field ShareX's .sxcu (v13+ syntax)."""
from __future__ import annotations

from dataclasses import dataclass, field

DESTINATION_TYPES = ("ImageUploader", "TextUploader", "FileUploader", "URLShortener", "URLSharingService")
BODY_TYPES = ("None", "MultipartFormData", "FormURLEncoded", "JSON", "XML", "Binary")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass
class CustomUploaderItem:
    version: str = "18.0.0"
    name: str = ""
    destination_type: list[str] = field(default_factory=list)  # subset of DESTINATION_TYPES
    request_method: str = "POST"
    request_url: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: str = "MultipartFormData"
    arguments: dict[str, str] = field(default_factory=dict)
    file_form_name: str = ""
    data: str = ""  # JSON / XML body template
    url: str = ""
    thumbnail_url: str = ""
    deletion_url: str = ""
    error_message: str = ""

    def supports(self, dest_type: str) -> bool:
        return dest_type in self.destination_type

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        from urllib.parse import urlparse
        host = urlparse(self.request_url).hostname
        return host or "Custom uploader"
