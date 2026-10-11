"""Colour picker window (ShareX ColorPickerForm, opened in its "screen colour picker" mode by the ColorPicker job).

Layout follows ShareX: the colour box + slider (``ColorPicker`` control, with the six draw styles Hue,
Saturation, Brightness, Red, Green, Blue picked by the radio buttons next to the fields), the new/old preview
(click the old half to go back to it), HSB / RGBA / CMYK spin fields, Hex and Decimal entries and the colour
name, all kept in sync; the palette of recent or standard colours; "Pick colour from screen" (the region overlay
in ScreenColorPicker mode, fills the X/Y cursor position), "Pick colour from clipboard", and the Copy menu
(All, RGB, Hexadecimal, CMYK, HSB, Decimal, Position).

Recent colours live in ``app.store.app.recent_colors`` as ``#AARRGGBB`` strings, most recent first, at most
``RECENT_MAX`` (ShareX's HelpersOptions.RecentColorsMax); a colour is added when it is copied or picked from the
screen, and when the window closes with a colour different from the one it opened with.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import numpy as np
from gi.repository import Gio, GLib, Gtk
from PIL import Image

from ...core import clipboard
from ...core.enums import RegionCaptureMode
from ...tools import color as colormod
from ...tools.color import Color
from ..gtkutil import cairo_surface_from_pil
from ._base import ToolWindow, button, single, spin

RECENT_MAX = 32
BOX = 256  # colour box size (px), like ShareX's ColorBox
SLIDER_W = 24
STYLES = ("Hue", "Saturation", "Brightness", "Red", "Green", "Blue")

# ShareX ColorHelpers.StandardColors (the palette shown when "Standard colors" is selected)
STANDARD_COLORS = [
    "#000000", "#404040", "#FF0000", "#FF6A00", "#FFD800", "#B6FF00", "#4CFF00", "#00FF21", "#00FF90", "#00FFFF",
    "#0094FF", "#0026FF", "#4800FF", "#B200FF", "#FF00DC", "#FF006E",
    "#FFFFFF", "#808080", "#7F0000", "#7F3300", "#7F6A00", "#5B7F00", "#267F00", "#007F0E", "#007F46", "#007F7F",
    "#004A7F", "#00137F", "#21007F", "#57007F", "#7F006E", "#7F0037",
    "#A0A0A0", "#303030", "#FF7F7F", "#FFB27F", "#FFE97F", "#DAFF7F", "#A5FF7F", "#7FFF8E", "#7FFFC5", "#7FFFFF",
    "#7FC9FF", "#7F92FF", "#A17FFF", "#D67FFF", "#FF7FED", "#FF7FB6",
]


# ---- colour box / slider rendering --------------------------------------------------------------------


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorised HSV (h 0..360, s/v 0..1) to uint8 RGB."""
    h = (h % 360) / 60.0
    i = np.floor(h).astype(int) % 6
    f = h - np.floor(h)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return (np.stack([r, g, b], -1) * 255 + 0.5).clip(0, 255).astype(np.uint8)


def box_axes(style: str) -> tuple[str, str]:
    """The (x, y) quantities of the colour box for a draw style (y is drawn bottom → top)."""
    return {"Hue": ("s", "v"), "Saturation": ("h", "v"), "Brightness": ("h", "s"),
            "Red": ("b", "g"), "Green": ("b", "r"), "Blue": ("r", "g")}[style]


