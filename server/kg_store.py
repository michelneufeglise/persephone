"""
Knowledge graph storage (GraphRAG-style lexical + domain graph).

Persists entities, relations, and mentions across conversations and documents.
Same SQLite file as doc_index.py (paths.db_path()).

Tables:
  kg_entities    — lexical and domain entities (persons, orgs, roles, documents, profiles)
  kg_relations   — semantic relations between entities with confidence scores
  kg_mentions    — document mentions of entities with snippets for grounding

Entity types: person, organization, role, document, profile, location
Relation types: has_role, works_at, mentioned_in, candidate_profile, likely_profile, located_in
"""

from __future__ import annotations

import asyncio
import json
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
  UNIQUE(entity_id, doc_id, run_id)
);

CREATE INDEX IF NOT EXISTS kg_rel_src ON kg_relations(src_id);
CREATE INDEX IF NOT EXISTS kg_rel_dst ON kg_relations(dst_id);
CREATE INDEX IF NOT EXISTS kg_ment_ent ON kg_mentions(entity_id);
CREATE INDEX IF NOT EXISTS kg_ment_conv ON kg_mentions(conversation_id);
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


def _connect() -> sqlite3.Connection:
    """Open connection to the shared DB."""
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_sync() -> None:
    """Initialize KG tables (sync)."""
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        log.info("kg_store initialised at %s", DB_PATH)
    finally:
        conn.close()


async def init_db() -> None:
    """Async wrapper for schema initialization."""
    await asyncio.to_thread(_init_sync)


# ── Sync implementations ──


def _upsert_entity_sync(type_: str, name: str, props: Optional[dict] = None, entity_id: Optional[str] = None) -> str:
    """
    Upsert an entity. Returns the entity ID.

    Merges props JSON; keeps first name spelling unless new one has accents and old doesn't.
    Entity ID: if entity_id param provided, use it; otherwise f"{type}:{norm_name}".
    For document entities, pass entity_id=f"document:{doc_id}" to fix the ID.
    """
    now = time.time()
    norm_name = _normalize_text(name)
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
            return row["id"]
        else:
            # Create new entity
            conn.execute(
                "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entity_id, type_, name, norm_name, json.dumps(props), now, now),
            )
            return entity_id
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
                "UPDATE kg_relations SET confidence=?, props=?, created_at=? WHERE id=?",
                (final_conf, json.dumps(merged_props), now, row["id"]),
            )
            return row["id"]
        else:
            # Create new relation
            conn.execute(
                "INSERT INTO kg_relations (id, src_id, dst_id, type, confidence, source, props, run_id, conversation_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rel_id, src_id, dst_id, type_, confidence, source, json.dumps(props), run_id, conversation_id, now),
            )
            return rel_id
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
    now = time.time()
    mention_id = f"{entity_id}→{doc_id}→{run_id or 'none'}"

    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id FROM kg_mentions WHERE entity_id=? AND doc_id=? AND run_id=?",
            (entity_id, doc_id, run_id),
        )
        row = cur.fetchone()

        if row:
            # Update existing mention
            conn.execute(
                "UPDATE kg_mentions SET chunk_id=?, snippet=? WHERE id=?",
                (chunk_id, snippet, row["id"]),
            )
            return row["id"]
        else:
            # Create new mention
            conn.execute(
                "INSERT INTO kg_mentions (id, entity_id, doc_id, chunk_id, snippet, run_id, conversation_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mention_id, entity_id, doc_id, chunk_id, snippet, run_id, conversation_id, now),
            )
            return mention_id
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


