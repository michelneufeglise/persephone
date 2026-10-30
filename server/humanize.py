"""
Section-wise document rewriting — Humanize, plus the block/group pipeline the
Translate and Redact ops share.

Why blocks: one prompt over the whole document (the old approach) had three
failure modes — the 24 000-char cap, a fixed num_predict that thinking models
spent on reasoning (output stopped mid page 2), and tables/headings flattened
into one blob. Here the document is segmented into structural blocks
(headings, paragraphs, list items, table cells), protected blocks (headings,
short labels, table header row, captions, numbers) pass through verbatim, and
each body block is rewritten on its own with its neighbours as context,
sized num_predict, continuation on done_reason == 'length', think=false and a
per-block quality guard (repetition / length / missing names & numbers →
one stricter retry → keep the original and flag it).

Streamed events (passed straight through /api/idp/stream/{op} as SSE):
  {'phase': 'segmented', 'of': M, 'blocks': N, 'language': 'nl', 'model': …}
  {'phase': 'block', 'i': k, 'of': M, 'detail': 'block k/M'}
  {'draft': delta}            live tokens of the block being written (not final)
  {'content': piece}          final text, in document order, markdown structure
  {'flag': {...}}             a block kept in its original form
  {'done': True, 'stats': {...}, 'run_id': …, 'flagged': [...], 'docx': bool}

For .docx sources the result blocks are cached per run (memory + a small JSON
under the document's upload dir) so apply_docx() can write them back into a
copy of the original file without re-running the model.
"""
from __future__ import annotations

import copy
import io
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

import idp_engine as _idp

log = logging.getLogger("persephone.humanize")

# ── Tunables ──────────────────────────────────────────────────────────────────
MAX_CONTINUATIONS = 3          # extra calls when a block hits num_predict
CONTEXT_CHARS = 600            # neighbour-block context per side
GROUP_CHARS = 2500             # translate / redact: max chars per model call
PROTECT_MAX_WORDS = 4          # ≤ this many words → label, never rewritten
CAPTION_MAX_WORDS = 12         # no terminal punctuation and ≤ this → label/caption
RUNS_KEPT_PER_DOC = 5

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


# ── Blocks ────────────────────────────────────────────────────────────────────
@dataclass
class Block:
    idx: int
    kind: str                 # heading | para | list | cell | caption | label | empty
    text: str                 # original text (stripped)
    rewrite: bool = False
    reason: str = ""          # why a block is protected
    prefix: str = ""          # verbatim lead kept before the rewritten part (Q&A question / "Label:")
    sep: str = ""             # whitespace between prefix and body
    level: int = 0            # heading level
    marker: str = ""          # list marker for rendering ("- ", "1. ")
    table: int | None = None
    row: int | None = None
    col: int | None = None
    page: int | None = None
    loc: dict = field(default_factory=dict)   # docx locator {'n': walk index}

    @property
    def body(self) -> str:
        return self.text[len(self.prefix) + len(self.sep):] if self.prefix else self.text


def _words(s: str) -> list[str]:
    return re.findall(r"[\wÀ-ÖØ-öø-ÿ’'-]+", s or "")


_TERMINAL = ".!?:;…"
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b|\bca\.\s")
_QUESTION_LEAD_RE = re.compile(r"^([^\n?.!]{3,250}\?)(\s+)(?=\S)")
_LABEL_LEAD_RE = re.compile(r"^([A-ZÀ-Þ][^\n.:?!]{1,40}:)(\s+)(?=\S)")


def _split_lead(text: str) -> tuple[str, str]:
    """('Question?', ' ') when a paragraph is 'Question? Answer…' or
    'Label: text…' — the lead is kept verbatim, only the answer is rewritten."""
    m = _QUESTION_LEAD_RE.match(text)
    if m and len(_words(text[m.end():])) >= 3:
        return m.group(1), m.group(2)
    m = _LABEL_LEAD_RE.match(text)
    if m and len(_words(m.group(1))) <= 5 and len(_words(text[m.end():])) >= 3:
        return m.group(1), m.group(2)
    return "", ""


def classify(block: Block, *, header_row: bool = False) -> Block:
    """Decide whether a block is rewritten. Mutates and returns it."""
    t = block.text.strip()
    block.rewrite = False
    if not t:
        block.kind, block.reason = "empty", "empty"
        return block
    if block.kind == "heading":
        block.reason = "heading"
        return block
    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", t):
        block.reason = "numbers"
        return block
    if block.table is not None and header_row:
        block.reason = "table header"
        return block
    if block.kind == "caption":
        block.reason = "caption"
        return block
    if not block.prefix:
        p, s = _split_lead(t)
        if p:
            block.prefix, block.sep = p, s
    body = block.body.strip()
    n = len(_words(body))
    if n <= PROTECT_MAX_WORDS:
        block.reason = "short label" if not block.prefix else "short answer"
        if block.kind not in ("cell", "list"):
            block.kind = "label"
        return block
    if body[-1:] not in _TERMINAL and body[-1:] not in "”\"’')»" and n <= CAPTION_MAX_WORDS:
        block.reason = "caption/label"
        if block.kind not in ("cell", "list"):
            block.kind = "caption" if _YEAR_RE.search(body) else "label"
        return block
    if not block.prefix and body.endswith("?") and n <= 25:
        block.reason = "question"
        return block
    block.rewrite = True
    block.reason = ""
    return block


def _classify_all(blocks: list[Block]) -> list[Block]:
    first_row: dict[int, int] = {}
    for b in blocks:
        if b.table is not None and b.row is not None:
            first_row.setdefault(b.table, b.row)
    for i, b in enumerate(blocks):
        b.idx = i
        classify(b, header_row=b.table is not None and b.row == first_row.get(b.table))
    return blocks


# ── Segmentation: .docx ───────────────────────────────────────────────────────
def _nearest_p(el):
    cur = el.getparent()
    while cur is not None and cur.tag != _w("p"):
        cur = cur.getparent()
    return cur


def _in_fallback(el) -> bool:
    cur = el.getparent()
    while cur is not None:
        if cur.tag == f"{{{MC_NS}}}Fallback":
            return True
        cur = cur.getparent()
    return False


def _p_text(p_el) -> str:
    """Text of a w:p (own runs only — nested text-box paragraphs excluded)."""
    parts: list[str] = []
    for el in p_el.iter(_w("t"), _w("tab"), _w("br"), _w("cr")):
        if _nearest_p(el) is not p_el or _in_fallback(el):
            continue
        if el.tag == _w("t"):
            parts.append(el.text or "")
        elif el.tag == _w("tab"):
            # w:tab inside w:pPr/w:tabs is a tab-stop definition, not text
            if el.getparent() is not None and el.getparent().tag == _w("r"):
                parts.append("\t")
        elif el.tag == _w("br"):
            if el.get(_w("type")) not in ("page", "column"):
                parts.append("\n")
        else:
            parts.append("\n")
    return "".join(parts)


def _p_is_complex(p_el) -> bool:
    """Paragraphs whose text is not all in direct runs (hyperlinks, fields,
    tracked changes, smart tags, content controls) are never rewritten — the
    write-back could not keep them intact."""
    if p_el.find(f".//{_w('fldChar')}") is not None or p_el.find(f".//{_w('instrText')}") is not None:
        return True
    for t in p_el.iter(_w("t")):
        if _nearest_p(t) is not p_el or _in_fallback(t):
            continue
        r = t.getparent()
        if r is None or r.tag != _w("r") or r.getparent() is not p_el:
            return True
    return False


