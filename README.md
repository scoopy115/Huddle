<p align="center">
  <img src="apps/desktop/src/assets/huddle-logo.svg" alt="Huddle" width="220">
</p>

# Huddle: Private Meeting Notes, Made on Your Computer

![macOS](https://img.shields.io/badge/macOS-14.2%2B%20Apple%20Silicon-black.svg?logo=apple&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64%20(preview)-0078D4.svg)
![Status](https://img.shields.io/badge/Status-Active-success)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

**Huddle** records your meetings and turns them into transcripts, speaker-separated conversations,
summaries, decisions and action items, **entirely on your own computer**. No account, no cloud, no
subscription. Put your laptop on the table for an in-person meeting, or let it listen to the
other side of a video call; press Stop and the notes write themselves while nothing leaves your
computer.

---

## 🚀 Key Features

### 🎙️ Recording
* **In the room or on a call:** records the microphone and, optionally, the audio of every other
  app on your computer (Teams, Zoom, Meet, FaceTime).
* **Menu bar mode:** close the window and Huddle keeps a small recorder in the menu bar (the
  system tray on Windows). Click it or press **⌥⌘R** (**Ctrl+Alt+R** on Windows) to start and
  stop from anywhere.
* **Crash-safe:** the audio file is finalised every second, so a lost battery still leaves a
  recoverable recording.
* **Import:** drop in an existing audio file and get the same treatment.

### ✍️ Notes
* **Transcript with speakers:** who said what, with timestamps. Rename or merge speakers; Huddle
  suggests names it recognises from earlier meetings.
* **Summary, topics, decisions:** structured notes with evidence links back into the transcript,
  written in the language you choose (56 languages) whatever language was spoken.
* **Action items:** extracted on demand and tracked across meetings until you tick them off.
* **Refine:** tell Huddle what it got wrong  and it
  rewrites the notes.
* **Ask:** ask one meeting, one project or all of them a question.
* **Projects:** folders for the meetings of one client, product or team. Huddle suggests the
  project a new meeting belongs to; you confirm with one click.

### 🔒 Private by design
* **Everything runs locally:** transcription, speaker separation and summaries all run on your
  computer. Huddle works fully offline once the models are downloaded.
* **Hardware check:** on first launch Huddle shows what your computer can do for local AI and
  only offers the models that fit it (a PC with integrated graphics gets transcription and, with
  enough memory, CPU-only notes; a graphics card with 6 GB or more runs everything).
* **Your data stays yours:** recordings, transcripts and notes live in one folder under
  Application Support. Export any meeting as Markdown, text, SRT, JSON, or the original audio.
* **Storage limit:** set how much disk recordings may use; older audio is pruned first.

### 🤖 Works with your AI tools
* **MCP server:** Claude Desktop, Claude Code, Cursor and other MCP clients can search your
  meetings, read transcripts, pull open action items and reference whole projects, without
  touching your filesystem.
  Settings → MCP shows the ready-made configuration for each client.
* **Network access (optional):** share the MCP server on your local network with API keys.
* **Bring your own models:** Huddle finds models you already have (Ollama, LM Studio, Hugging
  Face cache) and only downloads what is missing.

---

## 📦 Install

**macOS:** a Mac with **Apple Silicon** running **macOS 14.2 or newer**. About 1 GB of disk for
the app plus the models you choose (the recommended set is around 5 GB). 16 GB of memory is
recommended; the small AI model also runs on 8 GB.

**Windows (preview):** Windows 10/11, x64. For AI notes you need a graphics card with 6 GB of
memory or more (NVIDIA recommended), or 16 GB of system memory for the slow CPU-only path;
with less, Huddle still transcribes and separates speakers but makes no notes. Transcription
runs on the CPU on Windows for now.

1. Download `Huddle-<version>-macos-arm64.dmg` (macOS) or `Huddle-<version>-windows-x64-setup.exe`
   (Windows) from the latest [release](../../releases/latest). On a Mac, open the image and drag
   Huddle into the Applications folder; on Windows, run the installer.
2. Open it. On first launch Huddle shows what your computer can do, asks for the microphone (and
   on macOS for system audio), checks what is already installed and offers the model downloads
   it needs.

---

## 🧑‍💻 Development

Requirements: Node 20+, Rust, Python 3.11. macOS: Apple Silicon, Xcode Command Line Tools,
Homebrew Python 3.11 (`python3.11`). Windows: Visual Studio Build Tools (C++ workload), Python
3.11 x64 from python.org, and the PowerShell scripts below in place of the shell ones.

```bash
# 1. engine
cd engine
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest                 # tests
.venv/bin/python -m huddle_engine doctor   # hardware, providers, models, resolution

# 2. system-audio helper (Swift; needs Xcode Command Line Tools)
../scripts/build-audio-tap.sh

# 3. speaker models (bundled into the app)
../scripts/fetch-speaker-models.sh

# 4. desktop app (starts Vite + Tauri; the app spawns engine/.venv automatically)
cd ../apps/desktop
npm install
npx tauri dev
```

Windows (PowerShell):

```powershell
cd engine; py -3.11 -m venv .venv; .venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\python -m pytest
..\scripts\fetch-speaker-models.ps1
cd ..\apps\desktop; npm install; npx tauri dev
# release: scripts\build-engine.ps1 then scripts\build-app.ps1 (NSIS installer)
```

Platform-specific Tauri settings live in `tauri.macos.conf.json` and `tauri.windows.conf.json`
next to the shared `tauri.conf.json`.

---

## ❤️ Credits & Acknowledgment

Huddle stands on excellent open-source work:

* **Transcription:** [Whisper](https://github.com/openai/whisper) via [mlx-whisper](https://github.com/ml-explore/mlx-examples) and [CTranslate2](https://github.com/OpenNMT/CTranslate2)
* **Speaker separation:** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) with the [pyannote](https://github.com/pyannote/pyannote-audio) segmentation model and NVIDIA's TitaNet embeddings
* **Notes:** [Ollama](https://ollama.com) running local models such as Qwen — installed by Huddle itself when it is not on your computer
* **App:** [Tauri](https://tauri.app), [React](https://react.dev), [Tailwind CSS](https://tailwindcss.com), [FastAPI](https://fastapi.tiangolo.com)

Huddle is created by me with extensive assistance from **Claude Fable 5.1** (Anthropic) as
a co-pilot for architecture, implementation and debugging.

---

## 📄 License

Huddle is released under the [MIT License](LICENSE).
