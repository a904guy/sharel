"""Desktop mechanisms against fake services on the sandbox's private bus (real ShareL client code):
the clipboard chain, FileManager1, Mutter RemoteDesktop input, notifications and every toast action,
sounds, printing, gettext, the command line reaching the running instance, native-messaging actions
and the recording countdown / border / tray state."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import responses
from fakebus import FakeService
from gi.repository import GLib
from PIL import Image
from uihelpers import run_app

from sharel.core import clipboard
from sharel.core.enums import ToastClickAction as TCA
from sharel.core.tasks import TaskInfo, TaskStatus

ROOT = Path(__file__).resolve().parents[1]


# -- clipboard: extension > wl-copy > GTK ----------------------------------------------------------

class _Shell:
    def __init__(self, available=True, ok=True):
        self.available, self.ok, self.calls = available, ok, []

    def set_clipboard(self, mime, data):
        self.calls.append((mime, data))
        return self.ok


def test_clipboard_chain_order(monkeypatch):
    gtk = []
    monkeypatch.setattr(clipboard, "gtk_writer", lambda mime, data: gtk.append((mime, data)) or True)
    wl = []
    monkeypatch.setattr(clipboard, "_wl_copy", lambda mime, data: wl.append((mime, data)) or True)
    sh = _Shell()
    monkeypatch.setattr(clipboard, "shell", lambda: sh)
    assert clipboard.set_text("one") == "extension"  # a background app: the shell owns the clipboard
    assert sh.calls == [("text/plain;charset=utf-8", b"one")] and wl == [] and gtk == []
    sh.ok = False  # the extension refused (e.g. too large): next in the chain
    assert clipboard.set_text("two") == "wl-copy" and wl == [("text/plain;charset=utf-8", b"two")]
    monkeypatch.setattr(clipboard, "shell", lambda: _Shell(available=False))
    monkeypatch.setattr(clipboard, "_wl_copy", lambda mime, data: False)
    assert clipboard.set_text("three") == "gtk" and gtk == [("text/plain;charset=utf-8", b"three")]
    monkeypatch.setattr(clipboard, "gtk_writer", None)
    with pytest.raises(RuntimeError, match="could not set the clipboard"):
        clipboard.set_text("four")


def test_clipboard_payloads_for_images_and_files(monkeypatch, tmp_path):
    got = []
    sh = _Shell()
    sh.set_clipboard = lambda mime, data: got.append((mime, data)) or True
    monkeypatch.setattr(clipboard, "shell", lambda: sh)
    clipboard.set_image(Image.new("RGBA", (7, 5), "red"))
    mime, data = got[-1]
    assert mime == "image/png" and Image.open(io.BytesIO(data)).size == (7, 5)
    f = tmp_path / "a b.png"
    f.write_bytes(b"x")
    clipboard.set_files([str(f)])
    assert got[-1] == ("x-special/gnome-copied-files", f"copy\n{f.resolve().as_uri()}".encode())


# -- FileManager1.ShowItems ------------------------------------------------------------------------

FM_XML = """<node><interface name="org.freedesktop.FileManager1"><method name="ShowItems">
<arg type="as" direction="in"/><arg type="s" direction="in"/></method></interface></node>"""


def test_show_in_folder_uses_filemanager1_and_falls_back(tmp_path, monkeypatch):
    from sharel.core import desktop
    f = tmp_path / "shot one.png"
    f.write_bytes(b"x")
    svc = FakeService("org.freedesktop.FileManager1", {"/org/freedesktop/FileManager1": (FM_XML, lambda *a: None)})
    try:
        desktop.show_in_folder(str(f))
        assert [c[1:3] for c in svc.calls] == [("ShowItems", ([f.resolve().as_uri()], ""))]
    finally:
        svc.stop()
    opened = []
    monkeypatch.setattr(desktop, "open_folder", lambda p: opened.append(p))
    desktop.show_in_folder(str(f))  # no file manager on the bus: open the folder instead
    assert opened == [str(tmp_path)]


# -- Mutter RemoteDesktop input (scrolling capture) -------------------------------------------------

RD = "org.gnome.Mutter.RemoteDesktop"
RD_XML = f"""<node><interface name="{RD}"><method name="CreateSession"><arg type="o" direction="out"/></method>
</interface></node>"""
RD_SESSION_XML = f"""<node><interface name="{RD}.Session"><method name="Start"/><method name="Stop"/>
<method name="NotifyKeyboardKeysym"><arg type="u" direction="in"/><arg type="b" direction="in"/></method>
<method name="NotifyPointerMotionRelative"><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
<method name="NotifyPointerMotionAbsolute"><arg type="s" direction="in"/><arg type="d" direction="in"/>
<arg type="d" direction="in"/></method>
<method name="NotifyPointerAxisDiscrete"><arg type="u" direction="in"/><arg type="i" direction="in"/></method>
<method name="NotifyPointerButton"><arg type="i" direction="in"/><arg type="b" direction="in"/></method>
</interface></node>"""
PROPS_XML = """<node><interface name="org.freedesktop.DBus.Properties"><method name="Get">
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
</interface></node>"""


@pytest.fixture
def remote_desktop():
    from test_mechanisms_capture import DC, DC_XML, SC, SC_XML, SESSION_XML

    def mode(w, h):
        return (f"{w}x{h}", w, h, 60.0, 1.0, [1.0, 2.0], {"is-current": GLib.Variant("b", True)})
    dc_reply = GLib.Variant("(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})", (
        1, [(("DP-1", "", "", ""), [mode(1920, 1080)], {}), (("DP-2", "", "", ""), [mode(3200, 1800)], {})],
        [(0, 0, 1.0, 0, True, [("DP-1", "", "", "")], {}), (1920, 0, 2.0, 0, False, [("DP-2", "", "", "")], {})],
        {"layout-mode": GLib.Variant("u", 1)}))
    dc = FakeService(DC, {"/org/gnome/Mutter/DisplayConfig": (DC_XML, lambda *a: dc_reply)})
    sc_n = {"n": 0}
    sc = None

    def sc_root(method, args, sender):
        sp = "/org/gnome/Mutter/ScreenCast/Session/r1"

        def session(m, a, s):
            if m == "RecordMonitor":
                sc_n["n"] += 1
                return GLib.Variant("(o)", (f"/org/gnome/Mutter/ScreenCast/Stream/r{sc_n['n']}",))
            return None
        record_monitor = ('<method name="RecordMonitor"><arg type="s" direction="in"/>'
                          '<arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>')
        sc.add_object(sp, SESSION_XML.replace('<method name="RecordWindow">',
                                              record_monitor + '<method name="RecordWindow">'), session)
        return GLib.Variant("(o)", (sp,))
    sc = FakeService(SC, {"/org/gnome/Mutter/ScreenCast": (SC_XML, sc_root)})
    rd = None

    def rd_root(method, args, sender):
        sp = "/org/gnome/Mutter/RemoteDesktop/Session/s1"
        rd.add_object(sp, RD_SESSION_XML, lambda *a: None)
        rd.add_object(sp, PROPS_XML, lambda m, a, s: GLib.Variant("(v)", (GLib.Variant("s", "sid-1"),)))
        return GLib.Variant("(o)", (sp,))
    rd = FakeService(RD, {"/org/gnome/Mutter/RemoteDesktop": (RD_XML, rd_root)})
    yield rd, sc
    for s in (rd, sc, dc):
        s.stop()


def test_remote_input_drives_mutter_remote_desktop(remote_desktop):
    from sharel.capture.remoteinput import KEYSYMS, RemoteInput
    rd, sc = remote_desktop
    with RemoteInput() as inp:
        inp.move_to(2000, 100)  # on the 200 % monitor: physical stream coordinates
        inp.wheel(3)
        inp.key("Down", 2)
        inp.key("Page_Down")
        inp.click(1)
    calls = [(c[1], c[2]) for c in rd.calls if c[0].endswith("/s1")]
    names = [n for n, _a in calls]
    assert names[0] == "Get" or "Start" in names
    linked = next(c for c in sc.calls if c[1] == "CreateSession")[2][0]
    assert linked["remote-desktop-session-id"] == "sid-1"  # the ScreenCast session is tied to the input one
    assert [c[2][0] for c in sc.calls if c[1] == "RecordMonitor"] == ["DP-1", "DP-2"]
    after_start = calls[names.index("Start") + 1:]
    acts = [(n, a) for n, a in after_start if n not in ("NotifyPointerMotionRelative",)
            and not (n == "NotifyKeyboardKeysym" and a[0] == KEYSYMS["Shift_L"])]
    assert acts == [
        ("NotifyPointerMotionAbsolute", ("/org/gnome/Mutter/ScreenCast/Stream/r2", 160.0, 200.0)),
        ("NotifyPointerAxisDiscrete", (0, 3)),
        ("NotifyKeyboardKeysym", (0xFF54, True)), ("NotifyKeyboardKeysym", (0xFF54, False)),
        ("NotifyKeyboardKeysym", (0xFF54, True)), ("NotifyKeyboardKeysym", (0xFF54, False)),
        ("NotifyKeyboardKeysym", (KEYSYMS["Page_Down"], True)), ("NotifyKeyboardKeysym", (KEYSYMS["Page_Down"], False)),
        ("NotifyPointerButton", (0x110, True)), ("NotifyPointerButton", (0x110, False)),
        ("Stop", ()),
    ]


# -- notifications and toast actions ----------------------------------------------------------------

NOTIFY_XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
<arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal></interface></node>"""


