"""ShareX's Image Effects window (``ImageEffectsForm``).

Presets on the left (New / Remove / Duplicate / Move up / Move down, name), the selected preset's effects
below (Add from the categorized registry, Remove, Duplicate, Clear, Move up / down, an Enabled check per
effect, custom name), a property editor generated from each effect's ``params()`` metadata, and a live
preview of ``ImageEffectPreset.apply`` rendered off the main loop (debounced). ``.sxie`` packages are imported
(button or drag and drop) into ``data_dir()/ImageEffects`` and exported with the "Package" button.

Two modes, as in ShareX:

* pipeline (``open_effects_window(app, img, ts, done)``, after-capture "Add image effects" with "Show image
  effects window" on): OK applies the selected preset, saves the presets and calls ``done(image)``; Cancel or
  closing the window calls ``done(None)`` (the task is cancelled).
* tool (``open_effects_tool``): load an image from a file or the clipboard, save / upload the result; the
  presets are saved when the window closes.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango
from PIL import Image, ImageDraw

from ...core import clipboard, desktop
from ...effects import sxie
from ...effects.base import CATEGORIES, ImageEffect, ImageEffectPreset, default_presets, load_all
from ...effects.common import image_effects_dir
from .. import gtkutil
from ..tools import _base
from ..tools._params import param_rows

log = logging.getLogger("sharel.ui.effects")

IMAGE_EFFECTS_URL = "https://getsharex.com/image-effects"


def sample_image() -> Image.Image:
    """The image shown when the tool opens without one: a gradient card with some text and shapes."""
    w, h = 480, 320
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (40 + x * 160 // w, 90 + y * 100 // h, 200 - x * 80 // w, 255)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, w - 40, h - 40), 24, fill=(255, 255, 255, 230))
    d.ellipse((70, 90, 190, 210), fill=(230, 70, 60, 255))
    d.rectangle((220, 100, 420, 130), fill=(60, 60, 60, 255))
    d.rectangle((220, 150, 380, 170), fill=(120, 120, 120, 255))
    d.rectangle((220, 185, 400, 205), fill=(120, 120, 120, 255))
    return img


def load_presets(ts) -> tuple[list[ImageEffectPreset], int]:
    raw = list(ts.image_settings.image_effect_presets or [])
    presets = [ImageEffectPreset.from_jsonable(d) for d in raw if isinstance(d, dict)]
    if not presets:
        presets = default_presets()
    sel = min(max(0, ts.image_settings.selected_image_effect_preset), len(presets) - 1)
    return presets, sel


class EffectRow(Gtk.ListBoxRow):
    def __init__(self, effect: ImageEffect, toggled: Callable[[EffectRow, bool], None]) -> None:
        super().__init__()
        self.effect = effect
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        self.check = Gtk.CheckButton(active=effect.enabled, tooltip_text="Enabled")
        self.check.connect("toggled", lambda c: toggled(self, c.get_active()))
        self.label = Gtk.Label(label=effect.describe(), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.append(self.check)
        box.append(self.label)
        self.set_child(box)

    def refresh(self) -> None:
        self.label.set_label(self.effect.describe())


class ImageEffectsWindow(Adw.Window):
    def __init__(self, app, img: Image.Image | None, ts, done: Callable[[Image.Image | None], None] | None = None,
                 *, tool_mode: bool = False, file_path: str = "") -> None:
        super().__init__(application=app, title="Image effects", default_width=1200, default_height=760)
        load_all()
        self.app = app
        self.ts = ts
        self.done_cb = done
        self.tool_mode = tool_mode
        self.file_path = file_path
        self.source = (img or sample_image()).convert("RGBA")
        self.presets, self.preset_index = load_presets(ts)
        self.effect_index = -1
        self.result: Image.Image | None = None
        self.last_error = ""
        self.last_message = ""
        self._finished = False
        self._updating = False
        self.accepting = threading.Event()

        actions = Gio.SimpleActionGroup()
        add = Gio.SimpleAction.new("add", GLib.VariantType.new("s"))
        add.connect("activate", lambda _a, p: self.add_effect(p.unpack()))
        actions.add_action(add)
        self.insert_action_group("fx", actions)

        self.preview = _base.LivePreview(self._snapshot, lambda p: p.apply(self.source), self._show_result,
                                         self._show_error)
        self._build()
        self.connect("close-request", self._on_close_request)
        self.add_controller(_base.image_drop_target(self.handle_dropped_paths))
        self._reload_presets()
        self.schedule_preview()

    # ---- layout --------------------------------------------------------------------------

    def _build(self) -> None:
        header = Adw.HeaderBar()
        load_menu = Gio.Menu()
        load_menu.append("From file…", "fxwin.load-file")
        load_menu.append("From clipboard", "fxwin.load-clipboard")
        win_actions = Gio.SimpleActionGroup()
        for name, cb in (("load-file", self.load_image_dialog), ("load-clipboard", self.load_clipboard)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            win_actions.add_action(a)
        self.insert_action_group("fxwin", win_actions)
        self.load_button = Gtk.MenuButton(label="Load image", menu_model=load_menu, tooltip_text="Load image")
        header.pack_start(self.load_button)
        self.save_button = _base.button("Save image…", self.save_image_dialog)
        self.upload_button = _base.button("Upload image", self.upload_result)
        self.copy_button = _base.button("Copy image", self.copy_result)
        header.pack_start(self.save_button)
        header.pack_start(self.copy_button)
        header.pack_start(self.upload_button)
        if not self.tool_mode:
            self.ok_button = _base.button("OK", self.accept, suggested=True)
            self.cancel_button = _base.button("Cancel", self.cancel)
            header.pack_end(self.ok_button)
            header.pack_end(self.cancel_button)
        else:
            self.ok_button = self.cancel_button = None
        self.online_button = _base.button("Online presets", lambda: desktop.open_url(IMAGE_EFFECTS_URL),
                                          icon="help-browser-symbolic",
                                          tooltip="Browse image effect presets on getsharex.com")
        header.pack_end(self.online_button)

        # presets column
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                       margin_start=10, margin_end=6, width_request=280)
        lbl = Gtk.Label(label="Presets", xalign=0)
        lbl.add_css_class("heading")
        left.append(lbl)
        self.preset_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.preset_list.add_css_class("boxed-list")
        self.preset_list.connect("row-selected", self._on_preset_selected)
        left.append(_base.scrolled(self.preset_list, min_h=120))
        bar = Gtk.Box(spacing=4)
        self.preset_new_button = _base.button("New", self.new_preset, icon="list-add-symbolic", tooltip="New preset")
        self.preset_remove_button = _base.button("Remove", self.remove_preset, icon="list-remove-symbolic",
                                                 tooltip="Remove preset")
        self.preset_duplicate_button = _base.button("Duplicate", self.duplicate_preset,
                                                    icon="edit-copy-symbolic", tooltip="Duplicate preset")
        self.preset_up_button = _base.button("Move up", lambda: self.move_preset(-1), icon="go-up-symbolic",
                                             tooltip="Move preset up")
        self.preset_down_button = _base.button("Move down", lambda: self.move_preset(1), icon="go-down-symbolic",
                                               tooltip="Move preset down")
        self.import_button = _base.button("Import…", self.import_dialog, tooltip="Import a .sxie package")
        self.export_button = _base.button("Package…", self.export_dialog,
                                          tooltip="Export the preset as a .sxie package")
        for b in (self.preset_new_button, self.preset_remove_button, self.preset_duplicate_button,
                  self.preset_up_button, self.preset_down_button):
            bar.append(b)
        left.append(bar)
        bar2 = Gtk.Box(spacing=4)
        bar2.append(self.import_button)
        bar2.append(self.export_button)
        left.append(bar2)
        self.preset_name = Gtk.Entry(placeholder_text="Preset name")
        self.preset_name.update_property([Gtk.AccessibleProperty.LABEL], ["Preset name"])
        self.preset_name.connect("changed", lambda e: self.rename_preset(e.get_text()))
        left.append(self.preset_name)

        lbl = Gtk.Label(label="Effects", xalign=0, margin_top=8)
        lbl.add_css_class("heading")
        left.append(lbl)
        self.effect_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.effect_list.add_css_class("boxed-list")
        self.effect_list.connect("row-selected", self._on_effect_selected)
        left.append(_base.scrolled(self.effect_list, min_h=160))
        ebar = Gtk.Box(spacing=4)
        self.effect_add_button = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=self._add_menu(),
                                                tooltip_text="Add effect")
        self.effect_add_button.update_property([Gtk.AccessibleProperty.LABEL], ["Add effect"])
        self.effect_remove_button = _base.button("Remove effect", self.remove_effect, icon="list-remove-symbolic")
        self.effect_duplicate_button = _base.button("Duplicate effect", self.duplicate_effect,
                                                    icon="edit-copy-symbolic")
        self.effect_clear_button = _base.button("Clear effects", self.clear_effects, icon="edit-clear-all-symbolic")
        self.effect_up_button = _base.button("Move effect up", lambda: self.move_effect(-1), icon="go-up-symbolic")
        self.effect_down_button = _base.button("Move effect down", lambda: self.move_effect(1),
                                               icon="go-down-symbolic")
        self.refresh_button = _base.button("Refresh preview", self.schedule_preview, icon="view-refresh-symbolic")
        for b in (self.effect_add_button, self.effect_remove_button, self.effect_duplicate_button,
                  self.effect_clear_button, self.effect_up_button, self.effect_down_button, self.refresh_button):
            ebar.append(b)
        left.append(ebar)

        # parameters column
        self.params_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=10,
                                  margin_bottom=10, margin_start=6, margin_end=6)
        self.params_group: Adw.PreferencesGroup | None = None
        self.params_empty = Gtk.Label(label="Select an effect to edit its settings", margin_top=24)
        self.params_empty.add_css_class("dim-label")
        self.params_box.append(self.params_empty)
        params = _base.scrolled(self.params_box, min_w=340)

        # preview column
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                        margin_start=6, margin_end=10)
        self.preview_pane = _base.ImagePane("Rendering…")
        self.error_label = Gtk.Label(label="", wrap=True, xalign=0)
        self.error_label.add_css_class("error")
        right.append(self.preview_pane)
        right.append(self.error_label)

        inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=params, end_child=right,
                          resize_start_child=False, shrink_start_child=False, position=380)
        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=left, end_child=inner,
                          resize_start_child=False, shrink_start_child=False, position=300)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(outer)
        self.set_content(view)

    def _add_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        effects = load_all()
        for cat in CATEGORIES:
            sub = Gio.Menu()
            for name, cls in sorted(((n, c) for n, c in effects.items() if c.category == cat),
                                    key=lambda nc: nc[1].display_name.lower()):
                item = Gio.MenuItem.new(cls.display_name, None)
                item.set_action_and_target_value("fx.add", GLib.Variant("s", name))
                sub.append_item(item)
            menu.append_submenu(cat, sub)
        return menu

    # ---- presets -------------------------------------------------------------------------

    @property
    def preset(self) -> ImageEffectPreset | None:
        if 0 <= self.preset_index < len(self.presets):
            return self.presets[self.preset_index]
        return None

    def _reload_presets(self) -> None:
        self._updating = True
        try:
            self.preset_list.remove_all()
            for p in self.presets:
                self.preset_list.append(Gtk.Label(label=p.name or "(unnamed)", xalign=0, margin_top=6,
                                                  margin_bottom=6, margin_start=8, margin_end=8))
            if self.preset is not None:
                self.preset_list.select_row(self.preset_list.get_row_at_index(self.preset_index))
        finally:
            self._updating = False
        self._load_preset()

    def _load_preset(self) -> None:
        p = self.preset
        self._updating = True
        try:
            self.preset_name.set_text(p.name if p else "")
        finally:
            self._updating = False
        self.preset_name.set_sensitive(p is not None)
        self.effect_index = -1
        self._reload_effects()
        self.schedule_preview()

    def _on_preset_selected(self, _lb, row) -> None:
        if self._updating or row is None:
            return
        self.preset_index = row.get_index()
        self._load_preset()

    def select_preset(self, index: int) -> None:
        row = self.preset_list.get_row_at_index(index)
        if row is not None:
            self.preset_list.select_row(row)

    def new_preset(self) -> None:
        self.presets.append(ImageEffectPreset(name=f"Preset {len(self.presets) + 1}"))
        self.preset_index = len(self.presets) - 1
        self._reload_presets()

    def remove_preset(self) -> None:
        if self.preset is None:
            return
        del self.presets[self.preset_index]
        self.preset_index = min(self.preset_index, len(self.presets) - 1)
        self._reload_presets()

    def duplicate_preset(self) -> None:
        p = self.preset
        if p is None:
            return
        dup = copy.deepcopy(p)
        self.presets.insert(self.preset_index + 1, dup)
        self.preset_index += 1
        self._reload_presets()

    def move_preset(self, delta: int) -> None:
        i, j = self.preset_index, self.preset_index + delta
        if self.preset is None or not 0 <= j < len(self.presets):
            return
        self.presets[i], self.presets[j] = self.presets[j], self.presets[i]
        self.preset_index = j
        self._reload_presets()

    def rename_preset(self, name: str) -> None:
        if self._updating or self.preset is None:
            return
        self.preset.name = name
        row = self.preset_list.get_row_at_index(self.preset_index)
        if row is not None:
            row.get_child().set_label(name or "(unnamed)")

    # ---- effects -------------------------------------------------------------------------

    @property
    def effect(self) -> ImageEffect | None:
        p = self.preset
        if p is not None and 0 <= self.effect_index < len(p.effects):
            return p.effects[self.effect_index]
        return None

    def effect_rows(self) -> list[EffectRow]:
        out = []
        i = 0
        while (row := self.effect_list.get_row_at_index(i)) is not None:
            out.append(row)
            i += 1
        return out

    def _reload_effects(self) -> None:
        self._updating = True
        try:
            self.effect_list.remove_all()
            p = self.preset
            for e in (p.effects if p else []):
                self.effect_list.append(EffectRow(e, self._on_effect_toggled))
            if self.effect is not None:
                self.effect_list.select_row(self.effect_list.get_row_at_index(self.effect_index))
        finally:
            self._updating = False
        self._load_effect_editor()
        self._sync_buttons()

    def _on_effect_selected(self, _lb, row) -> None:
        if self._updating:
            return
        self.effect_index = row.get_index() if row is not None else -1
        self._load_effect_editor()
        self._sync_buttons()

    def _on_effect_toggled(self, row: EffectRow, active: bool) -> None:
        row.effect.enabled = active
        self.schedule_preview()

    def select_effect(self, index: int) -> None:
        row = self.effect_list.get_row_at_index(index)
        if row is not None:
            self.effect_list.select_row(row)

    def add_effect(self, type_name: str) -> ImageEffect | None:
        cls = load_all().get(type_name)
        if cls is None:
            return None
        if self.preset is None:
            self.new_preset()
        p = self.preset
        e = cls()
        insert_at = self.effect_index + 1 if self.effect is not None else len(p.effects)
        p.effects.insert(insert_at, e)
        self.effect_index = insert_at
        self._reload_effects()
        self.schedule_preview()
        return e

    def remove_effect(self) -> None:
        p = self.preset
        if self.effect is None:
            return
        del p.effects[self.effect_index]
        self.effect_index = min(self.effect_index, len(p.effects) - 1)
        self._reload_effects()
        self.schedule_preview()

    def duplicate_effect(self) -> None:
        e = self.effect
        if e is None:
            return
        self.preset.effects.insert(self.effect_index + 1, copy.deepcopy(e))
        self.effect_index += 1
        self._reload_effects()
        self.schedule_preview()

    def clear_effects(self) -> None:
        p = self.preset
        if p is None or not p.effects:
            return
        p.effects.clear()
        self.effect_index = -1
        self._reload_effects()
        self.schedule_preview()

    def move_effect(self, delta: int) -> None:
        p = self.preset
        i, j = self.effect_index, self.effect_index + delta
        if self.effect is None or not 0 <= j < len(p.effects):
            return
        p.effects[i], p.effects[j] = p.effects[j], p.effects[i]
        self.effect_index = j
        self._reload_effects()
        self.schedule_preview()

    def _sync_buttons(self) -> None:
        p = self.preset
        has_p = p is not None
        n = len(p.effects) if p else 0
        has_e = self.effect is not None
        self.preset_remove_button.set_sensitive(has_p)
        self.preset_duplicate_button.set_sensitive(has_p)
        self.preset_up_button.set_sensitive(has_p and self.preset_index > 0)
        self.preset_down_button.set_sensitive(has_p and self.preset_index < len(self.presets) - 1)
        self.export_button.set_sensitive(has_p)
        self.effect_remove_button.set_sensitive(has_e)
        self.effect_duplicate_button.set_sensitive(has_e)
        self.effect_clear_button.set_sensitive(n > 0)
        self.effect_up_button.set_sensitive(has_e and self.effect_index > 0)
        self.effect_down_button.set_sensitive(has_e and self.effect_index < n - 1)

    def _load_effect_editor(self) -> None:
        if self.params_group is not None:
            self.params_box.remove(self.params_group)
            self.params_group = None
        e = self.effect
        self.params_empty.set_visible(e is None)
        if e is None:
            return
        g = Adw.PreferencesGroup(title=e.display_name or e.type_name, description=e.category)
        name_row = Adw.EntryRow(title="Name (empty = default description)", text=e.name)

        def on_name(r):
            e.name = r.get_text()
            self._refresh_effect_label()
        name_row.connect("changed", on_name)
        g.add(name_row)
        for row in param_rows(e, e.params(), self._on_param_changed):
            g.add(row)
        self.params_group = g
        self.params_box.append(g)

    def param_row(self, field_name: str) -> Gtk.Widget | None:
        """The editor row of the selected effect's parameter ``field_name``."""
        if self.params_group is None:
            return None
        return _base_find(self.params_group, lambda w: getattr(w, "field_name", None) == field_name)

    def _on_param_changed(self) -> None:
        self._refresh_effect_label()
        self.schedule_preview()

    def _refresh_effect_label(self) -> None:
        row = self.effect_list.get_row_at_index(self.effect_index)
        if isinstance(row, EffectRow):
            row.refresh()

    # ---- preview -------------------------------------------------------------------------

    def _snapshot(self) -> ImageEffectPreset:
        return copy.deepcopy(self.preset) if self.preset is not None else ImageEffectPreset("", [])

    def schedule_preview(self) -> None:
        self.preview.schedule()

    def _show_result(self, img: Image.Image) -> None:
        self.result = img
        self.last_error = ""
        self.error_label.set_label("")
        self.preview_pane.set_image(img)

    def _show_error(self, msg: str) -> None:
        self.last_error = msg
        self.error_label.set_label(f"Effect error: {msg}")

    def render_now(self) -> Image.Image:
        """Apply the selected preset to the source image (blocking; used off the main loop)."""
        return self._snapshot().apply(self.source)

    # ---- image in / out ------------------------------------------------------------------

    def set_image(self, img: Image.Image, path: str = "") -> None:
        self.source = img.convert("RGBA")
        self.file_path = path
        self.schedule_preview()

    def handle_dropped_paths(self, paths: list[str]) -> None:
        for p in paths:
            if p.lower().endswith(".sxie"):
                self.import_package(p)
            elif _base.is_image_path(p):
                self.set_image(_base.load_image_file(p), p)

    def load_image_dialog(self) -> None:
        gtkutil.open_files(self, "Load image", lambda ps: ps and self.handle_dropped_paths(ps[:1]),
                           patterns=gtkutil.IMAGE_PATTERNS, multiple=False)

    def load_clipboard(self) -> None:
        got = _base.clipboard_image()
        if got is None:
            self._error("Clipboard", "The clipboard has no image.")
            return
        self.set_image(*got)

    def _rendered(self, then: Callable[[Image.Image], None]) -> None:
        """Run ``then(result)`` with an up-to-date render (computed on a thread when needed)."""
        if self.result is not None and self.preview.idle.is_set() and not self.last_error:
            then(self.result)
            return

        def work():
            try:
                img = self.render_now()
            except Exception as e:
                gtkutil.idle(self._error, "Image effects", str(e))
                return
            gtkutil.idle(then, img)
        threading.Thread(target=work, daemon=True).start()

    def save_image_dialog(self) -> None:
        base = Path(self.file_path).stem if self.file_path else "image"
        folder = str(Path(self.file_path).parent) if self.file_path else str(Path.home())
        gtkutil.save_file(self, "Save image", os.path.join(folder, f"{base}-effects.png"),
                          lambda p: p and self.save_image(p), gtkutil.IMAGE_PATTERNS)

    def save_image(self, path: str) -> None:
        from ...tools.imagetools import save_image

        def write(img):
            try:
                save_image(img, path)
            except (OSError, ValueError) as e:
                self._error("Save failed", str(e))
                return
            self.last_message = f"Saved {path}"
        self._rendered(write)

    def copy_result(self) -> None:
        def copy(img):
            clipboard.set_image(img)
            self.last_message = "Copied"
        self._rendered(copy)

    def upload_result(self) -> None:
        name = Path(self.file_path).stem if self.file_path else "image-effects"
        self._rendered(lambda img: _base.upload_image(self.app, img, name, self.ts))

    # ---- packages ------------------------------------------------------------------------

    def import_dialog(self) -> None:
        gtkutil.open_files(self, "Import image effect package", lambda ps: [self.import_package(p) for p in ps],
                           patterns=["*.sxie", "*.json"], filter_name="ShareX image effects")

    def import_package(self, path: str) -> ImageEffectPreset | None:
        try:
            if path.lower().endswith(".json"):
                preset = sxie.from_sharex_json(Path(path).read_bytes())
                preset.name = preset.name or Path(path).stem
            else:
                preset = sxie.import_sxie(path, image_effects_dir())
        except (OSError, ValueError, KeyError) as e:
            self._error("Import failed", f"{os.path.basename(path)}: {e}")
            return None
        self.presets.append(preset)
        self.preset_index = len(self.presets) - 1
        self._reload_presets()
        self.last_message = f"Imported {preset.name}"
        return preset

    def export_dialog(self) -> None:
        p = self.preset
        if p is None:
            return
        if not p.name.strip():
            self._error("Missing preset name", "The preset name cannot be empty.")
            return
        gtkutil.save_file(self, "Export image effect package", os.path.join(str(Path.home()), f"{p.name}.sxie"),
                          lambda path: path and self.export_package(path), ["*.sxie"])

    def export_package(self, path: str) -> Path | None:
        p = self.preset
        if p is None:
            return None
        if not path.lower().endswith(".sxie"):
            path += ".sxie"
        try:
            out = sxie.export_sxie(p, path)
        except (OSError, ValueError) as e:
            self._error("Export failed", str(e))
            return None
        self.last_message = f"Exported {out}"
        return out

    # ---- finishing -----------------------------------------------------------------------

    def save_presets(self) -> None:
        data = [p.to_jsonable() for p in self.presets]
        sel = max(0, self.preset_index)
        targets = {id(self.app.store.app.default_task_settings.image_settings):
                   self.app.store.app.default_task_settings.image_settings}
        targets.setdefault(id(self.ts.image_settings), self.ts.image_settings)
        for s in targets.values():
            s.image_effect_presets = copy.deepcopy(data)
            s.selected_image_effect_preset = sel
        self.app.store.save_app()
        self.app.settings_changed()

    def accept(self) -> None:
        """OK: apply the selected preset to the image, save the presets and hand the result back."""
        if self._finished or self.accepting.is_set():
            return
        self.accepting.set()
        for b in (self.ok_button, self.cancel_button):
            if b is not None:
                b.set_sensitive(False)
        snap = self._snapshot()
        src = self.source

        def work():
            try:
                img, err = snap.apply(src), None
            except Exception as e:
                img, err = None, str(e)
            gtkutil.idle(self._accept_done, img, err)
        threading.Thread(target=work, daemon=True, name="sharel-effects-ok").start()

    def _accept_done(self, img, err) -> None:
        if err is not None:
            self.accepting.clear()
            for b in (self.ok_button, self.cancel_button):
                if b is not None:
                    b.set_sensitive(True)
            self._error("Image effects", err)
            return
        self.save_presets()
        self._finish(img)
        self.destroy()

    def cancel(self) -> None:
        self._finish(None)
        self.destroy()

    def _on_close_request(self, *_a) -> bool:
        if self.tool_mode:
            self.save_presets()
        self._finish(None)
        return False

    def _finish(self, img) -> None:
        if self._finished:
            return
        self._finished = True
        self.preview.cancel()
        if self.done_cb is not None:
            cb, self.done_cb = self.done_cb, None
            cb(img)

    def _error(self, heading: str, body: str) -> None:
        self.last_message = f"{heading}: {body}"
        gtkutil.alert(self, heading, body)


