"""Hardware detection from the engine's point of view: what this computer is, which compute
backends *the runtimes we ship* can use, and whether local AI is realistic on it at all.

The hardware dict is what onboarding and Settings → Models show, and what the resolver uses to
decide which models fit (``resolver.fit``). Three machine shapes matter:

* **Apple Silicon** — one unified memory pool; the GPU can use all of it. ``acceleratorBytes``
  is the system memory.
* **A PC with a dedicated graphics card** — the AI model has to fit in the card's own memory
  (VRAM), which is usually far smaller than the system memory. ``acceleratorBytes`` is the VRAM
  of the best card.
* **A PC with integrated graphics only** (Intel Iris/UHD, AMD Radeon Graphics) — nothing we ship
  uses that GPU; everything runs on the CPU and the only limit is system memory.
  ``acceleratorBytes`` is ``None``.

Only usable backends are reported as compute devices. Detection never raises: a machine that
cannot be probed reports unknowns and the conservative tier.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys

from ..schemas import ComputeDevice

log = logging.getLogger(__name__)

GB = 1024 ** 3

# Memory a local AI model needs, by machine shape (see ``assess``). Measured for Qwen3.5 4B at
# Q4 (3.4 GB) plus a 16k context: ~5.5 GB on the GPU; comfortable with 8 GB of unified memory.
MIN_VRAM_FOR_LLM = 6 * GB
MIN_RAM_FOR_CPU_LLM = 16 * GB
MIN_RAM_FOR_TRANSCRIPTION = 8 * GB

# Set for every subprocess so a console window never flashes up behind the app on Windows.
_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


def _run(args: list[str], timeout: float = 4) -> str | None:
    try:
        return subprocess.check_output(args, text=True, timeout=timeout, stderr=subprocess.DEVNULL, **_NO_WINDOW).strip()
    except Exception:
        return None


def _sysctl(key: str) -> str | None:
    return _run(["sysctl", "-n", key], timeout=2)


# ---- per-platform probes ----------------------------------------------------------------- #

def _memory_bytes() -> int | None:
    if sys.platform == "darwin":
        mem = _sysctl("hw.memsize")
        return int(mem) if mem and mem.isdigit() else None
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemoryStatusEx()
            st.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                return int(st.ullTotalPhys)
        except Exception:
            log.debug("GlobalMemoryStatusEx failed", exc_info=True)
        return None
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return None


def _cpu_brand() -> str | None:
    if sys.platform == "darwin":
        return _sysctl("machdep.cpu.brand_string")
    if os.name == "nt":
        try:
            import winreg  # type: ignore[import-not-found]
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip() or None
        except Exception:
            return platform.processor() or None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or None


def gpu_vendor(name: str, device_id: str = "") -> str:
    """apple | nvidia | amd | intel | other, from the adapter name or its PCI vendor id."""
    n, d = name.lower(), device_id.upper()
    if "VEN_10DE" in d or "nvidia" in n or "geforce" in n or "quadro" in n or " rtx" in f" {n}":
        return "nvidia"
    if "VEN_1002" in d or "VEN_1022" in d or "amd" in n or "radeon" in n:
        return "amd"
    if "VEN_8086" in d or "intel" in n:
        return "intel"
    if "apple" in n:
        return "apple"
    return "other"


def is_integrated(name: str, vendor: str, vram: int | None) -> bool:
    """Shares system memory (not a dedicated card). Intel is integrated unless it is an Arc card;
    AMD "Radeon(TM) Graphics" without a model number is the APU; anything that reports less
    than 1 GB of its own memory is not a card we can use."""
    n = name.lower()
    if vendor == "apple":
        return True
    if vendor == "intel":
        return "arc" not in n
    if vendor == "amd" and re.search(r"\bradeon(\(tm\))?\s+(\d{3,}m\s+)?graphics\b", n) and not re.search(r"\brx\b", n):
        return True
    return vram is not None and vram < 1 * GB


def _nvidia_smi() -> list[dict]:
    """Exact names and memory of NVIDIA cards (the driver's own tool; on Windows it sits in
    System32, on Linux in the driver package)."""
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    return parse_nvidia_smi(out or "")


def parse_nvidia_smi(out: str) -> list[dict]:
    gpus: list[dict] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            mib = float(parts[1])
        except ValueError:
            continue
        gpus.append({"name": parts[0], "vendor": "nvidia", "vramBytes": int(mib * 1024 * 1024), "integrated": False,
                     "source": "nvidia-smi"})
    return gpus


def adapter_record(desc: str, device_id: str, vram) -> dict:
    """One GPU record from a Windows display-adapter registry entry. ``vram`` is the raw value of
    ``HardwareInformation.qwMemorySize`` (QWORD, or 8 raw bytes) or the older ``MemorySize``
    DWORD, which wraps at 4 GB."""
    if isinstance(vram, bytes):
        vram = int.from_bytes(vram[:8], "little")
    vram_i = int(vram) if isinstance(vram, int) and vram > 0 else None
    vendor = gpu_vendor(desc, device_id)
    return {"name": desc.strip(), "vendor": vendor, "vramBytes": vram_i,
            "integrated": is_integrated(desc, vendor, vram_i), "source": "registry"}


def _windows_display_adapters() -> list[dict]:
    """Every display adapter from the driver class key in the registry: name (``DriverDesc``),
    PCI ids (``MatchingDeviceId``) and dedicated memory (``HardwareInformation.qwMemorySize``,
    a 64-bit value; the older ``MemorySize`` DWORD wraps at 4 GB). No WMI, no PowerShell."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    out: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        def val(name: str):
                            try:
                                return winreg.QueryValueEx(k, name)[0]
                            except OSError:
                                return None
                        desc = val("DriverDesc")
                        if not desc:
                            continue
                        vram = val("HardwareInformation.qwMemorySize")
                        if vram is None:
                            vram = val("HardwareInformation.MemorySize")
                            if isinstance(vram, bytes):
                                vram = int.from_bytes(vram[:4], "little")
                        out.append(adapter_record(str(desc), str(val("MatchingDeviceId") or ""), vram))
                except OSError:
                    continue
    except OSError:
        return []
    # The same card appears once per driver install; keep the first of each name.
    seen: set[str] = set()
    uniq = []
    for g in out:
        if g["name"] in seen:
            continue
        seen.add(g["name"])
        uniq.append(g)
    return uniq


