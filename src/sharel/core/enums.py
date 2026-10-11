"""ShareX's enums, by the same names, so configs and the feature map line up with ShareX."""
from __future__ import annotations

from enum import Enum, IntFlag, auto


class StrEnum(str, Enum):
    """An enum serialized by name (like ShareX's StringEnumConverter)."""

    def __str__(self) -> str:
        return self.value


def _e(*names: str) -> list[tuple[str, str]]:
    return [(n, n) for n in names]


# ---- jobs -------------------------------------------------------------------------------

HOTKEY_GROUPS: dict[str, list[str]] = {
    "Upload": ["FileUpload", "FolderUpload", "ClipboardUpload", "ClipboardUploadWithContentViewer", "UploadText",
               "UploadURL", "DragDropUpload", "ShortenURL", "StopUploads"],
    "Screen capture": ["PrintScreen", "ActiveWindow", "CustomWindow", "ActiveMonitor", "RectangleRegion",
                       "CustomRegion", "LastRegion", "ScrollingCapture", "AutoCapture", "StartAutoCapture",
                       "StopAutoCapture"],
    "Screen record": ["ScreenRecorder", "ScreenRecorderActiveWindow", "ScreenRecorderCustomRegion",
                      "StartScreenRecorder", "ScreenRecorderGIF", "ScreenRecorderGIFActiveWindow",
                      "ScreenRecorderGIFCustomRegion", "StartScreenRecorderGIF", "StopScreenRecording",
                      "PauseScreenRecording", "AbortScreenRecording"],
    "Tools": ["ColorPicker", "ScreenColorPicker", "Ruler", "PinToScreen", "PinToScreenFromScreen",
              "PinToScreenFromClipboard", "PinToScreenFromFile", "PinToScreenCloseAll", "ImageEditor",
              "ImageBeautifier", "ImageEffects", "ImageViewer", "ImageCombiner", "ImageSplitter",
              "ImageThumbnailer", "VideoConverter", "VideoThumbnailer", "AnalyzeImage", "OCR", "QRCode",
              "QRCodeDecodeFromScreen", "QRCodeScanRegion", "HashCheck", "IndexFolder", "ClipboardViewer",
              "BorderlessWindow", "ActiveWindowBorderless", "ActiveWindowTopMost", "InspectWindow",
              "MonitorTest", "BackgroundRemover", "ImageComparer", "IconConverter", "ImageResizer",
              "ImageConverter", "ImageWatermark", "VideoTrimmer", "AnimatedGifTrimmer", "Metadata",
              "StripMetadata", "NetworkMonitor", "MouseHighlighter"],
    "Other": ["DisableHotkeys", "OpenMainWindow", "OpenScreenshotsFolder", "OpenHistory", "OpenImageHistory",
              "ToggleActionsToolbar", "ToggleTrayMenu", "ExitShareX"],
}

HotkeyType = StrEnum("HotkeyType", _e("None", *[n for g in HOTKEY_GROUPS.values() for n in g]))


def hotkey_group(job: HotkeyType) -> str:
    for group, names in HOTKEY_GROUPS.items():
        if job.value in names:
            return group
    return "Other"


