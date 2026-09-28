"""
Knowledge-graph relation evidence (GET /api/kg/relation-evidence) and the
"touched" (*_seen) ingest counts behind the Network view's knowledge-store card.

Uses the isolated test DB from conftest.py (kg_store is reset around each test).
"""

import asyncio
import sys
from types import SimpleNamespace

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import kg_store as _kg
import doc_agent as _agent

REGISTRY_TEXT = (
    "DEMO · Company Registry Extract\nRegistration\nTrade name\nNeuféglise Digital Solutions\n"
    "Legal form\nEenmanszaak (sole proprietorship)\nEmployees\n1 (owner)\n"
    "Owner / authorised person\nName\nMichel Neuféglise\nRole\nOwner (eigenaar)\n"
    "Authority\nSolely authorised\nSince\n1 March 2021\n"
)
LETTER_TEXT = "Michel Neuféglise\nNeuféglise Digital Solutions\n\nHierbij bevestig ik dat ik de eigenaar ben.\n"


def _run(body):
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            return await body()
        finally:
            await _kg.reset()
    return asyncio.run(go())


def _doc(doc_id, filename, text="", mime="application/pdf"):
    return SimpleNamespace(id=doc_id, filename=filename, mime=mime, text=text)


LETTER = _doc("let1", "DEMO_handwritten_letter_Michel_Neufeglise.png", LETTER_TEXT, "image/png")
REGISTRY = _doc("reg1", "DEMO_company_registry_extract_Michel_Neufeglise.pdf", REGISTRY_TEXT)
CARD = _doc("card1", "DEMO_signature_card_Michel_Neufeglise.png", "", "image/png")
TEXTS = {d.id: d.text for d in (LETTER, REGISTRY, CARD)}


async def _ingest_demo(run_id="run1", crops=True):
    sig = {
        "person": "Michel Neuféglise",
        "letter_doc": LETTER,
        "card_docs": [CARD],
        "score": 96,
        "band": "consistent",
        "n_references": 3,
        "verified_at": "2026-09-28T14:34:14",
    }
    if crops:
        sig["questioned_url"] = f"/api/idp/signatures/{run_id}/questioned.png"
        sig["reference_urls"] = [f"/api/idp/signatures/{run_id}/ref_{i}.png" for i in (1, 2, 3)] + ["https://evil.example/x.png"]
    return await _kg.ingest_run(
        conversation_id="conv-sig", run_id=run_id, intent="cross_reference",
        subject_docs=[LETTER, REGISTRY], answer_text="The letter was written by Michel Neuféglise.",
        persons=[{"name": "Michel Neuféglise", "doc_id": "let1"}],
        organizations=[{"name": "Neuféglise Digital Solutions", "doc_id": "reg1", "owner": "Michel Neuféglise",
                        "legal_form": "Eenmanszaak"}],
        signature=sig,
    )


def _rel_id(src, dst, type_):
    return f"{src}→{dst}→{type_}"


PERSON = "person:michel neufeglise"
ORG = "organization:neufeglise digital solutions"


def test_seen_counts_report_touched_rows_even_when_nothing_is_new():
    async def body():
        first = await _ingest_demo()
        again = await _ingest_demo()
        return first, again

    first, again = _run(body)
    # First run: everything is new, and seen == new
    assert first["entities"] >= 4 and first["relations"] >= 6
    assert first["entities_seen"] == first["entities"]
    assert first["relations_seen"] == first["relations"]
    # Re-run: nothing new, but the run still touched the same facts
    assert again["entities"] == 0 and again["relations"] == 0
    assert again["entities_seen"] == first["entities_seen"]
    assert again["relations_seen"] == first["relations_seen"]


def test_kg_ingest_detail_uses_seen_counts():
    d = _agent._kg_ingest_detail({"entities": 2, "relations": 4, "mentions": 3,
                                  "entities_seen": 5, "relations_seen": 7})
    assert d == "5 entities (+2 new) · 7 relations (+4 new)"
    d = _agent._kg_ingest_detail({"entities": 0, "relations": 0, "mentions": 0,
                                  "entities_seen": 5, "relations_seen": 7})
    assert d == "5 entities · 7 relations · already known"
    # Legacy ingest result (no *_seen): the old "+N" format
    assert _agent._kg_ingest_detail({"entities": 3, "relations": 2, "mentions": 1}) == \
        "+3 entities · +2 relations · +1 mentions"


