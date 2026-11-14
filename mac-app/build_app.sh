#!/bin/bash
# Build "SoundShredder.app" (a native Mac window for this folder's SoundShredder) and
# install it. Run it again after moving the SoundShredder folder.
#   bash mac-app/build_app.sh                 installs to /Applications
#   bash mac-app/build_app.sh ~/Applications  installs somewhere else
# Needs Xcode or the Command Line Tools (swiftc). The icon is rebuilt only when
# AppIcon.icns is missing, which needs rsvg-convert (brew install librsvg).
set -euo pipefail
here="$(cd -- "$(dirname -- "$0")" && pwd)"
root="$(cd -- "$here/.." && pwd)"
dest="${1:-/Applications}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
app="$work/SoundShredder.app"

if [ ! -f "$here/AppIcon.icns" ]; then
    command -v rsvg-convert >/dev/null || { echo "AppIcon.icns is missing and rsvg-convert is not installed."; exit 1; }
    iconset="$work/AppIcon.iconset"
    mkdir -p "$iconset"
    for size in 16 32 128 256 512; do
        rsvg-convert -w "$size" -h "$size" "$here/AppIcon.svg" -o "$iconset/icon_${size}x${size}.png"
        rsvg-convert -w $((size * 2)) -h $((size * 2)) "$here/AppIcon.svg" -o "$iconset/icon_${size}x${size}@2x.png"
    done
    iconutil -c icns "$iconset" -o "$here/AppIcon.icns"
fi

mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
xcrun swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos13.0" \
    "$here/main.swift" -o "$app/Contents/MacOS/SoundShredder" -framework Cocoa -framework WebKit
cp "$here/Info.plist" "$app/Contents/Info.plist"
/usr/bin/plutil -replace SoundShredderRoot -string "$root" "$app/Contents/Info.plist"
cp "$here/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"
codesign --force --sign - "$app"
codesign --verify --strict "$app"

target="$dest/SoundShredder.app"
if [ -e "$target" ]; then
    id="$(/usr/bin/defaults read "$target/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
    if [ "$id" != "local.soundshredder.mac" ]; then
        echo "$target exists and is a different app; not replacing it."
        exit 1
    fi
    rm -rf "$target"
fi
ditto "$app" "$target"
echo "Installed: $target"
