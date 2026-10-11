"""ShareX ``.sxie`` image effect packages and ShareX-format preset JSON.

A ``.sxie`` file (ShareX ``ImageEffectPackager``) is a zip holding:

* ``Config.json`` - the ``ImageEffectPreset`` serialized by Json.NET: PascalCase properties, each
  effect tagged ``"$type": "<ClassName>"`` (ShareX 16-18 use a known-types binder that writes the bare
  class name; older builds wrote ``"ShareX.ImageEffectsLib.<Name>, ShareX.ImageEffectsLib"``, which is
  accepted on import too). Colours are .NET ``ColorConverter`` strings (``"Black"``, ``"R, G, B"``,
  ``"A, R, G, B"``), Point/Size ``"x, y"``, Padding ``"left, top, right, bottom"``, enums by name,
  flags as ``"Top, Bottom, Left, Right"``, fonts as ``"Arial, 11.25pt"``, gradients as
  ``{"Type": ..., "Colors": [{"Color": ..., "Location": 0.0}]}``.
* the image assets, stored as ``<AssetsFolderName>/<file>``. ShareX packages the folder
  ``%ShareXImageEffects%\\<PresetName>`` and the effects reference their images as
  ``%ShareXImageEffects%\\<PresetName>\\<file>``; on import the images are extracted into the
  ShareX image effects folder, so those references resolve again.

:func:`import_sxie` extracts the images into ``assets_dir`` and rewrites the effects' file paths to the
extracted copies (absolute paths); :func:`export_sxie` copies every referenced image into the package
and writes the ``%ShareXImageEffects%`` form ShareX expects.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .base import EFFECTS, ImageEffect, ImageEffectPreset, load_all
from .common import (IMAGE_EXTENSIONS, GradientInfo, GradientStop, color_from_dotnet, color_to_dotnet,
                     expand_path, image_effects_dir)

CONFIG_FILE_NAME = "Config.json"
SHAREX_NAMESPACE = "ShareX.ImageEffectsLib"
FOLDER_VARIABLE = "%ShareXImageEffects%"
PARTICLE_EXTENSIONS = (".png", ".jpg")
MAX_UNCOMPRESSED = 100_000_000  # ShareX's extraction limit


def sharex_property_name(f: dataclasses.Field) -> str:
    """ShareX's property name for a parameter field (``sharex`` metadata, else PascalCase)."""
    override = f.metadata.get("sharex") if f.metadata else ""
    if override:
        return override
    return "".join(part[:1].upper() + part[1:] for part in f.name.split("_"))


def _type_name(value: str) -> str:
    """``"ShareX.ImageEffectsLib.Canvas, ShareX.ImageEffectsLib"`` / ``"Canvas"`` -> ``"Canvas"``."""
    return value.split(",")[0].strip().rsplit(".", 1)[-1]


# --------------------------------------------------------------------------- value conversion

def _ints_to_text(values: list[int], n: int) -> str:
    vals = [int(v) for v in (values or [])][:n]
    vals += [0] * (n - len(vals))
    return ", ".join(str(v) for v in vals)


def _text_to_ints(value: Any, n: int) -> list[int] | None:
    if isinstance(value, (list, tuple)):
        parts = list(value)
    elif isinstance(value, dict):  # Json.NET without the type converter: {"X": 1, "Y": 2} etc.
        keys = {2: (("X", "Width"), ("Y", "Height")), 4: (("Left",), ("Top",), ("Right",), ("Bottom",))}[n]
        parts = []
        for alts in keys:
            parts.append(next((value[k] for k in alts if k in value), 0))
    elif isinstance(value, str):
        parts = [p for p in value.split(",") if p.strip()]
        if n == 4 and len(parts) == 1:  # Padding "5" (All)
            parts = parts * 4
    else:
        return None
    try:
        out = [int(float(p)) for p in parts][:n]
    except (TypeError, ValueError):
        return None
    return out + [0] * (n - len(out))


def gradient_to_sharex(g: GradientInfo) -> dict:
    return {"Type": g.type, "Colors": [{"Color": color_to_dotnet(s.color), "Location": float(s.location)}
                                       for s in g.colors]}


def gradient_from_sharex(d: Any) -> GradientInfo | None:
    if not isinstance(d, dict):
        return None
    stops = []
    for s in d.get("Colors") or []:
        if isinstance(s, dict):
            loc = s.get("Location", 0.0)
            stops.append(GradientStop(color_from_dotnet(str(s.get("Color", "Black"))),
                                      float(loc) if isinstance(loc, (int, float)) else 0.0))
    return GradientInfo(str(d.get("Type", "Vertical")), stops)


