"""chat_tools_fn must always send an explicit, bounded num_ctx and a short
keep_alive — otherwise Ollama uses the model's trained default context
(e.g. 256k for qwen3-instruct-2507) and allocates a giant KV cache (Metal OOM)."""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from doc_agent_hooks import (
    build_hooks, HookDeps, tool_num_ctx, TOOL_NUM_CTX_MIN, TOOL_NUM_CTX_MAX,
)


def _deps() -> HookDeps:
    async def _anone(*a, **k):
        return None
    return HookDeps(
        get_doc=lambda _id: None,
        resolve_doc_model=_anone,
        resolve_doc_model_for=_anone,
        get_config=_anone,
        installed_models=_anone,
        name_is_vision=lambda n: False,
        ollama_tags=_anone,
        run_ocr=_anone,
        stream_text=MagicMock(),
        vision_call=_anone,
        supports_thinking=lambda m: False,
        tmp_dir=lambda: __import__("pathlib").Path("/tmp"),
        model_capabilities=_anone,
        ollama_base="http://127.0.0.1:11434",
    )


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {
            "content": "ok",
            "tool_calls": [{"function": {"name": "web_search",
                                         "arguments": '{"query": "x"}'}}],
        }}


def _capture(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    async def fake_post(self, url, *args, **kwargs):
        captured.append({"url": url, "json": kwargs.get("json")})
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


async def _call(model, messages, tools):
    hooks = await build_hooks(_deps())
    return await hooks.chat_tools(model, messages, tools)


TOOLS = [{"type": "function", "function": {
    "name": "web_search", "description": "Search the web",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}}]


def test_chat_tools_payload_small(monkeypatch):
    captured = _capture(monkeypatch)
    out = asyncio.run(_call(
        "qwen3:4b-instruct-2507-q4_K_M",
        [{"role": "user", "content": "who owns acme?"}], TOOLS))
    assert out["tool_calls"][0]["arguments"] == {"query": "x"}
    assert len(captured) == 1
    payload = captured[0]["json"]
    assert captured[0]["url"].endswith("/api/chat")
    assert payload["keep_alive"] == "2m"
    assert payload["options"]["temperature"] == 0.2
    assert payload["options"]["num_ctx"] == 8192
    assert payload["tools"] == TOOLS


def test_chat_tools_payload_huge_is_capped(monkeypatch):
    captured = _capture(monkeypatch)
    big = "x" * 500_000
    asyncio.run(_call(
        "qwen3:4b-instruct-2507-q4_K_M",
        [{"role": "system", "content": big}, {"role": "user", "content": "q"}],
        TOOLS))
    payload = captured[0]["json"]
    assert payload["options"]["num_ctx"] == 16384
    assert payload["keep_alive"] == "2m"


def test_chat_tools_payload_medium(monkeypatch):
    captured = _capture(monkeypatch)
    mid = "y" * 25_000  # ~8.3k tokens + 1024 -> next pow2 is 16384
    asyncio.run(_call(
        "m", [{"role": "user", "content": mid}], []))
    payload = captured[0]["json"]
    assert TOOL_NUM_CTX_MIN <= payload["options"]["num_ctx"] <= TOOL_NUM_CTX_MAX
    assert payload["options"]["num_ctx"] == 16384
    assert "tools" not in payload


@pytest.mark.parametrize("chars", [0, 10, 20_000, 30_000, 10_000_000])
def test_tool_num_ctx_bounds_and_pow2(chars):
    n = tool_num_ctx([{"role": "user", "content": "z" * chars}], None)
    assert TOOL_NUM_CTX_MIN <= n <= TOOL_NUM_CTX_MAX
    assert n & (n - 1) == 0
