"""Model downloads with progress.

* Whisper (CT2): whole repo via ``huggingface_hub.snapshot_download`` into
  ``models/whisper`` (faster-whisper's ``download_root`` layout), sha256 verified by the
  hub client.
* AI models: pulled through Ollama's documented ``/api/pull`` streaming API. Models Huddle
  pulled are remembered (``ollama.pulledByHuddle``) so only those can be removed from the
  Model Manager; anything else in Ollama stays read-only.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import httpx

from .db import Database
from .discovery.ollama import OLLAMA_URL
from .discovery.registry import Registry
from .schemas import DownloadCandidate, DownloadProgress

log = logging.getLogger(__name__)
PULLED_KEY = "ollama.pulledByHuddle"


class DownloadManager:
    def __init__(self, models_dir: Path, registry: Registry, db: Database):
        self.models_dir = models_dir
        self.registry = registry
        self.db = db
        self._jobs: dict[str, DownloadProgress] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    def list(self) -> list[DownloadProgress]:
        with self._lock:
            return list(self._jobs.values())

    def start(self, cand: DownloadCandidate) -> DownloadProgress:
        with self._lock:
            if cand.id in self._jobs and self._jobs[cand.id].state in ("downloading", "verifying"):
                return self._jobs[cand.id]
            prog = DownloadProgress(id=cand.id, candidate=cand, state="downloading", received_bytes=0,
                                    total_bytes=cand.size_bytes)
            self._jobs[cand.id] = prog
            self._cancel.discard(cand.id)
        threading.Thread(target=self._run, args=(cand,), name=f"huddle-download-{cand.id}", daemon=True).start()
        return prog

    def cancel(self, cand_id: str) -> None:
        with self._lock:
            self._cancel.add(cand_id)

    def _update(self, cid: str, **kw) -> None:
        with self._lock:
            p = self._jobs.get(cid)
            if p:
                for k, v in kw.items():
                    setattr(p, k, v)

    def pulled_by_huddle(self) -> list[str]:
        return list(self.db.get_setting(PULLED_KEY, []) or [])

    def _remember_pull(self, name: str) -> None:
        pulled = set(self.pulled_by_huddle())
        pulled.add(name)
        self.db.set_setting(PULLED_KEY, sorted(pulled))

    def delete_ollama_model(self, name: str) -> None:
        """Remove a model Huddle pulled. Refuses anything Huddle did not pull."""
        if name not in self.pulled_by_huddle():
            raise PermissionError(f"{name} was not installed by Huddle; remove it with Ollama itself.")
        r = httpx.request("DELETE", f"{OLLAMA_URL}/api/delete", json={"model": name}, timeout=30)
        if r.status_code not in (200, 404):
            r.raise_for_status()
        pulled = [p for p in self.pulled_by_huddle() if p != name]
        self.db.set_setting(PULLED_KEY, pulled)
        self.db.execute("DELETE FROM models WHERE id = ?", (f"ollama:{name}",))

    # ---- workers ------------------------------------------------------------ #
    def _run(self, cand: DownloadCandidate) -> None:
        try:
            if cand.task == "transcription":
                model_id = self._download_whisper(cand)
            else:
                model_id = self._pull_ollama(cand)
            self.registry.quick_check()
            self._update(cand.id, state="done", model_id=model_id)
        except _Cancelled:
            self._update(cand.id, state="cancelled")
        except Exception as e:
            log.exception("download failed: %s", cand.id)
            self._update(cand.id, state="failed", error=_friendly(e))

    def _download_whisper(self, cand: DownloadCandidate) -> str:
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm

        mgr, cid = self, cand.id

        class _Progress(tqdm):
            def update(self, n=1):
                super().update(n)
                if getattr(self, "unit", "") == "B":
                    mgr._update(cid, received_bytes=int(self.n), total_bytes=int(self.total or cand.size_bytes))
                if cid in mgr._cancel:
                    raise _Cancelled()

        root = self.models_dir / "whisper"
        root.mkdir(parents=True, exist_ok=True)
        path = snapshot_download(cand.url, cache_dir=str(root), tqdm_class=_Progress,
                                 allow_patterns=["*.bin", "*.json", "*.txt", "*.npz", "*.safetensors"])
        size = sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())
        self._update(cid, received_bytes=size, total_bytes=size)
        return f"our_app:{cand.url}"

    def _pull_ollama(self, cand: DownloadCandidate) -> str:
        name = cand.url
        try:
            with httpx.stream("POST", f"{OLLAMA_URL}/api/pull", json={"model": name, "stream": True},
                              timeout=httpx.Timeout(30, read=None)) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    if cand.id in self._cancel:
                        raise _Cancelled()
                    ev = json.loads(line)
                    if "error" in ev:
                        raise RuntimeError(ev["error"])
                    if ev.get("total"):
                        self._update(cand.id, total_bytes=int(ev["total"]), received_bytes=int(ev.get("completed") or 0))
                    if ev.get("status") == "success":
                        break
        except httpx.ConnectError as e:
            raise RuntimeError("Ollama is not running. Start Ollama and try again.") from e
        self._update(cand.id, state="verifying")
        self._remember_pull(name)
        return f"ollama:{name}"


def _friendly(e: Exception) -> str:
    s = str(e)
    if "Repository Not Found" in s or "404" in s:
        return "The model could not be found at its source."
    if "ConnectError" in type(e).__name__ or "connect" in s.lower():
        return "No connection. Check your internet connection (or that Ollama is running) and try again."
    return s[:300]


class _Cancelled(Exception):
    pass
