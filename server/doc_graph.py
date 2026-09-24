"""
Document routing graph executor and classification.

A tiny DAG engine for document processing pipelines, with a routing classifier
that decides which model should handle each document based on content analysis.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

# ── Configuration ──────────────────────────────────────────────────────────
CONF_MIN = 0.6              # Minimum confidence for decision-based routing
SHORT_CHARS = 4000          # Threshold for "short" documents
TABLE_DENSITY_MIN = 0.5     # Threshold for table-heavy documents


# ── DAG Executor ───────────────────────────────────────────────────────────
@dataclass
class Node:
    """A node in the processing DAG."""
    name: str
    fn: Callable[..., Any]
    deps: list[str] = field(default_factory=list)


@dataclass
class TraceEntry:
    """A single step in the execution trace."""
    node: str
    status: str  # 'ok' | 'skipped' | 'fallback' | 'error'
    ms: int
    detail: str
    output: dict[str, Any] = field(default_factory=dict)


def _topological_sort(nodes: dict[str, Node]) -> list[str]:
    """Kahn's algorithm: topological sort or raise ValueError on cycle."""
    in_degree = {n: len(node.deps) for n, node in nodes.items()}
    queue = [n for n in nodes if in_degree[n] == 0]

    if not queue and nodes:
        raise ValueError("Graph has a cycle (or all nodes have deps)")

    result = []
    while queue:
        current = queue.pop(0)
        result.append(current)
        # Decrease in_degree for dependents
        for other_name, other_node in nodes.items():
            if current in other_node.deps:
                in_degree[other_name] -= 1
                if in_degree[other_name] == 0:
                    queue.append(other_name)

    if len(result) != len(nodes):
        raise ValueError("Graph has a cycle")

    return result


def _execute_dag(
    nodes: dict[str, Node],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[TraceEntry]]:
    """Execute DAG with topo sort, catching exceptions per node.

    Returns (outputs, trace) where outputs[node_name] is the result (or None on error/skip).
    Downstream nodes still run if they can.
    """
    order = _topological_sort(nodes)
    outputs: dict[str, Any] = {**inputs}
    trace: list[TraceEntry] = []

    for node_name in order:
        if node_name in inputs:
            # Already in inputs, skip
            continue

        node = nodes[node_name]
        start = time.time()

        try:
            # Resolve dependencies
            args = {}
            skip = False
            for dep in node.deps:
                if dep not in outputs:
                    skip = True
                    break
                val = outputs[dep]
                if val is None:
                    skip = True
                    break
                args[dep] = val

            if skip:
                trace.append(
                    TraceEntry(node_name, "skipped", 0, "dependency unavailable", {})
                )
                outputs[node_name] = None
                continue

            # Execute
            result = node.fn(**args)
            ms = int((time.time() - start) * 1000)
            outputs[node_name] = result
            trace.append(
                TraceEntry(node_name, "ok", ms, "", {"result": result})
            )

        except Exception as e:
            ms = int((time.time() - start) * 1000)
            outputs[node_name] = None
            detail = str(e)[:200]
            trace.append(
                TraceEntry(node_name, "error", ms, detail, {})
            )

    return outputs, trace


# ── Probe & Classification ────────────────────────────────────────────────
@dataclass
class ProbeResult:
    """Analysis of a document's content and structure."""
    mime: str
    pages: int
    chars: int
    has_text_layer: bool
    has_page_images: bool
    table_density: float
    is_email: bool


def _is_numeric_or_currency(cell: str) -> bool:
    """Check if a cell is purely numeric, currency, or percent format.

    Examples: '0.5', '3.', '$4.20', '12%', '1,200'
    """
    cell = cell.strip()
    if not cell:
        return False
    # Remove common currency/percent symbols and separators
    cleaned = cell.lstrip('$£€¥').rstrip('%').replace(',', '').replace(' ', '')
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _is_valid_table_cell(cell: str) -> bool:
    """Check if a cell looks like a table cell, not prose.

    A cell is valid if:
    - At most 4 words
    - At most 30 characters
    - Does not end with sentence punctuation ('.', '!', '?')
    - OR is numeric/currency/percent (always OK even if it ends with '.')
    """
    cell = cell.strip()
    if not cell:
        return False

    # Numeric/currency/percent cells are always valid
    if _is_numeric_or_currency(cell):
        return True

    # Check length
    if len(cell) > 30:
        return False

    # Check word count
    word_count = len(cell.split())
    if word_count > 4:
        return False

    # Check for sentence-ending punctuation
    if cell.endswith(('.', '!', '?')):
        return False

    return True


