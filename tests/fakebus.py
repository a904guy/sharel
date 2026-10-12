"""A fake D-Bus service on the sandbox's private session bus, on its own connection and thread, for
testing ShareL's clients of desktop services (portals, Mutter, file managers, notifications)."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


class FakeService:
    """Owns ``name``; ``objects`` maps an object path to (introspection XML of ONE interface, handler).
    A handler gets (method, params as a Python tuple, sender) and returns a GLib.Variant (or None).
    Every call is recorded in ``calls`` as (path, method, params, sender)."""

    def __init__(self, name: str, objects: dict[str, tuple[str, Callable]]) -> None:
        self.name = name
        self.objects = objects
        self.calls: list[tuple[str, str, tuple, str]] = []
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"fake-{name}")
        self.thread.start()
        assert self._ready.wait(5), f"fake {name} did not start"
        if self._error:
            raise self._error

    def _run(self) -> None:
        try:
            self.ctx = GLib.MainContext.new()
            self.ctx.push_thread_default()
            self.conn = Gio.DBusConnection.new_for_address_sync(
                os.environ["DBUS_SESSION_BUS_ADDRESS"],
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            self._infos = []
            for path, (xml, handler) in self.objects.items():
                self.add_object(path, xml, handler)
            self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "RequestName", GLib.Variant("(su)", (self.name, 4)), None, Gio.DBusCallFlags.NONE,
                                5000, None)
            self.loop = GLib.MainLoop.new(self.ctx, False)
        except BaseException as e:  # reported to the constructor
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.run()

    def add_object(self, path: str, xml: str, handler: Callable) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(xml)
        self._infos.append(info)

        def call(_conn, sender, obj_path, _iface, method, params, inv):
            args = params.unpack()
            self.calls.append((obj_path, method, args, sender))
            try:
                ret = handler(method, args, sender)
            except Exception as e:
                inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))
                return
            if isinstance(ret, tuple):  # (variant, Gio.UnixFDList): a method that hands out fds
                inv.return_value_with_unix_fd_list(*ret)
            else:
                inv.return_value(ret)
        self.conn.register_object(path, info.interfaces[0], call)

    def emit(self, path: str, iface: str, signal: str, params: GLib.Variant | None) -> None:
        """Emit a signal from the service's thread."""
        done = threading.Event()

        def go():
            self.conn.emit_signal(None, path, iface, signal, params)
            done.set()
            return False
        self.ctx.invoke_full(GLib.PRIORITY_DEFAULT, go)
        assert done.wait(5)

    def later(self, fn: Callable[[], None], delay_ms: int = 20) -> None:
        """Run ``fn`` on the service's thread shortly (e.g. a Response after the method returned)."""
        src = GLib.timeout_source_new(delay_ms)
        src.set_callback(lambda *_a: (fn(), False)[1])
        src.attach(self.ctx)

    def signal_now(self, path: str, iface: str, signal: str, params: GLib.Variant | None) -> None:
        """Emit from the service's own thread (inside a handler or ``later``)."""
        self.conn.emit_signal(None, path, iface, signal, params)

    def stop(self) -> None:
        """Disconnect (the name is released, as when a service exits) and wait until the bus agrees."""
        if self.thread.is_alive():
            self.ctx.invoke_full(GLib.PRIORITY_DEFAULT,
                                 lambda: (self.conn.close_sync(None), self.loop.quit(), False)[2])
            self.thread.join(5)
        import time
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for _ in range(100):
            (has,) = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "NameHasOwner", GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE,
                                   2000, None).unpack()
            if not has:
                return
            time.sleep(0.02)


FILE_MANAGER_XML = """<node><interface name="org.freedesktop.FileManager1">
  <method name="ShowFolders"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ShowItems"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ShowItemProperties"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""


class FakeFileManager(FakeService):
    """org.freedesktop.FileManager1 (what Nautilus provides): records ShowItems and the rest."""

    def __init__(self) -> None:
        super().__init__("org.freedesktop.FileManager1",
                         {"/org/freedesktop/FileManager1": (FILE_MANAGER_XML, lambda *a: None)})
