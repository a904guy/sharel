"""Fake session services for the unit suite's private bus (which has nothing activatable).

A ``FakeService`` owns a well-known name on its own connection, in its own thread and main
context, so calls made from the code under test (blocking or not) are answered."""
from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


class FakeService:
    def __init__(self, name: str, path: str, xml: str) -> None:
        self.name, self.path, self.xml = name, path, xml
        self.calls: list[tuple[str, tuple]] = []
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self._ready.wait(5), f"{name}: fake service did not start"

    def handle(self, method: str, args: tuple):
        """Override: the reply's GLib.Variant (or None for no reply value)."""
        return None

    def _run(self) -> None:
        self.ctx = GLib.MainContext.new()
        self.ctx.push_thread_default()
        self.conn = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        info = Gio.DBusNodeInfo.new_for_xml(self.xml)
        self.conn.register_object(self.path, info.interfaces[0], self._call)
        (reply,) = self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "RequestName", GLib.Variant("(su)", (self.name, 4)), None,
                                       Gio.DBusCallFlags.NONE, 5000, None).unpack()
        assert reply == 1, f"could not own {self.name} (reply {reply})"
        self.loop = GLib.MainLoop.new(self.ctx, False)
        self._ready.set()
        self.loop.run()

    def _call(self, _conn, _sender, _path, _iface, method, params, inv) -> None:
        args = params.unpack()
        self.calls.append((method, args))
        inv.return_value(self.handle(method, args))

    def stop(self) -> None:
        if self.thread.is_alive():
            self.ctx.invoke_full(GLib.PRIORITY_DEFAULT,
                                 lambda: (self.conn.close_sync(None), self.loop.quit(), False)[2])
            self.thread.join(5)


FILE_MANAGER_XML = """<node><interface name="org.freedesktop.FileManager1">
  <method name="ShowFolders"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ShowItems"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ShowItemProperties"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""


class FakeFileManager(FakeService):
    """org.freedesktop.FileManager1 (what Nautilus provides): records ShowItems and the rest."""

    def __init__(self) -> None:
        super().__init__("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", FILE_MANAGER_XML)
