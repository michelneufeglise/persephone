"""
speech_summary.py — turn an assistant answer into text worth SAYING.

Every place the app speaks an assistant response (main chat auto-speak, the
per-message "Read aloud" button, the Documents agent) goes through
POST /api/tts/speech-text, which calls `speech_text()` here.

Pipeline
--------
1. Always clean for speech: drop <think> content, fenced code (→ a short
   "code block on screen" sentence), markdown tables (→ "table on screen"),
   URLs, emoji, citation markers, markdown syntax and HTML tags.
2. mode="full"     → the cleaned full text ("passthrough").
   mode="summary"  → short answers (< PASSTHROUGH_MAX_WORDS words after
                     cleaning) pass through unchanged; longer ones get a
                     2–3 sentence spoken summary from a small, fast local
                     model (the configured judge model). Any failure or a
                     timeout falls back to the cleaned first paragraph,
                     capped at SUMMARY_MAX_WORDS words.
3. Results are cached (in-memory LRU keyed by mode + language + text hash),
   and concurrent requests for the same text share one in-flight job, so a
   replay of the same message is instant.

main.py wires this module with `install_hooks()` (config getter, installed
model lister, Ollama base URL, Laya sentinel id) instead of this module
importing main — the same injected-hooks idiom used by delegate / planner.
Everything stays on the local Ollama instance.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

import httpx

log = logging.getLogger("persephone.speech")

# ── Tunables ──────────────────────────────────────────────────────────────────
PASSTHROUGH_MAX_WORDS = 40      # below this (after cleaning) → speak as-is
SUMMARY_MAX_WORDS = 60          # target length of the spoken summary / fallback
SUMMARY_HARD_CAP_WORDS = 70     # never speak more than this from the LLM
LLM_TIMEOUT_S = 8.0             # whole LLM round-trip budget → fallback after
NUM_CTX = 4096                  # context cap for the summariser (source capped below)
NUM_PREDICT = 120               # ~60 words + slack (hard length bound)
KEEP_ALIVE = "2m"               # small model; don't pin it in memory
MAX_SOURCE_CHARS = 9000         # ~2.3k tokens: fits NUM_CTX with prompt + output
CACHE_SIZE = 256

# Small instruct models to fall back on when the judge model is unset, is the
# Laya built-in (not a generative model) or isn't installed. Never a big model.
FALLBACK_MODELS = [
    "qwen2.5:1.5b", "llama3.2:3b", "qwen2.5:3b", "llama3.2:1b",
    "gemma3:1b", "qwen2.5:0.5b",
]

# ── Injected hooks (see install_hooks) ────────────────────────────────────────
_get_config: Optional[Callable[[str], Awaitable[Optional[str]]]] = None
_installed_models: Optional[Callable[[], Awaitable[set[str]]]] = None
_ollama_base: str = "http://127.0.0.1:11434"
_laya_id: str = "laya-builtin"


def install_hooks(
    *,
    get_config: Callable[[str], Awaitable[Optional[str]]],
    installed_models: Callable[[], Awaitable[set[str]]],
    ollama_base: str,
    laya_id: str = "laya-builtin",
) -> None:
    """Wire the module to main.py's config store / Ollama helpers."""
    global _get_config, _installed_models, _ollama_base, _laya_id
    _get_config = get_config
    _installed_models = installed_models
    _ollama_base = (ollama_base or _ollama_base).rstrip("/")
    _laya_id = laya_id or _laya_id


