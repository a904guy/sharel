"""Colour model and text formats for the colour picker and the screen colour picker (ShareX ColorHelpers /
MyColor / ColorPickerWindow / CodeMenuEntryPixelInfo).

Model
-----
``Color(r, g, b, a=255)``  frozen dataclass of 0..255 ints. Conversions (all return/accept the units ShareX's
picker shows):

* ``to_hex(order="RGB"|"RGBA"|"ARGB", prefix="#", upper=True)`` / ``Color.from_hex(text, order=None)``
  (``order=None`` accepts 3/6 digits as RGB and 8 digits as ``hex8_order``, default ARGB, which is how ShareL
  stores colours in its config, e.g. ``"#7D000000"``; pass ``"RGBA"`` for the ShareX picker's 8-digit field).
* ``to_decimal(order)`` / ``Color.from_decimal(n, order)`` — ShareX ``ColorToDecimal`` (RGB, RGBA, ARGB packing).
* ``to_hsb() -> HSB(hue 0..360, saturation 0..100, brightness 0..100)`` / ``Color.from_hsb(h, s, b, a)``
  (HSB is HSV).
* ``to_hsl() -> HSL(hue, saturation, lightness)`` / ``Color.from_hsl(h, s, l, a)``.
* ``to_cmyk() -> CMYK(c, m, y, k)`` each 0..100 / ``Color.from_cmyk(c, m, y, k, a)`` (ShareX formula; black is
  0,0,0,100).
* ``name()`` — the CSS / .NET known-colour name of an exact opaque match ("" if none).

Text formats (``FORMATS`` maps a format name to ``Color -> str``) — these are the strings ShareX's colour
picker copies: ``RGB`` "255, 0, 0"; ``RGBA`` "255, 0, 0, 255"; ``Hex`` "#FF0000"; ``HexRGBA`` "#FF0000FF";
``HexARGB`` "#FFFF0000"; ``Decimal`` "16711680"; ``HSB`` "0.0°, 100.0%, 100.0%"; ``HSL`` "0.0°, 100.0%, 50.0%";
``CMYK`` "0.0%, 100.0%, 100.0%, 0.0%"; ``All`` the multi-line summary of ShareX's MyColor.ToString().

``parse(text, hex8_order="ARGB") -> Color | None`` reads any of those back (also ``rgb()/rgba()/hsb()/hsv()/hsl()/
cmyk()`` functional notation, ``0x`` hex, ``#RGB`` and CSS colour names).

Screen colour picker info text
------------------------------
``format_pixel_info(template, color, x, y, newline="\\n")`` expands ShareX's ``CodeMenuEntryPixelInfo`` tokens:
``$r255 $g255 $b255`` (0..255), ``$r1 $g1 $b1`` (0..1, 3 decimals; ``$r1{n}`` = n decimals), ``$hex``/``$HEX``
(RRGGBB lower/upper), ``$rhex $ghex $bhex``/``$rHEX $gHEX $bHEX``, ``$c100 $m100 $y100 $k100``, ``$name``,
``$x $y``, ``$n`` (new line) and the legacy ``$r $g $b``. ShareL adds the alpha channel: ``$a255``, ``$a1``,
``$ahex``/``$aHEX``, ``$a``. Replacement order and case rules follow ShareX exactly (``$HEX``/``$rHEX`` are
case-sensitive and are expanded before the case-insensitive lowercase variants; ``$name`` before ``$n``).
``PIXEL_INFO_TOKENS`` lists (token, description) for a "insert token" menu.
"""
from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from PIL import ImageColor

__all__ = ["CMYK", "FORMATS", "HSB", "HSL", "PIXEL_INFO_TOKENS", "Color", "format_color", "format_pixel_info",
           "parse"]


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _round_half_up(v: float, places: int = 0) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return Decimal(v).quantize(q, rounding=ROUND_HALF_UP)


def _num(v: float, places: int) -> str:
    """.NET ``Math.Round(v, places, AwayFromZero).ToString()``: no trailing zeros, no exponent."""
    d = _round_half_up(v, places)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _fixed1(v: float) -> str:
    """.NET ``{0:0.0}`` formatting."""
    return format(_round_half_up(v, 1), "f")


def _byte(v: float) -> int:
    return int(_clamp(int(_round_half_up(v)), 0, 255))


class HSB(NamedTuple):
    hue: float  # 0..360
    saturation: float  # 0..100
    brightness: float  # 0..100


class HSL(NamedTuple):
    hue: float
    saturation: float
    lightness: float


