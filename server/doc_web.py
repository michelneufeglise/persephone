"""
Web lookup extension for the document agent.

Handles detection, planning, and execution of web searches to verify persons
or facts mentioned in documents. Works with Ollama tool-capable models and
direct search fallback.

Never imports main.py; uses injected hooks for all integrations.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, AsyncIterator, Callable, Optional

log = logging.getLogger("doc_web")

# ── Configuration ──────────────────────────────────────────────────────────

# Social platforms the web lookup can target. Each entry:
#   label            — display name ("LinkedIn")
#   domains          — hostnames (subdomains match too); the first is the primary
#   site             — the site: filter used in search queries (defaults to domains[0])
#   query_term       — word appended to the name+role query (defaults to label)
#   profile_patterns — regexes a profile URL must match
#   exclude_patterns — regexes that mark a URL as NOT a profile (posts, directories…)
#   keywords         — request phrases that select this platform (word-bounded)
# End of a standalone "x": not followed by a word char, hyphen, or ".<word>"
_X_END = r"(?![\w\-]|\.\w)"

PLATFORMS: dict[str, dict] = {
    "linkedin": {
        "label": "LinkedIn",
        "domains": ["linkedin.com"],
        "site": "linkedin.com/in",
        "profile_patterns": [r"linkedin\.com/in/"],
        "exclude_patterns": [r"/pub/dir/", r"/search/", r"/posts?/", r"/pulse/"],
        "keywords": ["linkedin", "linked in", "linked-in"],
    },
    "facebook": {
        "label": "Facebook",
        "domains": ["facebook.com", "fb.com"],
        "profile_patterns": [
            r"facebook\.com/(?!public/|pages/category|search|groups/|events/|watch|marketplace)[A-Za-z0-9.\-]+/?$",
            r"facebook\.com/profile\.php\?id=\d+",
        ],
        "exclude_patterns": [r"/public/", r"/posts/", r"/photos/", r"/videos/"],
        "keywords": ["facebook", "fb "],
    },
    "instagram": {
        "label": "Instagram",
        "domains": ["instagram.com"],
        "profile_patterns": [r"instagram\.com/[A-Za-z0-9._]+/?$"],
        "exclude_patterns": [r"/p/", r"/reel/", r"/explore/"],
        "keywords": ["instagram", "insta "],
    },
    "x": {
        "label": "X",
        "domains": ["x.com", "twitter.com"],
        "query_term": "Twitter",
        "profile_patterns": [r"(?:^|[/.])(?:x|twitter)\.com/[A-Za-z0-9_]+/?$"],
        "exclude_patterns": [
            r"/status/", r"/search", r"/hashtag/",
            r"\.com/(?:home|explore|i|intent|share|login|signup)/?$",
        ],
        "keywords": ["twitter", "x.com", "x/twitter"],
        # "X" alone is too short for a keyword: only these word-bounded forms
        # count (never "xbox", "x-ray", "max", "tax", "x.y").
        "patterns": [
            r"\bor x" + _X_END,
            r"\bon x" + _X_END,
            r"\band x" + _X_END,
            r"(?<![\w\-.])x\s*\?",
            r"\bx\.com\b",
            r"\btwitter\b",
            r"\bx/twitter\b",
            r"\bx \(twitter\)",
        ],
    },
}

# Generic web keywords (only used when no specific platform is named)
WEB_KEYWORDS: list[str] = [
    "check online", "search online", "search the web", "web search",
    "look up online", "look him up", "look her up", "look them up",
    "google", "verify online", "check the internet", "on the internet",
    "does this person exist", "really exist", "real person", "actually exist",
    "exists online", "find online", "find this person online",
    "look this person up", "look up this person", "search for this person online",
    "search the internet", "find this person on the web", "look it up online",
    "zoek op internet", "bestaat deze persoon", "echt bestaat",
]

# Web lookup target keywords (kept for backward compatibility: platform → keywords)
WEB_TARGET_KWS: dict[str, list[str]] = {
    **{pid: list(p["keywords"]) for pid, p in PLATFORMS.items()},
    "web": WEB_KEYWORDS,
}

# Max platforms per lookup and max planned queries
MAX_TARGETS = 3
MAX_QUERIES = 6


def platform_label(target: Optional[str]) -> str:
    """Display label for a target id ("linkedin" → "LinkedIn", "web" → "Web")."""
    if not target:
        return "Web"
    p = PLATFORMS.get(target)
    return p["label"] if p else ("Web" if target == "web" else str(target))


def targets_label(targets: list[str], sep: str = " + ") -> str:
    """"LinkedIn + Facebook" for a list of targets."""
    return sep.join(platform_label(t) for t in targets)


def _normalize_targets(targets: Any) -> list[str]:
    """Accept a str, None or list of targets; return a de-duplicated list (max 3)."""
    if targets is None:
        return []
    if isinstance(targets, str):
        targets = [targets]
    out: list[str] = []
    for t in targets:
        if not t:
            continue
        t = str(t).strip().lower()
        if t == "twitter":
            t = "x"
        if (t in PLATFORMS or t == "web") and t not in out:
            out.append(t)
    return out[:MAX_TARGETS]


def _verify_system_prompt(targets: list[str]) -> str:
    """System prompt for the tool-capable model, mentioning the requested platforms."""
    targets = _normalize_targets(targets) or ["web"]
    hints = []
    for t in targets:
        p = PLATFORMS.get(t)
        if p:
            site = p.get("site") or p["domains"][0]
            hints.append(f"for {p['label']}, search with site:{site} queries")
    hint_text = ("; ".join(hints) + ".") if hints else "search the open web."
    lines = "\n".join(
        f'**{platform_label(t)}:** one of "Likely match found", "Possible match", "No match found" — short reason and the matching [title](url)'
        for t in targets
    )
    return f"""You are a web search assistant. Your task is to verify whether a person exists online on: {targets_label(targets, ", ")}.

Use web_search to find information about the person — {hint_text} Social network pages (LinkedIn, Facebook, Instagram, X) require login and cannot be fetched; rely on their search snippets.

Perform up to 3 search rounds. On the final response (after searching or if no more searches are needed), give a per-platform verdict, one line per requested platform:
{lines}
**Overall:** one-line summary.

Followed by:
- Up to 3 candidate profiles as a bullet list with [name](url) and snippet evidence
- Which details match the document (name / role / employer)
- A one-line caveat: "Note: Search results are not proof of identity."

