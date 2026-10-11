"""JSONPath with Newtonsoft.Json ``JToken.SelectToken`` semantics (what ShareX's ``{json:}`` uses).

A port of Newtonsoft's ``JPath`` parser and its path filters: ``$``, ``.name``, ``['name']``,
``['a','b']``, ``[0]``, ``[0,2]``, ``[1:3]`` / ``[::2]`` slices, ``*`` / ``[*]`` wildcards, ``..`` recursive
descent, and ``[?(...)]`` filter queries with ``== != === !== < <= > >= =~ && ||`` and existence tests.

Values come from :func:`json.loads`: dict / list / str / int / float / bool / None (JSON null).
Newtonsoft enumerates a ``JObject``'s children as ``JProperty`` tokens; :class:`Prop` stands in for those
so recursive descent and queries walk the tree in the same order.

One extension over Newtonsoft: a negative array index (``[-1]``) counts from the end of the array
(Newtonsoft throws an out-of-range exception for it).
"""
from __future__ import annotations

import math
from decimal import Decimal
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any


class JsonPathError(ValueError):
    """A malformed path, or a result SelectToken refuses (several tokens, non-scalar to string)."""


@dataclass(eq=False)
class Prop:
    """A JSON object member (Newtonsoft ``JProperty``)."""

    name: str
    value: Any


_MISSING = object()


def _is_container(t: Any) -> bool:
    return isinstance(t, (dict, list, Prop))


def _children(t: Any) -> list[Any]:
    """Direct child tokens, as Newtonsoft enumerates them (objects yield their properties)."""
    if isinstance(t, dict):
        return [Prop(k, v) for k, v in t.items()]
    if isinstance(t, list):
        return list(t)
    if isinstance(t, Prop):
        return [t.value]
    return []


def _descendants(t: Any) -> Iterator[Any]:
    """Every token below ``t`` in document order (``JContainer.Descendants``)."""
    for c in _children(t):
        yield c
        if _is_container(c):
            yield from _descendants(c)


# ---- filters ------------------------------------------------------------------------------------

class _Filter:
    def run(self, root: Any, current: Iterable[Any]) -> Iterator[Any]:
        raise NotImplementedError


def _index(t: Any, index: int) -> Any:
    if isinstance(t, list):
        if index < 0:
            index += len(t)  # extension: Newtonsoft throws here
            if index < 0:
                return _MISSING
        if index >= len(t):
            return _MISSING
        return t[index]
    return _MISSING


class _Field(_Filter):
    def __init__(self, name: str | None) -> None:
        self.name = name

    def run(self, root, current):
        for t in current:
            if isinstance(t, dict):
                if self.name is None:
                    yield from t.values()
                elif self.name in t:
                    yield t[self.name]


class _FieldMultiple(_Filter):
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def run(self, root, current):
        for t in current:
            if isinstance(t, dict):
                for name in self.names:
                    if name in t:
                        yield t[name]


class _ArrayIndex(_Filter):
    def __init__(self, index: int | None) -> None:
        self.index = index

    def run(self, root, current):
        for t in current:
            if self.index is None:
                if isinstance(t, list):
                    yield from t
            else:
                v = _index(t, self.index)
                if v is not _MISSING:
                    yield v


class _ArrayMultipleIndex(_Filter):
    def __init__(self, indexes: list[int]) -> None:
        self.indexes = indexes

    def run(self, root, current):
        for t in current:
            for i in self.indexes:
                v = _index(t, i)
                if v is not _MISSING:
                    yield v


class _ArraySlice(_Filter):
    def __init__(self, start: int | None, end: int | None, step: int | None) -> None:
        self.start, self.end, self.step = start, end, step

    def run(self, root, current):
        if self.step == 0:
            raise JsonPathError("Step cannot be zero.")
        for t in current:
            if not isinstance(t, list):
                continue
            n = len(t)
            step = self.step if self.step is not None else 1
            start = self.start if self.start is not None else (0 if step > 0 else n - 1)
            stop = self.end if self.end is not None else (n if step > 0 else -1)
            if self.start is not None and self.start < 0:
                start = n + start
            if self.end is not None and self.end < 0:
                stop = n + stop
            start = max(start, 0) if step > 0 else start
            start = min(start, n if step > 0 else n - 1)
            stop = min(max(stop, -1), n)
            i = start
            while (i < stop) if step > 0 else (i > stop):
                yield t[i]
                i += step


