"""Monitor test (ShareX MonitorTestForm + ScreenTearingTestForm).

A fullscreen window on the chosen monitor showing one test at a time: black & white level, red/green/blue
colour (with a colour picker), two-colour gradient in four directions, line/checker patterns with a size,
the animated screen-tearing test (moving bars, ShareX's ScreenTearingTestForm), and ShareL's grid and RGB
gradient bands. Like ShareX, a click shows/hides the settings panel where it was clicked; the mouse wheel,
the arrow keys and Page Up/Down change the test, Space toggles the panel and Esc exits.

Patterns come from :mod:`sharel.tools.monitortest`. The tearing test renders ``motion_frame`` as a one-pixel
strip each frame (the bars are uniform across the other axis) and stretches it, so it runs at the display's
refresh rate; the bar offset comes from the frame clock's elapsed time, so dropped frames don't slow it.
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
import cairo
import numpy as np
from gi.repository import Gdk, Gtk
from PIL import Image

from ...tools import monitortest as mt
from ._base import ToolWindow, button, dropdown, single

MODE_LABELS = {"Grayscale": "Black & white", "Rgb": "Red, green, blue", "Gradient": "Gradient",
               "Pattern": "Pattern", "Motion": "Screen tearing test", "Grid": "Grid",
               "GradientBands": "Gradient bands"}
GRADIENT_LABELS = {"Horizontal": "Horizontal", "Vertical": "Vertical", "ForwardDiagonal": "Forward diagonal",
                   "BackwardDiagonal": "Backward diagonal"}
PATTERN_LABELS = {"HorizontalLines": "Horizontal lines", "VerticalLines": "Vertical lines",
                  "Checkerboard": "Checkerboard"}
MOTION_LABELS = {"VerticalBars": "Vertical bars (moving sideways)", "HorizontalBars": "Horizontal bars (moving down)"}
HINT = "← → or mouse wheel: change test · Click or Space: show/hide this panel · Esc: exit"


def surface_from_pil(img: Image.Image) -> cairo.ImageSurface:
    """An opaque RGB image as a cairo RGB24 surface (the surface owns a copy of the pixels)."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_RGB24, w)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    buf = np.ndarray((h, stride // 4, 4), np.uint8, buffer=surf.get_data())
    buf[:, :w, 0] = rgb[..., 2]
    buf[:, :w, 1] = rgb[..., 1]
    buf[:, :w, 2] = rgb[..., 0]
    buf[:, :w, 3] = 255
    surf.mark_dirty()
    return surf


def _rgb_of(btn: Gtk.ColorDialogButton) -> tuple[int, int, int]:
    c = btn.get_rgba()
    return (round(c.red * 255), round(c.green * 255), round(c.blue * 255))


def _rgba(rgb: tuple[int, int, int]) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1.0
    return c


def _scale(lo: int, hi: int, value: int, tooltip: str) -> Gtk.Scale:
    s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
    s.set_value(value)
    s.set_hexpand(True)
    s.set_draw_value(True)
    s.set_digits(0)
    s.set_size_request(220, -1)
    s.set_tooltip_text(tooltip)
    return s


