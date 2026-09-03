#!/usr/bin/env bash
# Build the engine sidecar with PyInstaller (one-dir) for the current architecture.
# Output: engine/dist.nosync/huddle-engine/huddle-engine  (*.nosync keeps it out of iCloud sync)
#
# Collected explicitly: CTranslate2 + faster-whisper (CPU Whisper, language detection), MLX +
# mlx-whisper (Metal Whisper; needs its .metallib and model assets), sherpa-onnx + onnxruntime
# (speaker separation), PyAV (decoding), sklearn/scipy (clustering), tiktoken/tokenizers,
# huggingface_hub (downloads), uvicorn/anyio (server), pydantic.
# The app bundles the sherpa speaker models (scripts/fetch-speaker-models.sh); Whisper and LLM
# models are never bundled. numba must stay: mlx_whisper needs it for word timings.
set -euo pipefail
cd "$(dirname "$0")/../engine"
PY=.venv/bin/python
$PY -m pip install -q pyinstaller
$PY -m PyInstaller --noconfirm --clean --name huddle-engine --onedir --distpath dist.nosync --workpath build.nosync \
  --paths . \
  --collect-all ctranslate2 --collect-all faster_whisper --collect-all av \
  --collect-all mlx --collect-all mlx_whisper --collect-all sherpa_onnx --collect-all onnxruntime \
  --collect-all sklearn --collect-all scipy --collect-all soundfile \
  --collect-all tiktoken --collect-submodules tiktoken_ext --collect-all tokenizers --collect-all huggingface_hub \
  --collect-all pydantic --exclude-module mcp.cli --collect-submodules huddle_engine \
  --collect-submodules uvicorn --collect-submodules anyio \
  --exclude-module torch --exclude-module torchaudio --exclude-module resemblyzer --exclude-module librosa --exclude-module webrtcvad \
  huddle_engine_entry.py

# ---- prune -----------------------------------------------------------------------------------
# PyInstaller's hooks copy whole package trees. Thousands of small files make copying, signing
# and installing the app slow, so remove what a frozen app can never use:
#   * test suites, C/C++ headers, cmake files, Cython/pyi sources, docs;
#   * .py source copies of packages whose modules already live compiled in the PYZ archive.
# Data the runtime needs (mlx .metallib, mlx_whisper assets, tokenizer files, dylibs) is untouched.
# The pruned bundle is verified by `huddle-engine process` on a fixture (see PACKAGING.md).
INTERNAL=dist.nosync/huddle-engine/_internal
before=$(find "$INTERNAL" -type f | wc -l | tr -d ' ')
find "$INTERNAL" -type d \( -name tests -o -name test -o -name testing -o -name include -o -name cmake -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$INTERNAL" -type f \( -name "*.pyi" -o -name "*.h" -o -name "*.hpp" -o -name "*.c" -o -name "*.cpp" -o -name "*.pxd" -o -name "*.pyx" -o -name "*.md" -o -name "*.rst" \) -delete
for pkg in scipy sklearn onnxruntime huggingface_hub pydantic av mlx mlx_whisper tokenizers ctranslate2 faster_whisper sherpa_onnx tiktoken tiktoken_ext anyio uvicorn; do
  [ -d "$INTERNAL/$pkg" ] && find "$INTERNAL/$pkg" -type f -name "*.py" -delete
done
find "$INTERNAL" -type d -empty -delete
after=$(find "$INTERNAL" -type f | wc -l | tr -d ' ')
echo "pruned: $before → $after files"

ls -la dist.nosync/huddle-engine/huddle-engine
du -sh dist.nosync/huddle-engine
echo "Sidecar built. Bundle via apps/desktop tauri.conf.json bundle.resources (see docs/PACKAGING.md)."
