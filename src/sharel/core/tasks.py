"""Tasks: one capture/upload job each, run on worker threads (ShareX's WorkerTask + TaskManager).

A task runs the after-capture flags in ShareX's order, uploads, then runs the after-upload flags.
Everything that needs the desktop (clipboard, windows, dialogs, notifications) goes through a
:class:`TaskHost`, which the GTK app implements and tests replace with a recording fake.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from PIL import Image

from . import imaging
from .config import ConfigStore, TaskSettings
from .enums import AfterCaptureTasks as ACT
from .enums import AfterUploadTasks as AUT
from .enums import EDataType, FileExistAction
from .history import HistoryItem, HistoryManager
from .nameparser import NameParser, NameParserType, WindowInfo

log = logging.getLogger("sharel.tasks")


class TaskStatus(Enum):
    InQueue = "In queue"
    Preparing = "Preparing"
    Working = "Working"
    Stopping = "Stopping"
    Stopped = "Stopped"
    Failed = "Failed"
    Completed = "Completed"


class TaskJob(Enum):
    Job = "Job"  # an image from a capture: after-capture tasks, maybe upload
    DataUpload = "DataUpload"
    FileUpload = "FileUpload"
    TextUpload = "TextUpload"
    ShortenURL = "ShortenURL"
    ShareURL = "ShareURL"
    DownloadUpload = "DownloadUpload"
    MediaFile = "MediaFile"  # a finished screen recording: file-level after-capture tasks, maybe upload


@dataclass
class TaskInfo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    job: TaskJob = TaskJob.Job
    data_type: EDataType = EDataType.Image
    status: TaskStatus = TaskStatus.InQueue
    file_path: str = ""
    file_name: str = ""
    text: str = ""
    url: str = ""  # input URL (ShortenURL / DownloadUpload)
    window: WindowInfo = field(default_factory=WindowInfo)
    region: tuple[int, int, int, int] | None = None
    upload_host: str = ""
    result_url: str = ""
    thumbnail_url: str = ""
    deletion_url: str = ""
    shortened_url: str = ""
    response: str = ""
    errors: list[str] = field(default_factory=list)
    progress: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed: float = 0.0  # bytes/s
    start_time: float = 0.0
    end_time: float = 0.0
    uploaded: bool = False
    ocr_text: str = ""
    qr_text: str = ""
    workflow_name: str = ""

    @property
    def is_image(self) -> bool:
        return self.data_type == EDataType.Image or Path(self.file_path).suffix.lower().lstrip(".") in \
            imaging_exts()

    @property
    def url_result(self) -> str:
        return self.shortened_url or self.result_url

    @property
    def title(self) -> str:
        return self.file_name or self.url or (self.text[:40] if self.text else self.id)


def imaging_exts() -> set[str]:
    return {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}


class TaskHost:
    """UI side effects. The default implementation does nothing interactive (CLI / tests)."""

    def copy_image(self, img: Image.Image) -> None: ...
    def copy_text(self, text: str) -> None: ...
    def copy_files(self, paths: list[str]) -> None: ...

    def quick_task_menu(self, info: TaskInfo, flags: ACT) -> ACT | None:
        return flags

    def after_capture_window(self, info: TaskInfo, img: Image.Image, flags: ACT) -> tuple[ACT, str] | None:
        return flags, info.file_name

    def beautify(self, img: Image.Image, ts: TaskSettings) -> Image.Image | None:
        from ..effects.beautifier import beautify
        return beautify(img, ts.image_settings.beautifier)

    def image_effects(self, img: Image.Image, ts: TaskSettings) -> Image.Image | None:
        return img

    def annotate(self, img: Image.Image, ts: TaskSettings, info: TaskInfo) -> Image.Image | None:
        return img

    def pin(self, img: Image.Image) -> None: ...
    def print_image(self, img: Image.Image, path: str = "") -> None: ...

    def save_dialog(self, suggested: Path) -> Path | None:
        return suggested

    def ask_file_exists(self, path: Path) -> FileExistAction:
        return FileExistAction.UniqueName

    def show_in_folder(self, path: str) -> None: ...
    def analyze_image(self, path: str, img: Image.Image) -> None: ...

    def scan_qr(self, img: Image.Image, info: TaskInfo) -> None:
        from ..tools.qr import decode_image
        info.qr_text = "\n".join(decode_image(img))

    def ocr(self, img: Image.Image, ts: TaskSettings, info: TaskInfo) -> None:
        from ..tools.ocr import ocr_image
        info.ocr_text = ocr_image(img, ts.capture_settings.ocr.language, ts.capture_settings.ocr.scale_factor)

    def before_upload(self, info: TaskInfo, ts: TaskSettings) -> bool:
        return True

    def after_upload_window(self, info: TaskInfo) -> None: ...
    def show_qr(self, text: str) -> None: ...
    def open_url(self, url: str) -> None: ...
    def notify(self, info: TaskInfo, ts: TaskSettings, img: Image.Image | None) -> None: ...
    def play_sound(self, kind: str, ts: TaskSettings) -> None: ...
    def prompt_input(self, title: str, default: str) -> str | None:
        return default

    def prompt_select(self, title: str, options: list[str]) -> str | None:
        return options[0] if options else None

    def show_output(self, title: str, text: str) -> None: ...


class TaskCancelled(Exception):
    pass


def format_result(fmt: str, info: TaskInfo) -> str:
    """ShareX's clipboard/balloon/open-URL content format tokens."""
    p = Path(info.file_path) if info.file_path else None
    values = {
        "$result": info.url_result or info.file_path or info.text,
        "$url": info.result_url, "$shorturl": info.shortened_url, "$thumbnailurl": info.thumbnail_url,
        "$deletionurl": info.deletion_url, "$filepath": info.file_path,
        "$filename": info.file_name, "$filenamenoext": Path(info.file_name).stem if info.file_name else "",
        "$folderpath": str(p.parent) if p else "", "$foldername": p.parent.name if p else "",
        "$uploadtime": f"{max(0.0, info.end_time - info.start_time):.1f}",
        "$n": "\n",
    }
    # longest first so $filenamenoext beats $filename
    pattern = re.compile("|".join(re.escape(k) for k in sorted(values, key=len, reverse=True)))
    return pattern.sub(lambda m: values[m.group(0)], fmt)


