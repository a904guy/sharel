"""The unit suite's private session bus must not be able to start real session services."""
import os
import time

import gi
import pytest

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

REAL_SERVICES = ["org.freedesktop.FileManager1", "org.gnome.Nautilus", "org.freedesktop.Notifications",
                 "org.freedesktop.portal.Desktop", "org.freedesktop.secrets", "org.gnome.Shell",
                 "org.gnome.Mutter.ScreenCast", "org.gtk.vfs.Daemon", "ca.desrt.dconf", "org.a11y.Bus"]


def _bus_call(method, args=None, sig=None):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", method,
                         GLib.Variant(sig, args) if sig else None, None, Gio.DBusCallFlags.NONE, 5000,
                         None).unpack()


def test_private_bus_is_not_the_real_one():
    addr = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    # the bus listens inside the sandbox's own runtime dir (sandbox.py: <listen>unix:tmpdir=...)
    assert os.environ["XDG_RUNTIME_DIR"] in addr and "sharel-unit-" in addr, addr
    for real in (os.environ.get("SHAREL_REAL_DBUS_SESSION_BUS_ADDRESS"), f"unix:path=/run/user/{os.getuid()}/bus"):
        if real:
            assert addr.split(",")[0] != real.split(",")[0], (addr, real)


def test_nothing_real_is_activatable():
    (names,) = _bus_call("ListActivatableNames")
    for svc in REAL_SERVICES:
        assert svc not in names, f"{svc} is D-Bus-activatable on the test bus"
    # the only activatable name is the bus itself: the service dir is private and empty
    assert set(names) <= {"org.freedesktop.DBus"}, names


def test_calling_a_real_service_name_fails_fast_without_starting_it():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    t = time.monotonic()
    with pytest.raises(GLib.Error) as err:
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", (["file:///"], "")),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
    assert "ServiceUnknown" in err.value.message or "not provided by any .service" in err.value.message
    assert time.monotonic() - t < 2.0
    (names,) = _bus_call("ListNames")
    assert "org.freedesktop.FileManager1" not in names


def test_show_in_folder_asks_the_file_manager(fake_file_manager, tmp_path, monkeypatch):
    from sharel.core import desktop
    opened = []
    monkeypatch.setattr(desktop, "open_folder", opened.append)
    f = tmp_path / "shot.png"
    f.write_bytes(b"x")
    desktop.show_in_folder(str(f))
    assert [c[1:3] for c in fake_file_manager.calls] == [("ShowItems", ([f.resolve().as_uri()], ""))]
    assert opened == []  # no fallback: the file manager answered


def test_show_in_folder_falls_back_to_opening_the_folder(tmp_path, monkeypatch):
    from sharel.core import desktop
    opened = []
    monkeypatch.setattr(desktop, "open_folder", opened.append)
    f = tmp_path / "shot.png"
    f.write_bytes(b"x")
    desktop.show_in_folder(str(f))  # no file manager on the private bus
    assert opened == [str(tmp_path)]


INNER_CONFTEST = """
import importlib.util
spec = importlib.util.spec_from_file_location("sharel_unit_conftest", {conftest!r})
spec.loader.exec_module(importlib.util.module_from_spec(spec))  # starts a fresh sandbox, as a real run does
"""

INNER_TEST = """
import json, os, time
import sandbox

def test_hold():
    home = os.path.dirname(os.environ["XDG_CONFIG_HOME"])
    with open({out!r}, "w") as fh:
        json.dump({{"pids": [p.pid for p in sandbox._procs], "dirs": [os.environ["XDG_RUNTIME_DIR"], home],
                    "bus": os.environ["DBUS_SESSION_BUS_ADDRESS"]}}, fh)
    time.sleep(120)
"""


def test_a_killed_run_leaves_no_daemon_and_no_directory(tmp_path):
    """SIGKILL a pytest run mid-test: its dbus-daemon and weston die and its sandbox directories go."""
    import json
    import subprocess
    import sys
    import time
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    inner = tmp_path / "inner"
    inner.mkdir()
    out = tmp_path / "state.json"

    def alive(pid):
        try:
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().split(") ")[1][0] != "Z"  # a zombie is dead
        except FileNotFoundError:
            return False
    (inner / "conftest.py").write_text(INNER_CONFTEST.format(conftest=str(tests / "conftest.py")))
    (inner / "test_hold.py").write_text(INNER_TEST.format(out=str(out)))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHAREL_") and k not in ("PYTEST_CURRENT_TEST",)}
    for var in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY"):  # as a fresh run sees them
        if os.environ.get(f"SHAREL_REAL_{var}"):
            env[var] = os.environ[f"SHAREL_REAL_{var}"]
    proc = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-s", str(inner)],
                            cwd=inner, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 60
        while not out.exists() and time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert out.exists(), proc.stdout.read().decode()[-2000:] if proc.poll() is not None else "no state"
        time.sleep(0.2)
        state = json.loads(out.read_text())
        # while the run is alive, its sandbox is really there (so "gone afterwards" means something)
        assert len(state["pids"]) == 2 and "sharel-unit-" in state["bus"], state  # weston and dbus-daemon
        assert all(os.path.isdir(d) for d in state["dirs"]), state
        assert all(alive(p) for p in state["pids"]), state
        # its own fresh sandbox, not this run's
        outer_home = os.path.dirname(os.environ["XDG_CONFIG_HOME"])
        assert state["bus"] != os.environ["DBUS_SESSION_BUS_ADDRESS"], state
        assert state["dirs"][0] != os.environ["XDG_RUNTIME_DIR"] and state["dirs"][1] != outer_home, state
        assert not state["dirs"][0].startswith(os.environ["XDG_RUNTIME_DIR"] + "/"), state  # not nested in ours
        assert {p.pid for p in __import__("sandbox")._procs}.isdisjoint(state["pids"]), state
    finally:
        proc.kill()  # SIGKILL: no atexit, no fixture teardown
        proc.wait(10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (any(alive(p) for p in state["pids"])
                                           or any(os.path.exists(d) for d in state["dirs"])):
        time.sleep(0.1)
    assert not [p for p in state["pids"] if alive(p)], "sandbox processes outlived the killed run"
    assert not [d for d in state["dirs"] if os.path.exists(d)], "sandbox directories outlived the killed run"
