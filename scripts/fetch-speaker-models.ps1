# Download the two speaker-separation models that ship inside the app
# (apps\desktop\src-tauri\resources\models\speaker, git-ignored; ~103 MB):
#   pyannote segmentation 3.0 (MIT)   - speaker turns
#   NVIDIA TitaNet large (CC-BY-4.0)  - speaker embeddings
# Same files and names as engine\huddle_engine\providers\speaker_models.py and fetch-speaker-models.sh.
$ErrorActionPreference = "Stop"
$dest = Join-Path $PSScriptRoot "..\apps\desktop\src-tauri\resources\models\speaker"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$r = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
$titanet = Join-Path $dest "nemo_titanet_large.onnx"
if (-not (Test-Path $titanet)) {
  Invoke-WebRequest -Uri "$r/speaker-recongition-models/nemo_en_titanet_large.onnx" -OutFile $titanet
}
$seg = Join-Path $dest "pyannote-segmentation-3.0.int8.onnx"
if (-not (Test-Path $seg)) {
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("huddle-seg-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  Invoke-WebRequest -Uri "$r/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" -OutFile "$tmp\seg.tar.bz2"
  # tar on Windows 10 1803+ (bsdtar) reads bzip2 archives.
  tar -xjf "$tmp\seg.tar.bz2" -C $tmp
  Copy-Item "$tmp\sherpa-onnx-pyannote-segmentation-3-0\model.int8.onnx" $seg
  Remove-Item $tmp -Recurse -Force
}
Get-ChildItem $dest
