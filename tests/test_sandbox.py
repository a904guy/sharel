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
