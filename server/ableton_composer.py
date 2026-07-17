"""
Orchestrates the LLM that produces SongSpec JSON.

Pattern mirrors `_stream_reels_plan` in main.py:
  1. Pick a fast non-thinking model.  Thinking-first models (agentworld,
     ornith, deepseek-r1) dump all their tokens into <think> and never emit
     structured JSON.
  2. Prompt with a schema, a target genre, and the user's topic sentence.
  3. Ask Ollama for `format: "json"` in non-streaming mode.
  4. Parse defensively into a SongSpec.

Emitted events (SSE):
  {"stage": "picking", "message": "…"}
  {"stage": "generating", "model": "qwen2.5:32b"}
  {"stage": "spec", "spec": {…}}                # the parsed SongSpec, once ready
  {"stage": "error", "error": "…"}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from song_spec import parse_song_spec, GENRE_PRESETS, SongSpec
from note_patterns import fill_missing_notes
from style_adapters import PATTERN_HELP

log = logging.getLogger("ableton_composer")

# ── Composer preference ─────────────────────────────────────────────────────
# Standard composer ladder — used when the user's `ableton_composer_model`
# config value is unset or points to a model that isn't installed. Ordered
# by our current best-guess for song composition on typical Apple Silicon:
# MoE thinker first (fast + creative), dense JSON emitters as reliable
# fallbacks that never truncate.
_PLANNER_PREF = [
    "qwen3.6:35b-a3b",                             # MoE thinker — 3B active, fast + creative
    "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
    "qwen2.5:32b",                                 # dense JSON emitter — reliable fallback
    "qwen2.5:14b", "qwen2.5:7b",
    "hermes3:8b",  "llama3.2:3b", "qwen2.5:1.5b",
]


# ── Deep-reasoning preference ───────────────────────────────────────────────
# Used when the composer's "Deep reasoning" toggle is on. Prefer models that
# actually think hard before writing JSON — Gemma 4 26B (Google's native
# thinker) leads, DeepSeek R1 as a bigger backup for users who pulled it.
_DEEP_PLANNER_PREF = [
    "gemma4:26b",                                  # Google's native-thinking dense 26B
    "deepseek-r1:70b",                             # dense 70B thinker (heavy, deep)
    "deepseek-r1:32b",                             # Qwen 2.5 32B distill of R1
    "qwen3.6:35b-a3b",                             # MoE reasoner already in _PLANNER_PREF
    "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
    "nemotron-3-nano:30b",
    "qwen2.5:32b",                                 # last-resort dense fallback
]


# ── Editor preference ───────────────────────────────────────────────────────
# The edit endpoint returns SMALL EditPlan JSONs (usually 1-3 ops), and each
# turn benefits from a bit of reasoning about musical intent ("darker",
# "syncopated", "less busy"). MoE thinking models — 3B active — are much
# faster in wall-clock than the 32B dense composer and their thinking phase
# is short at this JSON size. If they misbehave we fall back to the compose
# ladder for the exact-same JSON-reliability reasons above.
_EDITOR_PREF = [
    "qwen3.6:35b-a3b",                             # MoE thinker — fast + creative default
    "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
    "deepseek-r1:70b",                             # dense 70B — strongest but slowest
    "nemotron-3-nano:30b",                         # NVIDIA's MoE thinker
    # From here on we fall through to the compose ladder — proven JSON emitters.
    "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
    "hermes3:8b",  "llama3.2:3b", "qwen2.5:1.5b",
]


def _first_installed(prefs: list[str], installed: set[str]) -> str | None:
    for pref in prefs:
        if pref in installed:
            return pref
        # allow a family-prefix match too (e.g. "qwen2.5:32b" ↔ "qwen2.5:32b-instruct")
        for m in installed:
            if m.startswith(pref + ":") or m == pref:
                return m
    return None


def _configured_or_ladder(configured: str, installed: set[str], ladder: list[str]) -> str | None:
    """
    Honour a user-configured model when it's actually installed; otherwise
    walk the fallback ladder. Handles the family-prefix quirk (e.g. the
    user picked 'qwen3.6:35b-a3b' but Ollama tags it as 'qwen3.6:35b-a3b-q4_K_M').
    """
    if configured:
        if configured in installed:
            return configured
        # Family-prefix match for tag variants.
        base = configured.split(":")[0]
        for m in installed:
            if m == configured or m.startswith(configured + ":") or m == base:
                return m
    return _first_installed(ladder, installed)


async def pick_model(
    installed:  set[str],
    *,
    configured: str = "",
    deep:       bool = False,
) -> str:
    """
    Model for the initial compose call.

    Resolution order:
      1. `configured` if the caller passed one AND it's installed.
      2. `_DEEP_PLANNER_PREF` if `deep=True` (Gemma 4 26B, DeepSeek R1, …).
      3. `_PLANNER_PREF` for the standard compose path.
      4. Any installed model, as a last resort.
    """
    ladder = _DEEP_PLANNER_PREF if deep else _PLANNER_PREF
    return _configured_or_ladder(configured, installed, ladder) \
        or next(iter(installed), "qwen2.5:7b")


async def pick_editor_model(
    installed:  set[str],
    *,
    configured: str = "",
) -> str:
    """Model for iterative-edit calls — favour fast MoE thinkers."""
    return _configured_or_ladder(configured, installed, _EDITOR_PREF) \
        or await pick_model(installed)


# Substrings that identify a native-thinking family. Kept in sync with
# main._supports_native_thinking — this file is composer-only and shouldn't
# import from main, so we duplicate the tiny set.
_THINKER_PATTERNS = (
    "deepseek-r1", "qwen3", "ornith", "agentworld",
    "nemotron", "gpt-oss", "gemma4", "thinking", "reasoning",
)


def _is_thinker(model: str) -> bool:
    if not model:
        return False
    lower = model.lower()
    return any(p in lower for p in _THINKER_PATTERNS)


def _pattern_vocabulary_help() -> str:
    lines: list[str] = []
    for role, names in PATTERN_HELP.items():
        lines.append(f"  {role:8s}: {' | '.join(names)}")
    return "\n".join(lines)


def _system_prompt(spec_hints: dict[str, Any]) -> str:
    return (
        "You are Persephone's music composer. Output a strict JSON SongSpec — "
        "no prose, no markdown fences. The schema is:\n"
        "{\n"
        '  "version": 1,\n'
        '  "bpm": <number>,\n'
        '  "key": {"root": "C"|"D"|…|"B", "mode": "major"|"minor"|"dorian"|…},\n'
        '  "timesig": {"num": 4, "den": 4},\n'
        '  "bars": <int total bars>,\n'
        '  "genre": "<genre string>",\n'
        '  "mood": "<one-line mood>",\n'
        '  "topic": "<one-line topic>",\n'
        '  "sections": [{"id": "s1", "name": "intro|verse|chorus|bridge|drop|outro|…", "start_bar": <int>, "length_bars": <int>, "intensity": 0.0-1.0}, …],\n'
        '  "tracks": [{\n'
        '     "id": "t1", "role": "drums|bass|chord|lead|pad|fx",\n'
        '     "name": "<display name>",\n'
        '     "mix": {"volume_db": <negative float>, "pan": -1.0…+1.0},\n'
        '     "instrument_hint": "<free text describing the sound>",\n'
        '     "clips": [{\n'
        '        "section": "<matches section id>", "bars": 4|8,\n'
        '        "pattern": "<archetype name — see vocabulary below>",\n'
        '        "notes": []\n'
        '     }, …]\n'
        '  }, …]\n'
        "}\n"
        "\n"
        "Pattern archetype vocabulary (pick from these — the note events are\n"
        "GENERATED for you by Persephone's style adapters, so you do NOT have to\n"
        "hand-write MIDI note grids. Leave `notes` as an empty array):\n"
        f"{_pattern_vocabulary_help()}\n"
        "\n"
        "For each clip, set `pattern` to one of the names for that clip's track\n"
        "role. Example clip on a drums track:\n"
        '  {"section": "verse", "bars": 4, "pattern": "boom_bap", "notes": []}\n'
        "\n"
        "MIDI conventions (only matters if you choose to write custom notes\n"
        "instead of using a pattern — mostly you should NOT):\n"
        "  - Middle C = 60. Kick=36, snare=38, closed hat=42, open hat=46, "
        "clap=39, ride=51, crash=49.\n"
        "  - Bass usually pitches 28-45 (E1-A2). Chords 48-72. Lead 60-84.\n"
        "  - Notes MUST use start/length in beats — 1.0 is a quarter note.\n"
        "  - Every track MUST have at least one clip whose `section` id matches\n"
        "    a section in the sections[] list.\n"
        "\n"
        "Musical guidance:\n"
        f"  - Genre: {spec_hints.get('genre', 'unspecified')}\n"
        f"  - Suggested BPM range: {spec_hints.get('bpm_range', '')}\n"
        f"  - Typical mode: {spec_hints.get('typical_key_mode', '')}\n"
        f"  - Typical sections: {spec_hints.get('typical_sections', '')}\n"
        f"  - Typical tracks: {spec_hints.get('typical_tracks', '')}\n"
        f"  - Notes: {spec_hints.get('notes', '')}\n"
        "\n"
        "Be musically credible: use scale-appropriate pitches, avoid dissonance "
        "unless the mood calls for it, quantise notes to 16th-note grids for "
        "drums and 8th/16th for melody. Keep the piece 24-48 bars total for a "
        "reasonable sketch length.\n"
        "\n"
        "STRICT JSON RULES:\n"
        "  - No comments, no markdown fences, no trailing commas.\n"
        "  - Every string MUST be on ONE LINE — no raw newlines inside quotes. "
        "Keep display names / instrument_hint / mood / topic short (< 80 chars).\n"
        "  - Escape any embedded quote as \\\" and any backslash as \\\\.\n"
        "  - Emit the ENTIRE JSON object; do not truncate. If a field would be\n"
        "    long, prefer a short string over a truncated one."
    )


# ── JSON salvage ────────────────────────────────────────────────────────────
# LLM-emitted JSON is rarely well-formed. Common failure modes we hit in the
# wild for the composer specifically:
#   * Truncated mid-string (num_predict ran out).
#   * Unescaped newlines inside string values (LLM writes lyrics or notes
#     with real newlines instead of \n).
#   * Wrapped in ```json ... ``` code fences despite the prompt.
#   * Trailing text after the JSON object (thinker preamble or postamble).
#   * Trailing commas before `}` / `]`.
# `_salvage_json_object` tries progressively harder to rescue a parseable
# object out of whatever the model actually produced.
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(s: str) -> str:
    s = _CODE_FENCE_RE.sub("", s.strip())
    # Also handle un-anchored fences that only appear at the top.
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s


def _extract_outermost_object(s: str) -> tuple[str, bool, int]:
    """
    Walk `s` char-by-char tracking string state + brace depth and return the
    substring from the first `{` to the matching `}`. If we run out of chars
    while still inside a string or open braces, we return what we have plus
    (truncated=True, open_braces).
    """
    start = s.find("{")
    if start < 0:
        return "", False, 0
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1], False, 0
    # Ran off the end.
    return s[start:], (in_string or depth > 0), depth if not in_string else depth


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _salvage_json_object(raw: str) -> dict[str, Any]:
    """
    Best-effort JSON parse. Raises `ValueError` if nothing parseable can be
    recovered; otherwise returns the object dict.
    """
    if not raw:
        raise ValueError("empty content")

    text = _strip_fences(raw)

    # First try: straight parse of the outermost object. Handles the happy
    # case (well-formed JSON, possibly with a preamble like thinker output).
    candidate, truncated, open_braces = _extract_outermost_object(text)
    if not candidate:
        raise ValueError("no JSON object found in response")

    for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Truncation salvage — close the open string (if any) and the unclosed
    # objects, then re-try. Also strip anything after the last comma that
    # would produce an obvious "unexpected end" error.
    if truncated:
        salvaged = candidate
        # If we're inside a string, close it.
        if salvaged.count('"') % 2 == 1:
            salvaged += '"'
        # Trim any partial ", key": that got half-written.
        salvaged = re.sub(r',\s*"[^"]*"\s*:\s*[^,{}\[\]]*$', "", salvaged)
        salvaged = re.sub(r',\s*"[^"]*"\s*:\s*$',           "", salvaged)
        salvaged = salvaged.rstrip().rstrip(",")
        salvaged += "}" * max(1, open_braces)
        salvaged = _TRAILING_COMMA_RE.sub(r"\1", salvaged)
        try:
            obj = json.loads(salvaged)
            if isinstance(obj, dict):
                log.warning("JSON salvage: repaired truncated LLM output (%d open braces)", open_braces)
                return obj
        except json.JSONDecodeError:
            pass

    # Last resort: parse-progressively-shorter — some models leak newline chars
    # into string values. Chop from the end char-by-char until we can parse.
    # Cap iterations so a broken response can't hang the request.
    limit = min(len(candidate), 4096)
    for cut in range(len(candidate) - 1, len(candidate) - limit, -1):
        trimmed = candidate[:cut].rstrip().rstrip(",")
        # Balance braces at this truncation point.
        depth = 0
        in_str = False
        esc = False
        for ch in trimmed:
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
            else:
                if ch == '"':   in_str = True
                elif ch == "{": depth += 1
                elif ch == "}": depth -= 1
        if in_str or depth < 0:
            continue
        try:
            obj = json.loads(trimmed + ("}" * depth))
            if isinstance(obj, dict):
                log.warning("JSON salvage: partial parse succeeded after trimming %d chars", len(candidate) - cut)
                return obj
        except json.JSONDecodeError:
            continue

    raise ValueError(f"unrepairable JSON — first 200 chars: {text[:200]!r}")


async def stream_compose(
    ollama_base:  str,
    model_choice: str,
    topic:        str,
    genre:        str,
    installed:    set[str],
    *,
    configured_model: str = "",
    deep:             bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield SSE-ready event dicts. Caller wraps each as `data: {json}\\n\\n`.

    `model_choice` is a per-request override (wins over everything).
    `configured_model` is the user's saved role config (from Settings).
    `deep`  swaps to `_DEEP_PLANNER_PREF` for the Deep Reasoning toggle.
    """
    yield {"stage": "picking", "message": "choosing planner model…"}
    if model_choice and (model_choice in installed
                         or any(x.startswith(model_choice + ":") for x in installed)):
        planner = model_choice
    else:
        planner = await pick_model(installed, configured=configured_model, deep=deep)
    yield {"stage": "generating", "model": planner}

    hints = dict(GENRE_PRESETS.get(genre.lower().strip(), {}))
    hints["genre"] = genre or "user-defined"

    system  = _system_prompt(hints)
    user    = (topic or "").strip() or "make me a short, coherent, playable sketch"

    # Thinker-family planner (deepseek-r1, qwen3.x, ornith, agentworld, …) —
    # if the user explicitly picked one of these, give it room to reason and
    # let it emit into `thinking`; we salvage from there below. Non-thinkers
    # get the fast dense-JSON path with think:false.
    thinker = _is_thinker(planner)
    payload = {
        "model":    planner,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "format":     "json",
        "stream":     False,
        "think":      thinker,
        "options": {
            "num_ctx":     16384 if thinker else 8192,
            "num_predict": 16384 if thinker else 6144,
            "temperature": 0.75,
        },
        # Long keep-alive for thinker models — the 43GB deepseek-r1:70b
        # takes 30-60s to memory-map on cold-load, so paying that once and
        # then holding it for 30min avoids the same cost on every edit round.
        "keep_alive": "30m" if thinker else "5m",
    }

    # Timeout budget: dense JSON planners are fast (<60s typical). Thinker
    # models — especially deepseek-r1:70b — can burn several minutes on
    # <think> before emitting the first character of visible JSON. Bump
    # generously for thinkers; the client waits for the whole non-streaming
    # response so a stingy timeout aborts before Ollama is done.
    request_timeout = 1800.0 if thinker else 300.0
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            r = await client.post(f"{ollama_base}/api/chat", json=payload)
            if r.status_code != 200:
                yield {"stage": "error",
                       "error": f"planner HTTP {r.status_code}: {r.text[:200]}"}
                return
            data    = r.json()
            msg     = data.get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                # Some thinking-enabled models still dump into "thinking".
                content = (msg.get("thinking") or "").strip()
                if not content.lstrip().startswith("{"):
                    yield {"stage": "error",
                           "error": f"empty JSON from {planner} — try qwen2.5:14b or 7b."}
                    return
    except httpx.ReadTimeout:
        log.error("planner read timeout after %ss (model=%s)", request_timeout, planner)
        yield {"stage": "error",
               "error": (f"{planner} exceeded {request_timeout:.0f}s — thinker models can "
                         "take several minutes on first run. Try again once Ollama has the "
                         "model warmed up, or switch back to qwen2.5:32b for the fast path.")}
        return
    except httpx.ConnectError as exc:
        log.error("ollama connect failed: %s", exc)
        yield {"stage": "error",
               "error": f"can't reach Ollama at {ollama_base} — is it running?"}
        return
    except Exception as exc:
        log.error("ollama call failed: %s", exc, exc_info=True)
        yield {"stage": "error",
               "error": f"planner call failed: {type(exc).__name__}: {exc}"}
        return

    # Robust parse — LLMs routinely emit malformed JSON (truncated strings,
    # unescaped newlines, trailing text). Salvage does its best; if it still
    # fails, we surface a useful error including the model name so the user
    # knows to try a different composer or bump num_predict.
    try:
        obj = _salvage_json_object(content)
        spec: SongSpec = parse_song_spec(obj)
    except Exception as exc:
        log.warning("SongSpec parse failed: %s. Raw content: %s", exc, content[:400])
        yield {"stage": "error",
               "error": (f"{planner} emitted invalid SongSpec — {exc}. "
                         "Try Deep Reasoning off, or a different composer model in Settings.")}
        return

    # Sanity floors — if the LLM emits garbage, back it up with genre defaults.
    if spec.bpm < 40 or spec.bpm > 220:
        default_range = hints.get("bpm_range", (90, 110))
        spec.bpm = float(sum(default_range)) / 2
    if not spec.sections:
        spec.sections = _default_sections(spec.bars, hints)
    if not spec.tracks:
        yield {"stage": "error", "error": "spec has no tracks"}
        return

    # LLMs at this scale usually emit an empty `notes` list for most clips —
    # generating hundreds of accurate MIDI events per response blows the
    # token budget. Programmatic patterns from music_theory make the sketch
    # actually play music. LLM keeps its authority over BPM, key, sections,
    # tracks, and roles; we fill in the drum grid and chord voicings.
    filled_clips, added_notes = fill_missing_notes(spec)
    if filled_clips > 0:
        log.info(
            "note fallback: filled %d clip(s) with %d generated notes",
            filled_clips, added_notes,
        )
        yield {
            "stage": "fallback_notes",
            "message": f"seeded {added_notes} notes across {filled_clips} clips (LLM left them empty)",
            "clips_filled": filled_clips,
            "notes_added":  added_notes,
        }

    yield {"stage": "spec", "spec": _spec_to_dict(spec)}


