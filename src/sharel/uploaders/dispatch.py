"""Pick and run the destination for a task (ShareX's WorkerTask.UploadImage/UploadText/UploadFile...)."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..core.config import ApplicationConfig, TaskSettings
from ..core.enums import EDataType
from . import registry
from .base import FileUploader, TextUploader, UploadContext, UploaderError, UploadResult, URLSharer, URLShortener
from .config import UploadersConfig, resolve_secrets

CUSTOM_NAMES = {"CustomImageUploader": "ImageUploader", "CustomTextUploader": "TextUploader",
                "CustomFileUploader": "FileUploader", "CustomURLShortener": "URLShortener",
                "CustomURLSharingService": "URLSharingService"}


@dataclass
class Destination:
    category: str  # image | text | file | shortener | sharing
    name: str  # the enum value, e.g. "Imgur", "CustomImageUploader", "FTP"

    @property
    def title(self) -> str:
        if self.name in CUSTOM_NAMES:
            return "Custom uploader"
        info = registry.get(self.category, self.name)
        return info.title if info else self.name


def destination_for(data_type: EDataType, ts: TaskSettings, file_name: str = "") -> Destination:
    """Which destination a piece of data goes to, honoring "FileUploader" redirection and filters."""
    ext = Path(file_name).suffix.lower().lstrip(".")
    for flt in ts.upload_settings.uploader_filters:
        if not flt.get("enabled", True):
            continue
        patterns = [p.strip().lower() for p in str(flt.get("extensions", "")).replace(",", " ").split() if p.strip()]
        if ext and any(fnmatch.fnmatch(ext, p.lstrip("*.")) or fnmatch.fnmatch(file_name.lower(), p)
                       for p in patterns):
            return Destination(flt.get("category", "file"), flt.get("destination", ""))
    if data_type == EDataType.Image:
        name = ts.image_destination.value
        if name == "FileUploader":
            return Destination("file", ts.image_file_destination.value)
        return Destination("image", name)
    if data_type == EDataType.Text:
        name = ts.text_destination.value
        if name == "FileUploader":
            return Destination("file", ts.text_file_destination.value)
        return Destination("text", name)
    if data_type == EDataType.URL:
        return Destination("shortener", ts.url_shortener_destination.value)
    return Destination("file", ts.file_destination.value)


class DestinationError(UploaderError):
    pass


def create(dest: Destination, cfg: UploadersConfig, ctx: UploadContext, ts: TaskSettings | None = None):
    """An uploader instance for ``dest``, configured from UploadersConfig (and task overrides)."""
    if dest.name in CUSTOM_NAMES:
        from .custom.uploader import CustomUploader
        dest_type = CUSTOM_NAMES[dest.name]
        items = cfg.custom_uploader_items()
        idx = ts.custom_uploader_index if ts is not None and ts.override_custom_uploader else \
            cfg.selected_custom_index(dest_type)
        if not items:
            raise DestinationError("No custom uploader is configured (Destinations > Custom uploader settings).")
        if not 0 <= idx < len(items):
            idx = 0
        item = items[idx]
        return CustomUploader(item, ctx, dest_type)
    info = registry.get(dest.category, dest.name)
    if info is None:
        raise DestinationError(f"Unknown destination {dest.category}/{dest.name}")
    if info.defunct:
        raise DestinationError(f"{info.title} is no longer available: {info.defunct_reason} ({info.defunct})")
    settings = cfg.settings_for(info.key, info.settings_cls) if info.settings_cls else None
    if settings is not None:
        resolve_secrets(cfg, info.key, settings)
    up = info.cls(settings, ctx)
    if ts is not None and ts.override_ftp and hasattr(up, "account_index"):
        up.account_index = ts.ftp_index
    problems = up.validate()
    if problems:
        raise DestinationError(f"{info.title}: " + "; ".join(problems))
    return up


def _with_retries(fn, retries: int, ctx: UploadContext) -> UploadResult:
    last: Exception | None = None
    for _attempt in range(max(1, retries + 1)):
        ctx.check_cancelled()
        try:
            res = fn()
            if res.is_success:
                return res
            last = UploaderError("; ".join(res.errors) or "Upload returned no URL", res.response)
        except UploaderError as e:
            if e.__class__.__name__ == "UploadCancelled":
                raise
            last = e
    assert last is not None
    raise last


def upload_file(stream: BinaryIO, file_name: str, data_type: EDataType, ts: TaskSettings, cfg: UploadersConfig,
                app: ApplicationConfig, ctx: UploadContext) -> tuple[UploadResult, Destination]:
    dest = destination_for(data_type, ts, file_name)
    candidates = [dest]
    if app.use_secondary_uploaders:
        sec = {EDataType.Image: app.secondary_image_uploaders, EDataType.Text: app.secondary_text_uploaders}.get(
            data_type, app.secondary_file_uploaders)
        for key in sec:
            cat, _, name = key.partition("/")
            if name and Destination(cat, name) != dest:
                candidates.append(Destination(cat, name))
    errors = []
    for d in candidates:
        try:
            up = create(d, cfg, ctx, ts)
            if hasattr(up, "data_type"):  # FTP / SharedFolder pick their image/text/file account by data type
                up.data_type = data_type

            def run(up=up):
                stream.seek(0)
                if isinstance(up, TextUploader) or getattr(up, "dest_type", "") == "TextUploader":
                    text = stream.read().decode("utf-8", errors="replace")
                    return up.upload_text(text, file_name)
                if not isinstance(up, FileUploader):
                    raise DestinationError(f"{d.title} cannot upload files")
                return up.upload(stream, file_name)

            return _with_retries(run, app.max_upload_fail_retry, ctx), d
        except UploaderError as e:
            if e.__class__.__name__ == "UploadCancelled":
                raise
            errors.append(f"{d.title}: {e}")
    raise UploaderError("\n".join(errors))


def upload_text(text: str, file_name: str, ts: TaskSettings, cfg: UploadersConfig, app: ApplicationConfig,
                ctx: UploadContext) -> tuple[UploadResult, Destination]:
    import io
    return upload_file(io.BytesIO(text.encode("utf-8")), file_name, EDataType.Text, ts, cfg, app, ctx)


def shorten_url(url: str, ts: TaskSettings, cfg: UploadersConfig, app: ApplicationConfig,
                ctx: UploadContext) -> tuple[UploadResult, Destination]:
    dest = Destination("shortener", ts.url_shortener_destination.value)
    up = create(dest, cfg, ctx, ts)
    if not isinstance(up, URLShortener) and not hasattr(up, "shorten_url"):
        raise DestinationError(f"{dest.title} is not a URL shortener")
    res = _with_retries(lambda: up.shorten_url(url), app.max_upload_fail_retry, ctx)
    if not res.shortened_url:
        res.shortened_url = res.url
    return res, dest


def share_url(url: str, ts: TaskSettings, cfg: UploadersConfig, ctx: UploadContext) -> tuple[str, Destination]:
    dest = Destination("sharing", ts.url_sharing_service_destination.value)
    up = create(dest, cfg, ctx, ts)
    if not isinstance(up, URLSharer) and not hasattr(up, "share_url"):
        raise DestinationError(f"{dest.title} is not a URL sharing service")
    return up.share_url(url), dest
