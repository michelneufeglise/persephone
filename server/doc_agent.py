"""
Document agent orchestrator for the Documents tab.

A decision model (Laya: non-generative typed classifier) decides the intent;
the orchestrator runs steps and STREAMS EVENTS that the UI renders as tiles
popping up top-to-bottom in a right-hand panel.

Never imports main.py; uses injected hooks for all integrations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, AsyncIterator, Callable, Optional

import table_query as _tq

log = logging.getLogger("doc_agent")

# Prefer idp_engine.needs_ocr (single source of truth); fall back to the same
# rule locally when it's unavailable (import-time, hook-free).
try:  # pragma: no cover - trivial import guard
    from idp_engine import needs_ocr as _idp_needs_ocr  # type: ignore
except (ImportError, AttributeError):  # pragma: no cover
    _idp_needs_ocr = None


def _needs_ocr(doc: Any) -> bool:
    """True when the document has page images but (almost) no text layer —
    fewer than 40 chars per page — so a scan with a tiny text layer (a stamp,
    page numbers) still gets OCR'd."""
    if _idp_needs_ocr is not None:
        try:
            return bool(_idp_needs_ocr(doc))
        except Exception:
            pass
    images = getattr(doc, "page_images", None)
    if not images:
        return False
    meta = getattr(doc, "meta", None) or {}
    if isinstance(meta, dict) and meta.get("last_ocr_at"):
        return False
    pages = getattr(doc, "pages", 0) or 0
    text = getattr(doc, "text", "") or ""
    return len(text.strip()) < 40 * max(1, pages)

# ── Utility functions ──────────────────────────────────────────────────────

def _normalize_for_dedup(text: str) -> str:
    """Normalize text for deduplication (case/accent-insensitive)."""
    import unicodedata
    if not text:
        return ""
    # Remove accents
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Lowercase
    return text.lower().strip()

# ── Configuration ──────────────────────────────────────────────────────────
LAYA_INTENT_MIN_CONFIDENCE = 0.6
LAYA_ROLE_MIN_CONFIDENCE = 0.7
LAYA_DOC_KIND_MIN_CONFIDENCE = 0.65
VERIFY_SIGNATURE_STRONG_CONFIDENCE = 0.85
IDENTIFY_PERSON_STRONG_CONFIDENCE = 0.9

INTENTS = {
    "verify_signature": "Compare or verify a signature, handwriting, or document authenticity against a reference specimen or original",
    "identify_person": "Asks WHO a person is / whose document it is / which person it is about / the name of the person — the identity itself",
    "summarize": "Summarize content, extract key points, tl;dr, main takeaways, overview of the document",
    "extract_data": "Extract structured data from forms, tables, fields, amounts, dates, invoice items, entities, metadata, or billing information",
    "translate": "Translate document content into another language or check translation accuracy",
    "redact": "Remove, hide, obscure, or black out personal, sensitive, confidential, or private information",
    "general_question": "Asks for a specific fact or detail from the document(s), e.g. a date (date of birth, due date), an amount, an address, an age, a status, or what the document says about someone/something",
    "graph_query": "Asks what is known ACROSS the knowledge base / all documents / previous conversations about a person, organization or topic, or which documents mention something — not about one specific attached document",
    "cross_reference": "Asks to compare / cross-reference / check consistency between two or more documents, or to verify one document's claims against another (e.g. CV vs company registry, invoice vs contract)",
}

# Prompts
SIGNATURE_PROMPT = """You have been provided with one or more images for signature comparison.

If multiple images are provided, they are ordered as:
- First section(s): REFERENCE SPECIMEN (the known-good signature or handwriting to compare against)
- Remaining section(s): DOCUMENT UNDER EXAMINATION (the signature or handwriting in question)

If a single composite image is provided, it will have clear labels: 'REFERENCE SPECIMEN' for the top section and 'DOCUMENT UNDER EXAMINATION' for the lower section(s).

Your task:
1. Locate the signature(s) in the document.
2. Compare with the reference specimen on: stroke shape, letterforms, slant, proportions, spacing, pen pressure/line quality, flourishes.
3. List observed similarities and differences with specifics.
4. End with 'Visual similarity: low / moderate / high' and 'Confidence in this assessment: low/medium/high'.
5. REQUIRED closing caveat: "This is an automated visual comparison by an AI model, NOT a forensic document examination, and must not be used as proof of authenticity or forgery."

Do NOT declare the signature 'genuine' or 'forged'."""

IDENTIFY_PERSON_PROMPT = """Identify the person(s) the document is about, issued to, or signed by.
For each person found:
- Give their name
- State their role (e.g., holder, sender, signer, recipient)
- Quote the exact supporting text

Refer to people by their name; do not assume gender or use he/she.

If not found, say so — do not guess."""

SUMMARIZE_PROMPT = """Summarize the document content. Provide:
- Main points (3-5 bullet points)
- Key takeaways or conclusions
- Any important dates, amounts, or decisions

Be concise and focus on the essentials."""

EXTRACT_DATA_PROMPT = """Extract structured data from the document. Return:
- A list of all fields, amounts, dates, names, or entities found
- Organized by category (e.g., "Dates", "Amounts", "Names", "Contact Info")
- Each entry on its own line with context

If tables are present, preserve their structure. If no data to extract, say so."""

TRANSLATE_PROMPT = """Translate the document content. Target language: {language}

Return only the translation, no commentary. If translation is impossible or the document is already in the target language, say so."""

REDACT_PROMPT = """Review the document and:
1. List all personally identifiable information (PII) and sensitive content to redact
2. Return the text with those sections removed or obscured (e.g., [REDACTED])
3. Note what was redacted and why

Redactable items: names, addresses, phone numbers, emails, SSN, financial account numbers, dates of birth, medical info, legal case numbers, etc."""

GENERAL_QUESTION_PROMPT = """Answer the user's question directly and concisely in the first sentence.
For example, if asked "What is her date of birth?", start with "Her date of birth is 14 March 1985."

Then:
- Cite the supporting text and which document it comes from
- Answer ONLY from the DOCUMENT CONTENT section. Use the prior conversation only to resolve references like 'her' or 'this'; never take facts from it.
- Be clear if the answer is not found in the documents — do not guess or speculate"""

GRAPH_QUERY_PROMPT = """You are summarising what the local knowledge store knows about the subject of the user's question.

FACTS (subject —relation→ object, with source and confidence) are listed below.

Write a concise answer in 3–6 sentences or a short bullet list grouped as:
- Identity & role
- Documents
- Online presence

Do NOT copy the facts list verbatim and do NOT repeat any line. Distinguish a *likely* LinkedIn profile from mere *candidates*. Cite the document names or website hosts given as "source" in parentheses (e.g. "(cv.pdf)", "(linkedin.com)"); never cite internal labels such as doc_agent or web_lookup. If something isn't in the facts, say it's unknown. Refer to people by their name; do not assume gender or use he/she.

Facts:
{document_content}"""

CROSS_REFERENCE_PROMPT = """Compare the documents field by field. Output:
(1) a markdown table with columns Field | {doc_columns} | Result, with rows for Person name, Role/position, Company / employer, Business activities vs experience, Relevant dates, Location — Result is ✅ match, ⚠️ partial, ❌ mismatch or — not stated;
(2) a short answer to the user's actual question;
(3) an 'Authenticity & caveats' section noting anything that limits trust in a document (e.g. 'DEMO', 'fictitious', watermarks, missing official identifiers, 'not issued by …') — if a document says it is a demo/fictitious, say plainly that it cannot prove the company is real;
(4) one-line conclusion.
Refer to people by name; do not assume gender. Only use facts from the documents."""
# Marker that identifies the cross-reference task in a prompt (tests / debugging)
CROSS_REFERENCE_MARKER = "Compare the documents field by field."

# ── Data models ────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """A single decision made by Laya or rules."""
    id: str  # e.g., "intent", "role-doc1", "ocr_needed"
    label: str  # Human-readable label
    value: str  # The decision value
    confidence: Optional[float] = None  # [0, 1] or None if not applicable
    source: str = "laya"  # "laya" | "rules" | "probe" | "config"
    note: Optional[str] = None  # Additional context
    probabilities: Optional[dict[str, float]] = None  # Per-choice scores

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Tile:
    """A UI tile representing a processing step."""
    id: str  # Unique identifier (e.g., "laya", "extract-doc1", "answer")
    kind: str  # "laya" | "extract" | "ocr" | "llm" | "vision" | "web" | "planner" | "table"
    title: str
    status: str  # "pending" | "running" | "done" | "skipped" | "error"
    model: Optional[str] = None  # Model name if applicable
    model_info: Optional[dict[str, Any]] = None  # Model metadata
    detail: str = ""  # Explanation or status message
    decisions: list[Decision] = field(default_factory=list)  # For laya tile only
    items: list[dict] = field(default_factory=list)  # For tiles with sub-items (queries, results, etc.)
    started_ms: Optional[int] = None  # Epoch ms when started
    ms: Optional[int] = None  # Duration in ms when finished
    output_preview: Optional[str] = None  # First 300 chars of output
    doc: Optional[dict[str, str]] = None  # {"doc_id": str, "name": str} or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "model": self.model,
            "model_info": self.model_info,
            "detail": self.detail,
            "decisions": [d.to_dict() for d in self.decisions],
            "items": self.items,
            "started_ms": self.started_ms,
            "ms": self.ms,
            "output_preview": self.output_preview,
            "doc": self.doc,
        }


class RunCollector:
    """Fold the event stream from run_agent into a persistable result."""

    def __init__(self):
        self.content = ""
        self.thinking = ""
        self.tiles: dict[str, dict[str, Any]] = {}  # id -> tile dict
        self.tiles_list: list[str] = []  # ordered list of tile ids for first-appearance order
        self.model: Optional[str] = None
        self.intent: Optional[str] = None
        self.error: Optional[str] = None
        self.done = False
        self.stats: Optional[dict[str, Any]] = None

    def feed(self, event: dict) -> None:
        """Process a single event from run_agent."""
        if "content" in event:
            self.content += event["content"]
        elif "thinking" in event:
            self.thinking += event["thinking"]
        elif "tile" in event:
            tile_dict = event["tile"]
            tile_id = tile_dict.get("id")
            if tile_id:
                # Track first appearance order
                if tile_id not in self.tiles_list:
                    self.tiles_list.append(tile_id)
                # Update in place
                self.tiles[tile_id] = tile_dict
                # Extract model from answer tile
                if tile_id == "answer" and tile_dict.get("model"):
                    self.model = tile_dict["model"]
        elif "done" in event and event["done"]:
            self.done = True
            self.stats = event.get("stats")
            # Extract intent from stats if available
            if self.stats and "intent" in self.stats:
                self.intent = self.stats["intent"]
        elif "error" in event:
            self.error = event["error"]

    def to_meta(self, run_id: str, doc_ids: list[str]) -> dict[str, Any]:
        """Convert collected result to a persistable metadata dict."""
        # Build tiles list in first-appearance order
        tiles_list = [self.tiles[tid] for tid in self.tiles_list if tid in self.tiles]

        return {
            "kind": "doc_run",
            "run_id": run_id,
            "intent": self.intent,
            "doc_ids": doc_ids,
            "tiles": tiles_list,
            "error": self.error,
            "stats": self.stats,
        }


@dataclass
class AgentHooks:
    """All external dependencies injected by the orchestrator."""
    # Sync/blocking
    get_doc: Callable[[str], Any]  # Returns Document|None
    laya_intent: Callable[[str, list[dict]], Optional[dict]]  # Sync, blocking
    laya_role: Callable[[str, dict], Optional[dict]]  # Sync, blocking
    laya_doc_kind: Callable[[str], Optional[dict]]  # Sync, blocking
    laya_info: Callable[[], dict]  # Returns {name, version, device, available}
    page_image_paths: Callable[[Any, Optional[list[int]], int], list[str]]  # (doc, pages, dpi) -> paths

    # Async
    resolve_model: Callable[[str], Any]  # (category: ocr|docs|text|tables|handwriting) -> str
    resolve_text_model: Callable[[Any, str], Any]  # (doc, category) -> str
    pick_vision_model: Callable[[], Any]  # () -> str|None (for backwards compat; use vision_candidates)
    vision_candidates: Callable[[], Any]  # () -> awaitable list[str] of vision model candidates
    model_info: Callable[[str], Any]  # (model_name) -> dict|None
    run_ocr: Callable[[Any, str], Any]  # (doc, model) -> str
    stream_llm: Callable[[str, str, bool], Any]  # (model, prompt, think) -> AsyncIterator
    vision_call: Callable[[str, str, list[str], list[str]], Any]  # (model, prompt, reference_paths, subject_paths) -> str
    mark_vision_failed: Callable[[str, str], None]  # (model, error_text) -> None (sync)
    # Optional fields with defaults
    resolve_text_model_info: Optional[Callable[[Any, str], Any]] = None  # (doc, category) -> {"model": str, "configured": str|None, "reason": str|None}
    laya_web: Optional[Callable[[str], Any]] = None  # (message) -> {"value": "yes"|"no", "confidence": float} or None; sync/blocking
    web_search: Optional[Callable[[str], Any]] = None  # (query) -> awaitable list[dict]
    fetch_page: Optional[Callable[[str], Any]] = None  # (url) -> awaitable str
    pick_tool_model: Optional[Callable[[], Any]] = None  # () -> awaitable str|None
    chat_tools: Optional[Callable[[str, list, list], Any]] = None  # (model, messages, tools) -> awaitable dict
    kg_search: Optional[Callable[[str], Any]] = None  # (text) -> awaitable list[dict] of entities
    kg_neighborhood: Optional[Callable[[str], Any]] = None  # (entity_id) -> awaitable dict with entities/relations
    kg_ingest: Optional[Callable[..., Any]] = None  # (conversation_id, run_id, intent, ...) -> awaitable dict
    kg_known_profiles: Optional[Callable[[str], Any]] = None  # (person_name) -> awaitable list[dict] of stored profiles (web lookup cache when search is blocked)
    search_notes: Optional[Callable[[], list]] = None  # () -> [{"kind": "paced", "seconds": s}] pacing events since last call (sync)
    retrieve_chunks: Optional[Callable[[str, str, int], Any]] = None  # (doc_id, query, k) -> awaitable list[str] (RAG over the doc)
    sheet_frames: Optional[Callable[[Any], Any]] = None  # (doc) -> list[dict] parsed sheets (sheets.py) | None; sync/blocking
    now_ms: Callable[[], int] = field(default_factory=lambda: lambda: int(time.time() * 1000))

# ── Pure functions for intent & role resolution ─────────────────────────────

def _kw_re(kw: str) -> str:
    """Word-bounded regex for a keyword. A trailing '*' makes it a prefix
    ("samenvat*" matches "samenvatting"); multi-word phrases work as-is."""
    kw = kw.lower()
    if kw.endswith("*"):
        return r"(?<!\w)" + re.escape(kw[:-1])
    return r"(?<!\w)" + re.escape(kw) + r"(?!\w)"


def _kw_hits(msg_lower: str, kws: list[str]) -> list[str]:
    """Keywords that occur in msg_lower as whole words/phrases."""
    return [kw.rstrip("*") for kw in kws if re.search(_kw_re(kw), msg_lower)]


# Words that make a request explicitly about signatures (verify/compare/match
# alone are NOT enough — "verify online whether this person exists").
SIGNATURE_WORDS = [
    "signature", "signatures", "signed", "handtekening", "handtekeningen",
    "signatuur", "paraaf", "compare signature*",
]
SIGNATURE_VERBS = ["verify", "authentic*", "match", "compare", "genuine", "forged", "forgery"]

# Markers that a question spans the whole knowledge store rather than the
# attached document(s).
CROSS_SCOPE_MARKERS = [
    "across", "all documents", "all my documents", "all the documents",
    "knowledge base", "knowledge graph", "knowledge store", "in my documents",
    "in all documents", "in all my documents", "my files", "all my files",
    "previous conversations",
]
GRAPH_QUERY_PHRASES = [
    "what do we know about", "what do i know about", "what do you know about",
    "which documents mention", "which document mentions", "which documents mentions",
    "everything about", "all information about",
]

