"""Image tool windows (beautifier, viewer, batch tools, combiner, splitter, comparer, icons, background remover,
metadata, analyze) opened through their ``open(app, ctx)`` and driven in the UI sandbox."""
from pathlib import Path

from PIL import Image
from uihelpers import button_with_label, labels, run_app

from sharel.core.enums import AfterCaptureTasks, ContentAlignment, EImageFormat, HotkeyType
from sharel.core.jobs import JobContext


def _wait(cond, steps=300, dt=0.05):
    for _ in range(steps):
        if cond():
            return
        yield dt
    raise AssertionError("condition not reached")


def _ctx(app, job, files=()):
    return JobContext(job, app.store.task_settings_for(None), files=[str(f) for f in files])


def _img(path, size=(60, 40), color=(255, 0, 0, 255), fmt=None):
    Image.new("RGBA", size, color).save(path, fmt)
    return path


# ---- beautifier --------------------------------------------------------------------------------

def test_beautifier_preview_save_copy_upload_and_set_default(tmp_path, monkeypatch):
    src = _img(tmp_path / "shot.png", (50, 30), (10, 200, 30, 255))
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_image", lambda img: copied.append(img) or "t")

    def body(app):
        from sharel.ui.tools import beautifier
        uploads = []
        app.tasks.upload_data = lambda data, name, ts: uploads.append(name)
        win = beautifier.open(app, _ctx(app, HotkeyType.ImageBeautifier, [src]))
        assert beautifier.open(app, _ctx(app, HotkeyType.ImageBeautifier)) is win
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None)
        # defaults: margin 80 + padding 40 on each side, smart padding crops nothing on a uniform image?
        assert win.result.size[0] > 50
        win.rows["margin"].set_value(10)
        win.rows["padding"].set_value(5)
        win.rows["smart_padding"].set_active(False)
        win.rows["rounded_corner"].set_value(0)
        win.rows["shadow_opacity"].set_value(0)
        win.rows["background_type"].set_selected(1)  # Color
        assert win.options.background_type == "Color"
        assert win.rows["background_color"].get_visible() and not win.rows["background_gradient"].get_visible()
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None
                         and win.result.size == (50 + 30, 30 + 30))
        assert win.result.getpixel((2, 2)) == (0x22, 0x22, 0x22, 255)  # background colour
        assert win.result.getpixel((40, 30)) == (10, 200, 30, 255)
        out = tmp_path / "out.png"
        win.save_to(str(out))
        yield from _wait(out.exists)
        assert Image.open(out).size == (80, 60)
        win.copy_button.emit("clicked")
        win.upload_button.emit("clicked")
        yield from _wait(lambda: copied and uploads)
        assert copied[0].size == (80, 60) and uploads == ["out.png"]
        win.default_button.emit("clicked")
        saved = app.store.app.default_task_settings.image_settings.beautifier
        assert saved["margin"] == 10 and saved["padding"] == 5 and saved["background_type"] == "Color"
        # the after-capture BeautifyImage task now uses those defaults
        ts = app.store.task_settings_for(None)
        ts.after_capture_job = AfterCaptureTasks.BeautifyImage | AfterCaptureTasks.SaveImageToFile
        t = app.tasks.run_image(Image.new("RGBA", (50, 30), (10, 200, 30, 255)), ts)
        yield from _wait(t.done.is_set)
        assert Image.open(t.info.file_path).size == (80, 60)
        win.reset_button.emit("clicked")
        assert win.options.margin == 80
        return True
    assert run_app(body)


# ---- viewer ------------------------------------------------------------------------------------

