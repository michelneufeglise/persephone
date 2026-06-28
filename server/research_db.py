"""
Research knowledge-base storage.

Uses synchronous sqlite3 (not aiosqlite) so we can cleanly load the sqlite-vec
extension for semantic search. All public functions are async — they marshal
work to a thread pool via asyncio.to_thread so they don't block the event loop.

Tables
──────
  research_runs       — one row per /research/start call
  research_sources    — one row per fetched URL inside a run
  research_chunks     — text chunks (≤~500 tokens) with provenance back to source
  research_chunks_vec — sqlite-vec virtual table, one embedding per chunk
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    import sqlite_vec  # type: ignore
    _HAS_VEC = True
except Exception:
    _HAS_VEC = False

from embeddings import to_blob, EMBED_DIM

log = logging.getLogger("research_db")

DB_PATH = Path(__file__).parent / "persephone.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
    id          TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    report_md   TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON research_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS research_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    fetched_at  REAL NOT NULL,
    ok          INTEGER NOT NULL DEFAULT 1,
    chars       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sources_run ON research_sources(run_id);

CREATE TABLE IF NOT EXISTS research_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
    run_id      TEXT    NOT NULL REFERENCES research_runs(id)    ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    text        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_run    ON research_chunks(run_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON research_chunks(source_id);
"""

_VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS research_chunks_vec USING vec0(
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
            log.info("research KB initialised (sqlite-vec %s)", _vec_version(conn))
        else:
            log.warning("sqlite-vec not available — semantic search disabled")
    finally:
        conn.close()


def _vec_version(conn: sqlite3.Connection) -> str:
    try:
        cur = conn.execute("SELECT vec_version()")
        row = cur.fetchone()
        return row[0] if row else "?"
    except Exception:
        return "?"


async def init_db() -> None:
    await asyncio.to_thread(_init_sync)


# ─── runs ────────────────────────────────────────────────────────────────
async def create_run(run_id: str, query: str) -> None:
    def go():
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO research_runs (id, query, status, created_at) VALUES (?, ?, 'running', ?)",
                (run_id, query, time.time()),
            )
        finally:
            conn.close()
    await asyncio.to_thread(go)


async def finalise_run(
    run_id: str, status: str, report_md: str = "",
    report_json: dict | None = None, error: str = "",
) -> None:
    def go():
        conn = _connect()
        try:
            conn.execute(
                """UPDATE research_runs
                   SET status=?, report_md=?, report_json=?, error=?, finished_at=?
                   WHERE id=?""",
                (status, report_md, json.dumps(report_json or {}), error, time.time(), run_id),
            )
        finally:
            conn.close()
    await asyncio.to_thread(go)


async def list_runs(limit: int = 100) -> list[dict]:
    def go():
        conn = _connect()
        try:
            cur = conn.execute(
                """SELECT r.id, r.query, r.status, r.created_at, r.finished_at,
                          (SELECT COUNT(*) FROM research_sources s WHERE s.run_id = r.id) AS sources,
                          (SELECT COUNT(*) FROM research_chunks  c WHERE c.run_id = r.id) AS chunks
                   FROM research_runs r
                   ORDER BY r.created_at DESC LIMIT ?""",
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id":         r["id"],
                    "query":      r["query"],
                    "status":     r["status"],
                    "createdAt":  int(r["created_at"] * 1000),
                    "finishedAt": int(r["finished_at"] * 1000) if r["finished_at"] else None,
                    "sources":    r["sources"],
                    "chunks":     r["chunks"],
                }
                for r in rows
            ]
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def get_run(run_id: str) -> dict | None:
    def go():
        conn = _connect()
        try:
            cur = conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur = conn.execute(
                """SELECT id, url, title, fetched_at, ok, chars
                   FROM research_sources WHERE run_id=? ORDER BY id ASC""",
                (run_id,),
            )
            sources = [dict(r) for r in cur.fetchall()]
            return {
                "id":         row["id"],
                "query":      row["query"],
                "status":     row["status"],
                "reportMd":   row["report_md"],
                "reportJson": json.loads(row["report_json"] or "{}"),
                "error":      row["error"],
                "createdAt":  int(row["created_at"] * 1000),
                "finishedAt": int(row["finished_at"] * 1000) if row["finished_at"] else None,
                "sources":    sources,
            }
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def delete_run(run_id: str) -> bool:
    def go():
        conn = _connect()
        try:
            # Collect chunk IDs first so we can clean up the vec0 table by hand
            # (foreign-key cascade doesn't trigger across virtual tables).
            cur = conn.execute("SELECT id FROM research_chunks WHERE run_id=?", (run_id,))
            chunk_ids = [r["id"] for r in cur.fetchall()]
            if _HAS_VEC and chunk_ids:
                conn.executemany(
                    "DELETE FROM research_chunks_vec WHERE chunk_id=?",
                    [(c,) for c in chunk_ids],
                )
            cur = conn.execute("DELETE FROM research_runs WHERE id=?", (run_id,))
            return cur.rowcount > 0
        finally:
            conn.close()
    return await asyncio.to_thread(go)


