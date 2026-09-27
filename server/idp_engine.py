"""
Intelligent Document Processing engine for Persephone.

Supports: PDF, DOCX, XLSX/XLSM/XLS/ODS/CSV/TSV (see sheets.py), TXT, MD, PNG/JPEG.
Pipeline: upload → extract text + images → store → on-demand operations
  (OCR, Q&A, summarize, classify, translate, extract entities, tables, export).
Operations that need vision call the user-configured vision/OCR model in Ollama.
"""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

import hardware as _hw

log = logging.getLogger("idp_engine")

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Model names whose prefix indicates vision/image-capable inference
VISION_PREFIXES = (
    "qwen2.5vl", "qwen2-vl", "qwen-vl", "qwen3vl",
    "minicpm-v", "minicpm-o", "openbmb/minicpm",
    "llama3.2-vision", "llama4-vision",
    "llava",
    "gemma3",
    "granite3-vision", "granite3.2-vision",
    "moondream", "bakllava", "cogvlm", "internvl",
)


def _name_is_vision(model: str) -> bool:
    lower = model.lower()
    # match either start-of-string or after a slash (e.g. "openbmb/minicpm-v:8b")
    base = lower.split(":", 1)[0]
    return any(base.startswith(p) or f"/{p}" in base for p in VISION_PREFIXES)


async def _list_installed_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{OLLAMA_BASE}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def _ollama_has_model(model: str) -> bool:
    installed = await _list_installed_models()
    if model in installed:
        return True
    # Allow tag-less matches (user picked "qwen2.5vl:7b", Ollama has "qwen2.5vl:7b-q4")
    base = model.split(":", 1)[0]
    return any(m.split(":", 1)[0] == base for m in installed)


_SIZE_RE = re.compile(r"(\d+\.?\d*)b\b", re.IGNORECASE)


def _estimate_params_b(name: str) -> float:
    """Pull the parameter-count (B) out of a model tag, default to 999B if unknown."""
    m = _SIZE_RE.search(name)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 999.0


async def find_installed_vision_model() -> str:
    """
    Return the smallest installed vision-capable model.
    Smaller wins so the fallback fits in memory alongside the user's chat model.
    """
    visions = [m for m in await _list_installed_models() if _name_is_vision(m)]
    if not visions:
        return ""
    visions.sort(key=_estimate_params_b)
    return visions[0]

# Storage root — writable per Electron app data dir in production builds.
from paths import uploads_dir
STORAGE_DIR = uploads_dir()


# ── Document model & registry ──────────────────────────────────────────────────
@dataclass
class Document:
    id:         str
    filename:   str
    mime:       str
    size:       int
    uploaded_at: float
    pages:      int = 0
    text:       str = ""
    page_texts: list[str] = field(default_factory=list)
    page_images: list[str] = field(default_factory=list)   # absolute paths to per-page PNGs
    meta:       dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_text: bool = False) -> dict:
        d = {
            "id":         self.id,
            "filename":   self.filename,
            "mime":       self.mime,
            "size":       self.size,
            "uploaded_at": int(self.uploaded_at * 1000),
            "pages":      self.pages,
            "preview":    self.text[:240] if self.text else "",
            "has_images": bool(self.page_images),
            "meta":       self.meta,
        }
        if include_text:
            d["text"]       = self.text
            d["page_texts"] = self.page_texts
        return d


# In-memory document registry, persisted to disk as JSON for crash safety
REGISTRY: dict[str, Document] = {}
REGISTRY_FILE = STORAGE_DIR / "_registry.json"

# Serialises registry writes (they may run on worker threads via asyncio.to_thread).
_REGISTRY_LOCK = threading.Lock()
# Monotonic snapshot counter: a slower writer never clobbers a newer snapshot.
_REGISTRY_SEQ = itertools.count(1)
_REGISTRY_LAST_WRITTEN = 0
# True when the on-disk main file is known to be a good registry (loaded OK or
# written by us). Only then is it rotated into the .bak slot.
_REGISTRY_MAIN_GOOD = False


def _registry_bak_file() -> Path:
    return REGISTRY_FILE.with_name(REGISTRY_FILE.name + ".bak")


def _registry_snapshot() -> tuple[int, dict]:
    """Copy the registry into plain containers (cheap: strings are shared).

    Taken on the caller's thread so a background writer never iterates the
    live REGISTRY while the event loop mutates it.
    """
    seq = next(_REGISTRY_SEQ)
    snap: dict[str, dict] = {}
    for did, doc in list(REGISTRY.items()):
        snap[did] = {
            "id":          doc.id,
            "filename":    doc.filename,
            "mime":        doc.mime,
            "size":        doc.size,
            "uploaded_at": doc.uploaded_at,
            "pages":       doc.pages,
            "text":        doc.text,
            "page_texts":  list(doc.page_texts),
            "page_images": list(doc.page_images),
            "meta":        dict(doc.meta),
        }
    return seq, snap


def _write_registry(seq: int, snap: dict) -> None:
    """Atomically persist a registry snapshot.

    temp file in the same dir → fsync → rotate the previous good file to .bak
    → os.replace(temp, main). A crash at any point leaves either the old main
    file, or the .bak (previous good) plus the complete temp file.
    """
    global _REGISTRY_LAST_WRITTEN, _REGISTRY_MAIN_GOOD
    with _REGISTRY_LOCK:
        if seq < _REGISTRY_LAST_WRITTEN:
            return  # a newer snapshot is already on disk
        payload = json.dumps(snap)
        target = REGISTRY_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".registry-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            if _REGISTRY_MAIN_GOOD and target.exists():
                try:
                    os.replace(target, _registry_bak_file())
                except OSError as exc:
                    log.warning("Could not rotate registry backup: %s", exc)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        _REGISTRY_LAST_WRITTEN = seq
        _REGISTRY_MAIN_GOOD = True


def _save_registry() -> None:
    """Persist the registry synchronously (atomic). From async code on the
    event loop prefer ``await save_registry_async()``."""
    seq, snap = _registry_snapshot()
    _write_registry(seq, snap)


async def save_registry_async() -> None:
    """Snapshot on the loop, then serialise + write on a worker thread."""
    seq, snap = _registry_snapshot()
    await asyncio.to_thread(_write_registry, seq, snap)


def _doc_from_dict(d: dict) -> Document:
    meta = d.get("meta", {})
    if isinstance(meta, dict) and "sheets" in meta:
        # tolerate older uploads: legacy dict-style meta["sheets"], hidden
        # sheets with columns/samples → current list form (in memory; the
        # next registry save persists it)
        try:
            import sheets as _sheets
            _sheets.normalize_meta(meta)
        except Exception as exc:  # pragma: no cover - never block loading
            log.debug("sheet meta normalise failed for %s: %s", d.get("id"), exc)
    return Document(
        id=d["id"], filename=d["filename"], mime=d["mime"], size=d["size"],
        uploaded_at=d["uploaded_at"] if isinstance(d["uploaded_at"], float) else d["uploaded_at"] / 1000,
        pages=d.get("pages", 0),
        text=d.get("text", ""),
        page_texts=d.get("page_texts", []),
        page_images=d.get("page_images", []),
        meta=meta,
    )


