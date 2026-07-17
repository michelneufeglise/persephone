"""
Persephone FastAPI backend.
• Ollama chat/models proxy with SSE streaming
• In-process Orpheus TTS (SNAC loaded once at startup)
• SQLite persistent memory
• Fully offline — no HuggingFace Hub calls at runtime
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db as _db
import tts_engine as _tts
import hardware as _hw
import model_catalog as _catalog
import idp_engine as _idp
import mcp_catalog as _mcp
import mcp_manager as _mcp_mgr
import ollama_setup as _ollama
import research as _research
import research_db as _rdb
import embeddings as _emb
import comfy_client as _comfy
import reels_render as _reels
import workers as _workers
import delegate as _delegate
from dataclasses import dataclass


# Delegate hooks — injected into delegate.py at startup so it can insert
# messages into a conversation and ask the main model to add a short
# follow-up comment on the delegate's result, without importing from main
# (which would create a cycle).

async def _delegate_insert_message(
    conv_id: str, role: str, content: str, meta: dict,
) -> str:
    """
    Persist a new message from the delegate flow. Returns the new msg id.
    Uses upsert_message so we get atomic write + conversation.updated_at bump.
    """
    import uuid as _uuid
    msg_id = f"delg-{_uuid.uuid4().hex[:16]}"
    await _db.upsert_message(conv_id, {
        "id":        msg_id,
        "role":      role,
        "content":   content,
        "model":     meta.get("delegate_model") or "delegate",
        "timestamp": time.time() * 1000,
        "meta":      meta,
    })
    return msg_id


async def _delegate_main_model_comment(main_model: str, prompt: str) -> str:
    """
    Ask the main model for a SHORT follow-up comment on the delegate's result.
    Non-streaming, tools-off, small num_predict to keep it snappy. Falls back
    to empty string on any error — the delegate flow is fault-tolerant.
    """
    if not main_model:
        return ""
    payload = {
        "model": main_model,
        "messages": [
            {"role": "system", "content":
             "You are being asked for a very short follow-up comment on a "
             "subtask's result. 1-3 sentences maximum. Do not restate the "
             "delegate's answer."},
            {"role": "user", "content": prompt},
        ],
        "stream":      False,
        "keep_alive":  "5m",
        # Thinker models will burn budget on <think> here for no benefit —
        # a comment is short and doesn't need reasoning. Force off.
        "think":       False,
        "options": {
            "temperature":  0.5,
            "num_predict":  300,
            "num_ctx":      8192,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                return ""
            data = r.json()
            return ((data.get("message") or {}).get("content") or "").strip()
    except Exception:
        return ""


# NOTE: the auto-delegation infrastructure (system-prompt hints, dispatcher
# mode, task classifier, tool-based `delegate_task` injection) has been
# removed. The user explicitly chose the "two-button" model — one button
# sends to main chat, the other to POST /api/delegate/send. There's no
# more classifier deciding for them.


# ── Task classifier ──────────────────────────────────────────────────────────
# Runs before the main model on every turn. Classifies the user's latest
# message into 'task' or 'conversation'. Uses a tiny fast model + JSON output
# format so it's ~50-150ms overhead.
#
# 'conversation' = greetings, acks, meta questions about the assistant,
#                  personal chit-chat, follow-up clarifications.
# 'task'         = anything else that needs work — lookup, analysis, creation,
#                  code, research, summarisation, comparison, etc.

# ── Judge-based category picker for /api/delegate/send ──────────────────────
# The user clicks "send to worker" — we run the judge model against the
# request text to pick the strongest-fit category. Same tiny-model pattern
# as the existing auto-router judge, just with a different category set.
#
# The commented-out block below (`_TASK_CLASSIFIER_*`) is retained as a
# historical reference — see git log for the auto-delegation flow it powered.

# ── Removed: task classifier that ALSO decomposed multi-part tasks. ────────
# See `_judge_delegate_category` below for the new user-triggered picker.

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("persephone")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DIST_DIR    = Path(__file__).parent.parent / "dist"

# ── Hardware-tuned Ollama defaults ────────────────────────────────────────────
OLLAMA_DEFAULTS = {
    "num_thread":     _hw.recommended_num_thread(),  # matches the host's actual core count
    "num_batch":      512,     # larger batch = faster prompt processing
    # 8K is the starting budget for lightweight conversational turns. Bigger
    # prompts (heavy MCP tools, long histories, memory-rich turns) are
    # auto-scaled up to 16 / 32 / 64K per request by _stream_one_round via
    # `_estimate_message_tokens` + `_next_ctx_bucket`.
    "num_ctx":        8192,
    "f16_kv":         True,    # half-precision KV cache (saves bandwidth)
    "use_mmap":       True,
    "repeat_penalty": 1.1,
}


# Ordered ladder of Ollama context sizes we're willing to allocate. Powers of
# two so KV-cache alignment stays clean; capped at 128K (Llama 3.3 / Qwen 3.6 /
# Nemotron 3 Nano top out around there in practice).
_CTX_BUCKETS = (8192, 16384, 32768, 65536, 131072)


def _next_ctx_bucket(tokens_needed: int) -> int:
    """Pick the smallest `_CTX_BUCKETS` value that fits `tokens_needed`."""
    for b in _CTX_BUCKETS:
        if b >= tokens_needed:
            return b
    return _CTX_BUCKETS[-1]


def _estimate_message_tokens(messages: list[dict]) -> int:
    """
    Estimate the total tokens in a chat `messages` array without shelling out
    to a tokenizer. `chars ÷ 4` is the widely-used ballpark for BPE-tokenised
    English/multilingual text and is within ~10% for all Ollama chat models
    we ship.

    Includes tool_calls / tool results too — those are the exact things that
    push the prompt over the default 8K window in real-world MCP use.
    """
    total_chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total_chars += len(c)
        elif isinstance(c, list):
            # multimodal / tool-response array shape
            for part in c:
                if isinstance(part, dict):
                    total_chars += len(part.get("text", "") or "")
        tcalls = m.get("tool_calls")
        if isinstance(tcalls, list):
            for tc in tcalls:
                total_chars += len(json.dumps(tc, default=str))
        name = m.get("name")
        if isinstance(name, str):
            total_chars += len(name)
    # Per-message overhead: role tag, delimiters, ~4 tokens each in most
    # chat templates. Undercounting bites, overcounting is cheap.
    per_message_overhead = 6 * len(messages)
    return (total_chars // 4) + per_message_overhead


# ── Lifespan: pre-load SNAC model ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _db.init_db()
    await _rdb.init_db()
    log.info("Database initialised at %s", _db.DB_PATH)

    # Pre-load SNAC in background so first TTS request is instant
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _tts.load_snac)
    except Exception as exc:
        log.warning("SNAC pre-load failed (TTS disabled): %s", exc)

    # Spawn enabled MCP servers
    try:
        statuses = await _mcp_mgr.manager.sync_with_config()
        if statuses:
            log.info("MCP servers: %s", statuses)
    except Exception as exc:
        log.warning("MCP startup failed: %s", exc)

    # Pre-warm the auto-route judge in the background so the first ambiguous
    # turn doesn't pay a 1-3s cold-load penalty. Non-blocking.
    async def _prewarm_judge():
        try:
            installed = await _installed_models()
            user_pref = await _db.get_config("judge_model") or ""
            judge = user_pref if user_pref in installed else _pick_first_installed(
                ["qwen2.5:1.5b", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:0.5b"],
                installed,
            )
            if not judge:
                return
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{OLLAMA_BASE}/api/generate", json={
                    "model": judge, "prompt": "ok", "stream": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": 1, "num_ctx": 512},
                })
            log.info("auto-route judge warmed: %s", judge)
        except Exception as exc:
            log.debug("judge pre-warm skipped: %s", exc)
    asyncio.create_task(_prewarm_judge())

    # Background workers — Memory Curator + Model Warmer. Idle-gated so they
    # never fight the active chat model for unified memory.
    try:
        import workers_impl as _wimpl
        _wimpl.register_workers()
        await _workers.start()
        log.info("Background workers scheduler started")
    except Exception as exc:
        log.warning("Workers startup failed: %s", exc)

    # Wire the delegate module — it needs a way to insert new messages into
    # a conversation, invoke the main model for a short comment on the
    # delegate's result, AND access the MCP tools + call them so research /
    # general delegates can hit web-search. Inject those hooks here rather
    # than importing main from delegate.py (would create a cycle).
    try:
        _delegate.set_message_inserter(_delegate_insert_message)
        _delegate.set_comment_fn(_delegate_main_model_comment)
        _delegate.set_mcp_bridge(
            get_tools = _mcp_mgr.manager.list_tools_for_ollama,
            call_tool = _mcp_mgr.manager.call,
        )
        log.info("Delegate infrastructure ready")
    except Exception as exc:
        log.warning("Delegate setup failed: %s", exc)

    yield

    # Shut down MCP processes on exit
    try:
        await _mcp_mgr.manager.stop_all()
    except Exception:
        pass
    try:
        await _workers.stop()
    except Exception:
        pass


app = FastAPI(title="Persephone API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── /api/models ───────────────────────────────────────────────────────────────
@app.get("/api/models")
async def get_models():
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            raise HTTPException(502, f"Ollama unreachable: {exc}")


@app.get("/api/models/details/{model_name:path}")
async def get_model_details(model_name: str):
    """Proxy Ollama /api/show — returns parameters, modelfile snippet, details
    (family, parameter_size, quantization_level, format, parent_model)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.post(
                f"{OLLAMA_BASE}/api/show",
                json={"name": model_name},
            )
            if r.status_code != 200:
                raise HTTPException(r.status_code, r.text)
            data = r.json()
            # Slim the modelfile down — clients only need the first ~40 lines
            mf = data.get("modelfile", "")
            if isinstance(mf, str) and mf:
                lines = mf.splitlines()
                data["modelfile"] = "\n".join(lines[:80])
            return data
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"Ollama unreachable: {exc}")


# ── /api/chat  (SSE streaming) ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    options: dict = {}
    tool_model: str = ""  # optional dedicated tool-calling model
    auto_route: bool = False  # when true, server picks the best installed model
    # Optional metadata used to attribute extracted memories. Safe to omit.
    conv_id: str = ""
    user_msg_id: str = ""


# Cache the system-prompt addenda. They change at human-speed (new fact, MCP
# toggle) but get rebuilt on every chat request — caching saves SQLite hits and
# (more importantly) string concatenation that runs before the model can start.
_CTX_TTL_S = 20.0
_memory_ctx_cache: tuple[float, str] = (0.0, "")
_mcp_ctx_cache:    tuple[float, str] = (0.0, "")


def invalidate_context_cache() -> None:
    global _memory_ctx_cache, _mcp_ctx_cache
    _memory_ctx_cache = (0.0, "")
    _mcp_ctx_cache    = (0.0, "")


async def _build_memory_context() -> str:
    """Read stored user facts and format them as a system-prompt section."""
    global _memory_ctx_cache
    now = time.monotonic()
    ts, cached = _memory_ctx_cache
    if cached and now - ts < _CTX_TTL_S:
        return cached

    try:
        # Cap at 30 — most recent first — keeps the prompt compact even if the
        # user has accumulated hundreds of facts. The full list is still visible
        # in the Memory page; this is just what the model sees per turn.
        facts = await _db.list_user_facts(limit=30)
    except Exception:
        return ""
    if not facts:
        _memory_ctx_cache = (now, "")
        return ""

    grouped: dict[str, list[str]] = {}
    for f in facts:
        grouped.setdefault(f["category"], []).append(f["fact"])
    lines = ["", "## What I know about you"]
    for cat, items in grouped.items():
        for it in items[:8]:           # ≤8 facts per category
            lines.append(f"- ({cat}) {it}")
    lines.append("Use these naturally; don't recite them or mention 'memory' unless asked.")
    out = "\n".join(lines)
    _memory_ctx_cache = (now, out)
    return out


# ── Background fact extraction ────────────────────────────────────────────────
_FACT_EXTRACT_PROMPT = (
    "Extract DURABLE facts about the HUMAN USER from this conversation.\n"
    "\n"
    "ABSOLUTELY DO NOT extract:\n"
    "  - Anything the ASSISTANT said about itself (e.g. 'I am Persephone',\n"
    "    'I am an AI', 'I can help you').\n"
    "  - Persona or character details of the assistant.\n"
    "  - Greetings, acks, throwaway lines.\n"
    "  - Time-sensitive info ('I'm tired today').\n"
    "  - Hypotheticals, questions, things the user ASKED ABOUT.\n"
    "\n"
    "DO extract things the USER explicitly stated about themselves:\n"
    "  name, location, age, job, family, hobbies, preferences, projects,\n"
    "  hardware they own, ongoing concerns they mentioned.\n"
    "\n"
    "Format: third person, one sentence per fact.\n"
    "  Good: 'The user is named Alice.'\n"
    "  Good: 'The user works as a backend engineer at Stripe.'\n"
    "  Bad:  'I am Persephone.'         (about assistant — SKIP)\n"
    "  Bad:  'USER asked about names.'  (a question, not a fact — SKIP)\n"
    "\n"
    "Output STRICT JSON (no other text): {\"facts\":[{\"fact\":\"…\",\"category\":\"…\"}]}.\n"
    "Categories: name, location, work, preferences, family, projects, hardware, other.\n"
    "Return {\"facts\":[]} if nothing durable was learned. Never invent."
)

# Reject facts that look like prompt confusion, malformed JSON leakage, or
# assistant-identity-attributed-to-user errors. These are the failure modes
# we've actually seen in production.
_FACT_REJECT_PATTERNS = [
    re.compile(r"[{}\[\]]"),                              # JSON syntax leak
    re.compile(r"\b(category|fact)['\":]"),               # field-name leak
    re.compile(r"\b(USER|ASSISTANT)\b"),                  # uppercase role markers
    re.compile(r"\buser\s+is\s+named\s+(persephone|user|assistant|ai|bot)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(am|can|will|have|provide|am\s+an?)\b", re.IGNORECASE),  # 1st-person leak
    re.compile(r"\b(persephone|the\s+assistant|the\s+ai)\b.*\b(is|am)\b", re.IGNORECASE),
    re.compile(r"^assistant\s+(said|replied|asked)", re.IGNORECASE),
]


def _is_valid_fact(fact: str) -> bool:
    """Return False for facts that look like extraction noise / confusion."""
    f = fact.strip()
    if not (8 <= len(f) <= 240):
        return False
    # Heuristic: must start with "The user" / "User" — every prompt-confused
    # extraction we've seen starts otherwise.
    low = f.lower()
    if not (low.startswith("the user ") or low.startswith("user ")):
        return False
    for pat in _FACT_REJECT_PATTERNS:
        if pat.search(f):
            return False
    return True

# ── Throttle ─────────────────────────────────────────────────────────────────
# Extraction is expensive (a whole second LLM call) so we don't run it on every
# turn — it competes with the user's next message for GPU/CPU. We:
#   • only run on every Nth turn per conversation (counter below)
#   • allow only one extraction in flight globally (semaphore)
#   • skip if another extraction is already running (don't queue, drop it)
#   • cap the work time hard
_EXTRACT_EVERY_N_TURNS = 3
_extract_lock = asyncio.Semaphore(1)
_conv_turn_counter: dict[str, int] = {}
_installed_model_cache: tuple[float, set[str]] = (0.0, set())


async def _installed_models() -> set[str]:
    """Cached set of installed Ollama model tags (60s TTL)."""
    global _installed_model_cache
    now = time.monotonic()
    ts, names = _installed_model_cache
    if names and now - ts < 60.0:
        return names
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            tags = (await client.get(f"{OLLAMA_BASE}/api/tags")).json().get("models", [])
        names = {m["name"] for m in tags}
    except Exception:
        names = set()
    _installed_model_cache = (now, names)
    return names


async def _extract_facts_bg(
    conv_id: str, user_msg_id: str, user_text: str, assistant_text: str,
) -> None:
    """Background fact extraction. Throttled + lock-protected; never blocks the user."""
    # Throttle per-conversation: run on turn 1, 4, 7, … (after first reply, then
    # every 3 turns) so we capture early "my name is …" but don't grind on every reply.
    key = conv_id or "_no_conv"
    n = _conv_turn_counter.get(key, 0) + 1
    _conv_turn_counter[key] = n
    if n != 1 and (n - 1) % _EXTRACT_EVERY_N_TURNS != 0:
        return

    # If another extraction is in flight, drop this one rather than queueing —
    # the user's next message is more important than catching every fact.
    if _extract_lock.locked():
        log.debug("fact extraction skipped — busy")
        return

    async with _extract_lock:
        try:
            # Prefer the smallest installed model — extraction doesn't need a 32B beast.
            preferred = [
                await _db.get_config("memory_model") or "",
                "qwen2.5:0.5b", "qwen2.5:1.5b", "llama3.2:1b", "qwen2.5:3b",
                "llama3.2:3b", "qwen2.5:7b",
            ]
            installed = await _installed_models()
            model = next((c for c in preferred if c and c in installed), None)
            if not model:
                return

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _FACT_EXTRACT_PROMPT},
                    {"role": "user", "content":
                        f"USER said:\n{user_text[:800]}\n\nASSISTANT replied:\n{assistant_text[:800]}"},
                ],
                "format": "json",
                "stream": False,
                # 30s keep_alive — long enough for back-to-back turns to reuse,
                # short enough that VRAM goes back to the chat model.
                "keep_alive": "30s",
                "options": {
                    "temperature": 0.0,
                    "num_predict": 256,
                    "num_ctx": 2048,    # tiny — extraction prompt is small
                },
            }
            async with httpx.AsyncClient(timeout=25.0) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                if r.status_code != 200:
                    return
                raw = ((r.json().get("message") or {}).get("content") or "")

            try:
                parsed = json.loads(raw)
            except Exception:
                return
            facts = parsed.get("facts") if isinstance(parsed, dict) else parsed
            if not isinstance(facts, list):
                return

            for entry in facts[:8]:
                if isinstance(entry, dict):
                    fact = (entry.get("fact") or "").strip()
                    cat  = (entry.get("category") or "general").strip().lower() or "general"
                elif isinstance(entry, str):
                    fact, cat = entry.strip(), "general"
                else:
                    continue
                if not _is_valid_fact(fact):
                    log.debug("rejected fact: %r", fact)
                    continue
                inserted = await _db.add_user_fact(
                    fact=fact, category=cat, confidence=0.8,
                    source_conv=conv_id, source_msg=user_msg_id,
                )
                if inserted:
                    invalidate_context_cache()
        except Exception as exc:
            log.debug("fact extraction failed: %s", exc)


