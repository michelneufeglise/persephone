"""
Tiny Ollama-embedding wrapper used by the research knowledge base.

Wraps `/api/embed` (batch endpoint) for `mxbai-embed-large` (1024-dim).
"""

from __future__ import annotations

import os
import struct
import logging
from typing import Iterable

import httpx

log = logging.getLogger("embeddings")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = "mxbai-embed-large"
EMBED_DIM   = 1024


async def embed_batch(texts: list[str], model: str = EMBED_MODEL) -> list[list[float]]:
    """Return one 1024-dim embedding per input text. Strips empties."""
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return []
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": model, "input": cleaned},
        )
        if r.status_code != 200:
            log.warning("embed failed %d: %s", r.status_code, r.text[:200])
            return []
        data = r.json()
        embs = data.get("embeddings") or []
        return embs


async def embed_one(text: str, model: str = EMBED_MODEL) -> list[float]:
    result = await embed_batch([text], model=model)
    return result[0] if result else []


def to_blob(vec: Iterable[float]) -> bytes:
    """Serialize a float vector for `sqlite-vec`'s vec0 BLOB format."""
    arr = list(vec)
    return struct.pack(f"{len(arr)}f", *arr)


def from_blob(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))
