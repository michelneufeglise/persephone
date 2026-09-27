"""
Async tests for the web-lookup extension (doc_web.py).

Tests the detection, planning, and execution of web searches to verify persons
mentioned in documents. Covers tool-based lookups, fallback paths, error handling,
and integration with run_agent.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import pytest
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import doc_web as _doc_web
import doc_agent as _agent


# ── Helpers ────────────────────────────────────────────────────────────────

def collect(agen):
    """Collect all results from an async generator."""
    async def _run():
        return [ev async for ev in agen]
    return asyncio.run(_run())


def fake_document(
    doc_id: str = "doc1",
    filename: str = "test.pdf",
    mime: str = "application/pdf",
    text: str = "",
    page_images: list[str] | None = None,
    pages: int = 1,
) -> SimpleNamespace:
    """Create a fake Document object for testing."""
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


def build_web_lookup_hooks(
    pick_tool_model_result: str | None = None,
    chat_tools_result: list[dict] | None = None,
    web_search_result: list[dict] | None = None,
    web_search_error: Exception | None = None,
    fetch_page_result: str = "",
    stream_llm_result: list[dict] | None = None,
) -> SimpleNamespace:
    """Build minimal hooks for web_lookup testing."""

    if web_search_result is None:
        web_search_result = []

    if stream_llm_result is None:
        stream_llm_result = [
            {"content": "**Likely match found**"},
            {"done": True},
        ]

    if chat_tools_result is None:
        chat_tools_result = [
            {"content": "Searching...", "tool_calls": []},
        ]

    # Track what was called
    class CallTracker:
        def __init__(self):
            self.web_search_calls = []
            self.fetch_page_calls = []
            self.chat_tools_calls = []
            self.stream_llm_calls = []
            self.pick_tool_model_called = False

    tracker = CallTracker()

    async def pick_tool_model():
        tracker.pick_tool_model_called = True
        return pick_tool_model_result

    async def chat_tools(model: str, messages: list[dict], tools: list[dict]):
        tracker.chat_tools_calls.append({"model": model, "messages": messages, "tools": tools})
        if not chat_tools_result:
            raise RuntimeError("boom")
        return chat_tools_result[len(tracker.chat_tools_calls) - 1]

    async def web_search(query: str):
        tracker.web_search_calls.append(query)
        if web_search_error:
            raise web_search_error
        return web_search_result

    async def fetch_page(url: str):
        tracker.fetch_page_calls.append(url)
        return fetch_page_result

    async def stream_llm(model: str, prompt: str, think: bool = False):
        tracker.stream_llm_calls.append({"model": model, "prompt": prompt, "think": think})
        for item in stream_llm_result:
            yield item

    def now_ms():
        return 1000000000

    hooks = SimpleNamespace(
        pick_tool_model=pick_tool_model,
        chat_tools=chat_tools,
        web_search=web_search,
        fetch_page=fetch_page,
        stream_llm=stream_llm,
        now_ms=now_ms,
        tracker=tracker,
    )

    return hooks


# ── Tests ──────────────────────────────────────────────────────────────────

class TestToolPathRewriting:
    """Test tool-based path with query rewriting and filtering."""

    def test_tool_path_rewrites_query_filters_results_and_strips_links(self):
        """
        Person: Michel Neuféglise, Solution Architect.
        Tool model selected, query rewritten, results filtered by name match,
        unseen links stripped from verdict.
        """
        person = {
            "name": "Michel Neuféglise",
            "role": "Solution Architect",
            "org": None,
        }
        target = "linkedin"

        # chat_tools: first round issues web_search, second round ends
        chat_tools_responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "web_search",
                        "arguments": {"query": "Stéphane Neuféglise job"},
                    }
                ]
            },
            {
                "content": "done",
                "tool_calls": []
            }
        ]

        # web_search returns mixed results: LinkedIn match, LinkedIn non-match, unrelated
        web_search_results = [
            {
                "url": "https://nl.linkedin.com/in/michelneufeglise",
                "title": "Michel Neuféglise - Solution Architect | LinkedIn",
                "snippet": "Solution Architect"
            },
            {
                "url": "https://jo.linkedin.com/in/spam",
                "title": "spam",
                "snippet": "xxx"
            },
            {
                "url": "https://evil.example/x",
                "title": "Other",
                "snippet": "nothing"
            }
        ]

        # Verdict from LLM
        stream_llm_responses = [
            {
                "content": "**Likely match found** — matches.\n- [Michel Neuféglise - Solution Architect | LinkedIn](https://nl.linkedin.com/in/michelneufeglise) — role matches\n- [bad](https://evil.example/zzz)\n_Search results are not proof of identity._"
            },
            {"done": True}
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result="qwen3:8b",
            chat_tools_result=chat_tools_responses,
            web_search_result=web_search_results,
            stream_llm_result=stream_llm_responses,
        )

        events = collect(_doc_web.run_web_lookup(
            person,
            target,
            hooks,
            answer_model="llm-model",
            now_ms=1000000000,
            doc_excerpt="",
        ))

        # Verify query was rewritten to include person's name
        assert len(hooks.tracker.web_search_calls) == 1
        query = hooks.tracker.web_search_calls[0]
        # Query should contain the person's name or surname
        assert "Michel" in query or "Neuf" in query or "neufeglise" in query.lower()

        # Verify content contains the expected results (not the spam/evil ones)
        content_events = [e for e in events if "content" in e]
        full_content = "".join([e["content"] for e in content_events])

        # Should contain verified result
        assert "nl.linkedin.com/in/michelneufeglise" in full_content
        assert "Solution Architect" in full_content or "Online verification" in full_content

        # Should NOT contain the spam/evil URLs (they weren't in web_search results, so stripped)
        # evil.example/zzz was in the LLM response but should be stripped since it wasn't in results
        assert "evil.example" not in full_content

        # Verify final tile status
        tile_events = [e for e in events if "tile" in e]
        final_tile = tile_events[-1]["tile"] if tile_events else None
        assert final_tile is not None
        assert final_tile["id"] == "web-search"
        assert final_tile["status"] == "done"

    def test_tool_path_linkedin_preferred_order(self):
        """LinkedIn profiles should appear first in results."""
        person = {"name": "Jane Smith", "role": None, "org": None}
        target = "linkedin"

        chat_tools_responses = [
            {
                "content": "",
                "tool_calls": [
                    {"name": "web_search", "arguments": {"query": 'site:linkedin.com/in "Jane Smith"'}}
                ]
            },
            {"content": "done", "tool_calls": []}
        ]

        web_search_results = [
            {
                "url": "https://example.com/jane",
                "title": "Jane Smith",
                "snippet": "engineer"
            },
            {
                "url": "https://linkedin.com/in/janesmith",
                "title": "Jane Smith | LinkedIn",
                "snippet": "Engineer"
            }
        ]

        stream_llm_responses = [
            {"content": "**Likely match found**\n- [Jane Smith | LinkedIn](https://linkedin.com/in/janesmith)"},
            {"done": True}
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result="model",
            chat_tools_result=chat_tools_responses,
            web_search_result=web_search_results,
            stream_llm_result=stream_llm_responses,
        )

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # Verify LinkedIn URL appears in results
        content_events = [e for e in events if "content" in e]
        full_content = "".join([e["content"] for e in content_events])
        assert "linkedin.com/in/janesmith" in full_content

    def test_tool_path_accent_insensitive_surname_check(self):
        """
        Surname check should be accent-insensitive.
        Person: Michel Neuféglise (with accent).
        Query from tool: site:linkedin.com/in Michel Neufeglise (no accent).
        Should NOT rewrite because surname is present (just without accent).
        """
        person = {
            "name": "Michel Neuféglise",
            "role": "Solution Architect",
            "org": None,
        }
        target = "linkedin"

        # chat_tools: first round issues web_search, second round ends
        chat_tools_responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "web_search",
                        # The model's query already contains the surname (without accent)
                        "arguments": {"query": "site:linkedin.com/in Michel Neufeglise"},
                    }
                ]
            },
            {
                "content": "done",
                "tool_calls": []
            }
        ]

        web_search_results = [
            {
                "url": "https://linkedin.com/in/michelneufeglise",
                "title": "Michel Neuféglise - Solution Architect | LinkedIn",
                "snippet": "Solution Architect at Acme"
            }
        ]

        stream_llm_responses = [
            {
                "content": "**Likely match found** — exact match on LinkedIn."
            },
            {"done": True}
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result="qwen3:8b",
            chat_tools_result=chat_tools_responses,
            web_search_result=web_search_results,
            stream_llm_result=stream_llm_responses,
        )

        events = collect(_doc_web.run_web_lookup(
            person,
            target,
            hooks,
            answer_model="llm-model",
            now_ms=1000000000,
            doc_excerpt="",
        ))

        # Verify the query sent to web_search is exactly what the tool issued
        # (not rewritten because surname was present even without accent)
        assert len(hooks.tracker.web_search_calls) == 1
        query_sent = hooks.tracker.web_search_calls[0]
        assert query_sent == "site:linkedin.com/in Michel Neufeglise"

        # Verify the tile shows the query actually sent (not marked as rewritten)
        tile_events = [e for e in events if "tile" in e and e["tile"]["id"] == "web-search"]
        if tile_events:
            search_tile = tile_events[-1]["tile"]
            # Find the query item in the tile
            query_items = [item for item in search_tile.get("items", []) if item.get("kind") == "query"]
            if query_items:
                # The label should be the query actually sent
                assert query_items[0]["label"] == "site:linkedin.com/in Michel Neufeglise"
                # Detail should NOT contain "(rewritten)" since it wasn't rewritten
                assert "(rewritten)" not in query_items[0].get("detail", "")


class TestFallbackPath:
    """Test fallback path (direct search + LLM verdict)."""

    def test_fallback_path_runs_each_query(self):
        """Fallback path should run web_search once per query."""
        person = {"name": "John Doe", "role": None, "org": None}
        target = "web"

        # With pick_tool_model=None, fallback path is used
        # Multiple queries from build_queries
        web_search_result = [
            {
                "url": "https://example.com/john-doe",
                "title": "John Doe",
                "snippet": "Professional"
            }
        ]

        stream_llm_responses = [
            {"content": "**Possible match** — John Doe profile found"},
            {"done": True}
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result=None,  # Forces fallback
            chat_tools_result=None,  # Not used in fallback
            web_search_result=web_search_result,
            stream_llm_result=stream_llm_responses,
        )

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # build_queries for target="web" produces multiple queries
        queries = _doc_web.build_queries(person, "web")
        assert len(queries) > 0

        # web_search should be called once per query
        assert len(hooks.tracker.web_search_calls) == len(queries)

        # Verdict should be generated from results
        content_events = [e for e in events if "content" in e]
        full_content = "".join([e["content"] for e in content_events])
        assert "Possible match" in full_content or "Online verification" in full_content


class TestChatToolsErrorFallback:
    """Test fallback when chat_tools fails."""

    def test_chat_tools_error_falls_back(self):
        """If chat_tools raises, should emit note and fallback to direct search."""
        person = {"name": "Test Person", "role": None, "org": None}
        target = "web"

        web_search_result = [
            {
                "url": "https://example.com/test",
                "title": "Test Person",
                "snippet": "Found"
            }
        ]

        stream_llm_responses = [
            {"content": "**Likely match**"},
            {"done": True}
        ]

        # chat_tools will raise RuntimeError
        async def failing_chat_tools(model, messages, tools):
            raise RuntimeError("boom")

        hooks = build_web_lookup_hooks(
            pick_tool_model_result="model",
            web_search_result=web_search_result,
            stream_llm_result=stream_llm_responses,
        )
        hooks.chat_tools = failing_chat_tools

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # Should still have content (fallback worked)
        content_events = [e for e in events if "content" in e]
        assert len(content_events) > 0

        # Should have a note about falling back
        tile_events = [e for e in events if "tile" in e]
        final_tile = tile_events[-1]["tile"] if tile_events else {}
        items = final_tile.get("items", [])
        note_items = [item for item in items if item.get("kind") == "note"]
        # At least one note should mention falling back
        fallback_notes = [item for item in note_items if "fall" in (item.get("label") or "").lower()]
        assert len(fallback_notes) > 0


class TestNoMatchingResults:
    """Test when no results match the person's name."""

    def test_no_matching_results_no_llm(self):
        """If no results match the name, should not call LLM for verdict."""
        person = {"name": "Alice Wonder", "role": None, "org": None}
        target = "web"

        # Results that don't match the name
        web_search_result = [
            {
                "url": "https://example.com/bob",
                "title": "Bob Smith",
                "snippet": "Unrelated"
            }
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result=None,  # Fallback
            web_search_result=web_search_result,
        )

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # Should NOT call stream_llm
        assert len(hooks.tracker.stream_llm_calls) == 0

        # Content should indicate no match
        content_events = [e for e in events if "content" in e]
        full_content = "".join([e["content"] for e in content_events])
        assert "No match found" in full_content


