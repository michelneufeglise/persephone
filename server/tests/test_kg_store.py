"""
Tests for the knowledge graph store.

Tests without network/Ollama; uses isolated test DB via conftest.py.
Uses asyncio.run() for async test execution.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio

import kg_store as _kg


# ── Test helpers ────────────────────────────────────────────────────────────

async def _setup():
    """Initialize a fresh knowledge graph for tests."""
    await _kg.init_db()
    await _kg.reset()


async def _teardown():
    """Clean up after tests."""
    await _kg.reset()


# ── Tests ────────────────────────────────────────────────────────────────────

def test_upsert_entity():
    """Test upserting an entity."""
    async def run_test():
        await _setup()
        try:
            entity_id = await _kg.upsert_entity("person", "Michel Neuféglise")
            assert entity_id == "person:michel neufeglise"

            # Verify it was stored
            entity = await _kg.get_entity(entity_id)
            assert entity is not None
            assert entity["name"] == "Michel Neuféglise"
            assert entity["type"] == "person"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_entity_merge_properties():
    """Test that properties are merged on upsert."""
    async def run_test():
        await _setup()
        try:
            entity_id = await _kg.upsert_entity("person", "Michel Neuféglise", {"role": "Architect"})

            # Upsert again with additional properties
            entity_id2 = await _kg.upsert_entity("person", "Michel Neuféglise", {"org": "Rabobank"})
            assert entity_id == entity_id2

            # Verify properties were merged
            entity = await _kg.get_entity(entity_id)
            assert entity["props"]["role"] == "Architect"
            assert entity["props"]["org"] == "Rabobank"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_accent_insensitive_normalization():
    """Test that accents are stripped in normalization."""
    async def run_test():
        await _setup()
        try:
            entity_id1 = await _kg.upsert_entity("person", "Neuféglise")
            entity_id2 = await _kg.upsert_entity("person", "Neufeglise")

            # Should be the same normalized ID
            assert entity_id1 == entity_id2
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_add_relation():
    """Test adding a relation between entities."""
    async def run_test():
        await _setup()
        try:
            person_id = await _kg.upsert_entity("person", "Michel Neuféglise")
            role_id = await _kg.upsert_entity("role", "Solution Architect")

            rel_id = await _kg.add_relation(
                person_id, role_id, "has_role",
                confidence=0.9,
                source="doc_agent"
            )
            assert rel_id is not None

            # Verify the relation was stored
            entity = await _kg.get_entity(person_id)
            assert len(entity["relations_out"]) == 1
            assert entity["relations_out"][0]["type"] == "has_role"
            assert entity["relations_out"][0]["confidence"] == 0.9
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_relation_upsert_keeps_max_confidence():
    """Test that relation upsert keeps max confidence."""
    async def run_test():
        await _setup()
        try:
            person_id = await _kg.upsert_entity("person", "Alice")
            org_id = await _kg.upsert_entity("organization", "Acme Corp")

            # First relation with confidence 0.7
            rel_id1 = await _kg.add_relation(
                person_id, org_id, "works_at",
                confidence=0.7,
                source="source1"
            )

            # Second relation with confidence 0.9 (should update max)
            rel_id2 = await _kg.add_relation(
                person_id, org_id, "works_at",
                confidence=0.9,
                source="source2"
            )

            assert rel_id1 == rel_id2  # Same relation ID

            # Verify confidence was updated to max
            entity = await _kg.get_entity(person_id)
            assert entity["relations_out"][0]["confidence"] == 0.9
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_add_mention():
    """Test adding a mention."""
    async def run_test():
        await _setup()
        try:
            person_id = await _kg.upsert_entity("person", "Michel Neuféglise")

            mention_id = await _kg.add_mention(
                person_id,
                "doc-123",
                chunk_id=5,
                snippet="Michel Neuféglise is a Solution Architect.",
                run_id="run-456"
            )
            assert mention_id is not None

            # Verify the mention was stored
            entity = await _kg.get_entity(person_id)
            assert len(entity["mentions"]) == 1
            assert entity["mentions"][0]["doc_id"] == "doc-123"
            assert "Architect" in entity["mentions"][0]["snippet"]
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_search_entities():
    """Test searching for entities."""
    async def run_test():
        await _setup()
        try:
            # Add some entities
            await _kg.upsert_entity("person", "Michel Neuféglise")
            await _kg.upsert_entity("person", "Alice Smith")
            await _kg.upsert_entity("organization", "Rabobank")

            # Search for Michel
            results = await _kg.search_entities("Michel", limit=5)
            assert len(results) > 0
            assert any(r["type"] == "person" and "Michel" in r["name"] for r in results)

            # Persons should come first
            if len(results) > 1:
                assert results[0]["type"] == "person"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_search_entities_token_matching():
    """Test search_entities with token-based matching and stopword filtering."""
    async def run_test():
        await _setup()
        try:
            # Create test entities
            jane_id = await _kg.upsert_entity("person", "Jane Example")
            senior_id = await _kg.upsert_entity("role", "Senior Engineer")
            acme_id = await _kg.upsert_entity("organization", "Acme")

            # Create relations to give Jane mentions
            await _kg.add_relation(jane_id, senior_id, "has_role", confidence=0.9, source="test")
            await _kg.add_relation(jane_id, acme_id, "works_at", confidence=0.9, source="test")

            # Test 1: Query "what do we know about Jane Example across my documents"
            # Should extract "jane example" after stopwords are filtered
            results = await _kg.search_entities("what do we know about Jane Example across my documents", limit=5)
            assert len(results) > 0
            assert results[0]["type"] == "person"
            assert "Jane" in results[0]["name"] or "jane" in results[0]["name"].lower()

            # Test 2: Query with just "Jane" should still match Jane Example
            results = await _kg.search_entities("Jane", limit=5)
            assert len(results) > 0
            assert any("Jane" in r["name"] for r in results)

            # Test 3: Persons should come first even if other entities have mentions
            results = await _kg.search_entities("Jane Example", limit=10)
            if len(results) > 1:
                assert results[0]["type"] == "person"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_document_entity_with_filename():
    """Test that document entity uses filename as name and doc_id in props."""
    async def run_test():
        await _setup()
        try:
            # Create document entity with explicit entity_id
            doc_entity_id = await _kg.upsert_entity(
                "document",
                "my_cv.pdf",  # filename as name
                {"doc_id": "doc-123", "filename": "my_cv.pdf", "kind": "pdf"},
                entity_id="document:doc-123"  # explicit entity_id
            )

            # Entity ID should be f"document:{doc_id}"
            assert doc_entity_id == "document:doc-123"

            # Get the entity and verify name is filename
            entity = await _kg.get_entity(doc_entity_id)
            assert entity is not None
            assert entity["name"] == "my_cv.pdf"
            assert entity["props"]["doc_id"] == "doc-123"
            assert entity["props"]["filename"] == "my_cv.pdf"

            # Get graph and verify documents section uses filename
            graph = await _kg.get_graph()
            docs = graph["documents"]
            assert len(docs) > 0
            assert any(d["name"] == "my_cv.pdf" for d in docs)
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_neighborhood():
    """Test neighborhood traversal."""
    async def run_test():
        await _setup()
        try:
            person_id = await _kg.upsert_entity("person", "Michel")
            role_id = await _kg.upsert_entity("role", "Architect")
            org_id = await _kg.upsert_entity("organization", "Rabobank")
            doc_id = await _kg.upsert_entity("document", "cv.pdf", {"doc_id": "doc-1"})

            # Create relations
            await _kg.add_relation(person_id, role_id, "has_role", confidence=0.8, source="test")
            await _kg.add_relation(person_id, org_id, "works_at", confidence=0.8, source="test")
            await _kg.add_relation(person_id, doc_id, "mentioned_in", confidence=0.9, source="test")

            # Get neighborhood
            neighborhood = await _kg.neighborhood(person_id, hops=1, limit=10)

            # Should have the person + 3 neighbors + 3 relations
            assert len(neighborhood["entities"]) >= 1  # At least the root entity
            assert len(neighborhood["relations"]) >= 3
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_get_graph_shape():
    """Test that get_graph returns the correct JSON shape."""
    async def run_test():
        await _setup()
        try:
            # Add some data
            person_id = await _kg.upsert_entity("person", "Michel")
            org_id = await _kg.upsert_entity("organization", "Rabobank")
            doc_id = await _kg.upsert_entity("document", "cv.pdf", {"doc_id": "doc-1"})

            await _kg.add_relation(person_id, org_id, "works_at", confidence=0.8, source="doc_agent")
            await _kg.add_mention(person_id, "doc-1", run_id="run-1")

            # Get graph
            graph = await _kg.get_graph(scope="all")

            # Check shape
            assert "entities" in graph
            assert "relations" in graph
            assert "documents" in graph
            assert "stats" in graph

            # Check entity structure
            assert len(graph["entities"]) >= 3
            for entity in graph["entities"]:
                assert "id" in entity
                assert "type" in entity
                assert "name" in entity
                assert "props" in entity
                assert "mention_count" in entity

            # Check relation structure
            for rel in graph["relations"]:
                assert "id" in rel
                assert "src" in rel
                assert "dst" in rel
                assert "type" in rel
                assert "confidence" in rel
                assert "source" in rel
                assert "props" in rel

            # Check document structure
            for doc in graph["documents"]:
                assert "doc_id" in doc
                assert "name" in doc
                assert "chunk_count" in doc
                assert "mention_count" in doc

            # Check stats structure
            assert "entities" in graph["stats"]
            assert "relations" in graph["stats"]
            assert "mentions" in graph["stats"]
            assert "documents" in graph["stats"]
            assert "chunks" in graph["stats"]
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_delete_document():
    """Test deleting a document and its mentions."""
    async def run_test():
        await _setup()
        try:
            person_id = await _kg.upsert_entity("person", "Michel")
            doc_id_str = "doc-1"
            # Create document entity with doc_id as the name (as ingest_run does)
            doc_entity_id = await _kg.upsert_entity("document", doc_id_str, {"filename": "cv.pdf", "doc_id": doc_id_str})

            # Add a mention
            await _kg.add_mention(person_id, doc_id_str, run_id="run-1")

            # Verify it exists
            graph_before = await _kg.get_graph(scope="all")
            assert len(graph_before["entities"]) >= 2

            # Delete the document
            await _kg.delete_document(doc_id_str)

            # Verify it was deleted
            graph_after = await _kg.get_graph(scope="all")
            # The person was only known through doc-1 (no other mention or
            # relation), so it is garbage-collected along with the document.
            assert len(graph_after["entities"]) == 0
            assert not any(e["type"] == "document" for e in graph_after["entities"])
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_ingest_run_with_person():
    """Test ingesting a document agent run with person extraction."""
    async def run_test():
        await _setup()
        try:
            from types import SimpleNamespace

            # Create a fake document
            doc = SimpleNamespace(
                id="doc-1",
                filename="cv.pdf",
                mime="application/pdf",
                text="Michel Neuféglise\nSolution Architect – Rabobank"
            )

            # Ingest a run
            counts = await _kg.ingest_run(
                conversation_id="conv-1",
                run_id="run-1",
                intent="identify_person",
                subject_docs=[doc],
                answer_text="This document is about Michel Neuféglise.",
                person={"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"},
                web_candidates=[],
                verdict=None
            )

            # Should have created entities and relations
            assert counts["entities"] >= 4  # document, person, role, org
            assert counts["relations"] >= 3  # mentioned_in, has_role, works_at
            assert counts["mentions"] >= 1

            # Verify entities were created
            person_id = f"person:{_kg._normalize_text('Michel Neuféglise')}"
            entity = await _kg.get_entity(person_id)
            assert entity is not None
            assert entity["type"] == "person"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_ingest_run_with_web_candidates():
    """Test ingesting with web lookup candidates."""
    async def run_test():
        await _setup()
        try:
            from types import SimpleNamespace

            doc = SimpleNamespace(
                id="doc-1",
                filename="cv.pdf",
                mime="application/pdf",
                text="Michel Neuféglise"
            )

            candidates = [
                {
                    "url": "https://nl.linkedin.com/in/michelneufeglise",
                    "title": "Michel Neuféglise - LinkedIn",
                    "snippet": "Solution Architect at Rabobank",
                    "host": "linkedin.com"
                }
            ]

            verdict = """**Likely match found** — Based on the search results:
