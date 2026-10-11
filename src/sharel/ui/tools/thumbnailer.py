"""Image thumbnailer (ShareX ``ImageThumbnailerWindow``): JPEG thumbnails of many images.

Width / height (0 = proportional to the other side; both = fit inside), JPEG quality, output folder and
file name (``$filename_th`` by default, ShareX). Defaults come from the task settings' tool options.
"""
from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Adw

from ...core.jobs import JobContext
from ...tools import imagetools
from . import _base
from ._batch import BatchImageTool, open_batch_tool


@dataclass
class ThumbOptions:
    width: int = 200
    height: int = 0
    quality: int = 90


class ThumbnailerWindow(BatchImageTool):
    TITLE = "Image thumbnailer"
    RUN_LABEL = "Generate thumbnails"
    PATTERN = "$filename_th"

    def __init__(self, app, ctx: JobContext) -> None:
        t = ctx.task_settings.tool_settings
        self.opts = ThumbOptions(t.image_thumbnailer_width, t.image_thumbnailer_height, t.image_thumbnailer_quality)
        super().__init__(app, ctx)

    def build_options(self):
        g = Adw.PreferencesGroup(title="Thumbnail size")
        o = self.opts

        def setter(name, conv=int):
            def set_(v):
                setattr(o, name, conv(v))
                self.options_changed()
            return set_
        self.width_row = _base.spin_row("Width", o.width, 0, 10000, setter("width"), subtitle="0 = proportional")
        self.height_row = _base.spin_row("Height", o.height, 0, 10000, setter("height"),
                                         subtitle="0 = proportional")
        self.quality_row = _base.spin_row("JPEG quality", o.quality, 1, 100, setter("quality"))
        for r in (self.width_row, self.height_row, self.quality_row):
            g.add(r)
        return [g]

    def snapshot(self):
        return ThumbOptions(self.opts.width, self.opts.height, self.opts.quality)

    def save_defaults(self, o) -> None:
        self.tools.image_thumbnailer_width = o.width
        self.tools.image_thumbnailer_height = o.height
        self.tools.image_thumbnailer_quality = o.quality

    def output_ext(self, src, opts) -> str:
        return "jpg"

    def render_preview(self, img, o):
        return imagetools.create_thumbnail(img, o.width, o.height)

    def process(self, path, o, cancel):
        img = _base.load_image_file(path)
        thumb = imagetools.create_thumbnail(img, o.width, o.height)
        return imagetools.save_image(thumb, self.target_path(path, o), "JPEG", o.quality)

    def describe_result(self, r) -> str:
        return f"Created {r.name}"


def open(app, ctx: JobContext) -> ThumbnailerWindow:  # noqa: A001
    return open_batch_tool(ThumbnailerWindow, app, ctx)