def _linux_gpus() -> list[dict]:
    gpus = _nvidia_smi()
    out = _run(["lspci"], timeout=3) or ""
    for line in out.splitlines():
        if "VGA" in line or "3D controller" in line or "Display controller" in line:
            name = line.split(":", 2)[-1].strip()
            vendor = gpu_vendor(name)
            if vendor == "nvidia" and gpus:
                continue
            gpus.append({"name": name, "vendor": vendor, "vramBytes": None,
                         "integrated": vendor in ("intel", "amd") and is_integrated(name, vendor, None), "source": "lspci"})
    return gpus


def detect_gpus(system: str, cpu_brand: str | None, memory_bytes: int | None, apple_silicon: bool) -> list[dict]:
    if apple_silicon:
        chip = (cpu_brand or "Apple Silicon").strip()
        return [{"name": f"{chip} GPU", "vendor": "apple", "vramBytes": memory_bytes, "integrated": True, "source": "unified"}]
    if system == "Darwin":
        return []   # Intel Mac: nothing we ship uses its GPU
    if os.name == "nt":
        gpus = _windows_display_adapters()
        smi = _nvidia_smi()
        if smi:
            # nvidia-smi's memory is exact; the registry row of the same card may be missing it.
            gpus = smi + [g for g in gpus if g["vendor"] != "nvidia"]
        return gpus
    return _linux_gpus()


# ---- the public picture ------------------------------------------------------------------ #

def assess(hw: dict) -> dict:
    """Can this computer run Huddle's local AI, and how well? Three tiers:

    * ``full``    — a GPU that fits the models (Apple Silicon, or a card with ≥ 6 GB).
    * ``limited`` — no usable GPU, but ≥ 16 GB of memory: everything runs on the CPU, slowly.
    * ``minimal`` — transcription only; an AI model would not fit or would crawl.

    ``details`` are short lines for the onboarding card."""
    mem = hw.get("memoryBytes") or 0
    cores = hw.get("cpuCores") or 1
    acc = hw.get("acceleratorBytes")
    unified = bool(hw.get("unifiedMemory"))
    gpu = next((g for g in hw.get("gpus") or [] if not g.get("integrated")), None)
    details: list[str] = []

    if unified:
        tier = "full"
        details.append(f"{_gb(mem)} unified memory shared by the CPU and GPU")
        details.append("Transcription and AI notes run on the GPU")
        if mem < 16 * GB:
            details.append("Small AI models only (4B); larger ones need 16 GB")
    elif gpu and acc and acc >= MIN_VRAM_FOR_LLM:
        tier = "full"
        details.append(f"{gpu['name']} with {_gb(acc)} of graphics memory")
        details.append("AI notes run on the graphics card")
        details.append("Transcription runs on the CPU")
        if acc < 12 * GB:
            details.append("Small AI models only (4B); larger ones need 12 GB of graphics memory")
    elif mem >= MIN_RAM_FOR_CPU_LLM and cores >= 4:
        tier = "limited"
        if gpu:
            details.append(f"{gpu['name']} has too little graphics memory ({_gb(acc)}); it is not used")
        else:
            details.append("No dedicated graphics card")
        details.append(f"{_gb(mem)} of memory; everything runs on the CPU")
        details.append("Transcription and AI notes take several times longer than on a GPU")
    else:
        tier = "minimal"
        if mem and mem < MIN_RAM_FOR_TRANSCRIPTION:
            details.append(f"{_gb(mem)} of memory; small transcription models only")
        else:
            details.append(f"{_gb(mem)} of memory, no usable graphics card")
        details.append("AI notes need 16 GB of memory or a graphics card with 6 GB")
        details.append("Transcription works")
    title = {"full": "Ready for local AI", "limited": "Runs on the CPU", "minimal": "Transcription only"}[tier]
    return {"tier": tier, "title": title, "details": details}