class WorkerTask:
    def __init__(self, manager: TaskManager, info: TaskInfo, ts: TaskSettings, *, image: Image.Image | None = None,
                 data: bytes | None = None) -> None:
        self.manager = manager
        self.info = info
        self.ts = ts
        self.image = image
        self.data = data
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.done = threading.Event()

    @property
    def host(self) -> TaskHost:
        return self.manager.host

    @property
    def store(self) -> ConfigStore:
        return self.manager.store

    # -- helpers --

    def _parser(self, kind: NameParserType = NameParserType.FileName) -> NameParser:
        up = self.ts.upload_settings
        app = self.store.app
        w, h = self.image.size if self.image is not None else (0, 0)
        return NameParser(kind, window=self.info.window, image_width=w, image_height=h,
                          auto_increment=app.name_parser_auto_increment_number,
                          max_name_length=self.ts.advanced_settings.names_parser_max_name_length,
                          max_title_length=self.ts.advanced_settings.names_parser_max_title_length,
                          time_zone_offset_hours=up.custom_time_zone_offset if up.use_custom_time_zone else None)

    def _parse_name(self, pattern: str) -> str:
        np = self._parser()
        name = np.parse(pattern)
        if np.used_auto_increment:
            self.store.app.name_parser_auto_increment_number = np.auto_increment
            self.store.save_app()
        return name or uuid.uuid4().hex[:10]

    def _check(self) -> None:
        if self.cancel.is_set():
            raise TaskCancelled()

    def _update(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self.info, k, v)
        self.manager.emit("updated", self.info)

    def upload_context(self):
        from ..uploaders.base import UploadContext
        host = self.host
        started = time.monotonic()

        def progress(done: int, total: int) -> None:
            el = max(1e-3, time.monotonic() - started)
            self.info.bytes_done, self.info.bytes_total = done, total
            self.info.progress = done / total if total else 0.0
            self.info.speed = done / el
            self.manager.emit("progress", self.info)

        return UploadContext(proxies=self.store.app.proxy_settings.requests_proxies(),
                             timeout=self.store.app.request_timeout_seconds, progress=progress, cancel=self.cancel,
                             prompt_input=host.prompt_input, prompt_select=host.prompt_select,
                             show_output=host.show_output, open_url=host.open_url,
                             save_settings=self.store.save_uploaders)

    # -- run --

    def run(self) -> None:
        info = self.info
        info.start_time = time.time()
        self._update(status=TaskStatus.Working)
        img_for_notify = None
        try:
            if info.job == TaskJob.Job:
                img_for_notify = self._do_capture_job()
            elif info.job in (TaskJob.FileUpload, TaskJob.DataUpload):
                self._do_file_upload()
            elif info.job == TaskJob.TextUpload:
                self._do_text_upload()
            elif info.job == TaskJob.ShortenURL:
                self._do_shorten()
            elif info.job == TaskJob.ShareURL:
                self._do_share()
            elif info.job == TaskJob.DownloadUpload:
                self._do_download_upload()
            elif info.job == TaskJob.MediaFile:
                self._do_media_file()
            if info.uploaded or info.result_url:
                self._after_upload()
            info.end_time = time.time()
            self._update(status=TaskStatus.Completed, progress=1.0)
            self._add_history()
            if info.uploaded or info.result_url:
                self.host.play_sound("TaskCompleted", self.ts)
            elif info.job == TaskJob.Job:
                self.host.play_sound("ActionCompleted", self.ts)
            if not self.ts.general_settings.disable_notifications:
                self.host.notify(info, self.ts, img_for_notify)
        except TaskCancelled:
            info.end_time = time.time()
            self._update(status=TaskStatus.Stopped)
        except Exception as e:
            from ..uploaders.base import UploadCancelled
            info.end_time = time.time()
            if isinstance(e, UploadCancelled):
                self._update(status=TaskStatus.Stopped)
            else:
                log.exception("task %s failed", info.id)
                info.errors.append(str(e))
                self._update(status=TaskStatus.Failed)
                self.host.play_sound("Error", self.ts)
                if not self.ts.general_settings.disable_notifications:
                    self.host.notify(info, self.ts, None)
        finally:
            self.done.set()
            self.manager.emit("finished", info)

    # -- capture job --

    def _do_capture_job(self) -> Image.Image | None:
        info, ts, host = self.info, self.ts, self.host
        img = self.image
        if img is None and info.file_path:
            img = imaging.load(info.file_path)
        if img is None:
            raise ValueError("no image")
        flags = ts.after_capture_job
        if not info.file_name:
            pattern = ts.upload_settings.name_format_pattern_active_window if info.window.title else \
                ts.upload_settings.name_format_pattern
            self.image = img
            info.file_name = self._parse_name(pattern)
        if flags & ACT.ShowQuickTaskMenu:
            res = host.quick_task_menu(info, flags)
            if res is None:
                raise TaskCancelled()
            flags = res
        if flags & ACT.ShowAfterCaptureWindow:
            res2 = host.after_capture_window(info, img, flags)
            if res2 is None:
                raise TaskCancelled()
            flags, name = res2
            if name:
                info.file_name = name
        self._check()
        if flags & ACT.BeautifyImage:
            img = host.beautify(img, ts) or img
        if flags & ACT.AddImageEffects:
            if ts.image_settings.show_image_effects_window_after_capture:
                res_img = host.image_effects(img, ts)
                if res_img is None:
                    raise TaskCancelled()
                img = res_img
            else:
                img = self._apply_selected_preset(img)
        if flags & ACT.AnnotateImage:
            res_img = host.annotate(img, ts, info)
            if res_img is None:
                raise TaskCancelled()
            img = res_img
        self.image = img
        self._check()
        if flags & ACT.CopyImageToClipboard:
            host.copy_image(img)
        if flags & ACT.PinToScreen:
            host.pin(img)
        if flags & ACT.SendImageToPrinter:
            host.print_image(img, info.file_path)
        data, fmt = imaging.encode_for_task(img, ts.image_settings)
        ext = imaging.extension(fmt)
        base_name = Path(info.file_name).stem if Path(info.file_name).suffix.lower().lstrip(".") in \
            imaging_exts() else info.file_name
        info.file_name = f"{base_name}.{ext}"
        if flags & ACT.SaveImageToFile:
            path = self._save(data, info.file_name)
            if path:
                info.file_path = str(path)
        if flags & ACT.SaveImageToFileWithDialog:
            folder = self.store.screenshots_folder(ts)
            chosen = host.save_dialog(folder / info.file_name)
            if chosen:
                chosen = Path(chosen)
                chosen.parent.mkdir(parents=True, exist_ok=True)
                chosen.write_bytes(data)
                info.file_path = str(chosen)
                info.file_name = chosen.name
        if flags & ACT.SaveThumbnailImageToFile:
            self._save_thumbnail(img)
        if flags & ACT.PerformActions:
            self._perform_actions(data)
        if flags & ACT.CopyFileToClipboard and info.file_path:
            host.copy_files([info.file_path])
        elif flags & ACT.CopyFilePathToClipboard and info.file_path:
            host.copy_text(info.file_path)
        elif flags & ACT.CopyFolderPathToClipboard and info.file_path:
            host.copy_text(str(Path(info.file_path).parent))
        if flags & ACT.ShowInExplorer and info.file_path:
            host.show_in_folder(info.file_path)
        if flags & ACT.AnalyzeImage:
            host.analyze_image(info.file_path, img)
        if flags & ACT.ScanQRCode:
            host.scan_qr(img, info)
        if flags & ACT.DoOCR:
            host.ocr(img, ts, info)
        self._check()
        if flags & ACT.UploadImageToHost and (not (flags & ACT.ShowBeforeUploadWindow)
                                              or host.before_upload(info, ts)):
            self.data = data
            self._upload_bytes(data, info.file_name, EDataType.Image)
        if flags & ACT.DeleteFile and info.file_path:
            try:
                os.unlink(info.file_path)
            except OSError as e:
                info.errors.append(f"Delete failed: {e}")
            info.file_path = ""
        return img

    def _apply_selected_preset(self, img: Image.Image) -> Image.Image:
        presets = self.ts.image_settings.image_effect_presets
        if not presets:
            return img
        idx = min(max(0, self.ts.image_settings.selected_image_effect_preset), len(presets) - 1)
        from ..effects.base import ImageEffectPreset
        return ImageEffectPreset.from_jsonable(presets[idx]).apply(img)

    def _save(self, data: bytes, file_name: str) -> Path | None:
        folder = self.store.screenshots_folder(self.ts)
        folder.mkdir(parents=True, exist_ok=True)
        path = imaging.resolve_existing(folder / file_name, self.ts.image_settings.file_exist_action,
                                        self.host.ask_file_exists)
        if path is None:
            return None
        path.write_bytes(data)
        self.info.file_name = path.name
        return path

    def _save_thumbnail(self, img: Image.Image) -> None:
        opts = self.ts.image_settings
        if opts.thumbnail_check_size and img.width <= opts.thumbnail_width and img.height <= opts.thumbnail_height:
            return
        thumb = imaging.thumbnail(img, opts.thumbnail_width, opts.thumbnail_height)
        folder = Path(self.info.file_path).parent if self.info.file_path else self.store.screenshots_folder(self.ts)
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(self.info.file_name).stem
        data, fmt = imaging.encode_for_task(thumb, opts)
        path = imaging.unique_path(folder / f"{stem}{opts.thumbnail_name}.{imaging.extension(fmt)}")
        path.write_bytes(data)

    def _perform_actions(self, data: bytes) -> None:
        info = self.info
        if not info.file_path:
            # Actions work on files: write a temporary copy when the image wasn't saved.
            from . import paths
            tmp = paths.cache_dir() / "actions"
            tmp.mkdir(parents=True, exist_ok=True)
            p = tmp / info.file_name
            p.write_bytes(data)
            info.file_path = str(p)
        for prog in self.ts.external_programs:
            if not prog.is_active or not prog.path or not prog.matches(info.file_path):
                continue
            out = run_external_program(prog, info.file_path)
            if out and Path(out).exists():
                info.file_path = out
                info.file_name = Path(out).name

    # -- uploads --

    def _upload_bytes(self, data: bytes, file_name: str, data_type: EDataType) -> None:
        from ..uploaders import dispatch
        ctx = self.upload_context()
        self._update(status=TaskStatus.Working, bytes_total=len(data))
        res, dest = dispatch.upload_file(io.BytesIO(data), file_name, data_type, self.ts, self.store.uploaders,
                                         self.store.app, ctx)
        self._take_result(res, dest.title)

    def _take_result(self, res, host_title: str) -> None:
        info = self.info
        url = res.url
        if url and self.ts.advanced_settings.result_force_https and url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        if url and self.ts.upload_settings.url_regex_replace:
            url = _regex_replace(url, self.ts.upload_settings.url_regex_replace_pattern,
                                 self.ts.upload_settings.url_regex_replace_replacement)
        info.result_url = url
        info.thumbnail_url = res.thumbnail_url
        info.deletion_url = res.deletion_url
        info.shortened_url = res.shortened_url
        info.response = res.response
        info.upload_host = host_title
        info.uploaded = True
        self.manager.emit("updated", info)
        if self.ts.advanced_settings.early_copy_url and info.url_result:
            self.host.copy_text(info.url_result)

    def _do_file_upload(self) -> None:
        info = self.info
        path = Path(info.file_path)
        if not info.file_name:
            info.file_name = path.name
        if self.ts.upload_settings.file_upload_use_named_pattern:
            info.file_name = self._parse_name(self.ts.upload_settings.name_format_pattern) + path.suffix
        if self.ts.upload_settings.file_upload_replace_problematic_characters:
            info.file_name = re.sub(r"[^\w.\-]", "_", info.file_name)
        data = self.data if self.data is not None else path.read_bytes()
        ext = path.suffix.lower().lstrip(".") or Path(info.file_name).suffix.lower().lstrip(".")
        image_exts = set(self.ts.advanced_settings.image_extensions.split())
        is_image = ext in image_exts
        if is_image and self.ts.advanced_settings.use_after_capture_tasks_during_file_upload:
            self.image = imaging.load(path) if self.data is None else Image.open(io.BytesIO(data))
            self.info.job = TaskJob.Job
            self._do_capture_job()
            return
        if is_image and self.ts.advanced_settings.process_images_during_file_upload:
            img = imaging.load(path) if self.data is None else Image.open(io.BytesIO(data)).convert("RGBA")
            img = self._apply_selected_preset(img)
            data, fmt = imaging.encode_for_task(img, self.ts.image_settings)
            info.file_name = f"{Path(info.file_name).stem}.{imaging.extension(fmt)}"
        if is_image and self.ts.advanced_settings.upload_images_to_image_host:
            info.data_type = EDataType.Image
        elif self.ts.advanced_settings.upload_text_files_to_text_host and \
                ext in set(self.ts.advanced_settings.text_extensions.split()):
            info.data_type = EDataType.Text
        else:
            info.data_type = EDataType.File
        self._upload_bytes(data, info.file_name, info.data_type)

    def _do_text_upload(self) -> None:
        info = self.info
        ext = self.ts.advanced_settings.text_file_extension or "txt"
        if not info.file_name:
            info.file_name = f"{self._parse_name(self.ts.upload_settings.name_format_pattern)}.{ext}"
        if self.ts.advanced_settings.text_task_save_as_file:
            folder = self.store.screenshots_folder(self.ts)
            folder.mkdir(parents=True, exist_ok=True)
            p = imaging.unique_path(folder / info.file_name)
            p.write_text(info.text, encoding="utf-8")
            info.file_path = str(p)
        self._upload_bytes(info.text.encode("utf-8"), info.file_name, EDataType.Text)

    def _do_shorten(self) -> None:
        from ..uploaders import dispatch
        res, dest = dispatch.shorten_url(self.info.url, self.ts, self.store.uploaders, self.store.app,
                                         self.upload_context())
        self.info.result_url = self.info.url
        self.info.shortened_url = res.shortened_url or res.url
        self.info.upload_host = dest.title
        self.info.uploaded = True

    def _do_share(self) -> None:
        from ..uploaders import dispatch
        target, dest = dispatch.share_url(self.info.url, self.ts, self.store.uploaders, self.upload_context())
        if target:
            self.host.open_url(target)
        self.info.result_url = self.info.url
        self.info.upload_host = dest.title

    def _do_download_upload(self) -> None:
        """UploadURL / clipboard URL contents: download, then upload as a file."""
        ctx = self.upload_context()
        from urllib.parse import unquote, urlparse
        resp = ctx.get(self.info.url, stream=True)
        if resp.status_code >= 400:
            raise ValueError(f"Download failed: HTTP {resp.status_code}")
        data = resp.content
        name = unquote(Path(urlparse(self.info.url).path).name) or "download"
        self.info.file_name = self.info.file_name or name
        folder = self.store.screenshots_folder(self.ts)
        folder.mkdir(parents=True, exist_ok=True)
        p = imaging.unique_path(folder / self.info.file_name)
        p.write_bytes(data)
        self.info.file_path = str(p)
        self.info.job = TaskJob.FileUpload
        self.data = data
        self._do_file_upload()

    def _do_media_file(self) -> None:
        """A recorded video/GIF (ShareX's screen recording after-capture handling)."""
        info, flags, host = self.info, self.ts.after_capture_job, self.host
        if flags & ACT.PerformActions:
            for prog in self.ts.external_programs:
                if prog.is_active and prog.path and prog.matches(info.file_path):
                    out = run_external_program(prog, info.file_path)
                    if out and Path(out).exists():
                        info.file_path, info.file_name = out, Path(out).name
        if flags & ACT.CopyFileToClipboard:
            host.copy_files([info.file_path])
        elif flags & ACT.CopyFilePathToClipboard:
            host.copy_text(info.file_path)
        elif flags & ACT.CopyFolderPathToClipboard:
            host.copy_text(str(Path(info.file_path).parent))
        if flags & ACT.ShowInExplorer:
            host.show_in_folder(info.file_path)
        if flags & ACT.UploadImageToHost:
            path = info.file_path
            self._do_file_upload()
            if flags & ACT.DeleteFile and info.uploaded:
                try:
                    os.unlink(path)
                except OSError as e:
                    info.errors.append(f"Delete failed: {e}")
                info.file_path = ""

    # -- after upload --

    def _after_upload(self) -> None:
        from ..uploaders import dispatch
        info, ts, host = self.info, self.ts, self.host
        flags = ts.after_upload_job
        if not info.result_url:
            return
        if flags & AUT.UseURLShortener and info.job != TaskJob.ShortenURL:
            try:
                res, _ = dispatch.shorten_url(info.result_url, ts, self.store.uploaders, self.store.app,
                                              self.upload_context())
                info.shortened_url = res.shortened_url or res.url
            except Exception as e:
                info.errors.append(f"URL shortener: {e}")
        if flags & AUT.ShareURL and info.job != TaskJob.ShareURL:
            try:
                target, _ = dispatch.share_url(info.url_result, ts, self.store.uploaders, self.upload_context())
                if target:
                    host.open_url(target)
            except Exception as e:
                info.errors.append(f"URL sharing: {e}")
        if flags & AUT.CopyURLToClipboard:
            host.copy_text(format_result(ts.advanced_settings.clipboard_content_format, info))
        if flags & AUT.OpenURL:
            host.open_url(format_result(ts.advanced_settings.open_url_format, info))
        if flags & AUT.ShowQRCode:
            host.show_qr(info.url_result)
        if flags & AUT.ShowAfterUploadWindow:
            host.after_upload_window(info)

    def _add_history(self) -> None:
        info = self.info
        if not self.store.app.history_save_tasks:
            return
        if not (info.file_path or info.result_url):
            return
        typ = {EDataType.Image: "Image", EDataType.Text: "Text", EDataType.URL: "URL"}.get(info.data_type, "File")
        if info.job in (TaskJob.ShortenURL, TaskJob.ShareURL):
            typ = "URL"
        tags = {}
        if info.window.title:
            tags["WindowTitle"] = info.window.title
        if info.window.process_name:
            tags["ProcessName"] = info.window.process_name
        self.manager.history.add(HistoryItem(
            file_name=info.file_name, file_path=info.file_path, type=typ, host=info.upload_host,
            url=info.result_url, thumbnail_url=info.thumbnail_url, deletion_url=info.deletion_url,
            shortened_url=info.shortened_url, tags=tags,
            date_time=_dt.datetime.fromtimestamp(info.end_time or time.time()).isoformat(timespec="seconds")))


