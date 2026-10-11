"""Network monitor logic: read ``/proc/net/dev`` counters, turn successive samples into per-interface
receive/transmit rates and keep a rolling history for a live graph.

API
---
``InterfaceCounters``                 one interface's cumulative counters (bytes/packets/errors/drops, rx and tx).
``parse_proc_net_dev(text) -> dict[str, InterfaceCounters]``  parse the file's contents.
``read_counters(path="/proc/net/dev")``                         read + parse.
``Rate``                              bytes/s and packets/s for rx and tx between two samples.
``compute_rates(prev, cur, seconds) -> dict[str, Rate]``        rates for interfaces present in both samples;
                                      a counter that went backwards (wrap/reset) gives a rate of 0.
``NetworkMonitor(history=120, include_loopback=False, reader=read_counters, clock=time.monotonic)``
    ``sample() -> dict[str, Rate]``   take a sample now; the first call returns ``{}`` (no previous sample).
    ``feed(counters, timestamp)``     same, with externally supplied data (used by tests / replay).
    ``history(name) -> list[Rate]``   oldest→newest rates for one interface (at most ``history`` entries).
    ``total_history() -> list[Rate]`` the sum over all monitored interfaces per sample.
    ``interfaces``                    names seen in the latest sample (sorted).
    ``totals(name)``                  the latest cumulative counters of an interface.
``format_rate(bytes_per_second, bits=False)``  "1.50 MB/s" / "12.0 Mbit/s" text for labels.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["InterfaceCounters", "NetworkMonitor", "Rate", "compute_rates", "format_rate", "parse_proc_net_dev",
           "read_counters"]

_RX_FIELDS = ("rx_bytes", "rx_packets", "rx_errs", "rx_drop", "rx_fifo", "rx_frame", "rx_compressed", "rx_multicast")
_TX_FIELDS = ("tx_bytes", "tx_packets", "tx_errs", "tx_drop", "tx_fifo", "tx_colls", "tx_carrier", "tx_compressed")


@dataclass(frozen=True)
class InterfaceCounters:
    name: str
    rx_bytes: int = 0
    rx_packets: int = 0
    rx_errs: int = 0
    rx_drop: int = 0
    rx_fifo: int = 0
    rx_frame: int = 0
    rx_compressed: int = 0
    rx_multicast: int = 0
    tx_bytes: int = 0
    tx_packets: int = 0
    tx_errs: int = 0
    tx_drop: int = 0
    tx_fifo: int = 0
    tx_colls: int = 0
    tx_carrier: int = 0
    tx_compressed: int = 0

    @property
    def is_loopback(self) -> bool:
        return self.name == "lo"


@dataclass(frozen=True)
class Rate:
    rx_bytes_per_s: float = 0.0
    tx_bytes_per_s: float = 0.0
    rx_packets_per_s: float = 0.0
    tx_packets_per_s: float = 0.0
    timestamp: float = 0.0

    @property
    def total_bytes_per_s(self) -> float:
        return self.rx_bytes_per_s + self.tx_bytes_per_s

    def __add__(self, other: Rate) -> Rate:
        return Rate(self.rx_bytes_per_s + other.rx_bytes_per_s, self.tx_bytes_per_s + other.tx_bytes_per_s,
                    self.rx_packets_per_s + other.rx_packets_per_s, self.tx_packets_per_s + other.tx_packets_per_s,
                    max(self.timestamp, other.timestamp))


def parse_proc_net_dev(text: str) -> dict[str, InterfaceCounters]:
    """Parse ``/proc/net/dev``. Header lines (those without a ``:``) are skipped; ``eth0:123`` (no space after the
    colon, which the kernel emits for large counters) is handled by splitting on the colon first."""
    out: dict[str, InterfaceCounters] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or "|" in rest:
            continue
        fields = rest.split()
        try:
            nums = [int(f) for f in fields]
        except ValueError:
            continue
        if len(nums) < 16:
            nums += [0] * (16 - len(nums))
        kw = dict(zip(_RX_FIELDS + _TX_FIELDS, nums[:16], strict=True))
        out[name] = InterfaceCounters(name, **kw)
    return out


def read_counters(path: str = "/proc/net/dev") -> dict[str, InterfaceCounters]:
    with open(path, encoding="ascii", errors="replace") as f:
        return parse_proc_net_dev(f.read())


def _delta(a: int, b: int) -> int:
    return b - a if b >= a else 0


def compute_rates(prev: dict[str, InterfaceCounters], cur: dict[str, InterfaceCounters], seconds: float,
                  timestamp: float = 0.0) -> dict[str, Rate]:
    if seconds <= 0:
        raise ValueError("the sampling interval must be positive")
    rates: dict[str, Rate] = {}
    for name, c in cur.items():
        p = prev.get(name)
        if p is None:
            continue
        rates[name] = Rate(_delta(p.rx_bytes, c.rx_bytes) / seconds, _delta(p.tx_bytes, c.tx_bytes) / seconds,
                           _delta(p.rx_packets, c.rx_packets) / seconds, _delta(p.tx_packets, c.tx_packets) / seconds,
                           timestamp)
    return rates


class NetworkMonitor:
    def __init__(self, history: int = 120, include_loopback: bool = False,
                 reader: Callable[[], dict[str, InterfaceCounters]] = read_counters,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if history < 1:
            raise ValueError("history must be >= 1")
        self.max_history = history
        self.include_loopback = include_loopback
        self._reader = reader
        self._clock = clock
        self._prev: dict[str, InterfaceCounters] | None = None
        self._prev_time = 0.0
        self._hist: dict[str, deque[Rate]] = {}
        self._total: deque[Rate] = deque(maxlen=history)
        self._latest: dict[str, InterfaceCounters] = {}

    def sample(self) -> dict[str, Rate]:
        return self.feed(self._reader(), self._clock())

    def feed(self, counters: dict[str, InterfaceCounters], timestamp: float) -> dict[str, Rate]:
        if not self.include_loopback:
            counters = {k: v for k, v in counters.items() if not v.is_loopback}
        self._latest = counters
        rates: dict[str, Rate] = {}
        if self._prev is not None and timestamp > self._prev_time:
            rates = compute_rates(self._prev, counters, timestamp - self._prev_time, timestamp)
            total = Rate(timestamp=timestamp)
            for name, r in rates.items():
                self._hist.setdefault(name, deque(maxlen=self.max_history)).append(r)
                total = total + r
            self._total.append(total)
            for gone in set(self._hist) - set(counters):
                del self._hist[gone]
        self._prev = counters
        self._prev_time = timestamp
        return rates

    @property
    def interfaces(self) -> list[str]:
        return sorted(self._latest)

    def totals(self, name: str) -> InterfaceCounters | None:
        return self._latest.get(name)

    def history(self, name: str) -> list[Rate]:
        return list(self._hist.get(name, ()))

    def total_history(self) -> list[Rate]:
        return list(self._total)


def format_rate(bytes_per_second: float, bits: bool = False) -> str:
    if bits:
        v = bytes_per_second * 8
        units = ("bit/s", "kbit/s", "Mbit/s", "Gbit/s", "Tbit/s")
    else:
        v = bytes_per_second
        units = ("B/s", "KB/s", "MB/s", "GB/s", "TB/s")
    i = 0
    while v >= 1000 and i < len(units) - 1:
        v /= 1000
        i += 1
    return f"{v:.0f} {units[i]}" if i == 0 else f"{v:.2f} {units[i]}"
