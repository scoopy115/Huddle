# Build the Windows installer: Tauri release build (NSIS) with the bundled engine.
# Output: apps\desktop\src-tauri\target.nosync\release\bundle\nsis\Huddle_<version>_x64-setup.exe
#
#   powershell -ExecutionPolicy Bypass -File scripts\build-app.ps1
#
# Prerequisites: Node 20+, Rust (rustup, MSVC toolchain), the Visual Studio Build Tools with the
# "Desktop development with C++" workload, WebView2 (present on Windows 10/11),
# scripts\fetch-speaker-models.ps1 (once) and scripts\build-engine.ps1.
# Signing: set HUDDLE_WIN_CERT_THUMBPRINT to an Authenticode certificate in the current user's
# store to have Tauri sign the executable and the installer (SmartScreen trusts a signed build
# after enough installs; an unsigned one shows "Windows protected your PC" until "Run anyway").
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$sidecar = Join-Path $root "engine\dist.nosync\huddle-engine\huddle-engine.exe"
if (-not (Test-Path $sidecar)) { throw "engine sidecar missing - run scripts\build-engine.ps1 first" }
# The sidecar is reused as built; engine changes only reach the app through build-engine.ps1.
$newer = Get-ChildItem (Join-Path $root "engine\huddle_engine") -Recurse -Filter *.py | Where-Object { $_.LastWriteTime -gt (Get-Item $sidecar).LastWriteTime } | Select-Object -First 1
if ($newer) { throw "engine sources are newer than the built sidecar ($($newer.Name)) - run scripts\build-engine.ps1 first" }
if (-not (Test-Path (Join-Path $root "apps\desktop\src-tauri\resources\models\speaker\nemo_titanet_large.onnx"))) { throw "speaker models missing - run scripts\fetch-speaker-models.ps1 first" }

Set-Location (Join-Path $root "apps\desktop")
# Not `$args`: that is PowerShell's automatic parameter array, and `npx @tauriArgs` would then run npx with no arguments.
$tauriArgs = @("tauri", "build", "--bundles", "nsis")
if ($env:HUDDLE_WIN_CERT_THUMBPRINT) {
  $tauriArgs += @("--config", ('{"bundle":{"windows":{"certificateThumbprint":"' + $env:HUDDLE_WIN_CERT_THUMBPRINT + '","digestAlgorithm":"sha256","timestampUrl":"http://timestamp.digicert.com"}}}'))
}
npx @tauriArgs
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }

$version = (Get-Content (Join-Path $root "apps\desktop\src-tauri\tauri.conf.json") | ConvertFrom-Json).version
# Tauri names its installer Huddle_<version>_x64-setup.exe; the release asset copied below sits next to it.
$setup = Get-ChildItem (Join-Path $root "apps\desktop\src-tauri\target.nosync\release\bundle\nsis") -Filter "Huddle_*-setup.exe" | Select-Object -First 1
if (-not $setup) { throw "no installer produced" }
# The updater looks for "windows" or "x64" in the asset name; Tauri's default already has x64.
$asset = Join-Path $setup.DirectoryName "Huddle-$version-windows-x64-setup.exe"
Copy-Item $setup.FullName $asset -Force
Write-Host "Release asset: $asset ($([math]::Round($setup.Length / 1MB)) MB) - tag the release v$version"