def _walk_docx(document) -> list[dict]:
    """Every paragraph of the body in document order, including table cells
    (top-level tables only; nested tables are left untouched). The order is
    deterministic, so the walk index ('n') locates a paragraph again when
    writing results back into a fresh copy of the same file."""
    out: list[dict] = []
    tcount = [0]

    def walk(container, tctx: dict | None):
        for child in container.iterchildren():
            tag = child.tag
            if tag == _w("p"):
                out.append({"el": child, **(tctx or {})})
            elif tag == _w("tbl"):
                if tctx is not None:
                    continue
                ti = tcount[0]
                tcount[0] += 1
                for ri, tr in enumerate(child.iterchildren(_w("tr"))):
                    for ci, tc in enumerate(tr.iterchildren(_w("tc"))):
                        walk(tc, {"table": ti, "row": ri, "col": ci})
            elif tag == _w("sdt"):
                content = child.find(_w("sdtContent"))
                if content is not None:
                    walk(content, tctx)

    walk(document.element.body, None)
    return out


def _run_bold(run, para_style_bold: bool | None) -> bool:
    if run.bold is not None:
        return bool(run.bold)
    try:
        if run.style is not None and run.style.font.bold:
            return True
    except Exception:
        pass
    return bool(para_style_bold)


def segment_docx(path: Path | str) -> list[Block]:
    from docx import Document as DocxDocument  # type: ignore[import-not-found]
    from docx.text.paragraph import Paragraph

    document = DocxDocument(str(path))
    blocks: list[Block] = []
    for n, item in enumerate(_walk_docx(document)):
        p_el = item["el"]
        raw = _p_text(p_el)
        text = raw.strip()
        if not text:
            continue
        para = Paragraph(p_el, document._body)
        try:
            style_name = (para.style.name or "") if para.style is not None else ""
        except Exception:
            style_name = ""
        try:
            style_bold = para.style.font.bold if para.style is not None else None
        except Exception:
            style_bold = None
        in_table = "table" in item
        b = Block(idx=0, kind="cell" if in_table else "para", text=text, loc={"n": n},
                  table=item.get("table"), row=item.get("row"), col=item.get("col"))
        sl = style_name.lower()
        outline = p_el.find(f"{_w('pPr')}/{_w('outlineLvl')}")
        if re.match(r"^(heading|kop|title|titel|subtitle|ondertitel)", sl) or outline is not None:
            b.kind = "heading"
            m = re.search(r"(\d)", sl)
            b.level = 1 if sl.startswith(("title", "titel")) else (int(m.group(1)) if m else 2)
        elif p_el.find(f"{_w('pPr')}/{_w('numPr')}") is not None or "list" in sl or "lijst" in sl:
            if not in_table:
                b.kind = "list"
                b.marker = "- "
        if _p_is_complex(p_el):
            b.reason = "links/fields"
            b.loc["complex"] = True
        # Bold structure: an all-bold short line is a heading; a bold lead
        # ("Question?" / "Extra vraag:") followed by plain text is a Q&A prefix.
        runs = [r for r in para.runs if (r.text or "").strip()]
        if runs and b.kind in ("para", "list"):
            bold = [_run_bold(r, style_bold) for r in runs]
            if all(bold):
                if len(_words(text)) <= 15 and text[-1:] not in ".!?;…" and not in_table:
                    b.kind, b.level = "heading", 2
            elif bold[0]:
                lead = ""
                for r, isb in zip(para.runs, [_run_bold(r, style_bold) for r in para.runs]):
                    if not (r.text or "").strip():
                        lead += r.text or ""
                        continue
                    if not isb:
                        break
                    lead += r.text or ""
                lead_s = lead.strip()
                rest = text[len(lead_s):] if text.startswith(lead_s) else ""
                if lead_s and rest.strip() and len(lead_s) <= 300 and len(_words(rest)) >= 3:
                    sep = rest[: len(rest) - len(rest.lstrip())]
                    b.prefix, b.sep = lead_s, sep
        blocks.append(b)
    _classify_all(blocks)
    for b in blocks:
        if b.loc.get("complex"):
            b.rewrite = False
            b.reason = "links/fields"
    return blocks


# ── Segmentation: PDF (text layer) and plain / markdown text ─────────────────
_BULLET_ONLY = {"•", "◦", "▪", "‣", "●", "○", "-", "–", "*"}
_LIST_RE = re.compile(r"^(?:([-*•◦▪‣●○–])|(\d{1,3}[.)]))\s+")


def _join_lines(lines: list[str], trailing_spaces: bool) -> str:
    out = ""
    for i, ln in enumerate(lines):
        if i == 0:
            out = ln
            continue
        if not ln.strip():
            continue
        if out.endswith("-") and not out.endswith(" -") and ln[:1].islower():
            out = out[:-1] + ln
        elif out.endswith((" ", " ", "\t")):
            out = out + ln
        elif trailing_spaces:
            out = out + ln        # producer marks word ends with a space → mid-word break
        else:
            out = out + " " + ln
    return re.sub(r"[ \t ]+", " ", out).strip()


def _blocks_from_lines(lines: list[str], *, page: int | None, trailing_spaces: bool,
                       single_block: bool = False) -> list[Block]:
    """Paragraphs / list items / markdown headings & tables from raw lines.
    With single_block (one PDF text block), lines are one paragraph except
    where a bullet starts a new list item."""
    blocks: list[Block] = []
    buf: list[str] = []
    cur_kind = "para"
    cur_marker = ""
    table_rows: list[list[str]] = []
    tcounter = [0]

    def flush():
        nonlocal buf, cur_kind, cur_marker
        if buf:
            t = _join_lines(buf, trailing_spaces)
            if t:
                blocks.append(Block(idx=0, kind=cur_kind, text=t, marker=cur_marker, page=page))
        buf, cur_kind, cur_marker = [], "para", ""

    def flush_table():
        nonlocal table_rows
        if table_rows:
            ti = tcounter[0]
            tcounter[0] += 1
            for ri, cells in enumerate(table_rows):
                for ci, c in enumerate(cells):
                    blocks.append(Block(idx=0, kind="cell", text=c.strip(), table=ti, row=ri, col=ci, page=page))
            table_rows = []

    for line in lines:
        s = line.strip()
        if not s:
            if not single_block:
                flush()
                flush_table()
            continue
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            flush()
            inner = s[1:-1]
            if re.fullmatch(r"[\s|:\-]+", inner):
                continue
            table_rows.append([c for c in inner.split("|")])
            continue
        flush_table()
        mh = re.match(r"^(#{1,6})\s+(.*)$", s)
        if mh and not single_block:
            flush()
            blocks.append(Block(idx=0, kind="heading", text=mh.group(2).strip(),
                                level=len(mh.group(1)), page=page))
            continue
        if s in _BULLET_ONLY:
            flush()
            cur_kind, cur_marker = "list", "- "
            continue
        ml = _LIST_RE.match(s)
        if ml:
            flush()
            cur_kind = "list"
            cur_marker = (ml.group(2) + " ") if ml.group(2) else "- "
            rest = line.lstrip()[ml.end():]
            buf = [rest] if rest.strip() else []
            continue
        buf.append(line if trailing_spaces else s)
    flush()
    flush_table()
    return blocks


