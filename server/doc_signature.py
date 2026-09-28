"""
Signature verification step for the Documents agent (and handwritten-letter
helpers).

Like the web lookup (doc_web.py), a signature check is an EXTRA STEP attached
to a primary intent: "Read this handwritten letter, check the company in the
registry, and verify the signature against the reference card" is
cross_reference + a signature check. A pure "compare these signatures" request
stays verify_signature, which runs the same step before its vision answer.

The deterministic score comes from signature_engine.py (local OpenCV /
scikit-image); the Signature model (vision LLM) only picks which handwritten
line is the signature and explains similarities/differences. The engine score
is authoritative. Never imports main.py or doc_agent.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("doc_signature")

TILE_ID = "signature-check"
TILE_TITLE = "Signature verification"
CAVEAT = (
    "This is an automated comparison (local image analysis + an AI model) — "
    "not a forensic determination, and not proof of authenticity or forgery."
)

# ── Keyword rules ──────────────────────────────────────────────────────────

# Words that ask for a signature check (EN + NL)
SIGNATURE_STEP_WORDS = [
    "signature", "signatures", "signed", "signing", "sign-off",
    "handtekening", "handtekeningen", "handtekeningkaart", "handtekeningenkaart",
    "ondertekend", "ondertekening", "ondertekent", "getekend",
    "signatuur", "paraaf", "specimen", "specimens", "signature card",
]
_WHO_SIGNED_RE = re.compile(r"\b(?:who|wie)\s+(?:has\s+|heeft\s+)?(?:signed|ondertekend|getekend)\b")

# Filename hints for a signature reference card
CARD_NAME_STRONG = ["card", "kaart", "specimen", "referentie", "reference", "sample", "voorbeeld"]
CARD_NAME_WEAK = ["signature", "signatures", "handtekening", "handtekeningen", "sig", "autograph"]
LETTER_NAME_HINTS = ["letter", "brief", "handwritten", "handgeschreven", "note", "notitie", "contract", "invoice", "factuur"]

# Handwritten document hints
HANDWRITING_MSG_WORDS = [
    "handwritten", "hand-written", "handwriting", "hand written", "written by hand",
    "handgeschreven", "handschrift", "met de hand geschreven", "manuscript", "manuscrit",
]
HANDWRITING_NAME_HINTS = ["handwritten", "hand-written", "handgeschreven", "handschrift", "handwriting", "manuscript"]
LETTER_MSG_WORDS = ["letter", "brief", "note", "briefje", "notitie"]

_FILLER = {
    "please", "pls", "can", "could", "would", "you", "also", "the", "this", "that", "these", "a", "an",
    "and", "en", "graag", "kun", "kan", "je", "jij", "u", "ook", "de", "het", "deze", "dit", "then",
    "daarna", "now", "just", "me", "for", "to", "of", "with", "against", "it", "them", "check",
}


def _kw_re(kw: str) -> str:
    return r"(?<!\w)" + re.escape(kw.lower()) + r"(?!\w)"


def signature_hits(message: str) -> list[str]:
    """Signature words in the message ("who signed …?" asks for a person, not a check)."""
    msg = (message or "").lower()
    msg_wo_who = _WHO_SIGNED_RE.sub(" ", msg)
    return [kw for kw in SIGNATURE_STEP_WORDS if re.search(_kw_re(kw), msg_wo_who)]


_CLAUSE_SPLIT_RE = re.compile(
    r"\s*(?:[,;:!?]+|\.(?=\s|$)|\s—\s|\s-\s|\b(?:and|en|also|ook|then|daarna|plus|as well as)\b)\s*",
    re.IGNORECASE,
)


def split_clauses(message: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(message or "") if c and c.strip()]


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\wÀ-ÿ'-]+", (text or "").lower()) if w not in _FILLER and len(w) > 1]


def strip_signature_clause(message: str) -> tuple[str, list[str]]:
    """(remainder, removed clauses): the message without its signature-check
    clause(s), for deciding the PRIMARY intent. The remainder is "" when the
    whole request is about signatures."""
    clauses = split_clauses(message)
    kept, removed = [], []
    for c in clauses:
        (removed if signature_hits(c) else kept).append(c)
    if not removed:
        return message, []
    remainder = ", ".join(kept).strip()
    if len(_content_words(remainder)) < 2:
        return "", removed
    return remainder, removed


def plan_signature_step(message: str) -> dict[str, Any]:
    """Pure keyword plan: {"hits", "remainder", "combined"} — combined means the
    message asks for a signature check AND another task (primary intent from
    the remainder); not combined + hits = a pure signature comparison."""
    hits = signature_hits(message)
    if not hits:
        return {"hits": [], "remainder": message, "combined": False}
    remainder, _removed = strip_signature_clause(message)
    return {"hits": hits, "remainder": remainder or "", "combined": bool(remainder)}


def card_name_strength(name: str) -> int:
    """2 = filename strongly suggests a signature reference card, 1 = weakly,
    0 = no / looks like the letter itself."""
    n = (name or "").lower()
    tokens = set(re.split(r"[^a-z0-9à-ÿ]+", n))
    if any(h in n for h in LETTER_NAME_HINTS):
        # "…letter_FORGED_signature.png" is a letter, not a card
        if not any(t in tokens for t in ("card", "kaart", "specimen", "handtekeningkaart")):
            return 0
    strong = any(h in n for h in CARD_NAME_STRONG) or "handtekeningkaart" in n
    weak = any((h in tokens) if len(h) <= 3 else (h in n) for h in CARD_NAME_WEAK)
    if strong and (weak or "card" in n or "kaart" in n or "specimen" in n):
        return 2
    if strong:
        return 1
    return 1 if weak else 0


def is_image_doc(doc: Any) -> bool:
    mime = str(getattr(doc, "mime", "") or "")
    name = str(getattr(doc, "filename", "") or "").lower()
    return mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".tif", ".tiff"))


def text_len(doc: Any) -> int:
    return len((getattr(doc, "text", "") or "").strip())


async def find_signature_reference(docs: list[Any], probe: Optional[Any] = None) -> Optional[dict]:
    """The attached document that is a signature reference card:
    {"doc_id", "name", "how"} or None. Filename hints first; else (with a
    probe) an image with several signature-like blobs and little text.
    `probe(path) -> dict` is signature_engine.looks_like_signature_card."""
    best = None
    for d in docs:
        s = card_name_strength(getattr(d, "filename", ""))
        if s and (best is None or s > best[0]):
            best = (s, d)
    if best is not None and (best[0] >= 2 or len(docs) >= 2):
        d = best[1]
        return {"doc_id": d.id, "name": d.filename, "how": "file name"}
    if probe is None:
        return None
    # Probe images without a text layer (never the only document)
    if len(docs) < 2:
        return None
    for d in docs:
        if not is_image_doc(d) or text_len(d) > 80 or not getattr(d, "page_images", None):
            continue
        if card_name_strength(getattr(d, "filename", "")) == 0 and any(
            h in (d.filename or "").lower() for h in LETTER_NAME_HINTS
        ):
            continue
        try:
            res = await asyncio.to_thread(probe, d.page_images[0])
        except Exception as e:
            log.debug(f"card probe failed for {d.id}: {e}")
            continue
        if res and res.get("is_card"):
            return {"doc_id": d.id, "name": d.filename, "how": f"{res.get('count')} signature-like blobs, no text"}
    return None


def resolve_signature_step(
    plan: dict[str, Any],
    card: Optional[dict],
    n_docs: int,
    laya_result: Optional[dict] = None,
) -> dict[str, Any]:
    """Signature-check decision (mirrors doc_web.resolve_web_lookup: keyword
    rules trigger, Laya can corroborate but never trigger a check on its own).
    Returns {"value": "yes"|"no", "source", "confidence", "note", "reference"}."""
    hits = plan.get("hits") or []
    laya_yes = bool(laya_result and laya_result.get("value") == "yes")
    laya_conf = (laya_result or {}).get("confidence")
    if hits and card and n_docs >= 2:
        note = f"signature words: {', '.join(hits[:4])} · reference: {card['name']} ({card['how']})"
        if laya_yes:
            return {"value": "yes", "source": "laya", "confidence": laya_conf,
                    "note": "keyword rules agree · " + note, "reference": card}
        return {"value": "yes", "source": "rules", "confidence": None, "note": note, "reference": card}
    if hits and not card:
        note = "signature words found, but no signature reference card among the documents"
    elif hits:
        note = "a signature check needs the document and a reference card"
    elif laya_yes:
        note = f"Laya suggested a signature check ({laya_conf or 0:.2f}) — no signature words in the request"
    else:
        note = None
    return {"value": "no", "source": "rules", "confidence": None, "note": note, "reference": None}


def handwriting_hint(message: str, doc: Any, n_images: int = 1) -> Optional[str]:
    """Why a document is treated as handwritten (filename / message), or None."""
    name = str(getattr(doc, "filename", "") or "").lower()
    for h in HANDWRITING_NAME_HINTS:
        if h in name:
            return f"file name ({h})"
    msg = (message or "").lower()
    hw = [w for w in HANDWRITING_MSG_WORDS if re.search(_kw_re(w), msg)]
    if hw:
        # "handwritten letter" with the letter being one of several images: only
        # the image docs that need reading get this — the caller filters.
        return f"request mentions '{hw[0]}'"
    return None


HANDWRITING_CHECK_PROMPT = (
    "Look at this image of a document. Is the main text HANDWRITTEN (written by hand with a pen) "
    "or PRINTED/TYPED? Answer with exactly one word: handwritten or printed."
)

TRANSCRIBE_PROMPT = (
    "Transcribe this handwritten document exactly as written.\n"
    "- Preserve the line breaks and reading order. Write every line flush-left: no indentation or\n"
    "  alignment spaces (a right-aligned date is written like any other line).\n"
    "- Keep names, company names, addresses, dates and numbers exactly as written (including accents).\n"
    "- Mark any word you cannot read as [?]. Do not guess.\n"
    "- Write the signature line as [signature].\n"
    "Output ONLY the transcription — no commentary."
)


def parse_handwriting_answer(text: str) -> Optional[bool]:
    t = (text or "").strip().lower()
    if not t:
        return None
    if "handwrit" in t or "hand-writ" in t or "by hand" in t or "handgeschreven" in t:
        return True
    if "print" in t or "typed" in t or "typ" in t[:10]:
        return False
    return None


# ── Prompts ────────────────────────────────────────────────────────────────

PICK_PROMPT = (
    "This image shows {n} numbered regions cut from the bottom of a handwritten letter. "
    "Which number is the handwritten SIGNATURE — the stylised autograph, not the closing phrase "
    "(e.g. 'Kind regards', 'Met vriendelijke groet') and not the plainly written name below it? "
    "Answer with the number only."
)

ASSESS_PROMPT = """You are shown ONE image: at the top the QUESTIONED SIGNATURE (cut from a document under examination), below it {n} REFERENCE SIGNATURES (R1…R{n}) that are known specimens of the same person.