def test_image_viewer_navigates_folder_zooms_copies_and_pins(tmp_path, monkeypatch):
    for i, c in enumerate(["red", "green", "blue"]):
        _img(tmp_path / f"{i}.png", (30 + i * 10, 20), c)
    (tmp_path / "notes.txt").write_text("x")
    copied, pinned, edited = [], [], []
    monkeypatch.setattr("sharel.core.clipboard.set_image", lambda img: copied.append(img) or "t")
    import sys
    import types
    pin_mod = types.ModuleType("sharel.ui.windows.pin")
    pin_mod.pin_image = lambda app, img, **kw: pinned.append(img)
    pin_mod.close_all_pins = lambda: None
    ed_mod = types.ModuleType("sharel.ui.windows.editor")
    ed_mod.open_editor_for_file = lambda app, path: edited.append(path)
    monkeypatch.setitem(sys.modules, "sharel.ui.windows.pin", pin_mod)
    monkeypatch.setitem(sys.modules, "sharel.ui.windows.editor", ed_mod)

    def body(app):
        from sharel.ui.tools import imageviewer
        win = imageviewer.open(app, _ctx(app, HotkeyType.ImageViewer, [tmp_path / "1.png"]))
        yield 0.1
        assert [Path(p).name for p in win.images] == ["0.png", "1.png", "2.png"]
        assert win.index == 1 and win.image.size == (40, 20)
        assert "1.png — 40 × 20" in win.status.get_label() and "2 / 3" in win.status.get_label()
        win.next_button.emit("clicked")
        assert win.image.size == (50, 20) and not win.next_button.get_sensitive()
        win.prev_button.emit("clicked")
        win.prev_button.emit("clicked")
        assert win.index == 0 and not win.prev_button.get_sensitive()
        win.actual_button.emit("clicked")
        assert win.zoom == 1.0 and win.picture.get_size_request() == (30, 20)
        win.zoom_in_button.emit("clicked")
        assert win.zoom == 1.25
        win.fit_button.emit("clicked")
        assert win.zoom is None
        win.copy_button.emit("clicked")
        win.pin_button.emit("clicked")
        win.edit_button.emit("clicked")
        assert copied[0].getpixel((0, 0))[:3] == (255, 0, 0)
        assert pinned[0].size == (30, 20) and edited == [str(tmp_path / "0.png")]
        from gi.repository import Gtk
        # fits a laptop screen: the header's buttons are icons (named for assistive tech and tooltips)
        assert win.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= 1366
        assert win.zoom_in_button.get_tooltip_text() == "Zoom in" and win.zoom_in_button._label == "Zoom in"
        return True
    assert run_app(body)


# ---- batch tools --------------------------------------------------------------------------------

def test_thumbnailer_writes_jpegs_with_tool_defaults(tmp_path, monkeypatch):
    a = _img(tmp_path / "a.png", (400, 200))
    b = _img(tmp_path / "b.png", (100, 300), (0, 0, 255, 255))
    out = tmp_path / "out"
    out.mkdir()
    opened = []
    monkeypatch.setattr("sharel.core.desktop.open_folder", lambda p: opened.append(p))

    def body(app):
        from sharel.ui.tools import thumbnailer
        app.store.app.default_task_settings.tool_settings.image_thumbnailer_width = 100
        win = thumbnailer.open(app, _ctx(app, HotkeyType.ImageThumbnailer, [a, b]))
        assert win.opts.width == 100 and win.files.paths == [str(a), str(b)]
        win.height_row.set_value(0)
        win.quality_row.set_value(80)
        win.output.set_text(str(out))
        assert win.run_button.get_sensitive()
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert sorted(p.name for p in out.iterdir()) == ["a_th.jpg", "b_th.jpg"]
        assert Image.open(out / "a_th.jpg").size == (100, 50)
        assert Image.open(out / "b_th.jpg").size == (100, 300)
        assert Image.open(out / "a_th.jpg").format == "JPEG"
        assert "Created a_th.jpg" in win.status.get_label()
        assert app.store.app.default_task_settings.tool_settings.image_thumbnailer_quality == 80
        win.output.open_button.emit("clicked")
        assert opened == [str(out)]
        return True
    assert run_app(body)