def _to_sharex_value(f: dataclasses.Field, value: Any) -> Any:
    kind = f.metadata.get("kind", "") if f.metadata else ""
    if kind == "color":
        return color_to_dotnet(value)
    if kind in ("point", "size"):
        return _ints_to_text(value, 2)
    if kind == "padding":
        return _ints_to_text(value, 4)
    if kind == "gradient":
        return gradient_to_sharex(value)
    if isinstance(value, bool):
        return value
    if f.type in ("float", float):
        return float(value)
    return value


def _from_sharex_value(f: dataclasses.Field, value: Any) -> tuple[bool, Any]:
    """(ok, converted) for one ShareX property value."""
    kind = f.metadata.get("kind", "") if f.metadata else ""
    ftype = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
    if kind == "color":
        return (True, color_from_dotnet(str(value))) if isinstance(value, str) else (False, None)
    if kind in ("point", "size"):
        v = _text_to_ints(value, 2)
        return (v is not None, v)
    if kind == "padding":
        v = _text_to_ints(value, 4)
        return (v is not None, v)
    if kind == "gradient":
        g = gradient_from_sharex(value)
        return (g is not None, g)
    if ftype == "bool":
        return (isinstance(value, bool), value)
    if ftype == "int":
        if isinstance(value, bool):
            return False, None
        if isinstance(value, int):
            return True, value
        if isinstance(value, float) and value.is_integer():
            return True, int(value)
        return False, None
    if ftype == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True, float(value)
        return False, None
    if ftype == "str":
        if isinstance(value, int) and not isinstance(value, bool) and kind in ("choice", "flags"):
            return False, None  # enum by number: unknown mapping, keep the default
        return (isinstance(value, str), value)
    return True, value


def effect_to_sharex(e: ImageEffect, long_type_names: bool = False) -> dict:
    name = e.type_name
    out: dict[str, Any] = {"$type": f"{SHAREX_NAMESPACE}.{name}, {SHAREX_NAMESPACE}" if long_type_names else name}
    for f in e.params():
        out[sharex_property_name(f)] = _to_sharex_value(f, getattr(e, f.name))
    out["Enabled"] = bool(e.enabled)
    if e.name:
        out["Name"] = e.name
    return out


def effect_from_sharex(d: dict) -> ImageEffect | None:
    """One ShareX effect object -> ImageEffect, or None for an unknown ``$type``."""
    load_all()
    cls = EFFECTS.get(_type_name(str(d.get("$type", ""))))
    if cls is None:
        return None
    obj = cls()
    by_name = {sharex_property_name(f).lower(): f for f in obj.params()}
    for key, value in d.items():
        if key.startswith("$"):
            continue
        low = key.lower()
        if low == "enabled":
            if isinstance(value, bool):
                obj.enabled = value
            continue
        if low == "name":
            if isinstance(value, str):
                obj.name = value
            continue
        f = by_name.get(low)
        if f is None:
            continue
        ok, conv = _from_sharex_value(f, value)
        if ok:
            setattr(obj, f.name, conv)
    return obj


def to_sharex_dict(preset: ImageEffectPreset, long_type_names: bool = False) -> dict:
    return {"Name": preset.name, "Effects": [effect_to_sharex(e, long_type_names) for e in preset.effects]}


def to_sharex_json(preset: ImageEffectPreset, long_type_names: bool = False) -> str:
    """Serialize like ShareX's ``JsonHelpers.SerializeToString`` (indented, Json.NET layout)."""
    return json.dumps(to_sharex_dict(preset, long_type_names), indent=2, ensure_ascii=False)


def from_sharex_json(data: str | bytes | dict) -> ImageEffectPreset:
    """Parse a ShareX preset (``{"Name": ..., "Effects": [...]}``); unknown effect types are skipped.
    Our own format (``{"name": ..., "effects": [{"type": ...}]}``) is accepted as well."""
    if isinstance(data, (str, bytes)):
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data.lstrip("﻿")
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not an image effect preset")
    if "effects" in data and "Effects" not in data:
        return ImageEffectPreset.from_jsonable(data)
    effects = []
    for item in data.get("Effects") or []:
        if isinstance(item, dict):
            e = effect_from_sharex(item)
            if e is not None:
                effects.append(e)
    name = data.get("Name")
    return ImageEffectPreset(name if isinstance(name, str) else "", effects)


# --------------------------------------------------------------------------- packages

def _path_fields(e: ImageEffect) -> list[dataclasses.Field]:
    return [f for f in e.params() if (f.metadata or {}).get("kind") in ("file", "folder")]


def _safe_folder_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "ImageEffect"


def _is_image(name: str) -> bool:
    return name.lower().endswith(IMAGE_EXTENSIONS)


