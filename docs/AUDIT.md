# Phase 0 — MeetingScribe Audit

> Historical document. Huddle's design started from this audit and initially vendored parts of
> MeetingScribe; since 2026-09-03 no MeetingScribe code remains in the repository (DECISIONS #53).

Source inspected: `github.com/elmoghany/meeting-scribe` (shallow clone, 2026-09-03, 95 files,
~4.8k lines of Python in `app/`). Findings below come from reading the actual source, not the
README. Where the docs and code disagree it is called out.

## 1. Current architecture

Single Python package `app/`, split into:

| Area | Modules | Notes |
|---|---|---|
| Config | `config.py` | Env/.env only. Default data dir is a **Windows path** (`C:\cornell\meetingnotes`). |
| Data model | `models.py` | Plain dataclasses: `Meeting`, `Segment`, `ActionItem`, `Summary`. |
| Storage | `db.py` | stdlib `sqlite3`, single global connection + lock, FTS5 with triggers, ad-hoc column migrations. |
| Capture | `capture/recorder.py`, `session.py` | `soundcard` mic + **WASAPI loopback** (system audio). Live draft ASR thread. |
| Pipeline | `pipeline/asr.py`, `diarize.py`, `speakerid.py`, `assemble.py`, `notes.py`, `exporters.py`, `audiomix.py`, `process.py` | The reusable engine. `compute_pipeline()` is pure (no DB). |
| Server | `server/main.py` + vanilla-JS `static/` | FastAPI REST + WebSocket dashboard (811 lines, single file). |
| Remote | `remote/cornell.py`, `remote/worker.py`, `batch.py` | SSH/SLURM offload to a Cornell GPU node. |
| Integrations | `integrations/zoom.py`, `ics.py`, `webhook.py`, `scheduler.py`, `bot/` | Zoom OAuth, calendar auto-record, Zoom SDK C++ bot. |

There is no job/state persistence beyond `meetings.status` in {recording, processing, done,
error}. Processing runs in a daemon thread inside the FastAPI process; a crash mid-processing
leaves `status='processing'` forever.

## 2. Current processing pipeline

`process.compute_pipeline(audio_dir)`:

1. Expects `system.wav` (others) and/or `mic.wav` (me) in the recording dir — a **two-stream
   online-meeting assumption**.
2. `Transcriber.transcribe_file()` (faster-whisper, `beam_size=5`, `vad_filter=True`,
   `word_timestamps=False`) on each stream.
3. `diarize.label_speakers()` on the system stream only; mic stream is hard-labelled `"Me"`.
4. `diarize.speaker_embeddings()` → mean Resemblyzer d-vector per speaker label.
5. `assemble.merge_adjacent()` merges same-speaker segments (≤1 s gap, ≤30 s length cap).
6. `notes.get_notes_backend().summarize()` → `Summary` + `[ActionItem]`, with an extractive
   fallback on any exception.
7. `persist_result()` → `apply_profiles()` (voice matching), DB writes, Markdown export, webhook.

Everything runs sequentially in one thread. Stages are not individually retryable.

## 3. Existing local LLM implementation

`pipeline/notes.py`:

- `LlamaCppNotes` — `llama-cpp-python`, `n_ctx=8192`, **`n_gpu_layers=0`** (CPU only; no
  Metal offload). Whole transcript is pasted into one prompt; no chunking/retrieval.
- `TransformersNotes` — HF transformers, meant for the Cornell GPU. Not viable for a desktop app.
- `ExtractiveNotes` — pure-Python TextRank-ish summary + regex action-item/decision cues.
  **English-only** heuristics (stopwords, cue phrases, due-date regex). Useless for Dutch.
- Output schema: `{overview, key_points, decisions:[str], action_items:[{text, owner, due}]}`.
  Decisions are bare strings, no evidence timestamps. `_parse_llm_json` silently falls back to
  extractive output if JSON parsing fails, so a bad LLM response is indistinguishable from a
  working one.
- Chat (`chat()`) stuffs up to 24k chars of transcript into context. Cross-meeting "ask all"
  uses FTS5 hits as context — a good retrieval pattern worth keeping.
- **No Ollama, no LM Studio, no provider abstraction.** Backend chosen by env var string.

## 4. Existing model handling

- Whisper: faster-whisper downloads CTranslate2 models to `<data>/models/whisper` via
  `download_root`. Model chosen by name string (`large-v3`, `base.en`).
- GGUF: `scripts/download_models.py` pulls a `q4_k_m` file from an HF repo into
  `<data>/models/llm`; the path must then be hand-copied into `.env` (`MEETINGSCRIBE_GGUF_PATH`).
- Resemblyzer weights ship inside the pip package.
- pyannote: gated HF download, needs a token.
- **No inventory, no discovery of existing models, no compatibility checks, no checksums,
  no storage estimation.** Model handling is the weakest area relative to our requirements.

## 5. Existing transcription implementation

faster-whisper (CTranslate2). `device.detect()` uses CTranslate2's CUDA count → `cuda/float16`,
else `cpu/int8`. **CTranslate2 has no Metal backend**, so on Apple Silicon this is CPU-only.
Custom-vocabulary `initial_prompt` support is a nice, portable feature. Confidence is derived
from `avg_logprob`. Word timestamps are disabled.

## 6. Existing diarization implementation

Default `resemblyzer`: per-ASR-segment d-vector → agglomerative clustering (cosine, avg
linkage, threshold 0.40, calibrated per CHANGELOG). Segments <0.6 s inherit the previous label.
Diarization granularity is therefore **bounded by Whisper segments** — a segment containing two
speakers gets one label. Acceptable for V1; a real diarizer (pyannote / an ONNX segmentation
model) would be a clear upgrade behind the provider boundary.

Optional `pyannote` backend with CUDA→CPU fallback, needs HF token + gated terms.

## 7. Existing speaker recognition

Works and is worth preserving: `speaker_profiles` (name → running-mean embedding),
`meeting_speaker_embeddings` (per-meeting per-label mean). Rename in the UI enrols a profile;
`apply_profiles()` matches future meetings at cosine ≥ 0.75 and **renames silently** — our spec
requires suggest-and-confirm instead. The math (`speakerid.py`) is pure and unit-tested.

## 8. Existing database / search architecture

SQLite (WAL), tables: meetings, segments, summaries, action_items, annotations, meeting_tags,
speaker_profiles, meeting_speaker_embeddings, `segments_fts` (FTS5 external-content,
`porter unicode61`, trigger-synced). `search()` returns bm25-ranked snippets with a
sanitised-query fallback. Speaker is a **free-text string on each segment**; there is no
speakers table, no words table, no topics table, no jobs table, no migration versioning.
Rename is an in-place `CASE` update preserving segment IDs — a good trick to keep.

## 9. Existing API boundaries

FastAPI on `127.0.0.1:8765`, ~45 endpoints, plus a WebSocket for live events. Business logic is
mixed into route handlers (e.g. rename-speakers enrolment logic lives in the route). No auth
(fine for localhost). The API is shaped around the vanilla-JS dashboard; not a stable contract.

## 10. Windows-specific dependencies

- `soundcard` loopback capture (WASAPI) — the whole "Others" stream depends on it.
- Default data dir `C:\cornell\meetingnotes`.
- `.env.example` uses Windows paths.
- Zoom Meeting SDK bot is Linux/Docker.
- CUDA-only GPU detection in `device.py` and `diarize_pyannote`.

## 11. macOS blockers

- No system-audio loopback on macOS without a virtual device — irrelevant for our physical
  meeting use case, but `compute_pipeline` assumes the two-file layout; we must feed a single
  room recording.
- faster-whisper is CPU-only on Apple Silicon (no Metal). Usable, but large-v3 is slow;
  `large-v3-turbo` int8 is the pragmatic default. MLX-whisper / whisper.cpp+Metal are the
  upgrade paths behind `TranscriptionProvider`.
- `llama-cpp-python` needs a Metal build (`n_gpu_layers=-1`) for acceptable speed; upstream
  uses CPU. Prebuilt Metal wheels exist; building from source needs cmake.
- `resemblyzer` pulls torch (~150 MB arm64 wheel) + `webrtcvad` (C extension; builds with CLT).
- Python `>=3.10,<3.13` — Homebrew python3.11 is present; system miniconda 3.13 is not usable.

## 12. Components we can reuse unchanged

- `pipeline/assemble.py` — merge/split/renumber/rename/render (pure, tested).
- `pipeline/speakerid.py` — cosine/mean/match/running_mean (pure, tested).
- `pipeline/exporters.py` — SRT/VTT/TXT/JSON/talk-time (pure, tested).
- `pipeline/diarize.py` — `diarize_segments`, `speaker_embeddings`, `labels_from_embeddings`.
- `pipeline/asr.py` — `Transcriber` as the faster-whisper implementation of our provider.
- `pipeline/notes.py` — `ExtractiveNotes` as last-resort fallback, `_parse_llm_json` pattern,
  `fts_query_from_question`, `chapters`, `keywords`.
- `pipeline/audiomix.py` — `extract_clip`.
- FTS5 schema pattern and the in-place speaker rename `CASE` update from `db.py`.

## 13. Components requiring adaptation

- `process.compute_pipeline` — split into independently retryable stages driven by a persisted
  job; accept one room recording instead of mic/system pair; drop the hard-coded "Me".
- `notes.py` LLM prompt/schema — new structured schema (topics, decisions with evidence,
  action items with nullable owner/dueDate and confidence); Dutch/English instruction;
  move backend selection behind `LLMProvider`.
- `config.py` — replaced by explicit engine settings passed from the desktop app (data dir,
  model choices, provider choice). We set the `MEETINGSCRIBE_*` env vars the vendored modules
  read so upstream code stays byte-identical.
- `db.py` — new schema (see §35 of the spec) with versioned migrations; port the FTS pattern.
- `device.py` — becomes `ComputeDevice` detection (Apple/Metal/CPU now; CUDA/Vulkan later).

## 14. Components that should be replaced

- `capture/recorder.py` + `session.py` — replaced by native Rust recording (cpal → WAV on disk
  with periodic flush). Reasons: mic permission is attributed to the app process, no Python
  runtime needed for the most crash-sensitive path, and cpal covers CoreAudio + WASAPI.
- `server/main.py` routes + vanilla-JS dashboard — replaced by a thin typed engine API and
  the Tauri/React UI.
- `remote/*`, `integrations/*`, `scheduler.py`, `bot/` — not vendored (online-meeting and
  Cornell-specific).
- Live transcription thread — deliberately out of V1 scope.

## 15. Licensing concerns

| Component | License | Risk |
|---|---|---|
| MeetingScribe | MIT | Keep `LICENSE` + attribution in `engine/meetingscribe/`. |
| faster-whisper / CTranslate2 | MIT | OK. Whisper weights: MIT (OpenAI). CT2 conversions on HF (Systran) are MIT. |
| Resemblyzer | Apache-2.0 | OK; weights bundled under the same license. |
| scikit-learn, numpy, soundfile | BSD | OK. |
| torch | BSD-3 | OK (large). |
| llama-cpp-python / llama.cpp | MIT | OK. |
| pyannote.audio | MIT code, **gated weights** (user must accept terms, needs HF token) | Cannot redistribute; user-initiated download only. Optional. |
| ffmpeg | LGPL/GPL depending on build | **Do not bundle a GPL build.** Use an LGPL build or the user's existing binary; document. |
| Qwen 2.5 / Qwen 3 GGUF | Apache-2.0 (Qwen 3, Qwen 2.5 except 3B/72B which are "Qwen Research"/Qwen license) | Prefer Qwen 3 or Qwen 2.5 7B; **avoid Qwen2.5-3B** (upstream default) for commercial use. |
| Llama 3.x GGUF | Llama Community License | Redistribution restrictions; avoid as a default download. |
| Ollama / LM Studio models | Externally managed | Read-only; never delete or mutate. |

We never bundle models in the installer; all downloads are user-initiated with license shown.

## 16. Proposed Tauri integration boundary

```
Tauri (Rust)                          Engine (Python sidecar, localhost)
──────────────                        ─────────────────────────────────
recording (cpal → WAV)                FastAPI: typed JSON API
device enumeration                    SQLite (schema v1, migrations, FTS5)
hardware / compute detection          job runner (persisted state machine)
app data dirs, settings file          providers: transcription / diarization / llm
engine lifecycle (spawn, health,      model discovery + resolver
  restart, port + token)              MCP server (stdio) over the same services
`engine_fetch` proxy command
        ▲
        │ invoke()
React UI ── src/lib/api.ts (the only typed surface the UI uses)
```

- UI never imports Python types; `api.ts` mirrors the engine's Pydantic schemas.
- Rust never parses transcripts; it only moves files and processes.
- Dev: engine runs from `engine/.venv`. Prod: PyInstaller one-dir sidecar in the bundle.

## 17. Proposed provider / model abstraction

Python protocols in `huddle_engine/providers/`:

- `TranscriptionProvider.transcribe(wav, language) -> [Segment]` — `FasterWhisperProvider`
  (wraps upstream `Transcriber`); later `MlxWhisperProvider`, `WhisperCppProvider`.
- `DiarizationProvider.diarize(wav, segments) -> labels, embeddings` — `ResemblyzerProvider`
  (wraps upstream); later `PyannoteProvider`.
- `LLMProvider.complete_json(system, user, schema) -> dict` — `OllamaProvider`,
  `LMStudioProvider` (OpenAI-compatible), `LlamaCppProvider` (GGUF, Metal), `ExtractiveProvider`.
- `ComputeDevice` records: `{id, name, vendor, backend, memoryBytes?, deviceType, available,
  recommended}`; only backends that are actually usable are exposed.

The pipeline asks a `ProviderRegistry` for a capability; it never names Ollama.

## 18. Proposed model discovery strategy

`LocalModel` inventory (see §25 of the spec) built from adapters, each fail-soft:

1. App-managed dir (`<data>/models/**` + `models` table) — the only deletable source.
2. Ollama: `GET /api/tags` + `/api/show` when running; else parse
   `~/.ollama/models/manifests/**` (read-only) for names/sizes.
3. LM Studio: `GET http://localhost:1234/v1/models` when running; else `~/.lmstudio/models`.
4. Hugging Face cache: `~/.cache/huggingface/hub/models--*` (or `$HF_HOME`), classifying
   Whisper (CT2 vs transformers vs MLX by file contents), pyannote, embedding models.
5. whisper.cpp: `~/.cache/whisper.cpp`, `ggml-*.bin` in common dirs.
6. Custom user directories.

Compatibility is decided by (task, format, runtime) tuples, never by name alone. Results are
cached in the DB with a "Rescan" action; startup only pings HTTP endpoints (fast).

## 19. Minimal implementation plan for the first vertical slice

1. Scaffold Tauri 2 + React + TS + Tailwind; sidebar, meetings list, detail, settings shell.
2. Rust: cpal recording to WAV with 1 s flushes + `recording.json` metadata; device list;
   hardware detection; engine spawn with health check.
3. Engine: schema v1 + migrations; `POST /meetings/import`, `POST /meetings/{id}/process`,
   job runner with stages (preprocess → transcribe → diarize → summarize → ready);
   `FasterWhisperProvider` (`large-v3-turbo`, int8, CPU), `ResemblyzerProvider`,
   `OllamaProvider` if a compatible model is present, else extractive.
4. UI: record → stop → meeting appears → stage progress → transcript with speakers, audio
   player with click-to-seek, rename speaker, summary/decisions/action items.
5. Tests around migrations, job state, structured-output parsing, rename persistence.

Then Phases 6–9 (discovery, resolver, search, MCP) on top of the same services.

## Discrepancies between docs and code

- README says diarization "figures out who said what among remote participants" and the
  pyannote backend is "higher quality"; code default is resemblyzer with a per-Whisper-segment
  granularity, and pyannote's `label_speakers` path silently falls back on any exception.
- ARCHITECTURE.md lists `device.py` as "lazy torch import"; the code uses CTranslate2, not torch.
- README describes the LLM as "quantized local LLM (GGUF)"; the code runs it CPU-only
  (`n_gpu_layers=0`) even where a GPU exists.
