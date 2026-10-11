"""Watch folders (TaskSettings > Watch folders): new files are uploaded automatically.

Each WatchFolder gets a ``Gio.FileMonitor``; a file is taken once it has stopped growing (a
``CHANGES_DONE_HINT`` or no size change for a second), optionally moved into the screenshots
folder first, and uploaded with the owning workflow's task settings.
"""
from __future__ import annotations

import fnmatch
import logging
import shutil
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .config import WatchFolder

log = logging.getLogger("sharel.watchfolders")


def matches(folder: WatchFolder, path: Path) -> bool:
    patterns = [p.strip() for p in (folder.filter or "*.*").replace(",", ";").split(";") if p.strip()]
    name = path.name
    return any(fnmatch.fnmatch(name, p) or (p == "*.*" and "." not in name) for p in patterns)


class WatchFolderManager:
    def __init__(self, app) -> None:
        self.app = app
        self.monitors: list[tuple[Gio.FileMonitor, WatchFolder, object]] = []
        self._pending: dict[str, int] = {}
        self.handled: list[str] = []

    def stop(self) -> None:
        for mon, _f, _ts in self.monitors:
            mon.cancel()
        self.monitors.clear()
        for src in self._pending.values():
            GLib.source_remove(src)
        self._pending.clear()

    def apply(self) -> None:
        """(Re)create monitors for the default task settings and every workflow that enables them."""
        self.stop()
        store = self.app.store
        # Watch folders have no "use default" switch: every workflow's own list counts (ShareX's
        # WatchFolderManager.UpdateWatchFolders walks every hotkey's task settings).
        sources = [(store.app.default_task_settings, None)] + \
                  [(hk.task_settings, hk) for hk in store.hotkeys.hotkeys]
        for ts, hk in sources:
            if not ts.watch_folder_enabled:
                continue
            for wf in ts.watch_folder_list:
                if wf.folder_path:
                    self._watch(Path(wf.folder_path).expanduser(), wf, hk)

    def _watch(self, root: Path, wf: WatchFolder, hk) -> None:
        dirs = [root]
        if wf.include_subdirectories and root.is_dir():
            dirs += [p for p in root.rglob("*") if p.is_dir()]
        for d in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                mon = Gio.File.new_for_path(str(d)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except (GLib.Error, OSError) as e:
                log.warning("cannot watch %s: %s", d, e)
                continue
            mon.connect("changed", self._on_changed, wf, hk)
            self.monitors.append((mon, wf, hk))

    def _on_changed(self, _mon, file, other, event, wf, hk) -> None:
        if event in (Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED):
            file = other or file
        if event not in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                         Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED):
            return
        path = Path(file.get_path() or "")
        if not path.name or not path.is_file() or not matches(wf, path) or path.name.startswith("."):
            return
        key = str(path)
        if key in self._pending:
            GLib.source_remove(self._pending[key])
        self._pending[key] = GLib.timeout_add(1000, self._settled, path, wf, hk, -1)

    def _settled(self, path: Path, wf: WatchFolder, hk, last_size: int) -> bool:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            self._pending.pop(key, None)
            return False
        if size != last_size:
            self._pending[key] = GLib.timeout_add(700, self._settled, path, wf, hk, size)
            return False
        self._pending.pop(key, None)
        self._upload(path, wf, hk)
        return False

    def _upload(self, path: Path, wf: WatchFolder, hk) -> None:
        store = self.app.store
        ts = store.task_settings_for(hk)
        if wf.move_files_to_screenshots_folder:
            dest_dir = store.screenshots_folder(ts)
            dest_dir.mkdir(parents=True, exist_ok=True)
            from .imaging import unique_path
            dest = unique_path(dest_dir / path.name)
            shutil.move(str(path), dest)
            path = dest
        self.handled.append(str(path))
        log.info("watch folder: uploading %s", path)
        self.app.tasks.upload_file(str(path), ts)
