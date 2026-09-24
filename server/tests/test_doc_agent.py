"""
Tests for the document agent orchestrator.

Tests the document processing pipeline without importing main.py.
Uses fake hooks and Document objects for complete isolation.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from types import SimpleNamespace

import doc_agent as _agent


# ── Fixtures & Helpers ────────────────────────────────────────────────────

def fake_document(
    doc_id: str = "doc1",
    filename: str = "test.pdf",
    mime: str = "application/pdf",
    text: str = "",
    page_images: list[str] | None = None,
    pages: int = 1,
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
        pages=pages or len(page_images),
        text=text,
        page_texts=[text] if text else [""],
        page_images=page_images,
        meta={},
    )


def fake_hooks(
    get_doc_map: dict | None = None,
    laya_intent_result: dict | None = None,
    laya_role_result: dict | None = None,
    laya_doc_kind_result: dict | None = None,
    laya_available: bool = True,
    resolve_model_result: str = "default-model",
    resolve_text_model_result: str = "text-model",
    pick_vision_model_result: str | None = "vision-model",
    model_info_result: dict | None = None,
    ocr_result: str = "OCR text output",
    stream_llm_result: list[dict] | None = None,
    vision_call_result: str = "Vision result",
    page_image_paths_result: list[str] | None = None,
    laya_info_result: dict | None = None,
) -> _agent.AgentHooks:
    """Create fake hooks for testing."""
    if get_doc_map is None:
        get_doc_map = {}
    if stream_llm_result is None:
        stream_llm_result = [
            {"content": "This is "},
            {"content": "the answer."},
            {"done": True, "stats": {"eval_count": 10, "tok_per_s": 50.0, "total_duration_ms": 200}},
        ]
    if page_image_paths_result is None:
        page_image_paths_result = []
    if model_info_result is None:
        model_info_result = {"name": "test-model", "device": "cpu"}
    if laya_info_result is None:
        laya_info_result = {"name": "Laya", "available": laya_available, "device": "cpu" if laya_available else None}

    def get_doc(doc_id: str):
        return get_doc_map.get(doc_id)

    def laya_intent(msg: str, files: list[dict]):
        return laya_intent_result

    def laya_role(msg: str, file: dict):
        return laya_role_result

    def laya_doc_kind(text: str):
        return laya_doc_kind_result

    def laya_info():
        return laya_info_result

    async def resolve_model(category: str):
        return resolve_model_result

    async def resolve_text_model(doc, category: str):
        return resolve_text_model_result

    async def pick_vision_model():
        return pick_vision_model_result

    async def vision_candidates():
        # Return a single candidate if pick_vision_model has a result
        return [pick_vision_model_result] if pick_vision_model_result else []

    async def model_info(name: str):
        return model_info_result

    async def run_ocr(doc, model: str):
        return ocr_result

    async def stream_llm(model: str, prompt: str, think: bool = False):
        for item in stream_llm_result:
            yield item

    async def vision_call(model: str, prompt: str, reference_paths: list[str], subject_paths: list[str]):
        return vision_call_result

    def page_image_paths(doc, pages: list[int] | None = None, dpi: int = 120):
        return page_image_paths_result

    def mark_vision_failed(model: str, err: str):
        pass  # Do nothing in fake

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
    )


async def collect_events(agent_stream):
    """Collect all events from an async generator."""
    events = []
    async for event in agent_stream:
        events.append(event)
    return events


# ── Tests ──────────────────────────────────────────────────────────────────

class TestPureIntentResolution:
    """Test intent resolution logic without Laya."""

    def test_rules_intent_signature_with_reference(self):
        """Detect signature intent when reference file is present."""
        result, kws = _agent.rules_intent(
            "verify signature",
            [{"name": "reference_specimen.pdf", "kind": "pdf"}]
        )
        assert result == "verify_signature"
        assert len(kws) > 0

    def test_rules_intent_identify_person(self):
        """Detect identify_person from keywords."""
        result, kws = _agent.rules_intent("who is this person?", [])
        assert result == "identify_person"
        result, kws = _agent.rules_intent("wie is dit?", [])
        assert result == "identify_person"
        result, kws = _agent.rules_intent("qui est la personne?", [])
        assert result == "identify_person"

    def test_rules_intent_summarize(self):
        """Detect summarize intent."""
        result, kws = _agent.rules_intent("summarize this", [])
        assert result == "summarize"
        result, kws = _agent.rules_intent("tl;dr", [])
        assert result == "summarize"
        result, kws = _agent.rules_intent("key points", [])
        assert result == "summarize"

    def test_rules_intent_extract_data(self):
        """Detect extract_data intent."""
        result, kws = _agent.rules_intent("extract table", [])
        assert result == "extract_data"
        result, kws = _agent.rules_intent("list all dates", [])
        assert result == "extract_data"

    def test_rules_intent_translate(self):
        """Detect translate intent."""
        result, kws = _agent.rules_intent("translate to French", [])
        assert result == "translate"
        result, kws = _agent.rules_intent("vertaal naar Nederlands", [])
        assert result == "translate"

    def test_rules_intent_redact(self):
        """Detect redact intent."""
        result, kws = _agent.rules_intent("redact personal info", [])
        assert result == "redact"
        result, kws = _agent.rules_intent("anonymize", [])
        assert result == "redact"

    def test_rules_intent_empty_message_defaults_to_summarize(self):
        """Empty message defaults to summarize intent."""
        result, kws = _agent.rules_intent("", [])
        assert result == "summarize"
        assert "no explicit instruction" in kws

        result, kws = _agent.rules_intent("   ", [])
        assert result == "summarize"
        assert "no explicit instruction" in kws

    def test_rules_intent_look_at_attached_document_pattern(self):
        """'look at the attached document' pattern triggers summarize."""
        result, kws = _agent.rules_intent("look at the attached", [])
        assert result == "summarize"
        assert "no explicit instruction" in kws

        result, kws = _agent.rules_intent("take a look at the attached document", [])
        assert result == "summarize"

        result, kws = _agent.rules_intent("bekijk de bijlage", [])
        assert result == "summarize"

        result, kws = _agent.rules_intent("regarde le document", [])
        assert result == "summarize"

    def test_rules_intent_normal_text_no_match(self):
        """Return None when no keyword match for normal text."""
        result, kws = _agent.rules_intent("random text", [])
        assert result is None
        assert kws == []

    def test_resolve_intent_laya_confident(self):
        """Use Laya when confident and it's not extract_data without keyword agreement."""
        laya_result = {"intent": "identify_person", "confidence": 0.85, "probabilities": {"identify_person": 0.85}}
        resolved = _agent.resolve_intent(laya_result, ("summarize", ["summarize"]))
        assert resolved["intent"] == "identify_person"
        assert resolved["source"] == "laya"
        assert resolved["note"] == "Rules suggested summarize"

    def test_resolve_intent_laya_uncertain_fallback_to_rules(self):
        """Fall back to rules when Laya uncertain."""
        laya_result = {"intent": "general_question", "confidence": 0.45, "probabilities": {}}
        resolved = _agent.resolve_intent(laya_result, ("summarize", ["summarize"]))
        assert resolved["intent"] == "summarize"
        assert resolved["source"] == "rules"
        assert "unsure" in (resolved.get("note") or "").lower()

    def test_resolve_intent_neither_defaults_to_general(self):
        """Default to general_question when no signal."""
        resolved = _agent.resolve_intent(None, (None, []))
        assert resolved["intent"] == "general_question"

    def test_resolve_intent_verify_signature_strong_guard(self):
        """Apply strong guard for verify_signature via rules."""
        # Rules detect reference specimen
        laya_result = {"intent": "identify_person", "confidence": 0.6, "probabilities": {}}
        resolved = _agent.resolve_intent(laya_result, ("verify_signature", ["verify"]))
        # With low Laya confidence, should use verify_signature
        assert resolved["intent"] == "verify_signature"
        assert resolved["source"] == "rules"
        assert resolved["confidence"] is None  # Rules decisions have confidence=None

    def test_resolve_intent_verify_signature_laya_wins_if_very_confident(self):
        """If Laya very confident, it wins over verify_signature rules."""
        laya_result = {"intent": "identify_person", "confidence": 0.95, "probabilities": {}}
        resolved = _agent.resolve_intent(laya_result, ("verify_signature", ["verify"]))
        assert resolved["intent"] == "identify_person"
        assert resolved["source"] == "laya"

    def test_resolve_intent_verify_signature_laya_agrees(self):
        """If Laya agrees with verify_signature, credit Laya."""
        laya_result = {"intent": "verify_signature", "confidence": 0.75, "probabilities": {}}
        resolved = _agent.resolve_intent(laya_result, ("verify_signature", ["verify"]))
        assert resolved["intent"] == "verify_signature"
        assert resolved["source"] == "laya"
        assert resolved["note"] == "keyword rules agree"
        assert resolved["confidence"] == 0.75

    def test_resolve_intent_extract_data_agreement_both(self):
        """Accept Laya's extract_data only if rules also say extract_data."""
        laya_result = {"intent": "extract_data", "confidence": 0.85, "probabilities": {}}
        # Both agree on extract_data
        resolved = _agent.resolve_intent(laya_result, ("extract_data", ["extract"]))
        assert resolved["intent"] == "extract_data"
        assert resolved["source"] == "laya"
        assert resolved["note"] == "keyword rules agree"

    def test_resolve_intent_extract_data_laya_only(self):
        """Reject Laya's extract_data if rules disagree."""
        laya_result = {"intent": "extract_data", "confidence": 0.93, "probabilities": {}}
        # Laya says extract_data but rules say summarize
        resolved = _agent.resolve_intent(laya_result, ("summarize", ["summarize"]))
        assert resolved["intent"] == "summarize"
        assert resolved["source"] == "rules"
        assert "Laya suggested extract_data (0.93)" in resolved.get("note") or ""

    def test_resolve_intent_extract_data_laya_no_rules(self):
        """Default to general_question if Laya says extract_data but no rules match."""
        laya_result = {"intent": "extract_data", "confidence": 0.93, "probabilities": {}}
        # Laya says extract_data but no rules match
        resolved = _agent.resolve_intent(laya_result, (None, []))
        assert resolved["intent"] == "general_question"
        assert resolved["source"] == "rules"
        assert "Laya suggested extract_data (0.93)" in resolved.get("note") or ""


