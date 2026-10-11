"""Directory indexer window: indexes a folder, previews it, Upload calls app.tasks.upload_data, Save as
writes the file, settings changes re-render in the chosen format."""
import json

from toolhelpers import ctx_for, wait_until
from uihelpers import run_app

from sharel.core.enums import HotkeyType, IndexerOutput


def _tree(tmp_path):
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("aaaa")
    (root / "sub" / "b.txt").write_text("bb")
    (root / ".hidden").write_text("x")
    return root


def test_index_preview_and_upload(tmp_path):
    root = _tree(tmp_path)

    def body(app):
        from sharel.ui.tools import indexer
        uploads = []
        app.tasks.upload_data = lambda data, name, ts: uploads.append((data, name, ts))
        ctx = ctx_for(app, HotkeyType.IndexFolder, files=[str(root)])
        win = indexer.open(app, ctx)
        yield from wait_until(lambda: win.result is not None)
        text = win.result.text
        assert "<html>" in text and "a.txt" in text and "b.txt" in text and ".hidden" not in text
        assert win.tabs.get_current_page() == 1
        b = win.preview.get_buffer()
        assert b.get_text(b.get_start_iter(), b.get_end_iter(), False) == text
        assert win.upload_button.get_sensitive() and win.browser_button.get_sensitive()
        win.upload_button.emit("clicked")
        assert len(uploads) == 1
        data, name, ts = uploads[0]
        assert data.decode("utf-8") == text and name == "Index for docs.html" and ts is ctx.task_settings
        yield 0.1
        assert win not in app.get_windows()
        return True
    assert run_app(body)


def test_settings_json_and_save_as(tmp_path):
    root = _tree(tmp_path)
    out = tmp_path / "index.json"

    def body(app):
        from sharel.ui.tools import indexer
        ctx = ctx_for(app, HotkeyType.IndexFolder, files=[str(root)])
        win = indexer.open(app, ctx)
        yield from wait_until(lambda: win.result is not None)
        win.settings.output = IndexerOutput.Json
        win.settings.skip_hidden_files = False
        win._settings_changed()
        assert ctx.task_settings.tool_settings.indexer_output == IndexerOutput.Json
        win.index_button.emit("clicked")
        yield from wait_until(lambda: win.result.extension == "json" and not win.worker.running)
        obj = json.loads(win.result.text)
        assert obj["Name"] == "docs" and {f["Name"] for f in obj["Files"]} == {"a.txt", ".hidden"}
        assert not win.browser_button.get_sensitive()
        copied = []
        import sharel.core.clipboard as cb
        orig = cb.set_text
        cb.set_text = lambda t: copied.append(t)
        try:
            win.copy_button.emit("clicked")
        finally:
            cb.set_text = orig
        assert copied == [win.result.text]
        win.write_to(str(out))
        assert json.loads(out.read_text())["Name"] == "docs"
        return True
    assert run_app(body)
