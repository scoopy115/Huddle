# Licensing overview

| Component | License | Redistribution notes |
|---|---|---|
| Huddle (this repo) | MIT | — |
| faster-whisper, CTranslate2 | MIT | OK to bundle. |
| PyAV / FFmpeg libraries (via faster-whisper wheels) | LGPL (as shipped in PyAV wheels) | Dynamically linked; keep the wheels unmodified. Do **not** replace with a GPL ffmpeg build. |
| sherpa-onnx (+ onnxruntime) | Apache-2.0 / MIT | OK to bundle. Runs the speaker segmentation and embedding models below. |
| numpy, scipy, scikit-learn, soundfile | BSD-3 | OK. |
| MLX, mlx-whisper | MIT | Apple Silicon Whisper runtime. |
| numba, llvmlite | BSD-2 | Needed by mlx-whisper for word timings. |
| tiktoken, tokenizers, huggingface_hub | MIT / Apache-2.0 | OK. |
| FastAPI, uvicorn, pydantic, httpx | MIT/BSD | OK. |
| mcp (Python SDK) | MIT | OK. |
| Tauri, cpal, reqwest, tokio, sysinfo | MIT/Apache-2.0 | OK. |
| React, Tailwind, Lucide, Radix | MIT / ISC | OK. |

## Models (downloaded by the user, never bundled)

| Model | Source | License | Notes |
|---|---|---|---|
| Whisper large-v3-turbo (MLX) | `mlx-community/whisper-large-v3-turbo` | MIT | Recommended on Apple Silicon. |
| Whisper large-v3-turbo (CT2) | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | MIT | CPU build (Windows / Intel). |
| Whisper large-v3 / medium / small (CT2) | `Systran/faster-whisper-*` | MIT | |
| Qwen3.5 4B / 9B (via Ollama) | Ollama library `qwen3.5` | Apache-2.0 | 4B is the recommended notes model; pulled by Ollama on request. |
| pyannote segmentation 3.0 (ONNX) | sherpa-onnx GitHub releases | MIT | Speaker-turn detection. Shipped inside the app bundle; a dev checkout fetches it to `models/speaker` on first use. |
| NVIDIA TitaNet large (ONNX) | sherpa-onnx GitHub releases | CC-BY-4.0 | The speaker-embedding model. Shipped inside the app bundle (`Contents/Resources/models/speaker`); attribution here and in the About text. |
| pyannote speaker-diarization 3.1 pipeline | Hugging Face (gated) | MIT code, gated weights | Not used; the segmentation model above comes from the public ONNX conversion instead. |
| Ollama / LM Studio models | user's installation | model-specific | Read-only; used in place through their local APIs. Licenses (e.g. Llama Community License) are the user's responsibility as they already installed them. |

## Risks

* GPL contamination: only if someone swaps PyAV's FFmpeg for a GPL build. Documented above.
* Model licenses for external providers are not checked by the app; the Model Manager shows
  the source so users know what they are running.
* Qwen2.5 3B/72B carry a non-Apache "Qwen" license — intentionally not offered.
