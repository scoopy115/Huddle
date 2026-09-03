"""Provider/model registry: runs the discovery adapters, decides compatibility for
*our* runtimes, caches everything in the ``models`` / ``providers`` tables.

* ``quick_check()`` — startup: HTTP pings + managed dir only (fast).
* ``full_scan()``  — background/lazy: adds Hugging Face cache, LM Studio dir, whisper.cpp.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from ..db import Database
from ..schemas import LocalModel, ProviderStatus
from . import hf_cache, lmstudio, managed, ollama, whisper_cpp
from .common import RUNTIME_FASTER_WHISPER, RUNTIME_MLX, RUNTIME_OLLAMA, RUNTIME_SHERPA

log = logging.getLogger(__name__)

# Which runtimes we can actually drive today, per task.
# AI summaries are Ollama-only (see resolver.py); other GGUF locations are listed as
# "different runtime" so the user knows they exist but are not used.
def _transcription_runtimes() -> set[str]:
    from ..providers.transcription import mlx_available
    return {RUNTIME_FASTER_WHISPER} | ({RUNTIME_MLX} if mlx_available() else set())


SUPPORTED_RUNTIMES: dict[str, set[str]] = {
    "transcription": _transcription_runtimes(),
    "diarization": {RUNTIME_SHERPA},
    "llm": {RUNTIME_OLLAMA},
    "embedding": set(),
}

RUNTIME_LABELS = {
    "MLX": "MLX format — needs Apple Silicon",
    "safetensors": "Transformers format — a CTranslate2/GGUF version is required",
    "PyTorch": "PyTorch format — a CTranslate2/GGUF version is required",
    "whisper.cpp": "whisper.cpp format — a CTranslate2 version is required",
    "CoreML": "CoreML/WhisperKit format — a CTranslate2 version is required",
}


def annotate_compatibility(m: LocalModel) -> LocalModel:
    supported = SUPPORTED_RUNTIMES.get(m.task, set())
    usable = supported & set(m.compatible_runtimes)
    m.compatible = bool(usable)
    if m.compatible:
        if m.task == "llm" and m.format == "GGUF" and m.size_bytes and m.size_bytes < 1_500_000_000:
            m.compatibility_note = "Very small model — summaries may be low quality"
        elif m.task == "llm":
            params = (m.meta or {}).get("parameterSize") or ""
            if params and params.upper().endswith("B"):
                try:
                    if float(params[:-1]) < 3:
                        m.compatibility_note = "Small model — summaries may be low quality"
                except ValueError:
                    pass
        else:
            m.compatibility_note = None
    else:
        if m.task == "llm" and m.format == "GGUF" and m.source != "ollama":
            m.compatibility_note = "Not in Ollama — Huddle runs AI models through Ollama"
        else:
            m.compatibility_note = RUNTIME_LABELS.get(m.format, "Different runtime format")
    return m


class Registry:
    def __init__(self, db: Database, models_dir: Path):
        self.db = db
        self.models_dir = models_dir
        self._lock = threading.Lock()
        self.scanning = False
        self.last_scan_at: float | None = self.db.get_setting("registry.lastScanAt")

    # ---- persistence -------------------------------------------------------- #
    def _save(self, statuses: list[ProviderStatus], models: list[LocalModel], sources: set[str]) -> None:
        with self.db.tx() as c:
            for s in statuses:
                c.execute("INSERT INTO providers(id,kind,name,status,detail_json,checked_at) VALUES (?,?,?,?,?,?)"
                          " ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,status=excluded.status,"
                          " detail_json=excluded.detail_json,checked_at=excluded.checked_at",
                          (s.id, s.kind, s.name, s.status, json.dumps(s.detail), s.checked_at))
            for src in sources:
                c.execute("DELETE FROM models WHERE source = ?", (src,))
            for m in models:
                c.execute("INSERT OR REPLACE INTO models(id,name,family,task,source,format,quantization,path,size_bytes,"
                          "externally_managed,compatible_runtimes,meta_json,sha256,discovered_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (m.id, m.name, m.family, m.task, m.source, m.format, m.quantization, m.path, m.size_bytes,
                           int(m.externally_managed), json.dumps(m.compatible_runtimes), json.dumps(m.meta),
                           m.meta.get("sha256"), time.time()))

    def models(self, task: str | None = None) -> list[LocalModel]:
        rows = self.db.query("SELECT * FROM models" + (" WHERE task = ?" if task else "") + " ORDER BY source, name",
                             (task,) if task else ())
        out = []
        for r in rows:
            out.append(annotate_compatibility(LocalModel(
                id=r["id"], name=r["name"], family=r["family"], task=r["task"], source=r["source"], format=r["format"],
                quantization=r["quantization"], path=r["path"], size_bytes=r["size_bytes"],
                externally_managed=bool(r["externally_managed"]),
                compatible_runtimes=json.loads(r["compatible_runtimes"]), meta=json.loads(r["meta_json"]))))
        return out

    def model(self, model_id: str) -> LocalModel | None:
        for m in self.models():
            if m.id == model_id:
                return m
        return None

    def providers(self) -> list[ProviderStatus]:
        rows = self.db.query("SELECT * FROM providers ORDER BY kind, name")
        return [ProviderStatus(id=r["id"], kind=r["kind"], name=r["name"], status=r["status"],
                               detail=json.loads(r["detail_json"]), checked_at=r["checked_at"]) for r in rows]

    # ---- scanning ----------------------------------------------------------- #
    def quick_check(self) -> None:
        """Fast: managed dir + Ollama/LM Studio HTTP pings (1.5 s timeouts)."""
        statuses, models = [], []
        st, ms = managed.discover(self.models_dir)
        statuses += st
        models += ms
        for adapter in (ollama, lmstudio):
            try:
                s, m = adapter.discover()
                statuses.append(s)
                models += m
            except Exception as e:  # fail-soft
                log.warning("discovery %s failed: %s", adapter.__name__, e)
        self._save(statuses, models, {"our_app", "ollama", "lm_studio"})

    def full_scan(self) -> None:
        with self._lock:
            self.scanning = True
            try:
                self.quick_check()
                statuses, models, sources = [], [], set()
                try:
                    s, m = hf_cache.discover()
                    statuses.append(s)
                    models += m
                    sources.add("huggingface")
                except Exception as e:
                    log.warning("hf cache discovery failed: %s", e)
                try:
                    st, m = whisper_cpp.discover()
                    statuses += st
                    models += m
                    sources |= {"whisper_cpp", "whisperkit"}
                except Exception as e:
                    log.warning("whisper.cpp discovery failed: %s", e)
                self._save(statuses, models, sources)
                self.last_scan_at = time.time()
                self.db.set_setting("registry.lastScanAt", self.last_scan_at)
            finally:
                self.scanning = False

    def scan_async(self) -> None:
        if self.scanning:
            return
        threading.Thread(target=self.full_scan, name="huddle-model-scan", daemon=True).start()

    # ---- ownership ---------------------------------------------------------- #
    def delete_managed(self, model_id: str) -> None:
        """Delete a model *only* if Huddle owns it. External models raise."""
        m = self.model(model_id)
        if not m:
            raise KeyError(model_id)
        if m.externally_managed or m.source != "our_app" or not m.path:
            raise PermissionError(f"{m.name} is managed by {m.source}; removal is unavailable here.")
        p = Path(m.path).resolve()
        root = self.models_dir.resolve()
        if root not in p.parents:
            raise PermissionError("Refusing to delete a path outside the managed models directory.")
        import shutil
        # CT2 whisper: delete the whole models--… repo dir; GGUF: the file.
        target = p
        for parent in [p, *p.parents]:
            if parent.name.startswith("models--") and root in parent.parents:
                target = parent
                break
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        self.db.execute("DELETE FROM models WHERE id = ?", (model_id,))