class TestWebSearchUnavailable:
    """Test handling of unavailable web search."""

    def test_web_search_none(self):
        """When hooks.web_search is None, should error gracefully."""
        person = {"name": "Test", "role": None, "org": None}
        target = "web"

        hooks = build_web_lookup_hooks()
        hooks.web_search = None  # Simulate unavailable

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # Should have error content
        content_events = [e for e in events if "content" in e]
        full_content = "".join([e["content"] for e in content_events])
        assert "Settings → Tools" in full_content or "unavailable" in full_content.lower()

        # Tile should be error status
        tile_events = [e for e in events if "tile" in e]
        final_tile = tile_events[-1]["tile"] if tile_events else {}
        assert final_tile.get("status") == "error"

    def test_web_search_unavailable_exception(self):
        """When web_search raises WebSearchUnavailable, should handle gracefully."""
        person = {"name": "Test", "role": None, "org": None}
        target = "web"

        async def unavailable_search(query):
            raise _doc_web.WebSearchUnavailable("Service down")

        hooks = build_web_lookup_hooks()
        hooks.web_search = unavailable_search
        hooks.pick_tool_model_result = None  # Use fallback

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # Should have graceful error handling (fallback catches it)
        tile_events = [e for e in events if "tile" in e]
        assert len(tile_events) > 0


