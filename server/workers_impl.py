"""
Concrete worker implementations.

Kept in a separate module from `workers.py` so the scheduler code can be
reasoned about independently of the domain-specific work each worker does.
Import this module at boot to register the workers with the scheduler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from difflib import SequenceMatcher

import aiosqlite
import httpx

import db as _db
import workers as _workers
from workers import Worker, register

log = logging.getLogger("workers_impl")


# ── Shared helpers ──────────────────────────────────────────────────────────
def _ollama_base() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


async def _installed_models() -> set[str]:
    """Ollama's currently-installed model tags."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{_ollama_base()}/api/tags")
            return {m["name"] for m in (r.json().get("models") or [])}
    except Exception:
        return set()


def _first_installed(prefs: list[str], installed: set[str]) -> str | None:
    for p in prefs:
        if not p:
            continue
        if p in installed:
            return p
        for m in installed:
            if m.startswith(p + ":") or m == p:
                return m
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Memory Curator
# ─────────────────────────────────────────────────────────────────────────────
# Runs every 15 minutes when the user is idle. Two jobs:
#   1. Dedupe near-duplicate facts. The `user_facts` table has a NOCASE unique
#      constraint, but that only catches exact matches. In practice we get
#      pairs like "The user is named Michel" + "The user's name is Michel" —
#      both survive the constraint but say the same thing. Curator uses
#      difflib.SequenceMatcher on the trimmed fact text to find near-dupes
#      (ratio > 0.85), keeps the higher-confidence one, and deletes the other.
#   2. Re-extract facts from conversations that were "rushed" — short user
#      turns and quick assistant replies where the live fact-extractor may
#      have skipped context. Runs the same fact-extract prompt as the online
#      extractor, adds only new facts.
_CURATOR_INTERVAL_S = 15 * 60   # 15 minutes
_DEDUP_RATIO        = 0.85      # SequenceMatcher threshold for "same fact"
_MIN_CONV_TURNS     = 4         # skip conversations shorter than this
_MAX_CONVS_PER_RUN  = 5         # cap so a single run finishes fast
_RECENT_DAYS        = 14        # only look at conversations from last 2 weeks

# Same prompt as main.py's live extractor. Kept in sync manually — if you
# change one, change both. Duplicated instead of imported to avoid the
# main → workers → main import cycle.
_FACT_EXTRACT_PROMPT = (
    "Extract DURABLE facts about the HUMAN USER from this conversation.\n"
    "\n"
    "ABSOLUTELY DO NOT extract:\n"
    "  - Anything the ASSISTANT said about itself.\n"
    "  - Greetings, acks, throwaway lines.\n"
    "  - Time-sensitive info ('I'm tired today').\n"
    "  - Hypotheticals, questions, things the user ASKED ABOUT.\n"
    "\n"
    "DO extract things the USER explicitly stated about themselves:\n"
    "  name, location, age, job, family, hobbies, preferences, projects,\n"
    "  hardware they own, ongoing concerns they mentioned.\n"
    "\n"
    "Format: third person, one sentence per fact.\n"
    "\n"
    "Output STRICT JSON: {\"facts\":[{\"fact\":\"…\",\"category\":\"…\"}]}.\n"
    "Categories: name, location, work, preferences, family, projects, hardware, other.\n"
    "Return {\"facts\":[]} if nothing durable was learned. Never invent."
)

_MEMORY_MODEL_PREFS = [
    "qwen2.5:0.5b", "qwen2.5:1.5b", "llama3.2:1b", "qwen2.5:3b",
    "llama3.2:3b", "qwen2.5:7b",
]


async def _pick_memory_model() -> str | None:
    """Prefer the user's configured memory_model, then the small-model ladder."""
    cfg = (await _db.get_config("memory_model")) or ""
    installed = await _installed_models()
    prefs = [cfg] + _MEMORY_MODEL_PREFS
    return _first_installed(prefs, installed)


def _normalize_fact(fact: str) -> str:
    """Lowercase + strip punctuation for the dedup similarity comparison."""
    s = fact.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s)


