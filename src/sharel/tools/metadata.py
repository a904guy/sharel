"""Metadata viewer and stripper (ShareX "Metadata" / "Strip metadata" tools).

API
---
``read_metadata(path) -> Metadata``
    ``.file``  ``FileMetadata``: path, name, size, modified/accessed/changed (and created when the filesystem
               records it) as aware datetimes, MIME type and its description (Gio content type).
    ``.image`` ``ImageMetadata | None`` (None for non-images): format, mode, width/height, frame count, DPI,
               ``exif`` (list of ``ExifEntry(group, tag_id, name, value)`` for the IFD0/Exif/GPS/Interop IFDs,
               human-readable values), ``gps`` (raw GPS tag dict) + ``latitude``/``longitude``/``altitude``
               in decimal degrees/metres, ``orientation``, ``text`` (PNG tEXt/zTXt/iTXt chunks, GIF comment),
               ``xmp`` (XMP packet text), ``icc_profile`` presence + size + description.
    ``.rows()`` flat ``(section, key, value)`` rows for a table view.
``strip_metadata(src, dst=None, keep_icc_profile=True, jpeg_quality=95) -> Path``
    Re-encode the image from its pixels only, with the EXIF orientation applied first (so a rotated photo still
    looks the same), dropping EXIF, XMP, IPTC, comments and PNG/GIF text chunks. The ICC colour profile is kept
    by default, like ShareX's stripper, so colours do not change. Animated GIF/PNG/WebP keep their frames,
    per-frame durations and loop count. ``dst=None`` overwrites ``src`` (via a temporary file + rename).
"""
from __future__ import annotations

import datetime as _dt
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ExifTags, Image, ImageOps, ImageSequence, TiffImagePlugin, UnidentifiedImageError

__all__ = ["ExifEntry", "FileMetadata", "ImageMetadata", "Metadata", "read_metadata", "strip_metadata"]


@dataclass
class ExifEntry:
    group: str  # "Image", "Exif", "GPS", "Interop"
    tag_id: int
    name: str
    value: str


@dataclass
class FileMetadata:
    path: str
    name: str
    size: int
    modified: _dt.datetime
    accessed: _dt.datetime
    changed: _dt.datetime
    created: _dt.datetime | None = None
    mime_type: str = ""
    mime_description: str = ""


@dataclass
class ImageMetadata:
    format: str
    mode: str
    width: int
    height: int
    frames: int = 1
    dpi: tuple[float, float] | None = None
    orientation: int | None = None
    exif: list[ExifEntry] = field(default_factory=list)
    gps: dict[str, object] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    text: dict[str, str] = field(default_factory=dict)
    xmp: str = ""
    icc_profile: bool = False
    icc_profile_size: int = 0
    icc_description: str = ""


@dataclass
class Metadata:
    file: FileMetadata
    image: ImageMetadata | None = None

    def rows(self) -> list[tuple[str, str, str]]:
        f = self.file
        out = [("File", "Name", f.name), ("File", "Path", f.path), ("File", "Size", f"{f.size} bytes"),
               ("File", "Type", f.mime_type + (f" ({f.mime_description})" if f.mime_description else "")),
               ("File", "Modified", f.modified.isoformat(sep=" ", timespec="seconds")),
               ("File", "Accessed", f.accessed.isoformat(sep=" ", timespec="seconds")),
               ("File", "Changed", f.changed.isoformat(sep=" ", timespec="seconds"))]
        if f.created:
            out.append(("File", "Created", f.created.isoformat(sep=" ", timespec="seconds")))
        im = self.image
        if im is None:
            return out
        out += [("Image", "Format", im.format), ("Image", "Dimensions", f"{im.width} x {im.height}"),
                ("Image", "Mode", im.mode)]
        if im.frames > 1:
            out.append(("Image", "Frames", str(im.frames)))
        if im.dpi:
            out.append(("Image", "DPI", f"{im.dpi[0]:g} x {im.dpi[1]:g}"))
        if im.icc_profile:
            out.append(("Image", "ICC profile", im.icc_description or f"{im.icc_profile_size} bytes"))
        out += [(f"EXIF ({e.group})", e.name, e.value) for e in im.exif]
        if im.latitude is not None and im.longitude is not None:
            out.append(("GPS", "Position", f"{im.latitude:.6f}, {im.longitude:.6f}"))
        out += [("Text", k, v) for k, v in im.text.items()]
        if im.xmp:
            out.append(("XMP", "Packet", im.xmp))
        return out