def _spin(pred, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_toast_has_image_and_action_buttons_and_reports_clicks(tmp_path):
    from sharel.core.config import TaskSettingsGeneral
    from sharel.core.enums import EDataType
    from sharel.ui.notify import Notifier
    svc = FakeService("org.freedesktop.Notifications",
                      {"/org/freedesktop/Notifications": (NOTIFY_XML, lambda *a: GLib.Variant("(u)", (7,)))})
    try:
        clicks = []
        n = Notifier(lambda action, info: clicks.append((action, info.id)))
        img = tmp_path / "s.png"
        Image.new("RGBA", (4, 4)).save(img)
        info = TaskInfo(file_name="s.png", file_path=str(img), data_type=EDataType.Image, status=TaskStatus.Completed,
                        result_url="https://h.example/s.png", uploaded=True)
        g = TaskSettingsGeneral()
        g.toast_window_left_click_action = TCA.OpenUrl
        g.toast_window_duration = 4.0
        assert n.notify(info, g) == 7
        _app, _rid, app_id, summary, body, actions, hints, timeout = svc.calls[0][2]
        assert (summary, body, app_id) == ("Upload completed", "https://h.example/s.png", "app.sharel.ShareL")
        assert actions[:2] == ["default", "Open URL"] and "CopyUrl" in actions[2::2]
        assert hints["image-path"] == img.resolve().as_uri() and timeout == 4000
        svc.emit("/org/freedesktop/Notifications", "org.freedesktop.Notifications", "ActionInvoked",
                 GLib.Variant("(us)", (7, "CopyUrl")))
        svc.emit("/org/freedesktop/Notifications", "org.freedesktop.Notifications", "ActionInvoked",
                 GLib.Variant("(us)", (7, "default")))  # the toast itself: the left-click action
        assert _spin(lambda: len(clicks) == 2)
        assert clicks == [(TCA.CopyUrl, info.id), (TCA.OpenUrl, info.id)]
        svc.emit("/org/freedesktop/Notifications", "org.freedesktop.Notifications", "NotificationClosed",
                 GLib.Variant("(uu)", (7, 2)))
        _spin(lambda: False, 0.2)
        svc.emit("/org/freedesktop/Notifications", "org.freedesktop.Notifications", "ActionInvoked",
                 GLib.Variant("(us)", (7, "CopyUrl")))
        _spin(lambda: False, 0.3)
        assert len(clicks) == 2  # closed toasts no longer act
        n.close()
    finally:
        svc.stop()


def test_every_toast_click_action(tmp_path, monkeypatch):
    from sharel.core import desktop
    from sharel.ui import toastactions as T
    from sharel.ui.windows import editor as E
    from sharel.ui.windows import pin as P
    did = []
    monkeypatch.setattr(T.clipboard, "set_image", lambda img: did.append(("image", img.size)))
    monkeypatch.setattr(T.clipboard, "set_files", lambda p: did.append(("files", p)))
    monkeypatch.setattr(T.clipboard, "set_text", lambda t: did.append(("text", t)))
    monkeypatch.setattr(desktop, "open_file", lambda p: did.append(("open_file", p)))
    monkeypatch.setattr(desktop, "open_url", lambda u: did.append(("open_url", u)))
    monkeypatch.setattr(desktop, "show_in_folder", lambda p: did.append(("folder", p)))

    def body(app):
        from sharel.core.enums import EDataType
        img = tmp_path / "t.png"
        Image.new("RGBA", (9, 6), "red").save(img)
        info = TaskInfo(file_name="t.png", file_path=str(img), data_type=EDataType.Image,
                        result_url="https://h.example/t.png")
        app.tasks.upload_file = lambda p, ts: did.append(("upload", p))
        expect = {
            TCA.CloseNotification: ("closed", None),
            TCA.CopyImageToClipboard: ("copied image", ("image", (9, 6))),
            TCA.CopyFile: ("copied file", ("files", [str(img)])),
            TCA.CopyFilePath: ("copied path", ("text", str(img))),
            TCA.CopyUrl: ("copied url", ("text", "https://h.example/t.png")),
            TCA.OpenFile: ("opened file", ("open_file", str(img))),
            TCA.OpenFolder: ("opened folder", ("folder", str(img))),
            TCA.OpenUrl: ("opened url", ("open_url", "https://h.example/t.png")),
            TCA.Upload: ("upload started", ("upload", str(img))),
        }
        for action, (result, effect) in expect.items():
            n = len(did)
            assert T.run_toast_action(app, action, info) == result, action
            assert did[n:] == ([effect] if effect else []), action
        assert T.run_toast_action(app, TCA.AnnotateImage, info) == f"editor:{img}"
        assert E.OPEN_EDITORS and E.OPEN_EDITORS[-1].file_path == str(img)
        E.OPEN_EDITORS[-1].close()  # (an open editor would leak into later tests)
        assert not E.OPEN_EDITORS
        assert T.run_toast_action(app, TCA.PinToScreen, info) == "pinned" and [p.image.size for p in P.PINS] == [(9, 6)]
        P.close_all_pins()
        assert T.run_toast_action(app, TCA.DeleteFile, info) == "deleted" and not img.exists()
        assert T.run_toast_action(app, TCA.CopyFile, info) == "nothing to do"  # the file is gone now
        yield 0.1
        return True
    assert run_app(body)


# -- sounds ----------------------------------------------------------------------------------------

def test_sounds_for_each_event_custom_and_disabled(tmp_path, monkeypatch):
    from sharel.core.config import TaskSettingsGeneral
    from sharel.ui import sounds
    played = []
    monkeypatch.setattr(sounds, "_playbin", lambda p: played.append(p))
    monkeypatch.setattr(sounds.shutil, "which", lambda name: None)  # no canberra: play the theme file
    theme = tmp_path / "theme"
    theme.mkdir()
    for ev in sounds.THEME_EVENTS.values():
        (theme / f"{ev}.oga").write_bytes(b"x")
    monkeypatch.setattr(sounds, "THEME_DIRS", [theme])
    g = TaskSettingsGeneral()
    g.play_sound_after_capture = g.play_sound_after_upload = g.play_sound_after_action = True
    for kind, ev in sounds.THEME_EVENTS.items():
        assert sounds.play(kind, g, block=True) == str(theme / f"{ev}.oga")
    assert played == [str(theme / f"{e}.oga") for e in sounds.THEME_EVENTS.values()]
    custom = tmp_path / "shutter.wav"
    custom.write_bytes(b"x")
    g.use_custom_capture_sound, g.custom_capture_sound_path = True, str(custom)
    assert sounds.play("Capture", g, block=True) == str(custom) and played[-1] == str(custom)
    g.play_sound_after_capture = False
    assert sounds.play("Capture", g, block=True) == "" and played[-1] == str(custom)


def test_playbin_really_plays_a_file(tmp_path, monkeypatch):
    """A real GStreamer playbin to a fake audio sink: the file is decoded to the end."""
    import wave
    from sharel.ui import sounds
    p = tmp_path / "beep.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x10" * 800)  # 0.1 s
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    from sharel.capture.pwframe import gst_init
    gst_init()
    made = []
    orig = Gst.ElementFactory.make

    def make(name, alias):
        el = orig(name, alias)
        if name == "playbin":
            sink = orig("fakesink", None)
            sink.set_property("sync", False)
            el.set_property("audio-sink", sink)
            made.append(el)
        return el
    monkeypatch.setattr(Gst.ElementFactory, "make", make)
    t = time.monotonic()
    sounds._playbin(str(p))
    assert made and time.monotonic() - t < 5
    assert made[0].get_state(0)[1] == Gst.State.NULL  # finished and released