def _regex_replace(text: str, pattern: str, replacement: str) -> str:
    # ShareX (.NET) uses $1 for groups; Python uses \1.
    py_repl = re.sub(r"\$(\d+)", r"\\\1", replacement)
    try:
        return re.sub(pattern, py_repl, text)
    except re.error:
        return text


def run_external_program(prog, input_path: str, timeout: float = 300) -> str:
    """Run an Action. ``%input`` / ``%output`` in args; returns the output path (or "")."""
    out_path = ""
    if prog.output_extension:
        ext = prog.output_extension.lstrip(".")
        out_path = str(Path(input_path).with_suffix("." + ext))
        if out_path == input_path:
            out_path = str(Path(input_path).with_name(Path(input_path).stem + "-output." + ext))
    args = prog.args or '"%input"'
    args = args.replace("%input", input_path).replace("%output", out_path)
    try:
        argv = [os.path.expanduser(prog.path), *shlex.split(args)]
    except ValueError:
        argv = [os.path.expanduser(prog.path), input_path]
    subprocess.run(argv, timeout=timeout, check=False,
                   stdout=subprocess.DEVNULL if prog.hidden_window else None,
                   stderr=subprocess.DEVNULL if prog.hidden_window else None)
    if prog.delete_input_file and out_path and Path(out_path).exists() and out_path != input_path:
        try:
            os.unlink(input_path)
        except OSError:
            pass
    return out_path