class TestRoleAssignment:
    """Test file role assignment logic."""

    def test_user_role_wins(self):
        """User-specified role takes precedence."""
        files_meta = [{"name": "doc.pdf", "doc_id": "d1"}]

        def fake_laya_role(msg, file):
            return {"role": "reference_specimen", "confidence": 0.9}

        roles = _agent.assign_roles(
            files_meta,
            "check this",
            fake_laya_role,
            user_roles={"d1": "reference"}
        )
        assert roles[0]["role"] == "reference"
        assert roles[0]["source"] == "user"

    def test_laya_role_when_confident(self):
        """Use Laya when confidence >= threshold."""
        files_meta = [{"name": "doc.pdf"}]

        def fake_laya_role(msg, file):
            return {"role": "reference_specimen", "confidence": 0.75}

        roles = _agent.assign_roles(
            files_meta,
            "find reference",
            fake_laya_role,
        )
        assert roles[0]["role"] == "reference"
        assert roles[0]["source"] == "laya"

    def test_fallback_to_heuristic_when_laya_low_conf(self):
        """Fall back to heuristic when Laya low confidence."""
        files_meta = [{"name": "specimen.pdf"}]

        def fake_laya_role(msg, file):
            return {"role": "reference_specimen", "confidence": 0.5}

        roles = _agent.assign_roles(
            files_meta,
            "test",
            fake_laya_role,
        )
        assert roles[0]["source"] == "rules"

    def test_auto_assign_reference_for_verify_signature_with_two_files(self):
        """Auto-assign smallest file as reference when no reference assigned for verify_signature."""
        files_meta = [
            {"doc_id": "d1", "name": "specimen.jpg"},
            {"doc_id": "d2", "name": "document.pdf"},
        ]

        def fake_laya_role(msg, file):
            return {"role": "subject_document", "confidence": 0.5}

        roles = _agent.assign_roles(
            files_meta,
            "verify signature",
            fake_laya_role,
            intent="verify_signature",
        )
        # Both start as subject due to low Laya conf
        # Then the smallest one gets reassigned to reference
        reference_roles = [r for r in roles if r["role"] == "reference"]
        assert len(reference_roles) > 0


