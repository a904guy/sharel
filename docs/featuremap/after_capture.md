| ShowQuickTaskMenu | core/tasks.py | WorkerTask pipeline | planned | - |
| ShowAfterCaptureWindow | core/tasks.py | WorkerTask pipeline | planned | - |
| BeautifyImage | src/sharel/core/tasks.py, src/sharel/ui/tools/beautifier.py | WorkerTask pipeline with the options stored by the Beautifier window's Set as default | done | tests/test_ui_images.py::test_beautifier_preview_save_copy_upload_and_set_default |
| AddImageEffects | src/sharel/core/tasks.py, src/sharel/ui/host.py, src/sharel/ui/windows/effects.py | TaskHost.image_effects opens the Image Effects window; OK returns the effect image, Cancel cancels the task | done | tests/test_ui_effects.py::test_pipeline_runs_through_effects_window_and_ok_returns_effect_image |
| AnnotateImage | core/tasks.py | WorkerTask pipeline | planned | - |
| CopyImageToClipboard | core/tasks.py | WorkerTask pipeline | planned | - |
| PinToScreen | core/tasks.py | WorkerTask pipeline | planned | - |
| SendImageToPrinter | core/tasks.py | WorkerTask pipeline | planned | - |
| SaveImageToFile | core/tasks.py | WorkerTask pipeline | planned | - |
| SaveImageToFileWithDialog | core/tasks.py | WorkerTask pipeline | planned | - |
| SaveThumbnailImageToFile | core/tasks.py | WorkerTask pipeline | planned | - |
| PerformActions | core/tasks.py | WorkerTask pipeline | planned | - |
| CopyFileToClipboard | core/tasks.py | WorkerTask pipeline | planned | - |
| CopyFilePathToClipboard | core/tasks.py | WorkerTask pipeline | planned | - |
| CopyFolderPathToClipboard | core/tasks.py | WorkerTask pipeline | planned | - |
| ShowInExplorer | core/tasks.py | WorkerTask pipeline | planned | - |
| AnalyzeImage | src/sharel/core/tasks.py, src/sharel/ui/host.py, src/sharel/ui/tools/analyze.py | TaskHost.analyze_image opens open_analyze for the saved capture | done | tests/test_ui_images.py::test_after_capture_analyze_image_opens_window |
| ScanQRCode | ui/host.py + ui/tools/qrcode.py | GtkTaskHost.scan_qr → open_qr_results | done | tests/test_ui_tools_qrcode.py::test_results_window_and_jobs |
| DoOCR | ui/host.py + ui/tools/ocr.py | GtkTaskHost.ocr → open_ocr (silent/background → pipeline) | done | tests/test_ui_tools_ocr.py::test_ocr_window_recognises_text_and_options |
| ShowBeforeUploadWindow | core/tasks.py | WorkerTask pipeline | planned | - |
| UploadImageToHost | core/tasks.py | WorkerTask pipeline | planned | - |
| DeleteFile | core/tasks.py | WorkerTask pipeline | planned | - |
