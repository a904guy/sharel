"""install.sh / uninstall.sh in a throwaway HOME, and the .deb's contents."""
import json
import os
import subprocess
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
