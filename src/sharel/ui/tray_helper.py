"""The tray icon: a small GTK 3 process (GTK 3 and GTK 4 can't share one) using AyatanaAppIndicator3,
shown by GNOME's ubuntu-appindicators extension.

The main app starts it and drives it over stdin with JSON lines:

    {"cmd": "menu", "menu": [item, ...]}   item: {"label", "action", "target", "check", "checked",
                                                  "children", "separator", "sensitive"}
    {"cmd": "state", "icon": "recording" | "paused" | "disabled" | "progress-50" | ""}
    {"cmd": "quit"}

Menu items activate the app's GActions over D-Bus (``app.job::RectangleRegion`` and friends).
Middle click is the AppIndicator "secondary activate" target (the middle-click action); left and
double click are routed by the ShareL extension (TrayClicked) when it is running.
"""
from __future__ import annotations

import json
import os
import sys

APP_ID = "app.sharel.ShareL"
APP_PATH = "/app/sharel/ShareL"


def _icon_dir() -> str:
    from sharel.core.paths import package_data_dir
    d = package_data_dir() / "icons" / "tray"
    return str(d) if d.is_dir() else ""


def main() -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AI
    from gi.repository import Gio, GLib, Gtk

    Gtk.init(sys.argv)
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    actions = Gio.DBusActionGroup.get(conn, APP_ID, APP_PATH)

    ind = AI.Indicator.new(APP_ID, "sharel-tray", AI.IndicatorCategory.APPLICATION_STATUS)
    icon_dir = _icon_dir()
    if icon_dir:
        ind.set_icon_theme_path(icon_dir)
    ind.set_icon_full("sharel-tray", "ShareL")
    ind.set_title("ShareL")
    # Shown once the app's first menu is in place (or after a few seconds without one): the shell then
    # fetches a finished menu instead of a placeholder that is replaced straight away.
    ind.set_status(AI.IndicatorStatus.PASSIVE)
    state = {"building": False, "items": None}

    def activate(action: str, target) -> None:
        if not action:
            return
        name = action.split(".", 1)[1] if "." in action else action
        param = None
        if isinstance(target, bool):
            param = GLib.Variant("b", target)
        elif isinstance(target, int):
            param = GLib.Variant("i", target)
        elif isinstance(target, str):
            param = GLib.Variant("s", target)
        actions.activate_action(name, param)

    def kind(it: dict) -> str:
        if it.get("separator"):
            return "separator"
        if it.get("check") is not None:
            return "check"
        return "submenu" if it.get("children") else "item"

    def new_item(it: dict) -> Gtk.MenuItem:
        k = kind(it)
        if k == "separator":
            mi = Gtk.SeparatorMenuItem()
        elif k == "check":
            mi = Gtk.CheckMenuItem(label=it.get("label", ""))
            mi.connect("toggled", lambda m: (not state["building"]) and
                       activate(m.sharel_item.get("action", ""), m.sharel_item.get("target")))
        else:
            mi = Gtk.MenuItem(label=it.get("label", ""))
            if k == "submenu":
                mi.set_submenu(Gtk.Menu())
            else:
                mi.connect("activate", lambda m: activate(m.sharel_item.get("action", ""),
                                                          m.sharel_item.get("target")))
        mi.sharel_kind = k
        mi.show()
        return mi

    def sync(menu: Gtk.Menu, items, keep: int = 0) -> None:
        """Make ``menu`` show ``items``, changing the existing GtkMenuItems in place. Each GtkMenuItem is one
        dbusmenu id, so ids stay the same across updates: the shell's appindicator extension fetches labels
        separately from the layout, and replacing every id on each update could leave it with a layout
        whose labels never arrived (the tray menu with no text). ``keep``: trailing children left alone."""
        children = menu.get_children()
        if keep:
            children = children[:-keep]
        for pos, it in enumerate(items):
            mi = children[pos] if pos < len(children) else None
            if mi is None or getattr(mi, "sharel_kind", None) != kind(it):
                if mi is not None:
                    menu.remove(mi)
                mi = new_item(it)
                menu.insert(mi, pos)
            mi.sharel_item = it
            if mi.sharel_kind != "separator":
                if mi.get_label() != it.get("label", ""):
                    mi.set_label(it.get("label", ""))
                if mi.sharel_kind == "check" and mi.get_active() != bool(it.get("checked")):
                    mi.set_active(bool(it.get("checked")))
                if mi.sharel_kind == "submenu":
                    sync(mi.get_submenu(), it["children"])
            if mi.get_sensitive() != it.get("sensitive", True):
                mi.set_sensitive(it.get("sensitive", True))
        for mi in children[len(items):]:
            menu.remove(mi)

    root = Gtk.Menu()
    # Middle click: AppIndicator's secondary activate target (hidden item running the action).
    middle = Gtk.MenuItem(label="Middle click action")
    middle.connect("activate", lambda *_: activate("app.tray-click", "middle"))
    root.append(middle)
    ind.set_menu(root)
    ind.set_secondary_activate_target(middle)

    def show() -> bool:
        ind.set_status(AI.IndicatorStatus.ACTIVE)
        return False

    def set_menu(items) -> None:
        if items == state["items"]:
            return
        first = state["items"] is None
        state["building"] = True
        try:
            sync(root, items, keep=1)
        finally:
            state["building"] = False
        state["items"] = items
        if first:
            show()

    def on_line(source, _cond) -> bool:
        line = source.readline()
        if not line:
            Gtk.main_quit()
            return False
        try:
            msg = json.loads(line)
        except ValueError:
            return True
        cmd = msg.get("cmd")
        if cmd == "menu":
            set_menu(msg.get("menu", []))
        elif cmd == "state":
            icon = msg.get("icon") or ""
            ind.set_icon_full(f"sharel-tray-{icon}" if icon else "sharel-tray", msg.get("tooltip", "ShareL"))
            if msg.get("tooltip"):
                ind.set_title(msg["tooltip"])
        elif cmd == "quit":
            Gtk.main_quit()
            return False
        return True

    stdin = os.fdopen(sys.stdin.fileno(), "r", buffering=1)
    GLib.io_add_watch(stdin, GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, on_line)
    GLib.timeout_add_seconds(5, show)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
