"""Network monitor (ShareL tool; ShareX has no such window): live download/upload rates per interface.

Samples ``/proc/net/dev`` through :class:`sharel.tools.network.NetworkMonitor` every second and draws the
last two minutes as a cairo graph (download and upload lines, auto-scaled with a grid and the peak), with
the current rates, peaks and the interface's cumulative totals. The interface picker offers "All
interfaces" (the sum) or one interface; options show bits/s instead of bytes/s, include loopback and pause.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from ...tools import network as net
from ..gtkutil import human_size
from ._base import ToolWindow, dropdown, single

ALL = "All interfaces"
HISTORY = 120
RX_COLOR = (0.13, 0.55, 0.95)
TX_COLOR = (0.95, 0.45, 0.13)


class NetworkMonitorWindow(ToolWindow):
    def __init__(self, app, reader=None, clock=None, interval_ms: int = 1000) -> None:
        super().__init__(app, "Network monitor", 720, 480)
        self._reader = reader or net.read_counters
        self._clock = clock
        self.include_loopback = False
        self.bits = False
        self.paused = False
        self.selected = ALL
        self.monitor = self._new_monitor()
        self._timer = 0
        self.samples = 0

        top = Gtk.Box(spacing=8)
        top.append(Gtk.Label(label="Interface:"))
        self.iface_dd = dropdown([ALL], 0, "Interface")
        self.iface_dd.connect("notify::selected", lambda *_: self._iface_selected())
        top.append(self.iface_dd)
        top.append(Gtk.Box(hexpand=True))
        self.bits_check = Gtk.CheckButton(label="Bits per second", tooltip_text="Show rates in bit/s")
        self.bits_check.connect("toggled", lambda b: self._set_bits(b.get_active()))
        self.lo_check = Gtk.CheckButton(label="Include loopback", tooltip_text="Also monitor the lo interface")
        self.lo_check.connect("toggled", lambda b: self._set_loopback(b.get_active()))
        self.pause_button = Gtk.ToggleButton(label="Pause", tooltip_text="Stop sampling")
        self.pause_button.connect("toggled", lambda b: setattr(self, "paused", b.get_active()))
        for w in (self.bits_check, self.lo_check, self.pause_button):
            top.append(w)

        self.graph = Gtk.DrawingArea(hexpand=True, vexpand=True, content_height=240)
        self.graph.set_draw_func(self._draw)
        frame = Gtk.Frame(child=self.graph)

        grid = Gtk.Grid(column_spacing=24, row_spacing=4)
        self.labels: dict[str, Gtk.Label] = {}
        rows = (("rx", "Download"), ("tx", "Upload"))
        cols = (("now", "Current"), ("peak", "Peak"), ("avg", "Average"), ("total", "Total"), ("packets", "Packets"))
        for c, (_k, title) in enumerate(cols, start=1):
            h = Gtk.Label(label=title, xalign=0)
            h.add_css_class("heading")
            grid.attach(h, c, 0, 1, 1)
        for r, (rk, title) in enumerate(rows, start=1):
            name = Gtk.Label(label=title, xalign=0)
            color = RX_COLOR if rk == "rx" else TX_COLOR
            name.set_markup(f'<span foreground="#{int(color[0]*255):02x}{int(color[1]*255):02x}'
                            f'{int(color[2]*255):02x}">■</span> {title}')
            grid.attach(name, 0, r, 1, 1)
            for c, (ck, _t) in enumerate(cols, start=1):
                lab = Gtk.Label(label="–", xalign=0, selectable=True)
                lab.add_css_class("numeric")
                grid.attach(lab, c, r, 1, 1)
                self.labels[f"{rk}_{ck}"] = lab

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(top)
        body.append(frame)
        body.append(grid)
        self.set_body(body)

        self.tick()
        if interval_ms > 0:
            self._timer = GLib.timeout_add(interval_ms, self._on_timer)

    def _new_monitor(self) -> net.NetworkMonitor:
        kw = {"history": HISTORY, "include_loopback": self.include_loopback, "reader": self._reader}
        if self._clock is not None:
            kw["clock"] = self._clock
        return net.NetworkMonitor(**kw)

    # -- sampling --

    def _on_timer(self) -> bool:
        if not self.paused:
            self.tick()
        return GLib.SOURCE_CONTINUE

    def tick(self) -> dict[str, net.Rate]:
        """Take one sample, refresh the interface list, labels and graph."""
        try:
            rates = self.monitor.sample()
        except OSError as e:
            self.labels["rx_now"].set_label(str(e))
            return {}
        self.samples += 1
        self._sync_interfaces()
        self._update_labels()
        self.graph.queue_draw()
        return rates

    def _sync_interfaces(self) -> None:
        want = [ALL, *self.monitor.interfaces]
        model = self.iface_dd.get_model()
        have = [model.get_item(i).get_string() for i in range(model.get_n_items())]
        if want == have:
            return
        current = self.selected
        model.splice(0, len(have), want)
        self.iface_dd.set_selected(want.index(current) if current in want else 0)

    def _iface_selected(self) -> None:
        item = self.iface_dd.get_selected_item()
        self.selected = item.get_string() if item is not None else ALL
        self._update_labels()
        self.graph.queue_draw()

    def _set_bits(self, on: bool) -> None:
        self.bits = on
        self._update_labels()
        self.graph.queue_draw()

    def _set_loopback(self, on: bool) -> None:
        self.include_loopback = on
        self.monitor = self._new_monitor()
        self.tick()

    # -- data --

    def history(self) -> list[net.Rate]:
        if self.selected == ALL:
            return self.monitor.total_history()
        return self.monitor.history(self.selected)

    def totals(self) -> tuple[int, int, int, int]:
        """(rx bytes, tx bytes, rx packets, tx packets) since boot for the selection."""
        names = self.monitor.interfaces if self.selected == ALL else [self.selected]
        rx = tx = rxp = txp = 0
        for n in names:
            c = self.monitor.totals(n)
            if c is not None:
                rx, tx, rxp, txp = rx + c.rx_bytes, tx + c.tx_bytes, rxp + c.rx_packets, txp + c.tx_packets
        return rx, tx, rxp, txp

    def fmt(self, v: float) -> str:
        return net.format_rate(v, self.bits)

    def _update_labels(self) -> None:
        hist = self.history()
        rx_vals = [r.rx_bytes_per_s for r in hist]
        tx_vals = [r.tx_bytes_per_s for r in hist]
        for key, vals in (("rx", rx_vals), ("tx", tx_vals)):
            self.labels[f"{key}_now"].set_label(self.fmt(vals[-1]) if vals else "–")
            self.labels[f"{key}_peak"].set_label(self.fmt(max(vals)) if vals else "–")
            self.labels[f"{key}_avg"].set_label(self.fmt(sum(vals) / len(vals)) if vals else "–")
        rx, tx, rxp, txp = self.totals()
        self.labels["rx_total"].set_label(human_size(rx))
        self.labels["tx_total"].set_label(human_size(tx))
        self.labels["rx_packets"].set_label(f"{rxp:,}")
        self.labels["tx_packets"].set_label(f"{txp:,}")

    # -- graph --

    def _draw(self, area, cr, width: int, height: int) -> None:
        fg = area.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.05)
        cr.paint()
        hist = self.history()
        left, right, top, bottom = 70, 8, 8, 18
        gw, gh = max(1, width - left - right), max(1, height - top - bottom)
        peak = max([max(r.rx_bytes_per_s, r.tx_bytes_per_s) for r in hist] + [1.0])
        scale = _nice(peak * 1.1)
        # grid + labels
        cr.set_line_width(1)
        cr.select_font_face("sans")
        cr.set_font_size(10)
        for i in range(5):
            y = top + gh * i / 4
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.15)
            cr.move_to(left, int(y) + 0.5)
            cr.line_to(left + gw, int(y) + 0.5)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            text = self.fmt(scale * (4 - i) / 4)
            ext = cr.text_extents(text)
            cr.move_to(left - 6 - ext.width, y + ext.height / 2)
            cr.show_text(text)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
        cr.move_to(left, height - 4)
        cr.show_text(f"{HISTORY} s")
        ext = cr.text_extents("now")
        cr.move_to(left + gw - ext.width, height - 4)
        cr.show_text("now")
        if len(hist) < 1:
            return
        step = gw / max(1, HISTORY - 1)
        for attr, color in (("rx_bytes_per_s", RX_COLOR), ("tx_bytes_per_s", TX_COLOR)):
            pts = [(left + gw - (len(hist) - 1 - i) * step, top + gh - getattr(r, attr) / scale * gh)
                   for i, r in enumerate(hist)]
            cr.set_source_rgba(*color, 0.18)
            cr.move_to(pts[0][0], top + gh)
            for x, y in pts:
                cr.line_to(x, y)
            cr.line_to(pts[-1][0], top + gh)
            cr.close_path()
            cr.fill()
            cr.set_source_rgb(*color)
            cr.set_line_width(2)
            cr.move_to(*pts[0])
            for x, y in pts[1:]:
                cr.line_to(x, y)
            cr.stroke()

    def on_closed(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0


def _nice(v: float) -> float:
    """Round a graph maximum up to 1, 2 or 5 × 10^n."""
    import math
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def open(app, ctx, *, reader=None, clock=None, interval_ms: int = 1000) -> NetworkMonitorWindow:  # noqa: A001
    return single("networkmonitor", lambda: NetworkMonitorWindow(app, reader, clock, interval_ms))
