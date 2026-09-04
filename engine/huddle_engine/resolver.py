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
    DownloadCandidate(id="whisper:mlx-large-v3-turbo", name="Whisper large-v3-turbo (Apple Silicon)", task="transcription",
                      purpose="Transcription on the GPU — about 10× faster than the CPU version", size_bytes=1_620_000_000,
                      source="huggingface", url="mlx-community/whisper-large-v3-turbo", license="MIT",
                      recommended=True, description="99 languages. Recommended on every Apple Silicon Mac."),
    DownloadCandidate(id="whisper:large-v3-turbo", name="Whisper large-v3-turbo (CPU)", task="transcription",
                      purpose="Transcription on the CPU — works on any Mac and on Windows", size_bytes=1_620_000_000,
                      source="huggingface", url="mobiuslabsgmbh/faster-whisper-large-v3-turbo", license="MIT",
                      description="Same model, slower runtime."),
    DownloadCandidate(id="whisper:large-v3", name="Whisper large-v3", task="transcription",
                      purpose="Transcription — highest accuracy, about 2× slower", size_bytes=3_090_000_000,
                      source="huggingface", url="Systran/faster-whisper-large-v3", license="MIT"),
    DownloadCandidate(id="whisper:medium", name="Whisper medium", task="transcription",
                      purpose="Transcription — faster, less accurate in languages other than English", size_bytes=1_530_000_000,
                      source="huggingface", url="Systran/faster-whisper-medium", license="MIT"),
    DownloadCandidate(id="whisper:small", name="Whisper small", task="transcription",
                      purpose="Transcription — fastest, for older or low-memory Macs", size_bytes=484_000_000,
                      source="huggingface", url="Systran/faster-whisper-small", license="MIT"),
]

# Ollama library models (pulled through Ollama; sizes are Q4_K_M downloads). The 4B is the
# recommendation for everyone (product decision): quick, good notes; the 9B is offered for long
# meetings on 16 GB+ Macs and greyed out below that.
LLM_CANDIDATES: list[DownloadCandidate] = [
    DownloadCandidate(id="ollama:qwen3.5:4b", name="Qwen3.5 4B", task="llm",
                      purpose="Meeting summaries — fits Macs with 8 GB or more", size_bytes=3_400_000_000,
                      source="ollama", url="qwen3.5:4b", license="Apache-2.0", recommended=True,
                      description="Good, quick summaries. Recommended for daily use and 99% of people."),
    DownloadCandidate(id="ollama:qwen3.5:9b", name="Qwen3.5 9B", task="llm",
                      purpose="Meeting summaries — fits Macs with 16 GB or more", size_bytes=6_600_000_000,
                      source="ollama", url="qwen3.5:9b", license="Apache-2.0", min_memory_bytes=16 * GB,
                      description="Most thorough on long meetings, but uses more processing power and takes longer to process."),
]

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


def _whisper_download(ctx: ResolverContext) -> DownloadCandidate:
    from .providers.transcription import mlx_available
    mem = ctx.memory_bytes or 0
    if mlx_available():
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:mlx-large-v3-turbo")
    if mem and mem < 12 * GB:
        return next(c for c in WHISPER_CANDIDATES if c.id == "whisper:medium")
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
    """Marketplace list with the Recommended flag set for *this* machine: the MLX Whisper only
    where MLX runs, the CPU build elsewhere. AI models keep their fixed recommendation (4B);
    `min_memory_bytes` lets the UI grey out what this Mac cannot run well."""
    whisper_pick = _whisper_download(ctx).id
    llm_pick = _llm_download(ctx).id
    out: list[DownloadCandidate] = []
    for c in WHISPER_CANDIDATES + LLM_CANDIDATES:
        out.append(c.model_copy(update={"recommended": c.id in (whisper_pick, llm_pick)}))
    return out


def resolve_transcription(ctx: ResolverContext) -> Resolution:
    """The user's pick when there is one, otherwise the automatic choice; `auto_model` always
    says what Automatic would take so the UI can show it next to a manual selection."""
    auto = _auto_transcription(ctx)
    chosen_id = ctx.settings.get("models.whisper")
    if not chosen_id:
        return auto
    m = ctx.registry.model(chosen_id)
    if m and m.compatible:
        return Resolution(task="transcription", status="ready", model=m, provider="faster_whisper",
                          reason="Selected in Settings", auto_model=auto.model)
    return Resolution(task="transcription", status="unavailable", provider="faster_whisper", auto_model=auto.model,
                      reason="The selected Whisper model is no longer available. Choose another under Settings → Models.")


def _auto_transcription(ctx: ResolverContext) -> Resolution:
    models = [m for m in ctx.registry.models("transcription") if m.compatible]
    order = {"large-v3-turbo": 0, "turbo": 0, "large-v3": 1, "distil-large-v3": 2, "medium": 3, "large-v2": 4,
             "small": 5, "large": 6, "base": 7, "tiny": 8}
    src = {"our_app": 0, "huggingface": 1}

    def key(m: LocalModel):
        # Same model size: MLX (GPU) beats CTranslate2 (CPU); managed beats cache.
        return (order.get((m.meta or {}).get("whisperSize") or "", 9), 0 if m.format == "MLX" else 1, src.get(m.source, 3))

    if models:
        best = sorted(models, key=key)[0]
        where = {"our_app": "Installed", "huggingface": "Found in Hugging Face cache"}.get(best.source, "Found locally")
        return Resolution(task="transcription", status="ready", model=best, provider="faster_whisper", reason=where, auto_model=best)
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
    if ollama_state in ("not_found", "installed_not_running"):
        return Resolution(task="llm", status="download_required", provider="ollama", download=_llm_download(ctx),
                          reason="Huddle installs a small local AI runtime together with this model — nothing to set up.")
    return Resolution(task="llm", status="download_required", provider="ollama", download=_llm_download(ctx),
                      reason="No suitable AI model yet")


def resolve_all(ctx: ResolverContext) -> list[Resolution]:
    return [resolve_transcription(ctx), resolve_diarization(ctx), resolve_llm(ctx)]


def additional_bytes(resolutions: list[Resolution]) -> int:
    return sum(r.download.size_bytes for r in resolutions if r.status == "download_required" and r.download)
