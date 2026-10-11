"""ShareX's menus as Gio.Menu models, shared by the main window's left menu and the tray helper.

Every job is ``app.job::<HotkeyType>``; workflows are ``app.workflow(i)``; checkable after-capture
and after-upload flags and the destination radios are stateful actions on the main window.
"""
from __future__ import annotations

from gi.repository import Gio, GLib

from ..core.enums import AFTER_CAPTURE_DESCRIPTIONS, AFTER_UPLOAD_DESCRIPTIONS, HOTKEY_DESCRIPTIONS

CAPTURE = ["PrintScreen", "ActiveWindow", "CustomWindow", "ActiveMonitor", "RectangleRegion", "CustomRegion",
           "LastRegion", "ScreenRecorder", "ScreenRecorderGIF", "ScrollingCapture", "AutoCapture"]
CAPTURE_MORE = ["ScreenRecorderActiveWindow", "ScreenRecorderCustomRegion", "StartScreenRecorder",
                "ScreenRecorderGIFActiveWindow", "ScreenRecorderGIFCustomRegion", "StartScreenRecorderGIF",
                "StopScreenRecording", "PauseScreenRecording", "AbortScreenRecording", "StartAutoCapture",
                "StopAutoCapture"]
UPLOAD = ["FileUpload", "FolderUpload", "ClipboardUpload", "ClipboardUploadWithContentViewer", "UploadText",
          "UploadURL", "DragDropUpload", "ShortenURL", "StopUploads"]
TOOLS = ["ColorPicker", "ScreenColorPicker", "Ruler", "MouseHighlighter", "PinToScreen", "PinToScreenFromScreen",
         "PinToScreenFromClipboard", "PinToScreenFromFile", "PinToScreenCloseAll", "ImageEditor",
         "ImageBeautifier", "ImageEffects", "ImageViewer", "BackgroundRemover", "ImageComparer", "IconConverter",
         "ImageCombiner", "ImageSplitter", "ImageResizer", "ImageConverter", "ImageWatermark", "ImageThumbnailer",
         "VideoConverter", "VideoTrimmer", "VideoThumbnailer", "AnimatedGifTrimmer", "AnalyzeImage", "OCR",
         "QRCode", "QRCodeDecodeFromScreen", "QRCodeScanRegion", "HashCheck", "Metadata", "StripMetadata",
         "IndexFolder", "ClipboardViewer", "BorderlessWindow", "ActiveWindowBorderless", "ActiveWindowTopMost",
         "InspectWindow", "NetworkMonitor", "MonitorTest"]
TOOL_SECTIONS = [TOOLS[:4], TOOLS[4:9], TOOLS[9:22], TOOLS[22:26], TOOLS[26:36], TOOLS[36:]]
OTHER = ["DisableHotkeys", "OpenMainWindow", "OpenScreenshotsFolder", "OpenHistory", "OpenImageHistory",
         "ToggleActionsToolbar", "ToggleTrayMenu", "ExitShareX"]


def job_item(job: str) -> Gio.MenuItem:
    return Gio.MenuItem.new(HOTKEY_DESCRIPTIONS.get(job, job), f"app.job::{job}")


def jobs_menu(jobs: list[str]) -> Gio.Menu:
    m = Gio.Menu()
    for j in jobs:
        m.append_item(job_item(j))
    return m


def capture_menu() -> Gio.Menu:
    m = jobs_menu(CAPTURE)
    m.append_submenu("More", jobs_menu(CAPTURE_MORE))
    return m


def upload_menu() -> Gio.Menu:
    return jobs_menu(UPLOAD)


def tools_menu() -> Gio.Menu:
    m = Gio.Menu()
    for sec in TOOL_SECTIONS:
        m.append_section(None, jobs_menu(sec))
    return m


def workflows_menu(hotkeys) -> Gio.Menu:
    m = Gio.Menu()
    for i, hk in enumerate(hotkeys):
        m.append_item(Gio.MenuItem.new(hk.name, f"app.workflow({i})"))
    return m


def after_capture_menu(prefix: str = "win") -> Gio.Menu:
    m = Gio.Menu()
    for name, label in AFTER_CAPTURE_DESCRIPTIONS.items():
        m.append_item(Gio.MenuItem.new(label, f"{prefix}.ac-{name}"))
    return m


def after_upload_menu(prefix: str = "win") -> Gio.Menu:
    m = Gio.Menu()
    for name, label in AFTER_UPLOAD_DESCRIPTIONS.items():
        m.append_item(Gio.MenuItem.new(label, f"{prefix}.au-{name}"))
    return m


def destinations_menu(prefix: str = "win") -> Gio.Menu:
    from ..core.enums import (
        FileDestination,
        ImageDestination,
        TextDestination,
        UrlShortenerType,
        URLSharingServices,
    )
    from ..uploaders import registry

    def titled(category: str, value: str) -> tuple[str, bool]:
        if value.startswith("Custom"):
            return "Custom uploader", True
        if value == "FileUploader":
            return "File uploader", True
        info = registry.get(category, value)
        if info is None:
            return value, False
        return (info.title + (" (unavailable)" if info.defunct else "")), info.available

    m = Gio.Menu()
    for label, action, category, enum in (("Image uploaders", "dest-image", "image", ImageDestination),
                                          ("Text uploaders", "dest-text", "text", TextDestination),
                                          ("File uploaders", "dest-file", "file", FileDestination),
                                          ("URL shorteners", "dest-shortener", "shortener", UrlShortenerType),
                                          ("URL sharing services", "dest-sharing", "sharing", URLSharingServices)):
        sub = Gio.Menu()
        for v in enum:
            title, ok = titled(category, v.value)
            if not ok:
                continue
            item = Gio.MenuItem.new(title, None)
            item.set_action_and_target_value(f"{prefix}.{action}", GLib.Variant("s", v.value))
            sub.append_item(item)
        if action == "dest-image":
            fsub = Gio.Menu()
            for v in FileDestination:
                title, ok = titled("file", v.value)
                if ok:
                    it = Gio.MenuItem.new(title, None)
                    it.set_action_and_target_value(f"{prefix}.dest-image-file", GLib.Variant("s", v.value))
                    fsub.append_item(it)
            sub.append_submenu("File uploader destination", fsub)
        m.append_submenu(label, sub)
    sec = Gio.Menu()
    sec.append("Destination settings…", f"{prefix}.destination-settings")
    sec.append("Custom uploader settings…", f"{prefix}.custom-uploaders")
    m.append_section(None, sec)
    return m


def debug_menu(prefix: str = "win") -> Gio.Menu:
    m = Gio.Menu()
    m.append("Show debug log", f"{prefix}.debug-log")
    m.append("Open log folder", f"{prefix}.debug-folder")
    m.append("Test image upload", f"{prefix}.test-upload::image")
    m.append("Test text upload", f"{prefix}.test-upload::text")
    m.append("Test file upload", f"{prefix}.test-upload::file")
    m.append("Test URL shortener", f"{prefix}.test-upload::shortener")
    m.append("Test URL sharing", f"{prefix}.test-upload::sharing")
    return m
