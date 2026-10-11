"""ShareL's configuration model: ShareX's ApplicationConfig / HotkeysConfig / TaskSettings.

Field names follow ShareX's properties (snake_case). Workflows are hotkey entries, each with a
TaskSettings whose ``use_default_*`` switches say which sections come from the application's
default task settings (see :func:`effective_task_settings`).
"""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .enums import (
    AfterCaptureTasks,
    AfterUploadTasks,
    ArrowHeadDirection,
    BorderStyle,
    CaptureBackendName,
    ContentAlignment,
    CutOutEffectType,
    EImageFormat,
    FFmpegAudioCodec,
    FFmpegPaletteGenStatsMode,
    FFmpegPaletteUseDither,
    FFmpegPreset,
    FFmpegTune,
    FFmpegVideoCodec,
    FileDestination,
    FileExistAction,
    GIFQuality,
    HotkeyBackendName,
    HotkeyType,
    ImageDestination,
    ImageEditorStartMode,
    ImageInsertMethod,
    IndexerOutput,
    PNGBitDepth,
    ProxyMethod,
    ProxyType,
    RegionCaptureAction,
    ScrollingCaptureScrollTopMethod,
    ScrollMethod,
    ShapeType,
    StepType,
    TaskViewMode,
    TextDestination,
    ThemeMode,
    ThumbnailTitleLocation,
    ToastClickAction,
    TrayClickAction,
    UrlShortenerType,
    URLSharingServices,
)
from .serialize import load_json, save_json

# ---- small value types ------------------------------------------------------------------


@dataclass
class Rect:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersect(self, o: Rect) -> Rect:
        x0, y0 = max(self.x, o.x), max(self.y, o.y)
        x1, y1 = min(self.right, o.right), min(self.bottom, o.bottom)
        return Rect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    @classmethod
    def of(cls, seq) -> Rect:
        x, y, w, h = (int(round(v)) for v in seq)
        return cls(x, y, w, h)

    @classmethod
    def from_points(cls, x0: float, y0: float, x1: float, y1: float) -> Rect:
        return cls(int(min(x0, x1)), int(min(y0, y1)), int(abs(x1 - x0)), int(abs(y1 - y0)))


@dataclass
class Size:
    width: int = 0
    height: int = 0


@dataclass
class ExternalProgram:
    """An "Action" (TaskSettings > Actions): run a program on the file."""
    is_active: bool = False
    name: str = ""
    path: str = ""
    args: str = '"%input"'
    output_extension: str = ""
    extensions: str = ""  # "png jpg" — empty means every file
    hidden_window: bool = True
    delete_input_file: bool = False

    def matches(self, file_path: str) -> bool:
        if not self.extensions.strip():
            return True
        ext = Path(file_path).suffix.lower().lstrip(".")
        wanted = {e.strip().lower().lstrip("*.") for e in self.extensions.replace(",", " ").split()}
        return ext in wanted


@dataclass
class WatchFolder:
    folder_path: str = ""
    filter: str = "*.*"
    include_subdirectories: bool = False
    move_files_to_screenshots_folder: bool = False


# ---- annotation / region capture --------------------------------------------------------


@dataclass
class TextDrawingOptions:
    font: str = "Sans"
    size: int = 18
    color: str = "#FFFFFFFF"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    alignment_horizontal: str = "Center"  # Left | Center | Right
    alignment_vertical: str = "Center"  # Top | Center | Bottom
    gradient: bool = False
    color2: str = "#FFFFC864"
    enter_key_new_line: bool = False


