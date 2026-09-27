"""
Regression tests for the live end-to-end follow-ups (batch "be3"):

 1  blocked DuckDuckGo search → "search unavailable", never "No match found";
    retry + Brave fallback; answer-derived generic roles kept out of queries
 2  empty / garbled / errored answers under GPU memory pressure → one retry,
    then an error (no silent empty "done")
 3  page-aware prompts ("--- Page N ---" markers)
 4  X / Twitter platform detection (word-bounded, no false positives)
 5  knowledge-graph hygiene (organisations as persons, headline parsing,
    migration of ref:// profiles and junk roles, graph facts filter)
 6  cached OCR text label; non-http verdict candidates skipped
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])  # reuse the be2 test helpers

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

import doc_agent as _agent
import doc_web as _web
import idp_engine as _idp
import kg_store as _kg
from test_be2_fixes import Recorder, _hooks, _run, _last_tile, _doc, _kg_run


DDG_NOTICE = (
    "No results were found for your search query. This could be due to DuckDuckGo's bot "
    "detection or the query returned no matches. Please try rephrasing your search or try again "
    "in a few minutes. If this persists, DuckDuckGo may be blocking this server's TLS fingerprint."
)
DDG_RESULTS = (
    "1. Michel Neuféglise - Solution Architect - Rabobank | LinkedIn\n"
    "   URL: https://nl.linkedin.com/in/michelneufeglise\n"
    "   Summary: Solution Architect at Rabobank\n"
)


def _collect(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


def _content(events):
    return "".join(e["content"] for e in events if "content" in e)


# ── 1. blocked search ──────────────────────────────────────────────────────

def test_blocked_notice_detection():
    assert _web.is_blocked_notice(DDG_NOTICE)
    assert _web.is_blocked_notice("No results were found for your search query (DuckDuckGo).")
    assert not _web.is_blocked_notice(DDG_RESULTS)
    assert not _web.is_blocked_notice("")
    assert not _web.is_blocked_notice(None)


class _FakeServers:
    def __init__(self, replies: dict, running=("duckduckgo-search",)):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls: list[tuple] = []
        self.sleeps: list[float] = []
        self.running = list(running)

    async def call(self, sid, query):
        self.calls.append((sid, query))
        seq = self.replies[sid]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def sleep(self, s):
        self.sleeps.append(s)

    def searcher(self):
        return _web.WebSearcher(
            call=self.call, running=lambda: self.running,
            parse=__import__("research")._parse_search_results, sleep=self.sleep,
        )


def test_searcher_retries_once_then_reports_blocked():
    fs = _FakeServers({"duckduckgo-search": [DDG_NOTICE]})
    s = fs.searcher()
    with pytest.raises(_web.SearchBlocked):
        asyncio.run(s("q1"))
    assert fs.sleeps == [3.0]
    assert len(fs.calls) == 2
    # second query in the same run: no second backoff
    with pytest.raises(_web.SearchBlocked):
        asyncio.run(s("q2"))
    assert fs.sleeps == [3.0]
    assert s.blocked_queries == 2


def test_searcher_retry_succeeds():
    fs = _FakeServers({"duckduckgo-search": [DDG_NOTICE, DDG_RESULTS]})
    res = asyncio.run(fs.searcher()("q"))
    assert res and res[0]["url"] == "https://nl.linkedin.com/in/michelneufeglise"
    assert fs.sleeps == [3.0]


def test_searcher_falls_back_to_brave():
    fs = _FakeServers(
        {"duckduckgo-search": [DDG_NOTICE], "brave-search": [DDG_RESULTS]},
        running=("duckduckgo-search", "brave-search"),
    )
    res = asyncio.run(fs.searcher()("q"))
    assert res and "linkedin.com/in/michelneufeglise" in res[0]["url"]
    assert [c[0] for c in fs.calls] == ["duckduckgo-search", "duckduckgo-search", "brave-search"]


def test_searcher_no_server_running():
    fs = _FakeServers({"duckduckgo-search": [DDG_RESULTS]}, running=())
    with pytest.raises(_web.WebSearchUnavailable):
        asyncio.run(fs.searcher()("q"))


def _lookup_hooks(web_search, *, tool_model=None, chat_replies=None, verdict="**LinkedIn:** No match found"):
    replies = list(chat_replies or [])

    async def pick_tool_model():
        return {"model": tool_model, "source": "auto"} if tool_model else None

    async def chat_tools(model, messages, tools):
        return replies.pop(0) if replies else {"content": "", "tool_calls": []}

    async def stream_llm(model, prompt, think=False, **kw):
        yield {"content": verdict}
        yield {"done": True}

    async def fetch_page(url):
        return ""

    return SimpleNamespace(
        pick_tool_model=pick_tool_model, chat_tools=chat_tools, web_search=web_search,
        fetch_page=fetch_page, stream_llm=stream_llm,
    )


async def _blocked_search(query):
    raise _web.SearchBlocked(DDG_NOTICE)


PERSON = {"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"}


def _assert_unavailable(events, targets=("LinkedIn",)):
    text = _content(events)
    assert "No match found" not in text
    for label in targets:
        assert f"**{label}:** Search unavailable — couldn't verify" in text
    tile = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "web-search"][-1]
    assert tile["status"] == "error"
    assert "temporarily blocked by DuckDuckGo" in tile["detail"]
    web_result = [e["_web_result"] for e in events if "_web_result" in e][0]
    assert web_result["candidates"] == [] and web_result["verdict"] == ""


def test_blocked_search_fallback_path_is_unavailable_not_no_match():
    hooks = _lookup_hooks(_blocked_search)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin", "facebook"], hooks, answer_model="m", now_ms=0))
    _assert_unavailable(events, ("LinkedIn", "Facebook"))


def test_blocked_search_tool_path_is_unavailable_not_no_match():
    replies = [
        {"content": "", "tool_calls": [{"name": "web_search", "arguments": {"query": 'site:linkedin.com "Michel Neuféglise"'}}]},
        {"content": "done", "tool_calls": []},
    ]
    hooks = _lookup_hooks(_blocked_search, tool_model="qwen3:4b", chat_replies=replies)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    _assert_unavailable(events)


def test_partial_block_with_real_results_still_gets_verdict():
    n = {"i": 0}

    async def web_search(q):
        n["i"] += 1
        if n["i"] == 1:
            raise _web.SearchBlocked(DDG_NOTICE)
        return [{"url": "https://example.com/other", "title": "Someone else", "snippet": "x"}]

    hooks = _lookup_hooks(web_search)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "Search unavailable" not in text
    tile = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "web-search"][-1]
    assert tile["status"] == "done"


def test_blocked_search_in_full_agent_run():
    doc = _doc(text="Michel Neuféglise\nSolution Architect at Rabobank\nAmsterdam")
    rec = Recorder()
    hooks = _hooks([doc], rec, answer="**Name:** Michel Neuféglise\n**Role:** Solution Architect",
                   web_search=_blocked_search)
    events = _run({
        "message": "who is this document about, and check LinkedIn whether this person really exists",
        "attachments": [{"doc_id": "d1"}],
    }, hooks)
    assert _last_tile(events, "answer")["status"] == "done"
    assert _last_tile(events, "web-search")["status"] == "error"
    text = _content(events)
    assert "Search unavailable — couldn't verify" in text
    assert "No match found" not in text


@pytest.mark.parametrize("role", [
    "the individual whose CV is presented", "CV holder", "Holder", "the person in the document",
    "subject of this CV", "the candidate", "the author",
    "a very long description of what this person does in the document that goes on",
])
def test_generic_roles_rejected(role):
    assert _web._is_generic_role(role)


@pytest.mark.parametrize("role", [
    "Solution Architect", "Document Controller", "Subject Matter Expert", "Shareholder",
    "Stakeholder Manager", "Senior Software Engineer",
])
def test_real_roles_kept(role):
    assert not _web._is_generic_role(role)


def test_generic_role_never_in_queries():
    qs = _web.build_queries({"name": "Michel Neuféglise", "role": "the individual whose CV is presented",
                             "org": None}, ["linkedin"])
    assert all("individual" not in q for q in qs)
    qs = _web.build_queries({"name": "Temp Person", "role": None, "org": "Temp Corp as Temp Engineer."}, ["web"])
    assert all(" as " not in q for q in qs)


# ── 2. empty / errored answers ─────────────────────────────────────────────

@pytest.fixture
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(_agent, "ANSWER_RETRY_DELAY_S", 0)


def _seq_hooks(replies, rec, ingest_calls=None):
    """stream_llm returns replies[i] on call i (a str, a list of parts, or an Exception)."""
    doc = _doc(text="Temp Person works at Temp Corp as Temp Engineer.")
    n = {"i": 0}

    async def kg_ingest(**kw):
        if ingest_calls is not None:
            ingest_calls.append(kw)
        return {"entities": 0, "relations": 0, "mentions": 0}

    hooks = _hooks([doc], rec, kg_ingest=kg_ingest)

    async def stream_llm(model, prompt, think=False, **kw):
        i = min(n["i"], len(replies) - 1)
        n["i"] += 1
        rec.prompts.append(prompt)
        reply = replies[i]
        if isinstance(reply, Exception):
            raise reply
        for part in (reply if isinstance(reply, list) else [reply]):
            if part:
                yield {"content": part}
        yield {"done": True}

    hooks.stream_llm = stream_llm
    return hooks, n


REQ = {"message": "who is this document about?", "attachments": [{"doc_id": "d1"}]}


def test_empty_answer_retried_then_content(no_retry_delay):
    rec = Recorder()
    ingest = []
    hooks, n = _seq_hooks(["", "**Name:** Temp Person"], rec, ingest)
    events = _run(REQ, hooks)
    assert n["i"] == 2
    assert _content(events) == "**Name:** Temp Person"
    assert _last_tile(events, "answer")["status"] == "done"
    assert not any("error" in e for e in events)
    assert ingest  # knowledge store still fed on success


def test_garbled_short_answer_is_not_shown(no_retry_delay):
    rec = Recorder()
    hooks, n = _seq_hooks([["T", "ab"], "The document is about Temp Person."], rec)
    events = _run(REQ, hooks)
    assert n["i"] == 2
    assert _content(events) == "The document is about Temp Person."


def test_always_empty_answer_is_an_error(no_retry_delay):
    rec = Recorder()
    ingest = []
    hooks, n = _seq_hooks([""], rec, ingest)
    events = _run(REQ, hooks)
    assert n["i"] == 2
    tile = _last_tile(events, "answer")
    assert tile["status"] == "error"
    assert tile["detail"] == _agent.EMPTY_ANSWER_MESSAGE
    errors = [e["error"] for e in events if "error" in e]
    assert errors == [_agent.EMPTY_ANSWER_MESSAGE]
    assert ingest == []
    assert _content(events) == ""


def test_transient_error_retried(no_retry_delay):
    rec = Recorder()
    hooks, n = _seq_hooks([RuntimeError("Model 'm' failed (Ollama HTTP 500). out of memory"),
                           "Temp Person is a Temp Engineer."], rec)
    events = _run(REQ, hooks)
    assert n["i"] == 2
    assert _last_tile(events, "answer")["status"] == "done"
    assert _content(events) == "Temp Person is a Temp Engineer."


def test_non_transient_error_not_retried(no_retry_delay):
    rec = Recorder()
    hooks, n = _seq_hooks([RuntimeError("Model 'x' is not installed in Ollama.")], rec)
    events = _run(REQ, hooks)
    assert n["i"] == 1
    assert _last_tile(events, "answer")["status"] == "error"
    assert any("error" in e for e in events)


def test_short_answer_confirmed_by_retry_is_kept(no_retry_delay):
    rec = Recorder()
    hooks, n = _seq_hooks(["No"], rec)
    events = _run({"message": "is this an invoice?", "attachments": [{"doc_id": "d1"}]}, hooks)
    assert n["i"] == 2
    assert _content(events) == "No"
    assert _last_tile(events, "answer")["status"] == "done"


def test_stream_text_raises_on_error_line(monkeypatch):
    body = (
        json.dumps({"message": {"content": ""}, "done": False}) + "\n"
        + json.dumps({"error": "model runner has unexpectedly stopped (out of memory)"}) + "\n"
    )

    def handler(request):
        return httpx.Response(200, content=body.encode())

    real_client = httpx.AsyncClient

    async def prep(model):
        return model

    monkeypatch.setattr(_idp, "_prepare_text_model", prep)
    monkeypatch.setattr(_idp.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def go():
        return [d async for d in _idp.stream_text("gemma", "hi")]

    with pytest.raises(RuntimeError, match="out of memory"):
        asyncio.run(go())


# ── 3. page markers ────────────────────────────────────────────────────────

PAGE1 = "SERVICE AGREEMENT between Acme B.V. and Jane Example.\nArticle 1 · Scope\nArticle 2 · Fee EUR 2,500"
PAGE2 = "Article 4 · Confidentiality\nBoth parties keep all information confidential."


def _paged_doc(pages):
    d = _doc(doc_id="c1", filename="contract.pdf", text="\n\n".join(pages).strip(), pages=len(pages))
    d.page_texts = list(pages)
    return d


def test_page_markers_in_prompt():
    doc = _paged_doc([PAGE1, PAGE2])
    prompt, _ = _agent._build_prompt_ex("general_question", [doc], "what is on page 2?", [])
    assert "--- Page 1 ---\n" + PAGE1 in prompt
    assert "--- Page 2 ---\n" + PAGE2 in prompt
    assert prompt.index("--- Page 1 ---") < prompt.index("--- Page 2 ---")


def test_page_markers_in_agent_run():
    doc = _paged_doc([PAGE1, PAGE2])
    rec = Recorder()
    hooks = _hooks([doc], rec)
    _run({"message": "what is on page 2?", "attachments": [{"doc_id": "c1"}]}, hooks)
    assert "--- Page 2 ---\n" + PAGE2 in rec.prompts[0]


def test_no_markers_for_single_page_or_mismatched_text():
    doc = _doc(text=PAGE1)
    prompt, _ = _agent._build_prompt_ex("general_question", [doc], "q", [])
    assert "--- Page" not in prompt
    doc2 = _paged_doc([PAGE1, PAGE2])
    doc2.text = "Completely different OCR text"
    prompt, _ = _agent._build_prompt_ex("general_question", [doc2], "q", [])
    assert "--- Page" not in prompt


def test_no_markers_for_translate():
    doc = _paged_doc([PAGE1, PAGE2])
    prompt, _ = _agent._build_prompt_ex("translate", [doc], "translate to German", [])
    assert "--- Page" not in prompt


def test_page_markers_kept_in_excerpts():
    filler = "Lorem ipsum dolor sit amet. " * 400  # ~11k per page
    pages = [f"PAGE-ONE {filler}", f"PAGE-TWO {filler}", f"PAGE-THREE {filler} TAIL-NEEDLE"]
    doc = _paged_doc(pages)
    prompt, excerpts = _agent._build_prompt_ex("general_question", [doc], "q", [], max_chars=6000)
    assert excerpts["c1"]["mode"] == "head_tail"
    assert "--- Page 1 ---\nPAGE-ONE" in prompt
    tail = prompt.split("[…]")[1]
    assert tail.lstrip("\n").startswith("--- Page 3 ---")
    assert "TAIL-NEEDLE" in tail

    # RAG chunks are labelled with the page they come from
    chunk = "PAGE-TWO " + filler[:300].strip()
    prompt, excerpts = _agent._build_prompt_ex(
        "general_question", [doc], "q", [], max_chars=6000, retrieved={"c1": [chunk]},
    )
    assert excerpts["c1"]["mode"] == "rag"
    assert "--- Page 2 ---\n" + chunk in prompt


# ── 4. X detection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg,expected", [
    ("is he on Instagram or X?", ["instagram", "x"]),
    ("is she on X?", ["x"]),
    ("look him up on LinkedIn and X", ["linkedin", "x"]),
    ("check x.com for this person", ["x"]),
    ("is he on twitter", ["x"]),
    ("search X/Twitter", ["x"]),
    ("look her up on Facebook and X (Twitter)", ["facebook", "x"]),
])
def test_x_detected(msg, expected):
    assert _web.rules_web_lookup(msg)[0] == expected


@pytest.mark.parametrize("msg", [
    "summarize the x-ray report", "is my xbox order in this invoice?", "what is the max amount?",
    "what is the tax?", "based on x-ray findings", "what is the value of x.y in the table",
    "extract the max and tax values",
])
def test_x_not_detected(msg):
    assert "x" not in _web.rules_web_lookup(msg)[0]


def test_strip_web_clause_with_x():
    assert _web.strip_web_clause("who is this document about? is he on Instagram or X?") == "who is this document about"


# ── 5. knowledge-graph hygiene ─────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Acme B.V.", "Acme B.V", "Foo Holding", "Siemens AG", "Acme Ltd",
                                  "ABN AMRO Bank", "Delft University", "Stichting Vrienden", "Acme Inc."])
def test_org_names_detected(name):
    assert _web.looks_like_organization(name)


@pytest.mark.parametrize("name", ["Jane Example", "Anna de Vries", "Sarah Coleman", "Co Nguyen", "Michel Neuféglise"])
def test_person_names_not_orgs(name):
    assert not _web.looks_like_organization(name)


def test_extract_persons_skips_organisations():
    found = _web.extract_persons("The parties are **Acme B.V.** (Amsterdam) and **Jane Example** (contractor).")
    assert [p["name"] for p in found] == ["Jane Example"]
    assert _web.extract_person("**Name:** Acme B.V.", []) is None


@pytest.mark.parametrize("text,role,org", [
    ("Temp Person works at Temp Corp as Temp Engineer.", "Temp Engineer", "Temp Corp"),
    ("Temp Person works as Temp Engineer at Temp Corp", "Temp Engineer", "Temp Corp"),
    ("Solution Architect at Rabobank", "Solution Architect", "Rabobank"),
    ("Solution Architect bij Rabobank", "Solution Architect", "Rabobank"),
    ("Rabobank — Solution Architect", "Solution Architect", "Rabobank"),
    ("Solution Architect – Rabobank", "Solution Architect", "Rabobank"),
    ("Jan werkt bij Acme als Software Engineer", "Software Engineer", "Acme"),
])
def test_extract_headline_patterns(text, role, org):
    h = _web.extract_headline(text)
    assert h == {"role": role, "org": org}
    assert " as " not in (h["org"] or "") and "works" not in (h["role"] or "")


def _insert(conn, eid, type_, name, props=None):
    now = time.time()
    conn.execute(
        "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (eid, type_, name, _kg._normalize_text(name), json.dumps(props or {}), now, now),
    )


def _rel(conn, src, dst, type_):
    conn.execute(
        "INSERT INTO kg_relations (id, src_id, dst_id, type, confidence, source, props, created_at) "
        "VALUES (?,?,?,?,0.5,'t','{}',?)", (f"{src}→{dst}→{type_}", src, dst, type_, time.time()),
    )


def test_migration_purges_ref_profiles_junk_roles_and_org_persons():
    async def body():
        conn = _kg._connect()
        try:
            _insert(conn, "document:d", "document", "cv.pdf", {"doc_id": "d", "filename": "cv.pdf"})
            _insert(conn, "person:michel", "person", "Michel Neuféglise")
            _insert(conn, "profile:legacy", "profile", "Check out this TechTalk Podcast",
                    {"url": "ref://88677032"})
            _insert(conn, "profile:linkedin.com/in/michel", "profile", "Michel | LinkedIn",
                    {"url": "https://www.linkedin.com/in/michel"})
            _insert(conn, "role:junk", "role", "the individual whose CV is presented")
            _insert(conn, "role:frag", "role", "Temp Person works")
            _insert(conn, "role:real", "role", "Solution Architect")
            _insert(conn, "organization:frag", "organization", "Temp Corp as Temp Engineer.")
            _insert(conn, "person:acme b.v", "person", "Acme B.V")
            _rel(conn, "person:michel", "document:d", "mentioned_in")
            _rel(conn, "person:michel", "profile:legacy", "candidate_profile")
            _rel(conn, "person:michel", "profile:linkedin.com/in/michel", "likely_profile")
            _rel(conn, "person:michel", "role:junk", "has_role")
            _rel(conn, "person:michel", "role:frag", "has_role")
            _rel(conn, "person:michel", "role:real", "has_role")
            _rel(conn, "person:michel", "organization:frag", "works_at")
            _rel(conn, "person:acme b.v", "document:d", "mentioned_in")
        finally:
            conn.close()
        await _kg.init_db()
        conn = _kg._connect()
        try:
            ents = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM kg_entities").fetchall()}
            rels = [(r["src_id"], r["dst_id"], r["type"]) for r in conn.execute("SELECT * FROM kg_relations")]
        finally:
            conn.close()
        return ents, rels

    ents, rels = _kg_run(body)
    for gone in ("profile:legacy", "role:junk", "role:frag", "organization:frag", "person:acme b.v"):
        assert gone not in ents, gone
        assert all(gone not in (s, d) for s, d, _ in rels), gone
    assert "profile:linkedin.com/in/michel" in ents and "role:real" in ents
    orgs = [e for e in ents.values() if e["type"] == "organization"]
    assert [o["name"] for o in orgs] == ["Acme B.V"]
    assert (orgs[0]["id"], "document:d", "mentioned_in") in rels


def test_ingest_stores_organisation_not_person():
    doc = SimpleNamespace(id="doc-o", filename="contract.pdf", mime="application/pdf",
                          text="Acme B.V. and Jane Example")

    async def body():
        await _kg.ingest_run(
            conversation_id="c", run_id="r", intent="identify_person", subject_docs=[doc], answer_text="",
            persons=[{"name": "Acme B.V."}, {"name": "Jane Example", "role": "CV holder",
                                             "org": "Temp Corp as Temp Engineer."}],
        )
        conn = _kg._connect()
        try:
            return [(r["type"], r["name"]) for r in conn.execute("SELECT type, name FROM kg_entities")]
        finally:
            conn.close()

    ents = _kg_run(body)
    assert ("organization", "Acme B.V") in ents
    assert not any(t == "person" and "Acme" in n for t, n in ents)
    assert ("person", "Jane Example") in ents
    assert not any(t == "role" for t, _ in ents)
    assert not any(t == "organization" and " as " in n for t, n in ents)


def test_graph_query_never_cites_non_http_profiles():
    ents = [
        {"id": "person:michel", "type": "person", "name": "Michel Neuféglise", "props": {}},
        {"id": "role:sa", "type": "role", "name": "Solution Architect", "props": {}},
        {"id": "role:junk", "type": "role", "name": "CV holder", "props": {}},
        {"id": "profile:legacy", "type": "profile", "name": "TechTalk Podcast",
         "props": {"url": "ref://88677032", "host": ""}},
        {"id": "profile:linkedin.com/in/m", "type": "profile", "name": "Michel | LinkedIn",
         "props": {"url": "https://nl.linkedin.com/in/m", "host": "nl.linkedin.com"}},
    ]
    rels = [
        {"src": "person:michel", "dst": "role:sa", "type": "has_role", "confidence": 0.7},
        {"src": "person:michel", "dst": "role:junk", "type": "has_role", "confidence": 0.7},
        {"src": "person:michel", "dst": "profile:legacy", "type": "candidate_profile", "confidence": 0.5},
        {"src": "person:michel", "dst": "profile:linkedin.com/in/m", "type": "likely_profile", "confidence": 0.8},
    ]

    async def kg_search(t):
        return [ents[0]]

    async def kg_neighborhood(eid):
        return {"entities": ents, "relations": rels}

    rec = Recorder()
    hooks = _hooks([], rec, kg_search=kg_search, kg_neighborhood=kg_neighborhood)
    _run({"message": "what do we know about Michel Neuféglise across my documents?", "attachments": []}, hooks)
    facts = rec.prompts[0].split("Facts:")[1]
    assert "88677032" not in facts and "TechTalk" not in facts and "CV holder" not in facts
    assert "Solution Architect" in facts and "linkedin.com" in facts


# ── 6. cached OCR label / non-http candidates ──────────────────────────────

def test_cached_ocr_text_label():
    doc = _doc(text="Patient: J. Jansen, born 12-05-1978. " * 3, page_images=["/tmp/p1.png"],
               meta={"last_ocr_at": 123, "ocr_model": "glm-ocr"})
    rec = Recorder()
    hooks = _hooks([doc], rec)
    events = _run({"message": "what is the patient's date of birth?", "attachments": [{"doc_id": "d1"}]}, hooks)
    detail = _last_tile(events, "extract-d1")["detail"]
    assert detail.startswith("OCR text (cached,") and "Text layer present" not in detail
    assert rec.ocr_calls == []


def test_non_http_candidates_skipped():
    results = {
        "ref://88677032": {"title": "Michel Neuféglise podcast", "snippet": ""},
        "https://nl.linkedin.com/in/michelneufeglise": {"title": "Michel Neuféglise | LinkedIn", "snippet": ""},
    }
    cands = _web._build_candidates(results, ["linkedin"])
    assert [c["url"] for c in cands] == ["https://nl.linkedin.com/in/michelneufeglise"]
