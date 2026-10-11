"""QR code window (ShareX QRCodeForm) and the decoded-results window.

Encode tab: the text box drives a live QR image (``sharel.tools.qr.encode``); size (0 = fit the preview, at
least 64 px, like ShareX), error correction (L/M/Q/H) and quiet-zone border; "Copy image", "Save image…"
(PNG, JPEG, BMP or SVG like ShareX's save dialog) and "Upload image". ShareX's content limit applies (2952
UTF-8 bytes, ``TaskHelpers.CheckQRCodeContent``).

Decode tab: "From screen" (full-screen capture), "From region" (region overlay), "From file…" and
"From clipboard" (an image on the clipboard, or an image file copied in the file manager). Every code found is
listed (ShareX joins them with a blank line); "Copy", "Open URL" when the result is a link, and
"Encode this text" (ShareX puts scanned text into the encode box).

``open_qr_results(app, text, img)`` is the window the QRCodeDecodeFromScreen / QRCodeScanRegion jobs and the
ScanQRCode after-capture task show: the scanned image, each result with Copy and Open, and Copy all.
"""
from __future__ import annotations

import io
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from PIL import Image

from ...core import clipboard, desktop
from ...core.enums import RegionCaptureMode
from ...tools import qr as qrmod
from ..gtkutil import IMAGE_PATTERNS, open_files, save_file, texture_from_pil
from ._base import ProgressPanel, ToolWindow, button, dropdown, existing, single, spin, text_of, text_view

MAX_BYTES = 2952  # ShareX TaskHelpers.CheckQRCodeContent
EC_LEVELS = ["L", "M", "Q", "H"]
EC_LABELS = ["L (7%)", "M (15%)", "Q (25%)", "H (30%)"]
SAVE_PATTERNS = ["*.png", "*.jpg", "*.jpeg", "*.bmp", "*.svg"]


def check_content(text: str) -> bool:
    return bool(text) and len(text.encode("utf-8")) <= MAX_BYTES


def is_url(text: str) -> bool:
    from urllib.parse import urlparse
    t = text.strip()
    if not t or "\n" in t or " " in t:
        return False
    u = urlparse(t)
    return u.scheme in ("http", "https", "ftp") and bool(u.netloc)


def _as_list(text) -> list[str]:
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [str(t) for t in text if str(t)]
    return [text] if str(text) else []


def _safe_name(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in text.strip())[:60].strip(" ._")
    return keep or "QRCode"


def save_qr(path: str, text: str, img: Image.Image, error_correction: str = "M", border: int = 4) -> str:
    """Save the code like ShareX's Save image: SVG is re-encoded as vectors, the rest from the bitmap."""
    p = Path(path)
    if not p.suffix:
        p = p.with_suffix(".png")
    ext = p.suffix.lower()
    if ext == ".svg":
        import qrcode
        from qrcode.image.svg import SvgPathImage
        q = qrcode.QRCode(error_correction=getattr(qrcode.constants, f"ERROR_CORRECT_{error_correction}"),
                          border=border, image_factory=SvgPathImage)
        q.add_data(text.encode("utf-8"))
        q.make(fit=True)
        buf = io.BytesIO()
        q.make_image().save(buf)
        p.write_bytes(buf.getvalue())
    else:
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP"}.get(ext, "PNG")
        img.convert("RGB").save(p, fmt)
    return str(p)


