"""
SSE-streamed document agent service.

Handles conversation/message creation, runs the agent orchestrator,
persists results to the database, and streams events as SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Any, AsyncIterator, Callable, Optional

log = logging.getLogger("doc_agent_service")


async def agent_sse(
    req: dict[str, Any],
    hooks: Any,  # AgentHooks
    db: Any,  # Database interface
    *,
    new_id: Optional[Callable[[str], str]] = None,
    now_ms: Optional[Callable[[], int]] = None,
) -> AsyncIterator[str]:
    """
    Run the document agent and stream events as Server-Sent Events (SSE).

    Behavior:
    1. Validate request (attachments or carry-over; all docs must exist)
    2. Create conversation only if new
    3. Persist user message BEFORE running the agent
    4. Run agent, collect events, forward ALL events as SSE (including done and error)
    5. In finally block: persist assistant message (always, if validation passed)
    6. End with [DONE] exactly once, outside finally (never in finally)

    Args:
        req: {
            "conversation_id"?: str,
            "message": str,
            "attachments": [{"doc_id": str, "role"?: "auto"|"subject"|"reference"}],
            "model_override"?: str,
            "user_message_id"?: str,
            "assistant_message_id"?: str,
            "history"?: list[dict],
        }
        hooks: AgentHooks instance with get_doc, resolve_model, stream_llm, etc.
        db: Database interface with async upsert_conversation, upsert_message, get_conversation
        new_id: Custom ID generation function (prefix: str) -> str or None to use defaults
        now_ms: Custom timestamp function () -> int (ms) or None to use current time

    Yields:
        SSE strings: "data: {json}\n\n" for all events, final "data: [DONE]\n\n"
    """
    from doc_agent import run_agent, RunCollector

    # ── Helpers for ID and timestamp generation ──
    def _gen_conv_id() -> str:
        return f"dconv-{secrets.token_hex(7)}"

    def _gen_msg_id(prefix: str) -> str:
        return f"dmsg-{prefix}-{secrets.token_hex(6)}"

    def _get_now_ms() -> int:
        return int(time.time() * 1000)

    def _kind_of(doc: Any) -> str:
        """Convert doc's MIME type to short kind string.

        Priority:
        1. Check filename extension (most reliable)
        2. Fall back to MIME type
        3. Default to 'other'
        """
        filename = getattr(doc, "filename", "") or ""
        mime = getattr(doc, "mime", "") or ""

        # Normalize filename to lowercase for extension checks
        filename_lower = filename.lower()

        # Check by filename extension first (most reliable)
        if filename_lower.endswith(('.pdf',)):
            return "pdf"
        if filename_lower.endswith(('.docx', '.doc', '.odt', '.rtf')):
            return "docx"
        if filename_lower.endswith(('.xlsx', '.xls', '.csv', '.ods', '.tsv')):
            return "sheet"
        if filename_lower.endswith(('.pptx', '.ppt', '.odp')):
            return "other"
        if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff')):
            return "image"
        if filename_lower.endswith(('.eml', '.msg')):
            return "email"
        if filename_lower.endswith(('.txt', '.md', '.html', '.json', '.xml')):
            return "text"

        # Fall back to MIME type
        mime_lower = mime.lower()
        if "pdf" in mime_lower:
            return "pdf"
        if "word" in mime_lower or "msword" in mime_lower or "officedocument" in mime_lower and "wordprocessing" in mime_lower:
            return "docx"
        if "sheet" in mime_lower or "csv" in mime_lower or "officedocument" in mime_lower and "spreadsheet" in mime_lower:
            return "sheet"
        if "image" in mime_lower:
            return "image"
        if "message/rfc822" in mime_lower or "email" in mime_lower:
            return "email"
        if "text" in mime_lower:
            return "text"
        return "other"

    # Override with provided callables or use defaults
    if new_id is None:
        new_id = _gen_msg_id
    if now_ms is None:
        now_ms = _get_now_ms

    # ── Extract request fields ──
    message = req.get("message", "").strip()
    attachments = req.get("attachments", [])
    model_override = req.get("model_override")
    user_message_id = req.get("user_message_id") or new_id("user")
    assistant_message_id = req.get("assistant_message_id") or new_id("asst")

    # Conversation and run IDs
    conv_id = req.get("conversation_id") or _gen_conv_id()
    run_id = str(uuid.uuid4()).replace("-", "")[:16]

    # State for finally block
    collector = RunCollector()
    cancelled = False
    validation_passed = False  # Track if we got past validation

    try:
        # ── Phase 1: Get existing conversation ──
        existing_conv = await db.get_conversation(conv_id)
        is_new_conv = existing_conv is None

        # ── Phase 2: Validate attachments or carry-over ──
        doc_ids = []
        carried_over = False
        final_attachments = attachments

        if attachments:
            # Explicit attachments provided; validate each one
            for att in attachments:
                doc_id = att.get("doc_id")
                if not doc_id:
                    continue
                doc = hooks.get_doc(doc_id)
                if not doc:
                    yield f'data: {json.dumps({"error": f"Document not found: {doc_id}"})}\n\n'
                    yield "data: [DONE]\n\n"
                    return
                doc_ids.append(doc_id)
        elif existing_conv:
            # No explicit attachments; try to carry over from most recent user message
            prior_user_msg = None
            for msg in reversed(existing_conv.get("messages", [])):
                if msg.get("role") == "user":
                    msg_meta = msg.get("meta", {})
                    if msg_meta.get("kind") == "doc_user" and msg_meta.get("attachments"):
                        prior_user_msg = msg
                        break

            if prior_user_msg:
                # Found prior attachments; try to carry them over
                prior_attachments = prior_user_msg.get("meta", {}).get("attachments", [])
                carried_over_docs = []
                for att in prior_attachments:
                    doc_id = att.get("doc_id")
                    if doc_id:
                        doc = hooks.get_doc(doc_id)
                        if doc:  # Only keep if doc still exists
                            doc_ids.append(doc_id)
                            carried_over_docs.append({"doc_id": doc_id, "role": att.get("role", "auto")})

                if doc_ids:
                    carried_over = True
                    final_attachments = carried_over_docs

        # Final validation: must have at least one doc
        if not doc_ids:
            yield f'data: {json.dumps({"error": "Attach at least one document."})}\n\n'
            yield "data: [DONE]\n\n"
            return

        # ── Phase 3: Mark validation as passed (now we'll persist things) ──
        validation_passed = True

        # ── Phase 4: Create/get conversation (only upsert if new) ──
        if is_new_conv:
            # Title for new conversation: message[:60] or first document's filename
            title = message[:60] if message else ""
            if not title and doc_ids:
                # Use first document's filename (not attachments[0].name which doesn't exist)
                first_doc = hooks.get_doc(doc_ids[0])
                if first_doc:
                    title = getattr(first_doc, "filename", "Document")[:60]
            if not title:
                title = "New conversation"

            await db.upsert_conversation({
                "id": conv_id,
                "title": title,
                "model": "",
                "createdAt": now_ms(),
                "updatedAt": now_ms(),
                "pinned": 0,
            })

        # ── Phase 5: Persist user message (BEFORE running agent) ──
        attachments_meta = []
        for doc_id in doc_ids:
            doc = hooks.get_doc(doc_id)
            if doc:
                # Find the role from final_attachments (use "auto" as default)
                role = "auto"
                for att in final_attachments:
                    if att.get("doc_id") == doc_id:
                        role = att.get("role", "auto")
                        break

                attachments_meta.append({
                    "doc_id": doc_id,
                    "name": doc.filename,
                    "kind": _kind_of(doc),
                    "role": role,
                    "size": getattr(doc, "size", 0),
                })

        await db.upsert_message(conv_id, {
            "id": user_message_id,
            "role": "user",
            "content": message,
            "model": "",
            "timestamp": now_ms(),
            "meta": {
                "kind": "doc_user",
                "attachments": attachments_meta,
                "carried_over": carried_over,
            },
        })

        # ── Phase 6: Emit metadata event ──
        yield f'data: {json.dumps({"meta": {"conversation_id": conv_id, "run_id": run_id, "user_message_id": user_message_id, "assistant_message_id": assistant_message_id}})}\n\n'

        # ── Phase 7: Load history (last 6 messages, trimmed) ──
        history = []
        if existing_conv:
            all_msgs = existing_conv.get("messages", [])
            for msg in all_msgs:
                role = msg.get("role")
                if role in ("user", "assistant"):
                    content = (msg.get("content") or "")[:1500]
                    history.append({"role": role, "content": content})
            # Keep only last 6
            history = history[-6:]

        # ── Phase 8: Run agent ──
        agent_req = {
            "message": message,
            "attachments": final_attachments,
            "history": history,
            "model_override": model_override,
        }

        async for event in run_agent(agent_req, hooks):
            collector.feed(event)
            # Forward ALL events as SSE
            yield f'data: {json.dumps(event)}\n\n'

    except asyncio.CancelledError:
        # Cancellation: mark and re-raise
        cancelled = True
        raise
    except GeneratorExit:
        # Generator close (client disconnect): mark and re-raise
        cancelled = True
        raise
    except Exception as e:
        # Unexpected exception: log, feed to collector, and yield error SSE event
        # Do NOT re-raise; swallow the exception so [DONE] can be emitted
        log.exception(f"Agent error in {conv_id}: {e}")
        error_msg = str(e)[:200]

        # Yield error SSE event to client (but not if agent already sent one)
        if not collector.error:
            # No error from agent yet; send our exception error
            yield f'data: {json.dumps({"error": error_msg})}\n\n'
        # If collector.error exists, it was already yielded during agent execution

        # Feed to collector for persistence (always, even before validation)
        if validation_passed:
            collector.feed({"error": error_msg})

    finally:
        # ── Phase 9: Persist assistant message (always, if validation passed) ──
        if validation_passed:
            async def _persist_assistant() -> None:
                try:
                    # Determine content: use collected content if available, else error msg
                    if collector.content:
                        content = collector.content
                    elif collector.error:
                        content = f"⚠ {collector.error}"
                    else:
                        content = ""

                    # Build metadata
                    meta_dict = collector.to_meta(run_id, doc_ids)
                    if cancelled:
                        meta_dict["cancelled"] = True
                    if collector.error:
                        meta_dict["error"] = collector.error

                    # Persist assistant message
                    await db.upsert_message(conv_id, {
                        "id": assistant_message_id,
                        "role": "assistant",
                        "content": content,
                        "thinkingContent": collector.thinking,
                        "model": collector.model or "",
                        "timestamp": now_ms(),
                        "meta": meta_dict,
                    })

                except Exception as e:
                    log.exception(f"Failed to persist assistant message: {e}")

            # Use shield to protect the persistence even if cancellation occurs
            try:
                await asyncio.shield(_persist_assistant())
            except asyncio.CancelledError:
                # Shield should prevent this, but be defensive
                pass

    # ── Emit [DONE] exactly once, only on normal termination paths ──
    # Not reached if CancelledError or GeneratorExit caused a re-raise
    yield "data: [DONE]\n\n"