class _Scan(_Filter):
    def __init__(self, name: str | None) -> None:
        self.name = name

    def run(self, root, current):
        for c in current:
            if self.name is None:
                yield c
            for d in _descendants(c):
                if isinstance(d, Prop):
                    if d.name == self.name:
                        yield d.value
                elif self.name is None:
                    yield d


class _ScanMultiple(_Filter):
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def run(self, root, current):
        for c in current:
            for d in _descendants(c):
                if isinstance(d, Prop):
                    for name in self.names:
                        if d.name == name:
                            yield d.value


class _Root(_Filter):
    def run(self, root, current):
        yield root


class _Query(_Filter):
    def __init__(self, expression: _Expr, scan: bool) -> None:
        self.expression = expression
        self.scan = scan

    def run(self, root, current):
        for t in current:
            if not self.scan:
                candidates = _children(t)
            elif _is_container(t):
                candidates = [t, *_descendants(t)]
            else:
                candidates = [t]
            for v in candidates:
                if self.expression.is_match(root, v):
                    yield v


def _evaluate(filters: list[_Filter], root: Any, t: Any) -> Iterator[Any]:
    current: Iterable[Any] = [t]
    for f in filters:
        current = f.run(root, current)
    return iter(current)


# ---- query expressions ----------------------------------------------------------------------------

@dataclass
class _Literal:
    value: Any


class _Expr:
    def is_match(self, root: Any, t: Any) -> bool:
        raise NotImplementedError


class _Composite(_Expr):
    def __init__(self, op: str) -> None:
        self.op = op
        self.expressions: list[_Expr] = []

    def is_match(self, root, t):
        if self.op == "&&":
            return all(e.is_match(root, t) for e in self.expressions)
        return any(e.is_match(root, t) for e in self.expressions)


