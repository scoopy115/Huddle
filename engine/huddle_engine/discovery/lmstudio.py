"""LM Studio discovery: documented OpenAI-compatible server (/v1/models) when
running; documented models directory (~/.lmstudio/models) otherwise. Read-only."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from ..schemas import LocalModel, ProviderStatus
from .common import RUNTIME_LLAMACPP, RUNTIME_LMSTUDIO, gguf_quant_from_name, is_embedding_model, llm_family_from_name

LMSTUDIO_URL = "http://127.0.0.1:1234"


def _models_dir() -> Path | None:
    for p in (Path.home() / ".lmstudio" / "models", Path.home() / ".cache" / "lm-studio" / "models"):
        if p.exists():
            return p
    return None


def installed() -> bool:
    return (_models_dir() is not None or Path("/Applications/LM Studio.app").exists()
            or (Path.home() / ".lmstudio").exists())


def discover() -> tuple[ProviderStatus, list[LocalModel]]:
    now = time.time()
    served: list[str] = []
    running = False
    try:
        r = httpx.get(f"{LMSTUDIO_URL}/v1/models", timeout=1.5)
        r.raise_for_status()
        served = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        running = True
    except Exception:
        pass

    models: list[LocalModel] = []
    mdir = _models_dir()
    seen: set[str] = set()
    if mdir:
        for gguf in mdir.rglob("*.gguf"):
            rel = gguf.relative_to(mdir)
            name = str(rel.with_suffix(""))
            seen.add(name)
            try:
                size = gguf.stat().st_size
            except OSError:
                size = None
            task = "embedding" if is_embedding_model(name) else "llm"
            models.append(LocalModel(
                id=f"lm_studio:{name}", name=name, family=llm_family_from_name(name), task=task,
                source="lm_studio", format="GGUF", quantization=gguf_quant_from_name(gguf.name),
                path=str(gguf), size_bytes=size, externally_managed=True,
                # A GGUF on disk is also loadable by our built-in llama.cpp runtime (read-only).
                compatible_runtimes=[RUNTIME_LMSTUDIO, RUNTIME_LLAMACPP] if running else [RUNTIME_LLAMACPP],
                meta={"servedId": name if name in served else None, "running": running}))
    for sid in served:
        if sid not in seen:
            models.append(LocalModel(id=f"lm_studio:{sid}", name=sid, family=llm_family_from_name(sid),
                                     task="embedding" if is_embedding_model(sid) else "llm",
                                     source="lm_studio", format="GGUF", externally_managed=True,
                                     compatible_runtimes=[RUNTIME_LMSTUDIO], meta={"servedId": sid, "running": True}))

    if running:
        status = ProviderStatus(id="lmstudio", kind="llm", name="LM Studio", status="available",
                                detail={"url": LMSTUDIO_URL, "modelCount": len(models)}, checked_at=now)
    elif installed():
        status = ProviderStatus(id="lmstudio", kind="llm", name="LM Studio", status="installed_not_running",
                                detail={"modelCount": len(models)}, checked_at=now)
    else:
        status = ProviderStatus(id="lmstudio", kind="llm", name="LM Studio", status="not_found", checked_at=now)
    return status, models