def _spec_to_dict(spec: SongSpec) -> dict[str, Any]:
    """Round-trip through JSON so downstream sees plain dicts (no dataclass)."""
    return json.loads(spec.to_json())


# ── Track-first composer ────────────────────────────────────────────────────
def _add_track_system_prompt(spec: SongSpec, role: str, description: str) -> str:
    """
    Prompt the LLM to propose ONE track (with clips) that fits into the given
    song's tempo/key/timesig/sections. Vocabulary and MIDI conventions are
    the same as the full composer.
    """
    section_ids = ", ".join(s.id for s in spec.sections) or "main"
    return (
        "You are Persephone's per-track composer. The user already has a song "
        "in progress; you propose ONE new track that fits inside it.\n"
        "\n"
        f"Current song context:\n"
        f"  BPM: {spec.bpm}\n"
        f"  Key: {spec.key.root} {spec.key.mode}\n"
        f"  Time signature: {spec.timesig.num}/{spec.timesig.den}\n"
        f"  Total bars: {spec.bars}\n"
        f"  Genre: {spec.genre or 'unspecified'}\n"
        f"  Section ids you may write clips for: {section_ids}\n"
        f"  Existing track roles: {', '.join(t.role for t in spec.tracks) or 'none yet'}\n"
        f"  New track role requested: {role}\n"
        f"  User intent: {description or f'add a {role} track that fits'}\n"
        "\n"
        "Output STRICT JSON — one track only, matching this shape:\n"
        "{\n"
        '  "id":   "<lowercase-slug>",\n'
        '  "role": "drums|bass|chord|lead|pad|fx|vox",\n'
        '  "name": "<display name>",\n'
        '  "mix":  {"volume_db": <negative float>, "pan": -1.0…+1.0},\n'
        '  "instrument_hint": "<free text describing the sound>",\n'
        '  "clips": [\n'
        '     {"section": "<one of the section ids above>",\n'
        '      "bars":    4|8,\n'
        '      "pattern": "<archetype name — see vocabulary below>",\n'
        '      "notes":   []}\n'
        "  ]\n"
        "}\n"
        "\n"
        "Pattern archetype vocabulary (leave notes: [] — Persephone generates the MIDI):\n"
        f"{_pattern_vocabulary_help()}\n"
        "\n"
        "Rules:\n"
        f"  - Every clip's `section` MUST be one of: {section_ids}.\n"
        "  - Prefer at least one clip per section that this role should sound in.\n"
        "  - `id` must be unique within the song — pick something like "
        '"bass2", "pad_air", etc.\n'
        "  - No prose, no markdown fences. JSON only."
    )


