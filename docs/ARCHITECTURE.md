# Architecture

## Boundary

```
┌──────────────────────── Tauri desktop app (apps/desktop) ────────────────────────┐
│ React UI (src/)                                                                  │
│   screens: Meetings · Meeting · Record · Search · ActionItems · Settings · Onboarding
│   lib/api.ts   — the ONLY typed surface to the engine (mirrors schemas.py)       │
│   lib/native.ts — the ONLY place Tauri commands are invoked                      │
│                          │ invoke()                                              │
│ Rust (src-tauri/src/)    ▼                                                       │
│   recording.rs  cpal input → mono PCM16 WAV, header patched every second,        │
│                 recording.json metadata, level events, unfinished-recording scan │
│   devices.rs    input device enumeration (CoreAudio; WASAPI on Windows via cpal) │
│   hardware.rs   ComputeDevice records (Apple GPU/Metal, CPU; CUDA/Vulkan later)  │
│   paths.rs      app data dir ($APPDATA/com.huddle.desktop, HUDDLE_DATA_DIR)      │
│   engine.rs     spawn sidecar on a free port with a per-launch bearer token,      │
│                 health polling, restart, `engine_fetch` proxy                     │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │ HTTP 127.0.0.1:<port>, Authorization: Bearer
┌──────────────────────── Engine (engine/huddle_engine) ───────────────────────────┐
│ app.py            FastAPI routes (thin; all logic in services/)                  │
│ context.py        wires config · db · registry · downloads · job runner          │
│ db/               Database (one connection, RLock, WAL) · migrations (user_version)
│ schemas.py        Pydantic contract (camelCase)                                  │
│ services/         meetings · transcripts · search · action_items · exports · ask │
│ jobs/             runner (persisted state machine) · stages (independently retryable)
│ providers/        base protocols · transcription (faster-whisper) · diarization  │
│                   (sherpa-onnx) · llm (Ollama, LM Studio, llama.cpp, extractive) │
│                   · summarize (structured notes, map-reduce) · compute            │
│ discovery/        managed · ollama · lmstudio · hf_cache · whisper_cpp · registry│
│ resolver.py       resolveModel(requirements) with strict priority               │
│ downloads.py      HF snapshot / GGUF downloads, progress, sha256                 │
│ mcp_server.py     MCP tools over the same services (stdio)                       │
│ __main__.py       serve · mcp · process · doctor                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Data model (SQLite, schema v1)

`meetings` → `recordings` (1:1 today), `meeting_speakers` (diarization clusters with
`display_name` / `speaker_id` mapping and per-cluster embedding), `transcript_segments`
(canonical transcript, `meeting_speaker_id` FK), `transcript_words`, `summaries`, `topics`,
`decisions` (evidence start/end + segment FK), `action_items` (nullable owner/due_date,
confidence, evidence, `source` auto|manual), `embeddings` (reserved), `processing_jobs`
(one per meeting, per-stage JSON), `models`, `providers`, `settings`, `speakers` (known people
with running-mean voice embedding). `segments_fts` is an external-content FTS5 index kept in
sync by triggers.

Renaming a speaker changes `meeting_speakers`, never segment rows, so segment IDs and every
evidence reference stay valid.

## Processing job

```
saved → queued → running[preprocessing → transcribing → diarizing → identifying_speakers → summarizing → indexing] → ready | failed
```

* Each stage writes only its own tables and can be retried on its own; retrying re-runs the
  stages that depend on it (`stages.DOWNSTREAM`).
* `preprocessing`/`transcribing` failures skip the rest; any other failure leaves the meeting
  `ready` with the transcript and a per-stage retry button.
* On startup, jobs still `running` are marked interrupted (retryable); `queued` jobs resume.
* Errors carry a user-facing message plus technical `errorDetail` shown under "Details".

## Transcription of mixed Dutch/English

Whisper detects one language per 30 s window and silently drops speech in another language
inside that window (verified on a NL/EN fixture). In `auto` mode the provider therefore runs
the VAD, groups speech regions into ≤20 s chunks that end only at silence, and transcribes each
chunk with its own language detection. Word-level timestamps are kept; segments are split at
word gaps ≥0.55 s so diarization (one label per segment) can follow speaker turns.

## Provider & model resolution

`resolveModel` priority: explicit selection → app-managed → externally managed provider
(Ollama, LM Studio) → cached (HF) → recommended download. Compatibility is a
(task, format, runtime) decision; a Transformers-format Whisper is *not* compatible with
faster-whisper and the UI says so. External models are read-only; only `source == our_app`
models under `<data>/models` can be deleted.

## Security

The engine binds 127.0.0.1 only and requires a bearer token generated per launch by the
desktop shell. MCP runs over stdio, launched by the client. No remote MCP.
