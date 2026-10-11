"""The xdg-desktop-portal ScreenCast fallback for recording.

CreateSession → SelectSources(types monitor|window, persist_mode=2, restore_token) → Start →
OpenPipeWireRemote. The restore token returned by Start is stored (ApplicationConfig
``screencast_restore_token``) so the portal doesn't ask again next time.
"""
from __future__ import annotations

import random
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"
SOURCE_MONITOR, SOURCE_WINDOW = 1, 2
CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2


class PortalError(RuntimeError):
    pass


class TokenStore:
    """get() -> str, set(token). Backed by ApplicationConfig when given a ConfigStore."""

    def __init__(self, store=None) -> None:
        self.store = store
        self._token = ""

    def get(self) -> str:
        if self.store is not None:
            return getattr(self.store.app, "screencast_restore_token", "")
        return self._token

    def set(self, token: str) -> None:
        if self.store is not None:
            self.store.app.screencast_restore_token = token
            self.store.save_app()
        else:
            self._token = token


class PortalScreenCast:
    def __init__(self, tokens: TokenStore | None = None, conn: Gio.DBusConnection | None = None,
                 timeout: float = 120.0) -> None:
        self.tokens = tokens or TokenStore()
        self.conn = conn or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.timeout = timeout
        self.session = ""

    def _token(self) -> str:
        return f"sharel{random.randint(0, 1 << 30)}"

    def _request(self, method: str, sig: str, args: tuple, token: str) -> dict:
        """Call a portal method that answers through a Request object's Response signal."""
        from ..capture.backends import _PrivateLoop
        sender = self.conn.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        out: dict = {}
        got = threading.Event()
        with _PrivateLoop() as loop:
            def response(_c, _s, _p, _i, _n, params, _d):
                code, results = params.unpack()
                out["code"], out["results"] = code, results
                got.set()
            sub = self.conn.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", handle, None,
                                             Gio.DBusSignalFlags.NONE, response, None)
            try:
                self.conn.call_sync(PORTAL, PATH, IFACE, method, GLib.Variant(sig, args), None,
                                    Gio.DBusCallFlags.NONE, 30000, None)
                if not loop.wait(got, self.timeout):
                    raise PortalError(f"{method}: no answer from the portal")
            finally:
                self.conn.signal_unsubscribe(sub)
        if out.get("code") != 0:
            raise PortalError(f"{method} was cancelled or denied (code {out.get('code')})")
        return out["results"]

    def start(self, cursor: bool = True) -> tuple[int, int]:
        """Returns (PipeWire node id, PipeWire remote fd)."""
        t = self._token()
        res = self._request("CreateSession", "(a{sv})", ({"handle_token": GLib.Variant("s", t),
                                                          "session_handle_token": GLib.Variant("s", self._token())},), t)
        self.session = res["session_handle"]
        opts = {"handle_token": GLib.Variant("s", (t := self._token())),
                "types": GLib.Variant("u", SOURCE_MONITOR | SOURCE_WINDOW),
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED if cursor else CURSOR_HIDDEN),
                "persist_mode": GLib.Variant("u", 2)}
        saved = self.tokens.get()
        if saved:
            opts["restore_token"] = GLib.Variant("s", saved)
        self._request("SelectSources", "(oa{sv})", (self.session, opts), t)
        t = self._token()
        res = self._request("Start", "(osa{sv})", (self.session, "", {"handle_token": GLib.Variant("s", t)}), t)
        if res.get("restore_token"):
            self.tokens.set(res["restore_token"])
        streams = res.get("streams") or []
        if not streams:
            raise PortalError("the portal returned no stream")
        node_id = streams[0][0]
        reply, fds = self.conn.call_with_unix_fd_list_sync(PORTAL, PATH, IFACE, "OpenPipeWireRemote",
                                                            GLib.Variant("(oa{sv})", (self.session, {})), None,
                                                            Gio.DBusCallFlags.NONE, 10000, None, None)
        (fd_index,) = reply.unpack()
        return node_id, fds.get(fd_index)

    def close(self) -> None:
        if self.session:
            try:
                self.conn.call_sync(PORTAL, self.session, "org.freedesktop.portal.Session", "Close", None, None,
                                    Gio.DBusCallFlags.NONE, 3000, None)
            except GLib.Error:
                pass
            self.session = ""
