#!/usr/bin/env bash
# Build the drag-to-Applications disk image from the signed (and, in releases, stapled) Huddle.app:
#   Huddle-<version>-macos-arm64.dmg next to the bundle, with the brand background, the app icon
#   and an Applications alias laid out in a fixed Finder window. The image is signed and, when
#   HUDDLE_NOTARY_PROFILE is set, notarized and stapled too, so it opens without warnings.
# Tauri's own dmg bundler is not used: it runs before build-app.sh repairs the engine's symlinks
# and signs, so its image would carry the broken bundle.
# Usage: scripts/make-dmg.sh   (after scripts/build-app.sh; package-release.sh calls it)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_DIR="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/macos"
APP="$BUNDLE_DIR/Huddle.app"
[ -d "$APP" ] || { echo "No Huddle.app at $APP — run scripts/build-app.sh first" >&2; exit 1; }
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")"
OUT="$BUNDLE_DIR/Huddle-$VERSION-macos-arm64.dmg"
VOL="Huddle"
STAGE="$BUNDLE_DIR/dmg-stage"
RW="$BUNDLE_DIR/Huddle-rw.dmg"
# Window geometry; must match scripts/make-dmg-background.swift.
WIN_W=660; WIN_H=420; APP_X=165; APPS_X=495; ICON_Y=190

# Any leftover mount of a previous run would make the AppleScript talk to the wrong disk.
for v in /Volumes/"$VOL" /Volumes/"$VOL "*; do [ -d "$v" ] && hdiutil detach "$v" -force >/dev/null 2>&1 || true; done
rm -rf "$STAGE" "$RW" "$OUT"
mkdir -p "$STAGE/.background"

# 1. Background (1× + 2× in one TIFF so Retina Finder picks the sharp one).
swift "$ROOT/scripts/make-dmg-background.swift" "$ROOT/apps/desktop/src/assets/huddle-logo.svg" "$STAGE/.background" >/dev/null
tiffutil -cathidpicheck "$STAGE/.background/background.png" "$STAGE/.background/background@2x.png" -out "$STAGE/.background/background.tiff" >/dev/null 2>&1
rm -f "$STAGE/.background/background.png" "$STAGE/.background/background@2x.png"

# 2. Contents: the app (ditto keeps symlinks + signature) and an Applications alias.
ditto "$APP" "$STAGE/Huddle.app"
ln -s /Applications "$STAGE/Applications"

# 3. Writable image, laid out through Finder, then compressed read-only.
hdiutil create -volname "$VOL" -srcfolder "$STAGE" -fs HFS+ -format UDRW -ov "$RW" >/dev/null
DEV="$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | grep -E '^/dev/' | head -1 | awk '{print $1}')"
MNT="/Volumes/$VOL"
[ -d "$MNT" ] || { echo "image did not mount at $MNT" >&2; exit 1; }
# (No custom volume icon: Finder on macOS 26 removes a .VolumeIcon.icns from the volume again.)
# Finder must see the window once to write the .DS_Store with our layout.
osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set sidebar width of container window to 0
    set the bounds of container window to {400, 200, $((400 + WIN_W)), $((200 + WIN_H))}
    set viewOptions to the icon view options of container window
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 128
    set text size of viewOptions to 13
    set label position of viewOptions to bottom
    set background picture of viewOptions to file ".background:background.tiff"
    set position of item "Huddle.app" of container window to {$APP_X, $ICON_Y}
    set position of item "Applications" of container window to {$APPS_X, $ICON_Y}
    close
    open
    update without registering applications
    delay 2
    close
  end tell
end tell
APPLESCRIPT
# Finder writes the .DS_Store asynchronously; detaching before the background alias is in it
# (seen with the 750 MB volume) gives an image with icon positions but a plain background.
for _ in $(seq 1 30); do
  if python3 -c 'import sys; sys.exit(0 if b"backgroundImageAlias" in open(sys.argv[1], "rb").read() else 1)' "$MNT/.DS_Store" 2>/dev/null; then break; fi
  sleep 1
done
python3 -c 'import sys; sys.exit(0 if b"backgroundImageAlias" in open(sys.argv[1], "rb").read() else 1)' "$MNT/.DS_Store" \
  || { echo "Finder did not save the window layout" >&2; hdiutil detach "$DEV" -force >/dev/null; exit 1; }
sync; sleep 1
hdiutil detach "$DEV" >/dev/null || { sleep 3; hdiutil detach "$DEV" -force >/dev/null; }
hdiutil convert "$RW" -format ULFO -o "$OUT" >/dev/null
rm -rf "$RW" "$STAGE"

# 4. Sign; notarize + staple when a notary profile is configured (the app inside is already stapled).
IDENTITY="${HUDDLE_SIGN_IDENTITY:--}"
codesign --force --sign "$IDENTITY" --timestamp "$OUT" 2>/dev/null || codesign --force --sign "$IDENTITY" "$OUT"
if [ -n "${HUDDLE_NOTARY_PROFILE:-}" ] && [ "$IDENTITY" != "-" ]; then
  echo "submitting the disk image to Apple's notary service…"
  if ! xcrun notarytool submit "$OUT" --keychain-profile "$HUDDLE_NOTARY_PROFILE" --wait 2>&1 | tee "$BUNDLE_DIR/notary-dmg.log" | grep "status: Accepted" >/dev/null; then
    ID="$(grep -m1 "id: " "$BUNDLE_DIR/notary-dmg.log" | awk '{print $2}')"
    [ -n "$ID" ] && xcrun notarytool log "$ID" --keychain-profile "$HUDDLE_NOTARY_PROFILE" || true
    echo "disk image notarization failed — see the log above" >&2; exit 1
  fi
  xcrun stapler staple "$OUT" >/dev/null
  spctl --assess --type open --context context:primary-signature -v "$OUT"
fi
echo "Disk image: $OUT ($(du -h "$OUT" | cut -f1))"