def export_sxie(preset: ImageEffectPreset, path: str | os.PathLike, long_type_names: bool = False) -> Path:
    """Write ``preset`` as a ShareX ``.sxie`` package, bundling every image it references."""
    path = Path(path)
    folder = _safe_folder_name(preset.name)
    entries: dict[str, Path] = {}  # zip entry name -> source file
    used: set[str] = set()

    def add_file(src: Path, sub: str = "") -> str:
        """Bundle ``src``; returns its path relative to the assets folder (Windows separators)."""
        for existing, p in entries.items():
            if p == src and existing.startswith(f"{folder}/{sub}"):
                return existing[len(folder) + 1:].replace("/", "\\")
        base, ext = os.path.splitext(src.name)
        rel = f"{sub}{src.name}"
        n = 1
        while rel.lower() in used:
            n += 1
            rel = f"{sub}{base} ({n}){ext}"
        used.add(rel.lower())
        entries[f"{folder}/{rel}"] = src
        return rel.replace("/", "\\")

    doc = to_sharex_dict(preset, long_type_names)
    for e, d in zip(preset.effects, doc["Effects"], strict=True):
        for f in _path_fields(e):
            value = getattr(e, f.name)
            local = Path(expand_path(value)) if value else None
            key = sharex_property_name(f)
            if f.metadata.get("kind") == "file" and local and local.is_file() and _is_image(local.name):
                d[key] = f"{FOLDER_VARIABLE}\\{folder}\\{add_file(local)}"
            elif f.metadata.get("kind") == "folder" and local and local.is_dir():
                sub = _safe_folder_name(local.name) + "/"
                for img in sorted(local.iterdir()):
                    if img.is_file() and img.suffix.lower() in PARTICLE_EXTENSIONS:
                        add_file(img, sub)
                d[key] = f"{FOLDER_VARIABLE}\\{folder}\\{sub.rstrip('/')}"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(CONFIG_FILE_NAME, json.dumps(doc, indent=2, ensure_ascii=False))
        for name, src in entries.items():
            zf.write(src, name)
    os.replace(tmp, path)
    return path


def _safe_target(root: Path, entry: str) -> Path | None:
    """Where a zip entry may be extracted under ``root``; None if it would escape it."""
    name = entry.replace("\\", "/")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", name):
        return None
    target = (root / Path(*pure.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def read_sxie_config(path: str | os.PathLike) -> str:
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.filename.lower() == CONFIG_FILE_NAME.lower():
                return zf.read(info).decode("utf-8-sig")
    raise ValueError(f"{path}: no {CONFIG_FILE_NAME} in package")


def import_sxie(path: str | os.PathLike, assets_dir: str | os.PathLike | None = None) -> ImageEffectPreset:
    """Read a ShareX ``.sxie`` package: extract its images into ``assets_dir`` (default: the image
    effects folder) and return the preset with its image paths pointing at the extracted copies."""
    path = Path(path)
    root = Path(assets_dir) if assets_dir is not None else image_effects_dir()
    root.mkdir(parents=True, exist_ok=True)
    extracted: dict[str, Path] = {}  # entry name (posix, lower) -> file
    config_text = None
    total = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if config_text is None and info.filename.lower() == CONFIG_FILE_NAME.lower():
                config_text = zf.read(info).decode("utf-8-sig")
                continue
            if not _is_image(info.filename):
                continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError(f"{path}: package is larger than {MAX_UNCOMPRESSED} bytes")
            target = _safe_target(root, info.filename)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted[info.filename.replace("\\", "/").lower()] = target
    if config_text is None:
        raise ValueError(f"{path}: no {CONFIG_FILE_NAME} in package")
    preset = from_sharex_json(config_text)
    if not preset.name:
        preset.name = path.stem
    for e in preset.effects:
        for f in _path_fields(e):
            value = getattr(e, f.name)
            if value:
                setattr(e, f.name, _relink(value, f.metadata.get("kind"), root, extracted))
    return preset


def _relink(value: str, kind: str, root: Path, extracted: dict[str, Path]) -> str:
    """Point a packaged reference at its extracted copy (unchanged when nothing matches)."""
    norm = value.replace("\\", "/")
    low = norm.lower()
    var = FOLDER_VARIABLE.lower()
    if var in low:
        rel = norm[low.index(var) + len(var):].strip("/")
        cand = root / rel if rel else root
        if (kind == "file" and cand.is_file()) or (kind == "folder" and cand.is_dir()):
            return str(cand)
    if kind == "file":
        for entry, target in extracted.items():
            if low.endswith("/" + entry) or low == entry:
                return str(target)
        base = low.rsplit("/", 1)[-1]
        matches = [t for entry, t in extracted.items() if entry.rsplit("/", 1)[-1] == base]
        if len(matches) == 1:
            return str(matches[0])
    else:
        tail = low.rstrip("/").rsplit("/", 1)[-1]
        for entry, target in extracted.items():
            parts = entry.split("/")
            if len(parts) >= 2 and parts[-2] == tail:
                return str(target.parent)
    return value