def _read_registry_file(path: Path) -> dict[str, Document]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("registry root is not an object")
    return {did: _doc_from_dict(d) for did, d in data.items()}


def _load_registry() -> None:
    """Load the registry; fall back to the .bak copy if the main file is
    missing or corrupt."""
    global _REGISTRY_MAIN_GOOD
    main, bak = REGISTRY_FILE, _registry_bak_file()
    _REGISTRY_MAIN_GOOD = False
    if main.exists():
        try:
            docs = _read_registry_file(main)
            REGISTRY.update(docs)
            _REGISTRY_MAIN_GOOD = True
            return
        except Exception as exc:
            log.error("Registry %s is corrupt (%s) — trying backup %s", main, exc, bak)
    if bak.exists():
        try:
            docs = _read_registry_file(bak)
            REGISTRY.update(docs)
            log.warning("Recovered %d document(s) from registry backup %s", len(docs), bak)
        except Exception as exc:
            log.error("Registry backup %s is also unreadable: %s", bak, exc)


_load_registry()


def list_documents() -> list[dict]:
    return sorted(
        [d.to_dict() for d in REGISTRY.values()],
        key=lambda x: x["uploaded_at"], reverse=True,
    )


def get_document(doc_id: str) -> Document | None:
    return REGISTRY.get(doc_id)


def delete_document(doc_id: str) -> bool:
    doc = REGISTRY.pop(doc_id, None)
    if not doc:
        return False
    doc_dir = STORAGE_DIR / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)
    _save_registry()
    return True


async def delete_document_async(doc_id: str) -> bool:
    """Event-loop friendly delete: registry pop on the loop, disk I/O on a thread."""
    doc = REGISTRY.pop(doc_id, None)
    if not doc:
        return False
    doc_dir = STORAGE_DIR / doc_id
    await asyncio.to_thread(shutil.rmtree, doc_dir, True)
    await save_registry_async()
    return True


# ── File extraction ────────────────────────────────────────────────────────────
SHEET_MIMES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "xls": "application/vnd.ms-excel",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
}


def _detect_mime(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in SHEET_MIMES:
        return SHEET_MIMES[ext]
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        mime_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
            "xls": "application/vnd.ms-excel",
            "ods": "application/vnd.oasis.opendocument.spreadsheet",
            "csv": "text/csv",
            "md": "text/markdown",
            "txt": "text/plain",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "doc": "application/msword",
            "rtf": "application/rtf",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "odt": "application/vnd.oasis.opendocument.text",
            "html": "text/html",
            "htm": "text/html",
            "json": "application/json",
            "xml": "application/xml",
            "yaml": "text/yaml",
            "yml": "text/yaml",
            "log": "text/plain",
            "tsv": "text/tab-separated-values",
        }
        mime = mime_map.get(ext, "application/octet-stream")
    return mime


def _extract_pdf(path: Path, doc_dir: Path) -> tuple[list[str], list[str]]:
    """Extract per-page text and per-page PNG renders from a PDF."""
    import fitz   # type: ignore[import-not-found]

    page_texts: list[str] = []
    page_images: list[str] = []
    pdf = fitz.open(path)
    for i, page in enumerate(pdf):
        page_texts.append(page.get_text("text"))
        png_path = doc_dir / f"page_{i + 1:04d}.png"
        pix = page.get_pixmap(dpi=120)
        pix.save(png_path)
        page_images.append(str(png_path))
    pdf.close()
    return page_texts, page_images


def _extract_docx(path: Path) -> list[str]:
    """Extract text from DOCX, including text boxes, headers, and footers.

    Walks the XML body to collect all w:p paragraphs in document order, including
    those nested in text boxes (w:txbxContent). Skips duplicate paragraphs from
    the VML Fallback alternative representation. Includes header/footer text.
    """
    from docx import Document as DocxDocument  # type: ignore[import-not-found]

    try:
        doc = DocxDocument(path)
    except Exception as exc:
        log.warning("Failed to open DOCX: %s", exc)
        return [""]

    lines = []
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    mc_ns = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

    def _is_in_fallback(elem) -> bool:
        """Check if element is inside an mc:Fallback."""
        try:
            for ancestor in elem.iterancestors():
                if ancestor.tag == f"{mc_ns}Fallback":
                    return True
        except (AttributeError, TypeError):
            pass
        return False

    def _get_para_text(p_elem) -> str:
        """Extract all text from a w:p element."""
        text_parts = []
        for t_elem in p_elem.iter(f"{w_ns}t"):
            if t_elem.text:
                text_parts.append(t_elem.text)
        return "".join(text_parts).strip()

    # Collect all w:p elements from body (includes those in text boxes)
    seen_paras = set()  # Use id() to track uniqueness
    for p_elem in doc.element.body.iter(f"{w_ns}p"):
        # Skip if in Fallback (VML duplicate)
        if _is_in_fallback(p_elem):
            continue

        # Avoid duplicates
        p_id = id(p_elem)
        if p_id in seen_paras:
            continue
        seen_paras.add(p_id)

        # Extract text
        para_text = _get_para_text(p_elem)
        if para_text:
            lines.append(para_text)

    # Extract header/footer text (deduped)
    seen_header_footer = set()
    try:
        for section in doc.sections:
            # Headers
            try:
                if section.header._element is not None:
                    for p_elem in section.header._element.iter(f"{w_ns}p"):
                        if not _is_in_fallback(p_elem):
                            para_text = _get_para_text(p_elem)
                            if para_text and para_text not in seen_header_footer:
                                lines.insert(0, para_text)
                                seen_header_footer.add(para_text)
            except Exception:
                pass

            # Footers
            try:
                if section.footer._element is not None:
                    for p_elem in section.footer._element.iter(f"{w_ns}p"):
                        if not _is_in_fallback(p_elem):
                            para_text = _get_para_text(p_elem)
                            if para_text and para_text not in seen_header_footer:
                                lines.append(para_text)
                                seen_header_footer.add(para_text)
            except Exception:
                pass
    except Exception as exc:
        log.warning("Failed to extract headers/footers: %s", exc)

    return ["\n".join(lines)] if lines else [""]


def _extract_xlsx(path: Path, doc_dir: Path | None = None) -> tuple[list[str], dict]:
    """Spreadsheet (.xlsx/.xlsm/.xls/.ods/.csv/.tsv) → one markdown page per
    visible sheet + structured meta (see sheets.py). With `doc_dir` the parsed
    frames are cached next to the raw file for table queries."""
    import sheets as _sheets
    cache = (Path(doc_dir) / _sheets.CACHE_NAME) if doc_dir is not None else None
    return _sheets.extract(path, cache)


def _extract_csv(path: Path) -> list[str]:
    try:
        pages, _meta = _extract_xlsx(path)
        return pages
    except Exception:
        return [path.read_text(errors="ignore")]