def _base_find(widget: Gtk.Widget, pred) -> Gtk.Widget | None:
    if pred(widget):
        return widget
    child = widget.get_first_child()
    while child is not None:
        hit = _base_find(child, pred)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None


_open_windows = _base.WindowList()


def open_effects_window(app, img: Image.Image, ts, done: Callable[[Image.Image | None], None]) -> ImageEffectsWindow:
    """After-capture Image Effects window: ``done(image)`` on OK, ``done(None)`` on Cancel/close."""
    win = _open_windows.add(ImageEffectsWindow(app, img, ts, done, tool_mode=False))
    win.present()
    return win


def open_effects_tool(app, ts, img: Image.Image | None = None, path: str = "") -> ImageEffectsWindow:
    """Tools → Image effects: edit presets, load an image, save / upload the result."""
    win = _open_windows.add(ImageEffectsWindow(app, img, ts, None, tool_mode=True, file_path=path))
    win.present()
    return win


def import_image_effect_files(app, paths: list[str]) -> list[ImageEffectPreset]:
    """Opening a .sxie package (``sharel x.sxie``, ``sharel -ImageEffect x.sxie``): ShareX's
    TaskHelpers.ImportImageEffect. Each package is read by the .sxie reader (its images extracted into the image
    effects folder) and added to the default task settings' presets, shown in the Image effects window. If
    "Add image effects" isn't an after-capture task, ShareX's question follows: "Would you like to enable image
    effects?" Yes adds it. Nothing is uploaded. The question is ``win.enable_dialog``."""
    from ...core.enums import AfterCaptureTasks
    ts = app.store.app.default_task_settings
    win = next((w for w in open_windows() if w.tool_mode), None) or open_effects_tool(app, ts)
    win.present()
    win.enable_dialog = None
    presets = [p for p in (win.import_package(path) for path in paths) if p is not None]
    if not presets:
        return presets
    win.save_presets()
    if AfterCaptureTasks.AddImageEffects in ts.after_capture_job:
        return presets

    def answered(response: str) -> None:
        win.enable_dialog = None
        if response == "yes":
            ts.after_capture_job = ts.after_capture_job | AfterCaptureTasks.AddImageEffects
            app.store.save_app()
            app.settings_changed()
    win.enable_dialog = gtkutil.ask(win, "ShareL", "Would you like to enable image effects?",
                                    [("no", "No"), ("yes", "Yes")], answered, default="yes")
    return presets


def open_windows() -> list[ImageEffectsWindow]:
    return _open_windows.live()