# ─── sources + chunks ─────────────────────────────────────────────────────
async def add_source(run_id: str, url: str, title: str, ok: bool, chars: int) -> int:
    def go():
        conn = _connect()
        try:
            cur = conn.execute(
                """INSERT INTO research_sources (run_id, url, title, fetched_at, ok, chars)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, url, title, time.time(), 1 if ok else 0, chars),
            )
            return cur.lastrowid
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def add_chunks(
    source_id: int, run_id: str, chunks: list[str], embeddings: list[list[float]],
) -> int:
    """Insert chunks + their embeddings. Returns count actually inserted."""
    def go():
        if not chunks:
            return 0
        if embeddings and len(embeddings) != len(chunks):
            log.warning("chunk/embedding count mismatch: %d vs %d", len(chunks), len(embeddings))
        conn = _connect()
        try:
            inserted = 0
            for i, text in enumerate(chunks):
                cur = conn.execute(
                    """INSERT INTO research_chunks (source_id, run_id, position, text)
                       VALUES (?, ?, ?, ?)""",
                    (source_id, run_id, i, text),
                )
                chunk_id = cur.lastrowid
                if _HAS_VEC and i < len(embeddings) and embeddings[i]:
                    try:
                        conn.execute(
                            "INSERT INTO research_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                            (chunk_id, to_blob(embeddings[i])),
                        )
                    except Exception as exc:
                        log.warning("vec insert failed: %s", exc)
                inserted += 1
            return inserted
        finally:
            conn.close()
    return await asyncio.to_thread(go)


# ─── semantic search ─────────────────────────────────────────────────────
async def search_chunks(query_embedding: list[float], k: int = 12) -> list[dict]:
    """KNN lookup across every chunk in the KB. Returns chunk text + source info."""
    if not _HAS_VEC or not query_embedding:
        return []

    def go():
        conn = _connect()
        try:
            cur = conn.execute(
                f"""
                SELECT v.chunk_id, v.distance,
                       c.text, c.position, c.run_id,
                       s.url, s.title,
                       r.query AS run_query
                FROM research_chunks_vec v
                JOIN research_chunks  c ON c.id = v.chunk_id
                JOIN research_sources s ON s.id = c.source_id
                JOIN research_runs    r ON r.id = c.run_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance ASC
                """,
                (to_blob(query_embedding), k),
            )
            return [
                {
                    "chunkId":  r["chunk_id"],
                    "distance": float(r["distance"]),
                    "text":     r["text"],
                    "url":      r["url"],
                    "title":    r["title"],
                    "runId":    r["run_id"],
                    "runQuery": r["run_query"],
                    "position": r["position"],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()
    return await asyncio.to_thread(go)


async def kb_stats() -> dict:
    def go():
        conn = _connect()
        try:
            cur = conn.execute("SELECT COUNT(*) AS n FROM research_runs")
            runs = cur.fetchone()["n"]
            cur = conn.execute("SELECT COUNT(*) AS n FROM research_sources")
            sources = cur.fetchone()["n"]
            cur = conn.execute("SELECT COUNT(*) AS n FROM research_chunks")
            chunks = cur.fetchone()["n"]
            return {
                "runs": runs, "sources": sources, "chunks": chunks,
                "has_vec": _HAS_VEC,
            }
        finally:
            conn.close()
    return await asyncio.to_thread(go)
