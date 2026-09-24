"""
Document agent orchestrator for the Documents tab.

A decision model (Laya: non-generative typed classifier) decides the intent;
the orchestrator runs steps and STREAMS EVENTS that the UI renders as tiles
popping up top-to-bottom in a right-hand panel.

Never imports main.py; uses injected hooks for all integrations.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, AsyncIterator, Callable, Optional

log = logging.getLogger("doc_agent")

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
- Use the conversation history to resolve pronouns (e.g. 'her' refers to the person identified earlier)
- Be clear if the answer is not found in the documents — do not guess or speculate"""

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
    kind: str  # "laya" | "extract" | "ocr" | "llm" | "vision"
    title: str
    status: str  # "pending" | "running" | "done" | "skipped" | "error"
    model: Optional[str] = None  # Model name if applicable
    model_info: Optional[dict[str, Any]] = None  # Model metadata
    detail: str = ""  # Explanation or status message
    decisions: list[Decision] = field(default_factory=list)  # For laya tile only
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
    now_ms: Callable[[], int] = field(default_factory=lambda: lambda: int(time.time() * 1000))

# ── Pure functions for intent & role resolution ─────────────────────────────

def rules_intent(message: str, files: list[dict]) -> tuple[Optional[str], list[str]]:
    """
    Multilingual-ish keyword rules for intent detection.

    Returns (intent_name, matched_keywords) or (None, []) if no match.
    """
    # Handle empty or no-instruction messages
    if not message or not message.strip():
        return "summarize", ["no explicit instruction"]

    # Check for "look at attached document" pattern (across languages)
    msg_lower = message.lower()
    no_instruction_patterns = [
        r"look at the attached",
        r"take a look at the attached",
        r"bekijk de bijlage",
        r"regarde le document",
    ]
    import re
    for pattern in no_instruction_patterns:
        if re.search(pattern, msg_lower):
            return "summarize", ["no explicit instruction"]

    msg_lower = message.lower()
    matched_kws = []

    # verify_signature: signature + reference
    verify_sig_kws = ["signature", "handtekening", "verify", "authentic", "match", "compare signature"]
    for kw in verify_sig_kws:
        if kw in msg_lower:
            matched_kws.append(kw)

    if matched_kws:
        # Check if there's a reference-like file
        for f in files:
            fname = (f.get("name") or "").lower()
            if any(ref_kw in fname for ref_kw in ["reference", "specimen", "sample", "card", "template"]):
                return "verify_signature", matched_kws

    # identify_person: must ask WHO/WHOSE/which person/the name — NOT a fact lookup
    id_person_kws = [
        "who is", "who signed", "who wrote", "which person", "whose", "name of", "person's name",
        "persons name", "the name", "signer", "signatory",
        "wie is", "wie heeft", "naam van", "de naam", "ondertekenaar", "ondertekener",
        "qui est", "nom de", "find person", "identify",
    ]
    id_person_matches = [kw for kw in id_person_kws if kw in msg_lower]
    if id_person_matches:
        return "identify_person", id_person_matches

    # summarize
    summarize_kws = ["summarize", "summary", "tl;dr", "tl dr", "key points", "samenvat", "résum", "overview"]
    summarize_matches = [kw for kw in summarize_kws if kw in msg_lower]
    if summarize_matches:
        return "summarize", summarize_matches

    # extract_data
    extract_kws = ["extract", "table", "amount", "invoice number", "dates", "fields", "list all", "data"]
    extract_matches = [kw for kw in extract_kws if kw in msg_lower]
    if extract_matches:
        return "extract_data", extract_matches

    # translate
    translate_kws = ["translate", "vertaal", "traduis", "translation"]
    translate_matches = [kw for kw in translate_kws if kw in msg_lower]
    if translate_matches:
        return "translate", translate_matches

    # redact
    redact_kws = ["redact", "anonymize", "anonymise", "remove personal", "hide", "obscure"]
    redact_matches = [kw for kw in redact_kws if kw in msg_lower]
    if redact_matches:
        return "redact", redact_matches

    # general_question: fact lookup keywords
    # Check these LAST (after extract_data) so more specific keywords take precedence
    fact_lookup_kws = [
        "date of birth", "born", "birthday", "geboortedatum",
        "address", "adres", "age", "how old",
        "nationality", "when was", "what is his", "what is her", "what is their",
        "what does it say", "due date", "telephone", "phone", "email"
    ]
    fact_matches = [kw for kw in fact_lookup_kws if kw in msg_lower]
    if fact_matches:
        return "general_question", fact_matches

    return None, []


