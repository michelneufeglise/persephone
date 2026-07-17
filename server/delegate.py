"""
Delegate infrastructure — the main chat model can hand off subtasks to a
different Ollama model and keep serving the user in the meantime.

Flow:
    1. Main chat model calls the `delegate_task(description, category)` tool.
       The tool handler in main.py calls `dispatch(...)` here, which:
         a. Persists the task to SQLite with status='pending'.
         b. Kicks off `_run_task(task_id)` as a background asyncio task.
         c. Returns immediately with {task_id, delegate_model, status: "started"}.
       The tool result flows back to the main model, which sees the ack and
       tells the user "I've delegated this, will circle back" — then remains
       free to answer follow-up questions.

    2. `_run_task` runs concurrently:
         a. Picks a delegate model based on category (config or built-in default).
         b. Calls Ollama /api/chat with the task description as user message
            (plus tools if the category typically benefits from web search).
         c. Stores the raw delegate reply in `delegated_tasks.result`.
         d. Invokes the ORIGINAL main model with a short "annotate this
            result" prompt so the conversation gets a follow-up comment in
            the main model's voice.
         e. Inserts TWO new messages into the conversation:
              * assistant (delegated) — the raw delegate reply
              * assistant (main-model comment) — a short annotation
            Both carry meta.delegated_task_id so the frontend can badge them.

    3. Frontend polls /api/delegate/tasks?conv_id=X every 2s and polls the
       conversation's messages endpoint to catch the new messages when they
       land. No WebSocket needed for the single-user local case.

Cancellation: `cancel(task_id)` sets status='cancelled' and cancels the
running asyncio.Task. The user gets a "cancelled" chip in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import aiosqlite
import httpx

import db as _db

log = logging.getLogger("delegate")


# ── Category → default model mapping ────────────────────────────────────────
# Each category picks a model whose training strengths match the task.
# Overridable via _db config keys `delegate_model_{category}`. Falls through
# to `general` if the main model invents a category we don't recognise.
# Per-category ladder tuned for CAPABILITY not for speed. The user explicitly
# asked to use the biggest / best MoE models, not the tiniest. Ordering:
#   1. MoE thinker (fast wall-clock despite big total-params).
#   2. Frontier dense models.
#   3. Smaller fallbacks only if nothing above is installed.
_CATEGORY_DEFAULTS: dict[str, list[str]] = {
    # "Quick" no longer means "tiny". It means "answer without heavy reasoning".
    # Still prefer big-capability, just skip the deep-thinker.
    "quick":        [
        "qwen3.6:35b-a3b",
        "hf.co/InternScience/Agents-A1-Q4_K_M-GGUF:latest",
        "gemma4:26b", "qwen2.5:32b", "qwen2.5:14b",
    ],
    # Balanced default — MoE thinker with tools capability.
    "general":      [
        "qwen3.6:35b-a3b",
        "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
        "hf.co/InternScience/Agents-A1-Q4_K_M-GGUF:latest",
        "llama3.3:70b", "qwen2.5:32b", "gemma4:26b",
    ],
    # Web-grounded factual answers. Agents-A1's specialty (BrowseComp SOTA),
    # then MoE thinkers with tool support.
    "research":     [
        "hf.co/InternScience/Agents-A1-Q4_K_M-GGUF:latest",
        "qwen3.6:35b-a3b",
        "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
        "llama3.3:70b", "qwen2.5:32b",
    ],
    # Programming. Ornith is Qwen3-based agentic coder with project scope.
    "code":         [
        "ornith:latest",
        "qwen3.6:35b-a3b",   # MoE reasoner is very solid at code
        "qwen2.5-coder:7b",
        "qwen2.5:32b", "llama3.3:70b",
    ],
    # Long-chain reasoning. Use the biggest thinker available.
    "deep":         [
        "deepseek-r1:70b",
        "qwen3.6:35b-a3b",
        "Hydroxide538/qwen-agentworld-35b-a3b:q4_k_m",
        "nemotron-3-nano:30b",
        "deepseek-r1:32b",
        "gemma4:26b", "qwen2.5:32b",
    ],
    # Vision — image + screenshot analysis. Prefer the biggest VLM installed.
    "vision":       [
        "qwen2.5vl:32b",
        "minicpm-v:latest", "openbmb/minicpm-o2.6:8b",
        "qwen2.5vl:7b", "llama3.2-vision:latest",
    ],
    # Very long documents — 128K+ context native.
    "long_context": [
        "llama3.3:70b",
        "qwen3.6:35b-a3b",
        "hf.co/InternScience/Agents-A1-Q4_K_M-GGUF:latest",
        "hf.co/mradermacher/L3.3-70B-Euryale-v2.3-GGUF:q4_k_m",
        "qwen2.5:32b",
    ],
    # Strict structured output. Big models with reliable JSON emission.
    "structured":   [
        "qwen2.5:32b", "qwen3.6:35b-a3b", "llama3.3:70b",
        "hermes3:8b", "qwen2.5:14b",
    ],
    # NEW: emotional / empathic responses. Euryale is Sao10K's flagship for
    # this exact use case; fall through to bigger warm generalists.
    "emotional":    [
        "hf.co/mradermacher/L3.3-70B-Euryale-v2.3-GGUF:q4_k_m",
        "hf.co/bartowski/L3.3-70B-Euryale-v2.3-GGUF:Q4_K_M",
        "hermes3:8b", "gemma4:26b", "llama3.3:70b",
    ],
    # NEW: creative writing — long-form prose, marketing copy, storytelling.
    "creative":     [
        "hf.co/mradermacher/L3.3-70B-Euryale-v2.3-GGUF:q4_k_m",
        "hermes3:8b", "gemma4:26b", "qwen3.6:35b-a3b", "llama3.3:70b",
    ],
}

# Categories the main model advertises to itself in the tool description.
KNOWN_CATEGORIES = list(_CATEGORY_DEFAULTS.keys())

# Categories where the delegate benefits from MCP tools (web search, fetch).
# Wired below to actually attach the tools when the runner picks the model.
_CATEGORIES_WITH_TOOLS = {"research", "general", "quick"}


def _ollama_base() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


async def _installed_models() -> set[str]:
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
        base = p.split(":")[0]
        for m in installed:
            if m == p or m.startswith(p + ":") or m == base:
                return m
    return None


async def _pick_delegate_model(category: str) -> str:
    """
    Resolve the delegate model for `category`:
      1. User's configured `delegate_model_{category}` if installed.
      2. Otherwise the built-in default ladder for that category.
      3. Otherwise the general ladder.
    """
    cat = category if category in _CATEGORY_DEFAULTS else "general"
    installed = await _installed_models()
    configured = (await _db.get_config(f"delegate_model_{cat}")) or ""
    prefs = [configured] + _CATEGORY_DEFAULTS[cat] + _CATEGORY_DEFAULTS["general"]
    return _first_installed(prefs, installed) or "qwen2.5:7b"


# ── Task record CRUD ────────────────────────────────────────────────────────
@dataclass
class DelegatedTask:
    id:              str
    conversation_id: str
    source_msg_id:   str
    prompt:          str
    category:        str
    delegate_model:  str
    main_model:      str
    status:          str
    result:          str
    comment:         str
    error:           str
    created_at:      float
    started_at:      float | None
    completed_at:    float | None

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "conversation_id": self.conversation_id,
            "source_msg_id":   self.source_msg_id,
            "prompt":          self.prompt,
            "category":        self.category,
            "delegate_model":  self.delegate_model,
            "main_model":      self.main_model,
            "status":          self.status,
            "result":          self.result,
            "comment":         self.comment,
            "error":           self.error,
            "created_at":      self.created_at,
            "started_at":      self.started_at,
            "completed_at":    self.completed_at,
        }


async def _row_to_task(r: aiosqlite.Row) -> DelegatedTask:
    return DelegatedTask(
        id              = r["id"],
        conversation_id = r["conversation_id"],
        source_msg_id   = r["source_msg_id"] or "",
        prompt          = r["prompt"],
        category        = r["category"] or "general",
        delegate_model  = r["delegate_model"] or "",
        main_model      = r["main_model"] or "",
        status          = r["status"] or "pending",
        result          = r["result"] or "",
        comment         = r["comment"] or "",
        error           = r["error"] or "",
        created_at      = float(r["created_at"] or 0.0),
        started_at      = float(r["started_at"]) if r["started_at"] is not None else None,
        completed_at    = float(r["completed_at"]) if r["completed_at"] is not None else None,
    )


async def _load(task_id: str) -> DelegatedTask | None:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM delegated_tasks WHERE id=?", (task_id,),
        ) as cur:
            row = await cur.fetchone()
    return await _row_to_task(row) if row else None


async def _update(task_id: str, **kw: Any) -> None:
    if not kw:
        return
    cols = ",".join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [task_id]
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(f"UPDATE delegated_tasks SET {cols} WHERE id=?", vals)
        await conn.commit()


async def list_tasks(
    conv_id: str | None = None,
    status:  str | None = None,
    limit:   int = 50,
) -> list[dict]:
    """List tasks, newest first. Filter by conversation and/or status."""
    q  = "SELECT * FROM delegated_tasks"
    where: list[str] = []
    args:  list[Any] = []
    if conv_id:
        where.append("conversation_id=?")
        args.append(conv_id)
    if status:
        where.append("status=?")
        args.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(q, args) as cur:
            rows = await cur.fetchall()
    return [(await _row_to_task(r)).to_dict() for r in rows]


# ── Running task registry (for cancellation) ────────────────────────────────
_running: dict[str, asyncio.Task] = {}


# ── Live progress buffer (per running task) ─────────────────────────────────
# In-memory only — cleared when the task finishes. Lets the UI show the
# delegate's thinking + streamed content in real time via /api/delegate/<id>/
# progress polling. Not persisted; if the server restarts mid-task, the UI
# just falls back to the row's `result` column when the task completes.
@dataclass
class TaskProgress:
    stage:       str  = "queued"     # queued | picking | streaming | commenting | done
    content:     str  = ""           # streamed reply so far
    thinking:    str  = ""           # streamed <think> content (if any)
    tool_events: list[dict] = None   # type: ignore[assignment]
    tokens:      int  = 0
    started_at:  float = 0.0
    updated_at:  float = 0.0

    def __post_init__(self) -> None:
        if self.tool_events is None:
            self.tool_events = []


_progress: dict[str, TaskProgress] = {}


def get_progress(task_id: str) -> dict | None:
    """Snapshot of a running task's live streaming state, or None if it's finished."""
    p = _progress.get(task_id)
    if not p:
        return None
    return {
        "stage":       p.stage,
        "content":     p.content,
        "thinking":    p.thinking,
        "tool_events": list(p.tool_events),
        "tokens":      p.tokens,
        "started_at":  p.started_at,
        "updated_at":  p.updated_at,
    }


# ── Message insertion (delegated result + main-model comment) ───────────────
# Injected by main.py at startup so we don't have to import from main and
# create a cycle. Signature: (conv_id, role, content, meta) -> new_msg_id
InsertMessageFn = Callable[[str, str, str, dict], Awaitable[str]]
_insert_message: InsertMessageFn | None = None


def set_message_inserter(fn: InsertMessageFn) -> None:
    global _insert_message
    _insert_message = fn


# ── Main-model comment invocation ───────────────────────────────────────────
# Injected by main.py — takes (main_model, prompt, options) and returns text.
# Small non-streaming call, no tools needed.
CommentFn = Callable[[str, str], Awaitable[str]]
_comment: CommentFn | None = None


def set_comment_fn(fn: CommentFn) -> None:
    global _comment
    _comment = fn


# ── Dispatch (called from the delegate_task tool handler) ───────────────────
async def dispatch(
    prompt:      str,
    category:    str,
    conv_id:     str,
    main_model:  str,
    source_msg_id: str = "",
) -> dict:
    """
    Persist a new task + spawn its background runner. Returns the ack dict
    the main model sees as the tool result.
    """
    task_id = f"del-{uuid.uuid4().hex[:12]}"
    cat = category if category in _CATEGORY_DEFAULTS else "general"
    delegate_model = await _pick_delegate_model(cat)

    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO delegated_tasks
               (id, conversation_id, source_msg_id, prompt, category,
                delegate_model, main_model, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, conv_id, source_msg_id, prompt, cat,
             delegate_model, main_model, "pending", time.time()),
        )
        await conn.commit()

    _running[task_id] = asyncio.create_task(_run_task(task_id), name=f"delegate-{task_id}")
    log.info("delegate dispatched task=%s category=%s model=%s", task_id, cat, delegate_model)
    return {
        "task_id":        task_id,
        "delegate_model": delegate_model,
        "category":       cat,
        "status":         "started",
        "message": (
            f"Task delegated to {delegate_model}. "
            f"You can continue chatting — the answer will appear here when ready."
        ),
    }


async def cancel(task_id: str) -> bool:
    """Cancel a running task; returns True if it existed and was running."""
    task = _running.pop(task_id, None)
    if task and not task.done():
        task.cancel()
    row = await _load(task_id)
    if row and row.status in ("pending", "running"):
        await _update(task_id, status="cancelled",
                      completed_at=time.time(),
                      error="cancelled by user")
        return True
    return False


# ── MCP tool bridge (injected by main.py at startup, same pattern as
#    _insert_message / _comment above — keeps this module import-cycle-free).
# Signature: () -> list[dict] returning Ollama-format tool defs.
GetToolsFn = Callable[[], list[dict]]
_get_mcp_tools: GetToolsFn | None = None

# Signature: (name, args) -> str returning the tool result content.
CallToolFn = Callable[[str, dict], Awaitable[str]]
_call_mcp_tool: CallToolFn | None = None


def set_mcp_bridge(get_tools: GetToolsFn, call_tool: CallToolFn) -> None:
    global _get_mcp_tools, _call_mcp_tool
    _get_mcp_tools = get_tools
    _call_mcp_tool = call_tool


# ── The background runner ───────────────────────────────────────────────────
async def _run_task(task_id: str) -> None:
    """
    Execute one delegated task end-to-end. Streams the delegate's reply so
    the UI can show progress in real time; the accumulated content is
    persisted to `result` continuously. Never raises to the caller — all
    failures land in the task's `error` column with status='failed'.
    """
    p = TaskProgress(started_at=time.time(), updated_at=time.time())
    _progress[task_id] = p
    try:
        task = await _load(task_id)
        if not task or task.status == "cancelled":
            return

        await _update(task_id, status="running", started_at=time.time())
        p.stage = "streaming"

        # Attach MCP tools if this category benefits from them (research → web
        # search, general → web search + fetch). We reuse the main app's MCP
        # manager via injected functions — same pattern as _insert_message.
        tools: list[dict] | None = None
        if task.category in _CATEGORIES_WITH_TOOLS and _get_mcp_tools:
            try:
                mcp_tools = _get_mcp_tools()
                if mcp_tools:
                    tools = mcp_tools
            except Exception as exc:
                log.warning("delegate: failed to fetch MCP tools: %s", exc)

        # Detect which of the attached tools are actually web-lookup capable.
        # If a `research` task lands without any web tool, we must NOT let the
        # model claim it looked something up — otherwise the reply is a
        # hallucination. Categorise the tools we've got and adjust the
        # system prompt accordingly.
        web_tool_names: list[str] = []
        if tools:
            for t in tools:
                name = ((t.get("function") or {}).get("name") or "").lower()
                # Match common search/browse tool families the user might have
                # enabled: Brave, DuckDuckGo, Fetch, Puppeteer, Firecrawl.
                if any(kw in name for kw in (
                    "brave", "duckduckgo", "ddg", "google", "fetch",
                    "puppeteer", "firecrawl", "search", "browse",
                )):
                    web_tool_names.append(name)

        has_web_tools = bool(web_tool_names)
        if task.category in {"research", "quick", "general"} and not has_web_tools:
            log.warning(
                "delegate task=%s category=%s dispatched with NO web-lookup tools — "
                "the reply will be based on model training only. Enable Brave/DDG "
                "or Fetch in Settings → Tools to give delegates real web access.",
                task_id, task.category,
            )
            # Surface to the frontend progress buffer as a warning event so
            # the user sees it in the Auxiliary panel.
            p.tool_events.append({
                "name":    "warning",
                "args":    {},
                "status":  "done",
                "preview": (
                    "No web-lookup tools enabled — reply will be based on model "
                    "training data only. Enable Brave/DDG/Fetch in Settings → Tools."
                ),
                "ts":      time.time(),
            })

        system = _delegate_system_prompt(task.category, has_web_tools=has_web_tools)
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user",   "content": task.prompt},
        ]

        try:
            reply = await _stream_with_tools(
                task, p, messages, tools,
                # Wider ctx for long_context / deep / research — those often
                # collect long tool outputs or need deep chains of thought.
                num_ctx     = _ctx_for_category(task.category),
                num_predict = _predict_for_category(task.category),
            )
        except asyncio.CancelledError:
            await _update(task_id, status="cancelled",
                          completed_at=time.time(),
                          error="cancelled during delegate call")
            raise
        except Exception as exc:
            await _fail(task_id, f"delegate call failed: {type(exc).__name__}: {exc}")
            return

        if not reply or not reply.strip():
            await _fail(task_id, "delegate returned empty content")
            return

        # Persist to `delegated_tasks.result` — the right-panel Auxiliary
        # Models tab reads from there.
        await _update(task_id, result=reply)

        # Also insert into the main chat conversation as a new assistant
        # message so the user has a coherent transcript. Badge/meta lets
        # MessageBubble render the "delegated" chip identifying which
        # worker produced it.
        if _insert_message and task.conversation_id:
            try:
                await _insert_message(
                    task.conversation_id, "assistant", reply,
                    {
                        "delegated_task_id":  task_id,
                        "delegated_source":   "delegate",
                        "delegate_model":     task.delegate_model,
                        "delegate_category":  task.category,
                    },
                )
            except Exception as exc:
                log.warning("delegate: failed to insert result msg: %s", exc)

        p.stage = "done"
        await _update(
            task_id,
            status       = "done",
            completed_at = time.time(),
        )
        log.info("delegate task=%s done in %.1fs",
                 task_id, time.time() - (task.started_at or time.time()))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("delegate: unhandled error task=%s: %s", task_id, exc, exc_info=True)
        try:
            await _fail(task_id, f"unhandled: {type(exc).__name__}: {exc}")
        except Exception:
            pass
    finally:
        _running.pop(task_id, None)
        # Clear the progress buffer after a short delay so a slow UI poll
        # cycle can still see the "done" state before we forget the task.
        async def _clear_progress_after():
            await asyncio.sleep(30.0)
            _progress.pop(task_id, None)
        asyncio.create_task(_clear_progress_after())


async def _stream_with_tools(
    task:        DelegatedTask,
    p:           TaskProgress,
    messages:    list[dict],
    tools:       list[dict] | None,
    num_ctx:     int,
    num_predict: int,
    max_rounds:  int = 4,
) -> str:
    """
    Stream Ollama /api/chat, applying tool calls via the injected MCP bridge.
    Returns the final assistant content. Updates `p` (TaskProgress) after
    each chunk so the UI polling endpoint can render progress live.

    Loops until the model stops calling tools or `max_rounds` is hit.
    """
    accumulated: str = ""

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=10.0),
    ) as client:
        for _round in range(max_rounds):
            payload = {
                "model":      task.delegate_model,
                "messages":   messages,
                "stream":     True,
                # Short keep_alive on delegates. When the user switches back
                # to conversation, the main chat model needs to load; if the
                # delegate is still pinned in memory the main model swaps
                # out to disk and inference tanks (3-5 tok/s on M-series).
                # 90s gives back-to-back delegate turns a warm cache without
                # blocking the main model for too long.
                "keep_alive": "90s",
                "options": {
                    "temperature":  0.5,
                    "num_predict":  num_predict,
                    "num_ctx":      num_ctx,
                },
            }
            if tools:
                payload["tools"] = tools

            round_content:  str        = ""
            round_thinking: str        = ""
            round_tools:    list[dict] = []
            saw_done                    = False

            async with client.stream(
                "POST", f"{_ollama_base()}/api/chat", json=payload,
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    raise RuntimeError(f"delegate HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    msg = chunk.get("message") or {}
                    delta_content  = msg.get("content", "") or ""
                    delta_thinking = msg.get("thinking", "") or ""
                    if delta_content:
                        round_content += delta_content
                        p.content    += delta_content
                        p.tokens     += 1
                        p.updated_at  = time.time()
                        # Periodic DB writes so a browser reload mid-stream
                        # still sees the accumulated content in `result`.
                        if p.tokens % 32 == 0:
                            try:
                                await _update(task.id, result=(accumulated + round_content))
                            except Exception:
                                pass
                    if delta_thinking:
                        round_thinking += delta_thinking
                        p.thinking     += delta_thinking
                        p.updated_at    = time.time()
                    calls = msg.get("tool_calls")
                    if isinstance(calls, list) and calls:
                        round_tools.extend(calls)
                    if chunk.get("done"):
                        saw_done = True

            accumulated += round_content

            # No tool calls? We're done.
            if not round_tools or not tools:
                await _update(task.id, result=accumulated)
                return accumulated

            # Record the assistant's tool call + execute each tool.
            messages.append({
                "role":       "assistant",
                "content":    round_content,
                "tool_calls": round_tools,
            })
            for tc in round_tools:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                p.tool_events.append({
                    "name": name, "args": args, "status": "running",
                    "ts":   time.time(),
                })
                p.updated_at = time.time()
                if not _call_mcp_tool:
                    result = "[tool bridge not available]"
                else:
                    try:
                        result = await asyncio.wait_for(
                            _call_mcp_tool(name, args), timeout=45.0,
                        )
                    except asyncio.TimeoutError:
                        result = f"[tool '{name}' timed out]"
                    except Exception as exc:
                        result = f"[tool '{name}' error: {exc}]"
                p.tool_events[-1] = {
                    **p.tool_events[-1],
                    "status":  "done",
                    "preview": (result or "")[:500],
                }
                p.updated_at = time.time()
                messages.append({"role": "tool", "content": result, "name": name})

        # Ran out of rounds — return whatever we've got.
        return accumulated


async def _fail(task_id: str, err: str) -> None:
    await _update(task_id, status="failed",
                  error=err, completed_at=time.time())
    prog = _progress.get(task_id)
    if prog:
        prog.stage      = "failed"
        prog.updated_at = time.time()


# Per-category tuning knobs. Long-context / deep tasks get bigger ctx and
# larger predict budgets because their whole reason for existence is size.
def _ctx_for_category(category: str) -> int:
    return {
        "quick":         4096,
        "general":       8192,
        "research":      16384,   # tool results eat context fast
        "code":          16384,
        "structured":    8192,
        "vision":        8192,
        "deep":          32768,   # reasoners burn thousands of think tokens
        "long_context":  65536,
    }.get(category, 8192)


def _predict_for_category(category: str) -> int:
    return {
        "quick":         512,
        "general":       2048,
        "research":      4096,
        "code":          4096,
        "structured":    2048,
        "vision":        2048,
        "deep":          8192,    # thinker floor
        "long_context":  4096,
    }.get(category, 2048)


def _delegate_system_prompt(category: str, *, has_web_tools: bool = False) -> str:
    """
    Compact system prompt tuned per category. Kept short so the delegate model
    doesn't spend its context budget parsing our meta-instructions.

    `has_web_tools` tells the model whether web-lookup tools are actually
    attached. Never lie about this — a model that thinks it has web access
    will happily fabricate current facts if we tell it to "use its tools"
    when none are attached.
    """
    base = (
        "You are a delegated worker model. The main chat model handed you this "
        "task while it keeps talking to the user. Answer directly and "
        "concisely, in the tone appropriate to the task. When you finish, your "
        "reply is inserted straight into the user's chat with a 'delegated' "
        "badge — so no preamble like 'Here is the answer:' or 'As a delegated "
        "worker…', just answer."
    )
    # Universal anti-hallucination clause — applies to every category.
    honesty = (
        "\n\n## Honesty\n"
        "NEVER fabricate specific facts you don't actually know: dates, "
        "numbers, prices, weather, addresses, quotes, statistics, current "
        "events. If you don't know something with certainty and you don't "
        "have a tool that can look it up, SAY SO explicitly — e.g. "
        "\"I don't have live data on this — my training data cuts off in "
        "<month year>, so I can't tell you what the weather is right now.\" "
        "This applies regardless of how confident you feel; if a fact "
        "would change between when your training data was gathered and "
        "today, you must flag it."
    )
    if category == "research":
        if has_web_tools:
            return base + honesty + (
                "\n\n## Category: research\n"
                "Web-search + fetch tools are attached. USE THEM before you "
                "answer any question about current or verifiable facts. Do NOT "
                "answer from memory when the question is time-sensitive "
                "(weather, news, prices, live events). Cite source URLs at the "
                "bottom of your reply. If the tools fail or return nothing "
                "useful, say so — do not fabricate to fill the gap."
            )
        return base + honesty + (
            "\n\n## Category: research (NO TOOLS)\n"
            "You have NO web-lookup tools attached right now. You MUST NOT "
            "invent specific values for anything that could have changed since "
            "your training data was gathered — weather, news, prices, sports "
            "scores, stock quotes, current events, someone's current status. "
            "Instead, tell the user honestly: \"I can't look this up right "
            "now — please enable Brave Search, DuckDuckGo, or Fetch in "
            "Settings → Tools, then resend. Based on training data alone, "
            "here's what I can offer: <general context, historical info, or "
            "nothing at all>.\" Better to admit ignorance than fabricate."
        )
    if category == "code":
        return base + honesty + (
            "\n\nCategory: code. Reply with the working code first, then a short "
            "explanation. Language and dependencies should be explicit. No filler."
        )
    if category == "quick":
        if has_web_tools:
            return base + honesty + (
                "\n\nCategory: quick. One or two sentences maximum. If the "
                "answer requires a live lookup, use your search tool first."
            )
        return base + honesty + (
            "\n\nCategory: quick. One or two sentences maximum. If you'd need "
            "a live lookup to answer (weather, news, prices), just say so — "
            "don't guess."
        )
    if category == "deep":
        return base + (
            "\n\nCategory: deep. Reason step by step. Show your work in <think> if "
            "your model supports it, then give a crisp conclusion the user can act on."
        )
    if category == "vision":
        return base + honesty + (
            "\n\nCategory: vision. Describe the image contents factually first, then "
            "answer the specific question about it. Note any uncertainty explicitly."
        )
    if category == "long_context":
        return base + honesty + (
            "\n\nCategory: long_context. You have a very large context window. Read "
            "the entire document / conversation carefully. When you answer, quote "
            "the key passages you drew from with line references."
        )
    if category == "structured":
        return base + honesty + (
            "\n\nCategory: structured. Output MUST be valid JSON matching the schema "
            "the user described. No prose, no markdown fences. If the schema is "
            "ambiguous, pick the most conservative interpretation."
        )
    if category == "general":
        if has_web_tools:
            return base + honesty + (
                "\n\nCategory: general. If the question requires a live "
                "lookup, use your search tool — do not answer factual "
                "questions from memory when tools would give you a real answer."
            )
        return base + honesty + (
            "\n\nCategory: general. If a live lookup would change your answer "
            "(weather, current events, prices), say so — do not guess."
        )
    return base + honesty