HOTKEY_DESCRIPTIONS: dict[str, str] = {
    "None": "None",
    "FileUpload": "Upload file", "FolderUpload": "Upload folder", "ClipboardUpload": "Upload from clipboard",
    "ClipboardUploadWithContentViewer": "Upload from clipboard with content viewer", "UploadText": "Upload text",
    "UploadURL": "Upload from URL", "DragDropUpload": "Drag and drop upload", "ShortenURL": "Shorten URL",
    "StopUploads": "Stop all active uploads",
    "PrintScreen": "Capture entire screen", "ActiveWindow": "Capture active window",
    "CustomWindow": "Capture pre configured window", "ActiveMonitor": "Capture active monitor",
    "RectangleRegion": "Capture region", "CustomRegion": "Capture pre configured region",
    "LastRegion": "Capture last region", "ScrollingCapture": "Scrolling capture", "AutoCapture": "Auto capture",
    "StartAutoCapture": "Start auto capture using last region", "StopAutoCapture": "Stop auto capture",
    "ScreenRecorder": "Screen recording", "ScreenRecorderActiveWindow": "Screen recording (Active window)",
    "ScreenRecorderCustomRegion": "Screen recording (Pre configured region)",
    "StartScreenRecorder": "Start/Stop screen recording using last region",
    "ScreenRecorderGIF": "Screen recording (GIF)", "ScreenRecorderGIFActiveWindow": "Screen recording (GIF, active window)",
    "ScreenRecorderGIFCustomRegion": "Screen recording (GIF, pre configured region)",
    "StartScreenRecorderGIF": "Start/Stop screen recording (GIF) using last region",
    "StopScreenRecording": "Stop screen recording", "PauseScreenRecording": "Pause screen recording",
    "AbortScreenRecording": "Abort screen recording",
    "ColorPicker": "Color picker", "ScreenColorPicker": "Screen color picker", "Ruler": "Ruler",
    "PinToScreen": "Pin to screen", "PinToScreenFromScreen": "Pin to screen (From screen)",
    "PinToScreenFromClipboard": "Pin to screen (From clipboard)", "PinToScreenFromFile": "Pin to screen (From file)",
    "PinToScreenCloseAll": "Pin to screen (Close all)",
    "ImageEditor": "Image editor", "ImageBeautifier": "Image beautifier", "ImageEffects": "Image effects",
    "ImageViewer": "Image viewer", "ImageCombiner": "Image combiner", "ImageSplitter": "Image splitter",
    "ImageThumbnailer": "Image thumbnailer", "VideoConverter": "Video converter",
    "VideoThumbnailer": "Video thumbnailer", "AnalyzeImage": "Analyze image",
    "OCR": "OCR", "QRCode": "QR code", "QRCodeDecodeFromScreen": "QR code (Scan screen)",
    "QRCodeScanRegion": "QR code (Scan region)", "HashCheck": "Hash checker", "IndexFolder": "Directory indexer",
    "ClipboardViewer": "Clipboard viewer", "BorderlessWindow": "Borderless window",
    "ActiveWindowBorderless": "Make active window borderless", "ActiveWindowTopMost": "Make active window top most",
    "InspectWindow": "Inspect window", "MonitorTest": "Monitor test", "BackgroundRemover": "Background remover",
    "ImageComparer": "Image comparer", "IconConverter": "Icon converter", "ImageResizer": "Image resizer",
    "ImageConverter": "Image converter", "ImageWatermark": "Image watermark", "VideoTrimmer": "Video trimmer",
    "AnimatedGifTrimmer": "Animated GIF trimmer", "Metadata": "Metadata viewer", "StripMetadata": "Strip metadata",
    "NetworkMonitor": "Network monitor", "MouseHighlighter": "Mouse highlighter",
    "DisableHotkeys": "Disable/Enable hotkeys", "OpenMainWindow": "Open main window",
    "OpenScreenshotsFolder": "Open screenshots folder", "OpenHistory": "Open history window",
    "OpenImageHistory": "Open image history window", "ToggleActionsToolbar": "Toggle actions toolbar",
    "ToggleTrayMenu": "Toggle tray menu", "ExitShareX": "Exit ShareL",
}


class AfterCaptureTasks(IntFlag):
    NONE = 0
    ShowQuickTaskMenu = auto()
    ShowAfterCaptureWindow = auto()
    BeautifyImage = auto()
    AddImageEffects = auto()
    AnnotateImage = auto()
    CopyImageToClipboard = auto()
    PinToScreen = auto()
    SendImageToPrinter = auto()
    SaveImageToFile = auto()
    SaveImageToFileWithDialog = auto()
    SaveThumbnailImageToFile = auto()
    PerformActions = auto()
    CopyFileToClipboard = auto()
    CopyFilePathToClipboard = auto()
    CopyFolderPathToClipboard = auto()
    ShowInExplorer = auto()
    AnalyzeImage = auto()
    ScanQRCode = auto()
    DoOCR = auto()
    ShowBeforeUploadWindow = auto()
    UploadImageToHost = auto()
    DeleteFile = auto()


