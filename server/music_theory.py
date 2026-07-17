"""
Small music-theory helpers for the Ableton composer.

Deliberately narrow scope — just what the SongSpec → MIDI translator needs
in Phase 2. Broader theory (voice-leading, borrowed chords, modal
interchange) lands in Phase 4 alongside the style adapters.

MIDI conventions used throughout:
  60 = middle C (C4)
  36 = kick drum (GM standard)
  38 = snare
  42 = closed hi-hat
  46 = open hi-hat
  49 = crash
"""

from __future__ import annotations

from typing import Iterable

# ── Note names ────────────────────────────────────────────────────────────────
_PITCH_CLASS = {
    "c":  0, "b#": 0,
    "c#": 1, "db": 1,
    "d":  2,
    "d#": 3, "eb": 3,
    "e":  4, "fb": 4,
    "f":  5, "e#": 5,
    "f#": 6, "gb": 6,
    "g":  7,
    "g#": 8, "ab": 8,
    "a":  9,
    "a#": 10, "bb": 10,
    "b":  11, "cb": 11,
}


def note_to_midi(name: str, octave: int = 4) -> int:
    """
    Parse a note name to a MIDI number.

    Accepts either just the note ('C', 'F#', 'Bb') defaulting to `octave`,
    or a note+octave like 'C4', 'F#3', 'Bb5'.
    """
    s = name.strip()
    # Split off trailing digits (octave)
    tail = ""
    while s and (s[-1].isdigit() or s[-1] == "-"):
        tail = s[-1] + tail
        s = s[:-1]
    oct_ = int(tail) if tail else octave
    pc = _PITCH_CLASS.get(s.lower())
    if pc is None:
        raise ValueError(f"unknown note name: {name!r}")
    # MIDI: C-1 = 0, so C4 = 60.
    return (oct_ + 1) * 12 + pc


