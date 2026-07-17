"""
Install & lifecycle for the AbletonOSC control-surface script.

AbletonOSC (https://github.com/ideoforms/AbletonOSC, MIT-licensed) is a Python
Live Remote Script that exposes the Live Object Model over OSC — how Persephone
talks to Ableton at runtime.

Install lives at:
    ~/Music/Ableton/User Library/Remote Scripts/AbletonOSC/
(macOS; Windows analogue in ableton_detect.user_library_remote_scripts()).

We install by shallow-cloning the upstream repo. After the copy, the user must
enable AbletonOSC once in Live → Preferences → Link Tempo & MIDI → Control
Surface (the UI's Phase-1 status card explains this and links a screenshot).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from ableton_detect import user_library_remote_scripts, legacy_remote_scripts_dirs

# Persephone-authored patches to layer on top of upstream AbletonOSC.
_PATCH_SRC_DIR = Path(__file__).parent / "ableton_patches"

log = logging.getLogger("ableton_bridge")

# Where the upstream lives. `--depth 1` keeps the clone cheap.
_ABLETONOSC_REPO = "https://github.com/ideoforms/AbletonOSC.git"
_INSTALL_DIRNAME = "AbletonOSC"

# Files whose presence we treat as "install healthy". `__init__.py` is the
# script's entry point; Live loads it when the control surface is enabled.
_HEALTH_CHECK_FILES = ("__init__.py", "abletonosc")


def install_dir() -> Path:
    """The primary install location — the modern User Library Remote Scripts folder."""
    return user_library_remote_scripts() / _INSTALL_DIRNAME


def install_dirs_all() -> list[Path]:
    """
    Every location we should mirror AbletonOSC to. Includes the modern
    User Library folder + every per-version legacy folder Live is using.
    Live 12 minor versions can show scripts from ONLY the legacy folder,
    so mirroring both makes the install robust across editions.
    """
    dirs = [install_dir()]
    for legacy in legacy_remote_scripts_dirs():
        dirs.append(legacy / _INSTALL_DIRNAME)
    return dirs


def is_installed() -> bool:
    """True if AT LEAST ONE of the expected install locations is populated
    and passes the health check. Live only needs one to see AbletonOSC."""
    for d in install_dirs_all():
        if d.is_dir() and all((d / f).exists() for f in _HEALTH_CHECK_FILES):
            return True
    return False


def uninstall() -> bool:
    """Remove every AbletonOSC install location. Returns True if any dir was removed."""
    removed = False
    for d in install_dirs_all():
        if d.exists():
            shutil.rmtree(d)
            log.info("removed AbletonOSC install at %s", d)
            removed = True
    return removed


ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]


async def install(on_progress: ProgressCb) -> Path:
    """
    Shallow-clone AbletonOSC into the User Library remote-scripts folder.

    Streams progress via `on_progress({stage, message?, progress?})`. The
    stages are:
      "prep"    — creating the target directory
      "clone"   — git clone --depth 1 (progress parsed from stderr)
      "verify"  — sanity-check the clone
      "done"    — installation complete
      "error"   — anything above threw; message carries the details
    """
    dest = install_dir()

    await on_progress({"stage": "prep", "message": f"Preparing {dest}"})
    dest.parent.mkdir(parents=True, exist_ok=True)

    # If a previous install exists, remove it first so `git clone` succeeds.
    # (We don't try to `git pull` — reinstall is idempotent and simpler.)
    if dest.exists():
        try:
            shutil.rmtree(dest)
        except OSError as exc:
            raise RuntimeError(f"could not remove existing {dest}: {exc}")

    await on_progress({"stage": "clone", "message": f"Cloning {_ABLETONOSC_REPO}", "progress": 0.0})
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", "--progress",
        _ABLETONOSC_REPO, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # git writes progress to stderr as `Receiving objects: NN% (X/Y), Z MiB | ...`
    await asyncio.gather(
        _stream_git_progress(proc.stderr, on_progress),
        _drain_stream(proc.stdout, on_progress),
    )
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"git clone exited {rc}")

    await on_progress({"stage": "verify", "message": "Verifying primary install"})
    if not (dest / "__init__.py").is_file() or not (dest / "abletonosc").is_dir():
        raise RuntimeError(
            f"clone finished but {dest} is missing expected files. "
            "The upstream layout may have changed."
        )

    # Persephone patches — apply BEFORE mirroring so the legacy copies get
    # the same patched files.
    await on_progress({"stage": "patch", "message": "Applying Persephone patches (browser support)"})
    for action in _apply_patches(dest):
        await on_progress({"stage": "patch", "message": action})

    # Mirror to any legacy per-Live-version Remote Scripts folders. Some Live
    # 12 minor versions ONLY see user scripts here — this is how we make
    # "AbletonOSC doesn't show up in Live's prefs" stop happening.
    mirrored: list[str] = []
    for extra in install_dirs_all()[1:]:  # skip [0] which is `dest` itself
        try:
            if extra.exists():
                shutil.rmtree(extra)
            extra.parent.mkdir(parents=True, exist_ok=True)
            await on_progress({"stage": "mirror", "message": f"Mirroring to {extra}"})
            shutil.copytree(dest, extra, ignore=shutil.ignore_patterns(".git", ".github"))
            mirrored.append(str(extra))
        except OSError as exc:
            # Non-fatal: primary install still works. Log and continue.
            log.warning("mirror to %s failed: %s", extra, exc)
            await on_progress({"stage": "mirror", "message": f"[warn] {extra}: {exc}"})

    log.info("AbletonOSC installed at %s (mirrored to %d extra location(s))", dest, len(mirrored))
    await on_progress({
        "stage":      "done",
        "message":    "AbletonOSC installed",
        "install_dir": str(dest),
        "mirrored":   mirrored,
    })
    return dest


# ── Stream helpers ────────────────────────────────────────────────────────────
async def _stream_git_progress(stream: asyncio.StreamReader, emit: ProgressCb) -> None:
    """Parse `Receiving objects: NN%` from git's stderr, emit progress + messages."""
    import re
    percent_re = re.compile(r"(\d{1,3})%")
    buf = bytearray()
    while True:
        try:
            chunk = await stream.read(256)
        except Exception:
            break
        if not chunk:
            break
        for b in chunk:
            # git uses \r for progress overwrites — treat them like line breaks.
            if b in (0x0A, 0x0D):
                if buf:
                    line = buf.decode("utf-8", errors="replace").strip()
                    buf.clear()
                    if not line:
                        continue
                    await emit({"stage": "clone", "message": line[:200]})
                    m = percent_re.search(line)
                    if m and (line.startswith("Receiving") or line.startswith("Resolving")):
                        pct = int(m.group(1)) / 100.0
                        base = 0.0 if line.startswith("Receiving") else 0.85
                        span = 0.85 if line.startswith("Receiving") else 0.15
                        await emit({"stage": "clone", "progress": base + pct * span})
            else:
                buf.append(b)
    if buf:
        line = buf.decode("utf-8", errors="replace").strip()
        if line:
            await emit({"stage": "clone", "message": line[:200]})