class CMYK(NamedTuple):
    cyan: float  # 0..100
    magenta: float
    yellow: float
    key: float


_ORDERS = ("RGB", "RGBA", "ARGB")


@dataclass(frozen=True)
class Color:
    r: int
    g: int
    b: int
    a: int = 255

    def __post_init__(self) -> None:
        for ch in ("r", "g", "b", "a"):
            v = getattr(self, ch)
            if not isinstance(v, int) or not 0 <= v <= 255:
                raise ValueError(f"colour channel {ch}={v!r} is not an int in 0..255")

    # ---- tuples ---------------------------------------------------------------------------
    @property
    def rgb(self) -> tuple[int, int, int]:
        return self.r, self.g, self.b

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        return self.r, self.g, self.b, self.a

    def with_alpha(self, a: int) -> Color:
        return Color(self.r, self.g, self.b, a)

    # ---- hex ------------------------------------------------------------------------------
    def to_hex(self, order: str = "RGB", prefix: str = "#", upper: bool = True) -> str:
        if order not in _ORDERS:
            raise ValueError(f"unknown hex order {order!r}")
        vals = {"RGB": self.rgb, "RGBA": (self.r, self.g, self.b, self.a), "ARGB": (self.a, self.r, self.g, self.b)}
        s = "".join(f"{v:02X}" for v in vals[order])
        return prefix + (s if upper else s.lower())

    @classmethod
    def from_hex(cls, text: str, order: str | None = None, hex8_order: str = "ARGB") -> Color:
        s = text.strip()
        if s.startswith("#"):
            s = s[1:]
        elif s[:2].lower() == "0x":
            s = s[2:]
        if not re.fullmatch(r"[0-9A-Fa-f]+", s or "x"):
            raise ValueError(f"not a hex colour: {text!r}")
        if order is None:
            if len(s) == 3:
                s = "".join(c * 2 for c in s)
                order = "RGB"
            elif len(s) == 6:
                order = "RGB"
            elif len(s) == 8:
                order = hex8_order
            else:
                raise ValueError(f"hex colour must have 3, 6 or 8 digits: {text!r}")
        need = 6 if order == "RGB" else 8
        if len(s) != need:
            raise ValueError(f"{order} hex colour needs {need} digits: {text!r}")
        v = [int(s[i:i + 2], 16) for i in range(0, need, 2)]
        if order == "RGB":
            return cls(v[0], v[1], v[2])
        if order == "RGBA":
            return cls(v[0], v[1], v[2], v[3])
        return cls(v[1], v[2], v[3], v[0])

    # ---- decimal --------------------------------------------------------------------------
    def to_decimal(self, order: str = "RGB") -> int:
        if order == "RGB":
            return self.r << 16 | self.g << 8 | self.b
        if order == "RGBA":
            return self.r << 24 | self.g << 16 | self.b << 8 | self.a
        if order == "ARGB":
            return self.a << 24 | self.r << 16 | self.g << 8 | self.b
        raise ValueError(f"unknown decimal order {order!r}")

    @classmethod
    def from_decimal(cls, value: int, order: str = "RGB") -> Color:
        if value < 0:
            raise ValueError("decimal colour must be >= 0")
        if order == "RGB":
            if value > 0xFFFFFF:
                raise ValueError("RGB decimal colour must be <= 16777215")
            return cls((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        if value > 0xFFFFFFFF:
            raise ValueError("decimal colour must fit in 32 bits")
        if order == "RGBA":
            return cls((value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        if order == "ARGB":
            return cls((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF, (value >> 24) & 0xFF)
        raise ValueError(f"unknown decimal order {order!r}")

    # ---- HSB / HSV ------------------------------------------------------------------------
    def to_hsb(self) -> HSB:
        h, s, v = colorsys.rgb_to_hsv(self.r / 255, self.g / 255, self.b / 255)
        return HSB(h * 360, s * 100, v * 100)

    @classmethod
    def from_hsb(cls, hue: float, saturation: float, brightness: float, a: int = 255) -> Color:
        h = (hue % 360) / 360
        r, g, b = colorsys.hsv_to_rgb(h, _clamp(saturation, 0, 100) / 100, _clamp(brightness, 0, 100) / 100)
        return cls(_byte(r * 255), _byte(g * 255), _byte(b * 255), a)

    to_hsv = to_hsb
    from_hsv = from_hsb

    # ---- HSL ------------------------------------------------------------------------------
    def to_hsl(self) -> HSL:
        h, light, s = colorsys.rgb_to_hls(self.r / 255, self.g / 255, self.b / 255)
        return HSL(h * 360, s * 100, light * 100)

    @classmethod
    def from_hsl(cls, hue: float, saturation: float, lightness: float, a: int = 255) -> Color:
        h = (hue % 360) / 360
        r, g, b = colorsys.hls_to_rgb(h, _clamp(lightness, 0, 100) / 100, _clamp(saturation, 0, 100) / 100)
        return cls(_byte(r * 255), _byte(g * 255), _byte(b * 255), a)

    # ---- CMYK (ShareX ColorHelpers.ColorToCMYK / CMYKToColor) ------------------------------
    def to_cmyk(self) -> CMYK:
        if self.r == 0 and self.g == 0 and self.b == 0:
            return CMYK(0.0, 0.0, 0.0, 100.0)
        c = 1 - self.r / 255
        m = 1 - self.g / 255
        y = 1 - self.b / 255
        k = min(c, m, y)
        return CMYK((c - k) / (1 - k) * 100, (m - k) / (1 - k) * 100, (y - k) / (1 - k) * 100, k * 100)

    @classmethod
    def from_cmyk(cls, cyan: float, magenta: float, yellow: float, key: float, a: int = 255) -> Color:
        c, m, y, k = (_clamp(v, 0, 100) / 100 for v in (cyan, magenta, yellow, key))
        if c == 0 and m == 0 and y == 0 and k == 1:
            return cls(0, 0, 0, a)
        c = c * (1 - k) + k
        m = m * (1 - k) + k
        y = y * (1 - k) + k
        return cls(_byte((1 - c) * 255), _byte((1 - m) * 255), _byte((1 - y) * 255), a)

    # ---- names ----------------------------------------------------------------------------
    def name(self) -> str:
        if self.a != 255:
            return "transparent" if self.a == 0 and self.rgb == (255, 255, 255) else ""
        return _NAME_BY_RGB.get(self.rgb, "")

    def __str__(self) -> str:
        return self.to_hex("ARGB") if self.a != 255 else self.to_hex()


def _build_names() -> dict[tuple[int, int, int], str]:
    names: dict[tuple[int, int, int], str] = {}
    # alphabetical => "aqua" before "cyan", "fuchsia" before "magenta", "gray" before "grey" (like .NET's enum)
    for nm in sorted(ImageColor.colormap):
        rgb = ImageColor.getrgb(nm)[:3]
        names.setdefault(rgb, nm)
    return names


_NAME_BY_RGB = _build_names()

# ---- text formats -------------------------------------------------------------------------


def _all_text(c: Color) -> str:
    hsb, cmyk = c.to_hsb(), c.to_cmyk()
    return (f"RGBA (Red, Green, Blue, Alpha) = {c.r}, {c.g}, {c.b}, {c.a}\n"
            f"HSB (Hue, Saturation, Brightness) = {_fixed1(hsb.hue)}°, {_fixed1(hsb.saturation)}%, "
            f"{_fixed1(hsb.brightness)}%\n"
            f"CMYK (Cyan, Magenta, Yellow, Key) = {_fixed1(cmyk.cyan)}%, {_fixed1(cmyk.magenta)}%, "
            f"{_fixed1(cmyk.yellow)}%, {_fixed1(cmyk.key)}%\n"
            f"Hex (RGB, RGBA, ARGB) = {c.to_hex()}, {c.to_hex('RGBA')}, {c.to_hex('ARGB')}\n"
            f"Decimal (RGB, RGBA, ARGB) = {c.to_decimal()}, {c.to_decimal('RGBA')}, {c.to_decimal('ARGB')}")


FORMATS = {
    "RGB": lambda c: f"{c.r}, {c.g}, {c.b}",
    "RGBA": lambda c: f"{c.r}, {c.g}, {c.b}, {c.a}",
    "Hex": lambda c: c.to_hex(),
    "HexRGBA": lambda c: c.to_hex("RGBA"),
    "HexARGB": lambda c: c.to_hex("ARGB"),
    "Decimal": lambda c: str(c.to_decimal()),
    "HSB": lambda c: "{}°, {}%, {}%".format(*(_fixed1(v) for v in c.to_hsb())),
    "HSL": lambda c: "hsl({}, {}%, {}%)".format(*(_fixed1(v) for v in c.to_hsl())),
    "CMYK": lambda c: "{}%, {}%, {}%, {}%".format(*(_fixed1(v) for v in c.to_cmyk())),
    "All": _all_text,
}


def format_color(color: Color, fmt: str) -> str:
    try:
        return FORMATS[fmt](color)
    except KeyError:
        raise ValueError(f"unknown colour format {fmt!r}; known: {', '.join(FORMATS)}") from None


# ---- parsing ------------------------------------------------------------------------------

_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_SEP = r"\s*(?:,|\s)\s*"


def _alpha_value(raw: str) -> int:
    """Alpha in functional notation: ``50%`` is a percentage, ``0.5`` (has a dot) a 0..1 fraction, an integer
    is 0..255 (the form ShareL's RGBA format writes)."""
    raw = raw.strip()
    if raw.endswith("%"):
        return _byte(float(raw[:-1]) / 100 * 255)
    v = float(raw)
    if "." in raw and v <= 1:
        return _byte(v * 255)
    if not 0 <= v <= 255:
        raise ValueError("alpha out of range")
    return _byte(v)


def parse(text: str, hex8_order: str = "ARGB") -> Color | None:
    """Read a colour written in any of the supported formats; ``None`` if it isn't one."""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    low = s.lower()

    # functional notations
    m = re.fullmatch(r"(rgba?|hs[bvl]a?|cmyka?)\s*\((.*)\)", low)
    if m:
        kind, body = m.group(1), m.group(2)
        parts = [p for p in re.split(r"\s*[,/]\s*|\s+", body.strip()) if p]
        try:
            vals = [float(p.rstrip("%°deg")) for p in parts]
        except ValueError:
            return None
        try:
            if kind.startswith("rgb") and len(vals) in (3, 4):
                a = _alpha_value(parts[3]) if len(vals) == 4 else 255
                if any(not 0 <= v <= 255 for v in vals[:3]):
                    return None
                return Color(_byte(vals[0]), _byte(vals[1]), _byte(vals[2]), a)
            if kind[:3] in ("hsb", "hsv") and len(vals) in (3, 4):
                a = _alpha_value(parts[3]) if len(vals) == 4 else 255
                return Color.from_hsb(vals[0], vals[1], vals[2], a)
            if kind.startswith("hsl") and len(vals) in (3, 4):
                a = _alpha_value(parts[3]) if len(vals) == 4 else 255
                return Color.from_hsl(vals[0], vals[1], vals[2], a)
            if kind.startswith("cmyk") and len(vals) in (4, 5):
                a = _alpha_value(parts[4]) if len(vals) == 5 else 255
                return Color.from_cmyk(vals[0], vals[1], vals[2], vals[3], a)
        except ValueError:
            return None
        return None

    # hex: "#..." / "0x..." of 3, 6 or 8 digits, or bare 6/8 hex digits containing a letter. A bare string of
    # only decimal digits is read as a decimal colour (so the "Decimal" format round-trips).
    if re.fullmatch(r"(?:#|0x)(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", low) or (
            re.fullmatch(r"[0-9a-f]{6}|[0-9a-f]{8}", low) and not low.isdigit()):
        try:
            return Color.from_hex(s, hex8_order=hex8_order)
        except ValueError:
            return None

    # "h°, s%, b%" (ShareX HSB copy format) and "c%, m%, y%, k%" (CMYK copy format)
    m = re.fullmatch(rf"({_NUM})\s*°{_SEP}({_NUM})\s*%{_SEP}({_NUM})\s*%", low)
    if m:
        return Color.from_hsb(*(float(g) for g in m.groups()))
    m = re.fullmatch(rf"({_NUM})\s*%{_SEP}({_NUM})\s*%{_SEP}({_NUM})\s*%{_SEP}({_NUM})\s*%", low)
    if m:
        return Color.from_cmyk(*(float(g) for g in m.groups()))

    # "r, g, b" / "r, g, b, a" (ShareX ParseColor accepts commas or spaces)
    m = re.fullmatch(r"(\d{1,3})\s*(?:,|\s)\s*(\d{1,3})\s*(?:,|\s)\s*(\d{1,3})(?:\s*(?:,|\s)\s*(\d{1,3}))?", low)
    if m:
        vals = [int(g) for g in m.groups() if g is not None]
        if all(v <= 255 for v in vals):
            return Color(*vals)
        return None

    # decimal (RGB packing)
    if re.fullmatch(r"\d+", low):
        n = int(low)
        return Color.from_decimal(n) if n <= 0xFFFFFF else None

    # multi-line "All" text: take the RGBA line
    m = re.search(r"rgba[^=]*=\s*(\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3})", low)
    if m:
        vals = [int(g) for g in m.groups()]
        return Color(*vals) if all(v <= 255 for v in vals) else None

    # CSS names
    if low in ImageColor.colormap:
        return Color(*ImageColor.getrgb(low)[:3])
    if low == "transparent":
        return Color(255, 255, 255, 0)
    return None


# ---- screen colour picker info text --------------------------------------------------------

PIXEL_INFO_TOKENS: list[tuple[str, str]] = [
    ("$r255", "Red color (0-255)"), ("$g255", "Green color (0-255)"), ("$b255", "Blue color (0-255)"),
    ("$a255", "Alpha (0-255)"),
    ("$r1", "Red color (0-1). Specify decimal precision with {n}, defaults to 3."),
    ("$g1", "Green color (0-1). Specify decimal precision with {n}, defaults to 3."),
    ("$b1", "Blue color (0-1). Specify decimal precision with {n}, defaults to 3."),
    ("$a1", "Alpha (0-1). Specify decimal precision with {n}, defaults to 3."),
    ("$hex", "Hex color value (Lowercase)"), ("$rhex", "Red hex color value (00-ff)"),
    ("$ghex", "Green hex color value (00-ff)"), ("$bhex", "Blue hex color value (00-ff)"),
    ("$ahex", "Alpha hex value (00-ff)"),
    ("$HEX", "Hex color value (Uppercase)"), ("$rHEX", "Red hex color value (00-FF)"),
    ("$gHEX", "Green hex color value (00-FF)"), ("$bHEX", "Blue hex color value (00-FF)"),
    ("$aHEX", "Alpha hex value (00-FF)"),
    ("$c100", "Cyan color (0-100)"), ("$m100", "Magenta color (0-100)"), ("$y100", "Yellow color (0-100)"),
    ("$k100", "Key color (0-100)"), ("$name", "Color name"), ("$x", "X position"), ("$y", "Y position"),
    ("$n", "New line"),
]


def _ireplace(text: str, token: str, value: str) -> str:
    return re.sub(re.escape(token), lambda _m: value, text, flags=re.IGNORECASE)


def _replace_precision(text: str, token: str, value: float) -> str:
    def sub(m: re.Match) -> str:
        try:
            places = int(m.group(1))
        except ValueError:
            places = 0
        return _num(value, int(_clamp(places, 0, 15)))
    return re.sub(re.escape(token) + r"\{([^}]*)\}", sub, text, flags=re.IGNORECASE)


def format_pixel_info(template: str, color: Color, x: int = 0, y: int = 0, newline: str = "\n") -> str:
    """Expand ShareX's pixel-info tokens (see module docstring) in ``template``."""
    if not template:
        return ""
    c = color
    cmyk = c.to_cmyk()
    upper_hex = c.to_hex(prefix="")
    t = template
    for tok, val in (("$r255", c.r), ("$g255", c.g), ("$b255", c.b), ("$a255", c.a)):
        t = _ireplace(t, tok, str(val))
    for tok, val in (("$rHEX", c.r), ("$gHEX", c.g), ("$bHEX", c.b), ("$aHEX", c.a)):
        t = t.replace(tok, f"{val:02X}")  # case-sensitive, like ShareX
    for tok, val in (("$rhex", c.r), ("$ghex", c.g), ("$bhex", c.b), ("$ahex", c.a)):
        t = _ireplace(t, tok, f"{val:02x}")
    t = t.replace("$HEX", upper_hex)
    t = _ireplace(t, "$hex", upper_hex.lower())
    for tok, val in (("$c100", cmyk.cyan), ("$m100", cmyk.magenta), ("$y100", cmyk.yellow), ("$k100", cmyk.key)):
        t = _ireplace(t, tok, _num(val, 2))
    t = _ireplace(t, "$name", c.name())
    t = _ireplace(t, "$x", str(x))
    t = _ireplace(t, "$y", str(y))
    t = _ireplace(t, "$n", newline)
    for tok, val in (("$r1", c.r), ("$g1", c.g), ("$b1", c.b), ("$a1", c.a)):
        t = _replace_precision(t, tok, val / 255)
    for tok, val in (("$r1", c.r), ("$g1", c.g), ("$b1", c.b), ("$a1", c.a)):
        t = _ireplace(t, tok, _num(val / 255, 3))
    for tok, val in (("$r", c.r), ("$g", c.g), ("$b", c.b), ("$a", c.a)):
        t = _ireplace(t, tok, str(val))
    return t
