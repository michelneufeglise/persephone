"""
In-memory session state for the Ableton composer.

Phase 3 is single-user, so this is a module-level singleton — no per-request
scoping. Two things live here:

  1. current_spec  — the SongSpec last written to Ableton (source of truth
     for the LLM's context on the next edit turn).
  2. undo_stack    — list[list[Op]]; each entry is the *reverses* of one
     applied EditPlan, newest-first.

Chat history isn't stored server-side — the frontend keeps that. We only
persist the state Ableton itself would lose if the user reloaded the page.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from paths import data_dir
from song_spec import SongSpec, parse_song_spec


class Session:
    def __init__(self) -> None:
        self.current_spec:   SongSpec | None = None
        self.undo_stack:     list[list[dict[str, Any]]] = []
        self._lock = asyncio.Lock()

    async def set_spec(self, spec: SongSpec) -> None:
        async with self._lock:
            self.current_spec = spec
            self.undo_stack.clear()   # fresh compose = fresh undo history
            self._persist()

    async def add_track(self, track_dict: dict[str, Any]) -> None:
        """
        Append (or replace by id) a single track to the current spec — used by
        the track-first workflow so the LLM only proposes one track at a time
        without regenerating the whole song. If no current spec exists we
        raise; callers should compose a brief first.
        """
        async with self._lock:
            if self.current_spec is None:
                raise RuntimeError("no current song — compose a brief before adding tracks")
            # Round-trip through JSON to keep the parse/validate logic in one place.
            raw = json.loads(self.current_spec.to_json())
            tracks = list(raw.get("tracks") or [])
            new_id = str(track_dict.get("id") or "")
            replaced = False
            for i, t in enumerate(tracks):
                if str(t.get("id") or "") == new_id and new_id:
                    tracks[i] = track_dict
                    replaced = True
                    break
            if not replaced:
                tracks.append(track_dict)
            raw["tracks"] = tracks
            self.current_spec = parse_song_spec(raw)
            self.undo_stack.clear()
            self._persist()

    async def push_reverses(self, reverses: list[dict[str, Any]]) -> None:
        if not reverses:
            return
        async with self._lock:
            self.undo_stack.append(reverses)
            # Keep undo bounded — Phase 3 doesn't need infinite history.
            if len(self.undo_stack) > 32:
                self.undo_stack = self.undo_stack[-32:]
            self._persist()

    async def pop_reverses(self) -> list[dict[str, Any]] | None:
        async with self._lock:
            if not self.undo_stack:
                return None
            revs = self.undo_stack.pop()
            self._persist()
            return revs

    def snapshot(self) -> dict[str, Any]:
        return {
            "spec":  json.loads(self.current_spec.to_json()) if self.current_spec else None,
            "undo_depth": len(self.undo_stack),
        }

    # ── Cheap persistence — one JSON file, atomic write ─────────────────────
    def _state_path(self) -> Path:
        p = data_dir() / "ableton"
        p.mkdir(parents=True, exist_ok=True)
        return p / "session.json"

    def _persist(self) -> None:
        try:
            payload = {
                "spec": json.loads(self.current_spec.to_json()) if self.current_spec else None,
                "undo_stack": self.undo_stack,
            }
            tmp = self._state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self._state_path())
        except OSError:
            # Session persistence is best-effort — never break the request loop.
            pass

    def load(self) -> None:
        """Re-hydrate from disk on process boot. Silent on any error."""
        try:
            raw = self._state_path().read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return
        if isinstance(data.get("spec"), dict):
            try:
                self.current_spec = parse_song_spec(data["spec"])
            except Exception:
                pass
        stack = data.get("undo_stack")
        if isinstance(stack, list):
            self.undo_stack = [x for x in stack if isinstance(x, list)]


# Module-level singleton.
_session = Session()
_session.load()


def get() -> Session:
    return _session


def spec_to_dict(spec: SongSpec | None) -> dict[str, Any] | None:
    """Serialise a SongSpec back to a plain dict via its canonical JSON."""
    if spec is None:
        return None
    return json.loads(spec.to_json())