class TaskManager:
    """Runs tasks on threads, at most ``upload_limit`` at once; tells listeners about every change."""

    def __init__(self, store: ConfigStore, history: HistoryManager, host: TaskHost | None = None) -> None:
        self.store = store
        self.history = history
        self.host = host or TaskHost()
        self.tasks: list[WorkerTask] = []
        self._listeners: list[Callable[[str, TaskInfo], None]] = []
        self._lock = threading.RLock()
        self._sem = threading.BoundedSemaphore(max(1, store.app.upload_limit))

    def add_listener(self, cb: Callable[[str, TaskInfo], None]) -> None:
        self._listeners.append(cb)

    def set_upload_limit(self, limit: int) -> None:
        """Application settings > Upload limit: tasks started from now on share the new limit."""
        self._sem = threading.BoundedSemaphore(max(1, int(limit)))

    def emit(self, event: str, info: TaskInfo) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, info)
            except Exception:
                log.exception("task listener failed")

    def start(self, task: WorkerTask) -> WorkerTask:
        with self._lock:
            self.tasks.append(task)
        self.emit("added", task.info)

        def runner() -> None:
            with self._sem:
                if task.cancel.is_set():
                    task.info.status = TaskStatus.Stopped
                    self.emit("finished", task.info)
                    task.done.set()
                    return
                task.run()

        task.thread = threading.Thread(target=runner, name=f"task-{task.info.id}", daemon=True)
        task.thread.start()
        return task

    # -- convenience constructors (ShareX's TaskHelpers / UploadManager) --

    def run_image(self, img: Image.Image, ts: TaskSettings, *, window: WindowInfo | None = None,
                  region=None, file_name: str = "", workflow_name: str = "") -> WorkerTask:
        info = TaskInfo(job=TaskJob.Job, data_type=EDataType.Image, window=window or WindowInfo(), region=region,
                        file_name=file_name, workflow_name=workflow_name)
        return self.start(WorkerTask(self, info, ts, image=img))

    def upload_file(self, path: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.FileUpload, data_type=EDataType.File, file_path=str(path),
                        file_name=Path(path).name)
        return self.start(WorkerTask(self, info, ts))

    def upload_data(self, data: bytes, file_name: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.DataUpload, data_type=EDataType.File, file_name=file_name)
        return self.start(WorkerTask(self, info, ts, data=data))

    def upload_text(self, text: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.TextUpload, data_type=EDataType.Text, text=text)
        return self.start(WorkerTask(self, info, ts))

    def shorten_url(self, url: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.ShortenURL, data_type=EDataType.URL, url=url)
        return self.start(WorkerTask(self, info, ts))

    def share_url(self, url: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.ShareURL, data_type=EDataType.URL, url=url)
        return self.start(WorkerTask(self, info, ts))

    def run_media_file(self, path: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.MediaFile, data_type=EDataType.File, file_path=str(path),
                        file_name=Path(path).name)
        return self.start(WorkerTask(self, info, ts))

    def download_upload(self, url: str, ts: TaskSettings) -> WorkerTask:
        info = TaskInfo(job=TaskJob.DownloadUpload, data_type=EDataType.File, url=url)
        return self.start(WorkerTask(self, info, ts))

    def stop_all(self) -> int:
        n = 0
        with self._lock:
            for t in self.tasks:
                if not t.done.is_set():
                    t.cancel.set()
                    n += 1
        return n

    def remove(self, info_id: str) -> None:
        with self._lock:
            self.tasks = [t for t in self.tasks if t.info.id != info_id]

    def find(self, info_id: str) -> WorkerTask | None:
        with self._lock:
            return next((t for t in self.tasks if t.info.id == info_id), None)

    @property
    def running(self) -> list[WorkerTask]:
        with self._lock:
            return [t for t in self.tasks if not t.done.is_set()]

    def wait_all(self, timeout: float = 60) -> bool:
        deadline = time.monotonic() + timeout
        return all(t.done.wait(max(0.0, deadline - time.monotonic())) for t in list(self.tasks))
