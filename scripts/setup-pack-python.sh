#!/usr/bin/env bash
# Prepare the Python used to PACKAGE the engine (not the dev venv).
#
# Homebrew's python3.11 is built for the macOS it was installed on: on a macOS 26 machine every
# extension module and the Python framework carry "minos 26.0", and pip picks macOS-26 wheels for
# mlx / sherpa-onnx too. A sidecar built from that venv loads on nothing older — dyld refuses the
# library and the PyInstaller bootloader exits with code 1 ("The local processing engine could
# not start", seen on an M1 with an older macOS). Huddle supports macOS 14.2+, so the release
# sidecar is built from python-build-standalone (deployment target 11.0) with wheels chosen for
# macOS 14 (downloaded with explicit --platform tags, since pip otherwise picks for the host).
#
# Output: engine/.python-pack.nosync/ (the interpreter), engine/.wheels-pack.nosync/ (the wheels)
# and engine/.venv-pack.nosync/ (the venv with requirements + PyInstaller).
# scripts/build-engine.sh uses the venv when it exists.
set -euo pipefail
cd "$(dirname "$0")/../engine"
TARGET="${HUDDLE_MACOS_TARGET:-14.0}"
PBS_TAG="${HUDDLE_PBS_TAG:-20260901}"
PBS_VER="${HUDDLE_PBS_PYTHON:-3.11.16}"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PBS_VER%2B$PBS_TAG-aarch64-apple-darwin-install_only.tar.gz"
PYDIR=.python-pack.nosync
VENV=.venv-pack.nosync

if [ ! -x "$PYDIR/python/bin/python3.11" ]; then
  rm -rf "$PYDIR"; mkdir -p "$PYDIR"
  echo "downloading python-build-standalone $PBS_VER ($PBS_TAG)…"
  curl -fsSL "$URL" | tar -xz -C "$PYDIR"
fi
PY="$PYDIR/python/bin/python3.11"
"$PY" -c 'import platform; print("packaging python", platform.python_version())'

if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi
# pip picks wheels for the macOS it runs on (platform.mac_ver(), not overridable), so the wheels
# are downloaded with explicit platform tags — every tag a macOS $TARGET arm64 machine accepts —
# and installed from that folder. --only-binary: nothing may quietly build from source here.
WHEELS=.wheels-pack.nosync
"$VENV/bin/python" -m pip install -q --upgrade pip packaging
PLATFORMS=$("$VENV/bin/python" - "$TARGET" <<'PY'
import sys
from packaging import tags
major, minor = (int(x) for x in sys.argv[1].split(".")[:2])
print(" ".join(f"--platform {p}" for p in tags.mac_platforms((major, minor), "arm64")))
PY
)
# shellcheck disable=SC2086
"$VENV/bin/python" -m pip download -q -d "$WHEELS" --only-binary=:all: $PLATFORMS \
  --python-version 3.11 --implementation cp --abi cp311 --abi none -r requirements.txt pyinstaller
"$VENV/bin/python" -m pip install -q --no-index --find-links "$WHEELS" --only-binary=:all: -r requirements.txt pyinstaller

# Verify: no native library in the venv may need anything newer than $TARGET.
bad=0
while IFS= read -r f; do
  m=$(otool -l "$f" 2>/dev/null | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}')
  if [ -n "$m" ] && [ "$(printf '%s\n%s\n' "$TARGET" "$m" | sort -V | tail -1)" != "$TARGET" ]; then
    echo "needs macOS $m: $f"; bad=1
  fi
done < <(find "$VENV/lib" "$PYDIR" -type f \( -name "*.so" -o -name "*.dylib" \))
[ "$bad" = 0 ] || { echo "some libraries require a newer macOS than $TARGET" >&2; exit 1; }
echo "packaging venv ready: $VENV (all native libraries run on macOS $TARGET+)"
