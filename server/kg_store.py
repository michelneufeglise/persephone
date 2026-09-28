"""
Knowledge graph storage (GraphRAG-style lexical + domain graph).

Persists entities, relations, and mentions across conversations and documents.
Same SQLite file as doc_index.py (paths.db_path()).

Tables:
  kg_entities    — lexical and domain entities (persons, orgs, roles, documents, profiles)
  kg_relations   — semantic relations between entities with confidence scores
  kg_mentions    — document mentions of entities with snippets for grounding
  kg_conversation_docs — documents each conversation used (conversation graph scope)
  kg_meta        — key/value flags for one-off migrations

Entity types: person, organization, role, document, profile, location
Relation types: has_role, works_at, owns, mentioned_in, candidate_profile, likely_profile, located_in
"""

from __future__ import annotations

import asyncio
import json
import re
import logging
import sqlite3
import time
import unicodedata
from typing import Any, Optional

from paths import db_path

log = logging.getLogger("kg_store")

DB_PATH = db_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  norm_name TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(type, norm_name)
);

CREATE TABLE IF NOT EXISTS kg_relations (
  id TEXT PRIMARY KEY,
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  type TEXT NOT NULL,
  confidence REAL,
  source TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  run_id TEXT,
  conversation_id TEXT,
  created_at REAL NOT NULL,
  UNIQUE(src_id, dst_id, type)
);

CREATE TABLE IF NOT EXISTS kg_mentions (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  chunk_id INTEGER,
  snippet TEXT,
  run_id TEXT,
  conversation_id TEXT,
  created_at REAL NOT NULL,
  UNIQUE(entity_id, doc_id)
);

CREATE INDEX IF NOT EXISTS kg_rel_src ON kg_relations(src_id);
CREATE INDEX IF NOT EXISTS kg_rel_dst ON kg_relations(dst_id);
CREATE INDEX IF NOT EXISTS kg_ment_ent ON kg_mentions(entity_id);
CREATE INDEX IF NOT EXISTS kg_ment_conv ON kg_mentions(conversation_id);

