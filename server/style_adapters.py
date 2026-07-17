"""
Style adapters — a small named vocabulary of *musical intents* that map to
concrete Note events.

The composer LLM emits a `pattern` string per clip; we turn it into notes
using rhythm.py + music_theory.py primitives. This removes the LLM's need
to count sixteenths, count semitones, or reason about which drum pitch
sits on which beat — jobs it's genuinely bad at.

The vocabulary is deliberately small so the LLM can memorise it. Each entry
below appears in `PATTERN_HELP` and is echoed into the composer prompt.
"""

from __future__ import annotations

from typing import Callable

from song_spec  import Note
from music_theory import (
    chord_from_scale_degree, scale, note_to_midi, DRUM,
)
import rhythm as _r


# ── Drum kits (whole-kit patterns — kick + snare + hats combined) ───────────
def _drum_boom_bap(bars: int) -> list[Note]:
    """Kick 1 + '&-of-2', snare 2 + 4, 16th closed hats with a slight duck."""
    notes: list[Note] = []
    for b in range(bars):
        base = b * 4.0
        # Kick
        for beat in (0.0, 2.5):
            notes.append(Note(pitch=DRUM["kick"], start=base + beat, length=0.25, velocity=108))
        # Snare
        for beat in (1.0, 3.0):
            notes.append(Note(pitch=DRUM["snare"], start=base + beat, length=0.25, velocity=105))
        # 16th hats
        for i in range(16):
            beat = base + i * 0.25
            vel  = 72 if i % 2 == 0 else 58
            notes.append(Note(pitch=DRUM["closed"], start=beat, length=0.125, velocity=vel))
    return notes


def _drum_four_on_floor(bars: int, offbeat_open: bool = False) -> list[Note]:
    """Kick every beat, snare 2+4, and either closed 16ths or offbeat open hats."""
    notes: list[Note] = []
    for b in range(bars):
        base = b * 4.0
        for beat in (0.0, 1.0, 2.0, 3.0):
            notes.append(Note(pitch=DRUM["kick"], start=base + beat, length=0.25, velocity=112))
        for beat in (1.0, 3.0):
            notes.append(Note(pitch=DRUM["snare"], start=base + beat, length=0.25, velocity=105))
        if offbeat_open:
            for beat in (0.5, 1.5, 2.5, 3.5):
                notes.append(Note(pitch=DRUM["open"], start=base + beat, length=0.35, velocity=95))
        else:
            for i in range(16):
                beat = base + i * 0.25
                vel  = 78 if i % 2 == 0 else 62
                notes.append(Note(pitch=DRUM["closed"], start=beat, length=0.125, velocity=vel))
    return notes


def _drum_breakbeat(bars: int) -> list[Note]:
    """1 + 'and-of-2' + 3 kick, snare 2 + 4 + ghost 3.75, sparse offbeat hats."""
    notes: list[Note] = []
    for b in range(bars):
        base = b * 4.0
        for beat, vel in ((0.0, 108), (2.5, 100), (3.0, 96)):
            notes.append(Note(pitch=DRUM["kick"], start=base + beat, length=0.25, velocity=vel))
        for beat, vel in ((1.0, 105), (3.0, 102), (3.75, 60)):    # ghost snare
            notes.append(Note(pitch=DRUM["snare"], start=base + beat, length=0.25, velocity=vel))
        for beat in (0.5, 1.5, 2.5, 3.5):
            notes.append(Note(pitch=DRUM["closed"], start=base + beat, length=0.25, velocity=70))
    return notes


def _drum_techno_driving(bars: int) -> list[Note]:
    """Relentless kick, offbeat open hats, no snare — techno / minimal house."""
    notes: list[Note] = []
    for b in range(bars):
        base = b * 4.0
        for beat in (0.0, 1.0, 2.0, 3.0):
            notes.append(Note(pitch=DRUM["kick"], start=base + beat, length=0.25, velocity=115))
        for beat in (0.5, 1.5, 2.5, 3.5):
            notes.append(Note(pitch=DRUM["open"], start=base + beat, length=0.4, velocity=95))
    return notes


