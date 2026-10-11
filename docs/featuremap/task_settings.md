| Workflows: named hotkey entries with own TaskSettings | core/config.py, ui/windows/tasksettings.py, ui/windows/hotkeysettings.py | HotkeysConfig entries; per-section use_default_* switches gate the rows; effective_task_settings | done | tests/test_ui_settings.py::test_workflow_use_default_switch_gates_and_effective_settings |
| TaskSettings: General | core/config.py, ui/windows/tasksettings.py | generated rows (sounds, toast window, notifications) | done | tests/test_ui_settings.py::test_default_task_settings_edit_and_save |
| TaskSettings: Image (format PNG/JPG/GIF/BMP/TIFF/WebP, quality, size fallback, thumbnails) | core/imaging.py, ui/windows/tasksettings.py | generated rows + effects presets summary and Image effects… button | done | tests/test_ui_settings.py::test_default_task_settings_edit_and_save |
| TaskSettings: Capture (cursor, delay, transparent, region/recorder/scrolling/OCR options) | core/config.py, ui/windows/tasksettings.py | generated rows incl. region capture, screen recorder + FFmpeg, scrolling, OCR, auto capture | done | tests/test_ui_settings.py::test_default_task_settings_edit_and_save |
| TaskSettings: Upload (naming pattern, clipboard upload options) | core/config.py, ui/windows/tasksettings.py | name patterns with live NameParser preview and token menu, clipboard options, uploader filters editor | done | tests/test_ui_settings.py::test_name_pattern_preview_and_token_insert |
| TaskSettings: Actions (external programs %input/%output, extension filters) | core/tasks.py, ui/windows/tasksettings.py | list editor (name, program, args, output extension, extensions filter, enabled) | done | tests/test_ui_settings.py::test_actions_and_uploader_filter_editors_add_rows |
| TaskSettings: Watch folders (auto upload) | core/watchfolders.py, ui/windows/tasksettings.py | Gio.FileMonitor; editor applies app.watchers.apply() | done | tests/test_ui_settings.py::test_watch_folder_editor_uploads_dropped_file |
| TaskSettings: Tools | core/config.py, ui/windows/tasksettings.py | generated rows grouped per tool | done | tests/test_ui_settings.py::test_default_task_settings_edit_and_save |
| TaskSettings: Advanced | core/config.py, ui/windows/tasksettings.py | generated rows | done | tests/test_ui_settings.py::test_default_task_settings_edit_and_save |
| FileExistAction.Ask | core/imaging.py | - | planned | - |
| FileExistAction.Overwrite | core/imaging.py | - | planned | - |
| FileExistAction.UniqueName | core/imaging.py | - | planned | - |
| FileExistAction.Cancel | core/imaging.py | - | planned | - |
| Upload from clipboard content (image/files/text/URL) | core/jobs.py | clipboard chain | planned | - |
| Auto capture (repeat every N seconds) | capture/autocapture.py | GLib timer | planned | - |
