"""Azure Blob Storage (ShareX AzureStorage.cs): Put Blob (BlockBlob) authorized with the account key (SharedKey)
or a SAS token.

``PUT https://<account>.<environment>/<container>/<path>/<file>``. The URL is the blob URL, the custom domain
(CDN / static website) + path, or the account root for the ``$root`` container.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import hmac
from dataclasses import dataclass
from email.utils import format_datetime
from typing import BinaryIO
from urllib.parse import parse_qsl, urlsplit

from ..base import FileUploader, UploadContext, UploaderError, UploadResult, stream_size
from ..registry import register, setting
from ._file_helpers import combine_url, fix_prefix, mime_type, parse_folder, url_encode_path

API_VERSION = "2021-12-02"


@dataclass
class AzureStorageSettings:
    account_name: str = setting("", label="Storage account name")
    account_access_key: str = setting("", label="Access key", secret=True,
                                      help="Storage account > Access keys (SharedKey signing)")
    sas_token: str = setting("", label="SAS token", secret=True,
                             help="Alternative to the access key: a SAS with create/write permission (sv=...&sig=...)")
    container: str = setting("", label="Container", help="$root for the account root")
    environment: str = setting("blob.core.windows.net", label="Environment",
                               help="blob.core.windows.net, blob.core.chinacloudapi.cn, blob.core.usgovcloudapi.net")
    endpoint: str = setting("", label="Custom endpoint",
                            help="Optional, e.g. Azurite http://127.0.0.1:10000/devstoreaccount1")
    custom_domain: str = setting("", label="Custom domain", help="e.g. cdn.example.com (URL only)")
    upload_path: str = setting("", label="Upload path", help="Name-parser patterns allowed, e.g. ShareL/%y/%mo")
    cache_control: str = setting("", label="Cache-Control", help="e.g. public, max-age=31536000")


def string_to_sign(method: str, headers: dict[str, str], account: str, path: str, query: str = "") -> str:
    """The SharedKey string-to-sign for service version 2015-02-21 and later (Content-Length empty when 0)."""
    h = {k.lower(): v for k, v in headers.items()}
    length = h.get("content-length", "")
    if length == "0":
        length = ""
    fields = [method, h.get("content-encoding", ""), h.get("content-language", ""), length, h.get("content-md5", ""),
              h.get("content-type", ""), h.get("date", ""), h.get("if-modified-since", ""), h.get("if-match", ""),
              h.get("if-none-match", ""), h.get("if-unmodified-since", ""), h.get("range", "")]
    canonical_headers = "".join(f"{k}:{' '.join(v.split())}\n" for k, v in sorted(h.items())
                                if k.startswith("x-ms-"))
    resource = f"/{account}{path}"
    params: dict[str, list[str]] = {}
    for k, v in parse_qsl(query, keep_blank_values=True):
        params.setdefault(k.lower(), []).append(v)
    for k in sorted(params):
        resource += f"\n{k}:{','.join(sorted(params[k]))}"
    return "\n".join(fields) + "\n" + canonical_headers + resource


def shared_key(account: str, key_b64: str, to_sign: str) -> str:
    try:
        key = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise UploaderError("Azure access key is not valid base64") from e
    sig = base64.b64encode(hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).digest()).decode()
    return f"SharedKey {account}:{sig}"


@register("file", "AzureStorage", AzureStorageSettings, title="Azure Storage",
          homepage="https://azure.microsoft.com/products/storage/blobs")
class AzureStorageUploader(FileUploader):
    def validate(self) -> list[str]:
        s = self.settings
        problems = []
        if not s.account_name:
            problems.append("account name must not be empty")
        if not s.account_access_key and not s.sas_token:
            problems.append("access key or SAS token must be set")
        if not s.container:
            problems.append("container must not be empty")
        return problems

    def blob_path(self, file_name: str) -> str:
        folder = parse_folder(self.settings.upload_path)
        return url_encode_path(combine_url(folder, file_name))

    def request_url(self, path: str) -> str:
        s = self.settings
        if s.endpoint.strip():
            return combine_url(fix_prefix(s.endpoint.strip().rstrip("/")), s.container, path)
        return f"https://{s.account_name}.{s.environment or 'blob.core.windows.net'}/{s.container}/{path}"

    def result_url(self, path: str) -> str:
        s = self.settings
        if s.custom_domain:
            return fix_prefix(combine_url(s.custom_domain, path))
        if s.container == "$root" and not s.endpoint.strip():
            return f"https://{s.account_name}.{s.environment or 'blob.core.windows.net'}/{path}"
        return self.request_url(path)

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        path = self.blob_path(file_name)
        url = self.request_url(path)
        length = stream_size(stream)
        headers = {
            "Content-Type": mime_type(file_name),
            "Content-Length": str(length),
            "x-ms-date": format_datetime(_dt.datetime.now(_dt.UTC), usegmt=True),
            "x-ms-version": API_VERSION,
            "x-ms-blob-type": "BlockBlob",
        }
        if s.cache_control:
            headers["x-ms-blob-cache-control"] = s.cache_control
        if s.account_access_key:
            to_sign = string_to_sign("PUT", headers, s.account_name, urlsplit(url).path)
            headers["Authorization"] = shared_key(s.account_name, s.account_access_key, to_sign)
        else:
            token = s.sas_token.strip()
            token = token.split("?", 1)[1] if "?" in token else token
            url = f"{url}?{token}"
        resp = self.ctx.put(url, data=self.ctx.progress_stream(stream, length), headers=headers)
        if not 200 <= resp.status_code < 300:
            code = resp.headers.get("x-ms-error-code", "")
            raise UploaderError(f"Azure Storage upload failed: HTTP {resp.status_code} {code} "
                                f"{_message(resp.text)}".strip(), resp.text)
        return UploadResult(url=self.result_url(path))


def _message(text: str) -> str:
    start = text.find("<Message>")
    end = text.find("</Message>")
    return text[start + 9:end].strip() if start >= 0 and end > start else text[:200]


def preview_url(settings: AzureStorageSettings) -> str:
    up = AzureStorageUploader(settings, UploadContext())
    return up.result_url(up.blob_path("example.png"))