async def _build_mcp_context() -> str:
    """Compact MCP policy prompt — cached.

    NOTE: We deliberately do NOT list every tool here — Ollama already
    receives the full tool definitions via the request's `tools` array. The
    system prompt only needs the *policy* (when to use them).
    """
    global _mcp_ctx_cache
    now = time.monotonic()
    ts, cached = _mcp_ctx_cache
    if cached and now - ts < _CTX_TTL_S:
        return cached

    mgr     = _mcp_mgr.manager
    running = [sid for sid, c in mgr.clients.items() if c.is_running]
    if not running:
        _mcp_ctx_cache = (now, "")
        return ""
    names = ", ".join(running)
    out = (
        "\n\n## Tools available\n"
        f"You have function-calling tools from: {names}. "
        "Use them whenever the question depends on current data (weather, news, prices, "
        "filesystem, time) or anything outside your training. Call the tool — don't guess. "
        "After tool results arrive, synthesize them into a clean answer."
    )
    _mcp_ctx_cache = (now, out)
    return out


def _wants_thinking(model: str) -> bool:
    """Auto-enable native-thinking instructions for known reasoning models."""
    if not model:
        return False
    lower = model.lower()
    # gemma 4 native thinking, qwen3 /think mode, anything tagged "thinking"
    return (
        lower.startswith("gemma4")
        or lower.startswith("qwen3")
        or lower.startswith("ornith")   # Qwen3-based agentic coder
        or "thinking" in lower
        or "reasoning" in lower
    )


def _supports_native_thinking(model: str) -> bool:
    """Models where Ollama exposes a separate `thinking` field via `think: true`."""
    if not model:
        return False
    lower = model.lower()
    return (
        lower.startswith("qwen3")
        or lower.startswith("ornith")   # Qwen3-based agentic coder (architecture=qwen35)
        or lower.startswith("deepseek-r1")
        or lower.startswith("gpt-oss")
        or lower.startswith("nemotron")
        or "thinking" in lower
        or "reasoning" in lower
        or "agentworld" in lower  # community fine-tune of qwen3.6 MoE
    )


# num_predict floors: reasoning models spend thousands of tokens on <think>
# before the visible answer starts. The user-supplied max_tokens (default 2048)
# is a hard cap that truncates the answer mid-word on complex tasks. We floor
# it up to a sensible minimum per model family so complex reasoning has room
# to finish. This is a floor, not a target — models that don't need it stop
# naturally with done_reason=stop.
_EXTENDED_PREDICT_FLOOR = 8192       # qwen3, deepseek-r1, gpt-oss, nemotron, gemma4-thinking
_MOE_REASONING_FLOOR    = 16384      # qwen3.6, agentworld, nemotron-3-nano — MoEs with deep chains


def _predict_floor(model: str) -> int:
    """Minimum num_predict this model should get, regardless of user setting."""
    if not model:
        return 0
    lower = model.lower()
    # MoE reasoning MODELS — the longest chains-of-thought we've seen
    if (
        lower.startswith("qwen3.6")
        or lower.startswith("nemotron-3-nano")
        or "agentworld" in lower
        or "moe" in lower and ("qwen3" in lower or "reasoning" in lower)
    ):
        return _MOE_REASONING_FLOOR
    # Ornith is a Qwen3 agentic coder — it thinks + calls tools + reads
    # tool results (often long directory listings). Needs headroom on par
    # with the MoE reasoners, otherwise it truncates mid-plan.
    if lower.startswith("ornith"):
        return _MOE_REASONING_FLOOR
    # DeepSeek-R1 (dense 70B distill) has famously deep chains-of-thought —
    # on structured tasks like Ableton compose/edit it happily burns 8-12k
    # tokens on <think> before emitting the JSON. Floor at MoE-tier so the
    # visible JSON never gets truncated.
    if lower.startswith("deepseek-r1"):
        return _MOE_REASONING_FLOOR
    # Regular native-thinking models
    if _supports_native_thinking(model):
        return _EXTENDED_PREDICT_FLOOR
    return 0


# num_ctx floors: Ollama's default num_ctx is 8192 for most models, but that
# ceiling routinely trips for long-context models the moment a chat grows or
# includes tool output. Persephone's `_estimate_message_tokens` is a chars/4
# ballpark that undercounts JSON-heavy tool results by 20-40%, so the
# auto-scaler in _stream_one_round can miss the bump. We floor num_ctx here
# by model family so the auto-scaler NEVER hands Ollama a value below the
# floor, regardless of what the frontend sent.
def _ctx_floor(model: str) -> int:
    """Minimum num_ctx this model should get."""
    if not model:
        return 0
    lower = model.lower()
    # Frontier long-context MoE / thinker families — 32K minimum. These
    # models were trained on 128K-262K contexts and comfortably serve 32K
    # without VRAM pressure on M-series unified memory.
    if (
        lower.startswith("qwen3.6")
        or lower.startswith("qwen3")
        or lower.startswith("nemotron-3-nano")
        or lower.startswith("nemotron-3")
        or lower.startswith("deepseek-r1")
        or lower.startswith("ornith")
        or lower.startswith("gemma4")
        or "agentworld" in lower
        or "agents-a1" in lower               # InternScience Agents-A1 (262K native)
        or "internscience/agents-a1" in lower
    ):
        return 32768
    # Regular native-thinking models (older gemma-thinking, etc) — 16K floor.
    if _supports_native_thinking(model):
        return 16384
    return 0


_VISUAL_HINT = (
    "\n\n## Formatting hint\n"
    "Your replies render as rich markdown — headings, blockquotes, bullet lists, "
    "and fenced ```mermaid blocks turn into illustrated typography and real diagrams. "
    "For longer answers (more than one paragraph) feel free to:\n"
    "- use `## section` headings to organise,\n"
    "- pull key insights into `> blockquotes`,\n"
    "- and embed a small mermaid diagram when explaining a flow, comparison, or hierarchy.\n"
    "For short factual answers, plain prose is fine — don't force structure.\n"
    "\n"
    "MERMAID syntax (when you use it):\n"
    "- NO trailing semicolons. `A --> B`, not `A --> B;`\n"
    "- Quote any label with spaces: `A((\"Start node\"))`, `subgraph \"My Group\"`\n"
    "- Declare direction once at the top: `graph TD` or `graph LR`\n"
    "- Example:\n"
    "    ```mermaid\n"
    "    graph LR\n"
    "        A([Start]) --> B[\"Process step\"]\n"
    "        B --> C{\"Decide?\"}\n"
    "        C -- yes --> D([Done])\n"
    "        C -- no --> B\n"
    "    ```"
)


def _thinking_block() -> str:
    return (
        "\n\n## Thinking Mode\n"
        "Wrap your reasoning in <think>…</think> tags BEFORE your final answer. "
        "The user can see this thought process in a collapsed panel. "
        "Be thorough but concise inside the think block; the answer after </think> "
        "should be polished prose."
    )


# ── Routing heuristics ──────────────────────────────────────────────────────
# Tools take ~3K prompt tokens. Only attach the `tools` array when the latest
# user turn looks like it could actually need one. We err on the inclusive side
# (false-positives just cost a few tokens, false-negatives mean the model
# refuses to look something up). Word boundaries matter — without them "rain"
# matches inside "tRAINing", etc.
_TOOL_KEYWORDS = (
    # web / search
    "search", "google", "duckduckgo", "brave", "look up", "lookup",
    "browse", "url", "fetch",
    # current data
    "weather", "forecast", "temperature", "rain", "snow", "today",
    "tonight", "tomorrow", "yesterday", "current", "currently", "latest",
    "news", "headlines", "stock", "price",
    # time
    "timezone", "what day", "what time",
    # filesystem
    "file", "files", "folder", "directory", "documents", "downloads", "desktop",
    # git
    "commit", "branch", "diff", "git",
)
# Substrings that ALWAYS imply tool use regardless of surrounding word boundaries
_TOOL_SUBSTRINGS = ("http://", "https://", "/users/", "~/")
# Compile once: \b…\b around each keyword so "rain" doesn't match "training"
_TOOL_KW_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _TOOL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Casual conversational openers — no need to ship the whole memory block for these.
_TRIVIAL_PATTERN = re.compile(
    r"^\s*(?:hi|hey|hello|yo|sup|hola|thanks|thank you|thx|ty|ok|okay|"
    r"got it|cool|nice|great|good|bye|cya|see ya|gn|gm|gnight|morning)"
    r"[\s!.?]*$",
    re.IGNORECASE,
)


def _likely_needs_tools(messages: list[dict]) -> bool:
    """Cheap heuristic: does the latest user turn look tool-worthy?"""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    if not last_user:
        return False
    low = last_user.lower()
    if any(s in low for s in _TOOL_SUBSTRINGS):
        return True
    return _TOOL_KW_RE.search(last_user) is not None


