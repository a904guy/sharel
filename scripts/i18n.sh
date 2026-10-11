#!/bin/sh
# Extract translatable strings to po/sharel.pot and compile po/*.po into build/locale.
set -eu
cd "$(dirname "$0")/.."
find src/sharel -name '*.py' | sort > po/POTFILES
xgettext --language=Python --keyword=_ --keyword=N_ --keyword=ngettext:1,2 --from-code=UTF-8 \
    --package-name=ShareL --package-version=0.1.0 --files-from=po/POTFILES -o po/sharel.pot
for po in po/*.po; do
    [ -e "$po" ] || continue
    lang=$(basename "$po" .po)
    mkdir -p "build/locale/$lang/LC_MESSAGES"
    msgfmt -o "build/locale/$lang/LC_MESSAGES/sharel.mo" "$po"
done
echo "po/sharel.pot updated"