# -- printing --------------------------------------------------------------------------------------

PRINT_SCRIPT = """
import sys, types
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from PIL import Image
from sharel.core.config import PrintSettings
from sharel.ui import printing
Gtk.init()
out = sys.argv[1]
orig_run = Gtk.PrintOperation.run
def run(op, action, parent):  # what the user's "Print to File" does, without the dialog
    op.set_export_filename(out)
    return orig_run(op, Gtk.PrintOperationAction.EXPORT, parent)
Gtk.PrintOperation.run = run
cfg = types.SimpleNamespace(dont_show_print_settings_dialog=True)
app = types.SimpleNamespace(store=types.SimpleNamespace(app=cfg), active_parent=lambda: None,
                            message=lambda *a, **k: print("MESSAGE", a))
printing.print_image(app, Image.new("RGBA", (300, 200), "red"), PrintSettings(show_print_dialog=False, margin=10))
"""


def test_print_image_renders_the_page_to_a_pdf(tmp_path):
    """The GTK print operation exported to a file, in a fresh process: one page with the image on it."""
    out = tmp_path / "print.pdf"
    r = subprocess.run([sys.executable, "-c", PRINT_SCRIPT, str(out)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "MESSAGE" not in r.stdout, r.stdout + r.stderr
    info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True, check=True).stdout
    import re
    assert re.search(r"^Pages:\s+1$", info, re.M), info
    subprocess.run(["pdftoppm", "-r", "30", "-png", "-singlefile", str(out), str(tmp_path / "page")], check=True)
    import numpy as np
    page = np.asarray(Image.open(tmp_path / "page.png").convert("RGB")).astype(int)
    red = (page[..., 0] > 200) & (page[..., 1] < 60) & (page[..., 2] < 60)
    assert red.mean() > 0.05  # the image is on the page (scaled into the margins), not a blank sheet


# -- gettext ---------------------------------------------------------------------------------------

def test_translations_reach_modules_that_imported_underscore(tmp_path, monkeypatch):
    from sharel.core import i18n
    from sharel.ui import main_window
    po = tmp_path / "xx.po"
    po.write_text('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                  'msgid "Capture"\nmsgstr "Kaptur"\n')
    mo = tmp_path / "locale/xx/LC_MESSAGES/sharel.mo"
    mo.parent.mkdir(parents=True)
    subprocess.run(["msgfmt", "-o", str(mo), str(po)], check=True)
    monkeypatch.setattr(i18n, "locale_dir", lambda: str(tmp_path / "locale"))
    try:
        i18n.set_language("xx")
        assert i18n._("Capture") == "Kaptur" and main_window._("Capture") == "Kaptur"
        assert main_window._("Upload") == "Upload"  # untranslated strings stay English
        i18n.set_language("English")
        assert main_window._("Capture") == "Capture"
    finally:
        i18n.set_language(None)


def test_pot_lists_the_marked_strings():
    pot = (ROOT / "po/sharel.pot").read_text()
    for s in ("Capture", "After capture tasks", "Screenshots folder…", "About…"):
        assert f'msgid "{s}"' in pot, s


# -- the command line reaches the running instance ---------------------------------------------------

def test_second_sharel_forwards_its_command_line_to_the_running_one(tmp_path):
    f = tmp_path / "up.png"
    Image.new("RGBA", (3, 3)).save(f)

    def body(app):
        got = []
        app.upload_paths = lambda paths, ts: got.append(list(paths))
        env = {**os.environ, "SHAREL_NO_TRAY": "1", "SHAREL_DISABLE_HOTKEYS": "1", "SHAREL_NO_EXTENSION": "1"}
        procs = [subprocess.Popen([sys.executable, "-c", "import sys; from sharel.cli import main; sys.exit(main())",
                                   *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for args in (["-OpenMainWindow"], [str(f)])]
        for _ in range(300):
            if all(p.poll() is not None for p in procs):
                break
            yield 0.05
        assert [p.returncode for p in procs] == [0, 0], [p.stderr.read() for p in procs]
        yield 0.3
        assert app.main_window is not None and app.main_window.get_mapped()  # done by the first instance
        assert got == [[str(f)]]
        return True
    assert run_app(body, timeout=40)


def test_a_sxcu_forwarded_to_the_running_sharel_is_imported_not_uploaded(tmp_path):
    """A second `sharel x.sxcu` (a double-clicked custom uploader file) reaches the running instance, which imports
    it and asks whether to make it active, like ShareX; it is never uploaded."""
    f = tmp_path / "forwarded.sxcu"
    f.write_text(json.dumps({"Version": "18.0.0", "Name": "Forwarded host", "DestinationType": "TextUploader",
                             "RequestMethod": "POST", "RequestURL": "http://127.0.0.1:9/up",
                             "Body": "MultipartFormData",
                             "FileFormName": "file", "URL": "{response}"}))

    def body(app):
        from sharel.ui.windows.customuploader import _windows
        got = []
        app.upload_paths = lambda paths, ts: got.append(list(paths))
        env = {**os.environ, "SHAREL_NO_TRAY": "1", "SHAREL_DISABLE_HOTKEYS": "1", "SHAREL_NO_EXTENSION": "1"}
        p = subprocess.Popen([sys.executable, "-c", "import sys; from sharel.cli import main; sys.exit(main())",
                              f.name], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(300):
            if p.poll() is not None:
                break
            yield 0.05
        assert p.returncode == 0, p.stderr.read()
        yield 0.3
        assert got == []
        assert [it.name for it in app.store.uploaders.custom_uploader_items()] == ["Forwarded host"]
        win = _windows[id(app)]
        assert "texts" in win.activate_dialog.get_body()
        win.activate_dialog.emit("response", "yes")
        yield 0.2
        from sharel.core.enums import TextDestination
        assert app.store.app.default_task_settings.text_destination == TextDestination.CustomTextUploader
        win.close()
        return True
    assert run_app(body, timeout=40)


# -- native messaging actions ----------------------------------------------------------------------

def test_every_native_messaging_action():
    png = io.BytesIO()
    Image.new("RGBA", (11, 7), "green").save(png, "PNG")

    def body(app):
        calls = []
        app.tasks.download_upload = lambda url, ts: calls.append(("download", url))
        app.tasks.upload_text = lambda text, ts: calls.append(("text", text))
        app.tasks.shorten_url = lambda url, ts: calls.append(("shorten", url))
        app.tasks.run_image = lambda img, ts, **kw: calls.append(("image", img.size))
        ts = app.store.app.default_task_settings
        ts.advanced_settings.process_images_during_browser_upload = False
        for action, field, value, expect in (("UploadImage", "URL", "https://e.x/a.png", ("download", "https://e.x/a.png")),
                                             ("UploadVideo", "URL", "https://e.x/v.mp4", ("download", "https://e.x/v.mp4")),
                                             ("UploadAudio", "URL", "https://e.x/s.ogg", ("download", "https://e.x/s.ogg")),
                                             ("UploadText", "Text", "page text", ("text", "page text")),
                                             ("ShortenURL", "URL", "https://e.x/long", ("shorten", "https://e.x/long"))):
            assert app.on_native_message(json.dumps({"Action": action, field: value})) == action
            assert calls[-1] == expect, action
        # with image processing on, the browser image is downloaded and runs the after-capture tasks
        ts.advanced_settings.process_images_during_browser_upload = True
        with responses.RequestsMock() as rsps:
            rsps.get("https://e.x/b.png", body=png.getvalue(), content_type="image/png")
            app.on_native_message(json.dumps({"Action": "UploadImage", "URL": "https://e.x/b.png"}))
            for _ in range(100):
                if calls[-1][0] == "image":
                    break
                yield 0.05
        assert calls[-1] == ("image", (11, 7))
        return True
    assert run_app(body)


# -- recording countdown, region border and tray state ------------------------------------------------

def test_recording_countdown_border_and_tray_state(monkeypatch):
    monkeypatch.setenv("SHAREL_RECORD_TEST_PATTERN", "1")
    from test_ui_jobs import FakeShell

    from sharel.core.config import Rect
    from sharel.core.enums import HotkeyType as H
    from sharel.core.jobs import dispatch
    from sharel.record.controller import CONTROLLER
    shell = FakeShell()
    import sharel.record.controller as RC
    import sharel.shell.client as sc
    monkeypatch.setattr(sc, "shell", lambda: shell)
    monkeypatch.setattr(sc.ShellClient, "_instance", shell)
    if hasattr(RC, "shell"):
        monkeypatch.setattr(RC, "shell", lambda: shell)

    def body(app):
        app.start_tray()
        states = []
        app.tray._write = lambda msg: msg.get("cmd") == "state" and states.append(msg["icon"])
        ts = app.store.app.default_task_settings
        ro = ts.capture_settings.screen_recording
        ro.countdown_seconds, ro.screen_record_fps, ro.show_region_border = 2, 15, True
        ts.capture_settings.capture_custom_region = Rect(0, 0, 160, 120)
        dispatch(app, H.ScreenRecorderCustomRegion)
        yield 0.3
        assert CONTROLLER.active and CONTROLLER.recorder is None  # counting down, not recording yet
        assert CONTROLLER._countdown_win is not None and CONTROLLER._countdown_win.get_mapped()
        for _ in range(80):
            if CONTROLLER.recorder is not None:
                break
            yield 0.05
        assert CONTROLLER.recorder is not None and CONTROLLER._countdown_win is None
        yield 0.3
        border = next(c for c in shell.calls if c[0] == "show_border")
        assert border[1][0] == "sharel-recording" and tuple(border[1][1]) == (0, 0, 160, 120)
        assert "recording" in states
        dispatch(app, H.PauseScreenRecording)
        yield 0.2
        assert states[-1] == "paused"
        dispatch(app, H.AbortScreenRecording)
        yield 0.3
        assert states[-1] == "" and any(c[0] == "hide_border" for c in shell.calls)
        return True
    assert run_app(body, timeout=40)


def _assert_one_red_page(pdf: Path, tmp_path: Path) -> None:
    import re

    import numpy as np
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    assert re.search(r"^Pages:\s+1$", info, re.M), info
    subprocess.run(["pdftoppm", "-r", "30", "-png", "-singlefile", str(pdf), str(tmp_path / "page")], check=True)
    page = np.asarray(Image.open(tmp_path / "page.png").convert("RGB")).astype(int)
    assert (((page[..., 0] > 200) & (page[..., 1] < 60) & (page[..., 2] < 60)).mean() > 0.05)


def _settle(timeout: float = 3.0) -> bool:
    """Dispatch what is ready on the main context; True once nothing is left ready to run. An idle that
    never finishes (always ready) keeps it busy for good."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while ctx.pending() and time.monotonic() < deadline:
        ctx.iteration(False)
    return not ctx.pending()


def test_editor_closed_before_layout_leaves_nothing_spinning():
    """Regression: an editor closed before its first allocation kept an always-ready idle (polling for a
    canvas size that never came), which busy-looped the CPU and starved GTK's lower-priority idles."""
    def body(app):
        from sharel.ui.windows.editor import open_editor
        assert _settle(), "the main loop is busy before the test starts"
        ed = open_editor(app, Image.new("RGBA", (100, 50), "white"), app.store.task_settings_for(None), None,
                         task_mode=False)
        ed.close()  # before the main loop ever lays it out
        assert _settle(), "something stays ready on the main loop after the editor closed"
        yield 0.2
        assert _settle()
        return True
    assert run_app(body)


def test_print_after_an_editor_and_a_pin_closed_in_the_same_process(tmp_path, monkeypatch):
    """GTK's synchronous print renders pages from an idle below default-idle priority inside a nested
    loop, so any always-ready idle in the app froze "Print image" for good. Here: close an editor before
    layout and a pin, then print (exported to a PDF) in this process, with a hard deadline."""
    import faulthandler

    from gi.repository import Gtk

    from sharel.core.config import PrintSettings
    from sharel.ui import printing
    out = tmp_path / "print.pdf"
    orig_run = Gtk.PrintOperation.run

    def run(op, action, parent):  # the user's "Print to File", without the dialog
        op.set_export_filename(str(out))
        return orig_run(op, Gtk.PrintOperationAction.EXPORT, parent)
    monkeypatch.setattr(Gtk.PrintOperation, "run", run)

    def body(app):
        from sharel.ui.windows.editor import open_editor
        from sharel.ui.windows.pin import pin_image
        app.store.app.dont_show_print_settings_dialog = True
        ed = open_editor(app, Image.new("RGBA", (100, 50), "white"), app.store.task_settings_for(None), None,
                         task_mode=False)
        ed.close()
        pin = pin_image(app, Image.new("RGBA", (120, 80), "blue"))
        yield 0.5
        pin.close()
        yield 0.3
        messages = []
        app.message = lambda *a, **k: messages.append(a)
        faulthandler.dump_traceback_later(20, exit=True)  # a hang can't be interrupted from Python
        t = time.monotonic()
        try:
            printing.print_image(app, Image.new("RGBA", (300, 200), "red"), PrintSettings(show_print_dialog=False,
                                                                                        margin=10))
        finally:
            faulthandler.cancel_dump_traceback_later()
        assert time.monotonic() - t < 5 and not messages
        return True
    assert run_app(body)
    _assert_one_red_page(out, tmp_path)
