"""Desktop integration data: the desktop entries, the GSettings schema, the Nautilus scripts and the
browser's native messaging host."""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from sharel.tools import native_host as N

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


# -- desktop entries -------------------------------------------------------------------------------

def test_desktop_entries_validate_and_point_at_sharel():
    for name in ("app.sharel.ShareL.desktop", "app.sharel.ShareL.Editor.desktop"):
        path = DATA / "applications" / name
        out = subprocess.run(["desktop-file-validate", str(path)], capture_output=True, text=True)
        assert out.returncode == 0 and not out.stdout.strip(), out.stdout + out.stderr
        entry = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
        assert entry["Icon"] == "app.sharel.ShareL" and entry["Exec"].split()[0] == "sharel"
    main = (DATA / "applications/app.sharel.ShareL.desktop").read_text()
    assert "Exec=sharel %F" in main and "StartupWMClass=app.sharel.ShareL" in main
    # every desktop action (the launcher's right-click menu) runs a real ShareL job
    from sharel.cli import parse_args
    actions = [line.split("=", 1)[1] for line in main.splitlines() if line.startswith("Exec=sharel -")]
    assert len(actions) == 8
    for cmd in actions:
        parsed = parse_args(cmd.split()[1:])
        assert parsed.jobs, cmd
    # .sxcu files open in ShareL (which imports them): the MIME type its desktop entry claims is defined for *.sxcu
    import xml.etree.ElementTree as ET
    ns = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}
    types = {m.get("type"): [g.get("pattern") for g in m.findall("m:glob", ns)]
             for m in ET.parse(DATA / "mime/packages/app.sharel.ShareL.xml").getroot().findall("m:mime-type", ns)}
    assert types == {"application/x-sharex-custom-uploader": ["*.sxcu"],
                     "application/x-sharex-image-effect": ["*.sxie"]}
    assert "MimeType=application/x-sharex-custom-uploader;application/x-sharex-image-effect;" in main.splitlines()
    assert parse_args(["x.sxcu"]).custom_uploaders == ["x.sxcu"]  # what Exec=sharel %F passes
    assert parse_args(["x.sxie"]).image_effects == ["x.sxie"]

    svg = (DATA / "icons/hicolor/scalable/apps/app.sharel.ShareL.svg").read_text().lstrip()
    assert svg.startswith(("<svg", "<?xml"))
    assert (DATA / "icons/hicolor/symbolic/apps/app.sharel.ShareL-symbolic.svg").exists()


# -- GSettings -------------------------------------------------------------------------------------