AFTER_CAPTURE_DESCRIPTIONS = {
    "ShowQuickTaskMenu": "Show quick task menu", "ShowAfterCaptureWindow": "Show \"After capture\" window",
    "BeautifyImage": "Beautify image", "AddImageEffects": "Add image effects", "AnnotateImage": "Open in image editor",
    "CopyImageToClipboard": "Copy image to clipboard", "PinToScreen": "Pin to screen",
    "SendImageToPrinter": "Print image", "SaveImageToFile": "Save image to file",
    "SaveImageToFileWithDialog": "Save image to file as...", "SaveThumbnailImageToFile": "Save thumbnail image to file",
    "PerformActions": "Perform actions", "CopyFileToClipboard": "Copy file to clipboard",
    "CopyFilePathToClipboard": "Copy file path to clipboard", "CopyFolderPathToClipboard": "Copy folder path to clipboard",
    "ShowInExplorer": "Show file in file manager", "AnalyzeImage": "Analyze image", "ScanQRCode": "Scan QR code",
    "DoOCR": "Recognize text (OCR)", "ShowBeforeUploadWindow": "Show \"Before upload\" window",
    "UploadImageToHost": "Upload image to host", "DeleteFile": "Delete file locally",
}


class AfterUploadTasks(IntFlag):
    NONE = 0
    ShowAfterUploadWindow = auto()
    UseURLShortener = auto()
    ShareURL = auto()
    CopyURLToClipboard = auto()
    OpenURL = auto()
    ShowQRCode = auto()


AFTER_UPLOAD_DESCRIPTIONS = {
    "ShowAfterUploadWindow": "Show \"After upload\" window", "UseURLShortener": "Shorten URL",
    "ShareURL": "Share URL", "CopyURLToClipboard": "Copy URL to clipboard", "OpenURL": "Open URL",
    "ShowQRCode": "Show QR code window",
}


def flag_names(flags: IntFlag) -> list[str]:
    return [m.name for m in type(flags) if m.value and m in flags]


# ---- images / files ---------------------------------------------------------------------

EImageFormat = StrEnum("EImageFormat", _e("PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP"))
IMAGE_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "BMP": "bmp", "TIFF": "tif", "WEBP": "webp"}
PIL_FORMATS = {"PNG": "PNG", "JPEG": "JPEG", "GIF": "GIF", "BMP": "BMP", "TIFF": "TIFF", "WEBP": "WEBP"}

PNGBitDepth = StrEnum("PNGBitDepth", _e("Default", "Bit32", "Bit24"))
GIFQuality = StrEnum("GIFQuality", _e("Default", "Bit8", "Bit4", "Grayscale"))
FileExistAction = StrEnum("FileExistAction", _e("Ask", "Overwrite", "UniqueName", "Cancel"))
ImageEditorStartMode = StrEnum("ImageEditorStartMode", _e("AutoSize", "Normal", "Maximized", "PreviousState",
                                                          "Fullscreen"))
ImageInsertMethod = StrEnum("ImageInsertMethod", _e("Center", "CanvasExpandDown", "CanvasExpandRight"))

EDataType = StrEnum("EDataType", _e("Default", "File", "Image", "Text", "URL"))

# ---- region capture ---------------------------------------------------------------------

RegionCaptureMode = StrEnum("RegionCaptureMode", _e("Default", "Annotation", "ScreenColorPicker", "Ruler",
                                                    "OneClick", "Editor", "TaskEditor"))
