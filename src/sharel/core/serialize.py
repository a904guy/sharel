"""Dataclass <-> JSON, tolerant of missing and unknown keys (configs survive upgrades).

Enums are written by name, IntFlags as a list of names, Paths as strings. Loading uses the
dataclass's type hints, so nested dataclasses, ``list[X]``, ``dict[str, X]`` and ``X | None``
come back as the right types.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import types
import typing
from enum import Enum, IntFlag
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_hints_cache: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _hints_cache:
        _hints_cache[cls] = typing.get_type_hints(cls)
    return _hints_cache[cls]


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in dataclasses.fields(obj):
            if f.metadata.get("transient"):
                continue
            out[f.name] = to_jsonable(getattr(obj, f.name))
        return out
    if isinstance(obj, IntFlag):
        return [m.name for m in type(obj) if m.value and m in obj]
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        import base64
        return base64.b64encode(obj).decode()
    return obj


def _convert(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        for a in args:
            try:
                return _convert(a, value)
            except (TypeError, ValueError, KeyError):
                continue
        return value
    if origin in (list, tuple, set):
        (arg,) = typing.get_args(tp)[:1] or (Any,)
        seq = [_convert(arg, v) for v in (value or [])]
        return origin(seq) if origin is not list else seq
    if origin is dict:
        _k, varg = typing.get_args(tp) or (str, Any)
        return {k: _convert(varg, v) for k, v in (value or {}).items()}
    if tp is Any or tp is object:
        return value
    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp):
            return from_jsonable(tp, value)
        if issubclass(tp, IntFlag):
            if isinstance(value, int):
                return tp(value)
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            result = tp(0)
            for name in value:
                if name in tp.__members__:
                    result |= tp[name]
            return result
        if issubclass(tp, Enum):
            if isinstance(value, tp):
                return value
            if value in tp.__members__:
                return tp[value]
            return tp(value)
        if tp is Path:
            return Path(value)
        if tp is float and isinstance(value, (int, float)):
            return float(value)
        if tp is bool and isinstance(value, bool):
            return value
        if tp in (int, str, float, bool):
            if not isinstance(value, tp):
                raise TypeError(f"expected {tp.__name__}, got {type(value).__name__}")
            return value
    return value


def from_jsonable(cls: type[T], data: Any) -> T:
    if not isinstance(data, dict):
        return cls()
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data or not f.init:
            continue
        try:
            kwargs[f.name] = _convert(hints.get(f.name, Any), data[f.name])
        except (TypeError, ValueError, KeyError):
            continue  # a bad value falls back to the default rather than losing the whole file
    obj = cls(**kwargs)
    post = getattr(obj, "__post_load__", None)
    if post:
        post()
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), indent=2, ensure_ascii=False)


def loads(cls: type[T], text: str) -> T:
    return from_jsonable(cls, json.loads(text))


def save_json(obj: Any, path: str | os.PathLike, backup: bool = True) -> None:
    """Atomic write (tmp + rename), keeping the previous file as ``.bak`` like ShareX."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(obj)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if backup and path.exists():
            try:
                os.replace(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(cls: type[T], path: str | os.PathLike) -> T:
    path = Path(path)
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        try:
            return loads(cls, candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return cls()


def clone(obj: T) -> T:
    return from_jsonable(type(obj), json.loads(dumps(obj)))
