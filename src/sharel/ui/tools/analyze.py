"""Analyze image: dimensions, format, file size, transparency, colour statistics, dominant colours, hashes and an
EXIF summary of an image, every value copyable.

``open(app, ctx)`` (Tools → Analyze image: ``ctx.files``, a chooser or the clipboard) and
``open_analyze(app, img, path)`` (the after-capture "Analyze image" task). The analysis runs on a thread.

Like ShareX 18, it can also send the image to an AI provider for a description (OpenAI, Gemini, OpenRouter
or any OpenAI-compatible server: ``tools/ai.py``). That runs only once a provider has an API key (AI settings,
key kept in the keyring); without one nothing leaves the machine and only the local analysis is shown.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from gi.repository import Adw, GLib, Gtk
from PIL import Image

from ...core import clipboard
from ...core.jobs import JobContext
from ...tools import ai
from .. import gtkutil
from ..forms import Form
from . import _base

EXIF_SUMMARY = ("Make", "Model", "LensModel", "DateTimeOriginal", "DateTime", "ExposureTime", "FNumber",
                "ISOSpeedRatings", "PhotographicSensitivity", "FocalLength", "Flash", "Software", "Artist",
                "Copyright", "ImageDescription", "Orientation")


@dataclass
class Analysis:
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    dominant: list[tuple[str, float]] = field(default_factory=list)  # ("#RRGGBB", percent)

    def value(self, section: str, key: str) -> str:
        for s, k, v in self.rows:
            if s == section and k == key:
                return v
        raise KeyError((section, key))

    def as_text(self) -> str:
        out, last = [], None
        for s, k, v in self.rows:
            if s != last:
                if out:
                    out.append("")
                out.append(f"[{s}]")
                last = s
            out.append(f"{k}: {v}")
        return "\n".join(out)


def _ratio(w: int, h: int) -> str:
    from math import gcd
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g} ({w / h:.3f})" if h else "-"


def dhash(img: Image.Image) -> str:
    """64-bit difference hash (perceptual): equal for visually identical images at any size."""
    g = np.asarray(img.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def dominant_colors(img: Image.Image, count: int = 8) -> list[tuple[str, float]]:
    rgba = img.convert("RGBA")
    small = rgba.copy()
    small.thumbnail((256, 256))
    arr = np.asarray(small)
    opaque = arr[..., 3] > 0
    if not opaque.any():
        return []
    pixels = arr[opaque][:, :3]
    strip = Image.fromarray(pixels.reshape(1, -1, 3), "RGB")
    q = strip.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for c, idx in counts:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append((f"#{r:02X}{g:02X}{b:02X}", 100.0 * c / total))
    return out


def analyze(img: Image.Image, path: str = "") -> Analysis:
    a = Analysis()
    add = a.rows.append
    w, h = img.size
    fmt = "In memory (not saved)"
    file_bytes = b""
    if path and os.path.isfile(path):
        try:
            with Image.open(path) as im:
                fmt = im.format or "Unknown"
                frames = getattr(im, "n_frames", 1)
                dpi = im.info.get("dpi")
        except OSError:
            frames, dpi = 1, None
        file_bytes = Path(path).read_bytes()
    else:
        frames, dpi = 1, img.info.get("dpi")
    add(("General", "File name", os.path.basename(path) if path else "-"))
    if path:
        add(("General", "Path", path))
    add(("General", "Dimensions", f"{w} × {h} px"))
    add(("General", "Megapixels", f"{w * h / 1_000_000:.2f} MP"))
    add(("General", "Aspect ratio", _ratio(w, h)))
    add(("General", "Format", fmt))
    add(("General", "Color mode", img.mode))
    if frames > 1:
        add(("General", "Frames", str(frames)))
    if dpi:
        try:
            add(("General", "DPI", f"{float(dpi[0]):g} × {float(dpi[1]):g}"))
        except (TypeError, ValueError, IndexError):
            pass
    if file_bytes:
        add(("General", "File size", f"{gtkutil.human_size(len(file_bytes))} ({len(file_bytes):,} bytes)"))
        add(("General", "Bits per pixel (compressed)", f"{len(file_bytes) * 8 / max(1, w * h):.2f}"))

    rgba = np.asarray(img.convert("RGBA"))
    alpha = rgba[..., 3]
    n = alpha.size
    transparent = int((alpha == 0).sum())
    semi = int(((alpha > 0) & (alpha < 255)).sum())
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    add(("Transparency", "Alpha channel", "Yes" if has_alpha else "No"))
    add(("Transparency", "Transparent pixels", f"{transparent:,} ({100 * transparent / n:.2f}%)"))
    add(("Transparency", "Semi-transparent pixels", f"{semi:,} ({100 * semi / n:.2f}%)"))
    add(("Transparency", "Opaque pixels", f"{n - transparent - semi:,} ({100 * (n - transparent - semi) / n:.2f}%)"))

    packed = rgba.reshape(-1, 4).view(np.uint32).ravel()
    unique = int(np.unique(packed).size)
    rgb = rgba[..., :3].reshape(-1, 3).astype(np.float64)
    visible = alpha.ravel() > 0
    stats = rgb[visible] if visible.any() else rgb
    mean = stats.mean(axis=0)
    std = stats.std(axis=0)
    lum = stats @ np.array([0.299, 0.587, 0.114])
    add(("Colors", "Unique colors", f"{unique:,}"))
    add(("Colors", "Average color", "#{:02X}{:02X}{:02X}".format(*(int(round(v)) for v in mean))))
    add(("Colors", "Average (R, G, B)", ", ".join(f"{v:.1f}" for v in mean)))
    add(("Colors", "Standard deviation (R, G, B)", ", ".join(f"{v:.1f}" for v in std)))
    add(("Colors", "Min (R, G, B)", ", ".join(str(int(v)) for v in stats.min(axis=0))))
    add(("Colors", "Max (R, G, B)", ", ".join(str(int(v)) for v in stats.max(axis=0))))
    add(("Colors", "Brightness", f"{lum.mean():.1f} / 255 ({100 * lum.mean() / 255:.1f}%)"))
    gray = bool(np.all(stats[:, 0] == stats[:, 1]) and np.all(stats[:, 1] == stats[:, 2]))
    add(("Colors", "Grayscale", "Yes" if gray else "No"))

    a.dominant = dominant_colors(img)
    for i, (hexc, pct) in enumerate(a.dominant, 1):
        add(("Dominant colors", f"#{i}", f"{hexc} ({pct:.1f}%)"))

    data = file_bytes or _png(img)
    of = "file" if file_bytes else "PNG data"
    add(("Hashes", f"MD5 ({of})", hashlib.md5(data).hexdigest()))
    add(("Hashes", f"SHA-1 ({of})", hashlib.sha1(data).hexdigest()))
    add(("Hashes", f"SHA-256 ({of})", hashlib.sha256(data).hexdigest()))
    add(("Hashes", "Pixel data MD5", hashlib.md5(rgba.tobytes()).hexdigest()))
    add(("Hashes", "Perceptual hash (dHash)", dhash(img)))

    exif_rows = []
    if path and os.path.isfile(path):
        from ...tools.metadata import read_metadata
        try:
            md = read_metadata(path)
        except OSError:
            md = None
        if md is not None and md.image is not None:
            by_name = {e.name: e.value for e in md.image.exif}
            exif_rows = [(k, by_name[k]) for k in EXIF_SUMMARY if k in by_name]
            if md.image.latitude is not None and md.image.longitude is not None:
                exif_rows.append(("GPS position", f"{md.image.latitude:.6f}, {md.image.longitude:.6f}"))
            exif_rows.append(("EXIF tags", str(len(md.image.exif))))
    if exif_rows:
        for k, v in exif_rows:
            add(("EXIF", k, v))
    else:
        add(("EXIF", "EXIF", "No EXIF data"))
    return a


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _swatch(hex_color: str) -> Gtk.Widget:
    from ..forms import rgba_from_hex
    area = Gtk.DrawingArea(content_width=28, content_height=20, valign=Gtk.Align.CENTER)
    rgba = rgba_from_hex(hex_color)

    def draw(_a, cr, w, h):
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
    area.set_draw_func(draw)
    return area


class AnalyzeWindow(_base.ToolWindow):
    """One window per analysed image (several captures may be analysed at once)."""

    def __init__(self, app, img: Image.Image | None = None, path: str = "") -> None:
        super().__init__(app, "Analyze image", 720, 760)
        self.image = img
        self.path = path
        self.analysis: Analysis | None = None
        self.done = threading.Event()
        open_btn = _base.button("Open…", self.browse, tooltip="Analyze an image file")
        clip_btn = _base.button("From clipboard", self.from_clipboard)
        self.header.pack_start(open_btn)
        self.header.pack_start(clip_btn)
        self.copy_button = _base.button("Copy all", self.copy_all)
        self.header.pack_end(self.copy_button)
        self.header.pack_end(_base.button("AI settings", self.open_ai_settings, icon="preferences-system-symbolic",
                                          tooltip="AI provider for image descriptions"))
        self.ai_text = ""
        self.ai_error = ""
        self.ai_done = threading.Event()
        self.ai_group = Adw.PreferencesGroup(title="AI description")
        self.ai_prompt = Adw.EntryRow(title="Prompt")
        self.ai_prompt.update_property([Gtk.AccessibleProperty.LABEL], ["Prompt"])
        self.ai_button = _base.button("Describe", self.describe, suggested=True)
        self.ai_button.set_valign(Gtk.Align.CENTER)
        self.ai_prompt.add_suffix(self.ai_button)
        self.ai_result = Gtk.Label(wrap=True, selectable=True, xalign=0, margin_top=8, margin_bottom=8,
                                   margin_start=12, margin_end=12)
        self.ai_result.update_property([Gtk.AccessibleProperty.LABEL], ["AI description"])
        self.ai_off = Adw.ActionRow(title="AI description is off", use_markup=False,
                                    subtitle="Add an API key in AI settings to describe images with OpenAI, Gemini, "
                                             "OpenRouter or an OpenAI-compatible server. Nothing is sent until then.")
        self.ai_off.set_subtitle_lines(0)
        self.ai_group.add(self.ai_off)
        self.ai_group.add(self.ai_prompt)
        self.ai_group.add(self.ai_result)
        self.thumb = _base.ImagePane("Open an image to analyze", 200, 160)
        self.spinner = Gtk.Spinner()
        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        box.append(self.thumb)
        box.append(self.spinner)
        box.append(self.ai_group)
        box.append(self.groups_box)
        self.set_body(_base.scrolled(box))
        self.add_controller(_base.image_drop_target(lambda ps: self.load(ps[0])))
        self.copy_button.set_sensitive(False)
        self._sync_ai()
        if img is not None:
            self.set_image(img, path)

    def browse(self) -> None:
        gtkutil.open_files(self, "Analyze image", lambda ps: ps and self.load(ps[0]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def from_clipboard(self) -> None:
        got = _base.clipboard_image()
        if got is None:
            self.error("Clipboard", "The clipboard has no image.")
            return
        self.set_image(*got)

    def load(self, path: str) -> None:
        try:
            img = _base.load_image_file(path)
        except OSError as e:
            self.error("Can't open image", f"{path}: {e}")
            return
        self.set_image(img, os.path.abspath(path))

    def set_image(self, img: Image.Image, path: str = "") -> None:
        self.image, self.path = img, path
        self.analysis = None
        self.done.clear()
        self.set_title(f"Analyze image — {os.path.basename(path)}" if path else "Analyze image")
        self.thumb.set_image(img)
        self.spinner.start()
        self.copy_button.set_sensitive(False)

        def work():
            try:
                res, err = analyze(img, path), None
            except Exception as e:
                res, err = None, str(e)
            gtkutil.idle(self._show, img, res, err)
        threading.Thread(target=work, daemon=True, name="sharel-analyze").start()
        self.ai_done.clear()
        self.ai_text = self.ai_error = ""
        self.ai_result.set_text("")
        if ai.options(self.app).configured:
            self.describe()  # ShareX's AutoStartAnalyze
        else:
            self.ai_done.set()

    # -- AI description --

    def _sync_ai(self) -> None:
        opts = ai.options(self.app)
        on = opts.configured
        self.ai_off.set_visible(not on)
        self.ai_prompt.set_visible(on)
        self.ai_result.set_visible(on and bool(self.ai_result.get_text()))
        if not self.ai_prompt.get_text():
            self.ai_prompt.set_text(opts.input or ai.DEFAULT_PROMPT)
        self.ai_group.set_description(ai.PROVIDER_TITLES[opts.provider] if on else "")

    def describe(self) -> None:
        opts = ai.options(self.app)
        img = self.image
        if img is None or not opts.configured:
            self.ai_done.set()
            return
        prompt = self.ai_prompt.get_text().strip() or opts.input
        proxies = self.app.store.app.proxy_settings.requests_proxies()
        self.ai_done.clear()
        self.ai_button.set_sensitive(False)
        self.ai_result.set_text("Describing…")
        self.ai_result.set_visible(True)

        def work():
            try:
                text, err = ai.describe(img, opts, prompt, proxies=proxies), ""
            except ai.AIError as e:
                text, err = "", str(e)
            gtkutil.idle(self._ai_show, img, text, err, opts.auto_copy_result)
        threading.Thread(target=work, daemon=True, name="sharel-ai-describe").start()

    def _ai_show(self, img, text: str, err: str, copy: bool) -> None:
        if img is not self.image:
            return
        self.ai_button.set_sensitive(True)
        self.ai_text, self.ai_error = text, err
        self.ai_result.set_text(text if not err else f"The AI provider failed: {err}")
        if text and copy:
            clipboard.set_text(text)
            self.toast("Description copied")
        self.ai_done.set()

    def open_ai_settings(self) -> AISettingsWindow:
        win = AISettingsWindow(self.app, on_saved=self._sync_ai)
        win.set_transient_for(self)
        win.present()
        return win

    def _show(self, img, res: Analysis | None, err) -> None:
        if img is not self.image:
            return  # a newer image is being analysed
        self.spinner.stop()
        if err is not None:
            self.error("Analysis failed", err)
            self.done.set()
            return
        self.analysis = res
        child = self.groups_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.groups_box.remove(child)
            child = nxt
        groups: dict[str, Adw.PreferencesGroup] = {}
        swatches = dict(res.dominant)
        for section, key, value in res.rows:
            g = groups.get(section)
            if g is None:
                g = groups[section] = Adw.PreferencesGroup(title=section)
                self.groups_box.append(g)
            row = Adw.ActionRow(title=GLib.markup_escape_text(key), subtitle=GLib.markup_escape_text(value),
                                subtitle_selectable=True)
            hexc = value.split(" ")[0]
            if (section == "Dominant colors" and hexc in swatches) or key == "Average color":
                row.add_prefix(_swatch(hexc))
            b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Copy {key}")
            b.add_css_class("flat")
            b.connect("clicked", lambda *_a, v=value: clipboard.set_text(v))
            row.add_suffix(b)
            g.add(row)
        self.copy_button.set_sensitive(True)
        self.done.set()

    def copy_all(self) -> None:
        if self.analysis is not None:
            text = self.analysis.as_text()
            if self.ai_text:
                text = f"[AI description]\n{self.ai_text}\n\n{text}"
            clipboard.set_text(text)
            self.toast("Analysis copied")


class AISettingsWindow(_base.ToolWindow):
    """ShareX's AI options: provider, keys (kept in the keyring), models, prompt; Test connection."""

    def __init__(self, app, on_saved=None) -> None:
        super().__init__(app, "AI settings", 560, 640)
        self.opts = ai.options(app)
        self.on_saved = on_saved
        self.form = Form(self.opts, self.save, parent=self)
        self.status = Gtk.Label(wrap=True, xalign=0)
        test = _base.button("Test connection", self.test)
        self.test_button = test
        self.test_done = threading.Event()
        page = Adw.PreferencesPage()
        page.add(self.form.group("Provider", "API keys are stored in the keyring when one is available."))
        g = Adw.PreferencesGroup()
        row = Adw.ActionRow(title="Connection")
        test.set_valign(Gtk.Align.CENTER)
        row.add_suffix(test)
        g.add(row)
        g.add(self.status)
        page.add(g)
        self.set_body(page)

    def save(self) -> None:
        self.app.store.save_uploaders()
        if self.on_saved:
            self.on_saved()

    def test(self) -> None:
        opts = self.opts
        proxies = self.app.store.app.proxy_settings.requests_proxies()
        self.test_done.clear()
        self.status.set_text("Testing…")

        def work():
            ok, msg = ai.check_connection(opts, proxies=proxies)
            gtkutil.idle(self._tested, ok, msg)
        threading.Thread(target=work, daemon=True, name="sharel-ai-test").start()

    def _tested(self, ok: bool, msg: str) -> None:
        self.status.set_text(msg)
        self.test_ok = ok
        self.test_done.set()


_windows = _base.WindowList()


def open_analyze(app, img: Image.Image, path: str = "") -> AnalyzeWindow:
    win = _windows.add(AnalyzeWindow(app, img, path))
    win.present()
    return win


def open_windows() -> list[AnalyzeWindow]:
    return _windows.live()


def open(app, ctx: JobContext) -> AnalyzeWindow | None:  # noqa: A001
    for f in ctx.files:
        if _base.is_image_path(f):
            try:
                img = _base.load_image_file(f)
            except OSError as e:
                app.message("Analyze image", f"{f}: {e}", urgent=True)
                return None
            return open_analyze(app, img, os.path.abspath(f))
    win = _windows.add(AnalyzeWindow(app))
    win.present()
    return win