# ---- file -----------------------------------------------------------------------------------


def _ts(v: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(v, _dt.UTC).astimezone()


def _gio_info(path: str) -> tuple[str, str, _dt.datetime | None]:
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return "", "", None
    try:
        info = Gio.File.new_for_path(path).query_info("standard::content-type,time::created",
                                                      Gio.FileQueryInfoFlags.NONE, None)
    except Exception:  # GLib.Error: unreadable file etc.
        return "", "", None
    ctype = info.get_content_type() or ""
    mime = Gio.content_type_get_mime_type(ctype) or ctype
    desc = Gio.content_type_get_description(ctype) or ""
    created = None
    if info.has_attribute("time::created"):
        created = _ts(info.get_attribute_uint64("time::created"))
    return mime, desc, created


def _file_metadata(path: str) -> FileMetadata:
    st = os.stat(path)
    mime, desc, created = _gio_info(path)
    birth = getattr(st, "st_birthtime", None)
    if created is None and birth:
        created = _ts(birth)
    return FileMetadata(path=os.path.abspath(path), name=os.path.basename(path), size=st.st_size,
                        modified=_ts(st.st_mtime), accessed=_ts(st.st_atime), changed=_ts(st.st_ctime),
                        created=created, mime_type=mime, mime_description=desc)


# ---- image ----------------------------------------------------------------------------------


def _fmt_value(v: object) -> str:
    if isinstance(v, bytes):
        stripped = v.rstrip(b"\x00")
        try:
            s = stripped.decode("ascii")
            if s.isprintable():
                return s
        except UnicodeDecodeError:
            pass
        return f"<{len(v)} bytes: {v[:16].hex(' ')}{' ...' if len(v) > 16 else ''}>"
    if isinstance(v, TiffImagePlugin.IFDRational):
        try:
            f = float(v)
        except ZeroDivisionError:
            return "nan"
        return f"{f:g}"
    if isinstance(v, tuple):
        return ", ".join(_fmt_value(x) for x in v)
    if isinstance(v, str):
        return v.rstrip("\x00")
    return str(v)


def _to_float(v: object) -> float:
    return float(v)  # type: ignore[arg-type]


def _dms(value: object, ref: object) -> float | None:
    try:
        d, m, s = (_to_float(x) for x in value)  # type: ignore[union-attr]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60 + s / 3600
    r = ref.decode() if isinstance(ref, bytes) else str(ref or "")
    return -deg if r.strip().upper() in ("S", "W") else deg


_GROUPS = (("Exif", ExifTags.IFD.Exif, ExifTags.TAGS), ("GPS", ExifTags.IFD.GPSInfo, ExifTags.GPSTAGS),
           ("Interop", ExifTags.IFD.Interop, ExifTags.TAGS))
_IFD_POINTERS = {int(ExifTags.IFD.Exif), int(ExifTags.IFD.GPSInfo), int(ExifTags.IFD.Interop)}


def _image_metadata(img: Image.Image) -> ImageMetadata:
    md = ImageMetadata(format=img.format or "", mode=img.mode, width=img.width, height=img.height,
                       frames=getattr(img, "n_frames", 1))
    dpi = img.info.get("dpi")
    if dpi:
        try:
            md.dpi = (float(dpi[0]), float(dpi[1]))
        except (TypeError, ValueError, IndexError):
            pass
    exif = img.getexif()
    for tag, val in exif.items():
        if tag in _IFD_POINTERS:
            continue
        md.exif.append(ExifEntry("Image", tag, ExifTags.TAGS.get(tag, f"Tag 0x{tag:04X}"), _fmt_value(val)))
    orientation = exif.get(ExifTags.Base.Orientation)
    if orientation:
        md.orientation = int(orientation)
    for group, ifd_id, names in _GROUPS:
        try:
            ifd = exif.get_ifd(ifd_id)
        except (KeyError, ValueError, TypeError):
            continue
        for tag, val in ifd.items():
            name = names.get(tag, f"Tag 0x{tag:04X}")
            md.exif.append(ExifEntry(group, tag, name, _fmt_value(val)))
            if group == "GPS":
                md.gps[name] = val
    if md.gps:
        if "GPSLatitude" in md.gps:
            md.latitude = _dms(md.gps["GPSLatitude"], md.gps.get("GPSLatitudeRef"))
        if "GPSLongitude" in md.gps:
            md.longitude = _dms(md.gps["GPSLongitude"], md.gps.get("GPSLongitudeRef"))
        if "GPSAltitude" in md.gps:
            try:
                alt = _to_float(md.gps["GPSAltitude"])
                ref = md.gps.get("GPSAltitudeRef", 0)
                ref_v = ref[0] if isinstance(ref, bytes) and ref else ref
                md.altitude = -alt if ref_v == 1 else alt
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    # text chunks (PNG tEXt/zTXt/iTXt via .text), GIF/JPEG comments
    text = getattr(img, "text", None)
    if isinstance(text, dict):
        for k, v in text.items():
            if k == "XML:com.adobe.xmp":
                continue
            md.text[str(k)] = _fmt_value(v)
    comment = img.info.get("comment")
    if comment:
        md.text["Comment"] = _fmt_value(comment)
    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp") or (text or {}).get("XML:com.adobe.xmp")
    if xmp:
        md.xmp = xmp.decode("utf-8", "replace") if isinstance(xmp, bytes) else str(xmp)
    icc = img.info.get("icc_profile")
    if icc:
        md.icc_profile = True
        md.icc_profile_size = len(icc)
        try:
            import io

            from PIL import ImageCms

            md.icc_description = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
        except Exception:  # littlecms missing or unparsable profile
            md.icc_description = ""
    return md


def read_metadata(path: str | os.PathLike) -> Metadata:
    p = os.fspath(path)
    meta = Metadata(_file_metadata(p))
    try:
        with Image.open(p) as img:
            meta.image = _image_metadata(img)
    except (UnidentifiedImageError, OSError):
        meta.image = None
    return meta


# ---- strip ----------------------------------------------------------------------------------


def _clean_copy(im: Image.Image) -> Image.Image:
    """Pixels only: a fresh image object carries no ``info`` (so Pillow can't write EXIF/XMP/text back)."""
    mode = im.mode
    out = Image.frombytes(mode, im.size, im.tobytes())
    if mode == "P":
        pal = im.getpalette()
        if pal:
            out.putpalette(pal)
        if "transparency" in im.info:
            out.info["transparency"] = im.info["transparency"]
    return out


def strip_metadata(src: str | os.PathLike, dst: str | os.PathLike | None = None, keep_icc_profile: bool = True,
                   jpeg_quality: int = 95) -> Path:
    src_p = Path(src)
    dst_p = Path(dst) if dst is not None else src_p
    with Image.open(src_p) as img:
        fmt = (img.format or "PNG").upper()
        icc = img.info.get("icc_profile") if keep_icc_profile else None
        n_frames = getattr(img, "n_frames", 1)
        save_kw: dict = {}
        if icc:
            save_kw["icc_profile"] = icc
        if n_frames > 1 and fmt in ("GIF", "PNG", "WEBP"):
            frames, durations = [], []
            for fr in ImageSequence.Iterator(img):
                durations.append(int(fr.info.get("duration", img.info.get("duration", 100)) or 100))
                frames.append(_clean_copy(ImageOps.exif_transpose(fr.copy()) if fmt != "GIF" else fr.convert("RGBA")))
            save_kw.update(save_all=True, append_images=frames[1:], duration=durations,
                           loop=int(img.info.get("loop", 0)))
            if fmt == "GIF":
                save_kw["disposal"] = 2
            if fmt == "WEBP":
                save_kw["lossless"] = True
            first = frames[0]
        else:
            first = _clean_copy(ImageOps.exif_transpose(img))
            if fmt == "JPEG":
                if first.mode not in ("RGB", "L", "CMYK"):
                    first = first.convert("RGB")
                save_kw.update(quality=jpeg_quality, optimize=True)
                if img.info.get("progressive") or img.info.get("progression"):
                    save_kw["progressive"] = True
            elif fmt == "WEBP":
                save_kw["quality"] = jpeg_quality
            elif fmt == "TIFF":
                save_kw["compression"] = "tiff_lzw"
        dpi = img.info.get("dpi")
        if dpi and fmt in ("JPEG", "PNG", "TIFF"):
            save_kw["dpi"] = dpi  # resolution is not metadata in the privacy sense; keep print size
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".strip-", suffix=dst_p.suffix or ".img", dir=dst_p.parent)
    os.close(fd)
    try:
        first.save(tmp, fmt, **save_kw)
        os.replace(tmp, dst_p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return dst_p