def _get_graph_sync(scope: str = "all", conversation_id: Optional[str] = None) -> dict:
    """
    Get the knowledge graph as JSON.

    scope: "all" (everything, cap 500 entities by mention count) or "conversation" (only for this conv)

    Returns:
    {
      "entities": [{"id": "...", "type": "...", "name": "...", "props": {...}, "mention_count": N}],
      "relations": [{"id": "...", "src": "...", "dst": "...", "type": "...", "confidence": N, "source": "...", "props": {...}}],
      "documents": [{"doc_id": "...", "name": "...", "chunk_count": N, "mention_count": N}],
      "stats": {"entities": N, "relations": N, "mentions": N, "documents": N, "chunks": N}
    }
    """
    conn = _connect()
    try:
        # Get entities
        if scope == "conversation" and conversation_id:
            # Only entities mentioned in this conversation
            entity_ids = set()
            cur = conn.execute(
                "SELECT DISTINCT entity_id FROM kg_mentions WHERE conversation_id=?",
                (conversation_id,),
            )
            entity_ids.update(r["entity_id"] for r in cur.fetchall())

            # Also include entities with relations to those entities
            if entity_ids:
                placeholders = ",".join("?" * len(entity_ids))
                cur = conn.execute(
                    f"SELECT DISTINCT src_id FROM kg_relations WHERE conversation_id=? AND (src_id IN ({placeholders}) OR dst_id IN ({placeholders}))",
                    [conversation_id] + list(entity_ids) + list(entity_ids),
                )
                entity_ids.update(r["src_id"] for r in cur.fetchall())

                cur = conn.execute(
                    f"SELECT DISTINCT dst_id FROM kg_relations WHERE conversation_id=? AND (src_id IN ({placeholders}) OR dst_id IN ({placeholders}))",
                    [conversation_id] + list(entity_ids) + list(entity_ids),
                )
                entity_ids.update(r["dst_id"] for r in cur.fetchall())

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

        entities = []
        entity_ids_set = set()
        for row in cur.fetchall():
            entity_id = row["id"]
            entity_ids_set.add(entity_id)
            mention_count = row["mention_count"] if scope == "all" else 0

            if scope == "conversation" and conversation_id:
                # Count mentions in this conversation
                cur2 = conn.execute(
                    "SELECT COUNT(*) as cnt FROM kg_mentions WHERE entity_id=? AND conversation_id=?",
                    (entity_id, conversation_id),
                )
                mention_count = cur2.fetchone()["cnt"]
            elif scope == "all":
                # Already have mention_count from above
                pass

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
                filename = entity["props"].get("filename", doc_id)

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

        # Stats
        stats = {
            "entities": len(entities),
            "relations": len(relations),
            "mentions": 0,  # Count below
            "documents": len(documents),
            "chunks": 0,  # Count below
        }

        # Count total mentions and chunks
        if entity_ids_set:
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


def _delete_document_sync(doc_id: str) -> int:
    """Delete document entity, its mentions, and related relations. Returns count of deleted entities."""
    conn = _connect()
    try:
        # Get the document entity
        doc_entity_id = f"document:{doc_id}"

        # Get all entities mentioned in this document
        cur = conn.execute(
            "SELECT DISTINCT entity_id FROM kg_mentions WHERE doc_id=?",
            (doc_id,),
        )
        entity_ids = [row["entity_id"] for row in cur.fetchall()]

        # Delete mentions in this document
        conn.execute("DELETE FROM kg_mentions WHERE doc_id=?", (doc_id,))

        # Delete relations touching the document entity
        conn.execute("DELETE FROM kg_relations WHERE src_id=? OR dst_id=?", (doc_entity_id, doc_entity_id))

        # Delete the document entity
        conn.execute("DELETE FROM kg_entities WHERE id=?", (doc_entity_id,))

        return len(entity_ids) + 1
    finally:
        conn.close()


