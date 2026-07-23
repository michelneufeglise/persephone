"""
Detect + configure Ollama's parallelism knobs.

Persephone's tab strip runs concurrent chat streams in different tabs.
Ollama serialises requests to a single model unless `OLLAMA_NUM_PARALLEL`
is set, and serialises requests across models unless
`OLLAMA_MAX_LOADED_MODELS` is > 1. Both default to 1 on many installs.

This module reads the current live values from Ollama's `/api/version` +
its process environment, and (on macOS) can set them via
`launchctl setenv` — which persists across Ollama restarts. On Linux the
right approach is a systemd drop-in; on Windows it's `setx`. All three
are implemented.

Public API:
    read_config()       → { num_parallel, max_loaded, source, ok, hint }
    apply_config(np, ml) → { ok, restarted, error }
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

log = logging.getLogger("ollama_parallel")


# Recommended targets when the user asks to enable parallelism. Kept modest
# so a 16GB Mac doesn't try to load 4 huge models at once.
_DEFAULT_NUM_PARALLEL   = 4     # 4 concurrent requests to a single model
_DEFAULT_MAX_LOADED     = 2     # up to 2 models resident at once


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=timeout,
        ).strip()
    except Exception:
        return ""


def _ollama_process_env() -> dict[str, str]:
    """
    Read Ollama server's process env for the parallelism vars.

    macOS: parse `ps eww` output for the ollama-server process. Slow-ish
    but reliable — Ollama's HTTP API doesn't expose these values.
    Other platforms: fall back to os.environ (works when Persephone was
    started by the same shell that started Ollama).
    """
    sys_name = platform.system()
    env: dict[str, str] = {}
    # First try the current process env — cheap and often correct.
    for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
        v = os.environ.get(k)
        if v is not None:
            env[k] = v

    if sys_name == "Darwin":
        # `launchctl getenv` returns the value set via `launchctl setenv`,
        # which is what Ollama.app reads on start.
        for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
            v = _run(["launchctl", "getenv", k])
            if v:
                env[k] = v
        # Also try ps eww on the ollama server process for absolute truth.
        try:
            out = subprocess.check_output(
                ["ps", "eww", "-o", "pid,command", "-A"],
                stderr=subprocess.DEVNULL, text=True, timeout=6,
            )
            for line in out.splitlines():
                if "ollama" in line.lower() and "serve" in line.lower():
                    # e.g.  1234 /Applications/Ollama.app/Contents/... OLLAMA_NUM_PARALLEL=4 …
                    for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
                        needle = f" {k}="
                        idx = line.find(needle)
                        if idx > 0:
                            tail = line[idx + len(needle):]
                            end  = tail.find(" ")
                            env[k] = tail[:end] if end > 0 else tail
                    break
        except Exception:
            pass
    elif sys_name == "Linux":
        # systemd drop-in file at /etc/systemd/system/ollama.service.d/override.conf
        override = Path("/etc/systemd/system/ollama.service.d/override.conf")
        if override.exists():
            try:
                for line in override.read_text().splitlines():
                    line = line.strip()
                    for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
                        needle = f'Environment="{k}='
                        if line.startswith(needle):
                            val = line[len(needle):-1]   # drop trailing "
                            env[k] = val
            except Exception:
                pass
    elif sys_name == "Windows":
        # HKCU\Environment (user-level setx). Best-effort — a proper impl
        # would use winreg, but reading via reg query is portable.
        for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
            out = _run(["reg", "query", r"HKCU\Environment", "/v", k])
            for line in out.splitlines():
                if k in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        env[k] = parts[-1]
    return env


def _parse_int(s: str | None, default: int = 0) -> int:
    if not s:
        return default
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def read_config() -> dict[str, Any]:
    """
    Current live Ollama parallelism values + whether we consider them OK
    for parallel-tab use.
    """
    env = _ollama_process_env()
    num_parallel = _parse_int(env.get("OLLAMA_NUM_PARALLEL"), 0)
    max_loaded   = _parse_int(env.get("OLLAMA_MAX_LOADED_MODELS"), 0)
    ok = num_parallel >= 2 and max_loaded >= 2
    hint = ""
    if not ok:
        if num_parallel < 2 and max_loaded < 2:
            hint = ("Parallel tabs will queue at Ollama — both concurrency knobs "
                    "are unset. Click 'Enable parallel tabs' below to fix.")
        elif num_parallel < 2:
            hint = (f"OLLAMA_NUM_PARALLEL={num_parallel or 'unset'} means concurrent requests "
                    "to the same model will queue. Recommended: 4.")
        elif max_loaded < 2:
            hint = (f"OLLAMA_MAX_LOADED_MODELS={max_loaded or 'unset'} means Ollama can only "
                    "hold one model in memory — different-model tabs will thrash. Recommended: 2.")
    return {
        "num_parallel":       num_parallel,
        "max_loaded":         max_loaded,
        "num_parallel_target": _DEFAULT_NUM_PARALLEL,
        "max_loaded_target":   _DEFAULT_MAX_LOADED,
        "os":                 platform.system(),
        "ok":                 ok,
        "hint":               hint,
    }


async def _restart_ollama_macos() -> tuple[bool, str]:
    """
    Restart Ollama on macOS so it picks up the new env. We look for
    Ollama.app first (menubar-managed daemon), fall back to `ollama serve`
    if the user runs the CLI daemon.
    """
    # Bounce the menubar app — safe, no unsaved state.
    app = Path("/Applications/Ollama.app")
    if app.exists():
        # Kill any running instance + relaunch. Sleep briefly so the daemon
        # rebinds :11434 before we probe it.
        subprocess.call(["osascript", "-e", 'tell application "Ollama" to quit'],
                        stderr=subprocess.DEVNULL, timeout=6)
        await asyncio.sleep(2.5)
        subprocess.Popen(["open", "-a", str(app)], stderr=subprocess.DEVNULL)
        await asyncio.sleep(2.5)
        return (True, "restarted via Ollama.app")

    # No app — check if `ollama serve` is running as a plain process.
    pid_out = _run(["pgrep", "-f", "ollama serve"])
    if pid_out:
        for pid in pid_out.split():
            try:
                subprocess.call(["kill", "-TERM", pid], stderr=subprocess.DEVNULL)
            except Exception:
                pass
        await asyncio.sleep(1.5)
        # Try to restart from PATH.
        ollama_bin = shutil.which("ollama")
        if ollama_bin:
            subprocess.Popen(
                [ollama_bin, "serve"],
                stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                start_new_session=True,
            )
            await asyncio.sleep(2.0)
            return (True, "restarted ollama serve")
    return (False, "no Ollama process found to restart — please restart Ollama manually")


async def apply_config(num_parallel: int, max_loaded: int) -> dict[str, Any]:
    """
    Persist parallelism env vars for Ollama + restart Ollama so they take
    effect. Behaviour differs per platform:

    * macOS   — `launchctl setenv` (persists across boots for GUI apps).
    * Linux   — writes /etc/systemd/system/ollama.service.d/override.conf.
                Requires sudo; if we can't sudo, we return instructions.
    * Windows — `setx` at HKCU (persists across boots for user).

    Returns: { ok, restarted, error, hint }
    """
    if num_parallel < 1 or max_loaded < 1:
        return {"ok": False, "error": "values must be >= 1"}

    sys_name = platform.system()

    if sys_name == "Darwin":
        # launchctl setenv is the canonical way to make GUI-launched apps
        # see an env var. Doesn't need sudo for user session.
        for k, v in (("OLLAMA_NUM_PARALLEL", num_parallel),
                     ("OLLAMA_MAX_LOADED_MODELS", max_loaded)):
            rc = subprocess.call(["launchctl", "setenv", k, str(v)])
            if rc != 0:
                return {"ok": False, "error": f"launchctl setenv {k} failed (rc={rc})"}
        restarted, msg = await _restart_ollama_macos()
        return {"ok": True, "restarted": restarted, "hint": msg}

    if sys_name == "Linux":
        override = Path("/etc/systemd/system/ollama.service.d/override.conf")
        body = textwrap.dedent(f"""\
            [Service]
            Environment="OLLAMA_NUM_PARALLEL={num_parallel}"
            Environment="OLLAMA_MAX_LOADED_MODELS={max_loaded}"
        """)
        try:
            # Try direct write first (in case we're root). If that fails,
            # fall back to sudo — will prompt in the terminal if elevated
            # Persephone was launched from there.
            try:
                override.parent.mkdir(parents=True, exist_ok=True)
                override.write_text(body)
            except PermissionError:
                # Prepare the file in /tmp and sudo-move it.
                tmp = Path("/tmp/persephone-ollama-override.conf")
                tmp.write_text(body)
                rc = subprocess.call([
                    "sudo", "-n",
                    "install", "-D", "-m", "0644",
                    str(tmp), str(override),
                ])
                if rc != 0:
                    return {"ok": False,
                            "error": "cannot write /etc/systemd/system/ollama.service.d/ — "
                                     "run Persephone with sudo, OR create this file manually:\n"
                                     f"{override}\n\nContents:\n{body}"}
            # Reload systemd + restart Ollama.
            subprocess.call(["sudo", "-n", "systemctl", "daemon-reload"],
                            stderr=subprocess.DEVNULL)
            rc = subprocess.call(["sudo", "-n", "systemctl", "restart", "ollama"],
                                 stderr=subprocess.DEVNULL)
            return {"ok": True, "restarted": (rc == 0),
                    "hint": ("systemd drop-in written; if the restart failed you can "
                             "run `sudo systemctl restart ollama` manually.")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if sys_name == "Windows":
        # `setx` writes to HKCU\Environment, persisting for the user across
        # boots. Ollama on Windows reads its env at process start, so we
        # also try to bounce it via `Stop-Process` + relaunch.
        for k, v in (("OLLAMA_NUM_PARALLEL", num_parallel),
                     ("OLLAMA_MAX_LOADED_MODELS", max_loaded)):
            rc = subprocess.call(["setx", k, str(v)], stderr=subprocess.DEVNULL)
            if rc != 0:
                return {"ok": False, "error": f"setx {k} failed (rc={rc})"}
        # Restart Ollama service if present.
        subprocess.call(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force"],
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.5)
        # Relaunch — Ollama tray app usually auto-restarts. Best effort.
        exe = shutil.which("ollama")
        if exe:
            subprocess.Popen(
                [exe, "serve"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                creationflags=0x00000008,  # DETACHED_PROCESS
            )
        return {"ok": True, "restarted": bool(exe),
                "hint": "Please close and reopen Persephone if the change doesn't take effect."}

    return {"ok": False, "error": f"unsupported OS: {sys_name}"}
