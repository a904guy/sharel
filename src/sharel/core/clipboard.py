"""The clipboard from a background app on GNOME Wayland.

Mutter ignores selection changes from a client without keyboard focus, and only offers the
clipboard to the focused client. So writes go: extension ``SetClipboard`` (St.Clipboard, owned by
the shell) ▸ ``wl-copy`` ▸ the GTK clipboard (which only works while a ShareL window has focus).
Reads go: extension ``GetClipboard`` ▸ ``wl-paste`` ▸ GTK.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PIL import Image

from ..shell.client import ShellUnavailable, shell

log = logging.getLogger("sharel.clipboard")

GNOME_FILES = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"
IMAGE_MIMES = ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp", "image/tiff")

# The GTK fallback, installed by the app: fn(mime, data) -> bool and fn(mimes) -> (mime, data) | None
gtk_writer: Callable[[str, bytes], bool] | None = None
gtk_reader: Callable[[list[str]], tuple[str, bytes] | None] | None = None


@dataclass
class ClipboardContent:
    kind: str  # "image" | "files" | "text" | "empty"
    image: Image.Image | None = None
    files: list[str] | None = None
    text: str = ""
    mime: str = ""

    @property
    def is_url(self) -> bool:
        t = self.text.strip()
        return self.kind == "text" and "\n" not in t and urlparse(t).scheme in ("http", "https", "ftp") \
            and bool(urlparse(t).netloc)


def _wl_copy(mime: str, data: bytes) -> bool:
    if not shutil.which("wl-copy"):
        return False
    try:
        p = subprocess.run(["wl-copy", "--type", mime], input=data, timeout=5, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return p.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _wl_paste(mime: str) -> bytes | None:
    if not shutil.which("wl-paste"):
        return None
    try:
        p = subprocess.run(["wl-paste", "--no-newline", "--type", mime], timeout=5, capture_output=True)
        return p.stdout if p.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def _wl_types() -> list[str]:
    if not shutil.which("wl-paste"):
        return []
    try:
        p = subprocess.run(["wl-paste", "--list-types"], timeout=5, capture_output=True, text=True)
        return p.stdout.split() if p.returncode == 0 else []
    except (subprocess.SubprocessError, OSError):
        return []


def set_data(mime: str, data: bytes) -> str:
    """Put data on the clipboard; returns the method that worked ("extension", "wl-copy", "gtk")."""
    sh = shell()
    if sh.available:
        try:
            if sh.set_clipboard(mime, data):
                return "extension"
        except ShellUnavailable as e:
            log.info("extension clipboard failed: %s", e)
    if _wl_copy(mime, data):
        return "wl-copy"
    if gtk_writer is not None and gtk_writer(mime, data):
        return "gtk"
    raise RuntimeError("could not set the clipboard (no extension, wl-copy failed, no focused ShareL window)")


def set_text(text: str) -> str:
    return set_data("text/plain;charset=utf-8", text.encode("utf-8"))


def set_image(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return set_data("image/png", buf.getvalue())


def set_files(paths: list[str]) -> str:
    uris = [Path(p).resolve().as_uri() for p in paths]
    # GNOME Files (and most GTK file managers) paste x-special/gnome-copied-files.
    payload = ("copy\n" + "\n".join(uris)).encode()
    return set_data(GNOME_FILES, payload)


def mimetypes() -> list[str]:
    sh = shell()
    if sh.available:
        try:
            return sh.clipboard_mimetypes()
        except ShellUnavailable:
            pass
    return _wl_types()


def get_data(mime: str) -> bytes | None:
    sh = shell()
    if sh.available:
        try:
            data = sh.get_clipboard(mime)
            if data:
                return data
        except ShellUnavailable:
            pass
    data = _wl_paste(mime)
    if data:
        return data
    if gtk_reader is not None:
        res = gtk_reader([mime])
        if res:
            return res[1]
    return None


def _parse_uris(text: str) -> list[str]:
    files = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line in ("copy", "cut") or line.startswith("#"):
            continue
        u = urlparse(line)
        if u.scheme == "file":
            files.append(unquote(u.path))
    return files


def read() -> ClipboardContent:
    """What's on the clipboard, classified like ShareX's clipboard upload: image, files, or text."""
    types = mimetypes()
    for m in IMAGE_MIMES:
        if m in types:
            data = get_data(m)
            if data:
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        im.load()
                        return ClipboardContent("image", image=im.convert("RGBA"), mime=m)
                except OSError:
                    pass
    for m in (GNOME_FILES, URI_LIST):
        if m in types:
            data = get_data(m)
            if data:
                files = _parse_uris(data.decode("utf-8", errors="replace"))
                if files:
                    return ClipboardContent("files", files=files, mime=m)
    for m in ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT"):
        if m in types or not types:
            data = get_data(m)
            if data:
                return ClipboardContent("text", text=data.decode("utf-8", errors="replace"), mime=m)
            if not types:
                break
    return ClipboardContent("empty")


def file_uri(path: str) -> str:
    return "file://" + quote(str(Path(path).resolve()))