# ── Cleaning ──────────────────────────────────────────────────────────────────
CODE_SENTENCE = "There is a code block on screen."
TABLE_SENTENCE = "There is a table on screen."

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>[\s\S]*$", re.IGNORECASE)
_FENCE_RE = re.compile(r"(^|\n)[ \t]*(```|~~~)[^\n]*\n[\s\S]*?(?:\n[ \t]*\2[ \t]*(?=\n|$)|$)")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_AUTOLINK_RE = re.compile(r"<(?:https?|ftp|mailto):[^>\s]+>", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|ftp://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)
_CITATION_RE = re.compile(
    r"\[\^?\d+(?:\s*[,;–-]\s*\^?\d+)*\]"          # [1] [1, 2] [^3] [2-4]
    r"|【[^】]*】"                                   # 【1†source】
    r"|\[(?:source|src|ref|doc|citation)[\s:#]*\d*[^\]]{0,40}\]",  # [source 3]
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,200}>")
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_TASKBOX_RE = re.compile(r"^\[[ xX]\]\s+")
_QUOTE_RE = re.compile(r"^\s*>+\s?")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # pictographs, emoticons, transport, symbols ext.
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "☀-➿"           # misc symbols + dingbats (✅ ⚠ ✨ ✓ …)
    "⬀-⯿"           # arrows / stars (⭐ ⬆)
    "︎️‍⃣"
    "]+"
)
_END_PUNCT = (".", "!", "?", ":", ";", "…", "。", "！", "？")