async def _dedup_facts() -> dict:
    """
    Find near-duplicate pairs and keep only the higher-confidence one.
    Returns {"scanned": N, "removed": M}.
    """
    facts = await _db.list_user_facts(limit=500)
    if len(facts) < 2:
        return {"scanned": len(facts), "removed": 0}

    # Sort so higher-confidence facts come first — when we find a dupe we
    # keep the one earlier in the list.
    facts.sort(key=lambda f: (-float(f["confidence"] or 0.0), f["createdAt"]))

    keepers: list[dict] = []
    removed_ids: list[int] = []
    for candidate in facts:
        c_norm = _normalize_fact(candidate["fact"])
        if not c_norm:
            continue
        is_dupe = False
        for kept in keepers:
            k_norm = _normalize_fact(kept["fact"])
            if not k_norm:
                continue
            # Cheap prefilter — reject obviously different lengths before
            # running the expensive matcher.
            if abs(len(c_norm) - len(k_norm)) > max(len(c_norm), len(k_norm)) * 0.5:
                continue
            ratio = SequenceMatcher(None, c_norm, k_norm).ratio()
            if ratio >= _DEDUP_RATIO:
                is_dupe = True
                break
        if is_dupe:
            removed_ids.append(int(candidate["id"]))
        else:
            keepers.append(candidate)

    for fid in removed_ids:
        try:
            await _db.delete_user_fact(fid)
        except Exception:
            pass
    return {"scanned": len(facts), "removed": len(removed_ids)}


async def _recent_conversations_needing_reextract(cutoff_ts: float) -> list[str]:
    """
    Return conversation ids from the last `_RECENT_DAYS` that look like
    good candidates for re-extraction: substantive (≥ _MIN_CONV_TURNS
    messages) and where no facts were sourced from them yet.
    """
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        # Conversations already contributing at least one fact — skip those.
        async with conn.execute(
            "SELECT DISTINCT source_conv FROM user_facts WHERE source_conv IS NOT NULL"
        ) as cur:
            seen_convs = {r["source_conv"] for r in await cur.fetchall() if r["source_conv"]}
        # Conversations with enough messages, recent, not already contributing.
        async with conn.execute(
            """SELECT c.id, COUNT(m.id) as n_msgs
               FROM   conversations c
               JOIN   messages m ON m.conversation_id = c.id
               WHERE  c.updated_at >= ?
               GROUP  BY c.id
               HAVING n_msgs >= ?
               ORDER  BY c.updated_at DESC
               LIMIT  50""",
            (cutoff_ts, _MIN_CONV_TURNS),
        ) as cur:
            rows = await cur.fetchall()
    return [r["id"] for r in rows if r["id"] not in seen_convs][:_MAX_CONVS_PER_RUN]


