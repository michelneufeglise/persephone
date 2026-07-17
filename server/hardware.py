"""
Cross-platform hardware detection for the Persephone setup wizard.
Supports macOS (Apple Silicon + Intel), Linux, and Windows 10/11.

Extended fingerprinting (2026-07):
  * `chip_family` — apple_silicon_m{1,2,3,4}, intel_{9,10,11,12,13,14}th,
                    amd_zen{2,3,4,5}, unknown.
  * `chip_variant` — "base" | "pro" | "max" | "ultra" (Apple only)
  * `mem_bandwidth_gb_s` — estimated peak memory bandwidth (main
    throughput bottleneck for LLM inference on consumer hardware)
  * `perf_cores` — physical performance cores (excludes efficiency cores
    on hybrid Apple / Intel Alder Lake+ CPUs)
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


# ── Chip fingerprint parsers ────────────────────────────────────────────────
# Kept pure (no subprocess calls) so tests can round-trip known CPU strings.

def _parse_apple_silicon(cpu: str) -> tuple[str, str]:
    """('apple_silicon_m2', 'pro') etc — falls back to ('apple_silicon_unknown', 'base')."""
    low = cpu.lower()
    for gen in ("m4", "m3", "m2", "m1"):
        if gen in low or f"apple {gen}" in low:
            variant = "base"
            if "ultra" in low:  variant = "ultra"
            elif "max" in low:  variant = "max"
            elif "pro" in low:  variant = "pro"
            return (f"apple_silicon_{gen}", variant)
    return ("apple_silicon_unknown", "base")


def _parse_intel(cpu: str) -> str:
    """Intel Core generation extractor. i7-12700H → intel_12th; Ultra 7 155H → intel_14th (Meteor Lake)."""
    low = cpu.lower()
    # "Core Ultra …" (Meteor Lake+) — treat as 14th-gen for benchmark purposes.
    if "core ultra" in low or "ultra 5" in low or "ultra 7" in low or "ultra 9" in low:
        return "intel_14th"
    # i{3,5,7,9}-{gen}{model}{suffix} — e.g. i7-12700H, i9-13900K, i5-8265U.
    m = re.search(r"i[3579]-(\d{4,5})", low)
    if m:
        digits = m.group(1)
        # 10th gen and up: 5-digit part (e.g. 12700, 13900).
        # 4-digit is <=9th gen (e.g. 8265, 9750). First digit(s) = gen.
        if len(digits) == 5:
            gen = int(digits[:2])
        else:
            gen = int(digits[0])
        if 1 <= gen <= 15:
            return f"intel_{gen}th"
    return "intel_unknown"


def _parse_amd(cpu: str) -> str:
    """AMD Zen generation extractor. Ryzen 9 7940HS → amd_zen4; 5800X → amd_zen3."""
    low = cpu.lower()
    if "ryzen" not in low and "epyc" not in low and "amd" not in low:
        return "amd_unknown"
    # Ryzen 4-digit model → mapped to Zen gen. Ballpark:
    #   1xxx = Zen1, 2xxx = Zen+, 3xxx = Zen2, 5xxx = Zen3, 7xxx = Zen4, 9xxx = Zen5
    m = re.search(r"ryzen\s*[3579]\s*(\d)", low)
    if m:
        first = int(m.group(1))
        return {
            1: "amd_zen1", 2: "amd_zen1", 3: "amd_zen2",
            5: "amd_zen3", 7: "amd_zen4", 9: "amd_zen5",
        }.get(first, "amd_unknown")
    return "amd_unknown"


def fingerprint_chip(cpu_string: str, is_apple_silicon: bool) -> tuple[str, str]:
    """
    Given the OS-reported CPU brand string, return (chip_family, chip_variant).

    variant is "base" | "pro" | "max" | "ultra" for Apple Silicon,
    or "n/a" otherwise.
    """
    if is_apple_silicon:
        fam, var = _parse_apple_silicon(cpu_string)
        return (fam, var)
    if "intel" in cpu_string.lower():
        return (_parse_intel(cpu_string), "n/a")
    if "amd" in cpu_string.lower() or "ryzen" in cpu_string.lower():
        return (_parse_amd(cpu_string), "n/a")
    return ("unknown", "n/a")


# ── Memory bandwidth ballpark (GB/s) ────────────────────────────────────────
# Peak theoretical bandwidth per chip family — the primary LLM inference
# throughput ceiling. Numbers from Apple/Intel spec sheets + community
# benchmarks (llama.cpp discussions, TechPowerUp). Real workloads hit
# ~70-85% of peak. Used by benchmarks.py to estimate tok/s.
_APPLE_BANDWIDTH: dict[tuple[str, str], int] = {
    ("apple_silicon_m1",  "base"):  68,
    ("apple_silicon_m1",  "pro"):  200,
    ("apple_silicon_m1",  "max"):  400,
    ("apple_silicon_m1",  "ultra"): 800,
    ("apple_silicon_m2",  "base"):  100,
    ("apple_silicon_m2",  "pro"):  200,
    ("apple_silicon_m2",  "max"):  400,
    ("apple_silicon_m2",  "ultra"): 800,
    ("apple_silicon_m3",  "base"):  100,
    ("apple_silicon_m3",  "pro"):  150,
    ("apple_silicon_m3",  "max"):  400,
    ("apple_silicon_m4",  "base"):  120,
    ("apple_silicon_m4",  "pro"):  273,
    ("apple_silicon_m4",  "max"):  546,
    ("apple_silicon_m4",  "ultra"): 1092,
}

# CPU-only fallback (DDR4 vs DDR5).
_INTEL_BANDWIDTH: dict[str, int] = {
    "intel_9th":  40, "intel_10th": 45, "intel_11th": 50, "intel_12th": 76,
    "intel_13th": 90, "intel_14th": 90, "intel_unknown": 40,
}
_AMD_BANDWIDTH: dict[str, int] = {
    "amd_zen1": 35, "amd_zen2": 45, "amd_zen3": 51, "amd_zen4": 83,
    "amd_zen5": 90, "amd_unknown": 40,
}

# Rough VRAM-bandwidth values for common discrete GPUs. Only used when the
# GPU has ≥ model_size_gb VRAM — otherwise the layers spill to system RAM
# and we're back to CPU-bandwidth speeds.
_GPU_BANDWIDTH: dict[str, int] = {
    "rtx 4090":  1008, "rtx 4080": 717, "rtx 4070 ti": 504, "rtx 4070": 504,
    "rtx 4060":  272,  "rtx 3090": 936, "rtx 3080":   760, "rtx 3070":  448,
    "rtx 3060":  360,  "rtx 3050": 224,
    "rx 7900":   960,  "rx 7800":  624, "rx 7700":    432, "rx 7600":   288,
}


def _estimate_bandwidth(chip_family: str, chip_variant: str, gpu: str, gpu_vram_gb: int) -> int:
    """
    Return a bandwidth estimate in GB/s. Apple Silicon's unified memory
    bandwidth wins on Apple hardware. On x86, a discrete GPU with usable
    VRAM (≥6GB) takes over. Otherwise CPU/system-RAM bandwidth.
    """
    # Apple Silicon always uses its own unified-memory bandwidth — the
    # "gpu" is unified and doesn't have a discrete-VRAM number to compare.
    if chip_family.startswith("apple_silicon"):
        return _APPLE_BANDWIDTH.get((chip_family, chip_variant), 100)
    # Discrete GPU wins on x86 if it has non-trivial VRAM. Skip the
    # "Microsoft Basic Display Adapter" / "Intel HD Graphics" fallbacks
    # that appear in `Get-CimInstance Win32_VideoController` output.
    if gpu and gpu_vram_gb >= 6:
        low = gpu.lower()
        # Explicitly ignore integrated GPUs even if they've been reported
        # with high "VRAM" (which is really system RAM allocation).
        integrated = any(k in low for k in (
            "integrated", "iris", "uhd graphics", "hd graphics",
            "radeon graphics", "vega", "microsoft basic",
        ))
        if not integrated:
            for key, bw in _GPU_BANDWIDTH.items():
                if key in low:
                    return bw
            # Unknown discrete GPU — assume 350 GB/s (RTX 3060-class).
            return 350
    if chip_family.startswith("intel"):
        return _INTEL_BANDWIDTH.get(chip_family, 40)
    if chip_family.startswith("amd"):
        return _AMD_BANDWIDTH.get(chip_family, 40)
    return 40   # dead-conservative fallback


def _powershell(cmd: str, timeout: int = 5) -> str:
    """Invoke PowerShell with -NoProfile -Command. Returns trimmed stdout."""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout)


@lru_cache(maxsize=1)
def get_hardware() -> dict:
    sys_name = platform.system()
    info: dict = {
        "os":                 sys_name,
        "os_release":         platform.release(),
        "arch":               platform.machine(),
        "cpu":                platform.processor() or "Unknown",
        "ram_gb":             0,
        "cores":              os.cpu_count() or 0,
        "perf_cores":         0,          # populated below where available
        "is_apple_silicon":   False,
        "gpu":                "",
        "gpu_vram_gb":        0,
        "chip_family":        "unknown",
        "chip_variant":       "n/a",
        "mem_bandwidth_gb_s": 40,
        "tier":               "low",
        "python":             platform.python_version(),
    }

    if sys_name == "Darwin":
        info["is_apple_silicon"] = info["arch"] == "arm64"
        ram_bytes = _run(["sysctl", "-n", "hw.memsize"])
        if ram_bytes.isdigit():
            info["ram_gb"] = int(ram_bytes) // (1024 ** 3)
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if info["is_apple_silicon"]:
            # brand_string is empty on Apple Silicon; get the friendly name.
            hw_model = _run(["sysctl", "-n", "hw.model"])
            # Try to pull the actual chip name from a `system_profiler` call —
            # it prints "Chip: Apple M2 Pro" which we can parse for variant.
            sp = _run(["system_profiler", "SPHardwareDataType"], timeout=8)
            chip = ""
            for line in sp.splitlines():
                if "Chip:" in line:
                    chip = line.split(":", 1)[1].strip()  # e.g. "Apple M2 Pro"
                    break
            cpu = chip or cpu or hw_model or "Apple Silicon"
        info["cpu"] = cpu or info["cpu"]
        cores = _run(["sysctl", "-n", "hw.physicalcpu"])
        if cores.isdigit():
            info["cores"] = int(cores)
        # Perf-core count on Apple Silicon = `hw.perflevel0.physicalcpu`.
        pcores = _run(["sysctl", "-n", "hw.perflevel0.physicalcpu"])
        if pcores.isdigit():
            info["perf_cores"] = int(pcores)
        elif info["cores"]:
            info["perf_cores"] = info["cores"]
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

    # Chip family + variant fingerprint (used by benchmarks.py for tok/s
    # estimation). Uses the OS-reported CPU string + is_apple_silicon flag.
    fam, var = fingerprint_chip(info["cpu"], info["is_apple_silicon"])
    info["chip_family"]  = fam
    info["chip_variant"] = var
    info["mem_bandwidth_gb_s"] = _estimate_bandwidth(
        fam, var, info["gpu"], info["gpu_vram_gb"],
    )
    # Perf-core fallback: assume all cores are perf cores on non-hybrid CPUs.
    if not info["perf_cores"]:
        info["perf_cores"] = info["cores"]
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
