#!/usr/bin/env bash
# Build the engine sidecar with PyInstaller (one-dir) for Linux x86_64.
# Output: engine/dist.nosync/huddle-engine/huddle-engine  (the same path build-engine.sh uses)
#
# The Linux engine is the CPU path: faster-whisper (CTranslate2), sherpa-onnx speaker
# separation, scikit-learn clustering. MLX and Parakeet-MLX are macOS-only and left out.
# Python 3.11 comes from uv (python-build-standalone) so the sidecar does not depend on the
# distro's Python. Run once: uv is needed (https://docs.astral.sh/uv/).
set -euo pipefail
cd "$(dirname "$0")/../engine"

VENV=.venv-linux.nosync
if [ ! -x "$VENV/bin/python" ]; then
  command -v uv >/dev/null || { echo "uv not found — install it from https://docs.astral.sh/uv/" >&2; exit 1; }
  uv python install 3.11
  uv venv -p 3.11 "$VENV"
  VIRTUAL_ENV="$PWD/$VENV" uv pip install -r requirements.txt pyinstaller
fi
PY="$VENV/bin/python"

"$PY" -m PyInstaller --noconfirm --clean --name huddle-engine --onedir --distpath dist.nosync --workpath build.nosync \
  --paths . \
  --collect-all ctranslate2 --collect-all faster_whisper --collect-all av \
  --collect-all sherpa_onnx --collect-all onnxruntime \
  --collect-all sklearn --collect-all scipy --collect-all soundfile \
  --collect-all tiktoken --collect-submodules tiktoken_ext --collect-all tokenizers --collect-all huggingface_hub \
  --collect-all pydantic --exclude-module mcp.cli --collect-submodules huddle_engine \
  --collect-submodules uvicorn --collect-submodules anyio \
  --exclude-module torch --exclude-module torchaudio --exclude-module resemblyzer --exclude-module librosa --exclude-module webrtcvad \
  --exclude-module typer --exclude-module mlx --exclude-module mlx_whisper --exclude-module parakeet_mlx \
  huddle_engine_entry.py

# ---- prune (same rules as build-engine.sh) ---------------------------------------------------
INTERNAL=dist.nosync/huddle-engine/_internal
before=$(find "$INTERNAL" -type f | wc -l | tr -d ' ')
find "$INTERNAL" -type d \( -name tests -o -name test -o -name testing -o -name include -o -name cmake -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$INTERNAL" -type f \( -name "*.pyi" -o -name "*.h" -o -name "*.hpp" -o -name "*.c" -o -name "*.cpp" -o -name "*.pxd" -o -name "*.pyx" -o -name "*.md" -o -name "*.rst" \) -delete
for pkg in scipy sklearn onnxruntime huggingface_hub pydantic av tokenizers ctranslate2 faster_whisper sherpa_onnx tiktoken tiktoken_ext anyio uvicorn; do
  [ -d "$INTERNAL/$pkg" ] && find "$INTERNAL/$pkg" -type f -name "*.py" -delete
done
find "$INTERNAL" -type d -empty -delete
after=$(find "$INTERNAL" -type f | wc -l | tr -d ' ')
echo "pruned: $before → $after files"

ls -la dist.nosync/huddle-engine/huddle-engine
du -sh dist.nosync/huddle-engine