class QRCodeWindow(ToolWindow):
    def __init__(self, app, ctx=None, text: str = "") -> None:
        super().__init__(app, "QR code", 760, 560)
        self.ts = ctx.task_settings if ctx is not None else app.store.task_settings_for(None)
        self.qr_image: Image.Image | None = None
        self.scan_image: Image.Image | None = None
        self.results: list[str] = []
        self.decodes = 0  # finished scans (tests)
        self._regen_source = 0

        self.stack = Adw.ViewStack()
        self.header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))

        # ---- encode ----
        enc = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        left.append(Gtk.Label(label="Text to encode:", xalign=0))
        self.text, sw = text_view(height=160)
        self.text.set_tooltip_text("Input text to encode")
        self.text.get_buffer().connect("changed", lambda *_: self.schedule_regenerate())
        left.append(sw)
        self.limit_label = Gtk.Label(xalign=0)
        self.limit_label.add_css_class("dim-label")
        left.append(self.limit_label)
        opts = Gtk.Grid(row_spacing=6, column_spacing=8)
        opts.attach(Gtk.Label(label="Size (px, 0 = fit):", xalign=0), 0, 0, 1, 1)
        self.size = spin(0, 0, 4096, 16, 0, tooltip="QR code size")
        self.size.connect("value-changed", lambda *_: self.regenerate())
        opts.attach(self.size, 1, 0, 1, 1)
        opts.attach(Gtk.Label(label="Error correction:", xalign=0), 0, 1, 1, 1)
        self.ec = dropdown(EC_LABELS, 1, tooltip="Error correction level")
        self.ec.connect("notify::selected", lambda *_: self.regenerate())
        opts.attach(self.ec, 1, 1, 1, 1)
        opts.attach(Gtk.Label(label="Border (modules):", xalign=0), 0, 2, 1, 1)
        self.border = spin(4, 0, 20, 1, 0, tooltip="Quiet zone border")
        self.border.connect("value-changed", lambda *_: self.regenerate())
        opts.attach(self.border, 1, 2, 1, 1)
        left.append(opts)
        brow = Gtk.Box(spacing=6)
        self.copy_image_button = button("Copy image", self.copy_image, "Copy the QR code image")
        self.save_image_button = button("Save image…", self.save_image, "Save the QR code image")
        self.upload_button = button("Upload image", self.upload_image, "Upload the QR code image")
        for b in (self.copy_image_button, self.save_image_button, self.upload_button):
            brow.append(b)
        left.append(brow)
        enc.append(left)
        self.qr_picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, hexpand=True,
                                      vexpand=True, width_request=280, height_request=280)
        # "0 = fit": redraw at the real preview size once the window is laid out
        self.connect("map", lambda *_: GLib.timeout_add(100, lambda: (self.size.get_value() == 0 and
                                                                      self.regenerate(), False)[1]))
        enc.append(Gtk.Frame(child=self.qr_picture))
        self.stack.add_titled_with_icon(enc, "encode", "Encode", "insert-text-symbolic")

        # ---- decode ----
        dec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        srow = Gtk.Box(spacing=6)
        srow.append(button("From screen", self.scan_screen, "Scan the whole screen for QR codes"))
        srow.append(button("From region", self.scan_region, "Select a region of the screen to scan"))
        srow.append(button("From file…", self.scan_file_dialog, "Scan an image file"))
        srow.append(button("From clipboard", self.scan_clipboard, "Scan the image on the clipboard"))
        dec.append(srow)
        dpane = Gtk.Box(spacing=12, vexpand=True)
        self.scan_picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, hexpand=True,
                                        width_request=240)
        dpane.append(Gtk.Frame(child=self.scan_picture))
        rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.status = Gtk.Label(xalign=0, label="Scan the screen, a region, a file or the clipboard.")
        rbox.append(self.status)
        self.decoded, dsw = text_view(height=180)
        self.decoded.set_tooltip_text("Decoded text")
        self.decoded.get_buffer().connect("changed", lambda *_: self._decoded_changed())
        rbox.append(dsw)
        arow = Gtk.Box(spacing=6)
        self.copy_decoded_button = button("Copy", self.copy_decoded, "Copy the decoded text")
        self.open_url_button = button("Open URL", self.open_decoded_url, "Open the decoded link")
        self.encode_this_button = button("Encode this text", self.encode_decoded, "Put the text in the encoder")
        for b in (self.copy_decoded_button, self.open_url_button, self.encode_this_button):
            arow.append(b)
        rbox.append(arow)
        dpane.append(rbox)
        dec.append(dpane)
        self.progress = ProgressPanel()
        dec.append(self.progress)
        self.stack.add_titled_with_icon(dec, "decode", "Decode", "find-location-symbolic")

        self.set_body(self.stack)
        self.set_text(text)
        self._decoded_changed()

    # ---- encode ----

    def get_text(self) -> str:
        return text_of(self.text)

    def set_text(self, text: str) -> None:
        self.text.get_buffer().set_text(text or "")
        self.regenerate()

    @property
    def error_correction(self) -> str:
        return EC_LEVELS[self.ec.get_selected()]

    def schedule_regenerate(self) -> None:
        if self._regen_source:
            GLib.source_remove(self._regen_source)

        def go():
            self._regen_source = 0
            self.regenerate()
            return GLib.SOURCE_REMOVE
        self._regen_source = GLib.timeout_add(120, go)

    def regenerate(self) -> None:
        if self._regen_source:
            GLib.source_remove(self._regen_source)
            self._regen_source = 0
        text = self.get_text()
        n = len(text.encode("utf-8"))
        self.qr_image = None
        if not text:
            self.limit_label.set_label("")
        elif n > MAX_BYTES:
            self.limit_label.set_label(f"Too long for a QR code: {n} of {MAX_BYTES} bytes.")
        else:
            self.limit_label.set_label(f"{n} / {MAX_BYTES} bytes")
            size = int(self.size.get_value())
            if size <= 0:
                alloc = min(self.qr_picture.get_width(), self.qr_picture.get_height())
                size = alloc if alloc > 0 else 256
            size = max(size, 64)
            try:
                self.qr_image = qrmod.encode(text, size, int(self.border.get_value()), self.error_correction)
            except Exception as e:  # qrcode DataOverflowError: too big for this level
                self.limit_label.set_label(str(e))
        self.qr_picture.set_paintable(texture_from_pil(self.qr_image) if self.qr_image is not None else None)
        for b in (self.copy_image_button, self.save_image_button, self.upload_button):
            b.set_sensitive(self.qr_image is not None)

    def copy_image(self) -> None:
        if self.qr_image is not None:
            clipboard.set_image(self.qr_image)
            self.toast("QR code copied")

    def save_image(self) -> None:
        if self.qr_image is None:
            return
        text, img, ec, border = self.get_text(), self.qr_image.copy(), self.error_correction, \
            int(self.border.get_value())
        suggested = str(self.app.store.screenshots_folder(self.ts) / (_safe_name(text) + ".png"))

        def done(path):
            if not path:
                return
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                saved = save_qr(path, text, img, ec, border)
            except OSError as e:
                self.error("Could not save the QR code", e)
                return
            self.toast(f"Saved {Path(saved).name}")
        save_file(self, "Save QR code", suggested, done, SAVE_PATTERNS)

    def upload_image(self) -> None:
        if self.qr_image is None:
            return
        buf = io.BytesIO()
        self.qr_image.save(buf, "PNG")
        from ...core.nameparser import NameParser, NameParserType
        name = NameParser(NameParserType.FileName, image_width=self.qr_image.width,
                          image_height=self.qr_image.height).parse(self.ts.upload_settings.name_format_pattern)
        self.app.tasks.upload_data(buf.getvalue(), (name or "QRCode") + ".png", self.ts)
        self.toast("Uploading the QR code…")

    # ---- decode ----

    def show_results(self, results: list[str], img: Image.Image | None) -> None:
        self.results = list(results)
        self.scan_image = img
        self.scan_picture.set_paintable(texture_from_pil(img) if img is not None else None)
        self.decoded.get_buffer().set_text("\n\n".join(self.results))
        n = len(self.results)
        self.status.set_label("No QR code or barcode found." if n == 0 else
                              f"{n} code{'s' if n != 1 else ''} found.")

    def scan(self, img: Image.Image) -> None:
        """Decode ``img`` on a worker thread and show what it holds."""
        self.stack.set_visible_child_name("decode")
        self.status.set_label("Scanning…")
        img = img.copy()

        def done(res):
            self.decodes += 1
            self.show_results(res, img)

        def error(e):
            self.decodes += 1
            self.status.set_label("Scan failed.")
            self.error("QR code scan failed", e)
        self.run(lambda _p, cancel: qrmod.decode_image(img, cancel=cancel), done, on_error=error,
                 panel=self.progress, status="Scanning…")

    def scan_screen(self) -> None:
        from ...capture import backends
        self.set_visible(False)

        def go():
            try:
                cap = backends.capture_screen(False, self.app.backend_pref(self.ts))
            except backends.CaptureError as e:
                self.present()
                self.error("Capture failed", e)
                return GLib.SOURCE_REMOVE
            self.present()
            self.scan(cap.image)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(250, go)  # ShareX hides the form for 250 ms first

    def scan_region(self) -> None:
        self.set_visible(False)

        def outcome(o):
            self.present()
            from ...editor.manager import RegionResult
            if o.image is not None and getattr(o.result, "result", None) != RegionResult.Close:
                self.scan(o.image)
        GLib.timeout_add(250, lambda: (self.app.run_region(RegionCaptureMode.Default, self.ts, outcome), False)[1])

    def scan_file(self, path: str) -> None:
        from ...core import imaging
        try:
            img = imaging.load(path)
        except OSError as e:
            self.error("Could not open image", e)
            return
        self.scan(img)

    def scan_file_dialog(self) -> None:
        open_files(self, "Scan QR code from image file", lambda ps: ps and self.scan_file(ps[0]),
                   patterns=IMAGE_PATTERNS, multiple=False)

    def scan_clipboard(self) -> bool:
        c = clipboard.read()
        if c.kind == "image" and c.image is not None:
            self.scan(c.image)
            return True
        if c.kind == "files" and c.files:
            for f in c.files:
                if Path(f).suffix.lower() in {p[1:] for p in IMAGE_PATTERNS}:
                    self.scan_file(f)
                    return True
        self.stack.set_visible_child_name("decode")
        self.status.set_label("The clipboard has no image.")
        return False

    def _decoded_changed(self) -> None:
        t = text_of(self.decoded).strip()
        self.copy_decoded_button.set_sensitive(bool(t))
        self.encode_this_button.set_sensitive(bool(t))
        self.open_url_button.set_sensitive(is_url(t))

    def copy_decoded(self) -> None:
        t = text_of(self.decoded)
        if t:
            clipboard.set_text(t)
            self.toast("Copied")

    def open_decoded_url(self) -> None:
        t = text_of(self.decoded).strip()
        if is_url(t):
            desktop.open_url(t)

    def encode_decoded(self) -> None:
        self.set_text(text_of(self.decoded))
        self.stack.set_visible_child_name("encode")

    def on_closed(self) -> None:
        if self._regen_source:
            GLib.source_remove(self._regen_source)
            self._regen_source = 0