def test_owns_evidence_has_registry_snippet_and_provenance():
    async def body():
        await _ingest_demo()
        return await _kg.relation_evidence(
            _rel_id(PERSON, ORG, "owns"), get_doc_text=lambda did: TEXTS.get(did),
        )

    ev = _run(body)
    assert ev["relation"]["type"] == "owns"
    assert ev["relation"]["run_id"] == "run1"
    assert ev["relation"]["conversation_id"] == "conv-sig"
    assert isinstance(ev["relation"]["created_at"], float)
    assert ev["source_doc"]["doc_id"] == "reg1"
    assert ev["source_doc"]["filename"].startswith("DEMO_company_registry_extract")
    assert ev["snippet_source"] == "document"
    assert "Michel Neuféglise" in ev["snippet"] and "Owner" in ev["snippet"]
    assert ev["signature"] is None
    assert "Michel Neuféglise" in ev["highlights"]


def test_owns_evidence_falls_back_to_stored_mention_without_document_text():
    async def body():
        await _ingest_demo()
        return await _kg.relation_evidence(_rel_id(PERSON, ORG, "owns"), get_doc_text=lambda did: None)

    ev = _run(body)
    assert ev["source_doc"]["doc_id"] == "reg1"
    assert ev["snippet_source"] == "mention"
    assert "Michel Neuféglise" in ev["snippet"]


def test_signed_evidence_has_score_band_and_stored_crops():
    async def body():
        await _ingest_demo()
        return await _kg.relation_evidence(_rel_id(PERSON, "document:let1", "signed"))

    ev = _run(body)
    sig = ev["signature"]
    assert sig["score"] == 96 and sig["band"] == "consistent" and sig["n_references"] == 3
    assert sig["crops_source"] == "stored"
    assert sig["questioned"] == "/api/idp/signatures/run1/questioned.png"
    # Only same-origin crop URLs are stored
    assert sig["references"] == [f"/api/idp/signatures/run1/ref_{i}.png" for i in (1, 2, 3)]
    assert ev["source_doc"]["doc_id"] == "let1"
    assert ev["reference_doc"]["doc_id"] == "card1"
    assert ev["reference_doc"]["signature_card"] is True
    assert ev["snippet"] is None


def test_signature_evidence_derives_crops_for_older_data():
    async def body():
        await _ingest_demo(run_id="oldrun", crops=False)
        have = {("oldrun", "questioned.png"), ("oldrun", "ref_1.png"), ("oldrun", "ref_2.png")}
        va = await _kg.relation_evidence(
            _rel_id("document:let1", "document:card1", "verified_against"),
            crop_exists=lambda run, name: (run, name) in have,
        )
        none = await _kg.relation_evidence(_rel_id(PERSON, "document:let1", "signed"))
        spec = await _kg.relation_evidence(
            _rel_id("document:card1", PERSON, "signature_specimen"),
            crop_exists=lambda run, name: (run, name) in have,
        )
        return va, none, spec

    va, none, spec = _run(body)
    assert va["signature"]["crops_source"] == "derived"
    assert va["signature"]["questioned"] == "/api/idp/signatures/oldrun/questioned.png"
    assert va["signature"]["references"] == [
        "/api/idp/signatures/oldrun/ref_1.png", "/api/idp/signatures/oldrun/ref_2.png",
    ]
    assert va["source_doc"]["doc_id"] == "let1" and va["reference_doc"]["doc_id"] == "card1"
    # No crop_exists callback and nothing stored → degrade gracefully
    assert none["signature"]["questioned"] is None and none["signature"]["references"] == []
    assert none["signature"]["score"] == 96
    # Specimen relation: score comes from the verified_against check
    assert spec["signature"]["score"] == 96
    assert spec["source_doc"]["doc_id"] == "let1" and spec["reference_doc"]["doc_id"] == "card1"


