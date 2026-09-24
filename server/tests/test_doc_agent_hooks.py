"""
Tests for doc_agent_hooks module.

Comprehensive tests covering model_info caching, page_image_paths rendering,
vision_call composition, model override, laya hooks, and hook builder.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_agent_hooks import pick_signature_model, build_hooks, HookDeps, _ModelInfoCache


def async_test(func):
    """Decorator to run async tests synchronously."""
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))
    return wrapper


# ────────────────────────────────────────────────────────────────────────────
# Existing pick_signature_model tests (keep as-is)
# ────────────────────────────────────────────────────────────────────────────

class TestPickSignatureModel:
    """Test pick_signature_model logic."""

    def test_handwriting_model_wins_if_installed_and_vision(self):
        """Handwriting model from config wins if installed and vision-capable."""
        cfg = {
            "handwriting_model": "llama3.2-vision:latest",
            "vision_model": "qwen2.5vl:7b",
        }
        installed = ["llama3.2-vision:latest", "llama2:7b"]
        is_vision = lambda n: "vision" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "llama3.2-vision:latest"

    def test_handwriting_model_skipped_if_not_vision(self):
        """Handwriting model skipped if it's not vision-capable."""
        cfg = {
            "handwriting_model": "llama2:7b",  # Not vision
            "vision_model": "qwen2.5vl:7b",
        }
        installed = ["llama2:7b", "qwen2.5vl:7b"]
        is_vision = lambda n: "vision" in n.lower() or "qwen" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "qwen2.5vl:7b"

    def test_vision_model_from_config_if_not_handwriting(self):
        """Vision model from config used if no handwriting model."""
        cfg = {
            "handwriting_model": "",
            "vision_model": "minicpm-v:8b",
        }
        installed = ["minicpm-v:8b", "llama2:7b"]
        is_vision = lambda n: "vision" in n.lower() or "minicpm" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "minicpm-v:8b"

    def test_llama3_2_vision_preferred(self):
        """llama3.2-vision is preferred when no config models match."""
        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["qwen2.5vl:7b", "llama3.2-vision:latest", "llama2:7b"]
        is_vision = lambda n: "vision" in n.lower() or "qwen" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "llama3.2-vision:latest"

    def test_qwen_gemma_minicpm_second_tier(self):
        """qwen2.5vl, gemma3, minicpm-v preferred after llama3.2-vision."""
        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["gemma3:9b", "llama2:7b"]
        is_vision = lambda n: "vision" in n.lower() or "gemma" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "gemma3:9b"

    def test_smallest_vision_model_preferred(self):
        """Smallest (by param count) vision model chosen as final fallback."""
        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["moondream:1b", "cogvlm:7b", "some-vision:70b"]
        is_vision = lambda n: "vision" in n.lower() or "dream" in n.lower() or "cogvlm" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "moondream:1b"

    def test_no_vision_model_returns_none(self):
        """Returns None if no vision model available."""
        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["llama2:7b"]
        is_vision = lambda n: False

        result = pick_signature_model(cfg, installed, is_vision)
        assert result is None

    def test_tag_flexibility_in_installed_check(self):
        """Accepts config model with different tag if base name matches."""
        cfg = {
            "handwriting_model": "llama3.2-vision",  # No tag
            "vision_model": "",
        }
        installed = ["llama3.2-vision:latest"]  # Has tag
        is_vision = lambda n: "vision" in n.lower()

        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "llama3.2-vision"


# ────────────────────────────────────────────────────────────────────────────
# model_info cache tests
# ────────────────────────────────────────────────────────────────────────────