class MonitorTestWindow(ToolWindow):
    def __init__(self, app, monitor_index: int = -1) -> None:
        super().__init__(app, "Monitor test", 1024, 768)
        self.toolbar.set_reveal_top_bars(False)
        self.settings = mt.MonitorTestSettings()
        self.bar_size = 50
        self._cache: tuple | None = None
        self._tick_id = 0
        self._t0: float | None = None
        self.elapsed = 0.0
        self._building = True

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._clicked)
        self.area.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scrolled)
        self.area.add_controller(scroll)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._key)
        self.add_controller(key)

        self.overlay = Gtk.Overlay(child=self.area)
        self.panel = self._build_panel()
        self.overlay.add_overlay(self.panel)
        self.set_body(self.overlay)
        self._building = False
        self.choose_monitor(monitor_index)
        self._apply_mode()

    # -- settings panel --

    def _build_panel(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.START,
                      valign=Gtk.Align.START, margin_start=40, margin_top=40)
        box.add_css_class("card")
        box.add_css_class("osd")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                        margin_start=12, margin_end=12)
        box.append(inner)
        title = Gtk.Label(label="Monitor test", xalign=0)
        title.add_css_class("title-4")
        inner.append(title)

        self.mode_dd = dropdown([MODE_LABELS[m] for m in mt.MODES], 0, "Test")
        self.mode_dd.connect("notify::selected", lambda *_: self._mode_selected())
        inner.append(self._row("Test:", self.mode_dd))
        self.stack = Gtk.Stack()
        inner.append(self.stack)

        # Grayscale
        self.gray = _scale(0, 255, self.settings.gray_value, "Black & white level")
        self.gray.connect("value-changed", lambda s: self._set("gray_value", int(s.get_value())))
        self.stack.add_named(self._row("Level:", self.gray), "Grayscale")
        # RGB
        rgb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.red = _scale(0, 255, self.settings.red, "Red")
        self.green = _scale(0, 255, self.settings.green, "Green")
        self.blue = _scale(0, 255, self.settings.blue, "Blue")
        for name, s in (("red", self.red), ("green", self.green), ("blue", self.blue)):
            s.connect("value-changed", lambda sc, n=name: self._rgb_changed(n, int(sc.get_value())))
            rgb.append(self._row(name.capitalize() + ":", s))
        self.rgb_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), tooltip_text="Pick colour")
        self.rgb_button.set_rgba(_rgba((self.settings.red, self.settings.green, self.settings.blue)))
        self.rgb_button.connect("notify::rgba", lambda *_: self._rgb_picked())
        rgb.append(self._row("Colour:", self.rgb_button))
        self.stack.add_named(rgb, "Rgb")
        # Gradient
        grad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.grad1 = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), tooltip_text="Gradient colour 1")
        self.grad1.set_rgba(_rgba(self.settings.gradient_start))
        self.grad1.connect("notify::rgba", lambda b, _p: self._set("gradient_start", _rgb_of(b)))
        self.grad2 = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), tooltip_text="Gradient colour 2")
        self.grad2.set_rgba(_rgba(self.settings.gradient_end))
        self.grad2.connect("notify::rgba", lambda b, _p: self._set("gradient_end", _rgb_of(b)))
        self.grad_dir = dropdown([GRADIENT_LABELS[d] for d in mt.GRADIENT_DIRECTIONS],
                                 mt.GRADIENT_DIRECTIONS.index(self.settings.gradient_direction), "Gradient direction")
        self.grad_dir.connect("notify::selected", lambda d, _p: self._set(
            "gradient_direction", mt.GRADIENT_DIRECTIONS[d.get_selected()]))
        grad.append(self._row("From:", self.grad1))
        grad.append(self._row("To:", self.grad2))
        grad.append(self._row("Direction:", self.grad_dir))
        self.stack.add_named(grad, "Gradient")
        # Pattern
        pat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pattern_dd = dropdown([PATTERN_LABELS[p] for p in mt.PATTERNS], mt.PATTERNS.index(self.settings.pattern),
                                   "Pattern")
        self.pattern_dd.connect("notify::selected", lambda d, _p: self._set("pattern", mt.PATTERNS[d.get_selected()]))
        self.pattern_size = _scale(1, 50, self.settings.pattern_size, "Pattern size")
        self.pattern_size.connect("value-changed", lambda s: self._set("pattern_size", int(s.get_value())))
        pat.append(self._row("Pattern:", self.pattern_dd))
        pat.append(self._row("Size:", self.pattern_size))
        self.stack.add_named(pat, "Pattern")
        # Motion (screen tearing test)
        mot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.motion_dir = dropdown([MOTION_LABELS[d] for d in mt.MOTION_DIRECTIONS], 0, "Bar direction")
        self.motion_dir.connect("notify::selected", lambda d, _p: self._set(
            "motion_direction", mt.MOTION_DIRECTIONS[d.get_selected()]))
        self.motion_speed = _scale(100, 2000, int(self.settings.motion_speed), "Speed (pixels per second)")
        self.motion_speed.connect("value-changed", lambda s: self._set("motion_speed", float(s.get_value())))
        self.bar_scale = _scale(5, 400, self.bar_size, "Bar size (pixels)")
        self.bar_scale.connect("value-changed", lambda s: setattr(self, "bar_size", int(s.get_value())))
        mot.append(self._row("Bars:", self.motion_dir))
        mot.append(self._row("Speed (px/s):", self.motion_speed))
        mot.append(self._row("Bar size:", self.bar_scale))
        self.stack.add_named(mot, "Motion")
        # Grid
        self.grid_spacing = _scale(5, 400, self.settings.grid_spacing, "Grid spacing (pixels)")
        self.grid_spacing.connect("value-changed", lambda s: self._set("grid_spacing", int(s.get_value())))
        self.stack.add_named(self._row("Spacing:", self.grid_spacing), "Grid")
        # Gradient bands
        self.bands_dir = dropdown(["Horizontal", "Vertical"], 0, "Band direction")
        self.bands_dir.connect("notify::selected", lambda d, _p: self._set(
            "gradient_direction", ("Horizontal", "Vertical")[d.get_selected()]))
        self.stack.add_named(self._row("Direction:", self.bands_dir), "GradientBands")

        self.monitors = self._monitor_list()
        self.monitor_dd = dropdown([self._monitor_label(i, m) for i, m in enumerate(self.monitors)] or ["Monitor"],
                                   0, "Monitor to test")
        self.monitor_dd.connect("notify::selected", lambda d, _p: self._building or self.choose_monitor(
            d.get_selected()))
        inner.append(self._row("Monitor:", self.monitor_dd))
        hint = Gtk.Label(label=HINT, xalign=0, wrap=True, max_width_chars=40)
        hint.add_css_class("caption")
        inner.append(hint)
        btns = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        btns.append(button("Hide panel", self.toggle_panel, "Hide this panel (click the screen to show it)"))
        btns.append(button("Close", self.close, "Exit the monitor test", css="destructive-action"))
        inner.append(btns)
        return box

    @staticmethod
    def _row(label: str, widget: Gtk.Widget) -> Gtk.Box:
        b = Gtk.Box(spacing=8)
        lab = Gtk.Label(label=label, xalign=0, width_chars=12)
        lab.set_mnemonic_widget(widget)
        b.append(lab)
        widget.set_hexpand(True)
        b.append(widget)
        return b

    # -- monitors --

    @staticmethod
    def _monitor_list() -> list[Gdk.Monitor]:
        display = Gdk.Display.get_default()
        if display is None:
            return []
        model = display.get_monitors()
        return [model.get_item(i) for i in range(model.get_n_items())]

    @staticmethod
    def _monitor_label(i: int, m: Gdk.Monitor) -> str:
        g = m.get_geometry()
        name = m.get_connector() or m.get_model() or ""
        return f"{i + 1}: {g.width}×{g.height}" + (f" ({name})" if name else "")

    def choose_monitor(self, index: int) -> None:
        self.monitors = self._monitor_list()
        if not self.monitors:
            self.fullscreen()
            return
        if not 0 <= index < len(self.monitors):
            index = 0
        self.monitor_index = index
        self.fullscreen_on_monitor(self.monitors[index])

    # -- modes --

    @property
    def mode(self) -> str:
        return self.settings.mode

    def set_mode(self, mode: str) -> None:
        self.mode_dd.set_selected(mt.MODES.index(mode))

    def next_mode(self, step: int = 1) -> str:
        i = (mt.MODES.index(self.settings.mode) + step) % len(mt.MODES)
        self.mode_dd.set_selected(i)
        return self.settings.mode

    def _mode_selected(self) -> None:
        self.settings.mode = mt.MODES[self.mode_dd.get_selected()]
        if not self._building:
            self._apply_mode()

    def _apply_mode(self) -> None:
        self.stack.set_visible_child_name(self.settings.mode)
        if self.settings.mode == "GradientBands":
            self.settings.gradient_direction = ("Horizontal", "Vertical")[self.bands_dir.get_selected()]
        elif self.settings.mode == "Gradient":
            self.settings.gradient_direction = mt.GRADIENT_DIRECTIONS[self.grad_dir.get_selected()]
        if self.settings.mode == "Motion":
            self._start_animation()
        else:
            self._stop_animation()
        self._cache = None
        self.area.queue_draw()

    def _set(self, attr: str, value) -> None:
        setattr(self.settings, attr, value)
        self._cache = None
        self.area.queue_draw()

    def _rgb_changed(self, name: str, value: int) -> None:
        self._set(name, value)
        cur = _rgb_of(self.rgb_button)
        want = (self.settings.red, self.settings.green, self.settings.blue)
        if cur != want:
            self.rgb_button.set_rgba(_rgba(want))

    def _rgb_picked(self) -> None:
        r, g, b = _rgb_of(self.rgb_button)
        for s, v in ((self.red, r), (self.green, g), (self.blue, b)):
            if int(s.get_value()) != v:
                s.set_value(v)

    # -- input --

    def toggle_panel(self, x: float | None = None, y: float | None = None) -> None:
        show = not self.panel.get_visible()
        if show and x is not None and y is not None:
            self.panel.set_margin_start(max(0, int(x)))
            self.panel.set_margin_top(max(0, int(y)))
        self.panel.set_visible(show)

    def _clicked(self, gesture, _n, x, y) -> None:
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            self.next_mode()
        else:
            self.toggle_panel(x, y)

    def _scrolled(self, _c, _dx, dy) -> bool:
        if dy:
            self.next_mode(1 if dy > 0 else -1)
        return True

    def _key(self, _c, keyval, _code, _state) -> bool:
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Page_Down, Gdk.KEY_n):
            self.next_mode(1)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_Page_Up, Gdk.KEY_p):
            self.next_mode(-1)
            return True
        if keyval in (Gdk.KEY_space, Gdk.KEY_F1, Gdk.KEY_Return):
            self.toggle_panel()
            return True
        return False

    # -- animation --

    def _start_animation(self) -> None:
        if self._tick_id:
            return
        self._t0 = None
        self._tick_id = self.area.add_tick_callback(self._tick)

    def _stop_animation(self) -> None:
        if self._tick_id:
            self.area.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _tick(self, _w, clock) -> bool:
        now = clock.get_frame_time() / 1e6 if clock is not None else time.monotonic()
        if self._t0 is None:
            self._t0 = now
        self.elapsed = now - self._t0
        self.area.queue_draw()
        return True

    def on_closed(self) -> None:
        self._stop_animation()

    # -- rendering --

    def motion_strip(self, elapsed: float, width: int, height: int) -> Image.Image:
        """The tearing-test frame at ``elapsed`` seconds as a 1-px strip along the bars' movement."""
        s = self.settings
        vertical = s.motion_direction == "VerticalBars"
        size = (max(1, width), 1) if vertical else (1, max(1, height))
        return mt.motion_frame(size, elapsed * s.fps, s.fps, s.motion_speed, s.motion_direction, self.bar_size)

    def render_image(self, size: tuple[int, int]) -> Image.Image:
        """The current test as a full image (what the window shows)."""
        if self.settings.mode == "Motion":
            strip = self.motion_strip(self.elapsed, *size)
            return strip.resize(size, Image.Resampling.NEAREST)
        return mt.render(self.settings, size)

    def _draw(self, _area, cr, width: int, height: int) -> None:
        if width < 1 or height < 1:
            return
        s = self.settings
        if s.mode == "Grayscale":
            v = s.gray_value / 255
            cr.set_source_rgb(v, v, v)
            cr.paint()
            return
        if s.mode == "Rgb":
            cr.set_source_rgb(s.red / 255, s.green / 255, s.blue / 255)
            cr.paint()
            return
        if s.mode == "Motion":
            strip = self.motion_strip(self.elapsed, width, height)
            surf = surface_from_pil(strip)
            cr.save()
            if s.motion_direction == "VerticalBars":
                cr.scale(1, height)
            else:
                cr.scale(width, 1)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint()
            cr.restore()
            return
        key = (repr(s), width, height)
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, surface_from_pil(mt.render(s, (width, height))))
        cr.set_source_surface(self._cache[1], 0, 0)
        cr.paint()

    def close_on_escape(self) -> bool:
        return True


def open(app, ctx, monitor_index: int = -1) -> MonitorTestWindow:  # noqa: A001 - the tool contract's name
    return single("monitortest", lambda: MonitorTestWindow(app, monitor_index))
