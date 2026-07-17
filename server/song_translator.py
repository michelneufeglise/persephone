"""
Walk a SongSpec and materialise it inside a running Ableton via AbletonClient.

Public entry point:
    await apply(spec, client, on_progress)   →  count of tracks/clips/notes created

Design notes:
- Idempotent-*ish*: we `delete_all_tracks()` up-front so re-applying the same
  SongSpec doesn't stack duplicates. Ableton's own undo stack keeps a per-op
  history for the user; we don't try to be clever with a diff-based apply
  yet — that lands in Phase 3.
- Timing: OSC over UDP is fast but bursty. We chunk with tiny asyncio.sleep()
  yields inside AbletonClient.add_notes so we never blow the AbletonOSC queue.
- Progress: every stage (tempo, tracks, clips, notes) reports before it runs
  so the UI stage bar advances smoothly. `on_progress` is optional.

Section → clip slot mapping:
  Section N (0-indexed) → Clip Slot N in every track. The user gets Session
  view scenes named after each section that fire the right column when
  they're triggered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ableton_client import AbletonClient
from song_spec import SongSpec, Track, Clip

log = logging.getLogger("song_translator")

ProgressCb = Callable[[dict], Awaitable[None]]


async def _noop(_evt: dict) -> None:
    return None


# Per-role instrument defaults. Each entry is a list of (category, name)
# candidates tried in order — first successful load wins. A trailing
# ("instruments", "") sentinel means "load_first(instruments)" is the
# absolute fallback (empty name → the fallback loader in the client
# treats it as the last-resort).
#
# The canonical names below match Live 12 Intro (which is the smallest
# guaranteed set — Standard/Suite are supersets). Diagnosed from a real
# machine's /live/browser/get/instruments output:
#   instruments: Drift, Drum Rack, Drum Sampler, External Instrument, …
#   drums:       Drum Hits, Drum Rack, 505 Core Kit.adg, 606 Core Kit.adg, …
_ROLE_DEFAULTS: dict[str, list[tuple[str, str]]] = {
    "drums":  [("drums", "505 Core Kit"),  ("drums", "606 Core Kit"),
               ("drums", "808 Core Kit"),  ("drums", "Kit"),
               ("instruments", "Drum Rack")],
    "bass":   [("instruments", "Drift"),   ("instruments", "Drum Sampler")],
    "chord":  [("instruments", "Drift")],
    "lead":   [("instruments", "Drift")],
    "pad":    [("instruments", "Drift")],
    "fx":     [("instruments", "Drift"),   ("instruments", "Drum Sampler")],
    "vox":    [("instruments", "Drum Sampler")],
}


async def apply(
    spec:              SongSpec,
    client:            AbletonClient,
    on_progress:       ProgressCb | None = None,
    *,
    wipe_first:        bool = True,
    load_instruments:  bool = True,
) -> dict[str, Any]:
    """
    Materialise `spec` inside Ableton. Returns a stats dict:
        {tracks_created, clips_created, notes_added,
         instruments_loaded, instruments_failed: [str, ...]}

    If `wipe_first`, every existing track is deleted before we build (safe
    default for the composer — nobody wants their previous sketch to leak in).

    If `load_instruments`, each track gets a default instrument loaded via
    the Persephone-patched AbletonOSC browser endpoints. Silent no-op if the
    patch isn't installed on the running Live.
    """
    emit = on_progress or _noop

    # ── 1. Global settings ──
    await emit({"stage": "tempo", "message": f"Setting tempo to {spec.bpm} BPM"})
    await client.set_tempo(spec.bpm)

    await emit({"stage": "timesig", "message": f"Time signature {spec.timesig.num}/{spec.timesig.den}"})
    await client.set_time_signature(spec.timesig.num, spec.timesig.den)

    # ── 2. Clean slate ──
    if wipe_first:
        await emit({"stage": "wipe", "message": "Clearing existing tracks"})
        removed = await client.delete_all_tracks()
        log.info("cleared %d existing track(s)", removed)

    # ── 3. Section → clip slot map ──
    section_slot: dict[str, int] = {sec.id: idx for idx, sec in enumerate(spec.sections)}
    if not section_slot:
        # No sections defined → one implicit "main" slot at index 0.
        section_slot = {"main": 0}

    # Optional preflight for the browser patch — one round-trip so we can
    # tell the UI "auto-load unavailable" instead of silently doing nothing.
    browser_patch = False
    if load_instruments:
        browser_patch = await client.has_browser_patch(timeout=1.0)
        await emit({
            "stage": "browser_probe",
            "browser_patch": browser_patch,
            "message": ("browser patch detected — instruments will auto-load"
                        if browser_patch else
                        "browser patch not detected — instruments will be silent")
        })

    # ── 4. Tracks + clips + notes ──
    stats: dict[str, Any] = {
        "tracks_created": 0, "clips_created": 0, "notes_added": 0,
        "instruments_loaded": 0, "instruments_failed": [],
    }
    n_tracks = len(spec.tracks)
    for i, track in enumerate(spec.tracks):
        await emit({
            "stage": "track",
            "message": f"Track {i+1}/{n_tracks}: {track.name}",
            "progress": i / max(1, n_tracks),
        })
        await client.create_midi_track(-1)
        await asyncio.sleep(0.05)             # give Live a beat to add the track
        await client.set_track_name(i, track.name)
        await client.set_track_volume_db(i, track.mix.volume_db)
        await client.set_track_pan(i, track.mix.pan)
        stats["tracks_created"] += 1

        # Auto-load a default instrument. Tries the per-role candidate
        # ladder first; if EVERY name misses (Live version has different
        # naming, user's library is skinnier, etc), falls back to loading
        # the first available item in the target category.
        if load_instruments and browser_patch:
            candidates = _ROLE_DEFAULTS.get(track.role, _ROLE_DEFAULTS["chord"])
            loaded_name: str | None = None
            last_error:  str        = ""
            attempts:    list[str]  = []
            for cat, name in candidates:
                ok, detail = await client.load_instrument_named(i, cat, name)
                attempts.append(f"{cat}:{name} → {'OK' if ok else detail}")
                if ok:
                    loaded_name = detail
                    break
                last_error = detail
            if not loaded_name:
                # Last-resort: whichever category was the first choice for
                # this role, ask the bridge to load the first loadable item.
                fallback_cat = candidates[0][0] if candidates else "instruments"
                ok, detail   = await client.load_first_in_category(i, fallback_cat)
                attempts.append(f"first-in-{fallback_cat} → {'OK' if ok else detail}")
                if ok:
                    loaded_name = detail
                    log.info(
                        "auto-load fallback: '%s' (%s) → first available: %s",
                        track.name, fallback_cat, detail,
                    )
                else:
                    last_error = detail
            if loaded_name:
                stats["instruments_loaded"] += 1
                await emit({
                    "stage":   "instrument",
                    "message": f"'{track.name}' → {loaded_name}",
                    "track":   i, "name": loaded_name,
                })
            else:
                # Record BOTH the friendly track name and the detailed
                # error so the UI can show the user what actually happened.
                stats["instruments_failed"].append(track.name)
                stats.setdefault("instruments_failure_detail", []).append({
                    "track":     track.name,
                    "role":      track.role,
                    "last_error": last_error,
                    "attempts":  attempts,
                })
                log.warning(
                    "auto-load failed for '%s' (%s): %s | attempts: %s",
                    track.name, track.role, last_error, attempts,
                )
                await emit({
                    "stage":   "instrument",
                    "message": f"'{track.name}' → failed: {last_error}",
                    "track":   i,
                    "failed":  True,
                    "detail":  last_error,
                    "attempts": attempts,
                })
            # Ableton needs a beat after each device load before we start
            # dropping notes into the track's clip slots.
            await asyncio.sleep(0.05)

        # For each of this track's clips, drop into the correct slot.
        for clip in track.clips:
            slot = section_slot.get(clip.section)
            if slot is None:
                # Unrecognised section id → drop the clip.
                log.warning("clip refers to unknown section %r on track %r — skipped",
                            clip.section, track.name)
                continue
            length_beats = float(max(1, clip.bars)) * float(spec.timesig.num)
            await client.create_clip(i, slot, length_beats)
            await asyncio.sleep(0.03)
            stats["clips_created"] += 1

            if clip.notes:
                # Filter out notes we can't send (out of range / negative length).
                good = [
                    (n.pitch, n.start, n.length, n.velocity)
                    for n in clip.notes
                    if 0 <= n.pitch <= 127 and n.length > 0 and n.start >= 0
                ]
                if good:
                    await client.add_notes(i, slot, good)
                    stats["notes_added"] += len(good)

    await emit({"stage": "done", "progress": 1.0, **stats})
    return stats


async def apply_single_track(
    spec:             SongSpec,
    track_id:         str,
    client:           AbletonClient,
    on_progress:      ProgressCb | None = None,
    *,
    live_track_index: int | None = None,
    load_instrument:  bool = True,
) -> dict[str, Any]:
    """
    Update one track in Ableton without wiping the whole session — used by the
    "Apply modified only" path so the user doesn't lose the tracks they've
    already fine-tuned.

    Strategy:
      * If `live_track_index` is given, we assume that track already exists
        in Live and just want to refresh its clips (delete-then-recreate all
        section slots for this track).
      * If `live_track_index is None`, we append a new MIDI track at the end,
        rename it, mix it, load its instrument, and drop clips in.

    We do NOT touch the global tempo / time-sig — those are song-level and
    already set by the initial apply.
    """
    emit = on_progress or _noop

    track: Track | None = next((t for t in spec.tracks if t.id == track_id), None)
    if track is None:
        raise ValueError(f"track_id {track_id!r} not in spec")

    # Section → clip slot map (must match the initial apply's layout).
    section_slot: dict[str, int] = {s.id: i for i, s in enumerate(spec.sections)}
    if not section_slot:
        section_slot = {"main": 0}

    stats: dict[str, Any] = {
        "tracks_created": 0, "tracks_updated": 0,
        "clips_created": 0,  "notes_added": 0,
        "instruments_loaded": 0, "instruments_failed": [],
        # Included in the returned dict so the SSE `complete` event downstream
        # carries it back to the frontend — that's how the UI learns which
        # Live track index to fire on ▶ / apply on ⚡ later.
        "track_index": None,
    }

    # ── 1. Ensure the target track exists ──
    if live_track_index is None:
        current = await client.get_num_tracks()
        await emit({"stage": "track_new", "message": f"Creating track '{track.name}'"})
        await client.create_midi_track(-1)
        await asyncio.sleep(0.05)
        live_track_index = current
        await client.set_track_name(live_track_index, track.name)
        await client.set_track_volume_db(live_track_index, track.mix.volume_db)
        await client.set_track_pan(live_track_index, track.mix.pan)
        stats["tracks_created"] += 1

        # Auto-load a role-appropriate instrument (mirrors apply()'s ladder).
        if load_instrument and await client.has_browser_patch(timeout=1.0):
            candidates = _ROLE_DEFAULTS.get(track.role, _ROLE_DEFAULTS["chord"])
            loaded_name: str | None = None
            for cat, name in candidates:
                ok, detail = await client.load_instrument_named(live_track_index, cat, name)
                if ok:
                    loaded_name = detail
                    break
            if not loaded_name:
                fallback_cat = candidates[0][0] if candidates else "instruments"
                ok, detail = await client.load_first_in_category(live_track_index, fallback_cat)
                if ok:
                    loaded_name = detail
            if loaded_name:
                stats["instruments_loaded"] += 1
                await emit({"stage": "instrument",
                            "message": f"'{track.name}' → {loaded_name}",
                            "track": live_track_index, "name": loaded_name})
            else:
                stats["instruments_failed"].append(track.name)
                await emit({"stage": "instrument", "failed": True,
                            "message": f"'{track.name}' → auto-load failed",
                            "track": live_track_index})
            await asyncio.sleep(0.05)
    else:
        # Existing track — refresh name/mix in case the spec was edited.
        await emit({"stage": "track_update", "message": f"Updating '{track.name}'"})
        await client.set_track_name(live_track_index, track.name)
        await client.set_track_volume_db(live_track_index, track.mix.volume_db)
        await client.set_track_pan(live_track_index, track.mix.pan)
        stats["tracks_updated"] += 1
        # Clear existing clips before we drop new ones. Loop across every
        # known section slot — Live silently no-ops on empty slots.
        for slot in section_slot.values():
            await client.delete_clip(live_track_index, slot)
            await asyncio.sleep(0.02)

    # ── 2. (Re)create clips + notes for this track only ──
    for clip in track.clips:
        slot = section_slot.get(clip.section)
        if slot is None:
            log.warning("apply_single_track: clip refs unknown section %r", clip.section)
            continue
        length_beats = float(max(1, clip.bars)) * float(spec.timesig.num)
        await client.create_clip(live_track_index, slot, length_beats)
        await asyncio.sleep(0.03)
        stats["clips_created"] += 1
        if clip.notes:
            good = [
                (n.pitch, n.start, n.length, n.velocity)
                for n in clip.notes
                if 0 <= n.pitch <= 127 and n.length > 0 and n.start >= 0
            ]
            if good:
                await client.add_notes(live_track_index, slot, good)
                stats["notes_added"] += len(good)

    stats["track_index"] = live_track_index
    await emit({"stage": "done", "progress": 1.0, **stats})
    return stats