@dataclass
class AnnotationOptions:
    region_corner_radius: int = 0
    border_color: str = "#FFF03C3C"
    border_size: int = 4
    border_style: BorderStyle = BorderStyle.Solid
    fill_color: str = "#00000000"
    drawing_corner_radius: int = 3
    shadow: bool = True
    shadow_color: str = "#7D000000"
    shadow_offset_x: int = 0
    shadow_offset_y: int = 1
    line_center_point_count: int = 1
    arrow_head_direction: ArrowHeadDirection = ArrowHeadDirection.End
    text_outline_options: TextDrawingOptions = field(default_factory=lambda: TextDrawingOptions(size=25, bold=True))
    text_outline_border_color: str = "#FFF03C3C"
    text_outline_border_size: int = 5
    text_options: TextDrawingOptions = field(default_factory=TextDrawingOptions)
    text_border_color: str = "#FFFFFFFF"
    text_border_size: int = 0
    text_fill_color: str = "#FFF03C3C"
    last_image_file_path: str = ""
    step_font_size: int = 18
    step_type: StepType = StepType.Numbers
    step_border_color: str = "#FFFFFFFF"
    step_border_size: int = 0
    step_fill_color: str = "#FFF03C3C"
    magnifier_strength: int = 200
    sticker_pack: str = "Twemoji"
    sticker_size: int = 64
    last_sticker_path: str = ""
    blur_radius: int = 35
    pixelate_size: int = 15
    highlight_color: str = "#FFFFFF00"
    cut_out_effect_type: CutOutEffectType = CutOutEffectType["None"]
    cut_out_effect_size: int = 10
    cut_out_background_color: str = "#00000000"


@dataclass
class RegionCaptureOptions:
    quick_crop: bool = True
    minimum_size: int = 5
    region_capture_action_right_click: RegionCaptureAction = RegionCaptureAction.RemoveShapeCancelCapture
    region_capture_action_middle_click: RegionCaptureAction = RegionCaptureAction.SwapToolType
    region_capture_action_x1_click: RegionCaptureAction = RegionCaptureAction.CaptureFullscreen
    region_capture_action_x2_click: RegionCaptureAction = RegionCaptureAction.CaptureActiveMonitor
    detect_windows: bool = True
    detect_controls: bool = True
    snap_distance: int = 30
    use_dimming: bool = True
    background_dim_strength: int = 10
    use_custom_info_text: bool = False
    custom_info_text: str = "X: $x, Y: $y$nR: $r, G: $g, B: $b$nHex: $hex"
    snap_sizes: list[Size] = field(default_factory=lambda: [Size(426, 240), Size(640, 360), Size(854, 480),
                                                            Size(1280, 720), Size(1920, 1080)])
    show_info: bool = True
    show_magnifier: bool = True
    use_square_magnifier: bool = False
    magnifier_pixel_count: int = 15
    magnifier_pixel_size: int = 10
    show_crosshair: bool = False
    use_light_resize_nodes: bool = False
    enable_animations: bool = True
    is_fixed_size: bool = False
    fixed_size: Size = field(default_factory=lambda: Size(250, 250))
    show_fps: bool = False
    fps_limit: int = 100
    menu_icon_size: int = 0
    menu_locked: bool = False
    remember_menu_state: bool = False
    menu_collapsed: bool = False
    show_menu_tip: bool = True
    input_delay: float = 0.0
    switch_to_drawing_tool_after_selection: bool = False
    switch_to_selection_tool_after_drawing: bool = False
    active_monitor_mode: bool = False
    annotation_options: AnnotationOptions = field(default_factory=AnnotationOptions)
    last_region_tool: ShapeType = ShapeType.RegionRectangle
    last_annotation_tool: ShapeType = ShapeType.DrawingRectangle
    last_editor_tool: ShapeType = ShapeType.DrawingRectangle
    image_editor_start_mode: ImageEditorStartMode = ImageEditorStartMode.AutoSize
    image_editor_window_maximized: bool = False
    image_editor_window_size: Size = field(default_factory=lambda: Size(1200, 800))
    zoom_to_fit_on_open: bool = False
    editor_auto_copy_image: bool = False
    auto_close_editor_on_task: bool = False
    show_editor_pan_tip: bool = True
    image_editor_resize_interpolation: str = "Bicubic"
    editor_new_image_size: Size = field(default_factory=lambda: Size(800, 600))
    editor_new_image_transparent: bool = False
    editor_new_image_background_color: str = "#FFFFFFFF"
    editor_canvas_color: str = "#00000000"
    image_insert_method: ImageInsertMethod = ImageInsertMethod.Center
    screen_color_picker_info_text: str = ""


