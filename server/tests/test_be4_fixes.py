"""
Regression tests for batch "be4":

 1  multi-entity answers: a person's role/org is read only from that person's
    own bullet/block (never borrowed from a neighbouring organisation)
 2  knowledge store: organisation/role keys ignore trailing punctuation
    ("acme b.v" == "acme b.v."); existing duplicates merged at startup
 3  web lookup: a blocked `site:` query falls back to the plain
    `"<name>" <role/org> <Platform>` query before "Search unavailable"
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

import json
import time

import doc_web as _web
import kg_store as _kg
from test_be2_fixes import _kg_run
from test_be3_fixes import DDG_NOTICE, _collect, _content, _lookup_hooks


# ── 1. role/org from the chosen person's own block ─────────────────────────

PARTIES = (
    "The parties are:\n\n"
    "*   **Acme B.V. (Amsterdam)**\n"
    "    *   **Role:** Party (Contracting entity)\n"
    "*   **Jane Example**\n"
    "    *   **Role:** Contracting individual"
)


def test_extract_person_does_not_borrow_org_role():
    p = _web.extract_person(PARTIES, [])
    assert p["name"] == "Jane Example"
    assert p["role"] in ("Contracting individual", None)
    assert p["role"] != "Contracting entity"


def test_extract_person_multi_entity_role_not_in_own_block_is_none():
    text = (
        "*   **Acme B.V.**\n"
        "    *   **Role:** Supplier\n"
        "*   **Jane Example**\n"
        "    *   Signed on page 3"
    )
    p = _web.extract_person(text, [])
    assert p["name"] == "Jane Example" and p["role"] is None


def test_extract_person_own_block_role_used():
    text = (
        "*   **Acme B.V.**\n"
        "    *   **Role:** Supplier\n"
        "*   **Jane Example**\n"
        "    *   **Role:** Solution Architect\n"
        "    *   **Company:** Foo Corp"
    )
    p = _web.extract_person(text, [])
    assert p == {"name": "Jane Example", "role": "Solution Architect", "org": "Foo Corp"}


def test_extract_person_single_entity_keeps_global_labels():
    p = _web.extract_person("**Name:** Jane Example\n**Role:** Solution Architect\n**Company:** Acme", [])
    assert p == {"name": "Jane Example", "role": "Solution Architect", "org": "Acme"}


def test_extract_persons_each_role_from_own_block():
    text = (
        "*   **Jane Example**\n"
        "    *   **Role:** Solution Architect\n"
        "*   **John Smith**\n"
        "    *   **Role:** Data Engineer\n"
        "    *   **Company:** Foo Corp\n"
        "*   **Anna de Vries**\n"
        "    *   Witness"
    )
    found = _web.extract_persons(text)
    assert found == [
        {"name": "Jane Example", "role": "Solution Architect", "org": None},
        {"name": "John Smith", "role": "Data Engineer", "org": "Foo Corp"},
        {"name": "Anna de Vries", "role": None, "org": None},
    ]


def test_extract_persons_parties_text():
    found = _web.extract_persons(PARTIES)
    assert [p["name"] for p in found] == ["Jane Example"]
    assert found[0]["role"] != "Contracting entity"


# ── 2. organisation keys ignore trailing punctuation ───────────────────────

def _insert(conn, eid, type_, name, created):
    conn.execute(
        "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (eid, type_, name, _kg._normalize_text(name), "{}", created, created),
    )


def _rel(conn, src, dst, type_):
    conn.execute(
        "INSERT INTO kg_relations (id, src_id, dst_id, type, confidence, source, props, created_at) "
        "VALUES (?,?,?,?,0.5,'t','{}',?)", (f"{src}→{dst}→{type_}", src, dst, type_, time.time()),
    )


def test_norm_key_strips_trailing_punctuation_for_orgs_and_roles():
    assert _kg._norm_key("organization", "Acme B.V.") == _kg._norm_key("organization", "Acme B.V") == "acme b.v"
    assert _kg._norm_key("role", "Solution Architect;") == "solution architect"
    assert _kg._norm_key("person", "Jane Example.") == "jane example."  # persons unchanged


def test_upsert_org_ignores_trailing_punctuation():
    async def body():
        a = _kg._upsert_entity_sync("organization", "Acme B.V")
        b = _kg._upsert_entity_sync("organization", "Acme B.V.")
        return a, b

    a, b = _kg_run(body)
    assert a == b


def test_migration_merges_punctuation_duplicate_orgs():
    async def body():
        now = time.time()
        conn = _kg._connect()
        try:
            _insert(conn, "document:d1", "document", "a.pdf", now)
            _insert(conn, "document:d2", "document", "b.pdf", now)
            _insert(conn, "person:jane example", "person", "Jane Example", now)
            _insert(conn, "person:john smith", "person", "John Smith", now)
            _insert(conn, "organization:acme b.v", "organization", "Acme B.V", now)
            _insert(conn, "organization:acme b.v.", "organization", "Acme B.V.", now + 1)
            _rel(conn, "person:jane example", "organization:acme b.v", "works_at")
            _rel(conn, "person:john smith", "organization:acme b.v.", "works_at")
            _rel(conn, "person:jane example", "organization:acme b.v.", "works_at")  # duplicate edge
            _rel(conn, "organization:acme b.v.", "document:d2", "mentioned_in")
            _rel(conn, "person:jane example", "document:d1", "mentioned_in")
            _rel(conn, "person:john smith", "document:d2", "mentioned_in")
            conn.execute(
                "INSERT INTO kg_mentions (id, entity_id, doc_id, created_at) VALUES (?,?,?,?)",
                ("m1", "organization:acme b.v.", "d2", now),
            )
        finally:
            conn.close()
        await _kg.init_db()
        conn = _kg._connect()
        try:
            orgs = [dict(r) for r in conn.execute("SELECT * FROM kg_entities WHERE type='organization'")]
            rels = {(r["src_id"], r["dst_id"], r["type"]) for r in conn.execute("SELECT * FROM kg_relations")}
            ments = [dict(r) for r in conn.execute("SELECT * FROM kg_mentions")]
        finally:
            conn.close()
        again = _kg._upsert_entity_sync("organization", "Acme B.V.")
        return orgs, rels, ments, again

    orgs, rels, ments, again = _kg_run(body)
    assert len(orgs) == 1
    oid = orgs[0]["id"]
    assert oid == "organization:acme b.v" and orgs[0]["norm_name"] == "acme b.v"
    assert ("person:jane example", oid, "works_at") in rels
    assert ("person:john smith", oid, "works_at") in rels
    assert (oid, "document:d2", "mentioned_in") in rels
    assert all("acme b.v." not in (s, d) for s, d, _ in rels)
    assert [m["entity_id"] for m in ments] == [oid]
    assert again == oid


def test_org_person_conversion_uses_normalised_key():
    async def body():
        now = time.time()
        conn = _kg._connect()
        try:
            _insert(conn, "document:d", "document", "c.pdf", now)
            _insert(conn, "organization:acme b.v.", "organization", "Acme B.V.", now)
            _insert(conn, "person:acme b.v", "person", "Acme B.V", now)
            _rel(conn, "person:acme b.v", "document:d", "mentioned_in")
            _rel(conn, "organization:acme b.v.", "document:d", "mentioned_in")
        finally:
            conn.close()
        await _kg.init_db()
        conn = _kg._connect()
        try:
            ents = [dict(r) for r in conn.execute("SELECT * FROM kg_entities WHERE type IN ('organization','person')")]
        finally:
            conn.close()
        return ents

    ents = _kg_run(body)
    assert [e["type"] for e in ents] == ["organization"]


# ── 3. blocked site: search → plain query fallback ─────────────────────────

PERSON = {"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"}
PROFILE = {
    "url": "https://nl.linkedin.com/in/michelneufeglise",
    "title": "Michel Neuféglise - Solution Architect - Rabobank | LinkedIn",
    "snippet": "Solution Architect at Rabobank",
}


def _site_blocking_search(calls):
    async def web_search(query):
        calls.append(query)
        if "site:" in query:
            raise _web.SearchBlocked(DDG_NOTICE)
        if "LinkedIn" in query:
            return [PROFILE]
        return []
    return web_search


def _web_result(events):
    return [e["_web_result"] for e in events if "_web_result" in e][0]


def test_tool_path_blocked_site_query_falls_back_to_plain_query():
    calls: list = []
    replies = [
        {"content": "", "tool_calls": [{"name": "web_search", "arguments": {"query": 'site:linkedin.com/in "Michel Neuféglise"'}}]},
        {"content": "done", "tool_calls": []},
    ]
    hooks = _lookup_hooks(_site_blocking_search(calls), tool_model="qwen3:4b", chat_replies=replies,
                          verdict="**LinkedIn:** Likely match — https://nl.linkedin.com/in/michelneufeglise")
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "Search unavailable" not in text
    assert any("site:" not in q and "LinkedIn" in q for q in calls)
    assert len(calls) <= _web.MAX_QUERIES
    wr = _web_result(events)
    assert not wr.get("unavailable")
    tile = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "web-search"][-1]
    assert tile["status"] == "done"


def test_fallback_path_blocked_site_query_without_role_tries_plain_query():
    calls: list = []
    hooks = _lookup_hooks(_site_blocking_search(calls),
                          verdict="**LinkedIn:** Likely match — https://nl.linkedin.com/in/michelneufeglise")
    person = {"name": "Michel Neuféglise", "role": None, "org": None}
    events = _collect(_web.run_web_lookup(person, ["linkedin"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "Search unavailable" not in text
    assert calls[0].startswith("site:")
    assert '"Michel Neuféglise" LinkedIn' in calls
    assert len(calls) <= _web.MAX_QUERIES
    assert not _web_result(events).get("unavailable")


def test_all_queries_blocked_still_unavailable():
    calls: list = []

    async def web_search(query):
        calls.append(query)
        raise _web.SearchBlocked(DDG_NOTICE)

    hooks = _lookup_hooks(web_search)
    person = {"name": "Michel Neuféglise", "role": None, "org": None}
    events = _collect(_web.run_web_lookup(person, ["linkedin", "facebook"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "**LinkedIn:** Search unavailable — couldn't verify" in text
    assert "**Facebook:** Search unavailable — couldn't verify" in text
    assert len(calls) <= _web.MAX_QUERIES
    assert any("site:" not in q for q in calls)  # plain queries were tried too


def test_query_cap_respected_with_three_targets():
    calls: list = []

    async def web_search(query):
        calls.append(query)
        raise _web.SearchBlocked(DDG_NOTICE)

    hooks = _lookup_hooks(web_search)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin", "facebook", "instagram"], hooks,
                                          answer_model="m", now_ms=0))
    assert len(calls) <= _web.MAX_QUERIES
    assert "Search unavailable" in _content(events)