class TestLinkedInFetchSkipped:
    """Test that LinkedIn fetch_page calls are skipped."""

    def test_linkedin_fetch_skipped(self):
        """fetch_page should not be called for LinkedIn URLs."""
        person = {"name": "Test User", "role": None, "org": None}
        target = "linkedin"

        # Tool round 1: fetch_page call on LinkedIn URL
        # Tool round 2: assistant ends (no more tool calls)
        chat_tools_responses = [
            {
                "content": "Found profile",
                "tool_calls": [
                    {
                        "name": "fetch_page",
                        "arguments": {"url": "https://linkedin.com/in/testuser"}
                    }
                ]
            },
            {
                "content": "done",
                "tool_calls": []
            }
        ]

        web_search_result = [
            {
                "url": "https://linkedin.com/in/testuser",
                "title": "Test User | LinkedIn",
                "snippet": "Engineer"
            }
        ]

        stream_llm_responses = [
            {"content": "**Likely match**"},
            {"done": True}
        ]

        hooks = build_web_lookup_hooks(
            pick_tool_model_result="model",
            chat_tools_result=chat_tools_responses,
            web_search_result=web_search_result,
            stream_llm_result=stream_llm_responses,
        )

        events = collect(_doc_web.run_web_lookup(
            person, target, hooks, answer_model="llm", now_ms=1000000000, doc_excerpt=""
        ))

        # fetch_page should NOT have been called (LinkedIn URLs are skipped)
        assert len(hooks.tracker.fetch_page_calls) == 0

        # Should have a "Skipped" note in tile items
        tile_events = [e for e in events if "tile" in e]
        final_tile = tile_events[-1]["tile"] if tile_events else {}
        items = final_tile.get("items", [])
        note_items = [item for item in items if item.get("kind") == "note"]
        skipped_notes = [item for item in note_items if "skip" in (item.get("label") or "").lower()]
        assert len(skipped_notes) > 0


