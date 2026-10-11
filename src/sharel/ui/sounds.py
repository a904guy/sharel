"""ShareX's notification sounds: Capture, TaskCompleted, ActionCompleted, Error.

Default sounds come from the XDG sound theme (played by canberra when present, so the user's
theme and volume apply); custom sound files from TaskSettingsGeneral play through GStreamer's
``playbin``.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from ..core.config import TaskSettingsGeneral

log = logging.getLogger("sharel.sounds")

THEME_EVENTS = {"Capture": "camera-shutter", "TaskCompleted": "complete", "ActionCompleted": "message",
                "Error": "dialog-error"}
THEME_DIRS = [Path("/usr/share/sounds/freedesktop/stereo")]

played: list[str] = []  # what played last (tests and the debug log read this)


def theme_file(kind: str) -> Path | None:
    event = THEME_EVENTS.get(kind)
    for d in THEME_DIRS:
        for ext in (".oga", ".ogg", ".wav"):
            p = d / f"{event}{ext}"
            if p.exists():
                return p
    return None


def custom_file(kind: str, g: TaskSettingsGeneral) -> str:
    pairs = {"Capture": (g.use_custom_capture_sound, g.custom_capture_sound_path),
             "TaskCompleted": (g.use_custom_task_completed_sound, g.custom_task_completed_sound_path),
             "ActionCompleted": (g.use_custom_action_completed_sound, g.custom_action_completed_sound_path),
             "Error": (g.use_custom_error_sound, g.custom_error_sound_path)}
    use, path = pairs.get(kind, (False, ""))
    return path if use and path and Path(path).exists() else ""


def enabled(kind: str, g: TaskSettingsGeneral) -> bool:
    return {"Capture": g.play_sound_after_capture, "TaskCompleted": g.play_sound_after_upload,
            "ActionCompleted": g.play_sound_after_action, "Error": True}.get(kind, True)


def _playbin(path: str) -> None:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    from ..capture.pwframe import gst_init
    gst_init()
    pb = Gst.ElementFactory.make("playbin", None)
    if pb is None:
        return
    pb.set_property("uri", Path(path).resolve().as_uri())
    pb.set_state(Gst.State.PLAYING)
    bus = pb.get_bus()
    bus.timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pb.set_state(Gst.State.NULL)


def play(kind: str, g: TaskSettingsGeneral, block: bool = False) -> str:
    """Play the sound for ``kind`` if enabled. Returns what was played ("" if nothing)."""
    if not enabled(kind, g):
        return ""
    custom = custom_file(kind, g)
    theme = theme_file(kind)
    canberra = shutil.which("canberra-gtk-play")
    if custom:
        what = custom
    elif canberra:
        what = f"theme:{THEME_EVENTS[kind]}"
    elif theme is not None:
        what = str(theme)
    else:
        return ""

    def run():
        try:
            if custom:
                _playbin(custom)
            elif canberra:
                subprocess.run([canberra, "-i", THEME_EVENTS[kind], "-d", "ShareL"], timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                _playbin(str(theme))
        except Exception as e:
            log.info("sound %s failed: %s", kind, e)

    played.append(what)
    if block:
        run()
    else:
        threading.Thread(target=run, daemon=True, name=f"sound-{kind}").start()
    return what
