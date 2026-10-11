"""Encoding and saving images the way ShareX's TaskSettingsImage describes."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .config import TaskSettingsImage
from .enums import IMAGE_EXTENSIONS, PIL_FORMATS, EImageFormat, FileExistAction, GIFQuality, PNGBitDepth


def encode(img: Image.Image, fmt: EImageFormat, opts: TaskSettingsImage, jpeg_quality: int | None = None) -> bytes:
    buf = io.BytesIO()
    name = fmt.value
    im = img
    kw: dict = {}
    if name == "JPEG":
        im = flatten(img)
        kw["quality"] = jpeg_quality if jpeg_quality is not None else opts.image_jpeg_quality
        kw["optimize"] = True
    elif name == "PNG":
        if opts.image_png_bit_depth == PNGBitDepth.Bit24:
            im = flatten(img)
        elif img.mode not in ("RGBA", "RGB", "L", "LA", "P"):
            im = img.convert("RGBA")
        kw["optimize"] = False
    elif name == "GIF":
        q = opts.image_gif_quality
        if q == GIFQuality.Grayscale:
            im = img.convert("L")
        elif q == GIFQuality.Bit4:
            im = img.convert("RGB").quantize(colors=16, method=Image.Quantize.MEDIANCUT)
        else:
            im = img.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    elif name == "BMP":
        im = flatten(img)
    elif name == "TIFF":
        kw["compression"] = "tiff_lzw"
    elif name == "WEBP":
        kw["quality"] = opts.image_webp_quality
    im.save(buf, PIL_FORMATS[name], **kw)
    return buf.getvalue()


def flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def encode_for_task(img: Image.Image, opts: TaskSettingsImage) -> tuple[bytes, EImageFormat]:
    """Encode with the primary format; switch to the fallback format when the file is bigger than
    ``image_auto_use_jpeg_size`` KB (ShareX's "use JPEG if bigger than")."""
    data = encode(img, opts.image_format, opts)
    if (opts.image_auto_use_jpeg and opts.image_format != opts.image_format2
            and len(data) > opts.image_auto_use_jpeg_size * 1024):
        q = opts.image_auto_jpeg_quality if opts.image_format2 == EImageFormat.JPEG else None
        data = encode(img, opts.image_format2, opts, jpeg_quality=q)
        return data, opts.image_format2
    return data, opts.image_format


def extension(fmt: EImageFormat) -> str:
    return IMAGE_EXTENSIONS[fmt.value]


def unique_path(path: Path) -> Path:
    """name.png -> name (1).png -> name (2).png ... (ShareX's GetUniqueFilePath)."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 1
    while True:
        cand = path.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def resolve_existing(path: Path, action: FileExistAction, ask=None) -> Path | None:
    """Apply FileExistAction. ``ask(path) -> FileExistAction`` handles Ask. None means cancel."""
    if not path.exists():
        return path
    if action == FileExistAction.Ask:
        action = ask(path) if ask else FileExistAction.UniqueName
        if action == FileExistAction.Ask:
            action = FileExistAction.UniqueName
    if action == FileExistAction.Overwrite:
        return path
    if action == FileExistAction.UniqueName:
        return unique_path(path)
    return None


def thumbnail(img: Image.Image, width: int, height: int) -> Image.Image:
    """ShareX: width/height 0 keeps the aspect ratio from the other side."""
    w, h = img.size
    if width <= 0 and height <= 0:
        return img.copy()
    if width <= 0:
        width = max(1, round(w * height / h))
    elif height <= 0:
        height = max(1, round(h * width / w))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def load(path: str | Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        if im.mode not in ("RGBA", "RGB"):
            im = im.convert("RGBA")
        return im.copy()


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