RegionCaptureAction = StrEnum("RegionCaptureAction", _e(
    "None", "CancelCapture", "RemoveShapeCancelCapture", "RemoveShape", "OpenOptionsMenu", "SwapToolType",
    "CaptureFullscreen", "CaptureActiveMonitor", "CaptureLastRegion"))

ShapeType = StrEnum("ShapeType", _e(
    "RegionRectangle", "RegionEllipse", "RegionFreehand",
    "ToolSelect",
    "DrawingRectangle", "DrawingEllipse", "DrawingFreehand", "DrawingFreehandArrow", "DrawingLine", "DrawingArrow",
    "DrawingTextOutline", "DrawingTextBackground", "DrawingSpeechBalloon", "DrawingStep", "DrawingMagnify",
    "DrawingImage", "DrawingImageScreen", "DrawingSticker", "DrawingCursor", "DrawingSmartEraser",
    "EffectBlur", "EffectPixelate", "EffectHighlight",
    "ToolCrop", "ToolCutOut"))

BorderStyle = StrEnum("BorderStyle", _e("Solid", "Dash", "Dot", "DashDot", "DashDotDot"))
ArrowHeadDirection = StrEnum("ArrowHeadDirection", _e("End", "Start", "Both"))
StepType = StrEnum("StepType", _e("Numbers", "LettersUppercase", "LettersLowercase", "RomanNumeralsUppercase",
                                  "RomanNumeralsLowercase"))
CutOutEffectType = StrEnum("CutOutEffectType", _e("None", "ZigZag", "TornEdge", "Wave"))

ScrollMethod = StrEnum("ScrollMethod", _e("MouseWheel", "DownArrow", "PageDown", "ScrollMessage"))
ScrollingCaptureScrollTopMethod = StrEnum("ScrollingCaptureScrollTopMethod", _e(
    "None", "SendHomeKey", "ScrollMessage", "MouseWheel"))

# ---- notifications / tray ---------------------------------------------------------------

ToastClickAction = StrEnum("ToastClickAction", _e(
    "CloseNotification", "AnnotateImage", "CopyImageToClipboard", "CopyFile", "CopyFilePath", "CopyUrl",
    "OpenFile", "OpenFolder", "OpenUrl", "Upload", "PinToScreen", "DeleteFile"))
ContentAlignment = StrEnum("ContentAlignment", _e("TopLeft", "TopCenter", "TopRight", "MiddleLeft", "MiddleCenter",
                                                  "MiddleRight", "BottomLeft", "BottomCenter", "BottomRight"))
TrayClickAction = StrEnum("TrayClickAction", _e("None", "OpenMainWindow", "OpenTrayMenu", "OpenScreenshotsFolder",
                                                "OpenHistory", "OpenImageHistory", "RectangleRegion",
                                                "PrintScreen", "ClipboardUpload", "ScreenRecorder",
                                                "ScreenRecorderGIF", "ToggleActionsToolbar"))
TaskViewMode = StrEnum("TaskViewMode", _e("ListView", "ThumbnailView"))
ThumbnailTitleLocation = StrEnum("ThumbnailTitleLocation", _e("Top", "Bottom"))
SupportedLanguage = StrEnum("SupportedLanguage", _e("Automatic", "English"))
ThemeMode = StrEnum("ThemeMode", _e("System", "Light", "Dark"))
ProxyMethod = StrEnum("ProxyMethod", _e("None", "Manual", "Automatic"))
ProxyType = StrEnum("ProxyType", _e("HTTP", "SOCKS5"))
CaptureBackendName = StrEnum("CaptureBackendName", _e("Auto", "Extension", "MutterScreenCast", "Portal",
                                                      "GnomeScreenshot"))
HotkeyBackendName = StrEnum("HotkeyBackendName", _e("Auto", "Extension", "GlobalShortcutsPortal", "GSettings"))

# ---- recording --------------------------------------------------------------------------

