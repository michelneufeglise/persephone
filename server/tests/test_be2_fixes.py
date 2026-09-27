"""
Regression tests for the backend review fixes (batch "be2"):

 1  long documents: per-doc budget, retrieval, head+tail / sampled excerpts
 2  document questions are not hijacked as graph_query; attachments persisted
 3  OCR runs for scans with a tiny text layer
 4  translate target language
 5  web clause stripped before intent; verify_signature needs a signature word
 6  web lookup needs an explicit request; fetch_page guard
 7  Laya lock / availability cache
 8  knowledge store hygiene (mentions, GC, delete_conversation, dangling edges)
 9  word-bounded rules; "name of the company"; graph subject + pronouns
10  history filter
11  cancellation does not yield
12  cached doc kind
 A–I live-test follow-ups (person extraction, pure web clause, multi-person
     ingest, profile hygiene, single-fact questions, wrapper stripping, graph
     citations, no-document reply, page_image_paths off the event loop)
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import doc_agent as _agent
import doc_web as _web
import kg_store as _kg
import laya_decider as _laya
from doc_agent_service import agent_sse, _select_history


# ── helpers ────────────────────────────────────────────────────────────────

def _doc(doc_id="d1", filename="doc.pdf", text="", page_images=None, pages=1, meta=None):
    return SimpleNamespace(
        id=doc_id, filename=filename, mime="application/pdf", size=1000,
        uploaded_at=0.0, pages=pages, text=text, page_texts=[text],
        page_images=page_images or [], meta=meta if meta is not None else {},
    )


class Recorder:
    def __init__(self):
        self.prompts: list[str] = []
        self.web_searches: list[str] = []
        self.ocr_calls: list[str] = []
        self.doc_kind_calls = 0
        self.retrieve_calls: list[tuple] = []
        self.page_image_threads: list[int] = []
        self.ingest_kwargs: list[dict] = []


def _hooks(docs, rec: Recorder, *, laya_intent=None, laya_web=None, answer="The answer.",
           llm_reply=None, retrieve=None, doc_kind=None, ocr_text="OCR TEXT", web_search=None,
           kg_search=None, kg_neighborhood=None, kg_ingest=None, page_paths=None):
    doc_map = {d.id: d for d in docs}

    async def stream_llm(model, prompt, think=False, **kw):
        rec.prompts.append(prompt)
        text = llm_reply(prompt) if llm_reply else answer
        if isinstance(text, list):
            for part in text:
                yield {"content": part}
        else:
            yield {"content": text}
        yield {"done": True}

    async def resolve_model(cat):
        return "m"

    async def resolve_text_model(doc, cat):
        return "text-model"

    async def vision_candidates():
        return ["vision-model"]

    async def model_info(name):
        return {"name": name}

    async def run_ocr(doc, model):
        rec.ocr_calls.append(doc.id)
        doc.text = ocr_text
        return ocr_text

    async def vision_call(model, prompt, refs, subs):
        return "Visual similarity: moderate"

    def page_image_paths(doc, pages, dpi):
        rec.page_image_threads.append(threading.get_ident())
        return page_paths or ["/tmp/x.png"]

    def laya_doc_kind(text):
        rec.doc_kind_calls += 1
        return doc_kind

    async def _web_search(query):
        rec.web_searches.append(query)
        return []

    h = _agent.AgentHooks(
        get_doc=lambda i: doc_map.get(i),
        laya_intent=lambda m, f: laya_intent,
        laya_role=lambda m, f: None,
        laya_doc_kind=laya_doc_kind,
        laya_info=lambda: {"available": False},
        page_image_paths=page_image_paths,
        resolve_model=resolve_model,
        resolve_text_model=resolve_text_model,
        pick_vision_model=resolve_model,
        vision_candidates=vision_candidates,
        model_info=model_info,
        run_ocr=run_ocr,
        stream_llm=stream_llm,
        vision_call=vision_call,
        mark_vision_failed=lambda m, e: None,
        laya_web=(lambda m: laya_web) if laya_web is not None else None,
        web_search=web_search if web_search is not None else _web_search,
        kg_search=kg_search,
        kg_neighborhood=kg_neighborhood,
        kg_ingest=kg_ingest,
        retrieve_chunks=retrieve,
        now_ms=lambda: 1000,
    )
    return h


async def _collect(gen):
    out = []
    async for e in gen:
        out.append(e)
    return out


def _run(req, hooks):
    return asyncio.run(_collect(_agent.run_agent(req, hooks)))


def _last_tile(events, tile_id):
    tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == tile_id]
    return tiles[-1] if tiles else None


def _decision(events, dec_id):
    laya = _last_tile(events, "laya")
    for d in laya["decisions"]:
        if d["id"] == dec_id:
            return d
    return None


ANSWER_SENTENCE = "The secret project code name is BLUE HERON 42."


def _long_text():
    filler = "Lorem ipsum dolor sit amet consectetur. "  # 40 chars
    text = "HEADER: Annual report of Example Corp.\n" + filler * 600  # ~24k
    text = text[:20000] + ANSWER_SENTENCE + " " + (filler * 300)
    text = text[:29900] + " END-OF-DOCUMENT-MARKER"
    return text


# ── 1. long documents ──────────────────────────────────────────────────────

def test_long_doc_prompt_uses_retrieved_chunk():
    text = _long_text()
    assert len(text) > 29000 and text.index(ANSWER_SENTENCE) >= 20000
    doc = _doc(text=text)
    rec = Recorder()

    async def retrieve(doc_id, query, k):
        rec.retrieve_calls.append((doc_id, query, k))
        return ["unrelated passage", ANSWER_SENTENCE]

    hooks = _hooks([doc], rec, retrieve=retrieve)
    events = _run({"message": "What is the code name of the project?", "attachments": [{"doc_id": "d1"}]}, hooks)
    prompt = rec.prompts[0]
    assert ANSWER_SENTENCE in prompt
    assert prompt.count("HEADER: Annual report") == 1  # head context kept
    assert rec.retrieve_calls and rec.retrieve_calls[0][0] == "d1"
    answer = _last_tile(events, "answer")
    assert "used excerpts" in answer["detail"]
    extract = _last_tile(events, "extract-d1")
    assert "excerpts used" in extract["detail"]
    assert f"{len(text):,}" in extract["detail"]


def test_long_doc_head_tail_fallback_without_retrieval():
    text = _long_text()
    doc = _doc(text=text)
    rec = Recorder()
    hooks = _hooks([doc], rec)  # no retrieve_chunks hook
    _run({"message": "What is the code name of the project?", "attachments": [{"doc_id": "d1"}]}, hooks)
    prompt = rec.prompts[0]
    assert "HEADER: Annual report" in prompt
    assert "END-OF-DOCUMENT-MARKER" in prompt  # tail included (not head-only)
    assert "[…]" in prompt


def test_long_doc_retrieval_failure_falls_back_to_head_tail():
    doc = _doc(text=_long_text())
    rec = Recorder()

    async def retrieve(doc_id, query, k):
        raise RuntimeError("embedding model not available")

    _run({"message": "What is the code name?", "attachments": [{"doc_id": "d1"}]}, _hooks([doc], rec, retrieve=retrieve))
    assert "END-OF-DOCUMENT-MARKER" in rec.prompts[0]


def test_budget_is_split_per_document_and_short_docs_are_full():
    a = _doc("a", "a.pdf", text="A" * 10000)
    b = _doc("b", "b.pdf", text="B" * 10000)
    prompt, excerpts = _agent._build_prompt_ex("general_question", [a, b], "q", [], max_chars=24000)
    assert excerpts == {}  # 10k each fits the 12k budget
    assert prompt.count("A") >= 10000 and prompt.count("B") >= 10000
    c = _doc("c", "c.pdf", text="C" * 15000)
    _, excerpts = _agent._build_prompt_ex("general_question", [a, c], "q", [], max_chars=24000)
    assert set(excerpts) == {"c"} and excerpts["c"]["mode"] == "head_tail"


def test_summarize_long_doc_uses_head_middle_tail_and_tells_user():
    text = "START " + "x" * 14000 + " MIDDLE-MARKER " + "y" * 14000 + " FINISH"
    doc = _doc(text=text)
    rec = Recorder()
    events = _run({"message": "summarize this", "attachments": [{"doc_id": "d1"}]}, _hooks([doc], rec))
    prompt = rec.prompts[0]
    assert "START" in prompt and "MIDDLE-MARKER" in prompt and "FINISH" in prompt
    answer = _last_tile(events, "answer")
    assert f"Document is long ({len(text):,} chars) — used excerpts" == answer["detail"]


# ── 2. graph_query hijack ──────────────────────────────────────────────────

def test_rules_everything_about_with_attachment_is_not_graph_query():
    files = [{"doc_id": "d1", "name": "invoice.pdf"}]
    intent, _ = _agent.rules_intent("Tell me everything about this invoice", files)
    assert intent != "graph_query"
    # Without attachments it's still a knowledge-store question
    assert _agent.rules_intent("Tell me everything about Jane Example", [])[0] == "graph_query"
    # A cross-scope marker makes it a graph query even with attachments
    assert _agent.rules_intent("what do we know about Jane across all documents", files)[0] == "graph_query"


class _FakeDb:
    def __init__(self):
        self.conversations, self.messages = {}, {}

    async def get_conversation(self, cid):
        if cid not in self.conversations:
            return None
        return {**self.conversations[cid], "messages": list(self.messages.get(cid, []))}

    async def upsert_conversation(self, data):
        self.conversations[data["id"]] = dict(data)

    async def upsert_message(self, cid, msg):
        self.messages.setdefault(cid, []).append(msg)


class _SvcHooks:
    def __init__(self, docs):
        self.docs = {d.id: d for d in docs}

    def get_doc(self, i):
        return self.docs.get(i)


def _svc(req, hooks, db, run_agent_impl=None):
    captured = {}

    async def fake_run_agent(r, h):
        captured.update(r)
        if run_agent_impl:
            async for e in run_agent_impl(r, h):
                yield e
        else:
            yield {"done": True}

    async def go():
        out = []
        with patch("doc_agent.run_agent", side_effect=fake_run_agent):
            async for chunk in agent_sse(req, hooks, db):
                out.append(chunk)
        return out

    return asyncio.run(go()), captured


def test_service_everything_about_invoice_keeps_attachments():
    db = _FakeDb()
    hooks = _SvcHooks([_doc("inv", "invoice.pdf")])
    events, captured = _svc(
        {"conversation_id": "dconv-1", "message": "Tell me everything about this invoice",
         "attachments": [{"doc_id": "inv"}]}, hooks, db)
    assert captured["attachments"] == [{"doc_id": "inv"}]
    user = [m for m in db.messages["dconv-1"] if m["role"] == "user"][-1]
    assert [a["doc_id"] for a in user["meta"]["attachments"]] == ["inv"]


def test_service_graph_query_never_carries_over_docs():
    db = _FakeDb()
    db.conversations["dconv-2"] = {"id": "dconv-2", "title": "t"}
    db.messages["dconv-2"] = [
        {"id": "u1", "role": "user", "content": "who is this?",
         "meta": {"kind": "doc_user", "attachments": [{"doc_id": "cv", "name": "cv.pdf"}]}},
        {"id": "a1", "role": "assistant", "content": "**Jane Example**", "meta": {}},
    ]
    hooks = _SvcHooks([_doc("cv", "cv.pdf")])
    events, captured = _svc(
        {"conversation_id": "dconv-2", "message": "what do we know about her across all documents",
         "attachments": []}, hooks, db)
    assert captured["attachments"] == []
    user = [m for m in db.messages["dconv-2"] if m["role"] == "user"][-1]
    assert user["meta"]["attachments"] == [] and user["meta"]["carried_over"] is False


def test_service_explicit_attachments_validated_for_graph_query():
    db = _FakeDb()
    hooks = _SvcHooks([])
    events, _ = _svc({"message": "what do we know about Jane across all documents",
                      "attachments": [{"doc_id": "missing"}]}, hooks, db)
    assert "Document not found: missing" in events[0]


# ── 3. OCR for tiny text layers ────────────────────────────────────────────

def test_tiny_text_layer_scan_is_ocrd():
    doc = _doc(text="Page 1", page_images=["/p1.png", "/p2.png"], pages=2)
    assert _agent._needs_ocr(doc)
    rec = Recorder()
    events = _run({"message": "summarize", "attachments": [{"doc_id": "d1"}]}, _hooks([doc], rec))
    assert rec.ocr_calls == ["d1"]
    tile = _last_tile(events, "extract-d1")
    assert tile["kind"] == "ocr" and tile["status"] == "done"
    note = _decision(events, "ocr_needed-d1")["note"]
    assert "too small" in note


def test_needs_ocr_local_rule_matches_spec():
    with patch.object(_agent, "_idp_needs_ocr", None):
        assert _agent._needs_ocr(_doc(text="x" * 79, page_images=["a", "b"], pages=2))
        assert not _agent._needs_ocr(_doc(text="x" * 80, page_images=["a", "b"], pages=2))
        assert not _agent._needs_ocr(_doc(text="", page_images=[], pages=2))


# ── 4. translate target language ───────────────────────────────────────────

@pytest.mark.parametrize("msg,lang", [
    ("Translate from Dutch to English", "English"),
    ("translate these pages into English", "English"),
    ("vertaal naar het Duits", "German"),
    ("traduis ce document en anglais", "English"),
    ("translate it into Spanish please", "Spanish"),
    ("translate this", "English"),
    ("Translate this Spanish letter to French", "French"),
])
def test_translate_target_language(msg, lang):
    assert _agent.translate_target_language(msg) == lang
    assert f"Target language: {lang}" in _agent._build_translate_prompt(msg)


# ── 5. web clause before intent; verify_signature needs a signature word ──

def test_verify_online_with_id_card_is_not_verify_signature():
    files = [{"doc_id": "d1", "name": "id_card.pdf"}]
    intent, _ = _agent.rules_intent("Verify online whether this person exists", files)
    assert intent != "verify_signature"
    # an explicit signature request still is
    assert _agent.rules_intent("compare the signature with the specimen", [{"name": "signature_card.pdf"}])[0] == "verify_signature"
    assert _agent.rules_intent("verify the handtekening", [{"name": "reference.pdf"}])[0] == "verify_signature"

    doc = _doc(filename="id_card.pdf", text="IDENTITY CARD\nName: Jane Example\nNationality: Dutch\n" * 3)
    rec = Recorder()
    events = _run({"message": "Verify online whether this person exists", "attachments": [{"doc_id": "d1"}]},
                  _hooks([doc], rec, answer="The document is about **Jane Example**."))
    assert _decision(events, "intent")["value"] != "verify_signature"
    assert _decision(events, "web_lookup")["value"] == "web"


def test_web_clause_stripped_before_laya_intent():
    seen = []
    doc = _doc(text="Curriculum vitae of Jane Example, engineer." * 3)
    rec = Recorder()
    hooks = _hooks([doc], rec)
    hooks.laya_intent = lambda m, f: seen.append(m) or None
    _run({"message": "who is this document about and check linkedin if this person really exists",
          "attachments": [{"doc_id": "d1"}]}, hooks)
    assert seen and "linkedin" not in seen[0].lower()


# ── 6. web lookup privacy / fetch guard ────────────────────────────────────

def test_laya_only_yes_never_searches():
    doc = _doc(text="Jane Example, Software Engineer at Acme." * 3)
    rec = Recorder()
    events = _run({"message": "who is this document about?", "attachments": [{"doc_id": "d1"}]},
                  _hooks([doc], rec, laya_web={"value": "yes", "confidence": 0.99},
                         answer="This document is about **Jane Example**."))
    assert rec.web_searches == []
    dec = _decision(events, "web_lookup")
    assert dec["value"] == "none"
    assert "not run without an explicit request" in dec["note"]
    assert _last_tile(events, "web-search") is None


def test_fetch_guard_refusals():
    allowed = {"https://example.com/jane", "http://127.0.0.1:8000/", "http://10.0.0.5/"}

    async def go():
        return [
            await _web.check_fetch_url("https://evil.example/other", allowed),
            await _web.check_fetch_url("http://127.0.0.1:8000/", allowed),
            await _web.check_fetch_url("file:///etc/passwd", allowed | {"file:///etc/passwd"}),
            await _web.check_fetch_url("http://10.0.0.5/", allowed),
            await _web.check_fetch_url("http://localhost:5173/", {"http://localhost:5173/"}),
            await _web.check_fetch_url("http://printer.local/", {"http://printer.local/"}),
            await _web.check_fetch_url("http://[::1]/", {"http://[::1]/"}),
            await _web.check_fetch_url("http://169.254.169.254/latest", {"http://169.254.169.254/latest"}),
        ]

    r = asyncio.run(go())
    assert r[0] == "Fetch refused: URL was not returned by the search"
    assert "private address" in r[1]
    assert r[2].startswith("Fetch refused") and "http" in r[2]
    assert "private address" in r[3]
    assert all("private address" in x for x in r[4:])


def test_fetch_guard_dns_resolution():
    def fake_gai(host, port, *a, **k):
        ip = {"public.example": "93.184.216.34", "sneaky.example": "192.168.1.10"}[host]
        return [(2, 1, 6, "", (ip, port))]

    async def go():
        with patch("socket.getaddrinfo", side_effect=fake_gai):
            ok = await _web.check_fetch_url("https://public.example/page", {"https://public.example/page"})
            bad = await _web.check_fetch_url("https://sneaky.example/x", {"https://sneaky.example/x"})
        return ok, bad

    ok, bad = asyncio.run(go())
    assert ok is None
    assert "private address" in bad


def test_tool_path_refuses_unsearched_fetch():
    fetched = []
    calls = {"n": 0}

    async def chat_tools(model, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "tool_calls": [{"name": "fetch_page", "arguments": {"url": "http://127.0.0.1:8000/api/idp/documents"}}]}
        return {"content": "done", "tool_calls": []}

    async def fetch_page(url):
        fetched.append(url)
        return "secret"

    async def web_search(q):
        return []

    async def stream_llm(model, prompt, think=False, **kw):
        yield {"content": "verdict"}

    hooks = SimpleNamespace(chat_tools=chat_tools, fetch_page=fetch_page, web_search=web_search, stream_llm=stream_llm)
    tile = _agent.Tile(id="web-search", kind="web", title="Web", status="running", started_ms=0)

    async def go():
        return await _collect(_web._tool_path("tool", ["q"], {"name": "Jane Example"}, ["web"], tile, hooks, answer_model="m"))

    asyncio.run(go())
    assert fetched == []
    assert any("URL was not returned by the search" in it.get("label", "") for it in tile.items)


# ── 7. Laya thread safety ──────────────────────────────────────────────────

def test_laya_predict_runs_under_reentrant_lock():
    owned = []

    class FakeRouter:
        def predict(self, state, questions):
            owned.append(_laya._router_lock._is_owned())
            return {"answers": {"q": {"choice": "a", "answer_confidence": 0.9, "probabilities": {}}}}

    old = _laya._router_instance
    try:
        _laya._router_instance = FakeRouter()
        with patch.object(_laya, "_reset_idle_timer", lambda: None):
            res = _laya.judge_choice("text", "q", {"a": "A", "b": "B"})
        assert res["choice"] == "a"
        assert owned == [True]
        assert type(_laya._router_lock) is type(threading.RLock())
    finally:
        _laya._router_instance = old


def test_laya_is_available_cached_once_true():
    old = _laya._available_cached
    try:
        _laya._available_cached = True
        with patch.dict(sys.modules, {"laya": SimpleNamespace()}), \
             patch("huggingface_hub.scan_cache_dir", side_effect=AssertionError("scanned")):
            assert _laya.is_available() is True
    finally:
        _laya._available_cached = old


def test_laya_info_called_once_per_run():
    doc = _doc(text="Some document text long enough for things." * 3)
    rec = Recorder()
    hooks = _hooks([doc], rec)
    n = {"c": 0}

    def info():
        n["c"] += 1
        return {"available": False}

    hooks.laya_info = info
    _run({"message": "summarize", "attachments": [{"doc_id": "d1"}]}, hooks)
    assert n["c"] == 1


# ── 8. knowledge store hygiene ─────────────────────────────────────────────

def _kg_run(coro_fn):
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            return await coro_fn()
        finally:
            await _kg.reset()
    return asyncio.run(go())


def test_mentions_deduped_across_runs_and_counts_new_rows():
    doc = SimpleNamespace(id="doc-m", filename="cv.pdf", mime="application/pdf", text="Jane Example, Engineer")

    async def body():
        kw = dict(intent="identify_person", subject_docs=[doc], answer_text="",
                  person={"name": "Jane Example", "role": "Engineer", "org": "Acme"})
        c1 = await _kg.ingest_run(conversation_id="c1", run_id="run-1", **kw)
        c2 = await _kg.ingest_run(conversation_id="c1", run_id="run-2", **kw)
        conn = _kg._connect()
        try:
            rows = conn.execute("SELECT run_id FROM kg_mentions WHERE doc_id='doc-m'").fetchall()
        finally:
            conn.close()
        return c1, c2, [r["run_id"] for r in rows]

    c1, c2, runs = _kg_run(body)
    assert c1["entities"] == 4 and c1["mentions"] == 1 and c1["relations"] == 4
    assert c2["entities"] == 0 and c2["mentions"] == 0 and c2["relations"] == 0
    assert runs == ["run-2"]


def test_init_migrates_duplicate_mentions():
    async def body():
        conn = _kg._connect()
        try:
            conn.execute("DROP INDEX IF EXISTS kg_ment_ent_doc")
            conn.execute("DROP TABLE kg_mentions")
            conn.execute(
                "CREATE TABLE kg_mentions (id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, doc_id TEXT NOT NULL, "
                "chunk_id INTEGER, snippet TEXT, run_id TEXT, conversation_id TEXT, created_at REAL NOT NULL, "
                "UNIQUE(entity_id, doc_id, run_id))"
            )
            for r in ("r1", "r2", "r3"):
                conn.execute("INSERT INTO kg_mentions VALUES (?, 'person:x', 'd', NULL, NULL, ?, 'c', 0)", (f"m{r}", r))
        finally:
            conn.close()
        await _kg.init_db()
        conn = _kg._connect()
        try:
            return conn.execute("SELECT run_id FROM kg_mentions").fetchall()
        finally:
            conn.close()

    rows = _kg_run(body)
    assert [r["run_id"] for r in rows] == ["r3"]


def test_delete_document_gcs_orphans():
    doc = SimpleNamespace(id="doc-gc", filename="cv.pdf", mime="application/pdf", text="Jane Example")
    keep = SimpleNamespace(id="doc-keep", filename="other.pdf", mime="application/pdf", text="Bob Builder")

    async def body():
        await _kg.ingest_run(
            conversation_id="c", run_id="r", intent="identify_person", subject_docs=[doc], answer_text="",
            person={"name": "Jane Example", "role": "Engineer", "org": "Acme"},
            web_candidates=[{"url": "https://www.linkedin.com/in/jane-example", "title": "Jane Example | LinkedIn"}],
            verdict="",
        )
        await _kg.ingest_run(conversation_id="c", run_id="r2", intent="identify_person", subject_docs=[keep],
                             answer_text="", person={"name": "Bob Builder"})
        await _kg.delete_document("doc-gc")
        return await _kg.get_graph("all")

    g = _kg_run(body)
    types = {e["type"] for e in g["entities"]}
    names = {e["name"] for e in g["entities"]}
    assert "Jane Example" not in names and "Engineer" not in names and "Acme" not in names
    assert "profile" not in types
    assert names == {"other.pdf", "Bob Builder"}
    ids = {e["id"] for e in g["entities"]}
    assert all(r["src"] in ids and r["dst"] in ids for r in g["relations"])


def test_delete_conversation_removes_its_knowledge():
    d1 = SimpleNamespace(id="doc-a", filename="a.pdf", mime="application/pdf", text="Jane Example")
    d2 = SimpleNamespace(id="doc-b", filename="b.pdf", mime="application/pdf", text="Bob Builder")

    async def body():
        await _kg.ingest_run(conversation_id="dconv-a", run_id="r1", intent="identify_person",
                             subject_docs=[d1], answer_text="", person={"name": "Jane Example", "role": "Engineer"})
        await _kg.ingest_run(conversation_id="dconv-b", run_id="r2", intent="identify_person",
                             subject_docs=[d2], answer_text="", person={"name": "Bob Builder"})
        await _kg.delete_conversation("dconv-a")
        return await _kg.get_graph("all")

    g = _kg_run(body)
    names = {e["name"] for e in g["entities"]}
    assert "Jane Example" not in names and "Engineer" not in names
    assert {"Bob Builder", "a.pdf", "b.pdf"} <= names  # documents are kept


def test_get_graph_all_has_no_dangling_relations():
    async def body():
        p = await _kg.upsert_entity("person", "Jane Example")
        await _kg.add_mention(p, "doc-x", run_id="r")
        conn = _kg._connect()
        try:
            conn.execute(
                "INSERT INTO kg_relations (id, src_id, dst_id, type, source, created_at) "
                "VALUES ('dangling', ?, 'organization:ghost', 'works_at', 't', 0)", (p,)
            )
        finally:
            conn.close()
        return await _kg.get_graph("all")

    g = _kg_run(body)
    ids = {e["id"] for e in g["entities"]}
    assert all(r["src"] in ids and r["dst"] in ids for r in g["relations"])
    assert g["stats"]["relations"] == len(g["relations"]) == 0


# ── D. profile hygiene + doc kind ──────────────────────────────────────────

def test_profile_ingest_hygiene_and_canonical_keys():
    doc = SimpleNamespace(id="doc-p", filename="Resume.DOCX", mime="application/octet-stream", text="Jane Example")
    cands = [
        {"url": "ref://duckduckgo/abc", "title": "Jane Example"},
        {"url": "https://www.linkedin.com/in/someone-else", "title": "Someone Else | LinkedIn"},
        {"url": "https://nl.linkedin.com/in/jane-example/?trk=x", "title": "Jane Example - LinkedIn"},
        {"url": "https://www.linkedin.com/in/jane-example", "title": "Jane Example | LinkedIn"},
    ]
    verdict = "**LinkedIn:** Likely match found — [Jane](https://www.linkedin.com/in/jane-example)"

    async def body():
        await _kg.ingest_run(conversation_id="c", run_id="r", intent="identify_person", subject_docs=[doc],
                             answer_text="", person={"name": "Jane Example"}, web_candidates=cands, verdict=verdict)
        # a later run where the same profile is only a candidate must not add a second edge
        await _kg.ingest_run(conversation_id="c", run_id="r2", intent="identify_person", subject_docs=[doc],
                             answer_text="", person={"name": "Jane Example"},
                             web_candidates=[cands[3]], verdict="")
        return await _kg.get_graph("all")

    g = _kg_run(body)
    profiles = [e for e in g["entities"] if e["type"] == "profile"]
    assert [p["id"] for p in profiles] == ["profile:linkedin.com/in/jane-example"]
    rels = [r for r in g["relations"] if r["dst"] == profiles[0]["id"]]
    assert [r["type"] for r in rels] == ["likely_profile"]
    docs = [e for e in g["entities"] if e["type"] == "document"]
    assert docs[0]["props"]["kind"] == "docx"


def test_canonical_url():
    c = _kg._canonical_url
    assert c("https://nl.linkedin.com/in/Jane-Example/?trk=abc#x") == "linkedin.com/in/jane-example"
    assert c("https://www.example.com/a/") == "example.com/a"
    assert c("https://m.facebook.com/profile.php?id=123&ref=x") == "facebook.com/profile.php?id=123"


# ── 9. word-bounded rules, company names, graph subject ────────────────────

def test_page_is_not_age():
    intent, kws = _agent.rules_intent("What is on page 2?", [{"name": "a.pdf"}])
    assert "age" not in kws
    assert intent != "general_question" or "age" not in kws


def test_name_of_the_company_is_not_identify_person():
    for msg in ["What is the name of the company?", "what's the company name", "naam van het bedrijf?"]:
        assert _agent.rules_intent(msg, [{"name": "a.pdf"}])[0] != "identify_person", msg
    assert _agent.rules_intent("What is the name of the person?", [])[0] == "identify_person"


def test_graph_subject_word_boundaries_and_pronouns():
    assert _agent.graph_subject("which documents mention Jonathan Doe") == "Jonathan Doe"
    assert _agent.graph_subject("what do we know about Simon Dijkstra across my documents") == "Simon Dijkstra"
    history = [
        {"role": "user", "content": "who is this?"},
        {"role": "assistant", "content": "This document is about **Jane Example**."},
    ]
    assert _agent.graph_subject("what do we know about her", history) == "Jane Example"
    assert _agent.graph_subject("wat weten we over hem", [
        {"role": "assistant", "content": "**Name:** Piet de Vries"}]) == "Piet de Vries"


def test_graph_query_pronoun_searches_resolved_person():
    searched = []

    async def kg_search(text):
        searched.append(text)
        return []

    rec = Recorder()
    history = [{"role": "assistant", "content": "The document is about:\n\n*   **Jane Example** (holder)"}]
    _run({"message": "what do we know about her", "attachments": [], "history": history},
         _hooks([], rec, kg_search=kg_search))
    assert searched == ["Jane Example"]


# ── E. single-fact questions ───────────────────────────────────────────────

def test_single_fact_questions_are_general_questions():
    files = [{"name": "invoice.pdf"}]
    for msg in ["What is the total amount?", "How much is the fee?", "When is the due date?",
                "what is the invoice number?"]:
        assert _agent.rules_intent(msg, files)[0] == "general_question", msg
    for msg in ["extract all amounts", "list all fields", "Give me a table of the line items", "extract table"]:
        assert _agent.rules_intent(msg, files)[0] == "extract_data", msg


# ── 10. history filter ─────────────────────────────────────────────────────

def test_history_excludes_errors_and_graph_turns_includes_subset_docs():
    msgs = [
        {"role": "user", "content": "q-a", "meta": {"kind": "doc_user", "attachments": [{"doc_id": "a"}]}},
        {"role": "assistant", "content": "answer-a", "meta": {}},
        {"role": "user", "content": "q-err", "meta": {"kind": "doc_user", "attachments": [{"doc_id": "a"}]}},
        {"role": "assistant", "content": "⚠ LLM failed", "meta": {"error": "LLM failed"}},
        {"role": "user", "content": "q-cancel", "meta": {"kind": "doc_user", "attachments": [{"doc_id": "a"}]}},
        {"role": "assistant", "content": "partial", "meta": {"cancelled": True}},
        {"role": "user", "content": "q-graph", "meta": {"kind": "doc_user", "attachments": []}},
        {"role": "assistant", "content": "graph-answer", "meta": {"intent": "graph_query"}},
        {"role": "user", "content": "q-other", "meta": {"kind": "doc_user", "attachments": [{"doc_id": "z"}]}},
        {"role": "assistant", "content": "answer-z", "meta": {}},
    ]
    hist = [h["content"] for h in _select_history(msgs, {"a", "b"})]
    assert "answer-a" in hist and "q-a" in hist  # {a} ⊆ {a, b}
    assert "⚠ LLM failed" not in hist and "partial" not in hist
    assert "graph-answer" not in hist and "q-graph" not in hist
    assert "answer-z" not in hist
    graph_hist = [h["content"] for h in _select_history(msgs, set(), graph_request=True)]
    assert "graph-answer" in graph_hist and "⚠ LLM failed" not in graph_hist


# ── 11. cancellation ───────────────────────────────────────────────────────

def test_cancelled_error_path_does_not_yield():
    doc = _doc(text="Some document text that is long enough." * 3)
    rec = Recorder()
    hooks = _hooks([doc], rec)

    async def cancelling_stream(model, prompt, think=False, **kw):
        raise asyncio.CancelledError()
        yield  # pragma: no cover

    hooks.stream_llm = cancelling_stream

    async def go():
        events = []
        gen = _agent.run_agent({"message": "summarize", "attachments": [{"doc_id": "d1"}]}, hooks)
        with pytest.raises(asyncio.CancelledError):
            async for e in gen:
                events.append(e)
        return events

    events = asyncio.run(go())
    assert not any(e.get("tile", {}).get("detail") == "Cancelled" for e in events)


# ── 12. cached doc kind ────────────────────────────────────────────────────

def test_doc_kind_uses_route_cache_and_caches_result():
    text = "INVOICE 2024-001 total due 100 EUR. " * 5
    cached = _doc("c", "inv.pdf", text=text,
                  meta={"route": {"decision": {"kind": "invoice_or_form", "confidence": 0.9}}})
    rec = Recorder()
    events = _run({"message": "summarize", "attachments": [{"doc_id": "c"}]},
                  _hooks([cached], rec, doc_kind={"kind": "email", "confidence": 0.99}))
    assert rec.doc_kind_calls == 0
    assert _decision(events, "doc_kind-c")["value"] == "invoice_or_form"

    fresh = _doc("f", "inv.pdf", text=text)
    rec2 = Recorder()
    _run({"message": "summarize", "attachments": [{"doc_id": "f"}]},
         _hooks([fresh], rec2, doc_kind={"kind": "email", "confidence": 0.99}))
    assert rec2.doc_kind_calls == 1
    assert fresh.meta["doc_kind"]["kind"] == "email"
    _run({"message": "summarize", "attachments": [{"doc_id": "f"}]},
         _hooks([fresh], rec2, doc_kind={"kind": "email", "confidence": 0.99}))
    assert rec2.doc_kind_calls == 1  # cached on the doc


# ── A. person extraction (gemma formats + document fallback) ───────────────

def test_extract_person_gemma_formats():
    p = _web.extract_person("The document is about:\n\n*   **Michel Neuféglise** (holder)", [])
    assert p and p["name"] == "Michel Neuféglise"
    p = _web.extract_person("*   **Name:** Michel Neuféglise\n*   **Role:** holder", [])
    assert p and p["name"] == "Michel Neuféglise" and p["role"] is None
    assert _web.extract_person("**Key Points**\n- nothing", []) is None


def test_web_lookup_falls_back_to_document_person_extraction():
    doc = _doc(filename="cv.pdf", text="CURRICULUM VITAE\nJane Example\nSoftware Engineer at Acme\n" * 3)
    rec = Recorder()

    def reply(prompt):
        if "ONLY one JSON object" in prompt:
            return 'Sure: {"name": "Jane Example", "role": "Software Engineer", "org": "Acme"}'
        return "The document describes a professional profile."

    events = _run({"message": "who is this and check linkedin if this person exists",
                   "attachments": [{"doc_id": "d1"}]}, _hooks([doc], rec, llm_reply=reply))
    plan = _last_tile(events, "web-plan")
    assert plan["status"] != "error"
    assert "Jane Example" in plan["detail"]
    assert rec.web_searches  # the lookup actually ran


def test_parse_person_json():
    assert _web.parse_person_json('x {"name": "Anna de Vries", "role": "", "org": "Rabobank"} y') == \
        {"name": "Anna de Vries", "role": None, "org": "Rabobank"}
    assert _web.parse_person_json("no json") is None
    assert _web.parse_person_json('{"name": ""}') is None


# ── B. pure web clause ─────────────────────────────────────────────────────

def test_pure_web_clause_asks_who_the_document_is_about():
    assert _web.strip_web_clause_or_empty("search the web for this person") == ""
    assert _web.strip_web_clause_or_empty("look this person up online") == ""
    doc = _doc(text="Jane Example, Engineer at Acme." * 3)
    rec = Recorder()
    _run({"message": "search the web for this person", "attachments": [{"doc_id": "d1"}]},
         _hooks([doc], rec, answer="This document is about **Jane Example**."))
    prompt = rec.prompts[0]
    assert prompt.rstrip().endswith("USER REQUEST:\nWho is this document about?")
    assert _agent.ONLINE_LOOKUP_NOTE in prompt


# ── C. multi-person identify_person ingest ─────────────────────────────────

def test_multi_person_identify_ingests_each_with_its_document():
    contract = _doc("k", "contract.pdf", text="Contract signed by Jane Example on 1 May." * 2)
    invoice = _doc("i", "invoice.pdf", text="Invoice addressed to Anna de Vries, Utrecht." * 2)
    rec = Recorder()

    async def ingest(**kw):
        rec.ingest_kwargs.append(kw)
        return {}

    _run({"message": "who are these documents about?", "attachments": [{"doc_id": "k"}, {"doc_id": "i"}]},
         _hooks([contract, invoice], rec, kg_ingest=ingest,
                answer="Jane Example (contract), Anna de Vries (invoice)"))
    persons = rec.ingest_kwargs[0]["persons"]
    assert {(p["name"], p.get("doc_id")) for p in persons} == {("Jane Example", "k"), ("Anna de Vries", "i")}


def test_ingest_run_persons_tied_to_documents():
    k = SimpleNamespace(id="k", filename="contract.pdf", mime="application/pdf", text="Jane Example")
    i = SimpleNamespace(id="i", filename="invoice.pdf", mime="application/pdf", text="Anna de Vries")

    async def body():
        await _kg.ingest_run(conversation_id="c", run_id="r", intent="identify_person", subject_docs=[k, i],
                             answer_text="", persons=[{"name": "Jane Example", "doc_id": "k"},
                                                      {"name": "Anna de Vries", "doc_id": "i"}])
        return await _kg.get_graph("all")

    g = _kg_run(body)
    mentioned = {(r["src"], r["dst"]) for r in g["relations"] if r["type"] == "mentioned_in"}
    assert mentioned == {("person:jane example", "document:k"), ("person:anna de vries", "document:i")}


# ── F. document wrapper ────────────────────────────────────────────────────

def test_translate_output_strips_echoed_wrapper():
    doc = _doc(filename="brief.pdf", text="Beste Jan, dit is een brief." * 3)
    rec = Recorder()
    events = _run({"message": "translate to English", "attachments": [{"doc_id": "d1"}]},
                  _hooks([doc], rec, answer=["=== brief", ".pdf ===\n", "Dear Jan, this is a letter."]))
    content = "".join(e["content"] for e in events if "content" in e)
    assert content == "Dear Jan, this is a letter."
    assert '<document name="brief.pdf">' in rec.prompts[0]
    assert "=== brief.pdf ===" not in rec.prompts[0]
    assert _agent.strip_leading_wrapper('<document name="x">\nHallo') == "Hallo"


# ── G. graph citations ─────────────────────────────────────────────────────

def test_graph_query_facts_cite_documents_and_hosts():
    ents = [
        {"id": "person:jane example", "type": "person", "name": "Jane Example", "props": {}},
        {"id": "role:engineer", "type": "role", "name": "Engineer", "props": {}},
        {"id": "document:d", "type": "document", "name": "cv.pdf", "props": {"filename": "cv.pdf"}},
        {"id": "profile:linkedin.com/in/jane", "type": "profile", "name": "Jane | LinkedIn",
         "props": {"url": "https://www.linkedin.com/in/jane", "host": "www.linkedin.com"}},
    ]
    rels = [
        {"src": "person:jane example", "dst": "role:engineer", "type": "has_role", "confidence": 0.7, "source": "doc_agent"},
        {"src": "person:jane example", "dst": "document:d", "type": "mentioned_in", "confidence": 0.9, "source": "doc_agent"},
        {"src": "person:jane example", "dst": "profile:linkedin.com/in/jane", "type": "likely_profile",
         "confidence": 0.8, "source": "web_lookup"},
    ]

    async def kg_search(t):
        return [ents[0]]

    async def kg_neighborhood(eid):
        return {"entities": ents, "relations": rels}

    rec = Recorder()
    hooks = _hooks([], rec, kg_search=kg_search, kg_neighborhood=kg_neighborhood)

    async def rtmi(doc, cat):
        return {"model": "m"}

    hooks.resolve_text_model_info = rtmi
    _run({"message": "what do we know about Jane Example", "attachments": []}, hooks)
    facts = rec.prompts[0].split("Facts:")[1]
    assert "doc_agent" not in facts and "web_lookup" not in facts
    assert "(source: cv.pdf)" in facts
    assert "(source: linkedin.com)" in facts


# ── H. no documents → friendly reply ───────────────────────────────────────

def test_hello_without_documents_gets_friendly_reply_without_persistence():
    db = _FakeDb()
    events, captured = _svc({"message": "hello", "attachments": []}, _SvcHooks([]), db)
    payloads = [json.loads(e[6:]) for e in events if e.startswith("data: {")]
    assert not any("error" in p for p in payloads)
    assert "Attach or select a document" in payloads[0]["content"]
    assert payloads[1]["done"] is True
    assert events[-1] == "data: [DONE]\n\n"
    assert db.conversations == {} and db.messages == {}
    assert captured == {}  # the agent never ran


# ── I. page_image_paths off the event loop ─────────────────────────────────

def test_page_image_paths_runs_in_worker_thread():
    subj = _doc("s", "contract.pdf", text="", page_images=["/p1.png"], pages=1)
    ref = _doc("r", "signature_card.pdf", text="", page_images=["/r1.png"], pages=1)
    rec = Recorder()
    hooks = _hooks([subj, ref], rec)
    loop_thread = {}

    async def go():
        loop_thread["id"] = threading.get_ident()
        return await _collect(_agent.run_agent(
            {"message": "compare the signature with the specimen",
             "attachments": [{"doc_id": "s", "role": "subject"}, {"doc_id": "r", "role": "reference"}]}, hooks))

    asyncio.run(go())
    assert rec.page_image_threads
    assert all(t != loop_thread["id"] for t in rec.page_image_threads)
