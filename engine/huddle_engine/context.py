"""Engine context: wires config, database, registry, downloads, job runner and the
optional network MCP server. Used by the HTTP app, the MCP server and the CLI."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from .db import Database
from .discovery.registry import Registry
from .downloads import DownloadManager
from .jobs.runner import JobRunner
from .live import LiveManager
from .providers.compute import hardware_info
from .services import meetings as ms
from .settings import EngineConfig, apply_path_overrides, normalize_settings

log = logging.getLogger(__name__)


class EngineContext:
    def __init__(self, cfg: EngineConfig | None = None, start_jobs: bool = True):
        self.cfg = cfg or EngineConfig()
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_path)
        self.hardware = hardware_info()
        s = self.settings()
        apply_path_overrides(self.cfg, s)
        self.registry = Registry(self.db, self.cfg.models_dir)
        self.downloads = DownloadManager(self.cfg.models_dir, self.registry, self.db)
        self.jobs = JobRunner(self.db, self.cfg, self.registry, self.settings, self.hardware.get("memoryBytes"),
                              on_finished=self.enforce_storage)
        self.mcp_network = None
        self.live = LiveManager(self.db)
        if start_jobs:
            self.jobs.start()

    # ---- settings ----------------------------------------------------------- #
    def settings(self) -> dict[str, Any]:
        return normalize_settings(self.db.all_settings())

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        for k, v in patch.items():
            self.db.set_setting(k, v)
        s = self.settings()
        if "storage.maxBytes" in patch:
            self.enforce_storage()
        return s

    # ---- storage -------------------------------------------------------------- #
    def enforce_storage(self, *_: Any) -> list[str]:
        try:
            removed = ms.enforce_storage_limit(self.db, self.cfg, int(self.settings().get("storage.maxBytes") or 0))
            if removed:
                log.info("storage quota: removed audio of %d meeting(s) (transcripts kept)", len(removed))
            return removed
        except Exception:
            log.exception("storage enforcement failed")
            return []

    def move_dir(self, kind: str, new_path: str, move_files: bool) -> Path:
        """Relocate the models or logs directory. Existing files are moved when asked;
        the setting is stored so the next start uses the new location too."""
        new = Path(new_path).expanduser()
        if kind == "models":
            old = self.cfg.models_dir
        elif kind == "logs":
            old = self.cfg.logs_dir
        else:
            raise ValueError(kind)
        new.mkdir(parents=True, exist_ok=True)
        if new.resolve() == old.resolve():
            return new
        if move_files and old.exists():
            for child in old.iterdir():
                dest = new / child.name
                if dest.exists():
                    continue
                shutil.move(str(child), str(dest))
        self.db.set_setting(f"paths.{kind}Dir", str(new))
        if kind == "models":
            self.cfg.models_dir_override = new
            self.registry.models_dir = new
            self.downloads.models_dir = new
            self.registry.quick_check()
        else:
            self.cfg.logs_dir_override = new
        self.cfg.ensure_dirs()
        return new

    def close(self) -> None:
        if self.mcp_network:
            self.mcp_network.stop()
        self.db.close()
