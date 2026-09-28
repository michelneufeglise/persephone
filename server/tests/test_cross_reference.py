"""
Cross-reference intent: CV vs company-registry extract (live demo rehearsal).

Covers intent detection (cross_reference, noun vs verb "extract"), that
reference documents reach the prompt for non-signature intents, the
CROSS_REFERENCE_PROMPT, the deterministic registry parser and the knowledge
store ingest of organisation + ownership ('owns').
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio

import pytest

import doc_agent as _agent
import kg_store as _kg
import laya_decider as _laya
from tests.test_doc_agent import fake_document, fake_hooks, collect_events


# ── Fixtures ───────────────────────────────────────────────────────────────

MESSAGE = (
    "Cross-reference my CV with this company registry extract: is Michel Neuféglise the owner "
    "of a registered company, and do the role and business activities match the CV?"
)

CV_NAME = "cv_michelneufeglise.docx"
REGISTRY_NAME = "DEMO_company_registry_extract_Michel_Neufeglise.pdf"

CV_TEXT = (
    "Michel Neuféglise\n"
    "Solution Architect\n"
    "Utrecht, Netherlands · michel@example.com\n\n"
    "Experience\n"
    "Solution Architect — Rabobank (2023 – present)\n"
    "Designing cloud and integration architecture for payments platforms.\n"
    "Lead developer — Rabobank (2015 – 2022)\n"
    "Led a team building customer-facing banking applications.\n"
    "Full-stack developer (2010 – 2015)\n"
    "React, TypeScript, Java and Python for financial-services clients.\n"
)

# Mirrors the text layer of the demo PDF (labels and values on separate lines)
REGISTRY_TEXT = (
    "DEMO\nDEMO\nDEMO · Company Registry Extract\n"
    "Fictitious document for a Persephone product demo. NOT issued by the Kamer van Koophandel.\n"
    "Contains made-up data. Not valid for any legal, financial or identification purpose.\n"
    "Extract date: 28 September 2026   ·   Reference: DEMO-2026-0928-01\n"
    "Registration\n"
    "Registry number (demo)\n00 00 00 00  (DEMO · not a real KvK number)\n"
    "Trade name\nNeuféglise Digital Solutions\n"
    "Legal form\nEenmanszaak (sole proprietorship)\n"
    "Date of registration\n1 March 2021\n"
    "Status\nActive\n"
    "Business activities\n"
    "SBI code\n62.02 · Computer consultancy activities\n"
    "Description\nSolution architecture and full-stack software\n"
    "development consultancy for financial-services clients.\n"
    "Employees\n1 (owner)\n"
    "Establishment\nAddress (fictitious)\nDemostraat 1, 3500 AA Utrecht (DEMO)\n"
    "Website\nexample.com (placeholder)\n"
    "Owner / authorised person\n"
    "Name\nMichel Neuféglise\n"
    "Role\nOwner (eigenaar)\n"
    "Authority\nSolely authorised (alleen/zelfstandig bevoegd)\n"
    "Since\n1 March 2021\n"
    "DEMO DOCUMENT · FICTITIOUS DATA\n"
    "Created for demonstrating document cross-referencing in Persephone.\n"
    "It does not originate from the Kamer van Koophandel and proves nothing about any real company.\n"
)


def _docs():
    cv = fake_document(
        doc_id="cv", filename=CV_NAME,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        text=CV_TEXT,
    )
    reg = fake_document(doc_id="reg", filename=REGISTRY_NAME, text=REGISTRY_TEXT)
    return cv, reg


def _run_xref(laya_intent_result=None, registry_role="reference_specimen", kg_ingest=None,
              message=MESSAGE, attachments=None, docs=None):
    """Run the agent on the CV + registry pair; returns (events, prompts, ingest_calls)."""
    cv, reg = docs or _docs()
    prompts: list[str] = []
    ingest_calls: list[dict] = []

    async def capture_llm(model, prompt, think=False, **kw):
        prompts.append(prompt)
        yield {"content": "| Field | A | B | Result |\n|---|---|---|---|\n"}
        yield {"content": "Conclusion: consistent, but the registry extract is a DEMO document."}
        yield {"done": True, "stats": {}}

    hooks = fake_hooks(
        get_doc_map={d.id: d for d in (cv, reg)},
        laya_intent_result=laya_intent_result,
    )
    # The live failure: Laya marked the registry extract as a reference document
    def laya_role(msg, f):
        if f.get("name") == REGISTRY_NAME and registry_role:
            return {"role": registry_role, "confidence": 0.9}
        return {"role": "subject_document", "confidence": 0.9}

    hooks.laya_role = laya_role
    hooks.stream_llm = capture_llm

    async def fake_ingest(**kw):
        ingest_calls.append(kw)
        return {"entities": 0, "relations": 0, "mentions": 0}

    hooks.kg_ingest = kg_ingest or fake_ingest
    req = {
        "message": message,
        "attachments": attachments or [{"doc_id": "cv"}, {"doc_id": "reg"}],
        "conversation_id": "conv-x",
        "run_id": "run-x",
    }
    events = asyncio.run(collect_events(_agent.run_agent(req, hooks)))
    return events, prompts, ingest_calls


def _last_tile(events, tile_id):
    tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == tile_id]
    return tiles[-1] if tiles else None


def _decision(tile, did):
    return next((d for d in tile["decisions"] if d["id"] == did), None)


# ── Intent rules ───────────────────────────────────────────────────────────

TWO_FILES = [{"name": CV_NAME}, {"name": REGISTRY_NAME}]


def test_rules_exact_message_two_docs_is_cross_reference():
    intent, kws = _agent.rules_intent(MESSAGE, TWO_FILES)
    assert intent == "cross_reference"
    assert kws


@pytest.mark.parametrize("laya", [
    None,
    {"intent": "extract_data", "confidence": 0.95, "probabilities": {}},
    {"intent": "identify_person", "confidence": 0.95, "probabilities": {}},
    {"intent": "general_question", "confidence": 0.9, "probabilities": {}},
    {"intent": "verify_signature", "confidence": 0.95, "probabilities": {}},
])
def test_resolve_cross_reference_beats_other_intents(laya):
    res = _agent.resolve_intent(laya, _agent.rules_intent(MESSAGE, TWO_FILES), n_docs=2)
    assert res["intent"] == "cross_reference"


def test_resolve_laya_cross_reference_agrees():
    laya = {"intent": "cross_reference", "confidence": 0.8, "probabilities": {}}
    res = _agent.resolve_intent(laya, _agent.rules_intent(MESSAGE, TWO_FILES), n_docs=2)
    assert res["intent"] == "cross_reference" and res["source"] == "laya"


@pytest.mark.parametrize("msg", [
    "cross reference the invoice and the contract",
    "crossreference both files",
    "cross-check the amounts",
    "compare the invoice with the contract",
    "compare this CV to the registry",
    "please compare the totals against the purchase order",
    "a comparison of both documents",
    "does the address match the one in the contract?",
    "are these two documents consistent?",
    "check the consistency of the dates",
    "check the invoice against the contract",
    "verify the claims against the registry",
    "does the name correspond?",
    "any discrepancies?",
    "is there a mismatch?",
    "vergelijk het cv met het uittreksel",
    "controleer het cv tegen het uittreksel",
    "komt het adres overeen?",
])
def test_rules_cross_reference_phrases(msg):
    assert _agent.rules_intent(msg, TWO_FILES)[0] == "cross_reference"


def test_single_doc_compare_is_not_cross_reference():
    one = [{"name": "invoice.pdf"}]
    assert _agent.rules_intent("compare the invoice with the contract", one)[0] != "cross_reference"
    assert _agent.rules_intent(MESSAGE, one)[0] != "cross_reference"
    # Laya's cross_reference is ignored with a single document
    laya = {"intent": "cross_reference", "confidence": 0.95, "probabilities": {}}
    res = _agent.resolve_intent(laya, _agent.rules_intent("compare the invoice with the contract", one), n_docs=1)
    assert res["intent"] != "cross_reference"


def test_single_doc_run_is_not_cross_reference():
    cv, _reg = _docs()
    hooks = fake_hooks(get_doc_map={"cv": cv})
    events = asyncio.run(collect_events(_agent.run_agent(
        {"message": "compare the role with the experience", "attachments": [{"doc_id": "cv"}]}, hooks,
    )))
    done = next(e for e in events if e.get("done"))
    assert done["stats"]["intent"] != "cross_reference"


def test_verify_signature_precedence_kept():
    files = [{"name": "contract.pdf"}, {"name": "reference_card.png"}]
    msg = "compare the signature on the contract with the reference card"
    assert _agent.rules_intent(msg, files)[0] == "verify_signature"
    res = _agent.resolve_intent(None, _agent.rules_intent(msg, files), n_docs=2)
    assert res["intent"] == "verify_signature"


# ── "extract": verb vs noun ────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "please extract all fields from this invoice",
    "extract the invoice number and totals",
    "Extract all amounts",
    "can you extract the dates?",
    "list all fields",
    "extract table",
])
def test_extract_verb_is_extract_data(msg):
    assert _agent.rules_intent(msg, [{"name": "invoice.pdf"}])[0] == "extract_data"


@pytest.mark.parametrize("msg", [
    "what does this registry extract say about the owner?",
    "who owns the company in this company extract?",
    "is this KvK extract recent?",
    "what is in the uittreksel?",
    "check this extract from the chamber of commerce",
    "is the owner in this registry extract the same person?",
])
def test_extract_noun_is_not_extract_data(msg):
    assert _agent.rules_intent(msg, [{"name": "registry.pdf"}])[0] != "extract_data"


def test_registry_extract_question_one_doc_run_not_extract_data():
    _cv, reg = _docs()
    hooks = fake_hooks(get_doc_map={"reg": reg})
    events = asyncio.run(collect_events(_agent.run_agent(
        {"message": "what does this registry extract say about the owner?", "attachments": [{"doc_id": "reg"}]},
        hooks,
    )))
    done = next(e for e in events if e.get("done"))
    assert done["stats"]["intent"] != "extract_data"


# ── INTENTS ────────────────────────────────────────────────────────────────

def test_cross_reference_in_both_intent_tables():
    crit = "Asks to compare / cross-reference / check consistency between two or more documents"
    assert _agent.INTENTS["cross_reference"].startswith(crit)
    assert _laya.INTENTS["cross_reference"] == _agent.INTENTS["cross_reference"]


# ── Full run ───────────────────────────────────────────────────────────────

def test_full_run_cross_reference_includes_both_documents():
    events, prompts, ingest_calls = _run_xref()
    assert not [e for e in events if "error" in e]

    laya = _last_tile(events, "laya")
    assert _decision(laya, "intent")["value"] == "cross_reference"
    assert _decision(laya, "role-cv")["value"] == "subject"
    assert _decision(laya, "role-reg")["value"] == "reference"

    # One extract tile per document — the reference document included
    assert _last_tile(events, "extract-cv")["status"] == "done"
    assert _last_tile(events, "extract-reg")["status"] == "done"

    assert len(prompts) == 1
    prompt = prompts[0]
    assert _agent.CROSS_REFERENCE_MARKER in prompt
    assert f'<document name="{CV_NAME}">' in prompt
    assert f'<document name="{REGISTRY_NAME}" role="reference">' in prompt
    assert "Neuféglise Digital Solutions" in prompt
    assert "Solution Architect — Rabobank" in prompt
    # table columns name both documents
    assert f"Field | {CV_NAME} | {REGISTRY_NAME} | Result" in prompt
    assert "Authenticity & caveats" in prompt

    answer = _last_tile(events, "answer")
    assert answer["title"] == "Cross-reference"
    assert answer["status"] == "done"
    done = next(e for e in events if e.get("done"))
    assert done["stats"]["intent"] == "cross_reference"


def test_full_run_registry_as_subject_also_works():
    events, prompts, _ = _run_xref(registry_role=None)
    laya = _last_tile(events, "laya")
    assert _decision(laya, "intent")["value"] == "cross_reference"
    assert f'<document name="{REGISTRY_NAME}">' in prompts[0]
    assert f'<document name="{CV_NAME}">' in prompts[0]


def test_full_run_kg_ingest_receives_organization_and_owner():
    _events, _prompts, ingest_calls = _run_xref()
    assert len(ingest_calls) == 1
    kw = ingest_calls[0]
    assert kw["intent"] == "cross_reference"
    assert {d.id for d in kw["subject_docs"]} == {"cv", "reg"}
    orgs = kw["organizations"]
    assert len(orgs) == 1
    org = orgs[0]
    assert org["name"] == "Neuféglise Digital Solutions"
    assert org["owner"] == "Michel Neuféglise"
    assert org["doc_id"] == "reg"
    assert org["legal_form"].startswith("Eenmanszaak")
    assert org["registered_since"] == "1 March 2021"
    assert org["demo"] is True
    names = {p["name"] for p in kw["persons"]}
    assert names == {"Michel Neuféglise"}
    assert {p.get("doc_id") for p in kw["persons"]} == {"cv", "reg"}


def test_reference_docs_included_for_other_intents():
    events, prompts, _ = _run_xref(message="summarize these documents")
    laya = _last_tile(events, "laya")
    assert _decision(laya, "intent")["value"] == "summarize"
    assert _last_tile(events, "extract-reg") is not None
    assert f'role="reference"' in prompts[0]
    assert "Neuféglise Digital Solutions" in prompts[0]
    assert _agent.REFERENCE_DOCS_NOTE in prompts[0]


# ── Registry parser ────────────────────────────────────────────────────────

def test_parse_registry_extract_pdf_layout():
    p = _agent.parse_registry_extract(REGISTRY_TEXT)
    assert p["trade_name"] == "Neuféglise Digital Solutions"
    assert p["legal_form"] == "Eenmanszaak (sole proprietorship)"
    assert p["registered_since"] == "1 March 2021"
    assert p["status"] == "Active"
    assert p["owner"] == "Michel Neuféglise"
    assert p["owner_role"] == "Owner (eigenaar)"
    assert p["sbi"].startswith("62.02")
    assert "consultancy" in p["activities"]
    assert p["demo"] is True


def test_parse_registry_extract_inline_labels():
    text = (
        "Uittreksel Handelsregister Kamer van Koophandel\n"
        "Trade name Acme Consulting\n"
        "Legal form: Eenmanszaak\n"
        "Owner / Name Jane Example\n"
        "Date of registration 2 February 2019\n"
    )
    p = _agent.parse_registry_extract(text)
    assert p["trade_name"] == "Acme Consulting"
    assert p["legal_form"] == "Eenmanszaak"
    assert p["owner"] == "Jane Example"
    assert p["registered_since"] == "2 February 2019"
    assert p["demo"] is False


def test_parse_registry_extract_ignores_cv():
    assert _agent.parse_registry_extract(CV_TEXT) is None


# ── Real knowledge store ───────────────────────────────────────────────────

def _kg_run(coro_fn):
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            return await coro_fn()
        finally:
            await _kg.reset()
    return asyncio.run(go())


def test_kg_store_ingests_organization_with_owns_relation():
    async def body():
        _events, _prompts, _calls = await asyncio.to_thread(
            _run_xref, None, "reference_specimen", _kg.ingest_run,
        )
        return await _kg.get_graph("all")

    g = _kg_run(body)
    ents = {e["id"]: e for e in g["entities"]}
    org = next(e for e in g["entities"] if e["type"] == "organization"
               and e["name"] == "Neuféglise Digital Solutions")
    assert org["props"].get("demo") is True
    assert org["props"].get("legal_form", "").startswith("Eenmanszaak")
    person = next(e for e in g["entities"] if e["type"] == "person" and e["name"] == "Michel Neuféglise")

    owns = [r for r in g["relations"] if r["type"] == "owns"]
    assert len(owns) == 1
    assert owns[0]["src"] == person["id"] and owns[0]["dst"] == org["id"]
    assert owns[0]["confidence"] == pytest.approx(0.7)
    assert owns[0]["props"].get("demo") is True
    assert owns[0]["props"].get("registered_since") == "1 March 2021"

    mentioned = {(r["src"], r["dst"]) for r in g["relations"] if r["type"] == "mentioned_in"}
    assert (org["id"], "document:reg") in mentioned
    assert (person["id"], "document:reg") in mentioned
    assert (person["id"], "document:cv") in mentioned
    assert "document:cv" in ents and "document:reg" in ents


def test_kg_neighborhood_shows_owns_for_graph_queries():
    async def body():
        await asyncio.to_thread(_run_xref, None, "reference_specimen", _kg.ingest_run)
        hits = await _kg.search_entities("Michel Neuféglise")
        person = next(h for h in hits if h["type"] == "person")
        return await _kg.neighborhood(person["id"])

    nb = _kg_run(body)
    types = {r["type"] for r in nb["relations"]}
    assert "owns" in types