def probe(doc: Any) -> ProbeResult:
    """Analyze a document for routing signals.

    Expects doc to have: mime, pages, text, page_images, filename.
    Returns a ProbeResult dict-like object with routing signals.
    """
    mime = doc.mime or ""
    pages = doc.pages or 0
    text = doc.text or ""
    page_images = doc.page_images or []
    filename = doc.filename or ""

    chars = len(text.strip())
    has_text_layer = chars >= 40
    has_page_images = bool(page_images)

    # Detect email
    is_email = False
    if mime == "message/rfc822" or filename.lower().endswith(".eml"):
        is_email = True
    elif has_text_layer:
        lines = text.split("\n")[:10]
        header_pattern = re.compile(r"^(From|To|Subject|Date|Cc):\s*", re.IGNORECASE)
        if sum(1 for line in lines if header_pattern.match(line)) >= 2:
            is_email = True

    # Detect table density: fraction of non-empty lines that look tabular
    table_density = 0.0
    if has_text_layer:
        # Spreadsheet format detection: if mime or filename indicates a tabular source,
        # set density to 1.0 (requires has_text_layer to be True).
        spreadsheet_mimes = {
            "text/csv", "text/tab-separated-values",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/vnd.oasis.opendocument.spreadsheet",
        }
        spreadsheet_extensions = {".csv", ".tsv", ".xlsx", ".xls", ".ods"}
        filename_lower = filename.lower()

        if mime in spreadsheet_mimes or any(
            filename_lower.endswith(ext) for ext in spreadsheet_extensions
        ):
            table_density = 1.0
        else:
            # Compute table density from content: fraction of non-empty lines that look tabular
            lines = text.split("\n")
            non_empty_lines = [ln for ln in lines if ln.strip()]
            if len(non_empty_lines) > 1:  # Need at least 2 lines for meaningful density
                tabular_count = 0
                for line in non_empty_lines:
                    # A line looks tabular if it has tabs, pipes, or 2+ comma/semicolon-delimited columns
                    if "\t" in line or "|" in line:
                        tabular_count += 1
                    else:
                        # Count comma/semicolon delimiters
                        delims = line.count(",") + line.count(";")
                        if delims >= 2:
                            tabular_count += 1
                        else:
                            # Detect whitespace-aligned columns: 3+ columns separated by 2+ spaces.
                            # Only count a line as tabular if it has 3+ cells and EVERY cell
                            # is a valid table cell (short, not prose-like).
                            if len(line) < 200:
                                parts = [p for p in re.split(r"\s{2,}", line.strip()) if p]
                                if len(parts) >= 3 and all(_is_valid_table_cell(p) for p in parts):
                                    tabular_count += 1
                table_density = tabular_count / len(non_empty_lines)

    return ProbeResult(
        mime=mime,
        pages=pages,
        chars=chars,
        has_text_layer=has_text_layer,
        has_page_images=has_page_images,
        table_density=table_density,
        is_email=is_email,
    )


