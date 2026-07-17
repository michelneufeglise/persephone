"""
Role-based note-pattern generators.

Whenever the composer LLM emits an empty (or single-note) clip — which
happens *a lot* at scale because a 30-bar sketch with 7 tracks × ~16 notes
per bar blows the JSON budget — we fall back to a deterministic pattern
per track role. Not creative, but reliably produces music instead of a
silent Ableton project.

Every generator returns a list of `song_spec.Note`.

The generators use `music_theory` helpers so patterns are always in-key.
"""

from __future__ import annotations

import random
from typing import Iterable

from music_theory import (
    scale, chord_from_scale_degree, note_to_midi, DRUM,
    four_on_floor, backbeat_snare, sixteenth_hats,
)
from song_spec import Note
import style_adapters as _adapters


# ── Genre-flavoured progressions (roman numerals in the given key/mode) ─────
_PROGRESSIONS: dict[str, list[str]] = {
    "lo-fi hip-hop": ["i", "VI", "III", "VII"],
    "house":         ["i", "VII", "VI", "V"],
    "techno":        ["i", "i", "VI", "V"],
    "ambient":       ["I", "V", "vi", "IV"],
    "cinematic":     ["i", "VI", "iv", "V"],
    "":              ["i", "iv", "V", "i"],
}


def _prog(genre: str, mode: str) -> list[str]:
    """Pick a chord progression appropriate to genre + mode."""
    g = (genre or "").lower().strip()
    base = _PROGRESSIONS.get(g, _PROGRESSIONS[""])
    # Flip Roman-numeral case if the mode disagrees (major → uppercase tonic).
    is_major = ("major" in (mode or "").lower())
    return [c.upper() if is_major else c.lower() for c in base]


# ── Drums ────────────────────────────────────────────────────────────────────
def drum_pattern(bars: int, style: str = "basic", intensity: float = 0.5) -> list[Note]:
    """Combine kick, snare, and hats into a bar-count number of MIDI notes."""
    notes: list[Note] = []

    # Kick — four-on-floor for house/techno, boom-bap for lo-fi, sparse for ambient.
    if style in ("house", "techno"):
        for beat, vel in four_on_floor(bars, velocity=112):
            notes.append(Note(pitch=DRUM["kick"], start=beat, length=0.25, velocity=vel))
    elif style == "lo-fi hip-hop":
        # Boom-bap: kick on 1 and the "and-of-2" (0 and 2.5) per bar.
        for b in range(bars):
            notes.append(Note(pitch=DRUM["kick"], start=b * 4.0,       length=0.25, velocity=108))
            notes.append(Note(pitch=DRUM["kick"], start=b * 4.0 + 2.5, length=0.25, velocity=90))
    else:  # ambient / cinematic / fallback → sparse
        for b in range(bars):
            notes.append(Note(pitch=DRUM["kick"], start=b * 4.0, length=0.25, velocity=95))

    # Snare on 2 and 4 (skip for ambient).
    if style != "ambient":
        for beat, vel in backbeat_snare(bars, velocity=105):
            notes.append(Note(pitch=DRUM["snare"], start=beat, length=0.25, velocity=vel))

    # Hats.
    if style == "techno":
        # Offbeat open hats only (0.5, 1.5, 2.5, 3.5)
        for b in range(bars):
            for off in (0.5, 1.5, 2.5, 3.5):
                notes.append(Note(pitch=DRUM["open"], start=b * 4.0 + off, length=0.25, velocity=95))
    elif style == "ambient":
        pass  # no hats
    else:
        # 16th-note closed hats with a subtle velocity duck on offbeats.
        for beat, vel, pitch in sixteenth_hats(bars, velocity=70):
            notes.append(Note(pitch=pitch, start=beat, length=0.125, velocity=vel))

    return notes


# ── Bass ─────────────────────────────────────────────────────────────────────
def bass_pattern(bars: int, tonic: str, mode: str, genre: str) -> list[Note]:
    """Root notes of each chord in the progression, one per bar, low octave."""
    prog = _prog(genre, mode)
    notes: list[Note] = []
    for b in range(bars):
        roman = prog[b % len(prog)]
        chord = chord_from_scale_degree(tonic, mode, roman, octave=2)
        root  = chord[0]
        if genre in ("house", "techno"):
            # Offbeat 8th-note bass — root on 1.5, 2.5, 3.5 of each beat.
            for off in (0.5, 1.5, 2.5, 3.5):
                notes.append(Note(pitch=root, start=b * 4.0 + off, length=0.35, velocity=100))
        elif genre == "lo-fi hip-hop":
            # One long root under each bar, plus a small octave lift at the tail.
            notes.append(Note(pitch=root, start=b * 4.0, length=3.0, velocity=95))
            notes.append(Note(pitch=root + 12, start=b * 4.0 + 3.5, length=0.4, velocity=85))
        else:
            # One long root note under the whole bar.
            notes.append(Note(pitch=root, start=b * 4.0, length=3.8, velocity=95))
    return notes


