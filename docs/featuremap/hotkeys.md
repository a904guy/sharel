| FileUpload | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_upload_jobs |
| FolderUpload | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_upload_jobs |
| ClipboardUpload | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_upload_jobs |
| ClipboardUploadWithContentViewer | ui/jobs/upload.py + ui/tools/clipboardviewer.py | ClipboardUploadForm: image/text/files preview, Upload → app.clipboard_upload, Cancel, don't show again | done | tests/test_ui_tools_clipboardviewer.py::test_upload_viewer_shows_text_and_uploads |
| UploadText | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_upload_jobs |
| UploadURL | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_url_jobs_prompt_and_run |
| DragDropUpload | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_upload_jobs |
| ShortenURL | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_url_jobs_prompt_and_run |
| StopUploads | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_stop_uploads_stops_running_and_queued_tasks |
| PrintScreen | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| ActiveWindow | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| CustomWindow | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| ActiveMonitor | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| RectangleRegion | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| CustomRegion | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| LastRegion | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_capture_jobs |
| ScrollingCapture | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_scrolling_capture_job_opens_the_capture_window_for_the_selection; tests/test_scrolling.py::test_mouse_wheel_scrolling_reassembles_page |
| AutoCapture | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_auto_capture_start_stop |
| StartAutoCapture | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_auto_capture_start_stop |
| StopAutoCapture | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_auto_capture_start_stop |
| ScreenRecorder | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| ScreenRecorderActiveWindow | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| ScreenRecorderCustomRegion | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_custom_region_recording_and_abort |
| StartScreenRecorder | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| ScreenRecorderGIF | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| ScreenRecorderGIFActiveWindow | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| ScreenRecorderGIFCustomRegion | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| StartScreenRecorderGIF | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| StopScreenRecording | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs |
| PauseScreenRecording | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_recording_jobs; tests/test_record.py::test_pause_excludes_paused_time_and_abort_deletes |
| AbortScreenRecording | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_custom_region_recording_and_abort |
| ColorPicker | ui/tools/colorpicker.py | ColorPickerForm: box/slider (6 draw styles), RGB/HSB/CMYK/hex/decimal sync, recent/standard palette, screen + clipboard pick, copy menu | done | tests/test_ui_tools_colorpicker.py::test_fields_sync_and_copy_formats |
| ScreenColorPicker | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| Ruler | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_ruler_job_opens_the_ruler_overlay |
| PinToScreen | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_pin_to_screen_offers_the_sources_and_pins_the_choice |
| PinToScreenFromScreen | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| PinToScreenFromClipboard | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| PinToScreenFromFile | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| PinToScreenCloseAll | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| ImageEditor | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| ImageBeautifier | src/sharel/ui/tools/beautifier.py | Adw window: generated option rows, debounced threaded preview of effects.beautifier, copy/save/save as/upload, Set as default | done | tests/test_ui_images.py::test_beautifier_preview_save_copy_upload_and_set_default |
| ImageEffects | src/sharel/ui/tools/effects.py, src/sharel/ui/windows/effects.py | Image Effects window in tool mode: load file/clipboard, save/copy/upload, presets saved on close | done | tests/test_ui_effects.py::test_effects_tool_mode_loads_ctx_file_saves_copies_and_stores_presets_on_close |
| ImageViewer | src/sharel/ui/tools/imageviewer.py | folder browsing, prev/next (keys, wheel), zoom/fit, fullscreen, copy, open in editor, pin | done | tests/test_ui_images.py::test_image_viewer_navigates_folder_zooms_copies_and_pins |
| ImageCombiner | src/sharel/ui/tools/combiner.py | ordered list (move up/down, drop), orientation/alignment/space/wrap/auto fill, live preview, Combine = after capture tasks, save as, copy | done | tests/test_ui_images.py::test_combiner_orders_previews_and_runs_after_capture_tasks |
| ImageSplitter | src/sharel/ui/tools/splitter.py | rows x columns or tile size, grid preview, tiles to output folder | done | tests/test_ui_images.py::test_splitter_rows_columns_and_tile_size |
| ImageThumbnailer | src/sharel/ui/tools/thumbnailer.py | batch JPEG thumbnails, width/height/quality, $filename_th, open output folder | done | tests/test_ui_images.py::test_thumbnailer_writes_jpegs_with_tool_defaults |
| VideoConverter | ui/tools/videoconverter.py | ShareX VideoConverterForm on videotools.convert_video (ffmpeg -progress), live args, cancel | done | tests/test_ui_tools_video.py::test_video_converter_encodes_with_progress |
| VideoThumbnailer | ui/tools/videothumbnailer.py | ShareX VideoThumbnailerForm: options form, videotools.take_thumbnails, sheet preview, upload | done | tests/test_ui_tools_video.py::test_video_thumbnailer_makes_sheet_and_uploads |
| AnalyzeImage | src/sharel/ui/tools/analyze.py | threaded local analysis: dimensions, format, size, transparency, colour stats, dominant colours, hashes, EXIF summary; copy | done | tests/test_ui_images.py::test_analyze_tool_shows_dimensions_colors_hashes_and_exif |
| OCR | ui/tools/ocr.py | OCRForm: region or file → tesseract on worker, language/scale/single line re-run, copy, services | done | tests/test_ui_tools_ocr.py::test_ocr_tool_from_file_and_region |
| QRCode | ui/tools/qrcode.py | QRCodeForm: live encode (size/EC/border), copy/save/upload, decode tab | done | tests/test_ui_tools_qrcode.py::test_encode_decode_round_trip_and_actions |
| QRCodeDecodeFromScreen | ui/jobs/tools.py + ui/tools/qrcode.py | full-screen capture → zbarimg → results window | done | tests/test_ui_tools_qrcode.py::test_results_window_and_jobs |
| QRCodeScanRegion | ui/jobs/tools.py + ui/tools/qrcode.py | region overlay → zbarimg → results window | done | tests/test_ui_tools_qrcode.py::test_results_window_and_jobs |
| HashCheck | ui/tools/hashcheck.py | HashCheckerForm: file / compare two files / text, CRC32..SHA512 on worker with progress + Stop, target match colour, drag-and-drop | done | tests/test_ui_tools_hashcheck.py::test_hash_file_and_expected_match |
| IndexFolder | ui/tools/indexer.py | DirectoryIndexerForm: folder, IndexerSettings form, preview, Upload (tasks.upload_data), Save as, Copy | done | tests/test_ui_tools_indexer.py::test_index_preview_and_upload |
| ClipboardViewer | ui/tools/clipboardviewer.py | ClipboardViewerForm: content preview + clipboard formats list with raw data, Refresh | done | tests/test_ui_tools_clipboardviewer.py::test_viewer_image_files_and_formats |
| BorderlessWindow | ui/tools/borderless.py | ShareL extension MakeBorderless + MoveResize to monitor rect (X11) / fullscreen (Wayland); window list + filter + title search | done | tests/test_ui_tools_borderless.py::test_make_borderless_fills_monitor_and_restores |
| ActiveWindowBorderless | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| ActiveWindowTopMost | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| InspectWindow | ui/tools/inspectwindow.py | extension PickWindow (worker) / window list + GetWindowDetails; SetAbove, SetOpacity, MoveResize | done | tests/test_ui_tools_inspectwindow.py::test_pick_shows_details_and_actions |
| MonitorTest | ui/tools/monitortest.py | fullscreen_on_monitor + cairo; tools.monitortest patterns, motion_frame strip animated by frame clock | done | tests/test_ui_tools_monitortest.py::test_cycle_patterns_render_and_exit |
| BackgroundRemover | src/sharel/ui/tools/bgremove.py | rembg on a thread with cancel, model choice, alpha matting, source/result panes, save/save as/copy, models folder | done | tests/test_ui_images.py::test_background_remover_with_stubbed_model |
| ImageComparer | src/sharel/ui/tools/comparer.py | imagetools.compare_images: similarity, slider/difference/highlight views, save/copy view | done | tests/test_ui_images.py::test_comparer_similarity_and_views |
| IconConverter | src/sharel/ui/tools/iconconverter.py | image to multi-size ICO (size checkboxes), ICO to PNG | done | tests/test_ui_images.py::test_icon_converter_png_to_ico_and_back |
| ImageResizer | src/sharel/ui/tools/resizer.py | batch Fill/Fit/Stretch, PNG/JPEG, output folder + $filename pattern, preview, progress + cancel | done | tests/test_ui_images.py::test_resizer_modes_formats_and_preview |
| ImageConverter | src/sharel/ui/tools/converter.py | batch PNG/JPEG/WebP/GIF/BMP/TIFF, quality, background, encoded-size preview, progress + cancel | done | tests/test_ui_images.py::test_converter_converts_batch_and_cancel |
| ImageWatermark | src/sharel/ui/tools/watermark.py | batch text or image watermark, position/margin/opacity/rotation, preview | done | tests/test_ui_images.py::test_watermark_text_and_image |
| VideoTrimmer | ui/tools/videotrimmer.py | start/end sliders + ffmpeg preview frames, stream copy or re-encode via videotools.trim_video | done | tests/test_ui_tools_video.py::test_video_trimmer_trims_with_preview |
| AnimatedGifTrimmer | ui/tools/giftrimmer.py | frame preview, start/end frame sliders, range playback, videotools.trim_gif (Pillow) | done | tests/test_ui_tools_video.py::test_gif_trimmer_trims_frames |
| Metadata | src/sharel/ui/tools/metadata.py | tools.metadata.read_metadata rows grouped, search, copy all/row, strip in place after confirm | done | tests/test_ui_images.py::test_metadata_viewer_rows_search_copy_and_strip |
| StripMetadata | src/sharel/ui/tools/stripmetadata.py | batch tools.metadata.strip_metadata (overwrite after confirm or copies), keep ICC, per-file report | done | tests/test_ui_images.py::test_strip_metadata_overwrites_after_confirmation_and_reports |
| NetworkMonitor | ui/tools/networkmonitor.py | tools.network.NetworkMonitor sampled every 1 s, cairo history graph, interface picker | done | tests/test_ui_tools_networkmonitor.py::test_two_samples_rates_and_totals |
| MouseHighlighter | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| DisableHotkeys | ui/jobs/other.py, app.py | job toggles ApplicationConfig.disable_hotkeys; grabs stay, on_hotkey ignores every other activation | done | tests/test_ui_hotkeys.py::test_disable_hotkeys_job_toggles_and_on_hotkey_ignores |
| OpenMainWindow | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| OpenScreenshotsFolder | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| OpenHistory | core/jobs.py + ui/windows/history.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_history.py::test_open_history_jobs_and_remember_search |
| OpenImageHistory | core/jobs.py + ui/windows/history.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_history.py::test_open_history_jobs_and_remember_search |
| ToggleActionsToolbar | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| ToggleTrayMenu | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs.py::test_tool_and_window_jobs |
| ExitShareX | src/sharel/core/jobs.py | job dispatch (hotkey/CLI/tray/menu) | done | tests/test_ui_jobs_more.py::test_exit_sharex_quits_the_app |
