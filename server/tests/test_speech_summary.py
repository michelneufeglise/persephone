"""
Tests for speech_summary — the central "what do we say out loud" step.

No real Ollama calls: the LLM (`_ollama_chat`) and the injected hooks are
replaced with fakes. Does NOT import main.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import speech_summary as sp


LONG_MD = """<think>The user wants a comparison. Let me plan the answer.</think>
# Choosing a local model

Running models locally keeps your data on your machine and avoids per-token costs.
The trade-off is that you need enough memory, and bigger models are slower.

## Options

- **Small models** (1–3B) are fast and good for classification and short replies.
- **Medium models** (7–14B) are a good balance for everyday chat and coding help.
- **Large models** (30B+) give the best quality but need a lot of unified memory.

| Size | Speed | Quality |
|------|:-----:|--------:|
| 1.5B | fast  | ok      |
| 7B   | good  | good    |

```bash
ollama pull qwen2.5:7b
```

See https://ollama.com/library for the full list [1]. ✅🚀
"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    sp.clear_cache()

    async def get_config(key):
        return {"judge_model": "qwen2.5:1.5b"}.get(key)

    async def installed():
        return {"qwen2.5:1.5b", "qwen2.5:7b", "qwen2.5:0.5b"}

    sp.install_hooks(
        get_config=get_config,
        installed_models=installed,
        ollama_base="http://127.0.0.1:11434",
        laya_id="laya-builtin",
    )

    async def boom(*a, **k):  # any unexpected LLM call fails the test loudly
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(sp, "_ollama_chat", boom)
    yield
    sp.clear_cache()


# ── cleaning ──────────────────────────────────────────────────────────────────

def test_clean_strips_markdown_links_urls_emoji_citations_and_think():
    out = sp.clean_for_speech(
        "<think>secret plan</think>Here is **bold**, *italic* and `code`, a "
        "[link](https://example.com/x) and https://example.com/raw?x=1 plus "
        "a citation [2] and [1, 3] and 【4†source】 ✅🚀"
    )
    assert "secret plan" not in out
    for token in ("**", "`", "http", "example.com", "[", "]", "【", "✅", "🚀", "*"):
        assert token not in out, token
    assert "bold" in out and "italic" in out and "code" in out and "link" in out


def test_clean_keeps_snake_case_and_handles_headings_and_lists():
    blocks = sp.clean_blocks("# Title\n\n- first item\n- second\n\nuse my_var_name here")
    assert blocks[0] == "Title."
    assert "first item. second." in blocks[1]
    assert "my_var_name" in blocks[-1]


def test_clean_replaces_code_blocks_and_tables():
    out = sp.clean_for_speech(LONG_MD)
    assert "ollama pull" not in out
    assert sp.CODE_SENTENCE in out
    assert sp.TABLE_SENTENCE in out
    assert "|" not in out and "---" not in out
    assert "Let me plan" not in out


def test_unclosed_think_and_code_fence_are_dropped():
    out = sp.clean_for_speech("Answer first.\n\n```py\nx = 1\n")
    assert "x = 1" not in out and "Answer first." in out
    assert sp.clean_for_speech("<think>still thinking ...") == ""


# ── passthrough / full ────────────────────────────────────────────────────────

def test_short_text_passthrough_without_llm():
    res = run(sp.speech_text("**Paris** is the capital of France. 🇫🇷", message_id="m1"))
    assert res["mode"] == "passthrough"
    assert res["speech"] == "Paris is the capital of France."
    assert res["model"] is None
    assert res["cached"] is False


def test_full_mode_returns_cleaned_full_text_without_llm():
    res = run(sp.speech_text(LONG_MD, mode="full"))
    assert res["mode"] == "passthrough"
    assert "Medium models" in res["speech"]
    assert "https" not in res["speech"] and "**" not in res["speech"]


# ── summary path ──────────────────────────────────────────────────────────────

