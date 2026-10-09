"""Central model resolution (spec §28). Priority:

1. explicit user selection
2. compatible app-managed model
3. compatible externally managed provider/model (Ollama)
4. compatible cached model (Hugging Face cache, Whisper only)
5. recommended download / Ollama pull

AI summaries run through **Ollama only** (product decision, 2026-09-03): one runtime to
support, models are shared with every other Ollama app on the machine, and pulls come
with Ollama's own library/licensing metadata. Whisper still runs in-process.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .discovery.registry import Registry
from .schemas import DownloadCandidate, LocalModel, Resolution

GB = 1024 ** 3

# Whisper CT2 conversions (MIT). Licenses checked 2026-09.
WHISPER_CANDIDATES: list[DownloadCandidate] = [
    DownloadCandidate(id="whisper:mlx-large-v3-turbo", name="Whisper large-v3-turbo", task="transcription",
                      purpose="Transcription on the GPU — about 10× faster than the CPU version", size_bytes=1_620_000_000,
                      source="huggingface", url="mlx-community/whisper-large-v3-turbo", license="MIT",
                      recommended=True),
    # PCs with an NVIDIA card: the same CTranslate2 model plus cuBLAS (providers/cuda_runtime.py), as
    # one download, next to the CPU row — the way the Mac shows the Apple Silicon and CPU builds.
    DownloadCandidate(id="whisper:cuda-large-v3-turbo", name="Whisper large-v3-turbo", task="transcription",
                      purpose="Transcription — several times faster than on the CPU", size_bytes=1_620_000_000,
                      source="huggingface", url="mobiuslabsgmbh/faster-whisper-large-v3-turbo", license="MIT"),
    DownloadCandidate(id="whisper:large-v3-turbo", name="Whisper large-v3-turbo", task="transcription",
                      purpose="Transcription — works on every computer", size_bytes=1_620_000_000,
                      source="huggingface", url="mobiuslabsgmbh/faster-whisper-large-v3-turbo", license="MIT"),
    DownloadCandidate(id="whisper:large-v3", name="Whisper large-v3", task="transcription",
                      purpose="Transcription — highest accuracy, about 2× slower", size_bytes=3_090_000_000,
                      source="huggingface", url="Systran/faster-whisper-large-v3", license="MIT"),
    DownloadCandidate(id="whisper:medium", name="Whisper medium", task="transcription",
                      purpose="Transcription — faster, less accurate in languages other than English", size_bytes=1_530_000_000,
                      source="huggingface", url="Systran/faster-whisper-medium", license="MIT"),
    DownloadCandidate(id="whisper:small", name="Whisper small", task="transcription",
                      purpose="Transcription — fastest, for older or low-memory computers", size_bytes=484_000_000,
                      source="huggingface", url="Systran/faster-whisper-small", license="MIT"),
    # A second family next to Whisper. Never the automatic pick: chosen by hand under Settings → Models.
    # Windows and Linux get the ONNX export, run by sherpa-onnx (providers/parakeet_onnx.py).
    DownloadCandidate(id="parakeet:onnx-tdt-0.6b-v3", name="Parakeet TDT 0.6B v3", task="transcription",
                      purpose="Transcription — 25 European languages, punctuation from the model",
                      size_bytes=487_000_000, source="github",
                      url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
                      license="CC-BY-4.0", license_url="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3"),
    DownloadCandidate(id="parakeet:mlx-tdt-0.6b-v3", name="Parakeet TDT 0.6B v3", task="transcription",
                      purpose="Transcription on the GPU — 25 European languages", size_bytes=2_510_000_000,
                      source="huggingface", url="mlx-community/parakeet-tdt-0.6b-v3", license="CC-BY-4.0",
                      license_url="https://creativecommons.org/licenses/by/4.0/"),
]

# Ollama library models (pulled through Ollama; sizes are Q4_K_M downloads). The 4B is the
# recommendation for everyone (product decision): quick, good notes; the 9B is offered for long
# meetings on 16 GB+ Macs and greyed out below that.
LLM_CANDIDATES: list[DownloadCandidate] = [
    DownloadCandidate(id="ollama:qwen3.5:4b", name="Qwen3.5 4B", task="llm",
                      purpose="Meeting summaries — 8 GB of unified memory or a graphics card with 6 GB", size_bytes=3_400_000_000,
                      source="ollama", url="qwen3.5:4b", license="Apache-2.0", recommended=True,
                      min_memory_bytes=8 * GB, min_vram_bytes=6 * GB),
    DownloadCandidate(id="ollama:qwen3.5:9b", name="Qwen3.5 9B", task="llm",
                      purpose="Meeting summaries — 16 GB of unified memory or a graphics card with 12 GB", size_bytes=6_600_000_000,
                      source="ollama", url="qwen3.5:9b", license="Apache-2.0", min_memory_bytes=16 * GB, min_vram_bytes=12 * GB),
]

# System memory a CPU-only PC needs to run an AI model at all (Ollama keeps the whole model plus
# the context in RAM, next to Whisper and the app): twice the unified-memory floor.
CPU_LLM_MEMORY = {"ollama:qwen3.5:4b": 16 * GB, "ollama:qwen3.5:9b": 32 * GB}
# CPU Whisper (CTranslate2 int8) memory floors; large-v3 keeps ~3 GB of weights resident.
WHISPER_MEMORY = {"whisper:large-v3": 12 * GB, "whisper:small": 4 * GB}

GOOD_LLM_FAMILIES = ("qwen3.5", "qwen35", "qwen3", "qwen2.5", "llama-3.3", "llama-3.2", "llama-3.1", "llama3", "llama",
                     "gemma-3", "gemma3", "gemma", "mistral", "mixtral", "phi-4", "phi", "gpt-oss", "hermes",
                     "deepseek", "granite", "command-r")
EXCLUDE_NAME_PARTS = ("coder", "code", "vision", "-vl", ":vl", "embed", "reranker", "guard", "math", "audio")


def params_b(m: LocalModel) -> float | None:
    p = (m.meta or {}).get("parameterSize")
    if isinstance(p, str) and p.upper().endswith("B"):
        try:
            return float(p[:-1])
        except ValueError:
            return None
    if m.size_bytes:            # rough: Q4 ≈ 0.6 bytes/param
        return round(m.size_bytes / 0.6e9, 1)
    return None


def is_general_chat_model(m: LocalModel) -> bool:
    name = m.name.lower()
    fam = (m.family or "").lower()
    return (m.task == "llm" and not any(k in name for k in EXCLUDE_NAME_PARTS)
            and any(f in fam or f in name for f in GOOD_LLM_FAMILIES))


def recommendation_band(memory_bytes: int | None) -> tuple[float, float]:
    """Parameter range we recommend: ~4 B for everyone (quick, good notes). Bigger installed
    models still work and are chosen when nothing in the band is installed."""
    return (3.0, 5.0)


def is_recommended_size(m: LocalModel, memory_bytes: int | None) -> bool:
    p = params_b(m)
    lo, hi = recommendation_band(memory_bytes)
    return p is not None and lo <= p <= hi


def llm_score(m: LocalModel, memory_bytes: int | None) -> tuple:
    if not m.compatible or not is_general_chat_model(m):
        return (-1,)
    params = params_b(m) or 0
    lo, hi = recommendation_band(memory_bytes)
    running = bool((m.meta or {}).get("running", True))
    fam_rank = 0
    name, fam = m.name.lower(), (m.family or "").lower()
    for i, f in enumerate(GOOD_LLM_FAMILIES):
        if f in fam or f in name:
            fam_rank = len(GOOD_LLM_FAMILIES) - i
            break
    return (int(lo <= params <= hi), int(3 <= params <= 40), int(running), fam_rank, -abs(params - (lo + hi) / 2))


@dataclass
class ResolverContext:
    registry: Registry
    settings: dict[str, Any]
    memory_bytes: int | None = None
    # providers.compute.hardware_info(); None means "unknown machine" (fits are not judged).
    hardware: dict[str, Any] | None = None


def _gb(n: int | None) -> str:
    return f"{round((n or 0) / GB)} GB"


def fit(c: DownloadCandidate, hw: dict[str, Any] | None) -> tuple[str, str | None]:
    """Can this computer run the candidate: ("ok" | "slow" | "no", reason). Three machine shapes
    (see providers/compute.py): unified memory (Apple Silicon) judges by system memory, a PC with
    a dedicated card judges the AI model by graphics memory, a CPU-only PC needs twice the memory
    and is always slow for AI models."""
    if not hw:
        return "ok", None
    mem = hw.get("memoryBytes") or 0
    unified = bool(hw.get("unifiedMemory"))
    acc = hw.get("acceleratorBytes")
    if c.task == "llm":
        if unified:
            if c.min_memory_bytes and mem and mem < c.min_memory_bytes:
                return "no", f"Needs {_gb(c.min_memory_bytes)} of unified memory."
            return "ok", None
        cpu_floor = CPU_LLM_MEMORY.get(c.id, (c.min_memory_bytes or 8 * GB) * 2)
        if acc:
            if not c.min_vram_bytes or acc >= c.min_vram_bytes:
                return "ok", None
            if mem >= cpu_floor:
                return "slow", f"Does not fit in the graphics card's {_gb(acc)}; runs on the CPU, slowly."
            return "no", f"Needs a graphics card with {_gb(c.min_vram_bytes)}."
        if mem >= cpu_floor:
            return "slow", "No usable graphics card: runs on the CPU, slowly."
        return "no", f"Needs {_gb(cpu_floor)} of memory or a graphics card with {_gb(c.min_vram_bytes)}."
    if c.task == "transcription":
        floor = WHISPER_MEMORY.get(c.id, 8 * GB)
        if mem and mem < floor:
            return "no", f"Needs {_gb(floor)} of memory."
        return "ok", None
    return "ok", None


def _whisper_download(ctx: ResolverContext) -> DownloadCandidate:
    from .providers.transcription import mlx_available
    mem = ctx.memory_bytes or 0
    if mlx_available():
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:mlx-large-v3-turbo")
    if mem and mem < 8 * GB:
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:small")
    if mem and mem < 12 * GB:
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:medium")
    if has_nvidia_card(ctx.hardware):
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:cuda-large-v3-turbo")
    return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:large-v3-turbo")


def _llm_download(ctx: ResolverContext) -> DownloadCandidate:
    """The recommended AI model; when no Ollama is present the runtime download rides along."""
    from .providers import ollama_runtime
    cand = next(c for c in LLM_CANDIDATES if c.recommended)
    if ollama_runtime.binary() is None:
        cand = cand.model_copy(update={"size_bytes": cand.size_bytes + ollama_runtime.ARCHIVE_SIZE,
                                       "purpose": cand.purpose + " · includes the local AI runtime"})
    return cand


def candidates_for(ctx: ResolverContext) -> list[DownloadCandidate]:
    """Marketplace list for *this* machine: Apple-Silicon-only builds (MLX) appear only where MLX
    runs; every row carries `fit`/`fit_reason` so the UI can grey out what this computer cannot
    run, and Recommended marks the automatic picks."""
    from .providers.parakeet import parakeet_available
    from .providers.transcription import mlx_available
    whisper_pick = _whisper_download(ctx).id
    llm_pick = _llm_download(ctx).id
    out: list[DownloadCandidate] = []
    nvidia = has_nvidia_card(ctx.hardware)
    cuda = nvidia and _cuda_installed()
    for c in WHISPER_CANDIDATES + LLM_CANDIDATES:
        if c.id.startswith("whisper:mlx-") and not mlx_available():
            continue
        if c.id.startswith("parakeet:mlx-") and not parakeet_available():
            continue
        if c.id.startswith("parakeet:onnx-") and parakeet_available():
            continue   # the Mac has the MLX build
        if c.id.startswith("whisper:cuda-") and not nvidia:
            continue
        if c.id == "whisper:large-v3-turbo" and cuda:
            continue   # with cuBLAS in place every CTranslate2 model is the GPU row; no CPU twin
        f, reason = fit(c, ctx.hardware)
        label = runtime_label(c, cuda)
        out.append(c.model_copy(update={"name": f"{c.name} ({label})" if label else c.name,
                                        "recommended": c.id in (whisper_pick, llm_pick) and f != "no", "fit": f, "fit_reason": reason,
                                        "installed": _installed(ctx, c), "size_bytes": _download_size(c)}))
    return out


def runtime_label(c: DownloadCandidate, cuda: bool) -> str | None:
    """What runs this model here, as the suffix in its name: "Apple Silicon" (MLX), "NVIDIA GPU"
    (CTranslate2 with cuBLAS), "CPU" (CTranslate2 without it, Parakeet via ONNX). AI models carry
    no label: Ollama picks its own device."""
    if c.task != "transcription":
        return None
    if c.id.startswith(("whisper:mlx-", "parakeet:mlx-")):
        return "Apple Silicon"
    if c.id.startswith("whisper:cuda-"):
        return "NVIDIA GPU"
    if c.id.startswith("parakeet:onnx-"):
        return "CPU"
    return "NVIDIA GPU" if cuda else "CPU"


def _cuda_installed() -> bool:
    from .providers import cuda_runtime
    try:
        return cuda_runtime.installed()
    except Exception:
        return False


def has_nvidia_card(hw: dict[str, Any] | None) -> bool:
    """A PC (no unified memory) with a dedicated NVIDIA card: CTranslate2 can use CUDA there."""
    from .providers import cuda_runtime
    if not hw or not cuda_runtime.supported() or hw.get("unifiedMemory"):
        return False
    return any(g.get("vendor") == "nvidia" and not g.get("integrated") for g in hw.get("gpus") or [])


def _download_size(c: DownloadCandidate) -> int:
    """A GPU bundle also brings cuBLAS, unless that is already there."""
    if c.id.startswith("whisper:cuda-"):
        from .providers import cuda_runtime
        try:
            if not cuda_runtime.installed():
                return c.size_bytes + cuda_runtime.WHEEL_SIZE
        except Exception:
            return c.size_bytes + cuda_runtime.WHEEL_SIZE
    return c.size_bytes


def _installed(ctx: ResolverContext, c: DownloadCandidate) -> bool:
    if c.task == "llm":
        return any(m.source == "ollama" and m.name == c.url for m in ctx.registry.models("llm"))
    if c.id.startswith("parakeet:onnx-"):
        return any(m.family == "parakeet" and m.format == "ONNX" and m.source == "our_app" for m in ctx.registry.models("transcription"))
    present = any(m.compatible and m.source == "our_app" and m.id == f"our_app:{c.url}" for m in ctx.registry.models("transcription"))
    if c.id.startswith("whisper:cuda-"):
        return present and _cuda_installed()
    return present


def resolve_transcription(ctx: ResolverContext) -> Resolution:
    """The user's pick when there is one, otherwise the automatic choice; `auto_model` always
    says what Automatic would take so the UI can show it next to a manual selection."""
    auto = _auto_transcription(ctx)
    chosen_id = ctx.settings.get("models.whisper")
    if not chosen_id:
        return auto
    m = ctx.registry.model(chosen_id)
    if m and m.compatible:
        return Resolution(task="transcription", status="ready", model=m, provider=_transcription_provider_id(m),
                          reason="Selected in Settings", auto_model=auto.model)
    return Resolution(task="transcription", status="unavailable", provider="faster_whisper", auto_model=auto.model,
                      reason="The selected Whisper model is no longer available. Choose another under Settings → Models.")


def _transcription_provider_id(m: LocalModel) -> str:
    if m.family == "parakeet":
        return "parakeet_onnx" if m.format == "ONNX" else "parakeet_mlx"
    return "mlx_whisper" if m.format == "MLX" else "faster_whisper"


def _is_detector_only(m: LocalModel) -> bool:
    """The language detector pulls `faster-whisper-tiny` into the Hugging Face cache during the
    first meeting (providers/transcription.py DETECT_MODEL). Automatic must not then switch the
    transcription to that 75 MB model; it stays selectable by hand."""
    return m.source == "huggingface" and (m.meta or {}).get("whisperSize") == "tiny"


def _auto_transcription(ctx: ResolverContext) -> Resolution:
    # Automatic stays with Whisper: Parakeet is an opt-in choice (25 languages, no vocabulary prompt).
    models = [m for m in ctx.registry.models("transcription") if m.compatible and m.family != "parakeet"
              and not _is_detector_only(m)]
    order = {"large-v3-turbo": 0, "turbo": 0, "large-v3": 1, "distil-large-v3": 2, "medium": 3, "large-v2": 4,
             "small": 5, "large": 6, "base": 7, "tiny": 8}
    src = {"our_app": 0, "huggingface": 1}

    def key(m: LocalModel):
        # Same model size: MLX (GPU) beats CTranslate2 (CPU); managed beats cache.
        return (order.get((m.meta or {}).get("whisperSize") or "", 9), 0 if m.format == "MLX" else 1, src.get(m.source, 3))

    if models:
        best = sorted(models, key=key)[0]
        where = {"our_app": "Installed", "huggingface": "Found in Hugging Face cache"}.get(best.source, "Found locally")
        return Resolution(task="transcription", status="ready", model=best, provider=_transcription_provider_id(best), reason=where, auto_model=best)
    return Resolution(task="transcription", status="download_required", provider="faster_whisper",
                      download=_whisper_download(ctx), reason="No Whisper model installed yet")


def resolve_diarization(ctx: ResolverContext) -> Resolution:
    if not ctx.settings.get("speakers.diarization", True):
        return Resolution(task="diarization", status="builtin", provider="sherpa-onnx", reason="Speaker detection disabled")
    from .providers.speaker_models import bundled_dir, sherpa_available
    if not sherpa_available():
        return Resolution(task="diarization", status="unavailable", provider="sherpa-onnx", reason="sherpa-onnx is missing from this build")
    reason = "Speaker models included in the app" if bundled_dir() else "Speaker models (103 MB) are fetched the first time they are needed"
    return Resolution(task="diarization", status="builtin", provider="sherpa-onnx", reason=reason)


def resolve_llm(ctx: ResolverContext) -> Resolution:
    auto = _auto_llm(ctx)
    chosen_id = ctx.settings.get("models.ai")
    if not chosen_id:
        return auto
    ollama = next((p for p in ctx.registry.providers() if p.id == "ollama"), None)
    ollama_state = ollama.status if ollama else "not_found"
    m = ctx.registry.model(chosen_id)
    if m and m.source == "ollama" and m.compatible:
        if ollama_state != "available":
            return Resolution(task="llm", status="unavailable", model=m, provider="ollama", auto_model=auto.model,
                              reason=f"{m.name} is installed, but the AI runtime is not running.")
        return Resolution(task="llm", status="ready", model=m, provider="ollama", reason="Selected in Settings", auto_model=auto.model)
    return Resolution(task="llm", status="unavailable", provider="ollama", auto_model=auto.model,
                      reason="The selected AI model is no longer available. Choose another under Settings → Models.")


def _auto_llm(ctx: ResolverContext) -> Resolution:
    ollama_models = [m for m in ctx.registry.models("llm") if m.source == "ollama" and m.compatible]
    ollama = next((p for p in ctx.registry.providers() if p.id == "ollama"), None)
    ollama_state = ollama.status if ollama else "not_found"

    ranked = [m for m in sorted(ollama_models, key=lambda m: llm_score(m, ctx.memory_bytes), reverse=True)
              if llm_score(m, ctx.memory_bytes)[0] >= 0 and is_general_chat_model(m)]
    if ranked:
        best = ranked[0]
        reason = "Found in Ollama" + ("" if ollama_state == "available" else " (Ollama is not running)")
        return Resolution(task="llm", status="ready" if ollama_state == "available" else "unavailable", model=best,
                          provider="ollama", reason=reason, auto_model=best)
    cand = _llm_download(ctx)
    f, why = fit(cand, ctx.hardware)
    if f == "no":
        return Resolution(task="llm", status="unsupported", provider="ollama",
                          reason=why or "This computer cannot run a local AI model.")
    if ollama_state in ("not_found", "installed_not_running"):
        return Resolution(task="llm", status="download_required", provider="ollama", download=cand,
                          reason="Huddle installs a small local AI runtime together with this model.")
    return Resolution(task="llm", status="download_required", provider="ollama", download=cand,
                      reason="No suitable AI model yet")


def resolve_all(ctx: ResolverContext) -> list[Resolution]:
    return [resolve_transcription(ctx), resolve_diarization(ctx), resolve_llm(ctx)]


def additional_bytes(resolutions: list[Resolution]) -> int:
    return sum(r.download.size_bytes for r in resolutions if r.status == "download_required" and r.download)
