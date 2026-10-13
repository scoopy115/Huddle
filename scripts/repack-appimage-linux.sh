#!/usr/bin/env bash
# Repack the AppImage that `tauri build --bundles appimage` leaves behind, without the libraries
# the host already has.
#
# Tauri's AppDir carries the build machine's copy of every library the app links, and the
# AppImage puts them in front of the host's. Mixed with the host's own libraries that breaks:
#   * bundled WebKitGTK/GStreamer + the host's web-process helper and GStreamer plugins:
#     the meeting screen's <audio> element hung the whole window (grey screen on Bazzite);
#   * host GStreamer + bundled GLib: "undefined symbol: g_sort_array" at start-up;
#   * host GLib/GIO + bundled libmount/libpcre2: "version `MOUNT_2_40' not found".
# So every library the host provides comes from the host, and the bundle keeps only what the
# host lacks (the tray's libayatana-appindicator, the engine's libraries, …).
#
# The host is read from $HUDDLE_HOST_ROOT (default /run/host: a toolbox/distrobox container on
# the target machine sees the host there). Without it only the fixed EXCLUDES list applies.
# With it, the result is also checked with the host's own dynamic loader before packing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$ROOT/apps/desktop/src-tauri/target.nosync/release/bundle/appimage"
APPDIR="$BUNDLE/Huddle.AppDir"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")"
OUT="$BUNDLE/Huddle_${VERSION}_amd64.AppImage"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/tauri"
LINUXDEPLOY="$CACHE/linuxdeploy-x86_64.AppImage"
APPIMAGE_PLUGIN="$CACHE/linuxdeploy-plugin-appimage.AppImage"
HOST_ROOT="${HUDDLE_HOST_ROOT:-/run/host}"
[ -d "$APPDIR" ] || { echo "No AppDir at $APPDIR — run the AppImage build first" >&2; exit 1; }
[ -x "$LINUXDEPLOY" ] && [ -x "$APPIMAGE_PLUGIN" ] || { echo "linuxdeploy tools not found in $CACHE — run the AppImage build once" >&2; exit 1; }

# Always left out, whatever the host: the GUI stack (WebKit, GStreamer, GTK, GLib and friends)
# only works as the host's own matching set.
EXCLUDES=('libwebkit2gtk-4.1*' 'libjavascriptcoregtk-4.1*' 'libgst*' 'libsoup-3.0*'
          'libglib-2.0*' 'libgobject-2.0*' 'libgio-2.0*' 'libgmodule-2.0*' 'libgthread-2.0*'
          'libgtk-3*' 'libgdk-3*' 'libgdk_pixbuf*' 'libpango*' 'libcairo*' 'libatk*' 'libharfbuzz*'
          'libgirepository*')
strip_excluded() {
  for pat in "${EXCLUDES[@]}"; do
    find "$APPDIR/usr/lib" -maxdepth 1 -name "$pat" -exec rm -f {} +
  done
  # Helper processes, plugin and module directories that belong to that stack.
  find "$APPDIR/usr" -type d \( -name "webkit2gtk-4.1" -o -name "gstreamer-1.0" -o -name "gstreamer1.0" -o -name "gdk-pixbuf-2.0" -o -name "gtk-3.0" \) -prune -exec rm -rf {} +
}

HOST_LIBDIRS=()
for d in "$HOST_ROOT/usr/lib64" "$HOST_ROOT/usr/lib"; do [ -d "$d" ] && HOST_LIBDIRS+=("$d"); done
strip_host_provided() {
  [ "${#HOST_LIBDIRS[@]}" -gt 0 ] || return 0
  local removed=0 f name d
  while IFS= read -r -d '' f; do
    name="$(basename "$f")"
    for d in "${HOST_LIBDIRS[@]}"; do
      if [ -e "$d/$name" ]; then rm -f "$f"; removed=$((removed + 1)); break; fi
    done
  done < <(find "$APPDIR/usr/lib" -maxdepth 1 \( -type f -o -type l \) -name 'lib*.so*' -print0)
  echo "left out $removed libraries the host provides ($HOST_ROOT)"
}

# Link the app the way the host will: the host's loader, the AppImage's lib folder first (as its
# AppRun does), then the host's. Any missing library, missing symbol version or undefined symbol
# fails the build instead of the user's start-up.
verify_on_host() {
  local ldso="${HOST_LIBDIRS[0]:-}/ld-linux-x86-64.so.2"
  if [ "${#HOST_LIBDIRS[@]}" -eq 0 ] || [ ! -x "$ldso" ]; then
    echo "no host at $HOST_ROOT — skipping the host link check" >&2
    return 0
  fi
  local path="$APPDIR/usr/lib" d
  for d in "${HOST_LIBDIRS[@]}"; do path+=":$d"; [ -d "$d/pulseaudio" ] && path+=":$d/pulseaudio"; done
  local out
  out="$(LD_TRACE_LOADED_OBJECTS=1 LD_WARN=yes LD_BIND_NOW=yes "$ldso" --library-path "$path" "$APPDIR/usr/bin/Huddle" 2>&1 || true)"
  if grep -E "not found|undefined symbol|no version information" <<<"$out" >&2; then
    echo "the app would not start on this host (see above)" >&2
    exit 1
  fi
  echo "host link check passed"
}

excl_args=()
for pat in "${EXCLUDES[@]}"; do excl_args+=(--exclude-library "$pat"); done
run_linuxdeploy() { APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$LINUXDEPLOY" "$@"; }

# 1. remove, 2. deploy the executable's own dependencies (linuxdeploy adds some back),
# 3. remove again, 4. check against the host, 5. pack with the plugin alone (linuxdeploy would
# deploy again while packing).
strip_excluded
run_linuxdeploy --appdir "$APPDIR" "${excl_args[@]}" -e "$APPDIR/usr/bin/Huddle"
strip_excluded
strip_host_provided
verify_on_host

# Our own AppRun. linuxdeploy's (AppRun.wrapped + the GTK hook) points GStreamer, GSettings, GTK
# modules, GIO modules and Python at the bundle — the host's GStreamer then finds no plugins
# ("element appsink not found", no <audio>) and host GLib loads Ubuntu's modules. With the GUI
# stack from the host only the remaining bundled libraries need to be on the path.
# WEBKIT_DISABLE_DMABUF_RENDERER: WebKitGTK's DMA-BUF renderer fails on NVIDIA's driver
# ("Failed to create GBM buffer") and leaves the window grey; a user setting wins.
rm -rf "$APPDIR/apprun-hooks" "$APPDIR/AppRun.wrapped"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="${APPDIR:-$HERE}"
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export WEBKIT_DISABLE_DMABUF_RENDERER="${WEBKIT_DISABLE_DMABUF_RENDERER:-1}"
exec "$HERE/usr/bin/Huddle" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Pack beside the target and rename over it: a running AppImage cannot be overwritten in place
# ("Text file busy"), but a rename replaces it.
TMP="$OUT.new"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 OUTPUT="$TMP" "$APPIMAGE_PLUGIN" --appdir "$APPDIR"
mv -f "$TMP" "$OUT"
echo "Repacked $OUT ($(du -h "$OUT" | cut -f1))"