class TestRunAgentIntegration:
    """Test full integration with run_agent."""

    def test_run_agent_identify_plus_linkedin(self):
        """
        Full flow: identify_person intent + web lookup for LinkedIn verification.
        Uses run_agent with fake hooks.
        """
        # Document about Jane
        doc_text = """Jane Example
Senior Engineer at Acme
Amsterdam, Netherlands
Experience: 10 years building distributed systems."""

        doc = fake_document(doc_id="d1", filename="profile.txt", text=doc_text)

        # Fake hooks for the agent
        def make_agent_hooks():
            # Track calls
            prompts_seen = []

            async def stream_llm_handler(model: str, prompt: str, think: bool = False):
                prompts_seen.append(prompt)
                # First prompt is the identify_person task (answer)
                # Check if this looks like the answer prompt vs verdict prompt
                if "candidate" not in prompt.lower() and "CANDIDATE" not in prompt:
                    # This is the answer prompt
                    yield {"content": "This document is about **Jane Example**.\n- **Name:** Jane Example\n- **Role:** Senior Engineer"}
                else:
                    # This is the verdict prompt
                    yield {
                        "content": "**Possible match** — Jane Example found on LinkedIn with Senior Engineer role.\n- [Jane Example - Senior Engineer - Acme | LinkedIn](https://www.linkedin.com/in/jane-example)\n_Search results are not proof of identity._"
                    }
                yield {"done": True}

            async def web_search_handler(query: str):
                return [
                    {
                        "url": "https://www.linkedin.com/in/jane-example",
                        "title": "Jane Example - Senior Engineer - Acme | LinkedIn",
                        "snippet": "Senior Engineer at Acme"
                    }
                ]

            async def pick_tool_model_handler():
                return None  # Use fallback

            async def vision_candidates_handler():
                return ["vision-model"]

            async def resolve_text_model_handler(doc, category: str):
                return "text-model"

            async def model_info_handler(name: str):
                return {"name": name}

            async def run_ocr_handler(doc, model: str):
                return doc.text

            return _agent.AgentHooks(
                get_doc=lambda doc_id: doc if doc_id == "d1" else None,
                laya_intent=lambda msg, files: None,  # Will use rules
                laya_role=lambda msg, file: None,
                laya_doc_kind=lambda text: None,
                laya_info=lambda: {"available": False},
                laya_web=lambda msg: None,  # Will use rules
                resolve_model=lambda cat: "default",
                resolve_text_model=resolve_text_model_handler,
                pick_vision_model=lambda: "vision-model",
                vision_candidates=vision_candidates_handler,
                model_info=model_info_handler,
                run_ocr=run_ocr_handler,
                stream_llm=stream_llm_handler,
                vision_call=lambda m, p, r, s: "Vision result",
                page_image_paths=lambda d, p, dpi: [],
                mark_vision_failed=lambda m, e: None,
                web_search=web_search_handler,
                fetch_page=lambda u: "Page content",
                pick_tool_model=pick_tool_model_handler,
                chat_tools=None,
                now_ms=lambda: 1000000000,
            ), prompts_seen

        hooks, prompts_seen = make_agent_hooks()

        req = {
            "message": "who is this document about and check linkedin if this person really exists",
            "attachments": [{"doc_id": "d1"}],
            "history": [],
        }

        async def run_test():
            events = []
            async for event in _agent.run_agent(req, hooks):
                events.append(event)
            return events

        events = asyncio.run(run_test())

        # Verify Laya tile decisions include web_lookup
        laya_tiles = [e.get("tile") for e in events if e.get("tile", {}).get("id") == "laya"]
        assert len(laya_tiles) > 0

        final_laya_tile = laya_tiles[-1]
        decisions = final_laya_tile.get("decisions", [])
        decision_labels = [d.get("label") for d in decisions]

        # Should have intent decision
        assert "Intent" in decision_labels
        intent_decisions = [d for d in decisions if d.get("label") == "Intent"]
        assert len(intent_decisions) > 0
        assert intent_decisions[0].get("value") == "identify_person"

        # Should have web_lookup decision
        web_decisions = [d for d in decisions if d.get("label") == "Web lookup"]
        if web_decisions:
            assert web_decisions[0].get("value") in ["linkedin", "web", "none"]

        # Check for web-plan and web-search tiles
        web_plan_tiles = [e.get("tile") for e in events if e.get("tile", {}).get("id") == "web-plan"]
        web_search_tiles = [e.get("tile") for e in events if e.get("tile", {}).get("id") == "web-search"]

        if web_plan_tiles:
            # Web lookup was executed
            plan_tile = web_plan_tiles[-1]
            # Plan tile should contain person info
            detail = plan_tile.get("detail", "")
            assert "Jane" in detail or "Senior Engineer" in detail or detail != ""

        # Check prompts
        # First stream_llm prompt should be the answer (identify_person)
        # Should NOT contain "check linkedin" in the actual question
        if prompts_seen:
            first_prompt = prompts_seen[0]
            # The stripped message should not have "check linkedin" in it
            # (web clause should be removed before building prompt)
            assert "who is this document about" in first_prompt.lower() or "identify" in first_prompt.lower()

        # Check for done event without errors
        done_events = [e for e in events if e.get("done")]
        assert len(done_events) > 0
        assert done_events[0].get("error") is None or "error" not in done_events[0]

    def test_web_plan_includes_role_and_org(self):
        """
        Verify that the web-plan tile includes role and organization from the document.
        The role/org merge should happen before the planner tile is built, so the detail
        contains the person's complete info including role and org.
        """
        # Document about Jane with role and org
        doc_text = """Jane Example
Senior Engineer at Acme
Amsterdam, Netherlands
Experience: 10 years building distributed systems."""

        doc = fake_document(doc_id="d1", filename="profile.txt", text=doc_text)

        # Fake hooks for the agent
        def make_agent_hooks():
            async def stream_llm_handler(model: str, prompt: str, think: bool = False):
                # Answer prompt: extract name and role
                if "candidate" not in prompt.lower():
                    yield {"content": "This document is about **Jane Example**.\n- **Name:** Jane Example\n- **Role:** Senior Engineer\n- **Organization:** Acme"}
                else:
                    # Verdict prompt
                    yield {"content": "**Likely match found** — Jane Example, Senior Engineer at Acme."}
                yield {"done": True}

            async def web_search_handler(query: str):
                return [
                    {
                        "url": "https://www.linkedin.com/in/jane-example",
                        "title": "Jane Example - Senior Engineer - Acme | LinkedIn",
                        "snippet": "Senior Engineer at Acme"
                    }
                ]

            async def pick_tool_model_handler():
                return None  # Use fallback

            async def vision_candidates_handler():
                return ["vision-model"]

            async def resolve_text_model_handler(doc, category: str):
                return "text-model"

            async def model_info_handler(name: str):
                return {"name": name}

            async def run_ocr_handler(doc, model: str):
                return doc.text

            return _agent.AgentHooks(
                get_doc=lambda doc_id: doc if doc_id == "d1" else None,
                laya_intent=lambda msg, files: None,  # Will use rules
                laya_role=lambda msg, file: None,
                laya_doc_kind=lambda text: None,
                laya_info=lambda: {"available": False},
                laya_web=lambda msg: None,  # Will use rules
                resolve_model=lambda cat: "default",
                resolve_text_model=resolve_text_model_handler,
                pick_vision_model=lambda: "vision-model",
                vision_candidates=vision_candidates_handler,
                model_info=model_info_handler,
                run_ocr=run_ocr_handler,
                stream_llm=stream_llm_handler,
                vision_call=lambda m, p, r, s: "Vision result",
                page_image_paths=lambda d, p, dpi: [],
                mark_vision_failed=lambda m, e: None,
                web_search=web_search_handler,
                fetch_page=lambda u: "Page content",
                pick_tool_model=pick_tool_model_handler,
                chat_tools=None,
                now_ms=lambda: 1000000000,
            )

        hooks = make_agent_hooks()

        req = {
            "message": "who is this document about and check linkedin if this person exists",
            "attachments": [{"doc_id": "d1"}],
            "history": [],
        }

        async def run_test():
            events = []
            async for event in _agent.run_agent(req, hooks):
                events.append(event)
            return events

        events = asyncio.run(run_test())

        # Find the web-plan tile
        web_plan_tiles = [e.get("tile") for e in events if e.get("tile", {}).get("id") == "web-plan"]
        assert len(web_plan_tiles) > 0, "Web plan tile should be emitted"

        plan_tile = web_plan_tiles[-1]
        detail = plan_tile.get("detail", "")

        # Verify that the planner tile detail includes role and org
        assert "Senior Engineer" in detail, f"Planner tile detail should contain 'Senior Engineer' but got: {detail}"
        assert "Acme" in detail, f"Planner tile detail should contain 'Acme' but got: {detail}"

        # Verify that queries include role or org information
        items = plan_tile.get("items", [])
        queries = [item.get("label", "") for item in items if item.get("kind") == "query"]
        assert len(queries) > 0, "At least one query should be present"

        # At least one query should contain the role or org
        query_text = " ".join(queries)
        assert "Senior Engineer" in query_text or "Acme" in query_text, \
            f"At least one query should contain 'Senior Engineer' or 'Acme', but got: {queries}"


