# Engineering decisions

Each entry: decision, why, consequences (including Windows impact where relevant).

1. **Python sidecar owns storage + processing; Rust owns native I/O.**
   MeetingScribe's engine is Python; rewriting ASR/diarization in Rust was explicitly out of
   scope. Putting SQLite in Python lets the HTTP API, MCP server and CLI share one service layer
   with zero duplication. Rust does what needs the OS: mic capture, permissions, paths, process
   lifecycle. *Windows:* identical split; cpal covers WASAPI, PyInstaller covers the sidecar.

2. **Vendor MeetingScribe's `pipeline/`, `models.py`, `config.py`, `device.py` byte-identical.**
   Upstream fixes can be re-copied. Our settings are projected onto its `MEETINGSCRIBE_*` env
   vars (`settings.apply_meetingscribe_env`). Its `db.py`, `server/`, `capture/`, `session.py`,
   `remote/`, `integrations/`, `bot/` are not vendored (online-meeting / Cornell specific).

3. **New schema instead of MeetingScribe's.** The spec's entities (speakers, meeting_speakers,
   words, topics, decisions with evidence, jobs, models, providers) do not fit "speaker is a
   free-text column". FTS5 pattern and the in-place rename trick were ported.

4. **Recording in Rust with a hand-rolled WAV writer.** `hound` only finalises the header on
   close, so a crash leaves an unreadable file. Our writer patches RIFF/data sizes every second
   and writes `recording.json` alongside, so recovery is just "import what's there".

5. **faster-whisper (CPU int8) as the V1 transcription runtime.** Reliability and distribution
   simplicity over peak speed (spec §6). It is the existing MeetingScribe path, cross-platform
   (CUDA on Windows), and `large-v3-turbo` int8 runs a 60 s file in ~16 s on an M4 Pro. MLX-whisper
   or whisper.cpp+Metal can be added as `TranscriptionProvider`s later; the model inventory
   already recognises their formats.

6. **Mixed-language handling by silence-boundary chunking + per-chunk language detection.**
   See ARCHITECTURE.md. Measured on the NL/EN fixture: whole-file pass lost 2 of 3 English turns;
   20 s chunks still lost 2 (a chunk starting in Dutch dropped the English turn that followed);
   ≤10 s chunks kept all 8 turns. faster-whisper's own `chunk_length=8` kept them too but cut
   words at fixed boundaries. Cost: one language detection per ≤10 s chunk.

7. **Resemblyzer stays the diarizer; segments are split at word pauses first; threshold 0.27.**
   Measured d-vector cosine distances on the fixture: same voice 0.03–0.06, two male voices
   0.25–0.33, male/female 0.42–0.49. Upstream's 0.40 merged the two men into one speaker; 0.27
   (setting `speakers.clusterThreshold`) separates all three. Real diarization granularity would
   need pyannote (gated) or an ONNX segmentation model; behind `DiarizationProvider`.

8. **PyAV (bundled with faster-whisper) instead of an ffmpeg binary** for import decoding.
   Removes the ffmpeg install/LGPL-bundling question from V1 entirely.

9. **LLM providers: Ollama → LM Studio → built-in llama.cpp → extractive.** Ollama and LM
   Studio are queried through documented localhost APIs; their models are never touched.
   llama-cpp-python is optional (prebuilt Metal wheels; not installed in the dev venv because
   cmake is absent on this machine and Ollama covers the test path). With no LLM at all, the
   pipeline still produces MeetingScribe's extractive notes and the UI says so.

10. **Structured notes ask the LLM for segment *indices*, not timestamps.** Small models copy
    indices reliably; we map them to start/end. Owners and due dates are re-validated in code
    (`Speaker 2` → null, non-ISO date → null) so "never fabricate" holds even if the model slips.

11. **Long transcripts: map-reduce at ~28k chars/chunk**, 16k-token contexts for Ollama.

12. **Speaker recognition suggests, never assigns.** `identifying_speakers` writes
    `suggested_speaker_id`/confidence; the UI shows Confirm / Choose another.

13. **Unicode61 tokenizer without Porter stemming + prefix matching.** Porter is English-only;
    prefix matching gives Dutch and English the same recall.

14. **Compute-device selection is recorded but only CPU/Metal are real today.** faster-whisper
    cannot use Metal; the setting exists so the UI/API shape is final before a Metal-capable
    provider arrives. Backends not usable on the machine are never listed.

