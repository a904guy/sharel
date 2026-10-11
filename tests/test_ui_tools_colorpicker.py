"""Colour picker window: fields stay in sync, copy formats, screen/clipboard pick, recent colours, box/slider."""
from types import SimpleNamespace

from toolhelpers import ctx_for
from uihelpers import run_app

from sharel.core.clipboard import ClipboardContent
from sharel.core.enums import HotkeyType, RegionCaptureMode


def test_fields_sync_and_copy_formats(monkeypatch):
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: copied.append(t) or "test")

    def body(app):
        from sharel.ui.tools import colorpicker
        win = colorpicker.open(app, ctx_for(app, HotkeyType.ColorPicker))
        assert colorpicker.open(app, ctx_for(app, HotkeyType.ColorPicker)) is win
        yield 0.2
        assert win.get_mapped()
        sp = win.spins
        # RGB -> everything else
        sp["Red"].set_value(0)
        sp["Green"].set_value(128)
        sp["Blue"].set_value(255)
        assert win.color.rgb == (0, 128, 255)
        assert win.hex_entry.get_text() == "0080FF"
        assert win.dec_entry.get_text() == str(0x0080FF)
        assert round(sp["Hue"].get_value()) == 210
        assert round(sp["Saturation"].get_value()) == 100
        assert round(sp["Brightness"].get_value()) == 100
        assert sp["Cyan"].get_value() == 100 and sp["Key"].get_value() == 0
        assert abs(sp["Magenta"].get_value() - 49.8) < 0.05
        # hex -> fields
        win.hex_entry.set_text("FF0000")
        assert (sp["Red"].get_value(), sp["Green"].get_value(), sp["Blue"].get_value()) == (255, 0, 0)
        assert win.name_label.get_label() == "red"
        assert win.dec_entry.get_text() == "16711680"
        # 8-digit hex is RRGGBBAA like ShareX's field
        win.hex_entry.set_text("00FF0080")
        assert win.color.rgba == (0, 255, 0, 128) and sp["Alpha"].get_value() == 128
        # decimal -> fields (keeps alpha)
        win.dec_entry.set_text("255")
        assert win.color.rgba == (0, 0, 255, 128)
        # HSB -> fields
        sp["Alpha"].set_value(255)
        sp["Hue"].set_value(120)
        sp["Saturation"].set_value(100)
        sp["Brightness"].set_value(50)
        assert win.color.rgb == (0, 128, 0) and win.hex_entry.get_text() == "008000"
        # CMYK -> fields
        sp["Cyan"].set_value(0)
        sp["Magenta"].set_value(0)
        sp["Yellow"].set_value(0)
        sp["Key"].set_value(100)
        assert win.color.rgb == (0, 0, 0)
        # transparent toggle
        win.transparent.set_active(True)
        assert win.color.a == 0
        win.transparent.set_active(False)
        assert win.color.a == 255
        # copy in every format
        win.hex_entry.set_text("FF8000")
        assert win.copy("RGB") == "255, 128, 0"
        assert win.copy("Hex") == "#FF8000"
        assert win.copy("Decimal") == str(0xFF8000)
        assert win.copy("HSB") == "30.1°, 100.0%, 100.0%"
        assert win.copy("CMYK") == "0.0%, 49.8%, 100.0%, 0.0%"
        assert win.copy("All").startswith("RGBA (Red, Green, Blue, Alpha) = 255, 128, 0, 255")
        assert win.copy("Position") == "0, 0"
        assert copied[0] == "255, 128, 0" and len(copied) == 7
        # the copy menu actions reach the same code
        win.activate_action("cp.copy-rgb", None)
        assert copied[-1] == "255, 128, 0"
        assert app.store.app.recent_colors[0] == "#FFFF8000"
        return True
    assert run_app(body)


def test_pick_from_screen_and_clipboard_and_recent(monkeypatch):
    monkeypatch.setattr("sharel.core.clipboard.read", lambda: ClipboardContent("text", text=" rgb(10, 20, 30) "))

    def body(app):
        from sharel.ui.tools import colorpicker
        app.store.app.recent_colors[:] = []
        calls = []

        def fake_region(mode, ts, cb):
            calls.append(mode)
            cb(SimpleNamespace(color="#FF336699", point_logical=(12, 34), image=None))
        app.run_region = fake_region
        win = colorpicker.open(app, ctx_for(app, HotkeyType.ColorPicker))
        yield 0.1
        win.pick_from_screen()
        yield 0.5
        assert calls == [RegionCaptureMode.ScreenColorPicker]
        assert win.color.rgba == (0x33, 0x66, 0x99, 255)
        assert (win.x_entry.get_text(), win.y_entry.get_text()) == ("12", "34")
        assert win.copy_text("Position") == "12, 34"
        assert app.store.app.recent_colors == ["#FF336699"]
        assert win.palette_recent.get_active() is False  # was empty at open → standard palette
        win.palette_recent.set_active(True)
        assert win.palette_colors() == ["#336699FF"]
        # clipboard colour
        assert win.pick_from_clipboard()
        assert win.color.rgb == (10, 20, 30) and "rgb(10, 20, 30)" in win.clip_status.get_label()
        # recent list: most recent first, deduplicated, capped
        for i in range(40):
            colorpicker.add_recent(app, colorpicker.Color(i, 0, 0))
        colorpicker.add_recent(app, colorpicker.Color(5, 0, 0))
        rc = app.store.app.recent_colors
        assert len(rc) == colorpicker.RECENT_MAX and rc[0] == "#FF050000" and rc.count("#FF050000") == 1
        # closing with a changed colour remembers it
        win.close()
        yield 0.1
        assert app.store.app.recent_colors[0] == "#FF0A141E"
        assert colorpicker.open(app, ctx_for(app, HotkeyType.ColorPicker)) is not win
        return True
    assert run_app(body)


def test_box_and_slider_styles():
    def body(app):
        from sharel.ui.tools import colorpicker
        win = colorpicker.open_color_picker(app, "#FF0000")
        yield 0.2
        assert win.color.rgb == (255, 0, 0)
        # Hue style: slider = hue, box = saturation × brightness
        win.pick_slider(1 / 3)
        assert win.color.rgb == (0, 255, 0)
        win.pick_box(1.0, 0.5)
        assert win.color.rgb == (0, 128, 0)
        # hue survives a trip through black
        win.pick_box(0.5, 0.0)
        assert win.color.rgb == (0, 0, 0) and round(win.spins["Hue"].get_value()) == 120
        win.pick_box(1.0, 1.0)
        assert win.color.rgb == (0, 255, 0)
        # Red style: slider = red, box x = blue, y = green
        win.radios["Red"].set_active(True)
        assert win.style == "Red"
        win.pick_slider(1.0)
        assert win.color.rgb == (255, 255, 0)
        win.pick_box(1.0, 0.0)
        assert win.color.rgb == (255, 0, 255)
        img = colorpicker.render_box("Red", win.color, 16)
        assert img.getpixel((0, 15)) == (255, 0, 0) and img.getpixel((15, 0)) == (255, 255, 255)
        sl = colorpicker.render_slider("Hue", win.color, 12, 4)
        assert sl.getpixel((0, 11)) == (255, 0, 0)
        # every style draws without error
        for st in colorpicker.STYLES:
            win.radios[st].set_active(True)
            yield 0.02
        # old colour restore through the preview
        win.old_color = colorpicker.Color(1, 2, 3)
        win.set_color(win.old_color)
        assert win.hex_entry.get_text() == "010203"
        return True
    assert run_app(body)
