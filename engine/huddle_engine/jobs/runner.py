"""Persisted, resumable processing jobs (spec §36–37).

One worker thread processes meetings sequentially (the ML stages saturate the
machine anyway). Job + per-stage state is written to ``processing_jobs`` after
every transition, so a restart knows exactly what happened. Stages are
independently retryable; a failed stage never removes existing results.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from ..db import Database
from ..discovery.registry import Registry
from ..providers import ollama_runtime
from ..providers.base import ProviderError
from ..providers.transcription import release_models
from ..schemas import DEFAULT_PIPELINE, STAGES
from ..settings import EngineConfig
from . import stages as st
from .stages import JobCancelled

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, db: Database, cfg: EngineConfig, registry: Registry, settings_fn: Callable[[], dict[str, Any]],
                 memory_bytes: int | None = None, on_finished: Callable[[str], Any] | None = None):
        self.db = db
        self.cfg = cfg
        self.registry = registry
        self.settings_fn = settings_fn
        self.memory_bytes = memory_bytes
        self.on_finished = on_finished
        self._q: queue.Queue[tuple[str, list[str]]] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="huddle-jobs", daemon=True)
        self._active: str | None = None
        self._cancelled: set[str] = set()
        self._last_progress_write = 0.0

    # ---- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        self.recover()
        self._thread.start()

    def recover(self) -> None:
        """Called at startup: jobs left 'running' were interrupted by a crash/quit. Stages are
        idempotent and only write when they finish, so the interrupted stage and everything
        after it are simply queued again instead of being reported as failed."""
        for r in self.db.query("SELECT * FROM processing_jobs WHERE state IN ('running', 'queued')"):
            stages = json.loads(r["stages_json"])
            todo = [n for n in STAGES if stages.get(n, {}).get("status") in ("running", "pending")]
            if not todo:
                todo = list(DEFAULT_PIPELINE)
            for n in todo:
                stages[n] = {"status": "pending"}
            self._write(r["meeting_id"], state="queued", current_stage=None, stages=stages,
                        error=None, error_detail=None)
            log.info("[%s] resuming interrupted processing: %s", r["meeting_id"], ", ".join(todo))
            self._q.put((r["meeting_id"], todo))

    @property
    def active_meeting(self) -> str | None:
        return self._active

    # ---- public API --------------------------------------------------------- #
    def enqueue(self, meeting_id: str, stage_names: list[str] | None = None) -> None:
        names = stage_names or list(DEFAULT_PIPELINE)
        existing = self.db.one("SELECT stages_json FROM processing_jobs WHERE meeting_id = ?", (meeting_id,))
        stages = json.loads(existing["stages_json"]) if existing else {}
        for n in STAGES:
            if n in names:
                stages[n] = {"status": "pending"}
            else:
                # on-demand stages (refining, extracting_actions) are "skipped" until asked for,
                # so recovery never starts them by itself
                stages.setdefault(n, {"status": "pending" if n in DEFAULT_PIPELINE else "skipped"})
        self._write(meeting_id, state="queued", current_stage=None, stages=stages, error=None, error_detail=None)
        self.db.execute("UPDATE meetings SET status = 'processing' WHERE id = ?", (meeting_id,))
        self._q.put((meeting_id, names))

    def cancel(self, meeting_id: str) -> None:
        """Stop processing this meeting as soon as the current stage checks in (used on delete)."""
        self._cancelled.add(meeting_id)

    def retry_stage(self, meeting_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage '{stage}'")
        self.enqueue(meeting_id, [stage] + st.DOWNSTREAM[stage])

    # ---- worker ------------------------------------------------------------- #
    def _loop(self) -> None:
        while True:
            meeting_id, names = self._q.get()
            try:
                self._run(meeting_id, names)
            except Exception:  # never let the worker die
                log.exception("job runner crashed on %s", meeting_id)
            finally:
                self._active = None
                if self._q.empty():
                    # Idle: give the memory back instead of keeping models warm indefinitely, and stop
                    # Huddle's own Ollama server (never the user's) — it restarts on demand.
                    release_models()
                    ollama_runtime.stop()

    def _run(self, meeting_id: str, names: list[str]) -> None:
        self._cancelled.discard(meeting_id)
        if not self.db.one("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)):
            return
        self._active = meeting_id
        row = self.db.one("SELECT stages_json FROM processing_jobs WHERE meeting_id = ?", (meeting_id,))
        stages = json.loads(row["stages_json"]) if row else {n: {"status": "pending" if n in DEFAULT_PIPELINE else "skipped"} for n in STAGES}

        def is_cancelled() -> bool:
            return meeting_id in self._cancelled or not self.db.one("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,))

        current = {"name": None}

        def progress(fraction: float) -> None:
            name = current["name"]
            if not name:
                return
            stages[name]["progress"] = round(max(0.0, min(1.0, fraction)), 3)
            now = time.time()
            if now - self._last_progress_write >= 0.7 or fraction >= 1.0:
                self._last_progress_write = now
                self._write(meeting_id, state="running", current_stage=name, stages=stages)

        ctx = st.StageContext(db=self.db, cfg=self.cfg, registry=self.registry, settings=self.settings_fn(),
                              meeting_id=meeting_id, memory_bytes=self.memory_bytes, progress=progress, cancelled=is_cancelled)
        self._write(meeting_id, state="running", current_stage=None, stages=stages)
        aborted = False
        for name in STAGES:
            if name not in names:
                continue
            if aborted:
                stages[name] = {"status": "skipped", "error": "Skipped because an earlier step failed."}
                self._write(meeting_id, state="running", current_stage=None, stages=stages)
                continue
            if is_cancelled():
                self._cancelled_cleanup(meeting_id, stages, None)
                return
            stages[name] = {"status": "running", "started_at": time.time(), "progress": 0.0}
            current["name"] = name
            self._write(meeting_id, state="running", current_stage=name, stages=stages)
            log.info("[%s] %s …", meeting_id, name)
            t0 = time.time()
            try:
                detail = st.STAGE_FUNCS[name](ctx)
                stages[name].update(status="done", finished_at=time.time(), detail=detail, progress=1.0)
                log.info("[%s] %s done in %.1fs — %s", meeting_id, name, time.time() - t0, detail)
            except JobCancelled:
                self._cancelled_cleanup(meeting_id, stages, name)
                return
            except ProviderError as e:
                stages[name].update(status="failed", finished_at=time.time(), error=str(e), error_detail=e.detail)
                log.warning("[%s] %s failed: %s", meeting_id, name, e)
                if name in ("preprocessing", "transcribing"):
                    aborted = True
            except Exception:  # unexpected → still a clean user message + full detail
                stages[name].update(status="failed", finished_at=time.time(),
                                    error=f"{_friendly(name)} failed unexpectedly.",
                                    error_detail=traceback.format_exc())
                log.exception("[%s] %s crashed", meeting_id, name)
                if name in ("preprocessing", "transcribing"):
                    aborted = True
            self._write(meeting_id, state="running", current_stage=None, stages=stages)

        failed = [n for n, s in stages.items() if s.get("status") == "failed"]
        state = "failed" if failed else "ready"
        error = None
        if failed:
            first = stages[failed[0]]
            error = first.get("error")
        self._write(meeting_id, state=state, current_stage=None, stages=stages, error=error,
                    error_detail=stages[failed[0]].get("error_detail") if failed else None)
        self._set_meeting_status(meeting_id, stages)
        if self.on_finished:
            try:
                self.on_finished(meeting_id)
            except Exception:
                log.exception("on_finished hook failed")

    def _cancelled_cleanup(self, meeting_id: str, stages: dict, running: str | None) -> None:
        """Cancelled by the user (or the meeting was deleted). Stages only write their tables
        after finishing, so the previous transcript/notes are intact — keep the meeting usable."""
        log.info("[%s] cancelled%s", meeting_id, f" during {running}" if running else "")
        if not self.db.one("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)):
            self.db.execute("DELETE FROM processing_jobs WHERE meeting_id = ?", (meeting_id,))
            return
        has_transcript = bool(self.db.one("SELECT 1 FROM transcript_segments WHERE meeting_id = ? LIMIT 1", (meeting_id,)))
        for name, stage_state in stages.items():
            if stage_state.get("status") in ("running", "pending"):
                stages[name] = {"status": "done" if has_transcript else "pending", "detail": "Kept previous result" if has_transcript else None}
        self._write(meeting_id, state="ready" if has_transcript else "failed", current_stage=None, stages=stages,
                    error=None if has_transcript else "Processing was cancelled.")
        self.db.execute("UPDATE meetings SET status = ? WHERE id = ?", ("ready" if has_transcript else "saved", meeting_id))


    def _set_meeting_status(self, meeting_id: str, stages: dict) -> None:
        core_ok = stages.get("transcribing", {}).get("status") == "done"
        status = "ready" if core_ok else "failed"
        self.db.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))

    def _write(self, meeting_id: str, *, state: str, current_stage: str | None, stages: dict,
               error: str | None = None, error_detail: str | None = None) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO processing_jobs(meeting_id, state, current_stage, stages_json, error, error_detail, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(meeting_id) DO UPDATE SET state=excluded.state,"
            " current_stage=excluded.current_stage, stages_json=excluded.stages_json, error=excluded.error,"
            " error_detail=excluded.error_detail, updated_at=excluded.updated_at",
            (meeting_id, state, current_stage, json.dumps(stages), error, error_detail, now, now))


def _friendly(stage: str) -> str:
    return {"preprocessing": "Audio preparation", "transcribing": "Transcription", "diarizing": "Speaker detection",
            "identifying_speakers": "Speaker recognition", "summarizing": "Summary generation",
            "indexing": "Indexing"}.get(stage, stage)