def _is_trivial_turn(messages: list[dict]) -> bool:
    """Latest user turn is a one-word greeting / ack — no memory needed."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return bool(_TRIVIAL_PATTERN.match(m.get("content") or ""))
    return False


# ── Auto-router ─────────────────────────────────────────────────────────────
# Classifies the latest turn and picks the smallest installed model that fits.
# Each rank is an ordered preference list — first installed wins. Trailing
# entries are fallbacks for users who haven't downloaded specialist models.
_REASONING_RE = re.compile(
    r"\b(prove|derive|step by step|reason|analyse|analyze|"
    r"compare and contrast|why is|explain in detail|in depth)\b",
    re.IGNORECASE,
)
# Personal-recall questions — anything that requires the model to USE stored
# memory facts about the user. Tiny models (0.5b, 1.5b) routinely confuse
# "your name" with "my name" so we always route these to a capable model.
_PERSONAL_RECALL_RE = re.compile(
    r"\b(my\s+(name|age|job|work|location|address|email|family|wife|husband|"
    r"partner|kids?|children|hobby|hobbies|preferences?|favourite|favorite|"
    r"birthday|hometown|background)|"
    r"who\s+am\s+i|"
    r"about\s+me|"
    r"(do|did)\s+you\s+(know|remember)\s+(me|my|that|what|where|when|who)|"
    r"remind\s+me|"
    r"what\s+(did|do)\s+(i|we)\s+(say|talk|discuss|tell)|"
    r"(have|did)\s+i\s+told\s+you)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"```|\b(function|class|def |fn |let |const |var |import |export |"
    r"refactor|implement|bug|stack trace|exception|compile|typescript|"
    r"javascript|python|rust|golang|kotlin|swift)\b",
    re.IGNORECASE,
)

_ROUTER_RULES: list[dict] = [
    {
        "name":   "vision",
        "match":  lambda txt, kind: kind == "vision",
        "ranks":  [
            "qwen2.5vl:7b", "qwen2.5vl:32b", "qwen2.5vl",
            "llama3.2-vision", "openbmb/minicpm-o2.6:8b",
        ],
        "reason":     "image input → vision model",
        "confidence": "high",
    },
    {
        "name":   "code",
        "match":  lambda txt, kind: _CODE_RE.search(txt) is not None,
        "ranks":  [
            "qwen2.5-coder:7b", "qwen2.5-coder:14b", "qwen2.5-coder:32b",
            "deepseek-coder", "qwen2.5:7b", "qwen2.5:32b",
        ],
        "reason":     "code task → coder model",
        "confidence": "high",
    },
    {
        "name":   "personal",
        "match":  lambda txt, kind: _PERSONAL_RECALL_RE.search(txt) is not None,
        # Memory-recall needs at least a 7B model — 0.5b / 1.5b conflate
        # "your name" with their own and ignore the injected facts block.
        "ranks":  [
            "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b",
            "gemma4:12b", "llama3.1:8b", "hermes3:8b",
        ],
        "reason":     "personal recall → memory-capable model",
        "confidence": "high",
    },
    {
        "name":   "trivial",
        "match":  lambda txt, kind: _TRIVIAL_PATTERN.match(txt) is not None,
        "ranks":  [
            "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b",
            "llama3.2:1b", "llama3.2:3b", "qwen2.5:7b",
        ],
        "reason":     "trivial turn → fast tiny model",
        "confidence": "high",
    },
    {
        "name":   "tools",
        "match":  lambda txt, kind: kind == "needs_tools",
        "ranks":  [
            "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b",
            "mistral-nemo", "hermes3:8b", "llama3.1:8b", "llama3.3:70b",
        ],
        "reason":     "tool call expected → tool-capable model",
        "confidence": "high",
    },
    {
        "name":   "reasoning_explicit",
        "match":  lambda txt, kind: _REASONING_RE.search(txt) is not None,
        "ranks":  [
            "qwen3:ohm", "qwen3.6:35b-a3b", "qwen3",
            "deepseek-r1", "nemotron-3-nano:30b", "nemotron",
            "gemma4:26b", "gemma4:12b", "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
        ],
        "reason":     "complex reasoning → reasoning model",
        "confidence": "high",
    },
    {
        "name":   "reasoning_long",
        "match":  lambda txt, kind: len(txt) > 600,
        "ranks":  [
            "qwen3:ohm", "qwen3.6:35b-a3b", "qwen3",
            "deepseek-r1", "nemotron-3-nano:30b", "nemotron",
            "gemma4:26b", "gemma4:12b", "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
        ],
        "reason":     "long input → reasoning model",
        # Length alone is ambiguous (could be a verbose factual question, a
        # pasted log, an essay request, etc.) — flag low so the judge weighs in.
        "confidence": "low",
    },
    {
        "name":   "short",
        "match":  lambda txt, kind: len(txt) < 200,
        "ranks":  [
            "qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b", "gemma4:12b",
        ],
        "reason":     "short factual question → balanced fast model",
        # "Short" is the broadest catch-all — most ambiguous.
        "confidence": "low",
    },
]

_ROUTER_FALLBACK = [
    "gemma4:12b", "qwen2.5:7b", "qwen2.5:32b", "llama3.3:70b",
    "qwen2.5:14b", "qwen2.5:3b",
]


def _pick_first_installed(preferred: list[str], installed: set[str]) -> str | None:
    """Return the first preferred model that's actually installed.
    Matches by exact name first, then by family-prefix as a fallback."""
    # Exact match
    for p in preferred:
        if p in installed:
            return p
    # Prefix match (e.g. "qwen2.5vl" matches "qwen2.5vl:32b")
    for p in preferred:
        for inst in installed:
            if inst.lower().startswith(p.lower() + ":") or inst.lower() == p.lower():
                return inst
    return None


def _route_heuristic(
    preferred: str,
    messages: list[dict],
    installed: set[str],
    tools_attached: bool,
    has_image: bool = False,
) -> tuple[str, str, str]:
    """Pure heuristic router. Returns (model, reason, confidence in {high, low})."""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = (m.get("content") or "").strip()
            break

    kind = "vision" if has_image else ("needs_tools" if tools_attached else "default")
    txt  = last_user.lower()

    for rule in _ROUTER_RULES:
        try:
            if rule["match"](txt, kind):
                pick = _pick_first_installed(rule["ranks"], installed)
                if pick:
                    return pick, f"auto · {rule['reason']}", rule.get("confidence", "high")
        except Exception:
            continue

    if preferred and preferred in installed:
        return preferred, "auto · default", "low"
    fallback = _pick_first_installed(_ROUTER_FALLBACK, installed)
    return fallback or preferred, "auto · fallback", "low"


# ── LLM judge (slow path) ──────────────────────────────────────────────────
# Vision is intentionally NOT exposed to the judge — it can only be decided
# from an actual image attachment, never from text alone (a 0.5b classifier
# would hallucinate "vision" on any mention of "show", "look", etc.).
_JUDGE_CATEGORIES = ["trivial", "code", "tools", "reasoning", "short", "default"]

_JUDGE_PROMPT = (
    "Classify the user message into ONE category. Return ONLY the JSON object.\n\n"
    "Categories (be STRICT — when in doubt prefer 'default'):\n"
    "  trivial   — pure greeting / ack: 'hi', 'thanks', 'ok', 'cool'. NOTHING else.\n"
    "                Any actual question is NOT trivial.\n"
    "  code      — programming task / debug / refactor / code review\n"
    "  tools     — needs live data (weather, news, web search, files, current time)\n"
    "  reasoning — multi-step thinking, math, deep analysis, complex 'why'/'how'\n"
    "  short     — simple factual question that does NOT require recalling personal info\n"
    "  default   — general conversation, advice, brainstorming, opinions, anything\n"
    "                referencing the user themselves ('my', 'me', 'do you remember', 'what did I')\n\n"
    "EXAMPLES:\n"
    '  "hi"                              -> {"category": "trivial"}\n'
    '  "thanks!"                         -> {"category": "trivial"}\n'
    '  "do you know my name"             -> {"category": "default"}\n'
    '  "what did we talk about"          -> {"category": "default"}\n'
    '  "remind me what I said"           -> {"category": "default"}\n'
    '  "what is the capital of France"   -> {"category": "short"}\n'
    '  "what is the weather in Tokyo"    -> {"category": "tools"}\n'
    '  "debug this Python script"        -> {"category": "code"}\n'
    '  "prove that sqrt(2) is irrational"-> {"category": "reasoning"}\n'
    '  "help me brainstorm side projects"-> {"category": "default"}\n\n'
    'Output format (JSON only): {"category": "<word>"}'
)

# Structured output schema — forces Ollama to emit valid JSON with one of the
# enum values. Works on any modern instruct model regardless of size.
_JUDGE_FORMAT = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": _JUDGE_CATEGORIES},
    },
    "required": ["category"],
}

_CATEGORY_RANKS: dict[str, list[str]] = {
    "trivial":   ["qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b", "llama3.2:1b"],
    "code":      ["qwen2.5-coder:7b", "qwen2.5-coder:14b", "deepseek-coder", "qwen2.5:7b"],
    "tools":     ["qwen2.5:7b", "qwen2.5:14b", "mistral-nemo", "hermes3:8b"],
    "reasoning": ["qwen3:ohm", "qwen3.6:35b-a3b", "qwen3", "deepseek-r1", "nemotron",
                  "gemma4:12b", "qwen2.5:14b", "qwen2.5:7b"],
    "short":     ["qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b"],
    "default":   _ROUTER_FALLBACK,
}

_VALID_CATEGORIES = set(_JUDGE_CATEGORIES)
_judge_lock = asyncio.Semaphore(1)


async def _llm_judge(text: str, installed: set[str]) -> str | None:
    """Structured classification by a small but instruction-tuned model.

    Returns one of _VALID_CATEGORIES, or None on timeout / parse failure.
    First-pref: the user's wizard-chosen judge model (stored in the
    `judge_model` config key). Falls back to qwen2.5:1.5b if unset/missing.
    """
    if not text:
        return None
    judge_model = None
    user_pref = await _db.get_config("judge_model") or ""
    if user_pref and user_pref in installed:
        judge_model = user_pref
    if not judge_model:
        judge_pref = ["qwen2.5:1.5b", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:0.5b"]
        judge_model = _pick_first_installed(judge_pref, installed)
    if not judge_model:
        return None

    payload = {
        "model": judge_model,
        "messages": [
            {"role": "system", "content": _JUDGE_PROMPT},
            {"role": "user",   "content": text[:600]},
        ],
        "stream": False,
        "keep_alive": "5m",
        "format": _JUDGE_FORMAT,
        "options": {
            "temperature":  0.0,
            "num_predict":  20,
            "num_ctx":      1024,
        },
    }
    try:
        async with _judge_lock:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                return None
            raw = ((r.json().get("message") or {}).get("content") or "").strip()
        try:
            obj = json.loads(raw)
            cat = (obj.get("category") or "").strip().lower()
        except Exception:
            # Schema failed — try a permissive single-word fallback
            cat = re.split(r"[^a-z]+", raw.lower())[0] if raw else ""
        return cat if cat in _VALID_CATEGORIES else None
    except Exception:
        return None


# ── Per-conversation cache ─────────────────────────────────────────────────
# Once we've decided a conv is "code" or "reasoning", reuse that decision for
# follow-up turns rather than re-judging on every message. Decision evicts on
# TTL or on category shift (heuristic + judge agree on a new category).
_route_cache: dict[str, tuple[float, str, str, str]] = {}  # conv → (ts, model, reason, category)
# Category-aware TTL: low-trust classifications (trivial / short / default) get
# a short lease so a single misfire doesn't poison the whole conversation.
_CATEGORY_TTL_S: dict[str, float] = {
    "trivial":   20.0,
    "short":     30.0,
    "default":   60.0,
    "tools":     180.0,
    "code":      300.0,
    "reasoning": 300.0,
    "vision":    300.0,
    "heuristic": 60.0,
}
_ROUTE_CACHE_TTL_DEFAULT = 60.0


async def _route_model(
    preferred: str,
    messages: list[dict],
    installed: set[str],
    tools_attached: bool,
    has_image: bool = False,
    conv_id: str = "",
) -> tuple[str, str]:
    """Hybrid router.

    1. Run the heuristic. If high confidence → return immediately.
    2. Check per-conv cache. If we routed this conv recently → reuse.
    3. Otherwise consult the LLM judge (3s hard timeout); if it picks a
       different category and we have a matching model installed, use it.
    4. Fall back to the heuristic result if anything goes sideways.
    """
    h_model, h_reason, h_conf = _route_heuristic(
        preferred, messages, installed, tools_attached, has_image,
    )

    # Fast path
    if h_conf == "high":
        return h_model, h_reason

    # Cache hit (category-aware TTL — low-trust picks expire fast)
    if conv_id:
        cached = _route_cache.get(conv_id)
        if cached:
            ts, c_model, c_reason, c_cat = cached
            ttl = _CATEGORY_TTL_S.get(c_cat, _ROUTE_CACHE_TTL_DEFAULT)
            if time.monotonic() - ts < ttl:
                return c_model, c_reason + " (cached)"

    # Judge path
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = (m.get("content") or "").strip()
            break

    try:
        category = await asyncio.wait_for(
            _llm_judge(last_user, installed), timeout=6.0,
        )
    except asyncio.TimeoutError:
        category = None

    if category:
        ranked = _CATEGORY_RANKS.get(category, [])
        pick = _pick_first_installed(ranked, installed)
        if pick:
            reason = f"auto · judge: {category}"
            if conv_id:
                _route_cache[conv_id] = (time.monotonic(), pick, reason, category)
            return pick, reason

    # Judge failed / no model — stick with heuristic
    if conv_id:
        _route_cache[conv_id] = (time.monotonic(), h_model, h_reason, "heuristic")
    return h_model, h_reason


# Sliding-window cap on conversation history sent to the model. The full
# transcript stays in SQLite + Zustand; this only trims the *prompt*. With
# persistent memory facts, older messages can still be effectively recalled.
_HISTORY_TURN_CAP = 16


def _trim_history(messages: list[dict]) -> list[dict]:
    """Keep system message + last _HISTORY_TURN_CAP non-system messages."""
    if len(messages) <= _HISTORY_TURN_CAP + 1:
        return messages
    has_system = messages and messages[0].get("role") == "system"
    if has_system:
        head = messages[:1]
        tail = messages[1:][-_HISTORY_TURN_CAP:]
        return [*head, *tail]
    return messages[-_HISTORY_TURN_CAP:]


async def _augment_messages(model: str, messages: list[dict]) -> list[dict]:
    """Inject memory, MCP context, and thinking instructions into the system message.

    Builds the two heavy context blocks in parallel via asyncio.gather. Memory
    is skipped entirely for trivial turns (greetings/acks) to save tokens and
    prompt-eval time.
    """
    trivial = _is_trivial_turn(messages)
    if trivial:
        # parallel still — mcp + thinking are cheap
        mcp_ctx, _ = await asyncio.gather(_build_mcp_context(), asyncio.sleep(0))
        memory_ctx = ""
    else:
        memory_ctx, mcp_ctx = await asyncio.gather(
            _build_memory_context(),
            _build_mcp_context(),
        )
    think_ctx = _thinking_block() if _wants_thinking(model) else ""
    # Tiny formatting hint — the UI renders rich markdown into illustrations,
    # so encourage models to use it. Skipped for trivial turns to keep them
    # snappy and prevent acks from emitting headings.
    visual_ctx = "" if trivial else _VISUAL_HINT
    addendum  = (memory_ctx + mcp_ctx + think_ctx + visual_ctx).strip()

    if not addendum:
        return _trim_history(messages)

    if messages and messages[0].get("role") == "system":
        head = messages[0]
        new_sys = {**head, "content": (head.get("content", "") + "\n\n" + addendum).strip()}
        return _trim_history([new_sys, *messages[1:]])

    return _trim_history([{"role": "system", "content": addendum}, *messages])


_MAX_TOOL_ITERATIONS = 5

_FALLBACK_SYNTH_PREF = [
    "qwen2.5:7b", "qwen2.5:14b", "gemma4:12b",
    "llama3.1:8b", "hermes3:8b", "qwen2.5:32b",
]


async def _stream_fallback_synthesis(
    client: httpx.AsyncClient, messages: list[dict], options: dict,
) -> AsyncIterator[str]:
    """Silent retry when a reasoning model's tool-synthesis round emits only
    thinking with no visible content. Pick a fast non-thinking model and
    stream a proper answer using the same conversation + tool results.

    Yields raw SSE frames (data: ... \\n\\n) ready to forward to the client.
    """
    try:
        installed = await _installed_models()
        fallback = next(
            (p for p in _FALLBACK_SYNTH_PREF
             if p in installed or any(x.startswith(p + ":") for x in installed)),
            None,
        )
        if not fallback:
            return

        log.info("empty tool-synth from reasoning model → retry with %s", fallback)
        # Nudge the fallback to acknowledge that the tool has already been
        # invoked and to just write the answer.
        nudged = list(messages) + [{
            "role": "user",
            "content": (
                "The search results above are already available — use them to "
                "answer the question in a clear, well-organised markdown reply "
                "with inline [N] citations that reference each source URL."
            ),
        }]

        async for raw_line, chunk in _stream_one_round(
            client, fallback, nudged, options, None,
        ):
            # Rewrite the model name in the raw payload so the UI badge reads
            # the *originally chosen* model. The fallback is an implementation
            # detail; the user asked to talk to model X, not model Y.
            try:
                obj = json.loads(raw_line)
                if isinstance(obj, dict) and "message" in obj:
                    yield f"data: {json.dumps(obj)}\n\n"
                    continue
            except Exception:
                pass
            yield f"data: {raw_line}\n\n"
    except Exception as exc:
        log.warning("fallback synthesis failed: %s", exc)


async def _stream_one_round(
    client: httpx.AsyncClient, model: str, messages: list[dict],
    options: dict, tools: list[dict] | None,
) -> AsyncIterator[tuple[str, dict]]:
    """
    Stream one /api/chat request. Yields (raw_line, parsed_chunk) per token.
    Caller decides whether to forward raw_line to the SSE response.
    """
    # Detect whether we're synthesising after a tool call. On that round we
    #   - disable native thinking (otherwise the model spends its whole
    #     num_predict budget re-thinking about the tool result and truncates
    #     the visible answer with done_reason=length)
    #   - bump num_predict to at least 3072 so even a big DDG result has
    #     room to be summarised in full
    is_tool_synthesis = any(m.get("role") == "tool" for m in messages)

    # Reasoning-model num_predict floor. Long-chain thinkers (qwen3, qwen3.6,
    # AgentWorld, deepseek-r1, nemotron-3-nano, gpt-oss) burn thousands of
    # tokens on <think> before the answer starts; the frontend default of
    # 2048 truncates them mid-answer with done_reason=length. We floor it up
    # to a per-family minimum so complex tasks have room to finish.
    user_predict     = int(options.get("num_predict", 2048) or 2048)
    reasoning_floor  = _predict_floor(model)
    round_options    = dict(options)  # shallow copy — we may add/override keys
    effective_floor  = max(3072 if is_tool_synthesis else 0, reasoning_floor)
    if effective_floor > user_predict:
        round_options["num_predict"] = effective_floor
        log.info(
            "raised num_predict %d → %d for %s (tool_synth=%s reasoning=%s)",
            user_predict, effective_floor, model,
            is_tool_synthesis, reasoning_floor > 0,
        )

    # Generic prompt-size auto-scale (applies to EVERY request, every model).
    #
    # The old 8K default explodes with even modest MCP-tool loads:
    #   memory facts (30 lines) ≈ 600 tokens
    #   MCP policy + N tools    ≈ 2000-4000 tokens
    #   character prompt        ≈ 400 tokens
    #   history (16 turns)      ≈ 2000-4000 tokens
    #   the model's reply room  ≈ 2000 tokens
    # Total commonly clears 8K → Ollama returns HTTP 400 "exceed_context_size_error".
    #
    # We estimate prompt tokens (chars ÷ 4 is well-known 5% ballpark) and pick
    # the smallest power-of-2 ctx window that comfortably holds prompt + reply.
    est_prompt_tokens = _estimate_message_tokens(messages)
    room_for_reply    = max(2048, int(round_options.get("num_predict", 2048) or 2048))
    # Safety margin was 512 but chars/4 underestimates JSON/code-heavy tool
    # output by 20-40%, so bump to 2048 — costs nothing when we don't need it.
    needed_ctx        = est_prompt_tokens + room_for_reply + 2048
    cur_ctx           = int(round_options.get("num_ctx", 8192) or 8192)
    scaled_ctx        = _next_ctx_bucket(needed_ctx)
    # Apply per-model floor AFTER auto-scale — long-context models never get
    # less than their floor, regardless of what the frontend sent OR what the
    # bucketing math produced.
    ctx_floor         = _ctx_floor(model)
    if ctx_floor > scaled_ctx:
        scaled_ctx    = _next_ctx_bucket(ctx_floor)
    if scaled_ctx > cur_ctx:
        round_options["num_ctx"] = scaled_ctx
        log.info(
            "raised num_ctx %d → %d (prompt≈%d tokens + reply≈%d, model_floor=%d)",
            cur_ctx, scaled_ctx, est_prompt_tokens, room_for_reply, ctx_floor,
        )

    # Reasoning-model tool-synthesis context bump. When a reasoning model
    # summarises a tool result, the prompt already carries: system + memory +
    # MCP policy + character prompt + user turn + assistant tool_call +
    # role:tool result (often 500-2000 tokens). Add ≥8K of <think> tokens on
    # top and the default 8192 num_ctx is exceeded → Ollama silently
    # truncates the input, leaving the model with a partial tool result and
    # confused output. Raise num_ctx to 32K so the whole conversation +
    # thinking has room.
    if is_tool_synthesis and reasoning_floor > 0:
        cur_ctx = int(round_options.get("num_ctx", 8192) or 8192)
        if cur_ctx < 32768:
            round_options["num_ctx"] = 32768
            log.info("raised num_ctx %d → 32768 for %s (reasoning tool synth)",
                     cur_ctx, model)

    # Ornith Coder context bump. Ornith is meant to explore a real repo via
    # persephone-fs — a single `list_directory` on src/ + a system prompt with
    # the plan/approve workflow can easily be 3-5K tokens BEFORE thinking. The
    # default 8K num_ctx runs out on the FIRST tool round (not just synthesis),
    # so we widen it up-front for every Ornith round.
    if model.lower().startswith("ornith"):
        cur_ctx = int(round_options.get("num_ctx", 8192) or 8192)
        if cur_ctx < 32768:
            round_options["num_ctx"] = 32768
            log.info("raised num_ctx %d → 32768 for ornith (agentic coder)",
                     cur_ctx)

    payload: dict = {
        "model":   model,
        "messages": messages,
        "options": round_options,
        "stream":  True,
        # Keep the chat model resident for 10 minutes so back-to-back turns
        # skip the 1-5s reload. -1 (forever) sounds nice but on M-series with
        # unified memory it pins multiple models at once and tanks throughput
        # — 10m is the sweet spot: idle models evict, active stays hot.
        "keep_alive": "10m",
    }
    if tools:
        payload["tools"] = tools

    # `think` policy:
    #   - Casual thinkers (gemma4, etc — no reasoning_floor) burn their token
    #     budget on <think> after seeing a tool result → force think:false on
    #     the tool-synthesis round so they emit the actual answer.
    #   - Reasoning-trained models (qwen3, qwen3.6/AgentWorld, deepseek-r1,
    #     nemotron*) are TRAINED to always think first. Forcing think:false on
    #     them makes them emit a few confused tokens and stop. Keep think:true
    #     for them — the reasoning_floor above (8-16K) gives plenty of room.
    is_reasoning_model = reasoning_floor > 0
    if is_tool_synthesis and not is_reasoning_model:
        payload["think"] = False
    elif _supports_native_thinking(model):
        payload["think"] = True

    async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")
            log.warning("Ollama chat returned %d: %s", resp.status_code, body[:300])
            yield body, {"error": body, "done": True}
            return

        async for raw_line in resp.aiter_lines():
            if not raw_line.strip():
                continue
            try:
                chunk = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            yield raw_line, chunk


async def _stream_ollama_chat(
    model: str, messages: list[dict], options: dict, tool_model: str = "",
    conv_id: str = "", user_msg_id: str = "", auto_route: bool = False,
) -> AsyncIterator[str]:
    """
    Multi-round chat stream with MCP tool calling.

    When `tool_model` is set and differs from `model`, tool-calling rounds run on
    the tool_model (output suppressed from the user), and the final natural-language
    answer is streamed from the active `model`. This lets a small tool-capable
    model (e.g. qwen2.5) drive tool selection for an otherwise tool-less chat
    model (e.g. gemma3).

    Custom SSE events emitted in addition to raw Ollama chunks:
      data: {"tool_event": "start", "id": "...", "name": "...", "args": {...}}
      data: {"tool_event": "end",   "id": "...", "preview": "...", "error": null}
    """
    merged_opts = {**OLLAMA_DEFAULTS, **options}
    # Capture the latest USER turn from the raw history so we can extract
    # memories after the assistant finishes. We do this before augmentation
    # adds the system prompt with our internal context.
    last_user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_text = m.get("content", "") or ""
            break

    raw_messages = list(messages)
    messages     = await _augment_messages(model, raw_messages)

    # Only attach tools when the latest user turn looks tool-worthy. Saves
    # 2-3K prompt tokens on conversational turns, which cuts prompt-eval
    # latency proportionally.
    #
    # EXCEPTION: agentic coders (Ornith) are *designed* to always reach for
    # tools — filesystem read, git, etc. Their system prompt tells them to
    # "read files before answering", so gating tools by keyword makes them
    # hallucinate fake shell blocks when the user just says "summarise this
    # project". Force tools on for those models.
    all_tools     = _mcp_mgr.manager.list_tools_for_ollama()
    is_ornith     = model.lower().startswith("ornith")
    force_tools   = is_ornith
    tools         = all_tools if (all_tools and (force_tools or _likely_needs_tools(messages))) else []

    # Main chat is now completely delegation-free. The user explicitly
    # controls dispatch via the "send to worker" button in the UI, which
    # hits POST /api/delegate/send directly. This endpoint just streams a
    # normal chat completion from the active model — same as before all
    # the delegate infrastructure existed.

    # Ornith scope-lock: agentic coder mode is meant for the Persephone repo
    # only. If the generic `filesystem` MCP is enabled it exposes writable
    # paths like ~/Documents, ~/Downloads, ~/Desktop — dropping those into
    # Ornith's tool array is a footgun (it could rm or edit unrelated files
    # while thinking it's "cleaning up"). Filter to `persephone-fs__*` for
    # any filesystem operation. Keep git and the rest untouched.
    if is_ornith and tools:
        blocked_prefixes = ("filesystem__",)  # add other broad-scoped fs servers here if any
        before = len(tools)
        tools  = [t for t in tools if not t["function"]["name"].startswith(blocked_prefixes)]
        if len(tools) != before:
            log.info(
                "ornith scope-lock: filtered %d filesystem tool(s) — persephone-fs only",
                before - len(tools),
            )

    # Auto-route: swap `model` for the best installed match before the first
    # round. Routing decision is made *after* tool-gating so the router knows
    # whether tools are coming along.
    if auto_route:
        try:
            installed = await _installed_models()
            chosen, reason = await _route_model(
                preferred=model,
                messages=raw_messages,
                installed=installed,
                tools_attached=bool(tools),
                conv_id=conv_id,
            )
            if chosen and chosen != model:
                log.info("auto-route: %s → %s (%s)", model, chosen, reason)
                model = chosen
            yield f"data: {json.dumps({'route': model, 'reason': reason})}\n\n"
        except Exception as exc:
            log.warning("auto-route failed, using preferred: %s", exc)

    delegate  = bool(tool_model and tool_model != model and tools)
    final_assistant_text = ""

    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as client:
        for iteration in range(_MAX_TOOL_ITERATIONS):
            round_model = tool_model if delegate else model
            assistant_content = ""
            tool_calls: list[dict] = []
            saw_done = False

            try:
                async for raw_line, chunk in _stream_one_round(
                    client, round_model, messages, merged_opts,
                    tools if tools else None,
                ):
                    msg = chunk.get("message", {}) if isinstance(chunk, dict) else {}
                    content_delta  = msg.get("content", "") or ""
                    thinking_delta = msg.get("thinking", "") or ""
                    assistant_content += content_delta

                    calls = msg.get("tool_calls")
                    if isinstance(calls, list) and calls:
                        tool_calls.extend(calls)

                    if chunk.get("done"):
                        saw_done = True

                    # Native Ollama thinking → emit as our own thinking event
                    if thinking_delta:
                        yield f"data: {json.dumps({'thinking': thinking_delta})}\n\n"

                    # Delegated tool-model: surface its tokens as thinking so the
                    # user can see the reasoning behind tool selection.
                    if delegate and content_delta:
                        yield f"data: {json.dumps({'thinking': content_delta})}\n\n"

                    has_calls = bool(tool_calls and tools)
                    if delegate:
                        # Don't forward the tool-model's raw chunks — its content
                        # has already gone out as thinking events above.
                        forward = False
                    else:
                        forward = not (calls or (chunk.get("done") and has_calls))

                    if forward:
                        # Native thinking comes inside the same chunks as content
                        # for some models — strip the thinking field before
                        # forwarding so the UI doesn't double-render it.
                        if thinking_delta and "thinking" in msg:
                            cleaned = {**chunk, "message": {**msg, "thinking": ""}}
                            yield f"data: {json.dumps(cleaned)}\n\n"
                        else:
                            yield f"data: {raw_line}\n\n"

                    if chunk.get("error"):
                        yield "data: [DONE]\n\n"
                        return
            except Exception as exc:
                log.exception("Chat stream error: %s", exc)
                yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # No tool calls this round
            if not tool_calls or not tools:
                if delegate:
                    # The tool model decided no tool was needed — hand off to the
                    # active model so the user gets a proper streamed answer.
                    delegate = False
                    continue

                # Reasoning-model tool-synthesis failure mode: the model spent
                # its whole budget on <think> and never emitted visible content.
                # The user sees an empty bubble even though the tool call
                # succeeded. Silently retry the synthesis on a fast non-
                # thinking model so a real answer arrives.
                had_tool_msg = any(m.get("role") == "tool" for m in messages)
                if (
                    saw_done and had_tool_msg
                    and not assistant_content.strip()
                    and _predict_floor(round_model) > 0   # was a reasoning model
                ):
                    async for line in _stream_fallback_synthesis(
                        client, messages, merged_opts,
                    ):
                        # Track the fallback's content too so background
                        # fact extraction still has a useful sample.
                        try:
                            payload = json.loads(line[len("data: "):]) if line.startswith("data: ") else None
                            if isinstance(payload, dict):
                                delta = (payload.get("message") or {}).get("content", "")
                                if delta:
                                    assistant_content += delta
                        except Exception:
                            pass
                        yield line

                if saw_done:
                    final_assistant_text = assistant_content
                    yield "data: [DONE]\n\n"
                    # Fire-and-forget background fact extraction. Don't await —
                    # the response is already streamed and the user is gone.
                    if last_user_text and final_assistant_text:
                        asyncio.create_task(_extract_facts_bg(
                            conv_id      = conv_id,
                            user_msg_id  = user_msg_id,
                            user_text    = last_user_text,
                            assistant_text = final_assistant_text,
                        ))
                return

            # Tool calls present — record + execute
            messages.append({
                "role":      "assistant",
                "content":   assistant_content,
                "tool_calls": tool_calls,
            })

            for i, tc in enumerate(tool_calls):
                fn   = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                call_id = tc.get("id") or f"call-{iteration}-{i}"

                yield f"data: {json.dumps({'tool_event': 'start', 'id': call_id, 'name': name, 'args': args})}\n\n"

                err   = None
                value = ""
                # Ornith scope-lock (defense in depth): reject any filesystem
                # tool call that isn't routed through persephone-fs, even if
                # the model somehow references it. The tool schema was already
                # filtered above, so this only fires on jailbreak / stale
                # message-history references.
                if is_ornith and name.startswith("filesystem__"):
                    err = (
                        f"Blocked: {name} is outside the Persephone repo scope. "
                        f"Use persephone-fs__* tools for any file operation."
                    )
                    log.warning("ornith scope-lock: rejected tool call %s", name)

                try:
                    if err is None:
                        value = await asyncio.wait_for(
                            _mcp_mgr.manager.call(name, args), timeout=45.0,
                        )
                except asyncio.TimeoutError:
                    err = f"Tool '{name}' timed out"
                except Exception as exc:
                    err = str(exc)

                content_for_model = value if not err else f"[tool error: {err}]"
                preview           = (value or err or "")[:600]

                yield f"data: {json.dumps({'tool_event': 'end', 'id': call_id, 'name': name, 'preview': preview, 'error': err})}\n\n"

                messages.append({
                    "role":    "tool",
                    "content": content_for_model,
                    "name":    name,
                })

            # When delegating, after the first successful tool round we want the
            # active model to write the final answer using the tool results.
            if delegate:
                delegate = False
            continue

        yield f"data: {json.dumps({'error': 'Max tool iterations reached', 'done': True})}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # Tell the workers scheduler the user is active — pauses background
    # workers so they don't compete for the model's memory slot mid-turn.
    _workers.touch_user_activity()
    return StreamingResponse(
        _stream_ollama_chat(
            req.model, req.messages, req.options, req.tool_model,
            conv_id=req.conv_id, user_msg_id=req.user_msg_id,
            auto_route=req.auto_route,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── /api/generate (non-streaming proxy) ───────────────────────────────────────
@app.post("/api/generate")
async def generate(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(f"{OLLAMA_BASE}/api/generate", json=body)
            return r.json()
        except Exception as exc:
            raise HTTPException(502, str(exc))


# ── /api/tts ──────────────────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    voice: str  = "tara"
    speed: float = 1.0


@app.post("/api/tts")
async def tts(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(400, "text is required")

    loop = asyncio.get_running_loop()
    try:
        # synthesize is async-native (uses httpx internally)
        wav = await _tts.synthesize(req.text.strip(), req.voice, req.speed)
    except Exception as exc:
        log.error("TTS error: %s", exc, exc_info=True)
        raise HTTPException(500, f"TTS failed: {exc}")

    return Response(content=wav, media_type="audio/wav")


@app.get("/api/tts/voices")
async def tts_voices():
    return {"voices": _tts.VOICES}


# ── /api/reels — short-form vertical video studio ────────────────────────────
# Pipeline stages (built incrementally):
#   1. plan   : LLM decomposes topic → scenes with script + SD prompt + seconds
#   2. images : ComfyUI submits a stable-diffusion workflow per scene
#   3. voice  : Kokoro TTS speaks each scene's script line
#   4. render : ffmpeg composites 1080×1920 with Ken Burns + captions + music
# This section ships (1) + (2 status probe). (3) already exists via /api/tts.
# (4) is the follow-up commit.

COMFY_HOST = os.getenv("COMFY_HOST", "http://127.0.0.1:8188")

_TONE_HINTS = {
    "informative": "clear, factual, listicle-friendly; front-load the payoff",
    "energetic":   "punchy openings, short sentences, hook in first 3 seconds",
    "calm":        "slow deliberate pacing, gentle imagery, breath between beats",
    "dramatic":    "cinematic tension, escalating stakes, quotable close",
    "luxury":      "editorial refinement, brand-tier vocabulary, uncluttered visuals",
}


class ReelPlanRequest(BaseModel):
    topic:    str
    tone:     str  = "informative"
    duration: int  = 30       # seconds
    aspect:   str  = "9:16"
    voice:    str  = "af_heart"


def _scenes_target(duration_s: int) -> int:
    # ~4s per scene: fast enough for TikTok, slow enough to read.
    # Clamp so a 15s reel still has 3 scenes and a 60s reel has 12.
    return max(3, min(12, round(duration_s / 4)))


# Preferred models for the reels planner. The task is a small JSON emit —
# a fast non-thinking model is ideal. Thinking-first models (agentworld,
# qwen3.6, nemotron-3-nano, ornith, deepseek-r1) burn their whole
# num_predict budget on <think> before writing JSON and return empty
# `content`, which is what caused the "empty plan from model" error.
_PLANNER_PREF = [
    "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
    "hermes3:8b",  "llama3.2:3b", "qwen2.5:1.5b",
]


async def _pick_planner_model() -> str:
    installed = await _installed_models()
    for pref in _PLANNER_PREF:
        if pref in installed or any(x.startswith(pref + ":") for x in installed):
            return pref
    # Absolute fallback: whatever's active, and we'll force think:false + big budget.
    return await _db.get_config("active_model") or "qwen2.5:7b"


async def _stream_reels_plan(req: ReelPlanRequest) -> AsyncIterator[str]:
    n_scenes    = _scenes_target(req.duration)
    tone_hint   = _TONE_HINTS.get(req.tone, _TONE_HINTS["informative"])
    planner_model = await _pick_planner_model()

    system = (
        "You are a short-form video scriptwriter for TikTok / Instagram Reels / "
        f"YouTube Shorts. Aspect {req.aspect}. Total duration {req.duration}s. "
        f"Tone: {tone_hint}.\n"
        f"Output EXACTLY {n_scenes} scenes as strict JSON — no prose, no markdown fences.\n"
        "Schema: {\"scenes\":[{\"n\":int,\"script\":str,\"imagePrompt\":str,\"seconds\":int}, ...]}\n"
        "Rules:\n"
        "  - script: 1 spoken line, 6-18 words, natural read-aloud rhythm.\n"
        "  - imagePrompt: a vivid Stable-Diffusion prompt for a single still image\n"
        "    that visualises this scene. Include style tokens (cinematic, 35mm,\n"
        "    editorial, high-contrast, etc). Never mention 'text', 'letters', or\n"
        "    'watermark' — the caption is burned in separately.\n"
        "  - seconds: how long the scene stays on screen. All seconds must sum to "
        f"{req.duration}.\n"
        "  - Scene 1 must be a hook that stops the scroll in the first 2 seconds."
    )
    user = f"Topic: {req.topic.strip()}"

    payload = {
        "model":    planner_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "format":   "json",
        "stream":   False,
        # Belt-and-braces: even if the picked model is thinking-capable, force
        # native thinking off. And give plenty of predict headroom so a big
        # scene plan (12 scenes at 60s) doesn't get truncated.
        "think":    False,
        "options":  {
            **OLLAMA_DEFAULTS,
            "num_ctx":     8192,
            "num_predict": 4096,
            "temperature": 0.7,
        },
        "keep_alive": "5m",
    }

    log.info("reels plan: model=%s scenes=%d duration=%ds", planner_model, n_scenes, req.duration)

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                body = r.text
                yield f"data: {json.dumps({'error': f'planner {r.status_code}: {body[:200]}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            data = r.json()
            msg = data.get("message") or {}
            content  = (msg.get("content") or "").strip()
            thinking = (msg.get("thinking") or "").strip()
            if not content:
                # Diagnostic: some servers emit the JSON in `thinking` when
                # think:false was ignored. Try to salvage it.
                salvage = thinking
                log.warning(
                    "reels plan: empty content from %s (thinking=%d chars, done_reason=%r)",
                    planner_model, len(thinking), data.get("done_reason"),
                )
                if salvage and salvage.lstrip().startswith("{"):
                    content = salvage
                else:
                    yield f"data: {json.dumps({'error': f'empty plan from {planner_model} — try switching your active model to qwen2.5:7b or qwen2.5:14b, or pick one from Settings.'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                yield f"data: {json.dumps({'error': f'invalid JSON from planner: {exc}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
            if not isinstance(scenes, list) or not scenes:
                yield f"data: {json.dumps({'error': 'planner returned no scenes'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Stream one scene at a time so the UI feels alive.
            for i, s in enumerate(scenes, start=1):
                if not isinstance(s, dict):
                    continue
                scene = {
                    "n":           int(s.get("n", i)),
                    "script":      str(s.get("script", "")).strip(),
                    "imagePrompt": str(s.get("imagePrompt") or s.get("image_prompt") or "").strip(),
                    "seconds":     int(s.get("seconds", max(3, req.duration // len(scenes)))),
                }
                yield f"data: {json.dumps({'scene': scene})}\n\n"
                await asyncio.sleep(0.05)  # tiny stagger for the reveal animation
    except Exception as exc:
        log.error("reels plan error: %s", exc, exc_info=True)
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


@app.post("/api/reels/plan")
async def reels_plan(req: ReelPlanRequest):
    return StreamingResponse(
        _stream_reels_plan(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ReelImageRequest(BaseModel):
    prompt:     str
    checkpoint: str
    width:      int   = 1024
    height:     int   = 1024
    steps:      int   = 20
    seed:       int   = -1
    cfg:        float = 6.5


@app.post("/api/reels/image")
async def reels_image(req: ReelImageRequest):
    """Generate one still via ComfyUI. Returns raw PNG bytes."""
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if not req.checkpoint:
        raise HTTPException(400, "checkpoint is required — see /api/reels/comfy/checkpoints")
    try:
        png = await _comfy.generate(
            req.prompt.strip(),
            checkpoint=req.checkpoint,
            width=req.width, height=req.height,
            steps=req.steps, seed=req.seed, cfg=req.cfg,
        )
    except Exception as exc:
        log.error("comfy generate failed: %s", exc, exc_info=True)
        raise HTTPException(502, f"comfy: {exc}")
    return Response(content=png, media_type="image/png")


@app.get("/api/reels/comfy/checkpoints")
async def reels_comfy_checkpoints():
    """List installed SD checkpoints so the UI can populate a dropdown."""
    return {"checkpoints": await _comfy.list_checkpoints()}


class ReelScene(BaseModel):
    n:           int
    script:      str
    imagePrompt: str
    seconds:     int


class ReelRenderRequest(BaseModel):
    plan:         dict  # topic, tone, aspect, voice, duration, scenes[]
    checkpoint:   str
    musicPath:    str | None = None
    musicVolume:  float = 0.18
    voiceover:    bool  = True
    captions:     bool  = True
    captionMode:  str   = "script"    # "script" | "transcript"
    translate:    bool  = False       # only when captionMode == "transcript"


async def _stream_reel_render(req: ReelRenderRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            await _reels.render_reel(
                plan=req.plan,
                checkpoint=req.checkpoint,
                on_progress=emit,
                music_path=(Path(req.musicPath) if req.musicPath else None),
                music_volume=req.musicVolume,
                voiceover=req.voiceover,
                captions=req.captions,
                caption_mode=req.captionMode,
                translate=req.translate,
            )
        except Exception as exc:
            log.error("reels render failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/reels/render")
async def reels_render(req: ReelRenderRequest):
    return StreamingResponse(
        _stream_reel_render(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_REELS_ASSET_KINDS   = {"music", "scene_image", "scene_video"}
_REELS_MUSIC_MIMES   = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/aac", "audio/mp4", "audio/ogg", "audio/flac"}
_REELS_IMAGE_MIMES   = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_REELS_VIDEO_MIMES   = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska",
                        "video/x-m4v", "video/mpeg"}
# Per-kind size caps. Videos get much more headroom than images/music.
_REELS_ASSET_MAX_MB  = {"music": 40, "scene_image": 40, "scene_video": 300}


def _reels_assets_dir() -> Path:
    from paths import data_dir as _dd
    p = _dd() / "reels" / "assets"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _probe_video(path: Path) -> dict:
    """Return {container, video_codec, audio_codec, has_audio} for the given file."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=format_name:stream=codec_name,codec_type",
            "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        data = json.loads(out.decode("utf-8", errors="replace") or "{}")
    except Exception as exc:
        log.warning("ffprobe failed for %s: %s", path, exc)
        return {"container": "", "video_codec": "", "audio_codec": "", "has_audio": False}
    container   = ((data.get("format") or {}).get("format_name") or "").lower()
    video_codec = ""
    audio_codec = ""
    has_audio   = False
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not video_codec:
            video_codec = (s.get("codec_name") or "").lower()
        elif s.get("codec_type") == "audio":
            has_audio = True
            if not audio_codec:
                audio_codec = (s.get("codec_name") or "").lower()
    return {"container": container, "video_codec": video_codec,
            "audio_codec": audio_codec, "has_audio": has_audio}