def _strip_inline(s: str) -> str:
    s = _IMAGE_RE.sub(r"\1", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _AUTOLINK_RE.sub("", s)
    s = _URL_RE.sub("", s)
    s = _CITATION_RE.sub("", s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    # emphasis / strike — keep the words, lose the markers
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"\1", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"\s*(?:->|=>|→|⇒)\s*", ", ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = _EMOJI_RE.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    # tidy punctuation left behind by removed tokens: " ," → "," and "( )" → ""
    s = re.sub(r"\(\s*[,;:]?\s*\)", "", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,;:])\1+", r"\1", s)
    return s.strip()


def _replace_tables(text: str) -> str:
    """Replace every markdown table (header row + separator row + rows) with a
    single placeholder line."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (
            _TABLE_ROW_RE.match(lines[i])
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1])
        ):
            j = i + 2
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                j += 1
            out.extend(["", TABLE_SENTENCE, ""])
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def strip_thinking(text: str) -> str:
    text = _THINK_RE.sub(" ", text or "")
    return _THINK_OPEN_RE.sub(" ", text)


def _pre_clean(text: str, *, keep_tables: bool = False) -> str:
    """Block-level cleanup shared by the speech cleaner and the LLM input."""
    text = strip_thinking(text).replace("\r\n", "\n")
    text = _FENCE_RE.sub(lambda m: f"{m.group(1)}\n{CODE_SENTENCE}\n\n", text)
    if not keep_tables:
        text = _replace_tables(text)
    return text


def clean_blocks(text: str) -> list[str]:
    """Clean `text` for speech and return it as a list of spoken paragraphs.

    Headings and list items become their own sentences (a terminal period is
    added when missing so the TTS engine pauses naturally)."""
    text = _pre_clean(text)
    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            para = " ".join(current).strip()
            if para:
                paragraphs.append(para)
            current.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line.strip() or _HR_RE.match(line):
            flush()
            continue
        is_heading = bool(_HEADING_RE.match(line))
        is_item = bool(_BULLET_RE.match(line))
        line = _HEADING_RE.sub("", line)
        line = _QUOTE_RE.sub("", line)
        line = _BULLET_RE.sub("", line)
        line = _TASKBOX_RE.sub("", line)
        line = _strip_inline(line)
        # stray table pipes that weren't part of a well-formed table
        line = re.sub(r"\s*\|\s*", ", ", line).strip(" ,")
        line = line.strip()
        if not line or not re.search(r"\w", line):
            continue
        if (is_heading or is_item) and not line.endswith(_END_PUNCT):
            line += "."
        if is_heading:
            flush()
            paragraphs.append(line)
            continue
        current.append(line)
    flush()

    # collapse consecutive duplicate placeholder sentences
    deduped: list[str] = []
    for p in paragraphs:
        if deduped and p == deduped[-1] and p in (CODE_SENTENCE, TABLE_SENTENCE):
            continue
        deduped.append(p)
    return deduped


def clean_for_speech(text: str) -> str:
    """Full cleaned text as a single speakable string."""
    return re.sub(r"\s+", " ", " ".join(clean_blocks(text))).strip()


def word_count(text: str) -> int:
    return len(text.split())


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…。！？])\s+")


def _cap_words(text: str, max_words: int) -> str:
    """Keep whole sentences up to `max_words`; hard-cut a single overlong one."""
    sentences = [s for s in _SENT_SPLIT_RE.split(text.strip()) if s]
    out: list[str] = []
    n = 0
    for s in sentences:
        w = word_count(s)
        if n + w > max_words:
            break
        out.append(s)
        n += w
    if out:
        return " ".join(out)
    words = text.split()
    if not words:
        return ""
    cut = " ".join(words[:max_words]).rstrip(",;:—-")
    return cut if cut.endswith(_END_PUNCT) else cut + "…"


def fallback_speech(text: str, max_words: int = SUMMARY_MAX_WORDS) -> str:
    """Cleaned first paragraph (topped up with following ones when it's just a
    heading), capped at `max_words` words on a sentence boundary."""
    blocks = [b for b in clean_blocks(text) if b not in (CODE_SENTENCE, TABLE_SENTENCE)]
    if not blocks:
        blocks = clean_blocks(text)
    if not blocks:
        return ""
    picked: list[str] = []
    for b in blocks:
        picked.append(b)
        if word_count(" ".join(picked)) >= 12:
            break
    return _cap_words(" ".join(picked), max_words)


# ── Summariser (small local model) ────────────────────────────────────────────
_LANG_NAMES = {
    "en": "English", "nl": "Dutch", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ru": "Russian", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
    "tr": "Turkish", "ar": "Arabic", "hi": "Hindi",
}

SUMMARY_SYSTEM_PROMPT = (
    "You condense a written answer into a very short SPOKEN version for "
    "text-to-speech. You are not replying to the answer: you ARE the assistant "
    "who wrote it, now briefly saying its key point out loud.\n"
    "Rules:\n"
    "- 2 or 3 short sentences, at most {max_words} words in total.\n"
    "- Only the main point(s); skip details, lists, steps and examples.\n"
    "- Write in the SAME LANGUAGE as the written answer{lang_hint}.\n"
    "- Plain spoken sentences only: no markdown, bullets, headings, colons "
    "introducing lists, emoji, URLs, code or symbols.\n"
    "- Write numbers, units and symbols the way they are spoken aloud "
    "(e.g. 'about twenty percent', 'three point five kilometres').\n"
    "- No filler like 'Great question', 'Sure', 'Here is a summary' or "
    "'In summary', and add nothing that is not in the written answer.\n"
    "- If the answer contains code or a table, you may say it is on screen.\n"
    "Output ONLY the spoken sentences."
)

# One tiny worked example — small models follow a demonstration far better
# than rules alone (keeps them from "responding" to the answer).
_EXAMPLE_ANSWER = (
    "## Brewing better coffee\n\n"
    "The biggest improvements come from **fresh beans** and the right ratio.\n\n"
    "- Use about 60 g of coffee per litre of water.\n"
    "- Grind just before brewing.\n"
    "- Water should be 92–96 °C, not boiling.\n\n"
    "| Method | Grind |\n|---|---|\n| Espresso | fine |\n| French press | coarse |"
)
_EXAMPLE_SPOKEN = (
    "The biggest wins are fresh beans and the right ratio, about sixty grams of "
    "coffee per litre. Grind just before brewing and use water just off the boil. "
    "The table on screen shows the grind for each method."
)


# Tiny stop-word language guesser — enough to tell a 1.5B model which
# language to answer in (they drift to English otherwise).
_STOPWORDS: dict[str, set[str]] = {
    "en": {"the", "and", "is", "are", "you", "of", "to", "that", "with", "for", "this", "it"},
    "nl": {"de", "het", "een", "en", "is", "van", "je", "dat", "niet", "met", "voor", "op", "zijn", "ook", "wat"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "mit", "sie", "ein", "eine", "auch", "für", "zu", "ich"},
    "fr": {"le", "la", "les", "et", "est", "des", "une", "pour", "que", "pas", "vous", "avec", "dans", "du"},
    "es": {"el", "la", "los", "las", "y", "es", "que", "para", "una", "con", "por", "del", "no", "se"},
    "it": {"il", "la", "che", "e", "di", "per", "una", "non", "con", "sono", "gli", "del", "anche"},
    "pt": {"o", "a", "os", "que", "e", "para", "uma", "com", "não", "do", "da", "em", "você"},
}


def detect_language(text: str) -> Optional[str]:
    words = re.findall(r"[a-zà-öø-ÿ]+", text.lower())[:600]
    if len(words) < 8:
        return None
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 3:
        return None
    runner_up = max(v for k, v in scores.items() if k != best)
    return best if scores[best] >= 1.3 * max(runner_up, 1) else None


def build_messages(source: str, lang: Optional[str] = None) -> list[dict[str, str]]:
    code = (lang or "").strip().lower().split("-")[0] or (detect_language(source) or "")
    name = _LANG_NAMES.get(code, lang.strip() if lang else "") if code else ""
    lang_hint = f" (the answer is in {name})" if name else ""
    system = SUMMARY_SYSTEM_PROMPT.format(max_words=SUMMARY_MAX_WORDS, lang_hint=lang_hint)

    def wrap(ans: str, lang_name: str = "") -> str:
        tail = f"Spoken version, in {lang_name}:" if lang_name else "Spoken version:"
        return f"Written answer:\n\"\"\"\n{ans}\n\"\"\"\n\n{tail}"

    messages = [{"role": "system", "content": system}]
    # The English demo helps small models with English answers but pulls them
    # into English otherwise — only use it when the answer is (likely) English.
    if code in ("", "en"):
        messages += [
            {"role": "user", "content": wrap(_EXAMPLE_ANSWER)},
            {"role": "assistant", "content": _EXAMPLE_SPOKEN},
        ]
    messages.append({"role": "user", "content": wrap(source, name if code not in ("", "en") else "")})
    return messages


def _llm_source(text: str) -> str:
    """Input for the summariser: thinking + code bodies removed, tables kept
    (the model can summarise them), capped in length."""
    src = _pre_clean(text, keep_tables=True)
    src = re.sub(r"\n{3,}", "\n\n", src).strip()
    if len(src) > MAX_SOURCE_CHARS:
        src = src[:MAX_SOURCE_CHARS].rsplit(" ", 1)[0] + " …"
    return src


def _match_installed(pref: list[str], installed: set[str]) -> Optional[str]:
    for p in pref:                       # preference order wins; exact tag,
        if p in installed:               # then a same-family tag variant
            return p
        for inst in sorted(installed):
            if inst.lower().startswith(p.lower() + ":") or inst.lower().startswith(p.lower() + "-"):
                return inst
    return None


async def pick_model() -> Optional[str]:
    """The configured judge model, unless it's empty / Laya / not installed;
    then the first installed small fallback model; else None."""
    judge = ""
    if _get_config is not None:
        try:
            judge = (await _get_config("judge_model")) or ""
        except Exception:
            judge = ""
    installed: set[str] = set()
    if _installed_models is not None:
        try:
            installed = set(await _installed_models())
        except Exception:
            installed = set()
    if not installed:
        return None
    if judge and judge != _laya_id and judge in installed:
        return judge
    return _match_installed(FALLBACK_MODELS, installed)


async def _ollama_chat(model: str, messages: list[dict[str, str]], timeout: float) -> str:
    """One non-streaming /api/chat call. Separate so tests can replace it."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
        },
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{_ollama_base}/api/chat", json=payload)
        if r.status_code == 400 and "think" in r.text.lower():
            # Older Ollama / model without a thinking toggle — retry without it.
            payload.pop("think", None)
            r = await client.post(f"{_ollama_base}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    return ((data.get("message") or {}).get("content") or "").strip()


_llm_lock: Optional[asyncio.Lock] = None
_llm_lock_loop: Any = None


def _get_lock() -> asyncio.Lock:
    """One summary at a time on the GPU; recreated per event loop (tests)."""
    global _llm_lock, _llm_lock_loop
    loop = asyncio.get_running_loop()
    if _llm_lock is None or _llm_lock_loop is not loop:
        _llm_lock = asyncio.Lock()
        _llm_lock_loop = loop
    return _llm_lock


_FILLER_RE = re.compile(
    r"^(?:(?:great|good|excellent|nice)\s+(?:question|advice|plan|idea)[!.]\s*|sure[!,.]\s*|"
    r"here(?:'s|’s| is) (?:a |the )?(?:short |brief |spoken )?(?:summary|version)[^:.!]*[:.!]\s*)+",
    re.IGNORECASE,
)


def _postprocess_summary(raw: str) -> str:
    raw = strip_thinking(raw).strip().strip('"“”\'').strip()
    raw = re.sub(r"^(?:spoken(?: version)?|summary|what the assistant says[^:]*)\s*:\s*",
                 "", raw, flags=re.IGNORECASE)
    raw = _FILLER_RE.sub("", raw).strip()
    spoken = clean_for_speech(raw)
    return _cap_words(spoken, SUMMARY_HARD_CAP_WORDS) if spoken else ""


# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_inflight: dict[str, "asyncio.Future[dict[str, Any]]"] = {}


def _cache_key(text: str, mode: str, lang: Optional[str]) -> str:
    h = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
    return f"{mode}|{(lang or '').lower()}|{h}"


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
    return hit


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()
    _inflight.clear()


# ── Public entry point ────────────────────────────────────────────────────────
async def speech_text(
    text: str,
    message_id: Optional[str] = None,
    lang: Optional[str] = None,
    mode: str = "summary",
) -> dict[str, Any]:
    """Return {speech, mode, model, ms, cached} for an assistant answer.

    mode in the result: "passthrough" (cleaned text spoken as-is),
    "summary" (LLM spoken summary) or "fallback" (cleaned first paragraph
    because the LLM was unavailable / slow / returned nothing)."""
    t0 = time.perf_counter()
    mode = "full" if (mode or "").lower() in ("full", "full_text", "fulltext") else "summary"
    text = text or ""
    key = _cache_key(text, mode, lang)

    hit = _cache_get(key)
    if hit is not None:
        return {**hit, "cached": True, "ms": int((time.perf_counter() - t0) * 1000)}

    pending = _inflight.get(key)
    if pending is not None and not pending.done():
        try:
            res = await asyncio.shield(pending)
            return {**res, "cached": True, "ms": int((time.perf_counter() - t0) * 1000)}
        except Exception:
            pass

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    _inflight[key] = fut
    try:
        result = await _compute(text, mode, lang, message_id)
        if result["mode"] != "fallback":
            _cache_put(key, result)
        if not fut.done():
            fut.set_result(result)
    except Exception as exc:  # never fail the caller — speak *something*
        log.warning("speech-text failed (%s): %s", message_id or "-", exc)
        result = {"speech": fallback_speech(text), "mode": "fallback", "model": None}
        if not fut.done():
            fut.set_result(result)
    finally:
        if _inflight.get(key) is fut:
            _inflight.pop(key, None)

    ms = int((time.perf_counter() - t0) * 1000)
    log.info("speech-text %s: mode=%s model=%s words=%d ms=%d",
             message_id or "-", result["mode"], result.get("model"),
             word_count(result["speech"]), ms)
    return {**result, "cached": False, "ms": ms}


async def _compute(text: str, mode: str, lang: Optional[str], message_id: Optional[str]) -> dict[str, Any]:
    cleaned = clean_for_speech(text)
    if mode == "full" or word_count(cleaned) < PASSTHROUGH_MAX_WORDS:
        return {"speech": cleaned, "mode": "passthrough", "model": None}

    model = await pick_model()
    if not model:
        return {"speech": fallback_speech(text), "mode": "fallback", "model": None}

    messages = build_messages(_llm_source(text), lang)
    try:
        async def run() -> str:
            async with _get_lock():
                return await _ollama_chat(model, messages, LLM_TIMEOUT_S)
        raw = await asyncio.wait_for(run(), timeout=LLM_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.info("speech summary timed out after %.1fs (%s) — fallback", LLM_TIMEOUT_S, model)
        return {"speech": fallback_speech(text), "mode": "fallback", "model": model}
    except Exception as exc:
        log.info("speech summary failed (%s): %s — fallback", model, exc)
        return {"speech": fallback_speech(text), "mode": "fallback", "model": model}

    spoken = _postprocess_summary(raw)
    if word_count(spoken) < 3:
        return {"speech": fallback_speech(text), "mode": "fallback", "model": model}
    return {"speech": spoken, "mode": "summary", "model": model}