# ---- recording --------------------------------------------------------------------------


@dataclass
class FFmpegOptions:
    video_codec: FFmpegVideoCodec = FFmpegVideoCodec.libx264
    audio_codec: FFmpegAudioCodec = FFmpegAudioCodec.libvoaacenc
    video_source: str = "screen"  # PipeWire screen cast
    audio_source: str = ""  # "" = no audio; "default" or a PulseAudio source name
    user_args: str = ""
    use_custom_commands: bool = False
    custom_commands: str = ""
    # x264 / x265
    x264_preset: FFmpegPreset = FFmpegPreset.ultrafast
    x264_tune: FFmpegTune = FFmpegTune.zerolatency
    x264_crf: int = 28
    x264_use_bitrate: bool = False
    x264_bitrate: int = 3000  # kbit/s
    # vp8 / vp9 / av1
    vpx_bitrate: int = 3000
    vpx_crf: int = 31
    xvid_qscale: int = 10
    # hardware
    nvenc_preset: str = "p4"
    nvenc_bitrate: int = 3000
    vaapi_device: str = "/dev/dri/renderD128"
    vaapi_qp: int = 24
    qsv_preset: str = "medium"
    qsv_bitrate: int = 3000
    # gif
    gif_stats_mode: FFmpegPaletteGenStatsMode = FFmpegPaletteGenStatsMode.full
    gif_dither: FFmpegPaletteUseDither = FFmpegPaletteUseDither.sierra2_4a
    gif_bayer_scale: int = 2
    # audio
    aac_qscale: int = 4
    opus_bitrate: int = 128
    vorbis_qscale: int = 3
    mp3_qscale: int = 4


@dataclass
class ScreenRecordingOptions:
    ffmpeg: FFmpegOptions = field(default_factory=FFmpegOptions)
    screen_record_fps: int = 30
    gif_fps: int = 15
    screen_record_fixed_duration: bool = False
    screen_record_duration: float = 3.0
    screen_record_start_delay: float = 0.0
    screen_record_auto_start: bool = True
    screen_record_show_cursor: bool = True
    screen_record_two_pass_encoding: bool = False
    screen_record_ask_confirmation_on_abort: bool = False
    screen_record_transparent_region: bool = False
    show_region_border: bool = True
    countdown_seconds: int = 0


@dataclass
class ScrollingCaptureOptions:
    start_delay: int = 300  # ms
    scroll_delay: int = 300  # ms
    scroll_method: ScrollMethod = ScrollMethod.MouseWheel
    scroll_top_method: ScrollingCaptureScrollTopMethod = ScrollingCaptureScrollTopMethod.SendHomeKey
    scroll_amount: int = 2  # wheel clicks per step
    auto_detect_scroll_end: bool = True
    max_scroll_count: int = 100
    ignore_last_rows: int = 0  # ignore this many rows at the bottom (sticky footers)
    auto_ignore_bottom_edge: bool = True
    auto_upload: bool = False
    show_preview: bool = True
    manual_mode: bool = False
    manual_capture_interval: int = 500  # ms


@dataclass
class OCROptions:
    language: str = "eng"
    scale_factor: float = 2.0
    single_line: bool = False
    silent: bool = False
    auto_copy: bool = False
    close_window_after_copying: bool = False
    process_in_background: bool = False


@dataclass
class AutoCaptureOptions:
    repeat_time: float = 60.0  # seconds
    minimize_to_tray: bool = True
    capture_fullscreen: bool = False
    region: Rect = field(default_factory=Rect)
    auto_start: bool = False


# ---- task settings sections ----------------------------------------------------------------


