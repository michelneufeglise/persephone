"""
Behavioral tests for doc_agent_service module.

Tests cover SSE streaming, event forwarding, DB persistence, carry-over logic,
error handling, and cancellation. Uses asyncio.run() for async test execution.
"""

import asyncio
import json
import sys
from typing import Any, AsyncIterator, Optional
from unittest.mock import patch

# Add server/ to path
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from doc_agent_service import agent_sse


# ── Fake implementations ──

class FakeDoc:
    """Mock document object."""

    def __init__(self, doc_id: str, filename: str, mime: str, size: int = 1000, text: str = ""):
        self.id = doc_id
        self.filename = filename
        self.mime = mime
        self.size = size
        self.text = text
        self.page_images = []


class FakeDb:
    """In-memory fake database for testing."""

    def __init__(self):
        self.conversations: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}  # conv_id -> [messages]
        self.call_order: list[tuple] = []  # Track all DB calls in order

    async def get_conversation(self, conv_id: str) -> dict | None:
        self.call_order.append(("get_conversation", conv_id))
        if conv_id not in self.conversations:
            return None
        # Return a COPY of messages to avoid aliasing issues
        # (real DB would return a new list from fetched rows)
        return {
            **self.conversations[conv_id],
            "messages": list(self.messages.get(conv_id, [])),
        }

    async def upsert_conversation(self, data: dict) -> None:
        self.call_order.append(("upsert_conversation", data.get("id")))
        conv_id = data["id"]
        # Mimic real behavior: overwrites title
        self.conversations[conv_id] = {
            "id": data["id"],
            "title": data.get("title", "New conversation"),
            "model": data.get("model", ""),
            "createdAt": data.get("createdAt", 0),
            "updatedAt": data.get("updatedAt", 0),
            "pinned": data.get("pinned", 0),
        }

    async def upsert_message(self, conv_id: str, msg: dict) -> None:
        self.call_order.append(("upsert_message", conv_id, msg.get("id")))
        if conv_id not in self.messages:
            self.messages[conv_id] = []
        # Update or insert
        existing = next((m for m in self.messages[conv_id] if m["id"] == msg["id"]), None)
        if existing:
            existing.update(msg)
        else:
            self.messages[conv_id].append(msg)


class FakeHooks:
    """Mock hooks for testing."""

    def __init__(self):
        self.docs: dict[str, FakeDoc] = {}

    def get_doc(self, doc_id: str) -> Optional[FakeDoc]:
        return self.docs.get(doc_id)


# ── Helpers ──

def fake_now_ms():
    """Fake timestamp generator factory."""
    counter = [1000000000]

    def _now_ms():
        result = counter[0]
        counter[0] += 1000
        return result

    return _now_ms


def fake_new_id(prefix: str) -> str:
    """Fake ID generator."""
    return f"test-{prefix}-123"


# ── Test cases ──

class TestAgentSSEValidation:
    """Test validation and error handling."""

    def test_no_attachments_error(self):
        """No attachments and no prior docs -> error event + [DONE], zero DB writes."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            req = {
                "conversation_id": "conv-1",
                "message": "Analyze this",
                "attachments": [],
            }

            events = []
            async for chunk in agent_sse(req, fake_hooks, fake_db):
                events.append(chunk)

            # Should have error event and [DONE]
            assert len(events) == 2
            error_event = json.loads(events[0].replace("data: ", "").strip())
            assert "error" in error_event
            assert "Attach at least one document" in error_event["error"]
            assert events[1] == "data: [DONE]\n\n"

            # No DB writes
            assert len(fake_db.call_order) == 1  # Only get_conversation
            assert fake_db.call_order[0][0] == "get_conversation"

        asyncio.run(run_test())

    def test_unknown_doc_id_error(self):
        """Unknown doc_id -> error event + [DONE], zero DB writes."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            req = {
                "conversation_id": "conv-1",
                "message": "Analyze",
                "attachments": [{"doc_id": "unknown-doc"}],
            }

            events = []
            async for chunk in agent_sse(req, fake_hooks, fake_db):
                events.append(chunk)

            # Should have error event and [DONE]
            error_event = json.loads(events[0].replace("data: ", "").strip())
            assert "error" in error_event
            assert "not found" in error_event["error"].lower()
            assert events[-1] == "data: [DONE]\n\n"

            # No DB writes
            assert len(fake_db.call_order) == 1  # Only get_conversation
            assert fake_db.call_order[0][0] == "get_conversation"

        asyncio.run(run_test())


