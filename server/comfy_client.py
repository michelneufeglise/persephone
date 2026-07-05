"""
Minimal ComfyUI HTTP client for the Reels pipeline.

ComfyUI is an external local process (default: http://127.0.0.1:8188). We do
NOT bundle or spawn it — the user installs it separately (see README §Reels).
This module exposes a single `generate()` coroutine that:

  1. Submits a text-to-image workflow via POST /prompt
  2. Polls GET /history/{prompt_id} until the run finishes (or times out)
  3. Downloads the output PNG via GET /view

The workflow is a minimal SD 1.5 / SDXL / Pony-compatible graph — six nodes,
no LoRA / ControlNet / IPAdapter. Anything more advanced belongs behind a
user-supplied workflow JSON via the `workflow_override` param.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("comfy_client")

COMFY_HOST     = os.getenv("COMFY_HOST", "http://127.0.0.1:8188")
DEFAULT_STEPS  = 20
DEFAULT_CFG    = 6.5
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHED  = "normal"
DEFAULT_NEG    = (
    "text, letters, watermark, signature, lowres, blurry, deformed, "
    "extra fingers, extra limbs, disfigured, ugly, jpeg artifacts"
)


def _default_workflow(
    prompt:      str,
    checkpoint:  str,
    width:       int,
    height:      int,
    steps:       int,
    seed:        int,
    cfg:         float,
    negative:    str,
) -> dict[str, Any]:
    """The minimal text2image graph in ComfyUI's node-JSON format.

    Node keys are stringified ints because ComfyUI's runtime treats the
    workflow as a dict where keys are node IDs — the exact IDs don't matter
    as long as `inputs` reference them consistently.
    """
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": DEFAULT_SAMPLER, "scheduler": DEFAULT_SCHED,
            "denoise": 1.0,
            "model":    ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {
            "ckpt_name": checkpoint,
        }},
        "5": {"class_type": "EmptyLatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1,
        }},
        "6": {"class_type": "CLIPTextEncode", "inputs": {
            "text": prompt, "clip": ["4", 1],
        }},
        "7": {"class_type": "CLIPTextEncode", "inputs": {
            "text": negative, "clip": ["4", 1],
        }},
        "8": {"class_type": "VAEDecode", "inputs": {
            "samples": ["3", 0], "vae": ["4", 2],
        }},
        "9": {"class_type": "SaveImage", "inputs": {
            "images": ["8", 0], "filename_prefix": "persephone_reel",
        }},
    }


async def get_status() -> dict[str, Any]:
    """Ping ComfyUI. Kept in sync with /api/reels/comfy/status in main.py."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{COMFY_HOST}/system_stats")
            if r.status_code != 200:
                return {"running": False, "error": f"HTTP {r.status_code}"}
            return {"running": True}
    except Exception as exc:
        return {"running": False, "error": str(exc)}


