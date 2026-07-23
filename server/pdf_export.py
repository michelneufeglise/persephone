"""
Markdown → styled PDF exporter using ReportLab.

Used by two endpoints:
  * POST /api/chat/message/{msg_id}/pdf   — export a single assistant reply
  * GET  /api/research/runs/{id}/pdf      — export a research report

The output is intentionally editorial-looking: serif body, sans headings,
proper hierarchy (h1..h4), fenced code as monospace panels, blockquotes
indented + italicised, ordered/unordered lists indented. Enough for a
readable print-ready doc without pulling in a headless browser.

Kept dependency-light — only `reportlab` (already in requirements). No
markdown lib needed: we parse a curated subset ourselves so we control
exactly how each element renders.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Preformatted,
    Table, TableStyle, KeepTogether, ListFlowable, ListItem, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ── Style palette ───────────────────────────────────────────────────────────
def _build_styles() -> dict[str, ParagraphStyle]:
    styles = getSampleStyleSheet()
    palette = {
        "title": ParagraphStyle(
            "title", parent=styles["Title"],
            fontName="Helvetica-Bold", fontSize=22, leading=26,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=styles["Normal"],
            fontName="Helvetica", fontSize=10, leading=12,
            textColor=colors.HexColor("#7a5a5a"),
            spaceAfter=18, italic=True,
        ),
        "h1": ParagraphStyle(
            "h1", parent=styles["Heading1"],
            fontName="Helvetica-Bold", fontSize=18, leading=22,
            textColor=colors.HexColor("#3a1414"),
            spaceBefore=16, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2", parent=styles["Heading2"],
            fontName="Helvetica-Bold", fontSize=14, leading=18,
            textColor=colors.HexColor("#4a2020"),
            spaceBefore=14, spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3", parent=styles["Heading3"],
            fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=colors.HexColor("#5a2828"),
            spaceBefore=10, spaceAfter=3,
        ),
        "h4": ParagraphStyle(
            "h4", parent=styles["Heading4"],
            fontName="Helvetica-Oblique", fontSize=11, leading=14,
            textColor=colors.HexColor("#6a3030"),
            spaceBefore=8, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=styles["BodyText"],
            fontName="Times-Roman", fontSize=10.5, leading=15,
            textColor=colors.HexColor("#1a1a1a"),
            alignment=TA_JUSTIFY, spaceAfter=8,
        ),
        "code": ParagraphStyle(
            "code", parent=styles["Code"],
            fontName="Courier", fontSize=9, leading=12,
            backColor=colors.HexColor("#f5f3f0"),
            borderPadding=6, leftIndent=8, rightIndent=8,
            spaceBefore=6, spaceAfter=10,
            textColor=colors.HexColor("#3a1414"),
        ),
        "quote": ParagraphStyle(
            "quote", parent=styles["BodyText"],
            fontName="Times-Italic", fontSize=10.5, leading=15,
            leftIndent=18, rightIndent=6,
            borderColor=colors.HexColor("#8b2252"),
            borderPadding=(2, 0, 2, 8),
            spaceBefore=6, spaceAfter=10,
            textColor=colors.HexColor("#3a1414"),
        ),
        "list_item": ParagraphStyle(
            "list_item", parent=styles["BodyText"],
            fontName="Times-Roman", fontSize=10.5, leading=15,
            alignment=TA_LEFT, spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "meta", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8, leading=10,
            textColor=colors.HexColor("#8a8a8a"),
            spaceAfter=4,
        ),
    }
    return palette


# ── Inline formatting (bold / italic / code / links) ────────────────────────
# We hand-roll the inline pass because reportlab's Paragraph engine speaks
# HTML-like tags. Our job is to translate a small subset of markdown to
# those tags safely.

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE        = re.compile(r"\*\*([^\*]+)\*\*")
_ITALIC_RE      = re.compile(r"(?<![\*_])\*([^\*\n]+)\*(?!\*)")
_LINK_RE        = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _escape_xml(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def _render_inline(text: str) -> str:
    # Escape first so inline tags we add later aren't double-escaped.
    text = _escape_xml(text)
    # Links → underline + colour.
    text = _LINK_RE.sub(
        lambda m: f'<link href="{_escape_xml(m.group(2))}" color="#8b2252">'
                  f'<u>{m.group(1)}</u></link>',
        text,
    )
    # Inline code → monospace + subtle background (approximated with colour).
    text = _INLINE_CODE_RE.sub(
        lambda m: f'<font face="Courier" color="#3a1414">{m.group(1)}</font>',
        text,
    )
    # Bold.
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    # Italic.
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return text


# ── Block parser ────────────────────────────────────────────────────────────
def _parse_blocks(md: str) -> list[dict[str, Any]]:
    """
    Convert markdown text into a list of block dicts:
      {"type": "heading", "level": N, "text": str}
      {"type": "paragraph",           "text": str}
      {"type": "code",     "lang":str, "text": str}
      {"type": "quote",               "text": str}
      {"type": "list", "ordered": bool, "items": list[str]}
      {"type": "hr"}
    """
    blocks: list[dict[str, Any]] = []
    lines = (md or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1   # closing fence
            blocks.append({"type": "code", "lang": lang, "text": "\n".join(body)})
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", stripped):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            level = len(m.group(1))
            blocks.append({"type": "heading", "level": min(4, level), "text": m.group(2).strip()})
            i += 1
            continue

        # Blockquote (collapse consecutive > lines)
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            blocks.append({"type": "quote", "text": " ".join(quote_lines).strip()})
            continue

        # List (unordered or ordered)
        if re.match(r"^[\-\*\+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            ordered = bool(re.match(r"^\d+\.\s+", stripped))
            items: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if not s:
                    break
                m2 = re.match(r"^(?:[\-\*\+]\s+|\d+\.\s+)(.+)$", s)
                if not m2:
                    break
                items.append(m2.group(1))
                i += 1
            blocks.append({"type": "list", "ordered": ordered, "items": items})
            continue

        # Empty line → paragraph break
        if not stripped:
            i += 1
            continue

        # Regular paragraph — collect contiguous non-empty non-block lines
        para: list[str] = []
        while i < len(lines):
            s = lines[i]
            if not s.strip():
                break
            # Break if the next line is the start of a different block.
            if re.match(r"^#{1,6}\s", s.strip()) or s.strip().startswith(">") \
               or re.match(r"^[\-\*\+]\s+", s.strip()) \
               or re.match(r"^\d+\.\s+", s.strip()) \
               or s.strip().startswith("```"):
                break
            para.append(s.strip())
            i += 1
        if para:
            blocks.append({"type": "paragraph", "text": " ".join(para)})

    return blocks


# ── Assemble the flowables ──────────────────────────────────────────────────
def _blocks_to_flowables(blocks: list[dict[str, Any]], styles: dict[str, ParagraphStyle]) -> list:
    story: list = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            key = f"h{b['level']}"
            story.append(Paragraph(_render_inline(b["text"]), styles.get(key, styles["h4"])))
        elif t == "paragraph":
            story.append(Paragraph(_render_inline(b["text"]), styles["body"]))
        elif t == "code":
            # Preformatted keeps whitespace + line breaks verbatim.
            story.append(Preformatted(b["text"], styles["code"]))
        elif t == "quote":
            story.append(Paragraph(_render_inline(b["text"]), styles["quote"]))
        elif t == "list":
            list_items = [
                ListItem(
                    Paragraph(_render_inline(item), styles["list_item"]),
                    leftIndent=14,
                )
                for item in b["items"]
            ]
            story.append(ListFlowable(
                list_items,
                bulletType="1" if b["ordered"] else "bullet",
                start="1" if b["ordered"] else "•",
                leftIndent=18,
            ))
            story.append(Spacer(1, 6))
        elif t == "hr":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=colors.HexColor("#e0d0d0")))
            story.append(Spacer(1, 6))
    return story


# ── Public API ──────────────────────────────────────────────────────────────
def markdown_to_pdf(
    body_md:  str,
    *,
    title:    str = "",
    subtitle: str = "",
    footer:   str = "Persephone",
) -> bytes:
    """
    Render `body_md` (Markdown) as a styled A4 PDF. Returns the raw bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=22 * mm,  bottomMargin=20 * mm,
        title=title or "Persephone export",
        author="Persephone",
    )
    styles = _build_styles()
    story: list = []
    if title:
        story.append(Paragraph(_escape_xml(title), styles["title"]))
    if subtitle:
        story.append(Paragraph(_escape_xml(subtitle), styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=0.6,
                             color=colors.HexColor("#8b2252")))
    story.append(Spacer(1, 10))

    story.extend(_blocks_to_flowables(_parse_blocks(body_md), styles))

    # Footer on every page — small caps, gentle. Uses onPage hook.
    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#8a8a8a"))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        canvas.drawString(22 * mm, 12 * mm, f"{footer}  ·  {stamp}")
        canvas.drawRightString(A4[0] - 22 * mm, 12 * mm, f"page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