-- Which documents a conversation used (every doc-agent run's subject docs),
-- independent of whether the run learned anything about them.
CREATE TABLE IF NOT EXISTS kg_conversation_docs (
  conversation_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  run_id TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY(conversation_id, doc_id)
);

CREATE INDEX IF NOT EXISTS kg_conv_docs_doc ON kg_conversation_docs(doc_id);

-- One-off migration flags
CREATE TABLE IF NOT EXISTS kg_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def _normalize_text(text: str) -> str:
    """Normalize text: lowercase, remove accents, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    # Remove accents: decompose and filter out combining marks
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Collapse whitespace
    text = " ".join(text.split())
    return text


# Entity types whose names often carry stray trailing punctuation ("Acme B.V"
# vs "Acme B.V.", "Solution Architect;") — keyed without it.
_PUNCT_KEYED_TYPES = ("organization", "role")
_TRAILING_PUNCT = ".,;: \t"


def _norm_key(type_: str, name: str) -> str:
    """norm_name for an entity: _normalize_text, plus — for organisations and
    roles — trailing punctuation/whitespace (.,;:) stripped."""
    norm = _normalize_text(name)
    if type_ in _PUNCT_KEYED_TYPES:
        norm = norm.rstrip(_TRAILING_PUNCT) or norm
    return norm


def _connect() -> sqlite3.Connection:
    """Open connection to the shared DB."""
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_sync() -> None:
    """Initialize KG tables (sync) and migrate mentions to one row per (entity, doc)."""
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        # Migration: older DBs keyed mentions per (entity_id, doc_id, run_id), so
        # every run added a row. Keep the newest row per (entity_id, doc_id) and
        # enforce uniqueness from now on.
        try:
            conn.execute(
                "DELETE FROM kg_mentions WHERE rowid NOT IN ("
                "  SELECT MAX(rowid) FROM kg_mentions GROUP BY entity_id, doc_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS kg_ment_ent_doc ON kg_mentions(entity_id, doc_id)"
            )
        except sqlite3.DatabaseError as exc:
            log.warning("kg_mentions dedupe migration failed: %s", exc)
        try:
            _merge_punct_duplicates(conn)
        except sqlite3.DatabaseError as exc:
            log.warning("kg org/role dedupe migration failed: %s", exc)
        try:
            _migrate_profiles(conn)
        except sqlite3.DatabaseError as exc:
            log.warning("kg profile migration failed: %s", exc)
        _backfill_conversation_docs(conn)
        log.info("kg_store initialised at %s", DB_PATH)
    finally:
        conn.close()


async def init_db() -> None:
    """Async wrapper for schema initialization."""
    await asyncio.to_thread(_init_sync)


_BACKFILL_CONV_DOCS_KEY = "conv_docs_backfill_v1"


def _backfill_conversation_docs(conn: sqlite3.Connection) -> int:
    """
    One-off, best-effort: populate kg_conversation_docs from persisted doc-agent
    runs (messages rows whose meta.kind == 'doc_run', using meta.doc_ids and the
    message's conversation_id). Only documents that still have a document entity
    are linked. Guarded by a kg_meta flag so it runs once; on failure the flag is
    not set and the next start retries. Returns the number of rows inserted.
    """
    try:
        if conn.execute(
            "SELECT 1 FROM kg_meta WHERE key=?", (_BACKFILL_CONV_DOCS_KEY,)
        ).fetchone():
            return 0
        inserted = 0
        has_messages = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone() is not None
        if has_messages:
            doc_entities = {
                r["id"] for r in conn.execute("SELECT id FROM kg_entities WHERE type='document'")
            }
            rows = conn.execute(
                "SELECT conversation_id, meta, timestamp FROM messages "
                "WHERE meta LIKE '%doc_run%'"
            ).fetchall()
            for row in rows:
                try:
                    meta = json.loads(row["meta"] or "{}")
                except Exception:
                    continue
                if not isinstance(meta, dict) or meta.get("kind") != "doc_run":
                    continue
                conv_id = row["conversation_id"]
                doc_ids = meta.get("doc_ids") or []
                if not conv_id or not isinstance(doc_ids, list):
                    continue
                for doc_id in doc_ids:
                    if not isinstance(doc_id, str) or f"document:{doc_id}" not in doc_entities:
                        continue
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO kg_conversation_docs "
                        "(conversation_id, doc_id, run_id, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, doc_id, meta.get("run_id"), row["timestamp"] or time.time()),
                    )
                    inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.execute(
            "INSERT OR REPLACE INTO kg_meta (key, value) VALUES (?, ?)",
            (_BACKFILL_CONV_DOCS_KEY, str(time.time())),
        )
        if inserted:
            log.info("kg migration: backfilled %d conversation-document links", inserted)
        return inserted
    except Exception as exc:  # never block startup on a best-effort backfill
        log.warning("kg conversation-docs backfill failed: %s", exc)
        return 0


def _link_conversation_doc_sync(conversation_id: str, doc_id: str, run_id: Optional[str] = None) -> None:
    """Record that a conversation used a document (upsert; keeps first created_at)."""
    if not conversation_id or not doc_id:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO kg_conversation_docs (conversation_id, doc_id, run_id, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id, doc_id) DO UPDATE SET "
            "run_id=COALESCE(excluded.run_id, kg_conversation_docs.run_id)",
            (conversation_id, doc_id, run_id, time.time()),
        )
    finally:
        conn.close()


# ── Sync implementations ──


def _upsert_entity_sync(type_: str, name: str, props: Optional[dict] = None, entity_id: Optional[str] = None) -> str:
    """Upsert an entity. Returns the entity ID (see _upsert_entity_ex_sync)."""
    return _upsert_entity_ex_sync(type_, name, props, entity_id)[0]


def _upsert_entity_ex_sync(
    type_: str, name: str, props: Optional[dict] = None, entity_id: Optional[str] = None
) -> tuple[str, bool]:
    """
    Upsert an entity. Returns (entity_id, created) — created is True only when
    a new row was inserted.

    Upsert an entity. Returns the entity ID.

    Merges props JSON; keeps first name spelling unless new one has accents and old doesn't.
    Entity ID: if entity_id param provided, use it; otherwise f"{type}:{norm_name}".
    For document entities, pass entity_id=f"document:{doc_id}" to fix the ID.
    """
    now = time.time()
    norm_name = _norm_key(type_, name)
    if entity_id is None:
        entity_id = f"{type_}:{norm_name}"
    props = props or {}

    conn = _connect()
    try:
        # Try to find existing entity
        cur = conn.execute(
            "SELECT id, name, props FROM kg_entities WHERE type=? AND norm_name=?",
            (type_, norm_name),
        )
        row = cur.fetchone()

        if row:
            # Merge props, keep max spelling (prefer one with accents if new has them)
            old_props = json.loads(row["props"] or "{}")
            merged_props = {**old_props, **props}
            old_name = row["name"]
            # Prefer new name if it has accents and old doesn't
            final_name = name if name != _normalize_text(name) and old_name == _normalize_text(old_name) else old_name

            conn.execute(
                "UPDATE kg_entities SET name=?, props=?, updated_at=? WHERE id=?",
                (final_name, json.dumps(merged_props), now, row["id"]),
            )
            return row["id"], False
        # Fixed ids (e.g. document:<doc_id>) may already exist under another norm_name
        cur = conn.execute("SELECT id, props FROM kg_entities WHERE id=?", (entity_id,))
        row = cur.fetchone()
        if row:
            merged_props = {**json.loads(row["props"] or "{}"), **props}
            conn.execute(
                "UPDATE kg_entities SET props=?, updated_at=? WHERE id=?",
                (json.dumps(merged_props), now, row["id"]),
            )
            return row["id"], False
        # Create new entity
        conn.execute(
            "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity_id, type_, name, norm_name, json.dumps(props), now, now),
        )
        return entity_id, True
    finally:
        conn.close()


def _add_relation_sync(
    src_id: str,
    dst_id: str,
    type_: str,
    *,
    confidence: Optional[float] = None,
    source: str,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    props: Optional[dict] = None,
) -> str:
    """
    Upsert a relation. Keeps max confidence, updates props.
    Returns the relation ID.
    """
    return _add_relation_ex_sync(
        src_id, dst_id, type_, confidence=confidence, source=source,
        run_id=run_id, conversation_id=conversation_id, props=props,
    )[0]


def _add_relation_ex_sync(
    src_id: str,
    dst_id: str,
    type_: str,
    *,
    confidence: Optional[float] = None,
    source: str,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    props: Optional[dict] = None,
) -> tuple[str, bool]:
    """Upsert a relation; returns (relation_id, created).

    On update the relation is re-attributed to the latest run/conversation that
    produced it, so deleting an older conversation doesn't remove a relation a
    newer conversation still relies on."""
    now = time.time()
    rel_id = f"{src_id}→{dst_id}→{type_}"
    props = props or {}

    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, confidence, props FROM kg_relations WHERE src_id=? AND dst_id=? AND type=?",
            (src_id, dst_id, type_),
        )
        row = cur.fetchone()

        if row:
            # Keep max confidence
            old_conf = row["confidence"]
            final_conf = max(old_conf, confidence) if old_conf is not None and confidence is not None else (confidence or old_conf)
            # Merge props
            old_props = json.loads(row["props"] or "{}")
            merged_props = {**old_props, **props}

            conn.execute(
                "UPDATE kg_relations SET confidence=?, props=?, created_at=?, "
                "run_id=COALESCE(?, run_id), conversation_id=COALESCE(?, conversation_id) WHERE id=?",
                (final_conf, json.dumps(merged_props), now, run_id, conversation_id, row["id"]),
            )
            return row["id"], False
        else:
            # Create new relation
            conn.execute(
                "INSERT INTO kg_relations (id, src_id, dst_id, type, confidence, source, props, run_id, conversation_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rel_id, src_id, dst_id, type_, confidence, source, json.dumps(props), run_id, conversation_id, now),
            )
            return rel_id, True
    finally:
        conn.close()


def _add_mention_sync(
    entity_id: str,
    doc_id: str,
    *,
    chunk_id: Optional[int] = None,
    snippet: Optional[str] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Add/upsert a mention. Returns the mention ID."""
    return _add_mention_ex_sync(
        entity_id, doc_id, chunk_id=chunk_id, snippet=snippet,
        run_id=run_id, conversation_id=conversation_id,
    )[0]


def _add_mention_ex_sync(
    entity_id: str,
    doc_id: str,
    *,
    chunk_id: Optional[int] = None,
    snippet: Optional[str] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> tuple[str, bool]:
    """Upsert the single mention row for (entity_id, doc_id); returns (id, created).

    Repeated runs update run_id / conversation_id / snippet on the existing row
    instead of adding one row per run."""
    now = time.time()
    mention_id = f"{entity_id}→{doc_id}"

    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, chunk_id, snippet FROM kg_mentions WHERE entity_id=? AND doc_id=?",
            (entity_id, doc_id),
        )
        row = cur.fetchone()

        if row:
            # Update existing mention (keep the old grounding if the new run has none)
            conn.execute(
                "UPDATE kg_mentions SET chunk_id=?, snippet=?, "
                "run_id=COALESCE(?, run_id), conversation_id=COALESCE(?, conversation_id) WHERE id=?",
                (
                    chunk_id if chunk_id is not None else row["chunk_id"],
                    snippet if snippet else row["snippet"],
                    run_id, conversation_id, row["id"],
                ),
            )
            return row["id"], False
        else:
            # Create new mention
            conn.execute(
                "INSERT INTO kg_mentions (id, entity_id, doc_id, chunk_id, snippet, run_id, conversation_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mention_id, entity_id, doc_id, chunk_id, snippet, run_id, conversation_id, now),
            )
            return mention_id, True
    finally:
        conn.close()


def _find_chunk_sync(doc_id: str, text: str) -> tuple[int, str] | None:
    """
    Search doc_chunks for the text (accent/case-insensitive).
    Returns (chunk_id, snippet) or None.
    Snippet = ±120 chars around the hit.
    """
    if not text:
        return None

    search_norm = _normalize_text(text)

    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, text FROM doc_chunks WHERE doc_id=? ORDER BY position",
            (doc_id,),
        )
        for row in cur.fetchall():
            chunk_text = row["text"]
            chunk_norm = _normalize_text(chunk_text)

            # Simple substring match (accent/case-insensitive)
            if search_norm in chunk_norm:
                # Find the hit in the original text
                idx = chunk_norm.find(search_norm)
                if idx >= 0:
                    # Get surrounding context
                    start = max(0, idx - 120)
                    end = min(len(chunk_text), idx + len(text) + 120)
                    snippet = chunk_text[start:end].strip()
                    return (row["id"], snippet)

        return None
    except sqlite3.OperationalError:
        # doc_chunks table may not exist in tests
        return None
    finally:
        conn.close()


_CONV_GRAPH_LIMIT = 500


def _conversation_doc_ids(conn: sqlite3.Connection, conversation_id: str) -> list[str]:
    """Documents this conversation used, oldest link first."""
    return [
        r["doc_id"] for r in conn.execute(
            "SELECT doc_id FROM kg_conversation_docs WHERE conversation_id=? ORDER BY created_at, doc_id",
            (conversation_id,),
        ).fetchall()
    ]


def _conversation_entity_ids(
    conn: sqlite3.Connection, conversation_id: str, conv_doc_ids: list[str],
    hops: int = 2, limit: int = _CONV_GRAPH_LIMIT,
) -> set[str]:
    """
    Entity ids in a conversation's graph scope:
      (a) entities with a mention in this conversation, plus the endpoints of
          this conversation's relations touching them (the original rule);
      (b) the document entities of the documents this conversation used;
      (c) everything within `hops` relation hops of those documents, whichever
          conversation created it (person —mentioned_in→ document,
          person —works_at→ org, person —likely_profile→ profile, …).
    Expansion stops once `limit` entities are collected.
    """
    entity_ids: set[str] = set()
    cur = conn.execute(
        "SELECT DISTINCT entity_id FROM kg_mentions WHERE conversation_id=?",
        (conversation_id,),
    )
    entity_ids.update(r["entity_id"] for r in cur.fetchall())

    if entity_ids:
        seed = list(entity_ids)
        placeholders = ",".join("?" * len(seed))
        cur = conn.execute(
            f"SELECT src_id, dst_id FROM kg_relations WHERE conversation_id=? "
            f"AND (src_id IN ({placeholders}) OR dst_id IN ({placeholders}))",
            [conversation_id] + seed + seed,
        )
        for r in cur.fetchall():
            entity_ids.add(r["src_id"])
            entity_ids.add(r["dst_id"])

    # Documents the conversation used (only those that still have an entity)
    doc_entities: list[str] = []
    if conv_doc_ids:
        wanted = [f"document:{d}" for d in conv_doc_ids]
        placeholders = ",".join("?" * len(wanted))
        existing = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM kg_entities WHERE id IN ({placeholders})", wanted
            ).fetchall()
        }
        doc_entities = [e for e in wanted if e in existing]
    entity_ids.update(doc_entities)

    # Breadth-first expansion from the documents over all relations
    frontier = list(doc_entities)
    for _ in range(hops):
        if not frontier or len(entity_ids) >= limit:
            break
        nxt: list[str] = []
        for i in range(0, len(frontier), 400):
            batch = frontier[i:i + 400]
            ph = ",".join("?" * len(batch))
            cur = conn.execute(
                f"SELECT src_id, dst_id FROM kg_relations WHERE src_id IN ({ph}) OR dst_id IN ({ph}) "
                f"ORDER BY confidence DESC",
                batch + batch,
            )
            for r in cur.fetchall():
                for nb in (r["src_id"], r["dst_id"]):
                    if nb not in entity_ids and len(entity_ids) < limit:
                        entity_ids.add(nb)
                        nxt.append(nb)
        frontier = nxt
    return entity_ids


