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

# Tauri signs the bundle before the resources are complete (and the symlink restore above changes
# them), so the seal no longer matches; sign again here, innermost first. Apple discourages
# `--deep`, and notarization wants every nested Mach-O signed with the hardened runtime and a
# secure timestamp, so each binary is signed on its own:
#   engine dylibs/.so/executables → engine.plist (JIT + library validation off, PyInstaller)
#   huddle-audio-tap              → helper.plist (audio input)
#   Huddle.app                    → app.plist    (audio input)
# HUDDLE_SIGN_IDENTITY: "Developer ID Application: Name (TEAMID)" for releases, a self-signed
# certificate for local builds that keep their permissions, "-" (ad hoc, the default) otherwise.
# macOS ties Microphone and System Audio permissions to the signature, so ad-hoc builds lose them
# with every install. Notarization happens in scripts/package-release.sh.
# Extended attributes (Finder info, provenance) on files inside the bundle make codesign refuse
# the seal ("resource fork, Finder information, or similar detritus not allowed"); strip them first.
xattr -cr "$APP"
IDENTITY="${HUDDLE_SIGN_IDENTITY:--}"
ENT="$ROOT/apps/desktop/src-tauri/entitlements"
FLAGS=(--force --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ]; then FLAGS+=(--options runtime --timestamp); fi

is_macho() { head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n' | grep -qE '^(cffaedfe|feedfacf|cafebabe|feedface|cefaedfe)$'; }

signed=0
sign_engine_file() {
  is_macho "$1" || return 0
  if [ "$(basename "$1")" = "huddle-engine" ]; then
    codesign "${FLAGS[@]}" --entitlements "$ENT/engine.plist" "$1" 2>&1 | grep -v "replacing existing signature" || true
  else
    codesign "${FLAGS[@]}" "$1" 2>&1 | grep -v "replacing existing signature" || true
  fi
  signed=$((signed + 1))
}
# 1. every Mach-O inside the engine: libraries first, then executables (PyInstaller's output has
#    no nested bundles, so no deeper ordering is needed)
while IFS= read -r -d '' f; do sign_engine_file "$f"; done < <(find "$ENGINE" -type f \( -name "*.dylib" -o -name "*.so" \) -print0)
while IFS= read -r -d '' f; do sign_engine_file "$f"; done < <(find "$ENGINE" -type f -perm -u+x ! -name "*.dylib" ! -name "*.so" -print0)
# 1b. PyInstaller ships Python.framework inside _internal; a framework is a bundle and needs its
#     own seal on top of the files signed above.
while IFS= read -r -d '' fw; do
  codesign "${FLAGS[@]}" "$fw" 2>&1 | grep -v "replacing existing signature" || true
  signed=$((signed + 1))
done < <(find "$ENGINE" -type d -name "*.framework" -print0)
# 2. the system-audio helper, 3. the main executable and the bundle
codesign "${FLAGS[@]}" --entitlements "$ENT/helper.plist" "$APP/Contents/MacOS/huddle-audio-tap" 2>&1 | grep -v "replacing existing signature" || true
codesign "${FLAGS[@]}" --entitlements "$ENT/app.plist" "$APP" 2>&1 | grep -v "replacing existing signature" || true
echo "signed $signed engine binaries + helper + app with identity: $IDENTITY"
codesign -v --strict "$APP" && echo "signature ok: $APP"
if [ "$IDENTITY" != "-" ]; then
  spctl --assess --type execute -v "$APP" 2>&1 | head -2 || true   # "rejected" until notarized, "accepted: Notarized Developer ID" after package-release.sh
fi