def segment_text(text: str, page: int | None = None) -> list[Block]:
    blocks = _blocks_from_lines((text or "").splitlines(), page=page, trailing_spaces=False)
    for b in blocks:
        if b.kind == "para" and "\n" not in b.text and _looks_like_heading(b.text):
            b.kind = "caption" if _YEAR_RE.search(b.text) else "label"
    return blocks


def _looks_like_heading(t: str) -> bool:
    return 0 < len(_words(t)) <= CAPTION_MAX_WORDS and t[-1:] not in _TERMINAL and "\n" not in t


def _pdf_page_blocks(page, pno: int) -> list[Block]:
    import fitz  # type: ignore[import-not-found]

    raw_lines = (page.get_text("text") or "").splitlines()
    multi = [ln for ln in raw_lines if ln.strip()]
    trailing = bool(multi) and sum(1 for ln in multi if ln.endswith(" ")) >= 0.3 * len(multi)

    items: list[tuple[float, float, str, Any]] = []
    tables = []
    try:
        tables = list(page.find_tables().tables)
    except Exception as exc:  # pragma: no cover - optional feature
        log.debug("find_tables failed on page %d: %s", pno + 1, exc)
    trects = [fitz.Rect(t.bbox) for t in tables]
    for t in tables:
        items.append((t.bbox[1], t.bbox[0], "table", t))
    for b in page.get_text("blocks", sort=True):
        if len(b) > 6 and b[6] != 0:
            continue
        r = fitz.Rect(b[:4])
        if any((r & tr).get_area() > 0.5 * max(r.get_area(), 1e-6) for tr in trects):
            continue
        items.append((b[1], b[0], "text", b[4]))
    items.sort(key=lambda x: (round(x[0]), x[1]))

    out: list[Block] = []
    for _, _, kind, payload in items:
        if kind == "text":
            lines = payload.split("\n")
            got = _blocks_from_lines(lines, page=pno + 1, trailing_spaces=trailing, single_block=True)
            if len(got) == 1 and got[0].kind == "para" and _looks_like_heading(got[0].text) \
                    and len([ln for ln in lines if ln.strip()]) == 1:
                got[0].kind = "caption" if _YEAR_RE.search(got[0].text) else "heading"
                got[0].level = 2
            out.extend(got)
        else:
            t = payload
            ti = len({b.table for b in out if b.table is not None})
            for ri, row in enumerate(t.rows):
                for ci, cell in enumerate(row.cells):
                    if cell is None:
                        continue
                    txt = page.get_text("text", clip=fitz.Rect(cell)) or ""
                    joined = _join_lines(txt.split("\n"), trailing)
                    out.append(Block(idx=0, kind="cell", text=joined, table=(pno + 1) * 1000 + ti,
                                     row=ri, col=ci, page=pno + 1))
    return out


def segment_pdf(path: Path | str, page_texts: list[str] | None = None) -> list[Block]:
    """PDF pages with a usable native text layer are segmented from PyMuPDF
    blocks (+ detected tables); pages without one (scans) fall back to the
    document's page text (OCR output)."""
    import fitz  # type: ignore[import-not-found]

    blocks: list[Block] = []
    with fitz.open(str(path)) as pdf:
        for pno, page in enumerate(pdf):
            native = page.get_text("text") or ""
            if _idp.usable_text_layer(native):
                blocks.extend(_pdf_page_blocks(page, pno))
            else:
                fallback = (page_texts or [])[pno] if page_texts and pno < len(page_texts) else ""
                blocks.extend(segment_text(fallback, page=pno + 1))
    return blocks


def raw_file(doc) -> Path | None:
    p = _idp.STORAGE_DIR / doc.id / doc.filename
    return p if p.exists() else None


def source_kind(doc) -> str:
    fn = (doc.filename or "").lower()
    if fn.endswith(".docx") and raw_file(doc) is not None:
        return "docx"
    if (doc.mime == "application/pdf" or fn.endswith(".pdf")) and raw_file(doc) is not None:
        return "pdf"
    return "text"


def segment_document(doc) -> tuple[list[Block], str]:
    kind = source_kind(doc)
    blocks: list[Block] = []
    try:
        if kind == "docx":
            blocks = segment_docx(raw_file(doc))
        elif kind == "pdf":
            blocks = segment_pdf(raw_file(doc), doc.page_texts)
    except Exception as exc:
        log.warning("structured segmentation failed for %s (%s) — using text", doc.id, exc)
        blocks, kind = [], "text"
    if kind != "docx":
        if not blocks:
            kind = "text"
            pages = doc.page_texts if doc.page_texts and len(doc.page_texts) > 1 else [doc.text or ""]
            for i, pt in enumerate(pages):
                blocks.extend(segment_text(pt, page=i + 1 if len(pages) > 1 else None))
        _classify_all(blocks)
    return blocks, kind


# ── Rendering (markdown, document order) ──────────────────────────────────────
def _cell_md(s: str) -> str:
    return re.sub(r"\s*\n\s*", " ", s or "").replace("|", "\\|").strip()


def render_block(b: Block, text: str) -> str:
    if b.kind == "heading":
        return f"{'#' * max(1, min(b.level or 2, 4))} {text}"
    if b.kind == "list":
        return f"{b.marker or '- '}{text}"
    return text


class Emitter:
    """Turns (block, final text) pairs — fed in document order — into
    markdown pieces. Table cells are buffered per row and emitted as a
    markdown table row once the row is complete."""

    def __init__(self) -> None:
        self.started = False
        self.table: int | None = None
        self.row: int | None = None
        self.cells: dict[int, list[str]] = {}
        self.rows_done = 0
        self.header_cols = 0

    def _sep(self, s: str) -> str:
        return s if self.started else ""

    def _flush_row(self) -> str:
        if self.table is None or self.row is None:
            return ""
        ncols = max(self.cells) + 1 if self.cells else 0
        if self.rows_done == 0:
            self.header_cols = ncols
        ncols = max(ncols, self.header_cols)
        vals = [" ".join(self.cells.get(c, [])) for c in range(ncols)]
        line = "| " + " | ".join(_cell_md(v) for v in vals) + " |"
        prefix = self._sep("\n\n") if self.rows_done == 0 else "\n"
        if self.rows_done == 0:
            line += "\n|" + "|".join(" --- " for _ in range(ncols)) + "|"
        self.started = True
        self.rows_done += 1
        self.cells = {}
        self.row = None
        return prefix + line

    def _end_table(self) -> str:
        out = self._flush_row()
        self.table = None
        self.rows_done = 0
        self.header_cols = 0
        return out

    def feed(self, b: Block, text: str) -> str:
        out = ""
        if b.table is not None:
            if b.table != self.table:
                out += self._end_table()
                self.table = b.table
            if b.row != self.row and self.row is not None:
                out += self._flush_row()
            self.row = b.row
            self.cells.setdefault(b.col or 0, []).append(text)
            return out
        out += self._end_table()
        if b.kind == "empty" or not text.strip():
            return out
        out += self._sep("\n\n") + render_block(b, text)
        self.started = True
        return out

    def flush(self) -> str:
        return self._end_table()


