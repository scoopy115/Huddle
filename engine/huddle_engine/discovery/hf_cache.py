"""Hugging Face hub cache discovery (``$HF_HOME/hub`` or ``~/.cache/huggingface/hub``).

Classifies each ``models--org--name`` snapshot by *contents*, because identity
(e.g. "Whisper large-v3") says nothing about whether our runtime can load it:

* CTranslate2 Whisper (model.bin + vocabulary/tokenizer)   → usable by faster-whisper
* Transformers Whisper (safetensors/bin + generation_config) → NOT usable directly
* MLX Whisper (weights.npz / safetensors under mlx-community)  → NOT usable (no MLX runtime yet)
* pyannote                                                    → usable by pyannote backend (optional)
* GGUF files                                                  → usable by llama.cpp
* sentence-transformers style embedding models                → future embedding provider
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..schemas import LocalModel, ProviderStatus
from .common import (
    RUNTIME_FASTER_WHISPER,
    RUNTIME_LLAMACPP,
    RUNTIME_MLX,
    dir_size,
    gguf_quant_from_name,
    is_embedding_model,
    llm_family_from_name,
    whisper_size_from_name,
)


def hub_dir() -> Path:
    if os.getenv("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    return home / "hub"


def _snapshot(repo_dir: Path) -> Path | None:
    snaps = repo_dir / "snapshots"
    if not snaps.exists():
        return None
    candidates = [p for p in snaps.iterdir() if p.is_dir()]
    if not candidates:
        return None
    # newest snapshot wins
    return max(candidates, key=lambda p: p.stat().st_mtime)


def classify_repo(repo_id: str, snap: Path, source: str = "huggingface") -> list[LocalModel]:
    files = {p.name.lower() for p in snap.iterdir()} if snap.exists() else set()
    lower = repo_id.lower()
    out: list[LocalModel] = []
    size = dir_size(snap)
    base_id = f"{source}:{repo_id}"

    is_whisper = "whisper" in lower
    if is_whisper and "model.bin" in files and ({"vocabulary.txt", "vocabulary.json", "tokenizer.json"} & files):
        out.append(LocalModel(id=base_id, name=repo_id, family="whisper", task="transcription", source=source,
                              format="CTranslate2", path=str(snap), size_bytes=size, externally_managed=True,
                              compatible_runtimes=[RUNTIME_FASTER_WHISPER],
                              meta={"whisperSize": whisper_size_from_name(repo_id)}))
    elif is_whisper and ("mlx" in lower or "weights.npz" in files or ("weights.safetensors" in files and "config.json" in files)):
        out.append(LocalModel(id=base_id, name=repo_id, family="whisper", task="transcription", source=source,
                              format="MLX", path=str(snap), size_bytes=size, externally_managed=True,
                              compatible_runtimes=[RUNTIME_MLX], meta={"whisperSize": whisper_size_from_name(repo_id)}))
    elif is_whisper and ({"model.safetensors", "pytorch_model.bin", "generation_config.json"} & files):
        out.append(LocalModel(id=base_id, name=repo_id, family="whisper", task="transcription", source=source,
                              format="safetensors" if "model.safetensors" in files else "PyTorch",
                              path=str(snap), size_bytes=size, externally_managed=True, compatible_runtimes=[],
                              meta={"whisperSize": whisper_size_from_name(repo_id)}))
    elif "pyannote" in lower:
        out.append(LocalModel(id=base_id, name=repo_id, family="pyannote", task="diarization", source=source,
                              format="PyTorch", path=str(snap), size_bytes=size, externally_managed=True,
                              compatible_runtimes=["pyannote"], meta={}))
    else:
        ggufs = [p for p in snap.rglob("*.gguf")]
        if ggufs:
            for g in ggufs:
                try:
                    gsize = g.resolve().stat().st_size
                except OSError:
                    gsize = None
                out.append(LocalModel(id=f"{base_id}/{g.name}", name=f"{repo_id} · {g.name}",
                                      family=llm_family_from_name(repo_id), task="llm", source=source,
                                      format="GGUF", quantization=gguf_quant_from_name(g.name), path=str(g),
                                      size_bytes=gsize, externally_managed=True,
                                      compatible_runtimes=[RUNTIME_LLAMACPP], meta={}))
        elif is_embedding_model(lower) or "sentence-transformers" in lower:
            out.append(LocalModel(id=base_id, name=repo_id, family=llm_family_from_name(repo_id), task="embedding",
                                  source=source, format="safetensors" if "model.safetensors" in files else "PyTorch",
                                  path=str(snap), size_bytes=size, externally_managed=True,
                                  compatible_runtimes=["sentence-transformers"], meta={}))
        elif {"config.json"} & files and ({"model.safetensors", "pytorch_model.bin"} & files
                                          or any(f.startswith("model-") and f.endswith(".safetensors") for f in files)):
            out.append(LocalModel(id=base_id, name=repo_id, family=llm_family_from_name(repo_id), task="llm",
                                  source=source, format="safetensors", path=str(snap), size_bytes=size,
                                  externally_managed=True, compatible_runtimes=[], meta={"transformers": True}))
    return out


def discover(root: Path | None = None) -> tuple[ProviderStatus, list[LocalModel]]:
    now = time.time()
    root = root or hub_dir()
    if not root.exists():
        return ProviderStatus(id="huggingface", kind="model_source", name="Hugging Face cache",
                              status="not_found", detail={"path": str(root)}, checked_at=now), []
    models: list[LocalModel] = []
    for repo_dir in sorted(root.glob("models--*")):
        repo_id = repo_dir.name[len("models--"):].replace("--", "/")
        snap = _snapshot(repo_dir)
        if not snap:
            continue
        try:
            models.extend(classify_repo(repo_id, snap))
        except OSError:
            continue
    status = ProviderStatus(id="huggingface", kind="model_source", name="Hugging Face cache", status="available",
                            detail={"path": str(root), "modelCount": len(models)}, checked_at=now)
    return status, models