A deterministic local engine already measured the signatures (this score is AUTHORITATIVE — do not change it):
{engine}

Your task (max ~150 words):
1. Describe concrete visual similarities between the questioned signature and the references (letterforms, initials, connections, slant, proportions, flourishes, pen flow).
2. Describe concrete differences.
3. Give your own short impression in one sentence (you may disagree, but say that the engine score is the measured result).
Do NOT declare the signature genuine or forged. End with: "Not a forensic determination."
"""


def score_line(result: dict) -> str:
    return f"{result['score']}% — {result['band_label']}"


def engine_summary(result: dict, max_features: int = 9) -> str:
    """Compact text version of an engine result (for prompts)."""
    lines = [
        f"Score {result['score']}/100 — {result['band_label']} "
        f"(band: {result['band']}; ≥70 consistent, 40–69 inconclusive, <40 inconsistent).",
    ]
    feats = []
    for b in (result.get("breakdown") or [])[:max_features]:
        feats.append(f"{b['label']}: z={b['z']:+.1f} ({b['verdict']})")
    if feats:
        lines.append("Per feature (z = distance in units of the natural variation between the references): " + "; ".join(feats) + ".")
    if result.get("reasons"):
        lines.append("Key reasons: " + "; ".join(result["reasons"]) + ".")
    if result.get("warnings"):
        lines.append("Quality warnings: " + "; ".join(result["warnings"]) + ".")
    return "\n".join(lines)


SIGNATURE_ANSWER_NOTE = """SIGNATURE VERIFICATION is part of this request. Use the SIGNATURE VERIFICATION RESULT section.
In the "Signature verification" section: start with the exact score line "{score_line}" (bold), name the band, give 2–4 key reasons (from the result and the visual assessment), and end with: "{caveat}". Never change the score or the band."""

COMBINED_XREF_PROMPT = """Answer with these markdown sections, in this order:
**Letter** — a brief transcription summary of the handwritten letter: who wrote it, the date, and what it claims (quote key phrases; words marked [?] were unreadable).
**Company check** — a markdown table with columns Field | {doc_columns} | Result, with rows for Person name, Role/position, Company / trade name, Address / location, Registry number, Relevant dates (✅ match when the letter is dated on or after the registration date, ❌ mismatch when before) — Result is ✅ match, ⚠️ partial, ❌ mismatch or — not stated. Then one sentence on anything that limits trust (e.g. a document says it is a DEMO / fictitious — then it cannot prove the company is real).
**Signature verification** — start with the exact score line "{score_line}" (bold), name the band, give 2–4 key reasons (from the SIGNATURE VERIFICATION RESULT and the visual assessment), mention any quality warning from the result (e.g. only 2 reference signatures), and end with: "{caveat}".
**Conclusion** — one or two sentences combining the company check and the signature result.
Refer to people by name; do not assume gender. Only use facts from the documents and the signature result. Never change the signature score or band."""


def signature_prompt_block(result: dict, assessment: Optional[str], model: Optional[str]) -> str:
    """The SIGNATURE VERIFICATION RESULT block for the answer prompt."""
    parts = [
        f"Questioned signature: from {result.get('questioned_doc') or 'the document'}; "
        f"references: {result.get('n_references')} signatures from {', '.join(result.get('reference_docs') or []) or 'the reference card'}.",
        engine_summary(result),
    ]
    if assessment:
        parts.append(f"Visual assessment by {model or 'the signature model'}:\n{assessment.strip()[:1500]}")
    return "\n".join(parts)


def signature_section_md(result: dict, assessment: Optional[str] = None) -> str:
    """Deterministic markdown section (appended when the answer model left the
    score out, or as the whole signature answer without a model)."""
    lines = [f"**{score_line(result)}** (band: {result['band']})", ""]
    for r in (result.get("reasons") or [])[:4]:
        lines.append(f"- {r}")
    for w in (result.get("warnings") or [])[:3]:
        lines.append(f"- ⚠️ {w}")
    if assessment:
        lines += ["", f"_Visual assessment:_ {assessment.strip()[:900]}"]
    lines += ["", f"_{CAVEAT}_"]
    return "\n".join(lines)


def answer_mentions_score(answer: str, result: dict) -> bool:
    # "96%", "96 %" or "96/100" — the model sometimes rewrites the score line
    return bool(re.search(rf"(?<!\d){int(result['score'])}\s?(?:%|/\s?100\b)", answer or ""))


def parse_pick(text: str, n: int) -> Optional[int]:
    """1-based candidate number from a model reply → 0-based index, or None."""
    m = re.search(r"\b(\d{1,2})\b", text or "")
    if not m:
        return None
    k = int(m.group(1))
    return k - 1 if 1 <= k <= n else None


# ── Signer name (knowledge graph) ──────────────────────────────────────────

_NAME_STOP = {
    "demo", "signature", "signatures", "card", "kaart", "specimen", "reference", "referentie", "sample",
    "handtekening", "handtekeningen", "handtekeningkaart", "scan", "img", "image", "photo", "foto", "copy",
    "final", "v1", "v2", "handwritten", "letter", "brief", "forged",
}


def name_from_filename(filename: str) -> Optional[str]:
    """"DEMO_signature_card_Michel_Neufeglise.png" → "Michel Neufeglise"."""
    stem = Path(filename or "").stem
    toks = [t for t in re.split(r"[_\-\s.]+", stem) if t]
    words = [t for t in toks if t.lower() not in _NAME_STOP and t[:1].isalpha() and not t.isdigit()]
    caps = [w for w in words if w[:1].isupper()]
    if len(caps) >= 2:
        return " ".join(caps[:3])
    return None


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def pick_signer(candidates: list[Optional[str]], letter_text: str) -> Optional[str]:
    """First candidate name that occurs in the letter text (accent-insensitive),
    else the first candidate."""
    names = [c.strip() for c in candidates if c and c.strip()]
    folded = _fold(letter_text)
    for c in names:
        if _fold(c) in folded:
            return c
    for c in names:
        parts = [p for p in _fold(c).split() if len(p) > 2]
        if parts and parts[-1] in folded:
            return c
    return names[0] if names else None


# ── The step ───────────────────────────────────────────────────────────────

def _tile(**kw) -> dict:
    base = {
        "id": TILE_ID, "kind": "signature", "title": TILE_TITLE, "status": "running",
        "model": None, "model_info": None, "detail": "", "decisions": [], "items": [],
        "started_ms": None, "ms": None, "output_preview": None, "doc": None, "data": None,
    }
    base.update(kw)
    return base


def default_signature_dir(run_id: str) -> Path:
    import paths
    p = paths.uploads_dir() / "_signatures" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def crop_url(run_id: str, name: str) -> str:
    return f"/api/idp/signatures/{run_id}/{name}"


def _is_ocr_only(name: str) -> bool:
    n = (name or "").lower()
    return "ocr" in n or "embed" in n


async def _page_path(hooks: Any, doc: Any, last: bool) -> Optional[str]:
    imgs = getattr(doc, "page_images", None) or []
    if not imgs:
        return None
    page = len(imgs) if last else 1
    paths_: list[str] = []
    try:
        paths_ = await asyncio.to_thread(hooks.page_image_paths, doc, [page], 200)
    except Exception as e:
        log.debug(f"page_image_paths failed: {e}")
    for p in paths_ or []:
        if p and Path(p).is_file():
            return p
    cand = imgs[page - 1]
    return cand if cand and Path(cand).is_file() else None


async def _vision(hooks: Any, model: str, prompt: str, image: str, num_predict: Optional[int] = None) -> str:
    import inspect
    kwargs: dict[str, Any] = {}
    if num_predict is not None:
        try:
            params = inspect.signature(hooks.vision_call).parameters
            if "num_predict" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
                kwargs["num_predict"] = num_predict
        except (TypeError, ValueError):
            pass
    return await hooks.vision_call(model, prompt, [], [image], **kwargs)


async def _vision_fallback(
    hooks: Any, models: list[str], prompt: str, image: str, num_predict: Optional[int], timeout: float,
) -> tuple[Optional[str], Optional[str], list[str]]:
    """Try the signature models in order (max 3); a failing model is marked and
    dropped from `models` for the rest of the step. Returns (text, model, errors)."""
    errors: list[str] = []
    for m in list(models)[:3]:
        try:
            text = await asyncio.wait_for(_vision(hooks, m, prompt, image, num_predict=num_predict), timeout=timeout)
            return text, m, errors
        except Exception as e:
            errors.append(f"{m}: {str(e)[:90]}")
            try:
                hooks.mark_vision_failed(m, str(e))
            except Exception:
                pass
            if m in models:
                models.remove(m)
    return None, None, errors


async def signature_models(hooks: Any) -> list[str]:
    """Signature-role candidates (signature_model > handwriting_model > vision_model > …)."""
    fn = getattr(hooks, "signature_candidates", None) or getattr(hooks, "vision_candidates", None)
    if fn is None:
        return []
    try:
        cands = await fn()
    except Exception as e:
        log.debug(f"signature candidates failed: {e}")
        return []
    return [c for c in (cands or []) if c and not _is_ocr_only(c)]


async def run_signature_step(
    hooks: Any,
    *,
    run_id: Optional[str],
    questioned_doc: Any,
    reference_docs: list[Any],
    assess: bool = True,
) -> AsyncIterator[dict]:
    """Locate → segment → score → (optionally) visual assessment. Yields
    {"tile": …} events and finally {"_signature_result": {...}} (internal —
    callers must not forward keys starting with "_")."""
    import signature_engine as se

    now = hooks.now_ms
    run_id = run_id if (run_id and se.RUN_ID_RE.match(run_id)) else uuid.uuid4().hex[:16]
    started = now()
    items: list[dict] = []
    tile = _tile(started_ms=started, detail="Locating the signature…",
                 doc={"doc_id": questioned_doc.id, "name": questioned_doc.filename})
    yield {"tile": dict(tile)}

    def _fail(msg: str, status: str = "error") -> dict:
        tile.update(status=status, detail=msg, ms=now() - started, items=items + [{"kind": "note", "label": msg}])
        return {"tile": dict(tile)}

    ok, why = se.available()
    if not ok:
        yield _fail(f"Local signature engine unavailable — {why}")
        yield {"_signature_result": {"ok": False, "warning": why}}
        return

    try:
        out_dir_fn = getattr(hooks, "signature_dir", None)
        out_dir = Path(out_dir_fn(run_id) if out_dir_fn else default_signature_dir(run_id))
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        yield _fail(f"Could not create the crop folder: {e}")
        yield {"_signature_result": {"ok": False, "warning": str(e)}}
        return

    models = await signature_models(hooks)
    initial_models = list(models)
    model = models[0] if models else None
    if model:
        tile["model"] = model
        try:
            tile["model_info"] = await hooks.model_info(model)
        except Exception:
            tile["model_info"] = None

    # 1. references
    ref_crops: list[Any] = []
    ref_names: list[str] = []
    warnings: list[str] = []
    for rd in reference_docs:
        path = await _page_path(hooks, rd, last=False)
        if not path:
            warnings.append(f"No page image for {rd.filename}")
            continue
        try:
            crops, w = await asyncio.to_thread(se.analyze_card, path, 0, rd.filename)
        except (se.SignatureError, se.SignatureEngineUnavailable) as e:
            warnings.append(f"{rd.filename}: {e}")
            continue
        warnings.extend(w)
        if crops:
            ref_crops.extend(crops)
            ref_names.append(rd.filename)
    if not ref_crops:
        msg = "No reference signatures found on the reference card" + (f" ({'; '.join(warnings[:2])})" if warnings else "")
        yield _fail(msg)
        yield {"_signature_result": {"ok": False, "warning": msg}}
        return
    items.append({"kind": "note", "label": f"{len(ref_crops)} reference signature{'s' if len(ref_crops) != 1 else ''} segmented from {', '.join(ref_names)}"})
    tile.update(detail=f"{len(ref_crops)} reference signatures found · locating the questioned signature…", items=list(items))
    yield {"tile": dict(tile)}

    # 2. questioned signature
    qpath = await _page_path(hooks, questioned_doc, last=True)
    if not qpath:
        msg = f"No page image for {questioned_doc.filename}"
        yield _fail(msg)
        yield {"_signature_result": {"ok": False, "warning": msg}}
        return
    try:
        ink, la = await asyncio.to_thread(se.analyze_letter, qpath)
    except (se.SignatureError, se.SignatureEngineUnavailable) as e:
        msg = f"No signature found in {questioned_doc.filename}: {e}"
        yield _fail(msg)
        yield {"_signature_result": {"ok": False, "warning": msg}}
        return

    cands = [se.make_crop(ink, c.blob, questioned_doc.filename, i) for i, c in enumerate(la.candidates)]
    pick, picked_by = la.pick, "heuristic"
    cand_urls: list[str] = []
    if not la.standalone and len(cands) > 1:
        try:
            for i, c in enumerate(cands, 1):
                await asyncio.to_thread(se.save_png, c.gray, out_dir / f"cand_{i}.png")
                cand_urls.append(crop_url(run_id, f"cand_{i}.png"))
            from image_compose import compose_numbered_candidates
            sheet = out_dir / "candidates.png"
            await asyncio.to_thread(compose_numbered_candidates, [str(out_dir / f"cand_{i}.png") for i in range(1, len(cands) + 1)], str(sheet))
        except Exception as e:
            log.debug(f"candidate sheet failed: {e}")
            sheet = None
        if models and sheet is not None:
            tile.update(detail=f"Asking {model} which of {len(cands)} handwritten lines is the signature…")
            yield {"tile": dict(tile)}
            reply, used, errs = await _vision_fallback(
                hooks, models, PICK_PROMPT.format(n=len(cands)), str(sheet), 8, 120,
            )
            mp = parse_pick(reply, len(cands)) if reply is not None else None
            if mp is not None:
                agree = " (heuristic agrees)" if mp == la.pick else f" (heuristic suggested {la.pick + 1})"
                pick, picked_by = mp, "model"
                items.append({"kind": "note", "label": f"Signature located: line {mp + 1} of {len(cands)} — picked by {used}{agree}"})
            elif reply is not None:
                items.append({"kind": "note", "label": f"{used} gave no usable answer ('{(reply or '').strip()[:30]}') — heuristic pick: line {la.pick + 1} of {len(cands)}"})
            else:
                items.append({"kind": "note", "label": f"No signature model could pick the line ({'; '.join(errs)[:120]}) — heuristic pick: line {la.pick + 1} of {len(cands)}"})
            model = models[0] if models else None
            tile["model"] = model
        else:
            items.append({"kind": "note", "label": f"Signature located heuristically: line {la.pick + 1} of {len(cands)} (bottom of the letter)"})
    else:
        items.append({"kind": "note", "label": "Standalone signature image" if la.standalone else "One candidate region"})
    warnings.extend(la.warnings if picked_by == "heuristic" else [])
    q = cands[pick]

    # 3. score
    tile.update(detail=f"Comparing with {len(ref_crops)} reference signatures…", items=list(items))
    yield {"tile": dict(tile)}
    try:
        result = await asyncio.to_thread(se.compare, q, ref_crops)
    except (se.SignatureError, se.SignatureEngineUnavailable) as e:
        msg = f"Signature comparison failed: {e}"
        yield _fail(msg)
        yield {"_signature_result": {"ok": False, "warning": msg}}
        return
    result["warnings"] = list(dict.fromkeys(warnings + (result.get("warnings") or [])))

    # 4. crops for the UI / vision model
    try:
        await asyncio.to_thread(se.save_png, q.gray, out_dir / "questioned.png")
        for i, r in enumerate(ref_crops, 1):
            await asyncio.to_thread(se.save_png, r.gray, out_dir / f"ref_{i}.png")
    except Exception as e:
        log.warning(f"saving signature crops failed: {e}")
    result.update({
        "ok": True,
        "run_id": run_id,
        "questioned_doc": questioned_doc.filename,
        "questioned_doc_id": questioned_doc.id,
        "reference_docs": ref_names,
        "reference_doc_ids": [d.id for d in reference_docs if d.filename in ref_names],
        "questioned": {"url": crop_url(run_id, "questioned.png"), "doc": questioned_doc.filename, "bbox": list(q.bbox)},
        "references": [{"url": crop_url(run_id, f"ref_{i}.png"), "doc": r.source} for i, r in enumerate(ref_crops, 1)],
        "candidates": {
            "count": len(cands), "picked": pick + 1, "picked_by": picked_by,
            "urls": cand_urls, "sheet": crop_url(run_id, "candidates.png") if cand_urls else None,
        },
        "files": {
            "questioned": str(out_dir / "questioned.png"),
            "references": [str(out_dir / f"ref_{i}.png") for i in range(1, len(ref_crops) + 1)],
        },
        "engine": "local · OpenCV + scikit-image (deterministic)",
        "model": model,
        "assessment": None,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    tile.update(
        detail=f"{score_line(result)}" + (" · asking the signature model…" if assess and model else ""),
        data=_tile_data(result), items=list(items),
    )
    yield {"tile": dict(tile)}

    # 5. visual assessment by the signature model
    if assess and models:
        try:
            from image_compose import compose_signature_sheet
            sheet = out_dir / "compare.png"
            await asyncio.to_thread(
                compose_signature_sheet, str(out_dir / "questioned.png"),
                [str(out_dir / f"ref_{i}.png") for i in range(1, len(ref_crops) + 1)], str(sheet),
            )
            prompt = ASSESS_PROMPT.format(n=len(ref_crops), engine=engine_summary(result))
            text, used, errs = await _vision_fallback(hooks, models, prompt, str(sheet), 450, 240)
            text = (text or "").strip()
            if text and used:
                result["assessment"] = text
                result["model"] = used
                tile["model"] = used
                items.append({"kind": "note", "label": f"Visual assessment by {used}"})
            else:
                result["warnings"].append(
                    "Visual assessment failed" + (f" ({'; '.join(errs)[:140]})" if errs else "")
                )
        except Exception as e:
            result["warnings"].append(f"Visual assessment failed: {str(e)[:100]}")
    elif assess and initial_models:
        result["warnings"].append(
            f"Visual assessment skipped — {', '.join(initial_models[:3])} failed earlier in this step"
        )
    elif assess:
        result["warnings"].append("No vision-capable Signature model available — score from the local engine only")

    tile.update(
        status="done", detail=score_line(result), ms=now() - started,
        data=_tile_data(result), items=list(items), output_preview=(result.get("assessment") or "")[:300] or None,
    )
    yield {"tile": dict(tile)}
    yield {"_signature_result": result}


def _tile_data(result: dict) -> dict:
    return {
        "type": "signature",
        "score": result["score"],
        "band": result["band"],
        "band_label": result["band_label"],
        "combined_z": result.get("combined_z"),
        "n_references": result["n_references"],
        "breakdown": result.get("breakdown") or [],
        "reasons": result.get("reasons") or [],
        "warnings": result.get("warnings") or [],
        "model": result.get("model"),
        "assessment": (result.get("assessment") or None) and result["assessment"][:1500],
        "questioned": result.get("questioned"),
        "references": result.get("references") or [],
        "candidates": result.get("candidates"),
        "engine": result.get("engine"),
        "questioned_doc": result.get("questioned_doc"),
        "reference_docs": result.get("reference_docs") or [],
        "caveat": CAVEAT,
    }