FFmpegVideoCodec = StrEnum("FFmpegVideoCodec", _e(
    "libx264", "libx265", "libvpx", "libvpx_vp9", "mpeg4", "libxvid", "libwebp", "apng", "gif",
    "h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv", "h264_vaapi", "hevc_vaapi",
    "libaom_av1", "libsvtav1", "av1_nvenc", "av1_qsv", "av1_vaapi"))
FFmpegAudioCodec = StrEnum("FFmpegAudioCodec", _e("libvoaacenc", "libopus", "libvorbis", "libmp3lame"))
FFmpegPreset = StrEnum("FFmpegPreset", _e("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow",
                                          "slower", "veryslow"))
FFmpegTune = StrEnum("FFmpegTune", _e("film", "animation", "grain", "stillimage", "psnr", "ssim", "fastdecode",
                                      "zerolatency"))
FFmpegPaletteGenStatsMode = StrEnum("FFmpegPaletteGenStatsMode", _e("full", "diff"))
FFmpegPaletteUseDither = StrEnum("FFmpegPaletteUseDither", _e("none", "bayer", "heckbert", "floyd_steinberg",
                                                              "sierra2", "sierra2_4a"))
ScreenRecordOutput = StrEnum("ScreenRecordOutput", _e("FFmpeg", "GIF"))

# ---- destinations -----------------------------------------------------------------------

ImageDestination = StrEnum("ImageDestination", _e(
    "Imgur", "ImageShack", "Flickr", "Photobucket", "Chevereto", "Vgyme", "CustomImageUploader", "FileUploader"))
TextDestination = StrEnum("TextDestination", _e(
    "Pastebin", "Paste2", "Slexy", "Paste_ee", "Gist", "Upaste", "Hastebin", "OneTimeSecret", "Pastie",
    "PrivateBin", "CustomTextUploader", "FileUploader"))
FileDestination = StrEnum("FileDestination", _e(
    "Dropbox", "FTP", "OneDrive", "GoogleDrive", "Puush", "Box", "Mega", "AmazonS3", "GoogleCloudStorage",
    "AzureStorage", "BackblazeB2", "OwnCloud", "Nextcloud", "Immich", "MediaFire", "Pushbullet", "SendSpace",
    "Localhostr", "Lambda", "ImgFish", "Pomf", "Uguu", "Seafile", "Streamable", "Sul", "Lithiio", "Transfersh",
    "Plik", "YouTube", "SharedFolder", "Email", "CustomFileUploader"))
UrlShortenerType = StrEnum("UrlShortenerType", _e(
    "BITLY", "ISGD", "VGD", "TINYURL", "TURL", "YOURLS", "QRnet", "VURL", "TwoGP", "Polr", "FirebaseDynamicLinks",
    "Kutt", "ZeroWidthShortener", "CustomURLShortener"))
URLSharingServices = StrEnum("URLSharingServices", _e(
    "Email", "Facebook", "Reddit", "Pinterest", "Tumblr", "LinkedIn", "StumbleUpon", "Delicious", "VK",
    "Pushbullet", "GoogleImageSearch", "BingVisualSearch", "CustomURLSharingService"))

CustomUploaderDestinationType = StrEnum("CustomUploaderDestinationType", _e(
    "None", "ImageUploader", "TextUploader", "FileUploader", "URLShortener", "URLSharingService"))
HttpMethod = StrEnum("HttpMethod", _e("GET", "POST", "PUT", "PATCH", "DELETE"))
CustomUploaderBody = StrEnum("CustomUploaderBody", _e(
    "None", "MultipartFormData", "FormURLEncoded", "JSON", "XML", "Binary"))

NativeMessagingAction = StrEnum("NativeMessagingAction", _e(
    "None", "UploadImage", "UploadVideo", "UploadAudio", "UploadText", "ShortenURL"))

IndexerOutput = StrEnum("IndexerOutput", _e("Html", "Txt", "Xml", "Json"))
HashType = StrEnum("HashType", _e("CRC32", "MD5", "SHA1", "SHA256", "SHA384", "SHA512"))
