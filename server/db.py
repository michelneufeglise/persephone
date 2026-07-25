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

-- Planned tasks: user-scheduled prompts executed by the planner loop.
-- Each successful run posts its output into a fresh conversation.
CREATE TABLE IF NOT EXISTS planned_tasks (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    model          TEXT NOT NULL,
    schedule_kind  TEXT NOT NULL,                   -- once|daily|weekly|every_n_min|cron
    schedule_value TEXT NOT NULL,                   -- ISO datetime | 'HH:MM' | 'MON 09:30' | '30' | cron expr
    tool_ids       TEXT NOT NULL DEFAULT '[]',      -- JSON list of MCP tool names
    skill_names    TEXT NOT NULL DEFAULT '[]',      -- JSON list of skill names
    enabled        INTEGER NOT NULL DEFAULT 1,
    next_run_ts    REAL,
    last_run_ts    REAL,
    last_status    TEXT NOT NULL DEFAULT '',        -- ''|running|succeeded|failed
    last_conv_id   TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ptasks_due ON planned_tasks(enabled, next_run_ts);

CREATE TABLE IF NOT EXISTS planned_task_runs (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    started_at     REAL NOT NULL,
    finished_at    REAL,
    status         TEXT NOT NULL,                   -- running|succeeded|failed
    conv_id        TEXT NOT NULL DEFAULT '',
    output_preview TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ptruns_task ON planned_task_runs(task_id, started_at DESC);
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


# ── Planned tasks ──────────────────────────────────────────────────────────
def _row_to_task(r: aiosqlite.Row) -> dict:
    def _load_json(raw: Any, fallback: list) -> list:
        if not raw:
            return fallback
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else fallback
        except (ValueError, TypeError):
            return fallback

    return {
        "id":            r["id"],
        "name":          r["name"],
        "prompt":        r["prompt"],
        "model":         r["model"],
        "scheduleKind":  r["schedule_kind"],
        "scheduleValue": r["schedule_value"],
        "toolIds":       _load_json(r["tool_ids"], []),
        "skillNames":    _load_json(r["skill_names"], []),
        "enabled":       bool(r["enabled"]),
        "nextRunTs":     r["next_run_ts"],
        "lastRunTs":     r["last_run_ts"],
        "lastStatus":    r["last_status"] or "",
        "lastConvId":    r["last_conv_id"] or "",
        "createdAt":     int(r["created_at"] * 1000),
        "updatedAt":     int(r["updated_at"] * 1000),
    }


async def list_planned_tasks() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM planned_tasks
               ORDER BY enabled DESC,
                        CASE WHEN next_run_ts IS NULL THEN 1 ELSE 0 END,
                        next_run_ts ASC,
                        updated_at DESC"""
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_task(r) for r in rows]


async def get_planned_task(task_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM planned_tasks WHERE id=?", (task_id,),
        ) as cur:
            row = await cur.fetchone()
    return _row_to_task(row) if row else None


async def list_due_planned_tasks(now_ts: float) -> list[dict]:
    """Enabled tasks whose next_run_ts is set and <= now."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM planned_tasks
               WHERE enabled=1 AND next_run_ts IS NOT NULL AND next_run_ts <= ?
               ORDER BY next_run_ts ASC""",
            (now_ts,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_task(r) for r in rows]


async def upsert_planned_task(t: dict) -> None:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO planned_tasks
                 (id, name, prompt, model, schedule_kind, schedule_value,
                  tool_ids, skill_names, enabled,
                  next_run_ts, last_run_ts, last_status, last_conv_id,
                  created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 prompt=excluded.prompt,
                 model=excluded.model,
                 schedule_kind=excluded.schedule_kind,
                 schedule_value=excluded.schedule_value,
                 tool_ids=excluded.tool_ids,
                 skill_names=excluded.skill_names,
                 enabled=excluded.enabled,
                 next_run_ts=excluded.next_run_ts,
                 last_run_ts=excluded.last_run_ts,
                 last_status=excluded.last_status,
                 last_conv_id=excluded.last_conv_id,
                 updated_at=excluded.updated_at""",
            (
                t["id"],
                t.get("name", "Untitled task"),
                t.get("prompt", ""),
                t.get("model", ""),
                t.get("scheduleKind", "once"),
                t.get("scheduleValue", ""),
                json.dumps(t.get("toolIds") or []),
                json.dumps(t.get("skillNames") or []),
                1 if t.get("enabled", True) else 0,
                t.get("nextRunTs"),
                t.get("lastRunTs"),
                t.get("lastStatus", ""),
                t.get("lastConvId", ""),
                t.get("createdAt", now * 1000) / 1000,
                now,
            ),
        )
        await db.commit()


async def update_planned_task_run_bookkeeping(
    task_id: str,
    *,
    next_run_ts: float | None,
    last_run_ts: float,
    last_status: str,
    last_conv_id: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE planned_tasks SET
                 next_run_ts=?,
                 last_run_ts=?,
                 last_status=?,
                 last_conv_id=?,
                 updated_at=?
               WHERE id=?""",
            (next_run_ts, last_run_ts, last_status, last_conv_id, time.time(), task_id),
        )
        await db.commit()


async def delete_planned_task(task_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM planned_tasks WHERE id=?", (task_id,))
        await db.execute("DELETE FROM planned_task_runs WHERE task_id=?", (task_id,))
        await db.commit()
        return cur.rowcount > 0


async def record_task_run_start(run: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO planned_task_runs
                 (id, task_id, started_at, finished_at, status, conv_id, output_preview, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run["id"],
                run["task_id"],
                run["started_at"],
                None,
                run.get("status", "running"),
                run.get("conv_id", ""),
                run.get("output_preview", ""),
                run.get("error", ""),
            ),
        )
        await db.commit()


async def record_task_run_finish(
    run_id: str,
    *,
    status: str,
    conv_id: str,
    output_preview: str,
    error: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE planned_task_runs SET
                 finished_at=?, status=?, conv_id=?, output_preview=?, error=?
               WHERE id=?""",
            (time.time(), status, conv_id, output_preview, error, run_id),
        )
        await db.commit()


async def list_task_runs(task_id: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM planned_task_runs
               WHERE task_id=? ORDER BY started_at DESC LIMIT ?""",
            (task_id, max(1, min(200, limit))),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id":             r["id"],
            "taskId":         r["task_id"],
            "startedAt":      int(r["started_at"] * 1000),
            "finishedAt":     int(r["finished_at"] * 1000) if r["finished_at"] else None,
            "status":         r["status"],
            "convId":         r["conv_id"] or "",
            "outputPreview":  r["output_preview"] or "",
            "error":          r["error"] or "",
        }
        for r in rows
    ]