def test_unknown_relation_returns_none():
    async def body():
        return await _kg.relation_evidence("nope→nope→owns")

    assert _run(body) is None


def test_evidence_snippet_is_accent_insensitive_and_bounded():
    text = "x" * 500 + "\nOwner / authorised person\nName\nMichel Neufeglise\nRole\nOwner\n" + "y" * 500
    s = _kg._evidence_snippet(text, ["Michel Neuféglise"], ("owner",))
    assert "Michel Neufeglise" in s
    assert s.startswith("…") and s.endswith("…")
    assert len(s) < 400
    assert _kg._evidence_snippet("", ["x y"]) is None
    assert _kg._evidence_snippet("nothing here", ["Jane Doe"]) is None


def test_conversation_scope_keeps_reuploaded_documents_after_a_later_run():
    """Re-uploaded files (new doc ids, same file names) resolve to the existing
    document entities; an earlier conversation must keep them in its graph scope
    even after a later conversation's run moved the shared relations."""
    letter2 = _doc("let2", LETTER.filename, LETTER_TEXT, "image/png")
    registry2 = _doc("reg2", REGISTRY.filename, REGISTRY_TEXT)
    card2 = _doc("card2", CARD.filename, "", "image/png")

    async def ingest(conv, run, letter, registry, card):
        return await _kg.ingest_run(
            conversation_id=conv, run_id=run, intent="cross_reference",
            subject_docs=[letter, registry], answer_text="",
            persons=[{"name": "Michel Neuféglise", "doc_id": letter.id}],
            organizations=[{"name": "Neuféglise Digital Solutions", "doc_id": registry.id,
                            "owner": "Michel Neuféglise"}],
            signature={"person": "Michel Neuféglise", "letter_doc": letter, "card_docs": [card],
                       "score": 96, "band": "consistent", "n_references": 3},
        )

    async def body():
        await ingest("conv-old", "r0", LETTER, REGISTRY, CARD)   # creates the entities (ids let1/reg1/card1)
        await ingest("conv-a", "r1", letter2, registry2, card2)  # re-upload
        await ingest("conv-b", "r2", _doc("let3", LETTER.filename, LETTER_TEXT, "image/png"),
                     _doc("reg3", REGISTRY.filename, REGISTRY_TEXT), _doc("card3", CARD.filename, "", "image/png"))
        return await _kg.get_graph(scope="conversation", conversation_id="conv-a")

    g = _run(body)
    ids = {e["id"] for e in g["entities"]}
    assert {"document:let1", "document:reg1", "document:card1", PERSON, ORG} <= ids


def test_conversation_scope_excludes_other_letters_verified_against_the_same_card():
    forged = _doc("forg1", "DEMO_handwritten_letter_FORGED_signature.png", LETTER_TEXT, "image/png")

    async def ingest(conv, run, letter, score, band):
        return await _kg.ingest_run(
            conversation_id=conv, run_id=run, intent="cross_reference",
            subject_docs=[letter, REGISTRY], answer_text="",
            persons=[{"name": "Michel Neuféglise", "doc_id": letter.id}],
            organizations=[{"name": "Neuféglise Digital Solutions", "doc_id": REGISTRY.id,
                            "owner": "Michel Neuféglise"}],
            signature={"person": "Michel Neuféglise", "letter_doc": letter, "card_docs": [CARD],
                       "score": score, "band": band, "n_references": 3},
        )

    async def body():
        await ingest("conv-genuine", "g1", LETTER, 96, "consistent")
        await ingest("conv-forged", "f1", forged, 11, "inconsistent")
        return (await _kg.get_graph(scope="conversation", conversation_id="conv-genuine"),
                await _kg.get_graph(scope="conversation", conversation_id="conv-forged"))

    genuine, forged_g = _run(body)
    gids = {e["id"] for e in genuine["entities"]}
    fids = {e["id"] for e in forged_g["entities"]}
    assert "document:let1" in gids and "document:forg1" not in gids
    assert "document:forg1" in fids and "document:let1" not in fids
    assert {"document:card1", "document:reg1", PERSON, ORG} <= gids & fids
