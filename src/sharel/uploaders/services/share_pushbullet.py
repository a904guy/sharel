"""Pushbullet URL sharing (ShareX PushbulletSharingService -> Pushbullet.PushLinkAsync).

``POST https://api.pushbullet.com/v2/pushes`` with ``Access-Token: <key>`` and JSON
``{"type": "link", "title": "ShareL: URL share", "url": <url>, "body": <url>[, "device_iden": ...]}``.
Without ``device_iden`` Pushbullet pushes to all of the account's devices. The device list comes from
``GET /v2/devices`` (:func:`refresh_devices`, for the settings page). Pushbullet does the sharing, so
``share_url`` returns ``""``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import UploadContext, UploaderError, URLSharer
from ..registry import register, setting
from .short_common import api_json

API_URL = "https://api.pushbullet.com/v2"
PUSHES_URL = API_URL + "/pushes"
DEVICES_URL = API_URL + "/devices"
PUSH_TITLE = "ShareL: URL share"


@dataclass
class PushbulletDevice:
    key: str = ""  # the device "iden"
    name: str = ""  # its nickname


@dataclass
class PushbulletSettings:
    user_api_key: str = setting("", label="Access token", secret=True,
                                help="pushbullet.com > Settings > Account > Create access token")
    device_list: list[PushbulletDevice] = setting(default_factory=list, label="Devices", hidden=True)  # noqa: RUF009
    selected_device: int = setting(-1, label="Device", help="Index in the device list; -1 pushes to all devices")


def _headers(settings: PushbulletSettings) -> dict[str, str]:
    key = settings.user_api_key.strip()
    if not key:
        raise UploaderError("Pushbullet: access token is missing")
    return {"Access-Token": key, "Accept": "application/json"}


def refresh_devices(settings: PushbulletSettings, ctx: UploadContext) -> list[PushbulletDevice]:
    """Fetch the account's active, named devices into ``settings.device_list`` (and save)."""
    data = api_json(ctx.get(DEVICES_URL, params={"active": "true"}, headers=_headers(settings)),
                    "Pushbullet device list")
    devices = [PushbulletDevice(d.get("iden", ""), d.get("nickname", ""))
               for d in (data.get("devices") or []) if isinstance(d, dict)
               and d.get("active", True) and d.get("nickname") and d.get("iden")]
    settings.device_list = devices
    if settings.selected_device >= len(devices):
        settings.selected_device = -1
    ctx.save_settings()
    return devices


@register("sharing", "Pushbullet", PushbulletSettings, title="Pushbullet", homepage="https://www.pushbullet.com")
class PushbulletSharer(URLSharer):
    settings: PushbulletSettings

    def validate(self) -> list[str]:
        return [] if self.settings.user_api_key.strip() else ["access token is missing"]

    def share_url(self, url: str) -> str:
        if not url:
            raise UploaderError("Pushbullet: no URL to share")
        body = {"type": "link", "title": PUSH_TITLE, "url": url, "body": url}
        s = self.settings
        if 0 <= s.selected_device < len(s.device_list) and s.device_list[s.selected_device].key:
            body["device_iden"] = s.device_list[s.selected_device].key
        data = api_json(self.ctx.post(PUSHES_URL, json=body, headers=_headers(s)), "Pushbullet")
        if not isinstance(data, dict) or not data.get("iden"):
            raise UploaderError(f"Pushbullet: push was not created: {str(data)[:300]}")
        return ""
