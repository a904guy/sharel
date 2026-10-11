"""Tools jobs. Region-based tools run the capture overlay in the matching RegionCaptureMode; the
others open their window from :mod:`sharel.ui.tools` (``open(app, ctx)`` in each module)."""
from __future__ import annotations

import importlib
import logging

from ...core import clipboard
from ...core.enums import RegionCaptureMode
from ...core.jobs import JobContext
from ...editor.manager import RegionResult
from ...shell.client import ShellUnavailable, shell

log = logging.getLogger("sharel.tools.jobs")

# job -> module in sharel.ui.tools whose open(app, ctx) shows the tool window
TOOL_WINDOWS = {
    "ColorPicker": "colorpicker", "ImageBeautifier": "beautifier", "ImageViewer": "imageviewer",
    "ImageCombiner": "combiner", "ImageSplitter": "splitter", "ImageThumbnailer": "thumbnailer",
    "VideoConverter": "videoconverter", "VideoThumbnailer": "videothumbnailer", "AnalyzeImage": "analyze",
    "QRCode": "qrcode", "HashCheck": "hashcheck", "IndexFolder": "indexer", "ClipboardViewer": "clipboardviewer",
    "BorderlessWindow": "borderless", "InspectWindow": "inspectwindow", "MonitorTest": "monitortest",
    "BackgroundRemover": "bgremove", "ImageComparer": "comparer", "IconConverter": "iconconverter",
    "ImageResizer": "resizer", "ImageConverter": "converter", "ImageWatermark": "watermark",
    "VideoTrimmer": "videotrimmer", "AnimatedGifTrimmer": "giftrimmer", "Metadata": "metadata",
    "StripMetadata": "stripmetadata", "NetworkMonitor": "networkmonitor", "ImageEffects": "effects",
}


def _open_tool(app, ctx: JobContext, module: str) -> None:
    mod = importlib.import_module(f"sharel.ui.tools.{module}")
    mod.open(app, ctx)


def _make(job: str, module: str):
    def handler(self, ctx: JobContext) -> None:
        _open_tool(self, ctx, module)
    handler.__name__ = f"job_{job}"
    handler.__doc__ = f"{job}: opens sharel.ui.tools.{module}"
    return handler


