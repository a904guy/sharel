"""The custom uploader syntax: ``{function:arg1|arg2}`` (ShareX ShareXSyntaxParser / ShareXCustomUploaderSyntaxParser).

Grammar, exactly as ShareX parses it:

* ``{name}`` calls a function with no parameters, ``{name:a|b}`` with parameters ``["a", "b"]``
  (``{name:}`` passes one empty parameter). Names are case-insensitive.
* Parameters are parsed themselves, so functions nest: ``{json:{response}|data.link}``.
  A nested function's *result* is never re-parsed.
* ``\\`` escapes the next character (``\\{ \\} \\| \\\\``); a trailing lone ``\\`` is dropped.
* Outside any function an unescaped ``}`` or ``|`` ends the text: the rest is ignored (ShareX does this).
* An empty name, an unknown name or too few parameters is an error (:class:`CustomSyntaxError`).

Functions: response, responseurl, header, json, xml, regex, input, filename, random, select,
inputbox (alias prompt), outputbox, base64.
"""
from __future__ import annotations

import base64 as _b64
import io
import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ...core.nameparser import NameParser, NameParserType


class CustomSyntaxError(ValueError):
    """A syntax error, or a function that cannot produce a value (bad JSON, bad XPath...)."""


class InvalidJSONResponse(CustomSyntaxError):
    """``{json:}`` was given text that is not JSON (ShareX's JsonReaderException)."""


@dataclass
class ResponseInfo:
    """What the response-parsing functions see (ShareX ResponseInfo)."""

    status_code: int = 0
    status_description: str = ""
    response_url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    response_text: str = ""

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def header(self, name: str) -> str | None:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return None


# ---- encoders matching ShareX's URLHelpers ---------------------------------------------------------

def url_encode(text: str | None) -> str:
    """URLHelpers.URLEncode: UTF-8, everything but ``A-Za-z0-9-._~`` as uppercase ``%XX``."""
    return quote(text or "", safe="")


_FORM_SAFE = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.!*()")


def form_encode(text: str | None) -> str:
    """System.Web.HttpUtility.UrlEncode: space as ``+``, ``-_.!*()`` kept, lowercase ``%xx``."""
    out = []
    for b in (text or "").encode("utf-8"):
        if b in _FORM_SAFE:
            out.append(chr(b))
        elif b == 0x20:
            out.append("+")
        else:
            out.append(f"%{b:02x}")
    return "".join(out)


def create_query_string(args: Mapping[str, str] | None) -> str:
    """URLHelpers.CreateQueryString: ``key=value`` pairs, a bare ``key`` for an empty value, keys as-is."""
    if not args:
        return ""
    return "&".join(k if not v else f"{k}={form_encode(v)}" for k, v in args.items())


def append_query_string(url: str, args: Mapping[str, str] | None) -> str:
    query = create_query_string(args)
    if not query:
        return url
    return url + ("&" if "?" in url else "?") + query


_URL_PREFIXES = ("http://", "https://", "ftp://", "ftps://", "file://", "//")


def fix_prefix(url: str, prefix: str = "https://") -> str:
    """URLHelpers.FixPrefix: add ``https://`` unless the URL already starts with a known scheme or ``//``."""
    if url and not url.lower().startswith(_URL_PREFIXES):
        return prefix + url
    return url


