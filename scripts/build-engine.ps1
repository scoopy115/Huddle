# Build the engine sidecar for Windows with PyInstaller (one-dir).
# Output: engine\dist.nosync\huddle-engine\huddle-engine.exe
#
# Run from a "x64 Native Tools" prompt or plain PowerShell on the Windows build machine:
#   powershell -ExecutionPolicy Bypass -File scripts\build-engine.ps1
#
# Prerequisites (once): Python 3.11 x64 from python.org, then
#   cd engine; py -3.11 -m venv .venv; .venv\Scripts\pip install -r requirements.txt pyinstaller
# The macOS-only packages (mlx-whisper, parakeet-mlx) are skipped by their requirement markers.
#
# Collected explicitly, as in build-engine.sh: CTranslate2 + faster-whisper (CPU Whisper and the
# language detector), sherpa-onnx + onnxruntime (speaker separation), PyAV (decoding),
# sklearn/scipy (clustering), tiktoken/tokenizers, huggingface_hub (downloads), uvicorn/anyio,
# pydantic. Speaker models ride in the app bundle (scripts\fetch-speaker-models.ps1); Whisper and
# AI models are never bundled.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\engine")

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "engine\.venv is missing - create it first (see the header of this script)" }

& $py -m PyInstaller --noconfirm --clean --name huddle-engine --onedir --distpath dist.nosync --workpath build.nosync `
  --paths . `
  --collect-all ctranslate2 --collect-all faster_whisper --collect-all av `
  --collect-all sherpa_onnx --collect-all onnxruntime `
  --collect-all sklearn --collect-all scipy --collect-all soundfile `
  --collect-all tiktoken --collect-submodules tiktoken_ext --collect-all tokenizers --collect-all huggingface_hub `
  --collect-all pydantic --exclude-module mcp.cli --collect-submodules huddle_engine `
  --collect-submodules uvicorn --collect-submodules anyio `
  --exclude-module torch --exclude-module torchaudio --exclude-module resemblyzer --exclude-module librosa --exclude-module webrtcvad `
  --exclude-module typer --exclude-module mlx --exclude-module mlx_whisper --exclude-module parakeet_mlx --exclude-module numba --exclude-module llvmlite `
  huddle_engine_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# ---- prune (same rules as build-engine.sh) ---------------------------------------------------
$internal = "dist.nosync\huddle-engine\_internal"
$before = (Get-ChildItem $internal -Recurse -File).Count
Get-ChildItem $internal -Recurse -Directory | Where-Object { $_.Name -in @("tests", "test", "testing", "include", "cmake", "__pycache__") } |
  ForEach-Object { if (Test-Path $_.FullName) { Remove-Item $_.FullName -Recurse -Force } }
Get-ChildItem $internal -Recurse -File | Where-Object { $_.Extension -in @(".pyi", ".h", ".hpp", ".c", ".cpp", ".pxd", ".pyx", ".md", ".rst") } |
  Remove-Item -Force
foreach ($pkg in @("scipy", "sklearn", "onnxruntime", "huggingface_hub", "pydantic", "av", "tokenizers", "ctranslate2", "faster_whisper", "sherpa_onnx", "tiktoken", "tiktoken_ext", "anyio", "uvicorn")) {
  $dir = Join-Path $internal $pkg
  if (Test-Path $dir) { Get-ChildItem $dir -Recurse -File -Filter *.py | Remove-Item -Force }
}
$after = (Get-ChildItem $internal -Recurse -File).Count
Write-Host "pruned: $before -> $after files"

$exe = "dist.nosync\huddle-engine\huddle-engine.exe"
if (-not (Test-Path $exe)) { throw "sidecar missing: $exe" }
$size = "{0:N0} MB" -f ((Get-ChildItem dist.nosync\huddle-engine -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Sidecar built: $exe ($size). Next: scripts\build-app.ps1"
