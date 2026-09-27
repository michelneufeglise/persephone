"""
Tests for backend fixes in idp_engine.py / doc_agent_hooks.py:
num_ctx sizing, atomic registry + .bak recovery, upload filename
sanitising, per-page OCR, needs_ocr rule, and signature page-render paths.

Fakes only — no network, no Ollama. Every write goes to pytest's tmp_path
(STORAGE_DIR / REGISTRY_FILE / REGISTRY are monkeypatched per test).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import idp_engine as idp


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Point idp_engine storage + registry at tmp_path with an empty registry."""
    storage = tmp_path / "uploads"
    storage.mkdir()
    monkeypatch.setattr(idp, "STORAGE_DIR", storage)
    monkeypatch.setattr(idp, "REGISTRY_FILE", storage / "_registry.json")
    monkeypatch.setattr(idp, "REGISTRY", {})
    return storage


def _doc(doc_id="d1", *, text="", pages=1, page_images=None, page_texts=None,
         filename="scan.pdf", mime="application/pdf", meta=None) -> idp.Document:
    return idp.Document(
        id=doc_id, filename=filename, mime=mime, size=1, uploaded_at=time.time(),
        pages=pages, text=text,
        page_texts=list(page_texts or []),
        page_images=list(page_images or []),
        meta=dict(meta or {}),
    )


# ── 1. num_ctx ──────────────────────────────────────────────────────────────

class _FakeStreamResp:
    status_code = 200

    async def aread(self):
        return b""

    async def aiter_lines(self):
        yield json.dumps({"message": {"content": "ok"}})
        yield json.dumps({"done": True, "eval_count": 1, "eval_duration": 1_000_000})


class _FakeStreamCM:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


def _fake_async_client(captured: list):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, **kw):
            captured.append(json)
            return _FakeStreamCM(_FakeStreamResp())

    return _Client


async def _drain(agen):
    return [ev async for ev in agen]


