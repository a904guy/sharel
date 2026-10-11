""".NET regular expressions on Python's :mod:`re` (ShareX's ``{regex:}`` uses ``System.Text.RegularExpressions``).

:func:`compile_dotnet` rewrites the .NET-only constructs custom uploaders use into ``re`` syntax:

* named groups ``(?<name>...)`` / ``(?'name'...)`` and back references ``\\k<name>`` / ``\\k'name'``
* inline options in the middle of a pattern (``a(?i)b``) which .NET scopes to the enclosing group
  (Python only accepts global flags at the very start); the ``n`` (explicit capture) option is dropped
* ``\\z`` (absolute end) and ``\\Z`` (end, or before a final newline)
* ``\\p{L}`` / ``\\p{N}`` / ``\\p{Nd}`` and their ``\\P`` negations outside character classes

:class:`DotNetRegex` also numbers groups the .NET way: unnamed groups first, left to right, then named
groups, so ``match.group(dotnet_number)`` returns what ``Match.Groups[n]`` would in ShareX.
Other .NET-only syntax (balancing groups, class subtraction, other Unicode categories) raises
:class:`re.error`.
"""
from __future__ import annotations

import re
from functools import lru_cache

_CATEGORIES = {"L": r"[^\W\d_]", "N": r"\d", "Nd": r"\d"}
_NEG_CATEGORIES = {"L": r"[\W\d_]", "N": r"\D", "Nd": r"\D"}
_FLAG_CHARS = set("imnsx")


class DotNetRegex:
    """A compiled pattern plus the .NET group numbering."""

    def __init__(self, pattern: re.Pattern[str], unnamed: list[int], named: list[str]) -> None:
        self.pattern = pattern
        # .NET numbers: 1..k for the unnamed groups (python index i), then the named groups in order.
        self._numbers: dict[int, int | str] = {}
        n = 1
        for py_index in unnamed:
            self._numbers[n] = py_index
            n += 1
        for name in named:
            if name not in self._numbers.values():
                self._numbers[n] = name
                n += 1

    def search(self, text: str) -> re.Match[str] | None:
        return self.pattern.search(text)

    def group_value(self, match: re.Match[str], group: int | str) -> str:
        """``Match.Groups[group].Value``: "" for unknown or unmatched groups, as .NET's Group.Empty."""
        if isinstance(group, int):
            if group == 0:
                return match.group(0)
            key = self._numbers.get(group)
            if key is None:
                return ""
        else:
            key = group
            if key not in self.pattern.groupindex:
                return ""
        value = match.group(key)
        return value or ""


def _read_name(p: str, i: int, close: str) -> tuple[str, int]:
    end = p.find(close, i)
    if end < 0:
        raise re.error("unterminated group name", p, i)
    name = p[i:end]
    if "-" in name:
        raise re.error(".NET balancing groups are not supported", p, i)
    if not name.isidentifier():
        raise re.error(f"bad group name {name!r}", p, i)
    return name, end + 1


