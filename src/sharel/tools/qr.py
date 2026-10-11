"""QR code tool (ShareX QRCode window): encode text to a QR image and decode codes from images.

Encoding uses the ``qrcode`` package; decoding runs the ``zbarimg`` CLI (zbar), which also reads the other
barcode symbologies zbar supports (EAN, Code128, ...), like ShareX's ZXing-based decoder.

API
---
``encode(text, size=256, border=4, error_correction="M", fill=(0,0,0), back=(255,255,255)) -> PIL.Image``
    an RGB image exactly ``size``×``size`` px with whole-pixel modules, centred (``size=0`` = natural size of
    10 px per module). ``error_correction`` is "L", "M", "Q" or "H" (7/15/25/30 % recovery).
``decode_image(img, timeout=30, cancel=None) -> list[str]``     every code found ([] when there is none).
``decode_file(path, timeout=30, cancel=None) -> list[str]``      the same for an image file.
``decode_symbols(img, ...) -> list[Symbol]``                     with the symbol type and outline polygon.

Unicode: zbar guesses the character set of QR byte-mode data and can mangle UTF-8, so the payload is read in
``-Sbinary`` mode (raw bytes, decoded as UTF-8 by us). zbar's binary mode prints several codes without any
separator, so when an image holds more than one code each code is cropped (from the polygons of a first
``--xml`` pass) and read on its own. Text that isn't valid UTF-8 falls back to zbar's own charset conversion.
"""
from __future__ import annotations

import base64
import os
import tempfile
import threading
from dataclasses import dataclass, field

import qrcode
from lxml import etree
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

from ._common import ToolError, require_binary, run_cancelable

__all__ = ["Symbol", "decode_file", "decode_image", "decode_symbols", "encode"]

_EC = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
_NS = {"z": "http://zbar.sourceforge.net/2008/barcode"}


def encode(text: str, size: int = 256, border: int = 4, error_correction: str = "M",
           fill: tuple[int, int, int] = (0, 0, 0), back: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    if not text:
        raise ValueError("nothing to encode")
    try:
        ec = _EC[error_correction.upper()]
    except KeyError:
        raise ValueError(f"error correction must be one of L, M, Q, H (got {error_correction!r})") from None
    if border < 0:
        raise ValueError("border must be >= 0")
    qr = qrcode.QRCode(error_correction=ec, box_size=1, border=border)
    qr.add_data(text.encode("utf-8"))
    qr.make(fit=True)
    matrix = qr.get_matrix()  # includes the border
    n = len(matrix)
    if size <= 0:
        box = 10
        size = n * box
    else:
        box = max(1, size // n)
    side = n * box
    img = Image.new("RGB", (n, n), back)
    px = img.load()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                px[x, y] = fill
    img = img.resize((side, side), Image.Resampling.NEAREST)
    if side == size:
        return img
    if side > size:  # requested size smaller than one pixel per module: scale down (still readable when close)
        return img.resize((size, size), Image.Resampling.NEAREST)
    out = Image.new("RGB", (size, size), back)
    off = (size - side) // 2
    out.paste(img, (off, off))
    return out


@dataclass
class Symbol:
    type: str
    data: str
    polygon: list[tuple[int, int]] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        xs = [p[0] for p in self.polygon] or [0]
        ys = [p[1] for p in self.polygon] or [0]
        return min(xs), min(ys), max(xs), max(ys)


def _prepare(img: Image.Image) -> Image.Image:
    """zbar wants an opaque image; transparent pixels become white (QR codes on transparent PNGs)."""
    if img.mode in ("RGBA", "LA", "P", "PA"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def _run_zbar(path: str, args: list[str], timeout: float, cancel: threading.Event | None) -> tuple[int, bytes]:
    exe = require_binary("zbarimg")
    proc = run_cancelable([exe, "-q", *args, path], cancel, timeout=timeout)
    if proc.returncode not in (0, 4):  # 4 = no symbol found
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise ToolError(f"zbarimg failed ({proc.returncode}): {err}")
    return proc.returncode, proc.stdout


def _parse_xml(xml: bytes) -> list[Symbol]:
    if not xml.strip():
        return []
    root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    out = []
    for sym in root.iterfind(".//z:symbol", _NS):
        data_el = sym.find("z:data", _NS)
        raw = (data_el.text or "") if data_el is not None else ""
        if data_el is not None and data_el.get("format") == "base64":
            try:
                raw = base64.b64decode(raw.strip()).decode("utf-8", "replace")
            except ValueError:
                pass
        poly_el = sym.find("z:polygon", _NS)
        pts: list[tuple[int, int]] = []
        if poly_el is not None:
            for pair in (poly_el.get("points") or "").split():
                try:
                    x, y = pair.split(",")
                    pts.append((int(x), int(y)))
                except ValueError:
                    continue
        out.append(Symbol(sym.get("type") or "", raw, pts))
    return out


def _decode_binary(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _save_temp(img: Image.Image, directory: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".png", dir=directory)
    os.close(fd)
    img.save(path, "PNG")
    return path


def decode_symbols(img: Image.Image, timeout: float = 30, cancel: threading.Event | None = None) -> list[Symbol]:
    img = _prepare(img)
    with tempfile.TemporaryDirectory(prefix="sharel-qr-") as tmp:
        path = _save_temp(img, tmp)
        rc, xml = _run_zbar(path, ["--xml"], timeout, cancel)
        if rc == 4:
            return []
        symbols = _parse_xml(xml)
        if not symbols:
            return []
        if len(symbols) == 1:
            rc, raw = _run_zbar(path, ["--raw", "-Sbinary"], timeout, cancel)
            text = _decode_binary(raw) if rc == 0 else None
            if text is not None:
                symbols[0].data = text
            return symbols
        # several codes: read each one's bytes from a crop around its outline
        for s in symbols:
            if not s.polygon:
                continue
            x0, y0, x1, y1 = s.bbox
            pad = max(8, (max(x1 - x0, y1 - y0)) // 6)
            box = (max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad + 1), min(img.height, y1 + pad + 1))
            crop_path = _save_temp(img.crop(box), tmp)
            rc, raw = _run_zbar(crop_path, ["--raw", "-Sbinary"], timeout, cancel)
            if rc == 0:
                # also confirm the crop holds just this one code before trusting its bytes
                rc2, xml2 = _run_zbar(crop_path, ["--xml"], timeout, cancel)
                if rc2 == 0 and len(_parse_xml(xml2)) == 1:
                    text = _decode_binary(raw)
                    if text is not None:
                        s.data = text
        return symbols


def decode_image(img: Image.Image, timeout: float = 30, cancel: threading.Event | None = None) -> list[str]:
    return [s.data for s in decode_symbols(img, timeout, cancel)]


def decode_file(path: str | os.PathLike, timeout: float = 30, cancel: threading.Event | None = None) -> list[str]:
    with Image.open(path) as im:
        im.load()
        return decode_image(im, timeout, cancel)