def _reset_sync() -> None:
    """Delete all KG data."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM kg_mentions")
        conn.execute("DELETE FROM kg_relations")
        conn.execute("DELETE FROM kg_entities")
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


async def reset() -> None:
    """Async wrapper."""
    return await asyncio.to_thread(_reset_sync)


async def stats() -> dict:
    """Async wrapper."""
    return await asyncio.to_thread(_stats_sync)


# ── Ingest handler: populate graph after doc agent runs ──


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
) -> dict:
    """
    Populate the knowledge graph after a document agent run.

    Returns {"entities": N, "relations": N, "mentions": N, "entity_list": [...]}.
    """
    from doc_web import extract_person as _extract_person
    from doc_web import extract_headline as _extract_headline

    counts = {"entities": 0, "relations": 0, "mentions": 0, "entity_list": []}
    if not subject_docs:
        return counts

    try:
        # For each subject document: create document entity
        for doc in subject_docs:
            doc_id = getattr(doc, "id", "unknown")
            filename = getattr(doc, "filename", doc_id)
            kind = getattr(doc, "mime", "").split("/")[0] or "document"

            # Use filename as the entity name, with explicit entity_id = f"document:{doc_id}"
            doc_entity_id = await upsert_entity(
                "document",
                filename,
                {"doc_id": doc_id, "filename": filename, "kind": kind},
                entity_id=f"document:{doc_id}"
            )
            counts["entities"] += 1
            counts["entity_list"].append({"name": filename, "type": "document"})

            # If person dict provided: create person entity and relations
            if person and person.get("name"):
                person_entity_id = await upsert_entity("person", person["name"])
                counts["entities"] += 1
                counts["entity_list"].append({"name": person["name"], "type": "person"})

                # Mention in document
                doc_text = getattr(doc, "text", "")
                chunk_info = await find_chunk(doc_id, person["name"])
                chunk_id, snippet = chunk_info if chunk_info else (None, None)

                # Fallback snippet if not found in chunks
                if not snippet and person["name"] in doc_text:
                    idx = doc_text.find(person["name"])
                    start = max(0, idx - 120)
                    end = min(len(doc_text), idx + len(person["name"]) + 120)
                    snippet = doc_text[start:end].strip()

                await add_mention(
                    person_entity_id,
                    doc_id,
                    chunk_id=chunk_id,
                    snippet=snippet,
                    run_id=run_id,
                    conversation_id=conversation_id,
                )
                counts["mentions"] += 1

                # Mention person in document
                await add_relation(
                    person_entity_id,
                    doc_entity_id,
                    "mentioned_in",
                    confidence=0.9,
                    source="doc_agent",
                    run_id=run_id,
                    conversation_id=conversation_id,
                )
                counts["relations"] += 1

                # Role entity + has_role relation
                if person.get("role"):
                    role_entity_id = await upsert_entity("role", person["role"])
                    counts["entities"] += 1
                    counts["entity_list"].append({"name": person["role"], "type": "role"})

                    await add_relation(
                        person_entity_id,
                        role_entity_id,
                        "has_role",
                        confidence=0.7,
                        source="doc_agent",
                        run_id=run_id,
                        conversation_id=conversation_id,
                    )
                    counts["relations"] += 1

                # Organization entity + works_at relation
                if person.get("org"):
                    org_entity_id = await upsert_entity("organization", person["org"])
                    counts["entities"] += 1
                    counts["entity_list"].append({"name": person["org"], "type": "organization"})

                    await add_relation(
                        person_entity_id,
                        org_entity_id,
                        "works_at",
                        confidence=0.7,
                        source="doc_agent",
                        run_id=run_id,
                        conversation_id=conversation_id,
                    )
                    counts["relations"] += 1

                    # Org mentioned in document
                    await add_relation(
                        org_entity_id,
                        doc_entity_id,
                        "mentioned_in",
                        confidence=0.8,
                        source="doc_agent",
                        run_id=run_id,
                        conversation_id=conversation_id,
                    )
                    counts["relations"] += 1

            # Web lookup: profile entities and relations
            if web_candidates and person and person.get("name"):
                person_entity_id = f"person:{_normalize_text(person['name'])}"
                seen_candidate_titles = set()  # Track candidate titles to dedupe

                for candidate in web_candidates:
                    url = candidate.get("url", "")
                    if not url:
                        continue

                    # Skip LinkedIn directory pages and search pages
                    if "/pub/dir/" in url or "/search/" in url:
                        continue

                    title = candidate.get("title", url)
                    host = candidate.get("host") or url.split("/")[2] if "/" in url else ""

                    # Skip if we've already added a candidate with this title (for this person in this run)
                    title_normalized = _normalize_text(title)
                    if title_normalized in seen_candidate_titles:
                        continue
                    seen_candidate_titles.add(title_normalized)

                    profile_entity_id = await upsert_entity("profile", title, {
                        "url": url,
                        "snippet": candidate.get("snippet", ""),
                        "host": host,
                    })
                    counts["entities"] += 1

                    # candidate_profile relation
                    await add_relation(
                        person_entity_id,
                        profile_entity_id,
                        "candidate_profile",
                        confidence=0.5,
                        source="web_lookup",
                        run_id=run_id,
                        conversation_id=conversation_id,
                    )
                    counts["relations"] += 1

                    # Check if this is the likely match (first URL cited in verdict)
                    if verdict and "**Likely match found**" in verdict:
                        # Extract first URL from verdict
                        import re
                        url_matches = re.findall(r"\[.*?\]\((https?://[^\)]+)\)", verdict)
                        if url_matches and url_matches[0] == url:
                            await add_relation(
                                person_entity_id,
                                profile_entity_id,
                                "likely_profile",
                                confidence=0.8,
                                source="web_lookup",
                                run_id=run_id,
                                conversation_id=conversation_id,
                            )
                            counts["relations"] += 1

        return counts
    except Exception as e:
        log.warning("kg ingest failed: %s", e)
        return counts