async def _fetch_conv_turns(conv_id: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT role, content FROM messages
               WHERE conversation_id=?
               ORDER BY timestamp ASC LIMIT ?""",
            (conv_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [{"role": r["role"], "content": r["content"] or ""} for r in rows]


async def _reextract_from_conversation(conv_id: str, model: str) -> int:
    """Run the fact extractor over one conversation. Returns # facts added."""
    turns = await _fetch_conv_turns(conv_id)
    if not turns:
        return 0
    # Compact transcript — keep only user + assistant, cap each turn.
    transcript_parts: list[str] = []
    for t in turns:
        role = t["role"]
        if role not in ("user", "assistant"):
            continue
        content = (t.get("content") or "").strip()[:600]
        if content:
            transcript_parts.append(f"{role.upper()}: {content}")
    transcript = "\n\n".join(transcript_parts)
    if not transcript:
        return 0

    payload = {
        "model":     model,
        "messages": [
            {"role": "system", "content": _FACT_EXTRACT_PROMPT},
            {"role": "user",   "content": f"CONVERSATION:\n{transcript}"},
        ],
        "format":     "json",
        "stream":     False,
        "keep_alive": "30s",
        "options":    {"temperature": 0.0, "num_predict": 512, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(f"{_ollama_base()}/api/chat", json=payload)
            if r.status_code != 200:
                return 0
            raw = ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception:
        return 0

    try:
        parsed = json.loads(raw)
    except Exception:
        return 0
    facts_out = parsed.get("facts") if isinstance(parsed, dict) else None
    if not isinstance(facts_out, list):
        return 0

    added = 0
    for entry in facts_out[:12]:  # cap per-conversation
        if not isinstance(entry, dict):
            continue
        fact = str(entry.get("fact") or "").strip()
        cat  = str(entry.get("category") or "other").strip().lower() or "other"
        if not fact or len(fact) < 8 or len(fact) > 240:
            continue
        rowid = await _db.add_user_fact(
            fact         = fact,
            category     = cat,
            confidence   = 0.7,          # slightly lower than online — retrospective
            source_conv  = conv_id,
            source_msg   = None,
        )
        if rowid is not None:
            added += 1
    return added


async def memory_curator() -> dict:
    """
    Combined pass: dedupe existing facts, then re-extract from a few
    substantive-but-empty conversations. Returns a summary dict.
    """
    dedup = await _dedup_facts()
    reextract = {"conversations": 0, "new_facts": 0, "model": ""}
    model = await _pick_memory_model()
    if model:
        cutoff = time.time() - _RECENT_DAYS * 86400
        conv_ids = await _recent_conversations_needing_reextract(cutoff)
        added_total = 0
        for cid in conv_ids:
            added_total += await _reextract_from_conversation(cid, model)
            # Yield between conversations so a slow model doesn't hog the loop.
            await asyncio.sleep(0.05)
        reextract = {"conversations": len(conv_ids), "new_facts": added_total, "model": model}
    return {"dedup": dedup, "reextract": reextract}


# ─────────────────────────────────────────────────────────────────────────────
# Model Warmer
# ─────────────────────────────────────────────────────────────────────────────
# Not a "real" worker — just periodically pokes Ollama with a 1-token request
# so the currently-active chat model stays memory-resident. Skips cold-load
# cost on the next real turn, which for models like Agents-A1 / R1:70b /
# Llama3.3:70b is 30-90 seconds.
_WARMER_INTERVAL_S = 8 * 60  # 8 minutes; Ollama's default keep_alive is 5m


async def model_warmer() -> dict:
    active_model = (await _db.get_config("active_model")) or ""
    if not active_model:
        return {"skipped": "no active_model configured"}
    installed = await _installed_models()
    # Family-prefix match — the user's stored id might be a base name.
    picked = active_model
    if picked not in installed:
        for m in installed:
            if m.startswith(active_model + ":") or m == active_model:
                picked = m
                break
        else:
            return {"skipped": f"active_model {active_model!r} not installed"}

    payload = {
        "model":    picked,
        "messages": [{"role": "user", "content": "."}],
        "stream":   False,
        # 10min keep_alive so the model stays resident until the next warm ping.
        # We fire every 8min so there's a 2min overlap; if the user chats in
        # between, their /api/chat uses its own keep_alive and this ping is
        # effectively a no-op.
        "keep_alive": "10m",
        "options":  {"num_predict": 1, "num_ctx": 512, "temperature": 0.0},
    }
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{_ollama_base()}/api/chat", json=payload)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                return {"model": picked, "ok": False,
                        "latency_ms": latency_ms,
                        "error": f"HTTP {r.status_code}"}
            return {"model": picked, "ok": True, "latency_ms": latency_ms}
    except Exception as exc:
        return {"model": picked, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": int((time.monotonic() - t0) * 1000)}


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────
def register_workers() -> None:
    """Call once at process startup. Idempotent — re-registration is a no-op."""
    register(Worker(
        id               = "memory_curator",
        name             = "Memory Curator",
        description      = (
            "Every 15 minutes when idle: dedupes near-duplicate facts and "
            "re-extracts from substantive conversations that had no facts. "
            "Uses your configured memory model (or the smallest installed)."
        ),
        interval_seconds = _CURATOR_INTERVAL_S,
        fn               = memory_curator,
    ))
    register(Worker(
        id               = "model_warmer",
        name             = "Model Warmer",
        description      = (
            "Every 8 minutes: pings the active chat model with a 1-token "
            "request so it stays memory-resident. Skips the 30-90s cold-load "
            "on your next turn (matters for Agents-A1, R1:70b, Llama3.3:70b)."
        ),
        interval_seconds = _WARMER_INTERVAL_S,
        fn               = model_warmer,
    ))
