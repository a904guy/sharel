"""Borderless window tool with a fake ShareL shell extension (and without one)."""
from toolhelpers import ctx_for
from uihelpers import run_app

from sharel.core.enums import HotkeyType
from sharel.shell.client import Monitor, ShellClient, Window


class FakeShell:
    available = True
    version = "test"

    def __init__(self, method="motif-hints"):
        self.calls = []
        self.method = method
        self.wins = [Window(id=11, title="Terminal", app_name="Console", frame=(100, 120, 800, 600), monitor=0),
                     Window(id=22, title="My Game - Level 1", app_name="Game", frame=(50, 60, 1024, 768),
                            monitor=1)]
        self.borderless = {}

    def get_windows(self):
        return list(self.wins)

    def get_monitors(self):
        return [Monitor(0, 0, 0, 1920, 1080, primary=True), Monitor(1, 1920, 0, 2560, 1440)]

    def get_window_details(self, wid):
        return {"id": wid, "borderless": self.borderless.get(wid, "")}

    def make_borderless(self, wid, on=True):
        self.calls.append(("make_borderless", wid, on))
        if on:
            self.borderless[wid] = self.method
        else:
            self.borderless.pop(wid, None)
        return {"ok": True, "method": self.method}

    def move_resize(self, wid, x, y, w, h):
        self.calls.append(("move_resize", wid, x, y, w, h))
        return {"ok": True, "frame": [x, y, w, h]}


def _with_shell(fake, fn):
    def body(app):
        ShellClient._instance = fake
        from sharel.ui.tools import borderless
        borderless._saved.clear()
        try:
            res = yield from fn(app, borderless)
        finally:
            ShellClient._instance = None
            borderless._saved.clear()
        return res
    return body


def test_make_borderless_fills_monitor_and_restores():
    fake = FakeShell()

    def fn(app, borderless):
        win = borderless.open(app, ctx_for(app, HotkeyType.BorderlessWindow))
        yield 0.1
        assert not win.banner.get_revealed()
        assert [r.window.title for r in iter_rows(win.listbox)] == ["My Game - Level 1", "Terminal"]
        win.filter_entry.set_text("game")
        win.listbox.invalidate_filter()
        win.listbox.select_row(win.listbox.get_row_at_index(0))
        assert win.title_entry.get_text() == "My Game - Level 1"
        win.exclude_row.set_active(False)
        win.toggle_button.emit("clicked")
        assert fake.calls == [("make_borderless", 22, True), ("move_resize", 22, 1920, 0, 2560, 1440)]
        assert app.store.app.default_task_settings.tool_settings.border_window_title == "My Game - Level 1"
        fake.calls.clear()
        win.toggle_button.emit("clicked")  # toggles back: restore + saved frame
        assert fake.calls == [("make_borderless", 22, False), ("move_resize", 22, 50, 60, 1024, 768)]
        # exclude taskbar on the primary monitor, partial title search
        fake.calls.clear()
        win.title_entry.set_text("termin")
        win.exclude_row.set_active(True)
        assert app.store.app.default_task_settings.tool_settings.border_window_exclude_taskbar
        win.toggle_button.emit("clicked")
        assert fake.calls == [("make_borderless", 11, True), ("move_resize", 11, 0, 32, 1920, 1048)]
        fake.calls.clear()
        win.restore_button.emit("clicked")
        assert fake.calls == [("make_borderless", 11, False), ("move_resize", 11, 100, 120, 800, 600)]
        return True
    assert run_app(_with_shell(fake, fn))


def test_wayland_fullscreen_method_does_not_move_and_autoclose():
    fake = FakeShell(method="fullscreen")

    def fn(app, borderless):
        win = borderless.open(app, ctx_for(app, HotkeyType.BorderlessWindow))
        win.title_entry.set_text("Terminal")
        win.autoclose_row.set_active(True)
        win.toggle_button.emit("clicked")
        yield 0.1
        assert fake.calls == [("make_borderless", 11, True)]
        from sharel.ui.tools._base import existing
        assert existing("borderless") is None  # auto closed
        return True
    assert run_app(_with_shell(fake, fn))


def test_degraded_without_extension():
    def body(app):
        from sharel.ui.tools import borderless
        win = borderless.open(app, ctx_for(app, HotkeyType.BorderlessWindow))
        yield 0.1
        assert win.banner.get_revealed()
        assert "ShareL GNOME Shell extension" in win.banner.get_title()
        win.title_entry.set_text("anything")
        assert not win.toggle_button.get_sensitive() and not win.restore_button.get_sensitive()
        assert "extension" in win.status.get_label()
        return True
    assert run_app(body)


def iter_rows(listbox):
    i = 0
    while (r := listbox.get_row_at_index(i)) is not None:
        yield r
        i += 1
