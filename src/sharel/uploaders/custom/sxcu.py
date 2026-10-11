""".sxcu import and export: ShareX's custom uploader JSON, read and written field for field.

Reading follows Newtonsoft.Json as ShareX configures it: property names are case-insensitive, enums are
names (case-insensitive) or numbers, ``DestinationType`` is a flags string (``"ImageUploader, FileUploader"``)
or bitmask, a property of the wrong type is skipped, a UTF-8 BOM is fine. After reading, ShareX's
``CustomUploaderItem.CheckBackwardCompatibility`` runs: the request URL's query string moves into
Parameters, pre-13 files (``RequestType``, ``ResponseType``, ``RegexList``) are upgraded, and the
pre-13.7.1 ``$syntax$`` becomes the current ``{syntax}``.

Writing matches ``JsonHelpers.SerializeToFile(item, path, DefaultValueHandling.Ignore, NullValueHandling.Ignore)``:
the same key order, two-space indentation, CRLF line ends, and empty or irrelevant fields left out.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from .model import BODY_TYPES, DESTINATION_TYPES, METHODS, CustomUploaderItem
from .parser import host_name

#: The Version written into upgraded/new uploaders (ShareX writes its own version here).
SXCU_VERSION = "18.0.0"

KEY_ORDER = ("Version", "Name", "DestinationType", "RequestMethod", "RequestURL", "Parameters", "Headers",
             "Body", "Arguments", "FileFormName", "Data", "URL", "ThumbnailURL", "DeletionURL", "ErrorMessage")

DESTINATION_FLAGS = {name: 1 << i for i, name in enumerate(DESTINATION_TYPES)}
RESPONSE_TYPES = ("Text", "RedirectionURL", "Headers", "LocationHeader")


class SxcuError(ValueError):
    """The data is not a custom uploader (not JSON, not an object, or an unsupported version)."""


# ---- reading -------------------------------------------------------------------------------------------

def _enum(value: Any, names: Iterable[str]) -> str | None:
    names = tuple(names)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return names[value] if 0 <= value < len(names) else None
    if isinstance(value, str):
        v = value.strip()
        if v.lstrip("-").isdigit():
            return _enum(int(v), names)
        for n in names:
            if n.lower() == v.lower():
                return n
    return None


def _destination_types(value: Any) -> list[str] | None:
    """A ``CustomUploaderDestinationType`` flags value, as the list of set flags in enum order."""
    mask = 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        mask = value
    elif isinstance(value, str):
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if part.lstrip("-").isdigit():
                mask |= int(part)
                continue
            match = next((n for n in DESTINATION_FLAGS if n.lower() == part.lower()), None)
            if match is None:
                if part.lower() == "none":
                    continue
                return None  # Newtonsoft fails the property; the error handler skips it
            mask |= DESTINATION_FLAGS[match]
    elif isinstance(value, list):
        names = []
        for v in value:
            got = _destination_types(v)
            if got is None:
                return None
            names.extend(got)
        return [n for n in DESTINATION_TYPES if n in names]
    else:
        return None
    return [n for n, bit in DESTINATION_FLAGS.items() if mask & bit]


def _string(value: Any) -> str | None:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float, str)):
        return str(value)
    return None


def _string_dict(value: Any) -> dict[str, str] | None:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return None
    out: dict[str, str] = {}
    for k, v in value.items():
        s = _string(v)
        if s is None:
            return None
        out[str(k)] = s
    return out


def _decode(data: str | bytes | bytearray) -> str:
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data)
        for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
            if data.startswith(bom):
                return data[len(bom):].decode(enc)
        return data.decode("utf-8")
    return data.lstrip("\ufeff")


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as e:
        raise SxcuError(f"Not a valid custom uploader file: {e}") from e


def item_from_dict(obj: dict[str, Any], *, upgrade: bool = True) -> CustomUploaderItem:
    """One uploader from its .sxcu JSON object; ``upgrade`` runs ShareX's CheckBackwardCompatibility."""
    if not isinstance(obj, dict):
        raise SxcuError("A custom uploader must be a JSON object")
    keys = {k.lower(): k for k in obj}

    def get(name: str) -> tuple[bool, Any]:
        k = keys.get(name.lower())
        return (True, obj[k]) if k is not None else (False, None)

    item = CustomUploaderItem(version="", body="None", request_method="POST")
    legacy: dict[str, Any] = {}
    for name, attr, conv in (("Version", "version", _string), ("Name", "name", _string),
                             ("RequestURL", "request_url", _string), ("FileFormName", "file_form_name", _string),
                             ("Data", "data", _string), ("URL", "url", _string),
                             ("ThumbnailURL", "thumbnail_url", _string), ("DeletionURL", "deletion_url", _string),
                             ("ErrorMessage", "error_message", _string), ("Parameters", "parameters", _string_dict),
                             ("Headers", "headers", _string_dict), ("Arguments", "arguments", _string_dict)):
        present, raw = get(name)
        if present:
            value = conv(raw)
            if value is not None:
                setattr(item, attr, value)
    present, raw = get("DestinationType")
    if present:
        dest = _destination_types(raw)
        if dest is not None:
            item.destination_type = dest
    for name, attr, names in (("RequestMethod", "request_method", METHODS), ("Body", "body", BODY_TYPES)):
        present, raw = get(name)
        if present:
            value = _enum(raw, names)
            if value is not None:
                setattr(item, attr, value)
    # Pre-v13 fields ShareX still reads for backward compatibility.
    present, raw = get("RequestType")
    if present and (value := _enum(raw, METHODS)) is not None:
        item.request_method = value  # the private RequestType setter assigns RequestMethod
        legacy["RequestType"] = value
    present, raw = get("ResponseType")
    if present and (value := _enum(raw, RESPONSE_TYPES)) is not None:
        legacy["ResponseType"] = value
    present, raw = get("RegexList")
    if present and isinstance(raw, list):
        legacy["RegexList"] = [str(x) for x in raw if x is not None]
    if upgrade:
        check_backward_compatibility(item, legacy, has_body="body" in keys, has_method="requestmethod" in keys)
    return item