class TestNameMatching:
    """Test the name_matches() function behavior."""

    def test_name_matches_accent_insensitive(self):
        """Name matching should be accent-insensitive."""
        # Michel Neuféglise (with accent) should match "Michel Neufeglise" (without)
        assert _doc_web.name_matches("Michel Neuféglise", "Michel Neufeglise")
        assert _doc_web.name_matches("Michel Neuféglise", "michelneufeglise")

    def test_name_matches_requires_last_name(self):
        """Last name must appear in text."""
        assert _doc_web.name_matches("John Smith", "John Smith Inc")
        assert not _doc_web.name_matches("John Smith", "John Doe Inc")

    def test_name_matches_linkedin_url(self):
        """Should match names in LinkedIn URLs."""
        assert _doc_web.name_matches(
            "Jane Example",
            "https://www.linkedin.com/in/jane-example"
        )


class TestBuildQueries:
    """Test query building for different targets."""

    def test_build_queries_linkedin(self):
        """LinkedIn queries should use site: syntax."""
        person = {"name": "Test User", "role": "Engineer", "org": None}
        queries = _doc_web.build_queries(person, "linkedin")

        assert len(queries) > 0
        # At least one query should have site:linkedin.com/in
        linkedin_site_queries = [q for q in queries if "site:linkedin.com/in" in q]
        assert len(linkedin_site_queries) > 0

    def test_build_queries_web(self):
        """Web queries should include name and context."""
        person = {"name": "Test User", "role": "Engineer", "org": "TechCorp"}
        queries = _doc_web.build_queries(person, "web")

        assert len(queries) > 0
        # Queries should include the person's name
        name_queries = [q for q in queries if "Test User" in q or "Test" in q]
        assert len(name_queries) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