def route_category(probe: ProbeResult, decision: dict[str, Any] | None) -> tuple[str, str]:
    """Pure routing logic: decide which category a document belongs to.

    Routing rules in order:
    (a) image-only (no text layer + page images) → 'ocr'
    (b) tables (high table density OR decision says table_heavy with confidence) → 'tables'
    (c) short text (decision confident + kind in email/plain_note) → 'text'
    (d) fallback (no decision OR low confidence + (is_email OR short)) → 'text' or 'docs'
    (e) default → 'docs'

    Returns (category, reason) where reason explains what fired.
    """
    # (a) Image-only document
    if not probe.has_text_layer and probe.has_page_images:
        return ("ocr", "image-only document")

    # (b) Tables
    is_table_heavy_decision = (
        decision
        and decision.get("kind") == "table_heavy"
        and decision.get("confidence", 0) >= CONF_MIN
    )
    if is_table_heavy_decision or probe.table_density >= TABLE_DENSITY_MIN:
        return (
            "tables",
            f"table_density={probe.table_density:.2f}" if probe.table_density >= TABLE_DENSITY_MIN else "decision: table_heavy",
        )

    # (c) Short text with high-confidence decision
    if decision and decision.get("confidence", 0) >= CONF_MIN:
        kind = decision.get("kind", "")
        if kind in ("email", "plain_note") and probe.chars < SHORT_CHARS:
            return ("text", f"decision: {kind}, chars={probe.chars}")

    # (d) Fallback rules (no decision OR low confidence)
    if decision is None or decision.get("confidence", 0) < CONF_MIN:
        if probe.is_email or probe.chars < SHORT_CHARS:
            return (
                "text",
                f"rules fallback: email={probe.is_email}, chars={probe.chars}",
            )
        return ("docs", f"rules fallback: default to docs")

    # (e) Default: route to general docs
    return ("docs", f"decision: {decision.get('kind', 'unknown')}")


# ── Pure routing logic for testing ─────────────────────────────────────────
def pick_routed_model(meta: dict, category: str) -> str | None:
    """
    Pure function to pick a routed model based on meta and category.

    Precedence:
    1. If meta['route_override'] exists and is truthy, return it
    2. If category in {'docs', 'text'} AND meta.get('route') has a model
       with category in {'text', 'docs'}, return that model
    3. Otherwise return None

    Args:
        meta: Document metadata dict
        category: Operation category (docs, text, ocr, tables, etc.)

    Returns:
        Model ID string or None
    """
    # Check for override (a model ID, not a category)
    if meta.get("route_override"):
        return meta["route_override"]

    # For text-oriented ops, check if we should use the routed category
    if category in {"docs", "text"} and meta.get("route"):
        route_result = meta["route"]
        routed_category = route_result.get("category")
        routed_model = route_result.get("model")
        # If routed category is text-oriented and we have a model, use it
        if routed_category in {"text", "docs"} and routed_model:
            return routed_model

    return None


def plan_route_request(
    meta: dict,
    override_present: bool,
    override_value: str | None,
    force: bool,
) -> tuple[bool, str | None]:
    """
    Pure function to plan routing request override handling.

    Determines how to handle override_model field in /api/idp/route requests
    and updates meta['route_override'] accordingly.

    Precedence for override handling:
    1. If override_present and override_value is non-empty string:
       -> store it, force=True, pass it
    2. If override_present and (empty string or None):
       -> remove any stored override, force=True, pass None
       (BUG FIX: null now forces recompute, not just clears)
    3. If NOT override_present:
       -> leave stored override untouched, pass the stored override (or None),
          force unchanged

    Args:
        meta: Document metadata dict (will be mutated)
        override_present: bool — was override_model field in the request?
        override_value: str | None — the value of override_model (if present)
        force: bool — initial force value

    Returns:
        tuple of (new_force, override_to_pass) where:
        - new_force: whether to force recomputation
        - override_to_pass: model override to pass to run_routing (or None)
    """
    if override_present:
        if override_value == "" or override_value is None:
            # Clear override
            if "route_override" in meta:
                del meta["route_override"]
            return (True, None)  # Force recomputation when explicitly clearing
        else:
            # Set override (non-empty string)
            meta["route_override"] = override_value
            return (True, override_value)  # Force recomputation and pass it
    else:
        # Override not provided; reuse stored override if present
        override_to_pass = meta.get("route_override")
        return (force, override_to_pass)