# ── Chord / Pad ──────────────────────────────────────────────────────────────
def chord_pattern(bars: int, tonic: str, mode: str, genre: str,
                  octave: int = 4, sustain: bool = True) -> list[Note]:
    """
    Sustained (or rhythmic) triads per bar along the progression.
    `sustain=True` → one long chord per bar (great for pad/chord).
    `sustain=False` → chord stab on beats 1 and 3.
    """
    prog = _prog(genre, mode)
    notes: list[Note] = []
    for b in range(bars):
        roman = prog[b % len(prog)]
        try:
            voicing = chord_from_scale_degree(tonic, mode, roman, octave=octave)
        except ValueError:
            continue
        if sustain:
            for p in voicing:
                notes.append(Note(pitch=p, start=b * 4.0, length=3.9, velocity=80))
        else:
            for hit_start, vel in ((0.0, 90), (2.0, 82)):
                for p in voicing:
                    notes.append(Note(pitch=p, start=b * 4.0 + hit_start, length=0.9, velocity=vel))
    return notes


# ── Lead ─────────────────────────────────────────────────────────────────────
def lead_pattern(bars: int, tonic: str, mode: str, seed: int = 42) -> list[Note]:
    """
    Very simple lead line — pick scale-tone notes over each bar with a
    consistent seed so re-runs produce the same phrase.
    """
    rng = random.Random(seed)
    pool = scale(tonic, mode, octaves=1, start_octave=5)
    notes: list[Note] = []
    # One phrase per 2 bars — 6 to 8 eighth-note events.
    for phrase in range(0, bars, 2):
        phrase_start = phrase * 4.0
        n_events = rng.randint(6, 10)
        for i in range(n_events):
            step = i * 8.0 / n_events    # spread across 2 bars = 8 beats
            pitch = rng.choice(pool)
            notes.append(Note(pitch=pitch, start=phrase_start + step,
                              length=0.35, velocity=88))
    return notes


# ── Pad ──────────────────────────────────────────────────────────────────────
def pad_pattern(bars: int, tonic: str, mode: str, genre: str) -> list[Note]:
    # Even longer sustains than chord_pattern.
    prog = _prog(genre, mode)
    notes: list[Note] = []
    # Each pad chord holds two bars.
    for b in range(0, bars, 2):
        roman = prog[(b // 2) % len(prog)]
        try:
            voicing = chord_from_scale_degree(tonic, mode, roman, octave=4)
        except ValueError:
            continue
        # Extend to include the fifth an octave up for that "wide pad" feel.
        voicing = voicing + [voicing[2] + 12] if len(voicing) >= 3 else voicing
        for p in voicing:
            notes.append(Note(pitch=p, start=b * 4.0, length=8.0, velocity=70))
    return notes


# ── FX ───────────────────────────────────────────────────────────────────────
def fx_pattern(bars: int, tonic: str, mode: str) -> list[Note]:
    """Sparse, high-register accents — one every 4 bars at the tail."""
    notes: list[Note] = []
    pool = scale(tonic, mode, octaves=1, start_octave=6)
    for i, b in enumerate(range(0, bars, 4)):
        pitch = pool[i % len(pool)]
        notes.append(Note(pitch=pitch, start=b * 4.0 + 3.5, length=0.5, velocity=70))
    return notes


# ── Public dispatcher ────────────────────────────────────────────────────────
def default_notes_for_role(
    role:   str,
    bars:   int,
    tonic:  str,
    mode:   str,
    genre:  str,
) -> list[Note]:
    """
    Pick a deterministic pattern for a role. Used as a fallback when the LLM
    emits a clip with no notes.
    """
    r = role.lower().strip()
    if r == "drums":
        return drum_pattern(bars, style=genre, intensity=0.6)
    if r == "bass":
        return bass_pattern(bars, tonic, mode, genre)
    if r in ("chord", "keys"):
        return chord_pattern(bars, tonic, mode, genre, octave=4, sustain=True)
    if r == "pad":
        return pad_pattern(bars, tonic, mode, genre)
    if r == "lead":
        return lead_pattern(bars, tonic, mode)
    if r == "fx":
        return fx_pattern(bars, tonic, mode)
    # Unknown role → generic sustained chord.
    return chord_pattern(bars, tonic, mode, genre, octave=4, sustain=True)


def fill_missing_notes(spec) -> tuple[int, int]:
    """
    Walk `spec.tracks[*].clips[*].notes` and, wherever it's empty or has
    ≤1 note (nothing musically meaningful), fill it with a pattern.

    Preference order per clip:
      1. clip.pattern names a known style_adapters archetype → use it.
      2. Fall back to the role default from default_notes_for_role.

    Returns (clips_filled, notes_added).
    """
    filled_clips = 0
    added_notes  = 0
    tonic = spec.key.root
    mode  = spec.key.mode
    genre = spec.genre
    prog  = _PROGRESSIONS.get(genre.lower().strip(), _PROGRESSIONS[""])
    is_major = ("major" in (mode or "").lower())
    prog     = [r.upper() if is_major else r.lower() for r in prog]

    for track in spec.tracks:
        for clip in track.clips:
            if len(clip.notes) > 1:
                continue
            bars = max(1, int(clip.bars))

            new_notes: list[Note] | None = None
            if clip.pattern:
                new_notes = _adapters.resolve(clip.pattern, track.role, bars, tonic, mode, prog)
            if not new_notes:
                new_notes = default_notes_for_role(track.role, bars, tonic, mode, genre)
            if not new_notes:
                continue
            clip.notes = new_notes
            filled_clips += 1
            added_notes  += len(new_notes)
    return filled_clips, added_notes
