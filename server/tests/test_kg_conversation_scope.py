"""
Conversation scope of the knowledge graph (kg_conversation_docs).

A conversation's graph = what it produced + the documents it used + everything
within two relation hops of those documents (whichever conversation learned it).
Uses the isolated test DB from conftest.py; the backfill test uses its own
temporary DB file.
"""

import asyncio
import json
import sqlite3
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import kg_store as _kg


def _run(body):
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            return await body()
        finally:
            await _kg.reset()
    return asyncio.run(go())


def _doc(doc_id, filename, text=""):
    return SimpleNamespace(id=doc_id, filename=filename, mime="application/pdf", text=text)


def _conv_docs(conversation_id=None):
    conn = _kg._connect()
    try:
        if conversation_id is None:
            rows = conn.execute("SELECT conversation_id, doc_id FROM kg_conversation_docs").fetchall()
        else:
            rows = conn.execute(
                "SELECT conversation_id, doc_id FROM kg_conversation_docs WHERE conversation_id=?",
                (conversation_id,),
            ).fetchall()
        return sorted((r["conversation_id"], r["doc_id"]) for r in rows)
    finally:
        conn.close()


def test_general_question_without_person_shows_documents():
    a = _doc("doc-a", "report.pdf")
    b = _doc("doc-b", "invoice.pdf")

    async def body():
        counts = await _kg.ingest_run(
            conversation_id="conv-gq", run_id="r1", intent="general_question",
            subject_docs=[a, b], answer_text="Both documents are about budgets.",
        )
        graph = await _kg.get_graph(scope="conversation", conversation_id="conv-gq")
        return counts, graph

    counts, g = _run(body)
    assert counts["mentions"] == 0 and counts["relations"] == 0
    doc_entities = sorted(e["id"] for e in g["entities"] if e["type"] == "document")
    assert doc_entities == ["document:doc-a", "document:doc-b"]
    assert [d["doc_id"] for d in g["documents"]] == ["doc-a", "doc-b"]
    assert {d["name"] for d in g["documents"]} == {"report.pdf", "invoice.pdf"}
    for d in g["documents"]:
        assert d["chunk_count"] == 0 and d["mention_count"] == 0
    assert g["stats"]["entities"] == 2
    assert g["stats"]["documents"] == 2
    assert g["stats"]["relations"] == 0
    assert g["stats"]["mentions"] == 0


def test_rerun_same_doc_keeps_one_link():
    a = _doc("doc-a", "report.pdf")

    async def body():
        for run in ("r1", "r2"):
            await _kg.ingest_run(conversation_id="c", run_id=run, intent="summarize",
                                 subject_docs=[a], answer_text="")
        return _conv_docs()

    assert _run(body) == [("c", "doc-a")]


def test_person_from_other_conversation_visible_via_shared_document():
    a = _doc("doc-a", "cv.pdf", "Jane Example, Engineer at Acme")
    other = _doc("doc-z", "unrelated.pdf")

    async def body():
        # Conversation X learns about Jane from doc A (with a web profile)
        await _kg.ingest_run(
            conversation_id="conv-x", run_id="rx", intent="identify_person",
            subject_docs=[a], answer_text="",
            person={"name": "Jane Example", "role": "Engineer", "org": "Acme"},
            web_candidates=[{"url": "https://www.linkedin.com/in/jane-example",
                             "title": "Jane Example - Engineer - Acme | LinkedIn",
                             "snippet": "", "host": "linkedin.com"}],
            verdict="**LinkedIn:** Likely match found — [Jane](https://www.linkedin.com/in/jane-example)",
        )
        # Conversation Y asks a general question about the same document
        await _kg.ingest_run(conversation_id="conv-y", run_id="ry", intent="general_question",
                             subject_docs=[a], answer_text="It is a CV.")
        # Conversation Z uses a different document
        await _kg.ingest_run(conversation_id="conv-z", run_id="rz", intent="general_question",
                             subject_docs=[other], answer_text="")
        gy = await _kg.get_graph(scope="conversation", conversation_id="conv-y")
        gz = await _kg.get_graph(scope="conversation", conversation_id="conv-z")
        return gy, gz

    gy, gz = _run(body)
    ids_y = {e["id"] for e in gy["entities"]}
    person = "person:jane example"
    assert "document:doc-a" in ids_y
    assert person in ids_y
    assert "organization:acme" in ids_y
    assert "role:engineer" in ids_y
    assert any(i.startswith("profile:") for i in ids_y)
    rels_y = {(r["src"], r["dst"], r["type"]) for r in gy["relations"]}
    assert (person, "document:doc-a", "mentioned_in") in rels_y
    assert (person, "organization:acme", "works_at") in rels_y
    assert (person, "role:engineer", "has_role") in rels_y
    assert any(t == "likely_profile" and s == person for s, _, t in rels_y)
    # Relations only among returned entities
    assert all(r["src"] in ids_y and r["dst"] in ids_y for r in gy["relations"])
    # The person was found in a document Y used → counted in Y's scope
    jane = next(e for e in gy["entities"] if e["id"] == person)
    assert jane["mention_count"] == 1
    assert [d["doc_id"] for d in gy["documents"]] == ["doc-a"]
    assert gy["stats"]["entities"] == len(gy["entities"])
    assert gy["stats"]["relations"] == len(gy["relations"])
    assert gy["stats"]["mentions"] == 1
    assert gy["stats"]["documents"] == 1

    ids_z = {e["id"] for e in gz["entities"]}
    assert ids_z == {"document:doc-z"}
    assert gz["relations"] == []
    assert [d["doc_id"] for d in gz["documents"]] == ["doc-z"]