def _extract_pptx(path: Path) -> list[str]:
    """Pull slide text from a .pptx (OOXML zip) without python-pptx."""
    import zipfile, re as _re
    slides: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(
                (n for n in z.namelist() if _re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(_re.search(r"(\d+)", n).group(1)),
            )
            for n in names:
                xml = z.read(n).decode("utf-8", errors="ignore")
                texts = _re.findall(r"<a:t>(.*?)</a:t>", xml, _re.DOTALL)
                body = "\n".join(t.strip() for t in texts if t.strip())
                slides.append(body)
    except Exception as exc:
        return [f"[pptx extract error: {exc}]"]
    return slides or [""]


def _strip_xml_tags(xml: str) -> str:
    import re as _re
    # keep paragraph/line breaks then drop all tags
    xml = _re.sub(r"</(w:p|text:p|p)>", "\n", xml)
    xml = _re.sub(r"<[^>]+>", "", xml)
    import html as _html
    return _html.unescape(xml)


def _extract_odt(path: Path) -> list[str]:
    """Extract text from an .odt (ODF zip) content.xml without odfpy."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("content.xml").decode("utf-8", errors="ignore")
        text = _strip_xml_tags(xml)
        lines = [ln.strip() for ln in text.splitlines()]
        return ["\n".join(ln for ln in lines if ln)]
    except Exception as exc:
        return [f"[odt extract error: {exc}]"]


def _extract_html(path: Path) -> list[str]:
    """Strip tags/script/style from HTML using stdlib html.parser."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"): self._skip = True
            if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
                self.parts.append("\n")
        def handle_endtag(self, tag):
            if tag in ("script", "style"): self._skip = False
        def handle_data(self, data):
            if not self._skip and data.strip():
                self.parts.append(data)

    try:
        raw = path.read_text(errors="ignore")
        p = _Stripper(); p.feed(raw)
        text = "".join(p.parts)
        lines = [ln.strip() for ln in text.splitlines()]
        return ["\n".join(ln for ln in lines if ln)]
    except Exception as exc:
        return [f"[html extract error: {exc}]"]


def _strip_rtf(text: str) -> str:
    """Minimal RTF de-control-word stripper (stdlib only)."""
    import re as _re
    # unicode escapes \uNNNN?  -> char
    def _u(m):
        try: return chr(int(m.group(1)))
        except Exception: return ""
    text = _re.sub(r"\\u(-?\d+)\??", _u, text)
    text = _re.sub(r"\\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = _re.sub(r"\\par[d]?\b", "\n", text)
    text = _re.sub(r"\\line\b", "\n", text)
    text = _re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)  # other control words
    text = text.replace("{", "").replace("}", "")
    return text


def _extract_rtf(path: Path) -> list[str]:
    try:
        raw = path.read_text(errors="ignore")
        out = _strip_rtf(raw)
        lines = [ln.strip() for ln in out.splitlines()]
        return ["\n".join(ln for ln in lines if ln)]
    except Exception as exc:
        return [f"[rtf extract error: {exc}]"]


def _extract_doc(path: Path) -> list[str]:
    """Best-effort legacy .doc extraction. Tries antiword/soffice CLI if
    present, otherwise salvages readable text runs from the binary. Result
    may be imperfect for legacy binary Word files."""
    import shutil as _sh, subprocess as _sp, re as _re
    for tool, args in (("antiword", [str(path)]),):
        exe = _sh.which(tool)
        if exe:
            try:
                out = _sp.run([exe, *args], capture_output=True, timeout=30)
                txt = out.stdout.decode("utf-8", errors="ignore").strip()
                if txt:
                    return [txt]
            except Exception:
                pass
    # Fallback: pull printable ASCII/UTF-8 runs of length >= 4
    try:
        data = path.read_bytes()
        runs = _re.findall(rb"[\x20-\x7e\r\n\t]{4,}", data)
        text = "\n".join(r.decode("latin-1", errors="ignore") for r in runs)
        text = _re.sub(r"\n{3,}", "\n\n", text).strip()
        note = "[note: legacy .doc — text salvaged best-effort; formatting lost]\n\n"
        return [note + text] if text else ["[doc extract: no readable text found]"]
    except Exception as exc:
        return [f"[doc extract error: {exc}]"]


def _extract_image(path: Path, doc_dir: Path) -> list[str]:
    """Single-page 'document' for raw images. OCR happens on demand."""
    out = doc_dir / "page_0001.png"
    shutil.copy(path, out)
    return [str(out)]


def _extract_email(path: Path) -> str:
    """Extract text from an .eml file using stdlib email.message."""
    from email.message import EmailMessage
    from email.parser import BytesParser
    from email.policy import default

    try:
        data = path.read_bytes()
        msg = BytesParser(policy=default).parsebytes(data)

        # Build header lines
        headers = []
        for key in ("From", "To", "Subject", "Date", "Cc"):
            val = msg.get(key, "").strip()
            if val:
                headers.append(f"{key}: {val}")

        # Extract body: prefer plain text, fall back to HTML
        body = ""
        if msg.is_multipart():
            for part in msg.iter_parts():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
                    except Exception:
                        pass
                elif ctype == "text/html" and not body:
                    try:
                        html_body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        # Quick HTML strip: reuse existing _extract_html pattern
                        from html.parser import HTMLParser

                        class _Stripper(HTMLParser):
                            def __init__(self):
                                super().__init__()
                                self.parts: list[str] = []
                                self._skip = False
                            def handle_starttag(self, tag, attrs):
                                if tag in ("script", "style"): self._skip = True
                                if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
                                    self.parts.append("\n")
                            def handle_endtag(self, tag):
                                if tag in ("script", "style"): self._skip = False
                            def handle_data(self, data):
                                if not self._skip and data.strip():
                                    self.parts.append(data)

                        p = _Stripper()
                        p.feed(html_body)
                        body = "".join(p.parts)
                        break
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                body = msg.get_payload()

        # List attachments
        attachments = []
        if msg.is_multipart():
            for part in msg.iter_parts():
                filename = part.get_filename()
                if filename:
                    attachments.append(filename)

        # Combine
        result = "\n".join(headers)
        if attachments:
            result += f"\n\nAttachments: {', '.join(attachments)}"
        if body:
            result += f"\n\n{body}"

        return result.strip()
    except Exception as exc:
        log.exception("Email extract failed: %s", exc)
        return f"[email extract error: {exc}]"


SHEET_EXTS = (".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv")
REEXTRACTABLE_EXTS = (".docx", ".pptx", ".odt", ".rtf", ".doc", ".html", ".htm", ".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv", ".txt", ".md", ".eml")
_REEXTRACT_TRIED: set[str] = set()


def is_sheet_file(filename: str, mime: str = "") -> bool:
    """Spreadsheet by extension, or by a spreadsheet MIME type."""
    if (filename or "").lower().endswith(SHEET_EXTS):
        return True
    m = (mime or "").lower()
    return m in SHEET_MIMES.values() or m.endswith("spreadsheetml.sheet")