15. **Model candidates:** Whisper CT2 conversions (MIT) from `mobiuslabsgmbh` (turbo) and
    `Systran`; Qwen3 4B/8B GGUF (Apache-2.0) from Qwen's official repos. Qwen2.5-3B (upstream
    default) avoided for licensing. sha256 verified from HF LFS metadata for GGUF files.

16. **Auth between shell and engine**: random bearer token per launch, loopback only.

17. **AI summaries are Ollama-only (2026-09-03, product decision).** One runtime to support and
    debug; models are shared with other Ollama apps; pulls go through Ollama's own library.
    LM Studio / GGUF files are still *discovered* and shown as "not in Ollama" so nothing is
    silently ignored. `LMStudioProvider` / `LlamaCppProvider` remain in `providers/llm.py` but
    are not wired into resolution. Models Huddle pulled are remembered so only those can be
    removed from the Model Manager.

18. **Speaker names are inferred from the conversation** (`speakers.inferNames`): a vocative
    ("Daan, kun jij…") assigns the name to the next speaker; a thanks assigns to the previous
    one. An LLM pass confirms/extends this (confidence ≥ 0.75). Inferred names are applied
    automatically but marked `name_source='inferred'` and shown with an "auto" tag, so the
    user sees why and can override. Renames propagate into owners, summary, topics, decisions.

19. **Per-segment language.** `transcript_segments.language` is stored per chunk;
    `meetings.language` is a comma list ordered by speech time ("nl,en") shown as
    "Dutch · English".

20. **Recordings quota instead of only age-based retention.** `storage.maxBytes` (5–50 GB) deletes
    the oldest audio first, after every processed meeting. Transcripts/notes are never deleted.

21. **System audio via a loopback device, not ScreenCaptureKit (yet).** macOS has no loopback
    input; BlackHole (user-installed) is detected by name and recorded as a second stream
    (`system.wav`) which the engine mixes at 16 kHz before transcription. A native
    ScreenCaptureKit/Core Audio tap provider is the future path (no driver install).

22. **Network MCP is opt-in, API-key protected, on its own port.** The private engine API stays
    loopback-only; the MCP streamable-HTTP app runs in a separate uvicorn thread on 0.0.0.0
    and checks `Authorization: Bearer hud_…` against SHA-256 hashes. Keys are shown once.
    Keys are framed as "one per MCP client": the key dialog asks which client it is for
    (Claude Code, Claude Desktop, Codex, Cursor, Copilot/VS Code, Windsurf, other) and the
    confirmation shows that client's exact install recipe with the key filled in
    (`lib/mcpClients.ts`). The same picker drives the stdio recipe for clients on this Mac.
    Claude Desktop cannot talk to a remote MCP URL with a bearer header itself, so its network
    recipe goes through the `mcp-remote` bridge.

23. **Destructive confirmations** use a 5 s countdown and a confirm button that changes position
    when it unlocks (`DangerDialog`), so nothing irreversible can be clicked through.

24. **Language-homogeneous chunks with a tiny detector.** Whisper pads every window to 30 s, so
    10 s chunks cost 3× the encoder passes of 30 s ones (measured ≈1 s of CPU per 1 s of audio
    on an M4 Pro — too slow for hour-long meetings). Now: VAD regions → language per region with
    the *tiny* model (~0.1 s) → consecutive same-language regions grouped into ≤28 s chunks that
    end on silence → main model with a forced language per chunk. Same mixed-language robustness,
    roughly 3× fewer encoder passes. The tiny model (~75 MB) is downloaded on first use.

25. **Live transcription while recording** (`live.py`). The shell writes the WAV progressively;
    the engine transcribes closed speech regions every few seconds into `live_segments`, and the
    final `transcribing` stage reuses them, only transcribing the tail. Wrap-up time becomes
    diarization + summary. Live text is best-effort: any failure falls back to a full pass.

26. **Stage progress and cancellation.** Stages report a 0–1 fraction (throttled writes to
    `processing_jobs`) and check a cancel flag between chunks; deleting a meeting cancels its job.

27. **One engine per app instance.** The shell writes `<data>/engine.pid`, kills a stale engine
    on start (only if that pid is really a huddle engine) and on every exit event. Orphaned
    engines from dev reloads caused "frozen" progress because the UI polled a different process
    than the one doing the work. `HUDDLE_NO_JOBS=1` runs an engine without a job runner (browser
    dev mode) so two engines never process the same meeting.

