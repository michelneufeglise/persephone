"""
Live Ollama model-library fetcher for the Settings → Download tab.

DELIBERATE local-first exception: this module makes an OUTBOUND request to
ollama.com to list the newest publicly-available models. The user explicitly
opted into this; everything else in Persephone stays on 127.0.0.1. Ollama has
no official library-listing API, so we scrape the public /library page,
targeting the site's stable `x-test-*` attributes. On ANY failure we fall back
to the curated model_catalog so the tab is never empty and never blocks.
"""

from __future__ import annotations

import re
import time
import logging

import httpx

import model_catalog as _catalog

log = logging.getLogger("ollama_library")

LIBRARY_URL = "https://ollama.com/library"
_CACHE_TTL = 300.0  # seconds
_cache: dict = {"at": 0.0, "models": []}

# Normalised capability vocabulary the UI filters on.
CAPS = ("llm", "moe", "thinking", "vision", "ocr", "code", "embedding", "tools")


def _classify(name: str, description: str, raw_caps: list[str],
              category: str = "", tags: list[str] | None = None) -> tuple[str, list[str]]:
    """Return (primary_type, capabilities[]) from scraped pills + catalog hints."""
    tags = tags or []
    hay = " ".join([name, description, " ".join(raw_caps), category, " ".join(tags)]).lower()
    caps: set[str] = set()

    for c in raw_caps:
        cl = c.lower().strip()
        if "vision" in cl: caps.add("vision")
        if "embed" in cl: caps.add("embedding")
        if "tool" in cl: caps.add("tools")
        if "think" in cl or "reason" in cl: caps.add("thinking")

    if any(k in hay for k in ("embed", "bge", "nomic-embed", "mxbai")): caps.add("embedding")
    if any(k in hay for k in ("moe", "a3b", "mixture", "-a1", "a13b", "a22b")): caps.add("moe")
    if any(k in hay for k in ("think", "reason", "r1", "-r-", "qwq")): caps.add("thinking")
    if any(k in hay for k in ("vision", "-vl", "vl-", "multimodal", "image")): caps.add("vision")
    if any(k in hay for k in ("ocr", "olmocr", "document ocr")): caps.add("ocr")
    if any(k in hay for k in ("coder", "code", "codellama", "starcoder", "deepseek-coder")): caps.add("code")
    if any(k in hay for k in ("tool", "function call", "agent")): caps.add("tools")

    # Primary type
    if "embedding" in caps:
        ptype = "embedding"
    elif "ocr" in caps:
        ptype = "ocr"
    elif "vision" in caps:
        ptype = "vision"
    elif "code" in caps:
        ptype = "code"
    elif "moe" in caps:
        ptype = "moe"
    else:
        ptype = "llm"
        caps.add("llm")
    # Every generative model is at least an llm
    if ptype not in ("embedding",):
        caps.add("llm")

    return ptype, sorted(caps)