def test_resizer_modes_formats_and_preview(tmp_path):
    a = _img(tmp_path / "a.png", (200, 100), (0, 255, 0, 255))

    def body(app):
        from sharel.ui.tools import resizer
        win = resizer.open(app, _ctx(app, HotkeyType.ImageResizer, [a]))
        win.width_row.set_value(50)
        win.height_row.set_value(50)
        win.mode_row.set_selected(resizer.MODES.index("Fit"))
        win.files.select(0)
        yield from _wait(lambda: win.preview.idle.is_set() and win.preview_image is not None
                         and win.preview_image.size == (50, 50))
        assert win.preview_image.getpixel((25, 2))[3] == 0  # letterbox is transparent in PNG
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        res = tmp_path / "a_resized.png"
        im = Image.open(res).convert("RGBA")
        assert im.size == (50, 50) and im.getpixel((25, 25)) == (0, 255, 0, 255) and im.getpixel((25, 2))[3] == 0
        win.mode_row.set_selected(resizer.MODES.index("Fill"))
        win.format_row.set_selected(1)  # JPEG
        assert win.quality_row.get_visible()
        win.name_row.set_text("$filename-small")
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        im = Image.open(tmp_path / "a-small.jpg")
        assert im.size == (50, 50) and im.format == "JPEG"
        win.mode_row.set_selected(resizer.MODES.index("Stretch"))
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert app.store.app.default_task_settings.tool_settings.image_resizer_keep_aspect is False
        assert app.store.app.default_task_settings.tool_settings.image_resizer_width == 50
        return True
    assert run_app(body)


def test_converter_converts_batch_and_cancel(tmp_path):
    files = [_img(tmp_path / f"{i}.png", (20, 20), (0, 0, 0, 128)) for i in range(3)]

    def body(app):
        from sharel.ui.tools import converter
        app.store.app.default_task_settings.tool_settings.image_converter_format = EImageFormat.JPEG
        win = converter.open(app, _ctx(app, HotkeyType.ImageConverter, files))
        assert win.opts.fmt == "JPEG" and win.background_row.get_visible()
        win.files.select(0)
        yield from _wait(lambda: win.preview.idle.is_set() and win.preview_image is not None)
        assert "as JPEG" in win.preview_pane.caption.get_label()
        win.format_row.set_selected(converter.FORMATS.index("WEBP"))
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert sorted(p.name for p in tmp_path.glob("*.webp")) == ["0.webp", "1.webp", "2.webp"]
        assert Image.open(tmp_path / "0.webp").format == "WEBP"
        assert app.store.app.default_task_settings.tool_settings.image_converter_format == EImageFormat.WEBP
        # cancel: the batch stops before the remaining files
        win.format_row.set_selected(converter.FORMATS.index("BMP"))
        win.run_button.emit("clicked")
        win.batch.cancel_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert win.cancelled and len(list(tmp_path.glob("*.bmp"))) < 3
        return True
    assert run_app(body)


def test_watermark_text_and_image(tmp_path):
    a = _img(tmp_path / "a.png", (200, 100), (0, 0, 0, 255))
    mark = _img(tmp_path / "mark.png", (10, 10), (255, 0, 0, 255))

    def body(app):
        from sharel.ui.tools import watermark
        win = watermark.open(app, _ctx(app, HotkeyType.ImageWatermark, [a]))
        assert win.opts.text == "ShareL" and win.opts.position == ContentAlignment.BottomRight
        win.opacity_row.set_value(100)
        win.size_row.set_value(30)
        win.margin_row.set_value(0)
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        out = Image.open(tmp_path / "a_watermarked.png").convert("RGBA")
        br = out.crop((100, 50, 200, 100)).getextrema()
        assert br[0][1] > 200  # white text in the bottom right
        assert out.crop((0, 0, 100, 50)).getextrema()[0][1] == 0  # top left untouched
        win.type_row.set_selected(1)
        win.image_row.set_text(str(mark))
        win.scale_row.set_value(0)
        win.position_row.set_selected(0)  # top left
        win.name_row.set_text("$filename_img")
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        out = Image.open(tmp_path / "a_img.png").convert("RGBA")
        assert out.getpixel((5, 5)) == (255, 0, 0, 255) and out.getpixel((15, 15)) == (0, 0, 0, 255)
        assert app.store.app.default_task_settings.tool_settings.image_watermark_position == ContentAlignment.TopLeft
        return True
    assert run_app(body)


