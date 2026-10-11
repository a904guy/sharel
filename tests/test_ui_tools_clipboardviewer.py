"""Clipboard content viewer: shows text / image / files from clipboard.read(), Upload routes to
app.clipboard_upload with that content, "Don't show this window again" turns the viewer off."""
from PIL import Image
from toolhelpers import ctx_for, wait_until
from uihelpers import labels, run_app

from sharel.core import clipboard
from sharel.core.enums import HotkeyType


def test_upload_viewer_shows_text_and_uploads(monkeypatch):
    content = clipboard.ClipboardContent("text", text="hello clipboard", mime="text/plain")
    monkeypatch.setattr(clipboard, "read", lambda: content)

    def body(app):
        from sharel.ui.tools.clipboardviewer import open_clipboard_viewer
        calls = []
        app.clipboard_upload = lambda ts, c=None: calls.append((ts, c))
        ctx = ctx_for(app, HotkeyType.ClipboardUploadWithContentViewer)
        win = open_clipboard_viewer(app, ctx, upload=True)
        yield from wait_until(lambda: win.content is not None)
        assert win.heading.get_label() == "Clipboard content: Text (Length: 15)"
        assert win.preview.get_visible_child_name() == "text"
        buf = win.text.get_buffer()
        assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) == "hello clipboard"
        assert win.upload_button.get_sensitive()
        win.dont_show.set_active(True)
        assert app.store.app.show_clipboard_content_viewer is False
        win.dont_show.set_active(False)
        assert app.store.app.show_clipboard_content_viewer is True
        win.dont_show.set_active(True)
        win.upload_button.emit("clicked")
        assert calls == [(ctx.task_settings, content)]
        yield 0.1
        assert win not in app.get_windows()
        # with the viewer switched off, the job uploads straight away
        app.job_ClipboardUploadWithContentViewer(ctx)
        assert len(calls) == 2
        return True
    assert run_app(body)


def test_job_opens_viewer_and_cancel(monkeypatch):
    monkeypatch.setattr(clipboard, "read", lambda: clipboard.ClipboardContent("empty"))

    def body(app):
        calls = []
        app.clipboard_upload = lambda ts, c=None: calls.append(c)
        app.job_ClipboardUploadWithContentViewer(ctx_for(app, HotkeyType.ClipboardUploadWithContentViewer))
        from sharel.ui.tools._base import existing
        win = existing("clipboardviewer-upload")
        assert win is not None
        yield from wait_until(lambda: win.content is not None)
        assert "Clipboard is empty" in win.heading.get_label()
        assert not win.upload_button.get_sensitive()
        win.cancel_button.emit("clicked")
        yield 0.1
        assert existing("clipboardviewer-upload") is None and calls == []
        return True
    assert run_app(body)


def test_viewer_image_files_and_formats(monkeypatch):
    img = Image.new("RGBA", (40, 30), (0, 128, 255, 255))
    state = {"c": clipboard.ClipboardContent("image", image=img, mime="image/png")}
    monkeypatch.setattr(clipboard, "read", lambda: state["c"])
    monkeypatch.setattr(clipboard, "mimetypes", lambda: ["image/png", "text/plain"])
    import io
    png = io.BytesIO()
    img.save(png, "PNG")
    data = {"image/png": png.getvalue(), "text/plain": b"raw text"}
    monkeypatch.setattr(clipboard, "get_data", lambda m: data.get(m))

    def body(app):
        from sharel.ui.tools import clipboardviewer
        win = clipboardviewer.open(app, ctx_for(app, HotkeyType.ClipboardViewer))
        yield from wait_until(lambda: win.content is not None and win.formats)
        assert win.heading.get_label() == "Clipboard content: Image (Size: 40x30)"
        assert win.preview.get_visible_child_name() == "image"
        assert win.picture.get_paintable().get_width() == 40
        yield from wait_until(lambda: "image/png:" in win.raw_info.get_label()
                              and "reading" not in win.raw_info.get_label())
        assert win.raw_stack.get_visible_child_name() == "image"
        win.format_list.select_row(win.format_list.get_row_at_index(1))
        yield from wait_until(lambda: win.raw_info.get_label().startswith("text/plain: 8"))
        b = win.raw_text.get_buffer()
        assert b.get_text(b.get_start_iter(), b.get_end_iter(), False) == "raw text"
        # files + Refresh
        state["c"] = clipboard.ClipboardContent("files", files=["/tmp/a.txt", "/tmp/b.png"])
        win.content = None
        win.refresh_button.emit("clicked")
        yield from wait_until(lambda: win.content is not None)
        assert win.heading.get_label() == "Clipboard content: File (Count: 2)"
        assert "/tmp/b.png" in labels(win.file_list)
        return True
    assert run_app(body)
