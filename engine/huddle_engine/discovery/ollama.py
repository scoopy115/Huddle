"""Ollama discovery. Running → documented REST API (/api/tags, /api/show).
Installed but not running → read-only parse of the manifest store
(~/.ollama/models/manifests). Ollama's storage is never modified."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import httpx

from ..schemas import LocalModel, ProviderStatus
from .common import RUNTIME_OLLAMA, is_embedding_model, llm_family_from_name

OLLAMA_URL = "http://127.0.0.1:11434"


def _models_dir() -> Path:
    env = os.getenv("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def installed() -> bool:
    return (_models_dir().exists() or shutil.which("ollama") is not None
            or Path("/Applications/Ollama.app").exists())


def _api_models() -> list[dict] | None:
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        r.raise_for_status()
        return r.json().get("models", [])
    except Exception:
        return None


def _manifest_models() -> list[dict]:
    """Parse ~/.ollama/models/manifests/<host>/<ns>/<name>/<tag>. Size = sum of layer sizes."""
    root = _models_dir() / "manifests"
    out: list[dict] = []
    if not root.exists():
        return out
    for host in root.iterdir():
        if not host.is_dir():
            continue
        for ns in host.iterdir():
            if not ns.is_dir():
                continue
            for name in ns.iterdir():
                if not name.is_dir():
                    continue
                for tag in name.iterdir():
                    if not tag.is_file():
                        continue
                    try:
                        data = json.loads(tag.read_text())
                    except Exception:
                        continue
                    size = sum(int(layer.get("size", 0)) for layer in data.get("layers", []))
                    full = f"{name.name}:{tag.name}" if ns.name == "library" else f"{ns.name}/{name.name}:{tag.name}"
                    out.append({"name": full, "size": size, "details": {}})
    return out


def discover() -> tuple[ProviderStatus, list[LocalModel]]:
    now = time.time()
    api = _api_models()
    if api is not None:
        status = ProviderStatus(id="ollama", kind="llm", name="Ollama", status="available",
                                detail={"url": OLLAMA_URL, "modelCount": len(api)}, checked_at=now)
        raw, running = api, True
    elif installed():
        raw, running = _manifest_models(), False
        status = ProviderStatus(id="ollama", kind="llm", name="Ollama", status="installed_not_running",
                                detail={"modelCount": len(raw), "modelsDir": str(_models_dir())}, checked_at=now)
    else:
        return ProviderStatus(id="ollama", kind="llm", name="Ollama", status="not_found", checked_at=now), []

    models: list[LocalModel] = []
    for m in raw:
        name = m.get("name") or m.get("model") or ""
        if not name:
            continue
        details = m.get("details") or {}
        task = "embedding" if is_embedding_model(name) else "llm"
        models.append(LocalModel(
            id=f"ollama:{name}", name=name, family=(details.get("family") or llm_family_from_name(name)),
            task=task, source="ollama", format="GGUF", quantization=details.get("quantization_level"),
            path=None, size_bytes=int(m.get("size") or 0) or None, externally_managed=True,
            compatible_runtimes=[RUNTIME_OLLAMA],
            meta={"parameterSize": details.get("parameter_size"), "running": running,
                  "modifiedAt": m.get("modified_at")}))
    return status, models