def test_strip_metadata_overwrites_after_confirmation_and_reports(tmp_path, monkeypatch):
    a = tmp_path / "photo.jpg"
    img = Image.new("RGB", (30, 20), (9, 9, 9))
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"
    exif[0x0110] = "Model X"
    img.save(a, exif=exif.tobytes())
    monkeypatch.setattr("sharel.ui.gtkutil.ask", lambda parent, h, b, responses, on_done, **kw: on_done("strip"))

    def body(app):
        from sharel.tools.metadata import read_metadata
        from sharel.ui.tools import stripmetadata
        assert len(read_metadata(a).image.exif) == 2
        win = stripmetadata.open(app, _ctx(app, HotkeyType.StripMetadata, [a]))
        assert win.opts.overwrite and not win.name_row.get_visible()
        win.run_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert read_metadata(a).image.exif == []
        assert win.results[0].removed == 2
        assert "photo.jpg: removed 2 metadata entries" in win.status.get_label()
        return True
    assert run_app(body)


# ---- combiner / splitter ---------------------------------------------------------------------------

def test_combiner_orders_previews_and_runs_after_capture_tasks(tmp_path, monkeypatch):
    a = _img(tmp_path / "a.png", (30, 20), (255, 0, 0, 255))
    b = _img(tmp_path / "b.png", (10, 10), (0, 0, 255, 255))
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_image", lambda img: copied.append(img) or "t")

    def body(app):
        from sharel.ui.tools import combiner
        win = combiner.open(app, _ctx(app, HotkeyType.ImageCombiner, [a, b]))
        yield from _wait(lambda: win.preview.idle.is_set() and win.result is not None)
        assert win.result.size == (30, 30)  # vertical by default
        win.orientation_row.set_selected(1)
        win.space_row.set_value(5)
        win.autofill_row.set_active(False)
        yield from _wait(lambda: win.preview.idle.is_set() and win.result.size == (45, 20))
        assert win.result.getpixel((0, 0)) == (255, 0, 0, 255)
        assert win.result.getpixel((40, 15))[3] == 0  # below the 10 px image: transparent
        win.files.select(1)
        win.files.up_button.emit("clicked")
        assert [Path(p).name for p in win.files.paths] == ["b.png", "a.png"]
        yield from _wait(lambda: win.preview.idle.is_set() and win.result.getpixel((0, 0)) == (0, 0, 255, 255))
        win.copy_button.emit("clicked")
        assert copied and copied[0].size == (45, 20)
        out = tmp_path / "comb.png"
        win.save_to(str(out))
        assert Image.open(out).size == (45, 20)
        ts = win.ctx.task_settings
        ts.after_capture_job = AfterCaptureTasks.SaveImageToFile
        win.combine_button.emit("clicked")
        t = win.last_task
        yield from _wait(t.done.is_set)
        assert Image.open(t.info.file_path).size == (45, 20)
        tools = app.store.app.default_task_settings.tool_settings
        assert tools.image_combiner_orientation == "Horizontal" and tools.image_combiner_space == 5
        return True
    assert run_app(body)


def test_splitter_rows_columns_and_tile_size(tmp_path):
    a = _img(tmp_path / "big.png", (100, 60), (0, 128, 0, 255))
    out = tmp_path / "tiles"
    out.mkdir()

    def body(app):
        from sharel.ui.tools import splitter
        win = splitter.open(app, _ctx(app, HotkeyType.ImageSplitter, [a]))
        assert win.rows_n == 2 and win.columns_n == 2  # tool defaults
        win.columns_row.set_value(4)
        win.rows_row.set_value(3)
        assert win.size_label.get_subtitle() == "25 × 20 px, 12 tiles"
        win.output.set_text(str(out))
        win.split_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        names = sorted(p.name for p in out.iterdir())
        assert len(names) == 12 and "big_3_4.png" in names
        assert Image.open(out / "big_1_1.png").size == (25, 20)
        assert "Saved 12 tiles" in win.status.get_label()
        win.mode_row.set_selected(1)
        win.tile_w_row.set_value(40)
        win.tile_h_row.set_value(40)
        assert win.size_label.get_subtitle() == "40 × 40 px, 6 tiles"
        win.output.set_text("")
        win.split_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert Image.open(tmp_path / "big_2_3.png").size == (20, 20)
        assert app.store.app.default_task_settings.tool_settings.image_splitter_columns == 4
        return True
    assert run_app(body)


