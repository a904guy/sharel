"""/proc/net/dev parsing, rates and rolling history."""
# ruff: noqa: E501  (verbatim /proc/net/dev fixtures are wider than the line limit)
from __future__ import annotations

import pytest

from sharel.tools.network import NetworkMonitor, compute_rates, format_rate, parse_proc_net_dev, read_counters

FIXTURE_1 = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  123456     100    0    0    0     0          0         0   123456     100    0    0    0     0       0          0
  eth0:1000000000 2000    1    2    0     0          0         5 50000000    1000    0    3    0     0       0          0
wlp2s0:     500       5    0    0    0     0          0         0      700       7    0    0    0     0       0          0
"""

FIXTURE_2 = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  123556     101    0    0    0     0          0         0   123556     101    0    0    0     0       0          0
  eth0:1002000000 2200    1    2    0     0          0         5 50500000    1100    0    3    0     0       0          0
wlp2s0:     100       1    0    0    0     0          0         0      900       9    0    0    0     0       0          0
"""


def test_parse_fixture():
    c = parse_proc_net_dev(FIXTURE_1)
    assert set(c) == {"lo", "eth0", "wlp2s0"}
    e = c["eth0"]
    assert (e.rx_bytes, e.rx_packets, e.rx_errs, e.rx_drop, e.rx_multicast) == (1000000000, 2000, 1, 2, 5)
    assert (e.tx_bytes, e.tx_packets, e.tx_drop) == (50000000, 1000, 3)
    assert c["lo"].is_loopback and not e.is_loopback


def test_rates_between_samples():
    a, b = parse_proc_net_dev(FIXTURE_1), parse_proc_net_dev(FIXTURE_2)
    r = compute_rates(a, b, 2.0)
    assert r["eth0"].rx_bytes_per_s == 1_000_000
    assert r["eth0"].tx_bytes_per_s == 250_000
    assert r["eth0"].rx_packets_per_s == 100
    assert r["eth0"].total_bytes_per_s == 1_250_000
    # counter reset (wlp2s0 rx went backwards) -> 0, not negative
    assert r["wlp2s0"].rx_bytes_per_s == 0
    assert r["wlp2s0"].tx_bytes_per_s == 100
    with pytest.raises(ValueError):
        compute_rates(a, b, 0)


def test_monitor_history_rolls_and_skips_loopback():
    samples = []
    base = parse_proc_net_dev(FIXTURE_1)
    for i in range(6):
        samples.append({k: type(v)(**{**v.__dict__, "rx_bytes": v.rx_bytes + i * 1000}) for k, v in base.items()})
    mon = NetworkMonitor(history=3)
    assert mon.feed(samples[0], 10.0) == {}
    for i in range(1, 6):
        rates = mon.feed(samples[i], 10.0 + i)
        assert rates["eth0"].rx_bytes_per_s == 1000
    assert "lo" not in mon.interfaces
    assert mon.interfaces == ["eth0", "wlp2s0"]
    hist = mon.history("eth0")
    assert len(hist) == 3
    assert [h.timestamp for h in hist] == [13.0, 14.0, 15.0]
    tot = mon.total_history()
    assert len(tot) == 3 and tot[-1].rx_bytes_per_s == 2000  # eth0 + wlp2s0
    assert mon.totals("eth0").rx_bytes == base["eth0"].rx_bytes + 5000


def test_monitor_with_real_proc_file():
    clock = iter([1.0, 2.0])
    mon = NetworkMonitor(include_loopback=True, clock=lambda: next(clock))
    mon.sample()
    rates = mon.sample()
    assert "lo" in read_counters()
    assert set(rates) == set(read_counters())
    assert all(r.rx_bytes_per_s >= 0 for r in rates.values())


def test_format_rate():
    assert format_rate(512) == "512 B/s"
    assert format_rate(1_500_000) == "1.50 MB/s"
    assert format_rate(1_500_000, bits=True) == "12.00 Mbit/s"