class TestIntentClassificationAgainstMimeTypes:
    """Test intent classification with actual mime types."""

    def test_pdf_with_text_layer(self):
        """PDF with text → prioritize content-based intents."""
        doc = fake_document(
            doc_id="d1",
            filename="report.pdf",
            mime="application/pdf",
            text="This is a report about quarterly sales.",
        )
        files_meta = [{
            "name": "report.pdf",
            "kind": "pdf",
            "chars": len(doc.text),
        }]
        # Rules should pick up 'summarize' if message says "summarize"
        intent, kws = _agent.rules_intent("summarize this report", files_meta)
        assert intent == "summarize"

    def test_image_only_ocr_needed(self):
        """Image without text layer → OCR will be needed."""
        doc = fake_document(
            doc_id="d1",
            filename="signature.png",
            mime="image/png",
            text="",
            page_images=["/path/to/sig.png"],
        )
        # Verify signature should be detected with a reference file
        intent, kws = _agent.rules_intent(
            "verify signature against reference",
            [
                {"name": "reference.png", "kind": "image"},
                {"name": "signature.png", "kind": "image"}
            ]
        )
        assert intent == "verify_signature"


class TestEventOrder:
    """Test that events are emitted in the correct order."""

    def test_event_order_identify_person_scanned_pdf(self):
        """Event order for identify_person on scanned PDF."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="scanned.pdf",
                mime="application/pdf",
                text="",  # No text layer
                page_images=["/path/to/page1.png"],
                pages=1,
            )

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "identify_person", "confidence": 0.8, "probabilities": {}},
                laya_role_result={"role": "subject_document", "confidence": 0.9},
                ocr_result="John Smith\nChief Executive Officer",
                stream_llm_result=[
                    {"content": "John Smith"},
                    {"done": True, "stats": {}},
                ],
            )

            req = {
                "message": "who is this person?",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Extract tile types in order of first appearance
            tile_kinds = [
                e["tile"]["kind"]
                for e in events
                if "tile" in e
            ]

            # First appearance order should be: laya, ocr, llm
            first_appearances = []
            seen = set()
            for kind in tile_kinds:
                if kind not in seen:
                    first_appearances.append(kind)
                    seen.add(kind)

            assert first_appearances[:3] == ["laya", "ocr", "llm"], f"Got order: {first_appearances}"

        asyncio.run(run_test())

    def test_event_order_text_layer_pdf(self):
        """Event order for PDF with text layer → no OCR tile."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="report.pdf",
                mime="application/pdf",
                text="Long report text here...",
                pages=5,
            )

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
                stream_llm_result=[
                    {"content": "Summary here."},
                    {"done": True, "stats": {}},
                ],
            )

            req = {
                "message": "summarize this",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            tile_kinds = [
                e["tile"]["kind"]
                for e in events
                if "tile" in e
            ]

            # Should have laya and llm, but no ocr tile
            first_appearances = []
            seen = set()
            for kind in tile_kinds:
                if kind not in seen:
                    first_appearances.append(kind)
                    seen.add(kind)

            assert "ocr" not in first_appearances
            assert "extract" in first_appearances or "llm" in first_appearances

        asyncio.run(run_test())


class TestTileIdStability:
    """Test that tile IDs are stable and upserts work correctly."""

    def test_tile_id_uniqueness(self):
        """Each tile has a unique, stable ID."""
        async def run_test():
            doc = fake_document(doc_id="d1", filename="test.pdf", text="Some text")

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
            )

            req = {
                "message": "summarize",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))
            tile_ids = [e["tile"]["id"] for e in events if "tile" in e]

            # Should have at least: laya, extract, answer
            assert "laya" in tile_ids
            assert "answer" in tile_ids

            # Laya should appear multiple times (upsert on each decision)
            laya_count = sum(1 for tid in tile_ids if tid == "laya")
            assert laya_count >= 2, "Laya tile should be upserted multiple times"

        asyncio.run(run_test())

    def test_extract_tile_id_per_doc(self):
        """Extract tile IDs per document."""
        async def run_test():
            doc1 = fake_document(doc_id="d1", filename="file1.pdf", text="This is a longer text document that contains enough characters for extraction.")
            doc2 = fake_document(doc_id="d2", filename="file2.pdf", text="Another document with sufficient length to trigger extract tile creation.")

            hooks = fake_hooks(
                get_doc_map={"d1": doc1, "d2": doc2},
                laya_intent_result={"intent": "extract_data", "confidence": 0.8, "probabilities": {}},
            )

            req = {
                "message": "extract data",
                "attachments": [{"doc_id": "d1"}, {"doc_id": "d2"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))
            tile_ids = [e["tile"]["id"] for e in events if "tile" in e]

            assert "extract-d1" in tile_ids
            assert "extract-d2" in tile_ids

        asyncio.run(run_test())


class TestLayaTileDecisions:
    """Test that Laya tile collects decisions properly."""

    def test_laya_tile_includes_intent_decision(self):
        """Laya tile should have intent decision."""
        async def run_test():
            doc = fake_document(doc_id="d1", filename="test.pdf", text="Some text")

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
            )

            req = {
                "message": "summarize",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Find final Laya tile (status done)
            laya_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "laya"]
            final_laya = laya_tiles[-1] if laya_tiles else None

            assert final_laya is not None
            assert final_laya["status"] == "done"

            # Should have intent decision
            decision_labels = [d["label"] for d in final_laya["decisions"]]
            assert "Intent" in decision_labels

        asyncio.run(run_test())

    def test_laya_tile_has_model_info(self):
        """Laya tile should include model_info."""
        async def run_test():
            doc = fake_document(doc_id="d1", filename="test.pdf", text="Some text")

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_info_result={"name": "Laya", "available": True, "device": "cpu"},
            )

            req = {
                "message": "test",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            laya_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "laya"]
            first_laya = laya_tiles[0] if laya_tiles else None

            assert first_laya is not None
            assert first_laya["model_info"] is not None

        asyncio.run(run_test())


