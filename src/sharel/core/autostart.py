"""Start with the system (ShareX's "Run ShareX when Windows starts"): an XDG autostart entry."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ENTRY_NAME = "app.sharel.ShareL.desktop"


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def executable() -> str:
    found = shutil.which("sharel")
    if found:
        return found
    return f"{sys.executable} -m sharel"


def desktop_entry() -> str:
    return ("[Desktop Entry]\nType=Application\nName=ShareL\nComment=Screen capture, file sharing and productivity\n"
            f"Exec={executable()} -silent\nIcon=app.sharel.ShareL\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
            "X-GNOME-Autostart-Delay=3\nNoDisplay=false\n")


def is_enabled() -> bool:
    return entry_path().exists()


def sync(enabled: bool) -> None:
    p = entry_path()
    if enabled:
        p.parent.mkdir(parents=True, exist_ok=True)
        text = desktop_entry()
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
    elif p.exists():
        p.unlink()
