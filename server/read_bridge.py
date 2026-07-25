"""
File-read bridge for the main chat model.

When the chat model calls a filesystem MCP tool (`filesystem__read_file`,
`persephone-fs__read_file`, `persephone-fs__read_text_file`,
`persephone-fs__read_media_file`, or the `_multiple_files` variants) on a
non-text file, we intercept BEFORE the MCP call and route through Persephone's
own extractors:

  * PDF, DOCX, XLSX, CSV        → idp_engine's text extractors (no model call)
  * PNG, JPG, JPEG, WEBP, TIFF  → OCR via the user's configured OCR model
  * Everything else that's plainly text (.md .txt .py .json .yaml …) → passthrough

The main chat model then sees a normal text string as the tool result — it
doesn't have to know anything about OCR / PDF parsing.

Design notes
------------
- Interception happens in `main._run_chat_turn` (planner + delegated) and
  `main._stream_ollama_chat` (streaming chat). Both call `maybe_intercept`
  BEFORE dispatching the MCP call.
- We cache extracted text keyed by (path, size, mtime) — re-reading the same
  file inside one session is instant.
- Timeouts: PDFs up to ~50 pages are fine on CPU. We bound the total time so
  a huge scan doesn't stall the tool round; over the timeout we return a
  helpful message telling the model to open it via the Documents view.
- Results are truncated at 20 KB — matches the existing MCP tool cap in main.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("read_bridge")

# Filesystem MCP tools whose primary input is a path (or list of paths) and
# whose semantics are "return the file's contents". Anything else — get_file_info,
# list_directory, search_files, edit_file — stays untouched.
INTERCEPTED_TOOLS: set[str] = {
    "filesystem__read_file",
    "filesystem__read_text_file",
    "filesystem__read_media_file",
    "filesystem__read_multiple_files",
    "persephone-fs__read_file",
    "persephone-fs__read_text_file",
    "persephone-fs__read_media_file",
    "persephone-fs__read_multiple_files",
}

# Extensions we handle via IDP extractors (no OCR model needed).
STRUCTURED_EXTS: set[str] = {".pdf", ".docx", ".xlsx", ".xls", ".csv"}

# Extensions we treat as images (need OCR model). Kept broad; TIFF/BMP/GIF
# are rare but harmless to accept.
IMAGE_EXTS: set[str] = {
    ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif",
}

# Extensions we consider "already text" — passthrough to normal MCP call.
# Anything else (unknown extension) is also passed through — we don't want
# to intercept binaries we don't understand.
TEXT_EXTS: set[str] = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".text",
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".swift",
    ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".r", ".jl",
    ".sql", ".graphql", ".proto",
    ".xml", ".svg", ".plist",
    ".vue", ".astro", ".mdx",
    ".gitignore", ".dockerignore", ".editorconfig",
}

# Hard bounds — bigger than these and we tell the model to use the
# Documents view instead of blocking the tool round.
MAX_INTERCEPT_BYTES        = 40 * 1024 * 1024   # 40 MB
MAX_OCR_TIMEOUT_S          = 60.0
MAX_RESULT_CHARS           = 20_000

# In-process cache: (path, size, mtime) → extracted text. Grows unbounded
# per process but each entry is capped at MAX_RESULT_CHARS so the ceiling
# is small; entries are dropped when the file changes.
_cache: dict[tuple[str, int, float], str] = {}
_CACHE_MAX = 128


def _cache_get(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return _cache.get((str(path), st.st_size, st.st_mtime))


def _cache_put(path: Path, text: str) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    if len(_cache) >= _CACHE_MAX:
        # Simple LRU-ish: drop an arbitrary entry
        _cache.pop(next(iter(_cache)))
    _cache[(str(path), st.st_size, st.st_mtime)] = text[:MAX_RESULT_CHARS]


# ── Public entry points ─────────────────────────────────────────────────────
async def maybe_intercept(
    tool_name: str,
    args: dict,
    *,
    resolve_ocr_model: Callable[[], Awaitable[str]],
    ollama_vision_call: Callable[..., Awaitable[str]] | None = None,
) -> str | None:
    """
    Return an extracted-text string to substitute for the MCP tool call, or
    None to let the MCP call proceed normally.

    `resolve_ocr_model` returns the configured OCR model id (may be empty).
    `ollama_vision_call` is idp_engine._ollama_vision_call — injected to
    avoid a circular import.
    """
    if tool_name not in INTERCEPTED_TOOLS:
        return None

    # `read_multiple_files` returns a JSON-ish concat of each result.
    if tool_name.endswith("__read_multiple_files"):
        paths = args.get("paths") or args.get("files") or []
        if not isinstance(paths, list) or not paths:
            return None
        parts: list[str] = []
        for p in paths[:20]:   # sanity cap
            extracted = await _extract_one(
                str(p), resolve_ocr_model=resolve_ocr_model,
                ollama_vision_call=ollama_vision_call,
            )
            if extracted is None:
                # This one is a normal text file — fall back to reading it
                # raw ourselves so the caller still gets a single blob.
                extracted = _read_text_direct(str(p))
                if extracted is None:
                    parts.append(f"### {p}\n[unreadable]\n")
                    continue
            parts.append(f"### {p}\n{extracted}\n")
        joined = "\n".join(parts)
        return _cap(joined)

    # Single-file variants.
    path = args.get("path") or args.get("file")
    if not path or not isinstance(path, str):
        return None

    extracted = await _extract_one(
        path, resolve_ocr_model=resolve_ocr_model,
        ollama_vision_call=ollama_vision_call,
    )
    if extracted is None:
        return None    # let MCP handle plain text
    return _cap(extracted)


async def _extract_one(
    raw_path: str,
    *,
    resolve_ocr_model: Callable[[], Awaitable[str]],
    ollama_vision_call: Callable[..., Awaitable[str]] | None,
) -> str | None:
    """Route by extension. Returns extracted text or None for text passthrough."""
    path = Path(os.path.expanduser(raw_path))
    ext  = path.suffix.lower()

    # Known text extensions → let MCP read them raw.
    if ext in TEXT_EXTS:
        return None
    # Extensionless files: also let MCP handle them (could be READMEs, etc.).
    if not ext:
        return None
    # Unknown extension: passthrough — don't attempt to OCR a random binary.
    if ext not in STRUCTURED_EXTS and ext not in IMAGE_EXTS:
        return None

    if not path.exists():
        return f"[file not found: {raw_path}]"
    if not path.is_file():
        return f"[not a regular file: {raw_path}]"

    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"[cannot stat file: {exc}]"

    if size > MAX_INTERCEPT_BYTES:
        return (
            f"[file too large for inline extraction ({size / 1_048_576:.1f} MB > "
            f"{MAX_INTERCEPT_BYTES // 1_048_576} MB). Upload it via the Documents "
            f"view for full processing.]"
        )

    cached = _cache_get(path)
    if cached is not None:
        log.info("read_bridge cache hit for %s (%d chars)", path, len(cached))
        return cached

    started = time.monotonic()
    try:
        if ext in STRUCTURED_EXTS:
            text = await asyncio.wait_for(
                _extract_structured(path), timeout=MAX_OCR_TIMEOUT_S,
            )
        else:   # IMAGE_EXTS
            text = await asyncio.wait_for(
                _extract_image(
                    path,
                    resolve_ocr_model=resolve_ocr_model,
                    ollama_vision_call=ollama_vision_call,
                ),
                timeout=MAX_OCR_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        return f"[extraction timed out after {MAX_OCR_TIMEOUT_S:.0f}s for {path.name}]"
    except Exception as exc:
        log.exception("read_bridge failed for %s", path)
        return f"[extraction failed: {exc}]"

    elapsed = time.monotonic() - started
    header = (
        f"[Extracted from {path.name} ({size:,} bytes, {elapsed:.1f}s)]\n\n"
    )
    result = header + text
    _cache_put(path, result)
    return result


async def _extract_structured(path: Path) -> str:
    """PDF / DOCX / XLSX / CSV extractor. Runs in a thread so CPU work
    doesn't stall the asyncio loop."""
    import idp_engine as _idp   # local import — avoid circular at module load

    def sync() -> str:
        ext = path.suffix.lower()
        if ext == ".pdf":
            # We don't need the page images, just the text.
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                pages, _imgs = _idp._extract_pdf(path, Path(td))
            return _join_pages(pages)
        if ext == ".docx":
            return _join_pages(_idp._extract_docx(path))
        if ext in (".xlsx", ".xls"):
            pages, _meta = _idp._extract_xlsx(path)
            return _join_pages(pages)
        if ext == ".csv":
            return _join_pages(_idp._extract_csv(path))
        return "[unsupported structured type]"

    return await asyncio.to_thread(sync)