@dataclass
class TaskSettingsGeneral:
    play_sound_after_capture: bool = True
    play_sound_after_upload: bool = True
    play_sound_after_action: bool = True
    show_toast_notification_after_task_completed: bool = True
    toast_window_duration: float = 3.0
    toast_window_fade_duration: float = 1.0
    toast_window_placement: ContentAlignment = ContentAlignment.BottomRight
    toast_window_size: Size = field(default_factory=lambda: Size(400, 300))
    toast_window_left_click_action: ToastClickAction = ToastClickAction.OpenUrl
    toast_window_right_click_action: ToastClickAction = ToastClickAction.CloseNotification
    toast_window_middle_click_action: ToastClickAction = ToastClickAction.AnnotateImage
    toast_window_auto_hide: bool = True
    disable_notifications: bool = False
    disable_notifications_on_fullscreen: bool = False
    use_custom_capture_sound: bool = False
    custom_capture_sound_path: str = ""
    use_custom_task_completed_sound: bool = False
    custom_task_completed_sound_path: str = ""
    use_custom_action_completed_sound: bool = False
    custom_action_completed_sound_path: str = ""
    use_custom_error_sound: bool = False
    custom_error_sound_path: str = ""


@dataclass
class TaskSettingsImage:
    image_format: EImageFormat = EImageFormat.PNG
    image_png_bit_depth: PNGBitDepth = PNGBitDepth.Default
    image_jpeg_quality: int = 90
    image_webp_quality: int = 90
    image_gif_quality: GIFQuality = GIFQuality.Default
    image_auto_use_jpeg: bool = True  # size-based fallback format
    image_auto_use_jpeg_size: int = 2048  # KB
    image_format2: EImageFormat = EImageFormat.JPEG
    image_auto_jpeg_quality: int = 90
    file_exist_action: FileExistAction = FileExistAction.Ask
    image_effect_presets: list[dict] = field(default_factory=list)  # serialized ImageEffectPreset
    selected_image_effect_preset: int = 0
    show_image_effects_window_after_capture: bool = False
    image_effect_only_region_capture: bool = False
    thumbnail_width: int = 200
    thumbnail_height: int = 0
    thumbnail_name: str = "-thumbnail"
    thumbnail_check_size: bool = False
    beautifier: dict = field(default_factory=dict)  # serialized BeautifierOptions


@dataclass
class TaskSettingsCapture:
    show_cursor: bool = True
    capture_transparent: bool = False
    capture_shadow: bool = True
    capture_shadow_offset: int = 100
    capture_client_area: bool = False
    capture_auto_hide_taskbar: bool = False
    capture_custom_region: Rect = field(default_factory=Rect)
    capture_custom_window: str = ""  # window title (or wm_class) for CustomWindow
    screenshot_delay: float = 0.0
    capture_backend: CaptureBackendName = CaptureBackendName.Auto
    surface_options: RegionCaptureOptions = field(default_factory=RegionCaptureOptions)
    screen_recording: ScreenRecordingOptions = field(default_factory=ScreenRecordingOptions)
    scrolling_capture: ScrollingCaptureOptions = field(default_factory=ScrollingCaptureOptions)
    ocr: OCROptions = field(default_factory=OCROptions)
    auto_capture: AutoCaptureOptions = field(default_factory=AutoCaptureOptions)


@dataclass
class TaskSettingsUpload:
    use_custom_time_zone: bool = False
    custom_time_zone_offset: float = 0.0  # hours
    name_format_pattern: str = "%pn_%ra{10}"
    name_format_pattern_active_window: str = "%pn_%ra{10}"
    file_upload_use_named_pattern: bool = False
    file_upload_replace_problematic_characters: bool = False
    url_regex_replace: bool = False
    url_regex_replace_pattern: str = "^https?://(.+)$"
    url_regex_replace_replacement: str = "https://$1"
    clipboard_upload_url_contents: bool = False
    clipboard_upload_shorten_url: bool = False
    clipboard_upload_share_url: bool = False
    clipboard_upload_auto_index_folder: bool = False
    # Destination filters: "ext1 ext2" -> destination name, checked before the default
    uploader_filters: list[dict] = field(default_factory=list)


