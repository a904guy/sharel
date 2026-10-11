"""Pushbullet file push (ShareX Pushbullet.cs, API v2: https://docs.pushbullet.com/).

1. ``POST /v2/upload-request`` ``{"file_name", "file_type"}`` → ``file_name``, ``file_type``, ``file_url``,
   ``upload_url`` (and a deprecated ``data`` dict of S3 form fields, sent along if present).
2. ``POST upload_url`` multipart/form-data with the ``file`` field (204 No Content).
3. ``POST /v2/pushes`` ``{"type": "file", file_name, file_type, file_url, body, device_iden?}`` — without a
   device iden it goes to all of the account's devices.

Auth is the ``Access-Token: <token>`` header. The result URL is the push in the web app
(``https://www.pushbullet.com/pushes?push_iden=<iden>``), as ShareX returns. The URL-sharing variant
(pushing a link) is a separate sharing service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..registry import register, setting
from ._api_common import ensure_ok, json_ok, mime_type, multipart_form, send_body

API = "https://api.pushbullet.com/v2"
PUSHES_WEB_URL = "https://www.pushbullet.com/pushes"


@dataclass
class PushbulletSettings:
    user_api_key: str = setting("", label="Access token", secret=True,
                                help="Create one at https://www.pushbullet.com/#settings/account")
    device_iden: str = setting("", label="Device",
                               help="Device iden to push to; empty pushes to all devices. "
                                    "Use 'Get device list' to fill the list")
    device_list: list[dict] = setting(label="Devices", default_factory=list, hidden=True)  # noqa: RUF009
    body: str = setting("Sent via ShareL", label="Message", help="Text sent with the file")


def _headers(settings: PushbulletSettings) -> dict[str, str]:
    if not settings.user_api_key:
        raise UploaderError("Pushbullet access token is missing")
    return {"Access-Token": settings.user_api_key}


def get_devices(settings: PushbulletSettings, ctx: UploadContext) -> list[dict]:
    """Fetch the account's active, named devices into ``settings.device_list`` ([{"key", "name"}])."""
    js = json_ok(ctx.get(f"{API}/devices", headers=_headers(settings), params={"active": "true"}),
                 "Pushbullet device list")
    devices = [{"key": d["iden"], "name": d["nickname"]} for d in js.get("devices", [])
               if d.get("nickname") and d.get("iden") and d.get("active", True)]
    settings.device_list = devices
    if settings.device_iden and settings.device_iden not in {d["key"] for d in devices}:
        settings.device_iden = ""
    ctx.save_settings()
    return devices


def device_choices(settings: PushbulletSettings) -> list[tuple[str, str]]:
    """(iden, label) pairs for a device picker, "All devices" first."""
    return [("", "All devices")] + [(d["key"], d["name"]) for d in settings.device_list]


@register("file", "Pushbullet", PushbulletSettings, title="Pushbullet", homepage="https://www.pushbullet.com")
class PushbulletUploader(FileUploader):
    def validate(self) -> list[str]:
        return [] if self.settings.user_api_key else ["Pushbullet access token is missing"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        headers = _headers(s)
        req = json_ok(self.ctx.post(f"{API}/upload-request", headers=headers,
                                    json={"file_name": file_name, "file_type": mime_type(file_name)}),
                      "Pushbullet upload request")
        upload_url = req.get("upload_url")
        if not upload_url or not req.get("file_url"):
            raise UploaderError("Pushbullet upload request returned no upload URL", str(req))
        fields = dict(req.get("data") or {})  # deprecated S3 policy fields; empty on the current API
        file_type = req.get("file_type") or mime_type(file_name)
        body, ctype = multipart_form(fields, "file", req.get("file_name") or file_name, stream,
                                     content_type=file_type)
        ensure_ok(send_body(self.ctx, "POST", upload_url, body, ctype), "Pushbullet file upload")
        push: dict[str, str] = {"type": "file", "file_name": req.get("file_name") or file_name,
                                "file_type": file_type, "file_url": req["file_url"]}
        if s.body:
            push["body"] = s.body
        if s.device_iden:
            push["device_iden"] = s.device_iden
        resp = self.ctx.post(f"{API}/pushes", headers=headers, json=push)
        js = json_ok(resp, "Pushbullet push")
        iden = js.get("iden")
        url = f"{PUSHES_WEB_URL}?push_iden={iden}" if iden else req["file_url"]
        return UploadResult(url=url, response=resp.text)