def render_markdown(blocks: list[Block], outputs: dict[int, str] | None = None) -> str:
    em = Emitter()
    parts = [em.feed(b, (outputs or {}).get(b.idx, b.text)) for b in blocks]
    parts.append(em.flush())
    return "".join(parts)


# ── Language & prompts ────────────────────────────────────────────────────────
_LANG_NAMES = {
    "en": "English", "nl": "Dutch", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
}


def detect_language(text: str) -> str:
    try:
        from speech_summary import detect_language as _det
        return _det(text or "") or "en"
    except Exception:  # pragma: no cover
        return "en"


_TONE_EN = {
    "natural":      "natural and clear, lightly personal — like a thoughtful person wrote it",
    "casual":       "relaxed and informal, like a friendly email",
    "professional": "clear and confident, like a well-written business memo",
    "academic":     "careful and precise, but without stiff or robotic phrasing",
}
_TONE_NL = {
    "natural":      "natuurlijk en helder, licht persoonlijk — alsof een bedachtzaam mens het schreef",
    "casual":       "ontspannen en informeel, zoals een vriendelijke e-mail",
    "professional": "zakelijk, helder en zelfverzekerd",
    "academic":     "zorgvuldig en precies, maar niet stijf of houterig",
}
_INTENSITY_EN = {
    "light":  "Change as little as possible: only smooth out stiff or clumsy phrasing. Keep most of the wording and the sentence order.",
    "medium": "Rework the sentence structure: vary sentence length and rhythm, split long sentences or join short ones where that reads more naturally. The content stays the same.",
    "heavy":  "Rewrite more freely, in your own words and rhythm — but stay correct and completely faithful to the content.",
}
_INTENSITY_NL = {
    "light":  "Verander zo weinig mogelijk: maak alleen stijve of houterige formuleringen soepeler. Behoud de meeste woorden en de zinsvolgorde.",
    "medium": "Herschrijf de zinsbouw: varieer zinslengte en ritme, splits lange zinnen of voeg korte samen waar dat natuurlijker leest. De inhoud blijft gelijk.",
    "heavy":  "Herschrijf vrijer, in eigen woorden en met een eigen ritme — maar blijf correct en volledig trouw aan de inhoud.",
}
AI_PHRASES_EN = [
    "delve", "furthermore", "moreover", "in conclusion", "it is important to note",
    "navigate the landscape", "a testament to", "in today's fast-paced world", "tapestry",
    "realm", "crucial", "pivotal", "multifaceted", "holistic", "seamless", "let's explore",
]
AI_PHRASES_NL = [
    "Het is belangrijk om te benadrukken", "Het is belangrijk op te merken", "Kortom",
    "Daarnaast is het van belang", "in de hedendaagse samenleving", "in de huidige snelle wereld",
    "speelt een cruciale rol", "Al met al", "Concluderend", "Bovendien", "Tevens",
    "een rijk scala aan", "naadloos", "veelzijdig", "Laten we", "Dit toont aan dat",
]


def _system_prompt(lang: str, tone: str, intensity: str) -> str:
    intensity = intensity if intensity in _INTENSITY_EN else "medium"
    tone = tone if tone in _TONE_EN else "natural"
    if lang == "nl":
        phrases = ", ".join(f"„{p}”" for p in AI_PHRASES_NL)
        return (
            "Je bent een ervaren Nederlandstalige redacteur. Je herschrijft één tekstblok uit een "
            "document zodat het natuurlijk leest, alsof een mens het schreef.\n\n"
            f"Toon: {_TONE_NL[tone]}.\n"
            f"Mate van herschrijven: {_INTENSITY_NL[intensity]}\n\n"
            "Regels:\n"
            "- Behoud de betekenis en elk feit, elke naam, elk getal, jaartal, label (zoals V, R, M) en citaat exact.\n"
            "- Schrijf foutloos Nederlands: juiste spelling inclusief accenten (één, privé, café, financiële), "
            "correcte grammatica en interpunctie. Geen Engelse woorden of Engelse samentrekkingen.\n"
            "- Laat aanhalingstekens („ “ ” ‘ ’) en de tekst ertussen staan zoals ze zijn.\n"
            "- Herhaal nooit een zin of zinsdeel. Voeg geen nieuwe inhoud, voorbeelden, meningen of conclusies toe. Laat niets weg.\n"
            "- Maak nooit opzettelijk fouten of 'menselijke imperfecties'.\n"
            "- Wissel langere en kortere zinnen af; vermijd steeds dezelfde zinsopbouw.\n"
            f"- Vermijd clichés en typische AI-formuleringen, zoals {phrases}.\n"
            "- Houd dezelfde persoon, werkwoordstijd en het perspectief aan (ik-vorm blijft ik-vorm, "
            "tegenwoordige tijd blijft tegenwoordige tijd). Verander niet wie wat doet of van wie iets is.\n"
            "- Laat vaktermen en kernbegrippen (zoals historieschilderkunst, naaktstudie, agency) ongewijzigd.\n"
            "- Geef ALLEEN het herschreven blok terug: geen inleiding, geen uitleg, geen titel, "
            "geen aanhalingstekens rond het geheel, geen markdown."
        )
    lname = _LANG_NAMES.get(lang, "the document's language")
    phrases = ", ".join(f"'{p}'" for p in AI_PHRASES_EN)
    lang_rule = (
        "- Write correct, idiomatic English with correct spelling, grammar and punctuation."
        + (" Contractions (it's, don't) are fine where they suit the tone." if tone in ("natural", "casual") else "")
        if lang == "en" else
        f"- Write in {lname} only — do not translate. Follow {lname} spelling (including accents and "
        f"diacritics), grammar, punctuation and quotation-mark conventions. No English words, no English contractions."
    )
    return (
        "You are an experienced editor. You rewrite ONE block of text from a document so it reads "
        "naturally, as if a person wrote it.\n\n"
        f"Tone: {_TONE_EN[tone]}.\n"
        f"Rewrite strength: {_INTENSITY_EN[intensity]}\n\n"
        "Rules:\n"
        "- Keep the meaning and every fact, name, number, date, label and quotation exactly.\n"
        f"{lang_rule}\n"
        "- Keep quotation marks and the quoted words as they are.\n"
        "- Never repeat a sentence or phrase. Do not add new content, examples, opinions or conclusions. Do not leave anything out.\n"
        "- Never add deliberate errors or 'human imperfections'.\n"
        "- Vary sentence length and structure.\n"
        f"- Avoid stock AI phrasing such as {phrases}.\n"
        "- Keep the same person, tense and point of view. Do not change who does what or who owns what.\n"
        "- Keep technical terms and key concepts unchanged.\n"
        "- Output ONLY the rewritten block: no preamble, no explanation, no title, no quotes around it, no markdown."
    )


def _clip(s: str, n: int = CONTEXT_CHARS) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"