- [Michel Neuféglise - LinkedIn](https://nl.linkedin.com/in/michelneufeglise)"""

            counts = await _kg.ingest_run(
                conversation_id="conv-1",
                run_id="run-1",
                intent="identify_person",
                subject_docs=[doc],
                answer_text="Found online",
                person={"name": "Michel Neuféglise", "role": "Architect", "org": "Rabobank"},
                web_candidates=candidates,
                verdict=verdict
            )

            # Should have created profile entity and relations
            assert counts["entities"] >= 5  # document, person, role, org, profile
            assert counts["relations"] >= 4  # + candidate_profile + likely_profile

            # Verify profile was created
            graph = await _kg.get_graph(scope="all")
            assert any(e["type"] == "profile" for e in graph["entities"])
            assert any(r["type"] == "likely_profile" for r in graph["relations"])
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_ingest_run_per_platform_verdict():
    """Per-platform verdict: the Facebook profile cited on the Facebook "Likely match
    found" line becomes likely_profile; the LinkedIn "Possible match" stays a candidate;
    social non-profile pages (directories, posts) are skipped."""
    async def run_test():
        await _setup()
        try:
            from types import SimpleNamespace

            doc = SimpleNamespace(id="doc-fb", filename="cv.pdf", mime="application/pdf", text="Jane Example")
            candidates = [
                {"url": "https://www.linkedin.com/in/jane-example", "title": "Jane Example | LinkedIn",
                 "snippet": "Engineer", "platform": "linkedin", "is_profile": True},
                {"url": "https://www.facebook.com/jane.example", "title": "Jane Example | Facebook",
                 "snippet": "Works at Acme", "platform": "facebook", "is_profile": True},
                {"url": "https://www.facebook.com/public/Jane-Example", "title": "Jane Example profiles | Facebook",
                 "snippet": "Directory", "platform": "facebook", "is_profile": False},
                {"url": "https://www.facebook.com/jane.example/posts/123", "title": "Jane Example post",
                 "snippet": "A post", "platform": "facebook", "is_profile": False},
            ]
            verdict = (
                "**LinkedIn:** Possible match — same name [Jane Example | LinkedIn](https://www.linkedin.com/in/jane-example)\n"
                "**Facebook:** Likely match found — [x](https://www.facebook.com/jane.example)\n"
                "**Overall:** Likely match found on Facebook.\n"
                "_Search results are not proof of identity._"
            )

            await _kg.ingest_run(
                conversation_id="conv-fb",
                run_id="run-fb",
                intent="identify_person",
                subject_docs=[doc],
                answer_text="Found online",
                person={"name": "Jane Example", "role": None, "org": None},
                web_candidates=candidates,
                verdict=verdict,
            )

            graph = await _kg.get_graph(scope="all")
            profiles = {e["id"]: e for e in graph["entities"] if e["type"] == "profile"}
            by_url = {(e.get("props") or {}).get("url"): e for e in profiles.values()}
            assert set(by_url) == {
                "https://www.linkedin.com/in/jane-example",
                "https://www.facebook.com/jane.example",
            }
            fb = by_url["https://www.facebook.com/jane.example"]
            li = by_url["https://www.linkedin.com/in/jane-example"]
            assert fb["props"]["platform"] == "facebook"
            assert li["props"]["platform"] == "linkedin"

            rels = graph["relations"]
            likely = {r["dst"] for r in rels if r["type"] == "likely_profile"}
            candidate = {r["dst"] for r in rels if r["type"] == "candidate_profile"}
            assert likely == {fb["id"]}
            # ONE relation per (person, profile): likely_profile replaces candidate_profile
            assert candidate == {li["id"]}
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_ingest_run_legacy_verdict_likely_profile():
    """Backward compat: single "**Likely match found**" verdict → first cited URL is likely."""
    async def run_test():
        await _setup()
        try:
            from types import SimpleNamespace

            doc = SimpleNamespace(id="doc-li", filename="cv.pdf", mime="application/pdf", text="Jane Example")
            candidates = [
                {"url": "https://nl.linkedin.com/in/jane-example", "title": "Jane Example - LinkedIn", "snippet": "x"},
                {"url": "https://example.com/jane", "title": "Jane Example homepage", "snippet": "y"},
            ]
            verdict = "**Likely match found** — ok\n- [Jane](https://nl.linkedin.com/in/jane-example)\n- [Home](https://example.com/jane)"
            await _kg.ingest_run(
                conversation_id="conv-li", run_id="run-li", intent="identify_person",
                subject_docs=[doc], answer_text="", person={"name": "Jane Example"},
                web_candidates=candidates, verdict=verdict,
            )
            graph = await _kg.get_graph(scope="all")
            by_url = {(e.get("props") or {}).get("url"): e for e in graph["entities"] if e["type"] == "profile"}
            likely = {r["dst"] for r in graph["relations"] if r["type"] == "likely_profile"}
            assert likely == {by_url["https://nl.linkedin.com/in/jane-example"]["id"]}
            assert by_url["https://example.com/jane"]["props"]["platform"] == "web"
        finally:
            await _teardown()

    asyncio.run(run_test())


def test_graph_query_intent():
    """Test that rules_intent detects graph_query."""
    from doc_agent import rules_intent

    # Test various graph_query keywords
    test_cases = [
        "what do we know about Michel Neuféglise",
        "what do you know about Rabobank",
        "what do i know about",
        "across all documents",
        "knowledge graph",
        "which documents mention Alice",
    ]

    for message in test_cases:
        intent, kws = rules_intent(message, [])
        assert intent == "graph_query", f"Failed for: {message}"


# ── Integration test ──

def test_full_workflow():
    """Test a complete workflow: create entities, relations, query them."""
    async def run_test():
        await _setup()
        try:
            # 1. Create knowledge base with multiple sources
            person_id = await _kg.upsert_entity("person", "Michel Neuféglise")
            org_id = await _kg.upsert_entity("organization", "Rabobank")
            role_id = await _kg.upsert_entity("role", "Solution Architect")
            doc_id = await _kg.upsert_entity("document", "cv.pdf", {"doc_id": "doc-1"})

            # 2. Create relations
            await _kg.add_relation(person_id, role_id, "has_role", confidence=0.9, source="cv")
            await _kg.add_relation(person_id, org_id, "works_at", confidence=0.9, source="cv")
            await _kg.add_relation(person_id, doc_id, "mentioned_in", confidence=1.0, source="cv")

            # 3. Search
            results = await _kg.search_entities("Michel", limit=5)
            assert len(results) > 0
            assert results[0]["id"] == person_id

            # 4. Get neighborhood
            neighborhood = await _kg.neighborhood(person_id, hops=2)
            assert len(neighborhood["entities"]) >= 4
            assert len(neighborhood["relations"]) >= 3

            # 5. Get full graph
            graph = await _kg.get_graph(scope="all")
            assert graph["stats"]["entities"] >= 4
            assert graph["stats"]["relations"] >= 3
            assert len(graph["documents"]) >= 1
        finally:
            await _teardown()

    asyncio.run(run_test())
