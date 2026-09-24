"""
Contract tests for doc_agent_hooks: verify that hook calls match real function signatures.

These tests use inspect.signature to ensure that the hooks module makes calls
that are compatible with the real functions they will be bound to in main.py.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from doc_agent_hooks import build_hooks, HookDeps
import idp_engine


def async_test(func):
    """Decorator to run async tests synchronously."""
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))
    return wrapper


# ────────────────────────────────────────────────────────────────────────────
# Signature inspection tests: verify call patterns against real functions
# ────────────────────────────────────────────────────────────────────────────

class TestSignatureContracts:
    """Test that hook dependency calls match real function signatures."""

    def test_stream_text_signature_has_keyword_only_think(self):
        """stream_text has think as keyword-only parameter."""
        sig = inspect.signature(idp_engine.stream_text)
        params = sig.parameters

        # think should exist and be keyword-only
        assert "think" in params
        assert params["think"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["think"].default is False

        # model and prompt should be positional
        assert params["model"].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        )
        assert params["prompt"].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        )

    def test_stream_text_is_async_generator(self):
        """stream_text is an async generator function."""
        assert inspect.isasyncgenfunction(idp_engine.stream_text)

    def test_run_ocr_is_coroutine_function(self):
        """run_ocr is a coroutine function (async def)."""
        assert inspect.iscoroutinefunction(idp_engine.run_ocr)

    def test_run_ocr_signature(self):
        """run_ocr has expected parameters."""
        sig = inspect.signature(idp_engine.run_ocr)
        params = sig.parameters

        # First two params are positional
        assert "doc" in params
        assert "model" in params

        # page_range is optional
        assert "page_range" in params
        assert params["page_range"].default is not inspect.Parameter.empty

    def test_ollama_vision_call_is_coroutine_function(self):
        """_ollama_vision_call is a coroutine function."""
        assert inspect.iscoroutinefunction(idp_engine._ollama_vision_call)

    def test_ollama_vision_call_signature_has_keyword_only_num_predict(self):
        """_ollama_vision_call has num_predict as keyword-only."""
        sig = inspect.signature(idp_engine._ollama_vision_call)
        params = sig.parameters

        # num_predict should be keyword-only
        assert "num_predict" in params
        assert params["num_predict"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["num_predict"].default == 2048

    def test_get_document_is_sync(self):
        """get_document is a regular (sync) function."""
        assert not inspect.iscoroutinefunction(idp_engine.get_document)
        assert not inspect.isasyncgenfunction(idp_engine.get_document)

    def test_name_is_vision_is_sync(self):
        """_name_is_vision is a regular (sync) function."""
        assert not inspect.iscoroutinefunction(idp_engine._name_is_vision)
        assert not inspect.isasyncgenfunction(idp_engine._name_is_vision)


# ────────────────────────────────────────────────────────────────────────────
# End-to-end stub tests: verify hooks can actually call real-signature functions
# ────────────────────────────────────────────────────────────────────────────

class TestHookCallsWithRealSignatures:
    """End-to-end test that hooks work with real-signature stubs."""

    @async_test
    async def test_stream_llm_calls_stream_text_with_keyword_think(self):
        """stream_llm calls stream_text correctly with keyword-only think."""
        called_with = {}

        async def real_stream_text(model: str, prompt: str, *, think: bool = False, num_predict: int = 1536):
            """Stub with real signature."""
            called_with["model"] = model
            called_with["prompt"] = prompt
            called_with["think"] = think
            yield {"content": "test"}

        deps = MagicMock()
        deps.stream_text = real_stream_text
        # Stub out other required fields
        deps.get_doc = MagicMock()
        deps.resolve_doc_model = AsyncMock()
        deps.resolve_doc_model_for = AsyncMock()
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock()
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.run_ocr = AsyncMock()
        deps.vision_call = AsyncMock()
        deps.supports_thinking = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None
        deps.model_capabilities = AsyncMock(return_value=None)

        hooks = await build_hooks(deps)

        # Call stream_llm which internally calls deps.stream_text
        events = []
        async for event in hooks.stream_llm("test-model", "test prompt", think=True):
            events.append(event)

        # Verify it was called with keyword think
        assert called_with["model"] == "test-model"
        assert called_with["prompt"] == "test prompt"
        assert called_with["think"] is True
        assert len(events) > 0

    @async_test
    async def test_run_ocr_is_called_correctly(self):
        """Hooks can call run_ocr with correct signature."""
        called_with = {}

        async def real_run_ocr(doc, model: str, page_range=None) -> str:
            """Stub with real signature."""
            called_with["doc"] = doc
            called_with["model"] = model
            called_with["page_range"] = page_range
            return "OCR text"

        doc_stub = MagicMock()
        doc_stub.id = "doc1"

        deps = MagicMock()
        deps.run_ocr = real_run_ocr
        # Stub out other required fields
        deps.get_doc = MagicMock()
        deps.resolve_doc_model = AsyncMock()
        deps.resolve_doc_model_for = AsyncMock()
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock()
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.stream_text = AsyncMock()
        deps.vision_call = AsyncMock()
        deps.supports_thinking = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None
        deps.model_capabilities = AsyncMock(return_value=None)

        hooks = await build_hooks(deps)

        # run_ocr is passed through directly, so test it works
        result = await hooks.run_ocr(doc_stub, "ocr-model")

        assert result == "OCR text"
        assert called_with["model"] == "ocr-model"

    @async_test
    async def test_vision_call_adapter_calls_real_function(self):
        """vision_call adapter calls _ollama_vision_call with keyword num_predict."""
        called_with = {}

        async def real_vision_call(
            model: str,
            prompt: str,
            image_paths: list[str],
            *,
            num_predict: int = 2048,
        ) -> str:
            """Stub with real signature."""
            called_with["model"] = model
            called_with["prompt"] = prompt
            called_with["image_paths"] = image_paths
            called_with["num_predict"] = num_predict
            return "vision result"

        deps = MagicMock()
        deps.vision_call = real_vision_call
        # Stub out other required fields
        deps.get_doc = MagicMock()
        deps.resolve_doc_model = AsyncMock()
        deps.resolve_doc_model_for = AsyncMock()
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock()
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.run_ocr = AsyncMock()
        deps.stream_text = AsyncMock()
        deps.supports_thinking = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None
        deps.model_capabilities = AsyncMock(return_value=None)

        hooks = await build_hooks(deps)

        # Call vision_call through hooks (which wraps deps.vision_call)
        result = await hooks.vision_call(
            "vision-model",
            "compare images",
            ["/ref.png"],
            ["/subj.png"],
        )

        assert result == "vision result"
        # Verify the adapter called the real function
        assert called_with["model"] == "vision-model"

    @async_test
    async def test_get_document_is_called_sync(self):
        """get_document is passed through as sync callable."""
        def real_get_document(doc_id: str):
            """Stub with real signature - sync."""
            return {"id": doc_id, "text": "content"}

        deps = MagicMock()
        deps.get_doc = real_get_document
        # Stub out other required fields
        deps.resolve_doc_model = AsyncMock()
        deps.resolve_doc_model_for = AsyncMock()
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock()
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.run_ocr = AsyncMock()
        deps.stream_text = AsyncMock()
        deps.vision_call = AsyncMock()
        deps.supports_thinking = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None
        deps.model_capabilities = AsyncMock(return_value=None)

        hooks = await build_hooks(deps)

        # get_doc is passed through, call it directly
        result = hooks.get_doc("test-id")

        assert result == {"id": "test-id", "text": "content"}

    @async_test
    async def test_page_image_paths_is_sync(self):
        """page_image_paths is a sync function."""
        deps = MagicMock()
        deps.get_doc = MagicMock()
        deps.resolve_doc_model = AsyncMock()
        deps.resolve_doc_model_for = AsyncMock()
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock()
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.run_ocr = AsyncMock()
        deps.stream_text = AsyncMock()
        deps.vision_call = AsyncMock()
        deps.supports_thinking = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None
        deps.model_capabilities = AsyncMock(return_value=None)

        hooks = await build_hooks(deps)

        # page_image_paths should be sync (not a coroutine)
        assert not inspect.iscoroutinefunction(hooks.page_image_paths)

        # Call it without await - should return immediately
        doc = MagicMock()
        doc.page_images = ["/page1.png", "/page2.png"]
        result = hooks.page_image_paths(doc, [1], 150)

        # Should be a list, not a coroutine
        assert isinstance(result, list)
        assert result == ["/page1.png"]


# ────────────────────────────────────────────────────────────────────────────
# Signature binding tests: verify calls bind to real signatures without error
# ────────────────────────────────────────────────────────────────────────────

class TestSignatureBinding:
    """Test that hook calls bind correctly to real function signatures."""

    def test_stream_text_call_binds_to_real_signature(self):
        """A stream_text call with keyword think binds to real signature."""
        sig = inspect.signature(idp_engine.stream_text)

        # Simulate the call: stream_text(model, prompt, think=True)
        args = ("test-model", "test-prompt")
        kwargs = {"think": True}

        # This should not raise
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        assert bound.arguments["model"] == "test-model"
        assert bound.arguments["prompt"] == "test-prompt"
        assert bound.arguments["think"] is True

    def test_stream_text_positional_think_does_not_bind(self):
        """A stream_text call with positional think should NOT bind to real signature."""
        sig = inspect.signature(idp_engine.stream_text)

        # Simulate the old (broken) call: stream_text(model, prompt, think)
        args = ("test-model", "test-prompt", True)

        # This SHOULD raise TypeError because think is keyword-only
        with pytest.raises(TypeError):
            sig.bind(*args)

    def test_run_ocr_call_binds_to_real_signature(self):
        """A run_ocr call binds to real signature."""
        sig = inspect.signature(idp_engine.run_ocr)

        # Simulate the call: run_ocr(doc, model)
        doc = MagicMock()
        args = (doc, "ocr-model")
        kwargs = {}

        # This should not raise
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        assert bound.arguments["model"] == "ocr-model"

    def test_ollama_vision_call_call_binds_to_real_signature(self):
        """A _ollama_vision_call with keyword num_predict binds to real signature."""
        sig = inspect.signature(idp_engine._ollama_vision_call)

        # Simulate the call: _ollama_vision_call(model, prompt, image_paths, num_predict=2048)
        args = ("vision-model", "describe", ["/img.png"])
        kwargs = {"num_predict": 2048}

        # This should not raise
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        assert bound.arguments["model"] == "vision-model"
        assert bound.arguments["num_predict"] == 2048

    def test_get_document_call_binds_to_real_signature(self):
        """A get_document call binds to real signature."""
        sig = inspect.signature(idp_engine.get_document)

        # Simulate the call: get_document(doc_id)
        args = ("doc-123",)

        # This should not raise
        bound = sig.bind(*args)
        bound.apply_defaults()

        assert bound.arguments["doc_id"] == "doc-123"

    def test_name_is_vision_call_binds_to_real_signature(self):
        """A _name_is_vision call binds to real signature."""
        sig = inspect.signature(idp_engine._name_is_vision)

        # Simulate the call: _name_is_vision(model)
        args = ("llama3.2-vision:latest",)

        # This should not raise
        bound = sig.bind(*args)
        bound.apply_defaults()

        assert bound.arguments["model"] == "llama3.2-vision:latest"
