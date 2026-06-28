"""
Persephone research engine — v0.1 (single-pass) + v0.3 (KB persistence).

  query
    ↓
  PLAN     — reasoning model emits 3-6 sub-questions
    ↓
  GATHER   — for each sub-question:
             DDG / Brave search → top-N URLs → fetch each → chunk → embed → store
    ↓
  SYNTHESIZE — reasoning model writes a markdown report with [1][2] citations
               + a structured JSON sketch.
    ↓
  PERSIST  — runs/sources/chunks live in SQLite; chunk embeddings in sqlite-vec.

Each phase yields a progress event the SSE handler in main.py forwards verbatim
to the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncIterator
from uuid import uuid4

import httpx

import embeddings as _emb
import research_db as _rdb
import mcp_manager as _mcp_mgr

log = logging.getLogger("research")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# ── Tuning knobs ────────────────────────────────────────────────────────────
MAX_SUB_QUESTIONS    = 5     # capped to keep total time reasonable
MAX_SOURCES_PER_SUB  = 3     # how many URLs to fetch per sub-question
MAX_TOTAL_SOURCES    = 12    # global cap across the whole research
CHUNK_TARGET_CHARS   = 1200  # roughly 250-300 tokens per chunk
CHUNK_OVERLAP_CHARS  = 150
FETCH_TIMEOUT_S      = 25.0
SOURCE_MAX_CHARS     = 20_000  # truncate fetched pages

# ── Helpers ─────────────────────────────────────────────────────────────────
async def _ollama_chat(model: str, messages: list[dict], options: dict | None = None,
                       fmt: dict | str | None = None, timeout: float = 90.0,
                       think: bool = False) -> str:
    """One-shot non-streaming chat call.

    Returns assistant content as string. For thinking-capable models (qwen3,
    deepseek-r1, gemma4-thinking, …) we DISABLE thinking by default — research
    sub-tasks need a direct answer, and thinking can consume the entire
    num_predict budget on a reasoning model, leaving content empty.
    """
    payload: dict = {"model": model, "messages": messages, "stream": False, "keep_alive": "5m",
                     "think": think}
    if options:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        if r.status_code != 200:
            log.warning("ollama chat %d: %s", r.status_code, r.text[:200])
            return ""
        msg = (r.json().get("message") or {})
        content = (msg.get("content") or "").strip()
        # Strip any leaked <think>...</think> tags that some models emit even with think:false
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return content


async def _pick_reasoning_model() -> str:
    """Best available chat model for plan + synthesis.

    We deliberately AVOID native-thinking models (qwen3*, deepseek-r1) here —
    they often spend the whole token budget thinking and emit empty content.
    Synthesis wants a direct, citation-rich answer, not a reasoning trace.
    """
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            tags = (await client.get(f"{OLLAMA_BASE}/api/tags")).json().get("models", [])
        installed = {m["name"] for m in tags}
    except Exception:
        installed = set()
    preferred = [
        "gemma4:26b", "gemma4:12b",
        "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b",
        "llama3.3:70b", "hermes3:8b",
    ]
    for p in preferred:
        if p in installed:
            return p
        for x in installed:
            if x.startswith(p + ":") or x == p:
                return x
    return next(iter(installed)) if installed else "qwen2.5:7b"


# ── PLAN ────────────────────────────────────────────────────────────────────
_PLAN_PROMPT = (
    "You decompose a research question into 3-5 focused web-search sub-questions.\n"
    "Each sub-question must be standalone, specific, and answerable from public sources.\n"
    "Avoid duplicates. No meta-questions ('what is the user asking?'). No bullet markers.\n\n"
    "Output STRICT JSON: {\"sub_questions\": [\"...\", \"...\", \"...\"]}"
)
_PLAN_FORMAT = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_SUB_QUESTIONS,
        },
    },
    "required": ["sub_questions"],
}


async def plan(query: str, model: str) -> list[str]:
    raw = await _ollama_chat(
        model,
        [
            {"role": "system", "content": _PLAN_PROMPT},
            {"role": "user",   "content": query},
        ],
        options={"temperature": 0.2, "num_predict": 400, "num_ctx": 4096},
        fmt=_PLAN_FORMAT,
        timeout=60.0,
    )
    try:
        obj = json.loads(raw)
        subs = obj.get("sub_questions") or []
    except Exception:
        subs = []
    cleaned = [s.strip() for s in subs if isinstance(s, str) and s.strip()]
    if not cleaned:
        cleaned = [query]
    return cleaned[:MAX_SUB_QUESTIONS]


# ── GATHER ─────────────────────────────────────────────────────────────────
async def _search(sub_q: str) -> list[dict]:
    """Use DDG MCP (or Brave if installed) → list of {url, title, snippet}."""
    mgr = _mcp_mgr.manager
    # DDG is the preferred default — no API key, fast.
    candidates = [
        ("duckduckgo-search__search",       {"query": sub_q, "max_results": MAX_SOURCES_PER_SUB * 2}),
        ("brave-search__brave_web_search",  {"query": sub_q, "count":       MAX_SOURCES_PER_SUB * 2}),
    ]
    for tool_name, args in candidates:
        sid = tool_name.split("__")[0]
        if sid not in mgr.clients or not mgr.clients[sid].is_running:
            continue
        try:
            text = await asyncio.wait_for(mgr.call(tool_name, args), timeout=20.0)
        except Exception as exc:
            log.warning("search via %s failed: %s", tool_name, exc)
            continue
        return _parse_search_results(text)
    return []


_URL_LINE = re.compile(r"https?://\S+", re.IGNORECASE)


def _parse_search_results(text: str) -> list[dict]:
    """Extract {url, title, snippet} from DDG/Brave MCP plain-text output.

    DDG's mcp-server returns blocks like:
        1. <title>
           URL: <url>
           Summary: <snippet>
    Brave returns JSON-style text. We parse loosely so format drift doesn't
    silently break us.
    """
    results: list[dict] = []
    current: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current.get("url"):
                results.append(current)
                current = {}
            continue
        # Numbered entry: "1. Some title"
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            if current.get("url"):
                results.append(current)
            current = {"title": m.group(1).strip()}
            continue
        # URL line
        if line.lower().startswith("url:"):
            current["url"] = line.split(":", 1)[1].strip()
            continue
        if "url" not in current:
            m2 = _URL_LINE.search(line)
            if m2:
                current["url"] = m2.group(0).rstrip(").,")
                if "title" not in current:
                    current["title"] = line.replace(current["url"], "").strip(" -:")
                continue
        # Summary / description line
        if line.lower().startswith(("summary:", "description:", "snippet:")):
            current["snippet"] = line.split(":", 1)[1].strip()
            continue
        # Free-text continuation appended to snippet
        current["snippet"] = (current.get("snippet", "") + " " + line).strip()
    if current.get("url"):
        results.append(current)
    # de-dup by URL (preserve order)
    seen, out = set(), []
    for r in results:
        u = r.get("url")
        if u and u not in seen:
            seen.add(u)
            out.append(r)
    return out


async def _fetch(url: str) -> tuple[str, str]:
    """Return (title, content_markdown). Uses fetch MCP for clean markdown output."""
    mgr = _mcp_mgr.manager
    if "fetch" in mgr.clients and mgr.clients["fetch"].is_running:
        try:
            text = await asyncio.wait_for(
                mgr.call("fetch__fetch", {"url": url, "max_length": SOURCE_MAX_CHARS}),
                timeout=FETCH_TIMEOUT_S,
            )
            # fetch MCP prefixes with "Contents of <url>:" — strip it
            cleaned = re.sub(r"^Contents of [^\n]+:\n+", "", text)
            title = _extract_title(cleaned) or url
            return title, cleaned[:SOURCE_MAX_CHARS]
        except Exception as exc:
            log.warning("fetch MCP failed for %s: %s", url, exc)
    # Fallback: raw HTTP
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_S, follow_redirects=True,
            headers={"User-Agent": "PersephoneResearch/1.0"},
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return url, ""
            text = re.sub(r"<[^>]+>", " ", r.text)  # crude strip-tags
            text = re.sub(r"\s+", " ", text).strip()
            return url, text[:SOURCE_MAX_CHARS]
    except Exception as exc:
        log.warning("raw HTTP fetch failed for %s: %s", url, exc)
        return url, ""


def _extract_title(md_text: str) -> str:
    for line in md_text.splitlines()[:20]:
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return ""


def _chunk(text: str) -> list[str]:
    """Paragraph-aware chunker. Targets ~CHUNK_TARGET_CHARS with overlap."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= CHUNK_TARGET_CHARS:
        return [text] if text else []

    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if not p.strip():
            continue
        if len(buf) + len(p) + 2 <= CHUNK_TARGET_CHARS:
            buf = (buf + "\n\n" + p) if buf else p
        else:
            if buf:
                chunks.append(buf)
            # split overly long paragraph
            if len(p) > CHUNK_TARGET_CHARS:
                for i in range(0, len(p), CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS):
                    chunks.append(p[i : i + CHUNK_TARGET_CHARS])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


