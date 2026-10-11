"""History.json: every finished task (ShareX's HistoryItem), thread-safe, append-friendly."""
from __future__ import annotations

import datetime as _dt
import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .serialize import from_jsonable, save_json, to_jsonable

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "ico"}


@dataclass
class HistoryItem:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    file_name: str = ""
    file_path: str = ""
    date_time: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    type: str = "File"  # Image | Text | File | URL
    host: str = ""
    url: str = ""
    thumbnail_url: str = ""
    deletion_url: str = ""
    shortened_url: str = ""
    tags: dict[str, str] = field(default_factory=dict)  # WindowTitle, ProcessName

    @property
    def when(self) -> _dt.datetime:
        try:
            return _dt.datetime.fromisoformat(self.date_time)
        except ValueError:
            return _dt.datetime.min

    @property
    def is_image(self) -> bool:
        name = self.file_path or self.file_name or self.url
        return self.type == "Image" or Path(name.split("?")[0]).suffix.lower().lstrip(".") in IMAGE_EXTS

    @property
    def result_url(self) -> str:
        return self.shortened_url or self.url


@dataclass
class HistoryFilter:
    text: str = ""
    type: str = ""  # "" = any
    host: str = ""
    date_from: _dt.date | None = None
    date_to: _dt.date | None = None
    images_only: bool = False
    existing_files_only: bool = False

    def matches(self, item: HistoryItem) -> bool:
        if self.type and item.type != self.type:
            return False
        if self.host and item.host != self.host:
            return False
        if self.images_only and not item.is_image:
            return False
        if self.existing_files_only and not (item.file_path and Path(item.file_path).exists()):
            return False
        d = item.when.date()
        if self.date_from and d < self.date_from:
            return False
        if self.date_to and d > self.date_to:
            return False
        if self.text:
            needle = self.text.lower()
            hay = " ".join([item.file_name, item.file_path, item.url, item.shortened_url, item.host,
                            *item.tags.values()]).lower()
            if needle not in hay:
                return False
        return True


class HistoryManager:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: list[HistoryItem] | None = None
        self.listeners: list = []

    def _load(self) -> list[HistoryItem]:
        if self._items is None:
            items: list[HistoryItem] = []
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    items = [from_jsonable(HistoryItem, d) for d in raw if isinstance(d, dict)]
            except (OSError, ValueError):
                items = []
            self._items = items
        return self._items

    def items(self) -> list[HistoryItem]:
        with self._lock:
            return list(self._load())

    def add(self, item: HistoryItem) -> None:
        with self._lock:
            self._load().append(item)
            self._save()
        for cb in list(self.listeners):
            cb(item)

    def remove(self, item_id: str) -> bool:
        with self._lock:
            items = self._load()
            before = len(items)
            self._items = [i for i in items if i.id != item_id]
            if len(self._items) != before:
                self._save()
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()

    def _save(self) -> None:
        save_json([to_jsonable(i) for i in self._items or []], self.path, backup=False)

    def search(self, flt: HistoryFilter, newest_first: bool = True, limit: int = -1) -> list[HistoryItem]:
        res = [i for i in self.items() if flt.matches(i)]
        res.sort(key=lambda i: i.date_time, reverse=newest_first)
        return res if limit < 0 else res[:limit]

    def hosts(self) -> list[str]:
        return sorted({i.host for i in self.items() if i.host})

    def types(self) -> list[str]:
        return sorted({i.type for i in self.items() if i.type})
