"""Print an image (ShareX's PrintHelper): Gtk.PrintOperation, falling back to ``lp``.

PrintSettings: margin, auto rotate (landscape images on landscape pages), auto scale (fit the
printable area), allow enlarge (scale small images up), center, and whether to show the dialog.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from PIL import Image

from ..core.config import PrintSettings
from .gtkutil import cairo_surface_from_pil

log = logging.getLogger("sharel.print")


def layout(img_w: int, img_h: int, page_w: float, page_h: float, s: PrintSettings) -> tuple[float, float, float,
                                                                                                    float, bool]:
    """Where the image goes on the page: (x, y, w, h, rotated), in page units."""
    rotated = False
    if s.auto_rotate_image and (img_w > img_h) != (page_w > page_h):
        img_w, img_h = img_h, img_w
        rotated = True
    m = max(0, s.margin)
    aw, ah = max(1.0, page_w - 2 * m), max(1.0, page_h - 2 * m)
    scale = 1.0
    if s.auto_scale_image:
        fit = min(aw / img_w, ah / img_h)
        scale = fit if (fit < 1 or s.allow_enlarge_image) else 1.0
    w, h = img_w * scale, img_h * scale
    if s.center_image:
        x, y = m + (aw - w) / 2, m + (ah - h) / 2
    else:
        x, y = m, m
    return x, y, w, h, rotated


def lp_print(img: Image.Image, printer: str = "") -> bool:
    if not shutil.which("lp"):
        return False
    with tempfile.NamedTemporaryFile(prefix="sharel-print-", suffix=".png", delete=False) as fh:
        img.convert("RGB").save(fh, "PNG")
        path = fh.name
    argv = ["lp", "-o", "fit-to-page"] + (["-d", printer] if printer else []) + [path]
    try:
        return subprocess.run(argv, timeout=30, capture_output=True).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def print_image(app, img: Image.Image, settings: PrintSettings, parent: Gtk.Window | None = None) -> None:
    op = Gtk.PrintOperation(n_pages=1, use_full_page=False, job_name="ShareL image")
    op.set_unit(Gtk.Unit.POINTS)
    surface = cairo_surface_from_pil(img)

    def draw_page(_op, ctx, _n):
        cr = ctx.get_cairo_context()
        x, y, w, h, rotated = layout(img.width, img.height, ctx.get_width(), ctx.get_height(), settings)
        cr.save()
        cr.translate(x, y)
        if rotated:
            cr.translate(w, 0)
            cr.rotate(1.5707963267948966)
            cr.scale(h / img.width, w / img.height)
        else:
            cr.scale(w / img.width, h / img.height)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()

    op.connect("draw-page", draw_page)
    if settings.default_printer_override:
        ps = Gtk.PrintSettings()
        ps.set_printer(settings.default_printer_override)
        op.set_print_settings(ps)
    action = Gtk.PrintOperationAction.PRINT_DIALOG
    if not settings.show_print_dialog or app.store.app.dont_show_print_settings_dialog:
        action = Gtk.PrintOperationAction.PRINT
    try:
        res = op.run(action, parent or app.active_parent())
        if res == Gtk.PrintOperationResult.ERROR:
            raise RuntimeError("print operation failed")
    except Exception as e:
        log.warning("GTK printing failed (%s); trying lp", e)
        if not lp_print(img, settings.default_printer_override):
            app.message("Printing failed", str(e), urgent=True)
