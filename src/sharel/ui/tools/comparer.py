"""Image comparer (ShareX ``ImageComparerWindow``): compare two images.

Pick (or drop) a first and a second image. Views: "Slider" (the first image left of the divider, the second
right of it; drag the slider), "Difference" (changed pixels white on black, ShareX) and "Highlight" (the second
image dimmed with the changes in red). The similarity, changed pixel count, size difference and the bounding box
of the changes are shown; the current view can be saved or copied.
"""
from __future__ import annotations

import os

from gi.repository import Gtk, Pango
from PIL import Image

from ...core import clipboard
from ...core.jobs import JobContext
from ...tools import imagetools
from .. import gtkutil
from . import _base

MODES = ["Slider", "Difference", "Highlight"]


def slider_view(a: Image.Image, b: Image.Image, position: float) -> Image.Image:
    """``a`` left of the split at ``position`` (0..100 % of the width), ``b`` right of it, on the larger canvas."""
    w, h = max(a.width, b.width), max(a.height, b.height)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(b.convert("RGBA"))
    split = round(w * max(0.0, min(100.0, position)) / 100)
    if split > 0:
        left = a.convert("RGBA").crop((0, 0, min(split, a.width), a.height))
        out.paste((0, 0, 0, 0), (0, 0, split, h))
        out.alpha_composite(left)
    if 0 < split < w:
        out.paste((255, 255, 255, 255), (max(0, split - 1), 0, min(w, split + 1), h))
    return out


class ComparerWindow(_base.ToolWindow):
    def __init__(self, app, ctx: JobContext) -> None:
        super().__init__(app, "Image comparer", 1000, 700)
        self.images: list[Image.Image | None] = [None, None]
        self.paths = ["", ""]
        self.mode = 0
        self.position = 50.0
        self.result: imagetools.ComparisonResult | None = None
        self.view: Image.Image | None = None

        top = Gtk.Box(spacing=8, margin_top=10, margin_start=10, margin_end=10)
        self.slot_labels = []
        for i, name in enumerate(("First image", "Second image")):
            b = _base.button(f"{name}…", lambda i=i: self.browse(i))
            lbl = Gtk.Label(label="(none)", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
            lbl.add_css_class("dim-label")
            top.append(b)
            top.append(lbl)
            self.slot_labels.append(lbl)
        self.swap_button = _base.button("Swap", self.swap, icon="object-flip-horizontal-symbolic")
        top.append(self.swap_button)
        modes = Gtk.Box(spacing=8, margin_start=10, margin_end=10)
        self.mode_drop = Gtk.DropDown.new_from_strings(MODES)
        self.mode_drop.set_tooltip_text("View")
        self.mode_drop.connect("notify::selected", lambda d, _p: self.set_mode(d.get_selected()))
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_value(self.position)
        self.scale.set_hexpand(True)
        self.scale.set_tooltip_text("Divider position")
        self.scale.connect("value-changed", lambda s: self.set_position(s.get_value()))
        modes.append(Gtk.Label(label="View"))
        modes.append(self.mode_drop)
        modes.append(self.scale)
        self.similarity = Gtk.Label(label="Choose two images to compare", xalign=0, margin_start=10,
                                    selectable=True)
        self.similarity.add_css_class("title-4")
        self.details = Gtk.Label(label="", xalign=0, margin_start=10, selectable=True)
        self.details.add_css_class("dim-label")
        self.pane = _base.ImagePane("Choose or drop two images")
        self.pane.set_margin_start(10)
        self.pane.set_margin_end(10)
        self.save_button = _base.button("Save view…", self.save_dialog)
        self.copy_button = _base.button("Copy view", self.copy)
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=10, margin_end=10, halign=Gtk.Align.END)
        bottom.append(self.copy_button)
        bottom.append(self.save_button)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for w in (top, modes, self.similarity, self.details, self.pane, bottom):
            body.append(w)
        self.set_body(body)
        self.add_controller(_base.image_drop_target(self.drop))
        self._refresh()

    def browse(self, slot: int) -> None:
        gtkutil.open_files(self, "Choose image", lambda ps: ps and self.set_image(slot, ps[0]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def drop(self, paths: list[str]) -> None:
        paths = [p for p in paths if _base.is_image_path(p)]
        if len(paths) >= 2:
            self.set_image(0, paths[0])
            self.set_image(1, paths[1])
        elif paths:
            self.set_image(0 if self.images[0] is None else 1, paths[0])

    def set_image(self, slot: int, path: str) -> None:
        try:
            self.images[slot] = _base.load_image_file(path)
        except OSError as e:
            self.error("Can't open image", f"{path}: {e}")
            return
        self.paths[slot] = path
        self.slot_labels[slot].set_label(os.path.basename(path))
        self.slot_labels[slot].set_tooltip_text(path)
        self.result = None
        self._refresh()

    def swap(self) -> None:
        self.images.reverse()
        self.paths.reverse()
        for lbl, p in zip(self.slot_labels, self.paths, strict=True):
            lbl.set_label(os.path.basename(p) if p else "(none)")
        self.result = None
        self._refresh()

    def set_mode(self, mode: int) -> None:
        self.mode = mode
        self._refresh()

    def set_position(self, value: float) -> None:
        self.position = value
        if self.mode == 0:
            self._refresh()

    def _refresh(self) -> None:
        a, b = self.images
        ready = a is not None and b is not None
        self.scale.set_visible(self.mode == 0)
        for w in (self.save_button, self.copy_button, self.swap_button):
            w.set_sensitive(ready)
        if not ready:
            self.view = None
            self.pane.set_image(a or b)
            return
        if self.result is None:
            self.result = imagetools.compare_images(a, b)
            r = self.result
            self.similarity.set_label(f"Similarity: {r.similarity_percent:.2f}%"
                                      + (" (identical)" if r.identical else ""))
            parts = [f"{r.changed_pixels:,} of {r.total_pixels:,} pixels differ ({r.changed_percent:.2f}%)"]
            if r.size_a != r.size_b:
                parts.append(f"sizes differ: {r.size_a[0]} × {r.size_a[1]} vs {r.size_b[0]} × {r.size_b[1]}")
            if r.bbox:
                parts.append(f"changes within x {r.bbox[0]}–{r.bbox[2]}, y {r.bbox[1]}–{r.bbox[3]}")
            self.details.set_label("; ".join(parts))
        if self.mode == 0:
            self.view = slider_view(a, b, self.position)
        elif self.mode == 1:
            self.view = self.result.diff_image
        else:
            self.view = imagetools.highlight_differences(a, b)
        self.pane.set_image(self.view)

    def copy(self) -> None:
        if self.view is not None:
            clipboard.set_image(self.view)
            self.toast("Copied to the clipboard")

    def save_dialog(self) -> None:
        folder = os.path.dirname(self.paths[1] or self.paths[0]) or os.path.expanduser("~")
        gtkutil.save_file(self, "Save comparison", os.path.join(folder, f"comparison-{MODES[self.mode].lower()}.png"),
                          lambda p: p and self.save_to(p), gtkutil.IMAGE_PATTERNS)

    def save_to(self, path: str) -> None:
        if self.view is None:
            return
        try:
            imagetools.save_image(self.view, path)
        except (OSError, ValueError) as e:
            self.error("Save failed", str(e))
            return
        self.toast(f"Saved {os.path.basename(path)}")


def open(app, ctx: JobContext) -> ComparerWindow:  # noqa: A001
    win = ComparerWindow.present_single(app, ctx)
    files = [f for f in ctx.files if _base.is_image_path(f)]
    if files:
        win.drop(files[:2])
    return win