def _catalog_models(installed: set[str]) -> list[dict]:
    """Build the normalised model list purely from the curated catalog."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _catalog.MODELS:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        ptype, caps = _classify(
            m["name"], m.get("description", ""), m.get("tags", []),
            category=m.get("category", ""), tags=m.get("tags", []),
        )
        base = m["id"].split(":")[0]
        is_installed = m["id"] in installed or any(
            x == base or x.startswith(base + ":") for x in installed
        )
        out.append({
            "id":          m["id"],
            "name":        m["name"],
            "description": m.get("description", ""),
            "family":      m.get("family", ""),
            "params":      m.get("params", ""),
            "size_gb":     m.get("size_gb", 0),
            "type":        ptype,
            "capabilities": caps,
            "pulls":       "",
            "installed":   is_installed,
        })
    return out


async def _scrape_library() -> list[dict]:
    """Best-effort scrape of ollama.com/library. Returns [] on any failure."""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True,
                                 headers={"User-Agent": "Persephone/1.0"}) as client:
        r = await client.get(LIBRARY_URL)
        r.raise_for_status()
        html = r.text

    models: list[dict] = []
    # Each library entry is an <li x-test-model ...> ... </li> block.
    blocks = re.split(r'<li[^>]*x-test-model', html)
    for blk in blocks[1:]:
        # Name
        mname = re.search(r'x-test-search-response-title[^>]*>\s*([^<]+?)\s*<', blk)
        if not mname:
            mname = re.search(r'<span[^>]*>\s*([a-z0-9][a-z0-9._\-]+)\s*</span>', blk)
        if not mname:
            continue
        name = mname.group(1).strip()
        # Description
        mdesc = re.search(r'<p[^>]*>\s*([^<]+?)\s*</p>', blk)
        desc = (mdesc.group(1).strip() if mdesc else "")
        # Capability pills (x-test-capability)
        caps_raw = re.findall(r'x-test-capability[^>]*>\s*([^<]+?)\s*<', blk)
        # Sizes (x-test-size) e.g. "8b", "70b"
        sizes = re.findall(r'x-test-size[^>]*>\s*([^<]+?)\s*<', blk)
        # Pull count
        mpull = re.search(r'x-test-pull-count[^>]*>\s*([^<]+?)\s*<', blk)
        pulls = (mpull.group(1).strip() if mpull else "")

        params = (sizes[0].upper() if sizes else "")
        ptype, caps = _classify(name, desc, caps_raw)
        # Pull tag: prefer the smallest listed size as a concrete tag, else :latest
        tag = f"{name}:{sizes[0].lower()}" if sizes else f"{name}:latest"
        models.append({
            "id":          tag,
            "name":        name,
            "description": desc,
            "family":      "",
            "params":      params,
            "size_gb":     0,
            "type":        ptype,
            "capabilities": caps,
            "pulls":       pulls,
            "installed":   False,
        })
    return models


def _merge(live: list[dict], catalog: list[dict]) -> list[dict]:
    """Merge live entries with catalog, enriching by base-name; catalog fills gaps."""
    by_base: dict[str, dict] = {}
    for c in catalog:
        by_base.setdefault(c["id"].split(":")[0].lower(), c)

    out: list[dict] = []
    seen_bases: set[str] = set()
    for lv in live:
        base = lv["id"].split(":")[0].lower()
        seen_bases.add(base)
        cat = by_base.get(base)
        if cat:
            # enrich live with catalog metadata where live is missing it
            merged = {**lv}
            if not merged["description"]:
                merged["description"] = cat["description"]
            if not merged["params"]:
                merged["params"] = cat["params"]
            if not merged["size_gb"]:
                merged["size_gb"] = cat["size_gb"]
            if not merged["family"]:
                merged["family"] = cat["family"]
            # union capabilities
            merged["capabilities"] = sorted(set(merged["capabilities"]) | set(cat["capabilities"]))
            out.append(merged)
        else:
            out.append(lv)
    # append catalog-only models the live list didn't include
    for c in catalog:
        if c["id"].split(":")[0].lower() not in seen_bases:
            out.append(c)
    return out


def _mark_installed(models: list[dict], installed: set[str]) -> None:
    for m in models:
        base = m["id"].split(":")[0]
        m["installed"] = m["id"] in installed or any(
            x == base or x.startswith(base + ":") for x in installed
        )


async def get_library(installed: set[str], refresh: bool = False) -> dict:
    """
    Return {source, offline, fetched_at, models[]}. Live scrape merged with the
    curated catalog; falls back to catalog-only on any network error.
    """
    catalog = _catalog_models(installed)
    now = time.time()
    if not refresh and _cache["models"] and (now - _cache["at"] < _CACHE_TTL):
        models = _cache["models"]
        _mark_installed(models, installed)
        return {"source": "cache", "offline": False,
                "fetched_at": int(_cache["at"] * 1000), "models": models}
    try:
        live = await _scrape_library()
        if not live:
            raise RuntimeError("no models parsed from library page")
        models = _merge(live, catalog)
        _mark_installed(models, installed)
        _cache["at"] = now
        _cache["models"] = models
        return {"source": "live", "offline": False,
                "fetched_at": int(now * 1000), "models": models}
    except Exception as exc:
        log.warning("live library fetch failed, using catalog: %s", exc)
        _mark_installed(catalog, installed)
        return {"source": "catalog", "offline": True,
                "fetched_at": int(now * 1000), "models": catalog}
