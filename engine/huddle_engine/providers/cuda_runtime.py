"""NVIDIA's cuBLAS for CTranslate2 on Windows and Linux: the one piece of the CUDA runtime the
Whisper wheel does not carry.

CTranslate2's wheels are built with CUDA and ship cuDNN, so a Whisper model *loads* on an NVIDIA
card out of the box, but the first matrix multiply needs ``cublas64_12.dll`` (``libcublas.so.12``
on Linux), which only comes with the CUDA toolkit or the ``nvidia-cublas-cu12`` wheel (~550 MB).
Rather than make every installer half a gigabyte heavier, Huddle offers it as a download in the
marketplace ("GPU acceleration") and drops the two libraries into ``<models>/cuda``. A CUDA
toolkit already on the PATH counts as installed too.
"""
from __future__ import annotations

import contextlib
import ctypes.util
import logging
import os
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CANDIDATE_ID = "cuda:cublas-12"
# nvidia-cublas-cu12 from PyPI: a plain wheel (zip) with the libraries under nvidia/cublas/{bin,lib}.
if os.name == "nt":
    WHEEL_URL = "https://files.pythonhosted.org/packages/45/a1/a17fade6567c57452cfc8f967a40d1035bb9301db52f27808167fbb2be2f/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"
    WHEEL_SIZE = 553_000_000
    LIBS = ("cublas64_12.dll", "cublasLt64_12.dll")
else:
    WHEEL_URL = "https://pypi.org/simple/nvidia-cublas-cu12/"   # resolved per platform later
    WHEEL_SIZE = 600_000_000
    LIBS = ("libcublas.so.12", "libcublasLt.so.12")

_models_dir: Path | None = None
_activated = False


def configure(models_dir: Path) -> None:
    global _models_dir
    _models_dir = models_dir
    activate()


def runtime_dir() -> Path:
    if _models_dir is None:
        raise RuntimeError("cuda_runtime.configure() was not called")
    return _models_dir / "cuda"


def supported() -> bool:
    """Only PCs: Apple Silicon has no CUDA, and the macOS wheel has no CUDA build."""
    return sys.platform != "darwin"


def installed() -> bool:
    """Huddle's own copy, or a system CUDA toolkit whose cuBLAS the loader can find."""
    if _models_dir is not None and all((runtime_dir() / lib).exists() for lib in LIBS):
        return True
    return system_cublas() is not None


def system_cublas() -> str | None:
    try:
        return ctypes.util.find_library("cublas64_12" if os.name == "nt" else "cublas")
    except Exception:
        return None


def activate() -> None:
    """Put Huddle's cuBLAS where the loader looks (once). Harmless when nothing is installed."""
    global _activated
    if _activated or _models_dir is None:
        return
    d = runtime_dir()
    if not all((d / lib).exists() for lib in LIBS):
        return
    if os.name == "nt":
        with contextlib.suppress(AttributeError, OSError):
            os.add_dll_directory(str(d))
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    if os.name != "nt":
        os.environ["LD_LIBRARY_PATH"] = str(d) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    _activated = True
    log.info("cuBLAS from %s", d)


def _wanted(member: str) -> str | None:
    """The library file name for a wheel member we keep, else None."""
    name = member.rsplit("/", 1)[-1]
    if os.name == "nt":
        return name if name in LIBS else None
    return name if any(name.startswith(lib) for lib in LIBS) else None


def extract_libs(wheel: Path, dest: Path) -> list[str]:
    """Copy the cuBLAS libraries out of the wheel into ``dest``; returns their names."""
    dest.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        for member in z.namelist():
            name = _wanted(member)
            if not name:
                continue
            with z.open(member) as src, open(dest / name, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            out.append(name)
    return out


def install(progress: Callable[[int, int | None], None] | None = None, cancelled: Callable[[], bool] | None = None) -> Path:
    """Download the wheel and keep only the libraries. Raises InterruptedError when cancelled."""
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    wheel = d / "cublas.whl.part"
    received = 0
    with httpx.stream("GET", WHEEL_URL, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r, open(wheel, "wb") as f:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0) or None
        for chunk in r.iter_bytes(1 << 20):
            if cancelled and cancelled():
                raise InterruptedError()
            f.write(chunk)
            received += len(chunk)
            if progress:
                progress(received, total)
    try:
        got = extract_libs(wheel, d)
    finally:
        wheel.unlink(missing_ok=True)
    missing = [lib for lib in LIBS if lib not in got]
    if missing:
        raise RuntimeError(f"The cuBLAS package did not contain {', '.join(missing)}.")
    activate()
    log.info("installed cuBLAS in %s", d)
    return d


def remove() -> None:
    d = runtime_dir()
    for lib in LIBS:
        (d / lib).unlink(missing_ok=True)