@dataclass
class TaskSettingsTools:
    screen_color_picker_format: str = "$hex"
    screen_color_picker_info_text: str = "RGB: $r255, $g255, $b255$nHex: $hex$nX: $x Y: $y"
    indexer_output: IndexerOutput = IndexerOutput.Html
    indexer_max_depth: int = 0
    indexer_skip_hidden: bool = True
    indexer_show_sizes: bool = True
    indexer_upload: bool = True
    indexer_custom_css: str = ""
    image_combiner_orientation: str = "Vertical"
    image_combiner_space: int = 0
    image_combiner_align: str = "LeftOrTop"
    image_combiner_auto_fill_background: bool = True
    image_splitter_rows: int = 2
    image_splitter_columns: int = 2
    image_thumbnailer_width: int = 200
    image_thumbnailer_height: int = 0
    image_thumbnailer_quality: int = 90
    image_resizer_width: int = 1280
    image_resizer_height: int = 0
    image_resizer_keep_aspect: bool = True
    image_converter_format: EImageFormat = EImageFormat.PNG
    image_watermark_text: str = "ShareL"
    image_watermark_position: ContentAlignment = ContentAlignment.BottomRight
    video_thumbnailer_count: int = 9
    video_thumbnailer_columns: int = 3
    video_thumbnailer_width: int = 512
    video_converter_codec: str = "libx264"
    video_converter_crf: int = 23
    border_window_title: str = ""
    border_window_exclude_taskbar: bool = True


@dataclass
class TaskSettingsAdvanced:
    process_images_during_file_upload: bool = False
    process_images_during_clipboard_upload: bool = False
    process_images_during_browser_upload: bool = False
    use_after_capture_tasks_during_file_upload: bool = False
    upload_images_to_image_host: bool = True
    text_task_save_as_file: bool = True
    auto_clear_clipboard: bool = False
    region_capture_disable_annotation: bool = False
    image_extensions: str = "jpg jpeg png gif bmp ico tif tiff webp"
    text_extensions: str = "txt log nfo c cpp cc cxx h hpp hxx cs vb html htm xhtml xht xml css js php bat java lua py pl cfg ini md json yml yaml"
    upload_text_files_to_text_host: bool = True
    early_copy_url: bool = False
    text_file_extension: str = "txt"
    text_format: str = "text"
    text_custom: str = ""
    text_custom_encode_input: bool = True
    result_force_https: bool = False
    clipboard_content_format: str = "$result"
    balloon_tip_content_format: str = "$result"
    open_url_format: str = "$result"
    auto_shorten_url_length: int = 0
    auto_close_after_upload_form: bool = False
    names_parser_max_name_length: int = 100
    names_parser_max_title_length: int = 0
    after_upload_form_show_qr_code: bool = True
    tool_settings_title: str = ""


@dataclass
class TaskSettings:
    description: str = ""
    job: HotkeyType = HotkeyType["None"]
    use_default_after_capture_job: bool = True
    after_capture_job: AfterCaptureTasks = (AfterCaptureTasks.CopyImageToClipboard
                                            | AfterCaptureTasks.SaveImageToFile
                                            | AfterCaptureTasks.UploadImageToHost)
    use_default_after_upload_job: bool = True
    after_upload_job: AfterUploadTasks = AfterUploadTasks.CopyURLToClipboard
    use_default_destinations: bool = True
    image_destination: ImageDestination = ImageDestination.Imgur
    image_file_destination: FileDestination = FileDestination.Dropbox
    text_destination: TextDestination = TextDestination.Pastebin
    text_file_destination: FileDestination = FileDestination.Dropbox
    file_destination: FileDestination = FileDestination.Dropbox
    url_shortener_destination: UrlShortenerType = UrlShortenerType.BITLY
    url_sharing_service_destination: URLSharingServices = URLSharingServices.Email
    override_ftp: bool = False
    ftp_index: int = 0
    override_custom_uploader: bool = False
    custom_uploader_index: int = 0
    override_screenshots_folder: bool = False
    screenshots_folder: str = ""
    use_default_general_settings: bool = True
    general_settings: TaskSettingsGeneral = field(default_factory=TaskSettingsGeneral)
    use_default_image_settings: bool = True
    image_settings: TaskSettingsImage = field(default_factory=TaskSettingsImage)
    use_default_capture_settings: bool = True
    capture_settings: TaskSettingsCapture = field(default_factory=TaskSettingsCapture)
    use_default_upload_settings: bool = True
    upload_settings: TaskSettingsUpload = field(default_factory=TaskSettingsUpload)
    use_default_actions: bool = True
    external_programs: list[ExternalProgram] = field(default_factory=list)
    use_default_tool_settings: bool = True
    tool_settings: TaskSettingsTools = field(default_factory=TaskSettingsTools)
    use_default_advanced_settings: bool = True
    advanced_settings: TaskSettingsAdvanced = field(default_factory=TaskSettingsAdvanced)
    watch_folder_enabled: bool = False
    watch_folder_list: list[WatchFolder] = field(default_factory=list)

    @property
    def is_using_defaults(self) -> bool:
        return all(getattr(self, a) for a in _SECTIONS_SWITCHES)