# ── SYNTHESIZE ─────────────────────────────────────────────────────────────
_SYNTH_PROMPT = (
    "You are a research synthesizer. The user asked a question; below is\n"
    "evidence collected from several web sources, each labelled [N] with a URL.\n"
    "\n"
    "Write a thorough, neutral, well-organised markdown report that answers the\n"
    "user's question using ONLY the evidence provided. Use inline footnote\n"
    "citations like [1], [2] after every factual claim. Do not invent sources.\n"
    "\n"
    "VISUAL GUIDELINES — the renderer turns rich markdown into illustrations.\n"
    "Lean into it:\n"
    "  - Use `## Section` headings every 2-3 paragraphs (each gets an\n"
    "    ornamental divider above it).\n"
    "  - Use blockquotes `> ...` for pulled-out key insights — they render as\n"
    "    elegant editorial pull-quotes.\n"
    "  - Use **bold** for terms of art the reader should notice.\n"
    "  - Use bullet lists `- ...` for parallel points (rendered with diamond\n"
    "    bullets) and numbered lists `1. ...` for sequences (numbered orbs).\n"
    "  - Use compact markdown tables (| col | col |) when comparing things —\n"
    "    they get a hand-drawn frame.\n"
    "  - WHEN AN ANSWER INVOLVES A FLOW, ARCHITECTURE, HIERARCHY OR COMPARISON,\n"
    "    INCLUDE a fenced ```mermaid block — it renders as a real diagram.\n"
    "    Prefer flowchart TD, graph LR, sequenceDiagram, or simple classDiagram.\n"
    "    Keep diagrams small (≤8 nodes). One diagram per report usually.\n"
    "\n"
    "    STRICT mermaid syntax rules — follow these or it won't render:\n"
    "      • NO trailing semicolons (`A --> B`, NOT `A --> B;`)\n"
    "      • Multi-word node labels MUST be in double quotes:\n"
    "          `A((\"Start node\"))`   NOT  `A((Start node))`\n"
    "      • Multi-word subgraph titles MUST be quoted:\n"
    "          `subgraph \"My Sub\"`   NOT  `subgraph My Sub`\n"
    "      • No `direction TB;` inside subgraphs — declare it at the top\n"
    "        once: `graph TD` (top-down) or `graph LR` (left-right).\n"
    "\n"
    "    Example of a valid diagram:\n"
    "        ```mermaid\n"
    "        graph LR\n"
    "            User([User]) --> API[\"API Gateway\"]\n"
    "            API --> Auth{\"Auth?\"}\n"
    "            Auth -- yes --> DB[(Database)]\n"
    "            Auth -- no --> Reject([Reject])\n"
    "            DB --> API\n"
    "            API --> User\n"
    "        ```\n"
    "\n"
    "Structure:\n"
    "  # <title>\n"
    "  one-paragraph executive summary  (this becomes a drop-cap lede)\n"
    "  ## <section>\n"
    "  ... body with [N] citations, lists, an optional mermaid diagram ...\n"
    "  ## <section>\n"
    "  ... ...\n"
    "  ## Sources\n"
    "  [1] <url>\n"
    "  [2] <url>\n"
)