def test_summary_uses_llm_and_cleans_output(monkeypatch):
    calls = []

    async def fake_chat(model, messages, timeout):
        calls.append((model, messages, timeout))
        return ('"Running models locally keeps your data private. **Small** models are fast, '
                'medium ones are a good balance, and the table on screen compares them."')

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)
    res = run(sp.speech_text(LONG_MD, message_id="m2"))
    assert res["mode"] == "summary"
    assert res["model"] == "qwen2.5:1.5b"
    assert "**" not in res["speech"] and not res["speech"].startswith('"')
    assert res["speech"].startswith("Running models locally")
    assert len(calls) == 1
    model, messages, timeout = calls[0]
    assert timeout == sp.LLM_TIMEOUT_S
    # thinking and code bodies never reach the summariser
    user = messages[-1]["content"]
    assert "Let me plan" not in user and "ollama pull" not in user
    assert "Medium models" in user


def test_prompt_demands_same_language_and_passes_hint(monkeypatch):
    seen = {}

    async def fake_chat(model, messages, timeout):
        seen["system"] = messages[0]["content"]
        return "Lokale modellen houden je gegevens privé. Kleine modellen zijn snel en grote zijn beter."

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)
    nl = ("Lokale modellen draaien op je eigen computer. " * 12).strip()
    res = run(sp.speech_text(nl, lang="nl"))
    assert res["mode"] == "summary"
    assert "SAME LANGUAGE" in seen["system"]
    assert "Dutch" in seen["system"]
    assert res["speech"].startswith("Lokale modellen")


def test_summary_is_capped(monkeypatch):
    async def fake_chat(model, messages, timeout):
        return " ".join(["This is a rather long spoken sentence that goes on."] * 30)

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)
    res = run(sp.speech_text(LONG_MD))
    assert res["mode"] == "summary"
    assert sp.word_count(res["speech"]) <= sp.SUMMARY_HARD_CAP_WORDS


# ── fallbacks ─────────────────────────────────────────────────────────────────

def test_timeout_falls_back_to_first_paragraph(monkeypatch):
    monkeypatch.setattr(sp, "LLM_TIMEOUT_S", 0.05)

    async def slow_chat(model, messages, timeout):
        await asyncio.sleep(2)
        return "never"

    monkeypatch.setattr(sp, "_ollama_chat", slow_chat)
    res = run(sp.speech_text(LONG_MD))
    assert res["mode"] == "fallback"
    assert res["speech"].startswith("Choosing a local model. Running models locally")
    assert sp.word_count(res["speech"]) <= sp.SUMMARY_MAX_WORDS
    assert "Small models" not in res["speech"]


def test_llm_error_and_empty_output_fall_back(monkeypatch):
    async def err_chat(model, messages, timeout):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(sp, "_ollama_chat", err_chat)
    assert run(sp.speech_text(LONG_MD))["mode"] == "fallback"

    async def empty_chat(model, messages, timeout):
        return "<think>hm</think>  "

    monkeypatch.setattr(sp, "_ollama_chat", empty_chat)
    res = run(sp.speech_text(LONG_MD + " extra"))
    assert res["mode"] == "fallback" and res["speech"]


def test_fallback_caps_single_long_paragraph():
    para = " ".join(["word"] * 150) + "."
    out = sp.fallback_speech(para)
    assert sp.word_count(out) <= sp.SUMMARY_MAX_WORDS
    assert out.endswith("…")


def test_laya_or_empty_judge_uses_small_installed_fallback(monkeypatch):
    seen = []

    async def fake_chat(model, messages, timeout):
        seen.append(model)
        return "Local models keep data private. Pick a size that fits your memory."

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)

    async def installed():
        return {"qwen2.5:7b", "qwen2.5:0.5b", "llama3.2:3b-instruct-q4_K_M"}

    for judge in ("laya-builtin", "", "not-installed:1b"):
        sp.clear_cache()

        async def get_config(key, _j=judge):
            return _j

        sp.install_hooks(get_config=get_config, installed_models=installed,
                         ollama_base="http://127.0.0.1:11434", laya_id="laya-builtin")
        res = run(sp.speech_text(LONG_MD))
        assert res["mode"] == "summary"
    # prefix match on the fallback list, and never the 7B chat model
    assert seen == ["llama3.2:3b-instruct-q4_K_M"] * 3


