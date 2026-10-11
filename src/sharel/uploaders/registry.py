"""The destination registry.

A service module (any module in ``sharel.uploaders.services``) registers its uploader class and
its settings dataclass::

    @dataclass
    class PomfSettings:
        upload_url: str = setting("https://pomf.lain.la/upload.php", label="Upload URL")

    @register("file", "Pomf", PomfSettings, title="Pomf")
    class PomfUploader(FileUploader):
        def upload(self, stream, file_name): ...

A service that no longer exists is registered with :func:`register_defunct` and an evidence URL; it
shows in the destination list as unavailable, with the reason.

Settings fields carry UI metadata (``label``, ``help``, ``secret``, ``choices``, ``multiline``,
``kind``), so Destination settings pages are generated from the dataclass.
"""
from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CATEGORIES = ("image", "text", "file", "shortener", "sharing")
CATEGORY_TITLES = {"image": "Image uploaders", "text": "Text uploaders", "file": "File uploaders",
                   "shortener": "URL shorteners", "sharing": "URL sharing services"}


def setting(default: Any = dataclasses.MISSING, *, label: str = "", help: str = "", secret: bool = False,
            choices: list[str] | None = None, multiline: bool = False, kind: str = "",
            default_factory: Callable[[], Any] | Any = dataclasses.MISSING, hidden: bool = False) -> Any:
    """A settings field with UI metadata. ``kind``: '' (inferred), 'path', 'folder', 'oauth', 'button'."""
    md = {"label": label, "help": help, "secret": secret, "choices": choices, "multiline": multiline,
          "kind": kind, "hidden": hidden}
    if default_factory is not dataclasses.MISSING:
        return field(default_factory=default_factory, metadata=md)
    return field(default=default, metadata=md)


@dataclass
class OAuth2Info:
    """Tokens from an OAuth 2 flow (stored in the service settings)."""
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    scope: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        import time
        return bool(self.access_token) and (self.expires_at == 0 or self.expires_at > time.time() + 30)


@dataclass
class OAuth2Spec:
    """How to authorize a service with the loopback-redirect flow (see oauth.py)."""
    authorize_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    client_id_field: str = "client_id"
    client_secret_field: str = "client_secret"
    token_field: str = "oauth2"
    pkce: bool = True
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    scope_separator: str = " "
    # Some services want the client credentials as HTTP basic auth on the token endpoint
    token_auth_basic: bool = False
    # Fixed loopback port for services that match the registered callback exactly (0 = any free port)
    redirect_port: int = 0


@dataclass
class ServiceInfo:
    category: str
    name: str
    title: str
    settings_cls: type | None
    cls: type | None
    oauth: OAuth2Spec | None = None
    defunct: str = ""  # evidence URL when the service is gone
    defunct_reason: str = ""
    description: str = ""
    homepage: str = ""

    @property
    def key(self) -> str:
        return f"{self.category}/{self.name}"

    @property
    def available(self) -> bool:
        return not self.defunct and self.cls is not None


SERVICES: dict[str, ServiceInfo] = {}


def register(category: str, name: str, settings_cls: type | None = None, *, title: str = "",
             oauth: OAuth2Spec | None = None, description: str = "", homepage: str = ""):
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category}")

    def deco(cls):
        cls.category = category
        cls.service_name = name
        SERVICES[f"{category}/{name}"] = ServiceInfo(category, name, title or name, settings_cls, cls, oauth,
                                                     description=description, homepage=homepage)
        return cls
    return deco


def register_defunct(category: str, name: str, evidence: str, reason: str, title: str = "") -> None:
    SERVICES[f"{category}/{name}"] = ServiceInfo(category, name, title or name, None, None, defunct=evidence,
                                                 defunct_reason=reason)


_loaded = False


def load_all() -> dict[str, ServiceInfo]:
    """Import every service module once (each registers itself)."""
    global _loaded
    if not _loaded:
        from . import services
        for mod in pkgutil.iter_modules(services.__path__):
            importlib.import_module(f"{services.__name__}.{mod.name}")
        _loaded = True
    return SERVICES


def get(category: str, name: str) -> ServiceInfo | None:
    load_all()
    return SERVICES.get(f"{category}/{name}")


def by_category(category: str) -> list[ServiceInfo]:
    load_all()
    return [s for s in SERVICES.values() if s.category == category]


def settings_fields(settings_cls: type) -> list[dataclasses.Field]:
    return [f for f in dataclasses.fields(settings_cls) if not f.metadata.get("hidden")]
