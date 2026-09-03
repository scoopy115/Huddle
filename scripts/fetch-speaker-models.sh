#!/usr/bin/env bash
# Download the two speaker-separation models that ship inside the app bundle
# (apps/desktop/src-tauri/resources/models/speaker, git-ignored; ~103 MB):
#   pyannote segmentation 3.0 (MIT)   — speaker turns
#   NVIDIA TitaNet large (CC-BY-4.0)  — speaker embeddings
# Same files and names as engine/huddle_engine/providers/speaker_models.py.
set -euo pipefail
DEST="$(dirname "$0")/../apps/desktop/src-tauri/resources/models/speaker"
mkdir -p "$DEST"
R=https://github.com/k2-fsa/sherpa-onnx/releases/download
if [ ! -f "$DEST/nemo_titanet_large.onnx" ]; then
  curl -L --fail -o "$DEST/nemo_titanet_large.onnx" "$R/speaker-recongition-models/nemo_en_titanet_large.onnx"
fi
if [ ! -f "$DEST/pyannote-segmentation-3.0.int8.onnx" ]; then
  T=$(mktemp -d)
  curl -L --fail -o "$T/seg.tar.bz2" "$R/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
  tar xjf "$T/seg.tar.bz2" -C "$T"
  cp "$T/sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx" "$DEST/pyannote-segmentation-3.0.int8.onnx"
  rm -rf "$T"
fi
ls -la "$DEST"