def _drum_tresillo(bars: int) -> list[Note]:
    """Tresillo kick + backbeat clap — Latin / Afro-Cuban feel."""
    notes: list[Note] = []
    # Tresillo positions on a 16-step grid: [0, 6, 10] → [0.0, 1.5, 2.5] beats.
    for b in range(bars):
        base = b * 4.0
        for pos in (0.0, 1.5, 2.5):
            notes.append(Note(pitch=DRUM["kick"], start=base + pos, length=0.25, velocity=108))
        for pos in (1.0, 3.0):
            notes.append(Note(pitch=DRUM["clap"], start=base + pos, length=0.25, velocity=98))
        for i in range(8):   # 8th-note shaker feel
            notes.append(Note(pitch=DRUM["shaker"], start=base + i * 0.5, length=0.25, velocity=60))
    return notes


def _drum_sparse(bars: int) -> list[Note]:
    """Kick 1, snare 3 only. Ambient / cinematic."""
    notes: list[Note] = []
    for b in range(bars):
        notes.append(Note(pitch=DRUM["kick"],  start=b * 4.0,     length=0.25, velocity=95))
        notes.append(Note(pitch=DRUM["snare"], start=b * 4.0 + 2, length=0.25, velocity=90))
    return notes


def _drum_none(bars: int) -> list[Note]:
    return []


# ── Bass patterns ───────────────────────────────────────────────────────────
def _bass_root_hold(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            root = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=2)[0]
        except ValueError:
            continue
        notes.append(Note(pitch=root, start=b * 4.0, length=3.9, velocity=95))
    return notes


def _bass_offbeat_eights(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            root = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=2)[0]
        except ValueError:
            continue
        for off in (0.5, 1.5, 2.5, 3.5):
            notes.append(Note(pitch=root, start=b * 4.0 + off, length=0.35, velocity=100))
    return notes


def _bass_walking(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    """Root → 3rd → 5th → 6th walking up one bar at a time (quarter notes)."""
    notes: list[Note] = []
    pool = scale(tonic, mode, octaves=2, start_octave=2)
    for b in range(bars):
        try:
            chord = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=2)
        except ValueError:
            continue
        # Find the chord's degree indices in the pool, use them + the 6th.
        r, t, f = chord[0], chord[1], chord[2]
        try:
            r_idx = pool.index(r)
            walk  = [pool[r_idx], pool[r_idx + 2], pool[r_idx + 4], pool[min(r_idx + 5, len(pool) - 1)]]
        except (ValueError, IndexError):
            walk = [r, t, f, r + 2]
        for i, pitch in enumerate(walk):
            notes.append(Note(pitch=pitch, start=b * 4.0 + i, length=0.9, velocity=92))
    return notes


def _bass_syncopated_ghost(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            root = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=2)[0]
        except ValueError:
            continue
        base = b * 4.0
        for start, dur, vel in ((0.0, 0.5, 105), (0.75, 0.25, 60),
                                (1.5, 0.5, 95), (2.0, 0.5, 40),  # ghost
                                (2.75, 1.0, 100)):
            notes.append(Note(pitch=root, start=base + start, length=dur, velocity=vel))
    return notes


# ── Chord patterns ──────────────────────────────────────────────────────────
def _chord_sustained(bars: int, tonic: str, mode: str, prog: list[str],
                      octave: int = 4) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            voicing = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=octave)
        except ValueError:
            continue
        for p in voicing:
            notes.append(Note(pitch=p, start=b * 4.0, length=3.9, velocity=80))
    return notes


def _chord_stab_1_and_3(bars: int, tonic: str, mode: str, prog: list[str],
                         octave: int = 4) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            voicing = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=octave)
        except ValueError:
            continue
        for hit_start, vel in ((0.0, 92), (2.0, 82)):
            for p in voicing:
                notes.append(Note(pitch=p, start=b * 4.0 + hit_start, length=0.9, velocity=vel))
    return notes


