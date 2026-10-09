"""Ollama as Huddle's AI runtime, without asking the user to install anything.

Huddle talks to whichever Ollama server is reachable: the user's own (Ollama.app or Homebrew,
port 11434) when it runs; otherwise a server Huddle starts itself — from an `ollama` binary already
on the Mac if there is one, else from a copy Huddle downloads once into `<models>/ollama/`. Huddle's
own server listens on 127.0.0.1:11435 and uses the standard model store (~/.ollama/models), so
models pulled by either side are visible to both and nothing is stored twice."""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# HUDDLE_OLLAMA_URL points at an Ollama elsewhere (Huddle Server: the `ollama` container); Huddle then
# never starts its own.
SYSTEM_URL = os.getenv("HUDDLE_OLLAMA_URL", "").rstrip("/") or "http://127.0.0.1:11434"
MANAGED_HOST = "127.0.0.1:11435"
MANAGED_URL = f"http://{MANAGED_HOST}"
_RELEASES = "https://github.com/ollama/ollama/releases/latest/download"
if sys.platform == "darwin":
    ARCHIVE_URL = f"{_RELEASES}/ollama-darwin.tgz"
    ARCHIVE_SIZE = 160_000_000      # ~152 MB; the real size comes from the download headers
elif os.name == "nt":
    # The standalone CLI (ollama.exe + the NVIDIA/AMD runner libraries), not the installer:
    # no tray app, no autostart, nothing registered on the user's PC.
    ARCHIVE_URL = f"{_RELEASES}/ollama-windows-amd64.zip"
    ARCHIVE_SIZE = 1_100_000_000
else:
    ARCHIVE_URL = f"{_RELEASES}/ollama-linux-amd64.tgz"
    ARCHIVE_SIZE = 1_000_000_000
EXE = "ollama.exe" if os.name == "nt" else "ollama"
# No console window behind the app on Windows.
_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}

_models_dir: Path | None = None
_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def configure(models_dir: Path) -> None:
    global _models_dir
    _models_dir = models_dir


def runtime_dir() -> Path:
    return (_models_dir or Path.home() / ".huddle") / "ollama"


def managed_binary() -> Path:
    return runtime_dir() / "bin" / EXE


def system_binary() -> Path | None:
    candidates: list[str | None] = [shutil.which("ollama")]
    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA")
        if local:
            candidates.append(str(Path(local) / "Programs" / "Ollama" / "ollama.exe"))
    else:
        candidates += ["/Applications/Ollama.app/Contents/Resources/ollama", "/opt/homebrew/bin/ollama",
                       "/usr/local/bin/ollama", "/usr/bin/ollama"]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def binary() -> Path | None:
    """An `ollama` executable Huddle can start: the user's, else the one Huddle downloaded."""
    return system_binary() or (managed_binary() if managed_binary().exists() else None)


def responds(url: str, timeout: float = 1.5) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=timeout).status_code == 200
    except Exception:
        return False


def active_url(start: bool = True) -> str | None:
    """The Ollama server to use right now; starts Huddle's own when nothing answers (if allowed)."""
    if responds(SYSTEM_URL):
        return SYSTEM_URL
    if responds(MANAGED_URL):
        return MANAGED_URL
    if start and not os.getenv("HUDDLE_OLLAMA_URL") and binary() and ensure_started():
        return MANAGED_URL
    return None


def ensure_started(timeout: float = 25.0) -> bool:
    global _proc
    with _lock:
        if responds(MANAGED_URL):
            return True
        b = binary()
        if not b:
            return False
        env = dict(os.environ, OLLAMA_HOST=MANAGED_HOST)
        env.setdefault("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models"))
        runtime_dir().mkdir(parents=True, exist_ok=True)
        logf = open(runtime_dir() / "ollama.log", "ab")  # noqa: SIM115 — handed to the child
        try:
            _proc = subprocess.Popen([str(b), "serve"], env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                     **_NO_WINDOW)
        except OSError as e:
            log.error("could not start ollama from %s: %s", b, e)
            return False
        atexit.register(stop)
        log.info("started Ollama runtime (%s) on %s", b, MANAGED_HOST)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if responds(MANAGED_URL, timeout=0.5):
                return True
            if _proc.poll() is not None:
                log.error("ollama exited early with %s — see %s", _proc.returncode, runtime_dir() / "ollama.log")
                return False
            time.sleep(0.25)
        return False


def stop() -> None:
    global _proc
    p = _proc
    _proc = None
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def install(progress: Callable[[int, int | None], None] | None = None, cancelled: Callable[[], bool] | None = None) -> Path:
    """Download and unpack the official build for this OS into `<models>/ollama/bin` (once)."""
    dest = runtime_dir() / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    archive = runtime_dir() / ARCHIVE_URL.rsplit("/", 1)[-1]
    received = 0
    with httpx.stream("GET", ARCHIVE_URL, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r, open(archive, "wb") as f:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0) or None
        for chunk in r.iter_bytes(1 << 20):
            if cancelled and cancelled():
                raise InterruptedError("cancelled")
            f.write(chunk)
            received += len(chunk)
            if progress:
                progress(received, total)
    if archive.suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(dest, filter="data")
            except TypeError:  # Python < 3.11.4
                tar.extractall(dest)
    archive.unlink(missing_ok=True)
    exe = managed_binary()
    if not exe.exists():
        # The Linux archive nests the binary under bin/.
        nested = dest / "bin" / EXE
        if nested.exists():
            shutil.move(str(nested), str(managed_binary()))
            exe = managed_binary()
        else:
            raise RuntimeError("The Ollama archive did not contain the ollama executable.")
    if os.name != "nt":
        exe.chmod(0o755)
    log.info("installed Ollama runtime at %s", exe)
    return exe
