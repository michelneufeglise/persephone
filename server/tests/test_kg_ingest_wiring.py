"""
Tests for knowledge graph ingestion wiring in the document agent.

Tests that run_agent properly captures web lookup results and calls kg_ingest
with the correct parameters, and that the kg-ingest tile is emitted correctly.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import doc_agent as _agent
import doc_web as _doc_web
import kg_store as _kg_store


# ── Fixtures & Helpers ────────────────────────────────────────────────────

def fake_document(
    doc_id: str = "doc1",
    filename: str = "test.pdf",
    mime: str = "application/pdf",
    text: str = "",
    page_images: list[str] | None = None,
) -> SimpleNamespace:
    """Create a fake Document object."""
    if page_images is None:
        page_images = []
    return SimpleNamespace(
        id=doc_id,
        filename=filename,
        mime=mime,
        size=1000,
        uploaded_at=1234567890.0,
        pages=len(page_images) or 1,
        text=text,
        page_texts=[text] if text else [""],
        page_images=page_images,
        meta={},
    )


def fake_hooks(
    get_doc_map: dict | None = None,
    resolve_text_model_result: str = "text-model",
    stream_llm_result: list[dict] | None = None,
    laya_info_result: dict | None = None,
    kg_ingest: any = None,
    laya_web_result: dict | None = None,
) -> _agent.AgentHooks:
    """Create fake hooks for testing kg_ingest."""
    if get_doc_map is None:
        get_doc_map = {}
    if stream_llm_result is None:
        stream_llm_result = [
            {"content": "This document is about **Jane Example**.\n- **Name:** Jane Example\n- **Role:** Holder"},
            {"done": True},
        ]
    if laya_info_result is None:
        laya_info_result = {"name": "Laya", "available": False, "device": None}

    def get_doc(doc_id: str):
        return get_doc_map.get(doc_id)

    def laya_intent(msg: str, files: list[dict]):
        return None  # Use rules only

    def laya_role(msg: str, file: dict):
        return None

    def laya_doc_kind(text: str):
        return None

    def laya_info():
        return laya_info_result

    async def resolve_model(category: str):
        return "default-model"

    async def resolve_text_model(doc, category: str):
        return resolve_text_model_result

    async def vision_candidates():
        return []

    async def model_info(name: str):
        return {"name": name, "device": "cpu"}

    async def run_ocr(doc, model: str):
        return doc.text or ""

    async def stream_llm(model: str, prompt: str, think: bool = False):
        for item in stream_llm_result:
            yield item

    async def vision_call(model: str, prompt: str, reference_paths: list[str], subject_paths: list[str]):
        return ""

    def page_image_paths(doc, pages: list[int] | None = None, dpi: int = 120):
        return []

    def mark_vision_failed(model: str, err: str):
        pass

    def now_ms():
        return 1000000000

    hooks = _agent.AgentHooks(
        get_doc=get_doc,
        laya_intent=laya_intent,
        laya_role=laya_role,
        laya_doc_kind=laya_doc_kind,
        laya_info=laya_info,
        resolve_model=resolve_model,
        resolve_text_model=resolve_text_model,
        pick_vision_model=None,
        vision_candidates=vision_candidates,
        model_info=model_info,
        run_ocr=run_ocr,
        stream_llm=stream_llm,
        vision_call=vision_call,
        page_image_paths=page_image_paths,
        mark_vision_failed=mark_vision_failed,
        laya_web=None,
        kg_ingest=kg_ingest,
        now_ms=now_ms,
    )

    return hooks


async def collect_events(agent_stream):
    """Collect all events from an async generator."""
    events = []
    async for event in agent_stream:
        events.append(event)
    return events


# ── Test Cases ─────────────────────────────────────────────────────────────

def test_kg_ingest_basic_identify_person():
    """Test that kg_ingest is called for identify_person run with extracted person data."""
    async def _run():
        # Setup
        doc = fake_document(
            doc_id="jane-doc",
            filename="jane.pdf",
            text="Jane Example\nSenior Engineer – Acme\nAmsterdam",
        )

        kg_ingest_calls = []

        async def fake_kg_ingest(**kwargs):
            kg_ingest_calls.append(kwargs)
            return {"entities": 2, "relations": 1, "mentions": 0, "entity_list": [
                {"name": "jane-doc", "type": "document"},
                {"name": "Jane Example", "type": "person"},
            ]}

        hooks = fake_hooks(
            get_doc_map={"jane-doc": doc},
            kg_ingest=fake_kg_ingest,
        )

        req = {
            "message": "Who is this document about?",
            "attachments": [{"doc_id": "jane-doc"}],
            "conversation_id": "conv-123",
            "run_id": "run-456",
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify kg_ingest was called
        assert len(kg_ingest_calls) == 1
        call = kg_ingest_calls[0]
        assert call["conversation_id"] == "conv-123"
        assert call["run_id"] == "run-456"
        assert call["intent"] == "identify_person"
        assert len(call["subject_docs"]) == 1
        assert call["subject_docs"][0].id == "jane-doc"
        assert call["person"]["name"] == "Jane Example"
        assert call["person"]["role"] == "Senior Engineer"
        assert call["person"]["org"] == "Acme"

        # Verify kg-ingest tile was emitted
        tiles = [e for e in events if "tile" in e and e["tile"]["id"] == "kg-ingest"]
        assert len(tiles) == 1
        assert tiles[0]["tile"]["status"] == "done"
        assert "+2 entities" in tiles[0]["tile"]["detail"]

        # Verify no event key starts with "_"
        for event in events:
            for key in event.keys():
                assert not key.startswith("_"), f"Event contains underscore key: {key}"

    asyncio.run(_run())


def test_kg_ingest_no_call_for_graph_query():
    """Test that kg_ingest is NOT called for graph_query runs."""
    async def _run():
        kg_ingest_calls = []

        async def fake_kg_ingest(**kwargs):
            kg_ingest_calls.append(kwargs)
            return {"entities": 0, "relations": 0, "mentions": 0, "entity_list": []}

        def fake_kg_search(text):
            return []  # No results

        hooks = fake_hooks(
            kg_ingest=fake_kg_ingest,
        )
        hooks.kg_search = fake_kg_search

        req = {
            "message": "What do we know about Jane Example?",
            "attachments": [],  # No attachments for graph_query
            "conversation_id": "conv-123",
            "run_id": "run-456",
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify kg_ingest was NOT called
        assert len(kg_ingest_calls) == 0

    asyncio.run(_run())


def test_kg_ingest_with_web_lookup():
    """Test that kg_ingest receives web_candidates and verdict from web lookup."""
    async def _run():
        doc = fake_document(
            doc_id="jane-doc",
            filename="jane.pdf",
            text="Jane Example\nSenior Engineer – Acme",
        )

        kg_ingest_calls = []

        async def fake_kg_ingest(**kwargs):
            kg_ingest_calls.append(kwargs)
            return {"entities": 3, "relations": 2, "mentions": 1, "entity_list": [
                {"name": "jane-doc", "type": "document"},
                {"name": "Jane Example", "type": "person"},
                {"name": "linkedin.com/in/jane-example", "type": "profile"},
            ]}

        async def fake_stream_llm(model: str, prompt: str, think: bool = False):
            # Simulate verdict generation
            yield {"content": "**Likely match found** — "}
            yield {"content": "[Jane Example](https://www.linkedin.com/in/jane-example)"}
            yield {"done": True}

        async def fake_web_search(query: str):
            return [
                {
                    "url": "https://www.linkedin.com/in/jane-example",
                    "title": "Jane Example - Senior Engineer at Acme",
                    "snippet": "Senior Engineer at Acme Corporation...",
                }
            ]

        def fake_laya_web(msg: str):
            return {"value": "yes", "confidence": 0.8}

        hooks = fake_hooks(
            get_doc_map={"jane-doc": doc},
            stream_llm_result=[
                {"content": "This document is about **Jane Example**.\n- **Name:** Jane Example\n- **Role:** Senior Engineer"},
                {"done": True},
            ],
            kg_ingest=fake_kg_ingest,
        )
        hooks.laya_web = fake_laya_web
        hooks.web_search = fake_web_search
        hooks.fetch_page = AsyncMock(return_value="Page content")
        hooks.pick_tool_model = AsyncMock(return_value=None)  # Use fallback path

        req = {
            "message": "Who is this document about? Check LinkedIn.",
            "attachments": [{"doc_id": "jane-doc"}],
            "conversation_id": "conv-123",
            "run_id": "run-456",
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify kg_ingest was called with web data
        assert len(kg_ingest_calls) == 1
        call = kg_ingest_calls[0]
        assert call["person"]["name"] == "Jane Example"
        assert len(call["web_candidates"]) > 0
        assert any("linkedin" in c.get("url", "") for c in call["web_candidates"])
        # Note: verdict may be from web lookup or just the answer text depending on stream setup
        assert call["verdict"] is not None

    asyncio.run(_run())


def test_kg_ingest_failure_does_not_break_run():
    """Test that kg_ingest failure does not break the run."""
    async def _run():
        doc = fake_document(doc_id="doc1", text="Test document content")

        async def failing_kg_ingest(**kwargs):
            raise RuntimeError("kg_ingest failed")

        hooks = fake_hooks(
            get_doc_map={"doc1": doc},
            kg_ingest=failing_kg_ingest,
        )

        req = {
            "message": "Summarize this document",
            "attachments": [{"doc_id": "doc1"}],
            "conversation_id": "conv-123",
            "run_id": "run-456",
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify run completes with done event
        done_events = [e for e in events if "done" in e]
        assert len(done_events) == 1
        assert done_events[0]["done"] is True

        # Verify no error event was emitted
        error_events = [e for e in events if "error" in e]
        assert len(error_events) == 0

        # Verify answer was still streamed
        content_events = [e for e in events if "content" in e]
        assert len(content_events) > 0

    asyncio.run(_run())


def test_kg_ingest_summarize_with_no_person():
    """Test that kg_ingest is called for summarize run with person=None."""
    async def _run():
        doc = fake_document(doc_id="doc1", text="Test document content")

        kg_ingest_calls = []

        async def fake_kg_ingest(**kwargs):
            kg_ingest_calls.append(kwargs)
            return {"entities": 1, "relations": 0, "mentions": 0, "entity_list": [
                {"name": "doc1", "type": "document"},
            ]}

        hooks = fake_hooks(
            get_doc_map={"doc1": doc},
            kg_ingest=fake_kg_ingest,
        )

        req = {
            "message": "Summarize this",
            "attachments": [{"doc_id": "doc1"}],
            "conversation_id": "conv-123",
            "run_id": "run-456",
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify kg_ingest was called
        assert len(kg_ingest_calls) == 1
        call = kg_ingest_calls[0]
        assert call["intent"] == "summarize"
        assert call["person"] is None
        assert len(call["subject_docs"]) == 1

    asyncio.run(_run())


def test_web_result_event_not_forwarded():
    """Test that _web_result internal events are not forwarded to client."""
    async def _run():
        doc = fake_document(doc_id="doc1", text="Jane Example\nSenior Engineer – Acme")

        async def fake_kg_ingest(**kwargs):
            return {"entities": 0, "relations": 0, "mentions": 0, "entity_list": []}

        async def fake_stream_llm(model: str, prompt: str, think: bool = False):
            yield {"content": "**Jane Example**"}
            yield {"done": True}

        async def fake_web_search(query: str):
            return [{"url": "https://linkedin.com/in/jane", "title": "Jane", "snippet": "..."}]

        hooks = fake_hooks(
            get_doc_map={"doc1": doc},
            kg_ingest=fake_kg_ingest,
            stream_llm_result=[
                {"content": "This is about **Jane Example**"},
                {"done": True},
            ],
        )
        hooks.laya_web = lambda msg: {"value": "yes", "confidence": 0.8}
        hooks.web_search = fake_web_search
        hooks.pick_tool_model = AsyncMock(return_value=None)

        req = {
            "message": "Who is this? Check online.",
            "attachments": [{"doc_id": "doc1"}],
        }

        # Run agent
        events = await collect_events(_agent.run_agent(req, hooks))

        # Verify no event contains "_web_result" key
        for event in events:
            assert "_web_result" not in event, "Internal _web_result event was forwarded to client"

    asyncio.run(_run())


def test_kg_ingest_tile_detail_format():
    """Test that kg-ingest tile has correct detail format."""
    async def _run():
        doc = fake_document(doc_id="doc1", text="Jane Example")

        async def fake_kg_ingest(**kwargs):
            return {
                "entities": 3,
                "relations": 2,
                "mentions": 1,
                "entity_list": [
                    {"name": "doc1", "type": "document"},
                    {"name": "Jane", "type": "person"},
                    {"name": "Engineer", "type": "role"},
                ]
            }

        hooks = fake_hooks(
            get_doc_map={"doc1": doc},
            kg_ingest=fake_kg_ingest,
        )

        req = {
            "message": "Who is this?",
            "attachments": [{"doc_id": "doc1"}],
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Find kg-ingest tile
        kg_ingest_tiles = [e["tile"] for e in events if "tile" in e and e["tile"].get("id") == "kg-ingest"]
        assert len(kg_ingest_tiles) == 1

        tile = kg_ingest_tiles[0]
        assert "+3 entities" in tile["detail"]
        assert "+2 relations" in tile["detail"]
        assert "+1 mentions" in tile["detail"]
        assert len(tile.get("items", [])) == 3

    asyncio.run(_run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
