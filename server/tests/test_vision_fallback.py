"""
Tests for vision model fallback behavior in doc_agent.

Tests the fallback loop when the first vision model fails to load,
with proper Laya decision tracking and error handling.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from types import SimpleNamespace

import doc_agent as _agent


def async_test(func):
    """Decorator to run async tests synchronously."""
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))
    return wrapper


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


def fake_hooks_with_fallback(
    candidates: list[str] | None = None,
    vision_call_sequence: list[dict] | None = None,
    get_doc_map: dict | None = None,
    intent: str = "verify_signature",
) -> _agent.AgentHooks:
    """
    Create fake hooks with vision fallback support.

    Args:
        candidates: List of vision model candidates to return
        vision_call_sequence: List of dicts with 'model', 'raises', and optionally 'result' keys
                            describing what each vision call should do
        get_doc_map: Map of doc_id -> Document
        intent: The intent to return from laya_intent (default: verify_signature)
    """
    if candidates is None:
        candidates = ["model1", "model2"]
    if vision_call_sequence is None:
        # Default: first fails, second succeeds
        vision_call_sequence = [
            {"model": "model1", "raises": RuntimeError("mllama architecture")},
            {"model": "model2", "result": "Vision result from model2"},
        ]
    if get_doc_map is None:
        get_doc_map = {}

    marked_failed = []

    def get_doc(doc_id: str):
        return get_doc_map.get(doc_id)

    async def vision_candidates():
        return candidates

    def mark_vision_failed(model: str, err: str):
        marked_failed.append((model, err))

    def laya_intent(msg: str, files: list[dict]):
        # Return the specified intent
        return {"intent": intent, "confidence": 0.9}

    def laya_role(msg: str, file: dict):
        return None

    def laya_doc_kind(text: str):
        return None

    def laya_info():
        return {"name": "Laya", "available": False, "device": None}

    async def resolve_model(category: str):
        return "default-model"

    async def resolve_text_model(doc, category: str):
        return "text-model"

    async def pick_vision_model():
        return candidates[0] if candidates else None

    async def model_info(name: str):
        return {"name": name, "device": "cpu"}

    async def run_ocr(doc, model: str):
        return "OCR text output"

    async def stream_llm(model: str, prompt: str, think: bool = False):
        yield {"content": "This is "}
        yield {"content": "the answer."}
        yield {"done": True}

    call_count = [0]

    async def vision_call(model: str, prompt: str, reference_paths: list[str], subject_paths: list[str]):
        """Simulate vision call with sequence-based behavior."""
        idx = call_count[0]
        call_count[0] += 1

        if idx < len(vision_call_sequence):
            spec = vision_call_sequence[idx]
            if spec.get("raises"):
                raise spec["raises"]
            elif "result" in spec:
                return spec["result"]

        # Default fallback
        raise RuntimeError("Unexpected call to vision_call")

    def page_image_paths(doc, pages: list[int] | None = None, dpi: int = 120):
        return [f"/tmp/page_{p}.png" for p in (pages or [1])]

    def now_ms():
        return 1000000000

    return _agent.AgentHooks(
        get_doc=get_doc,
        laya_intent=laya_intent,
        laya_role=laya_role,
        laya_doc_kind=laya_doc_kind,
        laya_info=laya_info,
        resolve_model=resolve_model,
        resolve_text_model=resolve_text_model,
        pick_vision_model=pick_vision_model,
        vision_candidates=vision_candidates,
        model_info=model_info,
        run_ocr=run_ocr,
        stream_llm=stream_llm,
        vision_call=vision_call,
        page_image_paths=page_image_paths,
        mark_vision_failed=mark_vision_failed,
        now_ms=now_ms,
    ), marked_failed


async def collect_events(agent_stream):
    """Collect all events from an async generator."""
    events = []
    async for event in agent_stream:
        events.append(event)
    return events


class TestVisionFallback:
    """Test vision model fallback loop."""

    @async_test
    async def test_fallback_first_candidate_fails_second_succeeds(self):
        """First candidate fails, second succeeds -> content produced, fallback recorded."""
        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=["model1", "model2"],
            vision_call_sequence=[
                {"model": "model1", "raises": RuntimeError("unknown model architecture: 'mllama'")},
                {"model": "model2", "result": "Signature analysis result"},
            ],
            get_doc_map={"doc1": doc1},
        )

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Check that we got content
        content_events = [e for e in events if "content" in e]
        assert len(content_events) > 0
        full_content = "".join(e["content"] for e in content_events)
        assert "Signature analysis result" in full_content

        # Check answer tile is done
        answer_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "answer"]
        assert any(t["status"] == "done" for t in answer_tiles)

        # Check that a fallback decision was added
        laya_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "laya"]
        assert any(
            any(d.get("id") == "vision_fallback_0" for d in t.get("decisions", []))
            for t in laya_tiles
        )

        # Check that mark_vision_failed was called
        assert len(marked_failed) == 1
        assert marked_failed[0][0] == "model1"

        # Check final answer_model decision is second model
        final_laya = laya_tiles[-1] if laya_tiles else {}
        answer_model_decision = next(
            (d for d in final_laya.get("decisions", []) if d.get("id") == "answer_model"),
            None,
        )
        assert answer_model_decision and answer_model_decision["value"] == "model2"

    @async_test
    async def test_all_candidates_fail_error_tile(self):
        """All candidates fail -> answer tile error, single error event."""
        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=["model1", "model2", "model3"],
            vision_call_sequence=[
                {"model": "model1", "raises": RuntimeError("unknown model architecture: 'mllama'")},
                {"model": "model2", "raises": RuntimeError("CUDA out of memory")},
                {"model": "model3", "raises": RuntimeError("failed to load model")},
            ],
            get_doc_map={"doc1": doc1},
        )

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Check answer tile is error
        answer_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "answer"]
        assert any(t["status"] == "error" for t in answer_tiles)

        # Check error event
        error_events = [e for e in events if "error" in e]
        assert len(error_events) == 1

        # Check that mark_vision_failed was called for each failure
        assert len(marked_failed) == 3

        # Verify detail message includes tried models
        error_tile = next(t for t in answer_tiles if t["status"] == "error")
        assert "model1" in error_tile["detail"]
        assert "model2" in error_tile["detail"]

    @async_test
    async def test_empty_candidates_error(self):
        """No candidates available -> error tile with helpful message."""
        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=[],  # No candidates
            get_doc_map={"doc1": doc1},
        )

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Check answer tile is error
        answer_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "answer"]
        assert any(t["status"] == "error" for t in answer_tiles)

        # Check error message mentions installation
        error_tile = next(t for t in answer_tiles if t["status"] == "error")
        assert "install" in error_tile["detail"].lower() or "ollama" in error_tile["detail"].lower()

    @async_test
    async def test_vision_candidates_hook_exception(self):
        """Exception in vision_candidates hook -> error tile."""
        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=[],
            get_doc_map={"doc1": doc1},
        )

        # Override vision_candidates to raise
        async def failing_candidates():
            raise RuntimeError("Failed to get candidates")

        hooks.vision_candidates = failing_candidates

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Check that we got an error event
        error_events = [e for e in events if "error" in e]
        assert len(error_events) > 0

    @async_test
    async def test_fallback_loop_second_attempt_includes_first_failure_decision(self):
        """Second attempt includes a fallback decision recording the first failure."""
        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=["model1", "model2"],
            vision_call_sequence=[
                {"model": "model1", "raises": RuntimeError("unknown model architecture: 'mllama'")},
                {"model": "model2", "result": "Result from model2"},
            ],
            get_doc_map={"doc1": doc1},
        )

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Find all laya tiles to track decision history
        laya_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "laya"]

        # The fallback decision should appear in a laya tile update
        fallback_decisions = []
        for tile in laya_tiles:
            fallback_decisions.extend(
                d for d in tile.get("decisions", []) if d.get("id", "").startswith("vision_fallback")
            )

        assert len(fallback_decisions) >= 1
        assert fallback_decisions[0].get("value") == "model2"  # Next candidate

    @async_test
    async def test_rerun_with_broken_cache_skips_first_model(self):
        """When first model is broken from cache, vision_candidates reflects that."""
        # This test verifies that the fallback loop works correctly when
        # mark_vision_failed is called and rank_signature_models uses the broken cache.
        # (Simplified version that doesn't depend on global state changes)

        doc1 = fake_document("doc1", "signature.pdf", page_images=["/tmp/sig.png"])

        # Simulate a second run where model1 is already broken
        hooks, marked_failed = fake_hooks_with_fallback(
            candidates=["model2", "model3"],  # model1 excluded by cache
            vision_call_sequence=[
                {"model": "model2", "result": "Result from model2"},
            ],
            get_doc_map={"doc1": doc1},
        )

        req = {
            "message": "verify signature",
            "attachments": [{"doc_id": "doc1", "role": "subject"}],
            "history": [],
            "model_override": None,
        }

        events = await collect_events(_agent.run_agent(req, hooks))

        # Should succeed on first try with model2 (no fallback decision)
        laya_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "laya"]
        fallback_decisions = []
        for tile in laya_tiles:
            fallback_decisions.extend(
                d for d in tile.get("decisions", []) if d.get("id", "").startswith("vision_fallback")
            )

        # No fallback decisions since model2 worked on first try
        assert len(fallback_decisions) == 0

        # mark_vision_failed should not have been called
        assert len(marked_failed) == 0
