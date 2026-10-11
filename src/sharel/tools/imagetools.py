"""Image tools from ShareX's Tools menu: combiner, splitter, thumbnailer, resizer, converter, watermark, comparer
and icon converter. Pure Pillow/numpy; the batch helpers take ``progress(done, total)`` and ``cancel``.

Combiner (ShareX ImageHelpers.CombineImages)
    ``combine_images(images, orientation="Vertical", alignment="LeftOrTop", space=0, wrap_after=0,
    auto_fill_background=True) -> RGBA image``. ``orientation`` "Vertical" stacks top→bottom, "Horizontal"
    left→right; ``alignment`` "LeftOrTop" / "Center" / "RightOrBottom" places narrower images across the stack;
    ``wrap_after`` > 0 starts a new column/row after that many images; with ``auto_fill_background`` the canvas
    is filled with the first image's bottom-right pixel (ShareX), else it stays transparent.
    ``combine_files(paths, ...)``.
Splitter
    ``split_image(img, rows, columns)`` — ShareX: equal cells of ``W // columns`` × ``H // rows`` (the remainder
    is dropped), row by row. ``split_by_tile_size(img, tile_width, tile_height)`` — fixed tiles; edge tiles are
    smaller. ``split_to_files(src, out_dir, rows=, columns=, tile_size=, fmt=None) -> [Path]`` writes
    ``<stem>_<row>_<col>.<ext>`` (1-based).
Thumbnailer
    ``thumbnail_size(src_size, width, height)`` — 0 for one side = proportional; both given = fit inside.
    ``create_thumbnail(img, width, height)``; ``thumbnail_files(paths, out_dir=None, width=200, height=0,
    quality=90, suffix="-thumbnail", fmt="JPEG")`` → ``<stem><suffix>.<ext>`` next to each file by default.
Resizer
    ``resize_size(src_size, width=0, height=0, percentage=None, keep_aspect=True, allow_enlarge=True)``,
    ``resize_image(img, ..., resample="Lanczos")`` (Nearest, Bilinear, Bicubic, Lanczos, Box, Hamming),
    ``resize_files(paths, out_dir=None, suffix="-resized", ...)``.
Converter
    ``save_image(img, path, fmt=None, quality=90)`` — PNG/JPEG/GIF/BMP/TIFF/WEBP (JPEG and BMP are flattened on
    white; GIF is palettised); ``convert_file(src, fmt, out_dir=None, quality=90)``; ``convert_files(...)``.
Watermark (ShareX ImageWatermarkService)
    ``watermark_text(img, text, position=ContentAlignment.BottomRight, margin=10, opacity=100, font_size=24,
    color=(255,255,255), rotation=0, shadow=False)`` and ``watermark_image(img, mark, position, margin, opacity,
    scale=25)`` (``scale`` = percent of the canvas the mark may fill, aspect kept; ``scale=0`` = original size).
    ``watermark_files(...)`` batch version.
Comparer (ShareX ImageComparisonService)
    ``compare_images(a, b) -> ComparisonResult`` with ``identical``, ``changed_pixels``, ``total_pixels``
    (over the larger of the two sizes, so a size difference counts as change), ``changed_percent``,
    ``similarity_percent``, ``bbox`` (of the changes, or None) and ``diff_image`` (ShareX style: black, changed
    pixels white). ``highlight_differences(a, b)`` — b dimmed to gray with changed pixels in red.
Icon converter (ShareX IconConverterService)
    ``ICON_SIZES = (16, 32, 48, 64, 128, 256)``; ``image_to_ico(img, dst, sizes=ICON_SIZES)`` (non-square images
    are centred on a transparent square first); ``ico_to_png(src, dst)`` writes the largest frame;
    ``ico_frames(src) -> list[Image]`` every size in the file.
"""
from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, IcoImagePlugin, ImageOps

from ..core.enums import ContentAlignment
from ._common import ProgressFn, check_cancel, load_font