def build_block_messages(block: Block, ctx: dict, *, lang: str, tone: str, intensity: str,
                         issues: list[str] | None = None) -> list[dict]:
    nl = lang == "nl"
    lines: list[str] = []
    if ctx.get("title"):
        lines.append(("Document: " if not nl else "Document: ") + ctx["title"])
    if ctx.get("section"):
        lines.append(("Section: " if not nl else "Sectie: ") + ctx["section"])
    if block.table is not None:
        col = ctx.get("column")
        lines.append(
            (f"Dit blok is een cel in een tabel{f' (kolom „{col}”)' if col else ''}. Houd het ongeveer even lang."
             if nl else
             f"This block is a table cell{f' (column “{col}”)' if col else ''}. Keep it about the same length.")
        )
    if block.prefix:
        lines.append(
            (f"Dit blok is het antwoord op: „{block.prefix}” — herschrijf alleen het antwoord, herhaal de vraag niet."
             if nl else
             f"This block answers: “{block.prefix}” — rewrite only the answer, do not repeat the question.")
        )
    if ctx.get("prev"):
        lines.append(("Vorige tekst (alleen context, niet herschrijven):\n" if nl else
                      "Previous text (context only, do not rewrite):\n") + _clip(ctx["prev"]))
    if ctx.get("next"):
        lines.append(("Volgende tekst (alleen context, niet herschrijven):\n" if nl else
                      "Next text (context only, do not rewrite):\n") + _clip(ctx["next"]))
    if issues:
        lines.append(
            ("Let op — je vorige versie had deze problemen: " if nl else
             "Important — your previous attempt had these problems: ")
            + "; ".join(issues)
            + (". Blijf dicht bij het origineel, herhaal niets en neem alle namen en getallen over."
               if nl else ". Stay close to the original, repeat nothing and keep every name and number.")
        )
    lines.append(("Herschrijf dit blok:\n" if nl else "Rewrite this block:\n") + "<<<\n" + block.body.strip() + "\n>>>")
    return [
        {"role": "system", "content": _system_prompt(lang, tone, intensity)},
        {"role": "user", "content": "\n\n".join(lines)},
    ]


# ── Output cleaning & quality guard ───────────────────────────────────────────
_PREAMBLE_RE = re.compile(
    r"^\s*(hier is|hieronder|herschreven|herschrijving|de herschreven|here is|here's|rewritten|sure|certainly|"
    r"natuurlijk|zeker|antwoord|answer)\b[^\n]{0,80}:\s*\n", re.IGNORECASE)


def clean_output(out: str, block: Block | None = None) -> str:
    s = out or ""
    s = re.sub(r"<think>[\s\S]*?</think>", "", s)
    if "<think>" in s:
        s = s.split("<think>", 1)[0]
    s = s.strip()
    fence = re.match(r"^```[a-zA-Z]*\n([\s\S]*?)\n?```$", s)
    if fence:
        s = fence.group(1).strip()
    s = _PREAMBLE_RE.sub("", s, count=1).strip()
    s = s.replace("<<<", "").replace(">>>", "").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"' and block is not None and not block.body.strip().startswith('"'):
        s = s[1:-1].strip()
    if block is not None and block.prefix and s.startswith(block.prefix):
        s = s[len(block.prefix):].strip()
    if block is not None and "\n" not in block.body.strip():
        s = re.sub(r"\s*\n+\s*", " ", s)
    if block is not None:
        s = restore_quotes(block.body, s)
    return s.strip()


def restore_quotes(src: str, out: str) -> str:
    """Put the source's quotation-mark style back when the model switched it
    (“…” in the source, „…“ or "…" in the rewrite)."""
    if "“" in src and "„" not in src and "„" in out:
        out = re.sub(r"„([^„“”\"\n]{0,300}?)[“”\"]", r"“\1”", out)
    if "“" in src and '"' not in src and '"' in out:
        out = re.sub(r'"([^"\n]{0,300}?)"', r"“\1”", out)
    return out


_QUOTE_SPAN_RE = re.compile(r"[“„\"]([^“”„\"\n]{1,120})[”“\"]")


def quoted_spans(text: str) -> list[str]:
    return [m.group(1).strip() for m in _QUOTE_SPAN_RE.finditer(text or "") if m.group(1).strip()]


def key_terms(text: str) -> list[str]:
    """Long lowercase content words (Dutch compounds such as
    'historieschilderkunst') — swapping one usually changes the meaning."""
    seen: list[str] = []
    for w in re.findall(r"(?<![\wÀ-ÿ])[a-zà-öø-ÿ]{14,}(?![\wÀ-ÿ])", text or ""):
        if w not in seen:
            seen.append(w)
    return seen


def _term_present(term: str, out_lower: str) -> bool:
    need = min(len(term) - 3, 12)
    return any(w[:need] == term[:need] for w in re.findall(r"[a-zà-öø-ÿ]+", out_lower))


def _norm_dash(s: str) -> str:
    return re.sub(r"[‐‑‒–—−]", "-", s)


