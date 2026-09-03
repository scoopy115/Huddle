"""whisper.cpp / WhisperKit model discovery (read-only). These formats are *not*
loadable by faster-whisper; they are listed so the Model Manager can say so
honestly instead of downloading a duplicate silently — and so a future
whisper.cpp provider can pick them up."""
from __future__ import annotations

import time
from pathlib import Path

from ..schemas import LocalModel, ProviderStatus
from .common import dir_size, whisper_size_from_name

WHISPER_CPP_DIRS = [
    Path.home() / ".cache" / "whisper.cpp",
    Path.home() / ".local" / "share" / "whisper.cpp",
    Path.home() / "whisper.cpp" / "models",
    Path("/opt/homebrew/share/whisper-cpp/models"),
    Path("/usr/local/share/whisper-cpp/models"),
]
WHISPERKIT_DIRS = [
    Path.home() / "Library" / "Application Support" / "whisperkit",
    Path.home() / ".cache" / "huggingface" / "hub",   # argmaxinc/whisperkit-coreml lives in HF cache
]


def discover() -> tuple[list[ProviderStatus], list[LocalModel]]:
    now = time.time()
    models: list[LocalModel] = []
    found_cpp = False
    for d in WHISPER_CPP_DIRS:
        if not d.exists():
            continue
        for f in d.glob("ggml-*.bin"):
            found_cpp = True
            try:
                size = f.stat().st_size
            except OSError:
                size = None
            models.append(LocalModel(id=f"whisper_cpp:{f.name}", name=f.stem.replace("ggml-", "whisper "),
                                     family="whisper", task="transcription", source="whisper_cpp",
                                     format="whisper.cpp", path=str(f), size_bytes=size, externally_managed=True,
                                     compatible_runtimes=["whisper.cpp"],
                                     meta={"whisperSize": whisper_size_from_name(f.name)}))
    found_kit = False
    for d in WHISPERKIT_DIRS:
        if not d.exists():
            continue
        for repo in d.glob("*whisperkit*"):
            found_kit = True
            models.append(LocalModel(id=f"whisperkit:{repo.name}", name=repo.name.replace("models--", "").replace("--", "/"),
                                     family="whisper", task="transcription", source="whisperkit", format="CoreML",
                                     path=str(repo), size_bytes=dir_size(repo), externally_managed=True,
                                     compatible_runtimes=["whisperkit"], meta={}))
    statuses = [
        ProviderStatus(id="whisper_cpp", kind="model_source", name="whisper.cpp models",
                       status="available" if found_cpp else "not_found", checked_at=now),
        ProviderStatus(id="whisperkit", kind="model_source", name="WhisperKit / CoreML models",
                       status="available" if found_kit else "not_found", checked_at=now),
    ]
    return statuses, models
