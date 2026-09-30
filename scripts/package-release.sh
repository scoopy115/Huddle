#!/usr/bin/env bash
# Package the built Huddle.app for a GitHub release: notarize and staple the app, then build the
# drag-to-Applications disk image (scripts/make-dmg.sh), which is the one release asset — new
# users download it and the in-app updater (0.6.2+) opens it. The zip releases of 0.5.2–0.6.1 are
# no longer produced; those versions show "Open download page" instead of downloading.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_DIR="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/macos"
APP="$BUNDLE_DIR/Huddle.app"
[ -d "$APP" ] || { echo "No Huddle.app at $APP — run scripts/build-app.sh first" >&2; exit 1; }
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")"
OUT="$BUNDLE_DIR/Huddle-$VERSION-macos-arm64.dmg"
# A zip from an earlier run would look like a current asset next to the new image.
rm -f "$BUNDLE_DIR/Huddle-$VERSION-macos-arm64.zip"

# Notarize when a keychain profile is configured (see docs/PACKAGING.md):
#   xcrun notarytool store-credentials huddle-notary --apple-id you@example.com --team-id TEAMID --password <app-specific password>
# notarytool takes the app as a temporary zip (ditto without xattrs: AppleDouble `._*` sidecars
# would break the seal); the ticket is then stapled to the app so Gatekeeper accepts it offline.
if [ -n "${HUDDLE_NOTARY_PROFILE:-}" ]; then
  # (no `grep -q` in pipelines here: with pipefail, grep exiting early makes the producer fail on SIGPIPE)
  SIG="$(codesign -dvv "$APP" 2>&1 || true)"
  case "$SIG" in *"Authority=Developer ID Application"*) ;; *) echo "Notarization needs a Developer ID signature — build with HUDDLE_SIGN_IDENTITY set" >&2; exit 1;; esac
  TMP="$BUNDLE_DIR/Huddle-notary.zip"
  ditto -c -k --keepParent --norsrc --noextattr --noqtn "$APP" "$TMP"
  echo "submitting to Apple's notary service (usually 1–5 minutes)…"
  if ! xcrun notarytool submit "$TMP" --keychain-profile "$HUDDLE_NOTARY_PROFILE" --wait 2>&1 | tee "$BUNDLE_DIR/notary.log" | grep "status: Accepted" >/dev/null; then
    ID="$(grep -m1 "id: " "$BUNDLE_DIR/notary.log" | awk '{print $2}')"
    [ -n "$ID" ] && xcrun notarytool log "$ID" --keychain-profile "$HUDDLE_NOTARY_PROFILE" || true
    echo "notarization failed — see the log above" >&2; exit 1
  fi
  rm -f "$TMP"
  xcrun stapler staple "$APP"
  spctl --assess --type execute -v "$APP"
fi

"$ROOT/scripts/make-dmg.sh"
echo "Release asset: $OUT ($(du -h "$OUT" | cut -f1)) — tag the release v$VERSION"
