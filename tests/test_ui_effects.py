"""Image Effects window (pipeline + tool mode) driven through its widgets in the UI sandbox."""
import zipfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from PIL import Image
from uihelpers import find, find_all, run_app

from sharel.core.enums import AfterCaptureTasks, HotkeyType
from sharel.core.jobs import JobContext


def _wait(cond, steps=200, dt=0.05):
    for _ in range(steps):
        if cond():
            return
        yield dt
    raise AssertionError("condition not reached")


def _ctx(app, files=()):
    return JobContext(HotkeyType.ImageEffects, app.store.task_settings_for(None), files=list(files))


def test_pipeline_runs_through_effects_window_and_ok_returns_effect_image(tmp_path):
    def body(app):
        from sharel.ui.windows import effects as fxwin
        ts = app.store.task_settings_for(None)
        ts.image_settings.show_image_effects_window_after_capture = True
        ts.image_settings.image_effect_presets = [{"name": "Inv", "effects": [{"type": "Inverse"}]}]
        ts.after_capture_job = AfterCaptureTasks.AddImageEffects | AfterCaptureTasks.SaveImageToFile
        t = app.tasks.run_image(Image.new("RGBA", (40, 30), (255, 0, 0, 255)), ts)
        yield from _wait(lambda: fxwin.open_windows())
        win = fxwin.open_windows()[0]
        assert not win.tool_mode
        assert [p.name for p in win.presets] == ["Inv"]
        # Add a Canvas effect through the "fx.add" action the Add menu uses, set its margin via the editor.
        win.activate_action("fx.add", GLib.Variant("s", "Canvas"))
        assert [e.type_name for e in win.preset.effects] == ["Inverse", "Canvas"]
        row = win.param_row("margin")
        spins = row.spins
        spins[0].set_value(5)
        spins[2].set_value(5)
        assert win.preset.effects[1].margin == [5, 0, 5, 0]
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None
                         and win.result.size == (50, 30))
        win.ok_button.emit("clicked")
        yield from _wait(lambda: t.done.is_set())
        out = Image.open(t.info.file_path).convert("RGBA")
        assert out.size == (50, 30)
        assert out.getpixel((20, 15)) == (0, 255, 255, 255)  # red inverted
        saved = app.store.app.default_task_settings.image_settings.image_effect_presets
        assert [e["type"] for e in saved[0]["effects"]] == ["Inverse", "Canvas"]
        assert saved[0]["effects"][1]["margin"] == [5, 0, 5, 0]
        return True
    assert run_app(body)


def test_cancel_calls_done_none_and_keeps_config():
    def body(app):
        from sharel.ui.windows.effects import open_effects_window
        ts = app.store.task_settings_for(None)
        got = []
        win = open_effects_window(app, Image.new("RGBA", (10, 10), "blue"), ts, got.append)
        yield 0.1
        assert [p.name for p in win.presets] == ["Default"]  # fresh config: ShareX's default preset
        win.new_preset()
        win.cancel_button.emit("clicked")
        assert got == [None]
        assert app.store.app.default_task_settings.image_settings.image_effect_presets == []
        return True
    assert run_app(body)


def test_effect_list_add_remove_reorder_enable_and_presets(tmp_path):
    def body(app):
        from sharel.ui.windows.effects import EffectRow, open_effects_window
        ts = app.store.task_settings_for(None)
        ts.image_settings.image_effect_presets = [{"name": "P", "effects": []}]
        got = []
        src = Image.new("RGBA", (20, 20), (200, 100, 50, 255))
        win = open_effects_window(app, src, ts, got.append)
        yield 0.1
        for name in ("Inverse", "Grayscale", "Flip"):
            win.activate_action("fx.add", GLib.Variant("s", name))
        rows = win.effect_rows()
        assert [r.effect.type_name for r in rows] == ["Inverse", "Grayscale", "Flip"]
        # reorder: select Flip, move up twice
        win.effect_list.select_row(rows[2])
        assert win.effect_up_button.get_sensitive()
        win.effect_up_button.emit("clicked")
        win.effect_up_button.emit("clicked")
        assert [e.type_name for e in win.preset.effects] == ["Flip", "Inverse", "Grayscale"]
        assert not win.effect_up_button.get_sensitive()
        win.effect_down_button.emit("clicked")
        assert [e.type_name for e in win.preset.effects] == ["Inverse", "Flip", "Grayscale"]
        # remove Flip (selected)
        win.effect_remove_button.emit("clicked")
        assert [e.type_name for e in win.preset.effects] == ["Inverse", "Grayscale"]
        # disable Grayscale with its check box -> preview is the inverse only
        gray_row = next(r for r in win.effect_rows() if r.effect.type_name == "Grayscale")
        assert isinstance(gray_row, EffectRow)
        gray_row.check.set_active(False)
        assert win.preset.effects[1].enabled is False
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None)
        assert win.result.getpixel((5, 5)) == (55, 155, 205, 255)
        # duplicate effect + presets: new / duplicate / rename / move / remove
        win.select_effect(0)
        win.effect_duplicate_button.emit("clicked")
        assert [e.type_name for e in win.preset.effects] == ["Inverse", "Inverse", "Grayscale"]
        win.preset_duplicate_button.emit("clicked")
        assert len(win.presets) == 2 and win.preset_index == 1
        win.preset_name.set_text("Copy")
        assert win.presets[1].name == "Copy"
        win.preset_up_button.emit("clicked")
        assert [p.name for p in win.presets] == ["Copy", "P"]
        win.preset_new_button.emit("clicked")
        assert len(win.presets) == 3 and win.preset.effects == []
        win.preset_remove_button.emit("clicked")
        assert [p.name for p in win.presets] == ["Copy", "P"]
        win.select_preset(0)
        assert win.preset.name == "Copy"
        win.effect_clear_button.emit("clicked")
        assert win.preset.effects == []
        win.select_preset(1)
        win.ok_button.emit("clicked")
        yield from _wait(lambda: got)
        assert got[0].getpixel((5, 5)) == (200, 100, 50, 255)  # inverse twice, grayscale disabled
        cfg = app.store.app.default_task_settings.image_settings
        assert [p["name"] for p in cfg.image_effect_presets] == ["Copy", "P"]
        assert cfg.selected_image_effect_preset == 1
        assert cfg.image_effect_presets[1]["effects"][2]["enabled"] is False
        return True
    assert run_app(body)


