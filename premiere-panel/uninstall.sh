#!/bin/bash
# Remove the SoundShredder panel from Premiere Pro. Your stems and projects are not touched.
TARGET="$HOME/Library/Application Support/Adobe/CEP/extensions/com.soundshredder.premiere"
if [ -L "$TARGET" ]; then rm "$TARGET" && echo "Removed. Restart Premiere Pro."; else echo "The panel is not installed."; fi
