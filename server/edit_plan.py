"""
EditPlan — atomic, reversible modifications to a SongSpec + Ableton project.

Every LLM edit turn emits ONE EditPlan JSON. We apply each Op in order, and
for each we compute a *reverse Op* first so we can push it onto an undo
stack. `⌘Z` in the composer pops and re-applies reverses.

Supported ops (Phase 3 core):
    set_tempo(bpm)
    set_key(root, mode)
    track.rename(track_index, name)
    track.set_mix(track_index, volume_db?, pan?)
    track.remove(track_index)
    clip.replace_notes(track_index, section, notes)
    clip.transpose(track_index, section, semitones)

Deliberately not shipping in this phase:
    - track.add — requires knowing which clip slot to put the initial clip in
      and juggling section IDs; needs another iteration.
    - clip.add / clip.remove — same reasoning.
    - Adding sections — reshapes the timeline map; Phase 4.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict, field
from typing import Any, Awaitable, Callable

from song_spec import SongSpec, Note, Track, Clip

log = logging.getLogger("edit_plan")

# ── Ops ──────────────────────────────────────────────────────────────────────
#
# Every Op is stored as a plain dict `{kind: str, **kwargs}` so it round-trips
# through JSON cleanly. That keeps the LLM's schema simple and lets the client
# render a human-readable one-liner per Op without extra type registration.
#

Op = dict[str, Any]


def op_summary(op: Op) -> str:
    """One-line human-readable description of an Op — used in the UI."""
    k = op.get("kind", "?")
    if   k == "set_tempo":         return f"tempo → {op.get('bpm')} BPM"
    elif k == "set_key":           return f"key → {op.get('root')} {op.get('mode')}"
    elif k == "track.rename":      return f"rename track {op.get('track_index')} → {op.get('name')!r}"
    elif k == "track.set_mix":
        parts: list[str] = []
        if "volume_db" in op: parts.append(f"vol {op['volume_db']:.1f} dB")
        if "pan"       in op: parts.append(f"pan {op['pan']:+.2f}")
        return f"track {op.get('track_index')} mix: " + ", ".join(parts or ["(no change)"])
    elif k == "track.remove":      return f"remove track {op.get('track_index')}"
    elif k == "clip.replace_notes":
        n = len(op.get("notes") or [])
        return f"track {op.get('track_index')} · '{op.get('section')}': replace with {n} note(s)"
    elif k == "clip.transpose":
        s = int(op.get("semitones", 0))
        return f"track {op.get('track_index')} · '{op.get('section')}': transpose {s:+d} semitones"
    return f"unknown op {k!r}"


# ── Reverse computation ──────────────────────────────────────────────────────
def compute_reverse(op: Op, spec: SongSpec) -> Op | None:
    """
    Compute the Op that reverses `op` against the current `spec` (BEFORE
    applying it). Returns None if the reverse can't be computed (in which
    case the caller should refuse to apply the op).
    """
    k = op.get("kind", "")
    try:
        if k == "set_tempo":
            return {"kind": "set_tempo", "bpm": spec.bpm}
        if k == "set_key":
            return {"kind": "set_key", "root": spec.key.root, "mode": spec.key.mode}
        if k == "track.rename":
            i = int(op["track_index"])
            return {"kind": "track.rename", "track_index": i, "name": spec.tracks[i].name}
        if k == "track.set_mix":
            i = int(op["track_index"])
            reverse: Op = {"kind": "track.set_mix", "track_index": i}
            if "volume_db" in op:
                reverse["volume_db"] = spec.tracks[i].mix.volume_db
            if "pan" in op:
                reverse["pan"] = spec.tracks[i].mix.pan
            return reverse
        if k == "track.remove":
            i = int(op["track_index"])
            # We store the entire track dict — track.add / restore isn't shipped
            # as a user-callable op but the undo path uses `_reinsert_track`.
            t = spec.tracks[i]
            return {"kind": "_track.reinsert", "track_index": i,
                    "track": _track_to_dict(t)}
        if k == "clip.replace_notes":
            i    = int(op["track_index"])
            sect = str(op["section"])
            old  = _find_clip(spec.tracks[i], sect)
            if old is None:
                # No prior clip to restore — reverse is "clear notes on new clip".
                return {"kind": "clip.replace_notes", "track_index": i,
                        "section": sect, "notes": []}
            return {"kind": "clip.replace_notes", "track_index": i,
                    "section": sect,
                    "notes": [_note_to_dict(n) for n in old.notes]}
        if k == "clip.transpose":
            s = int(op.get("semitones", 0))
            return {**op, "semitones": -s}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("compute_reverse failed for %s: %s", op, exc)
        return None
    return None


# ── Apply ────────────────────────────────────────────────────────────────────
ProgressCb = Callable[[dict], Awaitable[None]]


async def apply_op(
    op:      Op,
    spec:    SongSpec,     # mutated in place to reflect the new state
    client,                # AbletonClient
    emit:    ProgressCb,
) -> None:
    """Apply a single Op to both Ableton (via OSC) and to `spec` in-place."""
    k = op.get("kind", "")

    if k == "set_tempo":
        bpm = float(op["bpm"])
        spec.bpm = bpm
        await client.set_tempo(bpm)
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "set_key":
        spec.key.root = str(op["root"])
        spec.key.mode = str(op["mode"])
        # No direct Live-side representation — the key is a Persephone concept.
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "track.rename":
        i = int(op["track_index"])
        name = str(op["name"])
        spec.tracks[i].name = name
        await client.set_track_name(i, name)
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "track.set_mix":
        i = int(op["track_index"])
        if "volume_db" in op:
            v = float(op["volume_db"])
            spec.tracks[i].mix.volume_db = v
            await client.set_track_volume_db(i, v)
        if "pan" in op:
            p = float(op["pan"])
            spec.tracks[i].mix.pan = p
            await client.set_track_pan(i, p)
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "track.remove":
        i = int(op["track_index"])
        # AbletonOSC exposes song/delete_track by index.
        client._send("/live/song/delete_track", [i])
        await asyncio.sleep(0.05)
        del spec.tracks[i]
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "_track.reinsert":
        # Undo path from track.remove. Recreates the track at the same index
        # and repopulates its clips. Best-effort: Ableton doesn't let us
        # insert at an arbitrary index, only append, so the visible order
        # may differ if there are later tracks. Good enough for undo.
        t_dict = op["track"]
        idx    = int(op["track_index"])
        await client.create_midi_track(-1)
        await asyncio.sleep(0.05)
        # Rename + set mix.
        real_idx = len(spec.tracks)   # the newly-created track
        await client.set_track_name(real_idx, t_dict.get("name", "Restored"))
        mix = t_dict.get("mix") or {}
        if "volume_db" in mix:
            await client.set_track_volume_db(real_idx, float(mix["volume_db"]))
        if "pan" in mix:
            await client.set_track_pan(real_idx, float(mix["pan"]))
        # Restore clips.
        section_slot = _current_section_slots(spec)
        for c in t_dict.get("clips", []):
            sect = c.get("section", "")
            slot = section_slot.get(sect)
            if slot is None:
                continue
            length_beats = int(c.get("bars", 4)) * spec.timesig.num
            await client.create_clip(real_idx, slot, length_beats)
            await asyncio.sleep(0.03)
            notes = [
                (int(n["pitch"]), float(n["start"]), float(n.get("length", 0.25)),
                 int(n.get("velocity", 100)))
                for n in c.get("notes", []) or []
                if 0 <= int(n.get("pitch", -1)) <= 127
            ]
            if notes:
                await client.add_notes(real_idx, slot, notes)
        # Rebuild in-memory Track and insert at requested index.
        restored = _dict_to_track(t_dict)
        spec.tracks.insert(min(idx, len(spec.tracks)), restored)
        await emit({"stage": "op", "summary": f"restored track {t_dict.get('name')!r}"})
        return

    if k == "clip.replace_notes":
        i    = int(op["track_index"])
        sect = str(op["section"])
        notes_json = op.get("notes") or []
        # Ableton path
        slot = _section_slot(spec, sect)
        if slot is None:
            await emit({"stage": "op", "summary": f"[skip] unknown section {sect!r}"})
            return
        length_beats = 4 * spec.timesig.num  # default fallback
        clip = _find_clip(spec.tracks[i], sect)
        if clip is not None:
            length_beats = int(clip.bars) * spec.timesig.num
        # Wipe + recreate clip so old notes go away.
        client._send("/live/clip_slot/delete_clip", [i, slot])
        await asyncio.sleep(0.03)
        await client.create_clip(i, slot, length_beats)
        await asyncio.sleep(0.03)
        good = [
            (int(n["pitch"]), float(n["start"]), float(n.get("length", 0.25)),
             int(n.get("velocity", 100)))
            for n in notes_json
            if 0 <= int(n.get("pitch", -1)) <= 127
        ]
        if good:
            await client.add_notes(i, slot, good)
        # Update spec
        if clip is None:
            new_clip = Clip(section=sect, bars=max(1, length_beats // spec.timesig.num), notes=[])
            spec.tracks[i].clips.append(new_clip)
            clip = new_clip
        clip.notes = [
            Note(
                pitch=int(n["pitch"]), start=float(n["start"]),
                length=float(n.get("length", 0.25)),
                velocity=int(n.get("velocity", 100)),
            )
            for n in notes_json
            if 0 <= int(n.get("pitch", -1)) <= 127
        ]
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    if k == "clip.transpose":
        i    = int(op["track_index"])
        sect = str(op["section"])
        semi = int(op.get("semitones", 0))
        clip = _find_clip(spec.tracks[i], sect)
        if clip is None:
            await emit({"stage": "op", "summary": f"[skip] no clip on '{sect}'"})
            return
        # Update in-memory notes.
        new_notes: list[Note] = []
        for n in clip.notes:
            p = n.pitch + semi
            if 0 <= p <= 127:
                new_notes.append(Note(pitch=p, start=n.start, length=n.length,
                                       velocity=n.velocity, probability=n.probability))
        clip.notes = new_notes
        # Push down to Ableton via replace_notes semantics.
        replace_op = {
            "kind": "clip.replace_notes",
            "track_index": i, "section": sect,
            "notes": [_note_to_dict(n) for n in new_notes],
        }
        # Recurse into replace_notes for the OSC side — don't recurse the
        # in-memory update because we already did it.
        # Simplest: reuse the same delete_clip + create_clip + add_notes dance.
        slot = _section_slot(spec, sect)
        if slot is None:
            return
        length_beats = int(clip.bars) * spec.timesig.num
        client._send("/live/clip_slot/delete_clip", [i, slot])
        await asyncio.sleep(0.03)
        await client.create_clip(i, slot, length_beats)
        await asyncio.sleep(0.03)
        good = [(n.pitch, n.start, n.length, n.velocity) for n in new_notes]
        if good:
            await client.add_notes(i, slot, good)
        await emit({"stage": "op", "summary": op_summary(op)})
        return

    await emit({"stage": "op", "summary": f"[skip] unknown op {k!r}"})


async def apply_plan(
    plan:   dict,
    spec:   SongSpec,
    client,
    emit:   ProgressCb,
) -> tuple[list[Op], list[Op]]:
    """
    Apply every Op in `plan['changes']`, returning (applied, reverses).

    `applied`  — the ops as executed, in order (for chat history).
    `reverses` — reverse ops in reverse order (ready to push onto undo stack).
    """
    ops: list[Op] = list(plan.get("changes") or [])
    applied:  list[Op] = []
    reverses: list[Op] = []
    for op in ops:
        rev = compute_reverse(op, spec)
        if rev is None:
            await emit({"stage": "op", "summary": f"[skip] cannot reverse {op.get('kind')}"})
            continue
        try:
            await apply_op(op, spec, client, emit)
        except Exception as exc:
            log.error("apply_op failed for %s: %s", op, exc, exc_info=True)
            await emit({"stage": "op", "summary": f"[error] {op_summary(op)}: {exc}"})
            continue
        applied.append(op)
        reverses.append(rev)
    reverses.reverse()  # newest-first for undo
    return applied, reverses


# ── Helpers ──────────────────────────────────────────────────────────────────
def _find_clip(track: Track, section: str) -> Clip | None:
    for c in track.clips:
        if c.section == section:
            return c
    return None


def _section_slot(spec: SongSpec, section_id: str) -> int | None:
    for idx, sec in enumerate(spec.sections):
        if sec.id == section_id:
            return idx
    return None


def _current_section_slots(spec: SongSpec) -> dict[str, int]:
    return {sec.id: idx for idx, sec in enumerate(spec.sections)}


def _note_to_dict(n: Note) -> dict:
    return {"pitch": n.pitch, "start": n.start, "length": n.length, "velocity": n.velocity}


def _track_to_dict(t: Track) -> dict:
    return {
        "id":   t.id,
        "role": t.role,
        "name": t.name,
        "mix":  {"volume_db": t.mix.volume_db, "pan": t.mix.pan},
        "instrument_hint": t.instrument_hint,
        "clips": [
            {
                "section": c.section, "bars": c.bars,
                "notes": [_note_to_dict(n) for n in c.notes],
                "groove": c.groove,
            }
            for c in t.clips
        ],
    }


def _dict_to_track(d: dict) -> Track:
    from song_spec import TrackMix
    return Track(
        id              = str(d.get("id", "t?")),
        role            = str(d.get("role", "chord")),
        name            = str(d.get("name", "Track")),
        instrument_hint = str(d.get("instrument_hint", "")),
        mix             = TrackMix(
            volume_db = float((d.get("mix") or {}).get("volume_db", -6.0)),
            pan       = float((d.get("mix") or {}).get("pan", 0.0)),
        ),
        clips = [
            Clip(
                section = str(c.get("section", "")),
                bars    = int(c.get("bars", 4)),
                notes   = [
                    Note(
                        pitch    = int(n["pitch"]),
                        start    = float(n["start"]),
                        length   = float(n.get("length", 0.25)),
                        velocity = int(n.get("velocity", 100)),
                    )
                    for n in c.get("notes", []) or []
                    if 0 <= int(n.get("pitch", -1)) <= 127
                ],
                groove = c.get("groove"),
            )
            for c in d.get("clips", []) or []
        ],
    )
