"""
SongSpec — the canonical intermediate form between the LLM composer and the
Ableton translator.

Every composition is described declaratively as one of these. The LLM emits a
SongSpec; the translator walks it and emits OSC calls that materialise the
song inside Live. Persisted verbatim to `data_dir()/ableton/sketches/<id>.json`
so we can re-open, iterate, or export later.

Same shape as documented in docs/ABLETON_COMPOSER_PLAN.md §6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Note:
    pitch:    int          # MIDI 0-127
    start:    float        # beats from clip start
    length:   float        # beats
    velocity: int = 100    # 0-127
    probability: float = 1.0


@dataclass
class Clip:
    section:  str          # section id — the clip fires while this section is active
    bars:     int          # usually 4 or 8; loops within its section
    notes:    list[Note]   = field(default_factory=list)
    groove:   str | None   = None    # optional Live groove-pool template name
    pattern:  str = ""     # optional style-adapter archetype name (see style_adapters.PATTERN_HELP)


@dataclass
class TrackMix:
    volume_db: float = -6.0
    pan:       float = 0.0   # -1 (L) .. +1 (R)


@dataclass
class Track:
    id:         str
    role:       str        # "drums" | "bass" | "chord" | "lead" | "pad" | "fx" | "vox"
    name:       str
    clips:      list[Clip] = field(default_factory=list)
    mix:        TrackMix   = field(default_factory=TrackMix)
    # instrument path is left as a hint in Phase 2 — Phase 5 wires the browser
    # walk that turns "warm rhodes" into a real device.
    instrument_hint: str = ""


@dataclass
class Section:
    id:         str        # matches Clip.section
    name:       str        # 'intro' | 'verse' | 'chorus' | 'bridge' | 'drop' | 'outro' | custom
    start_bar:  int
    length_bars: int
    intensity:  float = 0.5    # 0-1, purely informational for now


@dataclass
class Key:
    root: str = "C"
    mode: str = "major"


@dataclass
class TimeSig:
    num: int = 4
    den: int = 4


@dataclass
class SongSpec:
    version:  int = 1
    bpm:      float = 90.0
    key:      Key = field(default_factory=Key)
    timesig:  TimeSig = field(default_factory=TimeSig)
    bars:     int = 32
    sections: list[Section] = field(default_factory=list)
    tracks:   list[Track] = field(default_factory=list)
    genre:    str = ""
    mood:     str = ""
    topic:    str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def total_bars(self) -> int:
        if self.sections:
            return max(s.start_bar + s.length_bars for s in self.sections)
        return self.bars


# ── JSON → SongSpec (defensive parsing) ───────────────────────────────────────
def _get(d: Any, key: str, default: Any) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def parse_song_spec(payload: dict[str, Any] | str) -> SongSpec:
    """
    Turn an LLM-emitted JSON blob into a validated SongSpec.

    Missing fields are filled with sensible defaults; unrecognised fields are
    dropped silently. The goal is robustness — LLMs will produce imperfect
    JSON and we should still get a playable song out of it.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("SongSpec payload must be a JSON object")

    key_raw     = _get(payload, "key", {})
    key         = Key(
        root=str(_get(key_raw, "root", "C")).strip() or "C",
        mode=str(_get(key_raw, "mode", "major")).lower(),
    )
    ts_raw      = _get(payload, "timesig", {}) or _get(payload, "time_sig", {})
    timesig     = TimeSig(
        num=int(_get(ts_raw, "num", 4)),
        den=int(_get(ts_raw, "den", 4)),
    )

    # Sections
    sections: list[Section] = []
    for s in _get(payload, "sections", []) or []:
        try:
            sections.append(Section(
                id          = str(s.get("id", s.get("name", ""))).strip() or f"s{len(sections)+1}",
                name        = str(s.get("name", "section")).strip(),
                start_bar   = int(s.get("start_bar", s.get("startBar", 0))),
                length_bars = int(s.get("length_bars", s.get("lengthBars", 4))),
                intensity   = float(s.get("intensity", 0.5)),
            ))
        except (TypeError, ValueError):
            continue

    # Tracks + clips + notes
    tracks: list[Track] = []
    for i, t in enumerate(_get(payload, "tracks", []) or []):
        if not isinstance(t, dict):
            continue
        clips: list[Clip] = []
        for c in t.get("clips", []) or []:
            if not isinstance(c, dict):
                continue
            notes: list[Note] = []
            for n in c.get("notes", []) or []:
                if not isinstance(n, dict):
                    continue
                try:
                    notes.append(Note(
                        pitch    = int(n["pitch"]),
                        start    = float(n["start"]),
                        length   = float(n.get("length", 0.25)),
                        velocity = int(n.get("velocity", n.get("vel", 100))),
                        probability = float(n.get("probability", 1.0)),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            try:
                clips.append(Clip(
                    section = str(c.get("section", "")).strip(),
                    bars    = int(c.get("bars", 4)),
                    notes   = notes,
                    groove  = c.get("groove") or None,
                    pattern = str(c.get("pattern", "")).strip().lower(),
                ))
            except (TypeError, ValueError):
                continue

        mix_raw = t.get("mix", {}) or {}
        mix     = TrackMix(
            volume_db = float(mix_raw.get("volume_db", mix_raw.get("volumeDb", -6.0))),
            pan       = float(mix_raw.get("pan", 0.0)),
        )
        tracks.append(Track(
            id              = str(t.get("id", f"t{i+1}")).strip() or f"t{i+1}",
            role            = str(t.get("role", "chord")).lower(),
            name            = str(t.get("name", t.get("role", f"Track {i+1}"))).strip(),
            clips           = clips,
            mix             = mix,
            instrument_hint = str(t.get("instrument_hint", t.get("instrument", ""))).strip(),
        ))

    return SongSpec(
        version  = int(_get(payload, "version", 1)),
        bpm      = float(_get(payload, "bpm", 90.0)),
        key      = key,
        timesig  = timesig,
        bars     = int(_get(payload, "bars", 32)),
        sections = sections,
        tracks   = tracks,
        genre    = str(_get(payload, "genre", "")).lower().strip(),
        mood     = str(_get(payload, "mood", "")).strip(),
        topic    = str(_get(payload, "topic", "")).strip(),
    )


# ── Genre-preset scaffolds ────────────────────────────────────────────────────
# These aren't complete SongSpecs — they're strong hints that the LLM composer
# reads back into its prompt so tempo/key/section shape stay authentic. The
# LLM fills in the actual note content.
GENRE_PRESETS: dict[str, dict[str, Any]] = {
    "lo-fi hip-hop": {
        "bpm_range":  (72, 88),
        "typical_key_mode": "minor",
        "typical_sections": ["intro", "verse", "chorus", "verse", "chorus", "outro"],
        "typical_tracks":   ["drums", "bass", "chord", "lead"],
        "swing":            0.55,
        "notes": "warm rhodes chord + ii-V-i or i-VI-VII-i progression; boom-bap kit; upright bass",
    },
    "house": {
        "bpm_range":  (120, 128),
        "typical_key_mode": "minor",
        "typical_sections": ["intro", "verse", "buildup", "chorus", "verse", "chorus", "outro"],
        "typical_tracks":   ["drums", "bass", "chord", "lead", "fx"],
        "swing":            0.0,
        "notes": "four-on-the-floor kick, offbeat open hats, deep bass, filtered pad chords",
    },
    "techno": {
        "bpm_range":  (130, 140),
        "typical_key_mode": "minor",
        "typical_sections": ["intro", "verse", "buildup", "drop", "verse", "drop", "outro"],
        "typical_tracks":   ["drums", "bass", "lead", "fx"],
        "swing":            0.0,
        "notes": "relentless four-on-the-floor, sparse hats, driving offbeat bass, industrial FX",
    },
    "ambient": {
        "bpm_range":  (60, 80),
        "typical_key_mode": "major",
        "typical_sections": ["intro", "wash", "bloom", "wash", "outro"],
        "typical_tracks":   ["pad", "lead", "fx"],
        "swing":            0.0,
        "notes": "long pad tones, no drums or very sparse cymbal, drone-y bass",
    },
    "cinematic": {
        "bpm_range":  (70, 110),
        "typical_key_mode": "minor",
        "typical_sections": ["intro", "verse", "buildup", "climax", "outro"],
        "typical_tracks":   ["pad", "chord", "lead", "drums", "fx"],
        "swing":            0.0,
        "notes": "strings + brass, ostinato bass, dramatic percussion swells, wide reverbs",
    },
}