async def _drain_stream(stream: asyncio.StreamReader, emit: ProgressCb) -> None:
    """Read whatever git puts on stdout — usually nothing, but avoid backpressure."""
    while True:
        try:
            chunk = await stream.read(1024)
        except Exception:
            break
        if not chunk:
            break
        # Not important enough to emit as a message; just drain.


# ── Persephone patches (Phase 3.5 — Browser support / auto-instruments) ──────
def _apply_patches(install_root: Path) -> list[str]:
    """
    Layer Persephone-authored files/edits on top of the cloned upstream at
    `install_root` (i.e. the AbletonOSC/ folder). Returns a list of
    human-readable actions taken (for the SSE stream).

    Idempotent: running it twice is a no-op after the first application.
    """
    actions: list[str] = []
    mod_dir  = install_root / "abletonosc"
    if not mod_dir.is_dir():
        actions.append(f"[warn] {mod_dir} missing — skipping patch")
        return actions

    # 1. Drop browser.py into the module directory (overwrites any prior copy).
    src = _PATCH_SRC_DIR / "browser.py"
    dst = mod_dir / "browser.py"
    if src.is_file():
        shutil.copy2(src, dst)
        actions.append(f"copied browser.py → {dst.name}")
    else:
        actions.append("[warn] server/ableton_patches/browser.py not found")

    # 2. Patch abletonosc/__init__.py so it imports BrowserHandler.
    init_py = mod_dir / "__init__.py"
    if init_py.is_file():
        text = init_py.read_text(encoding="utf-8")
        marker = "from .browser import BrowserHandler"
        if marker not in text:
            # Insert just before the last non-import block or at end of imports.
            insertion = "\n# --- Persephone patch (auto-instruments) ---\n" + marker + "\n"
            text = text.rstrip() + insertion
            init_py.write_text(text, encoding="utf-8")
            actions.append("patched abletonosc/__init__.py")
        else:
            actions.append("__init__.py already patched")

    # 3. Patch manager.py to register BrowserHandler alongside ViewHandler.
    mgr_py = install_root / "manager.py"
    if mgr_py.is_file():
        text = mgr_py.read_text(encoding="utf-8")
        marker = "abletonosc.BrowserHandler(self)"
        if marker not in text:
            # Anchor after ViewHandler which is the last entry in the stock file.
            anchor = "abletonosc.ViewHandler(self),"
            patched = f"{anchor}\n                # --- Persephone patch (auto-instruments) ---\n                {marker},"
            if anchor in text:
                text = text.replace(anchor, patched, 1)
                mgr_py.write_text(text, encoding="utf-8")
                actions.append("patched manager.py")
            else:
                actions.append("[warn] manager.py anchor 'ViewHandler(self),' not found — upstream layout may have changed")
        else:
            actions.append("manager.py already patched")

    return actions


# ── Post-install user-facing instructions ────────────────────────────────────
_POST_INSTALL_NOTES = (
    "Almost there — one manual step Live needs from you:\n"
    "\n"
    "1. **Fully quit Ableton Live** (Cmd+Q — not just close the window).\n"
    "2. **Re-open Live.** Remote Scripts are only scanned on startup.\n"
    "3. Preferences  ▸  Link, Tempo & MIDI.\n"
    "4. Under 'Control Surface', pick an empty slot → choose 'AbletonOSC'\n"
    "   (Input and Output can stay 'None').\n"
    "\n"
    "If 'AbletonOSC' isn't in the dropdown after step 3:\n"
    "  • Confirm you fully quit Live before reopening (not just closed).\n"
    "  • Try Preferences ▸ Library and check that 'Location of User Library'\n"
    "    points at ~/Music/Ableton/User Library. If it points elsewhere,\n"
    "    update it there, quit, reopen.\n"
    "\n"
    "Persephone auto-probes every few seconds — the status chip will flip\n"
    "green once AbletonOSC is loaded."
)


def post_install_instructions() -> str:
    return _POST_INSTALL_NOTES
