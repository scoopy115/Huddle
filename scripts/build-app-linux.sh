#!/usr/bin/env bash
# Build the Linux packages: Tauri release build (.deb + AppImage) with the bundled engine.
# Output: apps/desktop/src-tauri/target.nosync/release/bundle/{deb,appimage}/, and release copies
# named Huddle-<version>-linux-<x64|arm64>.{deb,AppImage} next to them.
#
# Prerequisites: Node 20+, Rust (rustup), scripts/fetch-speaker-models.sh (once), scripts/build-engine.sh,
# and the system libraries Tauri needs (Debian/Ubuntu):
#   sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev \
#        libayatana-appindicator3-dev librsvg2-dev libasound2-dev patchelf
# Build on the oldest distribution you want to support: the engine and the app need at least its glibc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIDECAR="$ROOT/engine/dist.nosync/huddle-engine/huddle-engine"
[ -x "$SIDECAR" ] || { echo "engine sidecar missing — run scripts/build-engine.sh first" >&2; exit 1; }
NEWER="$(find "$ROOT/engine/huddle_engine" -name '*.py' -newer "$SIDECAR" -print -quit)"
if [ -n "$NEWER" ]; then
  echo "engine sources are newer than the built sidecar ($(basename "$NEWER") …) — run scripts/build-engine.sh first" >&2
  exit 1
fi
[ -f "$ROOT/apps/desktop/src-tauri/resources/models/speaker/nemo_titanet_large.onnx" ] || { echo "speaker models missing — run scripts/fetch-speaker-models.sh first" >&2; exit 1; }

cd "$ROOT/apps/desktop"
npx tauri build --bundles deb,appimage "$@"

VERSION="$(node -p "require('./src-tauri/tauri.conf.json').version")"
case "$(uname -m)" in x86_64) ARCH=x64 ;; aarch64|arm64) ARCH=arm64 ;; *) ARCH="$(uname -m)" ;; esac
OUT="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle"
for ext in deb AppImage; do
  dir="$OUT/$(echo "$ext" | tr 'A-Z' 'a-z')"
  src="$(find "$dir" -maxdepth 1 -name "*.$ext" -print -quit 2>/dev/null || true)"
  [ -n "$src" ] || { echo "no .$ext produced in $dir" >&2; continue; }
  asset="$dir/Huddle-$VERSION-linux-$ARCH.$ext"
  cp -f "$src" "$asset"
  echo "Release asset: $asset ($(du -h "$asset" | cut -f1)) — tag the release v$VERSION"
done