_CAP_RE = re.compile(r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ’'-]*")
_NUM_RE = re.compile(r"\d+(?:[.,:/‐‑‒–—−-]\d+)*")


def protected_tokens(text: str, lang: str = "", names: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Numbers, dates and names a rewrite must keep: capitalised words not at
    a sentence start, single-letter labels like V/R/M, and any document-level
    name (`names`) even at a sentence start."""
    toks: list[str] = []
    seen: set[str] = set()

    def add(t: str):
        if t not in seen:
            seen.add(t)
            toks.append(t)

    for m in _NUM_RE.finditer(text):
        add(_norm_dash(m.group()))
    for m in _CAP_RE.finditer(text):
        w = m.group().rstrip("’'-")
        before = text[: m.start()].rstrip()
        before = before.rstrip("\"“„‘'«»”’")
        initial = not before or before[-1] in ".!?:;…•\n"
        if initial and w not in names:
            continue
        if lang == "en" and w == "I":
            continue
        add(w)
    return toks


def _present(tok: str, out: str) -> bool:
    if re.fullmatch(r"[\d.,:/-]+", tok):
        return tok in out
    if len(tok) <= 2:
        return re.search(rf"(?<![\wÀ-ÿ]){re.escape(tok)}(?![\wÀ-ÿ])", out) is not None
    if tok in out:
        return True
    # tolerate inflection: "Nochlins" ↔ "Nochlin", "Pablita's"
    stem = re.sub(r"(’s|'s|s)$", "", tok)
    return len(stem) >= 3 and stem in out


def _norm_sentence(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\wÀ-ÿ ]+", " ", s.lower())).strip()


def _sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", t or "") if s.strip()]


def _ngrams(words: list[str], n: int) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for i in range(len(words) - n + 1):
        g = tuple(words[i:i + n])
        out[g] = out.get(g, 0) + 1
    return out


_RATIO_BOUNDS = {"light": (0.7, 1.4), "medium": (0.6, 1.6), "heavy": (0.5, 1.8)}


def document_names(texts: list[str], lang: str = "") -> set[str]:
    """Capitalised words that occur mid-sentence somewhere in the document —
    treated as names everywhere (also where they start a sentence)."""
    names: set[str] = set()
    for t in texts:
        for tok in protected_tokens(t, lang):
            if tok[:1].isalpha() and tok[:1].isupper() and len(tok) > 1:
                names.add(tok)
    return names


def check_block(src: str, out: str, *, intensity: str = "medium", lang: str = "",
                ratio: tuple[float, float] | None = None, check_tokens: bool = True,
                names: set[str] | frozenset[str] = frozenset(),
                same_language: bool = True) -> list[str]:
    """Return a list of problems with a rewritten block ([] = fine).
    same_language=False (translation) compares repetition with the output
    itself instead of with the source."""
    issues: list[str] = []
    src = (src or "").strip()
    out = (out or "").strip()
    if not out:
        return ["empty output"]
    # repeated sentences
    src_counts: dict[str, int] = {}
    for s in _sentences(src):
        k = _norm_sentence(s)
        src_counts[k] = src_counts.get(k, 0) + 1
    out_counts: dict[str, int] = {}
    for s in _sentences(out):
        k = _norm_sentence(s)
        if len(k) >= 20:
            out_counts[k] = out_counts.get(k, 0) + 1
    ow = _norm_sentence(out).split()
    sw = _norm_sentence(src).split()
    sg = _ngrams(sw, 8)
    og = _ngrams(ow, 8)
    if same_language:
        if any(c > max(1, src_counts.get(k, 0)) for k, c in out_counts.items()):
            issues.append("repeated sentence")
        elif any(c > max(1, sg.get(g, 0)) for g, c in og.items()):
            issues.append("repeated phrase")
    else:
        src_dups = sum(c - 1 for c in src_counts.values() if c > 1)
        out_dups = sum(c - 1 for c in out_counts.values() if c > 1)
        if out_dups > src_dups:
            issues.append("repeated sentence")
        elif og and max(og.values()) >= 4 and max(og.values()) > max(sg.values() or [1]):
            issues.append("repeated phrase")
    lo, hi = ratio or _RATIO_BOUNDS.get(intensity, _RATIO_BOUNDS["medium"])
    if len(src) < 120:
        lo, hi = lo * 0.75, hi * 1.4
    r = len(out) / max(1, len(src))
    if r < lo or r > hi:
        issues.append(f"length {r:.2f}× the original")
    if check_tokens:
        o = _norm_dash(out)
        missing = [t for t in protected_tokens(src, lang, names) if not _present(t, o)]
        if intensity in ("light", "medium"):
            ol = o.lower()
            missing += [t for t in key_terms(src) if not _term_present(t, ol)]
        if missing:
            issues.append("missing: " + ", ".join(missing[:8]))
        norm_out = re.sub(r"\s+", " ", out)
        lost = [q for q in quoted_spans(src)
                if not re.search(r"[“„\"‘'«]\s*" + re.escape(re.sub(r"\s+", " ", q)) + r"\s*[”“\"’'»]", norm_out)]
        if lost:
            issues.append("quotation changed: " + "; ".join(f"“{q}”" for q in lost[:3]))
    return issues


# ── Model call ────────────────────────────────────────────────────────────────
ChatFn = Callable[[str, list[dict], dict], AsyncIterator[dict]]


async def ollama_chat(model: str, messages: list[dict], options: dict) -> AsyncIterator[dict]:
    """Stream one /api/chat call with thinking disabled. Yields {'content': …}
    deltas and a final {'done': True, 'done_reason': …, 'eval_count', 'eval_duration'}."""
    payload = {"model": model, "messages": messages, "stream": True, "think": False, "options": options}
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", f"{_idp.OLLAMA_BASE}/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")[:300]
                    raise RuntimeError(f"Model '{model}' failed (Ollama HTTP {resp.status_code}). {body}")
                async for line in resp.aiter_lines():
                    if not line or not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("error"):
                        raise RuntimeError(f"Model '{model}' failed: {str(obj.get('error'))[:300]}")
                    msg = obj.get("message") or {}
                    if msg.get("content"):
                        yield {"content": msg["content"]}
                    if obj.get("done"):
                        yield {"done": True, "done_reason": obj.get("done_reason") or "stop",
                               "eval_count": obj.get("eval_count") or 0,
                               "eval_duration": obj.get("eval_duration") or 0}
                        return
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach Ollama at {_idp.OLLAMA_BASE}: {exc}")


def est_tokens(text: str) -> int:
    return max(1, len(text or "") // 3)


def block_num_predict(text: str, factor: float = 2.5, floor: int = 256) -> int:
    return int(max(floor, factor * est_tokens(text)))


class _Stats:
    def __init__(self) -> None:
        self.eval_count = 0
        self.eval_ns = 0
        self.t0 = time.time()
        self.calls = 0
        self.continuations = 0

    def as_dict(self) -> dict:
        tps = self.eval_count / (self.eval_ns / 1e9) if self.eval_ns else 0.0
        return {"eval_count": self.eval_count, "tok_per_s": round(tps, 1),
                "total_duration_ms": int((time.time() - self.t0) * 1000),
                "calls": self.calls, "continuations": self.continuations}


async def generate(chat: ChatFn, model: str, messages: list[dict], *, temperature: float,
                   num_predict: int, stats: _Stats | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Run a chat call, continuing (assistant prefill) while done_reason ==
    'length'. Yields ('delta', str) … then ('final', (text, info))."""
    out = ""
    conts = 0
    reason = "stop"
    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    while True:
        msgs = messages if not out else messages + [{"role": "assistant", "content": out}]
        opts = {
            "temperature": temperature,
            "num_predict": int(num_predict),
            "num_ctx": _idp._num_ctx_for("x" * (prompt_chars + len(out)), num_predict),
            "num_thread": _idp._hw.recommended_num_thread(),
        }
        reason = "stop"
        if stats:
            stats.calls += 1
        async for ev in chat(model, msgs, opts):
            if ev.get("content"):
                out += ev["content"]
                yield ("delta", ev["content"])
            if ev.get("done"):
                reason = ev.get("done_reason") or "stop"
                if stats:
                    stats.eval_count += int(ev.get("eval_count") or 0)
                    stats.eval_ns += int(ev.get("eval_duration") or 0)
        if reason == "length" and conts < MAX_CONTINUATIONS:
            conts += 1
            if stats:
                stats.continuations += 1
            continue
        break
    yield ("final", (out, {"done_reason": reason, "continuations": conts}))


# ── Run cache (docx write-back without re-running the model) ─────────────────
_RUNS: dict[str, dict] = {}
_RUN_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")


def _runs_dir(doc_id: str) -> Path:
    return _idp.STORAGE_DIR / doc_id / "rewrite_runs"


def save_run(run: dict) -> None:
    _RUNS[run["run_id"]] = run
    while len(_RUNS) > 40:
        _RUNS.pop(next(iter(_RUNS)))
    try:
        d = _runs_dir(run["doc_id"])
        if not d.parent.exists():
            return
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{run['run_id']}.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-RUNS_KEPT_PER_DOC]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("could not persist rewrite run %s: %s", run.get("run_id"), exc)


def load_run(doc_id: str, run_id: str) -> dict | None:
    if not _RUN_ID_RE.match(run_id or "") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id or ""):
        return None
    run = _RUNS.get(run_id)
    if run is None:
        p = _runs_dir(doc_id) / f"{run_id}.json"
        if p.exists():
            try:
                run = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                run = None
    if run is None or run.get("doc_id") != doc_id:
        return None
    return run


def _block_from_dict(d: dict) -> Block:
    return Block(**{k: v for k, v in d.items() if k in Block.__dataclass_fields__})


