"""
Roles / organisations parsed from identify_person answers into the knowledge store:

 1  a parenthetical organisation in a "Role:" line is the employer (works_at),
    never a role; "(supplier)"-style words never become a role by themselves
 2  "Job Title:" / "Functie:" / "Title:" preferred over "Role:"; no entities
    literally named "Job Title" / "Role" / "Company"
 3  names normalised: no trailing period ("…signer."), "B.V.." → "B.V."
 4  a title stated in the document next to the person's name wins
    ("Sanne de Vries, Account Manager"); otherwise a descriptive role is shortened
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from types import SimpleNamespace

import doc_web as _web
import kg_store as _kg
from test_be2_fixes import _kg_run


AGREEMENT = (
    "DEMO · Fictitious document for a software demo · not a real agreement\n"
    "Service Agreement\n"
    "Reference DEMO-SA-2026-0929\n"
    "Supplier\n"
    "Noordlicht Cloud Services B.V. (DEMO)\n"
    "Located in Groningen, the Netherlands\n"
    "Represented by Sanne de Vries, Account Manager\n"
    "Customer\n"
    "Neuféglise Digital Solutions\n"
    "Represented by Michel Neuféglise, Owner (eigenaar)\n"
    "Services\n"
    "Managed cloud hosting for customer applications\n"
    "Contacts\n"
    "Technical contact (supplier): Jeroen Bakker, Cloud Engineer\n"
    "Commercial contact (supplier): Sanne de Vries, Account Manager\n"
    "Signed for the supplier:  S. de Vries\n"
    "Signed for the customer:  M. Neuféglise\n"
)

# gemma4:12b's answer to "Who are the people in this document and which
# companies do they work for?" (captured from a live run)
ANSWER = (
    "The following people are mentioned in the document:\n\n"
    "*   **Sanne de Vries**\n"
    "    *   **Role:** Representative for the supplier (Noordlicht Cloud Services B.V.) and signer.\n"
    "    *   **Supporting Text:** \"Represented by Sanne de Vries, Account Manager\" and "
    "\"Signed for the supplier: S. de Vries\"\n"
    "*   **Michel Neuféglise**\n"
    "    *   **Role:** Representative for the customer (Neuféglise Digital Solutions) and signer.\n"
    "    *   **Supporting Text:** \"Represented by Michel Neuféglise, Owner (eigenaar)\" and "
    "\"Signed for the customer: M. Neuféglise\"\n"
    "*   **Jeroen Bakker**\n"
    "    *   **Role:** Technical contact for the supplier (Noordlicht Cloud Services B.V.).\n"
    "    *   **Supporting Text:** \"Technical contact (supplier): Jeroen Bakker, Cloud Engineer\"\n"
)

ANSWER_JOB_TITLE = (
    "*   **Sanne de Vries**\n"
    "    *   **Job Title:** Account Manager\n"
    "    *   **Role:** Representative of the supplier and signer.\n"
    "    *   **Company:** Noordlicht Cloud Services B.V.\n"
    "*   **Jeroen Bakker**\n"
    "    *   **Job Title**: Cloud Engineer\n"
    "    *   **Company**: Noordlicht Cloud Services B.V.\n"
)


def _by_name(persons):
    return {p["name"]: p for p in persons}


# ── 1. parentheticals ─────────────────────────────────────────────────────

def test_parenthetical_legal_form_is_the_org_not_the_role():
    role, org = _web._role_org_from(
        "- **Role:** Representative for the supplier (Noordlicht Cloud Services B.V.) and signer."
    )
    assert org == "Noordlicht Cloud Services B.V."
    assert role == "Representative for the supplier and signer"


def test_parenthetical_org_keeps_explicit_company_label():
    role, org = _web._role_org_from(
        "- **Role:** Account Manager (Acme B.V.)\n- **Company:** Foo Corp"
    )
    assert role == "Account Manager" and org == "Foo Corp"


def test_parenthetical_known_org_without_legal_form():
    role, org = _web._role_org_from(
        "- **Role:** Representative for the customer (Neuféglise Digital Solutions) and signer.",
        known_orgs=["Neuféglise Digital Solutions"],
    )
    assert role == "Representative for the customer and signer"
    assert org == "Neuféglise Digital Solutions"


def test_parenthetical_capitalised_name_is_org_not_role():
    role, org = _web._role_org_from("- **Role:** Representative for the customer (Neuféglise Digital Solutions)")
    assert role == "Representative for the customer"
    assert org == "Neuféglise Digital Solutions"


def test_parenthetical_generic_party_word_never_the_role():
    role, org = _web._role_org_from("- **Role:** Account Manager (supplier)")
    assert role == "Account Manager" and org is None
    role, org = _web._role_org_from("- **Role:** (supplier)")
    assert role is None and org is None
    role, _ = _web._role_org_from("- **Role:** Contactpersoon (klant)")
    assert role == "Contactpersoon"


def test_parenthetical_title_still_preferred_inside():
    role, _ = _web._role_org_from("- **Role:** Subject of the CV (Solution Architect)")
    assert role == "Solution Architect"


def test_extract_persons_on_live_answer_has_no_company_roles():
    found = _by_name(_web.extract_persons(ANSWER))
    assert set(found) == {"Sanne de Vries", "Michel Neuféglise", "Jeroen Bakker"}
    assert found["Sanne de Vries"]["org"] == "Noordlicht Cloud Services B.V."
    assert found["Jeroen Bakker"]["org"] == "Noordlicht Cloud Services B.V."
    assert found["Michel Neuféglise"]["org"] == "Neuféglise Digital Solutions"
    for p in found.values():
        assert p["role"] and not _web.looks_like_legal_org(p["role"])
        assert "Noordlicht" not in p["role"] and "Solutions" not in p["role"]
        assert p["role"].lower() != "supplier"


# ── 2. "Job Title:" preference, no label entities ─────────────────────────

def test_job_title_preferred_over_role():
    found = _by_name(_web.extract_persons(ANSWER_JOB_TITLE))
    assert found["Sanne de Vries"]["role"] == "Account Manager"
    assert found["Jeroen Bakker"]["role"] == "Cloud Engineer"
    assert found["Jeroen Bakker"]["org"] == "Noordlicht Cloud Services B.V."


def test_functie_and_title_labels():
    assert _web._role_org_from("Functie: Eigenaar\nRole: Signer of the contract")[0] == "Eigenaar"
    assert _web._role_org_from("- **Title:** Cloud Engineer\n- **Role:** Technical contact")[0] == "Cloud Engineer"


def test_no_entity_named_like_a_label():
    names = {p["name"] for p in _web.extract_persons(ANSWER_JOB_TITLE)}
    assert not any(_web.is_label_word(n) for n in names)
    assert "Job Title" not in names
    single = _web.extract_person("**Job Title:** Account Manager\n**Company:** Acme", [])
    assert single is None or single["name"] != "Job Title"
    assert _web.is_label_word("Job Title") and _web.is_label_word("Role") and _web.is_label_word("Company")
    assert not _web.is_label_word("Sanne de Vries")


def test_bold_name_followed_by_colon_is_still_a_person():
    """"**Sanne de Vries**: Account Manager" names a person (only label words are skipped)."""
    for text in (
        "1. **Sanne de Vries**: Account Manager\n2. **Jeroen Bakker**: Cloud Engineer",
        "- **Sanne de Vries:** Account Manager\n- **Jeroen Bakker:** Cloud Engineer",
    ):
        names = [p["name"] for p in _web.extract_persons(text)]
        assert names == ["Sanne de Vries", "Jeroen Bakker"]


def test_ingest_never_stores_label_entities():
    doc = SimpleNamespace(id="doc-l", filename="agreement.pdf", mime="application/pdf", text=AGREEMENT)

    async def body():
        await _kg.ingest_run(
            conversation_id="c", run_id="r", intent="identify_person", subject_docs=[doc], answer_text="",
            persons=[{"name": "Job Title", "role": "Account Manager"},
                     {"name": "Sanne de Vries", "role": "Role", "org": "Company"}],
        )
        conn = _kg._connect()
        try:
            return [(r["type"], r["name"]) for r in conn.execute("SELECT type, name FROM kg_entities")]
        finally:
            conn.close()

    ents = _kg_run(body)
    assert not any(_web.is_label_word(n) for t, n in ents if t != "document")
    assert ("person", "Sanne de Vries") in ents


# ── 3. punctuation normalisation ─────────────────────────────────────────

def test_clean_entity_name_trailing_punctuation():
    c = _web.clean_entity_name
    assert c("Representative of the supplier and signer.") == "Representative of the supplier and signer"
    assert c("Account Manager;") == "Account Manager"
    assert c("  Cloud Engineer ,") == "Cloud Engineer"
    assert c("Noordlicht Cloud Services B.V.") == "Noordlicht Cloud Services B.V."
    assert c("Noordlicht Cloud Services B.V..") == "Noordlicht Cloud Services B.V."
    assert c("Acme B.V") == "Acme B.V."
    assert c("Acme N.V.:") == "Acme N.V."
    assert c("Acme Inc.") == "Acme Inc."
    assert c("Acme Inc") == "Acme Inc"
    assert c("**Sanne de Vries.**") == "Sanne de Vries"
    assert c("") == "" and c(None) == ""


def test_clean_org_name_drops_demo_and_party_parentheticals():
    assert _web.clean_org_name("Noordlicht Cloud Services B.V. (DEMO)") == "Noordlicht Cloud Services B.V."
    assert _web.clean_org_name("Acme B.V. (supplier).") == "Acme B.V."
    assert _web.clean_org_name("Acme (Amsterdam)") == "Acme (Amsterdam)"


def test_ingest_normalises_stored_names():
    doc = SimpleNamespace(id="doc-p", filename="x.pdf", mime="application/pdf", text="Jane Example signed.")

    async def body():
        await _kg.ingest_run(
            conversation_id="c", run_id="r", intent="identify_person", subject_docs=[doc], answer_text="",
            persons=[{"name": "Jane Example.", "role": "Technical contact for the supplier.",
                      "org": "Acme Cloud B.V.."}],
        )
        conn = _kg._connect()
        try:
            return [(r["type"], r["name"]) for r in conn.execute("SELECT type, name FROM kg_entities")]
        finally:
            conn.close()

    ents = _kg_run(body)
    assert ("person", "Jane Example") in ents
    assert ("role", "Technical contact for the supplier") in ents
    assert ("organization", "Acme Cloud B.V.") in ents
    for t, n in ents:
        if t != "document":
            assert not n.endswith("..") and (not n.endswith(".") or n.endswith("B.V."))


# ── 4. title from the document / fallback shortening ──────────────────────

def test_title_from_document_for_agreement_people():
    t = _web.title_from_document
    assert t("Sanne de Vries", AGREEMENT) == "Account Manager"
    assert t("Jeroen Bakker", AGREEMENT) == "Cloud Engineer"
    # "Owner" alone reads as a generic "CV owner"; the document's own
    # parenthetical title is used instead
    assert t("Michel Neuféglise", AGREEMENT) in ("Owner", "Eigenaar")
    assert t("Michel Neufeglise", AGREEMENT) in ("Owner", "Eigenaar")  # accent-insensitive


def test_title_from_document_conservative():
    t = _web.title_from_document
    assert t("Jane Doe", "Signed: Jane Doe, Amsterdam") is None
    assert t("Jane Doe", "Jane Doe, Acme Cloud Services B.V.") is None
    assert t("Jane Doe", "Jane Doe (Cloud Engineer) signed") == "Cloud Engineer"
    assert t("Jane Doe", "Jane Doe, Account Manager at Acme B.V.") == "Account Manager"
    assert t("Jane Doe", "Jane Doe, Head of Sales") == "Head of Sales"
    assert t("Jane Doe", "Jane Doe\nAccount Manager") is None  # never across lines
    assert t("Jane Doe", "") is None and t("", AGREEMENT) is None


def test_concise_role_fallback():
    c = _web.concise_role
    assert c("Representative of the supplier and signer.") == "Representative of the supplier"
    assert c("Technical contact for the supplier.") == "Technical contact for the supplier"
    assert c("Head of Sales, EMEA") == "Head of Sales"
    assert c("Research and Development Manager") == "Research and Development Manager"
    assert c("Person responsible for the day to day operations of the account") == "Person responsible for the day"
    assert len(c("one two three four five six seven").split()) == 5
    assert c(None) is None and c(" . ") is None


def test_refine_person_facts_for_agreement_answer():
    found = _web.extract_persons(ANSWER)
    names = [p["name"] for p in found]
    got = {
        p["name"]: _web.refine_person_facts(p["name"], p["role"], p["org"], AGREEMENT, [], names)
        for p in found
    }
    assert got["Sanne de Vries"] == ("Account Manager", "Noordlicht Cloud Services B.V.")
    assert got["Jeroen Bakker"] == ("Cloud Engineer", "Noordlicht Cloud Services B.V.")
    role, org = got["Michel Neuféglise"]
    assert role in ("Owner", "Eigenaar") and org == "Neuféglise Digital Solutions"


def test_refine_role_equal_to_known_org_moves_to_org():
    role, org = _web.refine_person_facts(
        "Jane Example", "Neuféglise Digital Solutions", None, "", ["Neuféglise Digital Solutions"], [],
    )
    assert role is None and org == "Neuféglise Digital Solutions"
    role, org = _web.refine_person_facts("Jane Example", "Acme B.V.", None, "", [], [])
    assert role is None and org == "Acme B.V."


def test_refine_without_document_title_shortens_role():
    role, org = _web.refine_person_facts(
        "Jane Example", "Representative for the supplier (Acme B.V.) and signer.", None, "Jane Example signed.",
    )
    assert role == "Representative for the supplier" and org == "Acme B.V."


def test_ingest_agreement_stores_titles_and_employers():
    doc = SimpleNamespace(id="doc-a", filename="DEMO_service_agreement.pdf", mime="application/pdf",
                          text=AGREEMENT)
    persons = _web.extract_persons(ANSWER)

    async def body():
        kw = dict(intent="identify_person", subject_docs=[doc], answer_text=ANSWER, persons=persons)
        c1 = await _kg.ingest_run(conversation_id="c1", run_id="run-1", **kw)
        c2 = await _kg.ingest_run(conversation_id="c2", run_id="run-2", **kw)
        conn = _kg._connect()
        try:
            rels = [
                (r["s"], r["t"], r["d"]) for r in conn.execute(
                    "SELECT s.name AS s, r.type AS t, d.name AS d FROM kg_relations r "
                    "JOIN kg_entities s ON s.id = r.src_id JOIN kg_entities d ON d.id = r.dst_id"
                )
            ]
            roles = [r["name"] for r in conn.execute("SELECT name FROM kg_entities WHERE type='role'")]
        finally:
            conn.close()
        return c1, c2, rels, roles

    c1, c2, rels, roles = _kg_run(body)
    assert ("Sanne de Vries", "has_role", "Account Manager") in rels
    assert ("Jeroen Bakker", "has_role", "Cloud Engineer") in rels
    assert any(s == "Michel Neuféglise" and t == "has_role" and d in ("Owner", "Eigenaar") for s, t, d in rels)
    assert ("Sanne de Vries", "works_at", "Noordlicht Cloud Services B.V.") in rels
    assert ("Jeroen Bakker", "works_at", "Noordlicht Cloud Services B.V.") in rels
    assert ("Michel Neuféglise", "works_at", "Neuféglise Digital Solutions") in rels
    # no company names or party words as roles, no trailing punctuation
    michel_role = next(d for s, t, d in rels if s == "Michel Neuféglise" and t == "has_role")
    assert set(roles) == {"Account Manager", "Cloud Engineer", michel_role}
    assert c1["entities"] > 0 and c1["relations"] > 0
    # the same answer again adds nothing new
    assert c2["entities"] == 0 and c2["relations"] == 0
