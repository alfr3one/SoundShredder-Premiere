#!/bin/bash
# Install the SoundShredder panel for Premiere Pro (macOS). Run once, then restart Premiere Pro
# and open Window › Extensions › SoundShredder. Re-run it after moving the SoundShredder folder.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EXTENSIONS="$HOME/Library/Application Support/Adobe/CEP/extensions"
TARGET="$EXTENSIONS/com.soundshredder.premiere"
mkdir -p "$EXTENSIONS"
if [ -L "$TARGET" ]; then
  rm "$TARGET"
elif [ -e "$TARGET" ]; then
  echo "Remove the existing folder first: $TARGET" >&2
  exit 1
fi
# A link, not a copy: updating SoundShredder updates the panel too.
ln -s "$HERE" "$TARGET"
# The panel is not signed by Adobe, so Premiere must be allowed to load unsigned panels.
for version in 9 10 11 12 13; do
  defaults write "com.adobe.CSXS.$version" PlayerDebugMode 1
done
echo "Installed the SoundShredder panel."
echo "Restart Premiere Pro, then open Window › Extensions › SoundShredder."
