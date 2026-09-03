# macOS packaging (Phase 10)

Target: a DMG the user drags to Applications. No Python, Homebrew, ffmpeg, or terminal needed.

## Status (2026-09-03): first `.app` built and verified locally

`apps/desktop/src-tauri/target.nosync/release/bundle/macos/Huddle.app` — 754 MB, 716 files (first build
was 1.84 GB / 4 100+ files; see DECISIONS #49, #52), ad-hoc signed (no Developer ID yet), Apple Silicon only. Contents:

| Path inside the bundle | What |
|---|---|
| `Contents/MacOS/huddle` | the Tauri shell (Rust, ~6.5 MB) |
| `Contents/MacOS/huddle-audio-tap` | ScreenCaptureKit system-audio helper (Tauri `externalBin`) |
| `Contents/Resources/engine/` | PyInstaller one-dir engine, 714 MB (`du` reports up to 818 MB): MLX 203, numba/llvmlite 123, onnxruntime 75, scipy 72, PyAV 44, sherpa-onnx 37, sklearn 32 |
| `Contents/Resources/models/speaker/` | TitaNet large + pyannote segmentation (98 MB), used in place — no download for speaker separation |
| `Contents/Resources/icon.icns` | app icon from `src-tauri/brand/app-icon.svg` |

Verified: the bundled engine answers `/health` from a clean data dir in 6–40 s (first import of
torch/MLX; the shell waits up to 120 s), lists Ollama + Hugging Face models, runs `doctor`; the
audio helper reports the permission state. Models are never bundled; onboarding downloads only what
is missing (Whisper ~1.5 GB, plus an Ollama model).

## Pieces

1. **Engine sidecar** — `scripts/build-engine.sh` runs PyInstaller (one-dir) on
   `engine/huddle_engine_entry.py`. That wrapper exists because `huddle_engine/__main__.py` uses
   relative imports, which only work under `python -m`; PyInstaller runs the given file as a
   top-level script. Everything imported lazily or shipping data files is collected explicitly
   (ctranslate2, faster_whisper, av, mlx, mlx_whisper, sherpa_onnx, onnxruntime, sklearn, scipy,
   soundfile, tiktoken(+ext), tokenizers, huggingface_hub, pydantic, uvicorn, anyio, huddle_engine,
   huddle_engine). Excluded: `mcp.cli` (importing it without `typer` calls `sys.exit(1)` and aborts PyInstaller's
   analysis).   analysis). numba/llvmlite must stay: `mlx_whisper` imports numba for word timings — without it
   MLX silently disappears and the CPU Whisper is offered instead. The obsolete `typing` backport
   must not be in the venv (PyInstaller refuses to run with it).
   **Pruning.** PyInstaller's hooks copy whole package trees; the raw one-dir had 4 133 files, most
   of them useless in a frozen app (1 066 in `tests/`, 390 headers, `.pyi`/`.pxd`/`.pyx` sources
   and 2 390 `.py` copies of modules that already sit compiled in the PYZ archive). The build
   script deletes those (see the list in `scripts/build-engine.sh`) → 677 files / 649 MB. File
   count, not size, is what makes copying, signing and installing slow. The pruned bundle is
   verified end-to-end (Whisper download through the bundle, import, transcribe on MLX, separate
   speakers, summarise with Ollama, index) before every packaging change is accepted. One-file
   mode was rejected: it would unpack ~650 MB into a temp folder on every launch.