def _chord_arpeggio_up(bars: int, tonic: str, mode: str, prog: list[str],
                        octave: int = 4) -> list[Note]:
    notes: list[Note] = []
    for b in range(bars):
        try:
            voicing = chord_from_scale_degree(tonic, mode, prog[b % len(prog)], octave=octave)
        except ValueError:
            continue
        if not voicing:
            continue
        base = b * 4.0
        # Repeat the voicing across 8th-notes; wraps if voicing has 3 tones and 8 slots.
        for i in range(8):
            pitch = voicing[i % len(voicing)]
            notes.append(Note(pitch=pitch, start=base + i * 0.5, length=0.4, velocity=82))
    return notes


# ── Pad patterns ────────────────────────────────────────────────────────────
def _pad_sustained_wide(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    notes: list[Note] = []
    for b in range(0, bars, 2):
        try:
            voicing = chord_from_scale_degree(tonic, mode, prog[(b // 2) % len(prog)], octave=4)
        except ValueError:
            continue
        # Double the fifth up an octave for width.
        if len(voicing) >= 3:
            voicing = list(voicing) + [voicing[2] + 12]
        for p in voicing:
            notes.append(Note(pitch=p, start=b * 4.0, length=8.0, velocity=68))
    return notes


def _pad_drone(bars: int, tonic: str, mode: str, prog: list[str]) -> list[Note]:
    root = note_to_midi(tonic, octave=3)
    return [
        Note(pitch=root,      start=0.0, length=bars * 4.0, velocity=72),
        Note(pitch=root + 12, start=0.0, length=bars * 4.0, velocity=60),
    ]


# ── Lead patterns ───────────────────────────────────────────────────────────
def _lead_ostinato_16ths(bars: int, tonic: str, mode: str) -> list[Note]:
    pool = scale(tonic, mode, octaves=1, start_octave=5)
    # Simple 4-note ostinato repeating.
    figure = [pool[0], pool[2], pool[4], pool[2]]
    notes: list[Note] = []
    for b in range(bars):
        base = b * 4.0
        for i in range(16):
            notes.append(Note(pitch=figure[i % 4], start=base + i * 0.25,
                              length=0.2, velocity=78))
    return notes


def _lead_sparse_melodic(bars: int, tonic: str, mode: str) -> list[Note]:
    pool = scale(tonic, mode, octaves=1, start_octave=5)
    # 4-5 notes per 2-bar phrase.
    notes: list[Note] = []
    for phrase in range(0, bars, 2):
        base = phrase * 4.0
        events = [(0.0, pool[0], 0.8), (1.5, pool[2], 0.5),
                  (3.0, pool[4], 0.8), (4.5, pool[3], 0.5),
                  (6.0, pool[1], 1.5)]
        for start, pitch, dur in events:
            notes.append(Note(pitch=pitch, start=base + start, length=dur, velocity=88))
    return notes


def _lead_pentatonic_call(bars: int, tonic: str, mode: str) -> list[Note]:
    # A 1-bar call answered by a 1-bar response, offset up an octave.
    pool_low  = scale(tonic, "minor_pentatonic", octaves=1, start_octave=5)
    pool_high = scale(tonic, "minor_pentatonic", octaves=1, start_octave=6)
    call  = [(0.0, pool_low[0]),  (0.5, pool_low[1]),  (1.0, pool_low[2]),  (2.0, pool_low[0])]
    resp  = [(0.0, pool_high[2]), (0.5, pool_high[1]), (1.5, pool_high[0]), (2.5, pool_low[4])]
    notes: list[Note] = []
    for phrase in range(0, bars, 2):
        base = phrase * 4.0
        for start, pitch in call:
            notes.append(Note(pitch=pitch, start=base + start, length=0.4, velocity=90))
        for start, pitch in resp:
            notes.append(Note(pitch=pitch, start=base + 4.0 + start, length=0.4, velocity=95))
    return notes


# ── FX ──────────────────────────────────────────────────────────────────────
def _fx_sparse_tail(bars: int, tonic: str, mode: str) -> list[Note]:
    pool  = scale(tonic, mode, octaves=1, start_octave=6)
    notes: list[Note] = []
    for i, b in enumerate(range(0, bars, 4)):
        pitch = pool[i % len(pool)]
        notes.append(Note(pitch=pitch, start=b * 4.0 + 3.5, length=0.5, velocity=70))
    return notes


# ── Registry ────────────────────────────────────────────────────────────────
# The LLM emits these strings verbatim. Add new archetypes here and echo them
# in PATTERN_HELP for the composer's system prompt.
_ArgsMeta = tuple[str, str, list[str]]   # (tonic, mode, progression)

_KIT_PATTERNS: dict[str, Callable[[int], list[Note]]] = {
    "boom_bap":                _drum_boom_bap,
    "four_on_floor":           lambda bars: _drum_four_on_floor(bars, offbeat_open=False),
    "four_on_floor_open_hats": lambda bars: _drum_four_on_floor(bars, offbeat_open=True),
    "breakbeat":               _drum_breakbeat,
    "techno_driving":          _drum_techno_driving,
    "tresillo":                _drum_tresillo,
    "sparse":                  _drum_sparse,
    "none":                    _drum_none,
}

_MELODIC_PATTERNS: dict[str, Callable[[int, str, str, list[str]], list[Note]]] = {
    # Bass
    "bass.root_hold":          _bass_root_hold,
    "bass.offbeat_eights":     _bass_offbeat_eights,
    "bass.walking":            _bass_walking,
    "bass.syncopated_ghost":   _bass_syncopated_ghost,
    # Chord
    "chord.sustained":         _chord_sustained,
    "chord.stab_1_and_3":      _chord_stab_1_and_3,
    "chord.arpeggio_up":       _chord_arpeggio_up,
    # Pad
    "pad.sustained_wide":      _pad_sustained_wide,
    "pad.drone":               _pad_drone,
}

# Lead and FX don't take a progression, only key.
_KEY_ONLY_PATTERNS: dict[str, Callable[[int, str, str], list[Note]]] = {
    "lead.ostinato_16ths":     _lead_ostinato_16ths,
    "lead.sparse_melodic":     _lead_sparse_melodic,
    "lead.pentatonic_call":    _lead_pentatonic_call,
    "fx.sparse_tail":          _fx_sparse_tail,
}


# LLM-facing vocabulary map: role → list of valid pattern names.
PATTERN_HELP = {
    "drums": list(_KIT_PATTERNS.keys()),
    "bass":  [k[5:]  for k in _MELODIC_PATTERNS if k.startswith("bass.")],
    "chord": [k[6:]  for k in _MELODIC_PATTERNS if k.startswith("chord.")],
    "pad":   [k[4:]  for k in _MELODIC_PATTERNS if k.startswith("pad.")],
    "lead":  [k[5:]  for k in _KEY_ONLY_PATTERNS if k.startswith("lead.")],
    "fx":    [k[3:]  for k in _KEY_ONLY_PATTERNS if k.startswith("fx.")],
}


def resolve(
    pattern:  str,
    role:     str,
    bars:     int,
    tonic:    str,
    mode:     str,
    prog:     list[str],
) -> list[Note] | None:
    """
    Turn a `pattern` name into concrete notes.

    Returns None when the pattern is unrecognised; callers should fall
    back to the role-default from `note_patterns.default_notes_for_role`.
    Role name lookup is case-insensitive; the pattern name may or may not
    include the `role.` prefix (`bass.walking` and `walking` both work).
    """
    if not pattern:
        return None
    name = pattern.strip().lower()

    # Drums — kit-level; role isn't needed to disambiguate.
    if name in _KIT_PATTERNS:
        return _KIT_PATTERNS[name](bars)

    role_lc = role.strip().lower()
    # Support both fully-qualified ("bass.walking") and bare ("walking") forms.
    lookup_forms = [name, f"{role_lc}.{name}"]
    for form in lookup_forms:
        if form in _MELODIC_PATTERNS:
            return _MELODIC_PATTERNS[form](bars, tonic, mode, prog)
        if form in _KEY_ONLY_PATTERNS:
            return _KEY_ONLY_PATTERNS[form](bars, tonic, mode)
    return None
