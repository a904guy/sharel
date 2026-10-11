"""Global hotkeys on GNOME Wayland.

Backends, by preference:

1. **Extension**: ``GrabAccelerator`` in the shell, ``AcceleratorActivated`` signal back (works
   for Print too once GNOME's own Print bindings are freed).
2. **GlobalShortcutsPortal**: ``org.freedesktop.portal.GlobalShortcuts`` (GNOME asks the user to
   confirm the bindings once).
3. **GSettings**: ShareL-owned entries in ``org.gnome.settings-daemon.plugins.media-keys
   custom-keybindings`` that run ``sharel -<Job>`` / ``sharel -workflow "<name>"``. This edits the
   user's keybindings, so it is used only when chosen explicitly in Application settings ▸
   Advanced (Auto never falls through to it on its own); ShareL removes its entries when the
   backend is switched off.

Conflicts are reported per hotkey: duplicates within ShareL, clashes with GNOME's own
keybindings (read from gsettings), and grabs the shell refused.
"""
from __future__ import annotations

import logging
import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from ..core.config import HotkeySettings
from .client import ShellUnavailable, shell

log = logging.getLogger("sharel.hotkeys")

GNOME_KEYBINDING_SCHEMAS = ("org.gnome.shell.keybindings", "org.gnome.desktop.wm.keybindings",
                            "org.gnome.settings-daemon.plugins.media-keys", "org.gnome.mutter.keybindings",
                            "org.gnome.mutter.wayland.keybindings")
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KB = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/sharel-"


def normalize(accel: str) -> str:
    """Canonical accelerator string ("<Ctrl><shift>print" -> "<Shift><Control>Print")."""
    if not accel:
        return ""
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        mods_part, _, key_part = accel.rpartition(">")
        prefix = mods_part + ">" if mods_part else ""
        # Key names are case-sensitive ("Print", "Page_Down"); try the usual spellings.
        for cand in (key_part, key_part.capitalize(), key_part.lower(), key_part.title(),
                     "_".join(p.capitalize() for p in key_part.split("_"))):
            ok, key, mods = Gtk.accelerator_parse(prefix + cand)
            if ok and key:
                return Gtk.accelerator_name(key, mods)
    except (ImportError, ValueError):
        pass
    return accel


def label(accel: str) -> str:
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        ok, key, mods = Gtk.accelerator_parse(accel)
        if ok and key:
            return Gtk.accelerator_get_label(key, mods)
    except (ImportError, ValueError):
        pass
    return accel


def gnome_bindings() -> dict[str, str]:
    """normalized accelerator -> 'schema key' for GNOME's own keybindings (read-only)."""
    out: dict[str, str] = {}
    src = Gio.SettingsSchemaSource.get_default()
    if src is None:
        return out
    for schema_id in GNOME_KEYBINDING_SCHEMAS:
        schema = src.lookup(schema_id, True)
        if schema is None:
            continue
        settings = Gio.Settings.new(schema_id)
        for key in schema.list_keys():
            try:
                val = settings.get_value(key)
            except Exception:
                continue
            if val.get_type_string() == "as":
                for a in val.unpack():
                    if a:
                        out.setdefault(normalize(a), f"{schema_id} {key}")
            elif val.get_type_string() == "s" and val.unpack().startswith("<"):
                out.setdefault(normalize(val.unpack()), f"{schema_id} {key}")
    return out


@dataclass
class HotkeyStatus:
    hotkey: HotkeySettings
    state: str = "unregistered"  # registered | pending | failed | conflict | disabled | unregistered
    detail: str = ""


def find_conflicts(hotkeys: list[HotkeySettings], gnome: dict[str, str] | None = None) -> dict[int, str]:
    """index -> reason, for duplicates within ShareL and clashes with GNOME keybindings."""
    gnome = gnome_bindings() if gnome is None else gnome
    seen: dict[str, int] = {}
    out: dict[int, str] = {}
    for i, hk in enumerate(hotkeys):
        a = normalize(hk.hotkey_info.hotkey)
        if not a or not hk.hotkey_info.enabled:
            continue
        if a in seen:
            out[i] = f"also used by \"{hotkeys[seen[a]].name}\""
            out.setdefault(seen[a], f"also used by \"{hk.name}\"")
        else:
            seen[a] = i
        if a in gnome and i not in out:
            out[i] = f"GNOME uses it ({gnome[a]})"
    return out


def command_for(hk: HotkeySettings) -> str:
    exe = os.environ.get("SHAREL_EXECUTABLE") or shlex.quote(sys.argv[0] if sys.argv[0].endswith("sharel")
                                                             else "sharel")
    if hk.task_settings.description:
        return f"{exe} -workflow {shlex.quote(hk.task_settings.description)}"
    return f"{exe} -{hk.job.value}"