def _is_browser_playable_mp4(probe: dict, ext: str) -> bool:
    """Chrome/Electron's built-in decoder is happy with H.264 + AAC in an mp4.
    Anything else (ProRes .mov, HEVC in mp4 without hvc1 tag, VP9 in webm, etc.)
    we transcode to be safe."""
    if ext.lower() != ".mp4":
        return False
    if probe.get("video_codec") != "h264":
        return False
    if probe.get("has_audio") and probe.get("audio_codec") not in ("aac", ""):
        return False
    return True


async def _transcode_to_mp4(src: Path, dst: Path) -> None:
    """Re-encode `src` to a browser-safe H.264 / AAC / faststart mp4.

    Uses macOS videotoolbox when available (M-series does this in hardware and
    is 3-5× faster than libx264 for typical phone footage), else falls back to
    software x264. `movflags +faststart` moves the moov atom to the front so
    HTML5 <video> can begin playback before the whole file has loaded.
    """
    # veryfast software encoder is our portable default; if the user's ffmpeg
    # was built with videotoolbox (Homebrew macOS default) we prefer it.
    v_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
    try:
        # Cheap probe: does this ffmpeg know h264_videotoolbox?
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-encoders",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if b"h264_videotoolbox" in (out or b""):
            v_codec = ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
    except Exception:
        pass

    args = [
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        *v_codec,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", errors="replace").splitlines()[-15:]
        raise RuntimeError(f"transcode failed:\n" + "\n".join(tail))


def _safe_asset_name(kind: str, orig_name: str) -> str:
    """Return a random-token filename that preserves the original extension."""
    from secrets import token_hex
    ext = ""
    if "." in orig_name:
        ext = "." + orig_name.rsplit(".", 1)[-1].lower()
        # Whitelist reasonable extensions only.
        if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
                       ".png", ".jpg", ".jpeg", ".webp", ".gif",
                       ".mp4", ".mov", ".webm", ".mkv", ".m4v", ".mpeg"}:
            ext = ""
    return f"{kind}_{token_hex(6)}{ext}"