def loads_many(data: str | bytes | bytearray) -> list[CustomUploaderItem]:
    """Every uploader in ``data``: a single .sxcu object, a JSON array of them, or an UploadersConfig
    object with a ``CustomUploadersList``."""
    obj = _parse_json(_decode(data))
    if isinstance(obj, list):
        return [item_from_dict(o) for o in obj if isinstance(o, dict)]
    if isinstance(obj, dict):
        lst = next((v for k, v in obj.items() if k.lower() == "customuploaderslist"), None)
        if isinstance(lst, list) and not any(k.lower() in ("requesturl", "requestmethod") for k in obj):
            return [item_from_dict(o) for o in lst if isinstance(o, dict)]
        return [item_from_dict(obj)]
    raise SxcuError("A custom uploader must be a JSON object")


def loads(data: str | bytes | bytearray) -> CustomUploaderItem:
    """The single uploader in ``data`` (the first one if it holds several)."""
    items = loads_many(data)
    if not items:
        raise SxcuError("The file contains no custom uploader")
    return items[0]


def load(path: str | Path) -> CustomUploaderItem:
    return loads(Path(path).read_bytes())


def load_many(path: str | Path) -> list[CustomUploaderItem]:
    return loads_many(Path(path).read_bytes())


# ---- backward compatibility -------------------------------------------------------------------------------

def compare_version(a: str, b: str) -> int:
    """Helpers.CompareVersion: dotted numeric versions, missing parts count as 0."""
    def parts(v: str) -> list[int]:
        out = []
        for p in v.strip().split("."):
            m = re.match(r"\d+", p.strip())
            out.append(int(m.group()) if m else 0)
        return out
    pa, pb = parts(a), parts(b)
    width = max(len(pa), len(pb))
    pa += [0] * (width - len(pa))
    pb += [0] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def _parse_query_string(query: str) -> list[tuple[str | None, str]]:
    """HttpUtility.ParseQueryString: (key, value) pairs; a part without ``=`` has key None."""
    pairs: list[tuple[str | None, str]] = []
    for part in query.split("&"):
        if part == "":
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            pairs.append((unquote_plus(k), unquote_plus(v)))
        else:
            pairs.append((None, unquote_plus(part)))
    return pairs


