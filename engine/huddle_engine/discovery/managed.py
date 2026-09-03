"""Models managed by Huddle itself (``<data>/models``). These are the ONLY models the
Model Manager may delete.

* ``models/whisper`` is faster-whisper's ``download_root`` → HF-cache layout
  (``models--Systran--faster-whisper-<size>``), classified with the same rules.
* ``models/llm/*.gguf`` are GGUF downloads for the built-in llama.cpp runtime.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..schemas import LocalModel, ProviderStatus
from .common import RUNTIME_LLAMACPP, gguf_quant_from_name, llm_family_from_name
from .hf_cache import _snapshot, classify_repo


def _scan_models_dir(models_dir: Path, source: str, managed: bool) -> list[LocalModel]:
    out: list[LocalModel] = []
    wdir = models_dir / "whisper"
    if wdir.exists():
        for repo_dir in wdir.glob("models--*"):
            repo_id = repo_dir.name[len("models--"):].replace("--", "/")
            snap = _snapshot(repo_dir)
            if snap:
                for m in classify_repo(repo_id, snap, source=source):
                    m.externally_managed = not managed
                    out.append(m)
    ldir = models_dir / "llm"
    if ldir.exists():
        for g in ldir.rglob("*.gguf"):
            try:
                size = g.stat().st_size
            except OSError:
                size = None
            out.append(LocalModel(id=f"{source}:llm/{g.name}", name=g.stem, family=llm_family_from_name(g.name),
                                  task="llm", source=source, format="GGUF", quantization=gguf_quant_from_name(g.name),
                                  path=str(g), size_bytes=size, externally_managed=not managed,
                                  compatible_runtimes=[RUNTIME_LLAMACPP], meta={}))
    return out


def discover(models_dir: Path) -> tuple[list[ProviderStatus], list[LocalModel]]:
    now = time.time()
    models = _scan_models_dir(models_dir, source="our_app", managed=True)
    statuses = [ProviderStatus(id="our_app", kind="model_source", name="Huddle managed models", status="available",
                               detail={"path": str(models_dir), "modelCount": len(models)}, checked_at=now)]
    return statuses, models