@app.post("/api/reels/assets/upload")
async def reels_assets_upload(
    kind: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a user-supplied music track or per-scene image override.

    Returns {name, path, url, bytes, mime, kind} for the frontend to reference
    in later render calls.
    """
    if kind not in _REELS_ASSET_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_REELS_ASSET_KINDS)}")

    mime = (file.content_type or "").lower()
    if kind == "music" and mime and mime not in _REELS_MUSIC_MIMES:
        raise HTTPException(415, f"music mime {mime} not supported")
    if kind == "scene_image" and mime and mime not in _REELS_IMAGE_MIMES:
        raise HTTPException(415, f"image mime {mime} not supported")
    if kind == "scene_video" and mime and mime not in _REELS_VIDEO_MIMES:
        raise HTTPException(415, f"video mime {mime} not supported")

    payload = await file.read()
    if not payload:
        raise HTTPException(400, "empty upload")
    cap_mb = _REELS_ASSET_MAX_MB.get(kind, 40)
    if len(payload) > cap_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {cap_mb} MB cap for {kind}")

    name = _safe_asset_name(kind, file.filename or "")
    dst  = _reels_assets_dir() / name
    dst.write_bytes(payload)
    log.info("reels asset uploaded: %s (%d bytes)", dst, len(payload))

    converted = False
    if kind == "scene_video":
        try:
            probe = await _probe_video(dst)
            ext   = dst.suffix.lower()
            if not _is_browser_playable_mp4(probe, ext):
                log.info("transcoding video %s (codec=%s ext=%s)",
                         dst.name, probe.get("video_codec"), ext)
                new_name = dst.with_suffix(".mp4").name.replace(dst.stem, dst.stem + "_h264")
                new_dst  = _reels_assets_dir() / new_name
                await _transcode_to_mp4(dst, new_dst)
                dst.unlink(missing_ok=True)   # drop the original — we only serve the mp4
                dst       = new_dst
                name      = new_name
                mime      = "video/mp4"
                converted = True
        except Exception as exc:
            log.warning("video conversion failed for %s: %s (serving original)", dst.name, exc)

    return {
        "name":       name,
        "path":       str(dst),
        "url":        f"/api/reels/assets/{name}",
        "bytes":      dst.stat().st_size,
        "mime":       mime,
        "kind":       kind,
        "converted":  converted,
    }


def _asset_kind_from_name(name: str) -> str:
    if name.startswith("music_"):        return "music"
    if name.startswith("scene_image_"):  return "scene_image"
    if name.startswith("scene_video_"):  return "scene_video"
    return "unknown"


@app.get("/api/reels/assets")
async def reels_assets_list(kind: str | None = None):
    """List every asset saved under data_dir()/reels/assets, newest first.

    Query params:
      kind : "music" | "scene_image" | "scene_video" — optional filter.
    """
    d = _reels_assets_dir()
    out: list[dict] = []
    if d.exists():
        for p in d.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            k = _asset_kind_from_name(p.name)
            if kind and k != kind:
                continue
            stat = p.stat()
            out.append({
                "name":  p.name,
                "kind":  k,
                "path":  str(p),
                "url":   f"/api/reels/assets/{p.name}",
                "bytes": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
    out.sort(key=lambda a: a["mtime"], reverse=True)
    return {"assets": out}


@app.delete("/api/reels/assets/{name}")
async def reels_assets_delete(name: str):
    """Delete a previously-uploaded asset from disk.

    Only removes the asset file itself — any conversation, plan, or render
    that referenced it by absolute path will get a "file not found" error
    at next render, which is the intended UX for cache-clearing.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "bad name")
    p = _reels_assets_dir() / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "asset not found")
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(500, f"delete failed: {exc}")
    log.info("reels asset deleted: %s", name)
    return {"ok": True, "name": name}


@app.get("/api/reels/assets/{name}")
async def reels_assets_get(name: str):
    """Serve a previously-uploaded asset back for preview in the UI."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "bad name")
    p = _reels_assets_dir() / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "asset not found")
    # Best-effort media type from extension.
    ext = p.suffix.lower()
    mt  = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac",  ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".png": "image/png",  ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
        ".mp4": "video/mp4",  ".mov": "video/quicktime",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".m4v": "video/x-m4v", ".mpeg": "video/mpeg",
    }.get(ext, "application/octet-stream")
    return Response(content=p.read_bytes(), media_type=mt,
                    headers={"Accept-Ranges": "bytes"})


@app.get("/api/reels/library")
async def reels_library():
    """List all rendered reels (metadata + URLs)."""
    return {"reels": _reels.list_finished_reels()}


@app.get("/api/reels/media/{name}")
async def reels_media(name: str):
    """Serve a rendered reel MP4 (or its sidecar metadata JSON)."""
    p = _reels.reel_media_path(name)
    if p is None:
        raise HTTPException(404, f"reel {name} not found")
    media_type = "video/mp4" if p.suffix == ".mp4" else "application/octet-stream"
    return Response(content=p.read_bytes(), media_type=media_type,
                    headers={"Accept-Ranges": "bytes"})


class ComfyStartRequest(BaseModel):
    path: str | None = None   # optional user override; else auto-discover


@app.post("/api/reels/comfy/start")
async def reels_comfy_start(req: ComfyStartRequest):
    """Auto-discover (or use the supplied `path`) and spawn ComfyUI detached.

    Never spawns twice — checks the port first. If we can't find an install,
    returns `{started: false, need_path: true, ...}` so the UI can ask.
    """
    # Trivial case: already running.
    if await _comfy.is_port_up():
        return {"started": True, "already_running": True}

    hint = None
    if req.path:
        hint = req.path.strip() or None
    else:
        stored = await _db.get_config("comfy_path")
        if stored:
            hint = stored

    install_dir = _comfy.find_install_dir(hint)
    if install_dir is None:
        return {
            "started":   False,
            "need_path": True,
            "checked":   [str(p) for p in _comfy._COMMON_INSTALL_DIRS],
            "message":   "ComfyUI not found in common locations. Provide the install path (the folder containing main.py).",
        }

    # Persist the working path so future launches skip discovery.
    await _db.set_config("comfy_path", str(install_dir))

    try:
        info = await _comfy.start(install_dir)
    except Exception as exc:
        log.error("comfy spawn failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"failed to spawn ComfyUI: {exc}")
    return info


@app.post("/api/reels/comfy/stop")
async def reels_comfy_stop():
    """Terminate the ComfyUI *we* spawned. No-op if the user started it manually."""
    return _comfy.stop_if_ours()


class ComfyInstallRequest(BaseModel):
    path:                 str  = "~/ComfyUI"
    download_checkpoint:  bool = True


async def _stream_comfy_install(req: ComfyInstallRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            install_dir = await _comfy.install(
                Path(req.path).expanduser(),
                emit,
                download_checkpoint=req.download_checkpoint,
            )
            # Persist the path so /start uses it without re-discovery.
            await _db.set_config("comfy_path", str(install_dir))
            # Kick off the actual ComfyUI process now that everything's on disk.
            await emit({"stage": "start", "message": "Launching ComfyUI…"})
            info = await _comfy.start(install_dir)
            await emit({"stage": "start", "progress": 1.0, "message": f"ComfyUI spawned (pid {info.get('pid', 0)})"})
            await emit({"stage": "ready", "install_path": str(install_dir)})
        except Exception as exc:
            log.error("comfy install failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/reels/comfy/install")
async def reels_comfy_install(req: ComfyInstallRequest):
    return StreamingResponse(
        _stream_comfy_install(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/reels/comfy/status")
async def reels_comfy_status():
    """Probe ComfyUI. Returns {running, version?, model?, error?}."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            # ComfyUI exposes /system_stats for a quick health/liveness check.
            r = await client.get(f"{COMFY_HOST}/system_stats")
            if r.status_code != 200:
                return {"running": False, "error": f"HTTP {r.status_code}"}
            stats   = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            version = ((stats.get("system") or {}).get("comfyui_version")
                       or (stats.get("system") or {}).get("version") or "")

            # Best-effort peek at the first available checkpoint. Not critical.
            model = ""
            try:
                oi = await client.get(f"{COMFY_HOST}/object_info/CheckpointLoaderSimple", timeout=2.0)
                if oi.status_code == 200:
                    info = oi.json()
                    ckpts = (info.get("CheckpointLoaderSimple", {})
                                  .get("input", {})
                                  .get("required", {})
                                  .get("ckpt_name", [[]])[0]) or []
                    if ckpts:
                        model = ckpts[0]
            except Exception:
                pass

            return {"running": True, "version": version, "model": model}
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        return {"running": False, "error": f"no process on {COMFY_HOST}"}
    except Exception as exc:
        return {"running": False, "error": str(exc)}


# ── /api/ableton — Ableton Live integration (Phase 1: detect + bridge + ping) ─
# The Music tab in the sidebar shows up when `abletonAvailable` is true in the
# app-shell status probe below. Composer endpoints land in Phase 2.
import ableton_detect    as _abl_detect
import ableton_bridge    as _abl_bridge
import ableton_client    as _abl_client
import ableton_composer  as _abl_composer
import song_translator   as _abl_song_translator
import song_spec         as _abl_song_spec
import ableton_session   as _abl_session
import ableton_library   as _abl_library
import edit_plan         as _abl_edit


@app.get("/api/ableton/status")
async def ableton_status():
    """Everything the Music tab needs to render its state in one round-trip.

    - `installed`      : is any Ableton Live app on disk?
    - `running`        : is any Ableton Live process currently up?
    - `bridgeInstalled`: is AbletonOSC in ~/Music/Ableton/User Library/Remote Scripts?
    - `connected`      : does the running Live actually answer an OSC ping?
    """
    installs = _abl_detect.find_installs()
    best     = _abl_detect.best_install()
    running  = _abl_detect.is_running()
    bridge   = _abl_bridge.is_installed()

    connected = False
    if running and bridge:
        try:
            client = await _abl_client.get()
            connected = await client.ping(timeout=1.0)
        except Exception as exc:
            log.debug("ableton ping error: %s", exc)

    return {
        "installed":       bool(installs),
        "installs":        installs,
        "best":            best,
        "running":         running,
        "bridgeInstalled": bridge,
        "bridgeDir":       str(_abl_bridge.install_dir()),
        "connected":       connected,
        "hostVersion":     best.get("version") if best else "",
        "hostEdition":     best.get("edition") if best else "",
        "isTrial":         bool(best and best.get("is_trial")),
    }


async def _stream_bridge_install() -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            await _abl_bridge.install(emit)
            await emit({"stage": "notes", "message": _abl_bridge.post_install_instructions()})
        except Exception as exc:
            log.error("ableton bridge install failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/install-bridge")
