"""
Cross-platform hardware detection for the Persephone setup wizard.
Supports macOS (Apple Silicon + Intel), Linux, and Windows 10/11.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from functools import lru_cache
from pathlib import Path


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=timeout,
        ).strip()
    except Exception:
        return ""


def _powershell(cmd: str, timeout: int = 5) -> str:
    """Invoke PowerShell with -NoProfile -Command. Returns trimmed stdout."""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout)


@lru_cache(maxsize=1)
def get_hardware() -> dict:
    sys_name = platform.system()
    info: dict = {
        "os":               sys_name,
        "os_release":       platform.release(),
        "arch":             platform.machine(),
        "cpu":              platform.processor() or "Unknown",
        "ram_gb":           0,
        "cores":            os.cpu_count() or 0,
        "is_apple_silicon": False,
        "gpu":              "",
        "gpu_vram_gb":      0,
        "tier":             "low",
        "python":           platform.python_version(),
    }

    if sys_name == "Darwin":
        info["is_apple_silicon"] = info["arch"] == "arm64"
        ram_bytes = _run(["sysctl", "-n", "hw.memsize"])
        if ram_bytes.isdigit():
            info["ram_gb"] = int(ram_bytes) // (1024 ** 3)
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if not cpu and info["is_apple_silicon"]:
            cpu = _run(["sysctl", "-n", "hw.model"]) or "Apple Silicon"
        info["cpu"] = cpu or info["cpu"]
        cores = _run(["sysctl", "-n", "hw.physicalcpu"])
        if cores.isdigit():
            info["cores"] = int(cores)
        if info["is_apple_silicon"]:
            info["gpu"] = "Apple Silicon (unified memory)"
            info["gpu_vram_gb"] = info["ram_gb"]   # unified memory acts as VRAM

    elif sys_name == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        info["ram_gb"] = int(line.split()[1]) // (1024 ** 2)
                        break
        except OSError:
            pass

        lscpu = _run(["lscpu"])
        for line in lscpu.splitlines():
            if "Model name" in line:
                info["cpu"] = line.split(":", 1)[1].strip()
            elif "CPU(s):" in line and "NUMA" not in line and "On-line" not in line:
                v = line.split(":")[1].strip()
                if v.isdigit():
                    info["cores"] = int(v)
        if info["cpu"] == "Unknown":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line:
                            info["cpu"] = line.split(":", 1)[1].strip()
                            break
            except OSError:
                pass

        # NVIDIA GPU + VRAM
        nvidia = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if nvidia:
            line = nvidia.splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                info["gpu"] = parts[0]
                if parts[1].isdigit():
                    info["gpu_vram_gb"] = int(parts[1]) // 1024

        # AMD GPU via rocm-smi
        if not info["gpu"]:
            rocm = _run(["rocm-smi", "--showproductname"])
            for line in rocm.splitlines():
                if "Card Series" in line and ":" in line:
                    info["gpu"] = line.split(":", 1)[1].strip()
                    break

    elif sys_name == "Windows":
        # Modern PowerShell CIM (replaces deprecated wmic)
        ram_bytes = _powershell("(Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory")
        if ram_bytes.isdigit():
            info["ram_gb"] = int(ram_bytes) // (1024 ** 3)

        cpu_name = _powershell("(Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1).Name")
        if cpu_name:
            info["cpu"] = cpu_name.splitlines()[0].strip()

        cores = _powershell("(Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1).NumberOfCores")
        if cores.isdigit():
            info["cores"] = int(cores)

        # Pick the first non-virtual / non-basic GPU
        gpu_list = _powershell(
            "Get-CimInstance -ClassName Win32_VideoController | Select-Object -ExpandProperty Name"
        )
        ignore_terms = ("microsoft basic", "remote display", "virtual")
        for line in gpu_list.splitlines():
            line = line.strip()
            if line and not any(t in line.lower() for t in ignore_terms):
                info["gpu"] = line
                break

        # NVIDIA VRAM if nvidia-smi is on PATH
        nvidia = _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
        if nvidia:
            first = nvidia.splitlines()[0].strip()
            if first.isdigit():
                info["gpu_vram_gb"] = int(first) // 1024

    info["tier"] = _tier(info["ram_gb"], info["is_apple_silicon"], info["gpu_vram_gb"])
    return info


@lru_cache(maxsize=1)
def recommended_num_thread() -> int:
    """
    CPU thread count to hand Ollama for `num_thread`.

    Previously this was a flat constant (10) tuned for M1 Pro/Max and used
    everywhere regardless of host — fine on the machine it was tuned for,
    but it silently under-utilises bigger CPUs (e.g. a 12-core Windows
    laptop only ever got 10 threads) and over-subscribes smaller ones.
    Use the actual detected physical core count instead, with the old
    constant as a sane fallback when detection fails.
    """
    cores = get_hardware()["cores"]
    return cores if cores > 0 else 10


def _tier(ram_gb: int, apple_silicon: bool, gpu_vram_gb: int = 0) -> str:
    """
    Performance tier accounting for the fact that:
      * Apple Silicon uses unified memory (RAM ≈ effective VRAM)
      * A discrete GPU with N GB VRAM can punch above its weight class because
        models stay resident in fast HBM/GDDR memory.
    """
    effective = max(
        ram_gb * (1.2 if apple_silicon else 1.0),
        gpu_vram_gb * 3 if gpu_vram_gb else 0,
    )
    if effective >= 64:  return "ultra"
    if effective >= 32:  return "high"
    if effective >= 16:  return "mid"
    if effective >= 8:   return "low"
    return "minimal"