def _get_graph_sync(scope: str = "all", conversation_id: Optional[str] = None) -> dict:
    """
    Get the knowledge graph as JSON.

    scope: "all" (everything, cap 500 entities by mention count) or
    "conversation" — the entities this conversation produced, the documents it
    used (kg_conversation_docs) and everything within two relation hops of
    those documents, regardless of which conversation created it.

    Returns:
    {
      "entities": [{"id": "...", "type": "...", "name": "...", "props": {...}, "mention_count": N}],
      "relations": [{"id": "...", "src": "...", "dst": "...", "type": "...", "confidence": N, "source": "...", "props": {...}}],
      "documents": [{"doc_id": "...", "name": "...", "chunk_count": N, "mention_count": N}],
      "stats": {"entities": N, "relations": N, "mentions": N, "documents": N, "chunks": N}
    }
    """
    conv_scope = scope == "conversation" and bool(conversation_id)
    conn = _connect()
    try:
        conv_doc_ids: list[str] = []
        # Get entities
        if conv_scope:
            conv_doc_ids = _conversation_doc_ids(conn, conversation_id)
            entity_ids = _conversation_entity_ids(conn, conversation_id, conv_doc_ids)

            # Query just these entities
            if entity_ids:
                placeholders = ",".join("?" * len(entity_ids))
                query = f"SELECT id, type, name, props FROM kg_entities WHERE id IN ({placeholders}) ORDER BY id"
                cur = conn.execute(query, list(entity_ids))
            else:
                cur = conn.execute("SELECT id, type, name, props FROM kg_entities WHERE 1=0")
        else:
            # All entities, sorted by mention count (most mentioned first), cap 500
            cur = conn.execute("""
                SELECT e.id, e.type, e.name, e.props, COUNT(m.id) as mention_count
                FROM kg_entities e
                LEFT JOIN kg_mentions m ON e.id = m.entity_id
                GROUP BY e.id
                ORDER BY mention_count DESC
                LIMIT 500
            """)

        entity_rows = cur.fetchall()
        entities = []
        entity_ids_set = set()
        conv_doc_ph = ",".join("?" * len(conv_doc_ids))
        for row in entity_rows:
            entity_id = row["id"]
            entity_ids_set.add(entity_id)
            mention_count = row["mention_count"] if scope == "all" else 0

            if conv_scope:
                # Mentions made in this conversation or found in its documents
                if conv_doc_ids:
                    cur2 = conn.execute(
                        f"SELECT COUNT(*) as cnt FROM kg_mentions WHERE entity_id=? "
                        f"AND (conversation_id=? OR doc_id IN ({conv_doc_ph}))",
                        [entity_id, conversation_id] + conv_doc_ids,
                    )
                else:
                    cur2 = conn.execute(
                        "SELECT COUNT(*) as cnt FROM kg_mentions WHERE entity_id=? AND conversation_id=?",
                        (entity_id, conversation_id),
                    )
                mention_count = cur2.fetchone()["cnt"]

            entities.append({
                "id": entity_id,
                "type": row["type"],
                "name": row["name"],
                "props": json.loads(row["props"] or "{}"),
                "mention_count": mention_count,
            })

        # Get relations (only among the entities we have)
        relations = []
        if entity_ids_set:
            placeholders = ",".join("?" * len(entity_ids_set))
            query = f"""
                SELECT id, src_id, dst_id, type, confidence, source, props
                FROM kg_relations
                WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})
            """
            cur = conn.execute(query, list(entity_ids_set) + list(entity_ids_set))
            for row in cur.fetchall():
                # Both endpoints must be in the returned entity set — the UI
                # can't draw an edge to a node it doesn't have.
                if row["src_id"] not in entity_ids_set or row["dst_id"] not in entity_ids_set:
                    continue
                relations.append({
                    "id": row["id"],
                    "src": row["src_id"],
                    "dst": row["dst_id"],
                    "type": row["type"],
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "props": json.loads(row["props"] or "{}"),
                })

        # Get documents
        documents = []
        for entity in entities:
            if entity["type"] == "document":
                doc_id = entity["props"].get("doc_id") or entity["id"].split(":", 1)[1]
                filename = entity["props"].get("filename") or entity["name"] or doc_id

                # Count chunks and mentions for this document
                chunk_count = 0
                try:
                    cur = conn.execute(
                        "SELECT COUNT(*) as cnt FROM doc_chunks WHERE doc_id=?",
                        (doc_id,),
                    )
                    row = cur.fetchone()
                    chunk_count = row["cnt"] if row else 0
                except sqlite3.OperationalError:
                    # doc_chunks table may not exist in tests
                    chunk_count = 0

                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM kg_mentions WHERE doc_id=?",
                    (doc_id,),
                )
                mention_count = cur.fetchone()["cnt"]

                documents.append({
                    "doc_id": doc_id,
                    "name": filename,
                    "chunk_count": chunk_count,
                    "mention_count": mention_count,
                })

        if conv_scope:
            # The conversation's own documents first (in the order it used them)
            order = {d: i for i, d in enumerate(conv_doc_ids)}
            documents.sort(key=lambda d: (order.get(d["doc_id"], len(order)), d["name"]))

        # Stats
        stats = {
            "entities": len(entities),
            "relations": len(relations),
            "mentions": 0,  # Count below
            "documents": len(documents),
            "chunks": 0,  # Count below
        }

        if conv_scope:
            # Stats of the returned subgraph: mentions of its entities within
            # its documents, chunks of its documents.
            doc_ids_in_graph = [d["doc_id"] for d in documents]
            if entity_ids_set and doc_ids_in_graph:
                eph = ",".join("?" * len(entity_ids_set))
                dph = ",".join("?" * len(doc_ids_in_graph))
                cur = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM kg_mentions WHERE entity_id IN ({eph}) AND doc_id IN ({dph})",
                    list(entity_ids_set) + doc_ids_in_graph,
                )
                stats["mentions"] = cur.fetchone()["cnt"]
            stats["chunks"] = sum(d["chunk_count"] for d in documents)
        elif entity_ids_set:
            # Count total mentions and chunks
            placeholders = ",".join("?" * len(entity_ids_set))
            cur = conn.execute(
                f"SELECT COUNT(*) as cnt FROM kg_mentions WHERE entity_id IN ({placeholders})",
                list(entity_ids_set),
            )
            stats["mentions"] = cur.fetchone()["cnt"]

            # Count total chunks across all documents in the graph
            try:
                cur = conn.execute("""
                    SELECT COUNT(DISTINCT c.id) as cnt FROM doc_chunks c
                    WHERE c.doc_id IN (SELECT DISTINCT doc_id FROM kg_mentions)
                """)
                stats["chunks"] = cur.fetchone()["cnt"]
            except sqlite3.OperationalError:
                # doc_chunks table may not exist
                stats["chunks"] = 0

        return {
            "entities": entities,
            "relations": relations,
            "documents": documents,
            "stats": stats,
        }
    finally:
        conn.close()