# ---- comparer / icons / background remover ------------------------------------------------------------

def test_comparer_similarity_and_views(tmp_path, monkeypatch):
    a = _img(tmp_path / "a.png", (10, 10), (0, 0, 0, 255))
    im = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    im.putpixel((9, 9), (255, 255, 255, 255))
    im.save(tmp_path / "b.png")

    def body(app):
        from sharel.ui.tools import comparer
        win = comparer.open(app, _ctx(app, HotkeyType.ImageComparer, [a, tmp_path / "b.png"]))

        def settled():
            return _wait(lambda: win.preview.idle.is_set() and win.view is not None)
        yield from settled()
        assert win.similarity.get_label() == "Similarity: 99.00%"
        assert "1 of 100 pixels differ" in win.details.get_label()
        assert win.view.size == (10, 10)
        win.mode_drop.set_selected(1)
        yield from settled()
        assert win.view.getpixel((9, 9)) == (255, 255, 255) and win.view.getpixel((0, 0)) == (0, 0, 0)
        win.mode_drop.set_selected(2)
        yield from settled()
        assert win.view.getpixel((9, 9)) == (255, 0, 0)
        win.mode_drop.set_selected(0)
        win.scale.set_value(100)
        yield from settled()
        assert win.view.getpixel((9, 9)) == (0, 0, 0, 255)  # all of the first image
        win.scale.set_value(0)
        yield from settled()
        assert win.view.getpixel((9, 9)) == (255, 255, 255, 255)
        out = tmp_path / "view.png"
        win.save_to(str(out))
        assert out.exists()
        win.swap_button.emit("clicked")
        assert Path(win.paths[0]).name == "b.png"
        win.set_image(1, str(tmp_path / "b.png"))
        yield from settled()
        assert win.similarity.get_label() == "Similarity: 100.00% (identical)"
        return True
    assert run_app(body)


def test_icon_converter_png_to_ico_and_back(tmp_path):
    src = _img(tmp_path / "logo.png", (64, 40), (0, 200, 0, 255))

    def body(app):
        from sharel.tools.imagetools import ico_frames
        from sharel.ui.tools import iconconverter
        win = iconconverter.open(app, _ctx(app, HotkeyType.IconConverter, [src]))
        assert "64 × 40" in win.source_row.get_subtitle()
        win.size_checks[128].set_active(False)
        win.size_checks[256].set_active(False)
        assert win.selected_sizes() == [16, 32, 48, 64]
        out = win.convert_to(str(tmp_path / "logo.ico"))
        assert sorted(f.size for f in ico_frames(out)) == [(16, 16), (32, 32), (48, 48), (64, 64)]
        for cb in win.size_checks.values():
            cb.set_active(False)
        assert not win.convert_button.get_sensitive()
        win.load(str(out))
        assert win.is_ico and win.convert_button.get_label() == "Convert to PNG…"
        png = win.convert_to(str(tmp_path / "back.png"))
        assert Image.open(png).size == (64, 64)
        yield 0
        return True
    assert run_app(body)


def test_background_remover_with_stubbed_model(tmp_path, monkeypatch):
    src = _img(tmp_path / "photo.png", (20, 20), (90, 90, 90, 255))
    calls = []

    def fake_remove(img, model="u2netp", alpha_matting=False, **kw):
        calls.append((model, alpha_matting))
        out = img.convert("RGBA")
        out.putpixel((0, 0), (0, 0, 0, 0))
        return out
    monkeypatch.setattr("sharel.tools.bgremove.is_available", lambda: True)
    monkeypatch.setattr("sharel.tools.bgremove.remove_background", fake_remove)
    opened = []
    monkeypatch.setattr("sharel.core.desktop.open_folder", lambda p: opened.append(p))

    def body(app):
        from sharel.ui.tools import bgremove
        win = bgremove.open(app, _ctx(app, HotkeyType.BackgroundRemover, [src]))
        assert win.available and win.remove_button.get_sensitive() and not win.save_button.get_sensitive()
        win.model_row.set_selected(bgremove.MODELS.index("u2net"))
        win.matting_row.set_active(True)
        win.remove_button.emit("clicked")
        yield from _wait(win.batch.finished.is_set)
        assert calls == [("u2net", True)] and win.result.getpixel((0, 0))[3] == 0
        win.save_button.emit("clicked")
        out = tmp_path / "photo-nobg.png"
        assert Image.open(out).convert("RGBA").getpixel((0, 0))[3] == 0
        win.open_models_folder()
        assert opened and opened[0].endswith("rembg")
        return True
    assert run_app(body)