def _jtype(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return "container"


def _is_value(v: Any) -> bool:
    return _jtype(v) != "container"


def _values_equal(a: Any, b: Any) -> bool:
    ta, tb = _jtype(a), _jtype(b)
    if {ta, tb} == {"int", "float"}:
        return a == b
    return ta == tb and a == b


def _compare(a: Any, b: Any) -> int | None:
    """JValue.CompareTo; ``None`` where Newtonsoft would not produce an ordering."""
    ta, tb = _jtype(a), _jtype(b)
    if ta in ("int", "float") and tb in ("int", "float"):
        return (a > b) - (a < b)
    if ta == tb == "str":
        return (a > b) - (a < b)
    if ta == tb == "bool":
        return int(a) - int(b)
    if ta == tb == "null":
        return 0
    return None


class _Boolean(_Expr):
    def __init__(self, op: str, left: Any, right: Any) -> None:
        self.op, self.left, self.right = op, left, right

    @staticmethod
    def _result(root: Any, t: Any, side: Any) -> list[Any]:
        if isinstance(side, _Literal):
            return [side.value]
        if isinstance(side, list):
            return list(_evaluate(side, root, t))
        return []

    def is_match(self, root, t):
        left = self._result(root, t, self.left)
        if self.op == "exists":
            return bool(left)
        if not left:
            return False
        right = self._result(root, t, self.right)
        return any(self._match(lv, rv) for lv in left for rv in right)

    def _match(self, lv: Any, rv: Any) -> bool:
        if not (_is_value(lv) and _is_value(rv)):
            return self.op in ("exists", "!=")
        op = self.op
        if op == "=~":
            return _regex_equals(lv, rv)
        if op == "==":
            return _values_equal(lv, rv)
        if op == "!=":
            return not _values_equal(lv, rv)
        if op == "===":
            return _values_equal(lv, rv)
        if op == "!==":
            return not _values_equal(lv, rv)
        c = _compare(lv, rv)
        if c is None:
            return False
        return {"<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[op]


def _regex_equals(value: Any, pattern: Any) -> bool:
    if not (isinstance(value, str) and isinstance(pattern, str)):
        return False
    from .dotnet_regex import compile_dotnet
    cut = pattern.rfind("/")
    body, options = pattern[1:cut], pattern[cut + 1:]
    return compile_dotnet(body, options).search(value) is not None


# ---- parser ---------------------------------------------------------------------------------------

class _PathParser:
    """Newtonsoft ``JPath``: a straight port so edge cases parse the same way."""

    def __init__(self, expression: str) -> None:
        self.expr = expression
        self.i = 0
        self.filters: list[_Filter] = []
        self._parse_main()

    def _err(self, message: str) -> JsonPathError:
        return JsonPathError(message)

    def _parse_main(self) -> None:
        e = self.expr
        part_start = self.i
        self._eat_whitespace()
        if len(e) == self.i:
            return
        if e[self.i] == "$":
            if len(e) == 1:
                return
            c = e[self.i + 1]
            if c in ".[":
                self.i += 1
                part_start = self.i
        if not self._parse_path(self.filters, part_start, False):
            last = self.i
            self._eat_whitespace()
            if self.i < len(e):
                raise self._err("Unexpected character while parsing path: " + e[last])

    def _parse_path(self, filters: list[_Filter], part_start: int, query: bool) -> bool:
        e = self.expr
        scan = False
        following_indexer = False
        following_dot = False
        ended = False
        while self.i < len(e) and not ended:
            ch = e[self.i]
            if ch in "[(":
                if self.i > part_start:
                    member = e[part_start:self.i]
                    filters.append(self._path_filter(None if member == "*" else member, scan))
                    scan = False
                filters.append(self._parse_indexer(ch, scan))
                scan = False
                self.i += 1
                part_start = self.i
                following_indexer = True
                following_dot = False
            elif ch in "]) ":
                ended = True
            elif ch == ".":
                if self.i > part_start:
                    member = e[part_start:self.i]
                    filters.append(self._path_filter(None if member == "*" else member, scan))
                    scan = False
                if self.i + 1 < len(e) and e[self.i + 1] == ".":
                    scan = True
                    self.i += 1
                self.i += 1
                part_start = self.i
                following_indexer = False
                following_dot = True
            else:
                if query and ch in "=<!>|&":
                    ended = True
                else:
                    if following_indexer:
                        raise self._err("Unexpected character following indexer: " + ch)
                    self.i += 1
        at_end = self.i == len(e)
        if self.i > part_start:
            member = e[part_start:self.i].rstrip()
            filters.append(self._path_filter(None if member == "*" else member, scan))
        elif following_dot and (at_end or query):
            raise self._err("Unexpected end while parsing path.")
        return at_end

    @staticmethod
    def _path_filter(member: str | None, scan: bool) -> _Filter:
        return _Scan(member) if scan else _Field(member)

    def _ensure(self, message: str) -> None:
        if self.i >= len(self.expr):
            raise self._err(message)

    def _eat_whitespace(self) -> None:
        while self.i < len(self.expr) and self.expr[self.i] == " ":
            self.i += 1

    def _parse_indexer(self, open_char: str, scan: bool) -> _Filter:
        self.i += 1
        close = "]" if open_char == "[" else ")"
        self._ensure("Path ended with open indexer.")
        self._eat_whitespace()
        c = self.expr[self.i]
        if c == "'":
            return self._parse_quoted_field(close, scan)
        if c == "?":
            return self._parse_query(close, scan)
        return self._parse_array_indexer(close)

    def _to_int(self, text: str) -> int:
        try:
            return int(text)
        except ValueError:
            raise self._err(f"Input string '{text}' was not in a correct format.") from None

    def _parse_array_indexer(self, close: str) -> _Filter:
        e = self.expr
        start = self.i
        end: int | None = None
        indexes: list[int] | None = None
        colons = 0
        s_idx: int | None = None
        e_idx: int | None = None
        step: int | None = None
        while self.i < len(e):
            ch = e[self.i]
            if ch == " ":
                end = self.i
                self._eat_whitespace()
                continue
            if ch == close:
                length = (end if end is not None else self.i) - start
                if indexes is not None:
                    if length == 0:
                        raise self._err("Array index expected.")
                    indexes.append(self._to_int(e[start:start + length]))
                    return _ArrayMultipleIndex(indexes)
                if colons > 0:
                    if length > 0:
                        v = self._to_int(e[start:start + length])
                        if colons == 1:
                            e_idx = v
                        else:
                            step = v
                    return _ArraySlice(s_idx, e_idx, step)
                if length == 0:
                    raise self._err("Array index expected.")
                return _ArrayIndex(self._to_int(e[start:start + length]))
            if ch == ",":
                length = (end if end is not None else self.i) - start
                if length == 0:
                    raise self._err("Array index expected.")
                indexes = indexes or []
                indexes.append(self._to_int(e[start:start + length]))
                self.i += 1
                self._eat_whitespace()
                start = self.i
                end = None
            elif ch == "*":
                self.i += 1
                self._ensure("Path ended with open indexer.")
                self._eat_whitespace()
                if e[self.i] != close:
                    raise self._err("Unexpected character while parsing path indexer: " + ch)
                return _ArrayIndex(None)
            elif ch == ":":
                length = (end if end is not None else self.i) - start
                if length > 0:
                    v = self._to_int(e[start:start + length])
                    if colons == 0:
                        s_idx = v
                    elif colons == 1:
                        e_idx = v
                    else:
                        step = v
                colons += 1
                self.i += 1
                self._eat_whitespace()
                start = self.i
                end = None
            elif not ch.isdigit() and ch != "-":
                raise self._err("Unexpected character while parsing path indexer: " + ch)
            else:
                if end is not None:
                    raise self._err("Unexpected character while parsing path indexer: " + ch)
                self.i += 1
        raise self._err("Path ended with open indexer.")

    def _read_quoted_string(self) -> str:
        e = self.expr
        out: list[str] = []
        self.i += 1
        escapes = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", "\\": "\\", '"': '"', "'": "'", "/": "/"}
        while self.i < len(e):
            ch = e[self.i]
            if ch == "\\" and self.i + 1 < len(e):
                self.i += 1
                nxt = e[self.i]
                if nxt not in escapes:
                    raise self._err("Unknown escape character: \\" + nxt)
                out.append(escapes[nxt])
                self.i += 1
            elif ch == "'":
                self.i += 1
                return "".join(out)
            else:
                self.i += 1
                out.append(ch)
        raise self._err("Path ended with an open string.")

    def _parse_quoted_field(self, close: str, scan: bool) -> _Filter:
        fields: list[str] | None = None
        while self.i < len(self.expr):
            name = self._read_quoted_string()
            self._eat_whitespace()
            self._ensure("Path ended with open indexer.")
            c = self.expr[self.i]
            if c == close:
                if fields is not None:
                    fields.append(name)
                    return _ScanMultiple(fields) if scan else _FieldMultiple(fields)
                return self._path_filter(name, scan)
            if c == ",":
                self.i += 1
                self._eat_whitespace()
                fields = fields or []
                fields.append(name)
            else:
                raise self._err("Unexpected character while parsing path indexer: " + c)
        raise self._err("Path ended with open indexer.")

    def _parse_query(self, close: str, scan: bool) -> _Filter:
        self.i += 1
        self._ensure("Path ended with open indexer.")
        if self.expr[self.i] != "(":
            raise self._err("Unexpected character while parsing path indexer: " + self.expr[self.i])
        self.i += 1
        expression = self._parse_expression()
        self.i += 1
        self._ensure("Path ended with open indexer.")
        self._eat_whitespace()
        if self.expr[self.i] != close:
            raise self._err("Unexpected character while parsing path indexer: " + self.expr[self.i])
        return _Query(expression, scan)

    def _try_parse_expression(self) -> list[_Filter] | None:
        c = self.expr[self.i]
        if c == "$":
            path: list[_Filter] = [_Root()]
        elif c == "@":
            path = []
        else:
            return None
        self.i += 1
        if self._parse_path(path, self.i, True):
            raise self._err("Path ended with open query.")
        return path

    def _parse_side(self) -> Any:
        self._eat_whitespace()
        self._ensure("Path ended with open query.")
        path = self._try_parse_expression()
        if path is not None:
            self._eat_whitespace()
            self._ensure("Path ended with open query.")
            return path
        ok, value = self._try_parse_value()
        if ok:
            self._eat_whitespace()
            self._ensure("Path ended with open query.")
            return _Literal(value)
        raise self._err("Unexpected character while parsing path query: " + self.expr[self.i])

    def _match(self, s: str) -> bool:
        if self.expr.startswith(s, self.i):
            self.i += len(s)
            return True
        return False

    def _try_parse_value(self) -> tuple[bool, Any]:
        e = self.expr
        c = e[self.i]
        if c == "'":
            return True, self._read_quoted_string()
        if c.isdigit() or c == "-":
            buf = [c]
            self.i += 1
            while self.i < len(e):
                c = e[self.i]
                if c in " )":
                    text = "".join(buf)
                    try:
                        if any(ch in text for ch in ".Ee"):
                            return True, float(text)
                        return True, int(text)
                    except ValueError:
                        return False, None
                buf.append(c)
                self.i += 1
            return False, None
        for word, value in (("true", True), ("false", False), ("null", None)):
            if c == word[0] and self._match(word):
                return True, value
        if c == "/":
            return True, self._read_regex_string()
        return False, None

    def _read_regex_string(self) -> str:
        e = self.expr
        start = self.i
        self.i += 1
        while self.i < len(e):
            c = e[self.i]
            if c == "\\" and self.i + 1 < len(e):
                self.i += 2
            elif c == "/":
                self.i += 1
                while self.i < len(e) and e[self.i].isalpha():
                    self.i += 1
                return e[start:self.i]
            else:
                self.i += 1
        raise self._err("Path ended with an open regex.")

    def _parse_operator(self) -> str:
        if self.i + 1 >= len(self.expr):
            raise self._err("Path ended with open query.")
        for op, name in (("===", "==="), ("==", "=="), ("=~", "=~"), ("!==", "!=="), ("!=", "!="), ("<>", "!="),
                         ("<=", "<="), ("<", "<"), (">=", ">="), (">", ">")):
            if self._match(op):
                return name
        raise self._err("Could not read query operator.")

    def _parse_expression(self) -> _Expr:
        e = self.expr
        root_expr: _Expr | None = None
        parent: _Composite | None = None
        while self.i < len(e):
            left = self._parse_side()
            right = None
            if e[self.i] in ")|&":
                op = "exists"
            else:
                op = self._parse_operator()
                right = self._parse_side()
            boolean = _Boolean(op, left, right)
            if e[self.i] == ")":
                if parent is not None:
                    parent.expressions.append(boolean)
                    assert root_expr is not None
                    return root_expr
                return boolean
            for sym, word in (("&", "&&"), ("|", "||")):
                if e[self.i] == sym:
                    if not self._match(word):
                        raise self._err("Unexpected character while parsing path query: " + e[self.i])
                    if parent is None or parent.op != word:
                        comp = _Composite(word)
                        if parent is not None:
                            parent.expressions.append(comp)
                        parent = comp
                        if root_expr is None:
                            root_expr = parent
                    parent.expressions.append(boolean)
        raise self._err("Path ended with open query.")


# ---- public API -----------------------------------------------------------------------------------

def select_tokens(data: Any, path: str) -> list[Any]:
    """Every token ``path`` matches in ``data`` (``JToken.SelectTokens``)."""
    filters = _PathParser(path).filters
    return list(_evaluate(filters, data, data))


def select_token(data: Any, path: str, default: Any = None) -> Any:
    """The single token ``path`` matches (``JToken.SelectToken``): ``default`` if none, error if several."""
    found: Any = _MISSING
    for t in _evaluate(_PathParser(path).filters, data, data):
        if found is not _MISSING:
            raise JsonPathError("Path returned multiple tokens.")
        found = t
    return default if found is _MISSING else found


def format_double(v: float) -> str:
    """``double.ToString(CultureInfo.InvariantCulture)`` as .NET Core 3.0+ prints it (shortest round trip).

    Fixed notation while the decimal exponent is in (-5, 15), otherwise ``d.dddE+XX``.
    """
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "\u221e" if v > 0 else "-\u221e"
    if v == 0:
        return "-0" if math.copysign(1.0, v) < 0 else "0"
    sign = "-" if v < 0 else ""
    _, digit_tuple, exp = Decimal(repr(abs(v))).normalize().as_tuple()
    digits = "".join(map(str, digit_tuple))
    sci = len(digits) - 1 + int(exp)
    if -5 < sci < 15:
        if sci >= len(digits) - 1:
            return sign + digits + "0" * (sci - len(digits) + 1)
        if sci >= 0:
            return sign + digits[: sci + 1] + "." + digits[sci + 1:]
        return sign + "0." + "0" * (-sci - 1) + digits
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{mantissa}E{'+' if sci >= 0 else '-'}{abs(sci):02d}"


def token_to_string(t: Any) -> str | None:
    """``(string)JToken``: scalars to text (bool as True/False), JSON null to None, containers raise."""
    if t is None:
        return None
    if isinstance(t, bool):
        return "True" if t else "False"
    if isinstance(t, int):
        return str(t)
    if isinstance(t, float):
        return format_double(t)
    if isinstance(t, str):
        return t
    kind = "Object" if isinstance(t, dict) else "Array" if isinstance(t, list) else "Property"
    raise JsonPathError(f"Can not convert {kind} to String.")