__all__ = [
    "ICON_SIZES", "ComparisonResult", "combine_files", "combine_images", "compare_images", "convert_file",
    "convert_files", "create_thumbnail", "highlight_differences", "ico_frames", "ico_to_png", "image_to_ico",
    "resize_files", "resize_image", "resize_size", "save_image", "split_by_tile_size", "split_image",
    "split_to_files", "thumbnail_files", "thumbnail_size", "watermark_files", "watermark_image", "watermark_text",
]

RGB = tuple[int, int, int]
ICON_SIZES = (16, 32, 48, 64, 128, 256)

_RESAMPLE = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR,
             "bicubic": Image.Resampling.BICUBIC, "lanczos": Image.Resampling.LANCZOS,
             "box": Image.Resampling.BOX, "hamming": Image.Resampling.HAMMING}
_FORMATS = {"PNG": ("PNG", "png"), "JPEG": ("JPEG", "jpg"), "JPG": ("JPEG", "jpg"), "GIF": ("GIF", "gif"),
            "BMP": ("BMP", "bmp"), "TIFF": ("TIFF", "tif"), "TIF": ("TIFF", "tif"), "WEBP": ("WEBP", "webp")}


def _open(path: str | os.PathLike) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return ImageOps.exif_transpose(im)


def _batch(paths: Sequence[str | os.PathLike], fn: Callable[[Path], Path | list[Path]],
           progress: ProgressFn | None, cancel: threading.Event | None) -> list[Path]:
    out: list[Path] = []
    total = len(paths)
    if progress:
        progress(0, total)
    for i, p in enumerate(paths):
        check_cancel(cancel)
        r = fn(Path(p))
        out.extend(r if isinstance(r, list) else [r])
        if progress:
            progress(i + 1, total)
    return out


def _out_path(src: Path, out_dir: str | os.PathLike | None, suffix: str, ext: str) -> Path:
    d = Path(out_dir) if out_dir else src.parent
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{src.stem}{suffix}.{ext}"


# ---- format / saving -------------------------------------------------------------------------


def _resolve_format(fmt: str | None, path: Path | None = None) -> tuple[str, str]:
    if fmt is None:
        if path is None or not path.suffix:
            return _FORMATS["PNG"]
        fmt = path.suffix.lstrip(".")
    key = str(fmt).upper().lstrip(".")
    if key not in _FORMATS:
        raise ValueError(f"unsupported image format {fmt!r}")
    return _FORMATS[key]


def _flatten(img: Image.Image, background: RGB = (255, 255, 255)) -> Image.Image:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB") if img.mode not in ("RGB", "L") else img


def save_image(img: Image.Image, path: str | os.PathLike, fmt: str | None = None, quality: int = 90) -> Path:
    p = Path(path)
    pil, _ext = _resolve_format(fmt, p)
    q = max(1, min(100, int(quality)))
    kw: dict = {}
    im = img
    if pil == "JPEG":
        im = _flatten(img)
        kw.update(quality=q, optimize=True)
    elif pil == "BMP":
        im = _flatten(img)
    elif pil == "GIF":
        if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 255:
            alpha = img.getchannel("A")
            im = img.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
            mask = alpha.point(lambda a: 255 if a < 128 else 0)
            im.paste(255, mask=mask)
            kw["transparency"] = 255
        elif img.mode not in ("P", "L"):
            im = img.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    elif pil == "WEBP":
        kw["quality"] = q
    elif pil == "TIFF":
        kw["compression"] = "tiff_lzw"
    elif pil == "PNG" and img.mode not in ("RGBA", "RGB", "L", "LA", "P", "I", "I;16", "1"):
        im = img.convert("RGBA")
    p.parent.mkdir(parents=True, exist_ok=True)
    im.save(p, pil, **kw)
    return p


def convert_file(src: str | os.PathLike, fmt: str, out_dir: str | os.PathLike | None = None,
                 quality: int = 90, suffix: str = "") -> Path:
    s = Path(src)
    _pil, ext = _resolve_format(fmt)
    dst = _out_path(s, out_dir, suffix, ext)
    if dst.resolve() == s.resolve():
        raise ValueError(f"{s.name} is already a .{ext} file in that folder")
    return save_image(_open(s), dst, fmt, quality)


