"""
Per-document RAG index for multi-document Q&A.

Mirrors research_db.py's sqlite-vec pattern, but keyed by document id so
semantic search can be filtered to just the documents the user selected in
the Documents panel. Uses the same SQLite file as db.py / research_db.py.

Tables
──────
  doc_chunks      — text chunks with provenance back to an IDP document id
  doc_chunks_vec  — sqlite-vec virtual table, one embedding per chunk
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3

try:
    import sqlite_vec  # type: ignore
    _HAS_VEC = True
except Exception:
    _HAS_VEC = False

from embeddings import to_blob, EMBED_DIM, embed_batch, embed_one
from paths import db_path

log = logging.getLogger("doc_index")

DB_PATH = db_path()

CHUNK_TARGET_CHARS = 1200  # roughly 250-300 tokens per chunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    text         TEXT    NOT NULL,
    content_hash TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(doc_id);
"""

_VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_vec USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[{EMBED_DIM}]
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    if _HAS_VEC:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as exc:
            log.warning("sqlite-vec load failed: %s", exc)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_sync() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        if _HAS_VEC:
            conn.executescript(_VEC_SCHEMA)
            log.info("doc index initialised (sqlite-vec available)")
        else:
            log.warning("sqlite-vec not available — multi-doc semantic search disabled")
    finally:
        conn.close()


async def init_db() -> None:
    await asyncio.to_thread(_init_sync)


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def _chunk(text: str) -> list[str]:
    """Paragraph-aware chunker. Targets ~CHUNK_TARGET_CHARS."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 <= CHUNK_TARGET_CHARS:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) > CHUNK_TARGET_CHARS:
                for i in range(0, len(p), CHUNK_TARGET_CHARS):
                    chunks.append(p[i : i + CHUNK_TARGET_CHARS])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


async def indexed_hash(doc_id: str) -> str:
    """Return the content_hash the doc is currently indexed under, or ''."""
    def go():
        conn = _connect()
        try:
            cur = conn.execute(
                "SELECT content_hash FROM doc_chunks WHERE doc_id=? LIMIT 1", (doc_id,)
            )
            row = cur.fetchone()
            return row["content_hash"] if row else ""
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def remove_document(doc_id: str) -> None:
    """Delete all chunks + vectors for a document."""
    def go():
        conn = _connect()
        try:
            cur = conn.execute("SELECT id FROM doc_chunks WHERE doc_id=?", (doc_id,))
            chunk_ids = [r["id"] for r in cur.fetchall()]
            if _HAS_VEC and chunk_ids:
                conn.executemany(
                    "DELETE FROM doc_chunks_vec WHERE chunk_id=?",
                    [(c,) for c in chunk_ids],
                )
            conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
        finally:
            conn.close()
    await asyncio.to_thread(go)


async def index_document(doc_id: str, text: str) -> int:
    """
    Idempotently (re)index a document's text. If the text is unchanged since
    the last index (same content hash) this is a no-op. Returns the number of
    chunks indexed (0 if unchanged or empty).
    """
    text = (text or "").strip()
    if not text:
        return 0
    h = _hash(text)
    existing = await indexed_hash(doc_id)
    if existing == h:
        return 0

    chunks = _chunk(text)
    if not chunks:
        return 0
    vecs = await embed_batch(chunks)

    def go():
        conn = _connect()
        try:
            # drop any stale chunks for this doc first
            cur = conn.execute("SELECT id FROM doc_chunks WHERE doc_id=?", (doc_id,))
            old_ids = [r["id"] for r in cur.fetchall()]
            if _HAS_VEC and old_ids:
                conn.executemany(
                    "DELETE FROM doc_chunks_vec WHERE chunk_id=?",
                    [(c,) for c in old_ids],
                )
            conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))

            inserted = 0
            for i, chunk_text in enumerate(chunks):
                cur = conn.execute(
                    "INSERT INTO doc_chunks (doc_id, position, text, content_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (doc_id, i, chunk_text, h),
                )
                chunk_id = cur.lastrowid
                if _HAS_VEC and i < len(vecs) and vecs[i]:
                    try:
                        conn.execute(
                            "INSERT INTO doc_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                            (chunk_id, to_blob(vecs[i])),
                        )
                    except Exception as exc:
                        log.warning("vec insert failed: %s", exc)
                inserted += 1
            return inserted
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def search(query_embedding: list[float], doc_ids: list[str], k: int = 8) -> list[dict]:
    """
    KNN search over the chunks of the given doc_ids only. sqlite-vec KNN is
    global, so we over-fetch then filter to the selected documents in Python.
    Returns [{doc_id, text, position, distance}] ordered by distance asc.
    """
    if not _HAS_VEC or not query_embedding or not doc_ids:
        return []
    id_set = set(doc_ids)
    overfetch = max(k * 8, 64)

    def go():
        conn = _connect()
        try:
            cur = conn.execute(
                """
                SELECT v.chunk_id, v.distance, c.doc_id, c.text, c.position
                FROM doc_chunks_vec v
                JOIN doc_chunks c ON c.id = v.chunk_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance ASC
                """,
                (to_blob(query_embedding), overfetch),
            )
            out = []
            for r in cur.fetchall():
                if r["doc_id"] in id_set:
                    out.append({
                        "doc_id":   r["doc_id"],
                        "text":     r["text"],
                        "position": r["position"],
                        "distance": float(r["distance"]),
                    })
                if len(out) >= k:
                    break
            return out
        finally:
            conn.close()
    return await asyncio.to_thread(go)