def _sheet_path(raw_path: Path, filename: str, mime: str) -> Path:
    """sheets.py dispatches on the suffix; a spreadsheet MIME with an
    unexpected name maps onto the matching suffix (via a sibling link/copy)."""
    if raw_path.suffix.lower() in SHEET_EXTS:
        return raw_path
    m = (mime or "").lower()
    ext = next((e for e, v in SHEET_MIMES.items() if v == m), "xlsx")
    alias = raw_path.with_name(raw_path.name + "." + ext)
    if not alias.exists():
        shutil.copyfile(raw_path, alias)
    return alias


def sheet_cache_path(doc: "Document") -> Path:
    import sheets as _sheets
    return STORAGE_DIR / doc.id / _sheets.CACHE_NAME


def _refresh_sheet_meta(doc: "Document", frames: list[dict]) -> bool:
    """Bring an older upload's meta["sheets"] up to date from its parsed
    frames: the legacy {name: {rows, cols}} map (or a list that still carries
    a hidden sheet's columns/samples) is replaced by the current list form.
    Returns True when doc.meta changed."""
    import sheets as _sheets
    meta = doc.meta if isinstance(doc.meta, dict) else None
    if meta is None:
        return False
    raw = meta.get("sheets")
    stale = not isinstance(raw, list) or not raw
    if not stale:
        _norm, changed = _sheets.normalize_meta_sheets(meta)
        stale = changed
    if not stale:
        return False
    fmt = Path(doc.filename or "").suffix.lower().lstrip(".") or "sheet"
    fresh = _sheets.build_meta(frames, fmt)
    for key in ("sheets", "sheet_count", "hidden_sheets", "table_rows", "sheet_format"):
        meta[key] = fresh.get(key)
    if "uncomputed_formulas" in fresh:
        meta["uncomputed_formulas"] = fresh["uncomputed_formulas"]
    return True


def sheet_frames(doc: "Document") -> list[dict] | None:
    """Parsed sheets of a spreadsheet document (cached pickle, regenerated
    from the raw file when missing). None for non-spreadsheets / on failure.
    An older upload's meta["sheets"] is refreshed to the list form (and the
    registry persisted) once the frames are available."""
    if doc is None or not is_sheet_file(doc.filename, doc.mime):
        return None
    import sheets as _sheets
    raw_path = STORAGE_DIR / doc.id / doc.filename
    try:
        frames = _sheets.load_sheets(_sheet_path(raw_path, doc.filename, doc.mime) if raw_path.exists() else raw_path,
                                     sheet_cache_path(doc))
    except Exception as exc:
        log.warning("sheet load failed for %s: %s", doc.id, exc)
        return None
    try:
        if _refresh_sheet_meta(doc, frames) and REGISTRY.get(doc.id) is doc:
            _save_registry()   # sync: sheet_frames runs on a worker thread
    except Exception as exc:  # meta refresh is best-effort
        log.debug("sheet meta refresh failed for %s: %s", doc.id, exc)
    return frames


def sheet_preview(doc_id: str, sheet: str | None = None, offset: int = 0, limit: int = 200,
                  include_hidden: bool = False) -> dict:
    """GET /api/idp/documents/{id}/sheets: {sheets:[{name, rows, cols, hidden}],
    sheet, columns, dtypes, rows, offset, total, hidden_sheets}. Hidden sheets
    are neither listed nor served unless `include_hidden` (query param
    include_hidden=1). LookupError when the doc or sheet is unknown or the
    sheet is hidden ("sheet is hidden"), ValueError when it is not a readable
    spreadsheet."""
    import sheets as _sheets
    doc = get_document(doc_id)
    if doc is None:
        raise LookupError("Document not found")
    if not is_sheet_file(doc.filename, doc.mime):
        raise ValueError("Not a spreadsheet document")
    frames = sheet_frames(doc)
    if frames is None:
        raise ValueError("Could not read the spreadsheet")
    try:
        return _sheets.preview(frames, sheet, offset, limit, include_hidden=bool(include_hidden))
    except PermissionError:
        raise LookupError("sheet is hidden") from None
    except KeyError:
        raise LookupError(f"Sheet not found: {sheet}") from None