# ── Humanize pipeline ─────────────────────────────────────────────────────────
def _contexts(blocks: list[Block], title: str) -> dict[int, dict]:
    """prev/next text, current section heading and table column header per block."""
    ctx: dict[int, dict] = {}
    section = ""
    headers: dict[tuple[int, int], str] = {}
    first_row: dict[int, int] = {}
    for b in blocks:
        if b.table is not None:
            first_row.setdefault(b.table, b.row or 0)
            if b.row == first_row[b.table]:
                headers[(b.table, b.col or 0)] = b.text
    texts = [b for b in blocks if b.kind != "empty"]
    pos = {b.idx: i for i, b in enumerate(texts)}
    for b in blocks:
        if b.kind == "heading":
            section = b.text
        i = pos.get(b.idx)
        prev = texts[i - 1].text if i is not None and i > 0 else ""
        nxt = texts[i + 1].text if i is not None and i + 1 < len(texts) else ""
        ctx[b.idx] = {
            "title": title, "section": section if section != b.text else "",
            "prev": prev, "next": nxt,
            "column": headers.get((b.table, b.col or 0)) if b.table is not None else None,
        }
    return ctx


def _temperature(intensity: str) -> float:
    return {"light": 0.4, "medium": 0.6, "heavy": 0.75}.get(intensity, 0.6)


async def stream_humanize(doc, model: str, options: dict | None = None, *,
                          chat: ChatFn | None = None,
                          blocks: list[Block] | None = None,
                          kind: str | None = None) -> AsyncIterator[dict]:
    options = options or {}
    tone = str(options.get("tone") or "natural")
    intensity = str(options.get("intensity") or "medium")
    chat = chat or ollama_chat
    if blocks is None:
        blocks, kind = segment_document(doc)
    kind = kind or "text"
    full = "\n".join(b.text for b in blocks)
    lang = detect_language(full)
    names = document_names([b.text for b in blocks], lang)
    title = Path(doc.filename or "").stem
    ctxs = _contexts(blocks, title)
    todo = [b for b in blocks if b.rewrite]
    total = len(todo)
    stats = _Stats()
    run_id = uuid.uuid4().hex[:16]
    outputs: dict[int, str] = {}
    flagged: list[dict] = []
    yield {"phase": "segmented", "of": total, "blocks": len(blocks), "language": lang,
           "model": model, "source": kind, "detail": f"{total} blocks to rewrite"}
    em = Emitter()
    k = 0
    for b in blocks:
        if not b.rewrite:
            piece = em.feed(b, b.text)
            if piece:
                yield {"content": piece}
            continue
        k += 1
        yield {"phase": "block", "i": k, "of": total, "detail": f"block {k}/{total}"}
        src = b.body.strip()
        final: str | None = None
        issues: list[str] = []
        for attempt in (0, 1):
            msgs = build_block_messages(b, ctxs.get(b.idx, {}), lang=lang, tone=tone,
                                        intensity=intensity, issues=issues if attempt else None)
            temp = _temperature(intensity) if attempt == 0 else 0.2
            raw = ""
            async for kind_, val in generate(chat, model, msgs, temperature=temp,
                                             num_predict=block_num_predict(src), stats=stats):
                if kind_ == "delta":
                    yield {"draft": val}
                else:
                    raw = val[0]
            cleaned = clean_output(raw, b)
            issues = check_block(src, cleaned, intensity=intensity, lang=lang, names=names)
            if not issues:
                final = cleaned
                break
            log.info("humanize block %d attempt %d issues: %s", b.idx, attempt + 1, issues)
            yield {"draft_reset": True}
        if final is None:
            final = src
            item = {"i": k, "idx": b.idx, "issues": issues, "preview": src[:120]}
            flagged.append(item)
            yield {"flag": item}
        text = f"{b.prefix}{b.sep or ' '}{final}" if b.prefix else final
        outputs[b.idx] = text
        yield {"draft_reset": True}
        piece = em.feed(b, text)
        if piece:
            yield {"content": piece}
    tail = em.flush()
    if tail:
        yield {"content": tail}
    run = {
        "run_id": run_id, "doc_id": doc.id, "op": "humanize", "model": model,
        "settings": {"tone": tone, "intensity": intensity}, "language": lang, "source": kind,
        "created_at": int(time.time() * 1000), "blocks": [asdict(b) for b in blocks],
        "outputs": {str(i): t for i, t in outputs.items()}, "flagged": flagged,
        "text": render_markdown(blocks, outputs),
    }
    save_run(run)
    yield {"done": True, "stats": stats.as_dict(), "run_id": run_id, "flagged": flagged,
           "blocks": total, "docx": kind == "docx", "language": lang}


# ── Translate / Redact (grouped markdown chunks) ──────────────────────────────
def _groups(blocks: list[Block], limit: int = GROUP_CHARS) -> list[tuple[str, str]]:
    """Render the document to markdown and cut it into (separator, chunk)
    groups at block / table-row boundaries."""
    pieces: list[str] = []
    em = Emitter()
    for b in blocks:
        p = em.feed(b, b.text)
        if p:
            pieces.append(p)
    tail = em.flush()
    if tail:
        pieces.append(tail)
    groups: list[tuple[str, str]] = []
    cur_sep, cur = "", ""
    for p in pieces:
        m = re.match(r"^(\n*)", p)
        sep = m.group(1) if m else ""
        body = p[len(sep):]
        if cur and len(cur) + len(p) > limit:
            groups.append((cur_sep, cur))
            cur_sep, cur = sep, body
        elif not cur:
            cur_sep, cur = sep, body
        else:
            cur += p
    if cur:
        groups.append((cur_sep, cur))
    return groups


def _translate_messages(chunk: str, target: str, strict: bool) -> list[dict]:
    sys = (
        f"You are a professional translator. Translate the user's markdown text into {target}. "
        "Translate everything except names, numbers, dates, codes and single-letter labels (keep those exactly). "
        "Keep the markdown structure exactly: the same headings (#), list markers, table rows and pipes (|), "
        "and blank lines. Use correct spelling, grammar and punctuation for the target language. "
        "Do not summarise, do not add or omit anything, never repeat sentences. "
        "Output ONLY the translation."
    )
    if strict:
        sys += " Translate line by line; the output must have the same number of lines as the input."
    return [{"role": "system", "content": sys}, {"role": "user", "content": chunk}]


def _redact_messages(chunk: str, cats: str, strict: bool) -> list[dict]:
    sys = (
        f"Redact the following from the user's text by replacing each occurrence with [REDACTED]: {cats}. "
        "Change nothing else — keep every other word, the markdown structure (headings, lists, table pipes) "
        "and line breaks exactly. Output ONLY the full text with redactions applied."
    )
    if strict:
        sys += " Do not rephrase anything."
    return [{"role": "system", "content": sys}, {"role": "user", "content": chunk}]