def _build_synth_input(query: str, evidence: list[dict]) -> str:
    parts = [f"USER QUESTION: {query}\n\nEVIDENCE:\n"]
    for i, ev in enumerate(evidence, start=1):
        parts.append(f"\n[{i}] {ev['title']}  ({ev['url']})\n{ev['text']}\n")
    return "".join(parts)


# ── Main entry ──────────────────────────────────────────────────────────────
async def run_research(query: str, run_id: str | None = None) -> AsyncIterator[dict]:
    """Generator of progress events.

    Yields dicts; main.py wraps them as `data: <json>\\n\\n` SSE frames.
    Final event is `{"phase":"done", "run_id": ..., "report_md": ...}`.
    """
    run_id = run_id or uuid4().hex
    started = time.monotonic()
    yield {"phase": "started", "run_id": run_id, "query": query}

    try:
        model = await _pick_reasoning_model()
        yield {"phase": "model", "model": model}

        await _rdb.create_run(run_id, query)

        # 1. PLAN
        yield {"phase": "planning"}
        sub_questions = await plan(query, model)
        yield {"phase": "plan", "sub_questions": sub_questions}

        # 2. GATHER (concurrent searches, then per-URL fetch)
        evidence: list[dict] = []   # rolled-up for synthesis
        seen_urls: set[str] = set()
        total_sources = 0

        for sub_q in sub_questions:
            if total_sources >= MAX_TOTAL_SOURCES:
                break
            yield {"phase": "search", "sub_question": sub_q}
            hits = await _search(sub_q)
            yield {"phase": "search_done", "sub_question": sub_q, "n": len(hits)}

            taken = 0
            for hit in hits:
                if taken >= MAX_SOURCES_PER_SUB or total_sources >= MAX_TOTAL_SOURCES:
                    break
                url = hit.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                yield {"phase": "fetch", "url": url, "title": hit.get("title", "")}
                title, content = await _fetch(url)
                ok = bool(content.strip())
                source_id = await _rdb.add_source(
                    run_id=run_id, url=url, title=title or hit.get("title", ""),
                    ok=ok, chars=len(content),
                )
                if not ok:
                    yield {"phase": "fetch_failed", "url": url}
                    continue
                chunks = _chunk(content)
                vecs = await _emb.embed_batch(chunks)
                await _rdb.add_chunks(source_id=source_id, run_id=run_id,
                                      chunks=chunks, embeddings=vecs)
                yield {"phase": "stored", "url": url, "chunks": len(chunks)}
                # keep the trimmed text for synthesis (avoid blowing up the prompt)
                evidence.append({
                    "url":   url,
                    "title": title or hit.get("title", ""),
                    "text":  content[:4000],   # synthesis-side cap per source
                })
                taken += 1
                total_sources += 1

        if not evidence:
            await _rdb.finalise_run(run_id, "failed",
                                    error="no sources could be fetched")
            yield {"phase": "failed", "error": "no sources could be fetched"}
            return

        # 3. SYNTHESIZE
        yield {"phase": "synthesizing", "sources": len(evidence)}
        synth_input = _build_synth_input(query, evidence)
        synth_msgs = [
            {"role": "system", "content": _SYNTH_PROMPT},
            {"role": "user",   "content": synth_input},
        ]
        report_md = await _ollama_chat(
            model, synth_msgs,
            options={"temperature": 0.3, "num_predict": 4096, "num_ctx": 16384},
            timeout=300.0,
        )
        # Retry once with a known-safe non-thinking model if first attempt empty
        if not report_md.strip():
            fallback = "qwen2.5:7b"
            yield {"phase": "synth_retry", "model": fallback}
            report_md = await _ollama_chat(
                fallback, synth_msgs,
                options={"temperature": 0.3, "num_predict": 4096, "num_ctx": 16384},
                timeout=240.0,
            )
        # Optional structured sidecar — best-effort, ignored on failure
        report_json = {
            "query":   query,
            "sources": [{"n": i + 1, "url": ev["url"], "title": ev["title"]}
                        for i, ev in enumerate(evidence)],
            "duration_s": round(time.monotonic() - started, 1),
        }

        await _rdb.finalise_run(run_id, "done", report_md=report_md,
                                report_json=report_json)
        yield {
            "phase":      "done",
            "run_id":     run_id,
            "report_md":  report_md,
            "sources":    report_json["sources"],
            "duration_s": report_json["duration_s"],
        }

    except Exception as exc:
        log.exception("research failed: %s", exc)
        try:
            await _rdb.finalise_run(run_id, "failed", error=str(exc))
        except Exception:
            pass
        yield {"phase": "failed", "error": str(exc)}