class TestModelInfoCache:
    """Test _ModelInfoCache behavior."""

    @async_test
    async def test_cache_returns_same_value_within_ttl(self):
        """Cache returns the same info if still within TTL."""
        cache = _ModelInfoCache(ttl_seconds=1.0)
        call_count = 0

        async def fetch_fn(name: str):
            nonlocal call_count
            call_count += 1
            return {"name": name, "size": 7}

        # First call should fetch
        result1 = await cache.get_or_fetch("model1", fetch_fn)
        assert result1 == {"name": "model1", "size": 7}
        assert call_count == 1

        # Second call within TTL should return cached
        result2 = await cache.get_or_fetch("model1", fetch_fn)
        assert result2 == {"name": "model1", "size": 7}
        assert call_count == 1  # No new fetch

    @async_test
    async def test_cache_expires_after_ttl(self):
        """Cache refetches after TTL expires."""
        cache = _ModelInfoCache(ttl_seconds=0.05)
        call_count = 0

        async def fetch_fn(name: str):
            nonlocal call_count
            call_count += 1
            return {"name": name, "call": call_count}

        # First call
        result1 = await cache.get_or_fetch("model1", fetch_fn)
        assert result1["call"] == 1

        # Wait for TTL to expire
        await asyncio.sleep(0.1)

        # Second call should refetch
        result2 = await cache.get_or_fetch("model1", fetch_fn)
        assert result2["call"] == 2

    @async_test
    async def test_cache_separate_entries_per_model(self):
        """Different models are cached separately."""
        cache = _ModelInfoCache(ttl_seconds=60.0)

        async def fetch_fn(name: str):
            return {"name": name}

        result1 = await cache.get_or_fetch("model1", fetch_fn)
        result2 = await cache.get_or_fetch("model2", fetch_fn)

        assert result1["name"] == "model1"
        assert result2["name"] == "model2"


# ────────────────────────────────────────────────────────────────────────────
# page_image_paths tests
# ────────────────────────────────────────────────────────────────────────────

class TestPageImagePaths:
    """Test page_image_paths hook behavior."""

    @async_test
    async def test_page_image_paths_fallback_to_stored_images_no_fitz(self):
        """Falls back to stored images when fitz not available."""
        # Create a mock document with page_images
        doc = MagicMock()
        doc.id = "doc1"
        doc.page_images = ["/storage/doc1/page_1.png", "/storage/doc1/page_2.png"]

        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp/test"))

        # Build hooks with fitz not available (patch fitz import)
        with patch.dict("sys.modules", {"fitz": None}):
            hooks = await build_hooks(deps)
            result = hooks.page_image_paths(doc, [1, 2], 150)

            # Should return stored image paths
            assert result == doc.page_images

    @async_test
    async def test_page_image_paths_clamps_pages_to_valid_range(self):
        """Clamps out-of-range page numbers to valid range."""
        doc = MagicMock()
        doc.id = "doc1"
        doc.page_images = ["/page1.png", "/page2.png", "/page3.png"]

        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp/test"))

        with patch.dict("sys.modules", {"fitz": None}):
            hooks = await build_hooks(deps)
            # Request pages 0, 1, 4 (0 and 4 are out of range)
            result = hooks.page_image_paths(doc, [0, 1, 4], 150)

            # Should clamp to valid pages: [1, 1, 3] -> dedupe -> [1, 3]
            assert result == ["/page1.png", "/page3.png"]

    @async_test
    async def test_page_image_paths_deduplicates_pages(self):
        """Deduplicates repeated page requests."""
        doc = MagicMock()
        doc.id = "doc1"
        doc.page_images = ["/page1.png", "/page2.png"]

        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp/test"))

        with patch.dict("sys.modules", {"fitz": None}):
            hooks = await build_hooks(deps)
            # Request page 1 three times
            result = hooks.page_image_paths(doc, [1, 1, 1], 150)

            # Should return just one copy of page 1
            assert result == ["/page1.png"]

    @async_test
    async def test_page_image_paths_returns_empty_if_no_stored_images(self):
        """Returns empty list if doc has no page_images."""
        doc = MagicMock()
        doc.id = "doc1"
        doc.page_images = []

        deps = MagicMock()

        with patch.dict("sys.modules", {"fitz": None}):
            hooks = await build_hooks(deps)
            result = hooks.page_image_paths(doc, [1, 2], 150)
            assert result == []

    @async_test
    async def test_page_image_paths_defaults_to_page_1(self):
        """Defaults to page 1 if no pages specified."""
        doc = MagicMock()
        doc.id = "doc1"
        doc.page_images = ["/page1.png", "/page2.png"]

        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp/test"))

        with patch.dict("sys.modules", {"fitz": None}):
            hooks = await build_hooks(deps)
            result = hooks.page_image_paths(doc, None, 150)
            assert result == ["/page1.png"]


# ────────────────────────────────────────────────────────────────────────────
# vision_call tests
# ────────────────────────────────────────────────────────────────────────────