# "name of the company" etc. asks for an organisation, not a person
_NON_PERSON_NAME_RE = re.compile(
    r"\b(?:name|naam|nom)\s+(?:of|van|de|du)\s+(?:the\s+|de\s+|het\s+|la\s+|le\s+|l')?"
    r"(?:company|companies|organi[sz]ation|organi[sz]ations|firm|business|employer|bank|school|"
    r"university|product|file|document|project|street|city|town|shop|store|bedrijf|organisatie|"
    r"firma|entreprise|soci[ée]t[ée]|vendor|supplier|leverancier|client|customer)\b"
    r"|\b(?:company|organi[sz]ation|firm|business|employer|vendor|supplier|file|product)(?:'s)?\s+name\b"
)
_NAME_KWS = {"name of", "the name", "naam van", "de naam", "nom de"}

# A single-fact question ("what is the total amount?", "how much…") is a
# general_question, not a structured extraction.
_QUESTION_START_RE = re.compile(
    r"^\s*(?:what|what's|whats|when|how much|how many|how old|which|where|is there|is the|"
    r"wat|wanneer|hoeveel|welke|waar|quel|quelle|quand|combien|was|wie viel)\b"
)
SINGLE_FACT_NOUNS = [
    "total", "amount", "date", "fee", "fees", "price", "cost", "costs", "due", "balance",
    "vat", "btw", "iban", "sum", "number", "deadline", "rate", "salary", "bedrag", "totaal",
    "datum", "prijs", "kosten", "montant", "prix",
]
STRONG_EXTRACT_KWS = [
    # "extract" itself is handled by extract_verb_hits (only the VERB counts:
    # "registry extract" / "KvK extract" are documents, not requests)
    "list all", "list every", "all fields", "all the fields", "every field",
    "table of", "as a table", "in a table", "into a table", "tables", "all amounts", "all dates",
    "all the amounts", "all the dates", "all data", "all the data", "structured data",
    "extraheer*", "extraire",
]
WEAK_EXTRACT_KWS = ["table", "amount", "amounts", "invoice number", "dates", "fields", "data"]

# ── "extract": verb (a request) vs noun (a registry/KvK extract document) ──
_EXTRACT_WORD_RE = re.compile(r"(?<!\w)extract(?:s|ed|ing)?(?!\w)")
# "…registry extract", "this extract", "KvK-extract" → noun
_EXTRACT_NOUN_BEFORE_RE = re.compile(
    r"(?:(?<!\w)(?:a|an|the|this|that|these|those|my|your|our|his|her|their|its|registry|register|"
    r"company|companies|kvk|coc|commerce|trade|bank|official|handelsregister|business|chamber)"
    r"[\s-]+|[\w-]+-)$"
)
# "extract from the chamber of commerce / KvK / trade register" → noun
_EXTRACT_NOUN_AFTER_RE = re.compile(
    r"^\s+(?:from|of|van|uit)\s+(?:the\s+|de\s+|het\s+|a\s+|an\s+)?(?:dutch\s+)?"
    r"(?:chamber\s+of\s+commerce|kvk|kamer\s+van\s+koophandel|trade\s+regist\w*|company\s+regist\w*|"
    r"commercial\s+regist\w*|business\s+regist\w*|handelsregister|registry|register)\b"
)
# "extract the / all / key / data …" → verb
_EXTRACT_VERB_AFTER_RE = re.compile(
    r"^\s+(?:the|all|every|each|any|some|key|main|important|relevant|structured|data|fields?|information|"
    r"info|details?|names?|dates?|amounts?|values?|text|tables?|entities|line\s+items|items|numbers?|"
    r"totals?|contacts?|addresses?|emails?|phone|it|them|everything|this|these|those|out)\b"
)
# "please extract", "can you extract", sentence start … → verb
_EXTRACT_VERB_BEFORE_RE = re.compile(
    r"(?:^|[.!?;:\n]\s*|(?<!\w)(?:please|pls|kindly|can\s+you|could\s+you|would\s+you|will\s+you|can\s+u|"
    r"to|and|then|also|now|just|and\s+then|help\s+me)\s+)$"
)


def extract_verb_hits(msg_lower: str) -> list[str]:
    """
    Occurrences of "extract" used as a VERB ("extract the totals", "please
    extract all fields", message-initial "Extract …"). The noun — "registry
    extract", "company extract", "KvK extract", "extract from the chamber of
    commerce" — is a document, not a request, and returns nothing.
    """
    hits: list[str] = []
    for m in _EXTRACT_WORD_RE.finditer(msg_lower):
        before = msg_lower[:m.start()]
        after = msg_lower[m.end():]
        if _EXTRACT_NOUN_AFTER_RE.match(after):
            continue
        if _EXTRACT_NOUN_BEFORE_RE.search(before) and not _EXTRACT_VERB_BEFORE_RE.search(before):
            continue
        if _EXTRACT_VERB_AFTER_RE.match(after) or _EXTRACT_VERB_BEFORE_RE.search(before):
            hits.append(m.group(0))
    return hits


# ── cross_reference: compare / check one document against another ──────────
CROSS_REFERENCE_PATTERNS = [
    r"(?<!\w)cross[\s-]?referenc\w*",
    r"(?<!\w)cross[\s-]?check\w*",
    r"(?<!\w)compar(?:e|es|ed|ing)(?!\w)(?:\W+\w+){0,10}?\W+(?:with|to|against)(?!\w)",
    r"(?<!\w)comparison(?!\w)",
    r"(?<!\w)match(?:es)?\s+(?:the|with)(?!\w)",
    r"(?<!\w)(?:in)?consisten(?:t|cy|cies)(?!\w)",
    r"(?<!\w)(?:check|verify)(?!\w)(?:\W+\w+){0,6}?\W+against(?!\w)",
    r"(?<!\w)correspond(?:s|ing)?(?!\w)",
    r"(?<!\w)discrepanc(?:y|ies)(?!\w)",
    r"(?<!\w)mismatch(?:es|ed)?(?!\w)",
    r"(?<!\w)vergelijk\w*",
    r"(?<!\w)controleer(?!\w)(?:\W+\w+){0,10}?\W+tegen(?!\w)",
    r"(?<!\w)kom(?:t|en)(?!\w)(?:\W+\w+){0,5}?\W+overeen(?!\w)",
]
_CROSS_REFERENCE_RES = [re.compile(p) for p in CROSS_REFERENCE_PATTERNS]


def cross_reference_hits(msg_lower: str) -> list[str]:
    """Phrases in the (lower-cased) message that ask to compare documents."""
    hits: list[str] = []
    for rx in _CROSS_REFERENCE_RES:
        m = rx.search(msg_lower)
        if m:
            hits.append(re.sub(r"\s+", " ", m.group(0)).strip()[:40])
    return hits


def rules_intent(message: str, files: list[dict]) -> tuple[Optional[str], list[str]]:
    """
    Multilingual-ish keyword rules for intent detection (word-bounded).

    Returns (intent_name, matched_keywords) or (None, []) if no match.
    `files` is the list of attached files ({"name": ...}); graph_query is only
    chosen when nothing is attached or the request has a cross-scope marker.
    """
    # Handle empty or no-instruction messages
    if not message or not message.strip():
        return "summarize", ["no explicit instruction"]

    msg_lower = message.lower()
    no_instruction_patterns = [
        r"look at the attached",
        r"take a look at the attached",
        r"bekijk de bijlage",
        r"regarde le document",
    ]
    for pattern in no_instruction_patterns:
        if re.search(pattern, msg_lower):
            return "summarize", ["no explicit instruction"]

    files = files or []

    # verify_signature: an explicit signature word + a reference-like file
    sig_hits = _kw_hits(msg_lower, SIGNATURE_WORDS)
    if "signed" in sig_hits and re.search(r"\bwho\s+(?:has\s+)?signed\b", msg_lower):
        sig_hits.remove("signed")  # "who signed …?" asks for a person
    if sig_hits:
        matched_kws = sig_hits + _kw_hits(msg_lower, SIGNATURE_VERBS)
        for f in files:
            fname = (f.get("name") or "").lower()
            if any(ref_kw in fname for ref_kw in ["reference", "specimen", "sample", "card", "template"]):
                return "verify_signature", matched_kws

    # cross_reference: compare / check documents against each other — only
    # with two or more documents attached and no explicit signature request
    # (a signature comparison stays with verify_signature / Laya).
    if len(files) >= 2 and not sig_hits:
        xref_hits = cross_reference_hits(msg_lower)
        if xref_hits:
            return "cross_reference", xref_hits

    # graph_query: what is known across all documents / the knowledge base.
    # With attachments, only an explicit cross-scope marker makes it a graph
    # query ("tell me everything about this invoice" is about the invoice).
    scope_hits = _kw_hits(msg_lower, CROSS_SCOPE_MARKERS)
    if "across" in scope_hits and any(
        (f.get("kind") == "xlsx") or (f.get("name") or "").lower().endswith(SHEET_EXTS) for f in files
    ):
        # "total across Q1..Q4" on a spreadsheet is about its columns
        scope_hits.remove("across")
    graph_hits = _kw_hits(msg_lower, GRAPH_QUERY_PHRASES) + scope_hits
    if graph_hits and (not files or scope_hits):
        return "graph_query", graph_hits

    # identify_person: must ask WHO/WHOSE/which person/the name — NOT a fact lookup
    id_person_kws = [
        "who is", "who are", "who signed", "who wrote", "which person", "which people", "whose",
        "name of", "person's name", "wie zijn", "qui sont",
        "persons name", "the name", "signer", "signatory",
        "wie is", "wie heeft", "naam van", "de naam", "ondertekenaar", "ondertekener",
        "qui est", "nom de", "find person", "identify",
    ]
    id_person_matches = _kw_hits(msg_lower, id_person_kws)
    if id_person_matches and _NON_PERSON_NAME_RE.search(msg_lower):
        id_person_matches = [kw for kw in id_person_matches if kw not in _NAME_KWS]
    if id_person_matches:
        return "identify_person", id_person_matches

    # summarize
    summarize_kws = ["summarize", "summarise", "summary", "tl;dr", "tl dr", "key points",
                     "samenvat*", "résum*", "overview"]
    summarize_matches = _kw_hits(msg_lower, summarize_kws)
    if summarize_matches:
        return "summarize", summarize_matches

    # translate (before extract so "translate the table" is a translation)
    translate_kws = ["translate*", "vertaal*", "traduis*", "traduire", "translation", "übersetz*"]
    translate_matches = _kw_hits(msg_lower, translate_kws)
    if translate_matches:
        return "translate", translate_matches

    # extract_data: explicit extraction requests
    strong_extract = extract_verb_hits(msg_lower) + _kw_hits(msg_lower, STRONG_EXTRACT_KWS)
    if strong_extract:
        return "extract_data", strong_extract

    fact_lookup_kws = [
        "date of birth", "born", "birthday", "geboortedatum",
        "address", "adres", "age", "how old",
        "nationality", "when was", "what is his", "what is her", "what is their",
        "what does it say", "due date", "telephone", "phone", "email",
    ]
    fact_matches = _kw_hits(msg_lower, fact_lookup_kws)

    # A single-fact question about an amount/date/… is a general question
    if _QUESTION_START_RE.search(msg_lower):
        fact_nouns = _kw_hits(msg_lower, SINGLE_FACT_NOUNS)
        if fact_nouns:
            return "general_question", fact_matches + [n for n in fact_nouns if n not in fact_matches]

    weak_extract = _kw_hits(msg_lower, WEAK_EXTRACT_KWS)
    if weak_extract:
        return "extract_data", weak_extract

    # redact
    redact_kws = ["redact*", "anonymize", "anonymise", "anonymi*", "remove personal", "hide",
                  "obscure", "black out", "anonimiseer*"]
    redact_matches = _kw_hits(msg_lower, redact_kws)
    if redact_matches:
        return "redact", redact_matches

    # general_question: fact lookup keywords (checked LAST so more specific
    # keywords take precedence)
    if fact_matches:
        return "general_question", fact_matches

    return None, []


def resolve_intent(
    laya_result: Optional[dict],
    rules_result: tuple[Optional[str], list[str]],
    min_conf: float = LAYA_INTENT_MIN_CONFIDENCE,
    n_docs: Optional[int] = None,
) -> dict[str, Any]:
    """
    Resolve the final intent using Laya and rules.

    Args:
        laya_result: {"intent": str, "confidence": float, "probabilities": dict} or None
        rules_result: (intent_name, matched_keywords) from rules_intent()
        min_conf: Minimum confidence threshold for Laya
        n_docs: number of attached documents (cross_reference needs ≥ 2);
                None = unknown (no check)

    Returns:
        {
            "intent": str,
            "source": "laya" | "rules",
            "confidence": float | None,
            "note": str | None,
            "probabilities": dict | None,
        }
    """
    rules_intent_name, rules_keywords = rules_result

    # cross_reference needs two or more documents: Laya's cross_reference with
    # a single document falls back to the other signals.
    if (
        laya_result and laya_result.get("intent") == "cross_reference"
        and n_docs is not None and n_docs < 2
    ):
        laya_result = None

    # Strong rule guard for cross_reference (rules only fire with ≥ 2 docs and
    # no signature words): it beats extract_data / general_question /
    # identify_person / verify_signature from Laya; only a very confident
    # translate/redact/summarize/graph_query from Laya overrides it.
    if rules_intent_name == "cross_reference":
        kw_note = f"keyword rules matched: {'/'.join(rules_keywords)}" if rules_keywords else None
        if laya_result and laya_result.get("intent") == "cross_reference":
            return {
                "intent": "cross_reference",
                "source": "laya",
                "confidence": laya_result.get("confidence"),
                "note": "keyword rules agree",
                "probabilities": laya_result.get("probabilities"),
            }
        if (
            laya_result
            and laya_result.get("intent") in ("translate", "redact", "summarize", "graph_query")
            and laya_result.get("confidence", 0) >= VERIFY_SIGNATURE_STRONG_CONFIDENCE
        ):
            return {
                "intent": laya_result["intent"],
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": f"Laya confident ({laya_result['confidence']:.2f}) — rules suggested cross_reference",
                "probabilities": laya_result.get("probabilities"),
            }
        note = kw_note
        if laya_result and laya_result.get("intent"):
            laya_note = f"Laya suggested {laya_result['intent']} ({laya_result.get('confidence', 0):.2f})"
            note = f"{kw_note} · {laya_note}" if kw_note else laya_note
        return {
            "intent": "cross_reference",
            "source": "rules",
            "confidence": None,
            "note": note,
            "probabilities": None,
        }

    # Strong rule guard for verify_signature
    if rules_intent_name == "verify_signature":
        # If Laya has same intent, credit Laya
        if laya_result and laya_result.get("intent") == "verify_signature":
            return {
                "intent": "verify_signature",
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": "keyword rules agree",
                "probabilities": laya_result.get("probabilities"),
            }
        # If Laya very confident in something else, Laya wins
        if laya_result and laya_result.get("confidence", 0) >= VERIFY_SIGNATURE_STRONG_CONFIDENCE:
            return {
                "intent": laya_result["intent"],
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": f"Laya confident ({laya_result['confidence']:.2f}) — rules suggested verify_signature",
                "probabilities": laya_result.get("probabilities"),
            }
        # Otherwise use verify_signature from rules (confidence: None for rules)
        kw_note = f"keyword rules matched: {'/'.join(rules_keywords)}" if rules_keywords else "reference specimen detected"
        return {
            "intent": "verify_signature",
            "source": "rules",
            "confidence": None,
            "note": kw_note,
            "probabilities": None,
        }

    # NEW: identify_person agreement rule
    # Accept Laya's identify_person ONLY if:
    # (a) keyword rules also say identify_person, OR
    # (b) Laya's confidence >= IDENTIFY_PERSON_STRONG_CONFIDENCE
    if laya_result and laya_result.get("intent") == "identify_person":
        if rules_intent_name == "identify_person":
            # Both agree on identify_person; credit Laya
            return {
                "intent": "identify_person",
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": "keyword rules agree",
                "probabilities": laya_result.get("probabilities"),
            }
        elif laya_result.get("confidence", 0) >= IDENTIFY_PERSON_STRONG_CONFIDENCE:
            # Laya very confident in identify_person (>= 0.9); accept it
            return {
                "intent": "identify_person",
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": "high confidence score",
                "probabilities": laya_result.get("probabilities"),
            }
        else:
            # Laya suggests identify_person but low-medium confidence and rules don't agree
            note = f"Laya suggested identify_person ({laya_result['confidence']:.2f}) but the question asks for a specific fact, not who someone is"
            if rules_intent_name:
                note += f", using {rules_intent_name} instead"
                return {
                    "intent": rules_intent_name,
                    "source": "rules",
                    "confidence": None,
                    "note": note,
                    "probabilities": None,
                }
            else:
                return {
                    "intent": "general_question",
                    "source": "rules",
                    "confidence": None,
                    "note": note,
                    "probabilities": None,
                }

    # extract_data agreement rule
    # Accept Laya's extract_data ONLY if keyword rules also say extract_data
    if laya_result and laya_result.get("intent") == "extract_data":
        if rules_intent_name == "extract_data":
            # Both agree on extract_data; credit Laya
            return {
                "intent": "extract_data",
                "source": "laya",
                "confidence": laya_result["confidence"],
                "note": "keyword rules agree",
                "probabilities": laya_result.get("probabilities"),
            }
        elif laya_result.get("confidence", 0) >= min_conf:
            # Laya says extract_data but rules don't; treat as unconfirmed
            note = f"Laya suggested extract_data ({laya_result['confidence']:.2f}) but no matching keywords"
            if rules_intent_name:
                note += f", using {rules_intent_name} instead"
            if rules_intent_name:
                return {
                    "intent": rules_intent_name,
                    "source": "rules",
                    "confidence": None,
                    "note": note,
                    "probabilities": None,
                }
            else:
                return {
                    "intent": "general_question",
                    "source": "rules",
                    "confidence": None,
                    "note": note,
                    "probabilities": None,
                }

    # Normal priority: Laya if confident
    if laya_result and laya_result.get("confidence", 0) >= min_conf:
        intent = laya_result["intent"]
        note = None
        if rules_intent_name and rules_intent_name != intent:
            note = f"Rules suggested {rules_intent_name}"
        return {
            "intent": intent,
            "source": "laya",
            "confidence": laya_result["confidence"],
            "note": note,
            "probabilities": laya_result.get("probabilities"),
        }

    # Fallback to rules
    if rules_intent_name:
        note = None
        if laya_result:
            note = f"Laya unsure ({laya_result.get('confidence', 0):.2f}) — used keyword rules"
        return {
            "intent": rules_intent_name,
            "source": "rules",
            "confidence": None,
            "note": note,
            "probabilities": None,
        }

    # Default to general_question
    note = "No confident signal"
    if laya_result:
        note = f"Laya uncertain — defaulted to general_question"
    return {
        "intent": "general_question",
        "source": "rules",
        "confidence": None,
        "note": note,
        "probabilities": None,
    }


