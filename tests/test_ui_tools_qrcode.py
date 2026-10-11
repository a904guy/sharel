"""QR code window: encode → decode round trip through the widgets, options, copy/save/upload, every scan
source, and the results window used by the scan jobs and the ScanQRCode after-capture task."""
from types import SimpleNamespace

from PIL import Image
from toolhelpers import ctx_for, wait_until
from uihelpers import run_app

from sharel.core.clipboard import ClipboardContent
from sharel.core.enums import HotkeyType, RegionCaptureMode
from sharel.tools import qr


def test_encode_decode_round_trip_and_actions(monkeypatch, tmp_path):
    images, texts, opened = [], [], []
    monkeypatch.setattr("sharel.core.clipboard.set_image", lambda im: images.append(im) or "test")
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: texts.append(t) or "test")
    monkeypatch.setattr("sharel.core.desktop.open_url", lambda u: opened.append(u))
    monkeypatch.setattr("sharel.core.clipboard.read", lambda: ClipboardContent("text", text="from clipboard"))
    saves = []
    monkeypatch.setattr("sharel.ui.tools.qrcode.save_file",
                        lambda parent, title, suggested, done, patterns: saves.append(suggested) or done(
                            str(tmp_path / "code.png")))

    def body(app):
        from sharel.ui.tools import qrcode
        win = qrcode.open(app, ctx_for(app, HotkeyType.QRCode))
        yield 0.3
        assert win.get_text() == "from clipboard"  # ShareX GenerateQRCodeFromClipboard
        assert qrcode.open(app, ctx_for(app, HotkeyType.QRCode)) is win
        payload = "https://example.com/ShareL?q=ünïcödé"
        win.text.get_buffer().set_text(payload)
        yield from wait_until(lambda: win.qr_image is not None and qr.decode_image(win.qr_image) == [payload])
        win.size.set_value(300)
        assert win.qr_image.size == (300, 300)
        win.ec.set_selected(3)
        assert win.error_correction == "H"
        win.size.set_value(10)  # below ShareX's minimum
        assert win.qr_image.size == (64, 64)
        win.size.set_value(256)
        # copy / save / upload
        win.copy_image_button.emit("clicked")
        assert images and images[-1].size == (256, 256)
        win.save_image_button.emit("clicked")
        assert saves and saves[0].endswith(".png")
        assert qr.decode_file(tmp_path / "code.png") == [payload]
        svg = qrcode.save_qr(str(tmp_path / "code.svg"), payload, win.qr_image, "M")
        from pathlib import Path
        assert "<svg" in Path(svg).read_text()
        uploads = []
        app.tasks.upload_data = lambda data, name, ts: uploads.append((data, name))
        win.upload_button.emit("clicked")
        assert uploads and uploads[0][1].endswith(".png")
        import io
        assert qr.decode_image(Image.open(io.BytesIO(uploads[0][0]))) == [payload]
        # decode the generated code from the clipboard (image) through the Decode tab
        monkeypatch.setattr("sharel.core.clipboard.read", lambda: ClipboardContent("image", image=win.qr_image))
        assert win.scan_clipboard()
        yield from wait_until(lambda: win.decodes == 1)
        assert win.stack.get_visible_child_name() == "decode"
        assert qrcode.text_of(win.decoded) == payload and win.results == [payload]
        assert win.open_url_button.get_sensitive()
        win.open_url_button.emit("clicked")
        assert opened == [payload]
        win.copy_decoded_button.emit("clicked")
        assert texts[-1] == payload
        win.encode_this_button.emit("clicked")
        assert win.stack.get_visible_child_name() == "encode" and win.get_text() == payload
        # over-long content
        win.set_text("x" * 3000)
        assert win.qr_image is None and "Too long" in win.limit_label.get_label()
        assert not win.copy_image_button.get_sensitive()
        return True
    assert run_app(body, timeout=60)


