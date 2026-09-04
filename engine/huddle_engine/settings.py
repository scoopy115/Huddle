"""Engine settings.

Two layers:

* **Process settings** (``EngineConfig``) come from the desktop app via env:
  data dir, port, auth token. They never change while the engine runs — except the
  models/logs directories, which the user can relocate (stored in user settings and
  applied at startup via ``apply_path_overrides``).
* **User settings** live in the ``settings`` table (see ``db.settings``) and are
  edited from the UI.

"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_data_dir() -> Path:
    env = os.getenv("HUDDLE_DATA_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", home / "AppData" / "Roaming"))
        return base / "com.huddle.desktop"
    if os.uname().sysname == "Darwin":
        return home / "Library" / "Application Support" / "com.huddle.desktop"
    return Path(os.getenv("XDG_DATA_HOME", home / ".local" / "share")) / "com.huddle.desktop"


@dataclass
class EngineConfig:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"
    port: int = int(os.getenv("HUDDLE_PORT", "48731"))
    token: str | None = os.getenv("HUDDLE_TOKEN") or None
    models_dir_override: Path | None = None
    logs_dir_override: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "huddle.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def models_dir(self) -> Path:
        return self.models_dir_override or (self.data_dir / "models")

    @property
    def logs_dir(self) -> Path:
        return self.logs_dir_override or (self.data_dir / "logs")

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.recordings_dir, self.models_dir,
                  self.models_dir / "whisper", self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)


GB = 1024 ** 3

# Defaults for user-editable settings. Keys are stable identifiers used by the UI.
DEFAULT_USER_SETTINGS: dict[str, Any] = {
    # general
    "general.language": "auto",              # spoken language detection: always auto (kept for API compatibility)
    "general.uiLanguage": "auto",            # language of summaries/answers; "auto" = system language, English if unsupported
    "general.computeDevice": "auto",
    "general.sounds": True,                  # subtle interface sounds
    "general.menuBar": True,                 # keep Huddle in the menu bar when the window is closed
    "general.autoUpdate": True,              # check GitHub releases on launch and every 24 hours
    "notes.autoActionItems": False,          # extract action items automatically after every meeting
    # storage
    "storage.maxBytes": 10 * GB,             # recordings quota (5–50 GB); transcripts are never deleted
    "paths.modelsDir": None,                 # None = <data>/models
    "paths.logsDir": None,                   # None = <data>/logs
    # recording
    "recording.inputDevice": None,           # device name; None = system default
    "recording.systemAudio": False,          # also capture desktop/system audio via a loopback device
    "recording.systemDevice": None,          # loopback device name; None = auto-detect
    # models (selection; None = automatic)
    "models.whisper": None,                  # LocalModel id
    "models.ai": None,                       # LocalModel id (Ollama model)
    # speakers
    "speakers.diarization": True,
    "speakers.recognition": True,
    "speakers.inferNames": True,             # "Daan, kun jij…" → next speaker is Daan
    "speakers.matchThreshold": 0.75,
    # Cosine-distance merge threshold for d-vector clustering (see DECISIONS.md #7).
    "speakers.similarityThreshold": 0.60,    # sherpa-onnx (TitaNet): cosine distance between speaker embeddings
    # privacy
    "privacy.retentionDays": 0,              # 0 = never delete audio by age
    # mcp
    "mcp.enabled": True,                     # stdio server for local clients
    "mcp.networkEnabled": False,             # streamable-http on the LAN, API-key protected
    "mcp.port": 48800,
    # advanced
    "developer.mode": False,
    # onboarding
    "onboarding.completed": False,
}

# Old keys → new keys (kept so existing installs migrate transparently).
_RENAMED = {
    "transcription.language": "general.language",
    "transcription.computeDevice": "general.computeDevice",
    "transcription.model": "models.whisper",
    "ai.model": "models.ai",
    "ai.computeDevice": "general.computeDevice",
}


def normalize_settings(stored: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_USER_SETTINGS)
    for k, v in stored.items():
        k = _RENAMED.get(k, k)
        if k in DEFAULT_USER_SETTINGS or "." in k:
            merged[k] = v
    return merged


def resolve_notes_language(user: dict[str, Any]) -> str:
    """Language code the notes are written in. "auto" follows the system language, which the
    desktop shell passes as HUDDLE_SYSTEM_LANGUAGE; anything Huddle cannot write falls back to English."""
    from .providers.summarize import LANG_NAMES
    code = str(user.get("general.uiLanguage") or "auto").lower()
    if code != "auto":
        return code if code in LANG_NAMES else "en"
    system = (os.environ.get("HUDDLE_SYSTEM_LANGUAGE") or "").replace("_", "-").split("-")[0].lower()
    return system if system in LANG_NAMES else "en"


def apply_path_overrides(cfg: EngineConfig, user: dict[str, Any]) -> None:
    models_dir, logs_dir = user.get("paths.modelsDir"), user.get("paths.logsDir")
    cfg.models_dir_override = Path(models_dir).expanduser() if models_dir else None
    cfg.logs_dir_override = Path(logs_dir).expanduser() if logs_dir else None
    cfg.ensure_dirs()
