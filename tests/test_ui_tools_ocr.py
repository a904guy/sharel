"""OCR window: recognises Pillow-rendered text, language picker, options re-run and persist, copy options."""
from types import SimpleNamespace

from PIL import Image, ImageDraw
from toolhelpers import ctx_for, wait_until
from uihelpers import run_app

from sharel.core.config import OCROptions
from sharel.core.enums import HotkeyType
from sharel.tools._common import load_font


def text_image(text: str, size: int = 48) -> Image.Image:
    font = load_font(size)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    x0, t, r, b = probe.textbbox((0, 0), text, font=font)
    img = Image.new("RGB", (r - x0 + 40, b - t + 40), "white")
    ImageDraw.Draw(img).text((20 - x0, 20 - t), text, font=font, fill="black")
    return img


def test_ocr_window_recognises_text_and_options(monkeypatch):
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: copied.append(t) or "test")
    opened = []
    monkeypatch.setattr("sharel.core.desktop.open_url", lambda u: opened.append(u))

    def body(app):
        from sharel.ui.tools import ocr
        opts = OCROptions(language="eng", scale_factor=2.0, auto_copy=True)
        win = ocr.open_ocr(app, text_image("HELLO SHAREL 42"), opts)
        yield from wait_until(lambda: win.runs >= 1, 30)
        assert win.languages == ["eng"]
        assert ocr.text_of(win.text).strip() == "HELLO SHAREL 42"
        assert copied == ["HELLO SHAREL 42"]  # auto copy
        # an option change re-runs OCR, edits the passed options and persists into the defaults
        win.single_line.set_active(True)
        assert opts.single_line is True
        assert app.store.app.default_task_settings.capture_settings.ocr.single_line is True
        yield from wait_until(lambda: win.runs >= 2, 30)
        assert "HELLO SHAREL 42" in ocr.text_of(win.text)
        win.scale.set_value(3.0)
        assert opts.scale_factor == 3.0
        win.scale.set_value(1.5)  # supersedes the run started for 3.0
        yield from wait_until(lambda: not win.busy_ocr and win.runs >= 3, 30)
        assert opts.scale_factor == 1.5
        # the result is editable; copy all copies the edited text
        win.text.get_buffer().set_text("edited text")
        win.copy_all()
        assert copied[-1] == "edited text"
        # service links
        win.service.set_selected(1)
        win.open_service()
        assert opened == ["https://www.google.com/search?q=edited%20text"]
        # a new image re-presents the same window and recognises it
        assert ocr.open_ocr(app, text_image("SECOND IMAGE"), opts) is win
        yield from wait_until(lambda: "SECOND" in ocr.text_of(win.text), 30)
        # close after copying
        win.close_after.set_active(True)
        assert opts.close_window_after_copying
        win.copy_all()
        yield 0.1
        assert ocr.existing("ocr") is None
        return True
    assert run_app(body, timeout=90)


def test_ocr_tool_from_file_and_region(tmp_path):
    p = tmp_path / "t.png"
    text_image("FILE TEXT").save(p)

    def body(app):
        from sharel.ui.tools import ocr
        win = ocr.open(app, ctx_for(app, HotkeyType.OCR, [str(p)]))
        yield from wait_until(lambda: win.runs >= 1, 30)
        assert "FILE TEXT" in ocr.text_of(win.text)
        # Select region: the overlay outcome's image is recognised
        from sharel.editor.manager import RegionResult
        app.run_region = lambda mode, ts, cb: cb(SimpleNamespace(image=text_image("REGION TEXT"),
                                                                 result=SimpleNamespace(result=RegionResult.Region)))
        win.select_region()
        yield from wait_until(lambda: "REGION TEXT" in ocr.text_of(win.text), 30)
        assert win.get_visible()
        return True
    assert run_app(body, timeout=90)


def test_ocr_without_tesseract(monkeypatch):
    monkeypatch.setattr("sharel.tools.ocr.is_available", lambda: False)

    def body(app):
        from sharel.ui.tools import ocr
        win = ocr.open_ocr(app, text_image("X"), OCROptions())
        yield 0.1
        assert win.banner.get_revealed() and "not installed" in win.banner.get_title()
        assert not win.language.get_sensitive()
        return True
    assert run_app(body)
