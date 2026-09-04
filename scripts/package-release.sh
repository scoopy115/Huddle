#!/usr/bin/env bash
# Zip the built Huddle.app for a GitHub release. ditto keeps symlinks and the code signature
# intact. Extended attributes / resource forks are deliberately left out (--norsrc --noextattr
# --noqtn): ditto would store them as AppleDouble `._*` sidecars, which Archive Utility unpacks as
# real files inside the bundle — extra files break the seal and Gatekeeper reports "damaged".
# The signature never covers xattrs, so nothing is lost. Output sits next to the bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_DIR="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/macos"
APP="$BUNDLE_DIR/Huddle.app"
[ -d "$APP" ] || { echo "No Huddle.app at $APP — run scripts/build-app.sh first" >&2; exit 1; }
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")"
OUT="$BUNDLE_DIR/Huddle-$VERSION-macos-arm64.zip"
rm -f "$OUT"

# Notarize when a keychain profile is configured (see docs/PACKAGING.md):
#   xcrun notarytool store-credentials huddle-notary --apple-id you@example.com --team-id TEAMID --password <app-specific password>
# Apple's service scans the zip; the ticket is then stapled to the app so Gatekeeper accepts it offline.
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

ditto -c -k --keepParent --norsrc --noextattr --noqtn "$APP" "$OUT"
echo "Release asset: $OUT ($(du -h "$OUT" | cut -f1)) — tag the release v$VERSION"