def _join_pages(pages: list[str]) -> str:
    if not pages:
        return "[no text extracted]"
    if len(pages) == 1:
        return pages[0].strip() or "[empty]"
    parts = []
    for i, p in enumerate(pages, 1):
        parts.append(f"--- Page {i} ---\n{p.strip()}")
    return "\n\n".join(parts)


async def _extract_image(
    path: Path,
    *,
    resolve_ocr_model: Callable[[], Awaitable[str]],
    ollama_vision_call: Callable[..., Awaitable[str]] | None,
) -> str:
    """Run OCR on a single image using the user's configured OCR model."""
    if ollama_vision_call is None:
        return "[OCR unavailable — vision runner not wired]"

    model = await resolve_ocr_model()
    if not model:
        return (
            "[No OCR model configured. Open Settings → Models and pick one "
            "for the OCR / Vision category (e.g. minicpm-v, olmOCR, or "
            "frob/unlimited-ocr).]"
        )

    prompt = (
        "Transcribe every word visible in this image verbatim. Preserve "
        "line breaks and paragraph structure. Do NOT add commentary or "
        "descriptions — output ONLY the transcribed text. If the image "
        "has no readable text, output exactly: [no text visible]."
    )
    text = await ollama_vision_call(model, prompt, [str(path)])
    return (text or "").strip() or "[no text extracted]"


def _read_text_direct(raw_path: str) -> str | None:
    """Best-effort text read for extensions we deferred to MCP but got
    called on directly (e.g. from `read_multiple_files`)."""
    try:
        p = Path(os.path.expanduser(raw_path))
        if p.stat().st_size > MAX_INTERCEPT_BYTES:
            return f"[file too large: {p.name}]"
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[cannot read: {exc}]"


def _cap(s: str) -> str:
    if len(s) <= MAX_RESULT_CHARS:
        return s
    truncated = len(s) - MAX_RESULT_CHARS
    return (
        s[:MAX_RESULT_CHARS]
        + f"\n\n[... {truncated} chars truncated — re-read a narrower slice "
          f"or open in Documents view for full text]"
    )
