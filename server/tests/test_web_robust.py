"""
Web lookup robustness against DuckDuckGo bot-blocking (live demo hardening):

 1  early stop — once a matching profile is found for every requested platform,
    no further searches are issued (tool path + fallback path)
 2  process-wide pacing (>= 3 s between DuckDuckGo queries) and a cool-down
    after a block during which DuckDuckGo is not called at all
 3  verified-before fallback — when every live search is blocked/skipped, an
    earlier likely_profile from the knowledge store is shown (clearly marked);
    never when live search ran and found nothing
 4  kg_store.known_profiles returns stored profiles with verified_at

No network, no sleeping: clocks and sleeps are injected; DB isolated by conftest.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import doc_web as _web
import kg_store as _kg


DDG_NOTICE = (
    "No results were found for your search query. This could be due to DuckDuckGo's bot "
    "detection or the query returned no matches. Please try rephrasing your search or try again "
    "in a few minutes."
)
DDG_RESULTS = (
    "1. Michel Neuféglise - Solution Architect - Rabobank | LinkedIn\n"
    "   URL: https://nl.linkedin.com/in/michelneufeglise\n"
    "   Summary: Solution Architect at Rabobank\n"
)
PROFILE_URL = "https://nl.linkedin.com/in/michelneufeglise"
PROFILE_RESULT = {
    "url": PROFILE_URL,
    "title": "Michel Neuféglise - Solution Architect - Rabobank | LinkedIn",
    "snippet": "Solution Architect at Rabobank",
}
PERSON = {"name": "Michel Neuféglise", "role": "Solution Architect", "org": "Rabobank"}
VERDICT = (
    f"**LinkedIn:** Likely match found — same role and employer [Michel Neuféglise]({PROFILE_URL})\n"
    "**Overall:** Likely match found.\n_Search results are not proof of identity._"
)
STORED = {
    "url": PROFILE_URL,
    "title": "Michel Neuféglise - Solution Architect - Rabobank | LinkedIn",
    "platform": "linkedin",
    "relation": "likely_profile",
    "confidence": 0.8,
    "verified_at": "2026-09-28",
}


# ── helpers ────────────────────────────────────────────────────────────────

def _collect(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


def _content(events):
    return "".join(e["content"] for e in events if "content" in e)


def _search_tile(events):
    return [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == "web-search"][-1]


def _labels(tile):
    return [i.get("label", "") for i in tile["items"]]


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class FakeDDG:
    """Fake MCP search servers; records (server, query, clock time) per call."""

    def __init__(self, clock: FakeClock, replies: dict, running=(_web.DDG_SERVER,)):
        self.clock = clock
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls: list[tuple] = []
        self.running = list(running)

    async def call(self, sid, query):
        self.calls.append((sid, query, self.clock.t))
        seq = self.replies[sid]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def ddg_calls(self):
        return [c for c in self.calls if c[0] == _web.DDG_SERVER]

    def searcher(self, governor):
        import research
        return _web.WebSearcher(
            call=self.call, running=lambda: self.running,
            parse=research._parse_search_results, sleep=self.clock.sleep, governor=governor,
        )


def _governor(clock: FakeClock, **kw):
    return _web.SearchGovernor(clock=clock, sleep=clock.sleep, **kw)


def _hooks(web_search, *, tool_model=None, chat_replies=None, verdict=VERDICT,
           known=None, search_notes=None):
    replies = list(chat_replies or [])
    rec = SimpleNamespace(chat_messages=[], kg_calls=[])

    async def pick_tool_model():
        return {"model": tool_model, "source": "auto"} if tool_model else None

    async def chat_tools(model, messages, tools):
        rec.chat_messages.append([dict(m) for m in messages])
        return replies.pop(0) if replies else {"content": "", "tool_calls": []}

    async def stream_llm(model, prompt, think=False, **kw):
        yield {"content": verdict}
        yield {"done": True}

    async def fetch_page(url):
        return ""

    kg_known_profiles = None
    if known is not None:
        async def kg_known_profiles(name):
            rec.kg_calls.append(name)
            return list(known)

    hooks = SimpleNamespace(
        pick_tool_model=pick_tool_model, chat_tools=chat_tools, web_search=web_search,
        fetch_page=fetch_page, stream_llm=stream_llm, kg_known_profiles=kg_known_profiles,
        search_notes=search_notes,
    )
    hooks.rec = rec
    return hooks


async def _blocked(query):
    raise _web.SearchBlocked(DDG_NOTICE)


# ── 1. early stop ──────────────────────────────────────────────────────────

def test_early_stop_fallback_path_one_ddg_call():
    clock = FakeClock()
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_RESULTS]})
    searcher = ddg.searcher(_governor(clock))
    hooks = _hooks(searcher, search_notes=searcher.drain_notes)
    # Two LinkedIn queries are planned (site: + role); the first already matches
    assert len(_web.build_queries(PERSON, ["linkedin"])) == 2
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    assert len(ddg.ddg_calls()) == 1
    tile = _search_tile(events)
    assert tile["status"] == "done"
    assert _web.EARLY_STOP_NOTE in _labels(tile)
    assert any(i.get("detail") == "Skipped (profile already found)" for i in tile["items"])
    assert "Likely match found" in _content(events)
    assert tile["detail"].startswith("1 search(es)")


def test_early_stop_tool_path_further_searches_answered_without_ddg():
    clock = FakeClock()
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_RESULTS]})
    searcher = ddg.searcher(_governor(clock))
    calls = [
        {"name": "web_search", "arguments": {"query": 'site:linkedin.com "Michel Neuféglise"'}},
        {"name": "web_search", "arguments": {"query": 'site:linkedin.com/in "Michel Neuféglise" Rabobank'}},
        {"name": "web_search", "arguments": {"query": '"Michel Neuféglise" Solution Architect LinkedIn'}},
    ]
    replies = [
        {"content": "", "tool_calls": calls},
        {"content": "", "tool_calls": [calls[2]]},
        {"content": "done", "tool_calls": []},
    ]
    hooks = _hooks(searcher, tool_model="qwen3:4b", chat_replies=replies, search_notes=searcher.drain_notes)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    assert len(ddg.ddg_calls()) == 1
    last_msgs = hooks.rec.chat_messages[-1]
    tool_msgs = [m["content"] for m in last_msgs if m["role"] == "tool"]
    assert tool_msgs.count(_web.ENOUGH_RESULTS_MSG) == 3
    tile = _search_tile(events)
    assert _labels(tile).count(_web.EARLY_STOP_NOTE) == 1
    assert tile["status"] == "done"
    assert "Likely match found" in _content(events)


def test_no_early_stop_without_matching_profile():
    n = {"calls": 0}

    async def web_search(q):
        n["calls"] += 1
        return [{"url": "https://example.com/about", "title": "Michel Neuféglise", "snippet": "x"}]

    hooks = _hooks(web_search)
    _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    assert n["calls"] == 2  # a web page isn't a profile — keep searching


def test_early_stop_waits_for_every_platform():
    seen = []

    async def web_search(q):
        seen.append(q)
        if "linkedin" in q.lower():
            return [PROFILE_RESULT]
        return []

    hooks = _hooks(web_search)
    _collect(_web.run_web_lookup(PERSON, ["linkedin", "facebook"], hooks, answer_model="m", now_ms=0))
    assert sum("linkedin" in q.lower() for q in seen) == 1
    assert sum("facebook" in q.lower() for q in seen) == 2


# ── 2. pacing + cool-down ──────────────────────────────────────────────────

def test_pacing_enforces_min_interval():
    clock = FakeClock()
    gov = _governor(clock)
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_RESULTS]})
    s = ddg.searcher(gov)

    async def go():
        await s("q1")
        await s("q2")
        clock.t += 1.0  # 1 s of other work between queries
        await s("q3")
    asyncio.run(go())
    times = [c[2] for c in ddg.ddg_calls()]
    assert len(times) == 3
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:]))
    assert clock.sleeps == pytest.approx([3.0, 2.0])
    notes = s.drain_notes()
    assert notes and notes[0]["kind"] == "paced" and notes[0]["seconds"] == pytest.approx(5.0)
    assert s.drain_notes() == []


def test_pacing_is_process_wide_across_searchers():
    clock = FakeClock()
    gov = _governor(clock)
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_RESULTS]})

    async def go():
        await ddg.searcher(gov)("q1")
        await ddg.searcher(gov)("q2")  # a new run right after
    asyncio.run(go())
    t1, t2 = [c[2] for c in ddg.ddg_calls()]
    assert t2 - t1 >= 3.0


def test_pacing_tile_note():
    clock = FakeClock()
    ddg = FakeDDG(clock, {_web.DDG_SERVER: ["1. Other person\n   URL: https://example.com/x\n   Summary: nothing\n"]})
    searcher = ddg.searcher(_governor(clock))
    hooks = _hooks(searcher, search_notes=searcher.drain_notes)
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    assert len(ddg.ddg_calls()) == 2
    pacing = [l for l in _labels(_search_tile(events)) if l.startswith(_web.PACING_NOTE_PREFIX)]
    assert len(pacing) == 1 and "waited 3.0 s" in pacing[0]


def test_cooldown_skips_ddg_then_resumes():
    clock = FakeClock()
    gov = _governor(clock, cooldown=90.0)
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_NOTICE, DDG_NOTICE, DDG_RESULTS]})

    # Run 1: blocked, the single retry is also blocked → cool-down starts
    s1 = ddg.searcher(gov)
    with pytest.raises(_web.SearchBlocked) as ei:
        asyncio.run(s1("q1"))
    assert not isinstance(ei.value, _web.SearchSkipped)
    assert len(ddg.ddg_calls()) == 2
    assert gov.cooldown_remaining() > 0

    # Same run and a new run inside the window: DuckDuckGo is not called at all
    with pytest.raises(_web.SearchSkipped):
        asyncio.run(s1("q2"))
    clock.t += 30
    s2 = ddg.searcher(gov)
    with pytest.raises(_web.SearchSkipped) as ei:
        asyncio.run(s2("q3"))
    assert 0 < ei.value.remaining <= 60.5
    assert len(ddg.ddg_calls()) == 2

    # After the window: DuckDuckGo is used again
    clock.t += 61
    s3 = ddg.searcher(gov)
    res = asyncio.run(s3("q4"))
    assert res and res[0]["url"] == PROFILE_URL
    assert len(ddg.ddg_calls()) == 3


def test_cooldown_goes_straight_to_brave_when_running():
    clock = FakeClock()
    gov = _governor(clock)
    gov.note_blocked()
    ddg = FakeDDG(
        clock, {_web.DDG_SERVER: [DDG_RESULTS], _web.BRAVE_SERVER: [DDG_RESULTS]},
        running=(_web.DDG_SERVER, _web.BRAVE_SERVER),
    )
    res = asyncio.run(ddg.searcher(gov)("q"))
    assert res and res[0]["url"] == PROFILE_URL
    assert [c[0] for c in ddg.calls] == [_web.BRAVE_SERVER]


def test_no_retry_when_cooldown_started_meanwhile():
    clock = FakeClock()
    gov = _governor(clock)
    ddg = FakeDDG(clock, {_web.DDG_SERVER: [DDG_NOTICE]})
    orig = ddg.call

    async def call(sid, query):
        text = await orig(sid, query)
        gov.note_blocked()  # another run tripped the cool-down while this query ran
        return text
    ddg.call = call
    with pytest.raises(_web.SearchBlocked):
        asyncio.run(ddg.searcher(gov)("q"))
    assert len(ddg.ddg_calls()) == 1  # no retry during the cool-down


def test_cooldown_tile_note_and_unavailable_without_cache():
    async def skipped(q):
        raise _web.SearchSkipped(64.2)

    hooks = _hooks(skipped, known=[])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    tile = _search_tile(events)
    labels = _labels(tile)
    assert "Cool-down after DuckDuckGo block: 64 s left — skipped live search" in labels
    assert labels.count("Cool-down after DuckDuckGo block: 64 s left — skipped live search") == 1
    assert any("skipped (DuckDuckGo cool-down" in (i.get("detail") or "") for i in tile["items"])
    assert "**LinkedIn:** Search unavailable — couldn't verify" in _content(events)
    assert tile["status"] == "error"


# ── 3. verified-before fallback ────────────────────────────────────────────

def _assert_cached(events, hooks):
    text = _content(events)
    assert "previously verified on 28 Sep 2026" in text
    assert f"({PROFILE_URL})" in text
    assert "from the knowledge store" in text
    assert "Search unavailable" not in text
    assert "No match found" not in text
    tile = _search_tile(events)
    assert tile["status"] == "done"
    assert tile["detail"] == "Live search blocked — used earlier verification"
    labels = _labels(tile)
    assert "Used knowledge-store verification from 28 Sep 2026" in labels
    res = [i for i in tile["items"] if i["kind"] == "result" and i.get("url") == PROFILE_URL]
    assert res and res[0]["platform"] == "linkedin"
    web_result = [e["_web_result"] for e in events if "_web_result" in e][0]
    assert web_result["candidates"] == []  # cached data is never re-ingested as live
    assert hooks.rec.kg_calls == ["Michel Neuféglise"]


def test_cached_fallback_when_all_blocked_fallback_path():
    hooks = _hooks(_blocked, known=[STORED])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    _assert_cached(events, hooks)


def test_cached_fallback_when_all_blocked_tool_path():
    replies = [
        {"content": "", "tool_calls": [{"name": "web_search", "arguments": {"query": 'site:linkedin.com "Michel Neuféglise"'}}]},
        {"content": "done", "tool_calls": []},
    ]
    hooks = _hooks(_blocked, tool_model="qwen3:4b", chat_replies=replies, known=[STORED])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    _assert_cached(events, hooks)


def test_cached_fallback_during_cooldown():
    async def skipped(q):
        raise _web.SearchSkipped(50)

    hooks = _hooks(skipped, known=[STORED])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    _assert_cached(events, hooks)


def test_cached_fallback_mixed_platforms():
    hooks = _hooks(_blocked, known=[STORED])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin", "facebook"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "**LinkedIn:** Live search is blocked right now — previously verified on 28 Sep 2026" in text
    assert "**Facebook:** Search unavailable — couldn't verify" in text


def test_cached_candidate_is_possible_match_not_verified():
    cand = {**STORED, "relation": "candidate_profile", "confidence": 0.5}
    hooks = _hooks(_blocked, known=[cand])
    text = _content(_collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0)))
    assert "previously found on 28 Sep 2026" in text and "possible match" in text
    assert "previously verified" not in text


def test_no_cache_when_live_search_ran_with_zero_results():
    async def empty(q):
        return []

    hooks = _hooks(empty, known=[STORED])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "No match found" in text
    assert "previously verified" not in text
    assert hooks.rec.kg_calls == []


def test_no_cache_when_some_search_ran():
    n = {"i": 0}

    async def partly(q):
        n["i"] += 1
        if n["i"] == 1:
            raise _web.SearchBlocked(DDG_NOTICE)
        return []

    hooks = _hooks(partly, known=[STORED])
    text = _content(_collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0)))
    assert "previously verified" not in text
    assert hooks.rec.kg_calls == []


def test_search_unavailable_when_nothing_cached():
    hooks = _hooks(_blocked, known=[])
    events = _collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0))
    text = _content(events)
    assert "**LinkedIn:** Search unavailable — couldn't verify" in text
    assert "previously verified" not in text
    assert _search_tile(events)["status"] == "error"


def test_cached_profile_on_other_platform_not_used():
    fb = {**STORED, "url": "https://www.facebook.com/michel.neufeglise", "platform": "facebook"}
    hooks = _hooks(_blocked, known=[fb])
    text = _content(_collect(_web.run_web_lookup(PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0)))
    assert "**LinkedIn:** Search unavailable — couldn't verify" in text


# ── 4. real kg_store helper ────────────────────────────────────────────────

def test_kg_known_profiles_returns_stored_profile_with_verified_at():
    async def go():
        await _kg.init_db()
        await _kg.reset()
        try:
            pid = await _kg.upsert_entity("person", "Michel Neuféglise")
            prof_id, _ = _kg._upsert_profile_sync(PROFILE_URL, STORED["title"], {
                "url": PROFILE_URL, "platform": "linkedin", "host": "nl.linkedin.com",
            })
            other_id, _ = _kg._upsert_profile_sync("https://example.com/michel", "Michel page", {
                "url": "https://example.com/michel", "platform": "web",
            })
            _kg._add_relation_ex_sync(pid, prof_id, "likely_profile", confidence=0.8, source="web_lookup")
            _kg._add_relation_ex_sync(pid, other_id, "candidate_profile", confidence=0.5, source="web_lookup")
            # Pin the likely relation's timestamp to a known local date
            ts = dt.datetime(2026, 9, 28, 12, 0).timestamp()
            conn = _kg._connect()
            try:
                conn.execute(
                    "UPDATE kg_relations SET created_at=? WHERE src_id=? AND dst_id=?", (ts, pid, prof_id)
                )
            finally:
                conn.close()

            rows = await _kg.known_profiles("Michel Neufeglise")  # accent-insensitive
            assert [r["relation"] for r in rows] == ["likely_profile", "candidate_profile"]
            top = rows[0]
            assert top["url"] == PROFILE_URL
            assert top["platform"] == "linkedin"
            assert top["title"] == STORED["title"]
            assert top["confidence"] == pytest.approx(0.8)
            assert top["verified_at"] == "2026-09-28"
            assert rows[1]["verified_at"] == dt.date.today().isoformat()
            assert await _kg.known_profiles("Someone Else") == []

            # End to end: blocked lookup uses the real store
            hooks = _hooks(_blocked)
            hooks.kg_known_profiles = _kg.known_profiles
            events = [ev async for ev in _web.run_web_lookup(
                PERSON, ["linkedin"], hooks, answer_model="m", now_ms=0)]
            text = _content(events)
            assert "previously verified on 28 Sep 2026" in text
            assert _search_tile(events)["status"] == "done"
        finally:
            await _kg.reset()

    asyncio.run(go())
