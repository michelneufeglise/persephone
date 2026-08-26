"""
Ollama-embedding wrapper used by the research KB and the document RAG index.

The active embedding model is configurable at runtime (set from the user's
`embed_model` role) but the sqlite-vec tables are fixed at EMBED_DIM (1024).
To stay robust when a user selects a model with a different native dimension,
every returned vector is coerced to EMBED_DIM (truncate if longer, zero-pad if
shorter) so an index insert can never crash on a dimension mismatch. If the
configured model is missing/unreachable we fall back to the default model.
"""

from __future__ import annotations

import os
import struct
import logging
from typing import Iterable

import httpx

log = logging.getLogger("embeddings")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_EMBED_MODEL = "mxbai-embed-large"
EMBED_MODEL = DEFAULT_EMBED_MODEL   # back-compat alias for existing imports
EMBED_DIM   = 1024

# Runtime-active embedding model (settable from main.py once config is read).
_ACTIVE_EMBED_MODEL = DEFAULT_EMBED_MODEL


def set_embed_model(name: str) -> None:
    """Set the active embedding model (called at startup + on role update)."""
    global _ACTIVE_EMBED_MODEL
    _ACTIVE_EMBED_MODEL = (name or "").strip() or DEFAULT_EMBED_MODEL
    log.info("active embedding model set to %s", _ACTIVE_EMBED_MODEL)


def get_embed_model() -> str:
    return _ACTIVE_EMBED_MODEL


def _coerce_dim(vec: list[float]) -> list[float]:
    """Force a vector to exactly EMBED_DIM: truncate if longer, zero-pad if shorter."""
    n = len(vec)
    if n == EMBED_DIM:
        return vec
    if n > EMBED_DIM:
        return vec[:EMBED_DIM]
    return list(vec) + [0.0] * (EMBED_DIM - n)


async def _post_embed(model: str, inputs: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": model, "input": inputs},
        )
        if r.status_code != 200:
            log.warning("embed failed %d (model=%s): %s", r.status_code, model, r.text[:200])
            return []
        return r.json().get("embeddings") or []


async def embed_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Return one EMBED_DIM embedding per input text. Strips empties.

    Uses the active embed model by default. Falls back to DEFAULT_EMBED_MODEL
    if the chosen model returns nothing (e.g. not installed). All vectors are
    coerced to EMBED_DIM so downstream sqlite-vec inserts never crash.
    """
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return []
    chosen = (model or _ACTIVE_EMBED_MODEL or DEFAULT_EMBED_MODEL).strip()
    try:
        embs = await _post_embed(chosen, cleaned)
    except httpx.RequestError as exc:
        log.warning("embed request error (model=%s): %s", chosen, exc)
        embs = []
    if not embs and chosen != DEFAULT_EMBED_MODEL:
        log.info("embed fallback: %s -> %s", chosen, DEFAULT_EMBED_MODEL)
        try:
            embs = await _post_embed(DEFAULT_EMBED_MODEL, cleaned)
        except httpx.RequestError as exc:
            log.warning("embed fallback request error: %s", exc)
            embs = []
    return [_coerce_dim(e) for e in embs if e]


async def embed_one(text: str, model: str | None = None) -> list[float]:
    result = await embed_batch([text], model=model)
    return result[0] if result else []


def to_blob(vec: Iterable[float]) -> bytes:
    """Serialize a float vector for `sqlite-vec`'s vec0 BLOB format."""
    arr = list(vec)
    return struct.pack(f"{len(arr)}f", *arr)


def from_blob(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))
