| Pixels: extension Shell.Screenshot backend | capture/backends.py, extension | app.sharel.Shell Screenshot (Shell.Screenshot in gnome-shell) | planned | - |
| Pixels: Mutter ScreenCast backend | capture/backends.py | org.gnome.Mutter.ScreenCast RecordArea/RecordMonitor + one PipeWire frame (GStreamer pipewiresrc) | planned | - |
| Pixels: Screenshot portal backend | capture/backends.py | org.freedesktop.portal.Screenshot | planned | - |
| Pixels: gnome-screenshot CLI backend | capture/backends.py | gnome-screenshot -f | planned | - |
| Backend auto-detect + settings override | capture/backends.py, ui/windows/appsettings.py | probe chain; ApplicationConfig.capture_backend (Advanced), TaskSettings capture_backend wins | done | tests/test_ui_settings.py::test_capture_backend_setting_overrides_auto_detect |
| Physical vs logical pixels, fractional scaling | capture/screen.py | org.gnome.Mutter.DisplayConfig GetCurrentState scales | planned | - |
| Window list / geometry / active window | shell/client.py, extension | app.sharel.Shell GetWindows/GetActiveWindow/GetMonitors | planned | - |
| Pointer position | shell/client.py, extension | app.sharel.Shell GetPointer | planned | - |
| SetAbove / MoveResize / Borderless / Fullscreen | shell/client.py, extension | Meta.Window make_above/move_resize_frame/set decorations | planned | - |
| Interactive window pick (CustomWindow) | extension | PickWindow: highlight-on-hover shell actor | planned | - |
| Clipboard from background app | core/clipboard.py, extension | extension SetClipboard (St.Clipboard) > wl-copy > GTK clipboard | planned | - |
| Mouse highlighter | extension | MouseHighlighter shell actor following the pointer | planned | - |
| Global hotkeys | shell/hotkeys.py, extension | GrabAccelerator + AcceleratorActivated; GlobalShortcuts portal; gsettings custom-keybindings | planned | - |
| Position own windows (pin, toast, toolbar) | extension | PositionWindow(win, x, y) | planned | - |
| Degraded mode without extension | capture/, shell/ | ScreenCast capture; window capture -> region + notice; portal/gsettings hotkeys | planned | - |
| Region capture overlay (frozen, one window per monitor) | capture/region.py | Gtk.Window.fullscreen_on_monitor per Gdk.Monitor | planned | - |
| Region: crosshair + magnifier with pixel grid | capture/region.py | cairo | planned | - |
| Region: window and child-area snapping | capture/region.py | rects from GetWindows (frame + buffer) | planned | - |
| Region: info text (position, size, colour) | capture/region.py | cairo/pango | planned | - |
| Region: keyboard nudge, right-click cancel/remove | capture/region.py | Gtk event controllers | planned | - |
| Region: annotation toolbar (region capture is the editor) | capture/region.py, editor/ | shared ShapeManager | planned | - |
| RegionCaptureMode Default/Annotation/ScreenColorPicker/Ruler/OneClick/Editor/TaskEditor | capture/region.py | modes of the overlay | planned | - |
| RegionCaptureAction mouse-button mapping | capture/region.py | RegionCaptureOptions right/middle/X1/X2 | planned | - |
| Screen recording: Mutter ScreenCast -> GStreamer encoder | record/recorder.py | RecordArea/RecordWindow/RecordMonitor + pipewiresrc | planned | - |
| Recording codecs x264/x265/vp8/vp9/AV1 | record/pipeline.py | x264enc/x265enc/vp8enc/vp9enc/svtav1enc/av1enc | planned | - |
| Recording hardware encoders nvenc/vaapi/qsv | record/pipeline.py | nvh264enc/vah264enc/qsv when present | planned | - |
| GIF via ffmpeg palettegen/paletteuse | record/gif.py | ffmpeg two-pass | planned | - |
| Recording audio + source selection | record/pipeline.py | pulsesrc (PipeWire-Pulse) | planned | - |
| Recording start/stop/pause/abort | record/recorder.py | GStreamer pipeline control | planned | - |
| Recording countdown, region border, tray indicator | record/, ui/ | overlay window + tray state | planned | - |
| FFmpeg-style codec options | record/pipeline.py | FFmpegOptions mapped to GStreamer/ffmpeg | planned | - |
| ScreenCast portal fallback with restore token | record/portal.py | org.freedesktop.portal.ScreenCast persist_mode=2 | planned | - |
| Scrolling capture: input injection | capture/scrolling.py | org.gnome.Mutter.RemoteDesktop wheel/keys | planned | - |
| Scrolling capture ScrollMethod MouseWheel/DownArrow/PageDown/ScrollMessage | capture/scrolling.py | RemoteDesktop NotifyPointerAxisDiscrete / keysyms | planned | - |
| Scrolling capture stitching (row overlap, ignore last N, end detect) | capture/stitch.py | numpy row comparison | planned | - |
| Scrolling capture manual mode + preview/options window | capture/scrolling.py, ui/ | timer capture; Adw window | planned | - |
| Tray icon with full menu | ui/tray_helper.py | GTK3 AyatanaAppIndicator3 helper process + GApplication actions | planned | - |
| TrayClickAction + recording state icon | ui/tray_helper.py | AppIndicator activate / icon swap | planned | - |
| Toast notifications with image + actions | ui/notify.py | org.freedesktop.Notifications | planned | - |
| ToastClickAction.CloseNotification | ui/notify.py | notification action | planned | - |
| ToastClickAction.AnnotateImage | ui/notify.py | notification action | planned | - |
| ToastClickAction.CopyImageToClipboard | ui/notify.py | notification action | planned | - |
| ToastClickAction.CopyFile | ui/notify.py | notification action | planned | - |
| ToastClickAction.CopyFilePath | ui/notify.py | notification action | planned | - |
| ToastClickAction.CopyUrl | ui/notify.py | notification action | planned | - |
| ToastClickAction.OpenFile | ui/notify.py | notification action | planned | - |
| ToastClickAction.OpenFolder | ui/notify.py | notification action | planned | - |
| ToastClickAction.OpenUrl | ui/notify.py | notification action | planned | - |
| ToastClickAction.Upload | ui/notify.py | notification action | planned | - |
| ToastClickAction.PinToScreen | ui/notify.py | notification action | planned | - |
| ToastClickAction.DeleteFile | ui/notify.py | notification action | planned | - |
| Sounds Capture/TaskCompleted/ActionCompleted/Error | ui/sounds.py | GStreamer playbin / canberra | planned | - |
| Pin to screen window (zoom, opacity, move, shadow, close all) | tools/pin.py | undecorated GTK4 window, kept above via extension | planned | - |
| Pin sources: region, clipboard, file, after-capture | tools/pin.py | - | planned | - |
| Show in Explorer | core/desktop.py | org.freedesktop.FileManager1.ShowItems | planned | - |
| Print | ui/printing.py | Gtk.PrintOperation, fallback lp | planned | - |
| OCR window + language picker | ui/tools/ocr.py | tesseract --list-langs, worker thread, cancel on option change | done | tests/test_ui_tools_ocr.py::test_ocr_window_recognises_text_and_options |
| Scan/decode QR | ui/tools/qrcode.py | zbarimg from screen/region/file/clipboard | done | tests/test_ui_tools_qrcode.py::test_scan_sources |
| Encode QR | ui/tools/qrcode.py | qrcode package, live preview, PNG/JPEG/BMP/SVG save | done | tests/test_ui_tools_qrcode.py::test_encode_decode_round_trip_and_actions |
| Explorer context menu Upload/Edit with ShareL | data/nautilus, data/applications | Nautilus scripts + .desktop actions | planned | - |
| Windows startup | core/autostart.py, ui/windows/appsettings.py | XDG autostart entry toggled by "Run ShareL when I log in" | done | tests/test_ui_settings.py::test_app_settings_autostart_tray_language |
| Browser extension native messaging | tools/native_host.py, browser-extension/ | Chrome/Firefox native messaging host manifests | planned | - |
| NativeMessagingAction.UploadImage | tools/native_host.py | native messaging | planned | - |
| NativeMessagingAction.UploadVideo | tools/native_host.py | native messaging | planned | - |
| NativeMessagingAction.UploadAudio | tools/native_host.py | native messaging | planned | - |
| NativeMessagingAction.UploadText | tools/native_host.py | native messaging | planned | - |
| NativeMessagingAction.ShortenURL | tools/native_host.py | native messaging | planned | - |
| Installer (ShareX.Setup) | install.sh, packaging/build-deb.sh | install.sh + .deb | planned | - |
| ShareX.Steam and auto-update | - | not applicable: updates come from apt | planned | - |
| Localization | core/i18n.py | gettext scaffolding, English only | planned | - |
| CLI: -<HotkeyType>, -workflow, -silent, -autoclose, files | cli.py | GApplication command line forwarding | planned | - |
| Single instance | app.py | Adw.Application app.sharel.ShareL | planned | - |