_SECTIONS_SWITCHES = {
    "use_default_after_capture_job": ("after_capture_job",),
    "use_default_after_upload_job": ("after_upload_job",),
    "use_default_destinations": ("image_destination", "image_file_destination", "text_destination",
                                 "text_file_destination", "file_destination", "url_shortener_destination",
                                 "url_sharing_service_destination"),
    "use_default_general_settings": ("general_settings",),
    "use_default_image_settings": ("image_settings",),
    "use_default_capture_settings": ("capture_settings",),
    "use_default_upload_settings": ("upload_settings",),
    "use_default_actions": ("external_programs",),
    "use_default_tool_settings": ("tool_settings",),
    "use_default_advanced_settings": ("advanced_settings",),
}


def effective_task_settings(workflow: TaskSettings | None, default: TaskSettings) -> TaskSettings:
    """The settings a task really runs with: each section from the workflow unless its
    ``use_default_*`` switch is on, in which case from the defaults (ShareX's
    TaskSettings.GetDefaultTaskSettings semantics)."""
    if workflow is None or workflow is default:
        return copy.deepcopy(default)
    out = copy.deepcopy(workflow)
    for switch, attrs in _SECTIONS_SWITCHES.items():
        if getattr(workflow, switch):
            for a in attrs:
                setattr(out, a, copy.deepcopy(getattr(default, a)))
    if not workflow.override_ftp:
        out.ftp_index = default.ftp_index
    if not workflow.override_custom_uploader:
        out.custom_uploader_index = default.custom_uploader_index
    if not workflow.override_screenshots_folder:
        out.screenshots_folder = default.screenshots_folder
        out.override_screenshots_folder = default.override_screenshots_folder
    return out


# ---- hotkeys ----------------------------------------------------------------------------


@dataclass
class HotkeyInfo:
    hotkey: str = ""  # GTK accelerator syntax, e.g. "<Control>Print"
    enabled: bool = True


@dataclass
class HotkeySettings:
    hotkey_info: HotkeyInfo = field(default_factory=HotkeyInfo)
    task_settings: TaskSettings = field(default_factory=TaskSettings)

    @property
    def job(self) -> HotkeyType:
        return self.task_settings.job

    @property
    def name(self) -> str:
        from .enums import HOTKEY_DESCRIPTIONS
        return self.task_settings.description or HOTKEY_DESCRIPTIONS.get(self.job.value, self.job.value)


def default_hotkeys() -> list[HotkeySettings]:
    """ShareX's defaults."""
    pairs = [("RectangleRegion", "<Control>Print"), ("PrintScreen", "Print"), ("ActiveWindow", "<Alt>Print"),
             ("ScreenRecorder", "<Shift>Print"), ("ScreenRecorderGIF", "<Control><Shift>Print")]
    return [HotkeySettings(HotkeyInfo(accel), TaskSettings(job=HotkeyType[job])) for job, accel in pairs]


@dataclass
class HotkeysConfig:
    hotkeys: list[HotkeySettings] = field(default_factory=default_hotkeys)


# ---- application ------------------------------------------------------------------------


