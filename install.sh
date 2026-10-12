#!/bin/bash
# Install ShareL for the current user (no root needed, except for --deps).
#
#   ./install.sh            venv in ~/.local/share/sharel/venv + pip install of this checkout
#   ./install.sh --dev      launchers that run this checkout with its uv venv (for development)
#   ./install.sh --deps     also apt-install the system packages ShareL needs (asks for sudo)
#
# It installs: the `sharel`, `sharel-native-host` and `sharel-tray` commands (~/.local/bin), the
# desktop entries, icons, GSettings schema, the GNOME Shell extension (sharel@hawkins.app),
# Nautilus scripts ("Upload with ShareL", "Edit with ShareL", "Pin to screen with ShareL"),
# browser native-messaging manifests, and data (stickers, tray icons). Every file it writes is
# listed in ~/.local/share/sharel/install-manifest.txt, which uninstall.sh uses.
#
# It never enables the extension or changes keybindings: after installing, log out and back in,
# then run `gnome-extensions enable sharel@hawkins.app` (see README).
set -euo pipefail

repo=$(cd "$(dirname "$0")" && pwd)
mode=venv
deps=0
for arg in "$@"; do
    case "$arg" in
    --dev) mode=dev ;;
    --deps) deps=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
    esac
done

bin_dir=${SHAREL_BIN_DIR:-$HOME/.local/bin}
data_home=${XDG_DATA_HOME:-$HOME/.local/share}
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
share=$data_home/sharel
venv=$share/venv
manifest=$share/install-manifest.txt

APT_PACKAGES="python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 \
gir1.2-gst-plugins-base-1.0 gir1.2-secret-1 gstreamer1.0-pipewire gstreamer1.0-plugins-good \
gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav python3-cairo python3-venv \
wl-clipboard tesseract-ocr tesseract-ocr-eng zbar-tools ffmpeg gnome-shell-extension-appindicator"

missing=()
for p in $APT_PACKAGES; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
    if [ "$deps" = 1 ]; then
        sudo apt-get install -y "${missing[@]}"
    else
        echo "install.sh: these system packages are missing (run with --deps to install them):" >&2
        echo "  sudo apt install ${missing[*]}" >&2
    fi
fi

mkdir -p "$share" "$bin_dir"
: > "$manifest.new"
record() { printf '%s\n' "$1" >> "$manifest.new"; }

put() {  # put SRC DEST [mode]
    mkdir -p "$(dirname "$2")"
    install -m "${3:-644}" "$1" "$2"
    record "$2"
}

# ---- the program ------------------------------------------------------------------------------
if [ "$mode" = venv ]; then
    if command -v uv >/dev/null; then
        uv venv -q --allow-existing --python /usr/bin/python3 --system-site-packages "$venv"
        uv pip install -q --python "$venv/bin/python" "$repo"
    else
        /usr/bin/python3 -m venv --system-site-packages "$venv"
        "$venv/bin/pip" install -q "$repo"
    fi
    for cmd in sharel sharel-native-host sharel-tray; do
        ln -sfn "$venv/bin/$cmd" "$bin_dir/$cmd"
        record "$bin_dir/$cmd"
    done
    record "$venv"