def test_unknown_conversation_is_empty():
    a = _doc("doc-a", "report.pdf")

    async def body():
        await _kg.ingest_run(conversation_id="c1", run_id="r", intent="general_question",
                             subject_docs=[a], answer_text="")
        return await _kg.get_graph(scope="conversation", conversation_id="nope")

    g = _run(body)
    assert g["entities"] == [] and g["documents"] == [] and g["stats"]["entities"] == 0


def test_delete_document_removes_links():
    a = _doc("doc-a", "a.pdf")
    b = _doc("doc-b", "b.pdf")

    async def body():
        await _kg.ingest_run(conversation_id="c1", run_id="r1", intent="general_question",
                             subject_docs=[a, b], answer_text="")
        await _kg.ingest_run(conversation_id="c2", run_id="r2", intent="general_question",
                             subject_docs=[a], answer_text="")
        await _kg.delete_document("doc-a")
        g = await _kg.get_graph(scope="conversation", conversation_id="c1")
        return _conv_docs(), g

    rows, g = _run(body)
    assert rows == [("c1", "doc-b")]
    assert [d["doc_id"] for d in g["documents"]] == ["doc-b"]


def test_delete_conversation_removes_links_keeps_documents():
    a = _doc("doc-a", "a.pdf")

    async def body():
        await _kg.ingest_run(conversation_id="c1", run_id="r1", intent="general_question",
                             subject_docs=[a], answer_text="")
        await _kg.ingest_run(conversation_id="c2", run_id="r2", intent="general_question",
                             subject_docs=[a], answer_text="")
        await _kg.delete_conversation("c1")
        g1 = await _kg.get_graph(scope="conversation", conversation_id="c1")
        g2 = await _kg.get_graph(scope="conversation", conversation_id="c2")
        doc = await _kg.get_entity("document:doc-a")
        return _conv_docs(), g1, g2, doc

    rows, g1, g2, doc = _run(body)
    assert rows == [("c2", "doc-a")]
    assert g1["entities"] == [] and g1["documents"] == []
    assert [d["doc_id"] for d in g2["documents"]] == ["doc-a"]
    # Document entities are never garbage-collected as orphans
    assert doc is not None


def test_reset_clears_links():
    a = _doc("doc-a", "a.pdf")

    async def body():
        await _kg.ingest_run(conversation_id="c1", run_id="r1", intent="general_question",
                             subject_docs=[a], answer_text="")
        assert _conv_docs() == [("c1", "doc-a")]
        await _kg.reset()
        return _conv_docs()

    assert _run(body) == []


def test_backfill_from_messages_runs_once(tmp_path, monkeypatch):
    db_file = tmp_path / "kg_backfill.db"
    monkeypatch.setattr(_kg, "DB_PATH", db_file)

    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.executescript(_kg._SCHEMA)
    conn.execute(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT NOT NULL, thinking TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', "
        "timestamp REAL NOT NULL, meta TEXT NOT NULL DEFAULT '{}')"
    )
    now = time.time()
    for doc_id in ("doc-a", "doc-b"):
        conn.execute(
            "INSERT INTO kg_entities (id, type, name, norm_name, props, created_at, updated_at) "
            "VALUES (?, 'document', ?, ?, ?, ?, ?)",
            (f"document:{doc_id}", f"{doc_id}.pdf", f"{doc_id}.pdf", json.dumps({"doc_id": doc_id}), now, now),
        )

    def msg(mid, conv, meta):
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp, meta) "
            "VALUES (?, ?, 'assistant', '', ?, ?)",
            (mid, conv, now, json.dumps(meta)),
        )

    msg("m1", "conv-1", {"kind": "doc_run", "run_id": "r1", "intent": "general_question",
                         "doc_ids": ["doc-a", "doc-b"]})
    msg("m2", "conv-2", {"kind": "doc_run", "run_id": "r2", "doc_ids": ["doc-a", "doc-gone"]})
    msg("m3", "conv-3", {"kind": "chat", "doc_ids": ["doc-a"], "note": "doc_run"})
    msg("m4", "conv-4", {})
    conn.close()

    _kg._init_sync()
    assert _conv_docs() == [("conv-1", "doc-a"), ("conv-1", "doc-b"), ("conv-2", "doc-a")]

    # Runs once: a second init does not re-backfill removed or new rows
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute("DELETE FROM kg_conversation_docs")
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, timestamp, meta) "
        "VALUES ('m5', 'conv-5', 'assistant', '', ?, ?)",
        (now, json.dumps({"kind": "doc_run", "doc_ids": ["doc-b"]})),
    )
    conn.close()
    _kg._init_sync()
    assert _conv_docs() == []

    g = _kg._get_graph_sync(scope="conversation", conversation_id="conv-1")
    assert g["documents"] == []  # links removed above; nothing re-created


def test_backfill_without_messages_table(tmp_path, monkeypatch):
    db_file = tmp_path / "kg_nomsg.db"
    monkeypatch.setattr(_kg, "DB_PATH", db_file)
    _kg._init_sync()  # must not raise
    assert _conv_docs() == []