async def list_checkpoints() -> list[str]:
    """Return the list of installed checkpoint filenames, best-effort."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{COMFY_HOST}/object_info/CheckpointLoaderSimple")
            if r.status_code != 200:
                return []
            info = r.json()
            names = (info.get("CheckpointLoaderSimple", {})
                         .get("input", {})
                         .get("required", {})
                         .get("ckpt_name", [[]])[0]) or []
            return [str(n) for n in names]
    except Exception:
        return []


# ── Discovery + auto-start ────────────────────────────────────────────────────
_HOME = Path.home()
_COMMON_INSTALL_DIRS = [
    _HOME / "ComfyUI",
    _HOME / "comfyui",
    _HOME / "Documents"    / "ComfyUI",
    _HOME / "Applications" / "ComfyUI",
    _HOME / "ComfyUI_windows_portable" / "ComfyUI",
    Path("/opt") / "ComfyUI",
]

_PORT = 8188  # match COMFY_HOST default; kept in sync with main.py:reels_comfy_status


def find_install_dir(hint: str | None = None) -> Path | None:
    """Return a plausible ComfyUI install dir, or None. Checks (in order):
       1. explicit `hint` argument
       2. COMFYUI_DIR env var
       3. Common conventional install locations under ~
    Considers a directory valid when it contains a `main.py` at its root.
    """
    candidates: list[Path] = []
    if hint:
        candidates.append(Path(hint).expanduser())
    env = os.environ.get("COMFYUI_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(_COMMON_INSTALL_DIRS)
    for c in candidates:
        try:
            if (c / "main.py").is_file():
                return c
        except OSError:
            continue
    return None


def _python_for(install_dir: Path) -> str:
    """Prefer the venv python next to the install, fall back to system python3."""
    candidates: list[Path] = [
        install_dir / ".venv" / "bin" / "python",
        install_dir / "venv"  / "bin" / "python",
        install_dir / ".venv" / "Scripts" / "python.exe",
        install_dir / "venv"  / "Scripts" / "python.exe",
        # ComfyUI's Windows portable ships its own python next to the ComfyUI/ dir
        install_dir.parent / "python_embeded" / "python.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    # Fallback: system interpreter. Persephone's own Python already has enough
    # deps to run *most* Comfy startups on macOS/Linux dev machines.
    return sys.executable if sys.executable else ("python3" if os.name != "nt" else "python.exe")


async def is_port_up(timeout: float = 1.5) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{COMFY_HOST}/system_stats")
            return r.status_code == 200
    except Exception:
        return False


async def start(install_dir: Path, log_path: Path | None = None) -> dict[str, Any]:
    """
    Spawn ComfyUI *detached* from Persephone's process group so it survives
    a Persephone restart. Returns {started, pid, cmd, log}.

    If the port is already up, we skip spawning and just report the state.
    """
    if await is_port_up():
        return {"started": True, "pid": 0, "cmd": [], "log": "", "note": "already running"}

    py  = _python_for(install_dir)
    cmd = [py, "main.py", "--port", str(_PORT)]

    log_path = log_path or (install_dir / "persephone-comfy.log")
    logf = open(log_path, "ab", buffering=0)

    kwargs: dict[str, Any] = {
        "cwd":    str(install_dir),
        "stdout": logf,
        "stderr": subprocess.STDOUT,
        # Inherit env so venv python finds its packages, plus a hint for
        # any tool that wants to know we spawned it.
        "env":    {**os.environ, "PERSEPHONE_SPAWNED": "1"},
    }
    # Detach from the parent process group so a Persephone Ctrl+C won't
    # take ComfyUI down with it.
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    log.info("spawning ComfyUI: %s (cwd=%s)", " ".join(cmd), install_dir)
    proc = subprocess.Popen(cmd, **kwargs)

    # Best-effort record. This gets picked up by /api/reels/comfy/stop.
    _record_pid(proc.pid)

    return {
        "started": True,
        "pid":     proc.pid,
        "cmd":     cmd,
        "log":     str(log_path),
        "install": str(install_dir),
    }


# Small file so we know which PID we spawned (survives Persephone restart).
def _pid_file() -> Path:
    from paths import data_dir
    p = data_dir() / "reels" / "comfy.pid"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _record_pid(pid: int) -> None:
    try:
        _pid_file().write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _read_pid() -> int | None:
    try:
        return int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# ── Auto-install ───────────────────────────────────────────────────────────────
_COMFY_REPO_URL   = "https://github.com/comfyanonymous/ComfyUI.git"
_SDXL_BASE_URL    = "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
_SDXL_BASE_BYTES  = 6_938_040_856  # ~6.5 GB, used for a progress bar denominator


async def _read_stream_progress(
    stream: asyncio.StreamReader,
    emit,
    stage: str,
    parse_percent=None,
):
    """Read stdout/stderr line-by-line (also handles \\r-separated git progress),
    forward each line as a `{stage, message}` event, and if a `parse_percent`
    callback matches, also emit `{stage, progress}`.
    """
    buf = bytearray()
    while True:
        try:
            chunk = await stream.read(256)
        except Exception:
            break
        if not chunk:
            break
        for b in chunk:
            if b in (0x0A, 0x0D):  # LF or CR
                if buf:
                    line = buf.decode("utf-8", errors="replace").strip()
                    buf.clear()
                    if not line:
                        continue
                    await emit({"stage": stage, "message": line[:200]})
                    if parse_percent:
                        pct = parse_percent(line)
                        if pct is not None:
                            await emit({"stage": stage, "progress": pct})
            else:
                buf.append(b)
    if buf:
        line = buf.decode("utf-8", errors="replace").strip()
        if line:
            await emit({"stage": stage, "message": line[:200]})


def _git_progress(line: str) -> float | None:
    """git clone --progress writes 'Receiving objects: NN% (...)' — parse the NN."""
    if line.startswith("Receiving objects:") or line.startswith("Resolving deltas:"):
        import re
        m = re.search(r"(\d{1,3})%", line)
        if m:
            pct = int(m.group(1)) / 100.0
            # Receiving is ~85% of the perceived clone time; deltas the rest.
            base = 0.0 if line.startswith("Receiving") else 0.85
            span = 0.85 if line.startswith("Receiving") else 0.15
            return base + pct * span
    return None


async def install(
    dest:                Path,
    on_progress,
    download_checkpoint: bool = True,
) -> Path:
    """
    End-to-end ComfyUI install with streamed progress:
      1. git clone into `dest`
      2. python3 -m venv .venv
      3. .venv/bin/pip install -r requirements.txt
      4. (optional) download SDXL Base 1.0 into models/checkpoints/
    Yields progress via `on_progress({stage, message?, progress?})`.
    Returns the install directory on success. Raises on failure.
    """
    dest = dest.expanduser().resolve()

    # ── 1. Clone ──
    if dest.exists() and any(dest.iterdir()) and not (dest / "main.py").is_file():
        raise RuntimeError(f"Refuse to clone into non-empty dir {dest} (no main.py present).")
    if not (dest / "main.py").is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        await on_progress({"stage": "clone", "message": f"Cloning ComfyUI into {dest}…", "progress": 0.0})
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--progress", "--depth", "1", _COMFY_REPO_URL, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # git writes progress to stderr; stdout is usually empty
        await asyncio.gather(
            _read_stream_progress(proc.stderr, on_progress, "clone", _git_progress),
            _read_stream_progress(proc.stdout, on_progress, "clone", None),
        )
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"git clone exited {rc}")
        await on_progress({"stage": "clone", "progress": 1.0, "message": "clone done"})
    else:
        await on_progress({"stage": "clone", "progress": 1.0, "message": "ComfyUI already present — skipping clone"})

    # ── 2. venv ──
    venv_python = dest / ".venv" / "bin" / "python"
    if os.name == "nt":
        venv_python = dest / ".venv" / "Scripts" / "python.exe"

    if not venv_python.is_file():
        await on_progress({"stage": "venv", "message": "Creating .venv…", "progress": 0.0})
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", str(dest / ".venv"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"venv creation failed: {err.decode(errors='replace')[:300]}")
    await on_progress({"stage": "venv", "progress": 1.0, "message": "venv ready"})

    # ── 3. Requirements ──
    req_file = dest / "requirements.txt"
    if req_file.is_file():
        await on_progress({"stage": "deps", "message": "Installing Python dependencies (may take a few minutes)…", "progress": 0.05})
        proc = await asyncio.create_subprocess_exec(
            str(venv_python), "-m", "pip", "install", "--upgrade",
            "--progress-bar", "off", "--disable-pip-version-check",
            "-r", str(req_file),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(dest),
        )
        # Rough progress: increment as pip announces each Collecting / Installing line.
        seen_packages = 0
        buf = bytearray()

        async def emit_lines():
            nonlocal buf, seen_packages
            while True:
                try:
                    chunk = await proc.stdout.read(256)
                except Exception:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
                while b"\n" in buf:
                    line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    await on_progress({"stage": "deps", "message": text[:200]})
                    low = text.lower()
                    if low.startswith("collecting ") or low.startswith("downloading "):
                        seen_packages += 1
                        # ComfyUI's requirements pulls ~35 packages incl. torch.
                        pct = min(0.05 + 0.9 * (seen_packages / 40.0), 0.95)
                        await on_progress({"stage": "deps", "progress": pct})
        await emit_lines()
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"pip install exited {rc}")
        await on_progress({"stage": "deps", "progress": 1.0, "message": "dependencies installed"})
    else:
        await on_progress({"stage": "deps", "progress": 1.0, "message": "no requirements.txt found — skipping"})

    # ── 4. Optional starter checkpoint ──
    if download_checkpoint:
        ckpt_dir  = dest / "models" / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt_path = ckpt_dir / "sd_xl_base_1.0.safetensors"
        if ckpt_path.exists() and ckpt_path.stat().st_size > 1_000_000_000:
            await on_progress({"stage": "checkpoint", "progress": 1.0, "message": "SDXL Base already present — skipping"})
        else:
            await on_progress({"stage": "checkpoint", "message": "Downloading SDXL Base 1.0 (~6.5 GB)…", "progress": 0.0})
            tmp = ckpt_path.with_suffix(".safetensors.part")
            downloaded = 0
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", _SDXL_BASE_URL) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"checkpoint fetch HTTP {r.status_code}")
                    total = int(r.headers.get("content-length") or _SDXL_BASE_BYTES)
                    with open(tmp, "wb") as f:
                        last_pct = 0.0
                        async for chunk in r.aiter_bytes(1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            pct = downloaded / total
                            if pct - last_pct > 0.01 or pct >= 1.0:
                                last_pct = pct
                                await on_progress({
                                    "stage": "checkpoint",
                                    "progress": pct,
                                    "downloaded_bytes": downloaded,
                                    "total_bytes":       total,
                                })
            tmp.rename(ckpt_path)
            await on_progress({"stage": "checkpoint", "progress": 1.0, "message": "SDXL Base ready"})
    else:
        await on_progress({"stage": "checkpoint", "progress": 1.0, "message": "skipped (no checkpoint requested)"})

    await on_progress({"stage": "done", "install": str(dest)})
    return dest


def stop_if_ours() -> dict[str, Any]:
    """Gracefully terminate the ComfyUI we spawned (if any). Never touches
    a ComfyUI the user started by hand — we only kill the recorded PID.
    """
    pid = _read_pid()
    if not pid:
        return {"stopped": False, "reason": "no recorded pid"}
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _pid_file().unlink(missing_ok=True)
        return {"stopped": False, "reason": "pid not found (already gone)"}
    except Exception as exc:
        return {"stopped": False, "reason": str(exc)}
    _pid_file().unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


async def generate(
    prompt:      str,
    *,
    checkpoint:  str,
    width:       int   = 1024,
    height:      int   = 1024,
    steps:       int   = DEFAULT_STEPS,
    seed:        int   = -1,
    cfg:         float = DEFAULT_CFG,
    negative:    str   = DEFAULT_NEG,
    timeout_s:   float = 180.0,
    workflow_override: dict[str, Any] | None = None,
) -> bytes:
    """Run one text-to-image job. Returns the PNG bytes.

    Raises RuntimeError on ComfyUI errors or timeouts. Caller should log the
    prompt so a failed run is diagnosable without needing the workflow JSON.
    """
    if seed < 0:
        # Any int in [0, 2^63) is valid — ComfyUI accepts negative as
        # "randomize" but the API is more predictable if we pick server-side.
        seed = int.from_bytes(os.urandom(6), "big")

    workflow = workflow_override or _default_workflow(
        prompt=prompt, checkpoint=checkpoint,
        width=width, height=height, steps=steps,
        seed=seed, cfg=cfg, negative=negative,
    )
    client_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=timeout_s) as http:
        # ── Submit ──
        r = await http.post(f"{COMFY_HOST}/prompt", json={
            "prompt":    workflow,
            "client_id": client_id,
        })
        if r.status_code != 200:
            raise RuntimeError(f"comfy /prompt HTTP {r.status_code}: {r.text[:400]}")
        payload    = r.json()
        prompt_id  = payload.get("prompt_id") or ""
        node_errors = payload.get("node_errors") or {}
        if not prompt_id:
            raise RuntimeError(f"comfy /prompt returned no prompt_id: {payload}")
        if node_errors:
            raise RuntimeError(f"comfy node errors: {node_errors}")

        # ── Poll history (0.4s cadence) ──
        deadline = asyncio.get_running_loop().time() + timeout_s
        images:  list[dict] = []
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError(f"comfy timeout after {timeout_s:.0f}s (prompt_id={prompt_id})")
            hr = await http.get(f"{COMFY_HOST}/history/{prompt_id}")
            if hr.status_code == 200:
                hist = hr.json() or {}
                entry = hist.get(prompt_id)
                if entry:
                    status = (entry.get("status") or {}).get("status_str", "")
                    if status == "error":
                        raise RuntimeError(f"comfy run failed: {entry.get('status')}")
                    outputs = entry.get("outputs") or {}
                    for node_out in outputs.values():
                        for img in node_out.get("images") or []:
                            if img.get("type") in (None, "output", "temp"):
                                images.append(img)
                    if images:
                        break
            await asyncio.sleep(0.4)

        # ── Download the first image ──
        img_meta = images[0]
        params = {
            "filename":  img_meta.get("filename", ""),
            "subfolder": img_meta.get("subfolder", ""),
            "type":      img_meta.get("type", "output"),
        }
        dl = await http.get(f"{COMFY_HOST}/view", params=params)
        if dl.status_code != 200:
            raise RuntimeError(f"comfy /view HTTP {dl.status_code}: {dl.text[:200]}")
        return dl.content