def convert_files(paths: Sequence[str | os.PathLike], fmt: str, out_dir: str | os.PathLike | None = None,
                  quality: int = 90, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None) -> list[Path]:
    return _batch(paths, lambda p: convert_file(p, fmt, out_dir, quality), progress, cancel)


# ---- combiner --------------------------------------------------------------------------------


def combine_images(images: Sequence[Image.Image], orientation: str = "Vertical", alignment: str = "LeftOrTop",
                   space: int = 0, wrap_after: int = 0, auto_fill_background: bool = True) -> Image.Image:
    if not images:
        raise ValueError("no images to combine")
    if orientation not in ("Vertical", "Horizontal"):
        raise ValueError(f"orientation must be Vertical or Horizontal, not {orientation!r}")
    if alignment not in ("LeftOrTop", "Center", "RightOrBottom"):
        raise ValueError(f"unknown alignment {alignment!r}")
    space = max(0, int(space))
    rects: list[tuple[int, int, int, int]] = []
    px = py = 0
    current = 0
    for i, im in enumerate(images):
        if orientation == "Horizontal":
            if wrap_after > 0:
                if i % wrap_after == 0:
                    if i > 0:
                        px = 0
                        py += current + space
                    current = max(x.height for x in images[i:i + wrap_after])
            elif i == 0:
                current = max(x.height for x in images)
            oy = {"LeftOrTop": 0, "Center": current // 2 - im.height // 2,
                  "RightOrBottom": current - im.height}[alignment]
            rects.append((px, py + oy, im.width, im.height))
            px += im.width + space
        else:
            if wrap_after > 0:
                if i % wrap_after == 0:
                    if i > 0:
                        px += current + space
                        py = 0
                    current = max(x.width for x in images[i:i + wrap_after])
            elif i == 0:
                current = max(x.width for x in images)
            ox = {"LeftOrTop": 0, "Center": current // 2 - im.width // 2,
                  "RightOrBottom": current - im.width}[alignment]
            rects.append((px + ox, py, im.width, im.height))
            py += im.height + space
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    first = images[0].convert("RGBA")
    bg = first.getpixel((first.width - 1, first.height - 1)) if auto_fill_background else (0, 0, 0, 0)
    canvas = Image.new("RGBA", (right - left, bottom - top), bg)
    for im, (x, y, _w, _h) in zip(images, rects, strict=True):
        rgba = im.convert("RGBA")
        canvas.alpha_composite(rgba, (x - left, y - top))
    return canvas


def combine_files(paths: Sequence[str | os.PathLike], **kw) -> Image.Image:
    return combine_images([_open(p) for p in paths], **kw)


# ---- splitter --------------------------------------------------------------------------------


def split_image(img: Image.Image, rows: int, columns: int) -> list[Image.Image]:
    if rows < 1 or columns < 1:
        raise ValueError("rows and columns must be >= 1")
    w, h = img.width // columns, img.height // rows
    if w < 1 or h < 1:
        raise ValueError("the image is too small for that many cells")
    return [img.crop((x * w, y * h, (x + 1) * w, (y + 1) * h)) for y in range(rows) for x in range(columns)]


def split_by_tile_size(img: Image.Image, tile_width: int, tile_height: int) -> list[list[Image.Image]]:
    """Rows of tiles; the last column/row holds the remainder."""
    if tile_width < 1 or tile_height < 1:
        raise ValueError("tile size must be >= 1")
    return [[img.crop((x, y, min(x + tile_width, img.width), min(y + tile_height, img.height)))
             for x in range(0, img.width, tile_width)] for y in range(0, img.height, tile_height)]


