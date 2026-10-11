"""Windows opened by name from menus, the tray and actions (``app.open-window::<name>``)."""
from __future__ import annotations

import importlib

# name -> (module in sharel.ui.windows, function(app))
NAMED = {
    "task-settings": ("tasksettings", "open_task_settings"),
    "app-settings": ("appsettings", "open_app_settings"),
    "hotkey-settings": ("hotkeysettings", "open_hotkey_settings"),
    "destination-settings": ("destinations", "open_destination_settings"),
    "custom-uploaders": ("customuploader", "open_custom_uploaders"),
    "history": ("history", "open_history"),
    "image-history": ("history", "open_image_history"),
    "debug-log": ("debuglog", "open_debug_log"),
    "about": ("about", "open_about"),
}


def open_named(app, name: str) -> None:
    module, fn = NAMED[name]
    mod = importlib.import_module(f"{__name__}.{module}")
    getattr(mod, fn)(app)