class TestVisionCall:
    """Test vision_call hook with composition."""

    @async_test
    async def test_vision_call_multi_image_model(self):
        """Multi-image model passes reference + subject directly."""
        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=Path("/tmp/test"))
        deps.vision_call = AsyncMock(return_value="Multi-image response")

        with patch("image_compose.is_single_image_model", return_value=False):
            hooks = await build_hooks(deps)
            result = await hooks.vision_call(
                "qwen2.5vl:7b",
                "Compare these images",
                ["/ref1.png", "/ref2.png"],
                ["/subj1.png"],
            )

            # Should call deps.vision_call with all paths
            deps.vision_call.assert_called_once()
            call_args = deps.vision_call.call_args
            model, prompt, paths = call_args[0]
            assert model == "qwen2.5vl:7b"
            assert prompt == "Compare these images"
            assert paths == ["/ref1.png", "/ref2.png", "/subj1.png"]
            assert result == "Multi-image response"

    @async_test
    async def test_vision_call_single_image_model_composes(self):
        """Single-image model composes reference and subject."""
        tmp_dir = Path("/tmp/test")
        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=tmp_dir)
        deps.vision_call = AsyncMock(return_value="Single-image response")

        with patch("image_compose.is_single_image_model", return_value=True):
            with patch("image_compose.compose_comparison") as mock_compose:
                hooks = await build_hooks(deps)
                result = await hooks.vision_call(
                    "llama3.2-vision:latest",
                    "Compare these",
                    ["/ref.png"],
                    ["/subj.png"],
                )

                # Should compose images
                mock_compose.assert_called_once()
                compose_args = mock_compose.call_args[0]
                assert compose_args[0] == ["/ref.png"]
                assert compose_args[1] == ["/subj.png"]

                # Should call deps.vision_call with composite only
                deps.vision_call.assert_called_once()
                call_args = deps.vision_call.call_args[0]
                assert call_args[0] == "llama3.2-vision:latest"
                assert call_args[1] == "Compare these"
                # Third arg is list with composite path
                assert len(call_args[2]) == 1
                assert "composite.png" in call_args[2][0]

                assert result == "Single-image response"

    @async_test
    async def test_vision_call_single_image_cleanup_on_error(self):
        """Composite is cleaned up even if vision_call raises."""
        tmp_dir = Path("/tmp/test")
        tmp_dir.mkdir(exist_ok=True)

        # Create a fake composite file that would be created
        composite_path = tmp_dir / "composite.png"
        composite_path.write_text("fake image data")

        deps = MagicMock()
        deps.tmp_dir = MagicMock(return_value=tmp_dir)
        deps.vision_call = AsyncMock(side_effect=RuntimeError("Vision error"))

        with patch("image_compose.is_single_image_model", return_value=True):
            with patch("image_compose.compose_comparison") as mock_compose:
                # Make compose_comparison write the file
                def fake_compose(ref, subj, out):
                    Path(out).write_text("composite")
                mock_compose.side_effect = fake_compose

                hooks = await build_hooks(deps)

                # Should raise the vision error
                with pytest.raises(RuntimeError, match="Vision error"):
                    await hooks.vision_call(
                        "llama3.2-vision:latest",
                        "Compare",
                        ["/ref.png"],
                        ["/subj.png"],
                    )

                # Composite should be cleaned up
                assert not composite_path.exists()


# ────────────────────────────────────────────────────────────────────────────
# model_override tests
# ────────────────────────────────────────────────────────────────────────────

class TestModelOverride:
    """Test model_override behavior."""

    @async_test
    async def test_resolve_text_model_respects_override(self):
        """resolve_text_model returns override if set."""
        doc = MagicMock()
        deps = MagicMock()
        deps.model_override = "gpt-4"
        deps.resolve_doc_model_for = AsyncMock(return_value="qwen:7b")

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model(doc, "text")

        # Should return override, not call resolve_doc_model_for
        assert result == "gpt-4"
        deps.resolve_doc_model_for.assert_not_called()

    @async_test
    async def test_resolve_text_model_no_override(self):
        """resolve_text_model calls resolve_doc_model_for if no override."""
        doc = MagicMock()
        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="qwen:7b")

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model(doc, "text")

        # Should call resolve_doc_model_for
        assert result == "qwen:7b"
        deps.resolve_doc_model_for.assert_called_once_with(doc, "text")


# ────────────────────────────────────────────────────────────────────────────
# laya hooks tests
# ────────────────────────────────────────────────────────────────────────────