def guess_role(message: str, file: dict) -> str:
    """Heuristic role guess: 'reference' or 'subject'."""
    name = (file.get("name") or "").lower()
    msg_lower = message.lower()

    # Reference keywords
    ref_keywords = ["reference", "specimen", "sample", "card", "template", "spec", "referentie", "model"]
    if "signature" in name and any(kw in name for kw in ref_keywords):
        return "reference"

    # Subject keywords typically indicate the main document
    if any(kw in msg_lower for kw in ["check this", "verify this", "look at this", "document", "my"]):
        return "subject"

    # Default
    return "subject"


def assign_roles(
    files: list[dict],
    message: str,
    laya_role_fn: Callable[[str, dict], Optional[dict]],
    user_roles: Optional[dict[str, str]] = None,
    intent: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Assign roles to files based on user input, Laya, and heuristics.

    Returns list of {doc_id, name, role, source, confidence}.
    """
    if user_roles is None:
        user_roles = {}

    result = []
    for f in files:
        doc_id = f.get("doc_id")
        name = f.get("name", "?")

        # User role always wins
        if user_roles.get(doc_id) and user_roles[doc_id] != "auto":
            role = user_roles[doc_id]
            result.append({
                "doc_id": doc_id,
                "name": name,
                "role": role,
                "source": "user",
                "confidence": 1.0,
            })
            continue

        # Try Laya
        laya_result = laya_role_fn(message, f)
        if laya_result and laya_result.get("confidence", 0) >= LAYA_ROLE_MIN_CONFIDENCE:
            role = laya_result.get("role", "subject_document")
            # Map Laya's return format to role names
            if role == "subject_document":
                role = "subject"
            elif role == "reference_specimen":
                role = "reference"
            result.append({
                "doc_id": doc_id,
                "name": name,
                "role": role,
                "source": "laya",
                "confidence": laya_result.get("confidence"),
            })
            continue

        # Fallback to heuristic
        role = guess_role(message, f)
        result.append({
            "doc_id": doc_id,
            "name": name,
            "role": role,
            "source": "rules",
            "confidence": None,
        })

    # For verify_signature: if no reference assigned but >=2 files, pick smallest image/'card'-named file
    if intent == "verify_signature":
        roles_dict = {r["doc_id"]: r for r in result}
        if not any(r["role"] == "reference" for r in result) and len(result) >= 2:
            # Find smallest image file or 'card'-named file
            candidates = []
            for r in result:
                doc_id = r["doc_id"]
                candidates.append((doc_id, r["name"]))

            # Sort by file size heuristic (shorter name ~ smaller)
            candidates.sort(key=lambda x: len(x[1]))
            if candidates:
                smallest_doc_id = candidates[0][0]
                roles_dict[smallest_doc_id]["role"] = "reference"
                roles_dict[smallest_doc_id]["source"] = "rules"
                roles_dict[smallest_doc_id]["note"] = "Auto-assigned as reference (smallest file)"

        return list(roles_dict.values())

    return result


# ── Main orchestrator ──────────────────────────────────────────────────────

async def run_agent(
    req: dict[str, Any],
    hooks: AgentHooks,
) -> AsyncIterator[dict[str, Any]]:
    """
    Run the document agent orchestrator.

    Args:
        req: {
            "message": str,
            "attachments": [{"doc_id": str, "role"?: "auto"|"subject"|"reference"}],
            "history": [...optional prior messages],
            "model_override": str|None,
        }
        hooks: AgentHooks instance with all injected dependencies.

    Yields:
        Events: {"tile": Tile}, {"thinking": str}, {"content": str}, {"done": True, ...}, {"error": str}
    """
    start_ms = hooks.now_ms()
    laya_tile = None
    tiles_emitted = {}

    try:
        # Extract request
        message = req.get("message", "").strip()
        attachments = req.get("attachments", [])
        history = req.get("history", [])
        model_override = req.get("model_override")
        conversation_id = req.get("conversation_id")
        run_id = req.get("run_id")

        import doc_web as _doc_web

        # Strip any web-lookup clause BEFORE intent detection so "…and check
        # LinkedIn" / "verify online…" can't skew the intent. A message that is
        # only a web clause ("search the web for this person") asks who the
        # document is about.
        rules_web = _doc_web.rules_web_lookup(message) if message else ([], [])
        message_for_intent = message
        if rules_web[0]:
            message_for_intent = _doc_web.strip_web_clause_or_empty(message)
            if len(message_for_intent.strip()) < 3:
                message_for_intent = "Who is this document about?"

        # Early intent detection (rules only) to check for graph_query
        # This allows graph_query to bypass attachment validation
        early_files = [{"doc_id": a.get("doc_id"), "name": ""} for a in attachments]
        early_rules_result = rules_intent(message_for_intent, early_files)
        early_intent = early_rules_result[0] if early_rules_result else None

        # Validate: require at least one attachment (unless intent is graph_query)
        if not attachments and early_intent != "graph_query":
            yield {"error": "Attach at least one document."}
            return

        # (a) Emit Laya tile (running, no decisions yet)
        try:
            laya_status = hooks.laya_info() or {}
        except Exception:
            laya_status = {"available": False}
        laya_tile = Tile(
            id="laya",
            kind="laya",
            title="Analyzing intent",
            status="running",
            model="Laya" if laya_status.get("available") else "rules",
            model_info=laya_status,
            decisions=[],
            started_ms=start_ms,
        )
        yield {"tile": laya_tile.to_dict()}
        tiles_emitted["laya"] = laya_tile

        # Fetch documents
        docs = []
        for att in attachments:
            doc_id = att.get("doc_id")
            doc = hooks.get_doc(doc_id)
            if not doc:
                yield {"error": f"Document not found: {doc_id}"}
                return
            docs.append((doc, att))

        # (b) Build files_meta for Laya
        files_meta = []
        for doc, att in docs:
            files_meta.append({
                "doc_id": doc.id,
                "name": doc.filename,
                "kind": _mime_to_kind(doc.mime),
                "chars": len(doc.text or ""),
                "snippet": (doc.text or "")[:300],
            })

        # Decision 1: intent (on the message with any web clause stripped)
        laya_intent_result = None
        if message_for_intent and message_for_intent.strip():
            # Only call Laya if message is not empty
            try:
                # Run in executor to avoid blocking
                laya_intent_result = await asyncio.to_thread(
                    hooks.laya_intent, message_for_intent, files_meta
                )
            except Exception as e:
                log.warning(f"Laya intent failed: {e}")

        rules_intent_result = rules_intent(message_for_intent, files_meta)

        # Add note if message was empty (rules_intent handles this)
        if not message or not message.strip():
            log.debug("Empty message detected — intent decision: 'summarize' via rules")
        intent_decision = resolve_intent(laya_intent_result, rules_intent_result, n_docs=len(docs))
        intent = intent_decision["intent"]
        rules_intent_name = rules_intent_result[0]  # Extract intent name for note display

        # Add decision to Laya tile
        laya_tile.decisions.append(Decision(
            id="intent",
            label="Intent",
            value=intent,
            confidence=intent_decision.get("confidence"),
            source=intent_decision.get("source", "rules"),
            note=intent_decision.get("note"),
            probabilities=intent_decision.get("probabilities"),
        ))
        yield {"tile": laya_tile.to_dict()}

        # ── GRAPH_QUERY handling ──
        if intent == "graph_query":
            # Add tool decision
            laya_tile.decisions.append(Decision(
                id="tool",
                label="Tool",
                value="knowledge graph query",
                source="rules",
            ))
            yield {"tile": laya_tile.to_dict()}

            # Create query tile
            query_tile = Tile(
                id="kg-query",
                kind="query",
                title="Knowledge graph query",
                status="running",
                started_ms=hooks.now_ms(),
            )
            yield {"tile": query_tile.to_dict()}
            tiles_emitted["kg-query"] = query_tile

            try:
                # Subject phrase (word-bounded keywords; pronouns resolved from history)
                subject = graph_subject(message, history)

                # Search knowledge graph
                if hooks.kg_search:
                    entities = await hooks.kg_search(subject)
                else:
                    entities = []

                if not entities:
                    # No match
                    query_tile.status = "done"
                    query_tile.detail = f"No matching entities in the knowledge store yet"
                    query_tile.ms = hooks.now_ms() - query_tile.started_ms
                    yield {"tile": query_tile.to_dict()}

                    # Answer: no match
                    answer_tile = Tile(
                        id="answer",
                        kind="llm",
                        title="Answer",
                        status="done",
                        started_ms=hooks.now_ms(),
                    )
                    yield {"tile": answer_tile.to_dict()}

                    answer_text = f'I don\'t have anything about "{subject}" in the knowledge store yet. Ask about a document first (e.g. "who is this document about?") so I can learn from it.'
                    yield {"content": answer_text}
                    answer_tile.status = "done"
                    answer_tile.output_preview = answer_text[:300]
                    answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                    yield {"tile": answer_tile.to_dict()}
                    tiles_emitted["answer"] = answer_tile

                else:
                    # Found entities - get neighborhood
                    top_entity = entities[0]
                    entity_id = top_entity["id"]

                    if hooks.kg_neighborhood:
                        neighborhood = await hooks.kg_neighborhood(entity_id)
                    else:
                        neighborhood = {"entities": [top_entity], "relations": []}

                    # Build facts list for the prompt with deduplication and ordering
                    entities_map = {e["id"]: e for e in neighborhood.get("entities", [])}
                    relations = neighborhood.get("relations", [])

                    # Where each fact comes from: document names (via mentioned_in)
                    # and website hosts — never internal labels like "doc_agent".
                    docs_of: dict[str, list[str]] = {}
                    for rel in relations:
                        if rel.get("type") == "mentioned_in":
                            dst_e = entities_map.get(rel.get("dst"), {})
                            if dst_e.get("type") == "document":
                                dname = (dst_e.get("props") or {}).get("filename") or dst_e.get("name") or ""
                                if dname and dname not in docs_of.setdefault(rel.get("src"), []):
                                    docs_of[rel.get("src")].append(dname)

                    def _cite(rel: dict) -> str:
                        src_e = entities_map.get(rel.get("src"), {})
                        dst_e = entities_map.get(rel.get("dst"), {})
                        for e in (dst_e, src_e):
                            if e.get("type") == "profile":
                                props = e.get("props") or {}
                                host = props.get("host") or _doc_web._hostname(props.get("url", ""))
                                return (host or "").removeprefix("www.")
                        if dst_e.get("type") == "document":
                            return (dst_e.get("props") or {}).get("filename") or dst_e.get("name") or ""
                        names = docs_of.get(rel.get("src")) or docs_of.get(rel.get("dst")) or []
                        return ", ".join(names[:3])

                    # Build fact dicts with rendered labels
                    fact_items = []
                    seen_labels = set()
                    for rel in relations:
                        src_id = rel.get("src")
                        dst_id = rel.get("dst")
                        # Never cite legacy junk: profiles without an http(s)
                        # URL ("ref://…") or answer-derived generic roles.
                        if any(
                            _is_junk_graph_entity(entities_map.get(x, {}))
                            for x in (src_id, dst_id)
                        ):
                            continue
                        src_name = entities_map.get(src_id, {}).get("name", src_id)
                        dst_name = entities_map.get(dst_id, {}).get("name", dst_id)
                        rel_type = rel.get("type", "relates_to")
                        confidence = rel.get("confidence")
                        source = _cite(rel)

                        # Render label for deduplication
                        label = f"{src_name} —{rel_type}→ {dst_name}"
                        label_normalized = _normalize_for_dedup(label)

                        # Skip if duplicate (case/accent-insensitive)
                        if label_normalized in seen_labels:
                            continue
                        seen_labels.add(label_normalized)

                        conf_str = f" ({confidence:.0%})" if confidence else ""
                        fact_text = f"- {label}{conf_str}" + (f" (source: {source})" if source else "")
                        if (rel.get("props") or {}).get("demo") or any(
                            (entities_map.get(x, {}).get("props") or {}).get("demo")
                            for x in (src_id, dst_id)
                        ):
                            fact_text += " [from a DEMO/fictitious document — not proof of a real company]"

                        fact_items.append({
                            "type": rel_type,
                            "text": fact_text,
                            "confidence": confidence or 0,
                            "rel": rel,  # Keep original relation for items
                            "src_name": src_name,
                            "dst_name": dst_name,
                        })

                    # Sort by type: has_role/works_at/located_in first, then mentioned_in, likely_profile, then candidate_profile (max 3)
                    priority_types = {
                        "has_role": 0,
                        "works_at": 1,
                        "owns": 1.5,
                        "located_in": 2,
                        "mentioned_in": 3,
                        "likely_profile": 4,
                        "candidate_profile": 5,
                    }

                    # Count candidate_profile facts
                    candidate_count = 0
                    ordered_facts = []

                    for fact in sorted(fact_items, key=lambda f: (priority_types.get(f["type"], 99), -f["confidence"])):
                        if fact["type"] == "candidate_profile":
                            if candidate_count >= 3:
                                continue
                            candidate_count += 1
                        ordered_facts.append(fact["text"])
                        if len(ordered_facts) >= 12:
                            break

                    facts_text = "\n".join(ordered_facts)

                    query_tile.status = "done"
                    n_entities = len(neighborhood.get("entities", []))
                    n_relations = len(ordered_facts)  # Use deduplicated count
                    query_tile.detail = f"Matched {top_entity['name']} · {n_relations} fact{'s' if n_relations != 1 else ''} from {n_entities} entit{'ies' if n_entities != 1 else ''}"

                    # Build items for the tile using deduplicated facts
                    items = []
                    for fact in sorted(fact_items, key=lambda f: (priority_types.get(f["type"], 99), -f["confidence"]))[:12]:
                        if fact["type"] == "candidate_profile":
                            if candidate_count >= 3:
                                continue
                            candidate_count += 1

                        rel = fact["rel"]
                        dst_id = rel.get("dst")
                        dst_entity = entities_map.get(dst_id, {})
                        url = dst_entity.get("props", {}).get("url") if dst_entity.get("type") == "profile" else None

                        items.append({
                            "kind": "result",
                            "label": f"{fact['src_name']} —{rel.get('type', 'relates_to')}→ {fact['dst_name']}",
                            "detail": _cite(rel),
                            "url": url,
                        })

                    query_tile.items = items
                    query_tile.ms = hooks.now_ms() - query_tile.started_ms
                    yield {"tile": query_tile.to_dict()}

                    # Now call LLM with facts
                    answer_tile = Tile(
                        id="answer",
                        kind="llm",
                        title="Answer",
                        status="running",
                        started_ms=hooks.now_ms(),
                    )
                    yield {"tile": answer_tile.to_dict()}
                    tiles_emitted["answer"] = answer_tile

                    # Resolve model (no doc context, use fallback chain)
                    llm_model = ""
                    model_reason = None
                    if hooks.resolve_text_model_info:
                        model_info = await hooks.resolve_text_model_info(None, "text")
                        llm_model = model_info.get("model", "")
                        model_reason = model_info.get("reason")
                    else:
                        # Fallback for older hook implementations
                        llm_model = await hooks.resolve_model("text")

                    answer_tile.model = llm_model
                    if model_reason:
                        answer_tile.decisions.append(Decision(
                            id="model_resolution",
                            label="Model selection",
                            value=llm_model,
                            source="fallback",
                            note=model_reason,
                        ))

                    # Check if model is available
                    if not llm_model:
                        answer_tile.status = "error"
                        answer_tile.output_preview = "No text model available"
                        answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                        yield {"tile": answer_tile.to_dict()}

                        error_msg = "No text model available — set a Documents or Main Chat model in Settings"
                        yield {"content": error_msg}
                        tiles_emitted["answer"] = answer_tile
                    else:
                        # Build prompt with facts
                        prompt_template = GRAPH_QUERY_PROMPT.format(document_content=facts_text)
                        parts = [prompt_template, "USER REQUEST:\n" + message]
                        prompt = "\n\n".join(parts)

                        # Stream LLM with token limit for graph_query answers
                        answer_text = ""
                        # Try to pass num_predict if supported; cap graph_query answers at ~600 tokens
                        try:
                            async for delta in hooks.stream_llm(llm_model, prompt, think=False, num_predict=600):
                                if "thinking" in delta:
                                    yield {"thinking": delta["thinking"]}
                                elif "content" in delta:
                                    chunk = delta["content"]
                                    answer_text += chunk
                                    yield {"content": chunk}
                                elif delta.get("done"):
                                    break
                        except TypeError:
                            # Fallback if num_predict is not supported
                            async for delta in hooks.stream_llm(llm_model, prompt, think=False):
                                if "thinking" in delta:
                                    yield {"thinking": delta["thinking"]}
                                elif "content" in delta:
                                    chunk = delta["content"]
                                    answer_text += chunk
                                    yield {"content": chunk}
                                elif delta.get("done"):
                                    break

                        answer_tile.status = "done"
                        answer_tile.output_preview = answer_text[:300]
                        answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                        yield {"tile": answer_tile.to_dict()}
                        tiles_emitted["answer"] = answer_tile

            except Exception as e:
                log.error(f"Graph query failed: {e}")
                query_tile.status = "error"
                query_tile.detail = str(e)[:100]
                query_tile.ms = hooks.now_ms() - query_tile.started_ms
                yield {"tile": query_tile.to_dict()}
                yield {"error": f"Graph query failed: {str(e)}"}
                # Continue to mark Laya as done
                laya_tile.status = "done"
                laya_tile.ms = hooks.now_ms() - laya_tile.started_ms
                yield {"tile": laya_tile.to_dict()}
                yield {
                    "done": True,
                    "stats": {
                        "total_ms": hooks.now_ms() - start_ms,
                        "intent": intent,
                        "tiles_count": len(tiles_emitted) + 2,
                    },
                }
                return

            # Mark Laya as done and emit final done event
            laya_tile.status = "done"
            laya_tile.ms = hooks.now_ms() - laya_tile.started_ms
            yield {"tile": laya_tile.to_dict()}

            yield {
                "done": True,
                "stats": {
                    "total_ms": hooks.now_ms() - start_ms,
                    "intent": intent,
                    "tiles_count": len(tiles_emitted) + 2,
                },
            }
            return

        # Compute web lookup decision (after intent, before roles). A search
        # only ever runs on an explicit keyword request (rules); Laya can
        # corroborate but never trigger one on its own.
        web_lookup = None
        if message and message.strip() and (hooks.laya_web or rules_web[0]):
            laya_web_result = None
            if hooks.laya_web:
                try:
                    laya_web_result = await asyncio.to_thread(hooks.laya_web, message)
                except Exception as e:
                    log.debug(f"Laya web lookup failed: {e}")
            web_lookup = _doc_web.resolve_web_lookup(laya_web_result, rules_web)

        # Add web lookup decision to Laya tile
        web_targets: list[str] = list(web_lookup.get("targets") or []) if web_lookup else []
        if web_lookup:
            web_note = web_lookup.get("note")
            if web_targets and not web_note:
                web_note = "Sends the person's name/role to DuckDuckGo — leaves this machine"
            laya_tile.decisions.append(Decision(
                id="web_lookup",
                label="Web lookup",
                value=", ".join(web_targets) if web_targets else "none",
                confidence=web_lookup.get("confidence"),
                source=web_lookup.get("source") or "rules",
                note=web_note,
            ))
        yield {"tile": laya_tile.to_dict()}

        # Decisions 2..n: roles (only if >=2 attachments or intent is verify_signature)
        user_roles = {att.get("doc_id"): att.get("role") for att in attachments}
        if len(docs) >= 2 or intent == "verify_signature":
            assigned_roles = await asyncio.to_thread(
                lambda: assign_roles(files_meta, message, hooks.laya_role, user_roles, intent)
            )
            for role_info in assigned_roles:
                laya_tile.decisions.append(Decision(
                    id=f"role-{role_info['doc_id']}",
                    label=f"Role ({role_info['name']})",
                    value=role_info["role"],
                    confidence=role_info.get("confidence"),
                    source=role_info.get("source") or "rules",
                ))
            yield {"tile": laya_tile.to_dict()}
        else:
            # Single file: infer from intent
            if docs:
                # For single docs, generally they're the subject unless intent says otherwise
                laya_tile.decisions.append(Decision(
                    id=f"role-{docs[0][0].id}",
                    label=f"Role ({docs[0][0].filename})",
                    value="subject",
                    source="rules",
                ))
            yield {"tile": laya_tile.to_dict()}

        # Decision: doc kind per document (if has text layer). Reuse a kind
        # cached on the document (doc.meta["doc_kind"] from an earlier run, or
        # the /api/idp/route decision) instead of calling Laya again.
        for doc, att in docs:
            if doc.text and len(doc.text.strip()) >= 40:
                try:
                    doc_kind_result = _cached_doc_kind(doc)
                    if doc_kind_result is None:
                        doc_kind_result = await asyncio.to_thread(
                            hooks.laya_doc_kind, doc.text[:6000]
                        )
                        _cache_doc_kind(doc, doc_kind_result)
                    if doc_kind_result and doc_kind_result.get("confidence", 0) >= LAYA_DOC_KIND_MIN_CONFIDENCE:
                        laya_tile.decisions.append(Decision(
                            id=f"doc_kind-{doc.id}",
                            label=f"Document Kind",
                            value=doc_kind_result.get("kind", "unknown"),
                            confidence=doc_kind_result.get("confidence"),
                            source="laya",
                        ))
                except Exception as e:
                    log.debug(f"doc_kind failed: {e}")

        yield {"tile": laya_tile.to_dict()}

        # (c) Extraction step per document. Signature comparison only reads
        # the SUBJECT docs' text (references are images); every other intent
        # uses every attached document — subjects first, then references
        # (labelled role="reference" in the prompt).
        subject_docs = []
        reference_docs = []
        doc_roles: dict[str, str] = {}
        for doc, att in docs:
            role = _find_role(doc, laya_tile, docs)
            doc_roles[doc.id] = role
            if role == "subject":
                subject_docs.append(doc)
            else:
                reference_docs.append(doc)

        if intent == "verify_signature":
            answer_docs = list(subject_docs)
        else:
            answer_docs = subject_docs + reference_docs
        # The document the answer model / web lookup keys on
        primary_docs = subject_docs or answer_docs
        prompt_roles = {
            d.id: "reference" for d in answer_docs if doc_roles.get(d.id) == "reference"
        }

        empty_docs = []
        for doc in answer_docs:
            extract_tile = Tile(
                id=f"extract-{doc.id}",
                kind="extract",
                title=f"Extract from {doc.filename}",
                status="pending",
                doc={"doc_id": doc.id, "name": doc.filename},
            )

            # A scan with a tiny text layer (stamp, page numbers) still needs
            # OCR: < 40 chars per page with page images → OCR.
            text_len = len(doc.text.strip()) if doc.text else 0
            already_ocrd = bool((getattr(doc, "meta", None) or {}).get("last_ocr_at"))
            ocr_needed = (
                intent != "verify_signature"
                and bool(doc.page_images)
                and (_needs_ocr(doc) or (text_len == 0 and not already_ocrd))
            )
            doc_budget = _doc_budget(len(answer_docs))
            if ocr_needed:
                # Too little text for the page images; run OCR
                extract_tile.kind = "ocr"
                extract_tile.status = "running"
                extract_tile.started_ms = hooks.now_ms()

                # Add OCR decision to Laya tile
                laya_tile.decisions.append(Decision(
                    id=f"ocr_needed-{doc.id}",
                    label="OCR Needed",
                    value="yes",
                    source="probe",
                    note=(
                        "No text layer, page images available" if text_len == 0 else
                        f"Text layer too small ({text_len} chars for {max(1, getattr(doc, 'pages', 0) or 0)} page(s)) — page images available"
                    ),
                ))
                yield {"tile": laya_tile.to_dict()}
                yield {"tile": extract_tile.to_dict()}

                try:
                    ocr_model = await hooks.resolve_model("ocr")
                    extract_tile.model = ocr_model
                    extract_tile.model_info = await hooks.model_info(ocr_model)
                    yield {"tile": extract_tile.to_dict()}

                    ocr_text = await hooks.run_ocr(doc, ocr_model)
                    extract_tile.status = "done"
                    extract_tile.detail = f"{len(ocr_text)} chars from {len(doc.page_images)} page(s)"
                    if len(doc.text or ocr_text or "") > doc_budget:
                        extract_tile.detail += " · excerpts used"
                    extract_tile.output_preview = ocr_text[:300]
                    extract_tile.ms = hooks.now_ms() - extract_tile.started_ms
                    yield {"tile": extract_tile.to_dict()}
                    tiles_emitted[extract_tile.id] = extract_tile

                except Exception as e:
                    extract_tile.status = "error"
                    extract_tile.detail = str(e)[:200]
                    yield {"tile": extract_tile.to_dict()}
                    yield {"error": f"OCR failed: {str(e)}"}
                    return

            elif text_len > 0:
                # Text layer present; skip OCR
                extract_tile.status = "done"
                extract_tile.kind = "extract"
                meta = getattr(doc, "meta", None) or {}
                if isinstance(meta, dict) and (meta.get("last_ocr_at") or meta.get("ocr_model")):
                    # Text comes from an earlier OCR pass (e.g. an image-only scan)
                    extract_tile.detail = f"OCR text (cached, {text_len} chars)"
                elif text_len >= 40:
                    extract_tile.detail = f"Text layer present ({text_len} chars) — OCR skipped"
                else:
                    extract_tile.detail = f"Short text layer ({text_len} chars) — OCR skipped"
                if len(doc.text) > doc_budget and intent != "verify_signature":
                    extract_tile.detail = f"{len(doc.text):,} chars · excerpts used"
                sheet_list = None
                if isinstance(meta, dict) and meta.get("sheets"):
                    import sheets as _sheets_mod
                    sheet_list = _sheets_mod.normalize_meta_sheets(meta)[0]   # legacy dict form too
                if isinstance(sheet_list, list) and sheet_list:
                    vis = [x for x in sheet_list if isinstance(x, dict) and not x.get("hidden")]
                    n_rows = sum(int(x.get("rows") or 0) for x in vis)
                    extract_tile.detail = (
                        f"Spreadsheet · {len(vis)} sheet{'s' if len(vis) != 1 else ''} · {n_rows:,} rows"
                    )
                extract_tile.output_preview = doc.text[:300]
                yield {"tile": extract_tile.to_dict()}
                tiles_emitted[extract_tile.id] = extract_tile

            elif intent == "verify_signature" and not doc.page_images:
                # Signature comparison but no images
                extract_tile.kind = "extract"
                extract_tile.status = "error"
                extract_tile.detail = "No page images available for signature comparison"
                yield {"tile": extract_tile.to_dict()}
                yield {"error": "Signature comparison requires page images"}
                return
            elif intent == "verify_signature":
                # Signature comparison; mark extract as skipped
                extract_tile.status = "skipped"
                extract_tile.detail = "Signature comparison is visual — OCR not needed"
                yield {"tile": extract_tile.to_dict()}
                tiles_emitted[extract_tile.id] = extract_tile
            else:
                # No usable text and no page images (and intent != verify_signature)
                extract_tile.status = "error"
                extract_tile.detail = f"No readable text found in {doc.filename} (it may be empty, scanned without page images, or use an unsupported layout)"
                yield {"tile": extract_tile.to_dict()}
                tiles_emitted[extract_tile.id] = extract_tile
                empty_docs.append(doc)

        # Check if all subject docs are empty
        if answer_docs and all(doc in empty_docs for doc in answer_docs) and intent != "verify_signature":
            # All subject docs are empty
            answer_tile = Tile(
                id="answer",
                kind="llm",
                title="Generating answer",
                status="error",
                started_ms=hooks.now_ms(),
            )
            doc_names = ", ".join(doc.filename for doc in answer_docs)
            answer_tile.detail = "No document content available to answer from"
            answer_tile.output_preview = f"I couldn't read any text from {doc_names}."
            yield {"tile": answer_tile.to_dict()}

            # Emit answer content
            answer_text = f"I couldn't read any text from {doc_names}. The file may be empty, image-only without page images, or use a layout I can't extract. Try re-uploading it as PDF."
            for i in range(0, len(answer_text), 200):
                chunk = answer_text[i : i + 200]
                yield {"content": chunk}

            # Emit done event
            yield {
                "done": True,
                "stats": {
                    "total_ms": hooks.now_ms() - start_ms,
                    "intent": intent,
                    "tiles_count": len(tiles_emitted) + 2,
                },
            }
            return

        # (d) Final step: pick model and stream answer
        answer_tile = Tile(
            id="answer",
            kind="llm",
            title="Cross-reference" if intent == "cross_reference" else "Generating answer",
            status="pending",
            started_ms=hooks.now_ms(),
        )

        # Determine which model to use based on intent
        if intent == "verify_signature":
            # Vision model: get candidates and try them in order with fallback
            try:
                candidates = await hooks.vision_candidates()
            except Exception as e:
                answer_tile.status = "error"
                answer_tile.detail = f"Failed to get vision model candidates: {str(e)[:150]}"
                yield {"tile": answer_tile.to_dict()}
                yield {"error": answer_tile.detail}
                return

            if not candidates:
                answer_tile.status = "error"
                answer_tile.detail = "No vision-capable model could be found. Install one that Ollama can run (e.g. `ollama pull minicpm-v`) or set a Handwriting model in Settings."
                yield {"tile": answer_tile.to_dict()}
                yield {"error": answer_tile.detail}
                return

            answer_tile.kind = "vision"

            # Create answer_model decision (will be updated in loop)
            answer_model_decision = Decision(
                id="answer_model",
                label="Answer Model",
                value=candidates[0],  # Start with first
                source="config",
                note="Vision model selected for signature comparison",
            )
            laya_tile.decisions.append(answer_model_decision)

            # Prepare image paths once (before the loop)
            subject_images = []
            reference_images = []

            try:
                for doc, _ in docs:
                    role = _find_role(doc, laya_tile, docs)
                    if role == "reference" and doc.page_images:
                        # Get first page of reference
                        ref_paths = await asyncio.to_thread(hooks.page_image_paths, doc, [1], 200)
                        reference_images.extend(ref_paths)
                    elif role == "subject" and doc.page_images:
                        # Get first and last page of subject (deduped, max 3)
                        pages_to_check = [1, len(doc.page_images)]
                        pages_to_check = list(dict.fromkeys(pages_to_check))  # dedup
                        pages_to_check = pages_to_check[:3]  # max 3
                        subject_paths = await asyncio.to_thread(hooks.page_image_paths, doc, pages_to_check, 200)
                        subject_images.extend(subject_paths)
            except Exception as e:
                answer_tile.status = "error"
                answer_tile.detail = f"Failed to prepare images: {str(e)[:150]}"
                yield {"tile": answer_tile.to_dict()}
                yield {"error": answer_tile.detail}
                return

            # Try candidates in order
            vision_result = None
            fallback_attempts = []

            for attempt_idx, vision_model in enumerate(candidates[:3]):  # Max 3 attempts
                # Update the answer_model decision to current candidate
                answer_model_decision.value = vision_model
                answer_model_decision.note = "Vision model selected for signature comparison"

                # Set tile model
                answer_tile.model = vision_model
                answer_tile.status = "running"

                # Emit detail (Trying or Falling back)
                if attempt_idx == 0:
                    answer_tile.detail = f"Trying {vision_model}"
                else:
                    answer_tile.detail = f"Falling back to {vision_model}"

                yield {"tile": laya_tile.to_dict()}
                yield {"tile": answer_tile.to_dict()}

                try:
                    vision_result = await hooks.vision_call(
                        vision_model,
                        SIGNATURE_PROMPT,
                        reference_images,
                        subject_images,
                    )

                    # Success: emit content and mark tile as done
                    for i in range(0, len(vision_result), 200):
                        chunk = vision_result[i : i + 200]
                        yield {"content": chunk}

                    answer_tile.status = "done"
                    answer_tile.output_preview = vision_result[:300]
                    answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                    answer_tile.detail = f"Answer produced by {vision_model}"
                    yield {"tile": answer_tile.to_dict()}

                    # Break on success
                    break

                except Exception as e:
                    error_str = str(e)
                    # Mark this model as failed
                    hooks.mark_vision_failed(vision_model, error_str)

                    # Record fallback attempt
                    fallback_attempts.append({
                        "idx": attempt_idx,
                        "model": vision_model,
                        "error": error_str[:140],
                    })

                    # Add fallback decision to Laya tile
                    laya_tile.decisions.append(Decision(
                        id=f"vision_fallback_{attempt_idx}",
                        label="Vision model fallback",
                        value=candidates[attempt_idx + 1] if attempt_idx + 1 < len(candidates) else "none",
                        source="config",
                        note=f"{vision_model} failed: {error_str[:140]}",
                    ))
                    yield {"tile": laya_tile.to_dict()}

                    # Continue to next candidate
                    if attempt_idx < len(candidates) - 1:
                        continue
                    else:
                        # All failed; emit error
                        break

            # If no candidate succeeded
            if vision_result is None:
                answer_tile.status = "error"
                tried_info = "\n".join(
                    f"  • {att['model']}: {att['error']}"
                    for att in fallback_attempts
                )
                answer_tile.detail = (
                    f"No vision model could run. Tried: {', '.join(att['model'] for att in fallback_attempts)}. "
                    f"If Ollama cannot load a model (\"unknown model architecture\"), update or re-pull it, "
                    f"or choose another under Settings → Handwriting model."
                )
                yield {"tile": answer_tile.to_dict()}
                yield {
                    "error": (
                        f"Vision model failed on all {len(fallback_attempts)} candidates. "
                        f"Details:\n{tried_info}"
                    )
                }
                return

        else:
            # LLM for all other intents
            answer_tile.kind = "llm"

            # Resolve model
            category = "docs" if primary_docs else "text"
            answer_model_note = f"Text model for {intent}"

            if primary_docs:
                llm_model = await hooks.resolve_text_model(primary_docs[0], category)
                # Check if we have the info hook for fallback details
                if hooks.resolve_text_model_info:
                    try:
                        model_info = await hooks.resolve_text_model_info(primary_docs[0], category)
                        llm_model = model_info.get("model", llm_model)
                        if model_info.get("reason"):
                            answer_model_note = model_info["reason"]
                    except Exception:
                        pass  # Fall back to simple resolution
            else:
                llm_model = await hooks.resolve_model("text")

            answer_tile.model = llm_model

            # Add decision
            laya_tile.decisions.append(Decision(
                id="answer_model",
                label="Answer Model",
                value=llm_model,
                source="config",
                note=answer_model_note,
            ))
            yield {"tile": laya_tile.to_dict()}

            # Spreadsheet questions: compute the numbers with a deterministic
            # table query (pandas) before the answer — the model only writes
            # the JSON spec and then phrases the computed result.
            message_for_table = message_for_intent if web_targets else message
            sheet_overrides: dict[str, str] = {}
            computed_result: Optional[str] = None
            sheet_notes: list[str] = []
            sheet_rows_not_sent: dict[str, int] = {}
            sheet_docs = [d for d in answer_docs if is_sheet_doc(d)] if intent != "verify_signature" else []
            if sheet_docs and hooks.sheet_frames:
                loaded: list[tuple[Any, list[dict]]] = []
                for d in sheet_docs:
                    try:
                        frames = await asyncio.to_thread(hooks.sheet_frames, d)
                    except Exception as e:
                        log.debug(f"sheet_frames failed for {d.id}: {e}")
                        frames = None
                    if frames:
                        loaded.append((d, frames))
                tq_hits = table_question_hits(message_for_table)
                cols_hit = _mentioned_columns(message_for_table, loaded[0][1]) if loaded else []
                # A hidden sheet named by the user: tell the answer model it is
                # hidden (never its contents) so it doesn't claim it's missing.
                hidden_hits = list(dict.fromkeys(
                    h for _d, frames in loaded for h in hidden_sheets_mentioned(message_for_table, frames)
                ))
                if hidden_hits:
                    sheet_notes.append(HIDDEN_SHEET_NOTE.format(names=", ".join(f"'{h}'" for h in hidden_hits)))
                visible_named = any(
                    _visible_sheet_mentioned(message_for_table, frames) for _d, frames in loaded
                )
                wants_query = bool(loaded) and (
                    (intent in TABLE_QUERY_INTENTS and (tq_hits or cols_hit or intent == "extract_data"))
                    or (intent == "identify_person" and tq_hits)
                ) and not (hidden_hits and not cols_hit and not visible_named)
                if wants_query:
                    reason = []
                    if tq_hits:
                        reason.append("keywords: " + ", ".join(tq_hits[:5]))
                    if cols_hit:
                        reason.append("columns: " + ", ".join(cols_hit[:4]))
                    laya_tile.decisions.append(Decision(
                        id="table_question",
                        label="Table query",
                        value="yes",
                        source="rules",
                        note=("table_question — " + "; ".join(reason)) if reason else f"spreadsheet · {intent}",
                    ))
                    yield {"tile": laya_tile.to_dict()}
                    q_doc, q_sheets = loaded[0]
                    tq_state: dict[str, Any] = {}
                    tq_tile = Tile(id="table-query", kind="table", title="Table query", status="pending")
                    tiles_emitted["table-query"] = tq_tile
                    async for ev in _table_query_step(
                        hooks, llm_model, q_doc, message_for_table, q_sheets, tq_state, tq_tile,
                        history=history,
                    ):
                        yield ev
                    result = tq_state.get("result")
                    if result:
                        computed_result = (
                            f"Sheet: {result['sheet']} of {q_doc.filename} · query: {_tq.spec_summary(result['spec'])}"
                            f" · {result['matched_rows']} matching source rows · {result['row_count']} result rows\n"
                            + result["markdown"]
                        )
                    for d, frames in loaded:
                        if result:
                            sheet_overrides[d.id] = _sheet_overview(frames, TABLE_PROMPT_HEAD_ROWS)
                            sheet_rows_not_sent[d.id] = _rows_not_sent(frames, TABLE_PROMPT_HEAD_ROWS)
                        else:
                            sheet_overrides[d.id] = _sheet_fallback_content(frames)
                            if _visible_rows(frames) > TEXT_ROW_CAP_FOR_PROMPT:
                                sheet_rows_not_sent[d.id] = _rows_not_sent(frames, TABLE_FALLBACK_HEAD_ROWS)
                else:
                    # No computation needed — but a large sheet is never
                    # dumped whole into the prompt.
                    for d, frames in loaded:
                        total = _visible_rows(frames)
                        if total > TEXT_ROW_CAP_FOR_PROMPT and intent not in ("translate", "redact"):
                            sheet_overrides[d.id] = _sheet_fallback_content(frames)
                            sheet_rows_not_sent[d.id] = _rows_not_sent(frames, TABLE_FALLBACK_HEAD_ROWS)
                # Tiles say when the prompt got the schema (+ result) instead of the sheet
                for d, _frames in loaded:
                    n_not_sent = sheet_rows_not_sent.get(d.id)
                    if n_not_sent is None or n_not_sent <= 0:
                        continue
                    what = "computed result" if computed_result else "stats"
                    label = f"Used table schema + {what} ({n_not_sent:,} rows not sent)"
                    answer_tile.detail = label
                    ext = tiles_emitted.get(f"extract-{d.id}")
                    if ext is not None:
                        base = (ext.detail or "").split(" · Used table schema")[0]
                        ext.detail = f"{base} · {label}" if base else label
                        yield {"tile": ext.to_dict()}

            answer_tile.status = "running"
            yield {"tile": answer_tile.to_dict()}

            try:
                # With a web lookup on, answer the document part only (the web
                # clause was stripped; a pure web clause → "who is this about").
                message_for_answer = message_for_intent if web_targets else message

                # Retrieval for long documents on passage-level intents
                retrieved: dict[str, list[Any]] = {}
                budget = _doc_budget(len([d for d in answer_docs if d.text]) or 1)
                if intent in RAG_INTENTS and hooks.retrieve_chunks:
                    for d in answer_docs:
                        if d.id in sheet_overrides:
                            continue
                        if d.text and len(d.text) > budget:
                            try:
                                chunks = await hooks.retrieve_chunks(d.id, message_for_answer, RAG_TOP_K)
                                if chunks:
                                    retrieved[d.id] = list(chunks)
                            except Exception as e:
                                log.debug(f"retrieve_chunks failed for {d.id}: {e}")

                # Build prompt
                prompt, excerpts = _build_prompt_ex(
                    intent, answer_docs, message_for_answer, history,
                    max_chars=PROMPT_MAX_CHARS, retrieved=retrieved,
                    web_lookup_on=bool(web_targets),
                    doc_overrides=sheet_overrides or None,
                    computed_result=computed_result,
                    task_notes=sheet_notes or None,
                    doc_roles=prompt_roles or None,
                )
                if excerpts:
                    longest = max(v["chars"] for v in excerpts.values())
                    answer_tile.detail = f"Document is long ({longest:,} chars) — used excerpts"
                    for d in answer_docs:
                        info = excerpts.get(d.id)
                        if info:
                            how = {
                                "rag": f"{len(retrieved.get(d.id, []))} retrieved passages",
                                "head_tail": "beginning and end",
                                "sampled": "beginning, middle and end",
                            }.get(info["mode"], info["mode"])
                            answer_tile.items.append({
                                "kind": "note",
                                "label": f"{d.filename}: {info['chars']:,} chars — excerpts ({how})",
                            })
                    yield {"tile": answer_tile.to_dict()}

                # Stream LLM (see _stream_answer). An empty/garbled answer or a
                # transient Ollama failure before any output (GPU memory
                # pressure) is retried once with the same model.
                answer_text = ""
                attempt = 0
                while True:
                    run = {"text": "", "emitted": False}
                    try:
                        async for ev in _stream_answer(
                            hooks, llm_model, prompt,
                            intent in ("translate", "redact", "summarize"), run,
                        ):
                            yield ev
                    except Exception as e:
                        if attempt == 0 and not run["emitted"] and _is_transient_llm_error(e):
                            attempt += 1
                            answer_tile.items.append({
                                "kind": "note",
                                "label": f"Model error ({str(e)[:80]}) — retrying once",
                            })
                            yield {"tile": answer_tile.to_dict()}
                            await asyncio.sleep(ANSWER_RETRY_DELAY_S)
                            continue
                        raise
                    visible = _visible_len(run["text"])
                    if visible <= ANSWER_MIN_VISIBLE_CHARS and attempt == 0:
                        attempt += 1
                        answer_tile.items.append({
                            "kind": "note",
                            "label": "Empty answer from the model — retrying once",
                        })
                        yield {"tile": answer_tile.to_dict()}
                        await asyncio.sleep(ANSWER_RETRY_DELAY_S)
                        continue
                    if visible == 0:
                        raise EmptyAnswerError(EMPTY_ANSWER_MESSAGE)
                    if not run["emitted"]:
                        # Short (≤ 3 chars) answer confirmed by the retry.
                        yield {"content": run["text"]}
                    answer_text = run["text"]
                    break

                answer_tile.status = "done"
                answer_tile.output_preview = answer_text[:300]
                answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                yield {"tile": answer_tile.to_dict()}

            except EmptyAnswerError as e:
                answer_tile.status = "error"
                answer_tile.detail = str(e)
                answer_tile.ms = hooks.now_ms() - answer_tile.started_ms
                yield {"tile": answer_tile.to_dict()}
                yield {"error": str(e)}
                return
            except Exception as e:
                answer_tile.status = "error"
                answer_tile.detail = str(e)[:200]
                yield {"tile": answer_tile.to_dict()}
                yield {"error": f"LLM failed: {str(e)}"}
                return

            # (d) Run web lookup if requested
            web_result = None  # Capture _web_result event for kg_ingest
            if web_targets:
                try:
                    # Extract person from the answer; fall back to the document
                    # itself (small JSON extraction by the answer model).
                    person = _doc_web.extract_person(answer_text, history)
                    if person is None and primary_docs and primary_docs[0].text:
                        person = await _doc_web.llm_extract_person(hooks, llm_model, primary_docs[0].text)
                        if person:
                            person = _doc_web.merge_person_with_headline(person, primary_docs[0].text)
                    if person is None:
                        # No person found; emit error tile
                        web_plan_tile = Tile(
                            id="web-plan",
                            kind="planner",
                            title="Query planner",
                            status="error",
                            detail=(
                                "Couldn't find a person's name in the answer to search for"
                                f" → {_doc_web.targets_label(web_targets)}"
                            ),
                        )
                        yield {"tile": web_plan_tile.to_dict()}
                        tiles_emitted["web-plan"] = web_plan_tile
                        web_search_tile = Tile(
                            id="web-search",
                            kind="web",
                            title="Web lookup",
                            status="skipped",
                            detail="No person found in the answer",
                        )
                        yield {"tile": web_search_tile.to_dict()}
                        tiles_emitted["web-search"] = web_search_tile
                        yield {"content": "\n\n---\n\n### Online verification\n\nI couldn't find a person's name in the document to search for."}
                    else:
                        # Prepare full doc text and intelligent excerpt for web lookup
                        doc_text = ""
                        doc_excerpt = ""
                        if primary_docs and primary_docs[0].text:
                            doc_text = primary_docs[0].text
                            # Build an intelligent excerpt (skip lines > 200 chars)
                            doc_excerpt = _doc_web.build_excerpt_for_verdict(doc_text)

                        # Run web lookup (will merge headline from full doc_text before creating planner tile)
                        async for web_event in _doc_web.run_web_lookup(
                            person,
                            web_targets,
                            hooks,
                            answer_model=llm_model,
                            now_ms=hooks.now_ms(),
                            doc_excerpt=doc_excerpt,
                            doc_text=doc_text,
                        ):
                            # Intercept _web_result internal events (don't forward to client)
                            if "_web_result" in web_event:
                                web_result = web_event.get("_web_result")
                            elif not any(k.startswith("_") for k in web_event.keys()):
                                # Only yield events that don't have keys starting with "_"
                                yield web_event
                                if "tile" in web_event:
                                    tile_dict = web_event["tile"]
                                    tiles_emitted[tile_dict.get("id")] = tile_dict
                except Exception as e:
                    log.error(f"Web lookup failed: {e}")
                    # Emit error tile but don't kill the already-streamed answer
                    web_search_tile = Tile(
                        id="web-search",
                        kind="web",
                        title="Web lookup",
                        status="error",
                        detail=str(e)[:100],
                    )
                    yield {"tile": web_search_tile.to_dict()}
                    tiles_emitted["web-search"] = web_search_tile
                    yield {"content": "\n\n---\n\n### Online verification\n\nWeb lookup failed — see details above."}

        # (e) Call kg_ingest if needed (before marking Laya tile done)
        # Only call for intents that populate the knowledge graph (not graph_query)
        if intent != "graph_query" and hooks.kg_ingest:
            try:
                # Determine person: prefer web_result person, else extract from answer if identify_person
                person_for_kg = None
                if web_result and web_result.get("person"):
                    person_for_kg = web_result["person"]
                elif intent == "identify_person":
                    # Extract person from answer text
                    person_for_kg = _doc_web.extract_person(answer_text, history)
                    # Merge with headline data (role/org) if available from first subject doc
                    if person_for_kg and primary_docs and primary_docs[0].text:
                        person_for_kg = _doc_web.merge_person_with_headline(person_for_kg, primary_docs[0].text)

                # Gather web candidates and verdict from web_result
                web_candidates = []
                verdict_text = ""
                if web_result:
                    web_candidates = web_result.get("candidates", [])
                    verdict_text = web_result.get("verdict", "")

                # identify_person over several documents/people: ingest every
                # person, each tied to the document(s) that actually name them.
                persons_for_kg = None
                if intent == "identify_person":
                    found = _doc_web.extract_persons(answer_text)
                    if len(found) > 1 or (found and len(answer_docs) > 1):
                        persons_for_kg = _tie_persons_to_docs(found, answer_docs)
                        if person_for_kg and person_for_kg.get("name"):
                            key = _normalize_for_dedup(person_for_kg["name"])
                            for p in persons_for_kg:
                                if _normalize_for_dedup(p["name"]) == key:
                                    p["role"] = p.get("role") or person_for_kg.get("role")
                                    p["org"] = p.get("org") or person_for_kg.get("org")

                # Company-registry extracts: organisation (trade name) owned by
                # the person named as owner — deterministic parse, no LLM call.
                organizations_for_kg: list[dict] = []
                if intent in REGISTRY_INGEST_INTENTS:
                    organizations_for_kg = _registry_organizations(answer_docs)
                if intent == "cross_reference" and organizations_for_kg:
                    registry_ids = {o["doc_id"] for o in organizations_for_kg}
                    owners = [o["owner"] for o in organizations_for_kg if o.get("owner")]
                    if owners and not persons_for_kg:
                        persons_for_kg = []
                        for tied in _tie_persons_to_docs(
                            [{"name": n} for n in dict.fromkeys(owners)], answer_docs,
                        ):
                            d = next((x for x in answer_docs if x.id == tied.get("doc_id")), None)
                            if d is not None and d.id not in registry_ids and d.text:
                                # e.g. the CV: its headline gives role / employer
                                tied = _doc_web.merge_person_with_headline(tied, d.text) or tied
                            persons_for_kg.append(tied)

                # Call kg_ingest with exact signature from kg_store.ingest_run
                ingest_kwargs = dict(
                    conversation_id=req.get("conversation_id"),
                    run_id=req.get("run_id"),
                    intent=intent,
                    subject_docs=answer_docs,
                    answer_text=answer_text,
                    person=person_for_kg,
                    web_candidates=web_candidates,
                    verdict=verdict_text,
                )
                if persons_for_kg:
                    ingest_kwargs["persons"] = persons_for_kg
                if organizations_for_kg:
                    ingest_kwargs["organizations"] = organizations_for_kg
                counts = await hooks.kg_ingest(**ingest_kwargs)

                # If ingest returned counts, emit a kg-ingest tile
                if counts and any(counts.get(k, 0) > 0 for k in ["entities", "relations", "mentions"]):
                    entities_count = counts.get("entities", 0)
                    relations_count = counts.get("relations", 0)
                    mentions_count = counts.get("mentions", 0)

                    kg_ingest_tile = Tile(
                        id="kg-ingest",
                        kind="store",
                        title="Knowledge store",
                        status="done",
                        detail=f"+{entities_count} entities · +{relations_count} relations · +{mentions_count} mentions",
                        items=[],
                    )

                    # Add entity items if available (up to 6)
                    entity_list = counts.get("entity_list", [])
                    if entity_list and isinstance(entity_list, list):
                        for entity in entity_list[:6]:
                            if isinstance(entity, dict):
                                name = entity.get("name", "Unknown")
                                entity_type = entity.get("type", "unknown")
                                kg_ingest_tile.items.append({
                                    "kind": "note",
                                    "label": f"{name} ({entity_type})"
                                })

                    yield {"tile": kg_ingest_tile.to_dict()}
                    tiles_emitted["kg-ingest"] = kg_ingest_tile

            except Exception as e:
                log.warning(f"kg_ingest failed: {e}")
                # Continue; don't let kg_ingest errors break the run

        # (f) Mark Laya tile as done
        laya_tile.status = "done"
        laya_tile.ms = hooks.now_ms() - laya_tile.started_ms
        yield {"tile": laya_tile.to_dict()}

        # Emit final done event
        yield {
            "done": True,
            "stats": {
                "total_ms": hooks.now_ms() - start_ms,
                "intent": intent,
                "tiles_count": len(tiles_emitted) + 2,  # +2 for laya and answer
            },
        }

    except asyncio.CancelledError:
        # Mark tiles as cancelled in memory only — never yield while being
        # cancelled (the consumer is gone; a yield here can hang/mask the cancel).
        if laya_tile:
            laya_tile.status = "error"
            laya_tile.detail = "Cancelled"
        for t in tiles_emitted.values():
            if isinstance(t, Tile) and t.status in ("pending", "running"):
                t.status = "error"
                t.detail = "Cancelled"
        raise


# ── Helper functions ──────────────────────────────────────────────────────

def _mime_to_kind(mime: str) -> str:
    """Convert MIME type to document kind."""
    if not mime:
        return "file"
    if "pdf" in mime:
        return "pdf"
    if "sheet" in mime or "csv" in mime or "excel" in mime or "tab-separated" in mime:
        return "xlsx"
    if "word" in mime or "document" in mime:
        return "docx"
    if mime.startswith("image"):
        return "image"
    if mime == "message/rfc822" or "email" in mime:
        return "email"
    if "text" in mime:
        return "text"
    return "file"


def _cached_doc_kind(doc: Any) -> Optional[dict]:
    """A document kind already known for this doc: doc.meta["doc_kind"] (cached
    by an earlier agent run) or the /api/idp/route decision
    (doc.meta["route"]["decision"]). None when unknown."""
    meta = getattr(doc, "meta", None)
    if not isinstance(meta, dict):
        return None
    cached = meta.get("doc_kind")
    if isinstance(cached, dict) and cached.get("kind"):
        return cached
    route = meta.get("route")
    if isinstance(route, dict):
        decision = route.get("decision")
        if isinstance(decision, dict) and decision.get("kind"):
            return {
                "kind": decision["kind"],
                "confidence": decision.get("confidence") or 0.0,
            }
    return None


def _cache_doc_kind(doc: Any, result: Optional[dict]) -> None:
    """Remember a computed doc kind on doc.meta (in memory only — persistence
    is left to the existing registry save paths)."""
    if not result or not result.get("kind"):
        return
    meta = getattr(doc, "meta", None)
    if not isinstance(meta, dict):
        return
    meta["doc_kind"] = {
        "kind": result.get("kind"),
        "confidence": result.get("confidence"),
    }


def _tie_persons_to_docs(persons: list[dict], docs: list[Any]) -> list[dict]:
    """Attach doc_id to each person whose name occurs in exactly the subset of
    documents that name them (one entry per (person, document)); a person found
    in no document text is tied to all documents (no doc_id)."""
    out: list[dict] = []
    for p in persons:
        key = _normalize_for_dedup(p.get("name") or "")
        if not key:
            continue
        hits = [d for d in docs if key in _normalize_for_dedup(getattr(d, "text", "") or "")]
        if not hits:
            out.append({**p})
            continue
        for d in hits:
            out.append({**p, "doc_id": d.id})
    return out


def _find_role(doc: Any, laya_tile: Tile, docs: list[tuple[Any, dict]]) -> str:
    """Find the role assigned to a document in the Laya tile decisions."""
    for decision in laya_tile.decisions:
        if decision.id == f"role-{doc.id}":
            return decision.value
    return "subject"


def _is_junk_graph_entity(entity: dict) -> bool:
    """Profile without an http(s) URL, or a generic/fragment role — never a fact."""
    etype = entity.get("type")
    if etype == "profile":
        url = str((entity.get("props") or {}).get("url") or "")
        return not url.lower().startswith(("http://", "https://"))
    if etype == "role":
        import doc_web as _doc_web
        return _doc_web._is_generic_role(entity.get("name") or "")
    return False


# ── Company-registry extract parsing (deterministic, no LLM) ──────────────

# Intents whose runs ingest organisations found in company-registry extracts
REGISTRY_INGEST_INTENTS = {"cross_reference", "identify_person", "general_question"}

_REGISTRY_MARKERS_RE = re.compile(
    r"trade\s*name|handelsnaam|legal\s+form|rechtsvorm|(?<!\w)kvk(?!\w)|kamer\s+van\s+koophandel|"
    r"chamber\s+of\s+commerce|registry\s+extract|register\s+extract|company\s+registry|"
    r"uittreksel(?:\s+handelsregister)?|handelsregister",
    re.IGNORECASE,
)
_DEMO_MARKERS_RE = re.compile(
    r"(?<!\w)demo(?!\w)|fictitious|fictional|fictief|made[\s-]up\s+data|not\s+issued\s+by|"
    r"specimen\s+only|sample\s+document",
    re.IGNORECASE,
)
# label regex → field. A label may carry a parenthetical ("Address (fictitious)").
_REGISTRY_LABELS: list[tuple[str, str]] = [
    ("trade_name", r"trade\s*names?|handelsnaa?m(?:en)?|company\s+name|business\s+name|statutaire\s+naam"),
    ("legal_form", r"legal\s+form|rechtsvorm"),
    ("registered_since", r"date\s+of\s+registration|registration\s+date|registered\s+(?:on|since)|"
                         r"datum\s+(?:van\s+)?inschrijving|date\s+of\s+incorporation|oprichtingsdatum|"
                         r"datum\s+oprichting|start\s+date|startdatum"),
    ("registry_number", r"(?:kvk|coc|registry|registration|chamber\s+of\s+commerce)[\s-]*(?:number|nummer|no\.?)"),
    ("status", r"status"),
    ("sbi", r"sbi[\s-]*codes?|sbi"),
    ("activities", r"business\s+activities|activities|activiteiten|description|omschrijving"),
    ("address", r"(?:visiting\s+|business\s+|vestigings)?address|(?:vestigings)?adres"),
    ("owner_name", r"(?:owner|eigenaar|proprietor)\s*(?:/|-)?\s*(?:name|naam)|(?:name|naam)\s+(?:owner|eigenaar)"),
    ("name", r"name|naam"),
    ("owner_role", r"role|rol|function|functie|position"),
    ("authority", r"authority|bevoegdheid|authori[sz]ation"),
    ("since", r"since|sinds|in\s+function\s+since|per"),
    ("owner_inline", r"owner|eigenaar|proprietor"),
]
_REGISTRY_LABEL_RES = [
    (field, re.compile(
        r"^(?:" + rx + r")(?:\s*\([^)]*\))?(?:\s*[:：\-–—]\s*|\s+|$)(?P<value>.*)$", re.IGNORECASE,
    ))
    for field, rx in _REGISTRY_LABELS
]
_OWNER_SECTION_RE = re.compile(
    r"^(?:owners?|eigena(?:a|ren)r?|proprietors?|authori[sz]ed\s+(?:persons?|signator(?:y|ies))|"
    r"functionaris(?:sen)?|bestuurders?|directors?)\b",
    re.IGNORECASE,
)


def _match_registry_label(line: str) -> tuple[Optional[str], str]:
    """(field, inline value) when `line` starts with a known label."""
    for field, rx in _REGISTRY_LABEL_RES:
        m = rx.match(line)
        if m:
            return field, (m.group("value") or "").strip()
    return None, ""


def looks_like_registry(text: str) -> bool:
    """True when a document looks like a company-registry extract."""
    return bool(text) and bool(_REGISTRY_MARKERS_RE.search(text))


def is_demo_document(text: str) -> bool:
    """True when a document says it is a demo / fictitious / not officially issued."""
    return bool(text) and bool(_DEMO_MARKERS_RE.search(text))


def parse_registry_extract(text: str) -> Optional[dict[str, Any]]:
    """
    Parse a company-registry extract (KvK-style) with a small deterministic
    label/value parser. Labels and values may share a line ("Trade name Acme",
    "Legal form: Eenmanszaak") or the value may follow on the next line.

    Returns {"trade_name", "legal_form", "registered_since", "registry_number",
    "status", "sbi", "activities", "address", "owner", "owner_role",
    "owner_since", "demo"} (missing fields omitted, "demo" always present) or
    None when the text is not a registry extract or has no trade name.
    """
    if not text or not looks_like_registry(text):
        return None
    import doc_web as _dw

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out: dict[str, Any] = {}
    in_owner = False

    def _value_after(i: int, inline: str, multiline: bool = False) -> tuple[str, int]:
        """Inline value, else the next line(s) that are not labels themselves."""
        if inline:
            return inline, i
        if i + 1 >= len(lines):
            return "", i
        nxt = lines[i + 1]
        if _match_registry_label(nxt)[0] and _match_registry_label(nxt)[0] not in ("owner_inline",):
            return "", i
        val, j = nxt, i + 1
        while multiline and j + 1 < len(lines):
            cont = lines[j + 1]
            if _match_registry_label(cont)[0] or not cont[:1].islower():
                break
            val, j = f"{val} {cont}", j + 1
        return val, j

    def _clean(v: str) -> str:
        return re.sub(r"\s+", " ", v).strip(" \t:;,")

    i = 0
    while i < len(lines):
        line = lines[i]
        field, inline = _match_registry_label(line)
        if field is None:
            i += 1
            continue
        if field == "owner_inline":
            # "Owner / authorised person" (section header), "Owner (eigenaar)"
            # (a role value) or "Owner: Jane Example" / "Owner Jane Example".
            cand = _dw._strip_name_decorations(inline.lstrip("/ ").strip())
            if cand and _dw._is_proper_name(cand) and "owner" not in out:
                out["owner"] = cand
                in_owner = True
            elif _OWNER_SECTION_RE.match(line):
                in_owner = True
            i += 1
            continue
        if field in ("name",) and not in_owner:
            i += 1
            continue
        if field in ("owner_role", "authority", "since") and not in_owner:
            i += 1
            continue
        value, j = _value_after(i, inline, multiline=(field == "activities"))
        value = _clean(value)
        if value:
            if field in ("owner_name", "name"):
                cand = _dw._strip_name_decorations(value)
                if cand and _dw._is_valid_name(cand) and "owner" not in out:
                    out["owner"] = cand
                    in_owner = True
            elif field == "owner_role":
                out.setdefault("owner_role", value)
            elif field == "since":
                out.setdefault("owner_since", value)
            elif field == "authority":
                out.setdefault("owner_authority", value)
            elif field == "activities":
                prev = out.get("activities")
                if not prev:
                    out["activities"] = value
                elif value not in prev and len(prev) < 400:
                    out["activities"] = f"{prev} — {value}"
            else:
                out.setdefault(field, value)
        i = j + 1

    if not out.get("trade_name"):
        return None
    out["trade_name"] = re.sub(r"\s*\((?:demo|fictitious|fictief)\)\s*$", "", out["trade_name"], flags=re.IGNORECASE)
    out["demo"] = is_demo_document(text)
    return out


def _registry_organizations(docs: list[Any]) -> list[dict[str, Any]]:
    """Organisations (with owner) parsed from the registry-extract documents."""
    orgs: list[dict[str, Any]] = []
    for d in docs:
        text = getattr(d, "text", "") or ""
        try:
            parsed = parse_registry_extract(text)
        except Exception as e:  # never break a run on a parse problem
            log.debug(f"registry parse failed for {getattr(d, 'id', '?')}: {e}")
            parsed = None
        if not parsed:
            continue
        org = {
            "name": parsed["trade_name"],
            "doc_id": getattr(d, "id", None),
            "owner": parsed.get("owner"),
            "legal_form": parsed.get("legal_form"),
            "registered_since": parsed.get("registered_since") or parsed.get("owner_since"),
            "demo": bool(parsed.get("demo")),
        }
        for extra in ("sbi", "activities", "status", "address", "registry_number", "owner_role"):
            if parsed.get(extra):
                org[extra] = parsed[extra]
        orgs.append(org)
    return orgs


# Intents that answer from specific passages → retrieval over long documents
RAG_INTENTS = {"general_question", "extract_data", "identify_person", "cross_reference"}
PROMPT_MAX_CHARS = 24000
RAG_HEAD_CHARS = 1500
RAG_TOP_K = 8

# Empty/garbled answers (Ollama under GPU memory pressure) → one retry
ANSWER_RETRY_DELAY_S = 2.0
ANSWER_MIN_VISIBLE_CHARS = 3
EMPTY_ANSWER_MESSAGE = "The model returned an empty answer (Ollama may be low on memory — try again)"
_TRANSIENT_LLM_ERROR_RE = re.compile(
    r"\b500\b|out of memory|\boom\b|\beof\b|unexpected end|connection reset|server disconnected",
    re.IGNORECASE,
)


class EmptyAnswerError(RuntimeError):
    """The answer model produced no visible text (even after a retry)."""


def _visible_len(text: str) -> int:
    """Number of non-whitespace characters."""
    return len(re.sub(r"\s+", "", text or ""))


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Ollama failures worth one retry: HTTP 500, out of memory, truncated stream."""
    return bool(_TRANSIENT_LLM_ERROR_RE.search(str(exc) or ""))


async def _stream_answer(
    hooks: Any, model: str, prompt: str, strip_wrapper: bool, run: dict,
) -> AsyncIterator[dict]:
    """
    One streaming pass of the answer model. Yields {"thinking"} / {"content"}
    events and accumulates the answer in run["text"]. Content is held back
    until it has more than ANSWER_MIN_VISIBLE_CHARS visible characters, so an
    empty or garbled pass ("", "Tab") can be retried without the client having
    seen it; run["emitted"] tells whether any content reached the client.
    For translate/redact/summarize the first line is also held until we know
    it isn't an echoed "=== file ===" wrapper.
    """
    pending = ""   # wrapper hold-back
    held = ""      # short-answer hold-back

    def _release(chunk: str) -> Optional[str]:
        nonlocal held
        run["text"] += chunk
        if run["emitted"]:
            return chunk
        held += chunk
        if _visible_len(held) > ANSWER_MIN_VISIBLE_CHARS:
            out, held = held, ""
            run["emitted"] = True
            return out
        return None

    async for delta in hooks.stream_llm(model, prompt, think=False):
        if "thinking" in delta:
            yield {"thinking": delta["thinking"]}
        elif "content" in delta:
            chunk = delta["content"]
            if strip_wrapper:
                pending += chunk
                head = pending.lstrip()
                if not head or (
                    head[0] in "=<" and "\n" not in head and len(pending) < 400
                ):
                    continue  # might still be a wrapper line
                chunk = strip_leading_wrapper(pending)
                pending = ""
                strip_wrapper = False
                if not chunk:
                    continue
            out = _release(chunk)
            if out:
                yield {"content": out}
        elif delta.get("done"):
            break
    if pending:
        chunk = strip_leading_wrapper(pending)
        if chunk:
            out = _release(chunk)
            if out:
                yield {"content": out}


# ── Spreadsheet table queries ───────────────────────────────────────────────

SHEET_EXTS = (".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv")
TABLE_QUERY_INTENTS = {"general_question", "extract_data"}
# Words that make a question about a spreadsheet a computation ("table_question")
TABLE_QUESTION_KWS = [
    "total", "totals", "sum", "sums", "average", "averages", "avg", "mean", "median",
    "highest", "lowest", "top", "bottom", "how many", "how much", "count", "number of",
    "per", "by", "group*", "max", "maximum", "min", "minimum", "rank*", "largest",
    "smallest", "biggest", "most", "least", "trend*", "compare", "comparison",
    "greater than", "more than", "less than", "above", "below", "between", "sort*",
    "gemiddeld*", "totaal", "hoogste", "laagste", "hoeveel", "aantal", "grootste", "kleinste",
    "moyenne", "somme", "combien",
]
TABLE_RESULT_ITEMS = 10
TABLE_PROMPT_HEAD_ROWS = 5
TABLE_FALLBACK_HEAD_ROWS = 20
TABLE_QUERY_NUM_PREDICT = 512
TEXT_ROW_CAP_FOR_PROMPT = 300   # sheets.TEXT_ROW_CAP

TABLE_QUERY_PROMPT = """You translate a question about a spreadsheet into a JSON query. The query is executed exactly with pandas, so you never calculate anything yourself.

Output ONLY one JSON object — no prose, no code fences, no comments. Keys (all optional):
{{
  "sheet": "<sheet name>" | null,
  "filters": [{{"column": "<column>", "op": "==|!=|>|>=|<|<=|contains|in|between", "value": <value | [values] | [low, high]>}}],
  "row_total": {{"columns": ["<col>", "..."], "as": "<name>"}},
  "derive": [{{"as": "<new column>", "expr": {{"op": "mul|div|add|sub", "left": <column | number | expr>, "right": <column | number | expr>}}}}],
  "group_by": ["<column>"],
  "aggregate": [{{"column": "<column>", "fn": "sum|mean|min|max|count|median|nunique"}}],
  "select": ["<column>"],
  "sort": [{{"column": "<column or aggregate like sum(Sales)>", "desc": true}}],
  "limit": <integer> | null
}}

Order of execution: filters → row_total → derive → group_by/aggregate → sort → limit → select.

Rules:
- Use column and sheet names exactly as listed below.
- "row_total" ONLY adds columns: a per-row sum across several columns of a wide table (e.g. Q1..Q4 → Total); you can then select, sort or aggregate "Total". If the sheet already has a matching total/formula column, use that column instead.
- Use "derive" for multiplication/division (products, ratios, percentages, price × quantity, value per unit): e.g. stock value = {{"derive": [{{"as": "Value", "expr": {{"op": "mul", "left": "Stock", "right": "Unit price"}}}}]}}, then "aggregate": [{{"column": "Value", "fn": "sum"}}] for the total. Expressions nest (max 3 levels), e.g. a percentage = {{"op": "mul", "left": {{"op": "div", "left": "A", "right": "B"}}, "right": 100}}. Never use row_total for a product or ratio.
- Totals/averages/counts use "aggregate"; "per X" / "by X" uses "group_by": ["X"].
- "highest"/"top N" = sort desc + limit; "lowest" = sort asc + limit. You may sort by a column that is not in "select".
- Dates are ISO strings (yyyy-mm-dd): compare them with >=, <= or between.
- To list matching rows use filters + select (+ sort/limit).
- Hidden sheets are not available; never query them.
{context}
SHEETS:
{schema}

QUESTION: {question}

JSON:"""

TABLE_CONTEXT_TURNS = 4          # the last 2 user/assistant exchanges
TABLE_CONTEXT_TURN_CHARS = 300


def _table_conversation_context(history: Optional[list[dict]], question: str) -> str:
    """The last 2 user/assistant turns (short) for the spec prompt, so a
    follow-up like "and the maximum?" is resolved against the previous
    question. A turn may carry the previous query ("table_query")."""
    turns = [t for t in (history or []) if isinstance(t, dict) and t.get("role") in ("user", "assistant")]
    # the current message is never its own context
    if turns and turns[-1].get("role") == "user" and (turns[-1].get("content") or "").strip() == (question or "").strip():
        turns = turns[:-1]
    turns = turns[-TABLE_CONTEXT_TURNS:]
    if not turns:
        return ""
    lines = []
    prev_question = ""
    for t in turns:
        content = re.sub(r"\s+", " ", str(t.get("content") or "")).strip()
        if len(content) > TABLE_CONTEXT_TURN_CHARS:
            content = content[:TABLE_CONTEXT_TURN_CHARS - 1] + "…"
        lines.append(f"{t['role']}: {content}")
        spec = t.get("table_query")
        if spec:
            spec_text = spec if isinstance(spec, str) else json.dumps(spec, ensure_ascii=False, default=str)
            lines.append(f"(previous table query: {spec_text[:TABLE_CONTEXT_TURN_CHARS]})")
        if t["role"] == "user" and content:
            prev_question = content
    out = "\nCONVERSATION CONTEXT (earlier turns):\n" + "\n".join(lines)
    if prev_question:
        out += f"\nPrevious question: {prev_question}"
    out += (
        "\nResolve references like 'and the maximum?' using the previous question: keep its sheet, "
        "columns, filters and grouping and change only what the new question asks.\n"
    )
    return out


def is_sheet_doc(doc: Any) -> bool:
    """A spreadsheet document: parsed sheet meta, a spreadsheet extension or MIME."""
    meta = getattr(doc, "meta", None)
    if isinstance(meta, dict) and isinstance(meta.get("sheets"), (list, dict)) and meta["sheets"]:
        return True
    name = (getattr(doc, "filename", "") or "").lower()
    if name.endswith(SHEET_EXTS):
        return True
    return _mime_to_kind(getattr(doc, "mime", "") or "") == "xlsx"


def table_question_hits(message: str) -> list[str]:
    """Rule-detected "table_question" keywords (totals, averages, top, per, …)."""
    return _kw_hits((message or "").lower(), TABLE_QUESTION_KWS)


def _mentioned_columns(message: str, sheets: list[dict]) -> list[str]:
    msg = (message or "").lower()
    found: list[str] = []
    for sh in sheets or []:
        if sh.get("hidden"):
            continue
        for c in list(sh["df"].columns):
            name = str(c).strip().lower()
            if len(name) >= 3 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", msg):
                found.append(str(c))
    return list(dict.fromkeys(found))


def _stream_kwargs(stream_fn: Any, **wanted: Any) -> dict[str, Any]:
    """Only the keyword arguments the stream_llm hook accepts (temperature, …)."""
    import inspect
    try:
        params = inspect.signature(stream_fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return dict(wanted)
    return {k: v for k, v in wanted.items() if k in params}


async def _collect_llm(hooks: Any, model: str, prompt: str) -> str:
    """Run the model once, non-thinking, low temperature; return its content."""
    kwargs = _stream_kwargs(hooks.stream_llm, temperature=0.0, num_predict=TABLE_QUERY_NUM_PREDICT)
    out = ""
    async for delta in hooks.stream_llm(model, prompt, think=False, **kwargs):
        if "content" in delta:
            out += delta["content"] or ""
        elif delta.get("done"):
            break
    return out


def _sheet_overview(sheets: list[dict], head_rows: int) -> str:
    """Schema + first rows + per-column stats of every visible sheet (never the
    whole sheet)."""
    import sheets as _sheets
    blocks: list[str] = []
    for sh in sheets or []:
        if sh.get("hidden"):
            continue
        df = sh["df"]
        dtypes = sh.get("dtypes") or {}
        lines = [_sheets.sheet_title(sh)]
        lines.extend(sh.get("preamble") or [])
        lines.append("Columns: " + ", ".join(f"{c} ({dtypes.get(c, 'text')})" for c in df.columns))
        if len(df.columns):
            shown = min(head_rows, len(df))
            lines.append(f"First {shown} of {len(df)} rows:")
            lines.append(_sheets.markdown_table(
                [str(c) for c in df.columns], df.head(head_rows).itertuples(index=False, name=None),
                set(_sheets.numeric_columns(sh)),
            ))
        st = _sheets.stats_line(sh)
        if st:
            lines.append(st)
        if sh.get("formula_columns"):
            lines.append("Formula columns: " + ", ".join(sh["formula_columns"]))
        blocks.append("\n".join(lines))
    note = _tq.hidden_sheet_note(sheets or [])
    if note:
        blocks.append(note)
    return "\n\n".join(blocks)


def _sheet_fallback_content(sheets: list[dict]) -> str:
    """Current behaviour when no query result is available: the sheet tables
    with per-column stats — but a large sheet (> TEXT_ROW_CAP rows) is never
    dumped whole (schema + sample + stats only)."""
    import sheets as _sheets
    total = sum(len(sh["df"]) for sh in sheets or [] if not sh.get("hidden"))
    if total <= _sheets.TEXT_ROW_CAP:
        pages = "\n\n".join(_sheets.render_pages(sheets))
        note = _tq.hidden_sheet_note(sheets or [])
        return pages + ("\n\n" + note if note else "")
    return _sheet_overview(sheets, TABLE_FALLBACK_HEAD_ROWS)


def _visible_rows(sheets: list[dict]) -> int:
    return sum(len(sh["df"]) for sh in sheets or [] if not sh.get("hidden"))


def _rows_not_sent(sheets: list[dict], head_rows: int) -> int:
    """Rows of the visible sheets left out of an overview with `head_rows`."""
    return sum(max(0, len(sh["df"]) - head_rows) for sh in sheets or [] if not sh.get("hidden"))


def _visible_sheet_mentioned(message: str, sheets: list[dict]) -> bool:
    msg = (message or "").lower()
    for sh in sheets or []:
        if sh.get("hidden"):
            continue
        name = str(sh.get("name") or "").strip().lower()
        if len(name) >= 2 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", msg):
            return True
    return False


def hidden_sheets_mentioned(message: str, sheets: list[dict]) -> list[str]:
    """Hidden sheets the user names in the message (word-bounded, case-
    insensitive; "the Secret sheet", "sheet secret", "'Secret'")."""
    msg = (message or "").lower()
    out = []
    for sh in sheets or []:
        if not sh.get("hidden"):
            continue
        name = str(sh.get("name") or "").strip()
        if name and re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", msg):
            out.append(name)
    return out


HIDDEN_SHEET_NOTE = (
    "The user asks about a sheet named {names}. That sheet exists in the workbook but is HIDDEN: "
    "its contents are deliberately not provided. Say that the sheet is hidden and its contents are "
    "not shown — do not claim the sheet does not exist, and do not guess what it contains."
)


async def _table_query_step(
    hooks: Any, model: str, doc: Any, question: str, sheets: list[dict], state: dict,
    tile: Optional[Tile] = None, history: Optional[list[dict]] = None,
) -> AsyncIterator[dict]:
    """The "Table query" tile: the answer model writes a JSON query spec,
    table_query executes it with pandas (one retry with the validation error).
    On success state["result"] holds the execute() result; on failure
    state["error"] holds the reason."""
    if tile is None:
        tile = Tile(id="table-query", kind="table", title="Table query", status="pending")
    tile.status = "running"
    tile.model = model
    tile.started_ms = hooks.now_ms()
    tile.doc = {"doc_id": doc.id, "name": doc.filename}
    tile.detail = "Writing a query for the sheet"
    yield {"tile": tile.to_dict()}

    base_prompt = TABLE_QUERY_PROMPT.format(
        schema=_tq.schema_text(sheets), question=question.strip(),
        context=_table_conversation_context(history, question),
    )
    prompt = base_prompt
    error: Optional[str] = None
    result: Optional[dict] = None
    raw = ""
    for attempt in range(2):
        try:
            raw = await _collect_llm(hooks, model, prompt)
        except Exception as e:
            error = f"Model error: {str(e)[:160]}"
            break
        try:
            spec = _tq.parse_spec(raw)
            _tq.check_spec_for_question(question, spec)
            result = await asyncio.to_thread(_tq.execute, sheets, spec)
            error = None
            break
        except _tq.QueryError as e:
            error = str(e)
        except Exception as e:  # pandas edge case — report, don't crash the run
            error = f"Query failed: {str(e)[:160]}"
        if attempt == 0:
            tile.items.append({"kind": "note", "label": f"Retrying: {error[:140]}"})
            yield {"tile": tile.to_dict()}
            prompt = (
                base_prompt + "\n" + raw.strip()[:800]
                + f"\n\nThat query failed: {error}\n"
                + "Return a corrected JSON object only.\n\nJSON:"
            )

    tile.ms = hooks.now_ms() - tile.started_ms
    if result is None:
        tile.status = "error"
        tile.detail = "Query failed — answering from the sheet data instead"
        tile.items.append({"kind": "note", "label": (error or "No query produced")[:200]})
        state["error"] = error or "No query produced"
        yield {"tile": tile.to_dict()}
        return

    n = result["row_count"]
    tile.status = "done"
    tile.detail = f"{n} row{'s' if n != 1 else ''} · sheet {result['sheet']}"
    tile.items = [{
        "kind": "query",
        "label": _tq.spec_summary(result["spec"]),
        "columns": result["columns"],
    }]
    for row in result["rows"][:TABLE_RESULT_ITEMS]:
        tile.items.append({
            "kind": "result",
            "label": _tq.render_row(result["columns"], row),
            "row": row,
        })
    if n > TABLE_RESULT_ITEMS:
        tile.items.append({"kind": "note", "label": f"+{n - TABLE_RESULT_ITEMS} more rows in the result"})
    tile.output_preview = result["markdown"][:300]
    state["result"] = result
    yield {"tile": tile.to_dict()}


COMPUTED_RESULT_NOTE = (
    "The COMPUTED RESULT above is part of the document content: it was calculated exactly from the full spreadsheet. "
    "Use the COMPUTED RESULT for all numbers; do not recalculate, re-add or estimate them from "
    "the sample rows (they show only part of the sheet). Quote the figures as given and "
    "mention the relevant column/sheet. If the result does not answer the question, say what it shows."
)

ONLINE_LOOKUP_NOTE = (
    "An online lookup is performed separately after your answer — "
    "do not say you lack internet access."
)


def _doc_budget(n_docs: int, max_chars: int = PROMPT_MAX_CHARS) -> int:
    """Per-document character budget."""
    return max_chars // max(1, n_docs)


def _order_chunks(text: str, chunks: list[Any]) -> list[str]:
    """De-duplicate retrieved chunks and put them in document order."""
    seen: set = set()
    items: list[tuple[int, int, str]] = []
    for i, c in enumerate(chunks or []):
        if isinstance(c, dict):
            c_text = c.get("text") or ""
            pos = c.get("position")
        else:
            c_text = str(c or "")
            pos = None
        c_text = c_text.strip()
        if not c_text or c_text in seen:
            continue
        seen.add(c_text)
        where = text.find(c_text[:200])
        if where < 0:
            where = len(text) + (pos if isinstance(pos, int) else i)
        items.append((where, i, c_text))
    items.sort()
    return [t for _, _, t in items]


PAGE_MARKER_RE = re.compile(r"^--- Page \d+ ---")


def _paged_text(doc: Any) -> tuple[Optional[str], list[tuple[int, int]]]:
    """
    Document text with "--- Page N ---" markers when the document has at least
    two non-empty pages whose text matches doc.text (so OCR/edited text that no
    longer maps onto pages is never mislabelled). Returns (paged_text, starts)
    where starts is [(offset_of_marker, page_no), …]; (None, []) otherwise.
    """
    pages = getattr(doc, "page_texts", None) or []
    if not isinstance(pages, (list, tuple)):
        return None, []
    pages = [p if isinstance(p, str) else "" for p in pages]
    if sum(1 for p in pages if p.strip()) < 2:
        return None, []
    text = getattr(doc, "text", "") or ""
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip()
    if norm("\n\n".join(pages)) != norm(text):
        return None, []
    parts: list[str] = []
    starts: list[tuple[int, int]] = []
    offset = 0
    for i, p in enumerate(pages, 1):
        body = p.strip()
        if not body:
            continue
        block = f"--- Page {i} ---\n{body}"
        if parts:
            offset += 2  # "\n\n" separator
        starts.append((offset, i))
        parts.append(block)
        offset += len(block)
    return "\n\n".join(parts), starts


def _page_label(start: int, page_starts: Optional[list[tuple[int, int]]], segment: str) -> str:
    """Prefix an excerpt segment with the marker of the page it starts on."""
    if not page_starts or start < 0 or PAGE_MARKER_RE.match(segment.lstrip()):
        return segment
    page_no = None
    for off, n in page_starts:
        if off <= start:
            page_no = n
        else:
            break
    if page_no is None:
        return segment
    return f"--- Page {page_no} ---\n" + segment


def _excerpt_doc(
    text: str,
    budget: int,
    intent: str,
    chunks: Optional[list[Any]],
    page_starts: Optional[list[tuple[int, int]]] = None,
) -> tuple[str, Optional[str]]:
    """
    Fit one document into `budget` chars. Returns (content, mode) where mode is
    None (full text), "rag" (head + retrieved passages), "head_tail"
    (60% head + 40% tail) or "sampled" (head + middle + tail).
    With `page_starts` (from _paged_text) every excerpt segment keeps the
    "--- Page N ---" marker of the page it starts on.
    """
    if len(text) <= budget:
        return text, None
    lab = lambda start, seg: _page_label(start, page_starts, seg)
    if intent in RAG_INTENTS:
        ordered = _order_chunks(text, chunks or [])
        if ordered:
            head = text[:min(RAG_HEAD_CHARS, budget)]
            parts = [lab(0, head)]
            used = len(head)
            for c in ordered:
                if c in head:
                    continue
                pos = text.find(c[:200])
                if used + len(c) + 3 > budget:
                    remaining = budget - used - 3
                    if remaining > 200:
                        parts.append(lab(pos, c[:remaining]))
                    break
                parts.append(lab(pos, c))
                used += len(c) + 3
            if len(parts) > 1:
                return "\n…\n".join(parts), "rag"
        head_n = int(budget * 0.6)
        tail_n = budget - head_n
        return (
            lab(0, text[:head_n]) + "\n[…]\n" + lab(len(text) - tail_n, text[-tail_n:]),
            "head_tail",
        )
    third = budget // 3
    mid = len(text) // 2
    mid_start = max(0, mid - third // 2)
    middle = text[mid_start: mid - third // 2 + third]
    return (
        lab(0, text[:third]) + "\n[…]\n" + lab(mid_start, middle) + "\n[…]\n"
        + lab(len(text) - third, text[-third:]),
        "sampled",
    )


def _doc_block(filename: str, content: str, role: Optional[str] = None) -> str:
    """Wrap a document in a delimiter models don't echo back (reference
    documents carry role="reference")."""
    name = (filename or "document").replace('"', "'")
    role_attr = f' role="{role}"' if role else ""
    return f'<document name="{name}"{role_attr}>\n{content}\n</document>'


REFERENCE_DOCS_NOTE = (
    'Documents marked role="reference" were attached by the user as supporting/reference '
    "material: use their content too (e.g. to check the other document's claims against them)."
)


def _build_prompt_ex(
    intent: str,
    docs: list[Any],
    message: str,
    history: list[dict],
    max_chars: int = PROMPT_MAX_CHARS,
    retrieved: Optional[dict[str, list[Any]]] = None,
    web_lookup_on: bool = False,
    doc_overrides: Optional[dict[str, str]] = None,
    computed_result: Optional[str] = None,
    task_notes: Optional[list[str]] = None,
    doc_roles: Optional[dict[str, str]] = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """
    Build the LLM prompt. Returns (prompt, excerpts) where excerpts maps
    doc_id → {"chars": N, "mode": "rag"|"head_tail"|"sampled"} for every
    document that did not fit its budget (max_chars // number of docs).
    `doc_overrides` (doc_id → content) replaces a document's text as-is
    (spreadsheet schema/sample); `computed_result` is an exact table-query
    result the model must use for all numbers. `doc_roles` (doc_id → "reference")
    labels reference documents in their <document> block.
    """
    doc_overrides = doc_overrides or {}
    doc_roles = doc_roles or {}
    retrieved = retrieved or {}
    budget = _doc_budget(len([d for d in docs if getattr(d, "text", "")]) or 1, max_chars)
    doc_texts = []
    excerpts: dict[str, dict[str, Any]] = {}
    any_paged = False
    for doc in docs:
        text = getattr(doc, "text", "") or ""
        override = doc_overrides.get(getattr(doc, "id", None))
        if override is not None:
            doc_texts.append(_doc_block(doc.filename, override[: budget + 2000], doc_roles.get(doc.id)))
            continue
        if not text:
            continue
        # Page markers help page questions; translate/redact reproduce the
        # document, so they get the plain text.
        paged, page_starts = (None, []) if intent in ("translate", "redact") else _paged_text(doc)
        content, mode = _excerpt_doc(
            paged or text, budget, intent, retrieved.get(doc.id),
            page_starts=page_starts or None,
        )
        any_paged = any_paged or bool(paged)
        if mode:
            excerpts[doc.id] = {"chars": len(text), "mode": mode}
        doc_texts.append(_doc_block(doc.filename, content, doc_roles.get(doc.id)))

    full_context = "\n\n".join(doc_texts)
    hard_cap = max_chars + 2000  # wrappers + separators
    if len(full_context) > hard_cap:
        full_context = full_context[:hard_cap] + "\n\n[... truncated ...]"

    # Add history (last 6 turns)
    history_text = ""
    if history:
        recent_history = history[-6:]
        history_lines = []
        for turn in recent_history:
            role = turn.get("role", "unknown")
            content = (turn.get("content") or "")[:500]  # Trim
            history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines)

    # Select prompt template
    prompt_template = {
        "identify_person": IDENTIFY_PERSON_PROMPT,
        "summarize": SUMMARIZE_PROMPT,
        "extract_data": EXTRACT_DATA_PROMPT,
        "translate": _build_translate_prompt(message),
        "redact": REDACT_PROMPT,
        "general_question": GENERAL_QUESTION_PROMPT,
        "graph_query": GRAPH_QUERY_PROMPT,
        "cross_reference": _build_cross_reference_prompt(docs),
    }.get(intent)

    if not prompt_template and intent != "general_question":
        prompt_template = IDENTIFY_PERSON_PROMPT

    if excerpts:
        note = (
            "Some documents are long; only excerpts are shown (gaps are marked with … or […]). "
            "If the answer is not in the excerpts, say so."
        )
        prompt_template = (prompt_template + "\n\n" + note) if prompt_template else note
    if any_paged:
        page_note = 'Page boundaries are marked with "--- Page N ---" lines; use them for questions about specific pages.'
        prompt_template = (prompt_template + "\n" + page_note) if prompt_template else page_note
    if intent in ("translate", "redact", "summarize"):
        prompt_template += (
            "\nThe document is enclosed in <document> tags. Output only the result — "
            "do not repeat the tags, the file name or any header."
        )
    if any(doc_roles.get(getattr(d, "id", None)) == "reference" for d in docs):
        prompt_template = (prompt_template + "\n" if prompt_template else "") + REFERENCE_DOCS_NOTE
    if web_lookup_on:
        prompt_template = (prompt_template + "\n" + ONLINE_LOOKUP_NOTE) if prompt_template else ONLINE_LOOKUP_NOTE
    if computed_result:
        prompt_template = (prompt_template + "\n" if prompt_template else "") + COMPUTED_RESULT_NOTE
    for note in task_notes or []:
        prompt_template = (prompt_template + "\n" if prompt_template else "") + note

    # Build final prompt
    parts = []
    if history_text:
        parts.append("PRIOR CONVERSATION (context for pronouns only — do not use as a source of facts):\n" + history_text)
    parts.append("DOCUMENT CONTENT:\n" + full_context)
    if computed_result:
        parts.append(
            "COMPUTED RESULT (exact — computed with pandas over every row of the sheet):\n"
            + computed_result
        )
    if prompt_template:
        parts.append("TASK:\n" + prompt_template)
    parts.append("USER REQUEST:\n" + message)

    return "\n\n".join(parts), excerpts


def _build_cross_reference_prompt(docs: list[Any]) -> str:
    """CROSS_REFERENCE_PROMPT with one table column per document."""
    names = [
        (getattr(d, "filename", "") or "document").replace("|", "/")
        for d in docs
    ] or ["Document A", "Document B"]
    if len(names) == 1:
        names.append("Document B")
    return CROSS_REFERENCE_PROMPT.format(doc_columns=" | ".join(names))


def _build_prompt(
    intent: str,
    docs: list[Any],
    message: str,
    history: list[dict],
    max_chars: int = PROMPT_MAX_CHARS,
    retrieved: Optional[dict[str, list[Any]]] = None,
    web_lookup_on: bool = False,
) -> str:
    """Build LLM prompt from intent, documents, and context."""
    return _build_prompt_ex(intent, docs, message, history, max_chars, retrieved, web_lookup_on)[0]


_LANGUAGES = {
    "english": "English", "engels": "English", "anglais": "English", "englisch": "English",
    "french": "French", "frans": "French", "français": "French", "francais": "French", "französisch": "French",
    "german": "German", "duits": "German", "deutsch": "German", "allemand": "German",
    "spanish": "Spanish", "spaans": "Spanish", "español": "Spanish", "espanol": "Spanish", "spanisch": "Spanish",
    "dutch": "Dutch", "nederlands": "Dutch", "néerlandais": "Dutch", "niederländisch": "Dutch",
    "italian": "Italian", "italiaans": "Italian", "italiano": "Italian",
    "portuguese": "Portuguese", "portugees": "Portuguese", "português": "Portuguese",
}
_TARGET_LANG_RE = re.compile(
    r"\b(?:to|into|naar|in het|in|en|vers|nach|auf)\s+(?:het\s+)?"
    r"(english|engels|anglais|englisch|french|frans|français|francais|französisch|german|duits|deutsch|"
    r"allemand|spanish|spaans|español|espanol|spanisch|dutch|nederlands|néerlandais|niederländisch|"
    r"italian|italiaans|italiano|portuguese|portugees|português)\b",
    re.IGNORECASE,
)


def translate_target_language(message: str) -> str:
    """Target language of a translate request (the LAST "to/into/naar … <language>"),
    defaulting to English."""
    matches = _TARGET_LANG_RE.findall(message or "")
    if not matches:
        return "English"
    return _LANGUAGES.get(matches[-1].lower(), "English")


def _build_translate_prompt(message: str) -> str:
    """Translate task prompt with the target language from the message."""
    return TRANSLATE_PROMPT.format(language=translate_target_language(message))


_LEADING_WRAPPER_RE = re.compile(r'^\s*(?:={2,}[^\n]*={2,}|<document[^>]*>)[ \t]*\n?')


def strip_leading_wrapper(text: str) -> str:
    """Remove a leading "=== file ===" / <document …> line a model echoed back."""
    return _LEADING_WRAPPER_RE.sub("", text, count=1)


# ── graph_query subject extraction ─────────────────────────────────────────

_GRAPH_SCOPE_SUFFIXES = [
    "across my documents", "across all documents", "across all my documents", "across the documents",
    "in my documents", "in all documents", "in all my documents", "in my files", "in the documents",
    "in the knowledge base", "in the knowledge graph", "in my knowledge base", "in the knowledge store",
    "so far", "overall",
]
_PRONOUN_SUBJECTS = {
    "her", "him", "them", "she", "he", "they", "this person", "that person", "the person",
    "this guy", "this man", "this woman", "zij", "ze", "hij", "hem", "haar", "hen", "deze persoon",
    "die persoon", "elle", "lui", "cette personne",
}


def graph_subject(message: str, history: Optional[list[dict]] = None) -> str:
    """
    Subject of a graph_query ("what do we know about Jane Example across my
    documents" → "Jane Example"). Keywords are matched as whole words (never
    "on" inside "Neuféglise"). A pronoun subject ("her", "this person", "hem")
    is resolved from the latest person named in the assistant history.
    """
    subject = (message or "").strip()
    best = None
    for keyword in ["about", "mention", "mentions", "regarding", "on", "over"]:
        hits = list(re.finditer(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", subject, re.IGNORECASE))
        if hits:
            candidate = subject[hits[-1].end():].strip()
            if candidate:
                best = candidate
                break
    if best:
        subject = best

    changed = True
    while changed:
        changed = False
        low = subject.lower().rstrip("\"'.,;:!? ")
        for phrase in _GRAPH_SCOPE_SUFFIXES:
            if low.endswith(phrase):
                subject = subject.rstrip("\"'.,;:!? ")[: -len(phrase)].strip()
                changed = True
                break

    subject = subject.strip("\"'.,;:!? ")
    if not subject:
        subject = (message or "").strip()

    if subject.lower() in _PRONOUN_SUBJECTS and history:
        try:
            import doc_web as _doc_web
            person = _doc_web.extract_person("", history)
            if person and person.get("name"):
                return person["name"]
        except Exception:
            pass
    return subject