class TestVerifySignatureFlow:
    """Test the verify_signature intent flow."""

    def test_verify_signature_uses_vision_model(self):
        """verify_signature should use vision model, not LLM."""
        async def run_test():
            ref_doc = fake_document(
                doc_id="ref",
                filename="reference.png",
                mime="image/png",
                text="",
                page_images=["/path/ref.png"],
            )
            subject_doc = fake_document(
                doc_id="subj",
                filename="signature.png",
                mime="image/png",
                text="",
                page_images=["/path/sig.png"],
            )

            hooks = fake_hooks(
                get_doc_map={"ref": ref_doc, "subj": subject_doc},
                laya_intent_result={"intent": "verify_signature", "confidence": 0.9, "probabilities": {}},
                laya_role_result={"role": "reference_specimen", "confidence": 0.9},
                pick_vision_model_result="vision-model:latest",
                vision_call_result="Visual similarity: high",
                page_image_paths_result=["/path/image.png"],
            )

            req = {
                "message": "verify signature",
                "attachments": [
                    {"doc_id": "ref", "role": "reference"},
                    {"doc_id": "subj", "role": "subject"},
                ],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Find answer tile
            answer_tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "answer"]
            answer_tile = answer_tiles[-1] if answer_tiles else None

            assert answer_tile is not None
            assert answer_tile["kind"] == "vision"
            assert answer_tile["model"] == "vision-model:latest"

        asyncio.run(run_test())

    def test_verify_signature_no_vision_model_error(self):
        """verify_signature without vision model should error."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="sig.png",
                mime="image/png",
                text="",
                page_images=["/path/sig.png"],
            )

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "verify_signature", "confidence": 0.9, "probabilities": {}},
                pick_vision_model_result=None,  # No vision model available
            )

            req = {
                "message": "verify signature",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Should have error event
            error_events = [e for e in events if "error" in e]
            assert len(error_events) > 0
            assert "vision" in error_events[0]["error"].lower()

        asyncio.run(run_test())

    def test_verify_signature_vision_call_includes_caveat(self):
        """verify_signature answer should include the "not forensic" caveat."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="sig.png",
                mime="image/png",
                text="",
                page_images=["/path/sig.png"],
            )

            caveat_text = "This is an automated visual comparison by an AI model, NOT a forensic document examination"

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "verify_signature", "confidence": 0.9, "probabilities": {}},
                pick_vision_model_result="vision-model",
                vision_call_result=f"Comparison result...\n{caveat_text}",
                page_image_paths_result=["/path/sig.png"],
            )

            req = {
                "message": "verify",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Find content events
            content_events = [e for e in events if "content" in e]
            full_answer = "".join([e["content"] for e in content_events])

            # Caveat should be in the output (via vision_call_result)
            assert caveat_text in full_answer or "forensic" in full_answer

        asyncio.run(run_test())


class TestOcrErrorHandling:
    """Test error handling in OCR step."""

    def test_ocr_hook_exception_emits_error(self):
        """Hook exception in OCR should emit error tile and event."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="scanned.pdf",
                mime="application/pdf",
                text="",
                page_images=["/path/page.png"],
            )

            async def failing_ocr(d, model):
                raise RuntimeError("OCR service down")

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "identify_person", "confidence": 0.8, "probabilities": {}},
            )
            hooks.run_ocr = failing_ocr

            req = {
                "message": "who is this?",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Should have error event
            error_events = [e for e in events if "error" in e]
            assert len(error_events) > 0
            assert "OCR" in error_events[0]["error"]

            # Extract tile should be marked error
            extract_tiles = [
                e["tile"] for e in events
                if "tile" in e and e["tile"]["id"].startswith("extract")
            ]
            if extract_tiles:
                assert any(t["status"] == "error" for t in extract_tiles)

        asyncio.run(run_test())


class TestHistoryAndTruncation:
    """Test that history is included and text is truncated."""

    def test_history_included_in_prompt(self):
        """Prior conversation history should be included in LLM prompt."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="doc.pdf",
                mime="application/pdf",
                text="Report text...",
            )

            prompt_received = ""

            async def capture_llm(model: str, prompt: str, think: bool = False):
                nonlocal prompt_received
                prompt_received = prompt
                yield {"content": "Answer."}
                yield {"done": True, "stats": {}}

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
            )
            hooks.stream_llm = capture_llm

            req = {
                "message": "summarize this",
                "attachments": [{"doc_id": "d1"}],
                "history": [
                    {"role": "user", "content": "What about sales?"},
                    {"role": "assistant", "content": "Sales were up."},
                ],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Prompt should include history
            assert "user" in prompt_received or "sales" in prompt_received.lower()

        asyncio.run(run_test())

    def test_very_long_document_truncated_with_note(self):
        """Very long documents should be truncated with a note."""
        async def run_test():
            long_text = "x" * 30000  # Over 24000 char limit

            doc = fake_document(
                doc_id="d1",
                filename="huge.pdf",
                mime="application/pdf",
                text=long_text,
            )

            prompt_received = ""

            async def capture_llm(model: str, prompt: str, think: bool = False):
                nonlocal prompt_received
                prompt_received = prompt
                yield {"content": "Answer."}
                yield {"done": True, "stats": {}}

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
            )
            hooks.stream_llm = capture_llm

            req = {
                "message": "summarize",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Prompt should have truncation note
            assert "truncated" in prompt_received or len(prompt_received) < len(long_text)

        asyncio.run(run_test())


class TestNoAttachments:
    """Test handling of missing attachments."""

    def test_no_attachments_with_message_should_error(self):
        """Request with no attachments but message should error."""
        async def run_test():
            hooks = fake_hooks(get_doc_map={})

            req = {
                "message": "help me with documents",
                "attachments": [],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Should have error event
            error_events = [e for e in events if "error" in e]
            assert len(error_events) > 0

        asyncio.run(run_test())


class TestContentStreaming:
    """Test that content is streamed in deltas."""

    def test_llm_content_streamed_as_deltas(self):
        """LLM output should be streamed as separate content deltas."""
        async def run_test():
            doc = fake_document(
                doc_id="d1",
                filename="doc.pdf",
                mime="application/pdf",
                text="Text",
            )

            hooks = fake_hooks(
                get_doc_map={"d1": doc},
                laya_intent_result={"intent": "summarize", "confidence": 0.8, "probabilities": {}},
                stream_llm_result=[
                    {"content": "Part "},
                    {"content": "one. "},
                    {"content": "Part two."},
                    {"done": True, "stats": {}},
                ],
            )

            req = {
                "message": "summarize",
                "attachments": [{"doc_id": "d1"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            # Collect content deltas
            content_deltas = [e["content"] for e in events if "content" in e]
            assert len(content_deltas) >= 3
            assert "".join(content_deltas) == "Part one. Part two."

        asyncio.run(run_test())


class TestUnknownDocId:
    """Test error handling for unknown document IDs."""

    def test_unknown_doc_id_error(self):
        """Unknown doc_id should produce error."""
        async def run_test():
            hooks = fake_hooks(get_doc_map={})

            req = {
                "message": "test",
                "attachments": [{"doc_id": "nonexistent"}],
                "history": [],
            }

            events = await collect_events(_agent.run_agent(req, hooks))

            error_events = [e for e in events if "error" in e]
            assert len(error_events) > 0
            assert "not found" in error_events[0]["error"].lower()

        asyncio.run(run_test())


class TestRunCollector:
    """Test the RunCollector class for folding events into a result."""

    def test_run_collector_basic(self):
        """Basic RunCollector folding."""
        collector = _agent.RunCollector()

        # Feed content events
        collector.feed({"content": "Part 1. "})
        collector.feed({"content": "Part 2."})

        # Feed tile events
        collector.feed({"tile": {"id": "laya", "kind": "laya", "status": "done"}})
        collector.feed({"tile": {"id": "answer", "kind": "llm", "model": "gpt4", "status": "done"}})

        # Feed done event
        collector.feed({"done": True, "stats": {"intent": "summarize"}})

        assert collector.content == "Part 1. Part 2."
        assert collector.model == "gpt4"
        assert collector.intent == "summarize"
        assert collector.done is True

    def test_run_collector_tile_upsert_order(self):
        """Tiles should be updated in place; order tracks first appearance."""
        collector = _agent.RunCollector()

        # First appearance of laya
        collector.feed({"tile": {"id": "laya", "kind": "laya", "status": "running"}})
        assert collector.tiles_list == ["laya"]

        # Upsert laya (update in place)
        collector.feed({"tile": {"id": "laya", "kind": "laya", "status": "done", "ms": 100}})
        assert collector.tiles_list == ["laya"]  # Still just one entry
        assert collector.tiles["laya"]["ms"] == 100  # Updated

        # New tile
        collector.feed({"tile": {"id": "answer", "kind": "llm", "status": "pending"}})
        assert collector.tiles_list == ["laya", "answer"]

    def test_run_collector_error_capture(self):
        """Errors should be captured."""
        collector = _agent.RunCollector()

        collector.feed({"error": "Something went wrong"})
        assert collector.error == "Something went wrong"

    def test_run_collector_thinking(self):
        """Thinking events should accumulate."""
        collector = _agent.RunCollector()

        collector.feed({"thinking": "Step 1: "})
        collector.feed({"thinking": "analyze. "})
        collector.feed({"thinking": "Step 2: decide."})

        assert collector.thinking == "Step 1: analyze. Step 2: decide."

    def test_run_collector_to_meta(self):
        """to_meta() should produce correct shape."""
        collector = _agent.RunCollector()

        # Simulate a run
        collector.feed({"tile": {"id": "laya", "kind": "laya", "status": "done"}})
        collector.feed({"tile": {"id": "answer", "kind": "llm", "model": "test-model", "status": "done"}})
        collector.feed({"content": "Answer text"})
        collector.feed({"done": True, "stats": {"intent": "summarize", "total_ms": 1000}})

        meta = collector.to_meta("run-123", ["doc1", "doc2"])

        assert meta["kind"] == "doc_run"
        assert meta["run_id"] == "run-123"
        assert meta["intent"] == "summarize"
        assert meta["doc_ids"] == ["doc1", "doc2"]
        assert len(meta["tiles"]) == 2
        assert meta["tiles"][0]["id"] == "laya"
        assert meta["tiles"][1]["id"] == "answer"
        assert meta["stats"]["intent"] == "summarize"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
