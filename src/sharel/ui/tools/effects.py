"""Tools → Image effects: the Image Effects window in tool mode (load an image, edit presets, save/copy/upload).

``ctx.files``: the first image is loaded; ``.sxie`` packages are imported as presets.
"""
from __future__ import annotations

from ...core.jobs import JobContext
from . import _base

_tool = {"win": None}


def open(app, ctx: JobContext):  # noqa: A001 - the tool contract
    from ..windows.effects import ImageEffectsWindow, open_effects_tool
    win = _tool["win"]
    if not isinstance(win, ImageEffectsWindow) or win.get_application() is not app:
        img, path = None, ""
        for f in ctx.files:
            if _base.is_image_path(f):
                img, path = _base.load_image_file(f), f
                break
        win = open_effects_tool(app, ctx.task_settings, img, path)
        _tool["win"] = win
    else:
        for f in ctx.files:
            if _base.is_image_path(f):
                win.set_image(_base.load_image_file(f), f)
                break
        win.present()
    for f in ctx.files:
        if f.lower().endswith(".sxie"):
            win.import_package(f)
    return win