Refer to people by their name; do not assume gender or use he/she.
Never cite URLs that did not appear in search results. If search failed, be honest about it."""


# System prompt for tool-capable models (default: LinkedIn target)
VERIFY_SYSTEM_PROMPT = _verify_system_prompt(["linkedin"])

# Tool schema for Ollama
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo). Returns titles, URLs and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch a public web page as text. LinkedIn, Facebook, Instagram and X pages require login and are not fetchable — rely on search snippets for those.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch"
                    }
                },
                "required": ["url"]
            }
        }
    }
]

# ── Exceptions ────────────────────────────────────────────────────────────


class WebSearchUnavailable(Exception):
    """Web search MCP server is not available or not running."""
    pass


class SearchBlocked(Exception):
    """The search engine answered with its bot-detection / "no results" notice
    (DuckDuckGo rate limiting) — the query was NOT really searched."""
    pass


SEARCH_BLOCKED_DETAIL = (
    "Web search is temporarily blocked by DuckDuckGo (bot detection) — try again in a few "
    "minutes or enable Brave Search in Settings → Tools"
)
SEARCH_UNAVAILABLE_STATE = "Search unavailable — couldn't verify"
_BLOCKED_NOTICE_RE = re.compile(r"bot[\s-]*detection", re.IGNORECASE)


def is_blocked_notice(text: Any) -> bool:
    """True for DuckDuckGo's "No results were found … DuckDuckGo's bot detection …"
    notice (the MCP server returns it for every query while rate-limited)."""
    if not isinstance(text, str) or not text.strip():
        return False
    low = text.lower()
    if _BLOCKED_NOTICE_RE.search(low):
        return True
    return "no results were found" in low and "duckduckgo" in low


class SearchSkipped(SearchBlocked):
    """DuckDuckGo was NOT called: it blocked us recently and the process-wide
    cool-down is still running (and Brave isn't available). Treated exactly
    like a blocked search by callers; `remaining` = seconds of cool-down left."""

    def __init__(self, remaining: float) -> None:
        self.remaining = max(0.0, float(remaining))
        super().__init__(f"DuckDuckGo cool-down: {self.remaining:.0f} s left")


DDG_SERVER = "duckduckgo-search"
BRAVE_SERVER = "brave-search"

# Process-wide DuckDuckGo politeness (see SearchGovernor)
DDG_MIN_INTERVAL_S = 3.0  # minimum gap between two consecutive DuckDuckGo queries
DDG_COOLDOWN_S = 90.0  # after a bot-detection block: don't call DuckDuckGo at all for this long

COOLDOWN_NOTE_PREFIX = "Cool-down after DuckDuckGo block"
PACING_NOTE_PREFIX = "Pacing:"
EARLY_STOP_NOTE = "Stopped early: profile found"
ENOUGH_RESULTS_MSG = "Enough results found — give your final answer now."


class SearchGovernor:
    """
    Process-wide DuckDuckGo pacing + cool-down (one shared instance,
    DDG_GOVERNOR, is used by every doc-agent run so a demo retry can't keep
    hammering DuckDuckGo and extend its block).

    - pace(): waits until at least `min_interval` seconds have passed since the
      previous DuckDuckGo query (serialised by an asyncio.Lock), then stamps
      the new query time. Returns the seconds waited.
    - note_blocked(): starts a `cooldown` window during which callers must not
      call DuckDuckGo (cooldown_remaining() > 0).

    `clock` (monotonic seconds) and `sleep` are injectable for tests.
    """

    def __init__(
        self,
        *,
        min_interval: float = DDG_MIN_INTERVAL_S,
        cooldown: float = DDG_COOLDOWN_S,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Any]] = None,
    ) -> None:
        self.min_interval = float(min_interval)
        self.cooldown = float(cooldown)
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._last_query: Optional[float] = None
        self._cooldown_until: float = 0.0

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - self._clock())

    def note_blocked(self) -> None:
        """DuckDuckGo answered with its bot-detection notice: start (or extend) the cool-down."""
        self._cooldown_until = max(self._cooldown_until, self._clock() + self.cooldown)
        log.info("DuckDuckGo blocked — cool-down for %.0fs (no DuckDuckGo calls)", self.cooldown)

    async def pace(self, min_gap: Optional[float] = None) -> float:
        """Wait out the minimum gap since the previous DuckDuckGo query; returns seconds waited."""
        gap = self.min_interval if min_gap is None else max(self.min_interval, float(min_gap))
        async with self._lock:
            waited = 0.0
            if self._last_query is not None:
                remaining = self._last_query + gap - self._clock()
                if remaining > 0:
                    await self._sleep(remaining)
                    waited = remaining
            self._last_query = self._clock()
            return waited

    def reset(self) -> None:
        self._last_query = None
        self._cooldown_until = 0.0


# The one process-wide governor (main.py passes it to every WebSearcher)
DDG_GOVERNOR = SearchGovernor()


class WebSearcher:
    """
    Per-run web search over the DuckDuckGo / Brave MCP servers.

    - DuckDuckGo first. When it answers with its bot-detection notice, retry once
      (after `retry_delay` seconds, first time in the run only, and never while
      the cool-down runs); if still blocked, fall back to Brave Search when that
      server is running.
    - With a `governor` (process-wide SearchGovernor): DuckDuckGo queries are
      spaced at least `min_interval` apart, and a block starts a cool-down during
      which DuckDuckGo is not called at all — Brave is used when running, else
      SearchSkipped (a SearchBlocked) is raised immediately.
    - When DuckDuckGo stays blocked and Brave isn't available, raise
      SearchBlocked (callers must never turn that into a "no match" verdict).
    - WebSearchUnavailable when no search server is running at all.

    `call(server_id, query) -> raw text`, `running() -> [server ids]`,
    `parse(text) -> [{url, title, snippet}]` are injected (main.py wires the MCP
    manager in; tests pass fakes).
    """

    def __init__(
        self,
        call: Callable[[str, str], Any],
        running: Callable[[], list],
        parse: Callable[[str], list],
        *,
        retry_delay: float = 3.0,
        sleep: Optional[Callable[[float], Any]] = None,
        governor: Optional[SearchGovernor] = None,
    ) -> None:
        self._call = call
        self._running = running
        self._parse = parse
        self.retry_delay = retry_delay
        self._sleep = sleep or asyncio.sleep
        self._governor = governor
        self._retried = False
        self.blocked_queries = 0
        self.ddg_calls = 0
        self._paced_pending = 0.0  # seconds waited for pacing since the last drain_notes()

    def drain_notes(self) -> list[dict]:
        """Pacing events since the last call: [{"kind": "paced", "seconds": s}]."""
        out = []
        if self._paced_pending > 0:
            out.append({"kind": "paced", "seconds": self._paced_pending})
        self._paced_pending = 0.0
        return out

    async def _ddg(self, query: str, *, retry: bool = False) -> Any:
        if self._governor is not None:
            waited = await self._governor.pace(self.retry_delay if retry else None)
            self._paced_pending += waited
        elif retry:
            await self._sleep(self.retry_delay)
        self.ddg_calls += 1
        return await self._call(DDG_SERVER, query)

    def _cooldown_left(self) -> float:
        return self._governor.cooldown_remaining() if self._governor is not None else 0.0

    async def __call__(self, query: str) -> list[dict]:
        running = list(self._running() or [])
        if DDG_SERVER not in running and BRAVE_SERVER not in running:
            raise WebSearchUnavailable("No web search MCP server running")
        blocked_text = None
        skipped_left = 0.0
        if DDG_SERVER in running:
            skipped_left = self._cooldown_left()
            if skipped_left > 0:
                log.info("DuckDuckGo cool-down (%.0fs left) — not calling it for %r", skipped_left, query)
            else:
                try:
                    text = await self._ddg(query)
                    if is_blocked_notice(text) and not self._retried and self._cooldown_left() <= 0:
                        self._retried = True
                        log.info("DuckDuckGo returned its bot-detection notice — retrying in %.0fs", self.retry_delay)
                        text = await self._ddg(query, retry=True)
                    if is_blocked_notice(text):
                        blocked_text = text
                        if self._governor is not None:
                            self._governor.note_blocked()
                    else:
                        return self._parse(text or "")
                except (WebSearchUnavailable, SearchBlocked):
                    raise
                except Exception as exc:
                    log.warning("search via %s failed: %s", DDG_SERVER, exc)
        if BRAVE_SERVER in running:
            try:
                text = await self._call(BRAVE_SERVER, query)
                return self._parse(text or "")
            except Exception as exc:
                log.warning("search via %s failed: %s", BRAVE_SERVER, exc)
        if blocked_text is not None:
            self.blocked_queries += 1
            raise SearchBlocked(str(blocked_text)[:300])
        if skipped_left > 0:
            self.blocked_queries += 1
            raise SearchSkipped(skipped_left)
        return []


# ── Fetch guard (SSRF / privacy) ──────────────────────────────────────────

FETCH_REFUSED_UNSEEN = "Fetch refused: URL was not returned by the search"
FETCH_REFUSED_PRIVATE = "Fetch refused: URL points to a private address"
FETCH_REFUSED_SCHEME = "Fetch refused: only http(s) URLs can be fetched"
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".localdomain", ".lan", ".home.arpa", ".intranet")


def _url_key_for_fetch(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _ip_is_private(ip_text: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address(ip_text.split("%", 1)[0])
    except ValueError:
        return True  # unparsable → treat as unsafe
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    )


async def check_fetch_url(url: str, allowed_urls: set) -> Optional[str]:
    """
    Decide whether fetch_page may fetch `url`. Returns None when allowed, or the
    refusal message. Rules: http(s) only; the URL must have been returned by
    web_search in this run; the host must not be localhost/.local/.internal nor
    a loopback/private/link-local IP, literally or after DNS resolution.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    raw = (url or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return FETCH_REFUSED_SCHEME
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return FETCH_REFUSED_SCHEME
    allowed_keys = {_url_key_for_fetch(u) for u in (allowed_urls or set())}
    if _url_key_for_fetch(raw) not in allowed_keys:
        return FETCH_REFUSED_UNSEEN

    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return FETCH_REFUSED_PRIVATE
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        return FETCH_REFUSED_PRIVATE if _ip_is_private(host) else None
    if "." not in host:
        return FETCH_REFUSED_PRIVATE  # single-label intranet names

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except Exception:
        return "Fetch refused: host could not be resolved"
    addrs = {info[4][0] for info in infos if info and len(info) > 4 and info[4]}
    if not addrs or any(_ip_is_private(a) for a in addrs):
        return FETCH_REFUSED_PRIVATE
    return None


# ── Pure functions ────────────────────────────────────────────────────────


def _kw_positions(msg_lower: str, kw: str) -> list[int]:
    """Start positions of a keyword, word-bounded (no letter/digit right before/after)."""
    kw = kw.strip().lower()
    if not kw:
        return []
    pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
    return [m.start() for m in re.finditer(pattern, msg_lower)]


def rules_web_lookup(message: str) -> tuple[list[str], list[str]]:
    """
    Detect if message requests web lookup via keyword rules.

    Returns (targets, matched_keywords):
    - targets: ordered list of requested platforms in mention order
      (e.g. ["linkedin", "facebook"]), or ["web"] for generic web keywords,
      or [] when no web lookup is requested
    - matched_keywords: list of keywords that matched
    """
    if not message or not message.strip():
        return [], []

    msg_lower = message.lower()

    # Platform keywords (word-bounded so "fb"/"x" never fire inside other words)
    found: list[tuple[int, str]] = []
    matched: list[str] = []
    for pid, p in PLATFORMS.items():
        first_pos = None
        for kw in p["keywords"]:
            positions = _kw_positions(msg_lower, kw)
            if positions:
                matched.append(kw.strip())
                pos = positions[0]
                if first_pos is None or pos < first_pos:
                    first_pos = pos
        for pat in p.get("patterns", []):
            m = re.search(pat, msg_lower)
            if m:
                if m.group(0).strip() not in matched:
                    matched.append(m.group(0).strip())
                if first_pos is None or m.start() < first_pos:
                    first_pos = m.start()
        if first_pos is not None:
            found.append((first_pos, pid))
    if found:
        found.sort(key=lambda x: x[0])
        return [pid for _, pid in found][:MAX_TARGETS], matched

    # Generic web keywords
    for kw in WEB_KEYWORDS:
        if kw in msg_lower:
            matched.append(kw)
    if matched:
        return ["web"], matched

    return [], []


def rules_web_lookup_single(message: str) -> tuple[Optional[str], list[str]]:
    """Backward-compatible wrapper: (first target or None, matched_keywords)."""
    targets, kws = rules_web_lookup(message)
    return (targets[0] if targets else None), kws


def strip_web_clause(message: str) -> str:
    """
    Remove the sentence/clause containing web lookup keywords from message.

    This prevents rules_intent from picking the wrong intent when the message
    contains both a real intent (e.g., "who is this document about") and web
    keywords (e.g., "check linkedin if this person really exists", "is she on
    Instagram?", "look him up on LinkedIn and Facebook").

    Each sentence is split on ", and" / " and "; parts that mention a web or
    platform keyword are dropped, the rest is kept.

    Returns the stripped message, or the original if stripping would leave <3 chars.
    """
    targets, _ = rules_web_lookup(message)

    if not targets:
        # No web clause; return original
        return message

    sentences = re.split(r'[.!?;]', message)
    kept_sentences = []

    for sent in sentences:
        sent_stripped = sent.strip()
        if not sent_stripped:
            continue

        _, kws = rules_web_lookup(sent_stripped)
        if not kws:
            kept_sentences.append(sent_stripped)
            continue

        # Split on ", and" / " and " and keep the parts without web keywords
        parts = re.split(r",?\s+and\s+", sent_stripped, flags=re.IGNORECASE)
        kept_parts = []
        for part in parts:
            part = part.strip().strip(",").strip()
            if not part:
                continue
            _, part_kws = rules_web_lookup(part)
            if not part_kws:
                kept_parts.append(part)
        kept = " and ".join(kept_parts).strip()
        if kept and len(kept) >= 3:
            kept_sentences.append(kept)

    result = ". ".join(kept_sentences).strip()
    if result and len(result) >= 3:
        return result
    return message


def strip_web_clause_or_empty(message: str) -> str:
    """
    Like strip_web_clause, but a message that is ONLY a web clause ("search the
    web for this person", "look this person up online") becomes "" instead of
    being returned unchanged.
    """
    targets, _ = rules_web_lookup(message)
    if not targets:
        return message
    stripped = strip_web_clause(message)
    if stripped == message or len(stripped.strip()) < 3:
        return ""
    return stripped


LAYA_ONLY_WEB_NOTE = "Laya suggested a web lookup — not run without an explicit request"


def resolve_web_lookup(
    laya_result: Optional[dict],
    rules_result: tuple[Any, list[str]]
) -> dict:
    """
    Resolve web lookup decision from Laya and rules.

    Args:
        laya_result: {"value": "yes"|"no", "confidence": float} or None
        rules_result: (targets, keywords) from rules_web_lookup
            (the legacy (target: str|None, keywords) shape is accepted too)

    Returns:
        {
            "targets": ["linkedin", "facebook"] | ["web"] | [],
            "target": first target or None (backward compat),
            "source": "laya" | "rules" | "laya+rules",
            "confidence": float | None,
            "note": str | None
        }

    The source field is never None; defaults to "rules" when no match is found.
    """
    rules_targets_raw, rules_kws = rules_result
    rules_targets = _normalize_targets(rules_targets_raw)

    # Rules match → use it
    if rules_targets:
        source = "rules"
        confidence = 0.95  # High confidence for rules
        note = None
        if laya_result and laya_result.get("value") == "yes":
            source = "laya+rules"
            confidence = min(1.0, (0.95 + laya_result.get("confidence", 0.5)) / 2)
        return {
            "targets": rules_targets,
            "target": rules_targets[0],
            "source": source,
            "confidence": confidence,
            "note": note,
        }

    # Laya yes WITHOUT an explicit keyword request → never search. A web lookup
    # sends the person's name off this machine, so it needs an explicit ask.
    if laya_result and laya_result.get("value") == "yes":
        return {
            "targets": [],
            "target": None,
            "source": "laya",
            "confidence": laya_result.get("confidence"),
            "note": LAYA_ONLY_WEB_NOTE,
        }

    # No match: source defaults to "laya" if laya_result exists, else "rules"
    return {
        "targets": [],
        "target": None,
        "source": "laya" if laya_result else "rules",
        "confidence": None,
        "note": None,
    }


def build_excerpt_for_verdict(doc_text: str) -> str:
    """
    Build an intelligent excerpt from document text for the verdict prompt.

    Skips lines longer than 200 chars (e.g., date runs) to focus on real content.
    Returns up to ~1500 chars of meaningful text.

    Args:
        doc_text: Full document text

    Returns:
        Excerpt string, or empty string if doc_text is too short
    """
    if not doc_text or len(doc_text.strip()) < 10:
        return ""

    lines = doc_text.split("\n")
    excerpt_lines = []
    excerpt_len = 0

    for line in lines:
        # Skip lines longer than 200 chars (likely date runs or noise)
        if len(line) > 200:
            continue

        # Add line if it won't exceed ~1500 chars
        if excerpt_len + len(line) + 1 <= 1500:
            excerpt_lines.append(line)
            excerpt_len += len(line) + 1
        else:
            break

    return "\n".join(excerpt_lines)


def extract_headline(doc_text: str) -> dict:
    """
    Extract job title and organization from document text.

    Scans the first 40 non-empty lines (skipping lines >120 chars) for a line
    that looks like a job title (contains job keywords, 2-10 words, no "@" or "http").

    When a role line matches, looks for separator patterns ( – , — , - , | , at , bij , @ )
    and splits on the FIRST one found: left part is role, right part is org.
    Falls back to existing "at/bij/@" regex if no separator found.

    Args:
        doc_text: Document text to scan

    Returns:
        {"role": str|None, "org": str|None}
    """
    if not doc_text or len(doc_text.strip()) < 10:
        return {"role": None, "org": None}

    lines = doc_text.split("\n")

    job_keywords = {
        "architect", "developer", "engineer", "manager", "consultant",
        "designer", "analyst", "director", "lead", "specialist", "officer",
        "scientist", "founder", "owner", "advisor"
    }

    result = {"role": None, "org": None}

    # Scan first 40 non-empty lines, skip lines > 120 chars
    non_empty_lines_seen = 0
    for i, line in enumerate(lines):
        # Skip empty lines
        if not line.strip():
            continue

        # Skip lines longer than 120 chars (e.g., date runs)
        if len(line) > 120:
            continue

        non_empty_lines_seen += 1
        if non_empty_lines_seen > 40:
            break

        line_lower = line.lower()

        # Skip lines that are too short or contain special chars
        if len(line.strip()) < 5 or "@" in line or "http" in line:
            continue

        # Check if line contains job keywords
        words = line.split()
        if not (2 <= len(words) <= 10):
            continue

        # Check for any job keyword
        if any(kw in line_lower for kw in job_keywords):
            role_line = line.strip().rstrip(".")

            def _has_job(text: str) -> bool:
                t = text.lower()
                return any(kw in t for kw in job_keywords)

            def _ok(role: Optional[str], org: Optional[str]) -> bool:
                return bool(
                    role and org and not _is_generic_role(role) and not _is_bad_org(org)
                    and not _VERB_ROLE_RE.search(role)
                )

            # Sentences: "X works at ORG as ROLE", "works as ROLE at ORG",
            # "werkt bij ORG als ROLE"
            for pat in _HEADLINE_SENTENCE_RES:
                m = pat.search(role_line)
                if m:
                    role_s = m.group("role").strip(" ,;:")
                    org_s = m.group("org").strip(" ,;:")
                    if _ok(role_s, org_s):
                        return {"role": role_s, "org": org_s}

            # Check for separator patterns (in order): " – ", " — ", " - ", " | ", " at ", " bij ", " @ "
            separators = [" – ", " — ", " - ", " | ", " at ", " bij ", " @ "]
            for sep in separators:
                # Case-insensitive search for the separator
                sep_idx = role_line.lower().find(sep.lower())
                if sep_idx == -1:
                    continue
                left = role_line[:sep_idx].strip()
                right = role_line[sep_idx + len(sep):].strip()
                role_part, org_part = left, right
                if sep.strip() in ("–", "—", "-", "|") and _has_job(right) and not _has_job(left):
                    # "ORG — ROLE"
                    role_part, org_part = right, left
                # "ROLE at ORG as …" / "ORG as ROLE" leftovers
                as_m = re.search(r"\s(?:as|als)\s+(?:an?\s+)?", org_part, re.IGNORECASE)
                if as_m:
                    tail = org_part[as_m.end():].strip()
                    org_part = org_part[:as_m.start()].strip()
                    if _has_job(tail):
                        role_part = tail
                if org_part and 1 <= len(org_part) <= 60 and _ok(role_part, org_part):
                    result["role"] = role_part
                    result["org"] = org_part
                    return result
                if (
                    org_part and not _is_bad_org(org_part) and role_part
                    and not _VERB_ROLE_RE.search(role_part) and _is_generic_role(role_part)
                ):
                    # "Owner – Acme": a real organisation, a generic role
                    return {"role": None, "org": org_part}
                if role_part and not _VERB_ROLE_RE.search(role_part) and not _is_generic_role(role_part):
                    # Separator found but org part invalid; keep role, try fallback below
                    result["role"] = role_part
                break

            # Fallback: if no separator split happened, use existing regex on this line or next line
            if not result["org"]:
                org_match = re.search(r'(?:\bat|\bbij|@)\s+([A-Za-z0-9\s&\-]+?)(?:,|$)', role_line, re.IGNORECASE)
                if not org_match and i + 1 < len(lines):
                    # Try next line
                    next_line = lines[i + 1]
                    org_match = re.search(r'(?:\bat|\bbij|@)\s+([A-Za-z0-9\s&\-]+?)(?:,|$)', next_line, re.IGNORECASE)

                if org_match:
                    org = org_match.group(1).strip()
                    if org and len(org) < 100 and not _is_bad_org(org):  # Sanity check
                        result["org"] = org

            if not result["role"] and not _VERB_ROLE_RE.search(role_line) and not _is_generic_role(role_line):
                result["role"] = role_line

            if result["role"] or result["org"]:
                return result
            result = {"role": None, "org": None}

    return result


# "Temp Person works" — a sentence fragment, not a job title
_VERB_ROLE_RE = re.compile(r"\b(?:works|worked|working|werkt|is|was|has|heeft|as|als)\b", re.IGNORECASE)
_HEADLINE_SENTENCE_RES = [
    re.compile(
        r"\b(?:works|worked|working|is employed|employed)\s+(?:at|for|with)\s+(?P<org>.+?)\s+as\s+(?:an?\s+)?(?P<role>.+?)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:works|worked|working|is employed|employed)\s+as\s+(?:an?\s+)?(?P<role>.+?)\s+(?:at|for|with)\s+(?P<org>.+?)$",
        re.IGNORECASE,
    ),
    re.compile(r"\bwerk(?:t|te)?\s+bij\s+(?P<org>.+?)\s+als\s+(?P<role>.+?)$", re.IGNORECASE),
    re.compile(r"\bwerk(?:t|te)?\s+als\s+(?P<role>.+?)\s+bij\s+(?P<org>.+?)$", re.IGNORECASE),
]


_GENERIC_ROLE_EXACT = {
    "holder", "subject", "subject of the cv", "candidate", "applicant",
    "candidate/applicant", "owner", "author", "signer", "signatory",
    "person", "individual", "employee", "n/a", "unknown",
    "professional profile", "profile", "cv", "resume", "curriculum vitae",
    "cv owner", "document owner", "subject of the document",
}
# Answer-derived descriptions of "the person in the document", never job titles.
_GENERIC_ROLE_RE = re.compile(
    r"\bindividual\b|\bperson whose\b|\bcv holder\b|\bholder\b|\bwhose cv\b|\bthe person\b"
    r"|\bdocument\b(?!\s+(?:controller|control|specialist|manager|management|analyst|engineer|designer))"
    r"|\bsubject\b(?!\s+matter)"
    r"|^the\s+(?:individual|person|people|candidate|applicant|author|employee|signer|signatory|owner"
    r"|holder|subject|client|customer|user|writer|sender|recipient|addressee|party|parties|undersigned"
    r"|professional|profile|resume|cv|document)\b",
    re.IGNORECASE,
)
ROLE_MAX_CHARS = 60


def _is_generic_role(role: str) -> bool:
    """Check if role is a generic placeholder (or an answer-derived description
    such as "the individual whose CV is presented") that should be treated as None."""
    if not role:
        return True
    # Strip parentheses content first
    role_stripped = re.sub(r'\([^)]+\)', '', role).strip()
    low = role_stripped.lower().strip(" .:;,*_")
    if not low or low in _GENERIC_ROLE_EXACT:
        return True
    if len(role_stripped) > ROLE_MAX_CHARS:
        return True
    return bool(_GENERIC_ROLE_RE.search(low))


def _is_bad_org(org: str) -> bool:
    """An organisation string that is really a sentence fragment ("Temp Corp as
    Temp Engineer.") or too long to be a name."""
    if not org:
        return True
    o = org.strip().strip(".")
    return (not o) or len(o) > ROLE_MAX_CHARS or bool(re.search(r"\s(?:as|als)\s", o, re.IGNORECASE))


# Organisation / company suffixes — a "name" containing one is not a person.
_ORG_SUFFIX_SHORT_RE = re.compile(  # legal forms: case-sensitive, never the first word
    r"[\s,](?:B\.\s?V\.?|BV|N\.\s?V\.?|NV|AG|SA|S\.A\.?|SARL|S\.A\.R\.L\.?|Co\.|CO\.|LLC|L\.L\.C\.?"
    r"|Ltd\.?|LTD\.?|Inc\.?|INC\.?|GmbH|GMBH|PLC|plc|Plc|Corp\.?|CORP\.?)(?=$|[\s,.)])"
)
_ORG_SUFFIX_WORD_RE = re.compile(
    r"\b(?:limited|incorporated|corporation|company|group|holding|holdings|stichting|vereniging"
    r"|foundation|university|universiteit|bank)\b",
    re.IGNORECASE,
)


def looks_like_organization(name: str) -> bool:
    """True for "Acme B.V.", "Foo Holding", "Rabobank Group", "Delft University" …"""
    if not name:
        return False
    n = name.strip()
    return bool(_ORG_SUFFIX_SHORT_RE.search(n) or _ORG_SUFFIX_WORD_RE.search(n))


_NAME_STOPWORDS = {
    "document", "documents", "summary", "key", "points", "point", "takeaways", "takeaway",
    "name", "names", "role", "roles", "organization", "organisation", "company", "employer",
    "date", "dates", "amount", "amounts", "total", "invoice", "contract", "address",
    "main", "overview", "conclusion", "conclusions", "information", "details", "detail",
    "person", "people", "persons", "signature", "signatures", "holder", "subject",
    "supporting", "text", "quote", "source", "sources", "answer", "note", "notes",
    "online", "verification", "overall", "likely", "possible", "match", "found", "no",
    "the", "this", "that", "of", "and", "for", "with", "not", "unknown", "data", "fields",
    "contact", "info", "type", "status", "number", "table", "section", "page", "pages",
    "important", "decisions", "email", "phone", "linkedin", "facebook", "instagram", "twitter",
    "web", "search", "results", "result", "document's", "cv", "resume", "id", "card",
    "title", "titel", "functie", "position",
}
_NAME_PARTICLES = {"de", "van", "der", "den", "von", "la", "le", "di", "da", "du", "del", "ter", "te", "het", "'t", "bin", "al", "dos", "das", "y"}


def _strip_name_decorations(name: str) -> str:
    """Drop trailing parentheticals / role suffixes and punctuation from a name."""
    if not name:
        return ""
    name = re.sub(r'\s*\([^)]*\)\s*', ' ', name)
    name = re.split(r'\s+[–—-]\s+|,|;', name)[0]
    return name.strip().strip(":.*_\'\"` ").strip()


def _is_valid_name(name: str) -> bool:
    """Check if name is valid (2–6 words of letters, accents, hyphens, apostrophes)."""
    if not name:
        return False
    if name.lower() in ("unknown", "not specified", "n/a", "none", "not given"):
        return False
    words = name.split()
    if not (2 <= len(words) <= 6):
        return False
    if looks_like_organization(name):
        return False  # "Acme B.V." is an organisation, not a person
    return all(re.fullmatch(r"[^\W\d_](?:[^\W\d_]|[-'’.])*", w) for w in words)


def _is_proper_name(name: str) -> bool:
    """A 2–5-word proper name: capitalised words (lower-case particles such as
    "de"/"van" allowed inside), none of them a heading/label word."""
    if not _is_valid_name(name):
        return False
    words = name.split()
    if not (2 <= len(words) <= 5):
        return False
    if any(w.lower().strip(".") in _NAME_STOPWORDS for w in words):
        return False
    if not words[0][0].isupper() or not words[-1][0].isupper():
        return False
    return all(w[0].isupper() or w.lower() in _NAME_PARTICLES for w in words)


def _clean_md(text: str) -> str:
    """Remove markdown formatting (bold/italic/code) and surrounding quotes."""
    if not text:
        return ""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = text.strip('\'"')
    return text.strip()


# ── Role / organisation clean-up (knowledge store) ─────────────────────────
# Extra legal forms not covered by looks_like_organization (Dutch forms, dot-less).
_LEGAL_FORM_EXTRA_RE = re.compile(
    r"(?:^|[\s,])(?:V\.?O\.?F\.?|VOF|B\.V|N\.V|LLP|Eenmanszaak|eenmanszaak|EENMANSZAAK)(?=$|[\s,.)])"
)
# Generic party words: "(supplier)", "(klant)" … never a role or an organisation by themselves.
_GENERIC_PARTY_WORDS = {
    "supplier", "suppliers", "customer", "customers", "client", "clients", "klant", "klanten",
    "leverancier", "leveranciers", "buyer", "seller", "vendor", "contractor", "opdrachtgever",
    "opdrachtnemer", "party", "parties", "partij", "partijen", "signer", "signatory", "signatories",
    "ondertekenaar", "landlord", "tenant", "huurder", "verhuurder", "lessor", "lessee", "licensor",
    "licensee", "provider", "dienstverlener", "demo", "fictitious", "fictief", "company", "organisation",
    "organization", "employer", "werkgever", "side",
}
_PARTY_FILLER_WORDS = {"the", "for", "of", "on", "behalf", "de", "het", "van", "namens", "and", "en", "a", "an", "voor"}
# Head nouns of job titles ("Account Manager", "Cloud Engineer", "Owner", "eigenaar" …).
_ROLE_HEAD_WORDS = {
    "manager", "engineer", "owner", "eigenaar", "director", "directeur", "ceo", "cto", "cfo", "coo", "cio",
    "ciso", "founder", "co-founder", "cofounder", "oprichter", "architect", "consultant", "developer",
    "ontwikkelaar", "designer", "analyst", "specialist", "advisor", "adviser", "adviseur", "officer",
    "lead", "head", "president", "partner", "accountant", "lawyer", "advocaat", "notary", "notaris",
    "representative", "coordinator", "coördinator", "assistant", "administrator", "secretary",
    "secretaris", "chairman", "chairwoman", "chair", "voorzitter", "treasurer", "penningmeester",
    "bestuurder", "zaakvoerder", "proprietor", "scientist", "researcher", "programmer", "technician",
    "supervisor", "executive", "associate", "intern", "trainee", "teacher", "docent", "professor",
    "nurse", "doctor", "physician", "clerk", "broker", "auditor", "controller", "medewerker",
    "beheerder", "planner", "recruiter", "strategist", "editor", "journalist", "photographer",
    "marketeer", "marketer", "operator", "mechanic", "vp", "principal", "steward", "officier",
}
# Label words that are never an entity name ("Job Title", "Role", "Company" …).
_LABEL_WORDS = {
    "job title", "title", "titel", "role", "rol", "roles", "company", "organization", "organisation",
    "employer", "werkgever", "functie", "functietitel", "position", "name", "naam", "bedrijf",
    "job", "occupation", "beroep",
}


def is_label_word(name: Optional[str]) -> bool:
    """True for a bare field label such as "Job Title" / "Role" / "Company"."""
    if not name:
        return False
    return _normalize_text(name).strip(" .,;:*_") in _LABEL_WORDS


_DOTTED_ABBR_END_RE = re.compile(r"(?:^|[\s(])(?:[^\W\d_]\.)+[^\W\d_]$")
_ABBR_WORD_END_RE = re.compile(r"(?:^|\s)(?:Inc|Ltd|Co|Corp|Bros|Jr|Sr|St|Dr|Drs|Ir|Mr|Mrs|Ms)$")


def clean_entity_name(name: Any) -> str:
    """A person/role/organisation name without markdown quotes and trailing
    punctuation: "Representative of the supplier and signer." → "…signer",
    "Noordlicht Cloud Services B.V.." → "…B.V." (a dotted legal form keeps its
    final period, a bare "B.V" gets it back)."""
    if not name:
        return ""
    n = re.sub(r"\s+", " ", str(name)).strip()
    n = n.strip("*_`\"'“”‘’ ").strip()
    core = re.sub(r"[\s.,;:]+$", "", n)
    if not core:
        return ""
    had_dot = "." in n[len(core):]
    if _DOTTED_ABBR_END_RE.search(core) or (had_dot and _ABBR_WORD_END_RE.search(core)):
        core += "."
    return core


def _is_generic_party(text: str) -> bool:
    """"supplier", "the customer", "for the supplier", "DEMO" …"""
    words = [w for w in re.findall(r"[^\W\d_]+", (text or "").lower()) if w not in _PARTY_FILLER_WORDS]
    return bool(words) and all(w in _GENERIC_PARTY_WORDS for w in words)


def _org_key(name: Optional[str]) -> str:
    return _normalize_text(clean_entity_name(name or "")).rstrip(".,;: ")


def looks_like_legal_org(name: Optional[str]) -> bool:
    """An organisation by its legal form (B.V., N.V., Ltd, GmbH, Inc, LLC, VOF,
    Eenmanszaak …) or organisation word (Holding, Foundation …)."""
    if not name:
        return False
    return looks_like_organization(name) or bool(_LEGAL_FORM_EXTRA_RE.search(name.strip()))


def clean_org_name(name: Any) -> str:
    """An organisation name without a trailing generic parenthetical such as
    "(DEMO)" or "(supplier)", and without trailing punctuation."""
    o = clean_entity_name(name)
    while True:
        m = re.search(r"\s*\(([^()]*)\)\s*$", o)
        if not m or not _is_generic_party(m.group(1)):
            break
        o = clean_entity_name(o[:m.start()])
    return o


def _role_head_ok(words: list[str]) -> bool:
    return bool(words) and words[-1].lower().strip(".,") in _ROLE_HEAD_WORDS


def _split_role_paren(
    role: str, org: Optional[str] = None, known_orgs: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a parenthetical inside a role value → (role, org_from_parenthetical).

    - "(Noordlicht Cloud Services B.V.)" (a legal form / an organisation named in
      the same answer): the parenthetical is the employer, the text outside it is
      the role.
    - "(supplier)" / "(klant)": a generic party word — dropped.
    - "(Neuféglise Digital Solutions)" (capitalised, not a job title) next to a
      descriptive role: treated as the organisation.
    - otherwise the old rule: prefer the text inside ("Subject of the CV
      (Solution Architect)" → "Solution Architect").
    """
    m = re.search(r"\(([^()]+)\)", role or "")
    if not m:
        return role, None
    inner = _clean_md(m.group(1)).strip()
    outside = re.sub(r"\s+", " ", (role[:m.start()] + " " + role[m.end():])).strip()
    outside = re.sub(r"\s+([.,;:])", r"\1", outside).strip()
    known = {_org_key(o) for o in (known_orgs or []) if o}
    if org:
        known.add(_org_key(org))
    inner_key = _org_key(inner)
    if looks_like_legal_org(inner) or (inner_key and inner_key in known):
        return (outside or None), clean_org_name(inner) or None
    if _is_generic_party(inner):
        return (outside or None), None
    words = inner.split()
    capitalised = [w for w in words if w[:1].isupper()]
    if outside and len(words) >= 2 and len(capitalised) >= 2 and not _role_head_ok(words):
        return outside, clean_org_name(inner) or None
    return inner, None


_TITLE_LABELS = ("Job Title", "Job title", "Functietitel", "Functie", "Title", "Titel", "Position")
_ROLE_LABELS = ("Role", "Rol")


def _label_value(text: str, labels: tuple) -> Optional[str]:
    """The value of the first "**Label:** value" / "**Label**: value" / "Label: value" line."""
    alt = "|".join(re.escape(label) for label in labels)
    m = re.search(rf'\*\*(?:{alt}):\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
    if not m:
        m = re.search(rf'\*\*(?:{alt})\*\*[ \t]*:[ \t]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if not m:
        m = re.search(
            rf'(?:^|\n)[ \t]*(?:(?:[-*•+]|\d+[.)])[ \t]+)?(?:{alt}):\s+(.+?)(?:\n|$)', text, re.IGNORECASE
        )
    if not m:
        return None
    return _clean_md(m.group(1).strip()) or None


def _role_org_from(text: str, known_orgs: Optional[Any] = None) -> tuple[Optional[str], Optional[str]]:
    """The role ("Job Title:"/"Functie:"/"Title:" preferred over "Role:") and the
    "Organization/Company/Employer:" value in `text`. An organisation in the
    role's parenthetical becomes the org (when no org label is given)."""
    role: Optional[str] = None
    org: Optional[str] = None
    if not text:
        return None, None
    for org_label in ["Organization", "Organisation", "Company", "Employer"]:
        o = _label_value(text, (org_label,))
        if o and not is_label_word(o):
            org = o
            break
    paren_org: Optional[str] = None
    for labels in (_TITLE_LABELS, _ROLE_LABELS):
        r = _label_value(text, labels)
        if not r:
            continue
        r, p_org = _split_role_paren(r, org, known_orgs)
        r = clean_entity_name(r) if r else r
        if r and not _is_generic_role(r) and not is_label_word(r) and not looks_like_legal_org(r):
            role = r
            paren_org = paren_org or p_org
            break
        paren_org = paren_org or p_org
    if not org and paren_org:
        org = paren_org
    return role, org


def _answer_orgs(text: str) -> list[str]:
    """Organisations named anywhere in an answer: org labels and bold/parenthesised
    names with a legal form."""
    out: list[str] = []
    if not text:
        return out
    for m in re.finditer(
        r'(?:Organization|Organisation|Company|Employer)(?:\*\*)?:(?:\*\*)?[ \t]+(.+?)(?:\n|$)', text, re.IGNORECASE
    ):
        o = clean_org_name(_clean_md(m.group(1)))
        if o:
            out.append(o)
    for m in re.finditer(r'\*\*(.+?)\*\*|\(([^()]+)\)', text):
        cand = _clean_md(m.group(1) or m.group(2) or "")
        if cand and looks_like_legal_org(cand):
            out.append(clean_org_name(cand))
    return out


def _fold_same_length(s: str) -> str:
    """Lower-case, accent-free copy of `s` with the SAME length (index-aligned)."""
    import unicodedata
    return "".join(((unicodedata.normalize("NFKD", c)[:1] or c).lower()[:1] or c) for c in s)


_TITLE_AFTER_COMMA_RE = re.compile(r"[ \t]*,[ \t]*([^\W\d_][\w'’&/.-]*(?:[ \t]+[^\W\d_][\w'’&/.-]*){0,5})")
_TITLE_IN_PAREN_RE = re.compile(r"[ \t]*\(([^\W\d_][\w'’&/. -]{1,48})\)")
_TITLE_STOP_WORDS = {"at", "bij", "from", "with", "for", "voor", "in", "of", "van", "namens", "on", "op"}


def _title_candidate(raw: str) -> Optional[str]:
    """"Account Manager at Noordlicht" → "Account Manager"; "Owner" → "Owner";
    "eigenaar" → "Eigenaar"; "Amsterdam" → None (no job-title head word)."""
    words = raw.split()
    head_of = {"head", "hoofd", "director", "directeur", "vp", "chief"}
    # a title never runs on past a preposition ("… at Acme", "… bij Acme"),
    # except "Head of Sales"-style titles
    for i, w in enumerate(words):
        lw = w.lower()
        if i > 0 and lw in _TITLE_STOP_WORDS:
            if lw in ("of", "van") and i == 1 and words[0].lower() in head_of:
                continue
            words = words[:i]
            break
    words = words[:4]
    for n in range(len(words), 0, -1):
        cand = words[:n]
        is_head_of = len(cand) >= 3 and cand[0].lower() in head_of and cand[1].lower() in ("of", "van")
        if not (_role_head_ok(cand) or is_head_of):
            continue
        if not all(w[:1].isupper() or w.lower() in _ROLE_HEAD_WORDS or w.lower() in ("of", "van", "&") for w in cand):
            continue
        title = clean_entity_name(" ".join(cand))
        if title and not looks_like_legal_org(title):
            return title[:1].upper() + title[1:]
    return None


def title_from_document(name: Optional[str], doc_text: Optional[str]) -> Optional[str]:
    """The job title the document itself states next to the person's name:
    "Sanne de Vries, Account Manager" → "Account Manager",
    "Michel Neuféglise, Owner (eigenaar)" → "Owner", "Jane Doe (Cloud Engineer)"
    → "Cloud Engineer". Accent/case-insensitive; the first occurrence with a
    recognisable job title wins. None when the document states no title."""
    if not name or not doc_text:
        return None
    words = _normalize_text(name.strip()).split()
    if len(words) < 2:
        return None
    folded = _fold_same_length(doc_text)
    if len(folded) != len(doc_text):
        return None
    pat = re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)")
    for m in pat.finditer(folded):
        for rx in (_TITLE_AFTER_COMMA_RE, _TITLE_IN_PAREN_RE):
            t = rx.match(doc_text, m.end())
            if not t:
                continue
            title = _title_candidate(t.group(1))
            if title and _is_generic_role(title):
                # "Owner (eigenaar)": the bare "Owner" reads as "CV owner" — use
                # the parenthetical title right after it instead
                p = _TITLE_IN_PAREN_RE.match(doc_text, t.end())
                title = _title_candidate(p.group(1)) if p else None
            if title and not _is_generic_role(title):
                return title
    return None


def concise_role(role: Optional[str], max_words: int = 5) -> Optional[str]:
    """A descriptive role shortened deterministically: first clause only
    ("…, and signer" / "… and signer" dropped), at most `max_words` words,
    no dangling function word, no trailing punctuation."""
    r = clean_entity_name(role)
    if not r:
        return None
    r = re.split(r"\s*[;,]\s+|\s+[–—-]\s+", r)[0]
    m = re.search(r"\s+(?:and|en|&)\s+(?=[a-z])", r)
    if m and len(r[:m.start()].split()) >= 2:
        r = r[:m.start()]
    words = r.split()
    if len(words) > max_words:
        r = " ".join(words[:max_words])
    r = re.sub(r"(?:\s+(?:of|for|the|a|an|and|at|in|to|with|van|voor|de|het|bij|en|&))+$", "", r, flags=re.IGNORECASE)
    return clean_entity_name(r) or None


def refine_person_facts(
    name: Optional[str],
    role: Optional[str],
    org: Optional[str],
    doc_text: Optional[str] = None,
    known_orgs: Optional[Any] = None,
    person_names: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Final (role, org) for a person before it enters the knowledge store.

    - names are normalised (trailing punctuation; "B.V.." → "B.V.");
    - an organisation in the role (legal form / parenthetical / known org) moves
      to `org` and never becomes a role; "(supplier)"-style words are dropped;
    - a title stated in the document next to the person's name wins
      ("Sanne de Vries, Account Manager" → "Account Manager");
    - otherwise a descriptive role is shortened (≤ 5 words, first clause);
    - label words ("Job Title", "Role", "Company") are never kept.
    """
    known = [o for o in (known_orgs or []) if o]
    r = clean_entity_name(role) or None
    o = clean_org_name(org) or None
    if r and "(" in r:
        r, p_org = _split_role_paren(r, o, known)
        r = clean_entity_name(r) or None
        o = o or p_org
    known_keys = {_org_key(k) for k in known}
    if o:
        known_keys.add(_org_key(o))
    if r and (_org_key(r) in known_keys or looks_like_legal_org(r)):
        o = o or (clean_org_name(r) or None)
        r = None
    if r and is_label_word(r):
        r = None
    title = title_from_document(name, doc_text)
    if title:
        r = title
    elif r:
        r = concise_role(r)
    if r and _is_generic_role(r):
        r = None
    person_keys = {_normalize_text(p) for p in (person_names or []) if p}
    if o and (is_label_word(o) or _normalize_text(o) in person_keys or _is_generic_party(o)):
        o = None
    return r, o


_NAME_LABEL_RE = re.compile(r'(?:^|\n)[ \t]*(?:[-*•]\s*)?(?:\*\*)?Name:(?:\*\*)?\s+(.+?)(?:\n|$)', re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r'^([ \t]*)(?:[-*•+]|\d+[.)])[ \t]+', re.MULTILINE)


def _entity_markers(text: str) -> list[tuple[int, str]]:
    """(start, normalised name) of every named entity in an answer: bold proper
    names, bold organisations and "Name:" labels. "**Role:**"-style labels are not
    entities."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r'\*\*(.+?)\*\*', text):
        raw = m.group(1).strip()
        if raw.endswith(":") or (text[m.end():m.end() + 2].lstrip().startswith(":") and is_label_word(raw)):
            continue  # "**Role:**" / "**Company**:" are labels, not entities ("**Jane Doe**:" still is one)
        cleaned = _clean_md(raw)
        name = _strip_name_decorations(cleaned)
        if _is_proper_name(name) or looks_like_organization(name) or looks_like_organization(cleaned):
            out.append((m.start(), _normalize_text(name)))
    for m in _NAME_LABEL_RE.finditer(text):
        name = _strip_name_decorations(_clean_md(m.group(1).strip()))
        if name:
            out.append((m.start(1), _normalize_text(name)))
    out.sort(key=lambda x: x[0])
    return out


def _entity_block(text: str, start: int, end: int, labelled: bool,
                  markers: Optional[list[tuple[int, str]]] = None) -> str:
    """The part of `text` that belongs to the entity named at [start, end): from
    the name to the next entity (bold name/org or "Name:" label) or — for a name
    that heads a bullet — the next bullet at the same or a shallower level."""
    if markers is None:
        markers = _entity_markers(text)
    stop = len(text)
    for pos, _ in markers:
        if pos >= end:
            stop = min(stop, pos)
            break
    if not labelled:
        line_start = text.rfind("\n", 0, start) + 1
        bm = _BULLET_LINE_RE.match(text, line_start)
        indent = len(bm.group(1).expandtabs(4)) if bm and bm.end() <= start else 0
        for b in _BULLET_LINE_RE.finditer(text, end):
            if b.start() >= stop:
                break
            if b.start() > line_start and len(b.group(1).expandtabs(4)) <= indent:
                stop = b.start()
                break
    return text[end:stop]


def extract_persons(answer_text: str) -> list[dict]:
    """
    Every distinct person named in an answer (several documents / people):
    "**Name:** X" lines, bold proper names, and "Jane Example (contract)"-style
    names followed by a parenthetical. Returns [{"name", "role", "org"}] in order
    of appearance. With several persons, each one's role/org is read only from
    that person's own bullet/block (never from a neighbouring entity).
    """
    if not answer_text:
        return []
    found: list[tuple[int, int, str, bool]] = []

    def _add(pos: int, endpos: int, raw: str, require_proper: bool = True, labelled: bool = False) -> None:
        cleaned = re.sub(r'[*_`]', '', raw or '')
        name = _strip_name_decorations(cleaned)
        ok = _is_proper_name(name) if require_proper else _is_valid_name(name)
        # "Acme B.V." / "Foo Holding" are organisations, not persons
        if ok and (looks_like_organization(name) or looks_like_organization(cleaned.strip())):
            ok = False
        if ok:
            found.append((pos, endpos, name, labelled))

    for m in _NAME_LABEL_RE.finditer(answer_text):
        _add(m.start(1), m.end(1), m.group(1), require_proper=False, labelled=True)
    for m in re.finditer(r'\*\*(.+?)\*\*', answer_text):
        # "**Job Title:**" / "**Role**:" are field labels, never persons — but
        # "**Sanne de Vries**: Account Manager" / "**Sanne de Vries:** …" name one
        if is_label_word(m.group(1)):
            continue
        _add(m.start(1), m.end(1), m.group(1))
    name_re = r"([^\W\d_][\w'’-]*(?:\s+(?:[a-z]{1,3}\s+)*[^\W\d_][\w'’-]*){1,4})\s*\("
    for m in re.finditer(name_re, answer_text):
        _add(m.start(1), m.end(1), m.group(1))

    found.sort(key=lambda x: x[0])
    markers = _entity_markers(answer_text)
    known_orgs = _answer_orgs(answer_text)
    out: list[dict] = []
    seen: set = set()
    for pos, endpos, name, labelled in found:
        key = _normalize_text(name)
        if key in seen or is_label_word(name):
            continue
        seen.add(key)
        role, org = _role_org_from(_entity_block(answer_text, pos, endpos, labelled, markers), known_orgs)
        out.append({"name": name, "role": role, "org": org})
    if len(out) == 1:
        single = extract_person(answer_text, [])
        if single and _normalize_text(single["name"]) == _normalize_text(out[0]["name"]):
            return [single]
    return out


PERSON_JSON_PROMPT = """Who is the main person this document is about, issued to, or signed by?
Answer with ONLY one JSON object and nothing else:
{{"name": "<full name or empty>", "role": "<job title/role or empty>", "org": "<organisation or empty>"}}
Use only information from the document. If no person is named, use an empty name.

<document>
{doc}
</document>"""


def parse_person_json(text: str) -> Optional[dict]:
    """Parse the first JSON object in an LLM reply into {name, role, org} (or None)."""
    import json
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:
                        break
                    if not isinstance(obj, dict):
                        return None
                    name = _strip_name_decorations(str(obj.get("name") or "").strip())
                    if not _is_valid_name(name):
                        return None
                    role = str(obj.get("role") or "").strip() or None
                    org = str(obj.get("org") or obj.get("organization") or "").strip() or None
                    if role and _is_generic_role(role):
                        role = None
                    return {"name": name, "role": role, "org": org}
        start = text.find("{", start + 1)
    return None


async def llm_extract_person(hooks: Any, model: str, doc_text: str) -> Optional[dict]:
    """Document-first fallback: ask the answer model for the person as strict JSON
    over the first ~3000 chars of the subject document. None on any failure."""
    if not model or not doc_text or not getattr(hooks, "stream_llm", None):
        return None
    prompt = PERSON_JSON_PROMPT.format(doc=doc_text[:3000])
    reply = ""
    try:
        try:
            stream = hooks.stream_llm(model, prompt, think=False, num_predict=200)
        except TypeError:
            stream = hooks.stream_llm(model, prompt, think=False)
        async for delta in stream:
            if "content" in delta:
                reply += delta["content"]
            elif delta.get("done"):
                break
    except Exception as e:
        log.debug(f"llm_extract_person failed: {e}")
        return None
    return parse_person_json(reply)


def extract_person(answer_text: str, history: list[dict]) -> Optional[dict]:
    """
    Extract person details (name, role, org) from answer text or history.

    Searches answer_text first, then assistant history messages (newest → oldest).

    Returns:
        {"name": str, "role": str|None, "org": str|None}
    or None if no name found.

    Patterns searched:
    - **Name:** X / Name: X (line)
    - This document is about **X** / is about X.
    - **Role:** X / Role: X
    - **Organization:** X / **Company:** X / **Employer:** X

    If role contains parentheses, prefer text inside them (e.g., "Subject of CV (Solution Architect)" → "Solution Architect").
    """
    clean_text = _clean_md

    def is_valid_name(name: str) -> bool:
        return _is_valid_name(name)

    def extract_from_text(text: str) -> dict:
        """Extract name, role, org from text."""
        result = {"name": None, "role": None, "org": None}
        span: Optional[tuple[int, int]] = None  # where the chosen name sits in `text`
        labelled = False  # chosen via a "Name:" label (sibling bullets are its attributes)

        # Pattern: **Name:** X or Name: X
        name_match = re.search(r'\*\*Name:\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if not name_match:
            name_match = re.search(r'(?:^|\n)Name:\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if name_match:
            name = _strip_name_decorations(clean_text(name_match.group(1).strip()))
            if is_valid_name(name):
                result["name"] = name
                span, labelled = name_match.span(1), True

        # Pattern: This document is about **X** or is about X.
        if not result["name"]:
            about_match = re.search(r'This document is about \*\*(.+?)\*\*', text, re.IGNORECASE)
            if about_match:
                name = clean_text(about_match.group(1).strip())
                if is_valid_name(name):
                    result["name"] = name
                    span = about_match.span(1)
            if not result["name"]:
                about_match = re.search(r'is about ([^.]+)\.', text, re.IGNORECASE)
                if about_match:
                    name = clean_text(about_match.group(1).strip())
                    if is_valid_name(name):
                        result["name"] = name
                        span = about_match.span(1)

        # Pattern: "… about:" followed by a bullet whose first bold span is a name
        #   "The document is about:\n\n*   **Michel Neuféglise** (holder)"
        if not result["name"]:
            about_bullet = re.search(
                r'about:?\s*\n+\s*(?:[-*•]|\d+[.)])\s+[^\n]*?\*\*(.+?)\*\*', text, re.IGNORECASE
            )
            if about_bullet:
                name = _strip_name_decorations(clean_text(about_bullet.group(1)))
                if _is_proper_name(name):
                    result["name"] = name
                    span = about_bullet.span(1)

        # Fallback: the first bold 2–5-word proper name anywhere in the answer
        if not result["name"]:
            for m in re.finditer(r'\*\*(.+?)\*\*', text):
                name = _strip_name_decorations(clean_text(m.group(1)))
                if _is_proper_name(name):
                    result["name"] = name
                    span = m.span(1)
                    break

        # Role / organisation: read from the chosen name's OWN bullet/block first.
        # When the answer lists several entities (e.g. "**Acme B.V.**" then
        # "**Jane Example**", each with a "Role:" sub-bullet) a label outside that
        # block belongs to someone else, so it is never borrowed.
        role: Optional[str] = None
        org: Optional[str] = None
        if result["name"] and span:
            markers = _entity_markers(text)
            known_orgs = _answer_orgs(text)
            block = _entity_block(text, span[0], span[1], labelled, markers)
            role, org = _role_org_from(block, known_orgs)
            entities = {k for _, k in markers if k}
            entities.add(_normalize_text(result["name"]))
            if len(entities) <= 1:
                g_role, g_org = _role_org_from(text, known_orgs)
                role = role or g_role
                org = org or g_org
        else:
            role, org = _role_org_from(text, _answer_orgs(text))
        result["role"] = role
        result["org"] = org
        return result

    # Try answer text first
    result = extract_from_text(answer_text)
    if result["name"]:
        return result

    # Try assistant history messages (newest → oldest)
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            content = turn.get("content", "")
            result = extract_from_text(content)
            if result["name"]:
                return result

    return None


def merge_person_with_headline(person: Optional[dict], doc_text: str) -> Optional[dict]:
    """
    Merge person dict with headline data from doc_text.

    Headline is authoritative for role and org. Use extracted headline to fill missing fields
    or to upgrade generic roles with real ones from the document.

    Args:
        person: {"name": str, "role": str|None, "org": str|None} or None
        doc_text: Document text to extract headline from

    Returns:
        Merged person dict or None if no person
    """
    if not person or not person.get("name"):
        return person

    if not doc_text or len(doc_text.strip()) < 10:
        return person

    headline = extract_headline(doc_text)
    if not headline.get("role") and not headline.get("org"):
        return person

    # Copy person dict to avoid mutation
    merged = {**person}

    # Role merging logic:
    # 1. If headline has role and person role is missing or generic, use headline role
    # 2. If headline role appears in person role (substring, accent-insensitive), keep person role
    # 3. If person has real role, keep it (don't override with headline)
    if headline.get("role"):
        headline_role = headline["role"]
        person_role = merged.get("role") or ""

        # Check if person_role is generic
        is_person_generic = _is_generic_role(person_role) if person_role else True

        if is_person_generic:
            # Person has no role or a generic one; use headline
            merged["role"] = headline_role
        else:
            # Person has a real role; check if headline role is substring
            person_norm = _normalize_text(person_role)
            headline_norm = _normalize_text(headline_role)
            # If headline role is a substring of person role (or vice versa), it's consistent
            # Otherwise, keep person role as it came from the answer model
            if headline_norm not in person_norm and person_norm not in headline_norm:
                # They don't overlap; keep person role (answer model had context)
                pass

    # Organization: fill if missing, regardless of person role source
    if headline.get("org") and not merged.get("org"):
        merged["org"] = headline["org"]

    return merged


def build_queries(person: dict, targets: Any) -> list[str]:
    """
    Build search queries for a person.

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        targets: list of targets (["linkedin", "facebook"], ["web"]) — a single
            target string is accepted too

    Per platform: `site:<domain> "<name>"` plus `"<name>" <role or org> <Platform>`
    (the second only when role/org is known). For "web": `"<name>" <org or role>`
    and `"<name>"`. At most 3 targets and 6 queries.

    Returns:
        list of unique search queries (deduplicated)
    """
    name = (person.get("name") or "").strip()
    role = (person.get("role") or "").strip()
    org = (person.get("org") or "").strip()
    if role and _is_generic_role(role):
        role = ""
    if org and _is_bad_org(org):
        org = ""

    if not name:
        return []

    target_list = _normalize_targets(targets) or ["web"]

    queries = []
    for target in target_list:
        p = PLATFORMS.get(target)
        if p:
            site = p.get("site") or p["domains"][0]
            queries.append(f'site:{site} "{name}"')
            if role or org:
                term = p.get("query_term") or p["label"]
                queries.append(f'"{name}" {role or org} {term}')
        else:
            # Generic web queries
            query1_parts = [f'"{name}"']
            if org or role:
                query1_parts.append(org or role)
            queries.append(" ".join(query1_parts).strip())
            queries.append(f'"{name}"')

    # Deduplicate
    seen = set()
    unique = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique[:MAX_QUERIES]


def _query_target(query: str, targets: list[str]) -> Optional[str]:
    """Which requested target a search query is for: the platform whose site:
    domain or label/query term it mentions, else "web" (when requested), else None."""
    low = (query or "").lower()
    for t in targets:
        p = PLATFORMS.get(t)
        if not p:
            continue
        keys = [p.get("site") or "", *p.get("domains", []), p.get("query_term") or "", p.get("label") or ""]
        for k in keys:
            k = k.lower().strip()
            if k and re.search(rf"(?<![\w.]){re.escape(k)}(?![\w-])", low):
                return t
    return "web" if "web" in targets else None


def _plain_queries(person: dict, targets: list[str], planned: list[str]) -> list[str]:
    """Plain (non-`site:`) queries per target, used when `site:` searches are
    blocked: the planned `"<name>" <role/org> <Platform>` ones, plus
    `"<name>" <Platform>` for a platform that has none planned."""
    name = (person.get("name") or "").strip()
    out: list[str] = []
    if not name:
        return out
    plain = [q for q in planned if "site:" not in q.lower()]
    for t in targets:
        mine = [q for q in plain if _query_target(q, targets) == t]
        if not mine and t in PLATFORMS:
            p = PLATFORMS[t]
            mine = [f'"{name}" {p.get("query_term") or p["label"]}']
        for q in mine:
            if q not in out:
                out.append(q)
    return out


async def _rescue_blocked_searches(
    person: dict,
    targets: list[str],
    planned: list[str],
    *,
    executed: set,
    blocked_targets: set,
    ok_targets: set,
    budget: int,
    hooks: Any,
    search_tile: Any,
    all_results: dict,
    seen_urls: set,
    show_results: bool = True,
) -> dict:
    """A `site:` search that DuckDuckGo blocks often succeeds as a plain query.
    For every target whose searches were blocked and that has no successful
    search yet, run its not-yet-run plain queries (at most `budget` searches).
    Results are merged into all_results/seen_urls. Returns
    {"searches", "blocked", "ok", "raw"} counts."""
    stats = {"searches": 0, "blocked": 0, "ok": 0, "raw": 0}
    name = person.get("name", "")
    need = [t for t in targets if t in blocked_targets and t not in ok_targets]
    if not need or budget <= 0:
        return stats
    todo = [q for q in _plain_queries(person, targets, planned)
            if q not in executed and _query_target(q, targets) in need]
    if not todo:
        return stats
    search_tile.items.append({
        "kind": "note",
        "label": "site: search blocked — trying plain queries",
    })
    for query in todo:
        if stats["searches"] >= budget:
            break
        target = _query_target(query, targets)
        if target in ok_targets:
            continue
        executed.add(query)
        stats["searches"] += 1
        try:
            results = await hooks.web_search(query)
        except WebSearchUnavailable:
            raise
        except SearchBlocked as exc:
            _drain_search_notes(hooks, search_tile)
            stats["blocked"] += 1
            search_tile.items.append({
                "kind": "query",
                "label": query,
                "detail": _search_exc_detail(exc),
            })
            _note_search_skip(search_tile, exc)
            continue
        except Exception as e:
            _drain_search_notes(hooks, search_tile)
            log.debug(f"web_search failed: {e}")
            continue
        _drain_search_notes(hooks, search_tile)
        results = results or []
        stats["ok"] += 1
        stats["raw"] += len(results)
        if target:
            ok_targets.add(target)
        matching = [res for res in results if _accept_result(name, res)]
        search_tile.items.append({
            "kind": "query",
            "label": query,
            "detail": f"{len(matching)}/{len(results)} matching" if results else "no results",
        })
        for res in sorted(matching, key=lambda r: _rank(r.get("url", ""), targets)):
            url = res.get("url", "")
            if url and url not in all_results:
                all_results[url] = {
                    "title": res.get("title", ""),
                    "snippet": res.get("snippet", ""),
                    "name_matched": True,
                    "platform": platform_of(url),
                }
                seen_urls.add(url)
                if show_results:
                    search_tile.items.append(_result_item(url, res))
    return stats


def _hostname(url: str) -> str:
    """Lower-case hostname of a URL ('' when unparsable). Scheme-less URLs are accepted."""
    if not url:
        return ""
    from urllib.parse import urlparse
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        return (urlparse(u).hostname or "").lower()
    except Exception:
        return ""


def platform_of(url: str) -> Optional[str]:
    """Platform id for a URL by its domain ("linkedin", "facebook", …) or None."""
    host = _hostname(url)
    if not host:
        return None
    for pid, p in PLATFORMS.items():
        for domain in p["domains"]:
            if host == domain or host.endswith("." + domain):
                return pid
    return None


def is_profile_url(url: str, platform: Optional[str] = None) -> bool:
    """
    True when the URL looks like a personal profile on the platform: a profile
    pattern matches and no exclude pattern (posts, directories, search…) does.
    """
    if not url:
        return False
    pid = platform or platform_of(url)
    if not pid or pid not in PLATFORMS or platform_of(url) != pid:
        return False
    p = PLATFORMS[pid]
    full = url.strip().split("#", 1)[0]
    no_query = full.split("?", 1)[0]
    if any(re.search(pat, full, re.IGNORECASE) for pat in p["exclude_patterns"]):
        return False
    return any(
        re.search(pat, full, re.IGNORECASE) or re.search(pat, no_query, re.IGNORECASE)
        for pat in p["profile_patterns"]
    )


def is_linkedin_url(url: str) -> bool:
    """Check if URL is a LinkedIn domain."""
    return platform_of(url) == "linkedin"


def is_linkedin_profile(url: str) -> bool:
    """Check if URL is a LinkedIn profile."""
    return is_profile_url(url, "linkedin")


def _is_social_non_profile(url: str) -> bool:
    """A social-network URL that is not a profile (post, directory, search page…)."""
    pid = platform_of(url)
    return bool(pid) and not is_profile_url(url, pid)


def _normalize_text(text: str) -> str:
    """Normalize text: lowercase and remove accents."""
    if not text:
        return ""
    import unicodedata
    text = text.lower()
    # Remove accents: decompose and filter out combining marks
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text


def name_matches(person_name: str, text: str) -> bool:
    """
    Check if person's name appears in text (accent-insensitive, case-insensitive).

    Requires:
    - The surname (last token of name) appears in text
    - AND (first-name token appears OR text contains surname only once and
      first-name initial pattern "m." exists)

    For names with >2 tokens: require BOTH first and last name tokens.

    Args:
        person_name: Full name, e.g., "Michel Neuféglise"
        text: Text to search in (e.g., title, url, snippet)

    Returns:
        True if name matches, False otherwise
    """
    if not person_name or not text:
        return False

    name_tokens = person_name.split()
    if len(name_tokens) < 2:
        return False

    # Get first and last name tokens
    first_name = name_tokens[0]
    last_name = name_tokens[-1]

    # Normalize all tokens
    norm_text = _normalize_text(text)
    norm_first = _normalize_text(first_name)
    norm_last = _normalize_text(last_name)

    # Surname MUST appear in text
    if norm_last not in norm_text:
        return False

    # For >2 token names: require both first and last
    if len(name_tokens) > 2:
        return norm_first in norm_text and norm_last in norm_text

    # For 2 token names: first name OR first-name initial pattern
    if norm_first in norm_text:
        return True

    # Check for first-name initial pattern (e.g., "m." or "m ")
    initial_pattern = norm_first[0] + "."
    if initial_pattern in norm_text or (norm_first[0] + " " in norm_text):
        # Also verify surname appears and is not just a substring accident
        surname_count = norm_text.count(norm_last)
        if surname_count == 1:
            return True

    return False


def _strip_unseen_links(text: str, seen_urls: set[str]) -> str:
    """
    Remove markdown links to URLs that never appeared in tool results.

    Keeps link text, removes the link itself.
    """
    def replace_link(match):
        link_text = match.group(1)
        url = match.group(2)
        if url in seen_urls:
            return f"[{link_text}]({url})"
        return link_text

    # Match [text](url)
    return re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', replace_link, text)


# ── Web lookup verdict building ───────────────────────────────────────────

VERDICT_STATES = ("Likely match found", "Possible match", "No match found")
CAVEAT = "_Search results are not proof of identity._"


def _accept_result(name: str, res: dict) -> bool:
    """A search result is kept when it names the person and is not a social
    non-profile page (post, directory listing, search page…)."""
    url = res.get("url", "") or ""
    if not url:
        return False
    if _is_social_non_profile(url):
        return False
    return name_matches(name, f"{res.get('title', '')} {url} {res.get('snippet', '')}")


def _rank(url: str, targets: list[str]) -> tuple[int, int]:
    """Sort key: requested-platform profiles (target order), other profiles, web pages."""
    pid = platform_of(url)
    if pid and is_profile_url(url, pid):
        if pid in targets:
            return (0, targets.index(pid))
        return (1, 0)
    return (2, 0)


def _order_urls(urls: list[str], targets: list[str]) -> list[str]:
    """Stable ordering of URLs by _rank."""
    return sorted(urls, key=lambda u: _rank(u, targets))


def _result_item(url: str, res: dict) -> dict:
    """Tile item for a search result (carries its platform, None for generic pages)."""
    return {
        "kind": "result",
        "label": res.get("title", "") or url,
        "url": url,
        "detail": (res.get("snippet", "") or "")[:160],
        "platform": platform_of(url),
    }


def _build_candidates(all_results: dict, targets: Any = None) -> list[dict]:
    """
    Build a list of candidates from all_results.

    Returns unique name-matching results ordered: profiles of the requested
    platforms first (in target order), then other profiles, then web pages; max 8.
    Format: [{"title", "url", "snippet", "platform", "is_profile"}, ...]
    """
    target_list = _normalize_targets(targets) or ["linkedin"]
    candidates = []
    urls = [
        u for u in all_results
        # Only real web links (never DuckDuckGo "ref://…" short links) and no social non-profiles
        if (u or "").strip().lower().startswith(("http://", "https://")) and not _is_social_non_profile(u)
    ]
    for url in _order_urls(urls, target_list)[:8]:
        res = all_results[url]
        pid = platform_of(url)
        candidates.append({
            "title": res.get("title", ""),
            "url": url,
            "snippet": res.get("snippet", ""),
            "platform": pid,
            "is_profile": bool(pid) and is_profile_url(url, pid),
        })
    return candidates


def _no_match_verdict(targets: list[str]) -> str:
    """Per-platform "No match found" verdict when no candidate matched (no LLM call)."""
    lines = []
    for t in targets or ["web"]:
        p = PLATFORMS.get(t)
        if p:
            lines.append(
                f"**{p['label']}:** No match found — no {p['label']} profiles with this exact name appeared in the search results."
            )
        else:
            lines.append("**Web:** No match found — no pages with this exact name appeared in the search results.")
    lines.append("**Overall:** No match found.")
    return "\n".join(lines) + "\n\n" + CAVEAT


def _build_verdict_prompt(person: dict, doc_excerpt: str, candidates: list[dict], targets: Any = None) -> str:
    """
    Build verdict prompt with person dict, document excerpt, and candidates.

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        doc_excerpt: First ~1500 chars of document
        candidates: List of {"title", "url", "snippet", "platform", "is_profile"}
        targets: requested platforms (["linkedin", "facebook"], ["web"])

    Returns:
        Formatted verdict prompt asking for one verdict line per platform
    """
    target_list = _normalize_targets(targets) or ["linkedin"]
    name = person.get("name", "")
    role = person.get("role", "")
    org = person.get("org", "")

    # Build person section
    person_section = f"**Person from document:**\n- Name: {name}"
    if role:
        person_section += f"\n- Role: {role}"
    if org:
        person_section += f"\n- Organization: {org}"

    # Build document excerpt section
    doc_section = ""
    if doc_excerpt:
        doc_section = f"\n\n**Document excerpt (first ~1500 chars):**\n{doc_excerpt}"

    # Build candidates section
    candidates_section = "\n\n**Search results (numbered candidates):**\n"
    for i, cand in enumerate(candidates, 1):
        pid = cand.get("platform") if "platform" in cand else platform_of(cand.get("url", ""))
        is_prof = cand.get("is_profile") if "is_profile" in cand else is_profile_url(cand.get("url", ""))
        kind = f"{platform_label(pid)} profile" if pid and is_prof else (f"{platform_label(pid)} page" if pid else "Web page")
        candidates_section += f"{i}. ({kind}) [{cand.get('title', 'Unknown')}]({cand.get('url', '')})\n"
        if cand.get('snippet'):
            candidates_section += f"   {cand.get('snippet', '')}\n"

    labels = [platform_label(t) for t in target_list]
    example_lines = "\n".join(
        f"   **{label}:** Likely match found — <short reason> [title](url)" if i == 0
        else f"   **{label}:** No match found — <short reason>"
        for i, label in enumerate(labels)
    )

    prompt = f"""{person_section}{doc_section}{candidates_section}

**Requested platforms:** {", ".join(labels)}

**Your task:**
Compare each candidate's headline/snippet with the document excerpt (if provided). If several candidates share the name:
- Identify which one matches the document (same role, employer, location, education)
- Note which ones appear to be different people

Output format:
1. One verdict line per requested platform, in this order, each starting with the bold platform label, e.g.:
{example_lines}
   After the label use exactly one of "Likely match found", "Possible match" or "No match found", then a short reason.
   On a platform's line cite only that platform's candidate (as [title](url)); if none of its candidates match, say "No match found".

2. Then one line: "**Overall:** <one-line summary>"

3. Then bullet points with each candidate:
   - `[title](url) — why it matches / doesn't`

4. Last line: "{CAVEAT}"

Refer to people by their name; do not assume gender or use he/she.
Only use URLs that appear above. Do not fabricate or change URLs."""

    return prompt


async def _generate_verdict(
    person: dict,
    targets: list[str],
    all_results: dict,
    seen_urls: set[str],
    hooks: Any,
    *,
    answer_model: str,
    doc_excerpt: str,
) -> tuple[str, list[dict]]:
    """Produce the verdict text (LLM, or a canned no-match text) and the candidates."""
    candidates = _build_candidates(all_results, targets)
    verdict_text = ""
    if not candidates:
        # No matches: emit verdict without LLM call
        verdict_text = _no_match_verdict(targets)
    else:
        verdict_prompt = _build_verdict_prompt(person, doc_excerpt, candidates, targets)
        try:
            async for event in hooks.stream_llm(answer_model, verdict_prompt, think=False):
                if "content" in event:
                    verdict_text += event["content"]
                elif event.get("done"):
                    break
        except Exception as e:
            log.debug(f"Verdict LLM failed: {e}")
            verdict_text = f"Verdict generation failed: {str(e)[:100]}"
    verdict_text = _strip_unseen_links(verdict_text, seen_urls)
    return verdict_text, candidates


def _search_unavailable_verdict(targets: list[str]) -> str:
    """Per-platform "Search unavailable" text — never a negative verdict."""
    lines = [
        f"**{platform_label(t)}:** {SEARCH_UNAVAILABLE_STATE} — DuckDuckGo is temporarily blocking automated searches."
        for t in (targets or ["web"])
    ]
    lines.append(f"**Overall:** {SEARCH_UNAVAILABLE_STATE}.")
    return (
        "\n".join(lines)
        + "\n\n_Try again in a few minutes, or enable **Brave Search** in Settings → Tools._"
    )


async def _emit_search_unavailable(
    person: dict, targets: list[str], search_tile: Any, n_searches: int,
) -> AsyncIterator[dict]:
    """Every search was blocked (bot detection) and none returned results: say
    so honestly — error tile, "Search unavailable" per platform, no verdict."""
    text = _search_unavailable_verdict(targets)
    yield {
        "_web_result": {
            "person": person,
            "targets": targets,
            "candidates": [],
            "verdict": "",
            "unavailable": True,
        }
    }
    yield {"content": "\n\n---\n\n### Online verification\n\n" + text}
    search_tile.status = "error"
    search_tile.detail = SEARCH_BLOCKED_DETAIL
    search_tile.items.append({
        "kind": "note",
        "label": f"{n_searches} search(es) blocked by DuckDuckGo bot detection — no verdict",
    })
    search_tile.output_preview = text[:300]
    if getattr(search_tile, "started_ms", None):
        search_tile.ms = int(time.time() * 1000) - search_tile.started_ms
    yield {"tile": search_tile.to_dict()}


# ── Search robustness helpers (early stop, pacing/cool-down notes, cache) ──


def _search_exc_detail(exc: BaseException) -> str:
    """Tile detail for a search that did not really run."""
    if isinstance(exc, SearchSkipped):
        return f"skipped (DuckDuckGo cool-down, {exc.remaining:.0f} s left)"
    return "blocked (DuckDuckGo bot detection)"


def _note_search_skip(search_tile: Any, exc: BaseException) -> None:
    """Once per run: explain that DuckDuckGo is in its cool-down window."""
    if not isinstance(exc, SearchSkipped):
        return
    if any(str(it.get("label", "")).startswith(COOLDOWN_NOTE_PREFIX) for it in search_tile.items):
        return
    search_tile.items.append({
        "kind": "note",
        "label": f"{COOLDOWN_NOTE_PREFIX}: {exc.remaining:.0f} s left — skipped live search",
    })


def _drain_search_notes(hooks: Any, search_tile: Any) -> None:
    """Fold the searcher's pacing events into ONE running tile note."""
    drain = getattr(hooks, "search_notes", None)
    if not drain:
        return
    try:
        events = drain() or []
    except Exception:
        return
    waited = sum(float(e.get("seconds") or 0) for e in events if e.get("kind") == "paced")
    if waited <= 0:
        return
    item = next(
        (it for it in search_tile.items if str(it.get("label", "")).startswith(PACING_NOTE_PREFIX)), None
    )
    prev = 0.0
    if item:
        m = re.search(r"waited ([\d.]+) s", str(item.get("label", "")))
        prev = float(m.group(1)) if m else 0.0
    total = waited + prev
    label = (
        f"{PACING_NOTE_PREFIX} waited {total:.1f} s in total — DuckDuckGo queries are spaced "
        f"{DDG_MIN_INTERVAL_S:.0f} s apart"
    )
    if item:
        item["label"] = label
    else:
        search_tile.items.append({"kind": "note", "label": label})


def _profiles_in(results: list, name: str, targets: list[str], q_target: Optional[str]) -> set:
    """Requested platforms for which these search results contain a name-matching
    PROFILE. A profile only counts for the platform the query was aimed at (or for
    any platform when the query had no specific platform)."""
    found = set()
    for res in results or []:
        url = (res.get("url") or "").strip()
        pid = platform_of(url)
        if not pid or pid not in targets or not is_profile_url(url, pid):
            continue
        if q_target in PLATFORMS and q_target != pid:
            continue
        if name_matches(name, f"{res.get('title', '')} {url} {res.get('snippet', '')}"):
            found.add(pid)
    return found


def _all_profiles_found(found: set, targets: list[str]) -> bool:
    """True when every requested target is a social platform with a matching profile
    found — further searches can't improve the verdict ("web" never qualifies)."""
    if not targets or any(t not in PLATFORMS for t in targets):
        return False
    return set(targets) <= set(found)


def _note_early_stop(search_tile: Any) -> None:
    if not any(it.get("label") == EARLY_STOP_NOTE for it in search_tile.items):
        search_tile.items.append({"kind": "note", "label": EARLY_STOP_NOTE})


def _format_verified_date(value: Any) -> str:
    """"2026-09-28" / ISO datetime / epoch seconds → "28 Sep 2026" ('' when unknown)."""
    import datetime as _dt
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            d = _dt.datetime.fromtimestamp(float(value)).date()
        else:
            d = _dt.date.fromisoformat(str(value).strip()[:10])
    except Exception:
        return str(value)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{d.day} {months[d.month - 1]} {d.year}"


async def _known_profiles_by_target(person: dict, targets: list[str], hooks: Any) -> dict:
    """{target: best stored profile} from the knowledge store (likely_profile before
    candidate_profile, then the most recent) — only for social platforms."""
    fn = getattr(hooks, "kg_known_profiles", None)
    name = (person or {}).get("name", "")
    if not fn or not name:
        return {}
    try:
        rows = await fn(name) or []
    except Exception as e:
        log.debug(f"kg_known_profiles failed: {e}")
        return {}
    best: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        pid = row.get("platform") if row.get("platform") in PLATFORMS else platform_of(url)
        if pid not in targets or pid not in PLATFORMS or not is_profile_url(url, pid):
            continue
        rel = row.get("relation") or "candidate_profile"
        key = (1 if rel == "likely_profile" else 0, str(row.get("verified_at") or ""))
        cur = best.get(pid)
        if cur is None or key > cur[0]:
            best[pid] = (key, {**row, "url": url, "platform": pid})
    return {pid: v[1] for pid, v in best.items()}


def _cached_verdict_line(target: str, prof: dict) -> str:
    label = platform_label(target)
    title = (prof.get("title") or prof.get("url") or "").replace("[", "(").replace("]", ")")
    date = _format_verified_date(prof.get("verified_at"))
    on = f" on {date}" if date else ""
    if prof.get("relation") == "likely_profile":
        return (
            f"**{label}:** Live search is blocked right now — previously verified{on}: "
            f"[{title}]({prof['url']}) (likely match, from the knowledge store)."
        )
    return (
        f"**{label}:** Live search is blocked right now — previously found{on}: "
        f"[{title}]({prof['url']}) (possible match, from the knowledge store)."
    )


async def _emit_blocked_outcome(
    person: dict, targets: list[str], search_tile: Any, n_searches: int, hooks: Any,
) -> AsyncIterator[dict]:
    """Every live search was blocked/skipped. If the knowledge store holds an
    earlier verification for a requested platform, report THAT (clearly marked as
    earlier, not live); otherwise "Search unavailable" (never "no match")."""
    cached = await _known_profiles_by_target(person, targets, hooks)
    if not cached:
        async for ev in _emit_search_unavailable(person, targets, search_tile, n_searches):
            yield ev
        return

    lines = []
    for t in targets or ["web"]:
        prof = cached.get(t)
        if prof:
            lines.append(_cached_verdict_line(t, prof))
        else:
            lines.append(
                f"**{platform_label(t)}:** {SEARCH_UNAVAILABLE_STATE} — DuckDuckGo is temporarily "
                "blocking automated searches."
            )
    lines.append(
        "**Overall:** Live search is unavailable right now — showing an earlier verification "
        "from the knowledge store (not a live result)."
    )
    text = (
        "\n".join(lines)
        + f"\n\n{CAVEAT}\n\n_Try again in a few minutes for a live check, or enable **Brave Search** in Settings → Tools._"
    )
    # Not a live result: nothing new for kg_ingest (keeps verified_at honest)
    yield {
        "_web_result": {
            "person": person,
            "targets": targets,
            "candidates": [],
            "verdict": "",
            "unavailable": True,
            "cached": True,
        }
    }
    yield {"content": "\n\n---\n\n### Online verification\n\n" + text}
    search_tile.status = "done"
    search_tile.detail = "Live search blocked — used earlier verification"
    search_tile.items.append({
        "kind": "note",
        "label": f"{n_searches} live search(es) blocked by DuckDuckGo",
    })
    for t in targets:
        prof = cached.get(t)
        if not prof:
            continue
        date = _format_verified_date(prof.get("verified_at")) or "an earlier run"
        search_tile.items.append({
            "kind": "note",
            "label": f"Used knowledge-store verification from {date}",
        })
        search_tile.items.append({
            "kind": "result",
            "label": prof.get("title") or prof["url"],
            "url": prof["url"],
            "detail": (
                ("Likely match" if prof.get("relation") == "likely_profile" else "Possible match")
                + (f" · verified {date}" if prof.get("verified_at") else "")
                + " · from the knowledge store"
            ),
            "platform": t,
        })
    search_tile.output_preview = text[:300]
    if getattr(search_tile, "started_ms", None):
        search_tile.ms = int(time.time() * 1000) - search_tile.started_ms
    yield {"tile": search_tile.to_dict()}


# ── Web lookup execution ──────────────────────────────────────────────────


async def run_web_lookup(
    person: dict,
    targets: Any,
    hooks: Any,  # AgentHooks
    *,
    answer_model: str,
    now_ms: int,
    doc_excerpt: str = "",
    doc_text: str = "",
) -> AsyncIterator[dict]:
    """
    Run web lookup for a person.

    Yields events in the same format as run_agent:
    - {"tile": {...}} for tile state changes
    - {"content": "..."} for text content (final verdict)
    - {"_web_result": {...}} internal event (person, targets, candidates, verdict)
    - Handles tool calls, fallback paths, and error cases

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        targets: ["linkedin", "facebook", ...] | ["web"] (a single string is accepted)
        hooks: AgentHooks with web_search, fetch_page, chat_tools, etc.
        answer_model: Model name for fallback LLM verdict
        now_ms: Start time in ms
        doc_excerpt: First ~1500 chars of subject document text (for headline extraction) [DEPRECATED]
        doc_text: Full subject document text (for headline extraction)

    Yields:
        Event dicts: {"tile": ...}, {"content": ...}
    """
    from doc_agent import Tile  # Import here to avoid circular dependency

    target_list = _normalize_targets(targets) or ["web"]

    # Merge headline data (role/org from document text)
    # Prefer full doc_text over excerpt; if only excerpt provided, use it
    text_for_headline = doc_text if doc_text else doc_excerpt
    if text_for_headline:
        person = merge_person_with_headline(person, text_for_headline)

    # Build queries
    queries = build_queries(person, target_list)
    name = person.get("name", "")
    role = person.get("role", "")
    org = person.get("org", "")

    # Create query planner tile
    plan_tile = Tile(
        id="web-plan",
        kind="planner",
        title="Query planner",
        status="done",
        model=None,
        detail=(
            f"Person: {name}" + (f" · {role}" if role else "") + (f" · {org}" if org else "")
            + f" → {targets_label(target_list)}"
        ),
        items=[{"kind": "query", "label": q} for q in queries],
    )
    yield {"tile": plan_tile.to_dict()}

    # Create main web-search tile
    social = [t for t in target_list if t in PLATFORMS]
    search_tile = Tile(
        id="web-search",
        kind="web",
        title="Web lookup" + (f" · {targets_label(social)}" if social else ""),
        status="running",
        model=None,
        started_ms=int(time.time() * 1000),
    )

    # Try to get tool model
    picked = None
    if hooks.pick_tool_model:
        try:
            picked = await hooks.pick_tool_model()
        except Exception as e:
            log.debug(f"pick_tool_model failed: {e}")

    # Normalize picked result: dict or str or None
    if isinstance(picked, dict):
        tool_model = picked["model"]
        source = picked.get("source", "auto")
        note = picked.get("note")
    elif isinstance(picked, str):
        # Backward compat: treat str as auto-selected
        tool_model = picked
        source = "auto"
        note = None
    else:
        tool_model = None
        source = None
        note = None

    # Set model field and add source/note items
    search_tile.model = tool_model or "direct search"

    # Add tool model source note as first item
    if tool_model:
        source_label = "from Settings" if source == "settings" else "auto-selected"
        search_tile.items.append({
            "kind": "note",
            "label": f"Tool model: {tool_model} ({source_label})"
        })
    elif tool_model is None and source is not None:
        # No model found, but we attempted selection
        search_tile.items.append({
            "kind": "note",
            "label": "No tool-capable model found — using direct search"
        })

    # If there's a note about configuration issues, add it as a second item
    if note:
        search_tile.items.append({
            "kind": "note",
            "label": note
        })

    yield {"tile": search_tile.to_dict()}

    # Check if web_search is available
    if not hooks.web_search:
        search_tile.status = "error"
        search_tile.detail = "Web search is not available — enable DuckDuckGo search in Settings → Tools"
        yield {"tile": search_tile.to_dict()}
        yield {"content": "\n\n---\n\n### Online verification\n\nWeb search is not available — enable **DuckDuckGo search** in Settings → Tools."}
        return

    # Tool path: use tool-capable model if available
    if tool_model and hooks.chat_tools:
        try:
            async for event in _tool_path(
                tool_model, queries, person, target_list, search_tile, hooks,
                answer_model=answer_model, doc_excerpt=doc_excerpt
            ):
                yield event
            return
        except WebSearchUnavailable:
            # Propagate WebSearchUnavailable - don't catch it here
            raise
        except Exception as e:
            log.debug(f"Tool path failed: {e}")
            # Add error note and fall through to fallback
            search_tile.items.append({
                "kind": "note",
                "label": f"Tool model failed ({str(e)[:50]}) — falling back to direct search",
            })

    # Fallback path: direct search + LLM verdict
    try:
        async for event in _fallback_path(
            queries, person, target_list, search_tile, answer_model, hooks,
            doc_excerpt=doc_excerpt
        ):
            yield event
    except WebSearchUnavailable:
        # Web search unavailable - show settings message
        search_tile.status = "error"
        search_tile.detail = "Web search is not available — enable DuckDuckGo search in Settings → Tools"
        yield {"tile": search_tile.to_dict()}
        yield {"content": "\n\n---\n\n### Online verification\n\nWeb search is not available — enable **DuckDuckGo search** in Settings → Tools."}


async def _tool_path(
    model: str,
    queries: list[str],
    person: dict,
    targets: list[str],
    search_tile: Tile,
    hooks: Any,
    *,
    answer_model: str,
    doc_excerpt: str = "",
) -> AsyncIterator[dict]:
    """Execute tool-based web lookup with query rewriting and name filtering."""
    targets = _normalize_targets(targets) or ["web"]
    name = person.get("name", "")
    role = person.get("role", "")
    org = person.get("org", "")

    # Build initial message
    user_msg = f"Person from the document: name={name}"
    if role:
        user_msg += f"; role={role}"
    if org:
        user_msg += f"; organization={org}"
    user_msg += f". Targets: {targets_label(targets, ', ')}."
    if queries:
        user_msg += " Suggested queries: " + " | ".join(queries)

    messages = [
        {"role": "system", "content": _verify_system_prompt(targets)},
        {"role": "user", "content": user_msg},
    ]

    all_results = {}  # url -> result dict (with name_matching info)
    seen_urls = set()
    search_urls: set = set()  # every URL web_search returned in THIS run (fetch allow-list)
    search_count = 0  # Track web_search calls (capped)
    blocked_count = 0  # searches answered with the bot-detection notice
    ok_count = 0  # searches that really ran (not blocked, no error)
    raw_total = 0  # raw results over all searches (before name filtering)
    max_searches = min(MAX_QUERIES, max(4, 2 * len(targets)))
    executed: set = set()  # queries actually sent this run
    blocked_targets: set = set()  # targets with a blocked search
    ok_targets: set = set()  # targets with a search that really ran
    found_platforms: set = set()  # targets with a matching profile found (early stop)

    # Tool loop (max 3 rounds)
    for round_num in range(3):
        try:
            resp = await hooks.chat_tools(model, messages, TOOLS)
        except Exception as e:
            log.debug(f"chat_tools failed: {e}")
            raise

        # Append assistant message
        assistant_msg = {
            "role": "assistant",
            "content": resp.get("content", ""),
            "tool_calls": [
                {
                    "function": {
                        "name": tc["name"],
                        "arguments": tc.get("arguments", {}),
                    }
                }
                for tc in resp.get("tool_calls", [])
            ] if resp.get("tool_calls") else [],
        }
        messages.append(assistant_msg)

        # Process tool calls
        if not resp.get("tool_calls"):
            # No more tool calls; move to verdict
            break

        # Handle tool calls
        for tc in resp.get("tool_calls", []):
            tool_name = tc["name"]
            arguments = tc.get("arguments", {})

            if tool_name == "web_search":
                query = arguments.get("query", "")
                if not query:
                    continue

                # Early stop: a matching profile is already known for every
                # requested platform — don't spend (DuckDuckGo) searches on more
                if _all_profiles_found(found_platforms, targets):
                    _note_early_stop(search_tile)
                    search_tile.items.append({
                        "kind": "query",
                        "label": query,
                        "detail": "Skipped (profile already found)",
                    })
                    messages.append({
                        "role": "tool",
                        "content": ENOUGH_RESULTS_MSG,
                    })
                    yield {"tile": search_tile.to_dict()}
                    continue

                # Check if we've hit the cap
                if search_count >= max_searches:
                    # Add budget exhausted message
                    search_tile.items.append({
                        "kind": "query",
                        "label": query,
                        "detail": "Skipped (search budget exhausted)",
                    })
                    tool_content = "Search budget exhausted — give your final answer now."
                    messages.append({
                        "role": "tool",
                        "content": tool_content,
                    })
                    continue

                search_count += 1

                # Rewrite query if needed to stay on person
                query_rewritten = False
                if name:
                    # Check if query contains the surname (accent-insensitive)
                    surname = name.split()[-1] if name.split() else ""
                    if surname and _normalize_text(surname) not in _normalize_text(query):
                        # Rewrite to include the name
                        # Remove any quoted names that aren't our target
                        query = re.sub(r'"[^"]*"', '', query).strip()
                        query = f'"{name}" {query}'.strip()
                        query_rewritten = True

                blocked = False
                errored = False
                executed.add(query)
                q_target = _query_target(query, targets)
                blocked_exc: Optional[BaseException] = None
                try:
                    results = await hooks.web_search(query)
                except WebSearchUnavailable:
                    # Propagate to caller - must not swallow this
                    raise
                except SearchBlocked as exc:
                    results = []
                    blocked = True
                    blocked_exc = exc
                    blocked_count += 1
                    blocked_targets.update([q_target] if q_target else targets)
                except Exception as e:
                    results = []
                    errored = True
                    log.debug(f"web_search failed: {e}")
                _drain_search_notes(hooks, search_tile)
                if not blocked and not errored:
                    ok_count += 1
                    if q_target:
                        ok_targets.add(q_target)
                results = results or []
                raw_total += len(results)

                if blocked:
                    search_tile.items.append({
                        "kind": "query",
                        "label": query,
                        "detail": _search_exc_detail(blocked_exc),
                    })
                    _note_search_skip(search_tile, blocked_exc)
                    messages.append({
                        "role": "tool",
                        "content": "Search unavailable: DuckDuckGo is blocking automated searches right now. "
                                   "Do not conclude that the person doesn't exist.",
                    })
                    yield {"tile": search_tile.to_dict()}
                    continue

                for res in results or []:
                    if res.get("url"):
                        search_urls.add(res["url"])

                # Filter results by name matching (drops social posts/directories)
                matching_results = [res for res in results if _accept_result(name, res)]
                found_platforms |= _profiles_in(matching_results, name, targets, q_target)

                total_results = len(results)
                matching_count = len(matching_results)

                # Requested-platform profiles first, then other profiles, then web pages
                sorted_results = sorted(
                    matching_results, key=lambda r: _rank(r.get("url", ""), targets)
                )

                # Add query item to tile with detail
                detail_str = f"{matching_count}/{total_results} matching"
                if query_rewritten:
                    detail_str += " (rewritten)"
                search_tile.items.append({
                    "kind": "query",
                    "label": query,
                    "detail": detail_str,
                })

                # Add matching results to tile
                for res in sorted_results[:6]:  # Limit display to 6
                    url = res.get("url", "")
                    seen_urls.add(url)
                    if url not in all_results:
                        all_results[url] = {
                            "title": res.get("title", ""),
                            "snippet": res.get("snippet", ""),
                            "name_matched": True,
                            "platform": platform_of(url),
                        }
                    search_tile.items.append(_result_item(url, res))

                # Tool message for LLM (only matching results)
                tool_content = "\n".join(
                    f"{i+1}. [{platform_label(platform_of(r.get('url', ''))) if platform_of(r.get('url', '')) else 'Web'}] "
                    f"{r.get('title', 'No title')} — {r.get('url', 'No URL')} — {r.get('snippet', '')[:100]}"
                    for i, r in enumerate(sorted_results)
                ) or "No matching results."

                messages.append({
                    "role": "tool",
                    "content": tool_content,
                })

                yield {"tile": search_tile.to_dict()}

            elif tool_name == "fetch_page":
                url = arguments.get("url", "")
                if not url:
                    continue

                # Social networks are never fetched (login walls); everything
                # else must pass the allow-list / private-address guard.
                pid = platform_of(url)
                refusal = None if pid else await check_fetch_url(url, search_urls)
                if refusal:
                    tool_content = refusal
                    search_tile.items.append({
                        "kind": "note",
                        "label": f"{refusal}: {url[:120]}",
                    })
                elif pid:
                    label = platform_label(pid)
                    tool_content = f"{label} pages require login; use the search snippets instead."
                    search_tile.items.append({
                        "kind": "note",
                        "label": f"Skipped {url} ({label} requires login)",
                    })
                else:
                    try:
                        content = await hooks.fetch_page(url)
                        tool_content = content[:4000]
                        search_tile.items.append({
                            "kind": "note",
                            "label": f"Fetched {url}",
                        })
                    except Exception as e:
                        tool_content = f"Fetch failed: {str(e)[:100]}"
                        search_tile.items.append({
                            "kind": "note",
                            "label": f"Fetch failed: {url}",
                        })

                messages.append({
                    "role": "tool",
                    "content": tool_content,
                })

                yield {"tile": search_tile.to_dict()}

    # site: searches blocked → try the plain queries for those platforms
    if blocked_count:
        rescue = await _rescue_blocked_searches(
            person, targets, queries, executed=executed, blocked_targets=blocked_targets,
            ok_targets=ok_targets, budget=MAX_QUERIES - search_count, hooks=hooks,
            search_tile=search_tile, all_results=all_results, seen_urls=seen_urls,
        )
        search_count += rescue["searches"]
        blocked_count += rescue["blocked"]
        ok_count += rescue["ok"]
        raw_total += rescue["raw"]
        if rescue["searches"]:
            yield {"tile": search_tile.to_dict()}

    # Every search (site: and plain) blocked → "search unavailable", never "no match"
    if blocked_count and ok_count == 0 and raw_total == 0:
        async for ev in _emit_blocked_outcome(person, targets, search_tile, blocked_count, hooks):
            yield ev
        return

    # Generate dedicated verdict from matching results
    verdict_text, candidates = await _generate_verdict(
        person, targets, all_results, seen_urls, hooks,
        answer_model=answer_model, doc_excerpt=doc_excerpt,
    )

    # Emit internal _web_result event for kg_ingest (before yielding content)
    yield {
        "_web_result": {
            "person": person,
            "targets": targets,
            "candidates": candidates,
            "verdict": verdict_text,
        }
    }

    yield {"content": "\n\n---\n\n### Online verification\n\n" + verdict_text}

    search_tile.status = "done"
    search_tile.detail = (
        f"{search_count} search(es), {len(all_results)} matching result(s) · model used tools"
    )
    search_tile.items.append({
        "kind": "note",
        "label": f"Verdict by {answer_model}",
    })
    search_tile.output_preview = verdict_text[:300]
    search_tile.ms = int(time.time() * 1000) - search_tile.started_ms
    yield {"tile": search_tile.to_dict()}


async def _fallback_path(
    queries: list[str],
    person: dict,
    targets: list[str],
    search_tile: Tile,
    answer_model: str,
    hooks: Any,
    *,
    doc_excerpt: str = "",
) -> AsyncIterator[dict]:
    """Execute fallback direct search + LLM verdict."""
    targets = _normalize_targets(targets) or ["web"]
    name = person.get("name", "")
    all_results = {}  # url -> result dict
    seen_urls = set()

    blocked_count = 0  # searches answered with the bot-detection notice
    ok_count = 0  # searches that really ran (not blocked, no error)
    raw_total = 0  # raw results over all searches (before name filtering)
    executed: set = set()
    blocked_targets: set = set()
    ok_targets: set = set()

    skipped_early = 0  # queries not run because the platform's profile was already found
    found_platforms: set = set()  # targets with a matching profile found (early stop)

    # Perform searches
    for query in queries:
        q_target = _query_target(query, targets)
        # Early stop: this platform already has a matching profile — skip its
        # remaining queries (saves DuckDuckGo searches, avoids its bot detection)
        if q_target in PLATFORMS and q_target in found_platforms:
            skipped_early += 1
            _note_early_stop(search_tile)
            search_tile.items.append({
                "kind": "query",
                "label": query,
                "detail": "Skipped (profile already found)",
            })
            continue
        executed.add(query)
        errored = False
        try:
            results = await hooks.web_search(query)
        except WebSearchUnavailable:
            # Propagate to caller - must not swallow this
            raise
        except SearchBlocked as exc:
            _drain_search_notes(hooks, search_tile)
            blocked_count += 1
            blocked_targets.update([q_target] if q_target else targets)
            search_tile.items.append({
                "kind": "query",
                "label": query,
                "detail": _search_exc_detail(exc),
            })
            _note_search_skip(search_tile, exc)
            continue
        except Exception as e:
            log.debug(f"web_search failed: {e}")
            results = []
            errored = True
        _drain_search_notes(hooks, search_tile)
        if not errored:
            ok_count += 1
            if q_target:
                ok_targets.add(q_target)
        results = results or []
        raw_total += len(results)

        # Filter results by name matching (drops social posts/directories)
        matching_results = [res for res in results if _accept_result(name, res)]
        found_platforms |= _profiles_in(matching_results, name, targets, q_target)

        # Add query item to tile
        search_tile.items.append({
            "kind": "query",
            "label": query,
            "detail": f"{len(matching_results)}/{len(results)} matching" if len(results) > 0 else "no results",
        })

        # Collect unique matching results by URL
        for res in matching_results:
            url = res.get("url", "")
            if url and url not in all_results:
                all_results[url] = {
                    "title": res.get("title", ""),
                    "snippet": res.get("snippet", ""),
                    "platform": platform_of(url),
                }
                seen_urls.add(url)

    # site: searches blocked → try the plain queries for those platforms
    if blocked_count:
        rescue = await _rescue_blocked_searches(
            person, targets, queries, executed=executed, blocked_targets=blocked_targets,
            ok_targets=ok_targets, budget=MAX_QUERIES - len(executed), hooks=hooks,
            search_tile=search_tile, all_results=all_results, seen_urls=seen_urls,
            show_results=False,  # listed with all results below
        )
        blocked_count += rescue["blocked"]
        ok_count += rescue["ok"]
        raw_total += rescue["raw"]

    # Every search (site: and plain) blocked → "search unavailable", never "no match"
    if blocked_count and ok_count == 0 and raw_total == 0:
        async for ev in _emit_blocked_outcome(person, targets, search_tile, blocked_count, hooks):
            yield ev
        return

    # Format results for display: requested-platform profiles first
    sorted_urls = _order_urls(list(all_results.keys()), targets)
    result_items = [_result_item(url, all_results[url]) for url in sorted_urls[:10]]  # Limit to 10
    search_tile.items.extend(result_items)

    # Generate dedicated verdict from matching results
    verdict_text, candidates = await _generate_verdict(
        person, targets, all_results, seen_urls, hooks,
        answer_model=answer_model, doc_excerpt=doc_excerpt,
    )

    # Emit internal _web_result event for kg_ingest (before yielding content)
    yield {
        "_web_result": {
            "person": person,
            "targets": targets,
            "candidates": candidates,
            "verdict": verdict_text,
        }
    }

    yield {"content": "\n\n---\n\n### Online verification\n\n" + verdict_text}

    search_tile.status = "done"
    search_tile.detail = (
        f"{len(queries) - skipped_early} search(es), {len(all_results)} matching result(s) · direct search"
    )
    search_tile.items.append({
        "kind": "note",
        "label": f"Verdict by {answer_model}",
    })
    search_tile.output_preview = verdict_text[:300]
    search_tile.ms = int(time.time() * 1000) - search_tile.started_ms
    yield {"tile": search_tile.to_dict()}
