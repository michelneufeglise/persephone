"""
Cross-platform Ollama detection, installation, and lifecycle management.

Strategy per platform:
  • macOS   — `curl -fsSL https://ollama.com/install.sh | sh` (or pre-existing .app)
  • Linux   — same install script (sets up systemd)
  • Windows — guided manual install (UAC required for installer), then polls
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import AsyncIterator

import httpx

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")


# ── Detection ────────────────────────────────────────────────────────────────
def _fallback_paths() -> list[Path]:
    """Common install locations checked when `ollama` isn't on PATH."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        return [
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    if sys_name == "Linux":
        return [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    if sys_name == "Windows":
        pf       = Path(os.environ.get("ProgramFiles",  r"C:\Program Files"))
        local_ad = Path(os.environ.get("LOCALAPPDATA", ""))
        return [
            pf / "Ollama" / "ollama.exe",
            local_ad / "Programs" / "Ollama" / "ollama.exe",
            local_ad / "Programs" / "Ollama" / "Ollama.exe",
        ]
    return []


def is_installed() -> bool:
    if shutil.which("ollama"):
        return True
    return any(p.exists() for p in _fallback_paths())


def find_executable() -> str | None:
    p = shutil.which("ollama")
    if p:
        return p
    for fp in _fallback_paths():
        if fp.exists():
            return str(fp)
    return None


async def is_running() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{OLLAMA_BASE}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def get_version() -> str:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{OLLAMA_BASE}/api/version")
            if r.status_code == 200:
                return r.json().get("version", "")
    except Exception:
        pass
    return ""


def get_install_info() -> dict:
    """Return platform-specific install metadata for the UI."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        return {
            "method":          "shell",
            "command":         "curl -fsSL https://ollama.com/install.sh | sh",
            "url":             "https://ollama.com/download/mac",
            "label":           "Install Ollama (run shell script)",
            "instructions":    "Recommended: run the official install script. Alternatively download the macOS app.",
            "requires_manual": False,
        }
    if sys_name == "Linux":
        return {
            "method":          "shell",
            "command":         "curl -fsSL https://ollama.com/install.sh | sh",
            "url":             "https://ollama.com/download/linux",
            "label":           "Install Ollama (run shell script)",
            "instructions":    "The official script installs Ollama and configures it as a systemd service. Requires sudo.",
            "requires_manual": False,
        }
    if sys_name == "Windows":
        return {
            "method":          "download",
            "command":         "",
            "url":             "https://ollama.com/download/windows",
            "label":           "Download Ollama for Windows",
            "instructions":    "Download and run the .exe installer (UAC prompt). Persephone will detect it once installed.",
            "requires_manual": True,
        }
    return {
        "method":          "unknown",
        "command":         "",
        "url":             "https://ollama.com/download",
        "label":           "Open Ollama download page",
        "instructions":    f"Unsupported OS: {sys_name}. Install manually from ollama.com/download.",
        "requires_manual": True,
    }


# ── Installation ─────────────────────────────────────────────────────────────
async def run_install_script() -> AsyncIterator[str]:
    """Stream lines from the install script. Yields strings for the UI."""
    info = get_install_info()
    if info["requires_manual"]:
        yield f"This platform requires a manual installer. Opening {info['url']} …"
        return

    cmd = info["command"]
    if not cmd:
        yield "No install command available for this platform."
        return

    yield f"$ {cmd}"

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield line.decode(errors="replace").rstrip()

    await proc.wait()
    if proc.returncode == 0:
        yield "✓ Installation complete."
    else:
        yield f"✗ Install script exited with code {proc.returncode}."


# ── Lifecycle ────────────────────────────────────────────────────────────────
def try_start_ollama() -> dict:
    """Best-effort launch. Returns {ok: bool, method: str, error?: str}."""
    if not is_installed():
        return {"ok": False, "method": "none", "error": "Ollama not installed"}

    sys_name = platform.system()
    try:
        if sys_name == "Darwin":
            # Prefer the .app (gives the menu-bar daemon)
            if Path("/Applications/Ollama.app").exists():
                subprocess.Popen(["open", "-a", "Ollama"], start_new_session=True)
                return {"ok": True, "method": "open-app"}
            # Fall back to CLI serve
            exe = find_executable() or "ollama"
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"ok": True, "method": "ollama serve"}

        if sys_name == "Linux":
            for unit_cmd in (
                ["systemctl", "--user", "start", "ollama"],
                ["systemctl", "start", "ollama"],
            ):
                try:
                    subprocess.check_call(
                        unit_cmd, timeout=5,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    return {"ok": True, "method": " ".join(unit_cmd)}
                except Exception:
                    continue
            exe = find_executable() or "ollama"
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"ok": True, "method": "ollama serve"}

        if sys_name == "Windows":
            exe = find_executable()
            if not exe:
                return {"ok": False, "method": "none", "error": "Ollama executable not found"}
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen([exe, "serve"], creationflags=CREATE_NO_WINDOW)
            return {"ok": True, "method": "ollama serve"}

    except Exception as exc:
        return {"ok": False, "method": "error", "error": str(exc)}

    return {"ok": False, "method": "unsupported"}


async def wait_until_running(timeout_s: float = 30.0, interval_s: float = 0.5) -> bool:
    """Poll until Ollama responds, up to `timeout_s` seconds."""
    waited = 0.0
    while waited < timeout_s:
        if await is_running():
            return True
        await asyncio.sleep(interval_s)
        waited += interval_s
    return False