class HotkeyManager:
    def __init__(self, on_activate: Callable[[HotkeySettings], None], backend: str = "Auto") -> None:
        self.on_activate = on_activate
        self.backend_pref = backend
        self.active_backend = ""
        self.status: list[HotkeyStatus] = []
        self._handles: dict[int, HotkeySettings] = {}
        self._sub = 0
        self._portal: _PortalShortcuts | None = None
        self.disabled = False

    # -- public --

    def register(self, hotkeys: list[HotkeySettings]) -> list[HotkeyStatus]:
        self.unregister()
        self.status = [HotkeyStatus(hk) for hk in hotkeys]
        if self.disabled:
            for st in self.status:
                st.state, st.detail = "disabled", "hotkeys are disabled"
            return self.status
        conflicts = find_conflicts(hotkeys)
        usable = []
        for i, st in enumerate(self.status):
            info = st.hotkey.hotkey_info
            if not info.hotkey or not info.enabled:
                st.state = "disabled"
                continue
            if i in conflicts and conflicts[i].startswith("also used"):
                st.state, st.detail = "conflict", conflicts[i]
                continue
            if i in conflicts:
                st.detail = conflicts[i]
            usable.append(st)
        for name in self._backend_order():
            try:
                if name == "Extension" and self._register_extension(usable):
                    self.active_backend = name
                    break
                if name == "GlobalShortcutsPortal" and self._register_portal(usable):
                    self.active_backend = name
                    break
                if name == "GSettings" and self._register_gsettings(usable):
                    self.active_backend = name
                    break
            except Exception as e:
                log.warning("hotkey backend %s failed: %s", name, e)
        if not self.active_backend:
            for st in usable:
                st.state, st.detail = "failed", "no hotkey backend available (install the ShareL extension)"
        return self.status

    def unregister(self) -> None:
        sh = shell()
        if self._handles and sh.available:
            for h in list(self._handles):
                try:
                    sh.ungrab_accelerator(h)
                except ShellUnavailable:
                    pass
        if self._sub and sh.available:
            sh.disconnect(self._sub)
        self._sub = 0
        self._handles.clear()
        if self._portal:
            self._portal.close()
            self._portal = None
        self.active_backend = ""

    def refresh_states(self) -> list[HotkeyStatus]:
        """Pull the extension's view (pending grabs become registered or failed)."""
        if self.active_backend != "Extension":
            return self.status
        try:
            states = {e["handle"]: e for e in shell().list_accelerators()}
        except ShellUnavailable:
            return self.status
        for handle, hk in self._handles.items():
            st = next((s for s in self.status if s.hotkey is hk), None)
            e = states.get(handle)
            if st and e:
                st.state = {"grabbed": "registered", "pending": "pending", "failed": "failed"}.get(e["state"],
                                                                                                  e["state"])
                if e["state"] == "failed" and not st.detail:
                    st.detail = "the shell refused the shortcut (another binding uses it)"
        return self.status

    # -- backends --

    def _backend_order(self) -> list[str]:
        pref = self.backend_pref or "Auto"
        if pref == "Auto":
            return ["Extension", "GlobalShortcutsPortal"]
        return [pref]

    def _register_extension(self, usable: list[HotkeyStatus]) -> bool:
        sh = shell()
        if not sh.available:
            return False
        self._sub = sh.connect_accelerator(self._on_accel)
        for st in usable:
            accel = normalize(st.hotkey.hotkey_info.hotkey)
            handle = sh.grab_accelerator(accel, st.hotkey.name)
            self._handles[handle] = st.hotkey
            st.state = "pending"
        self.refresh_states()
        return True

    def _on_accel(self, handle: int, _name: str, _ts: int) -> None:
        hk = self._handles.get(handle)
        if hk is not None:
            self.on_activate(hk)

    def _register_portal(self, usable: list[HotkeyStatus]) -> bool:
        p = _PortalShortcuts(self.on_activate)
        if not p.available():
            return False
        p.bind([st.hotkey for st in usable])
        for st in usable:
            st.state = "pending"
            st.detail = st.detail or "waiting for GNOME to confirm the shortcuts"
        self._portal = p
        return True

    def _register_gsettings(self, usable: list[HotkeyStatus]) -> bool:
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup(MEDIA_KEYS, True) is None:
            return False
        media = Gio.Settings.new(MEDIA_KEYS)
        paths = [p for p in media.get_strv("custom-keybindings") if not p.startswith(CUSTOM_PREFIX)]
        for i, st in enumerate(usable):
            path = f"{CUSTOM_PREFIX}{i}/"
            kb = Gio.Settings.new_with_path(CUSTOM_KB, path)
            kb.set_string("name", f"ShareL: {st.hotkey.name}")
            kb.set_string("command", command_for(st.hotkey))
            kb.set_string("binding", normalize(st.hotkey.hotkey_info.hotkey))
            paths.append(path)
            st.state = "registered"
        media.set_strv("custom-keybindings", paths)
        Gio.Settings.sync()
        return True

    @staticmethod
    def remove_gsettings_entries() -> int:
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup(MEDIA_KEYS, True) is None:
            return 0
        media = Gio.Settings.new(MEDIA_KEYS)
        cur = media.get_strv("custom-keybindings")
        ours = [p for p in cur if p.startswith(CUSTOM_PREFIX)]
        for p in ours:
            kb = Gio.Settings.new_with_path(CUSTOM_KB, p)
            for k in ("name", "command", "binding"):
                kb.reset(k)
        if ours:
            media.set_strv("custom-keybindings", [p for p in cur if not p.startswith(CUSTOM_PREFIX)])
            Gio.Settings.sync()
        return len(ours)