@dataclass
class ProxyInfo:
    method: ProxyMethod = ProxyMethod["None"]
    type: ProxyType = ProxyType.HTTP
    host: str = ""
    port: int = 8080
    username: str = ""
    password: str = ""

    def requests_proxies(self) -> dict[str, str] | None:
        if self.method == ProxyMethod["None"]:
            return None
        if self.method == ProxyMethod.Automatic:
            import urllib.request
            p = urllib.request.getproxies()
            return {k: v for k, v in p.items() if k in ("http", "https")} or None
        if not self.host:
            return None
        scheme = "socks5h" if self.type == ProxyType.SOCKS5 else "http"
        auth = ""
        if self.username:
            from urllib.parse import quote
            auth = f"{quote(self.username)}:{quote(self.password)}@"
        url = f"{scheme}://{auth}{self.host}:{self.port}"
        return {"http": url, "https": url}


@dataclass
class PrintSettings:
    margin: int = 5
    auto_rotate_image: bool = True
    auto_scale_image: bool = True
    allow_enlarge_image: bool = False
    center_image: bool = False
    show_print_dialog: bool = True
    default_printer_override: str = ""


@dataclass
class HistorySettings:
    remember_window_state: bool = True
    remember_search_text: bool = False
    search_text: str = ""
    max_item_count: int = -1


@dataclass
class ImageHistorySettings:
    remember_window_state: bool = True
    thumbnail_size: int = 150
    max_item_count: int = 250
    filter_missing_files: bool = False
    remember_search_text: bool = False
    search_text: str = ""


@dataclass
class ApplicationConfig:
    default_task_settings: TaskSettings = field(default_factory=TaskSettings)
    first_time_run_date: str = ""
    file_upload_default_directory: str = ""
    name_parser_auto_increment_number: int = 0
    recent_tasks: list[dict] = field(default_factory=list)
    # General
    show_tray: bool = True
    silent_run: bool = False
    tray_icon_progress_enabled: bool = True
    tray_left_click_action: TrayClickAction = TrayClickAction.RectangleRegion
    tray_left_double_click_action: TrayClickAction = TrayClickAction.OpenMainWindow
    tray_middle_click_action: TrayClickAction = TrayClickAction.ClipboardUpload
    tray_auto_expand_capture_menu: bool = False
    show_main_window_tip: bool = True
    start_with_system: bool = False
    language: str = "Automatic"
    theme_mode: ThemeMode = ThemeMode.System
    # Paths
    use_custom_screenshots_path: bool = False
    custom_screenshots_path: str = ""
    save_image_sub_folder_pattern: str = "%y-%mo"
    save_image_sub_folder_pattern_window: str = ""
    # Main window
    show_menu: bool = True
    task_view_mode: TaskViewMode = TaskViewMode.ThumbnailView
    show_thumbnail_title: bool = True
    thumbnail_title_location: ThumbnailTitleLocation = ThumbnailTitleLocation.Top
    thumbnail_size: Size = field(default_factory=lambda: Size(200, 150))
    show_columns: bool = True
    main_window_size: Size = field(default_factory=lambda: Size(1000, 640))
    # History
    history_save_tasks: bool = True
    history_check_url: bool = False
    recent_tasks_save: bool = False
    recent_tasks_max_count: int = 10
    recent_tasks_show_in_main_window: bool = False
    recent_tasks_show_in_tray_menu: bool = True
    recent_tasks_tray_menu_most_recent_first: bool = False
    history_settings: HistorySettings = field(default_factory=HistorySettings)
    image_history_settings: ImageHistorySettings = field(default_factory=ImageHistorySettings)
    # Print
    dont_show_print_settings_dialog: bool = False
    print_settings: PrintSettings = field(default_factory=PrintSettings)
    # Proxy
    proxy_settings: ProxyInfo = field(default_factory=ProxyInfo)
    # Upload
    upload_limit: int = 5
    buffer_size_power: int = 5
    max_upload_fail_retry: int = 1
    use_secondary_uploaders: bool = False
    secondary_image_uploaders: list[str] = field(default_factory=list)
    secondary_text_uploaders: list[str] = field(default_factory=list)
    secondary_file_uploaders: list[str] = field(default_factory=list)
    url_encode_ignore_emoji: bool = True
    request_timeout_seconds: int = 60
    # Clipboard upload
    clipboard_upload_check_file_contents: bool = True
    show_clipboard_content_viewer: bool = True
    clipboard_upload_ignore_empty_text: bool = True
    # Advanced
    dev_mode: bool = False
    disable_hotkeys: bool = False
    disable_hotkeys_on_fullscreen: bool = False
    hotkey_repeat_limit: int = 500  # ms
    hotkey_backend: HotkeyBackendName = HotkeyBackendName.Auto
    capture_backend: CaptureBackendName = CaptureBackendName.Auto
    use_libsecret: bool = True
    # Actions toolbar
    actions_toolbar_list: list[HotkeyType] = field(default_factory=lambda: [
        HotkeyType.RectangleRegion, HotkeyType.PrintScreen, HotkeyType.ScreenRecorder,
        HotkeyType["None"], HotkeyType.FileUpload, HotkeyType.ClipboardUploadWithContentViewer])
    actions_toolbar_run_at_startup: bool = False
    actions_toolbar_position_x: int = -1
    actions_toolbar_position_y: int = -1
    actions_toolbar_lock_position: bool = False
    actions_toolbar_stay_top_most: bool = True
    recent_colors: list[str] = field(default_factory=list)
    workflows_only_show_edited: bool = False
    # misc
    image_viewer_last_folder: str = ""


