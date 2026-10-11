"""Inspect window tool with a fake ShareL shell extension (and without one)."""
import os

from toolhelpers import ctx_for, wait_until
from uihelpers import run_app

from sharel.core.enums import HotkeyType
from sharel.shell.client import ShellClient, Window

DETAILS = {"id": 0x2A, "title": "Text Editor", "wm_class": "org.gnome.TextEditor",
           "app_id": "org.gnome.TextEditor.desktop",
           "app_name": "Text Editor", "pid": os.getpid(), "client": "wayland", "type": "NORMAL",
           "frame": [10, 20, 800, 600], "buffer": [0, 10, 820, 620], "client_rect": [10, 57, 800, 563],
           "monitor": 0, "workspace": 0, "on_current_workspace": True, "minimized": False, "maximized": False,
           "fullscreen": False, "above": False, "focus": True, "stacking": 3, "sticky": False,
           "decorated": True, "opacity": 255, "layer": 2, "role": "", "borderless": "", "custom_key": "x"}


class FakeShell:
    available = True

    def __init__(self):
        self.calls = []
        self.d = dict(DETAILS)

    def pick_window(self, hint=""):
        self.calls.append(("pick_window",))
        return Window.of(self.d)

    def get_windows(self):
        return [Window.of(self.d), Window(id=7, title="Other")]

    def get_window_details(self, wid):
        self.calls.append(("details", wid))
        if wid == self.d["id"]:
            return dict(self.d)
        return {"id": wid, "title": "Other", "opacity": 128, "frame": [0, 0, 5, 5]}

    def set_above(self, wid, above=True):
        self.calls.append(("set_above", wid, above))
        self.d["above"] = above
        return True

    def set_opacity(self, wid, op):
        self.calls.append(("set_opacity", wid, op))
        self.d["opacity"] = op
        return True

    def move_resize(self, wid, x, y, w, h):
        self.calls.append(("move_resize", wid, x, y, w, h))
        self.d["frame"] = [x, y, w, h]
        return {"ok": True}


def test_pick_shows_details_and_actions(monkeypatch):
    fake = FakeShell()
    copied = []
    import sharel.core.clipboard as cb
    monkeypatch.setattr(cb, "set_text", lambda t: copied.append(t) or "test")

    def body(app):
        ShellClient._instance = fake
        try:
            from sharel.ui.tools import inspectwindow
            win = inspectwindow.open(app, ctx_for(app, HotkeyType.InspectWindow))
            yield from wait_until(lambda: win.details)  # picked on a worker at open
            assert fake.calls[0] == ("pick_window",)
            text = win.text()
            for expected in ("Window ID\n0000002A (42)", "Window title\nText Editor",
                             "Class name\norg.gnome.TextEditor", "Process identifier\n" + str(os.getpid()),
                             "Window rectangle\nX: 10, Y: 20, Width: 800, Height: 600",
                             "Client rectangle\nX: 10, Y: 57, Width: 800, Height: 563", "Focused: Yes",
                             "Opacity\n100% (255/255)", "Process file name\n", "Custom key\nx"):
                assert expected in text, expected
            assert win.info.get_row_at_index(0) is not None
            win.copy_button.emit("clicked")
            assert copied == [text]
            win.top_most.set_active(True)
            assert ("set_above", 42, True) in fake.calls and win.details["above"] is True
            win.opacity_row.set_value(50)
            assert ("set_opacity", 42, 128) in fake.calls
            assert "50% (128/255)" in win.text()
            win.x_spin.set_value(5)
            win.w_spin.set_value(640)
            win.apply_geo.emit("clicked")
            assert ("move_resize", 42, 5, 20, 640, 600) in fake.calls
            # choose another from the window list
            win.fill_window_list()
            row = win.list_box.get_row_at_index(0)  # sorted by title
            assert row.window.title == "Other"
            win.list_box.emit("row-activated", row)
            assert win.selected.id == 7 and "Window title\nOther" in win.text()
            n = len(fake.calls)
            win.refresh_button.emit("clicked")
            assert fake.calls[n:] == [("details", 7)]
        finally:
            ShellClient._instance = None
        return True
    assert run_app(body)


def test_degraded_without_extension():
    def body(app):
        from sharel.ui.tools import inspectwindow
        win = inspectwindow.open(app, ctx_for(app, HotkeyType.InspectWindow))
        yield 0.1
        assert win.banner.get_revealed()
        assert not win.pick_button.get_sensitive() and not win.actions_group.get_sensitive()
        assert "extension" in win.placeholder.get_label()
        return True
    assert run_app(body)
