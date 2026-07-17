"""
Detect Ableton Live installations on the host machine.

Public API:
  find_installs()  -> list[dict]  # every Ableton Live install found, with edition + version
  best_install()   -> dict | None # non-trial, non-intro if possible, newest otherwise
  is_running()     -> bool        # is any Ableton Live process currently up?

macOS-first (matches Persephone's primary platform). Windows/Linux hooks are
stubbed so the API surface stays uniform; broader coverage lands with Phase 2.
"""

from __future__ import annotations

import logging
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("ableton_detect")

# Editions the app name might contain, ranked worst → best for `best_install`.
_EDITION_RANK = {"Trial": 0, "Intro": 1, "Lite": 1, "Standard": 2, "Suite": 3, "": 1}


def _parse_edition_from_bundle_name(name: str) -> str:
    """Given "Ableton Live 12 Intro" → "Intro". Falls back to "" if unknown."""
    for e in ("Suite", "Standard", "Intro", "Trial", "Lite"):
        if e in name:
            return e
    return ""


def _read_plist(plist_path: Path) -> dict[str, Any]:
    try:
        with plist_path.open("rb") as f:
            return plistlib.load(f)
    except Exception as exc:
        log.debug("failed to read %s: %s", plist_path, exc)
        return {}


def _macos_candidates() -> list[Path]:
    """All .app directories under standard install locations that match Ableton."""
    roots = [
        Path("/Applications"),
        Path.home() / "Applications",
    ]
    hits: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        for child in r.iterdir():
            # Skip macOS auto-update staging dirs (they start with a dot) and
            # anything without "Ableton Live" in the display name.
            if child.name.startswith("."):
                continue
            if child.is_dir() and child.suffix == ".app" and "Ableton Live" in child.name:
                hits.append(child)
    return sorted(hits)


def _describe_macos(app_path: Path) -> dict[str, Any]:
    plist = _read_plist(app_path / "Contents" / "Info.plist")
    bundle_name  = plist.get("CFBundleName") or ""
    edition      = _parse_edition_from_bundle_name(app_path.name) or _parse_edition_from_bundle_name(bundle_name)
    version_raw  = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion") or ""
    m            = re.match(r"^(\d+(?:\.\d+)*)", version_raw)
    version      = m.group(1) if m else version_raw
    major        = int(version.split(".", 1)[0]) if version[:1].isdigit() else 0
    return {
        "path":            str(app_path),
        "name":            app_path.name.replace(".app", ""),
        "edition":         edition or "Unknown",
        "is_trial":        "Trial" in app_path.name,
        "version":         version,
        "version_major":   major,
        "version_full":    version_raw,
        "platform":        "darwin",
    }


def find_installs() -> list[dict[str, Any]]:
    """Return every Ableton Live install we can see, newest-major-version first."""
    if sys.platform == "darwin":
        rows = [_describe_macos(p) for p in _macos_candidates()]
    else:
        # Placeholder for Phase 2 — Windows scans %ProgramFiles%\Ableton\Live *
        rows = []

    # Sort: highest major, then edition rank, then non-trial before trial.
    rows.sort(key=lambda r: (
        r.get("version_major", 0),
        _EDITION_RANK.get(r.get("edition", ""), 1),
        0 if r.get("is_trial") else 1,
    ), reverse=True)
    return rows


def best_install() -> dict[str, Any] | None:
    """The install we'd default to using — highest edition/version, non-trial if possible."""
    rows = find_installs()
    if not rows:
        return None
    # Prefer any non-trial install first.
    for r in rows:
        if not r.get("is_trial"):
            return r
    return rows[0]


def is_running() -> bool:
    """Is *any* Ableton Live process currently up? Fast, low-privilege check."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["pgrep", "-if", "Ableton Live"],
                capture_output=True, text=True, timeout=2,
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    return False


def user_library_remote_scripts() -> Path:
    """Path to the 'User Library / Remote Scripts' folder, where control-surface
    scripts (like AbletonOSC) live. Created if missing."""
    if sys.platform == "darwin":
        p = Path.home() / "Music" / "Ableton" / "User Library" / "Remote Scripts"
    else:
        p = Path.home() / "Documents" / "Ableton" / "User Library" / "Remote Scripts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def legacy_remote_scripts_dirs() -> list[Path]:
    """Live 11+ ALSO scans a per-version folder under Preferences (macOS) or
    APPDATA (Windows). Some Live 12 minor versions surface user scripts from
    ONLY this folder — so we mirror our install to every version we find here
    to avoid the "AbletonOSC doesn't show up" trap.

    Returns the folders as they exist on disk; empty if none.
    """
    if sys.platform != "darwin":
        # Windows analogue lands in Phase 2.
        return []
    root = Path.home() / "Library" / "Preferences" / "Ableton"
    if not root.exists():
        return []
    dirs: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        # e.g. "Live 12.3.2", "Live 11.3.20"
        if not child.name.startswith("Live "):
            continue
        candidate = child / "User Remote Scripts"
        # Only offer folders that already exist — creating one Live isn't
        # actively using would be silently wasteful.
        if candidate.exists():
            dirs.append(candidate)
    return dirs