def render_box(style: str, c: Color, size: int = BOX) -> Image.Image:
    xs = np.linspace(0.0, 1.0, size)[None, :].repeat(size, 0)
    ys = np.linspace(1.0, 0.0, size)[:, None].repeat(size, 1)
    hsb = c.to_hsb()
    if style in ("Hue", "Saturation", "Brightness"):
        h = np.full((size, size), hsb.hue)
        s = np.full((size, size), hsb.saturation / 100)
        v = np.full((size, size), hsb.brightness / 100)
        ax, ay = box_axes(style)
        vals = {"h": h, "s": s, "v": v}
        vals[ax] = xs * 360 if ax == "h" else xs
        vals[ay] = ys * 360 if ay == "h" else ys
        arr = _hsv_to_rgb(vals["h"], vals["s"], vals["v"])
    else:
        ch = {"r": np.full((size, size), c.r / 255), "g": np.full((size, size), c.g / 255),
              "b": np.full((size, size), c.b / 255)}
        ax, ay = box_axes(style)
        ch[ax], ch[ay] = xs, ys
        arr = (np.stack([ch["r"], ch["g"], ch["b"]], -1) * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def render_slider(style: str, c: Color, height: int = BOX, width: int = SLIDER_W) -> Image.Image:
    t = np.linspace(1.0, 0.0, height)[:, None].repeat(width, 1)
    hsb = c.to_hsb()
    if style == "Hue":
        arr = _hsv_to_rgb(t * 360, np.ones_like(t), np.ones_like(t))
    elif style == "Saturation":
        arr = _hsv_to_rgb(np.full_like(t, hsb.hue), t, np.full_like(t, hsb.brightness / 100))
    elif style == "Brightness":
        arr = _hsv_to_rgb(np.full_like(t, hsb.hue), np.full_like(t, hsb.saturation / 100), t)
    else:
        ch = {"r": np.full_like(t, c.r / 255), "g": np.full_like(t, c.g / 255), "b": np.full_like(t, c.b / 255)}
        ch[style[0].lower()] = t
        arr = (np.stack([ch["r"], ch["g"], ch["b"]], -1) * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def slider_value(style: str, c: Color) -> float:
    """Slider position 0..1 (1 = top) of a colour."""
    hsb = c.to_hsb()
    return {"Hue": hsb.hue / 360, "Saturation": hsb.saturation / 100, "Brightness": hsb.brightness / 100,
            "Red": c.r / 255, "Green": c.g / 255, "Blue": c.b / 255}[style]


def box_point(style: str, c: Color) -> tuple[float, float]:
    """Box marker position (x 0..1, y 0..1 with 1 = top)."""
    hsb = c.to_hsb()
    q = {"h": hsb.hue / 360, "s": hsb.saturation / 100, "v": hsb.brightness / 100,
         "r": c.r / 255, "g": c.g / 255, "b": c.b / 255}
    ax, ay = box_axes(style)
    return q[ax], q[ay]


def color_from_box(style: str, c: Color, x: float, y: float, hue_hint: float | None = None) -> Color:
    """New colour after clicking box position (x, y) in 0..1 (y = 1 at the top)."""
    x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
    hsb = c.to_hsb()
    h = hsb.hue if hue_hint is None else hue_hint
    if style == "Hue":
        return Color.from_hsb(h, x * 100, y * 100, c.a)
    if style == "Saturation":
        return Color.from_hsb(x * 360, hsb.saturation, y * 100, c.a)
    if style == "Brightness":
        return Color.from_hsb(x * 360, y * 100, hsb.brightness, c.a)
    ch = {"r": c.r, "g": c.g, "b": c.b}
    ax, ay = box_axes(style)
    ch[ax], ch[ay] = round(x * 255), round(y * 255)
    return Color(ch["r"], ch["g"], ch["b"], c.a)


def color_from_slider(style: str, c: Color, t: float, hsb_hint=None) -> Color:
    t = min(1.0, max(0.0, t))
    hsb = hsb_hint or c.to_hsb()
    if style == "Hue":
        return Color.from_hsb(t * 360 if t < 1 else 0, hsb.saturation, hsb.brightness, c.a)
    if style == "Saturation":
        return Color.from_hsb(hsb.hue, t * 100, hsb.brightness, c.a)
    if style == "Brightness":
        return Color.from_hsb(hsb.hue, hsb.saturation, t * 100, c.a)
    ch = {"r": c.r, "g": c.g, "b": c.b}
    ch[style[0].lower()] = round(t * 255)
    return Color(ch["r"], ch["g"], ch["b"], c.a)


def add_recent(app, c: Color) -> None:
    """ShareX AddRecentColor: move/insert at the front, keep at most RECENT_MAX; persisted."""
    text = c.to_hex("ARGB")
    lst = [x for x in app.store.app.recent_colors if x.upper() != text.upper()]
    lst.insert(0, text)
    app.store.app.recent_colors[:] = lst[:RECENT_MAX]
    app.store.save_app()


def _checker(cr, w, h, cell=6):
    for yy in range(0, h, cell):
        for xx in range(0, w, cell):
            g = 0.8 if ((xx // cell) + (yy // cell)) % 2 == 0 else 1.0
            cr.set_source_rgb(g, g, g)
            cr.rectangle(xx, yy, cell, cell)
            cr.fill()


class ColorPickerWindow(ToolWindow):
    def __init__(self, app, ctx=None, initial: Color | None = None) -> None:
        super().__init__(app, "Color picker", 760, 520)
        self.ctx = ctx
        self.ts = ctx.task_settings if ctx is not None else app.store.task_settings_for(None)
        self.style = "Hue"
        self._changing = False
        self._hsb = None  # remembered HSB so hue survives grey/black colours while editing
        start = initial or self._first_color()
        self.color = start
        self.old_color = start
        self.opened_with = start
        self.position: tuple[int, int] | None = None

        root = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)

        # -- box + slider --
        self.box_area = Gtk.DrawingArea(content_width=BOX, content_height=BOX, tooltip_text="Colour box")
        self.box_area.set_draw_func(self._draw_box)
        self.slider_area = Gtk.DrawingArea(content_width=SLIDER_W + 8, content_height=BOX, tooltip_text="Slider")
        self.slider_area.set_draw_func(self._draw_slider)
        for area, handler in ((self.box_area, self._box_at), (self.slider_area, self._slider_at)):
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", lambda g, x, y, h=handler: h(x, y))
            drag.connect("drag-update", lambda g, dx, dy, h=handler: self._drag_update(g, dx, dy, h))
            area.add_controller(drag)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pick_row = Gtk.Box(spacing=8)
        pick_row.append(self.box_area)
        pick_row.append(self.slider_area)
        left.append(pick_row)

        # palette
        self.palette_recent = Gtk.CheckButton(label="Recent colors")
        self.palette_standard = Gtk.CheckButton(label="Standard colors", group=self.palette_recent)
        self.palette_recent.set_active(bool(app.store.app.recent_colors))
        self.palette_standard.set_active(not app.store.app.recent_colors)
        self.palette_recent.connect("toggled", lambda *_: self._fill_palette())
        prow = Gtk.Box(spacing=8)
        prow.append(self.palette_recent)
        prow.append(self.palette_standard)
        left.append(prow)
        self.palette = Gtk.FlowBox(max_children_per_line=16, min_children_per_line=16,
                                   selection_mode=Gtk.SelectionMode.NONE, row_spacing=2, column_spacing=2)
        left.append(self.palette)
        root.append(left)

        # -- fields --
        grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        self.radios: dict[str, Gtk.CheckButton] = {}
        self.spins: dict[str, Gtk.SpinButton] = {}
        group = None
        specs = [("Hue", "Hue", 0, 360, "°"), ("Saturation", "Saturation", 0, 100, "%"),
                 ("Brightness", "Brightness", 0, 100, "%"), ("Red", "Red", 0, 255, ""),
                 ("Green", "Green", 0, 255, ""), ("Blue", "Blue", 0, 255, ""), ("Alpha", "Alpha", 0, 255, ""),
                 ("Cyan", "Cyan", 0, 100, "%"), ("Magenta", "Magenta", 0, 100, "%"),
                 ("Yellow", "Yellow", 0, 100, "%"), ("Key", "Key", 0, 100, "%")]
        for row, (key, label, lo, hi, unit) in enumerate(specs):
            if key in STYLES:
                rb = Gtk.CheckButton(label=label + ":", group=group)
                group = group or rb
                rb.connect("toggled", lambda b, k=key: b.get_active() and self.set_style(k))
                self.radios[key] = rb
                grid.attach(rb, 0, row, 1, 1)
            else:
                grid.attach(Gtk.Label(label=label + ":", xalign=0), 0, row, 1, 1)
            digits = 1 if key in ("Cyan", "Magenta", "Yellow", "Key") else 0
            sp = spin(0, lo, hi, 1, digits, tooltip=label)
            sp.connect("value-changed", lambda *_a, k=key: self._spin_changed(k))
            self.spins[key] = sp
            grid.attach(sp, 1, row, 1, 1)
            grid.attach(Gtk.Label(label=unit, xalign=0), 2, row, 1, 1)
        self.radios["Hue"].set_active(True)
        r = len(specs)
        self.transparent = Gtk.CheckButton(label="Transparent", tooltip_text="Toggle alpha between 0 and 255")
        self.transparent.connect("toggled", lambda b: not self._changing and self.spins["Alpha"].set_value(
            0 if b.get_active() else 255))
        grid.attach(self.transparent, 1, r, 2, 1)
        r += 1
        grid.attach(Gtk.Label(label="Hex:", xalign=0), 0, r, 1, 1)
        self.hex_entry = Gtk.Entry(width_chars=10, tooltip_text="Hex")
        self.hex_entry.connect("changed", lambda *_: self._hex_changed())
        grid.attach(self.hex_entry, 1, r, 2, 1)
        r += 1
        grid.attach(Gtk.Label(label="Decimal:", xalign=0), 0, r, 1, 1)
        self.dec_entry = Gtk.Entry(width_chars=10, tooltip_text="Decimal")
        self.dec_entry.connect("changed", lambda *_: self._dec_changed())
        grid.attach(self.dec_entry, 1, r, 2, 1)
        r += 1
        grid.attach(Gtk.Label(label="Name:", xalign=0), 0, r, 1, 1)
        self.name_label = Gtk.Label(xalign=0, selectable=True)
        grid.attach(self.name_label, 1, r, 2, 1)
        root.append(grid)

        # -- preview + actions --
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.preview = Gtk.DrawingArea(content_width=96, content_height=96,
                                       tooltip_text="New colour (top) / old colour (bottom, click to restore)")
        self.preview.set_draw_func(self._draw_preview)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, _n, _x, y: y > self.preview.get_height() / 2 and self.set_color(
            self.old_color))
        self.preview.add_controller(click)
        right.append(Gtk.Label(label="New / Old", xalign=0))
        right.append(self.preview)
        right.append(button("Pick color from screen", self.pick_from_screen, "Pick a colour from the screen",
                            icon="color-select-symbolic"))
        right.append(button("Pick color from clipboard", self.pick_from_clipboard,
                            "Use the colour written on the clipboard (hex, rgb(), name…)", icon="edit-paste-symbolic"))
        self.clip_status = Gtk.Label(xalign=0, wrap=True)
        self.clip_status.add_css_class("dim-label")
        right.append(self.clip_status)
        pos = Gtk.Box(spacing=4)
        pos.append(Gtk.Label(label="X:"))
        self.x_entry = Gtk.Entry(width_chars=5, editable=False, tooltip_text="Cursor X")
        pos.append(self.x_entry)
        pos.append(Gtk.Label(label="Y:"))
        self.y_entry = Gtk.Entry(width_chars=5, editable=False, tooltip_text="Cursor Y")
        pos.append(self.y_entry)
        right.append(pos)
        right.append(self._copy_menu())
        spacer = Gtk.Box(vexpand=True)
        right.append(spacer)
        right.append(button("Close", self.close, "Close the colour picker"))
        root.append(right)

        self.set_body(root)
        self._fill_palette()
        self._update_controls(None)

    def _first_color(self) -> Color:
        for text in self.app.store.app.recent_colors:
            c = colormod.parse(text)
            if c is not None:
                return c
        return Color(255, 0, 0)

    # -- copy --

    COPY_ITEMS = (("All", "All"), ("RGB", "RGB"), ("Hexadecimal", "Hex"), ("CMYK", "CMYK"), ("HSB", "HSB"),
                  ("Decimal", "Decimal"), ("Position", "Position"))

    def _copy_menu(self) -> Gtk.MenuButton:
        group = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        for label, fmt in self.COPY_ITEMS:
            a = Gio.SimpleAction.new(f"copy-{fmt.lower()}", None)
            a.connect("activate", lambda *_a, f=fmt: self.copy(f))
            group.add_action(a)
            menu.append(label, f"cp.copy-{fmt.lower()}")
        self.insert_action_group("cp", group)
        mb = Gtk.MenuButton(label="Copy", menu_model=menu, tooltip_text="Copy the colour in a format")
        return mb

    def copy_text(self, fmt: str) -> str:
        c = self.color
        if fmt == "Position":
            x, y = self.position or (0, 0)
            return f"{x}, {y}"
        if fmt == "All":
            x, y = self.position or (0, 0)
            return colormod.format_color(c, "All") + f"\nCursor position (X, Y) = {x}, {y}"
        return colormod.format_color(c, fmt)

    def copy(self, fmt: str) -> str:
        text = self.copy_text(fmt)
        clipboard.set_text(text)
        if fmt != "Position":
            add_recent(self.app, self.color)
            if self.palette_recent.get_active():
                self._fill_palette()
        self.toast(f"Copied: {text.splitlines()[0]}")
        return text

    # -- colour state --

    def set_style(self, style: str) -> None:
        self.style = style
        self.box_area.queue_draw()
        self.slider_area.queue_draw()

    def set_color(self, c: Color, source: str | None = None, keep_hsb=None) -> None:
        self.color = c
        self._hsb = keep_hsb
        self._update_controls(source)

    def _update_controls(self, source: str | None) -> None:
        c = self.color
        self._changing = True
        try:
            hsb = self._hsb or c.to_hsb()
            if source != "HSB":
                self.spins["Hue"].set_value(round(hsb.hue))
                self.spins["Saturation"].set_value(round(hsb.saturation))
                self.spins["Brightness"].set_value(round(hsb.brightness))
            if source != "RGBA":
                self.spins["Red"].set_value(c.r)
                self.spins["Green"].set_value(c.g)
                self.spins["Blue"].set_value(c.b)
                self.spins["Alpha"].set_value(c.a)
            if source != "CMYK":
                cm = c.to_cmyk()
                self.spins["Cyan"].set_value(round(cm.cyan, 1))
                self.spins["Magenta"].set_value(round(cm.magenta, 1))
                self.spins["Yellow"].set_value(round(cm.yellow, 1))
                self.spins["Key"].set_value(round(cm.key, 1))
            if source != "Hex":
                self.hex_entry.set_text(self.hex_text(c))
            if source != "Decimal":
                self.dec_entry.set_text(str(c.to_decimal()))
            self.transparent.set_active(c.a == 0)
            self.name_label.set_label(c.name() or "-")
        finally:
            self._changing = False
        self.box_area.queue_draw()
        self.slider_area.queue_draw()
        self.preview.queue_draw()

    @staticmethod
    def hex_text(c: Color) -> str:
        """ShareX txtHex: RRGGBB, or RRGGBBAA when the colour isn't opaque."""
        return c.to_hex("RGB" if c.a == 255 else "RGBA", prefix="")

    def _spin_changed(self, key: str) -> None:
        if self._changing:
            return
        v = {k: s.get_value() for k, s in self.spins.items()}
        a = int(v["Alpha"])
        if key in ("Hue", "Saturation", "Brightness"):
            from ...tools.color import HSB
            hsb = HSB(v["Hue"], v["Saturation"], v["Brightness"])
            self.set_color(Color.from_hsb(*hsb, a), "HSB", keep_hsb=hsb)
        elif key in ("Red", "Green", "Blue", "Alpha"):
            self.set_color(Color(int(v["Red"]), int(v["Green"]), int(v["Blue"]), a), "RGBA")
        else:
            self.set_color(Color.from_cmyk(v["Cyan"], v["Magenta"], v["Yellow"], v["Key"], a), "CMYK")

    def _hex_changed(self) -> None:
        if self._changing:
            return
        try:
            c = Color.from_hex(self.hex_entry.get_text(), hex8_order="RGBA")
        except ValueError:
            return
        self.set_color(c, "Hex")

    def _dec_changed(self) -> None:
        if self._changing:
            return
        try:
            c = Color.from_decimal(int(self.dec_entry.get_text().strip()))
        except ValueError:
            return
        self.set_color(c.with_alpha(self.color.a), "Decimal")

    # -- pickers --

    def pick_from_screen(self) -> None:
        self.old_color = self.color
        self.set_visible(False)

        def outcome(o):
            self.present()
            c = colormod.parse(o.color) if getattr(o, "color", None) else None
            if c is None:
                return
            if o.point_logical:
                self.position = tuple(o.point_logical)
                self.x_entry.set_text(str(self.position[0]))
                self.y_entry.set_text(str(self.position[1]))
            self.set_color(c)
            add_recent(self.app, c)
            if self.palette_recent.get_active():
                self._fill_palette()

        # ShareX waits 250 ms after hiding so the window is gone from the frozen screenshot
        GLib.timeout_add(250, lambda: (self.app.run_region(RegionCaptureMode.ScreenColorPicker, self.ts, outcome),
                                       False)[1])

    def pick_from_clipboard(self) -> bool:
        cont = clipboard.read()
        text = cont.text.strip() if cont.kind == "text" else ""
        c = colormod.parse(text) if text else None
        if c is None:
            self.clip_status.set_label("The clipboard has no colour.")
            return False
        self.set_color(c)
        self.clip_status.set_label("Clipboard: " + text)
        return True

    # -- palette --

    def _fill_palette(self) -> None:
        while (child := self.palette.get_first_child()) is not None:
            self.palette.remove(child)
        texts = self.app.store.app.recent_colors if self.palette_recent.get_active() else STANDARD_COLORS
        for text in texts[:RECENT_MAX if self.palette_recent.get_active() else len(texts)]:
            c = colormod.parse(text)
            if c is None:
                continue
            area = Gtk.DrawingArea(content_width=16, content_height=16, tooltip_text=c.to_hex("RGBA"))
            area.set_draw_func(lambda _a, cr, w, h, col=c: self._draw_swatch(cr, w, h, col))
            g = Gtk.GestureClick()
            g.connect("pressed", lambda *_a, col=c: self.set_color(col))
            area.add_controller(g)
            self.palette.append(area)

    def palette_colors(self) -> list[str]:
        out = []
        child = self.palette.get_first_child()
        while child is not None:
            out.append(child.get_child().get_tooltip_text())
            child = child.get_next_sibling()
        return out

    # -- drawing --

    def _draw_swatch(self, cr, w, h, c: Color) -> None:
        _checker(cr, w, h, 4)
        cr.set_source_rgba(c.r / 255, c.g / 255, c.b / 255, c.a / 255)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(0.4, 0.4, 0.4)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()

    def _draw_box(self, _a, cr, w, h) -> None:
        surf = cairo_surface_from_pil(render_box(self.style, self.color, BOX))
        cr.save()
        cr.scale(w / BOX, h / BOX)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
        cr.restore()
        x, y = box_point(self.style, self.color)
        if self._hsb is not None and self.style in ("Hue", "Saturation", "Brightness"):
            q = {"h": self._hsb.hue / 360, "s": self._hsb.saturation / 100, "v": self._hsb.brightness / 100}
            ax, ay = box_axes(self.style)
            x, y = q[ax], q[ay]
        px, py = x * (w - 1), (1 - y) * (h - 1)
        cr.set_line_width(2)
        cr.set_source_rgb(1, 1, 1) if self.color.to_hsb().brightness < 60 else cr.set_source_rgb(0, 0, 0)
        cr.arc(px, py, 5, 0, 6.2832)
        cr.stroke()

    def _draw_slider(self, _a, cr, w, h) -> None:
        surf = cairo_surface_from_pil(render_slider(self.style, self.color, BOX, SLIDER_W))
        cr.save()
        cr.translate(4, 0)
        cr.scale((w - 8) / SLIDER_W, h / BOX)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
        cr.restore()
        t = slider_value(self.style, self.color)
        if self._hsb is not None and self.style in ("Hue", "Saturation", "Brightness"):
            t = {"Hue": self._hsb.hue / 360, "Saturation": self._hsb.saturation / 100,
                 "Brightness": self._hsb.brightness / 100}[self.style]
        y = (1 - t) * (h - 1)
        cr.set_source_rgb(0, 0, 0)
        cr.move_to(0, y - 4)
        cr.line_to(4, y)
        cr.line_to(0, y + 4)
        cr.fill()
        cr.move_to(w, y - 4)
        cr.line_to(w - 4, y)
        cr.line_to(w, y + 4)
        cr.fill()

    def _draw_preview(self, _a, cr, w, h) -> None:
        _checker(cr, w, h)
        half = h / 2
        for col, y0 in ((self.color, 0), (self.old_color, half)):
            cr.set_source_rgba(col.r / 255, col.g / 255, col.b / 255, col.a / 255)
            cr.rectangle(0, y0, w, half)
            cr.fill()

    # -- mouse --

    def _drag_update(self, g: Gtk.GestureDrag, dx: float, dy: float, handler) -> None:
        ok, sx, sy = g.get_start_point()
        if ok:
            handler(sx + dx, sy + dy)

    def _box_at(self, x: float, y: float) -> None:
        w, h = max(1, self.box_area.get_width() - 1), max(1, self.box_area.get_height() - 1)
        self.pick_box(x / w, 1 - y / h)

    def _slider_at(self, _x: float, y: float) -> None:
        h = max(1, self.slider_area.get_height() - 1)
        self.pick_slider(1 - y / h)

    def pick_box(self, fx: float, fy: float) -> None:
        """Click the colour box at fraction (fx, fy), fy = 1 at the top."""
        fx, fy = min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))
        from ...tools.color import HSB
        base = self._hsb or self.color.to_hsb()
        if self.style == "Hue":
            hsb = HSB(base.hue, fx * 100, fy * 100)
        elif self.style == "Saturation":
            hsb = HSB(fx * 360, base.saturation, fy * 100)
        elif self.style == "Brightness":
            hsb = HSB(fx * 360, fy * 100, base.brightness)
        else:
            self.set_color(color_from_box(self.style, self.color, fx, fy))
            return
        self.set_color(Color.from_hsb(*hsb, self.color.a), keep_hsb=hsb)

    def pick_slider(self, t: float) -> None:
        t = min(1.0, max(0.0, t))
        from ...tools.color import HSB
        base = self._hsb or self.color.to_hsb()
        if self.style == "Hue":
            hsb = HSB(min(t * 360, 359.999), base.saturation, base.brightness)
        elif self.style == "Saturation":
            hsb = HSB(base.hue, t * 100, base.brightness)
        elif self.style == "Brightness":
            hsb = HSB(base.hue, base.saturation, t * 100)
        else:
            self.set_color(color_from_slider(self.style, self.color, t))
            return
        self.set_color(Color.from_hsb(*hsb, self.color.a), keep_hsb=hsb)

    # -- lifecycle --

    def on_closed(self) -> None:
        if self.color != self.opened_with:
            add_recent(self.app, self.color)


def open(app, ctx) -> ColorPickerWindow:  # noqa: A001 - the tool contract's name
    return single("colorpicker", lambda: ColorPickerWindow(app, ctx))


def open_color_picker(app, color: str | Color | None = None, ctx=None) -> ColorPickerWindow:
    """Open (or re-present) the picker showing ``color`` (any text ``color.parse`` reads)."""
    c = colormod.parse(color) if isinstance(color, str) else color
    win = single("colorpicker", lambda: ColorPickerWindow(app, ctx, c))
    if c is not None:
        win.set_color(c)
    return win


__all__ = ["ColorPickerWindow", "add_recent", "open", "open_color_picker", "render_box", "render_slider"]