def split_to_files(src: str | os.PathLike, out_dir: str | os.PathLike | None = None, rows: int | None = None,
                   columns: int | None = None, tile_size: tuple[int, int] | None = None,
                   fmt: str | None = None, quality: int = 90) -> list[Path]:
    s = Path(src)
    img = _open(s)
    if tile_size is not None:
        grid = split_by_tile_size(img, tile_size[0], tile_size[1])
    elif rows and columns:
        flat = split_image(img, rows, columns)
        grid = [flat[r * columns:(r + 1) * columns] for r in range(rows)]
    else:
        raise ValueError("give rows and columns, or a tile size")
    _pil, ext = _resolve_format(fmt, s)
    d = Path(out_dir) if out_dir else s.parent
    out = []
    for r, row in enumerate(grid):
        for c, tile in enumerate(row):
            out.append(save_image(tile, d / f"{s.stem}_{r + 1}_{c + 1}.{ext}", fmt or ext, quality))
    return out


# ---- thumbnails / resize ---------------------------------------------------------------------


def thumbnail_size(src_size: tuple[int, int], width: int, height: int) -> tuple[int, int]:
    sw, sh = src_size
    if width <= 0 and height <= 0:
        return sw, sh
    if height <= 0:
        return width, max(1, round(sh * width / sw))
    if width <= 0:
        return max(1, round(sw * height / sh)), height
    ratio = min(width / sw, height / sh)
    return max(1, round(sw * ratio)), max(1, round(sh * ratio))


def create_thumbnail(img: Image.Image, width: int = 200, height: int = 0) -> Image.Image:
    size = thumbnail_size(img.size, width, height)
    return img.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)


def thumbnail_files(paths: Sequence[str | os.PathLike], out_dir: str | os.PathLike | None = None,
                    width: int = 200, height: int = 0, quality: int = 90, suffix: str = "-thumbnail",
                    fmt: str = "JPEG", progress: ProgressFn | None = None,
                    cancel: threading.Event | None = None) -> list[Path]:
    _pil, ext = _resolve_format(fmt)

    def one(p: Path) -> Path:
        return save_image(create_thumbnail(_open(p), width, height), _out_path(p, out_dir, suffix, ext), fmt,
                          quality)
    return _batch(paths, one, progress, cancel)


def resize_size(src_size: tuple[int, int], width: int = 0, height: int = 0, percentage: float | None = None,
                keep_aspect: bool = True, allow_enlarge: bool = True) -> tuple[int, int]:
    sw, sh = src_size
    if percentage is not None:
        if percentage <= 0:
            raise ValueError("percentage must be positive")
        w, h = max(1, round(sw * percentage / 100)), max(1, round(sh * percentage / 100))
    elif keep_aspect:
        w, h = thumbnail_size(src_size, width, height)
    else:
        w, h = width if width > 0 else sw, height if height > 0 else sh
    if not allow_enlarge and (w > sw or h > sh):
        ratio = min(sw / w, sh / h)
        w, h = max(1, round(w * ratio)), max(1, round(h * ratio))
    return w, h


def resize_image(img: Image.Image, width: int = 0, height: int = 0, percentage: float | None = None,
                 keep_aspect: bool = True, resample: str = "Lanczos", allow_enlarge: bool = True) -> Image.Image:
    try:
        rs = _RESAMPLE[resample.lower()]
    except KeyError:
        raise ValueError(f"unknown resampling {resample!r}") from None
    size = resize_size(img.size, width, height, percentage, keep_aspect, allow_enlarge)
    return img if size == img.size else img.resize(size, rs)


def resize_files(paths: Sequence[str | os.PathLike], out_dir: str | os.PathLike | None = None,
                 suffix: str = "-resized", width: int = 0, height: int = 0, percentage: float | None = None,
                 keep_aspect: bool = True, resample: str = "Lanczos", allow_enlarge: bool = True,
                 quality: int = 90, progress: ProgressFn | None = None,
                 cancel: threading.Event | None = None) -> list[Path]:
    def one(p: Path) -> Path:
        _pil, ext = _resolve_format(None, p)
        img = resize_image(_open(p), width, height, percentage, keep_aspect, resample, allow_enlarge)
        return save_image(img, _out_path(p, out_dir, suffix, ext), ext, quality)
    return _batch(paths, one, progress, cancel)


# ---- watermark -------------------------------------------------------------------------------


