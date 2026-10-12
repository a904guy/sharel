"""Auto capture (ShareX's AutoCaptureForm): repeat a region or screen capture every N seconds."""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ...capture.autocapture import AUTO
from ..gtkutil import live
from ...core.config import Rect
from ...core.enums import RegionCaptureMode

_state: dict = {}


def open_auto_capture(app, ctx) -> Adw.Window:
    if live(_state.get("win")):
        _state["win"].present()
        return _state["win"]
    opts = ctx.task_settings.capture_settings.auto_capture
    defaults = app.store.app.default_task_settings.capture_settings.auto_capture
    win = Adw.Window(application=app, title="ShareL: auto capture", default_width=440, default_height=420)
    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup(title="Auto capture")
    repeat = Adw.SpinRow(title="Repeat every (seconds)", adjustment=Gtk.Adjustment(
        value=opts.repeat_time, lower=0.5, upper=86400, step_increment=1, page_increment=10), digits=1)
    full = Adw.SwitchRow(title="Capture the whole screen", active=opts.capture_fullscreen)
    region_row = Adw.ActionRow(title="Region", subtitle=_region_text(opts.region))
    pick = Gtk.Button(label="Select region…", valign=Gtk.Align.CENTER)
    region_row.add_suffix(pick)
    hide = Adw.SwitchRow(title="Hide this window while capturing", active=opts.minimize_to_tray)
    auto = Adw.SwitchRow(title="Start at startup", active=defaults.auto_start)
    for r in (repeat, full, region_row, hide, auto):
        group.add(r)
    status_group = Adw.PreferencesGroup(title="Status")
    status = Adw.ActionRow(title="Stopped")
    status_group.add(status)
    page.add(group)
    page.add(status_group)
    header = Adw.HeaderBar()
    start = Gtk.Button(label="Start")
    start.add_css_class("suggested-action")
    header.pack_end(start)
    tv = Adw.ToolbarView(content=page)
    tv.add_top_bar(header)
    win.set_content(tv)

    def save() -> None:
        for o in (opts, defaults):
            o.repeat_time = repeat.get_value()
            o.capture_fullscreen = full.get_active()
            o.minimize_to_tray = hide.get_active()
            o.region = Rect(*opts.region.as_tuple())
        defaults.auto_start = auto.get_active()
        app.store.save_app()

    def pick_region(*_):
        def outcome(o):
            if o.rect_logical:
                opts.region = Rect(*o.rect_logical)
                region_row.set_subtitle(_region_text(opts.region))
                full.set_active(False)
                save()
            win.present()
        win.set_visible(False)
        GLib.timeout_add(250, lambda: (app.run_region(RegionCaptureMode.Default, ctx.task_settings, outcome), False)[1])

    def toggle(*_):
        if AUTO.running:
            AUTO.stop()
            return
        save()
        rect = None if full.get_active() else (opts.region.as_tuple() if not opts.region.empty else None)
        if rect is None and not full.get_active():
            return pick_region()
        AUTO.start(app, ctx, rect)
        if hide.get_active():
            win.set_visible(False)

    def update(_a=None) -> None:
        if AUTO.running:
            start.set_label("Stop")
            left = max(0.0, AUTO.next_at - time.monotonic())
            status.set_title(f"Capturing: {AUTO.count} capture(s), next in {left:.0f} s")
        else:
            start.set_label("Start")
            status.set_title(f"Stopped ({AUTO.count} capture(s))" if AUTO.count else "Stopped")

    pick.connect("clicked", pick_region)
    start.connect("clicked", toggle)
    AUTO.listeners.append(update)
    tick = GLib.timeout_add(500, lambda: (update(), True)[1])

    def destroyed(*_):
        GLib.source_remove(tick)
        if update in AUTO.listeners:
            AUTO.listeners.remove(update)
        _state.pop("win", None)
    win.connect("destroy", destroyed)
    _state["win"] = win
    win._toggle, win._full, win._repeat = toggle, full, repeat  # type: ignore[attr-defined]
    update()
    win.present()
    return win


def _region_text(r: Rect) -> str:
    return "Not set" if r.empty else f"{r.x}, {r.y}  {r.width} × {r.height}"