class QRResultsWindow(ToolWindow):
    """What a scan found: the scanned image and each decoded text with Copy / Open."""

    def __init__(self, app) -> None:
        super().__init__(app, "QR code scan", 640, 460)
        self.results: list[str] = []
        box = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, width_request=200,
                                   hexpand=True)
        box.append(Gtk.Frame(child=self.picture))
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.status = Gtk.Label(xalign=0)
        right.append(self.status)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        right.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, min_content_height=200))
        row = Gtk.Box(spacing=6)
        self.copy_all_button = button("Copy all", self.copy_all, "Copy every decoded text")
        row.append(self.copy_all_button)
        row.append(button("Open in QR code window", self.open_in_tool, "Edit / re-encode in the QR code window"))
        right.append(row)
        box.append(right)
        self.set_body(box)

    def set_results(self, results: list[str], img: Image.Image | None) -> None:
        self.results = list(results)
        self.image = img
        self.picture.set_paintable(texture_from_pil(img) if img is not None else None)
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        for text in self.results:
            r = Adw.ActionRow(title=GLib.markup_escape_text(text), title_lines=4, subtitle="URL" if is_url(text)
                              else "Text")
            r.set_title_selectable(True)
            r.add_suffix(button("Copy", lambda t=text: self.copy(t), "Copy this result"))
            if is_url(text):
                r.add_suffix(button("Open", lambda t=text: desktop.open_url(t.strip()), "Open this link"))
            self.list.append(r)
        n = len(self.results)
        self.status.set_label("No QR code or barcode found." if n == 0 else
                              f"{n} code{'s' if n != 1 else ''} found.")
        self.copy_all_button.set_sensitive(n > 0)

    def copy(self, text: str) -> None:
        clipboard.set_text(text)
        self.toast("Copied")

    def copy_all(self) -> None:
        if self.results:
            self.copy("\n\n".join(self.results))

    def open_in_tool(self) -> None:
        win = open_qr_window(self.app, "\n\n".join(self.results))
        if self.image is not None:
            win.show_results(self.results, self.image)
        self.close()