def _extract_file(
    raw_path: Path, filename: str, mime: str, doc_dir: Path
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Extract text, images, and metadata from a file.

    Returns: (page_texts, page_images, meta)
    """
    page_texts: list[str] = []
    page_images: list[str] = []
    meta: dict[str, Any] = {}

    if mime == "application/pdf":
        page_texts, page_images = _extract_pdf(raw_path, doc_dir)
    elif mime.endswith("wordprocessingml.document") or filename.lower().endswith(".docx"):
        page_texts = _extract_docx(raw_path)
    elif is_sheet_file(filename, mime):
        page_texts, sheet_meta = _extract_xlsx(_sheet_path(raw_path, filename, mime), doc_dir)
        meta.update(sheet_meta)
    elif mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation" or filename.lower().endswith(".pptx"):
        page_texts = _extract_pptx(raw_path)
    elif mime == "application/vnd.oasis.opendocument.text" or filename.lower().endswith(".odt"):
        page_texts = _extract_odt(raw_path)
    elif mime == "text/html" or filename.lower().endswith((".html", ".htm")):
        page_texts = _extract_html(raw_path)
    elif mime in ("application/rtf", "text/rtf") or filename.lower().endswith(".rtf"):
        page_texts = _extract_rtf(raw_path)
    elif mime == "application/msword" or filename.lower().endswith(".doc"):
        page_texts = _extract_doc(raw_path)
    elif mime == "message/rfc822" or filename.lower().endswith(".eml"):
        page_texts = [_extract_email(raw_path)]
    elif mime.startswith("image/"):
        page_images = _extract_image(raw_path, doc_dir)
        page_texts = [""]    # image-only — OCR fills this in on demand
    elif mime.startswith("text/"):
        page_texts = [raw_path.read_text(errors="ignore")]
    else:
        page_texts = [raw_path.read_text(errors="ignore")]

    return page_texts, page_images, meta


def reextract_if_empty(doc: Document) -> Document:
    """Re-extract a document if its text is empty, for reextractable formats.

    If doc.text is non-empty or the format is not reextractable, returns doc unchanged.
    If extraction succeeds and produces text, updates doc and saves registry.
    Only attempts once per doc per process (tracked in _REEXTRACT_TRIED).
    """
    # Check if document text is non-empty
    if doc.text.strip():
        return doc

    # Check if format is reextractable
    ext = Path(doc.filename).suffix.lower()
    if ext not in REEXTRACTABLE_EXTS:
        return doc

    # Check if already tried
    if doc.id in _REEXTRACT_TRIED:
        return doc
    _REEXTRACT_TRIED.add(doc.id)

    # Locate raw file
    raw_path = STORAGE_DIR / doc.id / doc.filename
    if not raw_path.exists():
        log.warning("Cannot re-extract %s: file not found at %s", doc.id, raw_path)
        return doc

    try:
        doc_dir = raw_path.parent
        mime = doc.mime or _detect_mime(doc.filename)

        page_texts, page_images, meta_new = _extract_file(raw_path, doc.filename, mime, doc_dir)
        joined_text = "\n\n".join(page_texts).strip()

        if joined_text:
            # Update document
            doc.text = joined_text
            doc.page_texts = page_texts
            doc.pages = max(len(page_texts), len(page_images))
            if page_images:
                doc.page_images = page_images
            doc.meta.update(meta_new)
            doc.meta["reextracted"] = True

            # Persist
            REGISTRY[doc.id] = doc
            _save_registry()

            log.info("Re-extracted %s: now has %d chars", doc.id, len(joined_text))

    except Exception as exc:
        log.warning("Re-extraction failed for %s: %s", doc.id, exc)

    return doc


def get_document_fresh(doc_id: str) -> Document | None:
    """Get a document, re-extracting if its text is empty."""
    doc = get_document(doc_id)
    return reextract_if_empty(doc) if doc else None


def reextract_empty_documents() -> int:
    """Re-extract all documents with empty text. Returns count of docs that got text."""
    count = 0
    for doc in list(REGISTRY.values()):
        if not doc.text.strip():
            old_len = len(doc.text)
            reextract_if_empty(doc)
            if len(doc.text) > old_len:
                count += 1
    return count


async def ingest_file(filename: str, data: bytes) -> Document:
    """Persist a file, extract its text/images, register it."""
    filename = _safe_upload_name(filename)
    doc_id = uuid.uuid4().hex[:12]
    doc_dir = STORAGE_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    raw_path = doc_dir / filename
    await asyncio.to_thread(raw_path.write_bytes, data)

    mime = _detect_mime(filename)

    try:
        # PDF page rendering / docx / xlsx parsing is CPU-bound: keep it off the
        # event loop so other SSE streams keep flowing during a big ingest.
        page_texts, page_images, meta = await asyncio.to_thread(
            _extract_file, raw_path, filename, mime, doc_dir,
        )
    except Exception as exc:
        log.exception("ingest failed: %s", exc)
        page_texts = [f"[ingest error: {exc}]"]
        page_images = []
        meta = {}

    full_text = "\n\n".join(page_texts).strip()

    doc = Document(
        id=doc_id, filename=filename, mime=mime, size=len(data),
        uploaded_at=time.time(),
        pages=max(len(page_texts), len(page_images)),
        text=full_text,
        page_texts=page_texts,
        page_images=page_images,
        meta=meta,
    )
    REGISTRY[doc_id] = doc
    await save_registry_async()
    return doc


def _safe_upload_name(filename: str | None) -> str:
    """Strip any directory components / traversal from a client-supplied name."""
    raw = (filename or "").replace("\\", "/")
    name = Path(raw).name.strip()
    if name in ("", ".", ".."):
        return "upload"
    return name


async def ingest_text(text: str, title: str | None = None, kind: str = "text") -> Document:
    """Create and register a Document from plain text.

    Args:
        text: The text content (must be non-empty).
        title: Optional title for the document (becomes filename).
        kind: Type of text: 'text', 'email', etc.

    Returns:
        A registered Document object.

    Raises:
        ValueError if text is empty or whitespace-only.
    """
    if not text or not text.strip():
        raise ValueError("text cannot be empty")

    text = text.strip()
    doc_id = uuid.uuid4().hex[:12]
    doc_dir = STORAGE_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Determine filename and MIME type
    if kind == "email":
        filename = title or "Pasted email"
        mime = "message/rfc822"
    else:
        filename = title or "Pasted text"
        mime = "text/plain"

    # Create document
    doc = Document(
        id=doc_id,
        filename=filename,
        mime=mime,
        size=len(text.encode("utf-8")),
        uploaded_at=time.time(),
        pages=1,
        text=text,
        page_texts=[text],
        page_images=[],
        meta={},
    )
    REGISTRY[doc_id] = doc
    await save_registry_async()
    return doc


# ── Vision-LLM helpers ────────────────────────────────────────────────────────
NUM_CTX_MIN = 8192
NUM_CTX_MAX = 32768
# Rough per-image token budget for vision models (a ~1000x1300 page render).
_TOKENS_PER_IMAGE = 1600


def _num_ctx_for(prompt: str, num_predict: int, *, extra_tokens: int = 0) -> int:
    """Context window big enough for prompt + generation, as a power of two
    clamped to [8192, 32768]. Without this Ollama's small default window
    silently truncates long document prompts from the *start*."""
    est = len(prompt or "") // 3 + int(num_predict) + 256 + int(extra_tokens)
    return min(NUM_CTX_MAX, max(NUM_CTX_MIN, 1 << (max(est, 1) - 1).bit_length()))


async def _ollama_vision_call(
    model: str, prompt: str, image_paths: list[str],
    *, num_predict: int = 2048,
) -> str:
    """Call Ollama with images. Resolves & validates the model before sending."""
    # Resolve the actual model to use ─────────────────────────────────────
    chosen = (model or "").strip()
    if not chosen or not _name_is_vision(chosen):
        fallback = await find_installed_vision_model()
        if fallback:
            log.info("Vision call: '%s' is text-only — using installed vision model '%s'",
                     chosen or "(none)", fallback)
            chosen = fallback
        else:
            raise RuntimeError(
                "No vision-capable model is installed. "
                "Install one with `ollama pull qwen2.5vl:7b` (or `minicpm-v`) "
                "and select it in Settings → Models."
            )

    if not await _ollama_has_model(chosen):
        raise RuntimeError(
            f"Model '{chosen}' is configured but not installed in Ollama. "
            f"Run `ollama pull {chosen}` from the terminal, then try again."
        )

    # Encode images to base64 ─────────────────────────────────────────────
    images_b64: list[str] = []
    for p in image_paths[:16]:        # cap to avoid blowing out context
        try:
            data = Path(p).read_bytes()
            if data:
                images_b64.append(base64.b64encode(data).decode())
        except Exception as exc:
            log.warning("Skipped unreadable image %s: %s", p, exc)

    if not images_b64:
        raise RuntimeError("No readable images to process")

    payload = {
        "model":  chosen,
        "prompt": prompt,
        "images": images_b64,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": num_predict,
            "num_ctx":     _num_ctx_for(prompt, num_predict,
                                        extra_tokens=_TOKENS_PER_IMAGE * len(images_b64)),
            "num_thread":  _hw.recommended_num_thread(),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/generate", json=payload)
            if r.status_code >= 400:
                body_text = (r.text or "")[:300]
                log.error("Ollama %d on vision call (model=%s): %s",
                          r.status_code, chosen, body_text)
                raise RuntimeError(
                    f"Vision model '{chosen}' failed (Ollama HTTP {r.status_code}). "
                    f"{body_text or 'Check that the model accepts images.'}"
                )
            return (r.json().get("response") or "").strip()
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_BASE}: {exc}")


async def _ollama_text_call(
    model: str, prompt: str, *, num_predict: int = 2048,
) -> str:
    if not model:
        # Fall back to whatever chat model is installed
        installed = await _list_installed_models()
        chat_candidates = [m for m in installed
                           if not any(p in m.lower() for p in ("embed", "orpheus"))]
        if not chat_candidates:
            raise RuntimeError(
                "No chat model is configured or installed. "
                "Install one with `ollama pull qwen2.5:7b` first."
            )
        model = chat_candidates[0]
        log.info("Text call: no model configured — using '%s'", model)

    if not await _ollama_has_model(model):
        raise RuntimeError(
            f"Model '{model}' is not installed in Ollama. "
            f"Run `ollama pull {model}` from the terminal, then try again."
        )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={
                    "model": model, "prompt": prompt, "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": num_predict,
                        "num_ctx":     _num_ctx_for(prompt, num_predict),
                        "num_thread":  _hw.recommended_num_thread(),
                    },
                },
            )
            if r.status_code >= 400:
                body_text = (r.text or "")[:300]
                log.error("Ollama %d on text call (model=%s): %s",
                          r.status_code, model, body_text)
                raise RuntimeError(
                    f"Model '{model}' failed (Ollama HTTP {r.status_code}). {body_text}"
                )
            return (r.json().get("response") or "").strip()
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_BASE}: {exc}")


async def _prepare_text_model(model: str) -> str:
    """Resolve/validate a text model the same way _ollama_text_call does."""
    if not model:
        installed = await _list_installed_models()
        chat_candidates = [m for m in installed
                           if not any(p in m.lower() for p in ("embed", "orpheus"))]
        if not chat_candidates:
            raise RuntimeError(
                "No chat model is configured or installed. "
                "Install one with `ollama pull qwen2.5:7b` first."
            )
        model = chat_candidates[0]
    if not await _ollama_has_model(model):
        raise RuntimeError(
            f"Model '{model}' is not installed in Ollama. "
            f"Run `ollama pull {model}` from the terminal, then try again."
        )
    return model


async def stream_text(
    model: str, prompt: str, *, think: bool = False, num_predict: int = 1536,
    temperature: float | None = None,
):
    """Stream a text generation over Ollama /api/chat.

    Yields dicts: {'thinking': str} and/or {'content': str} deltas, then a final
    {'done': True, 'stats': {...}}. Raises RuntimeError on model/connection errors.
    """
    model = await _prepare_text_model(model)
    payload = {
        "model":    model,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   True,
        "think":    bool(think),
        "options":  {
            "temperature": 0.3 if temperature is None else float(temperature),
            "num_predict": num_predict,
            "num_ctx":     _num_ctx_for(prompt, num_predict),
            "num_thread":  _hw.recommended_num_thread(),
        },
    }
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json=payload) as resp:
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
                        # Ollama reports mid-stream failures (e.g. GPU out of
                        # memory) as {"error": "..."} lines — never skip them.
                        raise RuntimeError(f"Model '{model}' failed: {str(obj.get('error'))[:300]}")
                    msg = obj.get("message") or {}
                    th = msg.get("thinking")
                    if th:
                        yield {"thinking": th}
                    ct = msg.get("content")
                    if ct:
                        yield {"content": ct}
                    if obj.get("done"):
                        ec = obj.get("eval_count") or 0
                        ed = obj.get("eval_duration") or 0
                        tps = (ec / (ed / 1e9)) if ed else 0.0
                        yield {"done": True, "stats": {
                            "eval_count":        ec,
                            "tok_per_s":         round(tps, 1),
                            "total_duration_ms": round((obj.get("total_duration") or 0) / 1e6),
                        }}
                        return
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_BASE}: {exc}")


# ── IDP operations ────────────────────────────────────────────────────────────
OCR_MAX_PAGES = 50
OCR_PAGE_NUM_PREDICT = 2048
OCR_TRUNCATION_NOTE = f"[OCR stopped after {OCR_MAX_PAGES} pages]"
_OCR_PROMPT = (
    "Perform OCR on the provided image. Extract every readable text element exactly as written, "
    "preserving line breaks and reading order. Output ONLY the extracted text — no commentary."
)


async def run_ocr(doc: Document, model: str, page_range: tuple[int, int] | None = None) -> str:
    """Run OCR on all (or a slice of) the document's page images.

    One vision call per page (so long scans are neither truncated by
    num_predict nor capped at 16 images); each result fills
    ``doc.page_texts[i]`` and ``doc.text`` is rebuilt from the pages. At most
    OCR_MAX_PAGES pages are processed per call. Returns the joined OCR text.
    """
    if not doc.page_images:
        return doc.text or "(no images available for OCR)"

    n_images = len(doc.page_images)
    indices = list(range(n_images))
    if page_range:
        s, e = page_range
        indices = indices[max(0, s - 1) : e]
    if not indices:
        raise RuntimeError("No pages in the requested range to OCR")

    truncated = len(indices) > OCR_MAX_PAGES
    indices = indices[:OCR_MAX_PAGES]

    page_texts = list(doc.page_texts)
    if len(page_texts) < n_images:
        page_texts.extend([""] * (n_images - len(page_texts)))

    ocr_parts: list[str] = []
    for pos, i in enumerate(indices):
        try:
            text = await _ollama_vision_call(
                model, _OCR_PROMPT, [doc.page_images[i]], num_predict=OCR_PAGE_NUM_PREDICT,
            )
        except RuntimeError as exc:
            if pos == 0:
                raise          # config problem (no/missing model, Ollama down) — surface it
            log.warning("OCR failed for %s page %d: %s", doc.id, i + 1, exc)
            ocr_parts.append(f"[OCR failed for page {i + 1}]")
            continue
        text = (text or "").strip()
        ocr_parts.append(text)
        # Keep native text when the OCR pass produced less (digital PDFs).
        if text and len(text) > len((page_texts[i] or "").strip()):
            page_texts[i] = text

    doc.page_texts = page_texts
    doc.pages = max(doc.pages, len(page_texts))
    doc.text = "\n\n".join(page_texts).strip()
    doc.meta["last_ocr_at"] = int(time.time() * 1000)
    doc.meta["ocr_model"]   = model
    doc.meta["ocr_pages"]   = len(indices)
    doc.meta["ocr_truncated"] = truncated
    await save_registry_async()

    joined = "\n\n".join(p for p in ocr_parts if p)
    if truncated:
        joined = f"{joined}\n\n{OCR_TRUNCATION_NOTE}".strip()
    return joined


async def summarize(doc: Document, model: str, style: str = "brief") -> str:
    style_prompts = {
        "brief":    "in 3-5 sentences",
        "detailed": "in 2-3 paragraphs with key facts and conclusions",
        "bullets":  "as a bullet list of the most important points",
    }
    prompt = (
        f"Summarize the following document {style_prompts.get(style, style_prompts['brief'])}.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:32000]}\n--- END ---"
    )
    return await _ollama_text_call(model, prompt, num_predict=1024)


async def qa(doc: Document, model: str, question: str) -> str:
    prompt = (
        "Answer the user's question using ONLY the document below. "
        "If the answer is not in the document, say so plainly.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:32000]}\n--- END ---\n\n"
        f"Question: {question}\nAnswer:"
    )
    return await _ollama_text_call(model, prompt, num_predict=1024)


def build_prompt(op: str, doc: "Document", options: dict) -> str:
    """Build the prompt for a streamable single-document text op. Mirrors the
    non-streaming functions' prompts exactly so streamed + non-streamed output
    match."""
    text = (doc.text or "")[:32000]
    if op == "summarize":
        style_prompts = {
            "brief":    "in 3-5 sentences",
            "detailed": "in 2-3 paragraphs with key facts and conclusions",
            "bullets":  "as a bullet list of the most important points",
        }
        style = options.get("style", "brief")
        return (
            f"Summarize the following document {style_prompts.get(style, style_prompts['brief'])}.\n\n"
            f"--- DOCUMENT ---\n{text}\n--- END ---"
        )
    if op == "qa":
        q = options.get("question", "")
        return (
            "Answer the user's question using ONLY the document below. "
            "If the answer is not in the document, say so plainly.\n\n"
            f"--- DOCUMENT ---\n{text}\n--- END ---\n\n"
            f"Question: {q}\nAnswer:"
        )
    if op == "translate":
        target = options.get("target", "French")
        text_for_translate = (doc.text or "")[:24000]
        return (
            f"Translate the following document into {target}. Preserve formatting and structure. "
            "Output ONLY the translation.\n\n"
            f"--- DOCUMENT ---\n{text_for_translate}\n--- END ---"
        )
    if op == "redact":
        cats = options.get("categories", [])
        cats_str = ", ".join(cats) if cats else "personal names, addresses, phone numbers, emails, IDs"
        text_for_redact = (doc.text or "")[:24000]
        return (
            f"Redact the following from the document by replacing each occurrence with [REDACTED]: {cats_str}.\n"
            "Output the full document with redactions applied.\n\n"
            f"--- DOCUMENT ---\n{text_for_redact}\n--- END ---"
        )
    if op == "humanize":
        tone = options.get("tone", "natural")
        intensity = options.get("intensity", "medium")
        tone_hint = {
            "natural":       "warm, conversational, but still polished",
            "casual":        "relaxed and informal, like a friendly email",
            "professional":  "clear and confident, like a well-written business memo",
            "academic":      "considered and precise, but without stiff or robotic phrasing",
        }.get(tone, "warm, conversational, but still polished")
        intensity_hint = {
            "light":  "Keep most of the wording. Only smooth out the most obvious AI tics (repetitive transitions, over-hedging, empty filler).",
            "medium": "Substantially rework sentences so the rhythm varies. Break up parallel structures. Cut hollow phrases. Prefer concrete verbs and specific nouns.",
            "heavy":  "Rewrite freely. Vary sentence length dramatically (some short, some long). Use idioms where natural. Add small imperfections a real writer would leave (a hedge, a slight tangent, an aside). Never sound formulaic.",
        }.get(intensity, "Substantially rework sentences so the rhythm varies. Break up parallel structures. Cut hollow phrases. Prefer concrete verbs and specific nouns.")
        text_for_humanize = (doc.text or "")[:24000]
        return (
            "You are rewriting a passage so it reads as if a real human wrote it — not an AI. "
            f"Target tone: {tone_hint}.\n\n"
            f"{intensity_hint}\n\n"
            "Specifically:\n"
            "- Avoid AI tells: 'delve', 'furthermore', 'moreover', 'in conclusion', 'it is important to note', "
            "'navigate the landscape', 'a testament to', 'in today's fast-paced world', 'tapestry', 'realm', "
            "'crucial', 'pivotal', 'multifaceted', 'holistic'.\n"
            "- Do not open with 'In today's', 'In the world of', 'It is important', 'Let's explore', or 'This article'.\n"
            "- Vary sentence length. Include at least one short sentence per paragraph.\n"
            "- Prefer active voice. Use contractions where natural (it's, don't, we're).\n"
            "- Cut redundant qualifiers (very, really, quite, extremely) unless they add real meaning.\n"
            "- Keep every fact, number, name, and quote intact. Do not invent details.\n"
            "- Preserve the original language of the document. Do not translate.\n"
            "- Preserve headings, lists, and paragraph breaks.\n\n"
            "Output ONLY the rewritten text — no preamble, no explanation, no markdown fences.\n\n"
            f"--- DOCUMENT ---\n{text_for_humanize}\n--- END ---"
        )
    raise ValueError(f"unknown streamable op: {op}")


def needs_ocr(doc: "Document") -> bool:
    """True when a document has page images but essentially no extracted text
    (image-only PDF / scan) — i.e. OCR is required before it can be queried.

    Scales with page count so a multi-page scan carrying only a stamp or page
    number per page still counts as needing OCR. A document that has already
    been through run_ocr is not re-queued (blank scans would otherwise be
    re-OCR'd on every query)."""
    if not doc.page_images:
        return False
    if (doc.meta or {}).get("last_ocr_at"):
        return False
    return len((doc.text or "").strip()) < 40 * max(1, doc.pages or 0)


def build_multi_prompt(question: str, contexts: list[tuple[str, str]]) -> str:
    """Prompt for multi-document RAG Q&A over ranked (filename, chunk) tuples."""
    if not contexts:
        return ""
    blocks = []
    for i, (fname, chunk) in enumerate(contexts, 1):
        blocks.append(f"[{i}] (source: {fname})\n{chunk}")
    joined = "\n\n".join(blocks)
    return (
        "You are answering a question using ONLY the numbered excerpts below, "
        "which are drawn from several documents. Cite the source filename(s) you "
        "used in your answer. If the answer is not present in the excerpts, say so "
        "plainly.\n\n"
        f"--- EXCERPTS ---\n{joined}\n--- END ---\n\n"
        f"Question: {question}\nAnswer:"
    )


async def multi_qa(model: str, question: str, contexts: list[tuple[str, str]]) -> str:
    """
    Answer a question over retrieved chunks drawn from MULTIPLE documents.
    `contexts` is a list of (filename, chunk_text) tuples (already ranked by
    relevance). Builds a cited prompt and calls the configured text model.
    """
    if not contexts:
        return "No relevant content was found in the selected documents for that question."
    prompt = build_multi_prompt(question, contexts)
    return await _ollama_text_call(model, prompt, num_predict=1536)


async def extract_tables(doc: Document, model: str) -> list[dict]:
    """Return tables as a list of {title, rows: [[...]]} objects."""
    use_vision = bool(doc.page_images) and not (doc.text or "").strip()
    if use_vision:
        prompt = (
            "Extract all tables from the image(s). For each table, output strict JSON with this schema:\n"
            "{\"tables\": [{\"title\": \"...\", \"headers\": [...], \"rows\": [[...]]}]}\n"
            "If no tables are present, output {\"tables\": []}. Output ONLY JSON."
        )
        raw = await _ollama_vision_call(model, prompt, doc.page_images, num_predict=4096)
    else:
        prompt = (
            "Extract all tables from the following document into strict JSON:\n"
            "{\"tables\": [{\"title\": \"...\", \"headers\": [...], \"rows\": [[...]]}]}\n"
            f"Output ONLY JSON.\n\n--- DOCUMENT ---\n{doc.text[:32000]}\n--- END ---"
        )
        raw = await _ollama_text_call(model, prompt, num_predict=2048)

    # Extract the first {...} block
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        return json.loads(m.group(0)).get("tables", [])
    except Exception:
        return []


async def extract_entities(doc: Document, model: str) -> dict:
    """Pull dates, people, organizations, amounts, emails, URLs, addresses."""
    prompt = (
        "Extract all named entities from this document. Return strict JSON with these keys:\n"
        "{\"people\":[], \"organizations\":[], \"dates\":[], \"amounts\":[], "
        "\"emails\":[], \"urls\":[], \"addresses\":[], \"phone_numbers\":[]}\n"
        "Each list contains de-duplicated strings exactly as they appear. Output ONLY JSON.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:32000]}\n--- END ---"
    )
    raw = await _ollama_text_call(model, prompt, num_predict=2048)
    m = re.search(r"\{[\s\S]*\}", raw)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


async def classify(doc: Document, model: str) -> dict:
    prompt = (
        "Classify this document. Return strict JSON: "
        "{\"type\":\"invoice|contract|resume|report|email|letter|receipt|form|other\", "
        "\"language\":\"en|fr|...\", \"confidence\":0.0-1.0, \"topics\":[]}\n"
        "Output ONLY JSON.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:16000]}\n--- END ---"
    )
    raw = await _ollama_text_call(model, prompt, num_predict=512)
    m = re.search(r"\{[\s\S]*\}", raw)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


async def translate(doc: Document, model: str, target_lang: str) -> str:
    prompt = (
        f"Translate the following document into {target_lang}. Preserve formatting and structure. "
        "Output ONLY the translation.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:24000]}\n--- END ---"
    )
    return await _ollama_text_call(model, prompt, num_predict=4096)


async def redact(doc: Document, model: str, categories: list[str]) -> str:
    cats = ", ".join(categories) if categories else "personal names, addresses, phone numbers, emails, IDs"
    prompt = (
        f"Redact the following from the document by replacing each occurrence with [REDACTED]: {cats}.\n"
        "Output the full document with redactions applied.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:24000]}\n--- END ---"
    )
    return await _ollama_text_call(model, prompt, num_predict=4096)


async def humanize(doc: Document, model: str, tone: str = "natural", intensity: str = "medium") -> str:
    tone_hint = {
        "natural":       "warm, conversational, but still polished",
        "casual":        "relaxed and informal, like a friendly email",
        "professional":  "clear and confident, like a well-written business memo",
        "academic":      "considered and precise, but without stiff or robotic phrasing",
    }.get(tone, "warm, conversational, but still polished")

    intensity_hint = {
        "light":  "Keep most of the wording. Only smooth out the most obvious AI tics (repetitive transitions, over-hedging, empty filler).",
        "medium": "Substantially rework sentences so the rhythm varies. Break up parallel structures. Cut hollow phrases. Prefer concrete verbs and specific nouns.",
        "heavy":  "Rewrite freely. Vary sentence length dramatically (some short, some long). Use idioms where natural. Add small imperfections a real writer would leave (a hedge, a slight tangent, an aside). Never sound formulaic.",
    }.get(intensity, "Substantially rework sentences so the rhythm varies. Break up parallel structures. Cut hollow phrases. Prefer concrete verbs and specific nouns.")

    prompt = (
        "You are rewriting a passage so it reads as if a real human wrote it — not an AI. "
        f"Target tone: {tone_hint}.\n\n"
        f"{intensity_hint}\n\n"
        "Specifically:\n"
        "- Avoid AI tells: 'delve', 'furthermore', 'moreover', 'in conclusion', 'it is important to note', "
        "'navigate the landscape', 'a testament to', 'in today's fast-paced world', 'tapestry', 'realm', "
        "'crucial', 'pivotal', 'multifaceted', 'holistic'.\n"
        "- Do not open with 'In today's', 'In the world of', 'It is important', 'Let's explore', or 'This article'.\n"
        "- Vary sentence length. Include at least one short sentence per paragraph.\n"
        "- Prefer active voice. Use contractions where natural (it's, don't, we're).\n"
        "- Cut redundant qualifiers (very, really, quite, extremely) unless they add real meaning.\n"
        "- Keep every fact, number, name, and quote intact. Do not invent details.\n"
        "- Preserve the original language of the document. Do not translate.\n"
        "- Preserve headings, lists, and paragraph breaks.\n\n"
        "Output ONLY the rewritten text — no preamble, no explanation, no markdown fences.\n\n"
        f"--- DOCUMENT ---\n{doc.text[:24000]}\n--- END ---"
    )
    return await _ollama_text_call(model, prompt, num_predict=4096)


# ── Exports ────────────────────────────────────────────────────────────────────
def export_markdown(doc: Document) -> bytes:
    md = f"# {doc.filename}\n\n"
    for i, page in enumerate(doc.page_texts, 1):
        if doc.pages > 1:
            md += f"\n## Page {i}\n\n"
        md += page + "\n"
    return md.encode()


def export_txt(doc: Document) -> bytes:
    return doc.text.encode() if doc.text else b""


def export_pdf(doc: Document) -> bytes:
    from reportlab.lib.pagesizes import letter  # type: ignore[import-not-found]
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak, Spacer
    from reportlab.lib.units import inch

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.7*inch, rightMargin=0.7*inch,
                            topMargin=0.7*inch, bottomMargin=0.7*inch)
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph(f"<b>{doc.filename}</b>", styles["Title"]), Spacer(1, 12)]
    for i, page in enumerate(doc.page_texts, 1):
        if doc.pages > 1:
            story.append(Paragraph(f"<b>Page {i}</b>", styles["Heading2"]))
        for paragraph in page.split("\n\n"):
            para_html = paragraph.replace("\n", "<br/>").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if para_html.strip():
                story.append(Paragraph(para_html, styles["BodyText"]))
                story.append(Spacer(1, 6))
        if i < doc.pages:
            story.append(PageBreak())
    pdf.build(story)
    return buf.getvalue()


def export_xlsx(tables: list[dict]) -> bytes:
    from openpyxl import Workbook  # type: ignore[import-not-found]
    wb = Workbook()
    wb.remove(wb.active)
    for i, t in enumerate(tables or [], 1):
        title = (t.get("title") or f"Table {i}")[:31] or f"Table {i}"
        ws = wb.create_sheet(title=title)
        headers = t.get("headers", [])
        if headers:
            ws.append(headers)
        for row in t.get("rows", []):
            ws.append(row)
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_json(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False).encode()


def export_csv(tables: list[dict]) -> bytes:
    """Export the first table to CSV."""
    if not tables:
        return b""
    t = tables[0]
    headers = t.get("headers", [])
    rows = t.get("rows", [])
    lines: list[str] = []
    if headers:
        lines.append(",".join(_csv_quote(str(h)) for h in headers))
    for row in rows:
        lines.append(",".join(_csv_quote(str(c)) for c in row))
    return ("\n".join(lines) + "\n").encode()


def _csv_quote(s: str) -> str:
    if any(c in s for c in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s