class TestAgentSSENormalRun:
    """Test normal run with event forwarding."""

    def test_all_events_forwarded(self):
        """Normal run: all events (tile, content, thinking, error, done) forwarded."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()
            now_ms = fake_now_ms()

            # Setup docs
            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf", text="Test content")
            fake_hooks.docs["doc-1"] = doc1

            # Setup mock run_agent events
            agent_events = [
                {"tile": {"id": "laya", "status": "running"}},
                {"content": "Part 1"},
                {"thinking": "Thinking..."},
                {"content": " Part 2"},
                {"tile": {"id": "answer", "status": "done"}},
                {"done": True, "stats": {"intent": "summarize"}},
            ]

            req = {
                "message": "Summarize this",
                "attachments": [{"doc_id": "doc-1"}],
            }

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    for event in agent_events:
                        yield event
                mock_run.return_value = mock_gen(None, None)

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db, now_ms=now_ms):
                    events.append(chunk)

            # Should have meta, all agent events, and [DONE]
            assert len(events) >= 8  # meta + 6 events + [DONE]

            # Verify meta event
            meta_event = json.loads(events[0].replace("data: ", "").strip())
            assert meta_event["meta"]["conversation_id"]
            assert meta_event["meta"]["run_id"]

            # Verify events are forwarded
            event_data = [json.loads(e.replace("data: ", "").strip()) for e in events[:-1]]
            content_events = [e for e in event_data if "content" in e]
            thinking_events = [e for e in event_data if "thinking" in e]
            done_events = [e for e in event_data if "done" in e]

            assert len(content_events) == 2
            assert "Part 1" in content_events[0]["content"]
            assert len(thinking_events) == 1
            assert len(done_events) == 1

            # Last event is [DONE]
            assert events[-1] == "data: [DONE]\n\n"

        asyncio.run(run_test())

    def test_db_call_order(self):
        """DB call order: get_conversation, upsert_conversation (new), upsert_message."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()
            now_ms = fake_now_ms()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            # Mock run_agent to complete immediately
            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "conversation_id": "conv-new",
                    "message": "Test",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db, now_ms=now_ms):
                    events.append(chunk)

            # Verify call order
            calls = [(c[0], c[1] if len(c) > 1 else None) for c in fake_db.call_order]
            assert calls[0][0] == "get_conversation"  # Initial check
            assert calls[1][0] == "upsert_conversation"  # New conversation
            assert calls[2][0] == "upsert_message"  # User message
            assert calls[3][0] == "upsert_message"  # Assistant message

        asyncio.run(run_test())


