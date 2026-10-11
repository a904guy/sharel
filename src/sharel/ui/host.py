"""GtkTaskHost: the task pipeline's side effects done by the GTK app.

Workers call these from their threads; anything touching GTK is marshalled to the main loop, and
interactive steps (editor, dialogs) block the worker until the user answers.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from ..core import clipboard, desktop
from ..core.config import TaskSettings
from ..core.enums import FileExistAction
from ..core.tasks import TaskHost, TaskInfo
from . import sounds
from .gtkutil import call_main_async, idle

if TYPE_CHECKING:
    from ..app import ShareLApp

log = logging.getLogger("sharel.host")


class GtkTaskHost(TaskHost):
    def __init__(self, app: ShareLApp) -> None:
        self.app = app

    # -- clipboard --

    def copy_image(self, img: Image.Image) -> None:
        clipboard.set_image(img)

    def copy_text(self, text: str) -> None:
        clipboard.set_text(text)

    def copy_files(self, paths: list[str]) -> None:
        clipboard.set_files(paths)

    # -- interactive steps --

    def quick_task_menu(self, info, flags):
        from .windows.aftercapture import open_quick_task_menu
        return call_main_async(lambda done: open_quick_task_menu(self.app, info, flags, done))

    def after_capture_window(self, info, img, flags):
        from .windows.aftercapture import open_after_capture
        return call_main_async(lambda done: open_after_capture(self.app, info, img, flags, done))

    def beautify(self, img, ts: TaskSettings):
        from ..effects.beautifier import beautify
        return beautify(img, ts.image_settings.beautifier)

    def image_effects(self, img, ts: TaskSettings):
        from .windows.effects import open_effects_window
        return call_main_async(lambda done: open_effects_window(self.app, img, ts, done))

    def annotate(self, img, ts: TaskSettings, info: TaskInfo):
        from .windows.editor import open_editor
        return call_main_async(lambda done: open_editor(self.app, img, ts, done, task_mode=True,
                                                        title=info.file_name))

    def pin(self, img):
        from .windows.pin import pin_image
        idle(pin_image, self.app, img)

    def print_image(self, img, path=""):
        from .printing import print_image
        idle(print_image, self.app, img, self.app.store.app.print_settings)

    def save_dialog(self, suggested: Path):
        from .gtkutil import IMAGE_PATTERNS, save_file
        res = call_main_async(lambda done: save_file(self.app.active_parent(), "Save image as", str(suggested),
                                                     done, IMAGE_PATTERNS))
        return Path(res) if res else None

    def ask_file_exists(self, path: Path) -> FileExistAction:
        from .gtkutil import ask
        res = call_main_async(lambda done: ask(self.app.active_parent(), "File already exists",
                                               f"{path.name} already exists in {path.parent}.",
                                               [("Cancel", "Cancel"), ("UniqueName", "Keep both"),
                                                ("Overwrite", "Overwrite")], done, default="UniqueName",
                                               destructive="Overwrite"))
        return FileExistAction[res] if res in FileExistAction.__members__ else FileExistAction.Cancel

    def show_in_folder(self, path: str) -> None:
        desktop.show_in_folder(path)

    def analyze_image(self, path: str, img: Image.Image) -> None:
        from .tools.analyze import open_analyze
        idle(open_analyze, self.app, img, path)

    def scan_qr(self, img, info: TaskInfo) -> None:
        super().scan_qr(img, info)
        from .tools.qrcode import open_qr_results
        idle(open_qr_results, self.app, info.qr_text, img)

    def ocr(self, img, ts, info: TaskInfo) -> None:
        opts = ts.capture_settings.ocr
        if opts.silent or opts.process_in_background:
            super().ocr(img, ts, info)
            if opts.auto_copy and info.ocr_text:
                clipboard.set_text(info.ocr_text)
            return
        from .tools.ocr import open_ocr
        idle(open_ocr, self.app, img, opts)

    def before_upload(self, info, ts):
        from .windows.aftercapture import open_before_upload
        return bool(call_main_async(lambda done: open_before_upload(self.app, info, ts, done)))

    def after_upload_window(self, info):
        from .windows.aftercapture import open_after_upload
        idle(open_after_upload, self.app, info)

    def show_qr(self, text: str) -> None:
        from .tools.qrcode import open_qr_window
        idle(open_qr_window, self.app, text)

    def open_url(self, url: str) -> None:
        desktop.open_url(url)

    def notify(self, info, ts, img) -> None:
        idle(self.app.post_toast, info, ts)

    def play_sound(self, kind: str, ts) -> None:
        sounds.play(kind, ts.general_settings)

    def prompt_input(self, title, default):
        from .gtkutil import prompt_text
        return call_main_async(lambda done: prompt_text(self.app.active_parent(), title, default, done))

    def prompt_select(self, title, options):
        from .gtkutil import prompt_choice
        return call_main_async(lambda done: prompt_choice(self.app.active_parent(), title, options, done))

    def show_output(self, title, text):
        from .gtkutil import prompt_text
        call_main_async(lambda done: prompt_text(self.app.active_parent(), title or "Output", text,
                                                 lambda _v: done(None), multiline=True))
