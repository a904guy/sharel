"""OCR (ShareX "Recognize text") via the tesseract CLI.

API
---
``is_available() -> bool``                    tesseract is installed.
``list_languages() -> list[str]``             installed traineddata names from ``tesseract --list-langs``
                                              (``osd`` — orientation/script detection, not a language — is left
                                              out).
``ocr_image(img, language="eng", scale_factor=2.0, single_line=False, cancel=None, timeout=120) -> str``
    The image is flattened onto white, upscaled by ``scale_factor`` (ShareX's OCR "scale" option; small UI text
    reads much better at 2-3×), written to a temporary PNG and passed to ``tesseract <png> stdout -l <lang>``
    with ``--psm 7`` (treat the image as one text line) when ``single_line`` else ``--psm 3`` (automatic page
    segmentation). Multiple languages are joined with "+" ("eng+deu"). Setting ``cancel`` kills tesseract and
    raises ``OperationCancelled``. The positional order (img, language, scale_factor) is what
    ``sharel.core.tasks`` uses.
``ocr_file(path, ...)``                       the same for an image file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading

from PIL import Image

from ._common import ToolError, require_binary, run_cancelable

__all__ = ["is_available", "list_languages", "ocr_file", "ocr_image"]

_ENV = {"OMP_THREAD_LIMIT": "1"}  # tesseract's OpenMP threads only slow small images down


def is_available() -> bool:
    return shutil.which("tesseract") is not None


def list_languages(timeout: float = 30) -> list[str]:
    exe = require_binary("tesseract")
    proc = run_cancelable([exe, "--list-langs"], timeout=timeout, env=_ENV)
    if proc.returncode != 0:
        raise ToolError(f"tesseract --list-langs failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    text = proc.stdout.decode("utf-8", "replace")
    langs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of available languages") or line == "osd":
            continue
        langs.append(line)
    return sorted(langs)


def _prepare(img: Image.Image, scale_factor: float) -> Image.Image:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        im = bg
    elif img.mode in ("RGB", "L"):
        im = img
    else:
        im = img.convert("RGB")
    if scale_factor and abs(scale_factor - 1.0) > 1e-6:
        if scale_factor <= 0:
            raise ValueError("scale factor must be positive")
        size = (max(1, round(im.width * scale_factor)), max(1, round(im.height * scale_factor)))
        im = im.resize(size, Image.Resampling.LANCZOS if scale_factor > 1 else Image.Resampling.BICUBIC)
    return im


def ocr_image(img: Image.Image, language: str = "eng", scale_factor: float = 2.0, single_line: bool = False,
              cancel: threading.Event | None = None, timeout: float = 120) -> str:
    exe = require_binary("tesseract")
    lang = "+".join(p.strip() for p in (language or "eng").replace(",", "+").split("+") if p.strip()) or "eng"
    im = _prepare(img, scale_factor)
    with tempfile.TemporaryDirectory(prefix="sharel-ocr-") as tmp:
        path = os.path.join(tmp, "in.png")
        im.save(path, "PNG")
        argv = [exe, path, "stdout", "-l", lang, "--psm", "7" if single_line else "3"]
        proc = run_cancelable(argv, cancel, timeout=timeout, env=_ENV)
    if proc.returncode != 0:
        raise ToolError(f"tesseract failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}")
    text = proc.stdout.decode("utf-8", "replace").replace("\f", "")
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def ocr_file(path: str | os.PathLike, language: str = "eng", scale_factor: float = 2.0, single_line: bool = False,
             cancel: threading.Event | None = None, timeout: float = 120) -> str:
    with Image.open(path) as im:
        im.load()
        return ocr_image(im, language, scale_factor, single_line, cancel, timeout)
