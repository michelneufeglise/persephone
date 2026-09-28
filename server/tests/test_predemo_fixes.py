"""
Pre-demo fixes:

 1  graph_query: identity / verification facts (likely_profile, owns, signed, …)
    are never dropped by the 12-fact cap in favour of mentioned_in; facts about
    the matched subject come first; a role is cited with the document it was
    read from (not every document the person is mentioned in).
 2  relation evidence: profile relations show the profile, not a CV window;
    contact data (street + number, postcode, phone, e-mail) is redacted from
    every evidence snippet.

Uses the isolated test DB from conftest.py (kg_store is reset around each test).
"""

import asyncio
import sys
from types import SimpleNamespace

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import kg_store as _kg
from tests.test_be2_fixes import Recorder, _hooks, _run as _run_agent


# ── 1. graph_query fact ordering / citations ───────────────────────────────

PERSON = "person:michel neufeglise"


def _graph_prompt(ents, rels, message="What do we know about Michel Neuféglise across my documents?"):
    async def kg_search(t):
        return [ents[0]]

    async def kg_neighborhood(eid):
        return {"entities": ents, "relations": rels}

    rec = Recorder()
    hooks = _hooks([], rec, kg_search=kg_search, kg_neighborhood=kg_neighborhood)
    events = _run_agent({"message": message, "attachments": []}, hooks)
    return rec.prompts[0].split("Facts:")[1], events


def _demo_graph(n_docs=14):
    ents = [
        {"id": PERSON, "type": "person", "name": "Michel Neuféglise", "props": {}},
        {"id": "role:solution architect", "type": "role", "name": "Solution Architect", "props": {}},
        {"id": "role:eigenaar", "type": "role", "name": "Eigenaar", "props": {}},
        {"id": "organization:rabobank", "type": "organization", "name": "Rabobank", "props": {}},
        {"id": "organization:nds", "type": "organization", "name": "Neuféglise Digital Solutions", "props": {"demo": True}},
        {"id": "document:cv", "type": "document", "name": "cv.docx", "props": {"filename": "cv.docx"}},
        {"id": "document:reg", "type": "document", "name": "registry.pdf", "props": {"filename": "registry.pdf"}},
        {"id": "document:card", "type": "document", "name": "card.png", "props": {"filename": "card.png"}},
        {"id": "profile:linkedin.com/in/m", "type": "profile", "name": "Michel Neuféglise | LinkedIn",
         "props": {"url": "https://nl.linkedin.com/in/m", "host": "nl.linkedin.com", "platform": "linkedin"}},
    ]
    rels = [
        {"src": PERSON, "dst": "document:cv", "type": "mentioned_in", "confidence": 0.9, "run_id": "r-cv"},
        {"src": PERSON, "dst": "role:solution architect", "type": "has_role", "confidence": 0.7, "run_id": "r-cv"},
        {"src": PERSON, "dst": "organization:rabobank", "type": "works_at", "confidence": 0.7, "run_id": "r-cv"},
        {"src": PERSON, "dst": "profile:linkedin.com/in/m", "type": "likely_profile", "confidence": 0.8, "run_id": "r-web"},
        {"src": PERSON, "dst": "document:reg", "type": "mentioned_in", "confidence": 0.9, "run_id": "r-reg"},
        {"src": PERSON, "dst": "organization:nds", "type": "owns", "confidence": 0.7, "run_id": "r-reg"},
        {"src": PERSON, "dst": "document:card", "type": "mentioned_in", "confidence": 0.9, "run_id": "r-card"},
        {"src": PERSON, "dst": "role:eigenaar", "type": "has_role", "confidence": 0.7, "run_id": "r-card"},
        {"src": "document:card", "dst": PERSON, "type": "signature_specimen", "confidence": 0.9, "run_id": "r-sig",
         "props": {"n_references": 5}},
        {"src": "organization:rabobank", "dst": "document:cv", "type": "mentioned_in", "confidence": 0.9, "run_id": "r-cv"},
        {"src": "organization:nds", "dst": "document:reg", "type": "mentioned_in", "confidence": 0.9, "run_id": "r-reg"},
    ]
    # Lots of other mentions in the 2-hop neighbourhood (other people / orgs in the same documents)
    for i in range(n_docs):
        ents.append({"id": f"person:other{i}", "type": "person", "name": f"Other Person {i}", "props": {}})
        rels.append({"src": f"person:other{i}", "dst": "document:cv", "type": "mentioned_in",
                     "confidence": 0.95, "run_id": f"r-o{i}"})
    return ents, rels