class TestLayaHooks:
    """Test Laya integration hooks."""

    @async_test
    async def test_laya_intent_returns_none_when_laya_unavailable(self):
        """laya_intent returns None if laya is None."""
        deps = MagicMock()
        deps.laya = None

        hooks = await build_hooks(deps)
        result = hooks.laya_intent("Test message", [])

        assert result is None

    @async_test
    async def test_laya_intent_catches_exceptions(self):
        """laya_intent catches and returns None on exception."""
        mock_laya = MagicMock()
        mock_laya.decide_intent.side_effect = RuntimeError("Laya error")

        deps = MagicMock()
        deps.laya = mock_laya

        hooks = await build_hooks(deps)
        result = hooks.laya_intent("Test message", [])

        # Should not raise, should return None
        assert result is None

    @async_test
    async def test_laya_role_returns_none_when_unavailable(self):
        """laya_role returns None if laya is None."""
        deps = MagicMock()
        deps.laya = None

        hooks = await build_hooks(deps)
        result = hooks.laya_role("Test message", {})

        assert result is None

    @async_test
    async def test_laya_doc_kind_returns_none_when_unavailable(self):
        """laya_doc_kind returns None if laya is None."""
        deps = MagicMock()
        deps.laya = None

        hooks = await build_hooks(deps)
        result = hooks.laya_doc_kind("Some text")

        assert result is None

    @async_test
    async def test_laya_info_returns_unavailable_when_none(self):
        """laya_info returns {available: False} when laya is None."""
        deps = MagicMock()
        deps.laya = None

        hooks = await build_hooks(deps)
        result = hooks.laya_info()

        assert result == {"available": False}

    @async_test
    async def test_laya_info_returns_full_status_when_available(self):
        """laya_info returns full details when laya is available."""
        mock_laya = MagicMock()
        mock_laya.status.return_value = {
            "device": "cpu",
            "available": True,
            "loaded": True,
        }

        deps = MagicMock()
        deps.laya = mock_laya

        hooks = await build_hooks(deps)
        result = hooks.laya_info()

        assert result["name"] == "Laya"
        assert result["variant"] == "English"
        assert result["params"] == "~421M"
        assert result["size_gb"] == 0.8
        assert result["type"] == "non-generative decision model"
        assert result["device"] == "cpu"
        assert result["available"] is True
        assert result["loaded"] is True

    @async_test
    async def test_laya_info_catches_exceptions(self):
        """laya_info catches exceptions and returns unavailable."""
        mock_laya = MagicMock()
        mock_laya.status.side_effect = RuntimeError("Status error")

        deps = MagicMock()
        deps.laya = mock_laya

        hooks = await build_hooks(deps)
        result = hooks.laya_info()

        assert result == {"available": False}


# ────────────────────────────────────────────────────────────────────────────
# stream_llm tests
# ────────────────────────────────────────────────────────────────────────────

class TestStreamLLM:
    """Test stream_llm hook."""

    @async_test
    async def test_stream_llm_passes_through_events(self):
        """stream_llm passes through events from deps.stream_text unchanged."""
        events = [
            {"content": "Hello"},
            {"content": " world"},
            {"thinking": "Internal thought"},
            {"content": "!"},
        ]

        async def mock_stream_text(model, prompt, *, think=False, num_predict=1536):
            for event in events:
                yield event

        deps = MagicMock()
        deps.stream_text = mock_stream_text

        hooks = await build_hooks(deps)
        result = []
        async for event in hooks.stream_llm("model1", "prompt", think=False):
            result.append(event)

        assert result == events

    @async_test
    async def test_stream_llm_passes_think_parameter(self):
        """stream_llm passes through think parameter."""
        async def mock_stream_text(model, prompt, *, think=False, num_predict=1536):
            yield {"think": think}

        deps = MagicMock()
        deps.stream_text = mock_stream_text

        hooks = await build_hooks(deps)
        result = []
        async for event in hooks.stream_llm("model1", "prompt", think=True):
            result.append(event)

        assert result == [{"think": True}]


# ────────────────────────────────────────────────────────────────────────────
# build_hooks completeness tests
# ────────────────────────────────────────────────────────────────────────────

