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

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("persephone")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DIST_DIR    = Path(__file__).parent.parent / "dist"

# ── Hardware-tuned Ollama defaults ────────────────────────────────────────────
OLLAMA_DEFAULTS = {
    "num_thread":     _hw.recommended_num_thread(),  # matches the host's actual core count
    "num_batch":      512,     # larger batch = faster prompt processing
    # 8K is enough now that we stopped listing every MCP tool in the system
    # prompt (tools schemas still go via the `tools` array). Doubling KV cache
    # to 16K cost ~2× prompt-eval time on M-series with no real benefit.
    "num_ctx":        8192,
    "f16_kv":         True,    # half-precision KV cache (saves bandwidth)
    "use_mmap":       True,
    "repeat_penalty": 1.1,
}


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

    yield

    # Shut down MCP processes on exit
    try:
        await _mcp_mgr.manager.stop_all()
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
    # Regular native-thinking models
    if _supports_native_thinking(model):
        return _EXTENDED_PREDICT_FLOOR
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


# ── /api/models/roles — post-setup model reassignment ────────────────────────
# The wizard's per-function model choices (main chat, auto-router judge,
# vision, code, OCR, …) all live in `app_config` as plain key/value pairs.
# These two endpoints let the UI read and update them after onboarding, so
# users can pick newly-pulled Ollama models without re-running the wizard.
_MODEL_ROLE_KEYS = [
    "active_model", "judge_model", "vision_model", "code_model",
    "ocr_model", "docs_model", "handwriting_model", "tables_model",
]


@app.get("/api/models/roles")
async def get_model_roles():
    return {k: (await _db.get_config(k)) or "" for k in _MODEL_ROLE_KEYS}


class ModelRolesUpdate(BaseModel):
    active_model:      str | None = None
    judge_model:        str | None = None
    vision_model:        str | None = None
    code_model:          str | None = None
    ocr_model:           str | None = None
    docs_model:          str | None = None
    handwriting_model:   str | None = None
    tables_model:        str | None = None


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
