"""OneTimeSecret (ShareX TextUploaders/OneTimeSecret.cs) against API v2
(https://api.onetimesecret.com/doc/api-v2): ``POST /api/v2/secret/conceal`` with HTTP Basic auth
(customer extid or email + API token), or ``/api/v2/guest/secret/conceal`` anonymously.

The API lives on the regional hosts (us./eu./uk./ca./nz.onetimesecret.com) or a self-hosted server;
the apex onetimesecret.com is only the website. The result URL is the one-time secret link; the
receipt page (where the creator can burn the secret) is used as the deletion URL.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import TextUploader, UploaderError, UploadResult
from ..registry import register, setting

REGIONS = ["https://us.onetimesecret.com", "https://eu.onetimesecret.com", "https://uk.onetimesecret.com",
           "https://ca.onetimesecret.com", "https://nz.onetimesecret.com"]
TTLS = {"5 minutes": 300, "30 minutes": 1800, "1 hour": 3600, "4 hours": 14400, "12 hours": 43200,
        "1 day": 86400, "3 days": 259200, "7 days": 604800, "14 days": 1209600, "30 days": 2592000}


@dataclass
class OneTimeSecretSettings:
    api_username: str = setting("", label="Username", help="Customer ID (ur...) or account email; empty = anonymous.")
    api_key: str = setting("", label="API key", secret=True)
    region: str = setting(REGIONS[0], label="Region", choices=REGIONS)
    custom_url: str = setting("", label="Self-hosted URL", help="Overrides the region, e.g. https://secrets.example.com")
    ttl: str = setting("7 days", label="Lifetime", choices=list(TTLS))
    passphrase: str = setting("", label="Passphrase", secret=True, help="The recipient needs it to read the secret.")
    recipient: str = setting("", label="Email recipient", help="Optional; needs an account.")


def base_url(settings: OneTimeSecretSettings) -> str:
    url = (settings.custom_url or settings.region or REGIONS[0]).strip().rstrip("/")
    return url if "://" in url else "https://" + url


@register("text", "OneTimeSecret", OneTimeSecretSettings, title="OneTimeSecret", homepage="https://onetimesecret.com")
class OneTimeSecretUploader(TextUploader):
    settings: OneTimeSecretSettings

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        s = self.settings
        base = base_url(s)
        secret: dict[str, object] = {"kind": "conceal", "secret": text, "ttl": TTLS.get(s.ttl, 604800)}
        if s.passphrase:
            secret["passphrase"] = s.passphrase
        authed = bool(s.api_username and s.api_key)
        if s.recipient and authed:
            secret["recipient"] = s.recipient
        path = "/api/v2/secret/conceal" if authed else "/api/v2/guest/secret/conceal"
        auth = (s.api_username, s.api_key) if authed else None
        resp = self.ctx.post(base + path, json={"secret": secret}, auth=auth, headers={"Accept": "application/json"})
        try:
            body = resp.json()
        except ValueError as e:
            raise UploaderError(f"OneTimeSecret failed: HTTP {resp.status_code}: {resp.text[:300]}", resp.text) from e
        record = body.get("record") if isinstance(body, dict) else None
        if not (200 <= resp.status_code < 300) or not isinstance(record, dict):
            msg = body.get("message") or body.get("error") if isinstance(body, dict) else body
            raise UploaderError(f"OneTimeSecret failed (HTTP {resp.status_code}): {msg}", resp.text)
        sec = record.get("secret") or {}
        key = sec.get("identifier") or sec.get("key")
        if not key:
            raise UploaderError("OneTimeSecret: no secret key in response", resp.text)
        result = UploadResult(url=f"{base}/secret/{key}", response=resp.text)
        receipt = record.get("receipt") or {}
        receipt_key = receipt.get("identifier") or receipt.get("key")
        if receipt_key:
            result.deletion_url = f"{base}/receipt/{receipt_key}"
        return result
