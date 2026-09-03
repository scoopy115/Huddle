"""Compute-device detection from the engine's point of view: which backends the
*runtimes we ship* can actually use. Only usable backends are reported."""
from __future__ import annotations

import os
import platform
import subprocess

from ..schemas import ComputeDevice


def _sysctl(key: str) -> str | None:
    try:
        return subprocess.check_output(["sysctl", "-n", key], text=True, timeout=2).strip()
    except Exception:
        return None


def hardware_info() -> dict:
    system = platform.system()
    machine = platform.machine()
    info = {"os": system.lower(), "osVersion": platform.mac_ver()[0] if system == "Darwin" else platform.release(),
            "arch": machine, "cpuBrand": None, "cpuCores": os.cpu_count() or 1, "memoryBytes": None,
            "appleSilicon": system == "Darwin" and machine == "arm64"}
    if system == "Darwin":
        info["cpuBrand"] = _sysctl("machdep.cpu.brand_string")
        mem = _sysctl("hw.memsize")
        info["memoryBytes"] = int(mem) if mem and mem.isdigit() else None
    else:  # pragma: no cover - non-mac
        info["cpuBrand"] = platform.processor() or None
        try:
            import psutil  # type: ignore
            info["memoryBytes"] = psutil.virtual_memory().total
        except Exception:
            pass
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
        devices.append(ComputeDevice(id="nvidia-cuda", name="NVIDIA GPU", vendor="nvidia", backend="cuda",
                                     memory_bytes=None, device_type="gpu", available=True, recommended=True))
    devices.append(ComputeDevice(
        id="cpu", name=f"CPU ({hw['cpuCores']} cores)", vendor="cpu", backend="cpu",
        memory_bytes=hw["memoryBytes"], device_type="cpu", available=True,
        recommended=not any(d.recommended for d in devices)))
    return devices