class ToolJobs:
    mouse_highlighter_on = False

    # -- region-mode tools --

    def job_ScreenColorPicker(self, ctx: JobContext) -> None:
        def outcome(o):
            if o.result.result == RegionResult.Close or not o.color:
                return
            from ...tools.color import Color, format_pixel_info
            fmt = ctx.task_settings.tool_settings.screen_color_picker_format
            c = Color.parse(o.color) if hasattr(Color, "parse") else None
            x, y = o.point_logical or (0, 0)
            text = format_pixel_info(fmt, c, x, y) if c is not None else o.color
            clipboard.set_text(text)
            self.message("Color copied", text)
            self.last_picked_color = text
        self.run_region(RegionCaptureMode.ScreenColorPicker, ctx.task_settings, outcome)

    def job_Ruler(self, ctx: JobContext) -> None:
        self.run_region(RegionCaptureMode.Ruler, ctx.task_settings, lambda o: None)

    def job_MouseHighlighter(self, ctx: JobContext) -> None:
        sh = shell()
        if not sh.available:
            self.message("Mouse highlighter", "Needs the ShareL GNOME Shell extension.", urgent=True)
            return
        ToolJobs.mouse_highlighter_on = not ToolJobs.mouse_highlighter_on
        try:
            sh.mouse_highlighter(ToolJobs.mouse_highlighter_on, {})
        except ShellUnavailable as e:
            self.message("Mouse highlighter", str(e), urgent=True)

    # -- pin to screen --

    def job_PinToScreen(self, ctx: JobContext) -> None:
        from ..windows.pin import open_pin_startup
        open_pin_startup(self, ctx)

    def job_PinToScreenFromScreen(self, ctx: JobContext) -> None:
        from ..windows.pin import pin_image

        def outcome(o):
            if o.image is not None and o.result.result != RegionResult.Close:
                pin_image(self, o.image, position=(o.rect_logical[0], o.rect_logical[1]) if o.rect_logical else None)
        self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)

    def job_PinToScreenFromClipboard(self, ctx: JobContext) -> None:
        from ..windows.pin import pin_image, pin_text
        c = clipboard.read()
        if c.kind == "image":
            pin_image(self, c.image)
        elif c.kind == "files" and c.files:
            from ...core import imaging
            try:
                pin_image(self, imaging.load(c.files[0]))
            except OSError:
                self.message("Pin to screen", "The clipboard file is not an image.", urgent=True)
        elif c.kind == "text" and c.text.strip():
            pin_text(self, c.text)
        else:
            self.message("Pin to screen", "The clipboard has no image or text.", urgent=True)

    def job_PinToScreenFromFile(self, ctx: JobContext) -> None:
        from ...core import imaging
        from ..gtkutil import IMAGE_PATTERNS, open_files
        from ..windows.pin import pin_image

        def done(paths):
            for p in paths:
                pin_image(self, imaging.load(p))
        if ctx.files:
            return done(ctx.files)
        open_files(self.active_parent(), "Pin image to screen", done, patterns=IMAGE_PATTERNS)

    def job_PinToScreenCloseAll(self, ctx: JobContext) -> None:
        from ..windows.pin import close_all_pins
        close_all_pins()

    # -- editor / OCR / QR --

    def job_ImageEditor(self, ctx: JobContext) -> None:
        from ..windows.editor import open_editor_for_file, open_editor_startup
        if ctx.files:
            for f in ctx.files:
                open_editor_for_file(self, f)
            return
        open_editor_startup(self, ctx)

    def job_OCR(self, ctx: JobContext) -> None:
        from ..tools.ocr import open_ocr
        if ctx.files:
            from ...core import imaging
            return open_ocr(self, imaging.load(ctx.files[0]), ctx.task_settings.capture_settings.ocr)

        def outcome(o):
            if o.image is not None and o.result.result != RegionResult.Close:
                open_ocr(self, o.image, ctx.task_settings.capture_settings.ocr)
        self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)

    def job_QRCodeDecodeFromScreen(self, ctx: JobContext) -> None:
        from ...capture import backends
        from ...tools.qr import decode_image
        from ..tools.qrcode import open_qr_results
        try:
            cap = backends.capture_screen(False, self.backend_pref(ctx.task_settings))
        except backends.CaptureError as e:
            return self.capture_error(e)
        open_qr_results(self, "\n".join(decode_image(cap.image)), cap.image)

    def job_QRCodeScanRegion(self, ctx: JobContext) -> None:
        from ...tools.qr import decode_image
        from ..tools.qrcode import open_qr_results

        def outcome(o):
            if o.image is not None and o.result.result != RegionResult.Close:
                open_qr_results(self, "\n".join(decode_image(o.image)), o.image)
        self.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome)

    # -- window tools --

    def _active_shell_window(self):
        sh = shell()
        if not sh.available:
            self.message("Window tools", "Needs the ShareL GNOME Shell extension.", urgent=True)
            return None, None
        try:
            return sh, sh.get_active_window()
        except ShellUnavailable as e:
            self.message("Window tools", str(e), urgent=True)
            return None, None

    def job_ActiveWindowBorderless(self, ctx: JobContext) -> None:
        sh, w = self._active_shell_window()
        if w is None:
            return
        details = sh.get_window_details(w.id)
        res = sh.make_borderless(w.id, not bool(details.get("borderless")))
        log.info("borderless %s: %s", w.title, res)

    def job_ActiveWindowTopMost(self, ctx: JobContext) -> None:
        sh, w = self._active_shell_window()
        if w is None:
            return
        above = sh.toggle_above(w.id)
        self.message("Always on top " + ("on" if above else "off"), w.title)


for _job, _module in TOOL_WINDOWS.items():
    setattr(ToolJobs, f"job_{_job}", _make(_job, _module))