def _gb(n: int | None) -> str:
    if not n:
        return "unknown"
    return f"{round(n / GB)} GB"


def hardware_info() -> dict:
    system = platform.system()
    machine = platform.machine()
    apple_silicon = system == "Darwin" and machine == "arm64"
    cpu_brand = _cpu_brand()
    mem = _memory_bytes()
    if system == "Darwin":
        os_version = platform.mac_ver()[0]
    elif os.name == "nt":
        os_version = platform.version()   # "10.0.26100": build ≥ 22000 is Windows 11
    else:
        os_version = platform.release()
    try:
        gpus = detect_gpus(system, cpu_brand, mem, apple_silicon)
    except Exception:
        log.warning("GPU detection failed", exc_info=True)
        gpus = []
    return describe(system, machine, cpu_brand, mem, os_version, gpus)


def describe(system: str, machine: str, cpu_brand: str | None, mem: int | None, os_version: str, gpus: list[dict],
             cpu_cores: int | None = None) -> dict:
    """The hardware dict from the raw probes (pure, so tests can feed it other machines)."""
    apple_silicon = system == "Darwin" and machine == "arm64"
    dedicated = [g for g in gpus if not g.get("integrated") and g.get("vramBytes")]
    best = max(dedicated, key=lambda g: g["vramBytes"]) if dedicated else None
    info = {
        "os": "windows" if system == "Windows" else system.lower(),
        "osVersion": os_version,
        "arch": machine,
        "cpuBrand": cpu_brand,
        "cpuCores": cpu_cores or os.cpu_count() or 1,
        "memoryBytes": mem,
        "appleSilicon": apple_silicon,
        "unifiedMemory": apple_silicon,
        "gpus": gpus,
        # Memory the AI models can use on an accelerator: all of it on Apple Silicon, the best
        # card's VRAM on a PC, nothing when only the CPU is usable.
        "acceleratorBytes": mem if apple_silicon else (best["vramBytes"] if best else None),
        "acceleratorName": gpus[0]["name"] if apple_silicon and gpus else (best["name"] if best else None),
    }
    info["capability"] = assess(info)
    return info


def compute_devices() -> list[ComputeDevice]:
    hw = hardware_info()
    devices: list[ComputeDevice] = []
    if hw["appleSilicon"]:
        # Metal is usable when either GPU runtime is present: MLX (Whisper) or torch's MPS backend.
        # The packaged app ships MLX only, so torch must not be required for the GPU to count.
        import importlib.util
        metal_ok = importlib.util.find_spec("mlx") is not None
        if not metal_ok:
            try:
                import torch
                metal_ok = bool(torch.backends.mps.is_available())
            except Exception:
                pass
        devices.append(ComputeDevice(
            id="apple-gpu-metal", name=f"{(hw['cpuBrand'] or 'Apple Silicon').strip()} GPU",
            vendor="apple", backend="metal", memory_bytes=hw["memoryBytes"], device_type="gpu",
            available=metal_ok, recommended=metal_ok))
    cuda = False
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    if cuda:  # pragma: no cover - no CUDA on mac
        nv = next((g for g in hw["gpus"] if g["vendor"] == "nvidia"), None)
        devices.append(ComputeDevice(id="nvidia-cuda", name=nv["name"] if nv else "NVIDIA GPU", vendor="nvidia", backend="cuda",
                                     memory_bytes=nv["vramBytes"] if nv else None, device_type="gpu", available=True, recommended=True))
    devices.append(ComputeDevice(
        id="cpu", name=f"CPU ({hw['cpuCores']} cores)", vendor="cpu", backend="cpu",
        memory_bytes=hw["memoryBytes"], device_type="cpu", available=True,
        recommended=not any(d.recommended for d in devices)))
    return devices