def test_graph_query_keeps_likely_profile_under_the_fact_cap():
    ents, rels = _demo_graph()
    facts, events = _graph_prompt(ents, rels)
    lines = [ln for ln in facts.strip().splitlines() if ln.startswith("- ")]
    assert len(lines) == 12
    assert any("likely_profile" in ln and "linkedin.com" in ln for ln in lines)
    for t in ("owns", "signature_specimen", "works_at", "has_role"):
        assert any(f"—{t}→" in ln for ln in lines), t
    # Facts about the subject come before the neighbourhood's mentions
    subj = [i for i, ln in enumerate(lines) if "Michel" in ln]
    other = [i for i, ln in enumerate(lines) if "Other Person" in ln]
    assert len(subj) == 9 and other and max(subj) < min(other)
    # The tile items mirror the prompt facts (same order, same count)
    tile = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "kg-query"]
    if tile and tile[-1].get("items"):
        items = tile[-1]["items"]
        assert len(items) == len(lines)
        assert any("likely_profile" in it["label"] for it in items)


def test_likely_profile_ranks_above_mentioned_in():
    ents, rels = _demo_graph(n_docs=0)
    facts, _ = _graph_prompt(ents, rels)
    lines = [ln for ln in facts.strip().splitlines() if ln.startswith("- ")]
    li = next(i for i, ln in enumerate(lines) if "likely_profile" in ln)
    mi = [i for i, ln in enumerate(lines) if "—mentioned_in→" in ln and "Michel" in ln]
    assert mi and li < min(mi)


def test_roles_cite_their_own_document_and_organisation():
    ents, rels = _demo_graph(n_docs=0)
    facts, _ = _graph_prompt(ents, rels)
    eig = next(ln for ln in facts.splitlines() if "role:eigenaar" in ln.lower() or "→ Eigenaar" in ln)
    sa = next(ln for ln in facts.splitlines() if "→ Solution Architect" in ln)
    # Eigenaar was read from the signature card run only — never tied to the CV or Rabobank
    assert "(source: card.png)" in eig
    assert "Rabobank" not in eig and "cv.docx" not in eig
    # Solution Architect + Rabobank come from the same (CV) run
    assert "Rabobank" in sa and "(source: cv.docx)" in sa
    # works_at cites the CV only
    wa = next(ln for ln in facts.splitlines() if "—works_at→" in ln)
    assert "(source: cv.docx)" in wa and "card.png" not in wa


def test_graph_prompt_forbids_merging_role_and_organisation():
    import doc_agent as _agent
    assert "never pair a role from one line with an organisation from another line" in _agent.GRAPH_QUERY_PROMPT


def test_neighborhood_relations_carry_run_id():
    async def body():
        await _kg.init_db()
        await _kg.reset()
        try:
            doc = SimpleNamespace(id="cv1", filename="cv.pdf", mime="application/pdf",
                                  text="Jane Example, Engineer at Acme B.V.")
            await _kg.ingest_run(
                conversation_id="c1", run_id="run-x", intent="identify_person", subject_docs=[doc],
                answer_text="", person={"name": "Jane Example", "role": "Engineer", "org": "Acme B.V."},
            )
            return await _kg.neighborhood("person:jane example")
        finally:
            await _kg.reset()

    nb = asyncio.run(body())
    assert nb["relations"]
    assert all(r.get("run_id") == "run-x" for r in nb["relations"])


# ── 2. evidence: redaction + profile evidence ──────────────────────────────

def test_redacts_dutch_street_and_postcode():
    out = _kg.redact_contact_data("Address\nDemostraat 1, 3500 AA Utrecht (DEMO)\nWebsite")
    assert "Demostraat" not in out and "3500" not in out
    assert "[redacted] Utrecht (DEMO)" in out
    assert _kg.redact_contact_data("Hoofdstraat 12a\n1234AB Amsterdam") == "[redacted] Amsterdam"


def test_redacts_concatenated_cv_contact_block():
    cv = ("Solution ArchitectAddressKneppelhoutstraat 173515EW UtrechtMobile+316 36932338"
          "Emailjan.example@gmail.comWork experience")
    out = _kg.redact_contact_data(cv)
    for leak in ("Kneppelhout", "3515", "36932338", "+31", "gmail", "jan.example"):
        assert leak not in out, leak
    assert out.startswith("Solution ArchitectAddress[redacted]")
    assert out.endswith("Work experience")


def test_redacts_phone_numbers_and_emails():
    for phone in ("06-12345678", "06 1234 5678", "020 123 4567", "+31 6 12345678", "+31 (0)20 123 4567", "0031 6 12345678"):
        out = _kg.redact_contact_data(f"Tel: {phone}.")
        assert out == "Tel: [redacted].", (phone, out)
    assert _kg.redact_contact_data("mail info@example.nl now") == "mail [redacted] now"


