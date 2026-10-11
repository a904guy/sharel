"""Strip metadata (ShareX metadata stripper): remove EXIF, GPS, XMP, comments and text chunks from images.

Pick files (Add, drag and drop, ``ctx.files``); by default the originals are overwritten after a confirmation,
like ShareX; switch that off to write cleaned copies (output folder + ``$filename_stripped``). The ICC colour
profile is kept unless "Keep ICC profile" is off. The report lists, per file, how many metadata entries were
removed and the size before and after.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Adw

from ...core.jobs import JobContext
from ...tools.metadata import read_metadata, strip_metadata
from .. import gtkutil
from . import _base
from ._batch import BatchImageTool, open_batch_tool


@dataclass
class StripOptions:
    keep_icc_profile: bool = True
    overwrite: bool = True


@dataclass
class StripResult:
    path: Path
    source: str
    removed: int
    size_before: int
    size_after: int


def metadata_count(path: str) -> int:
    """Entries a stripper removes: EXIF/GPS tags, text chunks / comments and the XMP packet."""
    md = read_metadata(path)
    im = md.image
    if im is None:
        return 0
    return len(im.exif) + len(im.text) + (1 if im.xmp else 0)


class StripMetadataWindow(BatchImageTool):
    TITLE = "Strip metadata"
    RUN_LABEL = "Strip metadata"
    PATTERN = "$filename_stripped"
    PREVIEW = False
    SIZE = (900, 620)

    def __init__(self, app, ctx: JobContext) -> None:
        self.opts = StripOptions()
        super().__init__(app, ctx)
        self._sync_rows()

    def build_options(self):
        o = self.opts

        def set_(name, v):
            setattr(o, name, v)
            self._sync_rows()
        g = Adw.PreferencesGroup(title="Options")
        self.icc_row = _base.switch_row("Keep ICC profile", o.keep_icc_profile, lambda v: set_("keep_icc_profile", v),
                                        subtitle="Keeps colours unchanged")
        self.overwrite_row = _base.switch_row("Overwrite original files", o.overwrite, lambda v: set_("overwrite", v))
        g.add(self.icc_row)
        g.add(self.overwrite_row)
        return [g]

    def _sync_rows(self) -> None:
        if hasattr(self, "name_row"):
            self.name_row.set_visible(not self.opts.overwrite)
            self.output.set_visible(not self.opts.overwrite)

    def snapshot(self):
        return StripOptions(**vars(self.opts))

    def run(self) -> None:
        if not self.files.paths or self.batch.busy:
            return
        if self.opts.overwrite:
            n = len(self.files.paths)
            gtkutil.ask(self, "Strip metadata?", f"The metadata of {n} file(s) will be removed from the "
                                                 "original files. This can't be undone.",
                        [("cancel", "Cancel"), ("strip", "Strip metadata")],
                        lambda r: r == "strip" and BatchImageTool.run(self), default="strip", destructive="strip")
            return
        BatchImageTool.run(self)

    def process(self, path, o, cancel):
        before = os.path.getsize(path)
        removed = metadata_count(path)
        dst = None if o.overwrite else self.target_path(path, o)
        out = strip_metadata(path, dst, keep_icc_profile=o.keep_icc_profile)
        removed -= metadata_count(str(out))
        return StripResult(Path(out), path, removed, before, os.path.getsize(out))

    def describe_result(self, r: StripResult) -> str:
        return (f"{r.path.name}: removed {r.removed} metadata entr{'y' if r.removed == 1 else 'ies'} "
                f"({gtkutil.human_size(r.size_before)} → {gtkutil.human_size(r.size_after)})")


def open(app, ctx: JobContext) -> StripMetadataWindow:  # noqa: A001
    return open_batch_tool(StripMetadataWindow, app, ctx)
