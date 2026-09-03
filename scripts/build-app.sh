#!/usr/bin/env bash
# Build the macOS app bundle: Tauri release build + the bundled engine, then repair what the
# bundler breaks.
#
# Tauri copies `bundle.resources` by dereferencing symlinks. PyInstaller's engine relies on a few
# symlinks (`_internal/libmlx.dylib -> mlx/lib/libmlx.dylib`, libjaccl likewise): MLX locates its
# Metal shader library *next to the real libmlx.dylib*, so a dereferenced copy at the top level
# makes MLX fail with "Failed to load the default metallib" and Huddle silently falls back to the
# CPU Whisper. This script recreates every symlink of the dist folder inside the .app.
#
# Prerequisites: scripts/fetch-speaker-models.sh (once) and scripts/build-engine.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/engine/dist.nosync/huddle-engine"
[ -x "$DIST/huddle-engine" ] || { echo "engine sidecar missing — run scripts/build-engine.sh first" >&2; exit 1; }
[ -f "$ROOT/apps/desktop/src-tauri/resources/models/speaker/nemo_titanet_large.onnx" ] || { echo "speaker models missing — run scripts/fetch-speaker-models.sh first" >&2; exit 1; }

cd "$ROOT/apps/desktop"
npx tauri build --bundles app "$@"

APP="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/macos/Huddle.app"
ENGINE="$APP/Contents/Resources/engine"
restored=0
while IFS= read -r -d '' link; do
  rel="${link#"$DIST"/}"
  target="$(readlink "$link")"
  rm -f "$ENGINE/$rel"
  ln -s "$target" "$ENGINE/$rel"
  restored=$((restored + 1))
done < <(find "$DIST" -type l -print0)
echo "restored $restored symlinks inside $ENGINE"
du -sh "$APP" | cut -f1
echo "Built $APP"