28. **System audio via ScreenCaptureKit, no driver.** (Supersedes the BlackHole approach.)
    A ~200-line Swift helper (`apps/desktop/native/audio-tap`, built by
    `scripts/build-audio-tap.sh` into the Tauri `externalBin`) captures system audio with
    `SCStream(capturesAudio: true, excludesCurrentProcessAudio: true)` into `system.wav`,
    patching the WAV header every second. It needs the macOS "Screen & System Audio Recording"
    permission; the UI can preflight/request it and open the right System Settings pane.
    Rust spawns the helper next to the microphone capture and stops it by closing its stdin.

29. **MLX Whisper on Apple Silicon.** CTranslate2 has no Metal backend: ~2× realtime on the CPU
    of an M4 Pro. MLX Whisper (`mlx-community/whisper-large-v3-turbo`) measured 6.1 s for a 114 s
    fixture (~19× realtime). `MlxWhisperProvider` reuses the shared VAD → language (tiny CT2
    model) → chunk path; the resolver prefers an MLX-format model of the same size, and the
    marketplace lists the MLX build as the recommended download. CTranslate2 stays as the
    cross-platform fallback (Windows, Intel Macs).

30. **Notes are written in the app language, not the spoken one** (`general.uiLanguage`).
    Spoken languages are always auto-detected; Whisper large-v3 covers 99 languages, so no
    language packs are needed. **Action items are opt-in** (`notes.autoActionItems`, default off)
    and extracted on demand with a dedicated prompt.

31. **One spoken language per transcript.** Per-utterance language switching produced garbled
    mixed transcripts; meetings are held in one language. The language is detected once
    (duration-weighted vote of the tiny detector over ≤12 speech regions) and forced for all
    chunks; the user can pick it when starting a recording or change it later per meeting
    (`meetings.language_override`), which re-transcribes and regenerates the notes.

32. **Window-level diarization.** Segments longer than ~4.5 s are embedded in 3 s windows
    (1.5 s hop); windows are clustered and a segment whose windows disagree is split at the word
    boundary where the speaker changes (runs shorter than 3 words are smoothed). Fixes "one
    speaker for minutes" without changing the encoder or threshold.

33. **API keys expire (30/60/90 days, default 30) and can be renewed.** Renewal keeps the same
    secret and extends the expiry by the original validity. Trade-off: convenient for a
    trusted LAN agent, but it does not rotate the secret; the UI recommends create-new + delete
    as the safer routine. Expired keys are refused immediately.

34. **System audio is all-or-nothing.** ScreenCaptureKit captures the system mix, not a chosen
    output device; the dropdown therefore offers Off / All apps. Per-device capture would need
    Core Audio process taps (macOS 14.2+), which is a possible follow-up.

