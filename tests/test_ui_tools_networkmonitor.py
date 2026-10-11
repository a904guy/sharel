"""Network monitor window: two samples from fixture /proc/net/dev text give the right rates and totals."""
from toolhelpers import ctx_for
from uihelpers import run_app

from sharel.core.enums import HotkeyType
from sharel.tools.network import parse_proc_net_dev

HEADER = ("Inter-|   Receive                                                |  Transmit\n"
          " face |bytes    packets errs drop fifo frame compressed multicast|"
          "bytes    packets errs drop fifo colls carrier compressed\n")


def dev(eth_rx, eth_tx, wl_rx, wl_tx):
    return HEADER + (
        f"    lo: 5000 50 0 0 0 0 0 0 5000 50 0 0 0 0 0 0\n"
        f"  eth0: {eth_rx} 100 0 0 0 0 0 0 {eth_tx} 80 0 0 0 0 0 0\n"
        f"wlan0: {wl_rx} 10 0 0 0 0 0 0 {wl_tx} 8 0 0 0 0 0 0\n")


def test_two_samples_rates_and_totals():
    texts = [dev(1_000_000, 500_000, 10_000, 2_000), dev(3_000_000, 600_000, 14_000, 2_500)]
    times = [100.0, 102.0]
    state = {"i": 0}

    def reader():
        return parse_proc_net_dev(texts[min(state["i"], 1)])

    def clock():
        return times[min(state["i"], 1)]

    def body(app):
        from sharel.ui.tools import networkmonitor
        win = networkmonitor.open(app, ctx_for(app, HotkeyType.NetworkMonitor), reader=reader, clock=clock,
                                  interval_ms=0)
        yield 0.1
        assert win.samples == 1 and win.history() == []  # first sample has no rate yet
        state["i"] = 1
        rates = win.tick()
        assert win.samples == 2
        assert rates["eth0"].rx_bytes_per_s == 1_000_000 and rates["eth0"].tx_bytes_per_s == 50_000
        assert rates["wlan0"].rx_bytes_per_s == 2_000 and "lo" not in rates
        model = win.iface_dd.get_model()
        assert [model.get_item(i).get_string() for i in range(model.get_n_items())] == \
            ["All interfaces", "eth0", "wlan0"]
        # all interfaces: summed
        assert win.history()[-1].rx_bytes_per_s == 1_002_000
        assert win.labels["rx_now"].get_label() == "1.00 MB/s"
        assert win.labels["tx_now"].get_label() == "50.25 KB/s"
        win.iface_dd.set_selected(2)  # wlan0
        assert win.selected == "wlan0"
        assert win.labels["rx_now"].get_label() == "2.00 KB/s"
        assert win.labels["rx_total"].get_label() == "13.7 KB" and win.labels["tx_packets"].get_label() == "8"
        win.bits_check.set_active(True)
        assert win.labels["rx_now"].get_label() == "16.00 kbit/s"
        win.graph.queue_draw()
        yield 0.2  # graph draws without errors
        win.lo_check.set_active(True)  # restarts the monitor, loopback listed
        assert "lo" in win.monitor.interfaces
        return True
    assert run_app(body)