async def ableton_install_bridge():
    return StreamingResponse(
        _stream_bridge_install(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AbletonLaunchRequest(BaseModel):
    path: str | None = None   # optional override; else uses best_install()


@app.post("/api/ableton/launch")
async def ableton_launch(req: AbletonLaunchRequest):
    """Open the Ableton Live app (macOS)."""
    target = req.path
    if not target:
        best = _abl_detect.best_install()
        if best is None:
            raise HTTPException(404, "No Ableton Live install found")
        target = best["path"]
    if not Path(target).exists():
        raise HTTPException(404, f"App not found at {target}")
    if sys.platform != "darwin":
        raise HTTPException(501, "Auto-launch only implemented on macOS in Phase 1")
    try:
        proc = await asyncio.create_subprocess_exec(
            "open", str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise HTTPException(500, "open command failed")
    except Exception as exc:
        raise HTTPException(500, f"launch failed: {exc}")
    return {"launched": True, "path": target}


@app.post("/api/ableton/browser-probe")
async def ableton_browser_probe():
    """
    Diagnostic: run one full auto-load attempt end-to-end and report every
    step. Returns a structured object the UI can render as a checklist.

    Requires at least one track in the current Ableton session (target=0).
    """
    try:
        client = await _abl_client.get()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    steps: list[dict] = []

    # 1) can we even talk to the bridge?
    ok = await client.ping(timeout=1.5)
    steps.append({"name": "OSC ping", "ok": ok})
    if not ok:
        return {"ok": False, "steps": steps, "error": "bridge unreachable"}

    # 2) does the patched browser handler respond at all?
    instruments = await client.get_instruments(timeout=2.0)
    steps.append({
        "name":  "GET /live/browser/get/instruments",
        "ok":    bool(instruments),
        "count": len(instruments),
        "sample": [n for n, _ in instruments[:6]],
    })
    drums = await client.get_drums(timeout=2.0)
    steps.append({
        "name":  "GET /live/browser/get/drums",
        "ok":    True,
        "count": len(drums),
        "sample": [n for n, _ in drums[:6]],
    })
    if not instruments:
        return {"ok": False, "steps": steps,
                "error": "browser patch not responding — Ableton needs a full Cmd+Q + restart after installing/updating the patch"}

    # 3) try loading three sensible candidates by name onto track 0.
    # These are the actual Live 12 Intro built-ins — see server/song_translator
    # _ROLE_DEFAULTS for the ladder used at compose time.
    tests = [
        ("instruments", "Drift"),
        ("drums",       "505 Core Kit"),
        ("instruments", "Drum Sampler"),
    ]
    for cat, name in tests:
        ok, detail = await client.load_instrument_named(0, cat, name, timeout=3.0)
        steps.append({
            "name": f"load_named({cat}, {name!r})",
            "ok":   ok, "detail": detail,
        })

    # 4) first-in-category fallback.
    ok, detail = await client.load_first_in_category(0, "instruments", timeout=3.0)
    steps.append({"name": "load_first(instruments)", "ok": ok, "detail": detail})

    return {"ok": True, "steps": steps, "instruments_count": len(instruments), "drums_count": len(drums)}


@app.get("/api/ableton/browser-list")
async def ableton_browser_list():
    """
    Diagnostic: reveals what the (patched) AbletonOSC reports for the
    Instruments + Drums browser categories. Empty lists → the patch isn't
    loaded (Live wasn't restarted after install/patch), or Live's browser
    is genuinely empty for that category.
    """
    try:
        client = await _abl_client.get()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "instruments": [], "drums": []}
    instruments = await client.get_instruments(timeout=2.0)
    drums       = await client.get_drums(timeout=2.0)
    return {
        "ok":           True,
        "patched":      bool(instruments or drums),
        "instruments":  [{"name": n, "uri": u} for n, u in instruments],
        "drums":        [{"name": n, "uri": u} for n, u in drums],
    }


class AbletonFireSceneRequest(BaseModel):
    scene_index: int = 0


@app.post("/api/ableton/fire-scene")
async def ableton_fire_scene(req: AbletonFireSceneRequest):
    """Trigger a Session-view scene — fires every clip on that row."""
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    await client.fire_scene(req.scene_index)
    return {"fired": req.scene_index}


@app.post("/api/ableton/stop-all")
async def ableton_stop_all():
    """Stop transport + all currently-playing clips + un-solo everything."""
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    await client.stop_all_clips()
    await client.stop_all()
    # Clear any solos left over from per-track preview.
    await client.clear_all_solos()
    return {"stopped": True}


# ── Per-track control (track-first composer workflow) ─────────────────────
class AbletonFireClipRequest(BaseModel):
    track_index: int
    slot_index:  int = 0
    solo:        bool = True   # solo-preview by default; false = additive


@app.post("/api/ableton/fire-clip")
async def ableton_fire_clip(req: AbletonFireClipRequest):
    """
    Preview one track's clip. When solo=True (default) we set track.solo=1
    first so only that track is audible — matches the "click ▶ = hear only
    this track" UX. When solo=False (shift+click) we fire additively so it
    layers with anything already playing.

    Key subtlety: Live's default global clip-launch quantise is "1 Bar",
    which means firing a clip while transport is stopped can silently wait
    up to a full bar before playback starts. We set quantise to None (0)
    and also nudge transport with start_playing so ▶ is instant.
    """
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    # Immediate-launch: 0 = "None" in Live's clip trigger quantization enum.
    await client.set_clip_trigger_quantization(0)
    if req.solo:
        # Clear any lingering solos so we don't stack them, then solo this one.
        await client.clear_all_solos()
        await client.set_track_solo(req.track_index, True)
    await client.fire_clip_slot(req.track_index, req.slot_index)
    # Firing a clip while transport is stopped should auto-start it, but on
    # some Live versions this is unreliable. Belt-and-braces.
    await client.start_all()
    return {"fired": {"track": req.track_index, "slot": req.slot_index}, "solo": req.solo}


class AbletonStopTrackRequest(BaseModel):
    track_index: int
    slot_index:  int = 0


@app.post("/api/ableton/stop-track")
async def ableton_stop_track(req: AbletonStopTrackRequest):
    """
    Stop just this track's clip + un-solo it. Complement to fire-clip:
      * Solo-preview mode: stop this track → clip stops + solo cleared → silence.
      * Additive mode:    stop this track → other soloed/playing tracks continue.
    Deliberately does NOT touch global transport — other clips keep playing.
    """
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    await client.stop_clip(req.track_index, req.slot_index)
    await client.set_track_solo(req.track_index, False)
    return {"stopped": {"track": req.track_index, "slot": req.slot_index}}


class AbletonSetSoloRequest(BaseModel):
    track_index: int
    solo:        bool = False


@app.post("/api/ableton/set-solo")
async def ableton_set_solo(req: AbletonSetSoloRequest):
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    await client.set_track_solo(req.track_index, req.solo)
    return {"ok": True}


class AbletonDeleteTrackRequest(BaseModel):
    track_index: int


@app.post("/api/ableton/delete-track")
async def ableton_delete_track(req: AbletonDeleteTrackRequest):
    try:
        client = await _abl_client.get()
    except Exception as exc:
        raise HTTPException(500, str(exc))
    await client.delete_track(req.track_index)
    return {"deleted": req.track_index}


@app.post("/api/ableton/ping")
async def ableton_ping():
    """One-shot connectivity probe. Returns latency in ms if reachable."""
    try:
        client = await _abl_client.get()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    t0 = time.monotonic()
    ok = await client.ping(timeout=1.5)
    ms = int((time.monotonic() - t0) * 1000)
    return {"ok": ok, "latency_ms": ms}


# ── /api/ableton/compose — LLM-driven SongSpec generation ─────────────────────
@app.get("/api/ableton/genre-presets")
async def ableton_genre_presets():
    return {"presets": _abl_song_spec.GENRE_PRESETS}


class AbletonComposeRequest(BaseModel):
    topic: str = ""
    genre: str = "lo-fi hip-hop"
    model: str = ""     # optional per-request override; empty → composer picks
    deep:  bool = False # true → use the Deep Reasoning role config


async def _stream_compose(req: AbletonComposeRequest) -> AsyncIterator[str]:
    try:
        installed = await _installed_models()
    except Exception:
        installed = set()
    # Pull the user-configured composer choice from Settings so their pick
    # wins over the built-in ladder (unless the request itself explicitly
    # overrode via `model`, which stream_compose already honours first).
    role_key = "ableton_deep_model" if req.deep else "ableton_composer_model"
    configured = (await _db.get_config(role_key)) or ""
    try:
        async for evt in _abl_composer.stream_compose(
            OLLAMA_BASE, req.model, req.topic, req.genre, installed,
            configured_model=configured, deep=req.deep,
        ):
            yield f"data: {json.dumps(evt)}\n\n"
    except Exception as exc:
        log.error("ableton compose failed: %s", exc, exc_info=True)
        yield f"data: {json.dumps({'stage': 'error', 'error': str(exc)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/compose")
async def ableton_compose(req: AbletonComposeRequest):
    return StreamingResponse(
        _stream_compose(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AbletonApplySongRequest(BaseModel):
    spec:              dict
    wipe_first:        bool = True
    load_instruments:  bool = True


async def _stream_apply_song(req: AbletonApplySongRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            spec  = _abl_song_spec.parse_song_spec(req.spec)
            client = await _abl_client.get()
            if not await client.ping(timeout=1.0):
                await emit({"stage": "error",
                            "error": "AbletonOSC bridge unreachable — is Live running and the control surface enabled?"})
                return
            stats = await _abl_song_translator.apply(
                spec, client, on_progress=emit,
                wipe_first=req.wipe_first,
                load_instruments=req.load_instruments,
            )
            # Record this compose as the new session state (fresh undo history).
            await _abl_session.get().set_spec(spec)
            await emit({"stage": "complete", **stats})
        except Exception as exc:
            log.error("apply song failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/apply-song")
async def ableton_apply_song(req: AbletonApplySongRequest):
    return StreamingResponse(
        _stream_apply_song(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Per-track apply (track-first workflow) ───────────────────────────────────
class AbletonApplyTrackRequest(BaseModel):
    spec:              dict            # full current SongSpec (client is source of truth)
    track_id:          str             # id of the track to (re)apply
    live_track_index:  int | None = None  # None → append as new; int → refresh existing
    load_instrument:   bool = True


async def _stream_apply_track(req: AbletonApplyTrackRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            spec   = _abl_song_spec.parse_song_spec(req.spec)
            client = await _abl_client.get()
            if not await client.ping(timeout=1.0):
                await emit({"stage": "error",
                            "error": "AbletonOSC bridge unreachable — is Live running?"})
                return
            stats = await _abl_song_translator.apply_single_track(
                spec, req.track_id, client, on_progress=emit,
                live_track_index=req.live_track_index,
                load_instrument=req.load_instrument,
            )
            # Update session with the latest spec so edit chat stays in sync.
            await _abl_session.get().set_spec(spec)
            await emit({"stage": "complete", **stats})
        except Exception as exc:
            log.error("apply track failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/apply-track")
async def ableton_apply_track(req: AbletonApplyTrackRequest):
    return StreamingResponse(
        _stream_apply_track(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Add-track composer (LLM proposes ONE track for the current song) ─────────
class AbletonAddTrackRequest(BaseModel):
    role:        str  = "chord"      # drums | bass | chord | lead | pad | fx | vox
    description: str  = ""           # free-text intent, e.g. "warm rhodes, jazzy"
    model:       str  = ""           # optional override
    deep:        bool = False        # true → use the Deep Reasoning role config


async def _stream_add_track(req: AbletonAddTrackRequest) -> AsyncIterator[str]:
    session = _abl_session.get()
    spec    = session.current_spec
    if spec is None:
        yield f"data: {json.dumps({'stage': 'error', 'error': 'No song loaded — compose a brief first or add a first track.'})}\n\n"
        yield "data: [DONE]\n\n"
        return
    try:
        installed = await _installed_models()
    except Exception:
        installed = set()
    role_key = "ableton_deep_model" if req.deep else "ableton_composer_model"
    configured = (await _db.get_config(role_key)) or ""
    try:
        async for evt in _abl_composer.stream_add_track(
            OLLAMA_BASE, req.model, req.role, req.description, spec, installed,
            configured_model=configured, deep=req.deep,
        ):
            # When the LLM returns a completed track, splice it into the session
            # spec so the edit chat sees it and the frontend can apply it.
            if evt.get("stage") == "track" and evt.get("track"):
                await session.add_track(evt["track"])
                # Refresh event with the updated full spec so the client is authoritative.
                evt["spec"] = _abl_session.spec_to_dict(session.current_spec)  # type: ignore[arg-type]
            yield f"data: {json.dumps(evt)}\n\n"
    except Exception as exc:
        log.error("ableton add-track failed: %s", exc, exc_info=True)
        yield f"data: {json.dumps({'stage': 'error', 'error': str(exc)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/add-track")
async def ableton_add_track(req: AbletonAddTrackRequest):
    return StreamingResponse(
        _stream_add_track(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Session state ────────────────────────────────────────────────────────────
@app.get("/api/ableton/session")
async def ableton_session_state():
    return _abl_session.get().snapshot()


# ── Song library ─────────────────────────────────────────────────────────────
# Persistent per-user library of saved SongSpecs. Each entry is a JSON file
# under data_dir()/ableton/songs/. The frontend uses these endpoints for the
# New / Save / Browse-Library / Delete buttons in the music panel.

@app.get("/api/ableton/song/library")
async def ableton_song_library():
    """Metadata-only listing of saved songs, newest first."""
    return {"songs": _abl_library.list_songs()}


class AbletonSongSaveRequest(BaseModel):
    name: str = ""       # user-given name; empty → "Untitled"
    song_id: str = ""    # when set, updates an existing record instead of duplicating


@app.post("/api/ableton/song/save")
async def ableton_song_save(req: AbletonSongSaveRequest):
    """Save the current session's SongSpec under a user-given name."""
    spec = _abl_session.get().current_spec
    if spec is None:
        raise HTTPException(400, "no current song to save — compose one first")
    record = _abl_library.save_song(
        req.name, _abl_session.spec_to_dict(spec), song_id=req.song_id,
    )
    return {"ok": True, "song": {
        "id": record["id"], "name": record["name"],
        "created_at": record["created_at"], "updated_at": record["updated_at"],
    }}


@app.get("/api/ableton/song/{song_id}")
async def ableton_song_get(song_id: str):
    record = _abl_library.load_song(song_id)
    if record is None:
        raise HTTPException(404, f"song not found: {song_id}")
    return record


@app.post("/api/ableton/song/{song_id}/load")
async def ableton_song_load(song_id: str):
    """Load a saved song into the session. Does NOT materialise it — the user
    still needs to click Apply to push it to Ableton."""
    record = _abl_library.load_song(song_id)
    if record is None:
        raise HTTPException(404, f"song not found: {song_id}")
    try:
        spec = _abl_song_spec.parse_song_spec(record.get("spec") or {})
    except Exception as exc:
        raise HTTPException(400, f"stored song is invalid: {exc}")
    await _abl_session.get().set_spec(spec)
    return {"ok": True, "song_id": song_id, "spec": _abl_session.spec_to_dict(spec)}


@app.delete("/api/ableton/song/{song_id}")
async def ableton_song_delete(song_id: str):
    ok = _abl_library.delete_song(song_id)
    return {"ok": ok, "deleted": song_id}


class AbletonSongNewRequest(BaseModel):
    wipe_ableton: bool = False


@app.post("/api/ableton/song/new")
async def ableton_song_new(req: AbletonSongNewRequest):
    """
    Clear the current session so the composer is a blank slate. Optionally
    also wipes the live Ableton session (delete every track) — off by default
    because that's destructive; the frontend prompts before enabling it.
    """
    session = _abl_session.get()
    async with session._lock:  # noqa: SLF001 — direct lock for clean reset
        session.current_spec = None
        session.undo_stack.clear()
        session._persist()  # noqa: SLF001
    removed = 0
    if req.wipe_ableton:
        try:
            client = await _abl_client.get()
            if await client.ping(timeout=1.0):
                removed = await client.delete_all_tracks()
        except Exception as exc:
            log.warning("song/new: wipe_ableton failed: %s", exc)
    return {"ok": True, "wiped_ableton": req.wipe_ableton, "removed_tracks": removed}


@app.get("/api/ableton/patterns")
async def ableton_patterns():
    """The pattern-archetype vocabulary the composer uses per role."""
    from style_adapters import PATTERN_HELP
    return {"patterns": PATTERN_HELP}


class AbletonSetPatternRequest(BaseModel):
    track_index:  int
    section_id:   str
    pattern:      str   # empty string = clear


@app.post("/api/ableton/set-pattern")
async def ableton_set_pattern(req: AbletonSetPatternRequest):
    """
    Set the pattern archetype on one clip AND materialise the change in Live.

    Session's SongSpec is mutated in place; the clip is rebuilt via the
    style-adapters + note_patterns dispatch; the resulting notes replace the
    clip's contents in Ableton via a clip.replace_notes op (which also pushes
    a reverse-plan onto the undo stack so ⌘Z works).
    """
    session = _abl_session.get()
    spec    = session.current_spec
    if spec is None:
        raise HTTPException(400, "no current song")
    try:
        track = spec.tracks[req.track_index]
    except IndexError:
        raise HTTPException(404, "track_index out of range")

    # Find (or create) the clip on the target section.
    clip = None
    for c in track.clips:
        if c.section == req.section_id:
            clip = c
            break
    if clip is None:
        raise HTTPException(404, f"no clip on section {req.section_id!r}")

    # Update the pattern and regenerate notes via the adapters.
    clip.pattern = req.pattern.strip().lower()
    clip.notes   = []                    # clear so fill_missing_notes rebuilds

    from note_patterns import fill_missing_notes
    fill_missing_notes(spec)

    # Push the new notes into Live via a clip.replace_notes edit op so undo works.
    op = {
        "kind":         "clip.replace_notes",
        "track_index":  req.track_index,
        "section":      req.section_id,
        "notes":        [
            {"pitch": n.pitch, "start": n.start,
             "length": n.length, "velocity": n.velocity}
            for n in clip.notes
        ],
    }
    plan = {"reply": f"Pattern → {req.pattern}", "changes": [op]}

    try:
        client = await _abl_client.get()
        if not await client.ping(timeout=1.0):
            raise HTTPException(502, "AbletonOSC unreachable")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"bridge error: {exc}")

    async def sink(_evt: dict) -> None:
        return None

    applied, reverses = await _abl_edit.apply_plan(plan, spec, client, sink)
    await session.set_spec(spec)
    await session.push_reverses(reverses)
    return {"applied": len(applied), "notes": len(clip.notes),
            "undo_depth": len(session.undo_stack)}


# ── Iterative editing ────────────────────────────────────────────────────────
class AbletonEditRequest(BaseModel):
    message: str                            # the user's natural-language turn
    model:   str = ""                       # optional planner override
    # Track-first scoping — when the frontend has "active" tracks selected,
    # it sends their ids here so the LLM only touches those. Empty list =
    # unconstrained (legacy behaviour). Focus track goes first if present.
    active_track_ids: list[str] = []
    focus_track_id:   str = ""


_EDIT_SYSTEM = (
    "You edit an in-progress music sketch inside Ableton. Emit a strict JSON\n"
    "EditPlan — no prose, no markdown fences. Schema:\n"
    "{\n"
    '  "reply":  "<1-2 sentence chat reply to the user>",\n'
    '  "changes": [ {kind: str, ...op-specific fields}, ... ]\n'
    "}\n"
    "\n"
    "Supported op kinds and their fields:\n"
    '  {"kind": "set_tempo", "bpm": <number>}\n'
    '  {"kind": "set_key",   "root": "C", "mode": "major"|"minor"|"dorian"|…}\n'
    '  {"kind": "track.rename",  "track_index": <int>, "name": "<str>"}\n'
    '  {"kind": "track.set_mix", "track_index": <int>, "volume_db"?: <number>, "pan"?: <number>}\n'
    '  {"kind": "track.remove",  "track_index": <int>}\n'
    '  {"kind": "clip.replace_notes", "track_index": <int>, "section": "<section id>",\n'
    '     "notes": [{"pitch": <MIDI 0-127>, "start": <beats>, "length": <beats>, "velocity": <0-127>}, ...]}\n'
    '  {"kind": "clip.transpose", "track_index": <int>, "section": "<section id>", "semitones": <int>}\n'
    "\n"
    "Rules:\n"
    "  - Prefer the smallest possible EditPlan that satisfies the user.\n"
    "  - Use `clip.transpose` for pitch-shifts (fast + reversible).\n"
    "  - Use `clip.replace_notes` for rhythm / melody changes.\n"
    "  - Track indices and section ids MUST match the CURRENT SESSION below.\n"
    "  - MIDI conventions (kick=36, snare=38, closed hat=42, open hat=46,\n"
    "    middle C = 60, bass usually pitches 28-45, chord 48-72, lead 60-84).\n"
    "  - Notes' start/length in beats (1.0 = quarter note).\n"
    "  - Empty {'changes': []} is fine if the user's message doesn't imply\n"
    "    a change — put your response in `reply`.\n"
)


def _session_context(spec: SongSpec) -> str:
    """Compact serialisation of the current SongSpec for the edit LLM."""
    lines = [
        f"BPM: {spec.bpm}",
        f"Key: {spec.key.root} {spec.key.mode}",
        f"Time signature: {spec.timesig.num}/{spec.timesig.den}",
        f"Genre: {spec.genre or '(unset)'}",
        "",
        "Sections:",
    ]
    for i, sec in enumerate(spec.sections):
        lines.append(f"  s{i} id={sec.id!r} name={sec.name!r} bars {sec.start_bar}-{sec.start_bar+sec.length_bars}")
    lines.append("")
    lines.append("Tracks:")
    for i, t in enumerate(spec.tracks):
        note_total = sum(len(c.notes) for c in t.clips)
        clip_sections = ",".join(c.section for c in t.clips)
        lines.append(
            f"  {i}: {t.name!r} role={t.role} "
            f"vol={t.mix.volume_db:.1f}dB pan={t.mix.pan:+.2f} "
            f"clips=[{clip_sections}] notes={note_total}"
        )
    return "\n".join(lines)


async def _stream_edit(req: AbletonEditRequest) -> AsyncIterator[str]:
    session = _abl_session.get()
    spec    = session.current_spec
    if spec is None:
        yield f"data: {json.dumps({'stage': 'error', 'error': 'No song loaded. Compose one first.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    installed = await _installed_models()
    # Edits honour the same "ableton composer" role config as compose — one
    # configured model runs the whole workflow. If the user has chosen a
    # specific one in Settings we prefer it; otherwise the editor ladder.
    configured_editor = (await _db.get_config("ableton_composer_model")) or ""
    planner   = req.model if (req.model and (req.model in installed or any(x.startswith(req.model + ':') for x in installed))) \
                          else await _abl_composer.pick_editor_model(installed, configured=configured_editor)

    yield f"data: {json.dumps({'stage': 'planning', 'model': planner})}\n\n"

    # Build scope directive if the frontend gave us active_track_ids.
    scope_note = ""
    if req.active_track_ids:
        # Map ids → indices (LLM works with track_index).
        id_to_idx = {t.id: i for i, t in enumerate(spec.tracks)}
        active_idx = [id_to_idx[tid] for tid in req.active_track_ids if tid in id_to_idx]
        focus_idx  = id_to_idx.get(req.focus_track_id) if req.focus_track_id else None
        if active_idx:
            active_list = ", ".join(str(i) for i in active_idx)
            focus_line  = (f"Focus track (primary target): track_index={focus_idx}. "
                           if focus_idx is not None else "")
            scope_note  = (
                "\n\nSCOPE — the user is currently editing a subset of tracks. "
                f"Only modify these track indices: [{active_list}]. "
                f"{focus_line}"
                "If the request is unambiguously about a track NOT in this list, "
                "gently say so in `reply` and return an empty changes[] array."
            )
    system   = _EDIT_SYSTEM + scope_note + "\n\nCURRENT SESSION:\n" + _session_context(spec)
    user_msg = req.message.strip() or "no message"

    # If we picked a thinking-family model, give it a wider num_predict + ctx
    # (thinking phase eats tokens even when we ask for it off) and DON'T force
    # think:false — MoE reasoners with think:false often emit empty content.
    # Instead, we lean on the `content OR thinking` salvage below.
    is_thinking = any(planner.startswith(p) for p in (
        "qwen3.6", "Hydroxide538/qwen-agentworld", "nemotron-3-nano",
        "qwen3", "deepseek-r1", "gpt-oss", "gemma4",
    ))
    # DeepSeek-R1 (dense 70B distill) has famously deep chains-of-thought —
    # 8k is enough for MoE thinkers but R1 routinely burns 8-12k tokens on
    # <think> before emitting the tiny EditPlan JSON. Give it room to breathe.
    is_deep_thinker = planner.startswith("deepseek-r1")

    payload = {
        "model":    planner,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        "format":     "json",
        "stream":     False,
        "think":      is_thinking,   # let thinkers think; force off for dense.
        "options": {
            "num_ctx":     16384 if is_thinking else 8192,
            "num_predict": (16384 if is_deep_thinker
                            else 8192 if is_thinking
                            else 4096),
            "temperature": 0.55,
        },
        # Long keep-alive for deep thinkers — paying 30-60s of cold-load
        # once and then holding the 43GB weights for 30min beats paying
        # that cost on every edit turn.
        "keep_alive": "30m" if is_deep_thinker else "5m",
    }

    # Deep thinkers (deepseek-r1:70b in particular) can spend several minutes
    # in <think> before emitting the tiny EditPlan JSON. Give thinkers a
    # generous timeout so httpx doesn't abort the read before Ollama is done.
    edit_timeout = 1800.0 if is_deep_thinker else 600.0 if is_thinking else 180.0
    try:
        async with httpx.AsyncClient(timeout=edit_timeout) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                yield f"data: {json.dumps({'stage': 'error', 'error': f'HTTP {r.status_code}: {r.text[:200]}'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            data    = r.json()
            msg     = data.get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                # Some thinking-only outputs bury the JSON at the end of
                # the thinking stream — try to salvage the last {...} block.
                thinking = (msg.get("thinking") or "").strip()
                if thinking:
                    last_open  = thinking.rfind("{")
                    last_close = thinking.rfind("}")
                    if 0 <= last_open < last_close:
                        content = thinking[last_open:last_close + 1]
                    elif thinking.lstrip().startswith("{"):
                        content = thinking
    except Exception as exc:
        log.error("edit call failed: %s", exc, exc_info=True)
        yield f"data: {json.dumps({'stage': 'error', 'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"
        return

    try:
        plan = json.loads(content)
    except Exception as exc:
        yield f"data: {json.dumps({'stage': 'error', 'error': f'invalid JSON from planner: {exc}'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    reply   = str(plan.get("reply", "")).strip()
    changes = plan.get("changes") or []
    yield f"data: {json.dumps({'stage': 'plan', 'reply': reply, 'changes': changes, 'summaries': [_abl_edit.op_summary(op) for op in changes]})}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/api/ableton/edit")
async def ableton_edit(req: AbletonEditRequest):
    return StreamingResponse(
        _stream_edit(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AbletonApplyEditRequest(BaseModel):
    plan: dict   # {reply?, changes: [...]}


async def _stream_apply_edit(req: AbletonApplyEditRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict):
        await queue.put(evt)

    async def run():
        session = _abl_session.get()
        spec    = session.current_spec
        if spec is None:
            await emit({"stage": "error", "error": "No current song."})
            return
        try:
            client = await _abl_client.get()
            if not await client.ping(timeout=1.0):
                await emit({"stage": "error", "error": "AbletonOSC bridge unreachable"})
                return
            applied, reverses = await _abl_edit.apply_plan(req.plan, spec, client, emit)
            # Persist updated spec + push reverse-plan onto undo stack.
            await session.set_spec(spec)   # NOTE clears undo — do BEFORE push
            await session.push_reverses(reverses)
            await emit({"stage": "complete", "applied": applied, "undo_depth": len(session.undo_stack)})
        except Exception as exc:
            log.error("apply-edit failed: %s", exc, exc_info=True)
            await emit({"stage": "error", "error": str(exc)})
        finally:
            await emit({"__end": True})

    task = asyncio.create_task(run())
    try:
        while True:
            evt = await queue.get()
            if evt.get("__end"):
                break
            yield f"data: {json.dumps(evt)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        yield "data: [DONE]\n\n"


@app.post("/api/ableton/apply-edit")
async def ableton_apply_edit(req: AbletonApplyEditRequest):
    return StreamingResponse(
        _stream_apply_edit(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ableton/undo")
async def ableton_undo():
    """Pop the last reverse-plan and apply it. Returns the reverses applied."""
    session = _abl_session.get()
    spec    = session.current_spec
    if spec is None:
        raise HTTPException(400, "no current song")
    reverses = await session.pop_reverses()
    if reverses is None:
        return {"applied": 0, "message": "nothing to undo"}
    try:
        client = await _abl_client.get()
        if not await client.ping(timeout=1.0):
            raise HTTPException(502, "AbletonOSC unreachable")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"bridge error: {exc}")

    async def sink(_evt: dict) -> None:
        return None

    n_ok = 0
    for rev in reverses:
        try:
            await _abl_edit.apply_op(rev, spec, client, sink)
            n_ok += 1
        except Exception as exc:
            log.warning("undo op failed: %s", exc)
    # Persist without clearing undo (set_spec would).
    async with session._lock:
        session._persist()
    return {"applied": n_ok, "remaining": len(session.undo_stack)}


# ── /api/memory — SQLite-backed conversation persistence ──────────────────────
@app.get("/api/memory/conversations")
async def list_conversations():
    return await _db.list_conversations()


@app.get("/api/memory/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    data = await _db.get_conversation(conv_id)
    if data is None:
        raise HTTPException(404, "Conversation not found")
    return data


@app.post("/api/memory/conversations")
async def save_conversation(request: Request):
    data = await request.json()
    await _db.upsert_conversation(data)
    return {"ok": True}


@app.post("/api/memory/conversations/{conv_id}/messages")
async def save_message(conv_id: str, request: Request):
    msg = await request.json()
    await _db.upsert_message(conv_id, msg)
    return {"ok": True}


@app.delete("/api/memory/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    await _db.delete_conversation(conv_id)
    return {"ok": True}


@app.delete("/api/memory/messages/{msg_id}")
async def delete_message(msg_id: str):
    await _db.delete_message(msg_id)
    return {"ok": True}


# ── /api/memory/facts — persistent user-facts memory ─────────────────────────
@app.get("/api/memory/facts")
async def memory_facts():
    return {"facts": await _db.list_user_facts(limit=500)}


class FactCreateRequest(BaseModel):
    fact: str
    category: str = "general"
    confidence: float = 1.0


@app.post("/api/memory/facts")
async def add_fact(req: FactCreateRequest):
    new_id = await _db.add_user_fact(
        fact=req.fact, category=req.category, confidence=req.confidence,
    )
    invalidate_context_cache()
    return {"ok": True, "id": new_id, "duplicate": new_id is None}


@app.delete("/api/memory/facts/{fact_id}")
async def delete_fact(fact_id: int):
    ok = await _db.delete_user_fact(fact_id)
    invalidate_context_cache()
    return {"ok": ok}


@app.delete("/api/memory/facts")
async def clear_facts():
    n = await _db.clear_user_facts()
    invalidate_context_cache()
    return {"ok": True, "removed": n}


@app.post("/api/memory/facts/purge_invalid")
async def purge_invalid_facts():
    """One-shot cleanup: delete any stored fact that fails the current validator.
    Useful after the extractor leaked junk in earlier builds."""
    all_facts = await _db.list_user_facts(limit=1000)
    removed: list[str] = []
    for f in all_facts:
        if not _is_valid_fact(f["fact"]):
            await _db.delete_user_fact(f["id"])
            removed.append(f["fact"])
    invalidate_context_cache()
    return {"ok": True, "removed_count": len(removed), "samples": removed[:10]}


# ── /api/setup — wizard endpoints ─────────────────────────────────────────────
@app.get("/api/setup/hardware")
async def setup_hardware():
    return _hw.get_hardware()


@app.get("/api/setup/ffmpeg")
async def setup_ffmpeg():
    """Check whether ffmpeg is on PATH — required by the Reels render pipeline."""
    import shutil, sys
    path = shutil.which("ffmpeg")
    if not path:
        install = {
            "darwin": "brew install ffmpeg",
            "linux":  "sudo apt install ffmpeg   # or dnf / pacman",
            "win32":  'winget install "Gyan.FFmpeg"',
        }.get(sys.platform, "https://ffmpeg.org/download.html")
        return {
            "installed": False,
            "path":      "",
            "version":   "",
            "install_cmd": install,
        }
    version = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "-version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        first  = (out or b"").decode("utf-8", errors="replace").splitlines()
        if first:
            # e.g. "ffmpeg version 8.1 Copyright..."
            parts = first[0].split()
            if len(parts) >= 3 and parts[0] == "ffmpeg":
                version = parts[2]
    except Exception:
        pass
    return {"installed": True, "path": path, "version": version, "install_cmd": ""}


@app.get("/api/setup/recommendations")
async def setup_recommendations():
    hw = _hw.get_hardware()
    # Get installed model IDs from Ollama
    installed: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            for m in r.json().get("models", []):
                installed.add(m["name"])
    except Exception:
        pass
    recs = _catalog.get_recommendations(hw["tier"], installed)
    return {"tier": hw["tier"], "hardware": hw, "recommendations": recs}


@app.get("/api/setup/hardware-profile")
async def setup_hardware_profile():
    """
    Extended hardware fingerprint — chip family + variant + estimated
    memory bandwidth + tier. Used by the wizard's optimized-models flow
    to filter models to those that clear the tok/s target.
    """
    hw = _hw.get_hardware()
    return {
        "os":                 hw["os"],
        "arch":               hw["arch"],
        "cpu":                hw["cpu"],
        "chip_family":        hw["chip_family"],
        "chip_variant":       hw["chip_variant"],
        "ram_gb":             hw["ram_gb"],
        "cores":              hw["cores"],
        "perf_cores":         hw["perf_cores"],
        "gpu":                hw["gpu"],
        "gpu_vram_gb":        hw["gpu_vram_gb"],
        "mem_bandwidth_gb_s": hw["mem_bandwidth_gb_s"],
        "tier":               hw["tier"],
    }


@app.get("/api/setup/optimized-models")
async def setup_optimized_models(min_tok_per_s: float = 20.0):
    """
    Per-model tok/s estimates + fit rating for the current hardware.
    Includes every family the user asked for (DeepSeek, Gemma, Qwen,
    MoE thinkers, vision, embeddings). The frontend uses fit=='top'/'good'
    as the primary picks and 'acceptable' as the fallback tier.

    Query params:
      min_tok_per_s — the throughput target (default 20.0). Models
                      returning ≥ this value get fit='good' or 'top'.
    """
    from benchmarks import estimate_tok_per_s, fit_rating
    hw = _hw.get_hardware()
    installed: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            for m in r.json().get("models", []):
                installed.add(m["name"])
    except Exception:
        pass

    # Build per-category lists. Each item carries the tok/s + fit so the
    # UI can colour-code and sort by strength.
    by_category: dict[str, list[dict]] = {}
    for m in _catalog.MODELS:
        est = estimate_tok_per_s(m, hw)
        fit = fit_rating(est, min_target=min_tok_per_s)
        base = m["id"].split(":")[0]
        is_installed = m["id"] in installed or any(
            x.startswith(base + ":") or x == base for x in installed
        )
        entry = {
            **m,
            "installed":     is_installed,
            "tok_per_s_est": round(est, 1),
            "fit":           fit,
            "meets_target":  fit in ("top", "good"),
        }
        by_category.setdefault(m["category"], []).append(entry)

    # Sort each category: installed first, then by tok/s descending.
    for cat, items in by_category.items():
        items.sort(key=lambda e: (
            not e["installed"], -(e["tok_per_s_est"] or 0),
        ))

    return {
        "profile":         (await setup_hardware_profile()),
        "min_tok_per_s":   min_tok_per_s,
        "categories":      by_category,
    }


@app.get("/api/setup/status")
async def setup_status():
    completed = await _db.get_config("wizard_completed")
    account   = await _db.get_config("account_name")
    return {
        "wizard_completed": completed == "1",
        "account_name":     account or "",
    }


class WizardCompleteRequest(BaseModel):
    account_name:        str
    account_color:       str = "#8b2252"
    active_model:        str = ""
    vision_model:        str = ""
    code_model:          str = ""
    embed_model:         str = "mxbai-embed-large"
    ocr_model:           str = ""
    docs_model:          str = ""
    handwriting_model:   str = ""
    tables_model:        str = ""
    judge_model:         str = ""
    tts_voice:           str = "tara"
    tts_speed:           float = 1.0
    theme:               str = "underworld"
    mcp_servers:         list[str] = []


@app.post("/api/setup/complete")
async def setup_complete(req: WizardCompleteRequest):
    pairs = [
        ("wizard_completed",  "1"),
        ("account_name",      req.account_name),
        ("account_color",     req.account_color),
        ("active_model",      req.active_model),
        ("vision_model",      req.vision_model),
        ("code_model",        req.code_model),
        ("embed_model",       req.embed_model),
        ("ocr_model",         req.ocr_model),
        ("docs_model",        req.docs_model),
        ("handwriting_model", req.handwriting_model),
        ("tables_model",      req.tables_model),
        # judge_model drives auto-router classification + background fact
        # extraction. Stored under both keys so both subsystems pick it up.
        ("judge_model",       req.judge_model),
        ("memory_model",      req.judge_model),
        ("tts_voice",         req.tts_voice),
        ("tts_speed",         str(req.tts_speed)),
        ("theme",             req.theme),
        ("mcp_servers",       ",".join(req.mcp_servers)),
    ]
    for k, v in pairs:
        await _db.set_config(k, v)
    return {"ok": True}


@app.post("/api/setup/reset")
async def setup_reset():
    await _db.set_config("wizard_completed", "0")
    return {"ok": True}


# ── TTS install verification ────────────────────────────────────────────────
# The wizard calls this at the end of setup to ensure Kokoro (TTS) is fully
# ready — Python package importable AND ONNX model files downloaded. Without
# this, the first speech attempt eats a 100-500MB download that surprises
# the user long after they finished setup.

@app.get("/api/setup/tts-status")
async def setup_tts_status():
    """
    Returns:
      { package_installed, model_downloaded, voices_downloaded, ready,
        model_size_mb, missing: [str], hint: str }
    """
    from pathlib import Path
    result = {
        "package_installed":  False,
        "model_downloaded":   False,
        "voices_downloaded":  False,
        "ready":              False,
        "model_size_mb":      0,
        "voices_size_mb":     0,
        "missing":            [],
        "hint":               "",
    }
    try:
        import kokoro_onnx  # noqa: F401
        result["package_installed"] = True
    except ImportError as exc:
        result["missing"].append("kokoro_onnx python package")
        result["hint"] = f"pip install kokoro-onnx failed on boot: {exc}"

    # Model file locations (see tts_engine._kokoro_dir).
    try:
        kokoro_dir = _tts._kokoro_dir()   # noqa: SLF001 — internal path helper
        model_path  = _tts._model_path()  # noqa: SLF001
        voices_path = _tts._voices_path() # noqa: SLF001
        if model_path.exists():
            result["model_downloaded"] = model_path.stat().st_size > 1_000_000
            result["model_size_mb"]    = model_path.stat().st_size // (1024 * 1024)
        if voices_path.exists():
            result["voices_downloaded"] = voices_path.stat().st_size > 100_000
            result["voices_size_mb"]    = voices_path.stat().st_size // (1024 * 1024)
        if not result["model_downloaded"]:  result["missing"].append("kokoro-v1.0.onnx")
        if not result["voices_downloaded"]: result["missing"].append("voices-v1.0.bin")
    except Exception as exc:
        result["missing"].append(f"path resolution failed: {exc}")

    result["ready"] = (
        result["package_installed"]
        and result["model_downloaded"]
        and result["voices_downloaded"]
    )
    return result


@app.post("/api/setup/tts-install")
async def setup_tts_install():
    """
    Force-download the Kokoro model + voices if missing. Returns the same
    shape as GET /api/setup/tts-status. Blocking — expected ~30-60s on
    first call, ~5s if already downloaded.
    """
    try:
        # Runs in a thread pool because urlretrieve is blocking; returns
        # once files are on disk.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _tts._download_if_missing)  # noqa: SLF001
    except Exception as exc:
        return {"ok": False, "error": f"Kokoro download failed: {exc}",
                **(await setup_tts_status())}
    # Also warm up the ONNX session so the first user speech is instant.
    try:
        await loop.run_in_executor(None, _tts.preload_pipeline)
    except Exception as exc:
        log.warning("Kokoro preload after install failed (non-fatal): %s", exc)
    return {"ok": True, **(await setup_tts_status())}


# ── /api/models/roles — post-setup model reassignment ────────────────────────
# The wizard's per-function model choices (main chat, auto-router judge,
# vision, code, OCR, …) all live in `app_config` as plain key/value pairs.
# These two endpoints let the UI read and update them after onboarding, so
# users can pick newly-pulled Ollama models without re-running the wizard.
_MODEL_ROLE_KEYS = [
    "active_model", "judge_model", "vision_model", "code_model",
    "ocr_model", "docs_model", "handwriting_model", "tables_model",
    # Ableton composer roles: standard + deep-reasoning slots. Empty string
    # means "fall back to _PLANNER_PREF / _DEEP_PLANNER_PREF in the composer".
    "ableton_composer_model", "ableton_deep_model",
]


@app.get("/api/models/roles")
async def get_model_roles():
    return {k: (await _db.get_config(k)) or "" for k in _MODEL_ROLE_KEYS}


class ModelRolesUpdate(BaseModel):
    active_model:           str | None = None
    judge_model:            str | None = None
    vision_model:           str | None = None
    code_model:             str | None = None
    ocr_model:              str | None = None
    docs_model:             str | None = None
    handwriting_model:      str | None = None
    tables_model:           str | None = None
    ableton_composer_model: str | None = None
    ableton_deep_model:     str | None = None


@app.post("/api/models/roles")
async def update_model_roles(req: ModelRolesUpdate):
    updates = req.model_dump(exclude_unset=True)
    for k, v in updates.items():
        await _db.set_config(k, v or "")
    # judge_model also drives background fact-extraction; keep both in sync
    # (mirrors the wizard's own setup_complete behaviour).
    if "judge_model" in updates:
        await _db.set_config("memory_model", updates["judge_model"] or "")
    return {"ok": True, "updated": list(updates.keys())}


# ── /api/models/pull — stream Ollama model download ──────────────────────────
@app.post("/api/models/pull")
async def pull_model(request: Request):
    body = await request.json()
    model_name = body.get("name", "")
    if not model_name:
        raise HTTPException(400, "name required")

    async def stream_pull() -> AsyncIterator[str]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=10.0)
        ) as client:
            async with client.stream(
                "POST", f"{OLLAMA_BASE}/api/pull",
                json={"name": model_name, "stream": True},
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"data: {line}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_pull(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/models/{model_name:path}")
async def delete_model(model_name: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.request(
            "DELETE", f"{OLLAMA_BASE}/api/delete",
            json={"name": model_name},
        )
    return {"ok": r.status_code in (200, 204)}


# ── /api/setup/ollama — cross-platform install & lifecycle ───────────────────
@app.get("/api/setup/ollama")
async def ollama_status():
    import platform as _pf
    installed = _ollama.is_installed()
    running   = await _ollama.is_running() if installed else False
    version   = await _ollama.get_version() if running else ""
    return {
        "installed":    installed,
        "running":      running,
        "version":      version,
        "executable":   _ollama.find_executable(),
        "install_info": _ollama.get_install_info(),
        "os":           _pf.system(),
    }


@app.post("/api/setup/ollama/install")
async def ollama_install():
    """SSE stream of install-script lines for the UI."""
    async def gen() -> AsyncIterator[str]:
        async for line in _ollama.run_install_script():
            yield f"data: {json.dumps({'line': line})}\n\n"
        # Poll briefly after install completes
        for _ in range(20):
            if await _ollama.is_running():
                yield f"data: {json.dumps({'running': True})}\n\n"
                break
            await asyncio.sleep(0.5)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/setup/ollama/start")
async def ollama_start():
    result = _ollama.try_start_ollama()
    started = False
    if result.get("ok"):
        started = await _ollama.wait_until_running(timeout_s=15.0)
    return {**result, "running": started}


# ── /api/mcp — curated MCP server catalog ─────────────────────────────────────
@app.get("/api/mcp/catalog")
async def mcp_catalog():
    return {"servers": _mcp.list_servers(), "categories": _mcp.CATEGORIES}


@app.get("/api/mcp/enabled")
async def mcp_enabled():
    """Return the MCP servers the user has enabled (from wizard + settings)."""
    raw = await _db.get_config("mcp_servers") or ""
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    catalog = {s["id"]: s for s in _mcp.list_servers()}
    enabled = [catalog[i] for i in ids if i in catalog]
    return {"servers": enabled, "ids": ids}


class McpEnabledRequest(BaseModel):
    ids: list[str]


@app.post("/api/mcp/enabled")
async def mcp_set_enabled(req: McpEnabledRequest):
    await _db.set_config("mcp_servers", ",".join(req.ids))
    statuses = await _mcp_mgr.manager.sync_with_config()
    invalidate_context_cache()
    return {"ok": True, "count": len(req.ids), "statuses": statuses}


@app.get("/api/mcp/status")
async def mcp_status():
    """Runtime status of all currently-spawned MCP servers."""
    return {"clients": _mcp_mgr.manager.status()}


@app.get("/api/mcp/tools")
async def mcp_tools():
    """List of tools currently available across all running MCP servers."""
    return {"tools": _mcp_mgr.manager.list_tools_summary()}


class McpToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


@app.post("/api/mcp/tools/call")
async def mcp_tool_call(req: McpToolCallRequest):
    """Manual tool invocation — useful for testing without going through chat."""
    try:
        text = await _mcp_mgr.manager.call(req.name, req.arguments)
    except Exception as exc:
        raise HTTPException(422, str(exc))
    return {"result": text}


@app.post("/api/mcp/restart")
async def mcp_restart():
    statuses = await _mcp_mgr.manager.sync_with_config()
    return {"ok": True, "statuses": statuses}


# ── /api/research — deep research + persistent knowledge base ────────────────
class ResearchStartRequest(BaseModel):
    query: str


@app.post("/api/research/start")
async def research_start(req: ResearchStartRequest):
    """Streams progress events as SSE while a research run executes."""
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query required")

    async def gen() -> AsyncIterator[str]:
        try:
            async for event in _research.run_research(query):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'phase': 'failed', 'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/research/runs")
async def research_runs():
    return {"runs": await _rdb.list_runs(limit=200)}


@app.get("/api/research/runs/{run_id}")
async def research_run(run_id: str):
    run = await _rdb.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.delete("/api/research/runs/{run_id}")
async def research_run_delete(run_id: str):
    ok = await _rdb.delete_run(run_id)
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True}


@app.get("/api/research/search")
async def research_kb_search(q: str, k: int = 12):
    """Semantic search across every chunk in the KB."""
    q = (q or "").strip()
    if not q:
        return {"results": []}
    vec = await _emb.embed_one(q)
    if not vec:
        raise HTTPException(503, "embedding model unavailable — is mxbai-embed-large installed?")
    results = await _rdb.search_chunks(vec, k=max(1, min(k, 50)))
    return {"results": results, "query": q}


@app.get("/api/research/stats")
async def research_stats():
    return await _rdb.kb_stats()


# ── /api/idp — Intelligent Document Processing ───────────────────────────────
async def _resolve_doc_model(category: str = "docs") -> str:
    """Return the user-configured model id for the given IDP category, with fallbacks."""
    preferred_keys = {
        "ocr":         ["ocr_model", "vision_model", "docs_model"],
        "docs":        ["docs_model", "vision_model", "ocr_model"],
        "handwriting": ["handwriting_model", "vision_model", "ocr_model"],
        "tables":      ["tables_model", "code_model", "active_model"],
        "text":        ["active_model"],
    }
    keys = preferred_keys.get(category, ["active_model"])
    for k in keys:
        v = await _db.get_config(k)
        if v:
            return v
    return ""


@app.get("/api/idp/documents")
async def idp_list():
    return {"documents": _idp.list_documents()}


@app.get("/api/idp/documents/{doc_id}")
async def idp_get(doc_id: str):
    d = _idp.get_document(doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return d.to_dict(include_text=True)


@app.post("/api/idp/upload")
async def idp_upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 100MB)")
    doc = await _idp.ingest_file(file.filename or "unnamed", data)
    return doc.to_dict()


@app.delete("/api/idp/documents/{doc_id}")
async def idp_delete(doc_id: str):
    ok = _idp.delete_document(doc_id)
    if not ok:
        raise HTTPException(404, "Document not found")
    return {"ok": True}


@app.get("/api/idp/documents/{doc_id}/page/{page_num}")
async def idp_page_image(doc_id: str, page_num: int):
    """Serve a rendered page image (for PDF viewer)."""
    from fastapi.responses import FileResponse
    doc = _idp.get_document(doc_id)
    if not doc or page_num < 1 or page_num > len(doc.page_images):
        raise HTTPException(404, "Page not found")
    return FileResponse(doc.page_images[page_num - 1], media_type="image/png")


class IDPRequest(BaseModel):
    doc_id: str
    options: dict = {}


def _idp_guard(doc_id: str) -> "_idp.Document":
    """Resolve a doc, 404 if missing."""
    doc = _idp.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


async def _run_idp(coro):
    """Run an IDP coroutine, converting RuntimeError into a 422 with the message."""
    try:
        return await coro
    except RuntimeError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/idp/ocr")
async def idp_ocr(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("ocr")
    text  = await _run_idp(_idp.run_ocr(doc, model))
    return {"text": text, "model": model}


@app.post("/api/idp/summarize")
async def idp_summarize(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("text")
    style = req.options.get("style", "brief")
    return {"text": await _run_idp(_idp.summarize(doc, model, style)), "model": model}


@app.post("/api/idp/qa")
async def idp_qa(req: IDPRequest):
    doc = _idp_guard(req.doc_id)
    q   = req.options.get("question", "")
    if not q:
        raise HTTPException(400, "question required")
    model = await _resolve_doc_model("docs")
    return {"text": await _run_idp(_idp.qa(doc, model, q)), "model": model}


@app.post("/api/idp/tables")
async def idp_tables(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("tables")
    return {"tables": await _run_idp(_idp.extract_tables(doc, model)), "model": model}


@app.post("/api/idp/entities")
async def idp_entities(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("text")
    return {"entities": await _run_idp(_idp.extract_entities(doc, model)), "model": model}


@app.post("/api/idp/classify")
async def idp_classify(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("text")
    return {"classification": await _run_idp(_idp.classify(doc, model)), "model": model}


@app.post("/api/idp/translate")
async def idp_translate(req: IDPRequest):
    doc    = _idp_guard(req.doc_id)
    model  = await _resolve_doc_model("text")
    target = req.options.get("target", "French")
    return {"text": await _run_idp(_idp.translate(doc, model, target)), "model": model}


@app.post("/api/idp/redact")
async def idp_redact(req: IDPRequest):
    doc   = _idp_guard(req.doc_id)
    model = await _resolve_doc_model("text")
    cats  = req.options.get("categories", [])
    return {"text": await _run_idp(_idp.redact(doc, model, cats)), "model": model}


@app.post("/api/idp/export/{fmt}")
async def idp_export(fmt: str, req: IDPRequest):
    from fastapi.responses import Response
    doc = _idp.get_document(req.doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    base = doc.filename.rsplit(".", 1)[0]
    fmt = fmt.lower()
    if fmt == "md":
        data = _idp.export_markdown(doc); mime = "text/markdown"; ext = "md"
    elif fmt == "txt":
        data = _idp.export_txt(doc); mime = "text/plain"; ext = "txt"
    elif fmt == "pdf":
        data = _idp.export_pdf(doc); mime = "application/pdf"; ext = "pdf"
    elif fmt == "json":
        data = _idp.export_json(doc.to_dict(include_text=True))
        mime = "application/json"; ext = "json"
    elif fmt == "xlsx":
        model = await _resolve_doc_model("tables")
        tables = await _idp.extract_tables(doc, model)
        data = _idp.export_xlsx(tables)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; ext = "xlsx"
    elif fmt == "csv":
        model = await _resolve_doc_model("tables")
        tables = await _idp.extract_tables(doc, model)
        data = _idp.export_csv(tables); mime = "text/csv"; ext = "csv"
    else:
        raise HTTPException(400, f"Unsupported format: {fmt}")
    return Response(
        content=data, media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{base}.{ext}"'},
    )


# ── Judge-based category picker for user-triggered "send to worker" ─────────
# Uses the same tiny model class the auto-router judge uses. Emits one of
# the delegate categories from a JSON-schema-constrained response so a
# small model (qwen2.5:0.5b/1.5b) can reliably return a valid label.
_DELEGATE_JUDGE_PROMPT = (
    "Pick the SINGLE strongest-fit category for the user's request. Output "
    "STRICT JSON only.\n"
    "\n"
    "Categories:\n"
    "  quick        — one-line factual lookup.\n"
    "  general      — balanced default when nothing else is a strong fit.\n"
    "  research     — needs web search / current facts / sources.\n"
    "  code         — programming, debugging, code review.\n"
    "  deep         — hard multi-step reasoning, proofs, complex analysis.\n"
    "  vision       — analyse an image / screenshot.\n"
    "  long_context — analyse a very long document.\n"
    "  structured   — extract to JSON / tables / strict schema.\n"
    "  emotional    — empathic / warm / personal.\n"
    "  creative     — long-form prose / storytelling / marketing copy.\n"
    "\n"
    "Output: {\"category\": \"<one of the labels above>\"}"
)

_DELEGATE_JUDGE_FORMAT = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [
            "quick", "general", "research", "code", "deep", "vision",
            "long_context", "structured", "emotional", "creative",
        ]},
    },
    "required": ["category"],
}


async def _judge_delegate_category(text: str) -> str:
    """
    Ask the judge model which delegate category best fits `text`.
    Falls back to 'general' on any failure — cheap + safe.
    """
    text = (text or "").strip()
    if not text:
        return "general"
    installed = await _installed_models()
    user_pref = (await _db.get_config("judge_model")) or ""
    prefs = [user_pref, "qwen2.5:1.5b", "qwen2.5:0.5b",
             "llama3.2:1b", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:7b"]
    model = _pick_first_installed(prefs, installed)
    if not model:
        return "general"
    payload = {
        "model":     model,
        "messages": [
            {"role": "system", "content": _DELEGATE_JUDGE_PROMPT},
            {"role": "user",   "content": text[:800]},
        ],
        "stream":    False,
        "keep_alive": "30s",
        "format":    _DELEGATE_JUDGE_FORMAT,
        "options":   {"temperature": 0.0, "num_predict": 30, "num_ctx": 2048},
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                return "general"
            raw = ((r.json().get("message") or {}).get("content") or "").strip()
        parsed = json.loads(raw)
        cat = str(parsed.get("category") or "general").strip().lower()
        if cat not in {
            "quick", "general", "research", "code", "deep", "vision",
            "long_context", "structured", "emotional", "creative",
        }:
            return "general"
        return cat
    except Exception:
        return "general"


# ── /api/delegate/send — user-triggered dispatch from the "send to worker"
# button in the main chat input. The user's text is sent to the judge model
# to pick a category; the delegate is dispatched; ack returned immediately.
# The user's own text and the delegate's result live ONLY in delegated_tasks
# — they never enter the main chat conversation.
class DelegateSendRequest(BaseModel):
    prompt:        str
    conv_id:       str = ""     # optional — for grouping in the Live tab
    source_msg_id: str = ""     # optional — links back to source turn
    category:      str = ""     # optional override; empty → judge picks


@app.post("/api/delegate/send")
async def delegate_send(req: DelegateSendRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    cat = req.category.strip().lower() if req.category else ""
    if not cat or cat not in _delegate.KNOWN_CATEGORIES:
        cat = await _judge_delegate_category(prompt)
    ack = await _delegate.dispatch(
        prompt        = prompt,
        category      = cat,
        conv_id       = req.conv_id,
        main_model    = "",          # main model stays uninvolved
        source_msg_id = req.source_msg_id,
    )
    return {"ok": True, **ack}


# ── /api/delegate — main-model → specialist async subtasks ───────────────────
@app.get("/api/delegate/tasks")
async def delegate_tasks(conv_id: str = "", status: str = "", limit: int = 50):
    """
    List delegated tasks, newest first. Filter by conversation and/or status.
    Frontend polls with `conv_id=<active>` while a chat is open.
    """
    tasks = await _delegate.list_tasks(
        conv_id = conv_id or None,
        status  = status  or None,
        limit   = max(1, min(200, limit)),
    )
    return {"tasks": tasks}


@app.post("/api/delegate/{task_id}/cancel")
async def delegate_cancel(task_id: str):
    ok = await _delegate.cancel(task_id)
    return {"ok": ok, "task_id": task_id}


@app.get("/api/delegate/{task_id}/progress")
async def delegate_progress(task_id: str):
    """
    Live streaming state for a running delegate: current content, thinking
    tokens, tool events. Returns null when the task is no longer running
    (the buffer is cleared 30s after completion).
    """
    return {"progress": _delegate.get_progress(task_id)}


@app.get("/api/delegate/config")
async def delegate_config():
    """Return current category → model mapping (config overrides + defaults)."""
    out = {}
    for cat in _delegate.KNOWN_CATEGORIES:
        configured = (await _db.get_config(f"delegate_model_{cat}")) or ""
        out[cat] = {
            "configured": configured,
            "resolved":   await _delegate._pick_delegate_model(cat),  # noqa: SLF001
        }
    return {"categories": out}


class DelegateConfigUpdate(BaseModel):
    category: str
    model:    str  # empty string clears the override → back to default


@app.post("/api/delegate/config")
async def delegate_config_update(req: DelegateConfigUpdate):
    if req.category not in _delegate.KNOWN_CATEGORIES:
        raise HTTPException(400, f"unknown category: {req.category!r}")
    await _db.set_config(f"delegate_model_{req.category}", req.model or "")
    return {"ok": True}


# ── /api/workers — background worker swarm ───────────────────────────────────
@app.get("/api/workers/status")
async def workers_status():
    """Snapshot of every worker + the scheduler's idle state."""
    return _workers.status()


@app.get("/api/workers/logs")
async def workers_logs(limit: int = 100):
    return {"events": _workers.logs(limit=limit)}


class WorkerEnableRequest(BaseModel):
    enabled: bool


@app.post("/api/workers/{worker_id}/enable")
async def workers_enable(worker_id: str, req: WorkerEnableRequest):
    ok = await _workers.enable(worker_id, req.enabled)
    if not ok:
        raise HTTPException(404, f"unknown worker: {worker_id}")
    return {"ok": True, "enabled": req.enabled}


@app.post("/api/workers/{worker_id}/run-now")
async def workers_run_now(worker_id: str):
    """Fire a worker immediately, bypassing the idle-gate + enabled flag."""
    result = await _workers.run_now(worker_id)
    return result


# ── Serve built frontend (production) ─────────────────────────────────────────
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port  = int(os.getenv("PORT", "8000"))
    # Electron-packaged builds set PERSEPHONE_PROD=1 (and don't want reload
    # since they ship a frozen interpreter without a watcher loop).
    is_prod = bool(os.getenv("PERSEPHONE_PROD")) or not os.access(__file__, os.W_OK)
    reload  = not is_prod
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=port,
        reload=reload,
        reload_dirs=[str(Path(__file__).parent)] if reload else None,
        log_level="info",
    )
