"""Toast notifications (ShareX's NotificationForm) via org.freedesktop.Notifications.

A finished task posts one notification with the image preview and buttons. Clicking the toast
runs the "left click" ToastClickAction; the "right" and "middle" click actions become buttons,
as do the other useful actions. Every ToastClickAction is implemented by :func:`run_action`.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from ..core.config import TaskSettingsGeneral
from ..core.enums import ToastClickAction
from ..core.paths import APP_ID
from ..core.tasks import TaskInfo, TaskStatus

log = logging.getLogger("sharel.notify")

DEST = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"

ACTION_LABELS = {
    "CloseNotification": "Close", "AnnotateImage": "Edit", "CopyImageToClipboard": "Copy image",
    "CopyFile": "Copy file", "CopyFilePath": "Copy path", "CopyUrl": "Copy URL", "OpenFile": "Open",
    "OpenFolder": "Show in folder", "OpenUrl": "Open URL", "Upload": "Upload", "PinToScreen": "Pin",
    "DeleteFile": "Delete",
}


def available_actions(info: TaskInfo) -> list[ToastClickAction]:
    """Actions that make sense for this task (ShareX hides the ones without a target)."""
    has_file = bool(info.file_path) and Path(info.file_path).exists()
    is_image = has_file and info.is_image
    has_url = bool(info.url_result)
    out = [ToastClickAction.CloseNotification]
    for a in ToastClickAction:
        v = a.value
        if v in ("AnnotateImage", "CopyImageToClipboard", "PinToScreen") and not is_image:
            continue
        if v in ("CopyFile", "CopyFilePath", "OpenFile", "OpenFolder", "Upload", "DeleteFile") and not has_file:
            continue
        if v in ("CopyUrl", "OpenUrl") and not has_url:
            continue
        if a not in out:
            out.append(a)
    return out


def body_for(info: TaskInfo) -> tuple[str, str]:
    if info.status == TaskStatus.Failed:
        return "Task failed", "\n".join(info.errors)[:500] or info.title
    if info.url_result:
        return "Upload completed", info.url_result
    if info.file_path:
        return "Capture saved", info.file_path
    if info.ocr_text:
        return "Text recognized", info.ocr_text[:300]
    return "Task completed", info.title


class Notifier:
    """Posts toasts and turns their actions into ``on_action(action, info)`` calls."""

    def __init__(self, on_action: Callable[[ToastClickAction, TaskInfo], None]) -> None:
        self.on_action = on_action
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._by_id: dict[int, TaskInfo] = {}
        self._defaults: dict[int, ToastClickAction] = {}
        self._subs = [
            self.conn.signal_subscribe(DEST, DEST, "ActionInvoked", PATH, None, Gio.DBusSignalFlags.NONE,
                                       self._on_action, None),
            self.conn.signal_subscribe(DEST, DEST, "NotificationClosed", PATH, None, Gio.DBusSignalFlags.NONE,
                                       self._on_closed, None),
        ]
        self.posted: list[dict] = []  # for the debug log / tests

    def close(self) -> None:
        for s in self._subs:
            self.conn.signal_unsubscribe(s)
        self._subs.clear()

    def notify(self, info: TaskInfo, g: TaskSettingsGeneral, preview_path: str = "") -> int:
        summary, body = body_for(info)
        actions: list[str] = []
        avail = available_actions(info)
        left = g.toast_window_left_click_action
        actions += ["default", ACTION_LABELS[left.value]]
        buttons = [g.toast_window_right_click_action, g.toast_window_middle_click_action]
        for extra in ("CopyUrl", "OpenFolder", "AnnotateImage", "PinToScreen"):
            buttons.append(ToastClickAction[extra])
        seen = {left}
        for a in buttons:
            if a in avail and a not in seen and a != ToastClickAction.CloseNotification and len(seen) < 4:
                actions += [a.value, ACTION_LABELS[a.value]]
                seen.add(a)
        hints = {"desktop-entry": GLib.Variant("s", APP_ID), "category": GLib.Variant("s", "transfer.complete"
                                                                                      if info.uploaded else
                                                                                      "device")}
        image = preview_path or (info.file_path if info.is_image and info.file_path else "")
        if image and os.path.exists(image):
            hints["image-path"] = GLib.Variant("s", Path(image).resolve().as_uri())
        if info.status == TaskStatus.Failed:
            hints["urgency"] = GLib.Variant("y", 2)
        timeout = int(g.toast_window_duration * 1000) if g.toast_window_auto_hide else 0
        try:
            (nid,) = self.conn.call_sync(
                DEST, PATH, DEST, "Notify",
                GLib.Variant("(susssasa{sv}i)", ("ShareL", 0, APP_ID, summary, body, actions, hints, timeout)),
                None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
        except GLib.Error as e:
            log.warning("notification failed: %s", e.message)
            return 0
        self._by_id[nid] = info
        self._defaults[nid] = left
        self.posted.append({"id": nid, "summary": summary, "body": body, "actions": actions[::2],
                            "task": info.id})
        return nid

    def message(self, summary: str, body: str = "", urgent: bool = False) -> int:
        hints = {"desktop-entry": GLib.Variant("s", APP_ID)}
        if urgent:
            hints["urgency"] = GLib.Variant("y", 2)
        try:
            (nid,) = self.conn.call_sync(
                DEST, PATH, DEST, "Notify",
                GLib.Variant("(susssasa{sv}i)", ("ShareL", 0, APP_ID, summary, body, [], hints, 5000)),
                None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
            self.posted.append({"id": nid, "summary": summary, "body": body, "actions": [], "task": ""})
            return nid
        except GLib.Error:
            return 0

    def _on_action(self, _c, _s, _p, _i, _n, params, _d):
        nid, key = params.unpack()
        info = self._by_id.get(nid)
        if info is None:
            return
        action = self._defaults.get(nid, ToastClickAction.CloseNotification) if key == "default" else \
            ToastClickAction[key] if key in ToastClickAction.__members__ else None
        if action is not None:
            self.on_action(action, info)

    def _on_closed(self, _c, _s, _p, _i, _n, params, _d):
        nid, _reason = params.unpack()
        self._by_id.pop(nid, None)
        self._defaults.pop(nid, None)