def test_background_remover_without_rembg_disables_removal(tmp_path, monkeypatch):
    monkeypatch.setattr("sharel.tools.bgremove.is_available", lambda: False)
    src = _img(tmp_path / "p.png")

    def body(app):
        from sharel.ui.tools import bgremove
        win = bgremove.open(app, _ctx(app, HotkeyType.BackgroundRemover, [src]))
        assert win.banner.get_revealed() and not win.remove_button.get_sensitive()
        assert win.source is not None
        yield 0
        return True
    assert run_app(body)


# ---- metadata / analyze ---------------------------------------------------------------------------

def _exif_jpeg(path):
    img = Image.new("RGB", (32, 24), (200, 10, 10))
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"
    exif[0x0110] = "Model X"
    img.save(path, exif=exif.tobytes())
    return path


def test_metadata_viewer_rows_search_copy_and_strip(tmp_path, monkeypatch):
    p = _exif_jpeg(tmp_path / "cam.jpg")
    texts = []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: texts.append(t) or "t")
    monkeypatch.setattr("sharel.ui.gtkutil.ask", lambda parent, h, b, responses, on_done, **kw: on_done("strip"))

    def body(app):
        from sharel.ui.tools import metadata
        win = metadata.open(app, _ctx(app, HotkeyType.Metadata, [p]))
        keys = {(s, k): v for s, k, v in win.visible_entries}
        assert keys[("Image", "Dimensions")] == "32 x 24"
        assert keys[("EXIF (Image)", "Make")] == "CameraMaker"
        assert "CameraMaker" in labels(win)
        win.search.set_text("model")
        yield 0.4  # search-changed is debounced by GTK
        assert [k for _s, k, _v in win.visible_entries] == ["Model"]
        win.copy_all_button.emit("clicked")
        assert texts[-1] == "EXIF (Image): Model: Model X"
        win.search.set_text("")
        yield 0.4
        win.strip_button.emit("clicked")
        assert not any(s.startswith("EXIF") for s, _k, _v in win.entries)
        assert win.last_message == "Metadata removed"
        return True
    assert run_app(body)


def test_analyze_tool_shows_dimensions_colors_hashes_and_exif(tmp_path, monkeypatch):
    p = _exif_jpeg(tmp_path / "cam.jpg")
    texts = []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: texts.append(t) or "t")

    def body(app):
        import hashlib

        from sharel.ui.tools import analyze
        win = analyze.open(app, _ctx(app, HotkeyType.AnalyzeImage, [p]))
        yield from _wait(win.done.is_set)
        a = win.analysis
        assert a.value("General", "Dimensions") == "32 × 24 px"
        assert a.value("General", "Format") == "JPEG"
        assert a.value("General", "File size").endswith(f"({p.stat().st_size:,} bytes)")
        assert a.value("Hashes", "SHA-256 (file)") == hashlib.sha256(p.read_bytes()).hexdigest()
        assert a.value("Transparency", "Alpha channel") == "No"
        assert a.value("EXIF", "Make") == "CameraMaker"
        assert a.dominant and a.dominant[0][1] > 90
        r, g, b = (int(a.dominant[0][0][i:i + 2], 16) for i in (1, 3, 5))
        assert r > 180 and g < 40 and b < 40
        assert "32 × 24 px" in labels(win)
        win.copy_button.emit("clicked")
        assert texts and "[General]" in texts[-1] and "Dimensions: 32 × 24 px" in texts[-1]
        return True
    assert run_app(body)


