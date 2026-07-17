"""
SQLite-backed persistent memory for Persephone.
Conversations + messages stored in server/persephone.db
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from paths import db_path

log = logging.getLogger("db")

# Resolves to PERSEPHONE_DATA_DIR/persephone.db in packaged builds (writable),
# otherwise next to this script for dev convenience.
DB_PATH = db_path()

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    pinned     INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    thinking        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    timestamp       REAL NOT NULL,
    meta            TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_updated  ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS user_facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fact         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    confidence   REAL NOT NULL DEFAULT 0.8,
    source_conv  TEXT,
    source_msg   TEXT,
    created_at   REAL NOT NULL,
    UNIQUE(fact COLLATE NOCASE)
);
CREATE INDEX IF NOT EXISTS idx_facts_created ON user_facts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_facts_cat     ON user_facts(category);

-- Delegated tasks: async subtasks the main chat model spawned via the
-- `delegate_task` tool. Result flows back into the conversation as a new
-- assistant message once the delegate finishes.
CREATE TABLE IF NOT EXISTS delegated_tasks (
    id                TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL,
    source_msg_id     TEXT NOT NULL DEFAULT '',
    prompt            TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT 'general',
    delegate_model    TEXT NOT NULL DEFAULT '',
    main_model        TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed|cancelled
    result            TEXT NOT NULL DEFAULT '',
    comment           TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    started_at        REAL,
    completed_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_deltasks_conv    ON delegated_tasks(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deltasks_status  ON delegated_tasks(status);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()


async def list_conversations() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id,title,model,created_at,updated_at,pinned FROM conversations ORDER BY pinned DESC, updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (r["id"],)
            ) as cur:
                cnt = (await cur.fetchone())[0]
        result.append({
            "id":         r["id"],
            "title":      r["title"],
            "model":      r["model"],
            "createdAt":  int(r["created_at"] * 1000),
            "updatedAt":  int(r["updated_at"] * 1000),
            "pinned":     bool(r["pinned"]),
            "messageCount": cnt,
        })
    return result


async def get_conversation(conv_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM conversations WHERE id=?", (conv_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        async with db.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY timestamp ASC",
            (conv_id,),
        ) as cur:
            msgs = await cur.fetchall()

    return {
        "id":        row["id"],
        "title":     row["title"],
        "model":     row["model"],
        "createdAt": int(row["created_at"] * 1000),
        "updatedAt": int(row["updated_at"] * 1000),
        "pinned":    bool(row["pinned"]),
        "messages": [
            {
                "id":             m["id"],
                "role":           m["role"],
                "content":        m["content"],
                "thinkingContent": m["thinking"],
                "model":          m["model"],
                "timestamp":      int(m["timestamp"] * 1000),
                "meta":           _parse_meta(m["meta"] if "meta" in m.keys() else "{}"),
            }
            for m in msgs
        ],
    }


def _parse_meta(raw: Any) -> dict:
    """Best-effort JSON parse for the meta column; missing/corrupt → {}."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


async def upsert_conversation(data: dict) -> None:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO conversations (id,title,model,created_at,updated_at,pinned)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, model=excluded.model,
                 updated_at=excluded.updated_at, pinned=excluded.pinned""",
            (
                data["id"],
                data.get("title", "New conversation"),
                data.get("model", ""),
                data.get("createdAt", now * 1000) / 1000,
                data.get("updatedAt", now * 1000) / 1000,
                1 if data.get("pinned") else 0,
            ),
        )
        await db.commit()


async def upsert_message(conv_id: str, msg: dict) -> None:
    meta = msg.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO messages (id,conversation_id,role,content,thinking,model,timestamp,meta)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 content=excluded.content, thinking=excluded.thinking, meta=excluded.meta""",
            (
                msg["id"],
                conv_id,
                msg["role"],
                msg.get("content", ""),
                msg.get("thinkingContent", ""),
                msg.get("model", ""),
                msg.get("timestamp", time.time() * 1000) / 1000,
                json.dumps(meta),
            ),
        )
        # Update conversation updated_at
        await db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (time.time(), conv_id),
        )
        await db.commit()


async def delete_conversation(conv_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        await db.commit()


async def delete_message(msg_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
        await db.commit()


# ── App config (key-value) ────────────────────────────────────────────────────
async def get_config(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM app_config WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_config(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO app_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_all_config() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM app_config") as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


# ── User long-term memory (facts) ────────────────────────────────────────────
async def add_user_fact(
    fact: str,
    category: str = "general",
    confidence: float = 0.8,
    source_conv: str | None = None,
    source_msg: str | None = None,
) -> int | None:
    """Insert a fact; returns the new rowid or None if it was a duplicate."""
    fact = fact.strip()
    if not fact:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                """INSERT INTO user_facts (fact, category, confidence, source_conv, source_msg, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (fact, category, confidence, source_conv, source_msg, time.time()),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None  # duplicate (UNIQUE collation NOCASE)


async def list_user_facts(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, fact, category, confidence, source_conv, source_msg, created_at
               FROM user_facts ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id":         r["id"],
            "fact":       r["fact"],
            "category":   r["category"],
            "confidence": r["confidence"],
            "sourceConv": r["source_conv"],
            "sourceMsg":  r["source_msg"],
            "createdAt":  int(r["created_at"] * 1000),
        }
        for r in rows
    ]


async def delete_user_fact(fact_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM user_facts WHERE id=?", (fact_id,))
        await db.commit()
        return cur.rowcount > 0


async def clear_user_facts() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM user_facts")
        await db.commit()
        return cur.rowcount
