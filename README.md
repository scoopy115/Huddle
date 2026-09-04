# Huddle

A private, local-first meeting recorder and knowledge base for **in-person meetings**.
Put your Mac on the table, press Record, press Stop. Huddle transcribes the room, separates
speakers, writes a summary with decisions and action items, and makes everything searchable —
entirely on your machine. An MCP server lets AI agents query your meeting memory without
touching your filesystem.

A Python processing engine (Whisper on MLX/CTranslate2, sherpa-onnx speaker separation, Ollama
notes) wrapped in a Tauri 2 + React desktop app. macOS (Apple Silicon) first; the architecture
is portable to Windows. Huddle started from an audit of [MeetingScribe](https://github.com/elmoghany/meeting-scribe);
its code is no longer used.

```
Tauri desktop app (Rust + React/TypeScript)        Processing engine (Python sidecar, localhost)
  recording (cpal → WAV, crash-safe) · menu-bar tray  SQLite + FTS5 (versioned migrations)
  devices · hardware · app paths                     resumable processing jobs
  engine lifecycle + token-authenticated proxy       providers: Whisper (MLX / CTranslate2) · sherpa-onnx · Ollama / LM Studio / llama.cpp
  UI: meetings · search · action items · settings    model discovery + resolver (never duplicates models you already have)
                                                     MCP server (stdio) over the same services
```

No accounts, no cloud, no API keys, no Ollama requirement (but Ollama/LM Studio models are reused
when present). After models are downloaded the app works fully offline.

## Install

Requirements: a Mac with Apple Silicon running macOS 14.2 or newer.

1. Download `Huddle-<version>-macos-arm64.zip` from the latest [release](../../releases/latest),
   unzip it and drag `Huddle.app` into Applications.
2. First launch: right-click `Huddle.app` → Open. Builds are not notarized yet, so macOS may still
   refuse; then go to System Settings → Privacy & Security and click **Open Anyway**.
3. Huddle asks for the microphone and, if you enable it, system audio (the other side of online
   meetings). It then checks what is already on your Mac and offers the model downloads it needs.

Huddle checks the releases page on launch and once a day and updates itself when you say so.

## Status

Phases 0–9 of the implementation plan are in place and verified on an M4 Pro:

| Phase | State |
|---|---|
| 1 Desktop shell | Sidebar, meetings list, meeting detail, settings |
| 2 Recording | cpal microphone capture + optional system audio via a Core Audio process tap (no driver; macOS 14.2+), level meter, WAV header patched every second, crash recovery |
| 3 Vertical slice | Record/import → transcript in the UI. MLX Whisper on Apple Silicon (~19× realtime), CTranslate2 fallback; language detected per utterance, 99 languages; live transcription while recording |
| 4 Diarization | sherpa-onnx (pyannote segmentation + TitaNet embeddings), rename/merge speakers, known-speaker suggestions (confirm, never auto-assign) |
| 5 AI output | Structured summary/topics/decisions in the app language with evidence timestamps; action items on demand; speaker names inferred from the conversation |
| 6 Discovery | Apple Silicon/Metal, Ollama (API or manifests), LM Studio, Hugging Face cache, whisper.cpp/WhisperKit. AI runs through Ollama only |
| 7 Models | Inventory, compatibility by (task, format, runtime), external ownership, downloads with sha256, storage estimate |
| 8 Search | FTS5 with prefix matching, click-to-seek, "ask this meeting" / "ask all meetings" over retrieval |
| 9 MCP | `huddle-engine mcp` (stdio) with targeted retrieval tools; optional LAN server with API keys |
| 10 Packaging | macOS `.app` builds (`scripts/build-engine.sh`, `scripts/build-app.sh`), release zip (`scripts/package-release.sh`) |
| 12 Updates | Checks the GitHub releases of this repo on launch and daily; "A new version of Huddle is available" downloads the zip, swaps the app in place and relaunches |
| 11 Menu bar | Optional menu-bar mode: close the window, the engine sleeps, the menu-bar popover or ⌥⌘R records instantly without touching the Dock; stopping opens Huddle and processes the meeting. Audio export per meeting |

## Development

Requirements: macOS (Apple Silicon), Xcode Command Line Tools, Node 20+, Rust (rustup),
Homebrew Python 3.11 (`python3.11`).

```bash
# 1. engine
cd engine
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest              # 57 tests
.venv/bin/python -m huddle_engine doctor  # hardware, providers, models, resolution

# 2. system-audio helper (ScreenCaptureKit; needs Xcode Command Line Tools)
../scripts/build-audio-tap.sh

# 3. desktop app (starts Vite + Tauri; the app spawns engine/.venv automatically)
cd ../apps/desktop
npm install
npx tauri dev
```

Headless vertical slice without the UI:

```bash
cd engine
.venv/bin/python -m huddle_engine process path/to/meeting.m4a --title "Branding"
```

Data lives in `~/Library/Application Support/com.huddle.desktop` (`huddle.db`, `recordings/`,
`models/`, `logs/`). Override with `HUDDLE_DATA_DIR`.

### MCP

```json
{ "mcpServers": { "huddle": { "command": "/path/to/engine/.venv/bin/python",
                              "args": ["-m", "huddle_engine", "mcp"],
                              "cwd": "/path/to/engine" } } }
```

Tools: `list_meetings`, `get_meeting`, `get_transcript`, `get_transcript_context`,
`search_transcripts`, `search_meetings`, `get_summary`, `get_topics`, `get_decisions`,
`get_action_items`, `get_open_action_items`, `search_semantic` (falls back to FTS until a local
embedding provider lands).

## License

Huddle is MIT licensed. Model and runtime licenses are listed in [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md); no model is bundled in the
installer.