def test_sxie_export_and_import_through_window(tmp_path):
    def body(app):
        from sharel.core import paths
        from sharel.ui.windows.effects import open_effects_window
        mark = tmp_path / "mark.png"
        Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(mark)
        ts = app.store.task_settings_for(None)
        ts.image_settings.image_effect_presets = [{"name": "Wm", "effects": [
            {"type": "DrawImage", "image_location": str(mark)}]}]
        win = open_effects_window(app, Image.new("RGBA", (30, 30), "white"), ts, lambda _i: None)
        yield 0.1
        pkg = win.export_package(str(tmp_path / "wm"))
        assert pkg == tmp_path / "wm.sxie"
        with zipfile.ZipFile(pkg) as zf:
            names = zf.namelist()
        assert "Config.json" in names and "Wm/mark.png" in names
        imported = win.import_package(str(pkg))
        assert imported is not None and len(win.presets) == 2 and win.preset is imported
        loc = imported.effects[0].image_location
        assert Path(loc).is_file() and Path(loc).is_relative_to(paths.data_dir() / "ImageEffects")
        assert win.effect_rows()[0].effect.type_name == "DrawImage"
        win.cancel()
        return True
    assert run_app(body)


def test_effects_tool_mode_loads_ctx_file_saves_copies_and_stores_presets_on_close(tmp_path, monkeypatch):
    src = tmp_path / "in.png"
    Image.new("RGBA", (16, 12), (0, 0, 0, 255)).save(src)
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_image", lambda img: copied.append(img) or "test")

    def body(app):
        from sharel.ui.tools import effects
        uploads = []
        app.tasks.upload_data = lambda data, name, ts: uploads.append((name, data))
        win = effects.open(app, _ctx(app, [str(src)]))
        assert effects.open(app, _ctx(app)) is win  # opened twice: same window
        assert win.tool_mode and win.source.size == (16, 12)
        win.preset.effects.clear()
        win.activate_action("fx.add", GLib.Variant("s", "Inverse"))
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None
                         and win.result.getpixel((0, 0)) == (255, 255, 255, 255))
        out = tmp_path / "out.png"
        win.save_image(str(out))
        yield from _wait(lambda: out.exists())
        assert Image.open(out).convert("RGBA").getpixel((3, 3)) == (255, 255, 255, 255)
        win.copy_button.emit("clicked")
        yield from _wait(lambda: copied)
        assert copied[0].getpixel((0, 0)) == (255, 255, 255, 255)
        win.upload_button.emit("clicked")
        yield from _wait(lambda: uploads)
        assert uploads[0][0] == "in.png" and uploads[0][1][:4] == b"\x89PNG"
        win.close()
        yield 0.1
        saved = app.store.app.default_task_settings.image_settings.image_effect_presets
        assert saved and [e["type"] for e in saved[0]["effects"]] == ["Inverse"]
        return True
    assert run_app(body)