else
    [ -x "$repo/.venv/bin/python" ] || (cd "$repo" && uv venv -q --python /usr/bin/python3 --system-site-packages \
        && uv sync -q)
    for pair in "sharel:sharel.cli" "sharel-native-host:sharel.tools.native_host" "sharel-tray:sharel.ui.tray_helper"; do
        cmd=${pair%%:*}; mod=${pair#*:}
        cat > "$bin_dir/$cmd" <<LAUNCH
#!/bin/sh
exec "$repo/.venv/bin/python" -m $mod "\$@"
LAUNCH
        chmod 755 "$bin_dir/$cmd"
        record "$bin_dir/$cmd"
    done
fi
sharel_bin=$bin_dir/sharel
host_bin=$bin_dir/sharel-native-host

# ---- data ---------------------------------------------------------------------------------------
for f in "$repo"/data/stickers/*/* "$repo"/data/icons/tray/*; do
    rel=${f#"$repo"/data/}
    put "$f" "$share/$rel"
done

# ---- desktop integration ----------------------------------------------------------------------
for f in "$repo"/data/applications/*.desktop; do
    dest=$data_home/applications/$(basename "$f")
    mkdir -p "$(dirname "$dest")"
    sed -e "s#^Exec=sharel#Exec=$sharel_bin#" -e "s#^TryExec=sharel#TryExec=$sharel_bin#" "$f" > "$dest"
    record "$dest"
done
put "$repo/data/icons/hicolor/scalable/apps/app.sharel.ShareL.svg" \
    "$data_home/icons/hicolor/scalable/apps/app.sharel.ShareL.svg"
put "$repo/data/icons/hicolor/symbolic/apps/app.sharel.ShareL-symbolic.svg" \
    "$data_home/icons/hicolor/symbolic/apps/app.sharel.ShareL-symbolic.svg"
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -q -t -f "$data_home/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$data_home/applications" || true
# .sxcu files open in ShareL, which imports them as custom uploaders
put "$repo/data/mime/packages/app.sharel.ShareL.xml" "$data_home/mime/packages/app.sharel.ShareL.xml"
command -v update-mime-database >/dev/null && update-mime-database "$data_home/mime" 2>/dev/null || true

put "$repo/data/glib-2.0/schemas/app.sharel.ShareL.gschema.xml" \
    "$data_home/glib-2.0/schemas/app.sharel.ShareL.gschema.xml"
glib-compile-schemas "$data_home/glib-2.0/schemas"

ext_dest=$data_home/gnome-shell/extensions/sharel@hawkins.app
for f in "$repo"/extension/sharel@hawkins.app/*; do
    put "$f" "$ext_dest/$(basename "$f")"
done

for f in "$repo"/data/nautilus/scripts/*; do
    dest="$data_home/nautilus/scripts/$(basename "$f")"
    mkdir -p "$(dirname "$dest")"
    sed "s#^exec sharel#exec \"$sharel_bin\"#" "$f" > "$dest"
    chmod 755 "$dest"
    record "$dest"
done

# ---- browser native messaging -----------------------------------------------------------------
ext_id=$(cat "$repo/browser-extension/EXTENSION_ID")
chrome_json=$(printf '{"name": "app.sharel.sharel", "description": "ShareL native messaging host", "path": "%s", "type": "stdio", "allowed_origins": ["chrome-extension://%s/"]}\n' "$host_bin" "$ext_id")
firefox_json=$(printf '{"name": "app.sharel.sharel", "description": "ShareL native messaging host", "path": "%s", "type": "stdio", "allowed_extensions": ["sharel@hawkins.app"]}\n' "$host_bin")
for d in "$config_home/google-chrome/NativeMessagingHosts" "$config_home/chromium/NativeMessagingHosts" \
         "$config_home/BraveSoftware/Brave-Browser/NativeMessagingHosts"; do
    mkdir -p "$d"
    printf '%s\n' "$chrome_json" > "$d/app.sharel.sharel.json"
    record "$d/app.sharel.sharel.json"
done
mkdir -p "$HOME/.mozilla/native-messaging-hosts"
printf '%s\n' "$firefox_json" > "$HOME/.mozilla/native-messaging-hosts/app.sharel.sharel.json"
record "$HOME/.mozilla/native-messaging-hosts/app.sharel.sharel.json"

mv "$manifest.new" "$manifest"
record_count=$(wc -l < "$manifest")

cat <<DONE
ShareL is installed ($record_count files, mode: $mode).

Next steps:
  1. Log out and back in (GNOME Shell loads new extensions at login), then run:
       gnome-extensions enable sharel@hawkins.app
     Without the extension ShareL still captures (Mutter ScreenCast) but window capture,
     global hotkeys through the shell, the clipboard from the background and window placement
     need it.
  2. GNOME binds Print to its own screenshot tool. To give Print to ShareL, clear the
     "Take a screenshot" shortcuts in Settings > Keyboard > Keyboard Shortcuts > Screenshots.
  3. Browser extension: load $repo/browser-extension as an unpacked extension
     (Chrome: chrome://extensions, Developer mode; Firefox: about:debugging).
  4. Start ShareL from the app grid, or run: sharel
DONE