def test_stream_text_sets_scaled_num_ctx(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(idp, "_prepare_text_model", AsyncMock(return_value="m:7b"))
    monkeypatch.setattr(idp.httpx, "AsyncClient", _fake_async_client(captured))

    # Short prompt → floor of 8192
    asyncio.run(_drain(idp.stream_text("m:7b", "hello", num_predict=512)))
    assert captured[-1]["options"]["num_ctx"] == 8192

    # 60k-char prompt: est = 20000 + 1536 + 256 = 21792 → next pow2 = 32768
    events = asyncio.run(_drain(idp.stream_text("m:7b", "x" * 60_000, num_predict=1536)))
    opts = captured[-1]["options"]
    assert opts["num_ctx"] >= 8192
    assert opts["num_ctx"] == 32768
    assert opts["num_ctx"] >= 60_000 // 3 + 1536
    assert opts["num_predict"] == 1536
    assert any(e.get("done") for e in events)


def test_num_ctx_helper_scaling_and_cap():
    assert idp._num_ctx_for("", 100) == 8192
    # est = 10000 + 2048 + 256 = 12304 → 16384
    assert idp._num_ctx_for("y" * 30_000, 2048) == 16384
    # huge prompt is capped
    assert idp._num_ctx_for("z" * 1_000_000, 4096) == 32768
    # images add to the estimate
    assert idp._num_ctx_for("p", 2048, extra_tokens=16 * 1600) == 32768


def test_text_call_sends_num_ctx(monkeypatch):
    captured: list[dict] = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"response": "fine"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            captured.append(json)
            return _Resp()

    monkeypatch.setattr(idp, "_ollama_has_model", AsyncMock(return_value=True))
    monkeypatch.setattr(idp.httpx, "AsyncClient", _Client)
    out = asyncio.run(idp._ollama_text_call("m:7b", "q" * 45_000, num_predict=1024))
    assert out == "fine"
    assert captured[0]["options"]["num_ctx"] == 16384  # 15000+1024+256 → 16384


# ── 2. registry atomic save + .bak recovery ─────────────────────────────────

def test_save_registry_is_atomic_temp_then_replace(iso, monkeypatch):
    idp.REGISTRY["a1"] = _doc("a1", text="alpha")
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(idp.os, "replace", spy_replace)
    idp._save_registry()

    main = idp.REGISTRY_FILE
    assert main.exists()
    # final step renamed a temp file from the same directory onto the main file
    src, dst = replaced[-1]
    assert dst == str(main)
    assert Path(src).parent == main.parent and src != str(main)
    assert not Path(src).exists()
    assert json.loads(main.read_text())["a1"]["text"] == "alpha"
    # no stray temp files left behind
    assert not list(main.parent.glob(".registry-*.tmp"))


def test_save_registry_failure_keeps_previous_file(iso, monkeypatch):
    idp.REGISTRY["a1"] = _doc("a1", text="v1")
    idp._save_registry()
    good = idp.REGISTRY_FILE.read_text()

    idp.REGISTRY["a1"].text = "v2"

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(idp.os, "fsync", boom)
    with pytest.raises(OSError):
        idp._save_registry()
    assert idp.REGISTRY_FILE.read_text() == good
    assert not list(idp.REGISTRY_FILE.parent.glob(".registry-*.tmp"))


def test_load_registry_recovers_from_bak(iso, monkeypatch):
    idp.REGISTRY["a1"] = _doc("a1", text="first")
    idp._save_registry()                       # main = {a1}
    idp.REGISTRY["b2"] = _doc("b2", text="second")
    idp._save_registry()                       # .bak = {a1}, main = {a1, b2}
    bak = idp._registry_bak_file()
    assert bak.exists()
    assert set(json.loads(bak.read_text())) == {"a1"}

    # Corrupt the main file (simulated torn write / disk error)
    idp.REGISTRY_FILE.write_text('{"a1": {"id": "a1", "filen')

    fresh: dict = {}
    monkeypatch.setattr(idp, "REGISTRY", fresh)
    idp._load_registry()
    assert set(fresh) == {"a1"}
    assert fresh["a1"].text == "first"
    assert idp._REGISTRY_MAIN_GOOD is False

    # Next save must not rotate the corrupt main over the good backup
    fresh["c3"] = _doc("c3", text="third")
    idp._save_registry()
    assert set(json.loads(bak.read_text())) == {"a1"}
    assert set(json.loads(idp.REGISTRY_FILE.read_text())) == {"a1", "c3"}


def test_load_registry_uses_bak_when_main_missing(iso, monkeypatch):
    idp.REGISTRY["a1"] = _doc("a1", text="kept")
    idp._save_registry()
    idp._save_registry()  # rotates a good copy to .bak
    idp.REGISTRY_FILE.unlink()
    fresh: dict = {}
    monkeypatch.setattr(idp, "REGISTRY", fresh)
    idp._load_registry()
    assert fresh["a1"].text == "kept"


# ── 3. ingest_file filename sanitising (+ off-loop extraction) ──────────────

def test_ingest_file_strips_path_traversal(iso):
    doc = asyncio.run(idp.ingest_file("../../evil.txt", b"hello world"))
    assert doc.filename == "evil.txt"
    stored = iso / doc.id / "evil.txt"
    assert stored.is_file()
    assert stored.read_bytes() == b"hello world"
    assert stored.resolve().parent == (iso / doc.id).resolve()
    assert not (iso.parent / "evil.txt").exists()
    assert doc.text == "hello world"
    # persisted through the atomic save
    assert doc.id in json.loads(idp.REGISTRY_FILE.read_text())


@pytest.mark.parametrize("raw,expected", [
    ("", "upload"), (None, "upload"), ("..", "upload"), ("/", "upload"), ("a/../..", "upload"),
    ("C:\\Users\\x\\report.pdf", "report.pdf"), ("/etc/passwd", "passwd"),
])
def test_safe_upload_name(raw, expected):
    assert idp._safe_upload_name(raw) == expected


def test_ingest_file_runs_extraction_off_loop(iso, monkeypatch):
    import threading
    seen: dict = {}
    real = idp._extract_file

    def spy(*a, **kw):
        seen["thread"] = threading.current_thread()
        return real(*a, **kw)

    monkeypatch.setattr(idp, "_extract_file", spy)
    asyncio.run(idp.ingest_file("a.txt", b"abc"))
    assert seen["thread"] is not threading.main_thread()


# ── 4. per-page OCR ─────────────────────────────────────────────────────────

def _make_pages(tmp_path: Path, n: int) -> list[str]:
    paths = []
    for i in range(n):
        p = tmp_path / f"p{i + 1}.png"
        p.write_bytes(b"\x89PNG fake")
        paths.append(str(p))
    return paths


def test_run_ocr_per_page_fills_page_texts(iso, tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        calls.append(list(image_paths))
        return f"text of {Path(image_paths[0]).stem}"

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    imgs = _make_pages(tmp_path, 3)
    doc = _doc("o1", pages=3, page_images=imgs, page_texts=["", "", ""])
    idp.REGISTRY[doc.id] = doc

    out = asyncio.run(idp.run_ocr(doc, "vis:7b"))
    assert len(calls) == 3
    assert all(len(c) == 1 for c in calls)
    assert doc.page_texts == ["text of p1", "text of p2", "text of p3"]
    assert doc.text == "text of p1\n\ntext of p2\n\ntext of p3"
    assert out == doc.text
    assert doc.meta["ocr_pages"] == 3 and doc.meta["ocr_truncated"] is False
    saved = json.loads(idp.REGISTRY_FILE.read_text())
    assert saved["o1"]["page_texts"][1] == "text of p2"


def test_run_ocr_caps_at_50_pages(iso, tmp_path, monkeypatch):
    count = {"n": 0}

    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        count["n"] += 1
        return "t"

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    imgs = _make_pages(tmp_path, 53)
    doc = _doc("o2", pages=53, page_images=imgs)
    idp.REGISTRY[doc.id] = doc
    out = asyncio.run(idp.run_ocr(doc, "vis:7b"))
    assert count["n"] == 50
    assert out.endswith("[OCR stopped after 50 pages]")
    assert doc.meta["ocr_truncated"] is True
    assert len(doc.page_texts) == 53 and doc.page_texts[52] == ""


def test_run_ocr_page_range_and_first_failure_raises(iso, tmp_path, monkeypatch):
    seen: list[str] = []

    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        seen.append(Path(image_paths[0]).stem)
        return "x"

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    imgs = _make_pages(tmp_path, 5)
    doc = _doc("o3", pages=5, page_images=imgs)
    asyncio.run(idp.run_ocr(doc, "vis", page_range=(2, 3)))
    assert seen == ["p2", "p3"]

    async def failing(*a, **kw):
        raise RuntimeError("No vision-capable model is installed.")

    monkeypatch.setattr(idp, "_ollama_vision_call", failing)
    with pytest.raises(RuntimeError, match="No vision-capable"):
        asyncio.run(idp.run_ocr(_doc("o4", pages=2, page_images=imgs[:2]), "vis"))


def test_run_ocr_keeps_richer_native_text(iso, tmp_path, monkeypatch):
    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        return "short"

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    imgs = _make_pages(tmp_path, 1)
    native = "A much longer natively extracted paragraph of real text."
    doc = _doc("o5", pages=1, text=native, page_images=imgs, page_texts=[native])
    asyncio.run(idp.run_ocr(doc, "vis"))
    assert doc.page_texts == [native]
    assert doc.text == native


# ── 5. needs_ocr ────────────────────────────────────────────────────────────

def test_needs_ocr_rules():
    imgs = ["/x/p1.png", "/x/p2.png", "/x/p3.png"]
    # 3-page scan with only a stamp + page numbers → still needs OCR
    stamp = "CONFIDENTIAL  page 1  page 2  page 3  received 2024-01-02"
    assert len(stamp) >= 40
    assert idp.needs_ocr(_doc(text=stamp, pages=3, page_images=imgs)) is True
    # real extracted text
    real = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10
    assert idp.needs_ocr(_doc(text=real, pages=3, page_images=imgs)) is False
    # no page images → never
    assert idp.needs_ocr(_doc(text="", pages=3, page_images=[])) is False
    # pages=0 treated as 1
    assert idp.needs_ocr(_doc(text="", pages=0, page_images=imgs[:1])) is True
    # already OCR'd → not re-queued
    assert idp.needs_ocr(_doc(text="", pages=3, page_images=imgs,
                              meta={"last_ocr_at": 123})) is False


# ── 6. signature page-render paths ──────────────────────────────────────────

def _write_pdf(path: Path, pages: int = 2) -> None:
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    for i in range(pages):
        page = pdf.new_page(width=200, height=200)
        page.insert_text((20, 40), f"page {i + 1}")
    pdf.save(str(path))
    pdf.close()


def test_original_pdf_path_uses_doc_filename(iso):
    from doc_agent_hooks import _original_pdf_path

    d = _doc("pdfA", filename="Contract Final.pdf")
    (iso / "pdfA").mkdir()
    assert _original_pdf_path(d) is None           # not stored yet
    _write_pdf(iso / "pdfA" / "Contract Final.pdf")
    assert _original_pdf_path(d) == iso / "pdfA" / "Contract Final.pdf"
    # a non-PDF doc never resolves
    assert _original_pdf_path(_doc("pdfA", filename="notes.txt", mime="text/plain")) is None


def test_page_render_paths_distinct_per_doc(iso, tmp_path):
    from doc_agent_hooks import build_hooks

    agent_tmp = tmp_path / "agent_tmp"
    agent_tmp.mkdir()
    docs = []
    for did, fname in (("docA", "alpha.pdf"), ("docB", "beta.pdf")):
        (iso / did).mkdir()
        _write_pdf(iso / did / fname, pages=2)
        stored = [str(iso / did / f"page_{i:04d}.png") for i in (1, 2)]
        docs.append(_doc(did, filename=fname, pages=2, page_images=stored))

    deps = MagicMock()
    deps.tmp_dir = MagicMock(return_value=agent_tmp)
    hooks = asyncio.run(build_hooks(deps))

    a = hooks.page_image_paths(docs[0], [1], 200)
    b = hooks.page_image_paths(docs[1], [1], 200)
    a2 = hooks.page_image_paths(docs[0], [1], 200)

    # re-rendered from the original upload (not the stored 120-dpi images)
    for paths, did in ((a, "docA"), (b, "docB"), (a2, "docA")):
        assert len(paths) == 1
        p = Path(paths[0])
        assert p.parent == agent_tmp and p.is_file()
        assert p.name.startswith(f"{did}_") and p.name.endswith("_p1_dpi200.png")
    assert len({a[0], b[0], a2[0]}) == 3            # no collisions across docs/runs


def test_page_render_falls_back_when_original_missing(iso, tmp_path):
    from doc_agent_hooks import build_hooks

    stored = [str(iso / "docC" / "page_0001.png")]
    d = _doc("docC", filename="gone.pdf", pages=1, page_images=stored)
    deps = MagicMock()
    deps.tmp_dir = MagicMock(return_value=tmp_path)
    hooks = asyncio.run(build_hooks(deps))
    assert hooks.page_image_paths(d, [1], 200) == stored


# ── 7. rank_signature_models probes concurrently ────────────────────────────

def test_rank_signature_models_probes_in_parallel():
    from doc_agent_hooks import rank_signature_models

    state = {"inflight": 0, "peak": 0}

    async def caps(name):
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.01)
        state["inflight"] -= 1
        return ["completion", "vision"] if "vl" in name or "vision" in name else ["completion"]

    deps = MagicMock()
    deps.model_capabilities = caps
    deps.name_is_vision = lambda n: False
    installed = ["qwen2.5vl:7b", "llama3.2-vision:11b", "minicpm-vl:8b", "qwen3:8b"]
    cfg = {"handwriting_model": "", "vision_model": "qwen2.5vl:7b"}
    result = asyncio.run(rank_signature_models(deps, cfg, installed, []))
    assert result[0] == "qwen2.5vl:7b"
    assert "llama3.2-vision:11b" in result and "qwen3:8b" not in result
    assert state["peak"] > 1
