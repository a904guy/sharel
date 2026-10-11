"""Hash checker window: SHA256 of a temp file, match/mismatch indicator, compare two files, text mode."""
import hashlib

from toolhelpers import ctx_for, wait_until
from uihelpers import run_app


def test_hash_file_and_expected_match(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"ShareL hash test\n" * 5000)
    expected = hashlib.sha256(f.read_bytes()).hexdigest()

    def body(app):
        from sharel.ui.tools import hashcheck
        win = hashcheck.open(app, ctx_for(app, files=[str(f)]))
        assert hashcheck.open(app, ctx_for(app)) is win  # re-presented, not duplicated
        yield 0.2
        assert win.selected_type.value == "SHA256"
        assert win.check_button.get_sensitive()
        win.check_button.emit("clicked")
        yield from wait_until(lambda: win.result.get_text())
        assert win.result.get_text() == expected.upper()
        assert win.progress.fraction == 1.0
        win.target.set_text(expected)  # lower case: still a match
        assert win.matches is True and win.target.has_css_class("hash-match")
        assert "match" in win.match_label.get_label()
        win.target.set_text("00" + expected[2:])
        assert win.matches is False and win.target.has_css_class("hash-mismatch")
        copied = []
        import sharel.core.clipboard as cb
        orig = cb.set_text
        cb.set_text = lambda t: copied.append(t) or "test"
        try:
            from uihelpers import button_with_label
            button_with_label(win, "Copy").emit("clicked")
        finally:
            cb.set_text = orig
        assert copied == [expected.upper()]
        return True
    assert run_app(body)


def test_compare_two_files_and_text_mode(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same")
    b.write_text("same")

    def body(app):
        from sharel.ui.tools import hashcheck
        win = hashcheck.open(app, ctx_for(app, files=[str(a), str(b)]))
        yield 0.1
        assert win.compare.get_active() and not win.target.get_editable()
        win.hash_type.set_selected(hashcheck.HASH_TYPES.index(hashcheck.HashType.MD5))
        win.check_button.emit("clicked")
        yield from wait_until(lambda: win.target.get_text())
        assert win.result.get_text() == hashlib.md5(b"same").hexdigest().upper()
        assert win.matches is True
        b.write_text("different")
        win.check_button.emit("clicked")
        yield from wait_until(lambda: win.target.get_text() and win.worker and not win.worker.running)
        assert win.matches is False
        # text mode
        win.stack.set_visible_child_name("text")
        win.text_input.get_buffer().set_text("hello")
        assert win.result.get_text() == hashlib.md5(b"hello").hexdigest().upper()
        win.hash_type.set_selected(hashcheck.HASH_TYPES.index(hashcheck.HashType.SHA1))
        assert win.result.get_text() == hashlib.sha1(b"hello").hexdigest().upper()
        return True
    assert run_app(body)


def test_cancel_large_file(tmp_path):
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:
        fh.truncate(400 * 1024 * 1024)

    def body(app):
        from sharel.ui.tools import hashcheck
        win = hashcheck.open(app, ctx_for(app, files=[str(big)]))
        win.hash_type.set_selected(hashcheck.HASH_TYPES.index(hashcheck.HashType.SHA512))
        win.check_button.emit("clicked")
        assert win.check_button.get_label() == "Stop"
        yield 0.05
        win.check_button.emit("clicked")  # Stop
        yield from wait_until(lambda: not win.worker.running)
        assert win.worker.cancelled and win.result.get_text() == ""
        assert win.check_button.get_label() == "Check"
        return True
    assert run_app(body)
