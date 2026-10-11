"""OCR window (ShareX OCRForm) on tesseract.

The image preview, the language picker (installed tesseract languages from ``list_languages()``; a "+"
combination such as ``eng+deu`` stored in the options is offered too), the scale factor (1–4, step 0.5, like
ShareX), "Single line", "Select region…" (re-capture with the region overlay) and "Open image…", the
recognised text (editable), "Copy all", ShareX's service links (Google Translate, Google Search, …: opens the
text in the browser), "Auto copy" and "Close window after copying".

Changing the language, scale or single line re-runs OCR (the previous tesseract run is cancelled). OCR runs
on a worker thread. The options passed in are edited in place like ShareX's OCRForm and the same values
are written to the default task settings' OCR options, which are saved.
"""
from __future__ import annotations

import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from PIL import Image

from ...core import clipboard, desktop
from ...core.config import OCROptions
from ...core.enums import RegionCaptureMode
from ...tools import ocr as ocrmod
from ..gtkutil import IMAGE_PATTERNS, open_files, texture_from_pil
from ._base import ProgressPanel, ToolWindow, button, dropdown, existing, single, spin, text_of, text_view

# ShareX OCROptions.DefaultServiceLinks
SERVICE_LINKS = [
    ("Google Translate", "https://translate.google.com/?sl=auto&tl=en&text={0}&op=translate"),
    ("Google Search", "https://www.google.com/search?q={0}"),
    ("Google Images", "https://www.google.com/search?q={0}&tbm=isch"),
    ("Bing", "https://www.bing.com/search?q={0}"),
    ("DuckDuckGo", "https://duckduckgo.com/?q={0}"),
    ("DeepL", "https://www.deepl.com/translator#auto/en/{0}"),
]

_OPTION_FIELDS = ("language", "scale_factor", "single_line", "auto_copy", "close_window_after_copying")


def service_url(index: int, text: str) -> str:
    return SERVICE_LINKS[index][1].format(urllib.parse.quote(text, safe=""))