class TestBuildHooks:
    """Test build_hooks returns complete AgentHooks."""

    @async_test
    async def test_build_hooks_returns_all_required_fields(self):
        """build_hooks returns AgentHooks with all required fields non-None."""
        deps = MagicMock()
        deps.get_doc = MagicMock(return_value=None)
        deps.resolve_doc_model = AsyncMock(return_value="qwen")
        deps.resolve_doc_model_for = AsyncMock(return_value="qwen")
        deps.get_config = AsyncMock(return_value="")
        deps.installed_models = AsyncMock(return_value=[])
        deps.name_is_vision = MagicMock(return_value=False)
        deps.ollama_tags = AsyncMock(return_value=[])
        deps.run_ocr = AsyncMock(return_value="")
        deps.stream_text = AsyncMock()
        deps.vision_call = AsyncMock(return_value="")
        deps.supports_thinking = MagicMock(return_value=False)
        deps.tmp_dir = MagicMock(return_value=Path("/tmp"))
        deps.laya = None
        deps.model_override = None

        hooks = await build_hooks(deps)

        # Check all fields exist and are callable/non-None
        assert hooks.get_doc is not None
        assert callable(hooks.get_doc)

        assert hooks.laya_intent is not None
        assert callable(hooks.laya_intent)

        assert hooks.laya_role is not None
        assert callable(hooks.laya_role)

        assert hooks.laya_doc_kind is not None
        assert callable(hooks.laya_doc_kind)

        assert hooks.laya_info is not None
        assert callable(hooks.laya_info)

        assert hooks.page_image_paths is not None
        assert callable(hooks.page_image_paths)

        assert hooks.resolve_model is not None
        assert callable(hooks.resolve_model)

        assert hooks.resolve_text_model is not None
        assert callable(hooks.resolve_text_model)

        assert hooks.pick_vision_model is not None
        assert callable(hooks.pick_vision_model)

        assert hooks.model_info is not None
        assert callable(hooks.model_info)

        assert hooks.run_ocr is not None
        assert callable(hooks.run_ocr)

        assert hooks.stream_llm is not None
        assert callable(hooks.stream_llm)

        assert hooks.vision_call is not None
        assert callable(hooks.vision_call)

        assert hooks.now_ms is not None
        assert callable(hooks.now_ms)

    @async_test
    async def test_build_hooks_now_ms_returns_milliseconds(self):
        """now_ms returns current time in milliseconds."""
        deps = MagicMock()
        deps.get_doc = MagicMock()
        deps.laya = None

        hooks = await build_hooks(deps)
        before = int(time.time() * 1000)
        result = hooks.now_ms()
        after = int(time.time() * 1000)

        assert before <= result <= after
        assert isinstance(result, int)


# ────────────────────────────────────────────────────────────────────────────
# Broken model cache tests
# ────────────────────────────────────────────────────────────────────────────

class TestBrokenModelCache:
    """Test broken model cache with TTL."""

    @async_test
    async def test_mark_and_check_broken(self):
        """Mark a model as broken and check its status."""
        from doc_agent_hooks import _BrokenModelCache

        cache = _BrokenModelCache(ttl_seconds=60.0)
        await cache.mark_broken("broken-model", "Load error")

        is_broken = await cache.is_broken("broken-model")
        assert is_broken is True

    @async_test
    async def test_broken_cache_ttl_expiry(self):
        """Broken cache entry expires after TTL."""
        from doc_agent_hooks import _BrokenModelCache

        # Use a very short TTL
        cache = _BrokenModelCache(ttl_seconds=0.05)
        await cache.mark_broken("model1", "Error")

        # Should be broken immediately
        assert await cache.is_broken("model1") is True

        # Wait for expiry
        await asyncio.sleep(0.1)

        # Should no longer be broken
        assert await cache.is_broken("model1") is False

    @async_test
    async def test_broken_cache_with_fake_clock(self):
        """Broken cache respects custom clock for testing."""
        from doc_agent_hooks import _BrokenModelCache

        # Create a fake clock that we can control
        current_time = [1000.0]

        def fake_clock():
            return current_time[0]

        cache = _BrokenModelCache(ttl_seconds=100.0, clock_fn=fake_clock)
        await cache.mark_broken("model1", "Error")

        # At time 1000, model should be broken
        assert await cache.is_broken("model1") is True

        # Fast-forward to 1050 (still within 100s TTL)
        current_time[0] = 1050.0
        assert await cache.is_broken("model1") is True

        # Fast-forward past TTL
        current_time[0] = 1150.0
        assert await cache.is_broken("model1") is False

    @async_test
    async def test_broken_cache_clear(self):
        """Clear all broken entries."""
        from doc_agent_hooks import _BrokenModelCache

        cache = _BrokenModelCache(ttl_seconds=60.0)
        await cache.mark_broken("model1", "Error")
        await cache.mark_broken("model2", "Error")

        assert await cache.is_broken("model1") is True
        assert await cache.is_broken("model2") is True

        await cache.clear()

        assert await cache.is_broken("model1") is False
        assert await cache.is_broken("model2") is False


