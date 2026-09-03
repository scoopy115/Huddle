"""Speaker-separation models for the sherpa-onnx diarizer.

Two ONNX files:

* **pyannote segmentation 3.0** (MIT) — frame-level "who is speaking, and where does the
  speaker change" inside 10 s windows. This is what gives precise turn boundaries.
* **NVIDIA TitaNet large** (CC-BY-4.0) — one vector per stretch of speech, clustered into people.
  It was the only one of three candidate models that separated both the real meetings and the
  synthetic fixtures (docs/DECISIONS.md #41), so it is the single supported model.

The packaged app ships both inside the bundle (`HUDDLE_BUNDLED_MODELS`); a development checkout
fetches them once from sherpa-onnx's GitHub releases (Apache-2.0 project, plain files, no
Hugging Face account) into ``<models>/speaker``.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class SpeakerModelFile:
    id: str
    filename: str            # name on disk inside <models>/speaker
    url: str
    size_bytes: int          # approximate, for progress
    license: str
    name: str
    inner: str | None = None  # for archives: the member to extract


SEGMENTATION = SpeakerModelFile(
    id="pyannote-segmentation-3.0", filename="pyannote-segmentation-3.0.int8.onnx",
    url=f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    size_bytes=6_000_000, license="MIT", name="pyannote segmentation 3.0",
    inner="sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx")

EMBEDDING = SpeakerModelFile(
    id="titanet-large", filename="nemo_titanet_large.onnx",
    url=f"{RELEASES}/speaker-recongition-models/nemo_en_titanet_large.onnx",
    size_bytes=101_405_493, license="CC-BY-4.0", name="NVIDIA TitaNet large")

FILES = (SEGMENTATION, EMBEDDING)


@dataclass
class SpeakerModelPaths:
    segmentation: Path
    embedding: Path
    embedding_id: str


def sherpa_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def speaker_models_dir(models_dir: Path) -> Path:
    return Path(models_dir) / "speaker"


def bundled_dir() -> Path | None:
    """Models shipped inside the app (set by the desktop shell), if that folder has them."""
    raw = os.environ.get("HUDDLE_BUNDLED_MODELS")
    if not raw:
        return None
    d = Path(raw) / "speaker"
    return d if all((d / f.filename).exists() for f in FILES) else None


def installed(models_dir: Path) -> bool:
    if bundled_dir():
        return True
    d = speaker_models_dir(models_dir)
    return all((d / f.filename).exists() for f in FILES)


def ensure_speaker_models(models_dir: Path, progress: Callable[[float], None] | None = None,
                          cancelled: Callable[[], bool] | None = None) -> SpeakerModelPaths:
    """Return the model paths: bundled ones when the app ships them, otherwise the user's model
    folder, downloading what is missing. Raises on network failure so the caller can fall back."""
    if (b := bundled_dir()) is not None:
        return SpeakerModelPaths(segmentation=b / SEGMENTATION.filename, embedding=b / EMBEDDING.filename, embedding_id=EMBEDDING.id)
    d = speaker_models_dir(models_dir)
    d.mkdir(parents=True, exist_ok=True)
    total = sum(f.size_bytes for f in FILES if not (d / f.filename).exists()) or 1
    done = 0
    for f in FILES:
        target = d / f.filename
        if target.exists():
            continue

        def tick(n: int, _done=done) -> None:
            if progress:
                progress(min(1.0, (_done + n) / total))
            if cancelled and cancelled():
                raise _Cancelled()

        _download(f, target, tick)
        done += f.size_bytes
    if progress:
        progress(1.0)
    return SpeakerModelPaths(segmentation=d / SEGMENTATION.filename, embedding=d / EMBEDDING.filename, embedding_id=EMBEDDING.id)


def _download(f: SpeakerModelFile, target: Path, tick: Callable[[int], None]) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="huddle-speaker-", dir=target.parent))
    try:
        blob = tmp_dir / "download"
        req = urllib.request.Request(f.url, headers={"User-Agent": "Huddle"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(blob, "wb") as out:
            n = 0
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                n += len(chunk)
                tick(n)
        if f.inner:
            with tarfile.open(blob, "r:bz2") as tar:
                member = tar.getmember(f.inner)
                with tar.extractfile(member) as src, open(tmp_dir / "model", "wb") as dst:  # type: ignore[arg-type]
                    shutil.copyfileobj(src, dst)
            (tmp_dir / "model").replace(target)
        else:
            blob.replace(target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class _Cancelled(Exception):
    pass
