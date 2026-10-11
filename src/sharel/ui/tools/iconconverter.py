"""Icon converter (ShareX ``IconConverterWindow``): image → multi-size ``.ico``, or ``.ico`` → PNG.

Choose or drop an image, tick the sizes (16, 32, 48, 64, 128, 256; non-square images are centred on a
transparent square), then "Convert" asks where to write the icon. An ``.ico`` source lists its frames and
converts its largest frame to PNG.
"""
from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Adw, Gtk
from PIL import Image

from ...core.jobs import JobContext
from ...tools import imagetools
from .. import gtkutil
from . import _base


class IconConverterWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Icon converter", 760, 560)
        self.path = ""
        self.image: Image.Image | None = None
        self.frames: list[Image.Image] = []
        self.output_path: Path | None = None
        src = Adw.PreferencesGroup(title="Source")
        self.source_row = Adw.ActionRow(title="Image", subtitle="Choose or drop a PNG, JPEG, … or ICO file")
        browse = _base.button("Browse…", self.browse)
        browse.set_valign(Gtk.Align.CENTER)
        self.source_row.add_suffix(browse)
        src.add(self.source_row)
        self.sizes_group = Adw.PreferencesGroup(title="Icon sizes")
        self.size_checks: dict[int, Gtk.CheckButton] = {}
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
        for s in imagetools.ICON_SIZES:
            cb = Gtk.CheckButton(label=f"{s} × {s}", active=True)
            cb.connect("toggled", lambda *_: self._sync())
            self.size_checks[s] = cb
            flow.append(cb)
        self.sizes_group.add(flow)
        self.preview_pane = _base.ImagePane("No image", 240, 200)
        self.status = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
        self.convert_button = _base.button("Convert…", self.convert_dialog, suggested=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        for w in (src, self.sizes_group, self.preview_pane, self.status):
            box.append(w)
        bottom = Gtk.Box(halign=Gtk.Align.END)
        bottom.append(self.convert_button)
        box.append(bottom)
        self.set_body(box)
        self.add_controller(_base.image_drop_target(lambda ps: self.load(ps[0])))
        self._sync()

    @property
    def is_ico(self) -> bool:
        return self.path.lower().endswith(".ico")

    def selected_sizes(self) -> list[int]:
        return [s for s, cb in self.size_checks.items() if cb.get_active()]

    def browse(self) -> None:
        gtkutil.open_files(self, "Choose image", lambda ps: ps and self.load(ps[0]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def load(self, path: str) -> None:
        try:
            if path.lower().endswith(".ico"):
                self.frames = imagetools.ico_frames(path)
                if not self.frames:
                    raise OSError("the icon has no images")
                self.image = max(self.frames, key=lambda f: f.width * f.height)
            else:
                self.frames = []
                self.image = _base.load_image_file(path)
        except (OSError, ValueError, SyntaxError) as e:
            self.error("Can't open image", f"{path}: {e}")
            return
        self.path = os.path.abspath(path)
        if self.frames:
            sizes = ", ".join(f"{f.width}×{f.height}" for f in self.frames)
            self.source_row.set_subtitle(f"{os.path.basename(path)} — icon with {len(self.frames)} image(s): {sizes}")
        else:
            square = " (will be centred on a square)" if self.image.width != self.image.height else ""
            self.source_row.set_subtitle(f"{os.path.basename(path)} — {self.image.width} × {self.image.height}"
                                         f"{square}")
        self.preview_pane.set_image(self.image)
        self._sync()

    def _sync(self) -> None:
        self.sizes_group.set_visible(not self.is_ico)
        self.convert_button.set_label("Convert to PNG…" if self.is_ico else "Convert…")
        self.convert_button.set_sensitive(self.image is not None and (self.is_ico or bool(self.selected_sizes())))

    def convert_dialog(self) -> None:
        if self.image is None:
            return
        p = Path(self.path)
        ext = "png" if self.is_ico else "ico"
        gtkutil.save_file(self, "Save as", str(p.with_suffix(f".{ext}")), lambda d: d and self.convert_to(d),
                          [f"*.{ext}"])

    def convert_to(self, dst: str) -> Path | None:
        try:
            if self.is_ico:
                out = imagetools.ico_to_png(self.path, dst)
            else:
                out = imagetools.image_to_ico(self.image, dst, self.selected_sizes())
        except (OSError, ValueError) as e:
            self.error("Conversion failed", str(e))
            return None
        self.output_path = out
        self.status.set_label(f"Saved {out} ({gtkutil.human_size(out.stat().st_size)})")
        return out


def open(app, ctx: JobContext) -> IconConverterWindow:  # noqa: A001
    win = IconConverterWindow.present_single(app, ctx)
    for f in ctx.files:
        if _base.is_image_path(f):
            win.load(f)
            break
    return win