2. **Tauri bundle** — `scripts/build-app.sh` runs `npx tauri build --bundles app` and then
   recreates every symlink of the dist folder inside the .app: Tauri dereferences symlinks when it
   copies resources, and MLX finds its Metal shader library next to the *real* `libmlx.dylib`
   (`_internal/mlx/lib/`). With the dereferenced copy at `_internal/libmlx.dylib`, MLX fails with
   "Failed to load the default metallib" and Huddle silently offers the CPU Whisper instead. (The
   `dmg` target is a follow-up: it needs an interactive Finder session for the disk-image layout.) `tauri.conf.json`
   `bundle.resources` copies `engine/dist.nosync/huddle-engine/` into `Contents/Resources/engine/` and
   `src-tauri/resources/models/` (filled by `scripts/fetch-speaker-models.sh`, git-ignored) into
   `Contents/Resources/models/`. The shell passes that folder as `HUDDLE_BUNDLED_MODELS`.
3. **Engine resolution** (`engine.rs::locate_engine`): `HUDDLE_ENGINE_CMD` override → *debug
   builds: the repo venv* → bundled sidecar (`Resources/engine/huddle-engine`, then next to the
   executable) → repo venv as a last resort. Debug builds must prefer the venv because Tauri copies
   `bundle.resources` next to the debug executable too; a stale sidecar there once shadowed the
   venv and broke `tauri dev`.
4. **MCP for local clients** points at the same program the shell uses (`engine_mcp_command`), so
   a packaged Huddle advertises `…/Huddle.app/Contents/Resources/engine/huddle-engine mcp …`.

## Build output lives in `*.nosync` folders

This checkout sits in `~/Documents`, which macOS syncs to iCloud ("Desktop & Documents").
Every file under it is handled by the iCloud file provider, so a Finder copy of the .app out of
the build folder crawled through hundreds of files at iCloud pace (~10 min), while `cp` on the
same disk takes 1.5 s. iCloud skips folders whose name ends in `.nosync`, so the Cargo target dir
is `target.nosync` (`src-tauri/.cargo/config.toml`) and PyInstaller writes to `engine/dist.nosync`
and `engine/build.nosync`. `.venv` and `node_modules` cannot be renamed and still sync; moving the
repository out of `~/Documents` (or turning off Desktop & Documents sync) would fix that too.

## Steps

```bash
# speaker models for the bundle (once, 103 MB)
bash scripts/fetch-speaker-models.sh

# engine sidecar (≈2 min, 714 MB)
bash scripts/build-engine.sh

# app (Rust release build ≈1–2 min, bundling copies the sidecar, then symlinks are restored)
bash scripts/build-app.sh
open apps/desktop/src-tauri/target.nosync/release/bundle/macos/Huddle.app
```

Quit the development app first: both use the same data directory and the shell's single-engine
rule (`engine.pid`) would stop the other one's engine.

## Known blockers / to verify

* **Signing & notarisation** — required for Gatekeeper on other Macs. Needs an Apple Developer
  ID; set `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` for `tauri build`.
  Hardened runtime needs `com.apple.security.cs.allow-unsigned-executable-memory` and
  `disable-library-validation` entitlements for the PyInstaller sidecar (it loads many dylibs).
  The ~4 000 files under `Resources/engine` all have to be signed; the sidecar's own Mach-O files
  are re-signed ad hoc by PyInstaller today.
* **Firewall prompt** — with the macOS Application Firewall on, enabling MCP network access
  prompts "Huddle would like to accept incoming network connections". Ad-hoc signed builds are
  asked again after every rebuild (the code hash changes); a Developer ID signature makes the
  answer stick.
* **DMG** — `--bundles dmg` on a headless run; `create-dmg` alternative.
* **Size** — 0.92 GB unpacked. Remaining levers: one Whisper runtime instead of MLX + CTranslate2
  (CTranslate2 is still needed for the tiny language detector), replace sklearn's clustering with
  scipy's (−32 MB), strip scipy/sklearn test data.
* **First launch** — 10–45 s to first `/health` (MLX/onnxruntime imports; the shell waits 120 s).
* **Ollama** — optional; without it the engine falls back to extractive notes and the UI says so.
* **Asset protocol scope** is `$APPDATA/**`; a user-chosen storage location must be added to the
  scope at runtime via `app.asset_protocol_scope().allow_directory`.
