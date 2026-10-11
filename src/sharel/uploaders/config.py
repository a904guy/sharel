"""UploadersConfig.json: per-service settings, custom uploaders, and which custom uploader is selected.

Service settings are kept as plain dicts on disk and turned into the service's settings dataclass
on first use (``settings_for``), so adding a service never needs an edit here.

Secret fields (``setting(secret=True)``) go to the libsecret keyring when one is reachable and
``ApplicationConfig.use_libsecret`` is on; the JSON then holds ``"@secret"`` in their place.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from ..core.serialize import from_jsonable, to_jsonable

SECRET_MARK = "@secret"


@dataclass
class UploadersConfig:
    services: dict[str, Any] = field(default_factory=dict)  # "file/AmazonS3" -> settings
    custom_uploaders: list[Any] = field(default_factory=list)  # CustomUploaderItem
    custom_image_uploader_selected: int = 0
    custom_text_uploader_selected: int = 0
    custom_file_uploader_selected: int = 0
    custom_url_shortener_selected: int = 0
    custom_url_sharing_service_selected: int = 0

    def settings_for(self, key: str, settings_cls: type) -> Any:
        cur = self.services.get(key)
        if isinstance(cur, settings_cls):
            return cur
        obj = from_jsonable(settings_cls, cur if isinstance(cur, dict) else {})
        self.services[key] = obj
        return obj

    def custom_uploader_items(self) -> list[Any]:
        from .custom.model import CustomUploaderItem
        items = []
        for it in self.custom_uploaders:
            items.append(it if isinstance(it, CustomUploaderItem) else from_jsonable(CustomUploaderItem, it))
        self.custom_uploaders = items
        return items

    def selected_custom_index(self, dest_type: str) -> int:
        return {
            "ImageUploader": self.custom_image_uploader_selected,
            "TextUploader": self.custom_text_uploader_selected,
            "FileUploader": self.custom_file_uploader_selected,
            "URLShortener": self.custom_url_shortener_selected,
            "URLSharingService": self.custom_url_sharing_service_selected,
        }.get(dest_type, 0)


# ---- libsecret --------------------------------------------------------------------------

_SCHEMA = None


def _secret():
    global _SCHEMA
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    if _SCHEMA is None:
        _SCHEMA = Secret.Schema.new("app.sharel.ShareL.Uploader", Secret.SchemaFlags.NONE,
                                    {"service": Secret.SchemaAttributeType.STRING,
                                     "field": Secret.SchemaAttributeType.STRING})
    return Secret, _SCHEMA


def _secret_fields(obj: Any, prefix: str = ""):
    """(path, field-name, owner) for every secret field, recursing into nested dataclasses."""
    if not dataclasses.is_dataclass(obj):
        return
    for f in dataclasses.fields(obj):
        val = getattr(obj, f.name)
        if f.metadata.get("secret") and isinstance(val, str):
            yield f"{prefix}{f.name}", f.name, obj
        elif dataclasses.is_dataclass(val):
            yield from _secret_fields(val, f"{prefix}{f.name}.")
        elif isinstance(val, list):  # e.g. the FTP account list
            for i, item in enumerate(val):
                yield from _secret_fields(item, f"{prefix}{f.name}.{i}.")


def store_secrets(cfg: UploadersConfig) -> bool:
    """Move secret values into the keyring. Returns False when no keyring is available."""
    Secret, schema = _secret()
    if Secret is None:
        return False
    try:
        for key, obj in cfg.services.items():
            for path, name, owner in _secret_fields(obj):
                val = getattr(owner, name)
                if val and val != SECRET_MARK:
                    Secret.password_store_sync(schema, {"service": key, "field": path}, Secret.COLLECTION_DEFAULT,
                                               f"ShareL {key} {path}", val, None)
    except Exception:
        return False
    return True


def jsonable_with_secrets_hidden(cfg: UploadersConfig, keyring_ok: bool) -> dict:
    data = to_jsonable(cfg)
    if not keyring_ok:
        return data
    for key, obj in cfg.services.items():
        for path, _name, owner in _secret_fields(obj):
            if getattr(owner, _name):
                node = data["services"][key]
                *parents, leaf = path.split(".")
                for p in parents:
                    node = node[int(p)] if isinstance(node, list) else node[p]
                node[leaf] = SECRET_MARK
    return data


def resolve_secrets(cfg: UploadersConfig, key: str, obj: Any) -> None:
    """Fill ``@secret`` placeholders of one service's settings from the keyring."""
    needs = [(p, n, o) for p, n, o in _secret_fields(obj) if getattr(o, n) == SECRET_MARK]
    if not needs:
        return
    Secret, schema = _secret()
    for path, name, owner in needs:
        val = ""
        if Secret is not None:
            try:
                val = Secret.password_lookup_sync(schema, {"service": key, "field": path}, None) or ""
            except Exception:
                val = ""
        setattr(owner, name, val)