class TestAgentSSEFollowUp:
    """Test follow-up turns on existing conversations."""

    def test_existing_conversation_title_unchanged(self):
        """Follow-up: upsert_conversation NOT called, title unchanged."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            conv_id = "conv-existing"
            # Pre-populate conversation
            fake_db.conversations[conv_id] = {
                "id": conv_id,
                "title": "Original Title",
                "model": "",
                "createdAt": 0,
                "updatedAt": 0,
                "pinned": 0,
            }
            fake_db.messages[conv_id] = []

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "conversation_id": conv_id,
                    "message": "Follow-up question",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify upsert_conversation was NOT called
            upsert_calls = [c for c in fake_db.call_order if c[0] == "upsert_conversation"]
            assert len(upsert_calls) == 0

            # Verify title is still the original
            assert fake_db.conversations[conv_id]["title"] == "Original Title"

        asyncio.run(run_test())

    def test_history_passed_to_agent(self):
        """Follow-up: last 6 messages passed to agent (filtered by role, trimmed)."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            conv_id = "conv-history"
            # Pre-populate with messages
            fake_db.conversations[conv_id] = {
                "id": conv_id,
                "title": "Test",
                "model": "",
                "createdAt": 0,
                "updatedAt": 0,
                "pinned": 0,
            }
            fake_db.messages[conv_id] = [
                {"id": "msg-1", "role": "user", "content": "Q1", "meta": {}},
                {"id": "msg-2", "role": "system", "content": "System", "meta": {}},  # should be skipped
                {"id": "msg-3", "role": "assistant", "content": "A1", "meta": {}},
                {"id": "msg-4", "role": "user", "content": "x" * 2000, "meta": {}},  # should be trimmed
            ]

            captured_req = {}

            async def mock_run_agent(req, hooks):
                captured_req.update(req)
                yield {"done": True}

            with patch("doc_agent.run_agent", side_effect=mock_run_agent):
                req = {
                    "conversation_id": conv_id,
                    "message": "Follow-up",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify history
            history = captured_req.get("history", [])
            assert len(history) == 3  # user, assistant, user (system skipped)
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "Q1"
            assert history[1]["role"] == "assistant"
            assert history[2]["role"] == "user"
            assert len(history[2]["content"]) == 1500  # trimmed

        asyncio.run(run_test())


class TestAgentSSECarryOver:
    """Test document carry-over for follow-ups."""

    def test_carry_over_from_prior_message(self):
        """Follow-up without attachments: use docs from most recent user message with attachments."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "doc1.pdf", "application/pdf")
            doc2 = FakeDoc("doc-2", "doc2.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1
            fake_hooks.docs["doc-2"] = doc2

            conv_id = "conv-carryover"
            fake_db.conversations[conv_id] = {
                "id": conv_id,
                "title": "Test",
                "model": "",
                "createdAt": 0,
                "updatedAt": 0,
                "pinned": 0,
            }
            fake_db.messages[conv_id] = [
                {
                    "id": "msg-1",
                    "role": "user",
                    "content": "Analyze",
                    "meta": {
                        "kind": "doc_user",
                        "attachments": [
                            {"doc_id": "doc-1", "name": "doc1.pdf", "kind": "pdf", "role": "subject", "size": 1000},
                            {"doc_id": "doc-2", "name": "doc2.pdf", "kind": "pdf", "role": "reference", "size": 500},
                        ],
                    },
                },
                {"id": "msg-2", "role": "assistant", "content": "Result", "meta": {}},
            ]

            captured_req = {}
            async def mock_run_agent(req, hooks):
                captured_req.update(req)
                yield {"done": True}

            with patch("doc_agent.run_agent", side_effect=mock_run_agent):
                req = {
                    "conversation_id": conv_id,
                    "message": "Follow-up question",
                    "attachments": [],  # No explicit attachments
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify attachments were carried over
            user_messages = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            new_user_msg = user_messages[-1]
            assert new_user_msg["meta"]["carried_over"] is True
            assert len(new_user_msg["meta"]["attachments"]) == 2
            assert new_user_msg["meta"]["attachments"][0]["role"] == "subject"
            assert new_user_msg["meta"]["attachments"][1]["role"] == "reference"

        asyncio.run(run_test())

    def test_carry_over_skips_deleted_docs(self):
        """Carry-over: skip docs that no longer exist."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "doc1.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1
            # doc-2 doesn't exist

            conv_id = "conv-deleted"
            fake_db.conversations[conv_id] = {
                "id": conv_id,
                "title": "Test",
                "model": "",
                "createdAt": 0,
                "updatedAt": 0,
                "pinned": 0,
            }
            fake_db.messages[conv_id] = [
                {
                    "id": "msg-1",
                    "role": "user",
                    "content": "Analyze",
                    "meta": {
                        "kind": "doc_user",
                        "attachments": [
                            {"doc_id": "doc-1", "name": "doc1.pdf", "kind": "pdf", "role": "auto", "size": 1000},
                            {"doc_id": "doc-2", "name": "doc2.pdf", "kind": "pdf", "role": "auto", "size": 500},
                        ],
                    },
                },
            ]

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "conversation_id": conv_id,
                    "message": "Follow-up",
                    "attachments": [],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify only existing doc was carried over
            user_messages = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            new_user_msg = user_messages[-1]
            assert len(new_user_msg["meta"]["attachments"]) == 1
            assert new_user_msg["meta"]["attachments"][0]["doc_id"] == "doc-1"

        asyncio.run(run_test())


class TestAgentSSEMetadata:
    """Test metadata structures."""

    def test_user_message_meta(self):
        """User message meta: kind, attachments (with proper kinds), carried_over."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            doc2 = FakeDoc("doc-2", "photo.jpg", "image/jpeg")
            fake_hooks.docs["doc-1"] = doc1
            fake_hooks.docs["doc-2"] = doc2

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [
                        {"doc_id": "doc-1", "role": "subject"},
                        {"doc_id": "doc-2"},  # role defaults to "auto"
                    ],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Get the user message
            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            user_msg = user_msgs[0]

            meta = user_msg["meta"]
            assert meta["kind"] == "doc_user"
            assert meta["carried_over"] is False
            assert len(meta["attachments"]) == 2

            att1 = meta["attachments"][0]
            assert att1["doc_id"] == "doc-1"
            assert att1["kind"] == "pdf"  # short kind, not MIME
            assert att1["role"] == "subject"
            assert att1["name"] == "test.pdf"

            att2 = meta["attachments"][1]
            assert att2["doc_id"] == "doc-2"
            assert att2["kind"] == "image"  # short kind
            assert att2["role"] == "auto"  # defaults to auto

        asyncio.run(run_test())

    def test_title_new_conversation_with_message(self):
        """New conversation title: message[:60] when message provided."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "my_document.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                # Case 1: message provided
                req1 = {
                    "message": "This is a long message " * 3,
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events1 = []
                async for chunk in agent_sse(req1, fake_hooks, fake_db):
                    events1.append(chunk)

                conv_id1 = json.loads(events1[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                assert fake_db.conversations[conv_id1]["title"][:20] == "This is a long messa"
                assert len(fake_db.conversations[conv_id1]["title"]) == 60

        asyncio.run(run_test())

    def test_title_new_conversation_empty_message_uses_filename(self):
        """New conversation with empty message: uses first document's filename as title."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "my_important_document.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                # Case 2: empty message - should use filename
                req2 = {
                    "message": "",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events2 = []
                async for chunk in agent_sse(req2, fake_hooks, fake_db):
                    events2.append(chunk)

                conv_id2 = json.loads(events2[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                title = fake_db.conversations[conv_id2]["title"]
                assert title == "my_important_document.pdf"

        asyncio.run(run_test())

    def test_assistant_message_meta(self):
        """Assistant message meta: kind=doc_run, intent, doc_ids, tiles, error."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"tile": {"id": "laya", "status": "done"}}
                    yield {"content": "Result"}
                    yield {"done": True, "stats": {"intent": "summarize"}}

                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            assistant_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "assistant"]
            asst_msg = assistant_msgs[0]

            meta = asst_msg["meta"]
            assert meta["kind"] == "doc_run"
            assert meta["doc_ids"] == ["doc-1"]
            assert "tiles" in meta
            assert meta["intent"] == "summarize"

        asyncio.run(run_test())


class TestAgentSSEErrorHandling:
    """Test error handling and partial content."""

    def test_error_event_in_stream(self):
        """Agent yields error event: forwarded, persisted, [DONE] emitted."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"content": "Partial result "}
                    yield {"error": "Processing failed"}
                    yield {"done": True}

                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify error event forwarded
            event_data = [json.loads(e.replace("data: ", "").strip()) for e in events[:-1]]
            error_events = [e for e in event_data if "error" in e]
            assert len(error_events) == 1
            assert "Processing failed" in error_events[0]["error"]

            # Verify [DONE]
            assert events[-1] == "data: [DONE]\n\n"

            # Verify partial content persisted
            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            assistant_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "assistant"]
            asst_msg = assistant_msgs[0]
            assert "Partial result" in asst_msg["content"]
            assert asst_msg["meta"]["error"] == "Processing failed"

        asyncio.run(run_test())

    def test_exception_mid_run(self):
        """Exception during agent run: error SSE event sent, partial content preserved, [DONE] emitted."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"content": "Partial "}
                    raise RuntimeError("Model crashed")

                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify exactly one [DONE] was emitted (even though exception occurred)
            done_count = sum(1 for e in events if e == "data: [DONE]\n\n")
            assert done_count == 1
            assert events[-1] == "data: [DONE]\n\n"

            # Verify error SSE event was sent
            event_data = [json.loads(e.replace("data: ", "").strip()) for e in events if e != "data: [DONE]\n\n"]
            error_events = [e for e in event_data if "error" in e]
            assert len(error_events) >= 1
            assert "Model crashed" in str(error_events[0]["error"])

            # Verify partial content and error were persisted
            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            assistant_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "assistant"]
            asst_msg = assistant_msgs[0]
            assert "Partial" in asst_msg["content"]
            assert "error" in asst_msg["meta"]
            assert "Model crashed" in asst_msg["meta"]["error"]

        asyncio.run(run_test())

    def test_exception_before_validation(self):
        """Exception before validation (db.get_conversation raises): error event, no message writes."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            # Make get_conversation raise an exception
            async def mock_get_conversation(conv_id):
                raise RuntimeError("Database connection failed")

            fake_db.get_conversation = mock_get_conversation

            req = {
                "conversation_id": "conv-1",
                "message": "Analyze",
                "attachments": [{"doc_id": "doc-1"}],
            }

            events = []
            async for chunk in agent_sse(req, fake_hooks, fake_db):
                events.append(chunk)

            # Should have error event and exactly one [DONE]
            done_count = sum(1 for e in events if e == "data: [DONE]\n\n")
            assert done_count == 1
            assert events[-1] == "data: [DONE]\n\n"

            # Verify error event was sent
            event_data = [json.loads(e.replace("data: ", "").strip()) for e in events if e != "data: [DONE]\n\n"]
            error_events = [e for e in event_data if "error" in e]
            assert len(error_events) >= 1
            assert "Database connection failed" in str(error_events[0]["error"])

            # No messages should be written (validation never passed)
            assert len(fake_db.messages) == 0

        asyncio.run(run_test())

    def test_generator_aclose_mid_run(self):
        """aclose() on async generator mid-run: assistant persisted with partial + cancelled flag."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"content": "Part 1 "}
                    await asyncio.sleep(10)  # Long wait to allow aclose
                    yield {"content": "Part 2"}

                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                agen = agent_sse(req, fake_hooks, fake_db)

                # Manually iterate and then close
                events = []
                try:
                    # Get a few events
                    events.append(await agen.__anext__())  # meta
                    events.append(await agen.__anext__())  # first content
                    # Now close the generator
                    await agen.aclose()
                except StopAsyncIteration:
                    pass

            # After aclose, no RuntimeError should have occurred
            # Check that assistant message was persisted with cancelled flag
            conv_id_data = json.loads(events[0].replace("data: ", "").strip())
            conv_id = conv_id_data["meta"]["conversation_id"]
            assistant_msgs = [m for m in fake_db.messages.get(conv_id, []) if m["role"] == "assistant"]

            if assistant_msgs:
                asst_msg = assistant_msgs[0]
                assert asst_msg["meta"].get("cancelled") is True
                assert "Part 1" in asst_msg["content"]

        asyncio.run(run_test())


class TestAgentSSEIDAndTimestamp:
    """Test custom ID and timestamp handling."""

    def test_custom_id_and_timestamp(self):
        """Custom new_id and now_ms are used correctly."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()
            now_ms = fake_now_ms()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(
                    req, fake_hooks, fake_db, new_id=fake_new_id, now_ms=now_ms
                ):
                    events.append(chunk)

            # Verify custom IDs were used
            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            user_msg = user_msgs[0]

            assert user_msg["id"] == "test-user-123"
            # Timestamps should be from fake_now_ms
            assert user_msg["timestamp"] in (1000000000, 1000001000, 1000002000)

        asyncio.run(run_test())

    def test_provided_message_ids_used(self):
        """User-provided message IDs are used verbatim."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc1 = FakeDoc("doc-1", "test.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                    "user_message_id": "user-msg-custom",
                    "assistant_message_id": "asst-msg-custom",
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            # Verify custom IDs
            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            asst_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "assistant"]

            assert user_msgs[0]["id"] == "user-msg-custom"
            assert asst_msgs[0]["id"] == "asst-msg-custom"

        asyncio.run(run_test())


class TestKindOfFunction:
    """Test _kind_of document classification by filename and MIME."""

    def test_kind_of_pdf(self):
        """PDF files detected by extension and MIME."""
        async def run_test():
            # We need to test _kind_of, which is an inner function
            # We'll do this by creating docs and checking the persisted metadata
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            # Test PDF by extension
            doc1 = FakeDoc("doc-1", "report.pdf", "application/pdf")
            fake_hooks.docs["doc-1"] = doc1

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {
                    "message": "Analyze",
                    "attachments": [{"doc_id": "doc-1"}],
                }

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            assert user_msgs[0]["meta"]["attachments"][0]["kind"] == "pdf"

        asyncio.run(run_test())

    def test_kind_of_docx_by_extension(self):
        """DOCX/DOC/ODT/RTF detected by extension."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            for filename, mime in [
                ("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                ("doc.doc", "application/msword"),
                ("doc.odt", "application/vnd.oasis.opendocument.text"),
                ("doc.rtf", "application/rtf"),
            ]:
                fake_hooks.docs = {}
                doc = FakeDoc("test-doc", filename, mime)
                fake_hooks.docs["test-doc"] = doc

                with patch("doc_agent.run_agent") as mock_run:
                    async def mock_gen(req, hooks):
                        yield {"done": True}
                    mock_run.return_value = mock_gen(None, None)

                    req = {"message": "Test", "attachments": [{"doc_id": "test-doc"}]}

                    events = []
                    async for chunk in agent_sse(req, fake_hooks, fake_db):
                        events.append(chunk)

                conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
                kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
                assert kind == "docx", f"Expected 'docx' for {filename}, got {kind}"

        asyncio.run(run_test())

    def test_kind_of_xlsx_by_extension_and_mime(self):
        """XLSX/XLS/CSV detected by extension and by officedocument MIME."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            for filename, mime in [
                ("data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                ("data.xls", "application/vnd.ms-excel"),
                ("data.csv", "text/csv"),
                ("data.ods", "application/vnd.oasis.opendocument.spreadsheet"),
                ("data.tsv", "text/tab-separated-values"),
            ]:
                fake_hooks.docs = {}
                doc = FakeDoc("test-sheet", filename, mime)
                fake_hooks.docs["test-sheet"] = doc

                with patch("doc_agent.run_agent") as mock_run:
                    async def mock_gen(req, hooks):
                        yield {"done": True}
                    mock_run.return_value = mock_gen(None, None)

                    req = {"message": "Test", "attachments": [{"doc_id": "test-sheet"}]}

                    events = []
                    async for chunk in agent_sse(req, fake_hooks, fake_db):
                        events.append(chunk)

                conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
                kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
                assert kind == "sheet", f"Expected 'sheet' for {filename}, got {kind}"

        asyncio.run(run_test())

    def test_kind_of_pptx_other(self):
        """PPTX/PPT files should return 'other'."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            doc = FakeDoc("test-ppt", "slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
            fake_hooks.docs["test-ppt"] = doc

            with patch("doc_agent.run_agent") as mock_run:
                async def mock_gen(req, hooks):
                    yield {"done": True}
                mock_run.return_value = mock_gen(None, None)

                req = {"message": "Test", "attachments": [{"doc_id": "test-ppt"}]}

                events = []
                async for chunk in agent_sse(req, fake_hooks, fake_db):
                    events.append(chunk)

            conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
            user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
            kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
            assert kind == "other"

        asyncio.run(run_test())

    def test_kind_of_image(self):
        """Image files (PNG, JPG, etc.) detected by extension."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            for filename in ["photo.png", "pic.jpg", "image.jpeg", "graphic.webp", "anim.gif", "scan.bmp", "tiff.tiff"]:
                fake_hooks.docs = {}
                doc = FakeDoc("test-img", filename, "image/jpeg")
                fake_hooks.docs["test-img"] = doc

                with patch("doc_agent.run_agent") as mock_run:
                    async def mock_gen(req, hooks):
                        yield {"done": True}
                    mock_run.return_value = mock_gen(None, None)

                    req = {"message": "Test", "attachments": [{"doc_id": "test-img"}]}

                    events = []
                    async for chunk in agent_sse(req, fake_hooks, fake_db):
                        events.append(chunk)

                conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
                kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
                assert kind == "image", f"Expected 'image' for {filename}, got {kind}"

        asyncio.run(run_test())

    def test_kind_of_email(self):
        """Email files (.eml, .msg) detected by extension."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            for filename in ["message.eml", "email.msg"]:
                fake_hooks.docs = {}
                doc = FakeDoc("test-email", filename, "message/rfc822")
                fake_hooks.docs["test-email"] = doc

                with patch("doc_agent.run_agent") as mock_run:
                    async def mock_gen(req, hooks):
                        yield {"done": True}
                    mock_run.return_value = mock_gen(None, None)

                    req = {"message": "Test", "attachments": [{"doc_id": "test-email"}]}

                    events = []
                    async for chunk in agent_sse(req, fake_hooks, fake_db):
                        events.append(chunk)

                conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
                kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
                assert kind == "email", f"Expected 'email' for {filename}, got {kind}"

        asyncio.run(run_test())

    def test_kind_of_text(self):
        """Text files (txt, md, html, json, xml) detected by extension."""
        async def run_test():
            fake_db = FakeDb()
            fake_hooks = FakeHooks()

            for filename in ["note.txt", "readme.md", "page.html", "config.json", "data.xml"]:
                fake_hooks.docs = {}
                doc = FakeDoc("test-text", filename, "text/plain")
                fake_hooks.docs["test-text"] = doc

                with patch("doc_agent.run_agent") as mock_run:
                    async def mock_gen(req, hooks):
                        yield {"done": True}
                    mock_run.return_value = mock_gen(None, None)

                    req = {"message": "Test", "attachments": [{"doc_id": "test-text"}]}

                    events = []
                    async for chunk in agent_sse(req, fake_hooks, fake_db):
                        events.append(chunk)

                conv_id = json.loads(events[0].replace("data: ", "").strip())["meta"]["conversation_id"]
                user_msgs = [m for m in fake_db.messages[conv_id] if m["role"] == "user"]
                kind = user_msgs[0]["meta"]["attachments"][0]["kind"]
                assert kind == "text", f"Expected 'text' for {filename}, got {kind}"

        asyncio.run(run_test())
