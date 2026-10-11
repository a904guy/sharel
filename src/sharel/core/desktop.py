"""Desktop integration: open files/URLs/folders, show a file in the file manager."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger("sharel.desktop")


def _launch(argv: list[str]) -> None:
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_url(url: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
    except GLib.Error:
        _launch(["xdg-open", url])


def open_file(path: str) -> None:
    open_url(Path(path).resolve().as_uri())


def open_folder(path: str) -> None:
    p = Path(path)
    if p.is_file():
        p = p.parent
    p.mkdir(parents=True, exist_ok=True)
    open_url(p.resolve().as_uri())


def show_in_folder(path: str) -> None:
    """ShareX's "Show in Explorer": org.freedesktop.FileManager1.ShowItems, else open the folder."""
    uri = Path(path).resolve().as_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", ([uri], "")),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as e:
        log.info("FileManager1.ShowItems failed (%s); opening the folder", e.message)
        open_folder(str(Path(path).parent))
