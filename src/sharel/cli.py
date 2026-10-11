"""ShareL's command line, mirroring ShareX's:

    sharel                         start (or show the running instance)
    sharel -RectangleRegion        run any HotkeyType job (case-insensitive)
    sharel -workflow "My flow"     run a workflow by its description (or job name)
    sharel -silent                 start without showing the main window
    sharel -autoclose              exit after the jobs on this command line finish
    sharel file1.png file2.txt     upload files (URLs are downloaded and uploaded)
    sharel -ImageEditor a.png      tools take the files that follow them

Everything is forwarded to the running instance (GApplication command-line handling).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from .core.enums import HotkeyType

FLAGS = {"silent", "autoclose", "dev", "multiinstance", "portable", "noupdate"}
_JOBS = {j.value.lower(): j for j in HotkeyType if j.value != "None"}
# ShareX's aliases
_JOBS["exitsharel"] = HotkeyType.ExitShareX


@dataclass
class CliRequest:
    jobs: list[tuple[HotkeyType, list[str]]] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    silent: bool = False
    autoclose: bool = False
    dev: bool = False
    errors: list[str] = field(default_factory=list)
    version: bool = False
    help: bool = False

    @property
    def has_work(self) -> bool:
        return bool(self.jobs or self.workflows or self.files or self.urls)


def _is_url(s: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(s)
    return u.scheme in ("http", "https", "ftp") and bool(u.netloc)


def parse_args(argv: list[str]) -> CliRequest:
    req = CliRequest()
    args = list(argv)
    i = 0
    current_job: tuple[HotkeyType, list[str]] | None = None
    while i < len(args):
        a = args[i]
        if a in ("--version", "-version", "-v"):
            req.version = True
        elif a in ("--help", "-help", "-h", "-?"):
            req.help = True
        elif a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            name = a[1:].lower()
            if name == "workflow" or name == "task":
                if i + 1 < len(args):
                    req.workflows.append(args[i + 1])
                    i += 1
                else:
                    req.errors.append(f"{a} needs a workflow name")
                current_job = None
            elif name in FLAGS:
                if name == "silent":
                    req.silent = True
                elif name == "autoclose":
                    req.autoclose = True
                elif name == "dev":
                    req.dev = True
                current_job = None
            elif name in _JOBS:
                current_job = (_JOBS[name], [])
                req.jobs.append(current_job)
            else:
                req.errors.append(f"unknown option {a}")
        elif a.startswith("--"):
            req.errors.append(f"unknown option {a}")
        else:
            target = current_job[1] if current_job is not None and current_job[0].value in FILE_TOOLS else None
            if target is not None:
                target.append(a)
            elif _is_url(a):
                req.urls.append(a)
            else:
                req.files.append(a)
        i += 1
    return req


# Tools that take the file arguments following them (instead of uploading them)
FILE_TOOLS = {"ImageEditor", "ImageEffects", "ImageViewer", "ImageBeautifier", "ImageCombiner", "ImageSplitter",
              "ImageThumbnailer", "VideoConverter", "VideoThumbnailer", "VideoTrimmer", "AnimatedGifTrimmer",
              "HashCheck", "Metadata", "StripMetadata", "OCR", "PinToScreenFromFile", "BackgroundRemover",
              "ImageComparer", "IconConverter", "ImageResizer", "ImageConverter", "ImageWatermark", "AnalyzeImage",
              "IndexFolder", "FileUpload", "FolderUpload"}


def usage() -> str:
    return __doc__ or ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    req = parse_args(argv[1:])
    if req.help:
        print(usage())
        print("Jobs: " + " ".join(f"-{j.value}" for j in HotkeyType if j.value != "None"))
        return 0
    if req.version:
        from . import __version__
        print(f"ShareL {__version__}")
        return 0
    if req.errors:
        for e in req.errors:
            print(f"sharel: {e}", file=sys.stderr)
        return 2
    from .app import run
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
