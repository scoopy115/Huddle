#!/usr/bin/env bash
# Zip the built Huddle.app for a GitHub release. ditto keeps symlinks, resource forks and the
# code signature intact; unzip would not. Output sits next to the bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_DIR="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/macos"
APP="$BUNDLE_DIR/Huddle.app"
[ -d "$APP" ] || { echo "No Huddle.app at $APP — run scripts/build-app.sh first" >&2; exit 1; }
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")"
OUT="$BUNDLE_DIR/Huddle-$VERSION-macos-arm64.zip"
rm -f "$OUT"
ditto -c -k --keepParent "$APP" "$OUT"
echo "Release asset: $OUT ($(du -h "$OUT" | cut -f1)) — tag the release v$VERSION"