@lru_cache(maxsize=256)
def _translate(pattern: str, options: str) -> tuple[str, int, tuple[int, ...], tuple[str, ...]]:
    p = pattern
    out: list[str] = []
    # Stack of open groups: the inline-option scopes opened inside each, closed (and reopened after
    # every '|') so the options cover the rest of the enclosing group like .NET.
    pending: list[list[str]] = [[]]
    unnamed: list[int] = []
    named: list[str] = []
    py_index = 0
    i = 0
    n = len(p)
    at_start = True
    while i < n:
        c = p[i]
        if c == "\\":
            if i + 1 >= n:
                raise re.error("bad escape (end of pattern)", p, i)
            nxt = p[i + 1]
            if nxt == "k" and i + 2 < n and p[i + 2] in "<'":
                name, i = _read_name(p, i + 3, ">" if p[i + 2] == "<" else "'")
                out.append(f"(?P={name})")
                at_start = False
                continue
            if nxt == "z":
                out.append(r"\Z")
            elif nxt == "Z":
                out.append(r"(?=\n?\Z)")
            elif nxt in "pP" and i + 2 < n and p[i + 2] == "{":
                end = p.find("}", i + 3)
                cat = p[i + 3:end] if end > 0 else ""
                table = _CATEGORIES if nxt == "p" else _NEG_CATEGORIES
                if cat not in table:
                    raise re.error(f"Unicode category \\{nxt}{{{cat}}} is not supported", p, i)
                out.append(table[cat])
                i = end + 1
                at_start = False
                continue
            else:
                out.append(p[i:i + 2])
            i += 2
            at_start = False
            continue
        if c == "[":
            # Copy a character class verbatim (a leading ']' or '^]' is literal in both engines).
            j = i + 1
            if j < n and p[j] == "^":
                j += 1
            if j < n and p[j] == "]":
                j += 1
            while j < n and p[j] != "]":
                if p[j] == "\\":
                    if p[j + 1:j + 2] in ("p", "P"):
                        raise re.error("Unicode categories inside a character class are not supported", p, j)
                    j += 2
                    continue
                if p[j] == "-" and p[j + 1:j + 2] == "[":
                    raise re.error(".NET character class subtraction is not supported", p, j)
                j += 1
            if j >= n:
                raise re.error("unterminated character set", p, i)
            out.append(p[i:j + 1])
            i = j + 1
            at_start = False
            continue
        if c == "(":
            if p.startswith("(?", i):
                rest = p[i + 2:i + 3]
                if rest == "<" and p[i + 3:i + 4] not in ("=", "!"):
                    name, i = _read_name(p, i + 3, ">")
                    py_index += 1
                    named.append(name)
                    out.append(f"(?P<{name}>")
                    pending.append([])
                    at_start = False
                    continue
                if rest == "'":
                    name, i = _read_name(p, i + 3, "'")
                    py_index += 1
                    named.append(name)
                    out.append(f"(?P<{name}>")
                    pending.append([])
                    at_start = False
                    continue
                if rest == "P" and p[i + 3:i + 4] == "<":
                    name, i = _read_name(p, i + 4, ">")
                    py_index += 1
                    named.append(name)
                    out.append(f"(?P<{name}>")
                    pending.append([])
                    at_start = False
                    continue
                m = re.match(r"\(\?([imnsx]*)(?:-([imnsx]*))?([:)])", p[i:])
                if m and (m.group(1) or m.group(2) is not None):
                    on = m.group(1).replace("n", "")
                    off = (m.group(2) or "").replace("n", "")
                    spec = on + ("-" + off if off else "")
                    if m.group(3) == ":":
                        out.append(f"(?{spec}:" if spec else "(?:")
                        pending.append([])
                    elif spec:
                        if at_start and not off and len(pending) == 1:
                            out.append(f"(?{spec})")
                        else:
                            # Scope the options to the rest of the enclosing group, as .NET does.
                            out.append(f"(?{spec}:")
                            pending[-1].append(spec)
                    i += m.end()
                    continue
                if p.startswith("(?#", i):
                    end = p.find(")", i)
                    if end < 0:
                        raise re.error("missing ), unterminated comment", p, i)
                    i = end + 1
                    continue
                # (?: (?= (?! (?<= (?<! (?> (?( ... : non-capturing, same in both engines.
                out.append("(?")
                pending.append([])
                i += 2
                at_start = False
                continue
            py_index += 1
            unnamed.append(py_index)
            out.append("(")
            pending.append([])
            i += 1
            at_start = False
            continue
        if c == ")":
            if len(pending) == 1:
                raise re.error("unbalanced parenthesis", p, i)
            out.append(")" * len(pending.pop()))
            if not pending:
                raise re.error("unbalanced parenthesis", p, i)
            out.append(")")
            i += 1
            at_start = False
            continue
        if c == "|":
            # An alternative ends the scope of inline options opened in this branch.
            out.append(")" * len(pending[-1]))
            out.append("|")
            out.extend(f"(?{spec}:" for spec in pending[-1])
            i += 1
            continue
        out.append(c)
        i += 1
        at_start = False
    if len(pending) != 1:
        raise re.error("missing ), unterminated subpattern", p, n)
    out.append(")" * len(pending[0]))
    flags = 0
    for opt in options:
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}.get(opt, 0)
    return "".join(out), flags, tuple(unnamed), tuple(named)


def compile_dotnet(pattern: str, options: str = "") -> DotNetRegex:
    """Compile a .NET pattern (``options`` like ``"im"``); raises :class:`re.error` for unsupported syntax."""
    translated, flags, unnamed, named = _translate(pattern, options)
    return DotNetRegex(re.compile(translated, flags), list(unnamed), list(named))