def _check_request_url(item: CustomUploaderItem) -> None:
    """Move the request URL's query string into Parameters (existing parameters win)."""
    url = item.request_url
    idx = url.find("?") if url else -1
    if idx < 0 or idx + 1 >= len(url):
        return
    pairs = _parse_query_string(url[idx + 1:])
    if not pairs:
        return
    params = dict(item.parameters)
    grouped: dict[str, list[str]] = {}
    for k, v in pairs:
        if k is None:
            if v not in params:
                params[v] = ""
        else:
            grouped.setdefault(k, []).append(v)
    for k, values in grouped.items():
        if k not in params:
            params[k] = ",".join(values)  # NameValueCollection joins repeated keys with ","
    item.parameters = params
    item.request_url = url[:idx]


def _escape_new(text: str, chars: str = "{}|\\") -> str:
    return "".join("\\" + c if c in chars else c for c in text)


def _convert_old_block(content: str, regex_list: list[str] | None) -> str:
    """The inside of an old ``$...$`` block in the new syntax (``|`` stays a parameter separator)."""
    if regex_list is not None and content.lower().startswith("regex:"):
        # Old: $regex:N$ / $regex:N|group$ / $regex:N,group$ referencing RegexList[N-1].
        rest = content[6:]
        m = re.match(r"(\d+)(?:([|,])(.*))?$", rest, re.S)
        if m and 1 <= int(m.group(1)) <= len(regex_list):
            pattern = _escape_new(regex_list[int(m.group(1)) - 1])
            group = m.group(3)
            if m.group(2) and group:
                return "regex:" + pattern + "|" + _escape_new(group)
            return "regex:" + pattern
    return _escape_new(content, "{}\\")


def migrate_old_syntax(text: str, regex_list: list[str] | None = None) -> str:
    """Pre-13.7.1 ``$function:args$`` syntax to the current ``{function:args}``.

    ShareX's MigrateOldSyntax with two of its bugs fixed: an old ``\\x`` escape keeps ``x`` (ShareX drops
    both characters), and literal ``{ } | \\`` outside a function are escaped (ShareX leaves ``|`` bare,
    which the new parser treats as the end of the text). With ``regex_list`` (the old RegexList), old
    ``$regex:N|group$`` references become self-contained ``{regex:pattern|group}`` calls.
    """
    if not text:
        return text
    out: list[str] = []
    block: list[str] | None = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n:
                lit = text[i + 1]
                if block is None:
                    out.append(_escape_new(lit))
                else:
                    block.append(lit)
            i += 2
            continue
        if c == "$":
            if block is None:
                block = []
            else:
                out.append("{" + _convert_old_block("".join(block), regex_list) + "}")
                block = None
            i += 1
            continue
        if block is None:
            out.append(_escape_new(c))
        else:
            block.append(c)
        i += 1
    # An unterminated $block produced nothing in the old parser.
    return "".join(out)


def _looks_current(item: CustomUploaderItem, legacy: dict[str, Any], has_body: bool, has_method: bool) -> bool:
    """A file without Version that is plainly written for the current syntax (hand-written .sxcu files
    often leave Version out). ShareX would treat it as pre-12.3.1 and break its ``{...}`` syntax."""
    if legacy:
        return False
    fields = [item.request_url, item.url, item.thumbnail_url, item.deletion_url, item.error_message,
              *item.parameters.values(), *item.headers.values(), *item.arguments.values()]
    uses_old = any("$" in f for f in fields if f)
    uses_new = any(re.search(r"(?<!\\)\{[A-Za-z]+[:}]", f) for f in fields if f)
    if uses_old and not uses_new:
        return False
    return has_body or has_method or uses_new