async def stream_add_track(
    ollama_base:  str,
    model_choice: str,
    role:         str,
    description:  str,
    current_spec: SongSpec,
    installed:    set[str],
    *,
    configured_model: str = "",
    deep:             bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """
    LLM proposes a single new track that fits into `current_spec`. Emits SSE
    events matching the compose flow: picking → generating → track (payload)
    → error?. Note materialisation via style_adapters happens client-side of
    this stream so the caller can splice the track into the session first.

    Same model-resolution rules as `stream_compose`.
    """
    yield {"stage": "picking", "message": "choosing track composer…"}
    if model_choice and (model_choice in installed
                         or any(x.startswith(model_choice + ":") for x in installed)):
        planner = model_choice
    else:
        planner = await pick_model(installed, configured=configured_model, deep=deep)
    yield {"stage": "generating", "model": planner, "role": role}

    system = _add_track_system_prompt(current_spec, role, description)
    user   = description.strip() or f"add a {role} track that fits the song"

    thinker = _is_thinker(planner)
    payload = {
        "model":    planner,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "format":     "json",
        "stream":     False,
        "think":      thinker,
        "options": {
            "num_ctx":     16384 if thinker else 8192,
            "num_predict":  8192 if thinker else 3072,   # one track ≪ one song
            "temperature": 0.75,
        },
        "keep_alive": "30m" if thinker else "5m",
    }
    request_timeout = 1800.0 if thinker else 240.0
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            r = await client.post(f"{ollama_base}/api/chat", json=payload)
            if r.status_code != 200:
                yield {"stage": "error",
                       "error": f"add-track HTTP {r.status_code}: {r.text[:200]}"}
                return
            data    = r.json()
            msg     = data.get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                content = (msg.get("thinking") or "").strip()
                if not content.lstrip().startswith("{"):
                    yield {"stage": "error",
                           "error": f"empty track JSON from {planner}."}
                    return
    except httpx.ReadTimeout:
        yield {"stage": "error",
               "error": f"{planner} exceeded {request_timeout:.0f}s on add-track."}
        return
    except Exception as exc:
        log.error("add-track ollama call failed: %s", exc, exc_info=True)
        yield {"stage": "error",
               "error": f"add-track call failed: {type(exc).__name__}: {exc}"}
        return

    # Parse the track dict + materialise its notes via style_adapters. Same
    # salvage layer as the whole-song composer — truncated strings, code
    # fences, and unescaped newlines all rescued.
    try:
        track_raw = _salvage_json_object(content)
    except Exception as exc:
        yield {"stage": "error",
               "error": (f"{planner} emitted invalid track JSON — {exc}. "
                         "Try Deep Reasoning off, or a different composer model in Settings.")}
        return

    # Basic shape validation — the frontend and session code assume these fields.
    if not isinstance(track_raw, dict) or "role" not in track_raw:
        yield {"stage": "error", "error": "track JSON missing required fields"}
        return

    # Ensure a unique id — the LLM sometimes reuses ones from the existing spec.
    existing_ids = {t.id for t in current_spec.tracks}
    proposed_id  = str(track_raw.get("id") or "").strip() or f"t{len(current_spec.tracks) + 1}"
    if proposed_id in existing_ids:
        n = len(current_spec.tracks) + 1
        while f"t{n}" in existing_ids:
            n += 1
        proposed_id = f"t{n}"
    track_raw["id"] = proposed_id

    # Splice into a temporary spec so fill_missing_notes can materialise
    # THIS track's patterns without touching existing tracks.
    tmp_dict = _spec_to_dict(current_spec)
    tmp_dict["tracks"] = list(tmp_dict.get("tracks") or []) + [track_raw]
    try:
        tmp_spec = parse_song_spec(tmp_dict)
    except Exception as exc:
        yield {"stage": "error", "error": f"splice failed: {exc}"}
        return
    filled_clips, added_notes = fill_missing_notes(tmp_spec)
    if filled_clips > 0:
        log.info("add-track: filled %d clip(s) with %d notes", filled_clips, added_notes)

    # Emit the materialised track back — the endpoint will splice it into
    # the session and echo the updated full spec.
    final_track = next(
        (t for t in _spec_to_dict(tmp_spec).get("tracks", []) if t.get("id") == proposed_id),
        track_raw,
    )
    yield {
        "stage":   "track",
        "track":   final_track,
        "message": f"proposed {proposed_id} ({role}) with {added_notes} notes",
    }


def _default_sections(bars: int, hints: dict[str, Any]) -> list:
    from song_spec import Section
    names = hints.get("typical_sections") or ["intro", "verse", "chorus", "outro"]
    per   = max(4, bars // max(1, len(names)))
    out   = []
    for i, name in enumerate(names):
        out.append(Section(
            id          = f"s{i+1}",
            name        = name,
            start_bar   = i * per,
            length_bars = per,
            intensity   = 0.3 + 0.15 * i,
        ))
    return out