def _compiled_schema(tmp_path):
    out = subprocess.run(["glib-compile-schemas", "--strict", "--targetdir", str(tmp_path),
                          str(DATA / "glib-2.0/schemas")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    src = Gio.SettingsSchemaSource.new_from_directory(str(tmp_path), None, False)
    return src.lookup("app.sharel.ShareL", False)


def test_gsettings_schema_compiles_and_has_every_key_the_code_reads(tmp_path):
    import re
    schema = _compiled_schema(tmp_path)
    assert schema is not None
    keys = set(schema.list_keys())
    used = set()
    for py in ("ui/main_window.py",):
        used |= set(re.findall(r'gsettings\.\w+\("([a-z-]+)"', (ROOT / "src/sharel" / py).read_text()))
    assert used == {"window-width", "window-height", "window-maximized"}
    assert used <= keys
    s = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
    assert s.get_int("window-width") > 0 and s.get_int("window-height") > 0
    assert s.get_boolean("window-maximized") is False


def test_main_window_geometry_goes_through_gsettings(tmp_path, monkeypatch):
    from uihelpers import run_app

    from sharel.ui import gsettings
    schema = _compiled_schema(tmp_path)
    store = Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)
    monkeypatch.setattr(gsettings, "settings", lambda: store)

    def body(app):
        app.show_main_window()
        yield 0.2
        win = app.main_window
        win.set_default_size(901, 611)
        win.close()
        yield 0.2
        assert (store.get_int("window-width"), store.get_int("window-height")) == (901, 611)
        app.show_main_window()  # a new window: sized from GSettings
        yield 0.2
        assert app.main_window.get_default_size() == (901, 611)
        return True
    assert run_app(body)


# -- Nautilus scripts ------------------------------------------------------------------------------

def test_nautilus_scripts_pass_the_selection_to_sharel(tmp_path):
    fake = tmp_path / "bin" / "sharel"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$ARGS_OUT"\n')
    fake.chmod(0o755)
    files = [str(tmp_path / "a b.png"), str(tmp_path / "c.png")]
    expect = {"Upload with ShareL": files, "Edit with ShareL": ["-ImageEditor", *files],
              "Pin to screen with ShareL": ["-PinToScreenFromFile", *files]}
    for script, argv in expect.items():
        out = tmp_path / "args.txt"
        env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}", "ARGS_OUT": str(out),
               "NAUTILUS_SCRIPT_SELECTED_FILE_PATHS": "\n".join(files) + "\n"}
        r = subprocess.run([str(DATA / "nautilus/scripts" / script)], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert out.read_text().splitlines() == argv, script  # one argument per file, spaces kept


# -- native messaging host -------------------------------------------------------------------------

def _frame(obj) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack("<I", len(data)) + data


def _frames(raw: bytes) -> list[dict]:
    out, buf = [], io.BytesIO(raw)
    while (m := N.read_message(buf)) is not None:
        out.append(m)
    return out


def test_native_host_framing_and_validation():
    buf = io.BytesIO()
    N.write_message(buf, {"a": 1})
    assert buf.getvalue() == struct.pack("<I", 8) + b'{"a": 1}'
    assert N.read_message(io.BytesIO(buf.getvalue())) == {"a": 1}
    forwarded = []
    assert N.handle({"Action": "UploadText", "Text": "hi"}, forward=forwarded.append) == {"ok": True}
    assert json.loads(forwarded[0]) == {"Action": "UploadText", "URL": "", "Text": "hi"}
    bad = N.handle({"Action": "ShortenURL"}, forward=forwarded.append)
    assert bad == {"ok": False, "error": "ShortenURL needs URL"}
    assert N.handle({"Action": "Nope"}, forward=forwarded.append)["ok"] is False
    assert len(forwarded) == 1
    m = N.chrome_manifest("/x/host", "abc")
    assert m["allowed_origins"] == ["chrome-extension://abc/"] and m["type"] == "stdio" and m["name"] == N.HOST_NAME


def test_native_host_process_reaches_the_running_app():
    """The browser starts the host and writes framed messages; the host forwards them to the running
    ShareL (its app.native action), which uploads the text / shortens the URL."""
    from uihelpers import run_app

    def body(app):
        calls = []
        app.tasks.upload_text = lambda text, ts: calls.append(("text", text))
        app.tasks.shorten_url = lambda url, ts: calls.append(("shorten", url))
        msgs = _frame({"Action": "UploadText", "Text": "from the browser"}) + \
            _frame({"Action": "ShortenURL", "URL": "https://example.com/long"}) + _frame({"Action": "Bogus"})
        proc = subprocess.Popen([sys.executable, "-m", "sharel.tools.native_host"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdin.write(msgs)
        proc.stdin.close()
        for _ in range(300):  # the app's loop must keep running to answer the host's D-Bus calls
            if proc.poll() is not None:
                break
            yield 0.05
        assert proc.returncode == 0, proc.stderr.read().decode()
        replies = _frames(proc.stdout.read())
        assert replies[:2] == [{"ok": True}, {"ok": True}] and replies[2]["ok"] is False
        yield 0.2
        assert calls == [("text", "from the browser"), ("shorten", "https://example.com/long")]
        return True
    assert run_app(body, timeout=40)


def test_readme_matches_the_code():
    """README's commands, hotkey table, keys and paths are the real ones (a stale README fails)."""
    import re

    from sharel.cli import parse_args
    from sharel.core.config import default_hotkeys
    readme = (ROOT / "README.md").read_text()
    # Every script and file it names exists.
    for rel in ("install.sh", "uninstall.sh", "packaging/build-deb.sh", "scripts/featuremap.py", "LICENSE",
                "docs/FEATURE_MAP.md", "docs/PROGRESS.md", "tests/e2e/shell.sh", "tests/js/test_logic.js",
                "scripts/screenshots.py"):
        assert rel in readme and (ROOT / rel).exists(), rel
    # Every screenshot it shows exists and has alt text (scripts/screenshots.py makes them).
    shots = re.findall(r"!\[([^\]]*)\]\((docs/screenshots/[^)]+\.png)\)", readme)
    assert len(shots) >= 10
    for alt, rel in shots:
        assert alt.strip() and (ROOT / rel).is_file(), rel
    # The installer flags it documents are the installer's.
    for flag, script in (("--deps", "install.sh"), ("--dev", "install.sh"), ("--purge", "uninstall.sh")):
        assert f"{flag})" in (ROOT / script).read_text() and flag in readme, flag
    # The extension UUID is the extension's.
    uuid = json.loads((ROOT / "extension/sharel@hawkins.app/metadata.json").read_text())["uuid"]
    assert f"gnome-extensions enable {uuid}" in readme and f"gnome-extensions disable {uuid}" in readme
    # The hotkey table is exactly the default hotkeys.
    table = re.findall(r"^\| ((?:Ctrl\+|Alt\+|Shift\+)*Print) \| (.+?) \|$", readme, re.M)
    accel = {"Ctrl+": "<Control>", "Alt+": "<Alt>", "Shift+": "<Shift>"}

    def to_accel(key: str) -> str:
        for k, v in accel.items():
            key = key.replace(k, v)
        return key
    assert sorted((to_accel(k), name) for k, name in table) == \
        sorted((h.hotkey_info.hotkey, h.name) for h in default_hotkeys())
    # The gsettings keys it tells the user to clear exist in GNOME Shell's schema.
    src = Gio.SettingsSchemaSource.get_default()
    schema = src.lookup("org.gnome.shell.keybindings", True)
    keys = re.findall(r"gsettings set org\.gnome\.shell\.keybindings (\S+) ", readme)
    assert keys == ["show-screenshot-ui", "screenshot", "screenshot-window"]
    assert schema is not None, "GNOME Shell's schema is not installed"
    assert all(schema.has_key(k) for k in keys)
    # Every command-line example parses without errors and does what the text says.
    for example in re.findall(r"`sharel (-[^`<]+)`", readme):
        req = parse_args(example.replace('"', "").split())
        assert not req.errors, (example, req.errors)
    assert [j.value for j, _ in parse_args(["-RectangleRegion"]).jobs] == ["RectangleRegion"]
    assert parse_args(["-workflow", "x"]).workflows == ["x"]
    assert parse_args(["-silent"]).silent and parse_args(["-autoclose"]).autoclose


def test_feature_map_check_passes_and_catches_missing_tests():
    """Every row is done or excluded and every cited test node exists (`featuremap.py check`)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("featuremap_script", ROOT / "scripts" / "featuremap.py")
    fm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fm)
    assert fm.missing_tests("tests/test_packaging_data.py::test_readme_matches_the_code") == []
    assert fm.missing_tests("tests/test_shorteners.py::test_defunct_shorteners_registered_with_evidence[QRnet]") == []
    assert fm.missing_tests("tests/test_packaging_data.py::test_no_such_thing") == \
        ["tests/test_packaging_data.py::test_no_such_thing"]
    assert fm.missing_tests("tests/nope.py::test_x") == ["tests/nope.py"]
    assert fm.missing_tests("-") == ["no test cited in '-'"]
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "featuremap.py"), "check"], capture_output=True,
                       text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout
    assert "'open': 0" in r.stdout and "missing test" not in r.stdout