async def stream_grouped(doc, model: str, op: str, options: dict | None = None, *,
                         chat: ChatFn | None = None,
                         blocks: list[Block] | None = None) -> AsyncIterator[dict]:
    """Translate / redact the whole document chunk by chunk — any length,
    structure kept, sized num_predict + continuation, thinking off."""
    options = options or {}
    chat = chat or ollama_chat
    if blocks is None:
        blocks, _ = segment_document(doc)
    groups = _groups(blocks)
    total = len(groups)
    stats = _Stats()
    flagged: list[dict] = []
    if op == "translate":
        target = str(options.get("target") or "French")
        ratio = (0.4, 2.6)
    else:
        cats = options.get("categories") or []
        target = ", ".join(cats) if cats else "personal names, addresses, phone numbers, emails, IDs"
        ratio = (0.5, 1.6)
    yield {"phase": "segmented", "of": total, "blocks": len(blocks), "model": model,
           "detail": f"{total} part{'s' if total != 1 else ''}"}
    started = False
    out_parts: list[str] = []
    for k, (sep, chunk) in enumerate(groups, 1):
        yield {"phase": "block", "i": k, "of": total, "detail": f"part {k}/{total}"}
        final = None
        issues: list[str] = []
        factor = 3.0 if op == "translate" else 2.0
        for attempt in (0, 1):
            msgs = (_translate_messages(chunk, target, attempt == 1) if op == "translate"
                    else _redact_messages(chunk, target, attempt == 1))
            raw = ""
            async for kind_, val in generate(chat, model, msgs, temperature=0.2 if attempt else 0.3,
                                             num_predict=block_num_predict(chunk, factor, 512), stats=stats):
                if kind_ == "delta":
                    yield {"draft": val}
                else:
                    raw = val[0]
            cleaned = clean_output(raw)
            issues = check_block(chunk, cleaned, ratio=ratio, check_tokens=False,
                                 same_language=(op != "translate"))
            if not issues:
                final = cleaned
                break
            yield {"draft_reset": True}
        if final is None:
            final = chunk
            item = {"i": k, "issues": issues, "preview": chunk[:120]}
            flagged.append(item)
            yield {"flag": item}
        yield {"draft_reset": True}
        piece = (sep if started else "") + final
        started = True
        out_parts.append(piece)
        yield {"content": piece}
    yield {"done": True, "stats": stats.as_dict(), "flagged": flagged, "blocks": total}


async def collect(gen: AsyncIterator[dict]) -> dict:
    """Drain a pipeline into {'text', 'run_id', 'flagged', 'stats', …}."""
    text = ""
    done: dict = {}
    async for ev in gen:
        if "content" in ev:
            text += ev["content"]
        elif ev.get("done"):
            done = ev
    return {"text": text, **{k: v for k, v in done.items() if k != "done"}}


# ── .docx write-back ──────────────────────────────────────────────────────────
_TEXT_CHILDREN = ("t", "tab", "br", "cr", "noBreakHyphen", "softHyphen")


def _run_text(r) -> str:
    parts = []
    for ch in r.iterchildren():
        tag = ch.tag
        if tag == _w("t"):
            parts.append(ch.text or "")
        elif tag == _w("tab"):
            parts.append("\t")
        elif tag == _w("br") and ch.get(_w("type")) not in ("page", "column"):
            parts.append("\n")
        elif tag == _w("cr"):
            parts.append("\n")
    return "".join(parts)


def _clear_run_text(r) -> None:
    for ch in list(r.iterchildren()):
        if ch.tag in tuple(_w(t) for t in _TEXT_CHILDREN):
            if ch.tag == _w("br") and ch.get(_w("type")) in ("page", "column"):
                continue
            r.remove(ch)


def _append_run_text(r, text: str) -> None:
    from docx.oxml import OxmlElement  # type: ignore[import-not-found]

    for i, line in enumerate(text.split("\n")):
        if i:
            r.append(OxmlElement("w:br"))
        for j, seg in enumerate(line.split("\t")):
            if j:
                r.append(OxmlElement("w:tab"))
            if seg:
                t = OxmlElement("w:t")
                t.text = seg
                t.set(f"{{{XML_NS}}}space", "preserve")
                r.append(t)


def _has_text(r) -> bool:
    return bool(_run_text(r))


def set_paragraph_text(p_el, new_text: str, keep_chars: int = 0) -> None:
    """Replace a paragraph's text, keeping its paragraph properties (style),
    images and the runs covering the first `keep_chars` characters (a Q&A
    question in bold stays bold). The new text goes into the first text run
    after the kept part, with the formatting of the dominant (longest) run;
    the other text runs are emptied."""
    runs = [r for r in p_el.iterchildren(_w("r")) if _has_text(r)]
    if not runs:
        from docx.oxml import OxmlElement  # type: ignore[import-not-found]
        r = OxmlElement("w:r")
        p_el.append(r)
        _append_run_text(r, new_text)
        return
    full = "".join(_run_text(r) for r in runs)
    lead_ws = len(full) - len(full.lstrip())
    keep = lead_ws + keep_chars if keep_chars else 0
    pos = 0
    after: list = []
    for r in runs:
        t = _run_text(r)
        start, end = pos, pos + len(t)
        pos = end
        if keep and end <= keep:
            continue
        if keep and start < keep < end:
            clone = copy.deepcopy(r)
            _clear_run_text(r)
            _append_run_text(r, t[: keep - start])
            # the clone carries the rest; drop any non-text content (images) from it
            for ch in list(clone.iterchildren()):
                if ch.tag != _w("rPr"):
                    clone.remove(ch)
            _append_run_text(clone, t[keep - start:])
            r.addnext(clone)
            after.append(clone)
            continue
        after.append(r)
    if not after:
        # everything was "kept" (should not happen) — append to the last run
        _append_run_text(runs[-1], new_text)
        return
    target = after[0]
    dominant = max(after, key=lambda r: len(_run_text(r)))
    if dominant is not target:
        rpr_t = target.find(_w("rPr"))
        rpr_d = dominant.find(_w("rPr"))
        if rpr_t is not None:
            target.remove(rpr_t)
        if rpr_d is not None:
            target.insert(0, copy.deepcopy(rpr_d))
    for r in after:
        _clear_run_text(r)
    _append_run_text(target, new_text)
    # drop now-empty runs that carry nothing but formatting
    for r in after[1:]:
        if all(ch.tag == _w("rPr") for ch in r.iterchildren()):
            p_el.remove(r)


def apply_docx(src_path: Path | str, blocks: list[Block], outputs: dict[int, str]) -> bytes:
    """Write rewritten blocks back into a copy of the original .docx."""
    from docx import Document as DocxDocument  # type: ignore[import-not-found]

    document = DocxDocument(str(src_path))
    walk = _walk_docx(document)
    applied = 0
    for b in blocks:
        new = outputs.get(b.idx)
        if new is None or not b.rewrite or "n" not in (b.loc or {}):
            continue
        n = int(b.loc["n"])
        if n >= len(walk):
            continue
        p_el = walk[n]["el"]
        if _p_text(p_el).strip() != b.text.strip():
            log.warning("docx write-back: paragraph %d changed since segmentation — skipped", n)
            continue
        if new.strip() == b.text.strip():
            continue
        if b.prefix:
            body = new[len(b.prefix):] if new.startswith(b.prefix) else new
            body = body.lstrip()
            set_paragraph_text(p_el, (b.sep or " ") + body, keep_chars=len(b.prefix))
        else:
            set_paragraph_text(p_el, new)
        applied += 1
    buf = io.BytesIO()
    document.save(buf)
    log.info("docx write-back: %d paragraph(s) replaced", applied)
    return buf.getvalue()


def docx_for_run(doc, run: dict) -> bytes:
    src = raw_file(doc)
    if src is None or not (doc.filename or "").lower().endswith(".docx"):
        raise ValueError("source is not a .docx document")
    blocks = [_block_from_dict(d) for d in run.get("blocks") or []]
    outputs = {int(k): v for k, v in (run.get("outputs") or {}).items()}
    return apply_docx(src, blocks, outputs)