def _location(canvas: tuple[int, int], mark: tuple[int, int], position: ContentAlignment | str,
              margin: int) -> tuple[int, int]:
    pos = ContentAlignment(position) if not isinstance(position, ContentAlignment) else position
    cw, ch = canvas
    mw, mh = mark
    left, cx, right = margin, (cw - mw) // 2, cw - mw - margin
    top, cy, bottom = margin, (ch - mh) // 2, ch - mh - margin
    name = pos.value
    x = left if name.endswith("Left") else right if name.endswith("Right") else cx
    y = top if name.startswith("Top") else bottom if name.startswith("Bottom") else cy
    return x, y


def _apply_opacity(mark: Image.Image, opacity: float) -> Image.Image:
    op = max(0.0, min(100.0, float(opacity))) / 100
    if op >= 1:
        return mark
    a = mark.getchannel("A").point(lambda v: round(v * op))
    out = mark.copy()
    out.putalpha(a)
    return out


def _text_mark(text: str, font_size: float, color: RGB | tuple[int, int, int, int], shadow: bool) -> Image.Image:
    font = load_font(font_size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.multiline_textbbox((0, 0), text, font=font)
    pad = 2 + (2 if shadow else 0)
    mark = Image.new("RGBA", (max(1, x1 - x0 + pad * 2), max(1, y1 - y0 + pad * 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(mark)
    fill = tuple(color) if len(color) == 4 else (*color, 255)
    if shadow:
        d.multiline_text((pad - x0 + 2, pad - y0 + 2), text, font=font, fill=(0, 0, 0, round(fill[3] * 0.6)))
    d.multiline_text((pad - x0, pad - y0), text, font=font, fill=fill)
    return mark


def watermark_text(img: Image.Image, text: str, position: ContentAlignment | str = ContentAlignment.BottomRight,
                   margin: int = 10, opacity: float = 100, font_size: float = 24,
                   color: RGB | tuple[int, int, int, int] = (255, 255, 255), rotation: float = 0,
                   shadow: bool = False) -> Image.Image:
    out = img.convert("RGBA")
    if not text or not text.strip():
        return out
    mark = _text_mark(text.replace("\r", ""), font_size, color, shadow)
    if rotation % 360:
        mark = mark.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    mark = _apply_opacity(mark, opacity)
    _composite(out, mark, _location(out.size, mark.size, position, margin))
    return out


def _composite(canvas: Image.Image, mark: Image.Image, xy: tuple[int, int]) -> None:
    """alpha_composite ``mark`` at ``xy``, clipping whatever falls outside the canvas (also negative offsets)."""
    x, y = xy
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(mark.width, canvas.width - x), min(mark.height, canvas.height - y)
    if ex <= sx or ey <= sy:
        return
    canvas.alpha_composite(mark, (x + sx, y + sy), (sx, sy, ex, ey))


def watermark_image(img: Image.Image, mark: Image.Image, position: ContentAlignment | str =
                    ContentAlignment.BottomRight, margin: int = 10, opacity: float = 100,
                    scale: float = 25) -> Image.Image:
    out = img.convert("RGBA")
    m = mark.convert("RGBA")
    if scale and scale > 0:
        pct = min(100.0, float(scale)) / 100
        ratio = min(out.width * pct / m.width, out.height * pct / m.height)
        m = m.resize((max(1, round(m.width * ratio)), max(1, round(m.height * ratio))), Image.Resampling.LANCZOS)
    m = _apply_opacity(m, opacity)
    _composite(out, m, _location(out.size, m.size, position, margin))
    return out


def watermark_files(paths: Sequence[str | os.PathLike], out_dir: str | os.PathLike | None = None,
                    suffix: str = "-watermark", text: str = "", mark: Image.Image | None = None,
                    quality: int = 90, progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                    **kw) -> list[Path]:
    def one(p: Path) -> Path:
        src = _open(p)
        res = watermark_image(src, mark, **kw) if mark is not None else watermark_text(src, text, **kw)
        _pil, ext = _resolve_format(None, p)
        return save_image(res, _out_path(p, out_dir, suffix, ext), ext, quality)
    return _batch(paths, one, progress, cancel)


# ---- comparer --------------------------------------------------------------------------------


@dataclass
class ComparisonResult:
    changed_pixels: int
    total_pixels: int
    bbox: tuple[int, int, int, int] | None  # (left, top, right, bottom) exclusive, like PIL
    diff_image: Image.Image
    size_a: tuple[int, int]
    size_b: tuple[int, int]

    @property
    def identical(self) -> bool:
        return self.changed_pixels == 0

    @property
    def changed_percent(self) -> float:
        return 100.0 * self.changed_pixels / self.total_pixels if self.total_pixels else 0.0

    @property
    def similarity_percent(self) -> float:
        return 100.0 - self.changed_percent


def _diff_mask(a: Image.Image, b: Image.Image) -> np.ndarray:
    """Boolean (H×W over the larger size) of changed pixels; outside the common area counts as changed."""
    w, h = max(a.width, b.width), max(a.height, b.height)
    aa = np.asarray(a.convert("RGBA"))
    bb = np.asarray(b.convert("RGBA"))
    cw, ch = min(a.width, b.width), min(a.height, b.height)
    mask = np.ones((h, w), dtype=bool)
    mask[:ch, :cw] = np.any(aa[:ch, :cw] != bb[:ch, :cw], axis=2)
    return mask


def compare_images(a: Image.Image, b: Image.Image) -> ComparisonResult:
    mask = _diff_mask(a, b)
    h, w = mask.shape
    changed = int(mask.sum())
    bbox = None
    if changed:
        ys, xs = np.nonzero(mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    diff = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L").convert("RGB")
    return ComparisonResult(changed, w * h, bbox, diff, a.size, b.size)


def highlight_differences(a: Image.Image, b: Image.Image, color: RGB = (255, 0, 0)) -> Image.Image:
    mask = _diff_mask(a, b)
    h, w = mask.shape
    base = Image.new("RGB", (w, h), (0, 0, 0))
    base.paste(b.convert("L").convert("RGB"), (0, 0))
    arr = np.asarray(base).astype(np.float32) * 0.5 + 64
    arr[mask] = color
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


# ---- icons -----------------------------------------------------------------------------------


def _square(img: Image.Image) -> Image.Image:
    im = img.convert("RGBA")
    side = max(im.width, im.height)
    if im.width == im.height:
        return im
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return sq


def image_to_ico(img: Image.Image, dst: str | os.PathLike, sizes: Iterable[int] = ICON_SIZES) -> Path:
    chosen = sorted({int(s) for s in sizes})
    if not chosen:
        raise ValueError("no icon sizes selected")
    bad = [s for s in chosen if s not in ICON_SIZES]
    if bad:
        raise ValueError(f"unsupported icon sizes {bad}; choose from {ICON_SIZES}")
    sq = _square(img)
    frames = [sq.resize((s, s), Image.Resampling.LANCZOS) for s in chosen]
    p = Path(dst)
    p.parent.mkdir(parents=True, exist_ok=True)
    # the largest frame is the base image; the rest are given explicitly so each size is resampled from the
    # full-resolution source rather than from a smaller frame
    frames[-1].save(p, "ICO", sizes=[(s, s) for s in chosen], append_images=frames[:-1])
    return p


def ico_frames(src: str | os.PathLike) -> list[Image.Image]:
    data = Path(src).read_bytes()
    ico = IcoImagePlugin.IcoFile(io.BytesIO(data))
    frames = []
    for size in sorted(ico.sizes(), key=lambda s: s[0] * s[1]):
        im = ico.getimage(size)
        im.load()
        frames.append(im.convert("RGBA"))
    return frames


def ico_to_png(src: str | os.PathLike, dst: str | os.PathLike) -> Path:
    frames = ico_frames(src)
    if not frames:
        raise ValueError("the icon has no images")
    largest = max(frames, key=lambda f: f.width * f.height)
    p = Path(dst)
    p.parent.mkdir(parents=True, exist_ok=True)
    largest.save(p, "PNG")
    return p
