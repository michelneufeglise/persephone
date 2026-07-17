"""
Persistent song library for the Ableton composer.

One JSON file per saved song under `data_dir()/ableton/songs/`. Each file
carries a SongSpec plus lightweight metadata (name, timestamps) so we don't
have to open the full spec just to render the library list.

Public surface:
    list_songs()          → list[dict]  (metadata only, cheap)
    save_song(name, spec) → dict        (full record)
    load_song(song_id)    → dict | None (full record with spec)
    delete_song(song_id)  → bool
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from paths import data_dir


def _songs_dir() -> Path:
    p = data_dir() / "ableton" / "songs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slugify(name: str) -> str:
    """
    Produce a filesystem-safe id from a user-given name. Falls back to a
    short random suffix so two songs named "Song" don't collide.
    """
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "song").strip().lower()).strip("-")
    base = base or "song"
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _song_path(song_id: str) -> Path:
    # Guard against directory traversal — song_id must be a plain slug.
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "", song_id)
    return _songs_dir() / f"{safe}.json"


def _meta_of(record: dict[str, Any]) -> dict[str, Any]:
    """Extract just the fields the library UI needs — cheap to serialise."""
    spec = record.get("spec") or {}
    tracks = spec.get("tracks") or []
    return {
        "id":         record.get("id", ""),
        "name":       record.get("name", "Untitled"),
        "created_at": record.get("created_at", 0),
        "updated_at": record.get("updated_at", 0),
        "bpm":        spec.get("bpm"),
        "key":        spec.get("key"),
        "genre":      spec.get("genre") or "",
        "n_tracks":   len(tracks),
        "n_sections": len(spec.get("sections") or []),
    }


def list_songs() -> list[dict[str, Any]]:
    """
    Return metadata for every saved song, newest first. Silently skips files
    that fail to parse (a partial write shouldn't take down the library).
    """
    items: list[dict[str, Any]] = []
    for path in _songs_dir().glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as f:
                record = json.load(f)
            items.append(_meta_of(record))
        except (OSError, ValueError):
            continue
    items.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
    return items


def save_song(name: str, spec: dict[str, Any], song_id: str = "") -> dict[str, Any]:
    """
    Persist `spec` under `name`. If `song_id` is given we overwrite the
    existing record in place (update); otherwise a fresh slug is minted.
    Returns the full stored record.
    """
    now = int(time.time())
    if song_id:
        existing = load_song(song_id)
        if existing is None:
            # Caller passed a stale id — mint a new one instead of orphaning.
            song_id = _slugify(name)
            created = now
        else:
            created = int(existing.get("created_at", now))
    else:
        song_id = _slugify(name)
        created = now
    record = {
        "id":         song_id,
        "name":       (name or "Untitled").strip() or "Untitled",
        "created_at": created,
        "updated_at": now,
        "spec":       spec,
    }
    tmp = _song_path(song_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(_song_path(song_id))
    return record


def load_song(song_id: str) -> dict[str, Any] | None:
    """Read one song by id. Returns None if the file is missing or corrupt."""
    p = _song_path(song_id)
    try:
        with p.open("r", encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def delete_song(song_id: str) -> bool:
    """
    Remove one song file. Returns True on successful delete, False if the
    file didn't exist. Callers can treat both as "gone" for UI purposes.
    """
    p = _song_path(song_id)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
