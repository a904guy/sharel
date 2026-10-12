"""install.sh / uninstall.sh in a throwaway HOME, and the .deb's contents."""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sandbox_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), XDG_DATA_HOME=str(home / ".local/share"), XDG_CONFIG_HOME=str(home / ".config"))
    env.pop("SHAREL_BIN_DIR", None)
    return env


def test_install_and_uninstall_dev_mode(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = sandbox_env(home)
    out = subprocess.run([str(ROOT / "install.sh"), "--dev"], env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr + out.stdout
    share = home / ".local/share"
    expect = [
        home / ".local/bin/sharel", home / ".local/bin/sharel-native-host",
        share / "applications/app.sharel.ShareL.desktop",
        share / "mime/packages/app.sharel.ShareL.xml",
        share / "sharel/icons/actions/sharel-draw-arrow-symbolic.svg",
        share / "icons/hicolor/scalable/apps/app.sharel.ShareL.svg",
        share / "glib-2.0/schemas/gschemas.compiled",
        share / "gnome-shell/extensions/sharel@hawkins.app/extension.js",
        share / "gnome-shell/extensions/sharel@hawkins.app/metadata.json",
        share / "nautilus/scripts/Upload with ShareL", share / "nautilus/scripts/Edit with ShareL",
        share / "sharel/icons/tray/sharel-tray.svg", share / "sharel/stickers/Twemoji",
        home / ".config/google-chrome/NativeMessagingHosts/app.sharel.sharel.json",
        home / ".mozilla/native-messaging-hosts/app.sharel.sharel.json",
    ]
    for p in expect:
        assert p.exists(), p
    desktop = (share / "applications/app.sharel.ShareL.desktop").read_text()
    assert f"Exec={home}/.local/bin/sharel %F" in desktop
    chrome = json.loads((home / ".config/google-chrome/NativeMessagingHosts/app.sharel.sharel.json").read_text())
    ext_id = (ROOT / "browser-extension/EXTENSION_ID").read_text().strip()
    assert chrome["allowed_origins"] == [f"chrome-extension://{ext_id}/"]
    assert chrome["path"] == str(home / ".local/bin/sharel-native-host")
    # the launcher really runs ShareL
    v = subprocess.run([str(home / ".local/bin/sharel"), "--version"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert v.stdout.strip() == "ShareL 0.1.0"
    assert "gnome-extensions enable sharel@hawkins.app" in out.stdout
    # uninstall removes every file it installed
    u = subprocess.run([str(ROOT / "uninstall.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert u.returncode == 0, u.stderr
    for p in expect:
        assert not p.exists(), p
    assert not (share / "sharel/install-manifest.txt").exists()


def test_deb_contents(tmp_path):
    out = subprocess.run([str(ROOT / "packaging/build-deb.sh"), str(tmp_path)], capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr
    deb = Path(out.stdout.strip())
    assert deb.name == "sharel_0.1.0_all.deb" and deb.exists()
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    for path in ("./usr/bin/sharel", "./usr/lib/python3/dist-packages/sharel/app.py",
                 "./usr/share/gnome-shell/extensions/sharel@hawkins.app/extension.js",
                 "./usr/share/applications/app.sharel.ShareL.desktop",
                 "./usr/share/mime/packages/app.sharel.ShareL.xml",
                 "./usr/share/sharel/icons/actions/sharel-draw-arrow-symbolic.svg",
                 "./usr/share/glib-2.0/schemas/app.sharel.ShareL.gschema.xml",
                 "./usr/share/sharel/stickers/Twemoji/",
                 "./usr/lib/mozilla/native-messaging-hosts/app.sharel.sharel.json",
                 "./etc/opt/chrome/native-messaging-hosts/app.sharel.sharel.json"):
        assert path in listing, path
    control = subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Depends"], capture_output=True,
                             text=True, check=True).stdout
    assert "Package: sharel" in control and "gir1.2-adw-1" in control and "zbar-tools" in control
    # the packaged launcher imports the packaged module
    launcher = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    assert b"from sharel.cli import main" in launcher


# Every non-stdlib module src imports, and the Ubuntu package that ships it. A new import fails the test below
# until it is added here and to the deb's Depends (or Recommends, for optional features).
PY_PACKAGES = {"PIL": "python3-pil", "cairo": "python3-cairo", "cryptography": "python3-cryptography",
               "gi": "python3-gi", "lxml": "python3-lxml", "numpy": "python3-numpy", "paramiko": "python3-paramiko",
               "qrcode": "python3-qrcode", "requests": "python3-requests"}
PY_UNPACKAGED = {"rembg", "onnxruntime"}  # background remover: not in Ubuntu, pip install (build-deb.sh header)
GI_PACKAGES = {("Adw", "1"): "gir1.2-adw-1", ("AyatanaAppIndicator3", "0.1"): "gir1.2-ayatanaappindicator3-0.1",
               ("Gdk", "4.0"): "gir1.2-gtk-4.0", ("Gtk", "4.0"): "gir1.2-gtk-4.0", ("Gtk", "3.0"): "gir1.2-gtk-3.0",
               ("Gdk", "3.0"): "gir1.2-gtk-3.0", ("GdkPixbuf", "2.0"): "gir1.2-gdkpixbuf-2.0",
               ("Gio", "2.0"): "gir1.2-glib-2.0", ("GLib", "2.0"): "gir1.2-glib-2.0",
               ("GObject", "2.0"): "gir1.2-glib-2.0", ("Gst", "1.0"): "gir1.2-gstreamer-1.0",
               ("GstVideo", "1.0"): "gir1.2-gst-plugins-base-1.0", ("GstApp", "1.0"): "gir1.2-gst-plugins-base-1.0",
               ("GstPbutils", "1.0"): "gir1.2-gst-plugins-base-1.0", ("Pango", "1.0"): "gir1.2-pango-1.0",
               ("PangoCairo", "1.0"): "gir1.2-pango-1.0", ("Secret", "1"): "gir1.2-secret-1"}
GI_DEFAULT_VERSION = {"GObject": "2.0", "GLib": "2.0", "Gio": "2.0"}  # imported without require_version


def _control_field(name: str) -> set[str]:
    """Package names in the Depends/Recommends field of the control file build-deb.sh writes."""
    text = (ROOT / "packaging/build-deb.sh").read_text()
    control = text.split("<<CONTROL\n", 1)[1].split("\nCONTROL\n", 1)[0]
    m = re.search(rf"^{name}: (.*(?:\n .*)*)", control, re.M)
    assert m, name
    return {d.split("(")[0].strip() for d in m.group(1).replace("\n", " ").split(",") if d.strip()}


def _src_imports() -> tuple[set[str], set[tuple[str, str]]]:
    """(top-level non-stdlib modules, (GI namespace, version) pairs) imported anywhere under src/sharel."""
    modules, versions, namespaces = set(), set(), set()
    for path in (ROOT / "src/sharel").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module == "gi.repository":
                    namespaces |= {a.name for a in node.names}
            else:
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "require_version":
                    versions.add(tuple(a.value for a in node.args))
                continue
            modules |= {n.split(".")[0] for n in names}
    modules -= set(sys.stdlib_module_names) | {"sharel"}
    pairs = set(versions)
    for ns in namespaces - {v[0] for v in versions}:
        assert ns in GI_DEFAULT_VERSION, f"gi.repository.{ns} imported without require_version"
        pairs.add((ns, GI_DEFAULT_VERSION[ns]))
    return modules, pairs


def test_deb_depends_cover_every_import():
    """The .deb installs into /usr/lib/python3/dist-packages and runs on the system python3 only, so every
    module src imports must come from a package the deb depends on (an import missing from Depends works in the
    uv venv and on a dev machine, then fails on a clean install)."""
    depends, recommends = _control_field("Depends"), _control_field("Recommends")
    modules, gi = _src_imports()
    assert {"PIL", "gi", "qrcode"} <= modules and ("Adw", "1") in gi and ("Pango", "1.0") in gi  # the scan works
    assert not modules - PY_PACKAGES.keys() - PY_UNPACKAGED, "add to PY_PACKAGES and Depends"
    assert not gi - GI_PACKAGES.keys(), "add to GI_PACKAGES and Depends"
    need = {PY_PACKAGES[m] for m in modules - PY_UNPACKAGED} | {GI_PACKAGES[p] for p in gi} | {"python3-gi-cairo"}
    optional = {"gir1.2-secret-1"}  # keyring: settings fall back to the config file without it
    assert not need - optional - depends, need - optional - depends
    assert not optional - depends - recommends
    # external programs ShareL runs
    assert {"wl-clipboard", "tesseract-ocr", "zbar-tools", "ffmpeg", "xdg-utils", "fontconfig"} <= depends
