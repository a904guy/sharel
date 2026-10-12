"""The tray menu as GNOME's appindicator extension reads it: the real sharel-tray helper (GTK 3 +
AyatanaAppIndicator3) on the sandbox's private bus and headless display, queried over com.canonical.dbusmenu.

The extension asks GetLayout for the structure only (type, children-display) and fetches every label later
with GetGroupProperties on the ids it got. The helper used to rebuild the whole GtkMenu on each update (three
times at startup, then after every task), so the ids the extension held went stale: GetGroupProperties on
them returns nothing, and the tray menu showed its submenu arrows and separators with no text."""
from __future__ import annotations

from uihelpers import run_app

from gi.repository import Gio, GLib

MENU_PATH = "/org/ayatana/NotificationItem/app_sharel_ShareL/Menu"
IFACE = "com.canonical.dbusmenu"


class DbusMenu:
    def __init__(self, pid: int) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.pid = pid
        self.name: str | None = None

    def _call(self, dest, path, iface, method, args, reply):
        return self.bus.call_sync(dest, path, iface, method, args, GLib.VariantType(reply), Gio.DBusCallFlags.NONE,
                                  3000, None).unpack()

    def find(self) -> bool:
        """The helper's connection that exports the menu."""
        (names,) = self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, "(as)")
        for n in names:
            if not n.startswith(":"):
                continue
            try:
                (pid,) = self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                    "GetConnectionUnixProcessID", GLib.Variant("(s)", (n,)), "(u)")
                if pid != self.pid:
                    continue
                (xml,) = self._call(n, MENU_PATH, "org.freedesktop.DBus.Introspectable", "Introspect", None, "(s)")
            except GLib.Error:
                continue
            if IFACE in xml:
                self.name = n
                return True
        return False

    def layout(self, props: list[str]) -> tuple:
        rev, root = self._call(self.name, MENU_PATH, IFACE, "GetLayout", GLib.Variant("(iias)", (0, -1, props)),
                               "(u(ia{sv}av))")
        return rev, root

    def group(self, ids: list[int]) -> dict[int, dict]:
        (rows,) = self._call(self.name, MENU_PATH, IFACE, "GetGroupProperties", GLib.Variant("(aias)", (ids, [])),
                             "(a(ia{sv}))")
        return dict(rows)


def flatten(node) -> list[tuple[int, dict]]:
    """[(id, properties)] for every item under the root, depth first."""
    out = []
    for child in node[2]:
        out.append((child[0], child[1]))
        out += flatten(child)
    return out


def labelled(items: list[tuple[int, dict]]) -> list[int]:
    """Ids that must show text: everything but separators and the hidden middle-click item."""
    return [i for i, p in items if p.get("type") != "separator" and p.get("visible", True)]


def test_tray_menu_labels_reach_the_extension_and_ids_stay_put(monkeypatch):
    from sharel.ui.tray import TrayClient

    def body(app):
        monkeypatch.delenv("SHAREL_NO_TRAY")
        tray = TrayClient(app)
        tray.start()
        try:
            m = DbusMenu(tray.proc.pid)
            for _ in range(100):
                if (m.name or m.find()) and len(flatten(m.layout([])[1])) > 50:  # the app's menu is in
                    break
                yield 0.1
            else:
                raise AssertionError("the tray helper never exported the app's menu")
            yield 0.5
            full = flatten(m.layout([])[1])
            must = labelled(full)
            assert len(must) > 50
            missing = [(i, p) for i, p in full if i in must and not p.get("label")]
            assert not missing, missing
            labels = {i: p["label"] for i, p in full if i in must}
            assert {"Capture", "Upload", "Tools", "Exit", "Disable hotkeys", "Capture region"} <= set(labels.values())

            # what the extension holds: the structure only, then the labels by id
            _rev, root = m.layout(["type", "children-display"])
            first = flatten(root)
            assert not any("label" in p for _i, p in first)  # labels really come only from GetGroupProperties

            # what happens after every task (send_state), and a real change (the hotkeys toggle's label)
            for _ in range(3):
                tray.send_state()
            app.store.app.disable_hotkeys = True
            tray.send_state()
            yield 1.0

            _rev, root2 = m.layout(["type", "children-display"])
            assert [i for i, _p in flatten(root2)] == [i for i, _p in first]  # same ids: nothing went stale
            got = m.group([i for i, _p in first])
            empty = [i for i in must if not got.get(i, {}).get("label")]
            assert not empty, f"ids the extension holds that no longer have a label: {empty[:10]}"
            assert got[next(i for i, t in labels.items() if t == "Disable hotkeys")]["label"] == "Enable hotkeys"
        finally:
            tray.stop()
        return True

    assert run_app(body, timeout=60)