def json_encode(text: str | None) -> str:
    """URLHelpers.JSONEncode: Newtonsoft's JsonConvert.ToString without the surrounding quotes."""
    if not text:
        return text or ""
    out = []
    for ch in text:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch in "\b\f\n\r\t":
            out.append({"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
        elif o < 0x20 or o in (0x85, 0x2028, 0x2029):
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def xml_encode(text: str | None) -> str:
    """URLHelpers.XMLEncode (SecurityElement.Escape): ``& < > " '`` as entities."""
    if not text:
        return text or ""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def host_name(url: str) -> str:
    """URLHelpers.GetHostName: the host without ``www.``, or the text itself when it is not an absolute URL."""
    from urllib.parse import urlsplit
    if url:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        if parts.scheme and parts.hostname:
            host = parts.hostname
            return host[4:] if host.lower().startswith("www.") else host
    return url


# ---- name parser (ShareX runs it before the syntax parser on parameters/headers/arguments) -------------

class _UploaderNameParser(NameParser):
    """NameParser with ``%rf`` disabled: ShareX sets AllowFileRead = false because a .sxcu file may come
    from anyone, and must not be able to read local files into a request. ``%rf{path}`` becomes ""."""

    def _expand(self, token, arg, now, uses_pm):
        if token == "rf":
            return ""
        return super()._expand(token, arg, now, uses_pm)


def name_parse(text: str) -> str:
    """NameParser(NameParserType.Text) with file reads disabled (CustomUploaderItem.GetData)."""
    if not text:
        return ""
    return _UploaderNameParser(NameParserType.Text).parse(text)


def name_parse_keep_escapes(text: str) -> str:
    """EscapeHelper(KeepEscapeCharacter=true) around the name parser: ``\\\\`` and ``\\%`` are protected
    from it and kept, so the syntax parser still sees (and unescapes) them afterwards."""
    if not text:
        return ""
    reserve_bs = "\x00BS\x00"
    reserve_pct = "\x00PCT\x00"
    text = text.replace("\\\\", reserve_bs).replace("\\%", reserve_pct)
    text = name_parse(text)
    return text.replace(reserve_pct, "\\%").replace(reserve_bs, "\\\\")


# ---- the parser -------------------------------------------------------------------------------------

class SyntaxParser:
    """ShareXSyntaxParser: the ``{name:a|b}`` grammar. Subclasses supply :meth:`call_function`."""

    syntax_start = "{"
    syntax_end = "}"
    syntax_parameter_start = ":"
    syntax_parameter_delimiter = "|"
    syntax_escape = "\\"

    def parse(self, text: str | None) -> str:
        if not text:
            return ""
        result, _ = self._parse(text, False, 0)
        return result

    def _parse(self, text: str, is_function: bool, start: int) -> tuple[str, int]:
        out: list[str] = []
        escape = False
        i = start
        n = len(text)
        while i < n:
            c = text[i]
            if not escape:
                if c == self.syntax_start:
                    parsed, i = self._parse(text, True, i + 1)
                    out.append(parsed)
                    i += 1
                    continue
                if c in (self.syntax_end, self.syntax_parameter_delimiter):
                    break
                if c == self.syntax_escape:
                    escape = True
                    i += 1
                    continue
                if is_function and c == self.syntax_parameter_start:
                    parameters: list[str] = []
                    while True:
                        parsed, i = self._parse(text, False, i + 1)
                        parameters.append(parsed)
                        if not (i < n and text[i] == self.syntax_parameter_delimiter):
                            break
                    return self.call_function("".join(out), parameters) or "", i
            escape = False
            out.append(c)
            i += 1
        if is_function:
            return self.call_function("".join(out), None) or "", i
        return "".join(out), i

    def call_function(self, name: str, parameters: list[str] | None) -> str | None:
        raise NotImplementedError


@dataclass(frozen=True)
class _Function:
    name: str
    call: Callable[[CustomUploaderSyntaxParser, list[str]], str | None]
    min_parameters: int = 0
    aliases: tuple[str, ...] = ()


class _DefaultPrompts:
    """Prompt callbacks when the parser has no UploadContext: select the first option, keep defaults."""

    @staticmethod
    def prompt_input(_title: str, default: str) -> str | None:
        return default

    @staticmethod
    def prompt_select(_title: str, options: list[str]) -> str | None:
        return options[0] if options else None

    @staticmethod
    def show_output(_title: str, _text: str) -> None:
        return None


class CustomUploaderSyntaxParser(SyntaxParser):
    """ShareXCustomUploaderSyntaxParser.

    ``file_name`` / ``input_text`` feed ``{filename}`` / ``{input}``; ``response`` feeds the response
    functions (only set while parsing URL / ThumbnailURL / DeletionURL / ErrorMessage); ``url_encode``
    URL-encodes only ``{filename}`` and ``{input}``; ``use_name_parser`` runs ShareL's name parser
    (``%y``, ``%mo``...) over the text first. ``ctx`` supplies prompt_select / prompt_input / show_output.
    """

    def __init__(self, *, file_name: str | None = None, input_text: str | None = None,
                 response: ResponseInfo | None = None, url_encode: bool = False, use_name_parser: bool = False,
                 ctx: Any = None, rng: random.Random | None = None) -> None:
        self.file_name = file_name
        self.input_text = input_text
        self.response = response
        self.url_encode = url_encode
        self.use_name_parser = use_name_parser
        self.ctx = ctx if ctx is not None else _DefaultPrompts()
        self.rng = rng or random.SystemRandom()

    def parse(self, text: str | None) -> str:
        if self.use_name_parser and text:
            text = name_parse_keep_escapes(text)
        return super().parse(text)

    def call_function(self, name: str, parameters: list[str] | None) -> str | None:
        if not name:
            raise CustomSyntaxError("Function name cannot be empty.")
        fn = _FUNCTIONS.get(name.lower())
        if fn is None:
            raise CustomSyntaxError(f"Invalid function name: {name}")
        if fn.min_parameters > 0 and (parameters is None or len(parameters) < fn.min_parameters):
            raise CustomSyntaxError(f'Minimum parameter count for function "{fn.name}" is {fn.min_parameters}.')
        return fn.call(self, parameters or [])

    def require_response(self, function: str) -> ResponseInfo:
        if self.response is None:
            raise CustomSyntaxError(f"{{{function}}} can only be used where the response is parsed "
                                    "(URL, Thumbnail URL, Deletion URL, Error message).")
        return self.response


# ---- functions ---------------------------------------------------------------------------------------

def _fn_response(p: CustomUploaderSyntaxParser, _params: list[str]) -> str | None:
    return p.require_response("response").response_text


def _fn_response_url(p: CustomUploaderSyntaxParser, _params: list[str]) -> str | None:
    return p.require_response("responseurl").response_url


def _fn_header(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    return p.require_response("header").header(params[0])


def _fn_json(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    from .jsonpath import JsonPathError, select_token, token_to_string
    if len(params) > 1:
        source, path = params[0], params[1]
    else:
        source, path = p.require_response("json").response_text, params[0]
    if not source or not path:
        return None
    if not path.startswith("$."):
        path = "$." + path
    try:
        data = json.loads(source.lstrip("\ufeff"))
    except ValueError as e:
        raise InvalidJSONResponse(f"Invalid JSON: {e}") from e
    try:
        return token_to_string(select_token(data, path))
    except JsonPathError as e:
        raise CustomSyntaxError(f"JSON path {path!r}: {e}") from e


_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")


def _fn_xml(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    from lxml import etree
    if len(params) > 1:
        source, xpath = params[0], params[1]
    else:
        source, xpath = p.require_response("xml").response_text, params[0]
    if not source or not xpath:
        return None
    # The text is already decoded, so an encoding declaration is meaningless (as for .NET's StringReader).
    source = _XML_DECL.sub("", source.lstrip("\ufeff"), count=1)
    parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.parse(io.BytesIO(source.encode("utf-8")), parser).getroot()
    except etree.XMLSyntaxError as e:
        raise CustomSyntaxError(f"Invalid XML: {e}") from e
    # Prefixes declared on the root element may be used in the XPath (ShareX would need a namespace manager).
    namespaces = {k: v for k, v in (root.nsmap or {}).items() if k}
    try:
        result = root.getroottree().xpath(xpath, namespaces=namespaces)
    except etree.XPathError as e:
        raise CustomSyntaxError(f"XPath {xpath!r}: {e}") from e
    if isinstance(result, list):
        if not result:
            return None
        node = result[0]
        if isinstance(node, etree._Element):
            return node.xpath("string()")
        return str(node)
    if isinstance(result, bool):
        return "True" if result else "False"
    if isinstance(result, float):
        from .jsonpath import format_double
        return format_double(result)
    return str(result)


def _fn_regex(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    from .dotnet_regex import compile_dotnet
    group = ""
    if len(params) > 2:
        source, pattern, group = params[0], params[1], params[2]
    else:
        source, pattern = p.require_response("regex").response_text, params[0]
        if len(params) > 1:
            group = params[1]
    if not source or not pattern:
        return None
    try:
        rx = compile_dotnet(pattern)
    except re.error as e:
        raise CustomSyntaxError(f"Regex {pattern!r}: {e}") from e
    m = rx.search(source)
    if not m:
        return None
    if group:
        try:
            number = int(group.strip())
        except ValueError:
            return rx.group_value(m, group)
        return rx.group_value(m, number)
    return m.group(0)


def _fn_input(p: CustomUploaderSyntaxParser, _params: list[str]) -> str | None:
    return url_encode(p.input_text) if p.url_encode else p.input_text


def _fn_filename(p: CustomUploaderSyntaxParser, _params: list[str]) -> str | None:
    return url_encode(p.file_name) if p.url_encode else p.file_name


def _fn_random(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    return p.rng.choice(params)


def _fn_select(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    values = [v for v in params if v]
    if not values:
        return None
    return p.ctx.prompt_select("Select", values)


def _fn_inputbox(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    title = params[0] if params else "Input"
    default = params[1] if len(params) > 1 else ""
    value = p.ctx.prompt_input(title, default)
    return default if value is None else value


def _fn_outputbox(p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    if len(params) > 1:
        title, text = params[0], params[1]
    else:
        title, text = "", params[0]
    if text:
        p.ctx.show_output(title or "Output", text)
    return None


def _fn_base64(_p: CustomUploaderSyntaxParser, params: list[str]) -> str | None:
    text = params[0]
    if not text:
        return None
    return _b64.b64encode(text.encode("utf-8")).decode("ascii")


_FUNCTION_LIST = (
    _Function("response", _fn_response),
    _Function("responseurl", _fn_response_url),
    _Function("header", _fn_header, 1),
    _Function("json", _fn_json, 1),
    _Function("xml", _fn_xml, 1),
    _Function("regex", _fn_regex, 1),
    _Function("input", _fn_input),
    _Function("filename", _fn_filename),
    _Function("random", _fn_random, 2),
    _Function("select", _fn_select, 1),
    _Function("inputbox", _fn_inputbox, 0, ("prompt",)),
    _Function("outputbox", _fn_outputbox, 1),
    _Function("base64", _fn_base64, 1),
)

_FUNCTIONS: dict[str, _Function] = {}
for _f in _FUNCTION_LIST:
    _FUNCTIONS[_f.name] = _f
    for _alias in _f.aliases:
        _FUNCTIONS[_alias] = _f

FUNCTION_NAMES = tuple(f.name for f in _FUNCTION_LIST)


def escape(text: str) -> str:
    """Escape ``{ } | \\`` so ``text`` parses back to itself."""
    return re.sub(r"([{}|\\])", r"\\\1", text)


def syntax_test(expression: str, response: ResponseInfo, file_name: str = "example.png", ctx: Any = None) -> str:
    """The editor's syntax tester: ``expression`` parsed against ``response`` (errors returned as text)."""
    parser = CustomUploaderSyntaxParser(file_name=file_name, response=response, url_encode=True, ctx=ctx)
    try:
        return parser.parse(expression)
    except CustomSyntaxError as e:
        return f"Error\n{e}"
