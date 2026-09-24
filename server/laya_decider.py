"""
Laya decision router — English-only local model for document classification.

Wraps the `convaiinnovations/laya` HF model as a thread-safe lazy singleton.
Provides synchronous classification with automatic idle unload after ~10 min.

Never imports laya or huggingface_hub at module level; lazy imports inside functions
to allow graceful fallback if laya is not installed.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger("laya_decider")

# Configuration constants
LAYA_REPO_ID = "convaiinnovations/laya"
LAYA_ENGLISH_PATTERNS = [
    "model.safetensors",
    "rl_agent_config.json",
    "rl_agent_api.py",
    "rl_common.py",
    "encoder/*.json",
    "encoder/*.bin",
    "tokenizer/*.json",
]
LAYA_TRUNCATE_LENGTH = 6000
LAYA_IDLE_TTL_SECONDS = 600  # 10 minutes

# Thresholds for chat auto-router classification
LAYA_JUDGE_MIN_CONFIDENCE = 0.75
LAYA_SHORT_MIN_CONFIDENCE = 0.9  # 'short' needs higher confidence due to known bias

# Thresholds for document intent and role classification
LAYA_INTENT_MIN_CONFIDENCE = 0.6
LAYA_ROLE_MIN_CONFIDENCE = 0.7

# Document assistant intents: ordered dict mapping intent name to one-line criteria description
INTENTS = OrderedDict([
    ("verify_signature", "Compare or verify a signature, handwriting, or document authenticity against a reference specimen or original"),
    ("identify_person", "Identify who the document is about, find a person's name, determine document ownership or authorship, find an individual"),
    ("summarize", "Summarize content, extract key points, tl;dr, main takeaways, overview of the document"),
    ("extract_data", "Extract structured data from forms, tables, fields, amounts, dates, invoice items, entities, metadata, or billing information"),
    ("translate", "Translate document content into another language or check translation accuracy"),
    ("redact", "Remove, hide, obscure, or black out personal, sensitive, confidential, or private information"),
    ("general_question", "Any other question about the document's content that doesn't fit the above categories"),
])

# Singleton state
_router_instance: Any = None
_router_lock = threading.Lock()
_last_access_time = time.time()
_idle_timer: threading.Timer | None = None
_status_logged_once = False


def status() -> dict[str, Any]:
    """
    Return router status: {available, loaded, device}.

    - available: bool — laya installed and English weights cached locally
    - loaded: bool — router currently in memory
    - device: str|None — 'mps', 'cpu', 'cuda', or None if not loaded
    """
    available = is_available()
    loaded = _router_instance is not None
    device = None

    if loaded:
        try:
            # Infer device from router's device setting
            device = getattr(_router_instance, "device", None)
        except Exception:
            pass

    return {
        "available": available,
        "loaded": loaded,
        "device": device,
    }


def is_available() -> bool:
    """
    Check if Laya is available offline (importable and English weights cached).

    Returns False if:
    - laya package is not installed
    - English model not downloaded to cache
    """
    try:
        import laya  # noqa: F401
    except ImportError:
        return False

    # Check if English model is cached locally
    try:
        from huggingface_hub import scan_cache_dir

        # Scan the HF cache (respects HF_HOME env var)
        cache_info = scan_cache_dir()

        # Check if convaiinnovations/laya repo is cached
        for repo in cache_info.repos:
            if repo.repo_id == "convaiinnovations/laya":
                # Repo is cached; assume weights are available
                # (snapshot_download with allow_patterns ensured English-only)
                return len(repo.revisions) > 0

        return False
    except Exception:
        # If cache check fails, assume not available
        return False


def ensure_downloaded() -> bool:
    """
    Download English-only Laya weights to local HuggingFace cache.

    Uses snapshot_download with allow_patterns to exclude multilingual/typed-decisions.
    Catches and logs errors; returns False on failure but never raises.

    Returns True on success (or if already cached), False on failure.
    """
    if is_available():
        return True

    try:
        from huggingface_hub import snapshot_download

        log.info(
            f"Downloading Laya English model from {LAYA_REPO_ID}… "
            f"(patterns: {', '.join(LAYA_ENGLISH_PATTERNS[:3])}…)"
        )

        snapshot_download(
            LAYA_REPO_ID,
            allow_patterns=LAYA_ENGLISH_PATTERNS,
            repo_type="model",
            revision="main",
        )

        log.info("✓ Laya English model cached successfully.")
        return True
    except Exception as exc:
        log.error(
            f"Failed to download Laya English model: {exc}\n"
            f"  Ensure internet access and laya is installed: pip install laya>=0.3.20"
        )
        return False


def _unload_router() -> None:
    """Unload router from memory and free resources."""
    global _router_instance

    if _router_instance is None:
        return

    try:
        log.debug("Unloading Laya router (idle timeout)…")
        del _router_instance
        _router_instance = None

        # Force garbage collection and free GPU/MPS cache if available
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    except Exception as exc:
        log.warning(f"Error during router unload: {exc}")


def _reset_idle_timer() -> None:
    """Reset the idle unload timer."""
    global _idle_timer, _last_access_time

    _last_access_time = time.time()

    # Cancel existing timer
    if _idle_timer is not None:
        _idle_timer.cancel()

    # Set new timer
    _idle_timer = threading.Timer(
        LAYA_IDLE_TTL_SECONDS,
        _unload_router
    )
    _idle_timer.daemon = True
    _idle_timer.start()


def _get_or_create_router() -> Any | None:
    """
    Get or create the singleton Router instance.

    Returns None if laya is not installed or weights are not cached.
    Never raises; logs errors once.
    """
    global _router_instance, _status_logged_once

    if _router_instance is not None:
        return _router_instance

    # Check availability
    if not is_available():
        if not _status_logged_once:
            log.warning(
                "Laya not available; call ensure_downloaded() first. "
                "Falling back to main router for document classification."
            )
            _status_logged_once = True
        return None

    try:
        from laya import Router
        import torch

        # Auto-detect device
        device = "mps" if torch.backends.mps.is_available() else "cpu"

        log.info(f"Loading Laya router on device={device}…")

        # Create router with English model only (standalone repos to avoid multilingual branches)
        # standalone_repos=True uses separate HF repos (convaiinnovations/laya, not branches)
        # preload=False defers model loading until first predict() call, avoiding attempts to
        # load multilingual/typed-decisions models that aren't in our English-only cache
        _router_instance = Router(
            standalone_repos=True,
            device=device,
            preload=False,
            default="english",
        )

        log.info("✓ Laya router created (English model will load on first predict).")
        _reset_idle_timer()
        return _router_instance
    except Exception as exc:
        if not _status_logged_once:
            log.error(
                f"Failed to load Laya router: {exc}\n"
                f"  Is laya installed? pip install laya>=0.3.20"
            )
            _status_logged_once = True
        return None


def judge_choice(
    text: str,
    question_name: str,
    criteria: dict[str, str],
    instructions: str = "",
) -> dict[str, Any] | None:
    """
    Judge a text prompt against a set of choice criteria using Laya's router.

    Reuses the singleton router to classify text into one of the provided criteria.
    Useful for benchmarking chat auto-router categorization.

    Args:
        text: text prompt to judge (truncated to ~6000 chars if longer)
        question_name: internal name for the question (e.g., "category")
        criteria: dict mapping category names to descriptions
                  e.g., {"trivial": "greeting", "code": "programming task", ...}
        instructions: optional instructions for the judge

    Returns:
        {
            "choice": str,                         # one of the criteria keys
            "confidence": float,                   # [0, 1] for prediction
            "probabilities": {option: float, ...}, # unnormalized per-option scores
        }
        or None if router unavailable or prediction fails.
    """
    global _status_logged_once

    # Reset idle timer on every call
    _reset_idle_timer()

    try:
        # Truncate input
        text_sample = text[:LAYA_TRUNCATE_LENGTH] if text else ""
        if not text_sample:
            return None

        # Get or create router
        with _router_lock:
            router = _get_or_create_router()

        if router is None:
            return None

        # Define the question
        question = {
            question_name: {
                "type": "choice",
                "instructions": instructions or "Classify the text.",
                "criteria": criteria,
            }
        }

        # Predict
        result = router.predict(state=text_sample, questions=question)

        # Extract and reshape
        ans = result.get("answers", {})
        choice_ans = ans.get(question_name, {})

        return {
            "choice": choice_ans.get("choice", "unknown"),
            "confidence": float(choice_ans.get("answer_confidence", 0.0)),
            "probabilities": dict(choice_ans.get("probabilities", {})),
        }
    except Exception as exc:
        if not _status_logged_once:
            log.error(f"Laya judge_choice failed: {exc}")
            _status_logged_once = True
        return None


def decide(sample_text: str) -> dict[str, Any] | None:
    """
    Classify a text sample using Laya's decision router.

    Asks two questions:
    1. "kind" (choice) — document type among:
       - email: email messages, correspondence
       - invoice_or_form: invoices, receipts, forms, financial docs
       - table_heavy: spreadsheets, tables, structured data
       - long_report_or_contract: long documents, contracts, reports
       - plain_note: simple text notes, memos, unstructured writing

    2. "complexity" (score) — 3-level complexity:
       - 0: simple/straightforward
       - 1: moderate
       - 2: highly complex

    Args:
        sample_text: text to classify (truncated to ~6000 chars if longer)

    Returns:
        {
            "kind": str,                           # one of the 5 options
            "confidence": float,                   # [0, 1] for kind prediction
            "probabilities": {option: float, ...}, # unnormalized per-option scores
            "complexity": int,                     # 0, 1, or 2 (rounded from score)
        }
        or None if router unavailable or prediction fails.

    Never raises; logs errors once per session.
    """
    global _status_logged_once

    # Reset idle timer on every call
    _reset_idle_timer()

    try:
        # Truncate input
        text = sample_text[:LAYA_TRUNCATE_LENGTH] if sample_text else ""
        if not text:
            return None

        # Get or create router
        with _router_lock:
            router = _get_or_create_router()

        if router is None:
            return None

        # Define questions
        questions = {
            "kind": {
                "type": "choice",
                "instructions": "What is the primary nature/type of this document?",
                "criteria": {
                    "email": "email message, correspondence, sender/recipient, subject line",
                    "invoice_or_form": (
                        "invoice, receipt, bill, financial form, structured payment/billing document"
                    ),
                    "table_heavy": "spreadsheet, table, CSV data, columnar layout, structured rows/columns",
                    "long_report_or_contract": (
                        "long document, legal contract, report, multi-page text, formal document"
                    ),
                    "plain_note": "simple text note, memo, unstructured writing, brief text",
                },
            },
            "complexity": {
                "type": "score",
                "instructions": "How complex or detailed is this document's content?",
                "criteria": [
                    "simple/straightforward",  # 0
                    "moderate",                # 1
                    "highly complex",          # 2
                ],
            },
        }

        # Predict
        result = router.predict(state=text, questions=questions)

        # Extract and reshape
        ans = result.get("answers", {})
        kind_ans = ans.get("kind", {})
        complexity_ans = ans.get("complexity", {})

        return {
            "kind": kind_ans.get("choice", "unknown"),
            "confidence": float(kind_ans.get("answer_confidence", 0.0)),
            "probabilities": dict(kind_ans.get("probabilities", {})),
            "complexity": round(float(complexity_ans.get("score", 1.0))),
        }
    except Exception as exc:
        if not _status_logged_once:
            log.error(f"Laya predict failed: {exc}")
            _status_logged_once = True
        return None


def judge_chat_category(
    text: str,
    criteria: dict[str, str],
    min_confidence: float = LAYA_JUDGE_MIN_CONFIDENCE,
) -> str | None:
    """
    Judge a chat message text against a set of category criteria using Laya.

    Returns the chosen category ONLY if:
    - It is a valid key in `criteria`
    - Confidence >= min_confidence
    - For 'short' category: confidence >= LAYA_SHORT_MIN_CONFIDENCE (stricter due to known bias)

    Returns None on:
    - Low confidence
    - Invalid category
    - Laya unavailable
    - Any exception

    Never raises. Safe for async contexts via asyncio.to_thread().

    Args:
        text: chat message to judge (truncated to ~6000 chars if longer)
        criteria: dict mapping category names to descriptions
                  e.g., {"trivial": "greeting", "code": "programming", ...}
        min_confidence: minimum confidence threshold [0, 1], default 0.75

    Returns:
        Category name (str) if confident and valid, None otherwise.
    """
    if not text or not criteria:
        return None

    try:
        # Use judge_choice to get prediction with confidences
        result = judge_choice(
            text=text,
            question_name="category",
            criteria=criteria,
            instructions="Classify the user message into ONE category.",
        )

        if result is None:
            return None

        choice = result.get("choice", "").strip().lower()
        confidence = float(result.get("confidence", 0.0))

        # Validate choice is in criteria
        if choice not in criteria:
            return None

        # Apply confidence thresholds
        if choice == "short":
            # 'short' category has known bias; require higher confidence
            if confidence < LAYA_SHORT_MIN_CONFIDENCE:
                return None
        else:
            # Standard threshold for other categories
            if confidence < min_confidence:
                return None

        return choice
    except Exception as exc:
        log.debug(f"judge_chat_category failed: {exc}")
        return None


def decide_intent(message: str, files: list[dict]) -> dict | None:
    """
    Classify a user request into one of the document assistant intents.

    Uses Laya to judge the user message and attached files against the INTENTS criteria.
    The intents are: verify_signature, identify_person, summarize, extract_data, translate,
    redact, or general_question.

    Args:
        message: user request text (truncated to ~1500 chars internally)
        files: list of attached file dicts, each with keys:
               - "name": str (filename)
               - "kind": str (pdf, docx, image, email, text, etc.)
               - "chars": int (character count or size estimate, optional)
               - "snippet": str (first ~300 chars of text content, optional)

    Returns:
        {
            "intent": str,                              # one of the INTENTS keys
            "confidence": float,                        # [0, 1] answer_confidence from Laya
            "probabilities": {intent: float, ...},     # unnormalized per-intent scores
        }
        or None if Laya unavailable, message empty, or prediction fails.

    Never raises; returns None on any failure.
    """
    # Validate input
    if not message or not isinstance(message, str):
        return None

    try:
        # Truncate message to ~1500 chars
        msg_truncated = message[:1500] if message else ""
        if not msg_truncated:
            return None

        # Build state text: message + file list
        state_parts = [f"USER REQUEST: {msg_truncated}"]

        if files:
            state_parts.append("ATTACHED FILES:")
            for f in files:
                name = f.get("name", "unknown")
                kind = f.get("kind", "file")
                state_parts.append(f"  - {name} ({kind})")

        state_text = "\n".join(state_parts)

        # Use judge_choice to classify the intent
        # Convert INTENTS OrderedDict to criteria dict: name -> description
        criteria = dict(INTENTS)

        result = judge_choice(
            text=state_text,
            question_name="intent",
            criteria=criteria,
            instructions="Classify the user's request into ONE intent. Consider the message text and attached file types.",
        )

        if result is None:
            return None

        # Ensure intent is valid; default to general_question if not found
        intent = result.get("choice", "general_question")
        if intent not in criteria:
            intent = "general_question"

        return {
            "intent": intent,
            "confidence": float(result.get("confidence", 0.0)),
            "probabilities": dict(result.get("probabilities", {})),
        }
    except Exception as exc:
        log.debug(f"Laya decide_intent failed: {exc}")
        return None


def decide_file_role(message: str, file: dict) -> dict | None:
    """
    Classify the role of a single file in the user's request.

    Determines whether a file is the main document to be examined (subject_document)
    or a reference/specimen used only for comparison (reference_specimen).

    Args:
        message: user request text (truncated to ~1500 chars internally)
        file: file dict with keys:
              - "name": str (filename)
              - "kind": str (pdf, docx, image, email, text, etc.)
              - "snippet": str (first ~300 chars of text content, optional)

    Returns:
        {
            "role": str,                                # "subject_document" or "reference_specimen"
            "confidence": float,                        # [0, 1] answer_confidence from Laya
            "probabilities": {role: float, ...},       # unnormalized per-role scores
        }
        or None if Laya unavailable, message/file empty, or prediction fails.

    Never raises; returns None on any failure.
    """
    # Validate input
    if not message or not isinstance(message, str):
        return None
    if not file or not isinstance(file, dict):
        return None

    try:
        # Truncate message to ~1500 chars
        msg_truncated = message[:1500] if message else ""
        if not msg_truncated:
            return None

        # Build state text: message + file info + snippet
        state_parts = [f"USER REQUEST: {msg_truncated}"]

        name = file.get("name", "unknown")
        kind = file.get("kind", "file")
        state_parts.append(f"FILE: {name} ({kind})")

        # Add snippet if available
        snippet = file.get("snippet", "")
        if snippet:
            first_chars = snippet[:300]
            state_parts.append(f"FIRST TEXT: {first_chars}")

        state_text = "\n".join(state_parts)

        # Define the role criteria
        role_criteria = {
            "subject_document": (
                "the main document the user wants examined, analyzed, or worked on; "
                "the primary focus of the request"
            ),
            "reference_specimen": (
                "a reference, specimen, sample, template, ID card, signature card, or original "
                "used only for comparison or verification; not the main document"
            ),
        }

        # Use judge_choice to classify the file role
        result = judge_choice(
            text=state_text,
            question_name="role",
            criteria=role_criteria,
            instructions="Determine if this file is the main document being examined or a reference specimen used for comparison.",
        )

        if result is None:
            return None

        # Ensure role is valid; default to subject_document if not found
        role = result.get("choice", "subject_document")
        if role not in role_criteria:
            role = "subject_document"

        return {
            "role": role,
            "confidence": float(result.get("confidence", 0.0)),
            "probabilities": dict(result.get("probabilities", {})),
        }
    except Exception as exc:
        log.debug(f"Laya decide_file_role failed: {exc}")
        return None