def test_after_capture_analyze_image_opens_window(tmp_path):
    def body(app):
        from sharel.ui.tools import analyze
        ts = app.store.task_settings_for(None)
        ts.after_capture_job = AfterCaptureTasks.SaveImageToFile | AfterCaptureTasks.AnalyzeImage
        img = Image.new("RGBA", (16, 8), (0, 0, 255, 0))
        img.putpixel((0, 0), (0, 0, 255, 255))
        t = app.tasks.run_image(img, ts)
        yield from _wait(lambda: analyze.open_windows())
        win = analyze.open_windows()[0]
        yield from _wait(win.done.is_set)
        assert t.done.wait(5)
        assert win.analysis.value("General", "Dimensions") == "16 × 8 px"
        assert win.analysis.value("General", "Format") == "PNG"
        assert win.analysis.value("Transparency", "Transparent pixels") == "127 (99.22%)"
        assert win.path == t.info.file_path
        return True
    assert run_app(body)


def test_every_tool_window_job_opens(tmp_path):
    """Each job in TOOL_WINDOWS that belongs to the image tools dispatches to a window."""
    from sharel.ui.jobs.tools import TOOL_WINDOWS
    mine = ["ImageBeautifier", "ImageEffects", "ImageViewer", "ImageCombiner", "ImageSplitter", "ImageThumbnailer",
            "BackgroundRemover", "ImageComparer", "IconConverter", "ImageResizer", "ImageConverter",
            "ImageWatermark", "Metadata", "StripMetadata", "AnalyzeImage"]
    p = _img(tmp_path / "x.png")

    def body(app):
        from gi.repository import Adw
        for job in mine:
            assert job in TOOL_WINDOWS
            before = set(app.get_windows())
            getattr(app, f"job_{job}")(_ctx(app, HotkeyType[job], [p]))
            yield 0.05
            new = [w for w in app.get_windows() if w not in before]
            assert new and all(isinstance(w, Adw.Window) for w in new), job
            assert button_with_label(new[0], "Cancel") is not None or new[0].get_title(), job
        return True
    assert run_app(body)


def test_spin_rows_are_in_the_accessibility_tree():
    """Adw.SpinRow defaults to the "presentation" role, which hides the row and its spin button from
    assistive tech (and RootPilot); spin_row gives it the list-item role of the other rows."""
    def body(app):
        from gi.repository import Adw, Gtk
        from sharel.ui.tools import _base
        assert Adw.SpinRow().get_accessible_role() == Gtk.AccessibleRole.PRESENTATION  # why this is needed
        got = []
        row = _base.spin_row("Width", 1280, 0, 20000, got.append)
        assert row.get_accessible_role() == Gtk.AccessibleRole.LIST_ITEM
        row.set_value(200)
        assert got[-1] == 200
        return True
        yield
    assert run_app(body)


def test_every_spin_row_in_src_sets_an_accessible_role():
    """No Adw.SpinRow(...) may keep the default "presentation" role (hidden from assistive tech)."""
    import ast
    src = Path(__file__).resolve().parents[1] / "src"
    missing = []
    for f in src.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "SpinRow" \
                    and not any(k.arg == "accessible_role" for k in node.keywords):
                missing.append(f"{f.relative_to(src)}:{node.lineno}")
    assert missing == [], missing


def test_generated_form_spin_rows_are_in_the_accessibility_tree():
    def body(app):
        from dataclasses import dataclass

        from gi.repository import Adw, Gtk
        from sharel.ui.forms import Form

        @dataclass
        class Opts:
            delay: int = 3
            scale: float = 1.5
        group = Form(Opts(), lambda: None).group("Options")
        rows = []
        child = group.get_first_child()
        stack = [child] if child else []
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.SpinRow):
                rows.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        assert len(rows) == 2, rows
        assert all(r.get_accessible_role() == Gtk.AccessibleRole.LIST_ITEM for r in rows)
        return True
        yield
    assert run_app(body)