35. **Brand palette (revised 2026-09-03): one red, warm paper.** The final logo is a single red
    (#ea3d3d) wordmark on off-white, so the UI uses exactly one accent: red for record, delete,
    active navigation, progress and highlights. Primary action buttons are "ink" (warm near-black,
    inverted to off-white in dark mode) so a confirm button is never mistaken for a destructive
    one. Neutrals are warm (paper #f9f8f6, ink #1c1918) rather than blue-gray. The earlier
    indigo/coral/lilac palette is gone. App icons are generated with `tauri icon` from
    `src-tauri/brand/app-icon.svg`, which places the brand tile on Apple's 824/1024 icon grid
    with a soft shadow so it sits at the same size as other Mac icons.

36. **Cancellable reprocessing keeps the previous version.** Stages write their tables only after
    they finish, so a cancel (or the delete of a meeting) between chunks leaves the old transcript
    and notes intact; the job is marked ready again with "Kept previous result". The Processes
    page lists running/queued jobs, live transcriptions and downloads with progress, ETA and cancel.

37. **Element resets live in `@layer base`.** Unlayered author CSS beats every Tailwind utility;
    `button { color: inherit }` outside a layer silently overrode `text-white` on red/indigo
    buttons in light mode.

38. **Visual language derived from the wordmark.** The logo's chunky rounded letterforms set the
    tone: headings, titles, the recording timer and avatar initials use the system rounded face
    (`ui-rounded` → SF Pro Rounded, falling back to the sans stack elsewhere); every content
    panel shares one `.panel` class (12 px radius, hairline border, faint layered shadow); section
    titles and the active sidebar item carry a small red tick; the record CTA and record button
    are pills with a red glow; the record screen has a soft red wash and expanding rings while
    recording; empty states carry the "h" glyph as a faint watermark; meeting rows show coloured
    initials for participants and speaker chips use the speaker's colour as a tint. Speaker colours
    stay a separate 8-hue palette (indigo swapped for fuchsia to avoid the old brand colour) so
    "Speaker 1" is never confused with a red state. Classes derived at runtime by string
    manipulation are not generated by Tailwind v4; the palette therefore lists every class
    literally (`dot`, `solid`, `text`, `bg`).

39. **Meeting actions live in one hook.** `useMeetingActions` (components/MeetingMenu.tsx) owns
    export, change-language, regenerate-summary, reprocess and delete plus their dialogs; the
    detail page's "…" menu and the overview's right-click menu both render `MeetingMenuList`
    against it, so the two menus cannot drift apart. Dialogs keep their own copy of the meeting,
    so the context menu can close before the confirmation is answered.

40. **⌘K quick search** (Ctrl+K on Windows, via `hasMod`) opens a palette with recent meetings,
    title matches and the first transcript hits; Enter hands the query to the Search page
    (`View.search.nonce` forces the page to adopt a repeated query). Destructive confirmations
    without a countdown (`DangerDialog seconds={0}`) are used where the data is easily rebuilt,
    such as a known voice profile.

41. **Speaker separation moved to sherpa-onnx: pyannote segmentation 3.0 + TitaNet embeddings**
    (supersedes 7 and 32 as the default; Resemblyzer stays as the offline fallback). Measured on
    a real 39-minute two-person meeting (Opus, band-limited to
    ~4 kHz, i.e. a Bluetooth headset microphone): Resemblyzer d-vectors of the two voices
    overlapped completely (centroid distance 0.21, within-speaker p90 0.24–0.28), so no threshold
    could work — 0.27 gave 133 clusters, 0.45 gave 4, 0.5 gave 1. Whisper was never the issue.
    The new pipeline: pyannote segmentation (frame-level speaker changes, so a Whisper segment
    with two speakers is cut at the right word) → 3 s windows inside speaker-homogeneous turns →
    TitaNet-large embeddings → average-link clustering → clusters under max(8 s, 3 %) folded
    into the nearest big one (average linkage otherwise leaves outlier windows as "speakers") →
    optional user hint "N people spoke" caps the count. Three embedding models were compared
    (TitaNet-large, ERes2Net-large, ERes2NetV2); TitaNet was the only one that separated the
    synthetic 3-voice fixtures, and on the real meeting all three agreed on who-is-who
    (adjusted Rand 0.88–0.94), so the split is trustworthy. Threshold 0.60 is the one value
    correct on all four meetings with a known speaker count (real meeting=2 for 0.55–0.80; fixture A=3 for
    0.50–0.70; Sprint Q4=3 for 0.60–0.70; Weekly design sync=3 for 0.50–0.60); the 1-hour
    a 1-hour real meeting yields 4 at 0.60 where the user named 3. Cost: ~4 min for 39 min of audio on
    an M4 Pro (segmentation dominates). Models (1.5 MB + 101 MB) are fetched from sherpa-onnx's
    GitHub releases into `models/speaker` on first use; if that fails the stage falls back to
    Resemblyzer and says so in its detail. Voice profiles are tagged with the embedding model;
    profiles of another model are never compared and are re-enrolled on the next confirmation.
    Narrowband audio is detected (energy above 4 kHz < 1 %) and mentioned in the stage detail,
    because it degrades every speaker model.

42. **Keyboard shortcuts are native menu accelerators.** Rust builds the macOS menu (Huddle ›
    Settings… ⌘, · File › New Recording ⌘N, Import Audio… ⌘O · View › Meetings ⌘1, Ask ⌘2,
    Action Items ⌘3, Processes ⌘4, Search ⌘K) and forwards every menu event to the UI as a
    `menu` event. macOS handles the key equivalents before the webview sees them, so the UI
    has no key handler in the desktop app (the browser dev build keeps a small one). ⌘N is
    deliberately not shown in the UI; the sidebar reveals ⌘1–4/⌘K/⌘, on hover only.

43. **Interface sounds are synthesised** (`lib/sounds.ts`, Web Audio): a 35 ms triangle tick for
    buttons, a rising/falling two-note figure for recording start/stop, a three-note chime when a
    meeting finishes processing, a soft low double thud on failure. No audio assets, no network;
    off with `general.sounds`. Pressable controls share one `.pressable` class (spring-eased
    scale to 0.94 on :active, transform only so layout never shifts).

44. **MCP recipes use the real engine command.** "huddle-engine" is the packaged sidecar's name
    and is never on `$PATH` (nor is the dev venv's python), so `engine_mcp_command` returns the
    absolute program + args the shell itself uses (`… -m huddle_engine mcp --data-dir …` in
    development, the bundled binary when packaged). The dev venv gets a `huddle_engine_dev.pth`
    so `-m huddle_engine` imports from any working directory, as MCP clients start it from theirs.

45. **System audio: two ScreenCaptureKit facts learned the hard way** (macOS 26.6). (a) Audio is
    only delivered while a live video pipeline exists: a 2×2 frame at 1 fps with no `.screen`
    output starts fine ("READY") and never produces one audio buffer. The helper now attaches a
    screen output whose frames it discards, at 64×36 / 5 fps. (b) SCK delivers non-interleaved
    float stereo (format flags 41); fetching it with a one-entry `AudioBufferList` fails with
    "array too small", which the helper used to swallow — every buffer dropped, `system.wav`
    stayed a 44-byte header. The list is now sized per channel. `HUDDLE_TAP_DEBUG=1` prints
    display, config, first-buffer format and frame/buffer counters to stderr; `HUDDLE_TAP_W/H/FPS`
    override the video settings. Verified from the shell with `afplay`: 6.5 s captured, peak 0.87.

46. **Playback mixes both streams; meetings name themselves.** `processed.wav` (16 kHz) is for the
    models; for listening, preprocessing also writes `mix.wav` (microphone + system audio at the
    microphone's sample rate, peak-normalised) and `audio_path` prefers it — generating it on
    first playback for recordings made before this existed. The summary prompt returns a `title`
    (3–7 words, no date); the summarising stage applies it only when the meeting still has a
    Huddle-generated name (`is_default_title`: "Meeting 3 Sep 2026, 15:59", "Recovered recording",
    empty), so typed names and imported file names are never overwritten. Deleting a meeting no
    longer has a countdown (the recording, transcript and notes are the user's own work to lose,
    but one click too many is recoverable by re-importing; "delete everything" keeps its 5 s).

47. **"Refine notes" and progressive action items are job stages.** Two on-demand stages joined
    the pipeline (`refining` before `summarizing`, `extracting_actions` after it); `DEFAULT_PIPELINE`
    excludes them and they start as "skipped", so recovery never runs them uninvited and the UI
    hides them until used. Refining stores the user's rich-text feedback on the meeting
    (`meetings.context_html`), asks the model only to *translate* it into speaker renames and
    word replacements (applied deterministically: word-boundary, case-insensitive, capital kept)
    and then reruns the summary with the feedback as authoritative context; the feedback is fed to
    every later summary/action-item pass too. Action items are extracted in ~9k-character chunks
    and inserted per chunk, so the list fills while the stage reports progress on the meeting and
    in Processes — the earlier synchronous endpoint blocked the request for minutes with no
    feedback. The editor is a dependency-free contentEditable (bold, italic, bullets).

48. **First macOS build (2026-09-03).** PyInstaller one-dir sidecar (1.3 GB) + Tauri `.app`
    (1.8 GB, ad-hoc signed), see PACKAGING.md for the exact recipe and blockers. Three lessons:
    PyInstaller needs a wrapper entry because `__main__.py` uses relative imports; `mcp.cli` must be
    excluded (its import exits the process when `typer` is missing); and Tauri copies
    `bundle.resources` next to the *debug* executable too, so `locate_engine` now prefers the repo
    venv in debug builds and the bundled sidecar only in release builds.

49. **The packaged app ships without torch; speaker models ride inside the bundle.** The first
    build was 1.84 GB, of which torch (526 MB) plus its numba/llvmlite/librosa tail existed only
    for the Resemblyzer fallback diarizer. The sidecar now excludes torch/torchaudio/resemblyzer/
    librosa (numba stays — `mlx_whisper` needs it for word timings) and the two sherpa models
    (TitaNet large 101 MB + pyannote segmentation 1.5 MB) are bundled as Tauri resources
    (`resources/models/speaker`, fetched by `scripts/fetch-speaker-models.sh`, git-ignored). The
    shell passes `HUDDLE_BUNDLED_MODELS`; the engine uses them in place and never downloads. The
    Resemblyzer path remains for dev checkouts only and the setting dropdown is gone: TitaNet is the
    one supported model. Whisper and LLM models are, as before, never bundled. Two packaging lessons:
    a failed import of a nanobind extension (MLX) must not be retried in-process — the second
    attempt re-registers its types and aborts the interpreter, which is what "engine unreachable"
    on a fresh start was — so `mlx_available()` caches and logs its result; and the marketplace's
    "Installed" must come from the model inventory, not from a snapshot folder that exists from the
    first byte of a download.

50. **Marketplace is hardware-aware; Qwen3.5 replaces Qwen3/Gemma.** `/models/candidates` sets
    "Recommended" per machine: the MLX Whisper where MLX imports, the CPU build elsewhere; the AI
    model by memory (Qwen3.5 9B from 12 GB up, Qwen3.5 4B below; Gemma 3 12B dropped — too big for
    what meeting notes need). Onboarding suggests the same picks. Download speed was checked: the
    Hugging Face path (huggingface_hub with hf_xet) pulls a 484 MB file in ~5 s on this connection
    (≈90 MB/s); hf_transfer gave no gain, so it is not added. Ollama pulls are paced by Ollama's own
    registry and are out of our hands.

51. **App builds go through `scripts/build-app.sh`.** Tauri dereferences symlinks in
    `bundle.resources`; MLX locates `mlx.metallib` relative to the real `libmlx.dylib`, so the
    dereferenced top-level copy broke Metal inside the .app ("Failed to load the default metallib")
    while the identical files worked from the build folder, and the marketplace recommended the
    CPU Whisper. MLX has no environment override for the path (checked the binary's strings and
    tested `METAL_PATH`), so the script recreates the dist folder's symlinks inside the bundle after
    `tauri build`. Verified: fresh bundle recommends the Apple Silicon Whisper.

52. **The sidecar is pruned after PyInstaller** (4 133 → 677 files). Test suites, C/C++ headers,
    Cython/pyi sources and the `.py` copies of packages whose modules live in the PYZ archive are
    deleted by `scripts/build-engine.sh`; runtime data (MLX metallib, mlx_whisper assets, tokenizer
    files, dylibs, dist-info) is kept. Reason: file count dominated copy/sign/install time of the
    .app. Verified by running the whole pipeline through the pruned bundle on a fixture.

53. **No more MeetingScribe code.** What was still imported had shrunk to a faster-whisper loader,
    three vector helpers, the export formats and the no-LLM fallback notes — all replaced by
    Huddle modules (`providers/transcription.py` `load_whisper`/`vocab_prompt`, `services/voices.py`,
    `services/exports.py`, `text.py`). The Resemblyzer fallback diarizer went with it: the sherpa
    models ship in the app, so a fallback that needed torch (~650 MB) and only ever ran in dev had
    no user. Speaker separation now fails loudly and retryably if the models are missing instead
    of silently degrading. `engine/meetingscribe/` is deleted; `docs/AUDIT.md` stays as history.

54. **The shell owns the LAN port for network MCP.** The engine's HTTP MCP server now listens on
    loopback only (`McpStatus.loopback_port`); when "Network access" is on, the Rust shell binds
    `0.0.0.0:<mcp.port>` itself and forwards each connection (tokio `copy_bidirectional`). Effects:
    macOS's "accept incoming network connections?" firewall prompt names Huddle and appears the
    moment the switch is flipped (the bind happens in the command, not in a background task), the
    decision sticks to the app rather than to an unsigned Python binary, and the engine process is
    never reachable from the network directly. Errors (port in use, firewall refusal) surface next
    to the switch with a shortcut to System Settings → Network → Firewall. `HUDDLE_MCP_BIND_PUBLIC=1`
    keeps the old direct binding for headless use without the shell.

55. **Dead code removed before the first commit.** The LM Studio and llama.cpp LLM providers
    (never wired since #17; LM Studio models are still discovered and shown as "not in Ollama"),
    the unused provider Protocols, and a handful of unreferenced helpers and settings
    (`speakers.embeddingModel`, `human_bytes`, `mean_embedding`, `queue_snapshot`) are gone.

## Known gaps / next

* Semantic search + `embeddings` table are reserved; `search_semantic` falls back to FTS.
* No live transcription (deliberate, spec §40).
* Diarization threshold (0.60) is calibrated on five meetings; more real room recordings with
  a known speaker count would tighten it. The voice-recognition match threshold for TitaNet
  embeddings (`speakers.sherpaMatchThreshold` 0.6) is a first guess — suggestions only.
* Packaging (PyInstaller + notarisation) is scripted but not yet run end-to-end — see PACKAGING.md.
* Windows: recording (cpal/WASAPI) and CUDA detection are in place conceptually; untested.
