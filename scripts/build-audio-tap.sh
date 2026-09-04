#!/usr/bin/env bash
# Compile the ScreenCaptureKit system-audio helper into the Tauri externalBin location.
set -euo pipefail
cd "$(dirname "$0")/../apps/desktop"
TRIPLE=$(rustc -vV 2>/dev/null | sed -n 's/host: //p'); TRIPLE=${TRIPLE:-aarch64-apple-darwin}
mkdir -p src-tauri/binaries
xcrun swiftc -O -target arm64-apple-macos14.2 -framework AVFoundation -framework CoreAudio \
  -o "src-tauri/binaries/huddle-audio-tap-$TRIPLE" native/audio-tap/main.swift
echo "built src-tauri/binaries/huddle-audio-tap-$TRIPLE"