def test_scan_sources(monkeypatch, tmp_path):
    code = qr.encode("screen code", 200)
    screen = Image.new("RGB", (800, 600), "white")
    screen.paste(code, (300, 200))
    from sharel.capture import backends
    monkeypatch.setattr("sharel.capture.backends.capture_screen",
                        lambda cursor=False, preferred="Auto": backends.Capture(screen, (0, 0, 800, 600)))
    f = tmp_path / "file.png"
    qr.encode("file code", 200).save(f)

    def body(app):
        from sharel.editor.manager import RegionResult
        from sharel.ui.tools import qrcode
        win = qrcode.open_qr_window(app, "")
        yield 0.1
        win.scan_screen()
        yield from wait_until(lambda: win.decodes == 1)
        assert win.results == ["screen code"] and win.get_visible()
        modes = []

        def fake_region(mode, ts, cb):
            modes.append(mode)
            cb(SimpleNamespace(image=qr.encode("region code", 160), result=SimpleNamespace(result=RegionResult.Region)))
        app.run_region = fake_region
        win.scan_region()
        yield from wait_until(lambda: win.decodes == 2)
        assert modes == [RegionCaptureMode.Default] and win.results == ["region code"]
        win.scan_file(str(f))
        yield from wait_until(lambda: win.decodes == 3)
        assert win.results == ["file code"]
        # a copied image file on the clipboard
        monkeypatch.setattr("sharel.core.clipboard.read", lambda: ClipboardContent("files", files=[str(f)]))
        assert win.scan_clipboard()
        yield from wait_until(lambda: win.decodes == 4)
        # nothing to decode
        win.scan(Image.new("RGB", (100, 100), "white"))
        yield from wait_until(lambda: win.decodes == 5)
        assert win.results == [] and "No QR code" in win.status.get_label()
        assert not win.open_url_button.get_sensitive()
        # QRCode job with a file argument scans it
        assert qrcode.open(app, ctx_for(app, HotkeyType.QRCode, [str(f)])) is win
        yield from wait_until(lambda: win.decodes == 6)
        assert win.results == ["file code"]
        return True
    assert run_app(body, timeout=60)


def test_results_window_and_jobs(monkeypatch):
    texts, opened = [], []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: texts.append(t) or "test")
    monkeypatch.setattr("sharel.core.desktop.open_url", lambda u: opened.append(u))
    screen = Image.new("RGB", (700, 400), "white")
    screen.paste(qr.encode("https://sharel.app/x", 180), (40, 40))
    screen.paste(qr.encode("plain text", 180), (400, 100))
    from sharel.capture import backends
    monkeypatch.setattr("sharel.capture.backends.capture_screen",
                        lambda cursor=False, preferred="Auto": backends.Capture(screen, (0, 0, 700, 400)))

    def body(app):
        from uihelpers import button_with_label, find_all

        from sharel.core.jobs import dispatch
        from sharel.ui.tools import qrcode
        dispatch(app, HotkeyType.QRCodeDecodeFromScreen)
        yield 0.3
        win = qrcode.existing("qrresults")
        assert win is not None and sorted(win.results) == ["https://sharel.app/x", "plain text"]
        assert "2 codes found" in win.status.get_label()
        opens = [b for b in find_all(win, lambda w: isinstance(w, qrcode.Gtk.Button))
                 if (b.get_tooltip_text() or "") == "Open this link"]
        assert len(opens) == 1
        opens[0].emit("clicked")
        assert opened == ["https://sharel.app/x"]
        button_with_label(win, "Copy all").emit("clicked")
        assert texts[-1] == "\n\n".join(win.results)
        # list input + re-present
        assert qrcode.open_qr_results(app, ["a", "b"], None) is win and win.results == ["a", "b"]
        # QRCodeScanRegion job uses the region overlay
        from sharel.editor.manager import RegionResult
        app.run_region = lambda mode, ts, cb: cb(SimpleNamespace(image=qr.encode("scanned region", 160),
                                                                 result=SimpleNamespace(result=RegionResult.Region)))
        dispatch(app, HotkeyType.QRCodeScanRegion)
        yield 0.3
        assert win.results == ["scanned region"]
        # the ScanQRCode after-capture task path (GtkTaskHost.scan_qr) and ShowQRCode after upload
        info = SimpleNamespace(qr_text="")
        app.tasks.host.scan_qr(qr.encode("pipeline", 160), info)
        yield 0.3
        assert info.qr_text == "pipeline" and win.results == ["pipeline"]
        app.tasks.host.show_qr("https://uploaded.example/abc")
        yield 0.3
        qw = qrcode.existing("qrcode")
        assert qw is not None and qw.get_text() == "https://uploaded.example/abc"
        assert qr.decode_image(qw.qr_image) == ["https://uploaded.example/abc"]
        win.open_in_tool()
        assert qw.get_text() == "pipeline"
        # empty scan
        res = qrcode.open_qr_results(app, "", None)
        assert res.results == [] and "No QR code" in res.status.get_label()
        return True
    assert run_app(body, timeout=60)