def resolve_intent(
    laya_result: Optional[dict],
    rules_result: tuple[Optional[str], list[str]],
    min_conf: float = LAYA_INTENT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """
    Resolve the final intent using Laya and rules.

    Args:
        laya_result: {"intent": str, "confidence": float, "probabilities": dict} or None
        rules_result: (intent_name, matched_keywords) from rules_intent()
        min_conf: Minimum confidence threshold for Laya

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

        # Validate: require at least one attachment
        if not attachments:
            yield {"error": "Attach at least one document."}
            return

        # (a) Emit Laya tile (running, no decisions yet)
        laya_tile = Tile(
            id="laya",
            kind="laya",
            title="Analyzing intent",
            status="running",
            model="Laya" if hooks.laya_info().get("available") else "rules",
            model_info=hooks.laya_info(),
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

        # Decision 1: intent
        laya_intent_result = None
        if message and message.strip():
            # Only call Laya if message is not empty
            try:
                # Run in executor to avoid blocking
                laya_intent_result = await asyncio.to_thread(
                    hooks.laya_intent, message, files_meta
                )
            except Exception as e:
                log.warning(f"Laya intent failed: {e}")

        rules_intent_result = rules_intent(message, files_meta)

        # Add note if message was empty (rules_intent handles this)
        if not message or not message.strip():
            log.debug("Empty message detected — intent decision: 'summarize' via rules")
        intent_decision = resolve_intent(laya_intent_result, rules_intent_result)
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
                    source=role_info.get("source"),
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

        # Decision: doc kind per document (if has text layer)
        for doc, att in docs:
            if doc.text and len(doc.text.strip()) >= 40:
                try:
                    doc_kind_result = await asyncio.to_thread(
                        hooks.laya_doc_kind, doc.text[:6000]
                    )
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

        # (c) Per SUBJECT doc: extraction step
        subject_docs = []
        for doc, att in docs:
            # Find role from Laya decisions
            role = "subject"  # default
            for decision in laya_tile.decisions:
                if decision.id == f"role-{doc.id}":
                    role = decision.value
                    break

            if role == "subject":
                subject_docs.append(doc)

        for doc in subject_docs:
            extract_tile = Tile(
                id=f"extract-{doc.id}",
                kind="extract",
                title=f"Extract from {doc.filename}",
                status="pending",
                doc={"doc_id": doc.id, "name": doc.filename},
            )

            # Check if has text layer
            if doc.text and len(doc.text.strip()) >= 40:
                # Text layer present; skip OCR
                extract_tile.status = "done"
                extract_tile.kind = "extract"
                extract_tile.detail = f"Text layer present ({len(doc.text)} chars) — OCR skipped"
                extract_tile.output_preview = doc.text[:300]
                yield {"tile": extract_tile.to_dict()}
                tiles_emitted[extract_tile.id] = extract_tile

            elif doc.page_images and intent != "verify_signature":
                # No text layer but has images; run OCR
                extract_tile.kind = "ocr"
                extract_tile.status = "running"
                extract_tile.started_ms = hooks.now_ms()

                # Add OCR decision to Laya tile
                laya_tile.decisions.append(Decision(
                    id=f"ocr_needed-{doc.id}",
                    label="OCR Needed",
                    value="yes",
                    source="probe",
                    note="No text layer, page images available",
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

        # (d) Final step: pick model and stream answer
        answer_tile = Tile(
            id="answer",
            kind="llm",
            title="Generating answer",
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
                        ref_paths = hooks.page_image_paths(doc, [1], 200)
                        reference_images.extend(ref_paths)
                    elif role == "subject" and doc.page_images:
                        # Get first and last page of subject (deduped, max 3)
                        pages_to_check = [1, len(doc.page_images)]
                        pages_to_check = list(dict.fromkeys(pages_to_check))  # dedup
                        pages_to_check = pages_to_check[:3]  # max 3
                        subject_paths = hooks.page_image_paths(doc, pages_to_check, 200)
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
            category = "docs" if subject_docs else "text"
            answer_model_note = f"Text model for {intent}"

            if subject_docs:
                llm_model = await hooks.resolve_text_model(subject_docs[0], category)
                # Check if we have the info hook for fallback details
                if hooks.resolve_text_model_info:
                    try:
                        model_info = await hooks.resolve_text_model_info(subject_docs[0], category)
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

            answer_tile.status = "running"
            yield {"tile": answer_tile.to_dict()}

            try:
                # Build prompt
                prompt = _build_prompt(
                    intent, subject_docs, message, history, max_chars=24000
                )

                # Stream LLM
                answer_text = ""
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

            except Exception as e:
                answer_tile.status = "error"
                answer_tile.detail = str(e)[:200]
                yield {"tile": answer_tile.to_dict()}
                yield {"error": f"LLM failed: {str(e)}"}
                return

        # (e) Mark Laya tile as done
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
        # Best-effort: mark tiles as cancelled
        if laya_tile:
            laya_tile.status = "error"
            laya_tile.detail = "Cancelled"
            yield {"tile": laya_tile.to_dict()}
        raise


# ── Helper functions ──────────────────────────────────────────────────────

def _mime_to_kind(mime: str) -> str:
    """Convert MIME type to document kind."""
    if not mime:
        return "file"
    if "pdf" in mime:
        return "pdf"
    if "word" in mime or "document" in mime:
        return "docx"
    if "sheet" in mime or "csv" in mime:
        return "xlsx"
    if mime.startswith("image"):
        return "image"
    if mime == "message/rfc822" or "email" in mime:
        return "email"
    if "text" in mime:
        return "text"
    return "file"


def _find_role(doc: Any, laya_tile: Tile, docs: list[tuple[Any, dict]]) -> str:
    """Find the role assigned to a document in the Laya tile decisions."""
    for decision in laya_tile.decisions:
        if decision.id == f"role-{doc.id}":
            return decision.value
    return "subject"


def _build_prompt(
    intent: str,
    docs: list[Any],
    message: str,
    history: list[dict],
    max_chars: int = 24000,
) -> str:
    """Build LLM prompt from intent, documents, and context."""
    # Collect document text
    doc_texts = []
    for doc in docs:
        if doc.text:
            text_snippet = doc.text[:5000]  # Limit per-doc
            doc_texts.append(f"=== {doc.filename} ===\n{text_snippet}")

    # Combine with truncation
    full_context = "\n\n".join(doc_texts)
    if len(full_context) > max_chars:
        full_context = full_context[:max_chars] + "\n\n[... truncated ...]"

    # Add history (last 6 turns)
    history_text = ""
    if history:
        recent_history = history[-6:]
        history_lines = []
        for turn in recent_history:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")[:500]  # Trim
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
    }.get(intent)

    if not prompt_template and intent != "general_question":
        prompt_template = IDENTIFY_PERSON_PROMPT

    # Build final prompt
    parts = []
    if history_text:
        parts.append("PRIOR CONVERSATION:\n" + history_text)
    parts.append("DOCUMENTS:\n" + full_context)
    if prompt_template:
        parts.append("TASK:\n" + prompt_template)
    parts.append("USER REQUEST:\n" + message)

    return "\n\n".join(parts)


def _build_translate_prompt(message: str) -> str:
    """Extract target language from message."""
    # Simple heuristic: look for language names
    target_lang = "English"
    if any(kw in message.lower() for kw in ["french", "français", "fr"]):
        target_lang = "French"
    elif any(kw in message.lower() for kw in ["spanish", "español", "es"]):
        target_lang = "Spanish"
    elif any(kw in message.lower() for kw in ["german", "deutsch", "de"]):
        target_lang = "German"
    elif any(kw in message.lower() for kw in ["dutch", "nederlands", "nl"]):
        target_lang = "Dutch"
    return TRANSLATE_PROMPT.format(language=target_lang)