# ────────────────────────────────────────────────────────────────────────────
# looks_like_load_failure tests
# ────────────────────────────────────────────────────────────────────────────

class TestLooksLikeLoadFailure:
    """Test load failure detection."""

    def test_unknown_model_architecture(self):
        """Detect 'unknown model architecture' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "error loading model: unknown model architecture: 'mllama'"
        assert looks_like_load_failure(err) is True

    def test_llama_server_terminated(self):
        """Detect 'llama-server process has terminated' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "llama-server process has terminated: exit status 1"
        assert looks_like_load_failure(err) is True

    def test_error_loading_model(self):
        """Detect 'error loading model' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "error loading model: CUDA out of memory"
        assert looks_like_load_failure(err) is True

    def test_failed_to_load_model(self):
        """Detect 'failed to load model' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "failed to load model weights"
        assert looks_like_load_failure(err) is True

    def test_requires_more_system_memory(self):
        """Detect 'requires more system memory' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "model requires more system memory than available"
        assert looks_like_load_failure(err) is True

    def test_model_runner_stopped(self):
        """Detect 'model runner has unexpectedly stopped' error."""
        from doc_agent_hooks import looks_like_load_failure

        err = "model runner has unexpectedly stopped"
        assert looks_like_load_failure(err) is True

    def test_case_insensitive(self):
        """Match case-insensitively."""
        from doc_agent_hooks import looks_like_load_failure

        err = "Error Loading Model: Something went wrong"
        assert looks_like_load_failure(err) is True

    def test_non_load_error_not_detected(self):
        """Non-load errors return False."""
        from doc_agent_hooks import looks_like_load_failure

        err = "timeout waiting for response"
        assert looks_like_load_failure(err) is False

        err = "connection refused"
        assert looks_like_load_failure(err) is False


# ────────────────────────────────────────────────────────────────────────────
# rank_signature_models tests
# ────────────────────────────────────────────────────────────────────────────

class TestRankSignatureModels:
    """Test ranking of vision models for signature comparison."""

    @async_test
    async def test_rank_prefers_config_handwriting_model(self):
        """Config handwriting model ranks first if installed and vision-capable."""
        from doc_agent_hooks import rank_signature_models

        # Mock deps
        deps = MagicMock()
        deps.get_config = AsyncMock(side_effect=lambda k: "llama3.2-vision:latest" if k == "handwriting_model" else "")
        deps.installed_models = AsyncMock(return_value=["llama3.2-vision:latest", "llama2:7b"])
        deps.name_is_vision = lambda n: "vision" in n.lower()
        deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])
        deps.ollama_tags = AsyncMock(return_value=[])

        cfg = {"handwriting_model": "llama3.2-vision:latest", "vision_model": "qwen2.5vl:7b"}
        installed = ["llama3.2-vision:latest", "llama2:7b"]
        tags = []

        result = await rank_signature_models(deps, cfg, installed, tags)
        assert result[0] == "llama3.2-vision:latest"

    @async_test
    async def test_rank_excludes_ocr_only_models(self):
        """OCR-only models excluded from ranking."""
        from doc_agent_hooks import rank_signature_models

        deps = MagicMock()
        deps.get_config = AsyncMock(return_value="")
        deps.installed_models = AsyncMock(return_value=["glm-ocr:latest", "llama3.2-vision:latest", "llama2:7b"])
        deps.name_is_vision = lambda n: any(x in n.lower() for x in ["vision", "ocr"])
        deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])
        deps.ollama_tags = AsyncMock(return_value=[])

        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["glm-ocr:latest", "llama3.2-vision:latest", "llama2:7b"]
        tags = []

        result = await rank_signature_models(deps, cfg, installed, tags)
        # glm-ocr should be excluded
        assert "glm-ocr:latest" not in result
        assert "llama3.2-vision:latest" in result

    @async_test
    async def test_rank_uses_capabilities_over_name_check(self):
        """Capability-based detection includes gemma4 even when name check fails."""
        from doc_agent_hooks import rank_signature_models

        deps = MagicMock()
        deps.get_config = AsyncMock(return_value="")
        deps.installed_models = AsyncMock(return_value=["gemma4:12b", "llama2:7b"])
        deps.name_is_vision = lambda n: "vision" in n.lower()  # gemma4 fails name check
        deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])  # But has capability
        deps.ollama_tags = AsyncMock(return_value=[])

        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["gemma4:12b", "llama2:7b"]
        tags = []

        result = await rank_signature_models(deps, cfg, installed, tags)
        # gemma4 should be included because of capability, despite name check failing
        assert "gemma4:12b" in result

    @async_test
    async def test_rank_respects_broken_cache(self):
        """Broken models excluded from ranking."""
        from doc_agent_hooks import rank_signature_models, _BrokenModelCache

        # Clear and mark a model as broken
        broken_cache = _BrokenModelCache(ttl_seconds=3600.0)
        await broken_cache.mark_broken("llama3.2-vision:latest", "Load error")

        # Monkey-patch the module-level broken cache
        import doc_agent_hooks
        old_cache = doc_agent_hooks._broken_cache
        doc_agent_hooks._broken_cache = broken_cache

        try:
            deps = MagicMock()
            deps.get_config = AsyncMock(return_value="")
            deps.installed_models = AsyncMock(return_value=["llama3.2-vision:latest", "minicpm-v:8b"])
            deps.name_is_vision = lambda n: any(x in n.lower() for x in ["vision", "minicpm"])
            deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])
            deps.ollama_tags = AsyncMock(return_value=[])

            cfg = {"handwriting_model": "", "vision_model": ""}
            installed = ["llama3.2-vision:latest", "minicpm-v:8b"]
            tags = []

            result = await rank_signature_models(deps, cfg, installed, tags)
            # llama3.2-vision should be excluded because it's broken
            assert "llama3.2-vision:latest" not in result
            assert "minicpm-v:8b" in result
        finally:
            # Restore
            doc_agent_hooks._broken_cache = old_cache

    @async_test
    async def test_rank_max_4_candidates(self):
        """Ranking returns at most 4 candidates."""
        from doc_agent_hooks import rank_signature_models

        deps = MagicMock()
        deps.get_config = AsyncMock(return_value="")
        deps.installed_models = AsyncMock(return_value=[
            "qwen2.5vl:7b", "minicpm-v:8b", "llama3.2-vision:latest",
            "gemma3:9b", "bakllava:13b", "cogvlm:4b"
        ])
        deps.name_is_vision = lambda n: any(x in n.lower() for x in ["vision", "qwen", "minicpm", "gemma", "bakllava", "cogvlm"])
        deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])
        deps.ollama_tags = AsyncMock(return_value=[])

        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["qwen2.5vl:7b", "minicpm-v:8b", "llama3.2-vision:latest", "gemma3:9b", "bakllava:13b", "cogvlm:4b"]
        tags = []

        result = await rank_signature_models(deps, cfg, installed, tags)
        assert len(result) <= 4


# ────────────────────────────────────────────────────────────────────────────
# New tests for is_ocr_only_model and resolve_text_model_info
# ────────────────────────────────────────────────────────────────────────────

class TestIsOcrOnlyModel:
    """Test is_ocr_only_model detection."""

    def test_detects_ocr_in_name(self):
        """Detect models with 'ocr' in the name."""
        from doc_agent_hooks import is_ocr_only_model

        assert is_ocr_only_model("hf.co/DevQuasar/baidu.Unlimited-OCR-GGUF:q4_k_m")
        assert is_ocr_only_model("glm-ocr:latest")
        assert is_ocr_only_model("richardyoung/olmocr2:7b-q8")
        assert is_ocr_only_model("frob/unlimited-ocr:q8_0")

    def test_detects_embedding_models(self):
        """Detect embedding models."""
        from doc_agent_hooks import is_ocr_only_model

        assert is_ocr_only_model("sentence-transformers/all-MiniLM-L6-v2:embed")
        assert is_ocr_only_model("nomic-embed-text:v1.5")
        assert is_ocr_only_model("bge-large-en-v1.5:embed")

    def test_rejects_text_models(self):
        """Reject normal text models."""
        from doc_agent_hooks import is_ocr_only_model

        assert not is_ocr_only_model("qwen2.5:7b")
        assert not is_ocr_only_model("gemma4:12b")
        assert not is_ocr_only_model("llama3.1:8b")
        assert not is_ocr_only_model("deepseek-r1:14b")

    def test_rejects_vision_models(self):
        """Reject vision models (unless they have ocr in the name)."""
        from doc_agent_hooks import is_ocr_only_model

        assert not is_ocr_only_model("llama3.2-vision:latest")
        assert not is_ocr_only_model("qwen2.5vl:7b")
        assert not is_ocr_only_model("minicpm-v:8b")

    def test_handles_empty_string(self):
        """Handle empty string gracefully."""
        from doc_agent_hooks import is_ocr_only_model

        assert not is_ocr_only_model("")
        assert not is_ocr_only_model(None)

    def test_case_insensitive(self):
        """OCR detection is case-insensitive."""
        from doc_agent_hooks import is_ocr_only_model

        assert is_ocr_only_model("GLM-OCR:latest")
        assert is_ocr_only_model("hf.co/DeepSeek/unlimited-OCR-q8")


class TestResolveTextModelInfo:
    """Test resolve_text_model_info with fallback logic."""

    @async_test
    async def test_override_respected(self):
        """Model override is respected even if OCR-named."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = "qwen2.5:7b"
        deps.resolve_doc_model_for = AsyncMock(return_value="hf.co/DevQuasar/baidu.Unlimited-OCR-GGUF:q4_k_m")

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        assert result["model"] == "qwen2.5:7b"
        assert result["reason"] is None
        assert result["configured"] is None

    @async_test
    async def test_no_fallback_if_configured_model_good(self):
        """No fallback if configured model is fine."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="qwen2.5:7b")
        deps.get_config = AsyncMock(return_value="")
        deps.installed_models = AsyncMock(return_value=["qwen2.5:7b"])

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        assert result["model"] == "qwen2.5:7b"
        assert result["reason"] is None
        assert result["configured"] is None

    @async_test
    async def test_fallback_from_ocr_to_multidoc(self):
        """Fall back from OCR-only docs_model to multidoc_model."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="glm-ocr:latest")
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=["glm-ocr:latest", "gemma4:26b"])

        # Mock get_config to return values for fallback chain
        async def get_config_side_effect(key):
            values = {
                "docs_model": "glm-ocr:latest",
                "multidoc_model": "gemma4:26b",
                "active_model": "deepseek-r1:14b",
            }
            return values.get(key, "")

        deps.get_config = AsyncMock(side_effect=get_config_side_effect)

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        assert result["model"] == "gemma4:26b"
        assert result["configured"] == "glm-ocr:latest"
        assert result["reason"] is not None
        assert "OCR-only" in result["reason"]

    @async_test
    async def test_fallback_excludes_embedding_models(self):
        """Fallback chain should exclude embedding models."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="glm-ocr:latest")
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=[
            "glm-ocr:latest",
            "nomic-embed-text:v1.5",  # This should be skipped
            "qwen2.5:7b"
        ])

        async def get_config_side_effect(key):
            values = {
                "docs_model": "glm-ocr:latest",
                "multidoc_model": "nomic-embed-text:v1.5",
                "active_model": "qwen2.5:7b",
            }
            return values.get(key, "")

        deps.get_config = AsyncMock(side_effect=get_config_side_effect)

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        # Should skip embedding model and use active_model
        assert result["model"] == "qwen2.5:7b"
        assert result["configured"] == "glm-ocr:latest"

    @async_test
    async def test_fallback_uses_builtin_prefs(self):
        """Fallback chain includes built-in model preferences."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="frob/unlimited-ocr:q8_0")
        deps.get_config = AsyncMock(return_value="")
        # qwen2.5:7b is in the built-in pref list
        deps.installed_models = AsyncMock(return_value=[
            "frob/unlimited-ocr:q8_0",
            "qwen2.5:7b"
        ])

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        # Should use built-in pref
        assert result["model"] == "qwen2.5:7b"
        assert result["configured"] == "frob/unlimited-ocr:q8_0"
        assert result["reason"] is not None

    @async_test
    async def test_fallback_reason_message(self):
        """Fallback reason message is informative."""
        from doc_agent_hooks import build_hooks

        deps = MagicMock()
        deps.model_override = None
        deps.resolve_doc_model_for = AsyncMock(return_value="glm-ocr:latest")
        deps.get_config = AsyncMock()
        deps.installed_models = AsyncMock(return_value=["glm-ocr:latest", "gemma4:12b"])

        async def get_config_side_effect(key):
            values = {
                "docs_model": "glm-ocr:latest",
                "multidoc_model": "gemma4:12b",
                "active_model": "",
            }
            return values.get(key, "")

        deps.get_config = AsyncMock(side_effect=get_config_side_effect)

        hooks = await build_hooks(deps)
        result = await hooks.resolve_text_model_info(None, "docs")

        assert result["reason"] is not None
        assert "glm-ocr" in result["reason"].lower() or "ocr" in result["reason"].lower()
        assert "gemma4" in result["reason"]
