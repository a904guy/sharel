"""History and Image history windows: search, every filter, actions, live update, image grid."""
import datetime as dt

from PIL import Image
from uihelpers import run_app

from sharel.core.history import HistoryItem


def _populate(app, tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir(exist_ok=True)
    img1 = shots / "cat.png"
    Image.new("RGBA", (40, 30), (255, 0, 0, 255)).save(img1)
    img2 = shots / "dog.jpg"
    Image.new("RGB", (30, 40), (0, 255, 0)).save(img2)
    txt = shots / "notes.txt"
    txt.write_text("hello notes")
    items = [
        HistoryItem(file_name="cat.png", file_path=str(img1), date_time="2025-01-05T10:00:00", type="Image",
                    host="Imgur", url="https://i.imgur.com/cat.png", thumbnail_url="https://i.imgur.com/catt.png",
                    deletion_url="https://imgur.com/delete/cat", tags={"WindowTitle": "Firefox"}),
        HistoryItem(file_name="dog.jpg", file_path=str(img2), date_time="2025-02-10T11:00:00", type="Image",
                    host="Chevereto", url="https://chev.example/dog.jpg"),
        HistoryItem(file_name="notes.txt", file_path=str(txt), date_time="2025-02-20T12:00:00", type="Text",
                    host="Pastebin", url="https://pastebin.com/abc"),
        HistoryItem(file_name="archive.zip", file_path=str(shots / "gone.zip"), date_time="2025-03-01T09:00:00",
                    type="File", host="Dropbox", url="https://dropbox.example/archive.zip"),
        HistoryItem(file_name="", date_time="2025-03-15T08:00:00", type="URL", host="is.gd",
                    url="https://long.example/page", shortened_url="https://is.gd/xyz"),
        HistoryItem(file_name="missing.png", file_path=str(shots / "missing.png"), date_time="2024-12-31T23:00:00",
                    type="Image", host="Imgur", url="https://i.imgur.com/missing.png"),
    ]
    for it in items:
        app.history.add(it)
    return items


def _names(win):
    return [win.model.get_item(i).item.file_name for i in range(win.model.get_n_items())]


def test_history_search_and_filters(tmp_path):
    def body(app):
        _populate(app, tmp_path)
        from sharel.ui.windows.history import open_history
        win = open_history(app)
        yield 0.2
        assert win.get_mapped()
        assert _names(win) == ["", "archive.zip", "notes.txt", "dog.jpg", "cat.png", "missing.png"]  # newest first
        assert win.item_count_text().startswith("Total: 6 - Image: 3")
        # simple search (also matches host and window title)
        win.search.set_text("firefox")
        assert _names(win) == ["cat.png"]
        assert "Filtered: 1" in win.item_count_text()
        win.search.set_text("imgur")
        assert _names(win) == ["cat.png", "missing.png"]
        win.search.set_text("")
        # file name and URL filters
        win.filename_filter.set_text(".png")
        assert _names(win) == ["cat.png", "missing.png"]
        win.filename_filter.set_text("")
        win.url_filter.set_text("is.gd")
        assert _names(win) == [""]
        win.url_filter.set_text("")
        # type filter
        assert [win.type_model.get_string(i) for i in range(win.type_model.get_n_items())] == \
            ["Image", "Text", "File", "URL"]
        win.set_type_filter("Text")
        assert _names(win) == ["notes.txt"]
        win.type_check.set_active(False)
        # host filter
        win.set_host_filter("Imgur")
        assert _names(win) == ["cat.png", "missing.png"]
        win.set_type_filter("Image")
        assert _names(win) == ["cat.png", "missing.png"]
        win.set_host_filter("Chevereto")
        assert _names(win) == ["dog.jpg"]
        win.type_check.set_active(False)
        win.host_check.set_active(False)
        # date range
        win.date_from.set_text("2025-02-01")
        win.date_to.set_text("2025-02-28")
        win.date_check.set_active(True)
        assert _names(win) == ["notes.txt", "dog.jpg"]
        win.date_to.set_text("2025-01-31")
        win.date_from.set_text("2024-01-01")
        assert _names(win) == ["cat.png", "missing.png"]
        # reset
        win.reset_filters()
        assert len(_names(win)) == 6
        # max item count
        win.max_spin.set_value(2)
        assert _names(win) == ["", "archive.zip"]
        assert app.store.app.history_settings.max_item_count == 2
        win.max_spin.set_value(-1)
        assert len(_names(win)) == 6
        return True
    assert run_app(body)


def test_history_actions_and_live_update(tmp_path, monkeypatch):
    from sharel.core import clipboard, desktop
    calls = []
    monkeypatch.setattr(clipboard, "set_text", lambda t: calls.append(("text", t)))
    monkeypatch.setattr(clipboard, "set_files", lambda p: calls.append(("files", list(p))))
    monkeypatch.setattr(clipboard, "set_image", lambda img: calls.append(("image", img.size)))
    monkeypatch.setattr(desktop, "open_url", lambda u: calls.append(("url", u)))
    monkeypatch.setattr(desktop, "open_file", lambda p: calls.append(("open", p)))
    monkeypatch.setattr(desktop, "show_in_folder", lambda p: calls.append(("folder", p)))

    def body(app):
        items = _populate(app, tmp_path)
        cat = items[0]
        from sharel.ui.windows.history import open_history
        win = open_history(app)
        yield 0.2
        idx = _names(win).index("cat.png")
        win.select_index(idx)
        # details pane
        assert win.detail_labels["url"].get_label() == cat.url
        assert win.detail_labels["tags"].get_label() == "Firefox"
        assert win.preview.get_paintable() is not None
        win.update_action_states()
        assert win.action_group.lookup_action("copy-image").get_enabled()
        assert not win.action_group.lookup_action("copy-text").get_enabled()
        expected = {
            "open-url": ("url", cat.url), "open-thumb": ("url", cat.thumbnail_url),
            "open-del": ("url", cat.deletion_url), "open-file": ("open", cat.file_path),
            "open-folder": ("folder", cat.file_path), "copy-url": ("text", cat.url),
            "copy-thumb": ("text", cat.thumbnail_url), "copy-del": ("text", cat.deletion_url),
            "copy-path": ("text", cat.file_path), "copy-name": ("text", "cat"),
            "copy-name-ext": ("text", "cat.png"), "copy-folder": ("text", str(tmp_path / "shots")),
            "copy-html-link": ("text", f'<a href="{cat.url}">{cat.url}</a>'),
            "copy-html-image": ("text", f'<img src="{cat.url}"/>'),
            "copy-html-linked": ("text", f'<a href="{cat.url}"><img src="{cat.thumbnail_url}"/></a>'),
            "copy-forum-link": ("text", f"[url]{cat.url}[/url]"),
            "copy-forum-image": ("text", f"[img]{cat.url}[/img]"),
            "copy-forum-linked": ("text", f"[url={cat.url}][img]{cat.thumbnail_url}[/img][/url]"),
            "copy-md-link": ("text", f"[cat.png]({cat.url})"),
            "copy-md-image": ("text", f"![cat.png]({cat.url})"),
            "copy-md-linked": ("text", f"[![cat.png]({cat.thumbnail_url})]({cat.url})"),
            "copy-file": ("files", [cat.file_path]), "copy-image": ("image", (40, 30)),
        }
        for action, want in expected.items():
            calls.clear()
            win.action_group.activate_action(action, None)
            assert calls == [want], (action, calls)
        # copy text of a text file, URL of a URL item, double click opens the shortened URL
        win.select_index(_names(win).index("notes.txt"))
        calls.clear()
        win.action_group.activate_action("copy-text", None)
        assert calls == [("text", "hello notes")]
        url_item = win.model.get_item(_names(win).index("")).item
        calls.clear()
        win.try_open(url_item)
        assert calls == [("url", "https://is.gd/xyz")]
        # several selected: one URL per line
        win.selection.select_item(_names(win).index("cat.png"), False)
        calls.clear()
        win.action_group.activate_action("copy-url", None)
        assert calls == [("text", f"{cat.url}\r\nhttps://pastebin.com/abc")] or \
            calls == [("text", f"https://pastebin.com/abc\r\n{cat.url}")]
        # upload again / edit image
        uploads, edits = [], []
        app.upload_paths = lambda paths, ts: uploads.append(paths)
        app.job_ImageEditor = lambda ctx: edits.append(ctx.files)
        win.select_index(_names(win).index("cat.png"))
        win.action_group.activate_action("upload", None)
        win.action_group.activate_action("edit-image", None)
        assert uploads == [[cat.file_path]] and edits == [[cat.file_path]]
        # delete from history
        win.action_group.activate_action("delete", None)
        assert "cat.png" not in _names(win)
        assert all(i.id != cat.id for i in app.history.items())
        # live update from a worker thread
        import threading
        t = threading.Thread(target=lambda: app.history.add(HistoryItem(file_name="new.png", type="Image",
                                                                        date_time="2026-01-01T00:00:00")))
        t.start()
        t.join()
        yield 0.2
        assert _names(win)[0] == "new.png"
        n_listeners = len(app.history.listeners)
        win.destroy()
        yield 0.1
        assert len(app.history.listeners) == n_listeners - 1
        return True
    assert run_app(body)


def test_delete_file_locally(tmp_path, monkeypatch):
    import sharel.ui.gtkutil as gu
    monkeypatch.setattr(gu, "ask", lambda parent, heading, body, responses, on_done, **kw: on_done("delete"))

    def body(app):
        items = _populate(app, tmp_path)
        from sharel.ui.windows.history import open_history
        win = open_history(app)
        win.select_index(_names(win).index("notes.txt"))
        win.action_group.activate_action("delete-file", None)
        assert not (tmp_path / "shots" / "notes.txt").exists()
        assert all(i.id != items[2].id for i in app.history.items())
        return True
    assert run_app(body)


def test_stats_text():
    from sharel.ui.windows.history import stats_text
    items = [HistoryItem(file_name="a.png", type="Image", host="Imgur"),
             HistoryItem(file_name="b.png", type="Image", host="Imgur"),
             HistoryItem(file_name="c.txt", type="Text", host="Pastebin")]
    s = stats_text(items)
    assert "Total: 3" in s and "  Image: 2" in s and "  Imgur: 2" in s and "  png: 2" in s and "  txt: 1" in s


def test_image_history_grid(tmp_path, monkeypatch):
    from sharel.core import desktop
    opened = []
    monkeypatch.setattr(desktop, "open_file", opened.append)

    def body(app):
        _populate(app, tmp_path)
        app.store.app.image_history_settings.thumbnail_size = 120
        from sharel.ui.windows.history import open_image_history
        win = open_image_history(app)
        yield 0.3
        assert win.get_mapped()
        # only image items (with an image file path), newest first
        assert _names(win) == ["dog.jpg", "cat.png", "missing.png"]
        # thumbnails load off the main loop and land in the cache
        for _ in range(60):
            if str(tmp_path / "shots" / "cat.png") in win.thumbs.cache:
                break
            yield 0.05
        tex = win.thumbs.cache[str(tmp_path / "shots" / "cat.png")]
        assert tex is not None and max(tex.get_width(), tex.get_height()) <= 120
        assert win.thumbs.cache.get(str(tmp_path / "shots" / "missing.png"), "absent") in (None, "absent")
        # filter missing files
        win.missing_check.set_active(True)
        assert _names(win) == ["dog.jpg", "cat.png"]
        assert app.store.app.image_history_settings.filter_missing_files is True
        # search
        win.search.set_text("chev")
        assert _names(win) == ["dog.jpg"]
        win.search.set_text("")
        # max item count
        win.max_item_count_spin.set_value(1)
        assert _names(win) == ["dog.jpg"]
        win.max_item_count_spin.set_value(0)
        assert _names(win) == ["dog.jpg", "cat.png"]
        # thumbnail size setting resets the cache
        win.thumbnail_size_spin.set_value(80)
        assert app.store.app.image_history_settings.thumbnail_size == 80 and win.thumbs.size == 80
        # double click (activate) opens the file; context actions work like History
        win.open_item(win.model.get_item(1).item)
        assert opened == [str(tmp_path / "shots" / "cat.png")]
        win.select_index(1)
        win.action_group.activate_action("delete", None)
        assert _names(win) == ["dog.jpg"]
        # live update: a new image item appears
        img = tmp_path / "shots" / "new.png"
        Image.new("RGB", (10, 10)).save(img)
        app.history.add(HistoryItem(file_name="new.png", file_path=str(img), type="Image",
                                    date_time=dt.datetime.now().isoformat(timespec="seconds")))
        yield 0.2
        assert _names(win)[0] == "new.png"
        return True
    assert run_app(body)


def test_open_history_jobs_and_remember_search(tmp_path):
    def body(app):
        from sharel.core.enums import HotkeyType
        from sharel.core.jobs import dispatch
        from sharel.ui.windows import history
        _populate(app, tmp_path)
        dispatch(app, HotkeyType["OpenHistory"], source="hotkey")
        dispatch(app, HotkeyType["OpenImageHistory"], source="hotkey")
        yield 0.2
        hwin = history._history_windows[id(app)]
        iwin = history._image_windows[id(app)]
        assert hwin.get_mapped() and iwin.get_mapped()
        assert history.open_history(app) is hwin
        hwin.remember_switch.set_active(True)
        hwin.search.set_text("dog")
        hwin.close()
        yield 0.2
        assert app.store.app.history_settings.search_text == "dog"
        again = history.open_history(app)
        assert again is not hwin and again.search.get_text() == "dog" and _names(again) == ["dog.jpg"]
        return True
    assert run_app(body)


def _no_hyphens(label) -> bool:
    """The label wraps at word or character boundaries and Pango won't insert hyphens where it breaks."""
    from gi.repository import Pango
    attrs = label.get_attributes()
    off = any(a.klass.type == Pango.AttrType.INSERT_HYPHENS and a.as_int().value == 0
              for a in (attrs.get_attributes() if attrs else []))
    return off and label.get_wrap() and label.get_wrap_mode() == Pango.WrapMode.WORD_CHAR


def test_history_details_wrap_urls_and_paths_without_hyphens(tmp_path):
    """Regression: wrapped details read "/…/s-unset.png" and "https://i.example.com/vuVVn-FT.png" (inserted
    hyphens), so a copied-by-eye URL or path was wrong."""
    def body(app):
        _populate(app, tmp_path)
        from sharel.ui.windows.history import open_history
        win = open_history(app)
        yield 0.2
        win.select_index(_names(win).index("cat.png"))
        assert set(win.detail_labels) >= {"file_path", "url", "thumbnail_url", "deletion_url", "shortened_url"}
        bad = [k for k, lbl in win.detail_labels.items() if not _no_hyphens(lbl)]
        assert not bad, bad
        assert win.detail_labels["url"].get_label() == "https://i.imgur.com/cat.png"  # the text itself is intact
        win.close()
        return True
    assert run_app(body)


def test_url_and_path_views_elsewhere_have_no_hyphens():
    """The same for the QR scan results and the tools' text views (OCR text, QR text, clipboard contents)."""
    from sharel.ui.tools import qrcode
    from sharel.ui.tools._base import text_view

    def body(app):
        win = qrcode.open_qr_results(app, ["https://i.example.com/a-long/path/to/an/image.png", "plain"], None)
        yield 0.2
        rows = []
        child = win.list.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        assert [r.value_label.get_label() for r in rows] == ["https://i.example.com/a-long/path/to/an/image.png",
                                                              "plain"]
        assert all(_no_hyphens(r.value_label) for r in rows)
        win.close()
        view, _sw = text_view()
        view.get_buffer().set_text("https://i.example.com/vuVVnFT.png")
        buf = view.get_buffer()
        it = buf.get_start_iter()
        assert view.no_hyphens_tag in it.get_tags() and not view.no_hyphens_tag.props.insert_hyphens
        it.forward_to_end()
        it.backward_char()
        assert view.no_hyphens_tag in it.get_tags()  # the whole text, including what was typed later
        return True
    assert run_app(body)
