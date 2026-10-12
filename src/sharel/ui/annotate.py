"""GTK widgets around the toolkit-independent ShapeManager (sharel.editor.manager).

* :class:`CanvasView` — a DrawingArea that forwards pointer/key/scroll input to the manager in
  image coordinates and paints it. A *mapping* turns widget coordinates into image pixels: fixed
  for region-capture overlays (one view per monitor), zoom + pan for the editor.
* :class:`Toolbar` — ShareX's ShapeManagerMenu generated from ``manager.toolbar_model()``: tools,
  colours, tool options, and the Edit / Image / Capture / Options menus.
* :class:`DialogBridge` — answers the manager's dialog callbacks (text, image file, sticker,
  image/canvas size, new image) with libadwaita dialogs.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..editor.manager import PENDING, Mod, ShapeManager

ICONS = {
    "tick": "object-select-symbolic", "disk-black": "document-save-symbolic",
    "disks-black": "document-save-as-symbolic", "clipboard": "edit-copy-symbolic",
    "drive-globe": "send-to-symbolic", "printer": "printer-symbolic", "cursor": "input-mouse-symbolic",
    "layer-shape-region": "selection-mode-symbolic", "layer-shape-ellipse-region": "selection-mode-symbolic",
    "layer-shape-polygon": "selection-mode-symbolic", "layer-shape": "view-paged-symbolic",
    "layer-shape-ellipse": "media-record-symbolic", "pencil": "document-edit-symbolic",
    "pencil-arrow": "document-edit-symbolic", "layer-shape-line": "sharel-draw-line-symbolic",
    "layer-shape-arrow": "sharel-draw-arrow-symbolic", "edit-outline": "format-text-bold-symbolic",
    "edit-shade": "insert-text-symbolic", "balloon-box-left": "mail-unread-symbolic",
    "counter-reset": "view-list-ordered-symbolic", "magnifier-zoom": "zoom-in-symbolic",
    "folder-open-image": "insert-image-symbolic", "monitor-image": "camera-photo-symbolic",
    "smiley-cool": "face-cool-symbolic", "stamp-cursor": "input-mouse-symbolic",
    "eraser": "edit-clear-symbolic", "layer-shade": "view-conceal-symbolic", "grid": "view-grid-symbolic",
    "highlighter-text": "format-text-underline-symbolic", "image-crop": "zoom-fit-best-symbolic",
    "table-delete-column": "edit-cut-symbolic", "arrow-circle-225-left": "edit-undo-symbolic",
    "arrow-circle-315": "edit-redo-symbolic", "document-copy": "edit-copy-symbolic",
    "layer-minus": "edit-delete-symbolic", "layers-stack-arrange": "go-top-symbolic",
    "layers-arrange": "go-up-symbolic", "layers-arrange-back": "go-down-symbolic",
    "layers-stack-arrange-back": "go-bottom-symbolic", "image-empty": "document-new-symbolic",
    "image-plus": "insert-image-symbolic", "camera": "camera-photo-symbolic",
    "image-select": "zoom-original-symbolic", "image-resize": "view-fullscreen-symbolic",
    "image-resize-actual": "zoom-fit-best-symbolic", "arrow-circle": "object-rotate-right-symbolic",
    "arrow-circle-135-left": "object-rotate-left-symbolic", "arrow-circle-double": "view-refresh-symbolic",
    "layer-flip": "object-flip-horizontal-symbolic", "layer-flip-vertical": "object-flip-vertical-symbolic",
    "image-saturation": "applications-graphics-symbolic", "cross": "window-close-symbolic",
    "monitor": "preferences-desktop-display-symbolic", "monitor-window": "window-new-symbolic",
}


def gdk_mods(state: Gdk.ModifierType) -> int:
    m = Mod.NONE
    if state & Gdk.ModifierType.SHIFT_MASK:
        m |= Mod.SHIFT
    if state & Gdk.ModifierType.CONTROL_MASK:
        m |= Mod.CTRL
    if state & Gdk.ModifierType.ALT_MASK:
        m |= Mod.ALT
    return int(m)


class FixedMapping:
    """Overlay on one monitor: widget logical px -> capture image px."""

    def __init__(self, offset: tuple[float, float], scale: float) -> None:
        self.offset = offset  # monitor origin relative to the capture region, logical px
        self.scale = scale  # image px per logical px

    def to_image(self, wx: float, wy: float) -> tuple[float, float]:
        return ((self.offset[0] + wx) * self.scale, (self.offset[1] + wy) * self.scale)

    def view(self) -> tuple[float, tuple[float, float]]:
        return 1.0 / self.scale, (-self.offset[0], -self.offset[1])


class ZoomMapping:
    """Editor: zoom + pan, centred when smaller than the view."""

    def __init__(self) -> None:
        self.zoom = 1.0
        self.pan = [0.0, 0.0]

    def to_image(self, wx: float, wy: float) -> tuple[float, float]:
        return ((wx - self.pan[0]) / self.zoom, (wy - self.pan[1]) / self.zoom)

    def view(self) -> tuple[float, tuple[float, float]]:
        return self.zoom, (self.pan[0], self.pan[1])


class CanvasView(Gtk.DrawingArea):
    def __init__(self, manager: ShapeManager, mapping, *, on_result: Callable | None = None,
                 editor: bool = False) -> None:
        # an explicit role so assistive tech (and end-to-end tests) see the canvas and its bounds
        super().__init__(hexpand=True, vexpand=True, focusable=True, can_focus=True,
                         accessible_role=Gtk.AccessibleRole.IMG)
        self.manager = manager
        self.mapping = mapping
        self.on_result = on_result
        self.editor = editor
        self._t0 = time.monotonic()
        self._panning: tuple[float, float, float, float] | None = None
        self.peers: list[CanvasView] = []  # other monitors' views of the same manager
        # True while one of its dialogs (text, sticker) is open: keys meant for the dialog must not reach the
        # canvas, where letters are tool shortcuts and Space/Enter/Escape finish or cancel the capture
        self.keys_blocked: Callable[[], bool] = lambda: False
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Image canvas" if editor else "Region capture"])
        self.set_cursor(Gdk.Cursor.new_from_name("crosshair" if not editor else "default"))

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._pressed)
        click.connect("released", self._released)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        self.add_controller(motion)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._key)
        self.add_controller(key)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        self._tick = self.add_tick_callback(self._on_tick)
        self._last_tick = 0.0

    # -- painting --

    def _draw(self, _area, cr, width, height) -> None:
        cr.set_source_rgb(0.18, 0.18, 0.18) if self.editor else cr.set_source_rgb(0, 0, 0)
        cr.paint()
        scale, offset = self.mapping.view()
        self.manager.draw(cr, scale, offset, time_s=time.monotonic() - self._t0, view_size=(width, height))

    def _on_tick(self, _w, clock) -> bool:
        now = clock.get_frame_time() / 1e6
        if now - self._last_tick > 1 / 30 and self.manager.options.enable_animations:
            self._last_tick = now
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def redraw_all(self) -> None:
        self.queue_draw()
        for p in self.peers:
            p.queue_draw()

    # -- input --

    def _img(self, x: float, y: float) -> tuple[float, float]:
        return self.mapping.to_image(x, y)

    def _finish(self, res) -> None:
        if res is not None and self.on_result:
            self.on_result(res)

    def _pressed(self, gesture, n_press, x, y) -> None:
        self.grab_focus()
        button = gesture.get_current_button()
        mods = gdk_mods(gesture.get_current_event_state())
        if self.editor and (button == 2 or (button == 1 and mods & Mod.ALT and False)):
            self._panning = (x, y, *self.mapping.pan)
            return
        ix, iy = self._img(x, y)
        if n_press == 2 and button == 1:
            self._finish(self.manager.double_click(ix, iy, button, mods))
        else:
            self._finish(self.manager.press(ix, iy, button, mods))
        self.redraw_all()

    def _released(self, gesture, _n, x, y) -> None:
        button = gesture.get_current_button()
        if self._panning is not None and button == 2:
            self._panning = None
            return
        ix, iy = self._img(x, y)
        self._finish(self.manager.release(ix, iy, button, gdk_mods(gesture.get_current_event_state())))
        self.redraw_all()

    def _motion(self, ctl, x, y) -> None:
        if self._panning is not None:
            sx, sy, px, py = self._panning
            self.mapping.pan = [px + x - sx, py + y - sy]
            self.queue_draw()
            return
        ix, iy = self._img(x, y)
        self.manager.motion(ix, iy, gdk_mods(ctl.get_current_event_state()))
        self.redraw_all()

    def _leave(self, _ctl) -> None:
        self.manager.pointer_left()
        self.redraw_all()

    def _key(self, ctl, keyval, _code, state) -> bool:
        if self.keys_blocked():
            return True
        name = Gdk.keyval_name(keyval) or ""
        mods = gdk_mods(state)
        if self.editor and mods & Mod.CTRL and name in ("plus", "equal", "KP_Add", "minus", "KP_Subtract", "0"):
            self.zoom_step(0 if name == "0" else (1 if name in ("plus", "equal", "KP_Add") else -1))
            return True
        res = self.manager.key(name, mods)
        self._finish(res)
        self.redraw_all()
        return True

    def _scroll(self, ctl, dx, dy) -> bool:
        mods = gdk_mods(ctl.get_current_event_state())
        if self.editor and mods & Mod.CTRL:
            self.zoom_step(-1 if dy > 0 else 1)
            return True
        if self.editor and not (mods & (Mod.SHIFT | Mod.ALT)) and isinstance(self.mapping, ZoomMapping) and \
                self.manager.current_shape is None:
            self.mapping.pan[1] -= dy * 40
            self.mapping.pan[0] -= dx * 40
            self.queue_draw()
            return True
        self.manager.scroll(dx, dy, mods)
        self.redraw_all()
        return True

    # -- editor zoom --

    ZOOMS = [0.1, 0.25, 0.33, 0.5, 0.66, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]

    def zoom_step(self, direction: int) -> None:
        if not isinstance(self.mapping, ZoomMapping):
            return
        z = self.mapping.zoom
        if direction == 0:
            new = 1.0
        elif direction > 0:
            new = next((v for v in self.ZOOMS if v > z + 1e-6), self.ZOOMS[-1])
        else:
            new = next((v for v in reversed(self.ZOOMS) if v < z - 1e-6), self.ZOOMS[0])
        w, h = self.get_width(), self.get_height()
        cx, cy = self.mapping.to_image(w / 2, h / 2)
        self.mapping.zoom = new
        self.mapping.pan = [w / 2 - cx * new, h / 2 - cy * new]
        self.queue_draw()

    def zoom_to_fit(self) -> None:
        if not isinstance(self.mapping, ZoomMapping):
            return
        w, h = max(1, self.get_width()), max(1, self.get_height())
        iw, ih = self.manager.canvas.size
        z = min(1.0, (w - 40) / iw, (h - 40) / ih) if iw and ih else 1.0
        self.mapping.zoom = max(0.05, z)
        self.center()

    def center(self) -> None:
        if not isinstance(self.mapping, ZoomMapping):
            return
        w, h = self.get_width(), self.get_height()
        iw, ih = self.manager.canvas.size
        z = self.mapping.zoom
        self.mapping.pan = [(w - iw * z) / 2, (h - ih * z) / 2]
        self.queue_draw()


# ---- toolbar ------------------------------------------------------------------------------------


def _icon_button(item, toggle: bool = False) -> Gtk.Button:
    icon = ICONS.get(item.icon, "")
    cls = Gtk.ToggleButton if toggle else Gtk.Button
    b = cls()
    if icon:
        b.set_icon_name(icon)
    else:
        b.set_label(item.label.split(" (")[0][:12] or item.id)
    b.set_tooltip_text(item.label)
    b.update_property([Gtk.AccessibleProperty.LABEL], [item.label])
    b.add_css_class("flat")
    b.set_sensitive(item.enabled)
    return b


class Toolbar(Gtk.Box):
    """Generated from ``manager.toolbar_model()``; rebuilt on every manager change."""

    def __init__(self, manager: ShapeManager, dialogs: DialogBridge, *, compact: bool = False,
                 extra_end: list[Gtk.Widget] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.manager = manager
        self.dialogs = dialogs
        self.compact = compact
        self.extra_end = extra_end or []
        self.add_css_class("toolbar")
        self._building = False
        self._pending = False
        self.rebuild()

    def schedule_rebuild(self) -> None:
        if not self._pending:
            self._pending = True
            GLib.idle_add(self._idle_rebuild)

    def _idle_rebuild(self) -> bool:
        self._pending = False
        self.rebuild()
        return False

    def rebuild(self) -> None:
        self._building = True
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if child not in self.extra_end:
                self.remove(child)
            child = nxt
        for w in self.extra_end:
            if w.get_parent() is self:
                self.remove(w)
        tm = self.manager.toolbar_model()
        for item in tm.main:
            self._add_item(item)
        for item in tm.tools:
            if item.kind == "separator":
                self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
                continue
            if not item.visible:
                continue
            b = _icon_button(item, toggle=True)
            b.set_active(item.checked)
            b.connect("toggled", lambda btn, it=item: (not self._building and btn.get_active() and it.activate
                                                        and (it.activate(), self.schedule_rebuild())))
            self.append(b)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        for item in tm.colors:
            if item.visible:
                self.append(self._color_button(item))
        if tm.tool_options_visible and any(i.visible for i in tm.tool_options):
            self.append(self._menu_button("Tool options", "preferences-other-symbolic", tm.tool_options))
        for title, icon, items in (("Edit", "document-edit-symbolic", tm.edit_menu),
                                   ("Image", "image-x-generic-symbolic", tm.image_menu),
                                   ("Capture", "camera-photo-symbolic", tm.capture_menu),
                                   ("Options", "open-menu-symbolic", tm.options_menu)):
            if any(i.visible and i.kind != "separator" for i in items):
                self.append(self._menu_button(title, icon, items))
        for w in self.extra_end:
            self.append(w)
        self._building = False

    def _add_item(self, item) -> None:
        if item.kind == "separator":
            self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            return
        if not item.visible:
            return
        b = _icon_button(item)
        b.connect("clicked", lambda *_a, it=item: self._activate(it))
        self.append(b)

    def _activate(self, item) -> None:
        if item.kind == "dialog":
            self.dialogs.run_toolbar_dialog(item, self.schedule_rebuild)
            return
        if item.activate:
            item.activate()
        self.schedule_rebuild()

    def _color_button(self, item) -> Gtk.Widget:
        from .forms import hex_from_rgba, rgba_from_hex
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), tooltip_text=item.label)
        btn.update_property([Gtk.AccessibleProperty.LABEL], [item.label.rstrip(".")])
        if item.value:
            btn.set_rgba(rgba_from_hex(item.value))
        btn.connect("notify::rgba", lambda b, _p, it=item: (not self._building and it.setter
                                                            and it.setter(hex_from_rgba(b.get_rgba()))))
        return btn

    def _menu_button(self, title: str, icon: str, items) -> Gtk.MenuButton:
        mb = Gtk.MenuButton(icon_name=icon, tooltip_text=title)
        mb.update_property([Gtk.AccessibleProperty.LABEL], [title])
        mb.add_css_class("flat")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        for it in items:
            w = self._menu_item(it, pop)
            if w is not None:
                box.append(w)
        pop.set_child(Gtk.ScrolledWindow(child=box, propagate_natural_height=True, propagate_natural_width=True,
                                         max_content_height=600))
        mb.set_popover(pop)
        return mb

    def _menu_item(self, it, pop: Gtk.Popover) -> Gtk.Widget | None:
        if not it.visible:
            return None
        if it.kind == "separator":
            return Gtk.Separator()
        label = it.label.rstrip(":")
        if it.kind in ("action", "dialog", "tool"):
            b = Gtk.Button(label=it.label, sensitive=it.enabled)
            b.add_css_class("flat")
            b.get_child().set_halign(Gtk.Align.START)

            def go(*_a, it=it):
                pop.popdown()
                self._activate(it)
            b.connect("clicked", go)
            return b
        if it.kind == "toggle":
            c = Gtk.CheckButton(label=it.label, active=bool(it.value if it.value is not None else it.checked))
            c.connect("toggled", lambda cb, it=it: it.setter and (it.setter(cb.get_active()), self.schedule_rebuild()))
            return c
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        if it.kind == "int":
            adj = Gtk.Adjustment(value=float(it.value or 0), lower=it.min if it.min is not None else 0,
                                 upper=it.max if it.max is not None else 10000, step_increment=it.step or 1)
            sp = Gtk.SpinButton(adjustment=adj, numeric=True)
            sp.connect("value-changed", lambda s, it=it: it.setter and it.setter(int(s.get_value())))
            row.append(sp)
        elif it.kind == "choice":
            choices = it.choices or []
            dd = Gtk.DropDown.new_from_strings([lbl for _v, lbl in choices])
            vals = [v for v, _l in choices]
            if str(it.value) in vals:
                dd.set_selected(vals.index(str(it.value)))
            dd.connect("notify::selected", lambda d, _p, it=it, vals=vals: it.setter and vals and
                       (it.setter(vals[d.get_selected()]), self.schedule_rebuild()))
            row.append(dd)
        elif it.kind == "color":
            row.append(self._color_button(it))
        elif it.kind == "font":
            fb = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
            if it.value:
                fb.set_font_desc(Pango.FontDescription.from_string(str(it.value)))
            fb.connect("notify::font-desc", lambda f, _p, it=it: it.setter and it.setter(
                f.get_font_desc().to_string()))
            row.append(fb)
        elif it.kind == "size":
            v = it.value or (0, 0)
            for i in (0, 1):
                sp = Gtk.SpinButton.new_with_range(1, 100000, 1)
                sp.set_value(v[i] if isinstance(v, (tuple, list)) else 0)
                sp.connect("value-changed", lambda s, i=i, it=it, v=list(v):
                           (v.__setitem__(i, int(s.get_value())), it.setter and it.setter(tuple(v))))
                row.append(sp)
        elif it.kind == "submenu":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(Gtk.Label(label=label, xalign=0, css_classes=["heading"]))
            for sub in it.items:
                w = self._menu_item(sub, pop)
                if w is not None:
                    box.append(w)
            return box
        return row


# ---- dialogs --------------------------------------------------------------------------------------


class DialogBridge:
    """Answers ShapeManager dialog callbacks with libadwaita dialogs on ``parent()``."""

    def __init__(self, manager_getter: Callable[[], ShapeManager], parent: Callable[[], Gtk.Widget | None],
                 redraw: Callable[[], None]) -> None:
        self.pending = 0  # dialogs open now (see CanvasView.keys_blocked)
        self.manager = manager_getter
        self.parent = parent
        self.redraw = redraw

    def request_text(self, shape, initial: str):
        from .gtkutil import prompt_text

        def done(text):
            self.pending -= 1
            self.manager().set_text(shape, text)
            self.redraw()
        self.pending += 1
        self.text_dialog = prompt_text(self.parent(), "Text", initial or "", done, multiline=True)
        return PENDING

    def request_image_file(self):
        from .gtkutil import IMAGE_PATTERNS, open_files

        def done(paths):
            shape = getattr(self.manager(), "_pending_dialog_shape", None)
            self.manager().set_shape_image_file(shape, paths[0] if paths else None)
            self.redraw()
        open_files(self.parent(), "Insert image", done, patterns=IMAGE_PATTERNS, multiple=False)
        return PENDING

    def request_sticker(self):
        from ..editor import stickers
        m = self.manager()
        pack = m.annotation.sticker_pack
        files = stickers.list_stickers(pack)
        shape = getattr(m, "_pending_dialog_shape", None)
        d = Adw.Dialog(title="Sticker", content_width=420, content_height=380)
        grid = Gtk.FlowBox(max_children_per_line=8, selection_mode=Gtk.SelectionMode.NONE, margin_top=8,
                           margin_bottom=8, margin_start=8, margin_end=8)
        state = {"answered": False}

        def pick(path):
            state["answered"] = True
            d.close()
            m.set_shape_sticker(shape, str(path))
            self.redraw()
        for f in files:
            b = Gtk.Button(child=Gtk.Picture.new_for_filename(str(f)), tooltip_text=Path(f).stem)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_a, f=f: pick(f))
            grid.append(b)

        def closed(*_):
            self.pending -= 1
            if not state["answered"]:
                m.set_shape_sticker(shape, None)
                self.redraw()
        d.connect("closed", closed)
        tv = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=grid))
        tv.add_top_bar(Adw.HeaderBar())
        d.set_child(tv)
        self.pending += 1
        d.present(self.parent())
        return PENDING

    def run_toolbar_dialog(self, item, after: Callable[[], None]) -> None:
        name = item.dialog
        vals = dict(item.value or {}) if isinstance(item.value, dict) else {}
        if name == "open_file":
            from .gtkutil import IMAGE_PATTERNS, open_files

            def done(paths):
                if paths:
                    key = item.params[0] if item.params else "path"
                    item.activate(**{key: paths[0]})
                    self.redraw()
                    after()
            open_files(self.parent(), item.label.rstrip("."), done, patterns=IMAGE_PATTERNS, multiple=False)
            return
        fields = [p for p in item.params]
        d = Adw.AlertDialog(heading=item.label.rstrip("."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        widgets = {}
        for p in fields:
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Label(label=p.replace("_", " ").capitalize(), xalign=0, hexpand=True))
            v = vals.get(p)
            if p in ("color",):
                from .forms import rgba_from_hex
                w = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True))
                w.set_rgba(rgba_from_hex(v or "#00000000"))
            elif p in ("transparent",):
                w = Gtk.CheckButton(active=bool(v))
            elif p in ("interpolation", "method"):
                opts = ["NearestNeighbor", "Bilinear", "Bicubic", "HighQualityBicubic", "HighQualityBilinear"] \
                    if p == "interpolation" else ["Center", "CanvasExpandDown", "CanvasExpandRight"]
                w = Gtk.DropDown.new_from_strings(opts)
                if v in opts:
                    w.set_selected(opts.index(v))
                w._opts = opts  # type: ignore[attr-defined]
            else:
                w = Gtk.SpinButton.new_with_range(-100000, 100000, 1)
                w.set_value(float(v or 0))
            widgets[p] = w
            row.append(w)
            box.append(row)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "OK")
        d.set_default_response("ok")

        def resp(_d, r):
            if r != "ok":
                return
            from .forms import hex_from_rgba
            out = {}
            for p, w in widgets.items():
                if isinstance(w, Gtk.ColorDialogButton):
                    out[p] = hex_from_rgba(w.get_rgba())
                elif isinstance(w, Gtk.CheckButton):
                    out[p] = w.get_active()
                elif isinstance(w, Gtk.DropDown):
                    out[p] = w._opts[w.get_selected()]
                else:
                    out[p] = int(w.get_value())
            item.activate(**out)
            self.redraw()
            after()
        d.connect("response", resp)
        d.present(self.parent())
