"""Bundled sticker packs for the Sticker tool (ShareX's StickerPackInfo / StickerForm).

A pack is a directory under ``<package data>/stickers/<Pack>/`` holding image files; extra
directories can be registered by the user (ShareX lets you add sticker pack folders). The
image editor resizes the chosen sticker to ``AnnotationOptions.sticker_size`` (longest side).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..core import imaging, paths

STICKER_EXTENSIONS = (".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp")


def stickers_root() -> Path:
    return paths.package_data_dir() / "stickers"


def pack_dir(pack: str | Path) -> Path:
    """A bundled pack name ("Twemoji") or an absolute folder path (user-added pack)."""
    p = Path(pack)
    if p.is_absolute():
        return p
    return stickers_root() / str(pack)


def list_packs() -> list[str]:
    """Names of the bundled sticker packs, sorted."""
    root = stickers_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and any(_image_files(d)))


def _image_files(folder: Path):
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in STICKER_EXTENSIONS:
            yield f


def list_stickers(pack: str | Path) -> list[Path]:
    """Image files of a pack, sorted by file name. Empty list for an unknown pack."""
    d = pack_dir(pack)
    if not d.is_dir():
        return []
    return sorted(_image_files(d), key=lambda f: f.name)


def load_sticker(path: str | Path, size: int) -> Image.Image:
    """Load a sticker as RGBA and limit it to ``size`` px on its longest side (ShareX's
    ``ResizeImageLimit``: never enlarges, keeps the aspect ratio)."""
    img = imaging.load(path).convert("RGBA")
    return resize_limit(img, size)


def resize_limit(img: Image.Image, size: int) -> Image.Image:
    w, h = img.size
    if size <= 0 or (w <= size and h <= size):
        return img
    rx, ry = size / w, size / h
    nw, nh = size, size
    if rx < ry:
        nh = max(1, round(h * rx))
    elif rx > ry:
        nw = max(1, round(w * ry))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)