def test_no_small_model_installed_falls_back_without_llm():
    async def get_config(key):
        return "laya-builtin"

    async def installed():
        return {"qwen2.5:32b"}

    sp.install_hooks(get_config=get_config, installed_models=installed,
                     ollama_base="http://127.0.0.1:11434")
    res = run(sp.speech_text(LONG_MD))
    assert res["mode"] == "fallback" and res["model"] is None


def test_ollama_unreachable_falls_back():
    async def installed():
        return set()

    sp.install_hooks(get_config=lambda k: asyncio.sleep(0, "qwen2.5:1.5b"),
                     installed_models=installed, ollama_base="http://127.0.0.1:11434")
    assert run(sp.speech_text(LONG_MD))["mode"] == "fallback"


# ── cache ─────────────────────────────────────────────────────────────────────

def test_cache_hit_skips_llm(monkeypatch):
    n = {"calls": 0}

    async def fake_chat(model, messages, timeout):
        n["calls"] += 1
        return "Local models keep your data private. Choose a size that fits your memory."

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)
    first = run(sp.speech_text(LONG_MD, message_id="abc"))
    second = run(sp.speech_text(LONG_MD, message_id="abc"))
    assert n["calls"] == 1
    assert second["cached"] is True and second["speech"] == first["speech"]
    assert second["mode"] == "summary"
    # different mode is a different cache entry (and needs no LLM)
    full = run(sp.speech_text(LONG_MD, message_id="abc", mode="full"))
    assert full["mode"] == "passthrough" and n["calls"] == 1


def test_fallback_is_not_cached(monkeypatch):
    async def err_chat(model, messages, timeout):
        raise RuntimeError("down")

    monkeypatch.setattr(sp, "_ollama_chat", err_chat)
    assert run(sp.speech_text(LONG_MD))["mode"] == "fallback"

    async def ok_chat(model, messages, timeout):
        return "Now the model is back. Here is the spoken summary."

    monkeypatch.setattr(sp, "_ollama_chat", ok_chat)
    assert run(sp.speech_text(LONG_MD))["mode"] == "summary"


def test_concurrent_requests_share_one_llm_call(monkeypatch):
    n = {"calls": 0}

    async def fake_chat(model, messages, timeout):
        n["calls"] += 1
        await asyncio.sleep(0.05)
        return "One call serves both requests. That keeps the GPU calm."

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)

    async def both():
        return await asyncio.gather(sp.speech_text(LONG_MD), sp.speech_text(LONG_MD))

    a, b = run(both())
    assert n["calls"] == 1
    assert a["speech"] == b["speech"]


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(sp, "CACHE_SIZE", 3)
    for i in range(6):
        run(sp.speech_text(f"Short answer number {i}."))
    assert len(sp._cache) == 3


def test_language_detected_when_not_given(monkeypatch):
    seen = {}

    async def fake_chat(model, messages, timeout):
        seen["messages"] = messages
        return "Bereid je goed voor. Onderzoek het bedrijf en oefen je antwoorden hardop."

    monkeypatch.setattr(sp, "_ollama_chat", fake_chat)
    nl = ("Een goede voorbereiding op een sollicitatiegesprek maakt echt verschil. Onderzoek het "
          "bedrijf, lees de vacature en bereid voorbeelden voor. Oefen de vragen hardop en kom op "
          "tijd, want het gesprek is ook voor jou een kans om het bedrijf te leren kennen.")
    assert sp.detect_language(nl) == "nl"
    assert sp.detect_language(LONG_MD) == "en"
    run(sp.speech_text(nl))
    msgs = seen["messages"]
    assert "Dutch" in msgs[0]["content"]
    # no English demo pair for a non-English answer; final turn names the language
    assert len(msgs) == 2 and msgs[-1]["content"].endswith("in Dutch:")