# ── Scales ────────────────────────────────────────────────────────────────────
# Interval offsets from tonic, in semitones.
_SCALES: dict[str, tuple[int, ...]] = {
    "major":            (0, 2, 4, 5, 7, 9, 11),
    "natural_minor":    (0, 2, 3, 5, 7, 8, 10),
    "minor":            (0, 2, 3, 5, 7, 8, 10),      # alias
    "harmonic_minor":   (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor":    (0, 2, 3, 5, 7, 9, 11),
    "dorian":           (0, 2, 3, 5, 7, 9, 10),
    "phrygian":         (0, 1, 3, 5, 7, 8, 10),
    "lydian":           (0, 2, 4, 6, 7, 9, 11),
    "mixolydian":       (0, 2, 4, 5, 7, 9, 10),
    "aeolian":          (0, 2, 3, 5, 7, 8, 10),      # = natural_minor
    "locrian":          (0, 1, 3, 5, 6, 8, 10),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "blues":            (0, 3, 5, 6, 7, 10),
}


def scale(tonic: str, mode: str, octaves: int = 2, start_octave: int = 3) -> list[int]:
    """
    Return MIDI notes for `octaves` full runs of `mode` starting on `tonic`
    at `start_octave`.

    Example: scale("C", "minor", octaves=1, start_octave=4) →
             [60, 62, 63, 65, 67, 68, 70, 72]
    """
    intervals = _SCALES.get(mode.lower().replace(" ", "_"))
    if intervals is None:
        raise ValueError(f"unknown scale: {mode!r}")
    root = note_to_midi(tonic, octave=start_octave)
    notes: list[int] = []
    for o in range(octaves):
        for iv in intervals:
            notes.append(root + iv + 12 * o)
    notes.append(root + 12 * octaves)  # top tonic
    return notes


# ── Chords ────────────────────────────────────────────────────────────────────
# Triad / seventh built from scale degrees. Roman-numeral notation.
_ROMAN_TO_DEGREE = {
    "i":   0, "ii":  1, "iii": 2, "iv":  3, "v":   4, "vi":  5, "vii": 6,
    "I":   0, "II":  1, "III": 2, "IV":  3, "V":   4, "VI":  5, "VII": 6,
}


def chord_from_scale_degree(
    tonic: str, mode: str, roman: str, octave: int = 4, seventh: bool = False,
) -> list[int]:
    """
    Return MIDI notes for a triad (or seventh) rooted at the given scale degree.

    'V' → V-major (uppercase = major-flavoured), 'v' → v-minor (lowercase =
    minor-flavoured). Case is honoured — 'V' in a minor key still produces
    the major-dominant with a leading tone. `roman` may also include '7'
    for a shorthand ('V7', 'ii7').
    """
    r = roman.strip()
    is_seventh = seventh or r.endswith("7")
    r = r.rstrip("7")
    degree = _ROMAN_TO_DEGREE.get(r)
    if degree is None:
        raise ValueError(f"unknown roman numeral: {roman!r}")
    intervals = _SCALES.get(mode.lower().replace(" ", "_"))
    if intervals is None:
        raise ValueError(f"unknown scale: {mode!r}")
    root_midi   = note_to_midi(tonic, octave=octave)

    # scale_note(i) — the i-th note of the scale, counting through octaves.
    # i=0 is the tonic; i=7 is the tonic one octave up.
    def scale_note(i: int) -> int:
        octave_offset, scale_index = divmod(i, len(intervals))
        return root_midi + intervals[scale_index] + 12 * octave_offset

    scale_root = scale_note(degree)
    third      = scale_note(degree + 2)
    fifth      = scale_note(degree + 4)
    notes      = [scale_root, third, fifth]
    if is_seventh:
        notes.append(scale_note(degree + 6))
    # If uppercase, force major flavour by raising the third if it's minor.
    if r.isupper() and (third - scale_root) == 3:
        notes[1] = scale_root + 4
    if r.islower() and (third - scale_root) == 4:
        notes[1] = scale_root + 3
    return notes


# ── Drums (GM convention) ─────────────────────────────────────────────────────
DRUM = {
    "kick":     36,
    "snare":    38,
    "clap":     39,
    "closed":   42,     # closed hi-hat
    "open":     46,     # open hi-hat
    "crash":    49,
    "ride":     51,
    "tom_lo":   45,
    "tom_mid":  47,
    "tom_hi":   50,
    "shaker":   70,
    "cowbell":  56,
    "rimshot":  37,
}


# ── Cadences (short chord phrases resolving to a target degree) ─────────────
# Roman-numeral shorthand: uppercase = major-flavoured, lowercase = minor-flavoured.
# Each entry maps a cadence name → a small ordered progression, terminating on
# the tonic (or another target).
CADENCES: dict[str, list[str]] = {
    "authentic":  ["V", "I"],        # V → I    (major resolves classically)
    "authentic_minor": ["V", "i"],   # V → i    (dominant with leading tone, minor tonic)
    "plagal":     ["IV", "I"],       # IV → I   (amen cadence)
    "plagal_minor": ["iv", "i"],     # iv → i
    "deceptive":  ["V", "vi"],       # V → vi   (unexpected resolution)
    "half":       ["I", "V"],        # ends on V (feels unfinished)
    "backdoor":   ["iv", "VII", "I"],
    "andalusian": ["i", "VII", "VI", "V"],   # flamenco / phrygian descent
}


# ── Progressions library ────────────────────────────────────────────────────
# Named 4- to 8-chord phrases that repeat well. Keys are lowercase.
PROGRESSIONS: dict[str, list[str]] = {
    # Timeless
    "pop_50s":         ["I", "vi", "IV", "V"],           # doo-wop
    "pop_axis":        ["I", "V", "vi", "IV"],           # "axis of awesome" — Beatles/Journey/most pop
    "pop_axis_minor":  ["i", "VI", "III", "VII"],        # same rotation in minor
    "pachelbel":       ["I", "V", "vi", "iii", "IV", "I", "IV", "V"],
    "twelve_bar_blues": ["I", "I", "I", "I", "IV", "IV", "I", "I", "V", "IV", "I", "V"],
    # Jazz / classic
    "ii_V_I":          ["ii", "V", "I"],
    "iii_vi_ii_V":     ["iii", "vi", "ii", "V"],
    "montgomery":      ["I", "vi", "ii", "V"],           # classic bebop turnaround
    "coltrane":        ["I", "III", "V"],                # major-thirds cycle (Giant Steps flavour)
    # Modal / atmospheric
    "aeolian_vamp":    ["i", "VII"],                     # two-chord ambient loop
    "dorian_vamp":     ["i", "IV"],
    "phrygian":        ["i", "II"],                      # dark
    "lydian":          ["I", "II"],                      # bright, movie-trailer
    # Electronic
    "house_epic":      ["i", "VII", "VI", "V"],          # driving minor
    "house_shuffle":   ["i", "iv", "VII", "III"],
    "techno_drone":    ["i", "i", "i", "VI"],            # sparse, hypnotic
    "trance_energy":   ["i", "VI", "III", "VII"],
    # Cinematic / drama
    "cinematic_rise":  ["i", "III", "VII", "V"],
    "epic_march":      ["i", "VI", "iv", "V"],
    "lament":          ["i", "V", "vi", "iii", "IV", "I", "IV", "V"],
}


def get_progression(name: str, mode: str = "minor") -> list[str] | None:
    """
    Look up a progression by name and case-flip the roman numerals to match
    the mode (uppercase for major, lowercase for minor). Returns None on
    unknown names so callers can fall back.
    """
    prog = PROGRESSIONS.get(name.strip().lower())
    if prog is None:
        return None
    is_major = "major" in (mode or "").lower()
    return [r.upper() if is_major else r.lower() for r in prog]


# ── Rhythm helpers ────────────────────────────────────────────────────────────
def four_on_floor(bars: int, velocity: int = 100) -> list[tuple[float, int]]:
    """Kick on every beat. Returns [(start_beat, velocity), ...]."""
    return [(b * 1.0, velocity) for b in range(bars * 4)]


def backbeat_snare(bars: int, velocity: int = 100) -> list[tuple[float, int]]:
    """Snare on beats 2 and 4 of every bar."""
    hits: list[tuple[float, int]] = []
    for bar in range(bars):
        base = bar * 4.0
        hits.append((base + 1.0, velocity))
        hits.append((base + 3.0, velocity))
    return hits


def sixteenth_hats(
    bars: int, velocity: int = 70, open_positions: Iterable[int] = (),
) -> list[tuple[float, int, int]]:
    """
    Sixteenth-note hi-hats. Returns [(start_beat, velocity, pitch), ...]
    where positions listed in `open_positions` (0-indexed sixteenths per bar)
    swap to the open hat.
    """
    steps_per_bar = 16
    step_len = 0.25
    open_set = set(open_positions)
    hits: list[tuple[float, int, int]] = []
    for bar in range(bars):
        for i in range(steps_per_bar):
            pitch = DRUM["open"] if i in open_set else DRUM["closed"]
            hits.append((bar * 4.0 + i * step_len, velocity, pitch))
    return hits
