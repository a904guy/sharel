"""Every ToastClickAction (what clicking the toast or one of its buttons does)."""
from __future__ import annotations

import os
from pathlib import Path

from ..core import clipboard, desktop, imaging
from ..core.enums import ToastClickAction
from ..core.tasks import TaskInfo


def run_toast_action(app, action: ToastClickAction, info: TaskInfo) -> str:
    """Do it; returns a short description of what happened (for the log and tests)."""
    v = action.value
    path = info.file_path
    has_file = bool(path) and Path(path).exists()
    if v == "CloseNotification":
        return "closed"
    if v == "AnnotateImage" and has_file:
        from .windows.editor import open_editor_for_file
        open_editor_for_file(app, path)
        return f"editor:{path}"
    if v == "CopyImageToClipboard" and has_file:
        clipboard.set_image(imaging.load(path))
        return "copied image"
    if v == "CopyFile" and has_file:
        clipboard.set_files([path])
        return "copied file"
    if v == "CopyFilePath" and path:
        clipboard.set_text(path)
        return "copied path"
    if v == "CopyUrl" and info.url_result:
        clipboard.set_text(info.url_result)
        return "copied url"
    if v == "OpenFile" and has_file:
        desktop.open_file(path)
        return "opened file"
    if v == "OpenFolder" and path:
        desktop.show_in_folder(path)
        return "opened folder"
    if v == "OpenUrl":
        if info.url_result:
            desktop.open_url(info.url_result)
            return "opened url"
        if has_file:
            desktop.open_file(path)
            return "opened file"
    if v == "Upload" and has_file:
        app.tasks.upload_file(path, app.store.task_settings_for(None))
        return "upload started"
    if v == "PinToScreen" and has_file:
        from .windows.pin import pin_image
        pin_image(app, imaging.load(path))
        return "pinned"
    if v == "DeleteFile" and has_file:
        os.unlink(path)
        return "deleted"
    return "nothing to do"