def _get_entity_sync(entity_id: str) -> dict | None:
    """Get a single entity with its relations (both directions) and mentions."""
    conn = _connect()
    try:
        # Get entity
        cur = conn.execute(
            "SELECT id, type, name, props FROM kg_entities WHERE id=?",
            (entity_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        entity = {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "props": json.loads(row["props"] or "{}"),
            "relations_out": [],
            "relations_in": [],
            "mentions": [],
        }

        # Get outgoing relations
        cur = conn.execute(
            "SELECT id, dst_id, type, confidence, source, props FROM kg_relations WHERE src_id=?",
            (entity_id,),
        )
        for row in cur.fetchall():
            entity["relations_out"].append({
                "id": row["id"],
                "dst": row["dst_id"],
                "type": row["type"],
                "confidence": row["confidence"],
                "source": row["source"],
                "props": json.loads(row["props"] or "{}"),
            })

        # Get incoming relations
        cur = conn.execute(
            "SELECT id, src_id, type, confidence, source, props FROM kg_relations WHERE dst_id=?",
            (entity_id,),
        )
        for row in cur.fetchall():
            entity["relations_in"].append({
                "id": row["id"],
                "src": row["src_id"],
                "type": row["type"],
                "confidence": row["confidence"],
                "source": row["source"],
                "props": json.loads(row["props"] or "{}"),
            })

        # Get mentions
        cur = conn.execute(
            "SELECT id, doc_id, chunk_id, snippet FROM kg_mentions WHERE entity_id=?",
            (entity_id,),
        )
        for row in cur.fetchall():
            # Get document name
            doc_cur = conn.execute(
                "SELECT name FROM kg_entities WHERE id=?",
                (f"document:{row['doc_id']}",),
            )
            doc_row = doc_cur.fetchone()
            doc_name = doc_row["name"] if doc_row else row["doc_id"]

            entity["mentions"].append({
                "id": row["id"],
                "doc_id": row["doc_id"],
                "doc_name": doc_name,
                "chunk_id": row["chunk_id"],
                "snippet": row["snippet"],
            })

        return entity
    finally:
        conn.close()


def _search_entities_sync(text: str, limit: int = 5) -> list[dict]:
    """
    Search entities by token matching (accent/case-insensitive).

    Scoring logic:
    - Match if ALL tokens of entity's norm_name appear in query tokens
      OR all query tokens (len ≥ 2 chars, excluding stopwords) appear in entity's norm_name
    - Stopwords to exclude: the, a, of, about, what, do, we, know, my, documents, across, in, all
    - Persons first, then sorted by mention_count DESC
    """
    norm_text = _normalize_text(text)
    if not norm_text:
        return []

    stopwords = {"the", "a", "of", "about", "what", "do", "we", "know", "my", "documents", "across", "in", "all"}

    # Split query into tokens, filter short ones and stopwords
    query_tokens = set(
        token for token in norm_text.split()
        if len(token) >= 2 and token not in stopwords
    )

    conn = _connect()
    try:
        # Get all entities, order by type then mention count
        cur = conn.execute("""
            SELECT e.id, e.type, e.name, e.norm_name, e.props, COUNT(m.id) as mention_count
            FROM kg_entities e
            LEFT JOIN kg_mentions m ON e.id = m.entity_id
            GROUP BY e.id
            ORDER BY (CASE WHEN e.type='person' THEN 0 ELSE 1 END), mention_count DESC
        """)

        results = []
        seen_ids = set()

        for row in cur.fetchall():
            entity_id = row["id"]
            if entity_id in seen_ids:
                continue

            norm_name = row["norm_name"]
            entity_tokens = set(norm_name.split())

            # Score logic: match if:
            # 1. ALL tokens of entity_name are in query tokens, OR
            # 2. ALL non-stopword query tokens (len >= 2) are in entity_tokens
            matches_by_entity_tokens = entity_tokens.issubset(query_tokens)
            matches_by_query_tokens = query_tokens.issubset(entity_tokens) if query_tokens else False

            if not (matches_by_entity_tokens or matches_by_query_tokens):
                continue

            seen_ids.add(entity_id)
            results.append({
                "id": entity_id,
                "type": row["type"],
                "name": row["name"],
                "props": json.loads(row["props"] or "{}"),
                "mention_count": row["mention_count"] or 0,
            })

            if len(results) >= limit:
                break

        return results
    finally:
        conn.close()


def _neighborhood_sync(entity_id: str, hops: int = 2, limit: int = 60) -> dict:
    """Get neighbors at N hops distance, up to limit entities. Returns subgraph dict."""
    # Breadth-first traversal
    visited = {entity_id}
    to_visit = [entity_id]
    current_hop = [entity_id]

    conn = _connect()
    try:
        for hop_idx in range(hops):
            next_hop = []
            for ent_id in current_hop:
                if len(visited) >= limit:
                    break

                # Get neighbors (both directions)
                cur = conn.execute(
                    "SELECT DISTINCT dst_id FROM kg_relations WHERE src_id=? UNION SELECT DISTINCT src_id FROM kg_relations WHERE dst_id=?",
                    (ent_id, ent_id),
                )
                for row in cur.fetchall():
                    neighbor = row[0]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_hop.append(neighbor)
                        if len(visited) >= limit:
                            break

            current_hop = next_hop
            if not current_hop:
                break

        # Get full subgraph
        if not visited:
            visited = {entity_id}

        placeholders = ",".join("?" * len(visited))

        # Get entities
        entities = []
        cur = conn.execute(
            f"SELECT id, type, name, props FROM kg_entities WHERE id IN ({placeholders})",
            list(visited),
        )
        for row in cur.fetchall():
            entities.append({
                "id": row["id"],
                "type": row["type"],
                "name": row["name"],
                "props": json.loads(row["props"] or "{}"),
            })

        # Get relations between visited entities
        relations = []
        cur = conn.execute(
            f"SELECT id, src_id, dst_id, type, confidence, source, props FROM kg_relations WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})",
            list(visited) + list(visited),
        )
        for row in cur.fetchall():
            relations.append({
                "id": row["id"],
                "src": row["src_id"],
                "dst": row["dst_id"],
                "type": row["type"],
                "confidence": row["confidence"],
                "source": row["source"],
                "props": json.loads(row["props"] or "{}"),
            })

        return {"entities": entities, "relations": relations}
    finally:
        conn.close()


def _gc_orphans(conn: sqlite3.Connection) -> int:
    """
    Garbage-collect orphaned knowledge.

    An entity is *anchored* when it is a document, has at least one mention, or
    is connected (through relations, any number of hops) to an anchored entity.
    Every non-anchored entity is deleted, as is every relation whose src or dst
    no longer exists. Returns the number of entities deleted.
    """
    # Relations with a missing endpoint first
    conn.execute(
        "DELETE FROM kg_relations WHERE src_id NOT IN (SELECT id FROM kg_entities) "
        "OR dst_id NOT IN (SELECT id FROM kg_entities)"
    )
    all_ids = {r["id"] for r in conn.execute("SELECT id FROM kg_entities").fetchall()}
    anchored = {
        r["id"] for r in conn.execute("SELECT id FROM kg_entities WHERE type='document'").fetchall()
    }
    anchored |= {
        r["entity_id"] for r in conn.execute("SELECT DISTINCT entity_id FROM kg_mentions").fetchall()
    } & all_ids
    adjacency: dict[str, set[str]] = {}
    for r in conn.execute("SELECT src_id, dst_id FROM kg_relations").fetchall():
        adjacency.setdefault(r["src_id"], set()).add(r["dst_id"])
        adjacency.setdefault(r["dst_id"], set()).add(r["src_id"])
    frontier = list(anchored)
    while frontier:
        nxt = []
        for eid in frontier:
            for nb in adjacency.get(eid, ()):
                if nb not in anchored:
                    anchored.add(nb)
                    nxt.append(nb)
        frontier = nxt
    orphans = sorted(all_ids - anchored)
    for i in range(0, len(orphans), 500):
        batch = orphans[i:i + 500]
        ph = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM kg_relations WHERE src_id IN ({ph}) OR dst_id IN ({ph})", batch + batch)
        conn.execute(f"DELETE FROM kg_mentions WHERE entity_id IN ({ph})", batch)
        conn.execute(f"DELETE FROM kg_entities WHERE id IN ({ph})", batch)
    return len(orphans)


def _delete_document_sync(doc_id: str) -> int:
    """Delete document entity, its mentions and relations, then GC orphans.
    Returns the number of deleted entities (document + orphans)."""
    conn = _connect()
    try:
        doc_entity_id = f"document:{doc_id}"
        existed = conn.execute(
            "SELECT 1 FROM kg_entities WHERE id=?", (doc_entity_id,)
        ).fetchone() is not None

        # Delete mentions in this document and its conversation links
        conn.execute("DELETE FROM kg_mentions WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM kg_conversation_docs WHERE doc_id=?", (doc_id,))

        # Delete relations touching the document entity
        conn.execute("DELETE FROM kg_relations WHERE src_id=? OR dst_id=?", (doc_entity_id, doc_entity_id))

        # Delete the document entity
        conn.execute("DELETE FROM kg_entities WHERE id=?", (doc_entity_id,))

        return (1 if existed else 0) + _gc_orphans(conn)
    finally:
        conn.close()


def _delete_conversation_sync(conversation_id: str) -> int:
    """Remove the mentions and relations a conversation produced and its
    document links, then GC orphans. Document entities are kept (the documents
    still exist).
    Returns the number of entities garbage-collected."""
    if not conversation_id:
        return 0
    conn = _connect()
    try:
        conn.execute("DELETE FROM kg_mentions WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM kg_relations WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM kg_conversation_docs WHERE conversation_id=?", (conversation_id,))
        return _gc_orphans(conn)
    finally:
        conn.close()


def _reset_sync() -> None:
    """Delete all KG data."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM kg_mentions")
        conn.execute("DELETE FROM kg_relations")
        conn.execute("DELETE FROM kg_entities")
        conn.execute("DELETE FROM kg_conversation_docs")
    finally:
        conn.close()


def _stats_sync() -> dict:
    """Get stats on the knowledge graph."""
    conn = _connect()
    try:
        cur = conn.execute("SELECT COUNT(*) as cnt FROM kg_entities")
        entity_count = cur.fetchone()["cnt"]

        cur = conn.execute("SELECT COUNT(*) as cnt FROM kg_relations")
        relation_count = cur.fetchone()["cnt"]

        cur = conn.execute("SELECT COUNT(*) as cnt FROM kg_mentions")
        mention_count = cur.fetchone()["cnt"]

        # Count by type
        cur = conn.execute("SELECT type, COUNT(*) as cnt FROM kg_entities GROUP BY type")
        by_type = {row["type"]: row["cnt"] for row in cur.fetchall()}

        return {
            "entities": entity_count,
            "relations": relation_count,
            "mentions": mention_count,
            "entities_by_type": by_type,
        }
    finally:
        conn.close()


# ── Async wrappers ──


async def upsert_entity(type_: str, name: str, props: Optional[dict] = None, entity_id: Optional[str] = None) -> str:
    """Async wrapper."""
    return await asyncio.to_thread(_upsert_entity_sync, type_, name, props, entity_id)


async def add_relation(
    src_id: str,
    dst_id: str,
    type_: str,
    *,
    confidence: Optional[float] = None,
    source: str,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    props: Optional[dict] = None,
) -> str:
    """Async wrapper."""
    return await asyncio.to_thread(
        _add_relation_sync,
        src_id,
        dst_id,
        type_,
        confidence=confidence,
        source=source,
        run_id=run_id,
        conversation_id=conversation_id,
        props=props,
    )


async def add_mention(
    entity_id: str,
    doc_id: str,
    *,
    chunk_id: Optional[int] = None,
    snippet: Optional[str] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Async wrapper."""
    return await asyncio.to_thread(
        _add_mention_sync,
        entity_id,
        doc_id,
        chunk_id=chunk_id,
        snippet=snippet,
        run_id=run_id,
        conversation_id=conversation_id,
    )


async def find_chunk(doc_id: str, text: str) -> tuple[int, str] | None:
    """Async wrapper."""
    return await asyncio.to_thread(_find_chunk_sync, doc_id, text)


async def get_graph(scope: str = "all", conversation_id: Optional[str] = None) -> dict:
    """Async wrapper."""
    return await asyncio.to_thread(_get_graph_sync, scope, conversation_id)


async def get_entity(entity_id: str) -> dict | None:
    """Async wrapper."""
    return await asyncio.to_thread(_get_entity_sync, entity_id)


async def search_entities(text: str, limit: int = 5) -> list[dict]:
    """Async wrapper."""
    return await asyncio.to_thread(_search_entities_sync, text, limit)


async def neighborhood(entity_id: str, hops: int = 2, limit: int = 60) -> dict:
    """Async wrapper."""
    return await asyncio.to_thread(_neighborhood_sync, entity_id, hops, limit)


async def delete_document(doc_id: str) -> int:
    """Async wrapper."""
    return await asyncio.to_thread(_delete_document_sync, doc_id)


async def delete_conversation(conversation_id: str) -> int:
    """Async wrapper: forget what a (document) conversation taught the store."""
    return await asyncio.to_thread(_delete_conversation_sync, conversation_id)


async def reset() -> None:
    """Async wrapper."""
    return await asyncio.to_thread(_reset_sync)


async def stats() -> dict:
    """Async wrapper."""
    return await asyncio.to_thread(_stats_sync)


# ── Ingest handler: populate graph after doc agent runs ──


_EXT_KIND = {
    "pdf": "pdf",
    "doc": "docx", "docx": "docx", "odt": "docx", "rtf": "docx",
    "xls": "xlsx", "xlsx": "xlsx", "csv": "xlsx", "ods": "xlsx", "tsv": "xlsx",
    "eml": "eml", "msg": "eml",
    "txt": "txt", "md": "txt", "html": "txt", "htm": "txt", "json": "txt", "xml": "txt",
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image", "gif": "image",
    "bmp": "image", "tif": "image", "tiff": "image", "heic": "image",
}


def _doc_kind(filename: str, mime: str) -> str:
    """Document kind from the filename extension (pdf/docx/xlsx/eml/txt/image),
    falling back to the MIME type."""
    fname = (filename or "").lower()
    if "." in fname:
        kind = _EXT_KIND.get(fname.rsplit(".", 1)[1])
        if kind:
            return kind
    m = (mime or "").lower()
    if "pdf" in m:
        return "pdf"
    if m.startswith("image/"):
        return "image"
    if "sheet" in m or "csv" in m:
        return "xlsx"
    if "word" in m:
        return "docx"
    if m == "message/rfc822":
        return "eml"
    if m.startswith("text/"):
        return "txt"
    return "document"


def _canonical_url(url: str) -> str:
    """
    Canonical key for a profile URL: lower-case host without "www."/"m."/country
    subdomains for known social platforms (nl.linkedin.com → linkedin.com),
    no scheme, fragment, trailing slash or query (except Facebook's
    profile.php?id=…, where the query IS the identity).
    """
    from urllib.parse import urlparse
    u = (url or "").strip()
    try:
        parsed = urlparse(u)
    except Exception:
        return u.lower().rstrip("/")
    host = (parsed.hostname or "").lower()
    try:
        from doc_web import PLATFORMS as _PLATFORMS
        for p in _PLATFORMS.values():
            for domain in p["domains"]:
                if host == domain or host.endswith("." + domain):
                    host = domain
                    break
    except Exception:
        pass
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    key = f"{host}{path}".lower()
    if path.lower().endswith("/profile.php") and parsed.query:
        m = re.search(r"(?:^|&)id=(\d+)", parsed.query)
        if m:
            key += f"?id={m.group(1)}"
    return key


def _upsert_profile_sync(url: str, title: str, props: dict) -> tuple[str, bool]:
    """Upsert a profile entity keyed on its canonical URL. Returns (id, created)."""
    now = time.time()
    canon = _canonical_url(url)
    entity_id = f"profile:{canon}"
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, props FROM kg_entities WHERE id=? OR (type='profile' AND norm_name=?)",
            (entity_id, canon),
        ).fetchone()
        if row:
            merged = {**json.loads(row["props"] or "{}"), **props}
            conn.execute(
                "UPDATE kg_entities SET props=?, updated_at=? WHERE id=?",
                (json.dumps(merged), now, row["id"]),
            )
            return row["id"], False
        conn.execute(
            "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) "
            "VALUES (?, 'profile', ?, ?, ?, ?, ?)",
            (entity_id, title or url, canon, json.dumps(props), now, now),
        )
        return entity_id, True
    finally:
        conn.close()


def _drop_candidate_if_likely_sync(src_id: str, dst_id: str) -> bool:
    """Keep ONE relation per (person, profile): when likely_profile exists the
    candidate_profile for the same pair is removed. Returns True if likely exists."""
    conn = _connect()
    try:
        likely = conn.execute(
            "SELECT 1 FROM kg_relations WHERE src_id=? AND dst_id=? AND type='likely_profile'",
            (src_id, dst_id),
        ).fetchone() is not None
        if likely:
            conn.execute(
                "DELETE FROM kg_relations WHERE src_id=? AND dst_id=? AND type='candidate_profile'",
                (src_id, dst_id),
            )
        return likely
    finally:
        conn.close()


def _delete_entities(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Delete entities together with their relations and mentions."""
    for i in range(0, len(ids), 500):
        batch = ids[i:i + 500]
        ph = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM kg_relations WHERE src_id IN ({ph}) OR dst_id IN ({ph})", batch + batch)
        conn.execute(f"DELETE FROM kg_mentions WHERE entity_id IN ({ph})", batch)
        conn.execute(f"DELETE FROM kg_entities WHERE id IN ({ph})", batch)


def _purge_junk_entities(conn: sqlite3.Connection) -> None:
    """
    Remove knowledge that earlier versions stored by mistake:
    - answer-derived generic roles ("the individual whose CV is presented",
      "CV holder") and roles that are sentence fragments ("Temp Person works");
    - organisations that are sentence fragments ("Temp Corp as Temp Engineer.");
    - organisations stored as persons ("Acme B.V"): re-created as an
      organisation mentioned in the same documents, the person row removed.
    """
    try:
        import doc_web as _dw
    except Exception:  # pragma: no cover - doc_web always importable in-tree
        return
    junk: list[str] = []
    for row in conn.execute("SELECT id, name FROM kg_entities WHERE type='role'").fetchall():
        name = row["name"] or ""
        if _dw._is_generic_role(name) or re.search(r"\bworks?\b$", name.strip(), re.IGNORECASE):
            junk.append(row["id"])
    for row in conn.execute("SELECT id, name FROM kg_entities WHERE type='organization'").fetchall():
        if _dw._is_bad_org(row["name"] or ""):
            junk.append(row["id"])
    now = time.time()
    for row in conn.execute("SELECT id, name FROM kg_entities WHERE type='person'").fetchall():
        name = row["name"] or ""
        if not _dw.looks_like_organization(name):
            continue
        norm = _norm_key("organization", name)
        org = conn.execute(
            "SELECT id FROM kg_entities WHERE type='organization' AND norm_name IN (?, ?)",
            (norm, _normalize_text(name)),
        ).fetchone()
        org_id = org["id"] if org else f"organization:{norm}"
        if not org:
            conn.execute(
                "INSERT OR IGNORE INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) "
                "VALUES (?, 'organization', ?, ?, '{}', ?, ?)",
                (org_id, name, norm, now, now),
            )
        docs = conn.execute(
            "SELECT dst_id, run_id, conversation_id FROM kg_relations "
            "WHERE src_id=? AND type='mentioned_in'", (row["id"],),
        ).fetchall()
        for d in docs:
            conn.execute(
                "INSERT OR IGNORE INTO kg_relations (id, src_id, dst_id, type, confidence, source, props, "
                "run_id, conversation_id, created_at) VALUES (?, ?, ?, 'mentioned_in', 0.8, 'migration', '{}', ?, ?, ?)",
                (f"{org_id}→{d['dst_id']}→mentioned_in", org_id, d["dst_id"], d["run_id"], d["conversation_id"], now),
            )
        junk.append(row["id"])
    if junk:
        log.info("kg migration: removing %d junk entities", len(junk))
        _delete_entities(conn, junk)


def _merge_punct_duplicates(conn: sqlite3.Connection) -> int:
    """Re-key organisation/role entities on _norm_key and merge rows that differ
    only by trailing punctuation ("acme b.v" / "acme b.v."): the oldest row
    survives, relations and mentions of the others are re-pointed to it
    (skipping edges it already has), and the duplicates are deleted.
    Returns the number of duplicates removed."""
    removed = 0
    for type_ in _PUNCT_KEYED_TYPES:
        rows = conn.execute(
            "SELECT id, name, norm_name FROM kg_entities WHERE type=? ORDER BY created_at, rowid", (type_,)
        ).fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(_norm_key(type_, r["name"] or r["norm_name"]), []).append(r)
        for key, members in groups.items():
            survivor = members[0]["id"]
            for dup in members[1:]:
                old_id = dup["id"]
                conn.execute("UPDATE OR IGNORE kg_relations SET src_id=? WHERE src_id=?", (survivor, old_id))
                conn.execute("UPDATE OR IGNORE kg_relations SET dst_id=? WHERE dst_id=?", (survivor, old_id))
                conn.execute("DELETE FROM kg_relations WHERE src_id=? OR dst_id=?", (old_id, old_id))
                conn.execute("UPDATE OR IGNORE kg_mentions SET entity_id=? WHERE entity_id=?", (survivor, old_id))
                conn.execute("DELETE FROM kg_mentions WHERE entity_id=?", (old_id,))
                conn.execute("DELETE FROM kg_entities WHERE id=?", (old_id,))
                removed += 1
            if members[0]["norm_name"] != key:
                conn.execute("UPDATE OR IGNORE kg_entities SET norm_name=? WHERE id=?", (key, survivor))
    if removed:
        log.info("kg migration: merged %d punctuation-duplicate organisations/roles", removed)
    return removed


def _migrate_profiles(conn: sqlite3.Connection) -> None:
    """Re-key legacy title-keyed profile entities on their canonical URL (merging
    duplicates), delete profiles without an http(s) URL (legacy DuckDuckGo
    "ref://…" links) with their relations, drop candidate_profile relations
    shadowed by likely_profile, and purge junk roles/organisations."""
    rows = conn.execute("SELECT id, props FROM kg_entities WHERE type='profile'").fetchall()
    non_http: list[str] = []
    for row in rows:
        try:
            props = json.loads(row["props"] or "{}")
        except Exception:
            props = {}
        url = props.get("url") or ""
        if not url.lower().startswith(("http://", "https://")):
            non_http.append(row["id"])
            continue
        canon = _canonical_url(url)
        new_id = f"profile:{canon}"
        old_id = row["id"]
        if old_id == new_id:
            continue
        target = conn.execute("SELECT id FROM kg_entities WHERE id=?", (new_id,)).fetchone()
        if not target:
            conn.execute(
                "UPDATE kg_entities SET id=?, norm_name=? WHERE id=?", (new_id, canon, old_id)
            )
        # Re-point relations (skip ones that would duplicate an existing edge)
        conn.execute("UPDATE OR IGNORE kg_relations SET dst_id=? WHERE dst_id=?", (new_id, old_id))
        conn.execute("UPDATE OR IGNORE kg_relations SET src_id=? WHERE src_id=?", (new_id, old_id))
        conn.execute("DELETE FROM kg_relations WHERE src_id=? OR dst_id=?", (old_id, old_id))
        if target:
            conn.execute("DELETE FROM kg_entities WHERE id=?", (old_id,))
    if non_http:
        _delete_entities(conn, non_http)
    conn.execute(
        "DELETE FROM kg_relations WHERE type='candidate_profile' AND EXISTS ("
        "  SELECT 1 FROM kg_relations r2 WHERE r2.type='likely_profile' "
        "  AND r2.src_id=kg_relations.src_id AND r2.dst_id=kg_relations.dst_id)"
    )
    try:
        _purge_junk_entities(conn)
    except Exception as exc:  # never block startup on hygiene
        log.warning("kg junk purge failed: %s", exc)


async def ingest_run(
    *,
    conversation_id: str,
    run_id: str,
    intent: str,
    subject_docs: list,  # List of Document objects
    answer_text: str,
    person: Optional[dict] = None,  # {name, role, org}
    web_candidates: Optional[list] = None,  # [{url, title, snippet, host}]
    verdict: Optional[str] = None,  # Web lookup verdict text
    persons: Optional[list] = None,  # [{name, role, org, doc_id?}] — several people (identify_person)
    organizations: Optional[list] = None,  # [{name, doc_id, owner?, legal_form?, registered_since?, demo?, …}] — registry extracts
) -> dict:
    """
    Populate the knowledge graph after a document agent run.

    `persons` (optional) lists several people; an entry with a `doc_id` is tied
    to that document only, otherwise to every subject document. When omitted,
    `person` is used for all subject documents.

    `organizations` (optional) are companies parsed from company-registry
    extracts: organization —mentioned_in→ its document, and (with an owner)
    person —owns→ organization (props legal_form / registered_since / demo).
    A demo/fictitious source marks the organization with props {"demo": true}.

    Returns {"entities": N, "relations": N, "mentions": N, "entity_list": [...]}
    where the counts are NEW rows only (re-running the same question adds 0).
    """
    from doc_web import name_matches as _name_matches
    from doc_web import looks_like_organization as _looks_like_org
    from doc_web import _is_generic_role as _generic_role, _is_bad_org as _bad_org

    counts = {"entities": 0, "relations": 0, "mentions": 0, "entity_list": []}
    if not subject_docs:
        return counts

    listed: set = set()

    async def _entity(type_: str, name: str, props: Optional[dict] = None, entity_id: Optional[str] = None) -> str:
        eid, created = await asyncio.to_thread(_upsert_entity_ex_sync, type_, name, props, entity_id)
        if created:
            counts["entities"] += 1
        if (type_, eid) not in listed:
            listed.add((type_, eid))
            counts["entity_list"].append({"name": name, "type": type_})
        return eid

    async def _relation(
        src: str, dst: str, type_: str, confidence: float, source: str, props: Optional[dict] = None,
    ) -> None:
        _, created = await asyncio.to_thread(
            _add_relation_ex_sync, src, dst, type_,
            confidence=confidence, source=source, run_id=run_id, conversation_id=conversation_id,
            props=props,
        )
        if created:
            counts["relations"] += 1

    async def _mention(entity_id: str, doc_id: str, chunk_id, snippet) -> None:
        _, created = await asyncio.to_thread(
            _add_mention_ex_sync, entity_id, doc_id,
            chunk_id=chunk_id, snippet=snippet, run_id=run_id, conversation_id=conversation_id,
        )
        if created:
            counts["mentions"] += 1

    people: list[dict] = []
    for p in (persons or ([person] if person else [])):
        if isinstance(p, dict) and (p.get("name") or "").strip():
            people.append(p)

    try:
        for doc in subject_docs:
            doc_id = getattr(doc, "id", "unknown")
            filename = getattr(doc, "filename", doc_id)
            kind = _doc_kind(filename, getattr(doc, "mime", "") or "")

            # Use filename as the entity name, with explicit entity_id = f"document:{doc_id}"
            doc_entity_id = await _entity(
                "document",
                filename,
                {"doc_id": doc_id, "filename": filename, "kind": kind},
                entity_id=f"document:{doc_id}",
            )
            # Link the document to the conversation even when the run learns
            # nothing else about it (e.g. general_question) — drives the
            # conversation graph scope.
            await asyncio.to_thread(_link_conversation_doc_sync, conversation_id, doc_id, run_id)

            for pers in people:
                if pers.get("doc_id") and pers["doc_id"] != doc_id:
                    continue
                pname = pers["name"].strip()
                if _looks_like_org(pname):
                    # "Acme B.V." is an organisation named in the document, not a person
                    org_entity_id = await _entity("organization", pname.strip(" .*"))
                    await _relation(org_entity_id, doc_entity_id, "mentioned_in", 0.8, "doc_agent")
                    continue
                person_entity_id = await _entity("person", pname)

                # Mention in document
                doc_text = getattr(doc, "text", "") or ""
                chunk_info = await find_chunk(doc_id, pname)
                chunk_id, snippet = chunk_info if chunk_info else (None, None)

                # Fallback snippet if not found in chunks
                if not snippet and pname in doc_text:
                    idx = doc_text.find(pname)
                    start = max(0, idx - 120)
                    end = min(len(doc_text), idx + len(pname) + 120)
                    snippet = doc_text[start:end].strip()

                await _mention(person_entity_id, doc_id, chunk_id, snippet)
                await _relation(person_entity_id, doc_entity_id, "mentioned_in", 0.9, "doc_agent")

                if pers.get("role") and not _generic_role(pers["role"]):
                    role_entity_id = await _entity("role", pers["role"])
                    await _relation(person_entity_id, role_entity_id, "has_role", 0.7, "doc_agent")

                if pers.get("org") and not _bad_org(pers["org"]):
                    org_entity_id = await _entity("organization", pers["org"])
                    await _relation(person_entity_id, org_entity_id, "works_at", 0.7, "doc_agent")
                    await _relation(org_entity_id, doc_entity_id, "mentioned_in", 0.8, "doc_agent")

            # Company-registry extracts: organisation + ownership
            for org in organizations or []:
                if not isinstance(org, dict) or (org.get("doc_id") and org["doc_id"] != doc_id):
                    continue
                oname = (org.get("name") or "").strip().strip("*").strip()
                if not oname or _bad_org(oname):
                    continue
                demo = bool(org.get("demo"))
                org_props = {
                    k: org[k] for k in (
                        "legal_form", "registered_since", "sbi", "activities", "status",
                        "address", "registry_number",
                    ) if org.get(k)
                }
                if demo:
                    org_props["demo"] = True
                org_entity_id = await _entity("organization", oname, org_props)
                await _relation(org_entity_id, doc_entity_id, "mentioned_in", 0.8, "doc_agent")
                oname_doc_text = getattr(doc, "text", "") or ""
                if oname in oname_doc_text:
                    idx = oname_doc_text.find(oname)
                    await _mention(
                        org_entity_id, doc_id, None,
                        oname_doc_text[max(0, idx - 120): idx + len(oname) + 120].strip(),
                    )
                owner = (org.get("owner") or "").strip()
                if not owner or _looks_like_org(owner):
                    continue
                owner_id = await _entity("person", owner)
                doc_text = getattr(doc, "text", "") or ""
                chunk_info = await find_chunk(doc_id, owner)
                chunk_id, snippet = chunk_info if chunk_info else (None, None)
                if not snippet and owner in doc_text:
                    idx = doc_text.find(owner)
                    snippet = doc_text[max(0, idx - 120): idx + len(owner) + 120].strip()
                await _mention(owner_id, doc_id, chunk_id, snippet)
                await _relation(owner_id, doc_entity_id, "mentioned_in", 0.9, "doc_agent")
                rel_props = {k: org[k] for k in ("legal_form", "registered_since") if org.get(k)}
                if demo:
                    rel_props["demo"] = True
                await _relation(owner_id, org_entity_id, "owns", 0.7, "doc_agent", rel_props)

            # Web lookup: profile entities and relations (for the looked-up person)
            if web_candidates and person and person.get("name"):
                person_entity_id = f"person:{_normalize_text(person['name'])}"
                likely_urls = _likely_profile_urls(verdict or "")
                likely_canon = {_canonical_url(u) for u in likely_urls}
                seen_keys: set = set()

                for candidate in web_candidates:
                    url = (candidate.get("url") or "").strip()
                    # Only real web links (skip e.g. DuckDuckGo "ref://…" short links)
                    if not url.lower().startswith(("http://", "https://")):
                        continue

                    # Skip directory pages and search pages
                    if "/pub/dir/" in url or "/search/" in url:
                        continue

                    # Skip non-profile pages on social networks (posts, directories…)
                    platform = candidate.get("platform") or _platform_of(url)
                    if _platform_of(url) and not _is_profile_url(url):
                        continue

                    title = candidate.get("title") or url
                    # The candidate must actually name the person
                    if not _name_matches(person["name"], title):
                        continue

                    canon = _canonical_url(url)
                    if canon in seen_keys:
                        continue
                    seen_keys.add(canon)

                    host = candidate.get("host") or _hostname(url)
                    profile_entity_id, created = await asyncio.to_thread(
                        _upsert_profile_sync, url, title, {
                            "url": url,
                            "snippet": candidate.get("snippet", ""),
                            "host": host,
                            "platform": platform or "web",
                        },
                    )
                    if created:
                        counts["entities"] += 1

                    # Likely match: first URL cited on its platform's "Likely match found"
                    # verdict line (or, for the legacy single verdict, the first cited URL).
                    # ONE relation per (person, profile): likely replaces candidate.
                    if _url_key(url) in likely_urls or canon in likely_canon:
                        await _relation(person_entity_id, profile_entity_id, "likely_profile", 0.8, "web_lookup")
                        await asyncio.to_thread(_drop_candidate_if_likely_sync, person_entity_id, profile_entity_id)
                    else:
                        has_likely = await asyncio.to_thread(
                            _drop_candidate_if_likely_sync, person_entity_id, profile_entity_id
                        )
                        if not has_likely:
                            await _relation(person_entity_id, profile_entity_id, "candidate_profile", 0.5, "web_lookup")

        return counts
    except Exception as e:
        log.warning("kg ingest failed: %s", e)
        return counts


def _hostname(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


# ── Web verdict parsing (per-platform verdicts) ─────────────────────────────

_VERDICT_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?\*\*\s*(LinkedIn|Facebook|Instagram|X|Twitter|X/Twitter|Web|Overall)\s*:?\s*\*\*\s*:?\s*(.*)$",
    re.IGNORECASE,
)
_CITED_URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^\)\s]+)\)")
_PLATFORM_BY_LABEL = {
    "linkedin": "linkedin", "facebook": "facebook", "instagram": "instagram",
    "x": "x", "twitter": "x", "x/twitter": "x", "web": "web", "overall": "overall",
}


def _platform_of(url: str):
    from doc_web import platform_of
    return platform_of(url)


def _is_profile_url(url: str) -> bool:
    from doc_web import is_profile_url
    return is_profile_url(url)


def _url_key(url: str) -> str:
    """Comparable form of a URL (no trailing slash, lower-case)."""
    return (url or "").strip().rstrip("/").lower()


def _likely_profile_urls(verdict: str) -> set:
    """
    URLs the verdict marks as likely matches.

    Per-platform format — lines like
        **Facebook:** Likely match found — [Jane](https://www.facebook.com/jane.example)
    A platform whose line starts with "Likely match found" contributes the first
    URL of that platform cited in its section (the line up to the next verdict
    line); if the section cites none, the first URL of that platform anywhere in
    the verdict.

    Legacy single-verdict format ("**Likely match found** …"): the first cited URL.
    """
    if not verdict:
        return set()
    lines = verdict.splitlines()
    sections = []  # (platform, status_text, section_text)
    current = None
    for line in lines:
        m = _VERDICT_LINE_RE.match(line)
        if m:
            if current:
                sections.append(current)
            label = m.group(1).lower()
            current = [_PLATFORM_BY_LABEL.get(label, label), m.group(2).strip(), line]
        elif current:
            current[2] += "\n" + line
    if current:
        sections.append(current)

    platform_sections = [s for s in sections if s[0] != "overall"]
    if platform_sections:
        all_urls = _CITED_URL_RE.findall(verdict)
        likely = set()
        for platform, status, text in platform_sections:
            status_clean = status.lstrip("*_ ").lower()
            if not status_clean.startswith("likely match found"):
                continue

            def _belongs(u: str) -> bool:
                pid = _platform_of(u)
                return (pid is None) if platform == "web" else (pid == platform)

            section_urls = [u for u in _CITED_URL_RE.findall(text) if _belongs(u)]
            if not section_urls:
                section_urls = [u for u in all_urls if _belongs(u)]
            if section_urls:
                likely.add(_url_key(section_urls[0]))
        return likely

    # Legacy single verdict
    if "**Likely match found**" in verdict:
        urls = _CITED_URL_RE.findall(verdict)
        if urls:
            return {_url_key(urls[0])}
    return set()
