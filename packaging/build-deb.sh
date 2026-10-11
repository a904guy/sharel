#!/bin/bash
# Build dist/sharel_<version>_all.deb (system-wide install; updates come from apt).
#
#   packaging/build-deb.sh [OUTDIR]
#
# Python dependencies come from Ubuntu packages (python3-qrcode, python3-paramiko, ...); the
# optional background remover (rembg) is not packaged in Ubuntu: `pip install rembg onnxruntime`.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$repo/dist}
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml" | head -1)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
pkg=$stage/sharel

mkdir -p "$pkg/DEBIAN" "$pkg/usr/bin" "$pkg/usr/lib/python3/dist-packages" "$pkg/usr/share/sharel" \
    "$pkg/usr/share/applications" "$pkg/usr/share/glib-2.0/schemas" \
    "$pkg/usr/share/gnome-shell/extensions/sharel@hawkins.app" "$pkg/usr/share/doc/sharel" \
    "$pkg/usr/lib/mozilla/native-messaging-hosts" "$pkg/etc/opt/chrome/native-messaging-hosts" \
    "$pkg/etc/chromium/native-messaging-hosts"

# Python package (no bytecode; python3 compiles on first use)
(cd "$repo/src" && find sharel -type f -name '*.py' -print0 | while IFS= read -r -d '' f; do
    install -D -m 644 "$f" "$pkg/usr/lib/python3/dist-packages/$f"
done)

for pair in "sharel:sharel.cli" "sharel-native-host:sharel.tools.native_host" "sharel-tray:sharel.ui.tray_helper"; do
    cmd=${pair%%:*}; mod=${pair#*:}
    printf '#!/usr/bin/python3\nimport sys\nfrom %s import main\nsys.exit(main())\n' "$mod" > "$pkg/usr/bin/$cmd"
    chmod 755 "$pkg/usr/bin/$cmd"
done

cp -r "$repo/data/stickers" "$pkg/usr/share/sharel/stickers"
mkdir -p "$pkg/usr/share/sharel/icons"
cp -r "$repo/data/icons/tray" "$pkg/usr/share/sharel/icons/tray"
cp -r "$repo/data/nautilus" "$pkg/usr/share/sharel/nautilus"
cp -r "$repo/browser-extension" "$pkg/usr/share/sharel/browser-extension"
install -m 644 "$repo"/data/applications/*.desktop "$pkg/usr/share/applications/"
install -D -m 644 "$repo/data/icons/hicolor/scalable/apps/app.sharel.ShareL.svg" \
    "$pkg/usr/share/icons/hicolor/scalable/apps/app.sharel.ShareL.svg"
install -D -m 644 "$repo/data/icons/hicolor/symbolic/apps/app.sharel.ShareL-symbolic.svg" \
    "$pkg/usr/share/icons/hicolor/symbolic/apps/app.sharel.ShareL-symbolic.svg"
install -m 644 "$repo/data/glib-2.0/schemas/app.sharel.ShareL.gschema.xml" "$pkg/usr/share/glib-2.0/schemas/"
install -m 644 "$repo"/extension/sharel@hawkins.app/* "$pkg/usr/share/gnome-shell/extensions/sharel@hawkins.app/"
install -m 644 "$repo/README.md" "$pkg/usr/share/doc/sharel/README.md"
install -m 644 "$repo/LICENSE" "$pkg/usr/share/doc/sharel/copyright"

ext_id=$(cat "$repo/browser-extension/EXTENSION_ID")
chrome='{"name": "app.sharel.sharel", "description": "ShareL native messaging host", "path": "/usr/bin/sharel-native-host", "type": "stdio", "allowed_origins": ["chrome-extension://'"$ext_id"'/"]}'
firefox='{"name": "app.sharel.sharel", "description": "ShareL native messaging host", "path": "/usr/bin/sharel-native-host", "type": "stdio", "allowed_extensions": ["sharel@hawkins.app"]}'
printf '%s\n' "$chrome" > "$pkg/etc/opt/chrome/native-messaging-hosts/app.sharel.sharel.json"
printf '%s\n' "$chrome" > "$pkg/etc/chromium/native-messaging-hosts/app.sharel.sharel.json"
printf '%s\n' "$firefox" > "$pkg/usr/lib/mozilla/native-messaging-hosts/app.sharel.sharel.json"

size=$(du -sk "$pkg" | cut -f1)
cat > "$pkg/DEBIAN/control" <<CONTROL
Package: sharel
Version: $version
Section: graphics
Priority: optional
Architecture: all
Installed-Size: $size
Maintainer: Andy Hawkins <andy@hawkins.app>
Depends: python3 (>= 3.12), python3-gi, python3-gi-cairo, python3-cairo, gir1.2-gtk-4.0, gir1.2-adw-1,
 gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0,
 gstreamer1.0-pipewire, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, gstreamer1.0-plugins-ugly,
 gstreamer1.0-libav, python3-pil, python3-numpy, python3-requests, python3-qrcode, python3-paramiko,
 python3-cryptography, python3-lxml, wl-clipboard, tesseract-ocr, tesseract-ocr-eng, zbar-tools, ffmpeg
Recommends: gnome-shell (>= 48), gnome-shell-extension-appindicator, gir1.2-secret-1, cups-client
Description: Screen capture, annotation, recording and sharing (ShareX for GNOME)
 ShareL rebuilds ShareX for the GNOME desktop on Wayland: region, window and screen
 capture, an annotation editor, image effects, screen recording to video or GIF,
 scrolling capture, OCR and QR tools, pin to screen, and uploads to dozens of hosts,
 URL shorteners and sharing services, including ShareX custom uploaders (.sxcu).
 .
 Enable the bundled GNOME Shell extension after installing (log out and back in, then
 run: gnome-extensions enable sharel@hawkins.app).
CONTROL

mkdir -p "$out"
deb=$out/sharel_${version}_all.deb
dpkg-deb --root-owner-group --build "$pkg" "$deb" >/dev/null
echo "$deb"
