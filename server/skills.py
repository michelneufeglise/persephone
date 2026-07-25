"""
Persephone skills — reusable instruction bundles the model can invoke.

A "skill" is a markdown file with YAML-ish frontmatter that declares:
- a name (kebab-case identifier)
- a short description (what it does, one sentence)
- optional trigger keywords for cheap heuristic pre-filtering
- an optional category
- an enabled flag (soft default; user can override per-installation)

The body of the file is the actual guidance the model receives when the skill
is activated — written as if speaking directly to the assistant.

## Layout

    server/skills/                  ← ships with Persephone
        weather-forecast.md
        code-review.md
        ...
    ~/.persephone/skills/           ← user-authored, hot-loaded on startup

## Selection

`select_skills(user_text)` runs a two-stage pipeline:

1. **Heuristic pre-filter** — regex/keyword match narrows the pool to at most
   ~6 candidates in <1 ms. If nothing matches, we short-circuit and skip the
   judge model entirely (0 skills, no latency added).
2. **Judge pass** — a tiny model (same class as the auto-router judge) reads
   the user turn plus the candidate list and emits a strict-JSON array of
   up to 3 skill names to activate. Bounded output keeps the pass ~200 ms
   on M-series hardware.

The result is memoised per conversation-turn hash so re-renders (streaming
chunks, memory queries) don't re-run the judge.

## Injection

`inject_into_system(system_prompt, selected)` appends a compact block
listing each active skill's body under a `## Skill: {name}` header. Order
is stable so caching in downstream tokenisers stays warm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

log = logging.getLogger("persephone.skills")

# Files ignored by the discovery walker even if they live in a skill dir.
_IGNORED_FILENAMES = {"readme.md", "index.md"}

# Where user-editable skills live. Same base path other Persephone data uses.
USER_SKILLS_DIR = Path(os.path.expanduser("~/.persephone/skills"))
# Bundled skills, resolved relative to this file.
BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"

# Cap on how many skills can be simultaneously active on one turn — the
# selector is instructed to pick <= this many. Keeps the injected block
# tight so we don't blow the context budget on a long user turn.
MAX_ACTIVE_SKILLS = 3

# Judge model selection reuses the same fallback ladder as other judges.
_JUDGE_MODEL_FALLBACKS = (
    "qwen2.5:1.5b", "qwen2.5:0.5b",
    "llama3.2:1b", "llama3.2:3b",
    "qwen2.5:3b",  "qwen2.5:7b",
)

# In-process cache — filename → mtime → parsed Skill. Re-parsed on file
# change, cheap enough to skip a full disk walk on every request.
_cache: dict[str, "Skill"] = {}
_cache_mtime: dict[str, float] = {}
_selection_cache: dict[str, tuple[float, list[str]]] = {}
_SELECTION_TTL = 30.0  # seconds


@dataclass
class Skill:
    name: str
    description: str
    body: str
    keywords: list[str] = field(default_factory=list)
    category: str = "general"
    source: str = "builtin"   # 'builtin' or 'user'
    path: str = ""            # for the UI to show provenance
    default_enabled: bool = True

    def matches_heuristic(self, text: str) -> bool:
        """Cheap keyword pre-filter. Case-insensitive substring match."""
        if not self.keywords:
            return False
        low = text.lower()
        return any(k.lower() in low for k in self.keywords)


# ── Parsing ────────────────────────────────────────────────────────────────
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Extract the YAML frontmatter block plus the body underneath.
    Uses PyYAML's safe_load so nested maps + block arrays "just work".
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()

    fm_raw, body = m.group(1), m.group(2).strip()
    try:
        data = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as exc:
        log.warning("frontmatter parse failed: %s", exc)
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _load_skill(path: Path, source: str) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("skill %s: unreadable (%s)", path, exc)
        return None

    fm, body = _parse_frontmatter(raw)
    name = str(fm.get("name") or path.stem).strip()
    if not name:
        return None

    triggers = fm.get("triggers") or {}
    if isinstance(triggers, list):
        keywords = [str(x) for x in triggers]
    elif isinstance(triggers, dict):
        keywords = [str(x) for x in triggers.get("keywords", [])]
    else:
        keywords = []

    return Skill(
        name=name,
        description=str(fm.get("description") or "").strip(),
        body=body,
        keywords=keywords,
        category=str(fm.get("category") or "general"),
        source=source,
        path=str(path),
        default_enabled=bool(fm.get("enabled", True)),
    )


def _discover() -> list[Skill]:
    """Walk skill dirs, load or refresh anything with a changed mtime."""
    dirs = [
        (BUILTIN_SKILLS_DIR, "builtin"),
        (USER_SKILLS_DIR,    "user"),
    ]
    seen: set[str] = set()
    skills: list[Skill] = []
    for base, source in dirs:
        if not base.exists():
            continue
        for path in sorted(base.glob("*.md")):
            if path.name.lower() in _IGNORED_FILENAMES:
                continue
            key = str(path.resolve())
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if _cache_mtime.get(key) != mtime:
                sk = _load_skill(path, source)
                if sk is None:
                    continue
                _cache[key] = sk
                _cache_mtime[key] = mtime
            sk = _cache.get(key)
            if sk and sk.name not in seen:
                seen.add(sk.name)
                skills.append(sk)
    return skills


# ── Public API ─────────────────────────────────────────────────────────────
def list_skills() -> list[Skill]:
    """All discovered skills. Cheap — walks the filesystem but caches."""
    return _discover()


def get_skill(name: str) -> Skill | None:
    for sk in _discover():
        if sk.name == name:
            return sk
    return None


async def _enabled_set(db) -> set[str]:
    """
    Load the user-toggled overrides. Value stored as JSON list of names to
    treat as DISABLED. Empty / missing = every skill runs at its default.
    """
    raw = await db.get_config("skills_disabled")
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except Exception:
        return set()


async def is_enabled(name: str, db) -> bool:
    sk = get_skill(name)
    if not sk:
        return False
    disabled = await _enabled_set(db)
    if name in disabled:
        return False
    return sk.default_enabled


async def set_enabled(name: str, enabled: bool, db) -> None:
    disabled = await _enabled_set(db)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    await db.set_config("skills_disabled", json.dumps(sorted(disabled)))


# ── Selector ───────────────────────────────────────────────────────────────
_SELECTOR_PROMPT = (
    "You are a skill selector. Given a user's request and a menu of skills, "
    "pick which skills would meaningfully improve the assistant's reply. "
    "Skills are optional — most turns need ZERO. Only pick a skill if its "
    "description clearly applies to what the user is asking. Prefer fewer "
    "over more. Never invent skill names.\n"
    "\n"
    f"Output STRICT JSON: {{\"skills\": [\"name1\", \"name2\"]}} (up to {MAX_ACTIVE_SKILLS}). "
    "Empty list is the correct answer when nothing fits."
)


def _selector_format(candidate_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "skills": {
                "type":  "array",
                "items": {"type": "string", "enum": candidate_names},
                "maxItems": MAX_ACTIVE_SKILLS,
            },
        },
        "required": ["skills"],
    }


def _pick_first(prefs: list[str], installed: set[str]) -> str | None:
    for m in prefs:
        if m and m in installed:
            return m
    return None


async def select_skills(
    user_text: str,
    *,
    db,
    installed_models: set[str],
    ollama_base: str,
) -> list[Skill]:
    """
    Pick 0..MAX_ACTIVE_SKILLS skills for this user turn.

    Two-stage:
      1) Heuristic keyword pre-filter narrows candidates. If empty, return [].
      2) Judge model picks a subset (or nothing) with a strict-JSON schema.

    Results are memoised per user_text for _SELECTION_TTL seconds so
    duplicate calls from parallel context-builders don't re-hit the judge.
    """
    text = (user_text or "").strip()
    if not text:
        return []

    now = time.monotonic()
    cache_key = text[:800]
    cached = _selection_cache.get(cache_key)
    if cached and now - cached[0] < _SELECTION_TTL:
        return [sk for sk in (get_skill(n) for n in cached[1]) if sk]

    disabled = await _enabled_set(db)
    all_skills = [
        sk for sk in list_skills()
        if sk.default_enabled and sk.name not in disabled
    ]
    if not all_skills:
        return []

    candidates = [sk for sk in all_skills if sk.matches_heuristic(text)]
    if not candidates:
        _selection_cache[cache_key] = (now, [])
        return []

    if len(candidates) == 1:
        # Trivial case — skip the judge, the heuristic already answered.
        _selection_cache[cache_key] = (now, [candidates[0].name])
        return candidates

    # Ask the judge to whittle it down.
    user_pref = (await db.get_config("judge_model")) or ""
    model = _pick_first([user_pref, *_JUDGE_MODEL_FALLBACKS], installed_models)
    if not model:
        # No judge available — fall back to the top heuristic hits (at most
        # MAX_ACTIVE_SKILLS, in filename order).
        picked = candidates[:MAX_ACTIVE_SKILLS]
        _selection_cache[cache_key] = (now, [sk.name for sk in picked])
        return picked

    menu = "\n".join(
        f"- `{sk.name}` — {sk.description or '(no description)'}"
        for sk in candidates
    )
    candidate_names = [sk.name for sk in candidates]

    payload = {
        "model":     model,
        "messages": [
            {"role": "system", "content": _SELECTOR_PROMPT + "\n\nMenu:\n" + menu},
            {"role": "user",   "content": text[:800]},
        ],
        "stream":    False,
        "keep_alive": "5m",
        "format":    _selector_format(candidate_names),
        "options":   {"temperature": 0.0, "num_predict": 60, "num_ctx": 2048},
    }

    picked_names: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{ollama_base}/api/chat", json=payload)
            if r.status_code == 200:
                raw = ((r.json().get("message") or {}).get("content") or "").strip()
                parsed = json.loads(raw)
                picked_names = [
                    n for n in (parsed.get("skills") or [])
                    if isinstance(n, str) and n in candidate_names
                ][:MAX_ACTIVE_SKILLS]
    except (httpx.HTTPError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
        log.debug("skill selector failed, falling back to heuristic: %s", exc)
        picked_names = [sk.name for sk in candidates[:MAX_ACTIVE_SKILLS]]

    _selection_cache[cache_key] = (now, picked_names)
    by_name = {sk.name: sk for sk in candidates}
    return [by_name[n] for n in picked_names if n in by_name]


# ── Injection ──────────────────────────────────────────────────────────────
def inject_into_system(system_prompt: str, selected: list[Skill]) -> str:
    """
    Append a compact `## Active skills` block. Called once per turn from
    `_augment_messages` in main.py. Ordering is stable (as given).
    """
    if not selected:
        return system_prompt

    blocks = []
    for sk in selected:
        blocks.append(
            f"### Skill: {sk.name}\n"
            f"_{sk.description}_\n\n"
            f"{sk.body.strip()}"
        )
    body = "\n\n---\n\n".join(blocks)
    addendum = (
        "\n\n## Active skills\n"
        "The following skills apply to this turn. Follow their guidance where "
        "it's relevant; ignore what doesn't fit the specific question.\n\n"
        f"{body}"
    )
    return (system_prompt or "").rstrip() + addendum
