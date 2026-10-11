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
from pathlib import Path

APP_ID = "app.sharel.ShareL"
APP_PATH = "/app/sharel/ShareL"


def _icon_dir() -> str:
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "data" / "icons" / "tray", Path("/usr/share/sharel/icons/tray")):
        if cand.is_dir():
            return str(cand)
    return ""


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
    ind.set_status(AI.IndicatorStatus.ACTIVE)
    state = {"building": False, "menu": None}

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

    def build(items, menu: Gtk.Menu) -> None:
        for it in items:
            if it.get("separator"):
                menu.append(Gtk.SeparatorMenuItem())
                continue
            if it.get("check") is not None:
                mi = Gtk.CheckMenuItem(label=it.get("label", ""))
                mi.set_active(bool(it.get("checked")))
                mi.connect("toggled", lambda m, it=it: (not state["building"]) and
                           activate(it.get("action", ""), it.get("target")))
            else:
                mi = Gtk.MenuItem(label=it.get("label", ""))
                if it.get("children"):
                    sub = Gtk.Menu()
                    build(it["children"], sub)
                    mi.set_submenu(sub)
                else:
                    mi.connect("activate", lambda _m, it=it: activate(it.get("action", ""), it.get("target")))
            mi.set_sensitive(it.get("sensitive", True))
            menu.append(mi)

    def set_menu(items) -> None:
        state["building"] = True
        menu = Gtk.Menu()
        build(items, menu)
        # Middle click: AppIndicator's secondary activate target (hidden item running the action).
        middle = Gtk.MenuItem(label="Middle click action")
        middle.connect("activate", lambda *_: activate("app.tray-click", "middle"))
        menu.append(middle)
        menu.show_all()
        middle.hide()
        ind.set_menu(menu)
        ind.set_secondary_activate_target(middle)
        state["menu"] = menu
        state["building"] = False

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
    set_menu([{"label": "ShareL", "sensitive": False}])
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
