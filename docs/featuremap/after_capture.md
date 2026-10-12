| ShowQuickTaskMenu | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_quick_task_menu_and_after_capture_window; tests/test_ui_capture.py::test_after_capture_quick_menu_before_and_after_upload |
| ShowAfterCaptureWindow | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_quick_task_menu_and_after_capture_window; tests/test_ui_capture.py::test_after_capture_quick_menu_before_and_after_upload |
| BeautifyImage | src/sharel/core/tasks.py, src/sharel/ui/tools/beautifier.py | WorkerTask pipeline with the options stored by the Beautifier window's Set as default | done | tests/test_ui_images.py::test_beautifier_preview_save_copy_upload_and_set_default |
| AddImageEffects | src/sharel/core/tasks.py, src/sharel/ui/host.py, src/sharel/ui/windows/effects.py | TaskHost.image_effects opens the Image Effects window; OK returns the effect image, Cancel cancels the task | done | tests/test_ui_effects.py::test_pipeline_runs_through_effects_window_and_ok_returns_effect_image |
| AnnotateImage | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_annotate_pin_shorten_open; tests/test_ui_capture.py::test_annotate_step_runs_through_editor_window |
| CopyImageToClipboard | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_capture_save_copy_upload_history |
| PinToScreen | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_annotate_pin_shorten_open; tests/test_ui_capture.py::test_pin_zoom_opacity_close_all |
| SendImageToPrinter | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_print_and_copy_flags; tests/test_pipeline_flags.py::test_print_layout_settings |
| SaveImageToFile | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_capture_save_copy_upload_history |
| SaveImageToFileWithDialog | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_save_with_dialog |
| SaveThumbnailImageToFile | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_thumbnail_and_delete |
| PerformActions | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_perform_actions_external_program |
| CopyFileToClipboard | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_print_and_copy_flags |
| CopyFilePathToClipboard | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_print_and_copy_flags |
| CopyFolderPathToClipboard | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_print_and_copy_flags |
| ShowInExplorer | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_print_and_copy_flags |
| AnalyzeImage | src/sharel/core/tasks.py, src/sharel/ui/host.py, src/sharel/ui/tools/analyze.py | TaskHost.analyze_image opens open_analyze for the saved capture | done | tests/test_ui_images.py::test_after_capture_analyze_image_opens_window |
| ScanQRCode | ui/host.py + ui/tools/qrcode.py | GtkTaskHost.scan_qr → open_qr_results | done | tests/test_ui_tools_qrcode.py::test_results_window_and_jobs |
| DoOCR | ui/host.py + ui/tools/ocr.py | GtkTaskHost.ocr → open_ocr (silent/background → pipeline) | done | tests/test_ui_tools_ocr.py::test_ocr_window_recognises_text_and_options |
| ShowBeforeUploadWindow | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_pipeline_flags.py::test_before_upload_window_accept_and_decline; tests/test_ui_capture.py::test_after_capture_quick_menu_before_and_after_upload |
| UploadImageToHost | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_capture_save_copy_upload_history |
| DeleteFile | src/sharel/core/tasks.py | WorkerTask pipeline | done | tests/test_tasks.py::test_thumbnail_and_delete |