def test_redaction_keeps_ordinary_text_dates_and_numbers():
    text = ("Owner (eigenaar)\nSolely authorised (alleen/zelfstandig bevoegd)\nSince\n1 March 2021\n"
            "Date 01-03-2021 12:00, 2015-10/2022-12 Angular 18, KvK 12345678, SBI 62.02")
    assert _kg.redact_contact_data(text) == text
    assert _kg.redact_contact_data(None) is None
    assert _kg.redact_contact_data("") == ""


REGISTRY_TEXT = (
    "DEMO · Company Registry Extract\nTrade name\nNeuféglise Digital Solutions\n"
    "Establishment\nAddress (fictitious)\nDemostraat 1, 3500 AA Utrecht (DEMO)\nWebsite\nexample.com\n"
    "Owner / authorised person\nName\nMichel Neuféglise\nRole\nOwner (eigenaar)\n"
    "Authority\nSolely authorised (alleen/zelfstandig bevoegd)\nSince\n1 March 2021\n"
)
CV_TEXT = (
    "Michel Neuféglise\nSolution Architect\nAddress Kneppelhoutstraat 17, 3515 EW Utrecht\n"
    "Mobile +31 6 12345678\nEmail michel@example.com\nWork experience\nSolution Architect – Rabobank\n"
)


def _kg_run(body):
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            return await body()
        finally:
            await _kg.reset()
    return asyncio.run(go())


def test_owns_evidence_keeps_ownership_wording_but_redacts_address():
    reg = SimpleNamespace(id="reg1", filename="DEMO_company_registry_extract.pdf", mime="application/pdf", text=REGISTRY_TEXT)

    async def body():
        await _kg.ingest_run(
            conversation_id="c1", run_id="run-reg", intent="cross_reference", subject_docs=[reg], answer_text="",
            persons=[{"name": "Michel Neuféglise", "doc_id": "reg1"}],
            organizations=[{"name": "Neuféglise Digital Solutions", "doc_id": "reg1", "owner": "Michel Neuféglise"}],
        )
        return await _kg.relation_evidence(
            f"{PERSON}→organization:neufeglise digital solutions→owns", get_doc_text=lambda d: REGISTRY_TEXT,
        )

    ev = _kg_run(body)
    assert ev["snippet_source"] == "document"
    s = ev["snippet"]
    assert "Owner (eigenaar)" in s and "Solely authorised" in s
    assert "Demostraat" not in s and "3500 AA" not in s
    assert ev["profile"] is None


def test_likely_profile_evidence_shows_the_profile_not_the_cv():
    cv = SimpleNamespace(id="cv1", filename="cv.docx", mime="application/pdf", text=CV_TEXT)
    url = "https://nl.linkedin.com/in/michelneufeglise"
    verdict = f"**LinkedIn:** Likely match found — [Michel Neuféglise]({url})"

    async def body():
        await _kg.ingest_run(
            conversation_id="c1", run_id="run-cv", intent="identify_person", subject_docs=[cv], answer_text="",
            person={"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"},
            web_candidates=[{"url": url, "title": "Michel Neuféglise - Solution Architect | LinkedIn",
                             "snippet": "Bekijk het profiel van Michel Neuféglise op LinkedIn. Tel 06-12345678",
                             "host": "nl.linkedin.com"}],
            verdict=verdict,
        )
        return await _kg.relation_evidence(
            f"{PERSON}→profile:linkedin.com/in/michelneufeglise→likely_profile", get_doc_text=lambda d: CV_TEXT,
        )

    ev = _kg_run(body)
    assert ev is not None and ev["relation"]["type"] == "likely_profile"
    p = ev["profile"]
    assert p["url"] == url
    assert p["title"] == "Michel Neuféglise - Solution Architect | LinkedIn"
    assert p["platform"] == "linkedin"
    assert p["host"] == "nl.linkedin.com"
    assert p["confidence"] == 0.8
    assert isinstance(p["verified_at"], str) and len(p["verified_at"]) == 10
    # No CV window: no source document, snippet is the (redacted) search snippet
    assert ev["source_doc"] is None
    assert ev["snippet_source"] == "profile"
    assert "Bekijk het profiel" in ev["snippet"] and "12345678" not in ev["snippet"]
    assert "Kneppelhout" not in (ev["snippet"] or "")


def test_document_evidence_for_cv_relations_is_redacted():
    cv = SimpleNamespace(id="cv1", filename="cv.docx", mime="application/pdf", text=CV_TEXT)

    async def body():
        await _kg.ingest_run(
            conversation_id="c1", run_id="run-cv", intent="identify_person", subject_docs=[cv], answer_text="",
            person={"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"},
        )
        return await _kg.relation_evidence(
            f"{PERSON}→role:solution architect→has_role", get_doc_text=lambda d: CV_TEXT,
        )

    ev = _kg_run(body)
    s = ev["snippet"] or ""
    assert "Solution Architect" in s
    for leak in ("Kneppelhout", "3515", "12345678", "michel@example.com"):
        assert leak not in s, leak