def check_backward_compatibility(item: CustomUploaderItem, legacy: dict[str, Any] | None = None, *,
                                 has_body: bool = True, has_method: bool = True) -> CustomUploaderItem:
    """CustomUploaderItem.CheckBackwardCompatibility (ShareX 14+), applied in place."""
    legacy = legacy or {}
    _check_request_url(item)
    version = item.version
    if not version and _looks_current(item, legacy, has_body, has_method):
        version = item.version = SXCU_VERSION
    if not version or compare_version(version, "12.3.1") <= 0:
        if item.request_method == "POST":
            item.body = "MultipartFormData"
        else:
            item.body = "None"
            if item.arguments:
                params = dict(item.parameters)
                for k, v in item.arguments.items():
                    params.setdefault(k, v)
                item.parameters = params
                item.arguments = {}
        response_type = legacy.get("ResponseType", "Text")
        if response_type == "RedirectionURL":
            if not item.url:
                item.url = "$responseurl$"
            item.url = item.url.replace("$response$", "$responseurl$")
            item.thumbnail_url = item.thumbnail_url.replace("$response$", "$responseurl$")
            item.deletion_url = item.deletion_url.replace("$response$", "$responseurl$")
        elif response_type == "Headers":
            item.url = "Response type option is deprecated, please use \\$header:header_name\\$ syntax instead."
        elif response_type == "LocationHeader":
            if not item.url:
                item.url = "$header:Location$"
            item.url = item.url.replace("$response$", "$header:Location$")
            item.thumbnail_url = item.thumbnail_url.replace("$response$", "$header:Location$")
            item.deletion_url = item.deletion_url.replace("$response$", "$header:Location$")
        version = item.version = "13.7.1"
    if compare_version(version, "13.7.1") <= 0:
        regex_list = legacy.get("RegexList")
        item.request_url = migrate_old_syntax(item.request_url)
        item.parameters = {k: migrate_old_syntax(v) for k, v in item.parameters.items()}
        item.headers = {k: migrate_old_syntax(v) for k, v in item.headers.items()}
        item.arguments = {k: migrate_old_syntax(v) for k, v in item.arguments.items()}
        if item.data:
            item.data = re.sub(r"\$input\$", "{input}", item.data, flags=re.I)
            item.data = re.sub(r"\$filename\$", "{filename}", item.data, flags=re.I)
        item.url = migrate_old_syntax(item.url, regex_list)
        item.thumbnail_url = migrate_old_syntax(item.thumbnail_url, regex_list)
        item.deletion_url = migrate_old_syntax(item.deletion_url, regex_list)
        item.error_message = migrate_old_syntax(item.error_message, regex_list)
        item.version = SXCU_VERSION
    return item


# ---- writing -------------------------------------------------------------------------------------------

def item_to_dict(item: CustomUploaderItem) -> dict[str, Any]:
    """The .sxcu JSON object, leaving out what ShareX leaves out (ShouldSerialize* and default values)."""
    out: dict[str, Any] = {}
    if item.version:
        out["Version"] = item.version
    if item.name and item.name != host_name(item.request_url):
        out["Name"] = item.name
    dest = [n for n in DESTINATION_TYPES if n in item.destination_type]
    if dest:
        out["DestinationType"] = ", ".join(dest)
    out["RequestMethod"] = item.request_method or "POST"
    if item.request_url:
        out["RequestURL"] = item.request_url
    if item.parameters:
        out["Parameters"] = dict(item.parameters)
    if item.headers:
        out["Headers"] = dict(item.headers)
    if item.body and item.body != "None":
        out["Body"] = item.body
    if item.body in ("MultipartFormData", "FormURLEncoded") and item.arguments:
        out["Arguments"] = dict(item.arguments)
    if item.body == "MultipartFormData" and item.file_form_name:
        out["FileFormName"] = item.file_form_name
    if item.body in ("JSON", "XML") and item.data:
        out["Data"] = item.data
    for key, attr in (("URL", "url"), ("ThumbnailURL", "thumbnail_url"), ("DeletionURL", "deletion_url"),
                      ("ErrorMessage", "error_message")):
        value = getattr(item, attr)
        if value:
            out[key] = value
    return out


def dumps(item: CustomUploaderItem) -> str:
    """The .sxcu text (Newtonsoft Formatting.Indented, as ShareX writes it on Windows: CRLF line ends)."""
    return json.dumps(item_to_dict(item), indent=2, ensure_ascii=False).replace("\n", "\r\n")


def dump(item: CustomUploaderItem, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(dumps(item), encoding="utf-8", newline="")
    return p


def file_name(item: CustomUploaderItem) -> str:
    """CustomUploaderItem.GetFileName: the display name plus ``.sxcu``, safe as a file name."""
    name = item.display_name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) + ".sxcu"


def dump_all(items: Iterable[CustomUploaderItem], folder: str | Path) -> list[Path]:
    """Export every uploader into ``folder`` (ShareX's "Export all")."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return [dump(it, folder / file_name(it)) for it in items]
