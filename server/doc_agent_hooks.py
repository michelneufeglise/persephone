"""
Hook builder and implementation for the document agent.

Injects all external dependencies (models, OCR, vision calls, etc.) without
importing main.py, enabling clean separation and preventing circular imports.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

log = logging.getLogger("doc_agent_hooks")


# ── Configuration ──────────────────────────────────────────────────────────
@dataclass
class HookDeps:
    """Injected dependencies from main.py (or test fixtures)."""
    # Callables/values for resolving models, docs, and config
    get_doc: Callable[[str], Any]  # (doc_id) -> Document|None
    resolve_doc_model: Callable[[str], Any]  # (category) -> awaitable str
    resolve_doc_model_for: Callable[[Any, str], Any]  # (doc, category) -> awaitable str
    get_config: Callable[[str], Any]  # (key) -> awaitable str
    installed_models: Callable[[], Any]  # () -> awaitable list[str]
    name_is_vision: Callable[[str], bool]  # (name) -> bool
    ollama_tags: Callable[[], Any]  # () -> awaitable list[dict]
    run_ocr: Callable[[Any, str], Any]  # (doc, model) -> awaitable str
    stream_text: Callable[..., Any]  # (model, prompt, *, think=False) -> AsyncIterator
    vision_call: Callable[[str, str, list[str]], Any]  # (model, prompt, image_paths) -> awaitable str
    supports_thinking: Callable[[str], bool]  # (model) -> bool
    tmp_dir: Callable[[], Path]  # () -> Path
    model_capabilities: Callable[[str], Any]  # (model_name) -> awaitable list[str] | None
    laya: Optional[Any] = None  # laya module or None
    model_override: Optional[str] = None  # override for answer model


# ── Broken model cache ────────────────────────────────────────────────────

class _BrokenModelCache:
    """Track models that failed to load, with TTL expiry."""

    def __init__(self, ttl_seconds: float = 3600.0, clock_fn: Callable[[], float] | None = None):
        self.ttl = ttl_seconds
        self.broken: dict[str, tuple[float, str]] = {}  # name -> (timestamp, reason)
        self.clock_fn = clock_fn or time.time
        self.lock = asyncio.Lock()

    async def mark_broken(self, name: str, reason: str) -> None:
        """Mark a model as broken."""
        async with self.lock:
            self.broken[name] = (self.clock_fn(), reason)

    async def is_broken(self, name: str) -> bool:
        """Check if a model is currently marked as broken."""
        async with self.lock:
            if name not in self.broken:
                return False
            timestamp, _ = self.broken[name]
            if self.clock_fn() - timestamp < self.ttl:
                return True
            # Expired; remove from cache
            del self.broken[name]
            return False

    async def clear(self) -> None:
        """Clear all broken entries."""
        async with self.lock:
            self.broken.clear()


# Module-level instance
_broken_cache = _BrokenModelCache(ttl_seconds=3600.0)


def looks_like_load_failure(err_text: str) -> bool:
    """
    Check if an error looks like a model loading failure.

    Matches (case-insensitive): 'unknown model architecture', 'llama-server process has terminated',
    'error loading model', 'failed to load model', 'requires more system memory', 'model runner has unexpectedly stopped'.
    """
    err_lower = err_text.lower()
    failure_indicators = [
        "unknown model architecture",
        "llama-server process has terminated",
        "error loading model",
        "failed to load model",
        "requires more system memory",
        "model runner has unexpectedly stopped",
    ]
    return any(indicator in err_lower for indicator in failure_indicators)


# ── Pure functions ────────────────────────────────────────────────────────

def is_ocr_only_model(name: str) -> bool:
    """
    Detect if a model name indicates an OCR-only model.

    Checks if the name contains OCR indicators (case-insensitive) or is an embedding model.
    Examples that return True:
    - 'hf.co/DevQuasar/baidu.Unlimited-OCR-GGUF:q4_k_m'
    - 'glm-ocr:latest'
    - 'richardyoung/olmocr2:7b-q8'
    - 'frob/unlimited-ocr:q8_0'
    - Any model with 'embed' in the name

    Args:
        name: The model name/ID to check

    Returns:
        True if the model is OCR-only or an embedding model, False otherwise
    """
    if not name:
        return False

    name_lower = name.lower()

    # Exclude embedding models
    if "embed" in name_lower:
        return True

    # Check for OCR indicators
    ocr_hints = ("ocr",)
    for hint in ocr_hints:
        if hint in name_lower:
            return True

    return False


def pick_signature_model(
    cfg: dict[str, str],
    installed: list[str],
    is_vision: Callable[[str], bool],
) -> Optional[str]:
    """
    Pick a vision model for signature/handwriting comparison.

    Priority order:
    1. config handwriting_model if installed AND vision-capable
    2. config vision_model if installed AND vision-capable
    3. First installed model starting with 'llama3.2-vision'
    4. First installed model starting with 'qwen2.5vl', 'gemma3', or 'minicpm-v'
    5. Smallest (by parameter size in tags if available) other vision model
    6. None if no vision model found

    Args:
        cfg: Dict with keys "handwriting_model", "vision_model" (from config)
        installed: List of installed model names
        is_vision: Callable that returns True if a model name is vision-capable

    Returns:
        Model name str, or None if no suitable model found.
    """
    # Helper to check if model is in installed list (with tag flexibility)
    def _is_installed(model_name: str) -> bool:
        if not model_name:
            return False
        if model_name in installed:
            return True
        base = model_name.split(":", 1)[0]
        return any(m.split(":", 1)[0] == base for m in installed)

    # 1. Handwriting model (only if vision-capable)
    handwriting = cfg.get("handwriting_model", "")
    if handwriting and _is_installed(handwriting) and is_vision(handwriting):
        return handwriting

    # 2. Vision model from config (only if vision-capable)
    vision = cfg.get("vision_model", "")
    if vision and _is_installed(vision) and is_vision(vision):
        return vision

    # 3. First llama3.2-vision
    for m in installed:
        if m.lower().startswith("llama3.2-vision"):
            return m

    # 4. First qwen2.5vl, gemma3, minicpm-v
    prefixes = ["qwen2.5vl", "gemma3", "minicpm-v"]
    for m in installed:
        base = m.lower().split(":", 1)[0].split("/")[-1]
        if any(base.startswith(p.lower()) for p in prefixes):
            return m

    # 5. Any other installed vision model (prefer smaller)
    vision_models = [m for m in installed if is_vision(m)]
    if not vision_models:
        return None

    # Try to sort by size (parse parameter count from name)
    def _model_size_key(name: str) -> float:
        # Extract size like "7b", "13b", "70b" from name
        import re
        m = re.search(r"(\d+\.?\d*)b", name.lower())
        return float(m.group(1)) if m else 999.0

    vision_models.sort(key=_model_size_key)
    return vision_models[0]


async def rank_signature_models(
    deps: HookDeps,
    cfg: dict[str, str],
    installed: list[str],
    tags: list[dict],
) -> list[str]:
    """
    Rank available vision models for signature/handwriting comparison.

    Returns up to 4 candidates in priority order:
    1. config handwriting_model (if installed, capable, not broken)
    2. config vision_model (if installed, capable, not broken)
    3. installed llama3.2-vision* (if capable, not broken)
    4. remaining installed vision models sorted by size (ascending), excluding OCR-only

    Requires that model_capabilities can distinguish capability-based inclusion
    (e.g., gemma4:12b is included even if name-check fails). Falls back to
    name-check if capabilities unavailable.

    Args:
        deps: HookDeps with model_capabilities callable
        cfg: {"handwriting_model": str, "vision_model": str}
        installed: list of installed model names
        tags: list of ollama tag dicts with 'name' and optionally 'details', 'size'

    Returns:
        list of up to 4 model names, highest priority first
    """
    OCR_ONLY_HINTS = ("ocr",)

    # Helper to check if model is installed (tag-flexible)
    def _is_installed(model_name: str) -> bool:
        if not model_name:
            return False
        if model_name in installed:
            return True
        base = model_name.split(":", 1)[0]
        return any(m.split(":", 1)[0] == base for m in installed)

    # Helper to check if model is vision-capable
    async def _is_vision_capable(name: str) -> bool:
        """Check vision capability via model_capabilities, fall back to name-check."""
        try:
            caps = await deps.model_capabilities(name)
            if caps is not None:
                return "vision" in caps
        except Exception:
            pass
        # Fallback to name-based check
        return deps.name_is_vision(name)

    # Helper to get parameter size from tags
    def _get_param_size(model_name: str) -> float:
        """Extract parameter size (B) from tags, return 999 if unknown."""
        for tag in tags:
            if tag.get("name") == model_name:
                details = tag.get("details", {})
                param_size_str = details.get("parameter_size", "")
                if param_size_str:
                    # Might be "7.0B", "70B", etc.
                    import re
                    m = re.search(r"(\d+\.?\d*)", param_size_str)
                    if m:
                        try:
                            return float(m.group(1))
                        except ValueError:
                            pass
        # Fallback: parse from name
        import re
        m = re.search(r"(\d+\.?\d*)b", model_name.lower())
        return float(m.group(1)) if m else 999.0

    # Helper to check if model is broken
    async def _is_broken(name: str) -> bool:
        return await _broken_cache.is_broken(name)

    candidates = []

    # 1. config handwriting_model
    hw_model = cfg.get("handwriting_model", "")
    if hw_model and _is_installed(hw_model):
        if not await _is_broken(hw_model) and await _is_vision_capable(hw_model):
            candidates.append(hw_model)

    # 2. config vision_model
    vision_model = cfg.get("vision_model", "")
    if vision_model and _is_installed(vision_model) and vision_model not in candidates:
        if not await _is_broken(vision_model) and await _is_vision_capable(vision_model):
            candidates.append(vision_model)

    # 3. llama3.2-vision (if available, capable, not broken)
    for m in installed:
        if m.lower().startswith("llama3.2-vision"):
            if m not in candidates and not await _is_broken(m) and await _is_vision_capable(m):
                candidates.append(m)
                break  # Only take first

    # 4. Other vision models (not OCR-only), sorted by size ascending
    if len(candidates) < 4:
        other_vision = []
        for m in installed:
            if m in candidates:
                continue  # Already included
            # Skip OCR-only models
            if any(hint.lower() in m.lower() for hint in OCR_ONLY_HINTS):
                continue
            # Check if broken
            if await _is_broken(m):
                continue
            # Check if vision-capable
            if await _is_vision_capable(m):
                other_vision.append(m)

        # Sort by parameter size ascending (smaller first)
        other_vision.sort(key=_get_param_size)
        candidates.extend(other_vision[: 4 - len(candidates)])

    return candidates[:4]


# ── Model info cache ──────────────────────────────────────────────────────

class _ModelInfoCache:
    """Thread-safe LRU cache for model info with ~60s TTL."""

    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl = ttl_seconds
        self.cache: dict[str, tuple[float, dict]] = {}  # name -> (timestamp, info)
        self.lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        name: str,
        fetch_fn: Callable[[str], Any],  # async
    ) -> dict:
        """Get cached info or fetch fresh."""
        async with self.lock:
            if name in self.cache:
                timestamp, cached = self.cache[name]
                if time.time() - timestamp < self.ttl:
                    return cached

            # Fetch fresh
            info = await fetch_fn(name)
            self.cache[name] = (time.time(), info)
            return info


# ── Hook builder ──────────────────────────────────────────────────────────

async def build_hooks(deps: HookDeps) -> "AgentHooks":
    """
    Build the AgentHooks instance with all injected dependencies.

    Implements lazy loading and caching where appropriate (e.g., model info).

    Args:
        deps: HookDeps with all required callables and values

    Returns:
        AgentHooks instance ready for use by run_agent()
    """
    from doc_agent import AgentHooks

    model_info_cache = _ModelInfoCache(ttl_seconds=60.0)

    # Helper: resolve model info from ollama_tags
    async def _fetch_model_info(name: str) -> dict:
        """Fetch model info from ollama_tags; return minimal dict if unknown."""
        try:
            tags = await deps.ollama_tags()
            for tag_dict in tags:
                if tag_dict.get("name") == name:
                    # Extract relevant fields
                    details = tag_dict.get("details", {})
                    size_bytes = tag_dict.get("size", 0)
                    size_gb = round(size_bytes / (1024**3), 1) if size_bytes else None

                    return {
                        "name": name,
                        "family": details.get("family", "unknown"),
                        "parameter_size": details.get("parameter_size", "unknown"),
                        "quantization_level": details.get("quantization_level", "unknown"),
                        "size_gb": size_gb,
                        "is_vision": deps.name_is_vision(name),
                    }
            # Unknown model
            return {"name": name}
        except Exception as e:
            log.debug(f"Failed to fetch model info for {name}: {e}")
            return {"name": name}

    async def model_info_fn(name: str) -> dict:
        """Get model info from cache or fetch fresh."""
        return await model_info_cache.get_or_fetch(name, _fetch_model_info)

    # Helper: pick a vision model (called from run_agent)
    async def pick_vision_model_fn() -> Optional[str]:
        """Pick the best available vision model based on config and installed."""
        cfg_str = await deps.get_config("vision_model") or ""
        handwriting_str = await deps.get_config("handwriting_model") or ""
        installed = await deps.installed_models()

        cfg = {
            "vision_model": cfg_str,
            "handwriting_model": handwriting_str,
        }
        return pick_signature_model(cfg, installed, deps.name_is_vision)

    # Helper: get page image paths
    def page_image_paths_fn(
        doc: Any,
        pages: Optional[list[int]],
        dpi: int,
    ) -> list[str]:
        """
        Get page image paths, either from stored images or re-rendered PDFs.

        If the original PDF is available and PyMuPDF is installed, re-render
        those pages at the given DPI. Otherwise, return stored page_images paths.
        """
        # Try to import PyMuPDF
        try:
            import fitz
        except ImportError:
            fitz = None

        # Determine which pages to process (1-based, clamped to available)
        target_pages = pages or [1]
        if not doc.page_images:
            return []

        num_pages = len(doc.page_images)
        target_pages = [max(1, min(p, num_pages)) for p in target_pages]

        # Deduplicate target_pages to avoid processing the same page twice
        seen = set()
        unique_pages = []
        for p in target_pages:
            if p not in seen:
                unique_pages.append(p)
                seen.add(p)
        target_pages = unique_pages

        # If no PyMuPDF or no original file, return stored images (0-based indexing)
        if fitz is None:
            result = []
            for p in target_pages:
                if 1 <= p <= num_pages:
                    result.append(doc.page_images[p - 1])
            return result

        # Try to find and re-render the original PDF
        from idp_engine import STORAGE_DIR
        doc_dir = STORAGE_DIR / doc.id

        # Look for original file (try various extensions)
        original_file = None
        for ext in [".pdf", ".PDF"]:
            candidate = doc_dir / f"document{ext}"
            if candidate.exists():
                original_file = candidate
                break

        # If no original, fall back to stored images
        if not original_file:
            result = []
            for p in target_pages:
                if 1 <= p <= num_pages:
                    result.append(doc.page_images[p - 1])
            return result

        # Re-render pages to PNG in tmp_dir
        pdf = None
        try:
            pdf = fitz.open(str(original_file))
            tmp = deps.tmp_dir()
            result = []

            for p in target_pages:
                if 1 <= p <= len(pdf):
                    page = pdf[p - 1]
                    # Render at DPI
                    mat = fitz.Matrix(dpi / 72, dpi / 72)
                    pix = page.get_pixmap(matrix=mat, alpha=False)

                    # Save to temp PNG
                    out_path = tmp / f"page_{p}_dpi{dpi}.png"
                    pix.save(str(out_path))
                    result.append(str(out_path))

            return result
        except Exception as e:
            log.warning(f"Failed to re-render PDF pages: {e}")
            # Fall back to stored images
            result = []
            for p in target_pages:
                if 1 <= p <= num_pages:
                    result.append(doc.page_images[p - 1])
            return result
        finally:
            # Ensure PDF is closed, even on exception
            if pdf is not None:
                try:
                    pdf.close()
                except Exception as e:
                    log.debug(f"Failed to close PDF: {e}")

    # Helper: vision call with composition for single-image models
    async def vision_call_fn(
        model: str,
        prompt: str,
        reference_paths: list[str],
        subject_paths: list[str],
    ) -> str:
        """
        Call vision model, composing images if needed for single-image models.

        If the model is single-image capable, compose reference and subject
        images into one composite image, call once, then clean up the temp file.
        Otherwise, pass reference_paths + subject_paths as a list.
        """
        from image_compose import is_single_image_model, compose_comparison

        if is_single_image_model(model):
            # Single-image model: compose reference and subject
            if not reference_paths or not subject_paths:
                raise ValueError("Single-image model requires both reference and subject images")

            tmp = deps.tmp_dir()
            composite_path = tmp / "composite.png"

            try:
                # Compose the images
                compose_comparison(reference_paths, subject_paths, str(composite_path))

                # Call vision with composite
                return await deps.vision_call(model, prompt, [str(composite_path)])
            finally:
                # Clean up composite
                try:
                    import os
                    if Path(composite_path).exists():
                        os.unlink(composite_path)
                except Exception as e:
                    log.debug(f"Failed to clean up composite: {e}")
        else:
            # Multi-image model: pass reference + subject directly
            all_paths = reference_paths + subject_paths
            return await deps.vision_call(model, prompt, all_paths)

    # Helper: stream LLM with thinking support
    async def stream_llm_fn(
        model: str,
        prompt: str,
        think: bool,
    ) -> AsyncIterator[dict]:
        """Stream LLM output with optional thinking."""
        async for event in deps.stream_text(model, prompt, think=think):
            yield event

    # Helper: Laya intent decision (SYNC function, called via asyncio.to_thread)
    def laya_intent_fn(message: str, files: list[dict]) -> Optional[dict]:
        """Call Laya intent decision (sync, blocking ~60-200ms)."""
        if deps.laya is None:
            return None
        try:
            return deps.laya.decide_intent(message, files)
        except Exception as e:
            log.debug(f"Laya intent failed: {e}")
            return None

    # Helper: Laya role decision (SYNC function, called via asyncio.to_thread)
    def laya_role_fn(message: str, file: dict) -> Optional[dict]:
        """Call Laya role decision (sync, blocking ~60-200ms)."""
        if deps.laya is None:
            return None
        try:
            return deps.laya.decide_file_role(message, file)
        except Exception as e:
            log.debug(f"Laya role failed: {e}")
            return None

    # Helper: Laya document kind decision (SYNC function, called via asyncio.to_thread)
    def laya_doc_kind_fn(text: str) -> Optional[dict]:
        """Call Laya document kind decision (sync, blocking ~60-200ms)."""
        if deps.laya is None:
            return None
        try:
            result = deps.laya.decide(text)
            if result:
                return {
                    "kind": result.get("kind", "unknown"),
                    "confidence": result.get("confidence", 0),
                }
            return None
        except Exception as e:
            log.debug(f"Laya doc_kind failed: {e}")
            return None

    # Helper: Laya status (SYNC function)
    def laya_info_fn() -> dict:
        """Get Laya status info (sync)."""
        if deps.laya is None:
            return {"available": False}
        try:
            status = deps.laya.status()
            return {
                "name": "Laya",
                "variant": "English",
                "params": "~421M",
                "size_gb": 0.8,
                "type": "non-generative decision model",
                "device": status.get("device"),
                "available": status.get("available", False),
                "loaded": status.get("loaded", False),
            }
        except Exception:
            return {"available": False}

    # Helper: resolve text model with fallback chain (respects override)
    async def resolve_text_model_fn(doc: Any, category: str) -> str:
        """Resolve the text model, respecting model_override if set."""
        if deps.model_override:
            return deps.model_override

        # Get the configured model (same as before for backwards compat)
        configured_model = await deps.resolve_doc_model_for(doc, category)
        return configured_model

    # Helper: resolve text model with fallback chain and reason info
    async def resolve_text_model_info_fn(doc: Any, category: str) -> dict[str, Optional[str]]:
        """
        Resolve the text model with fallback chain and return info about fallback.

        Returns:
            {"model": str, "configured": str|None, "reason": str|None}
            - model: The model to use
            - configured: The originally configured model (if known)
            - reason: Why a fallback happened (None if no fallback needed)
        """
        # If override is set, use it as-is (user's explicit choice)
        if deps.model_override:
            return {
                "model": deps.model_override,
                "configured": None,
                "reason": None,
            }

        # Get the configured model
        configured_model = await deps.resolve_doc_model_for(doc, category)

        # Check if it's OCR-only or embedding model
        if is_ocr_only_model(configured_model):
            # Need to walk fallback chain
            installed = await deps.installed_models()

            # Build a fallback chain based on config keys
            fallback_keys = ["docs_model", "multidoc_model", "active_model", "text_model"]
            fallback_models = []

            for key in fallback_keys:
                try:
                    candidate = await deps.get_config(key)
                    if candidate and not is_ocr_only_model(candidate):
                        # Check if installed
                        if candidate in installed or any(
                            m.split(":", 1)[0] == candidate.split(":", 1)[0]
                            for m in installed
                        ):
                            fallback_models.append(candidate)
                except Exception:
                    pass

            # Add built-in preference list
            builtin_prefs = [
                "gemma4:12b", "qwen2.5:7b", "qwen3:4b-instruct-2507-q4_K_M",
                "qwen2.5:7b-instruct-q4_K_M", "llama3.1:8b", "qwen2.5:3b",
                "qwen2.5:1.5b"
            ]
            for pref in builtin_prefs:
                if pref not in fallback_models and any(
                    m.split(":", 1)[0] == pref.split(":", 1)[0]
                    for m in installed
                ):
                    # Find the exact installed model
                    for m in installed:
                        if m.split(":", 1)[0] == pref.split(":", 1)[0]:
                            fallback_models.append(m)
                            break

            # Find any other installed non-OCR, non-embedding chat model
            for m in installed:
                if (not is_ocr_only_model(m) and
                    m not in fallback_models and
                    "vision" not in m.lower() and
                    "embed" not in m.lower()):
                    fallback_models.append(m)

            # Remove duplicates while preserving order
            seen = set()
            unique_fallbacks = []
            for m in fallback_models:
                if m not in seen:
                    unique_fallbacks.append(m)
                    seen.add(m)

            # Pick the first available fallback
            if unique_fallbacks:
                selected = unique_fallbacks[0]
                reason = f"{configured_model.split('/')[-1].split(':')[0]} is an OCR-only model — using {selected.split('/')[-1].split(':')[0]} instead"
                return {
                    "model": selected,
                    "configured": configured_model,
                    "reason": reason,
                }

            # No fallback found; return configured anyway (will likely fail)
            reason = f"{configured_model.split('/')[-1].split(':')[0]} is an OCR-only model but no text model is installed"
            return {
                "model": configured_model,
                "configured": configured_model,
                "reason": reason,
            }

        # Configured model is fine
        return {
            "model": configured_model,
            "configured": None,
            "reason": None,
        }

    # Helper: now in ms
    def now_ms_fn() -> int:
        return int(time.time() * 1000)

    # Helper: get vision model candidates (ranked list)
    async def vision_candidates_fn() -> list[str]:
        """Get ranked list of vision model candidates."""
        try:
            cfg_str = await deps.get_config("vision_model") or ""
            handwriting_str = await deps.get_config("handwriting_model") or ""
            installed = await deps.installed_models()
            tags = await deps.ollama_tags()

            cfg = {
                "vision_model": cfg_str,
                "handwriting_model": handwriting_str,
            }
            return await rank_signature_models(deps, cfg, installed, tags)
        except Exception as e:
            log.error(f"Error getting vision candidates: {e}")
            return []

    # Helper: mark a vision model as failed (sync, for use in async context)
    def mark_vision_failed_fn(model: str, err_text: str) -> None:
        """Mark a vision model as failed if it looks like a load failure."""
        if looks_like_load_failure(err_text):
            # Run async function in a new event loop if needed
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're already in an async context; schedule it as a task
                    asyncio.create_task(_broken_cache.mark_broken(model, err_text[:200]))
                else:
                    # Run synchronously
                    loop.run_until_complete(_broken_cache.mark_broken(model, err_text[:200]))
            except RuntimeError:
                # No event loop; create one
                asyncio.run(_broken_cache.mark_broken(model, err_text[:200]))

    # Build and return AgentHooks
    return AgentHooks(
        get_doc=deps.get_doc,
        laya_intent=laya_intent_fn,  # Sync callable for asyncio.to_thread
        laya_role=laya_role_fn,      # Sync callable for asyncio.to_thread
        laya_doc_kind=laya_doc_kind_fn,  # Sync callable for asyncio.to_thread
        laya_info=laya_info_fn,      # Sync callable
        page_image_paths=page_image_paths_fn,  # Async function
        resolve_model=deps.resolve_doc_model,
        resolve_text_model=resolve_text_model_fn,
        resolve_text_model_info=resolve_text_model_info_fn,  # Async function with fallback info
        pick_vision_model=pick_vision_model_fn,
        vision_candidates=vision_candidates_fn,  # Async function for ranked candidates
        model_info=model_info_fn,
        run_ocr=deps.run_ocr,
        stream_llm=stream_llm_fn,
        vision_call=vision_call_fn,
        mark_vision_failed=mark_vision_failed_fn,  # Sync function
        now_ms=now_ms_fn,
    )
