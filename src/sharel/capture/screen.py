"""The monitor layout in global logical pixels, from the extension or Mutter's DisplayConfig."""
from __future__ import annotations

from dataclasses import dataclass, field

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from ..shell.client import Monitor, shell

DC = "org.gnome.Mutter.DisplayConfig"
DC_PATH = "/org/gnome/Mutter/DisplayConfig"


@dataclass
class Layout:
    monitors: list[Monitor] = field(default_factory=list)

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        if not self.monitors:
            return (0, 0, 0, 0)
        x0 = min(m.x for m in self.monitors)
        y0 = min(m.y for m in self.monitors)
        x1 = max(m.x + m.width for m in self.monitors)
        y1 = max(m.y + m.height for m in self.monitors)
        return (x0, y0, x1 - x0, y1 - y0)

    @property
    def max_scale(self) -> float:
        return max((m.scale for m in self.monitors), default=1.0)

    @property
    def primary(self) -> Monitor | None:
        return next((m for m in self.monitors if m.primary), self.monitors[0] if self.monitors else None)

    def monitor_at(self, x: float, y: float) -> Monitor | None:
        for m in self.monitors:
            if m.x <= x < m.x + m.width and m.y <= y < m.y + m.height:
                return m
        return self.primary


def from_display_config() -> Layout:
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = conn.call_sync(DC, DC_PATH, DC, "GetCurrentState", None, None, Gio.DBusCallFlags.NONE, 5000, None)
    _serial, monitors, logical, props = reply.unpack()
    layout_mode = props.get("layout-mode", 1)
    out = []
    for x, y, scale, _transform, primary, mons, _p in sorted(logical, key=lambda lm: (lm[0], lm[1])):
        connector = mons[0][0]
        w = h = 0
        for (conn_name, *_), modes, _mp in monitors:
            if conn_name == connector:
                cur = next((m for m in modes if m[6].get("is-current")), None)
                if cur:
                    w, h = cur[1], cur[2]
        if layout_mode == 1:
            w, h = round(w / scale), round(h / scale)
        out.append(Monitor(len(out), x, y, w, h, scale, primary))
    return Layout(out)


def current_layout() -> Layout:
    sh = shell()
    if sh.available:
        try:
            mons = sh.get_monitors()
            if mons:
                return Layout(mons)
        except Exception:
            pass
    try:
        layout = from_display_config()
        if layout.monitors:
            return layout
    except GLib.Error:
        pass
    return from_gdk()


def from_gdk() -> Layout:
    """Last resort (no shell extension, no Mutter DisplayConfig): GDK's monitors."""
    try:
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
    except (ImportError, ValueError):
        return Layout([])
    if display is None:
        return Layout([])
    out = []
    for i, m in enumerate(display.get_monitors()):
        g = m.get_geometry()
        out.append(Monitor(i, g.x, g.y, g.width, g.height, float(m.get_scale_factor()), i == 0))
    return Layout(out)
