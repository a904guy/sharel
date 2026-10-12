#!/bin/bash
# Remove what install.sh installed (from its manifest). --purge also removes settings and history
# (~/.config/ShareL, ~/.local/share/ShareL). It never touches enabled-extension settings; disable
# the extension first with `gnome-extensions disable sharel@hawkins.app` if it is enabled.
set -euo pipefail

purge=0
for arg in "$@"; do
    case "$arg" in
    --purge) purge=1 ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "uninstall.sh: unknown option $arg" >&2; exit 2 ;;
    esac
done

data_home=${XDG_DATA_HOME:-$HOME/.local/share}
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
share=$data_home/sharel
manifest=$share/install-manifest.txt

if [ ! -f "$manifest" ]; then
    echo "uninstall.sh: no install manifest at $manifest (nothing to remove)" >&2
    exit 1
fi

removed=0
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
    "$HOME"/*) ;;
    *) echo "uninstall.sh: skipping $path (outside \$HOME)" >&2; continue ;;
    esac
    if [ -d "$path" ] && [ ! -L "$path" ]; then
        rm -rf --one-file-system "$path"
    else
        rm -f "$path"
    fi
    removed=$((removed + 1))
done < "$manifest"

rm -f "$manifest"
for d in "$data_home/gnome-shell/extensions/sharel@hawkins.app" "$share/stickers" "$share/icons" "$share"; do
    [ -d "$d" ] && find "$d" -depth -type d -empty -delete 2>/dev/null || true
done
if [ -d "$data_home/glib-2.0/schemas" ] && command -v glib-compile-schemas >/dev/null; then
    glib-compile-schemas "$data_home/glib-2.0/schemas" 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q "$data_home/applications" 2>/dev/null || true
if [ -d "$data_home/mime/packages" ] && command -v update-mime-database >/dev/null; then
    update-mime-database "$data_home/mime" 2>/dev/null || true
fi
rm -f "$config_home/autostart/app.sharel.ShareL.desktop"

if [ "$purge" = 1 ]; then
    rm -rf --one-file-system "$config_home/ShareL" "$data_home/ShareL"
fi
echo "ShareL removed ($removed entries)$([ "$purge" = 1 ] && echo ', settings and history purged')."
