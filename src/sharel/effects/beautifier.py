"""Image Beautifier (ShareX.MediaLib ``ImageBeautifier`` / ``ImageBeautifierOptions``).

Render order, as in ShareX: smart padding crops the uniform border off the screenshot; padding adds
a frame in the screenshot's top-left pixel colour; the card gets rounded corners; a transparent
margin is added; a drop shadow is drawn inside that margin (without growing the image); finally the
background (gradient, colour, image or desktop wallpaper) is painted behind everything. The output is
therefore ``(cropped) size + 2 * (padding + margin)`` in each dimension.
"""
# param() is a dataclass field specifier (it returns dataclasses.field), not a shared default:
# ruff: noqa: RUF009
from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import numpy as np
from PIL import Image

from ..core.serialize import from_jsonable
from .base import param
from .common import (GradientInfo, add_canvas, add_shadow, crop_bitmap, draw_background_image, fill_with,
                     find_auto_crop_rect, format_color, gradient_array, load_image, parse_color, rounded_corners)

BACKGROUND_TYPES = ["Gradient", "Color", "Image", "Desktop", "Transparent"]


def default_background_gradient() -> GradientInfo:
    """``new GradientInfo(ForwardDiagonal, Color(255, 81, 47), Color(221, 36, 118))``."""
    return GradientInfo.of("ForwardDiagonal", ("#FFFF512F", 0), ("#FFDD2476", 100))


@dataclass
class BeautifierOptions:
    margin: int = param(80, label="Margin", min=0, max=1000)
    padding: int = param(40, label="Padding", min=0, max=1000)
    smart_padding: bool = param(True, label="Smart padding")
    rounded_corner: int = param(20, label="Rounded corner", min=0, max=1000)
    shadow_radius: int = param(30, label="Shadow radius", min=0, max=500)
    shadow_opacity: int = param(80, label="Shadow opacity", min=0, max=100)
    shadow_distance: int = param(10, label="Shadow distance", min=0, max=1000)
    shadow_angle: int = param(180, label="Shadow angle", min=0, max=360)
    shadow_color: str = param("#FF000000", label="Shadow color", kind="color")
    background_type: str = param("Gradient", label="Background type", kind="choice", choices=BACKGROUND_TYPES)
    background_gradient: GradientInfo = param(label="Background gradient", kind="gradient",
                                              default_factory=default_background_gradient)
    background_color: str = param("#FF222222", label="Background color", kind="color")
    background_image_file_path: str = param("", label="Background image", kind="file")

    def shadow_offset(self) -> tuple[int, int]:
        """``(Point)MathHelpers.DegreeToVector2(ShadowAngle - 90, ShadowDistance)``: 180 deg = straight down."""
        rad = math.radians(self.shadow_angle - 90)
        return round(math.cos(rad) * self.shadow_distance), round(math.sin(rad) * self.shadow_distance)


def desktop_wallpaper_path() -> str:
    """The GNOME wallpaper file (dark variant when the dark style is active); "" if unavailable."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return ""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.desktop.background", True) is None:
        return ""
    bg = Gio.Settings.new("org.gnome.desktop.background")
    key = "picture-uri"
    if source.lookup("org.gnome.desktop.interface", True) is not None:
        scheme = Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
        if scheme == "prefer-dark" and "picture-uri-dark" in bg.list_keys():
            key = "picture-uri-dark"
    uri = bg.get_string(key) or bg.get_string("picture-uri")
    if not uri:
        return ""
    parsed = urlparse(uri)
    return unquote(parsed.path) if parsed.scheme in ("file", "") else ""


def _options(options: dict | BeautifierOptions | None) -> BeautifierOptions:
    if isinstance(options, BeautifierOptions):
        return options
    return from_jsonable(BeautifierOptions, options or {})


def beautify(img: Image.Image, options: dict | BeautifierOptions | None = None) -> Image.Image:
    """Render ShareX's Image Beautifier. ``options`` is a serialized :class:`BeautifierOptions`
    (``{}`` = ShareX's defaults) or an instance."""
    o = _options(options)
    src = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    padding_color = format_color(src.getpixel((0, 0)))
    result = src
    if o.smart_padding:
        rect = find_auto_crop_rect(src, True)
        if rect != (0, 0, src.width, src.height):
            cropped = crop_bitmap(src, *rect)
            if cropped is not None:
                result = cropped
    if o.padding > 0:
        result = add_canvas(result, [o.padding] * 4, padding_color) or result
    if o.rounded_corner > 0:
        result = rounded_corners(result, o.rounded_corner)
    if o.margin > 0:
        result = add_canvas(result, [o.margin] * 4) or result
    if o.shadow_opacity > 0 and (o.shadow_radius > 0 or o.shadow_distance > 0):
        result = add_shadow(result, o.shadow_opacity / 100, o.shadow_radius, 0.0, o.shadow_color,
                            o.shadow_offset(), auto_resize=False)
    bt = o.background_type
    if bt == "Gradient":
        if o.background_gradient is not None and o.background_gradient.is_visible:
            result = fill_with(result, gradient_array(result.width, result.height, o.background_gradient))
    elif bt == "Color":
        if parse_color(o.background_color)[3] == 255:  # ShareX skips any non-opaque colour
            fill = np.empty((result.height, result.width, 4), np.float32)
            fill[:] = parse_color(o.background_color)
            result = fill_with(result, fill)
    elif bt == "Image":
        result = draw_background_image(result, load_image(o.background_image_file_path))
    elif bt == "Desktop":
        result = draw_background_image(result, load_image(desktop_wallpaper_path()))
    return result