class OCRWindow(ToolWindow):
    def __init__(self, app, img: Image.Image | None, options: OCROptions | None) -> None:
        super().__init__(app, "OCR", 860, 560)
        self.options = options if options is not None else app.store.app.default_task_settings.capture_settings.ocr
        self.image = img
        self.languages: list[str] = []
        self.result = ""
        self.ocr_worker = None
        self._loading = True
        self.runs = 0  # completed OCR runs (tests)

        self.banner = Adw.Banner(title="", revealed=False)

        # -- left: preview --
        self.preview = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        frame = Gtk.Frame(child=self.preview)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        left.append(frame)
        srow = Gtk.Box(spacing=6)
        self.region_button = button("Select region…", self.select_region, "Capture a new region of the screen")
        self.open_button = button("Open image…", self.open_image, "Recognise text in an image file")
        srow.append(self.region_button)
        srow.append(self.open_button)
        left.append(srow)

        # -- right: options + text --
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        grid.attach(Gtk.Label(label="Language:", xalign=0), 0, 0, 1, 1)
        self.language = dropdown([], tooltip="OCR language")
        self.language.set_hexpand(True)
        self.language.connect("notify::selected", lambda *_: self._language_changed())
        grid.attach(self.language, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Scale factor:", xalign=0), 0, 1, 1, 1)
        self.scale = spin(min(4.0, max(1.0, float(self.options.scale_factor))), 1, 4, 0.5, 1,
                          tooltip="Scale factor (upscale small text before recognition)")
        self.scale.connect("value-changed", lambda *_: self._option_changed("scale_factor",
                                                                             float(self.scale.get_value())))
        grid.attach(self.scale, 1, 1, 1, 1)
        self.single_line = Gtk.CheckButton(label="Single line", active=self.options.single_line)
        self.single_line.connect("toggled", lambda b: self._option_changed("single_line", b.get_active()))
        grid.attach(self.single_line, 0, 2, 2, 1)
        right.append(grid)

        right.append(Gtk.Label(label="Result:", xalign=0))
        self.text, sw = text_view(height=200)
        self.text.set_tooltip_text("Recognised text")
        self.text.get_buffer().connect("changed", lambda *_: self._text_changed())
        right.append(sw)

        brow = Gtk.Box(spacing=6)
        self.copy_button = button("Copy all", self.copy_all, "Copy the text")
        brow.append(self.copy_button)
        self.service = dropdown([n for n, _u in SERVICE_LINKS], tooltip="Service")
        brow.append(self.service)
        self.service_button = button("Open", self.open_service, "Open the text with the selected service")
        brow.append(self.service_button)
        right.append(brow)
        self.auto_copy = Gtk.CheckButton(label="Auto copy", active=self.options.auto_copy,
                                         tooltip_text="Copy the text when recognition finishes")
        self.auto_copy.connect("toggled", lambda b: self._save_option("auto_copy", b.get_active()))
        self.close_after = Gtk.CheckButton(label="Close window after copying",
                                           active=self.options.close_window_after_copying)
        self.close_after.connect("toggled", lambda b: self._save_option("close_window_after_copying",
                                                                         b.get_active()))
        right.append(self.auto_copy)
        right.append(self.close_after)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=left, end_child=right,
                          position=400, resize_start_child=True, shrink_start_child=False, shrink_end_child=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self.banner)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                        margin_start=8, margin_end=8, vexpand=True)
        inner.append(paned)
        body.append(inner)
        self.set_body(body)
        self.progress = ProgressPanel()
        self.progress.set_margin_start(8)
        self.progress.set_margin_end(8)
        self.progress.set_margin_bottom(6)
        self.add_bottom(self.progress)

        self._show_image()
        self._text_changed()
        self._load_languages()

    # -- languages --

    def _load_languages(self) -> None:
        if not ocrmod.is_available():
            self._unavailable("Tesseract is not installed. Install it with: sudo apt install tesseract-ocr "
                              "(and tesseract-ocr-<lang> for more languages).")
            return

        def done(langs):
            self.languages = list(langs)
            want = self.options.language or ""
            parts = [p for p in want.replace(",", "+").split("+") if p]
            if len(parts) > 1 and all(p in self.languages for p in parts):
                self.languages.append("+".join(parts))
            if not self.languages:
                self._unavailable("No tesseract languages are installed (sudo apt install tesseract-ocr-eng).")
                return
            model = self.language.get_model()
            model.splice(0, model.get_n_items(), self.languages)
            idx = self.languages.index("+".join(parts)) if "+".join(parts) in self.languages else 0
            self.language.set_selected(idx)
            if self.options.language != self.languages[idx]:
                self._save_option("language", self.languages[idx])
            self._loading = False
            self.run_ocr()

        self.run(lambda _p, _c: ocrmod.list_languages(), done,
                 on_error=lambda e: self._unavailable(f"Could not list tesseract languages: {e}"))

    def _unavailable(self, text: str) -> None:
        self.banner.set_title(text)
        self.banner.set_revealed(True)
        for w in (self.language, self.scale, self.single_line, self.region_button, self.open_button):
            w.set_sensitive(False)
        self._loading = True

    # -- options --

    def _save_option(self, name: str, value) -> None:
        setattr(self.options, name, value)
        default = self.app.store.app.default_task_settings.capture_settings.ocr
        if default is not self.options:
            setattr(default, name, value)
        self.app.store.save_app()

    def _option_changed(self, name: str, value) -> None:
        self._save_option(name, value)
        if not self._loading:
            self.run_ocr()

    def _language_changed(self) -> None:
        if self._loading:
            return
        i = self.language.get_selected()
        if 0 <= i < len(self.languages):
            self._option_changed("language", self.languages[i])

    # -- image --

    def _show_image(self) -> None:
        self.preview.set_paintable(texture_from_pil(self.image) if self.image is not None else None)

    def set_image(self, img: Image.Image) -> None:
        self.image = img
        self._show_image()
        if not self._loading:
            self.run_ocr()

    def select_region(self) -> None:
        self.set_visible(False)
        ts = self.app.store.task_settings_for(None)

        def outcome(o):
            self.present()
            from ...editor.manager import RegionResult
            if o.image is not None and getattr(o.result, "result", None) != RegionResult.Close:
                self.set_image(o.image)
        self.app.run_region(RegionCaptureMode.Default, ts, outcome)

    def open_image(self) -> None:
        def done(paths):
            if paths:
                self.load_file(paths[0])
        open_files(self, "Open image", done, patterns=IMAGE_PATTERNS, multiple=False)

    def load_file(self, path: str) -> None:
        from ...core import imaging
        try:
            self.set_image(imaging.load(path))
        except OSError as e:
            self.error("Could not open image", e)

    # -- OCR --

    @property
    def busy_ocr(self) -> bool:
        return self.ocr_worker is not None and self.ocr_worker.running

    def run_ocr(self) -> None:
        if self.image is None or not self.options.language:
            return
        if self.ocr_worker is not None and self.ocr_worker.running:
            self.ocr_worker.cancel()
        img = self.image.copy()
        lang, scale, single_line = self.options.language, float(self.options.scale_factor), self.options.single_line
        self.text.get_buffer().set_text("")
        worker_box: dict = {}

        def work(_progress, cancel):
            return ocrmod.ocr_image(img, lang, scale, single_line, cancel)

        def done(text):
            if self.ocr_worker is not worker_box.get("w"):
                return  # superseded
            self.result = text
            self.text.get_buffer().set_text(text)
            self.runs += 1
            if self.options.auto_copy and text.strip():
                clipboard.set_text(text)

        def error(e):
            if self.ocr_worker is worker_box.get("w"):
                self.error("OCR failed", e)

        w = self.run(work, done, on_error=error, panel=self.progress, status="Recognising…")
        worker_box["w"] = w
        self.ocr_worker = w

    # -- text actions --

    def _text_changed(self) -> None:
        self.result = text_of(self.text).strip()
        has = bool(self.result)
        self.copy_button.set_sensitive(has)
        self.service_button.set_sensitive(has)

    def copy_all(self) -> None:
        text = text_of(self.text)
        if not text:
            return
        clipboard.set_text(text)
        self.toast("Copied")
        if self.options.close_window_after_copying:
            self.close()

    def open_service(self) -> None:
        if self.result:
            desktop.open_url(service_url(self.service.get_selected(), self.result))


def open_ocr(app, img: Image.Image | None, ocr_options: OCROptions | None = None) -> OCRWindow:
    """Show the OCR window for ``img`` (re-presents the open one and recognises the new image)."""
    win = existing("ocr")
    if win is not None:
        if ocr_options is not None:
            win.options = ocr_options
        if img is not None:
            win.set_image(img)
        win.present()
        return win
    return single("ocr", lambda: OCRWindow(app, img, ocr_options))


def open(app, ctx) -> OCRWindow:  # noqa: A001 - the tool contract's name
    """The OCR tool from the job table: an image file given on the command line, else the region overlay."""
    opts = ctx.task_settings.capture_settings.ocr
    if ctx.files:
        from ...core import imaging
        return open_ocr(app, imaging.load(ctx.files[0]), opts)
    win = open_ocr(app, None, opts)
    win.select_region()
    return win