# ── Main routing orchestrator ──────────────────────────────────────────────
def run_routing(
    doc: Any,
    *,
    decide: Callable[[str], dict[str, Any] | None],
    resolve_model: Callable[[str], str | None],
    override_model: str | None = None,
) -> dict[str, Any]:
    """Execute the full document routing pipeline.

    Builds a DAG of nodes (ingest, probe, decide, route, resolve_model),
    executes it, and returns a complete routing result with trace.

    Args:
        doc: A Document object with id, filename, mime, text, pages, page_images.
        decide: Callable that takes ~6000 chars of text and returns
                {kind, confidence, complexity, probabilities, source} or None.
        resolve_model: Callable that takes category (str) and returns model name or None.
        override_model: If non-empty, force this model (but compute category still).

    Returns:
        A JSON-serializable dict with:
        - doc_id, category, model, reason, overridden, decision, probe, trace
    """

    # Mutable state to track decide results
    decide_result = {"output": None, "was_called": False}

    def _ingest_node():
        """Record basic doc metadata."""
        return {
            "doc_id": doc.id,
            "filename": doc.filename,
            "mime": doc.mime,
            "pages": doc.pages,
        }

    def _probe_node(ingest=None):
        """Analyze document content."""
        return probe(doc)

    def _decide_node(probe=None):
        """Call the decision model if document has a text layer."""
        if not probe.has_text_layer:
            return {"status": "skipped", "detail": "image-only: no text for Laya"}

        # Sample first ~6000 chars
        sample = doc.text[:6000]
        decision = decide(sample)
        decide_result["output"] = decision
        decide_result["was_called"] = True

        if decision is None:
            return {"status": "fallback", "detail": "decision model unavailable or failed — using rules"}
        return {"status": "ok", "detail": ""}

    def _route_node(probe=None, decide_status=None):
        """Compute category and reason."""
        # decide_status tells us what happened with the decision node
        # but we use decide_result["output"] for the actual decision
        category, reason = route_category(probe, decide_result["output"])
        return {"category": category, "reason": reason}

    def _resolve_model_node(route=None):
        """Resolve model for the category."""
        category = route["category"]
        model = resolve_model(category)
        return model

    # Build DAG
    nodes = {
        "ingest": Node("ingest", _ingest_node, []),
        "probe": Node("probe", _probe_node, ["ingest"]),
        "decide_status": Node("decide_status", _decide_node, ["probe"]),
        "route": Node("route", _route_node, ["probe", "decide_status"]),
        "resolve_model": Node("resolve_model", _resolve_model_node, ["route"]),
    }

    # Execute
    outputs, trace = _execute_dag(nodes, {})

    # Collect results
    category = outputs["route"]["category"]
    reason = outputs["route"]["reason"]
    model = outputs["resolve_model"]

    # Determine decision source: "laya" if decider was called and returned a decision
    decision_source = "laya" if (decide_result["was_called"] and decide_result["output"]) else "rules"

    # Build decision output
    decision_output = None
    if decision_source == "laya":
        decision_output = decide_result["output"]
    else:
        decision_output = {
            "kind": None,
            "confidence": None,
            "complexity": None,
            "probabilities": {},
            "source": decision_source,
        }

    # Handle override
    overridden = False
    if override_model:
        model = override_model
        overridden = True

    # Build trace with sanitized fields, renaming decide_status back to decide for user visibility
    sanitized_trace = []
    for entry in trace:
        node_name = "decide" if entry.node == "decide_status" else entry.node
        # For decide_status node, check if the return value contains a status field
        status = entry.status
        if entry.node == "decide_status" and outputs.get("decide_status"):
            decided_status = outputs["decide_status"].get("status", entry.status)
            if decided_status in ("skipped", "fallback"):
                status = decided_status
        sanitized_trace.append({
            "node": node_name,
            "status": status,
            "ms": entry.ms,
            "detail": entry.detail,
        })

    # Final result
    return {
        "doc_id": doc.id,
        "category": category,
        "model": model,
        "reason": reason,
        "overridden": overridden,
        "decision": decision_output,
        "probe": {
            "mime": outputs["probe"].mime,
            "pages": outputs["probe"].pages,
            "chars": outputs["probe"].chars,
            "has_text_layer": outputs["probe"].has_text_layer,
            "has_page_images": outputs["probe"].has_page_images,
            "table_density": round(outputs["probe"].table_density, 3),
            "is_email": outputs["probe"].is_email,
        },
        "trace": sanitized_trace,
    }
