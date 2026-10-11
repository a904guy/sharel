"""Monitor test window: fullscreen, cycles every pattern, renders them, animates the tearing test, Esc exits."""
from gi.repository import Gdk
from toolhelpers import ctx_for, wait_until
from uihelpers import run_app

from sharel.core.enums import HotkeyType
from sharel.tools import monitortest as mt


def test_cycle_patterns_render_and_exit():
    def body(app):
        from sharel.ui.tools import monitortest
        from sharel.ui.tools._base import existing
        win = monitortest.open(app, ctx_for(app, HotkeyType.MonitorTest))
        yield from wait_until(lambda: win.get_mapped())
        yield from wait_until(lambda: win.is_fullscreen(), timeout=5)
        assert win.mode == "Grayscale"
        assert win.render_image((8, 4)).getpixel((0, 0)) == (255, 255, 255)
        win.gray.set_value(128)
        assert win.render_image((8, 4)).getpixel((3, 3)) == (128, 128, 128)
        seen = [win.mode]
        for _ in range(len(mt.MODES)):
            win._key(None, Gdk.KEY_Right, 0, 0)
            seen.append(win.mode)
        assert seen == [*mt.MODES, "Grayscale"]
        win._key(None, Gdk.KEY_Left, 0, 0)
        assert win.mode == "GradientBands"
        win._scrolled(None, 0, 1)
        assert win.mode == "Grayscale"

        # RGB: sliders and the colour button stay in sync
        win.set_mode("Rgb")
        assert win.stack.get_visible_child_name() == "Rgb"
        win.green.set_value(200)
        assert win.render_image((4, 4)).getpixel((1, 1)) == (255, 200, 0)
        c = Gdk.RGBA()
        c.parse("#0000ff")
        win.rgb_button.set_rgba(c)
        assert (win.settings.red, win.settings.green, win.settings.blue) == (0, 0, 255)

        win.set_mode("Gradient")
        img = win.render_image((10, 50))
        assert img.getpixel((0, 0)) == (255, 255, 255) and img.getpixel((0, 49)) == (0, 0, 0)
        win.grad_dir.set_selected(0)  # horizontal
        img = win.render_image((50, 10))
        assert img.getpixel((0, 5)) == (255, 255, 255) and img.getpixel((49, 5)) == (0, 0, 0)

        win.set_mode("Pattern")
        win.pattern_dd.set_selected(mt.PATTERNS.index("Checkerboard"))
        win.pattern_size.set_value(2)
        img = win.render_image((8, 8))
        assert img.getpixel((0, 0)) == (0, 0, 0) and img.getpixel((2, 0)) == (255, 255, 255)

        win.set_mode("Grid")
        win.grid_spacing.set_value(10)
        img = win.render_image((31, 31))
        assert img.getpixel((10, 5)) == (255, 255, 255) and img.getpixel((5, 5)) == (0, 0, 0)

        # tearing test: animated from the frame clock, bars move with elapsed time
        win.set_mode("Motion")
        assert win._tick_id
        a = win.motion_strip(0.0, 400, 300)
        b = win.motion_strip(0.05, 400, 300)  # 500 px/s * 0.05 s = 25 px
        assert a.size == (400, 1) and a.tobytes() != b.tobytes()
        assert b.getpixel((25, 0)) == a.getpixel((0, 0))
        e0 = win.elapsed
        yield from wait_until(lambda: win.elapsed > e0 + 0.1, timeout=5)  # frame clock drives it
        win.set_mode("Grayscale")
        assert not win._tick_id

        # panel toggles on click / Space
        assert win.panel.get_visible()
        win._key(None, Gdk.KEY_space, 0, 0)
        assert not win.panel.get_visible()
        win.toggle_panel(100, 120)
        assert win.panel.get_visible() and win.panel.get_margin_start() == 100
        yield 0.1
        win._on_key(None, Gdk.KEY_Escape, 0, 0)
        yield 0.1
        assert existing("monitortest") is None
        return True
    assert run_app(body)
