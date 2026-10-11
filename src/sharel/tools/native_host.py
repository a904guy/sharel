"""The browser extension's native messaging host (ShareX's NativeMessagingHost).

Chrome / Firefox start ``sharel-native-host`` and write messages framed as a 4-byte little-endian
length plus UTF-8 JSON: ``{"Action": "UploadImage" | "UploadVideo" | "UploadAudio" | "UploadText" |
"ShortenURL", "URL": "...", "Text": "..."}`` (ShareX's NativeMessagingInput). Each message is
forwarded to the running ShareL (started if needed) through its ``app.native`` GAction.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import time
from typing import BinaryIO

from ..core.enums import NativeMessagingAction

HOST_NAME = "app.sharel.sharel"
APP_ID = "app.sharel.ShareL"
APP_PATH = "/app/sharel/ShareL"


def read_message(stream: BinaryIO) -> dict | None:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("<I", raw)
    if n > 64 * 1024 * 1024:
        raise ValueError("message too large")
    body = stream.read(n)
    return json.loads(body.decode("utf-8"))


def write_message(stream: BinaryIO, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def validate(msg: dict) -> tuple[NativeMessagingAction, str, str]:
    action = str(msg.get("Action", "None"))
    if action not in NativeMessagingAction.__members__ or action == "None":
        raise ValueError(f"unknown action {action!r}")
    url, text = str(msg.get("URL", "") or ""), str(msg.get("Text", "") or "")
    a = NativeMessagingAction[action]
    if a == NativeMessagingAction.UploadText and not text:
        raise ValueError("UploadText needs Text")
    if a != NativeMessagingAction.UploadText and not url:
        raise ValueError(f"{action} needs URL")
    return a, url, text


def _activate(payload: str, timeout: float = 15.0) -> None:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def running() -> bool:
        (has,) = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "NameHasOwner", GLib.Variant("(s)", (APP_ID,)), None, Gio.DBusCallFlags.NONE, 2000,
                                None).unpack()
        return bool(has)
    if not running():
        exe = shutil.which("sharel") or sys.argv[0].replace("sharel-native-host", "sharel")
        subprocess.Popen([exe, "-silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        deadline = time.monotonic() + timeout
        while not running() and time.monotonic() < deadline:
            time.sleep(0.2)
    conn.call_sync(APP_ID, APP_PATH, "org.gtk.Actions", "Activate",
                   GLib.Variant("(sava{sv})", ("native", [GLib.Variant("s", payload)], {})), None,
                   Gio.DBusCallFlags.NONE, 10000, None)


def handle(msg: dict, forward=_activate) -> dict:
    try:
        action, url, text = validate(msg)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    forward(json.dumps({"Action": action.value, "URL": url, "Text": text}))
    return {"ok": True}


def main() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            msg = read_message(stdin)
        except (ValueError, json.JSONDecodeError) as e:
            write_message(stdout, {"ok": False, "error": str(e)})
            continue
        if msg is None:
            return 0
        write_message(stdout, handle(msg))


def chrome_manifest(path: str, extension_id: str) -> dict:
    return {"name": HOST_NAME, "description": "ShareL native messaging host", "path": path, "type": "stdio",
            "allowed_origins": [f"chrome-extension://{extension_id}/"]}


def firefox_manifest(path: str, extension_id: str = "sharel@hawkins.app") -> dict:
    return {"name": HOST_NAME, "description": "ShareL native messaging host", "path": path, "type": "stdio",
            "allowed_extensions": [extension_id]}


if __name__ == "__main__":
    sys.exit(main())