def test_param_editor_kinds_render_rows():
    def body(app):
        from sharel.ui.windows.effects import open_effects_window
        ts = app.store.task_settings_for(None)
        ts.image_settings.image_effect_presets = [{"name": "K", "effects": []}]
        win = open_effects_window(app, Image.new("RGBA", (8, 8), "white"), ts, lambda _i: None)
        yield 0.05
        win.activate_action("fx.add", GLib.Variant("s", "DrawText"))
        for name in ("text", "offset", "text_font", "padding", "gradient", "text_color"):
            assert win.param_row(name) is not None, name
        grad = win.param_row("gradient")
        n = len(win.effect.gradient.colors)
        grad.add_stop("#FF00FF00", 30)
        assert len(win.effect.gradient.colors) == n + 1
        grad.remove_stop(0)
        assert len(win.effect.gradient.colors) == n
        win.param_row("text").text_view.get_buffer().set_text("Hello")
        assert win.effect.text == "Hello"
        win.activate_action("fx.add", GLib.Variant("s", "TornEdge"))
        sides = win.param_row("sides")
        sides.switches["Top"].set_active(False)
        assert win.effect.sides == "Bottom, Left, Right"
        assert isinstance(find(win, lambda w: isinstance(w, Gtk.MenuButton)), Gtk.MenuButton)
        assert len(find_all(win.effect_list, lambda w: isinstance(w, Gtk.CheckButton))) == 2
        win.cancel()
        return True
    assert run_app(body)


# -- opening a .sxie package (ShareX's TaskHelpers.ImportImageEffect) ---------------------------------

def _sxie_package(tmp_path, name="Red corner"):
    from sharel.effects import sxie
    from sharel.effects.base import EFFECTS, ImageEffectPreset, load_all
    load_all()  # the effect classes register on import
    logo = tmp_path / "src" / "logo.png"
    logo.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (6, 4), (255, 0, 0, 255)).save(logo)
    preset = ImageEffectPreset(name, [EFFECTS["DrawImage"](image_location=str(logo), placement="BottomRight")])
    pkg = sxie.export_sxie(preset, tmp_path / f"{name}.sxie")
    logo.unlink()  # only the copy inside the package remains
    return pkg


def _open_sxie(tmp_path, answer, enabled_before=False, argv_of=lambda p: [str(p)]):
    import json

    from sharel.cli import parse_args
    from sharel.effects.base import ImageEffectPreset
    from sharel.effects.common import image_effects_dir
    from sharel.ui.windows.effects import open_windows
    ACT = AfterCaptureTasks
    pkg = _sxie_package(tmp_path)

    def body(app):
        uploads = []
        app.upload_paths = lambda paths, ts: uploads.append(list(paths))
        ts = app.store.app.default_task_settings
        ts.after_capture_job = ACT.SaveImageToFile | (ACT.AddImageEffects if enabled_before else ACT(0))
        ts.image_settings.image_effect_presets = [ImageEffectPreset("Existing", []).to_jsonable()]
        ts.image_settings.selected_image_effect_preset = 0
        app.handle_request(parse_args(argv_of(pkg)), str(tmp_path))
        yield 0.3
        assert uploads == [] and app.tasks.tasks == []  # opened, not uploaded
        win = next(w for w in open_windows() if w.tool_mode)
        assert win.get_mapped() and win.preset.name == "Red corner"  # the window shows the imported preset
        # saved to the default task settings (and to disk), its image extracted and linked
        assert [p["name"] for p in ts.image_settings.image_effect_presets] == ["Existing", "Red corner"]
        disk = json.loads((app.store.directory / "ApplicationConfig.json").read_text())
        assert [p["name"] for p in disk["default_task_settings"]["image_settings"]["image_effect_presets"]] == \
            ["Existing", "Red corner"]
        logo = win.preset.effects[0].image_location
        assert logo.startswith(str(image_effects_dir())) and Path(logo).is_file()
        if enabled_before:
            assert win.enable_dialog is None  # nothing to ask
        else:
            assert "enable image effects" in win.enable_dialog.get_body()
            assert ACT.AddImageEffects not in ts.after_capture_job  # not before the answer
            win.enable_dialog.emit("response", answer)
            yield 0.2
            assert (ACT.AddImageEffects in ts.after_capture_job) == (answer == "yes")
            disk = json.loads((app.store.directory / "ApplicationConfig.json").read_text())
            assert ("AddImageEffects" in disk["default_task_settings"]["after_capture_job"]) == (answer == "yes")
        assert ACT.SaveImageToFile in ts.after_capture_job
        win.close()
        yield 0.2
        return True
    assert run_app(body)


def test_opening_a_sxie_imports_the_preset_and_yes_enables_image_effects(tmp_path):
    _open_sxie(tmp_path, "yes")


def test_opening_a_sxie_and_answering_no_keeps_the_after_capture_tasks(tmp_path):
    _open_sxie(tmp_path, "no", argv_of=lambda p: ["-ImageEffect", p.name])  # relative to the caller's directory


def test_opening_a_sxie_when_image_effects_are_on_asks_nothing(tmp_path):
    _open_sxie(tmp_path, None, enabled_before=True)