# ---- entry points --------------------------------------------------------------------------------------


def _qr_window(app, ctx=None) -> QRCodeWindow:
    return single("qrcode", lambda: QRCodeWindow(app, ctx))


def open_qr_window(app, text: str = "") -> QRCodeWindow:
    """Show the QR window encoding ``text`` (ShowQRCode after upload, the tray, …)."""
    win = _qr_window(app)
    win.set_text(text or "")
    win.stack.set_visible_child_name("encode")
    return win


def open_qr_results(app, text, img: Image.Image | None = None) -> QRResultsWindow:
    """Decoded results of a scan: ``text`` is one string (codes separated by new lines, as the task pipeline
    stores it) or a list of codes."""
    results = text if isinstance(text, (list, tuple)) else (str(text).split("\n") if text else [])
    results = [r for r in _as_list(results) if r.strip()]
    win = existing("qrresults")
    if win is None:
        win = single("qrresults", lambda: QRResultsWindow(app))
    else:
        win.present()
    win.set_results(results, img)
    return win


def open(app, ctx) -> QRCodeWindow:  # noqa: A001 - the tool contract's name
    """ShareX QRCode job: scan an image given on the command line, else encode the clipboard text."""
    win = _qr_window(app, ctx)
    if ctx is not None and ctx.files:
        win.scan_file(ctx.files[0])
        return win
    c = clipboard.read()
    if c.kind == "text" and check_content(c.text) and not win.get_text():
        win.set_text(c.text)
    return win