# ---- loading / saving -------------------------------------------------------------------


class ConfigStore:
    """Holds the three config files, loads them lazily, saves them atomically."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else paths.config_dir()
        self._lock = threading.RLock()
        self.app: ApplicationConfig = load_json(ApplicationConfig, self.directory / "ApplicationConfig.json")
        self.hotkeys: HotkeysConfig = load_json(HotkeysConfig, self.directory / "HotkeysConfig.json")
        from ..uploaders.config import UploadersConfig
        self.uploaders: UploadersConfig = load_json(UploadersConfig, self.directory / "UploadersConfig.json")
        if not self.app.first_time_run_date:
            import datetime
            self.app.first_time_run_date = datetime.datetime.now().isoformat(timespec="seconds")

    def save(self) -> None:
        with self._lock:
            save_json(self.app, self.directory / "ApplicationConfig.json")
            save_json(self.hotkeys, self.directory / "HotkeysConfig.json")
            save_json(self.uploaders, self.directory / "UploadersConfig.json")

    def save_app(self) -> None:
        with self._lock:
            save_json(self.app, self.directory / "ApplicationConfig.json")

    def save_hotkeys(self) -> None:
        with self._lock:
            save_json(self.hotkeys, self.directory / "HotkeysConfig.json")

    def save_uploaders(self) -> None:
        with self._lock:
            save_json(self.uploaders, self.directory / "UploadersConfig.json")

    # -- workflows --

    def find_workflow(self, name_or_job: str) -> HotkeySettings | None:
        """``-workflow "<name>"``: match the description first, then the job name, case-insensitively."""
        key = name_or_job.strip().lower()
        for hk in self.hotkeys.hotkeys:
            if hk.task_settings.description.strip().lower() == key:
                return hk
        for hk in self.hotkeys.hotkeys:
            if hk.name.lower() == key or hk.job.value.lower() == key:
                return hk
        return None

    def task_settings_for(self, workflow: HotkeySettings | None) -> TaskSettings:
        return effective_task_settings(workflow.task_settings if workflow else None,
                                       self.app.default_task_settings)

    def screenshots_folder(self, ts: TaskSettings | None = None, parser=None) -> Path:
        """Root + the per-month subfolder pattern (ShareX's ScreenshotsFolder)."""
        if ts is not None and ts.override_screenshots_folder and ts.screenshots_folder:
            root = Path(ts.screenshots_folder).expanduser()
            return root
        if self.app.use_custom_screenshots_path and self.app.custom_screenshots_path:
            root = Path(self.app.custom_screenshots_path).expanduser()
        else:
            root = paths.default_screenshots_folder()
        pattern = self.app.save_image_sub_folder_pattern
        if pattern:
            from .nameparser import NameParser, NameParserType
            np = parser or NameParser(NameParserType.FolderPath)
            np.type = NameParserType.FolderPath
            root = root / np.parse(pattern)
        return root