class _PortalShortcuts:
    """org.freedesktop.portal.GlobalShortcuts (signals arrive on the app's main loop)."""

    PORTAL = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.GlobalShortcuts"

    def __init__(self, on_activate: Callable[[HotkeySettings], None]) -> None:
        self.on_activate = on_activate
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = ""
        self._ids: dict[str, HotkeySettings] = {}
        self._subs: list[int] = []

    def available(self) -> bool:
        try:
            v = self.conn.call_sync(self.PORTAL, self.PATH, "org.freedesktop.DBus.Properties", "Get",
                                    GLib.Variant("(ss)", (self.IFACE, "version")), None, Gio.DBusCallFlags.NONE,
                                    2000, None)
            return v.unpack()[0] >= 1
        except GLib.Error:
            return False

    def _token(self, kind: str) -> str:
        import random
        return f"sharel_{kind}_{random.randint(0, 1 << 30)}"

    def _request_path(self, token: str) -> str:
        sender = self.conn.get_unique_name()[1:].replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def bind(self, hotkeys: list[HotkeySettings]) -> None:
        self._ids = {f"sharel-{i}": hk for i, hk in enumerate(hotkeys)}
        token = self._token("req")
        session_token = self._token("session")

        def on_session(_c, _s, _p, _i, _n, params, _d):
            code, results = params.unpack()
            if code != 0:
                log.warning("GlobalShortcuts CreateSession refused (%s)", code)
                return
            self.session = results.get("session_handle", "")
            self._bind_now()

        self._subs.append(self.conn.signal_subscribe(self.PORTAL, "org.freedesktop.portal.Request", "Response",
                                                     self._request_path(token), None, Gio.DBusSignalFlags.NONE,
                                                     on_session, None))
        self._subs.append(self.conn.signal_subscribe(self.PORTAL, self.IFACE, "Activated", self.PATH, None,
                                                     Gio.DBusSignalFlags.NONE, self._on_activated, None))
        self.conn.call(self.PORTAL, self.PATH, self.IFACE, "CreateSession",
                       GLib.Variant("(a{sv})", ({"handle_token": GLib.Variant("s", token),
                                                 "session_handle_token": GLib.Variant("s", session_token)},)),
                       None, Gio.DBusCallFlags.NONE, 10000, None, None, None)

    def _bind_now(self) -> None:
        shortcuts = []
        for sid, hk in self._ids.items():
            trigger = _portal_trigger(normalize(hk.hotkey_info.hotkey))
            shortcuts.append((sid, {"description": GLib.Variant("s", f"ShareL: {hk.name}"),
                                    "preferred_trigger": GLib.Variant("s", trigger)}))
        token = self._token("bind")
        self.conn.call(self.PORTAL, self.PATH, self.IFACE, "BindShortcuts",
                       GLib.Variant("(oa(sa{sv})sa{sv})", (self.session, shortcuts, "",
                                                           {"handle_token": GLib.Variant("s", token)})),
                       None, Gio.DBusCallFlags.NONE, 60000, None, None, None)

    def _on_activated(self, _c, _s, _p, _i, _n, params, _d):
        session, sid, _ts, _opts = params.unpack()
        if session == self.session and sid in self._ids:
            self.on_activate(self._ids[sid])

    def close(self) -> None:
        for s in self._subs:
            self.conn.signal_unsubscribe(s)
        self._subs.clear()
        if self.session:
            try:
                self.conn.call_sync(self.PORTAL, self.session, "org.freedesktop.portal.Session", "Close", None,
                                    None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
        self.session = ""


def _portal_trigger(accel: str) -> str:
    """GTK accelerator -> the XDG shortcuts spec ("<Control><Shift>Print" -> "CTRL+SHIFT+Print")."""
    mods = {"control": "CTRL", "shift": "SHIFT", "alt": "ALT", "super": "LOGO", "meta": "LOGO", "primary": "CTRL"}
    out = []
    rest = accel
    while rest.startswith("<"):
        end = rest.index(">")
        m = rest[1:end].lower()
        if m in mods:
            out.append(mods[m])
        rest = rest[end + 1:]
    out.append(rest)
    return "+".join(out)
