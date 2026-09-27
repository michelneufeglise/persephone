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


DDG_SERVER = "duckduckgo-search"
BRAVE_SERVER = "brave-search"


class WebSearcher:
    """
    Per-run web search over the DuckDuckGo / Brave MCP servers.

    - DuckDuckGo first. When it answers with its bot-detection notice, retry once
      (after `retry_delay` seconds, first time in the run only); if still
      blocked, fall back to Brave Search when that server is running.
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
    ) -> None:
        self._call = call
        self._running = running
        self._parse = parse
        self.retry_delay = retry_delay
        self._sleep = sleep or asyncio.sleep
        self._retried = False
        self.blocked_queries = 0

    async def __call__(self, query: str) -> list[dict]:
        running = list(self._running() or [])
        if DDG_SERVER not in running and BRAVE_SERVER not in running:
            raise WebSearchUnavailable("No web search MCP server running")
        blocked_text = None
        if DDG_SERVER in running:
            try:
                text = await self._call(DDG_SERVER, query)
                if is_blocked_notice(text) and not self._retried:
                    self._retried = True
                    log.info("DuckDuckGo returned its bot-detection notice — retrying in %.0fs", self.retry_delay)
                    await self._sleep(self.retry_delay)
                    text = await self._call(DDG_SERVER, query)
                if is_blocked_notice(text):
                    blocked_text = text
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


def _role_org_from(text: str) -> tuple[Optional[str], Optional[str]]:
    """The first "Role:" and "Organization/Company/Employer:" values in `text`."""
    role: Optional[str] = None
    org: Optional[str] = None
    if not text:
        return None, None
    role_match = re.search(r'\*\*Role:\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
    if not role_match:
        role_match = re.search(r'(?:^|\n)[ \t]*(?:(?:[-*•+]|\d+[.)])[ \t]+)?Role:\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
    if role_match:
        r = _clean_md(role_match.group(1).strip())
        # If role contains parentheses, prefer text inside
        paren_match = re.search(r'\(([^)]+)\)', r)
        if paren_match:
            r = _clean_md(paren_match.group(1))
        if r and not _is_generic_role(r):
            role = r
    for org_label in ["Organization", "Organisation", "Company", "Employer"]:
        org_match = re.search(rf'\*\*{org_label}:\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if not org_match:
            org_match = re.search(
                rf'(?:^|\n)[ \t]*(?:(?:[-*•+]|\d+[.)])[ \t]+)?{org_label}:\s+(.+?)(?:\n|$)', text, re.IGNORECASE
            )
        if org_match:
            o = _clean_md(org_match.group(1).strip())
            if o:
                org = o
                break
    return role, org


_NAME_LABEL_RE = re.compile(r'(?:^|\n)[ \t]*(?:[-*•]\s*)?(?:\*\*)?Name:(?:\*\*)?\s+(.+?)(?:\n|$)', re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r'^([ \t]*)(?:[-*•+]|\d+[.)])[ \t]+', re.MULTILINE)


def _entity_markers(text: str) -> list[tuple[int, str]]:
    """(start, normalised name) of every named entity in an answer: bold proper
    names, bold organisations and "Name:" labels. "**Role:**"-style labels are not
    entities."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r'\*\*(.+?)\*\*', text):
        raw = m.group(1).strip()
        if raw.endswith(":"):
            continue
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
        _add(m.start(1), m.end(1), m.group(1))
    name_re = r"([^\W\d_][\w'’-]*(?:\s+(?:[a-z]{1,3}\s+)*[^\W\d_][\w'’-]*){1,4})\s*\("
    for m in re.finditer(name_re, answer_text):
        _add(m.start(1), m.end(1), m.group(1))

    found.sort(key=lambda x: x[0])
    markers = _entity_markers(answer_text)
    out: list[dict] = []
    seen: set = set()
    for pos, endpos, name, labelled in found:
        key = _normalize_text(name)
        if key in seen:
            continue
        seen.add(key)
        role, org = _role_org_from(_entity_block(answer_text, pos, endpos, labelled, markers))
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
            block = _entity_block(text, span[0], span[1], labelled, markers)
            role, org = _role_org_from(block)
            entities = {k for _, k in markers if k}
            entities.add(_normalize_text(result["name"]))
            if len(entities) <= 1:
                g_role, g_org = _role_org_from(text)
                role = role or g_role
                org = org or g_org
        else:
            role, org = _role_org_from(text)
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
        except SearchBlocked:
            stats["blocked"] += 1
            search_tile.items.append({
                "kind": "query",
                "label": query,
                "detail": "blocked (DuckDuckGo bot detection)",
            })
            continue
        except Exception as e:
            log.debug(f"web_search failed: {e}")
            continue
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
                try:
                    results = await hooks.web_search(query)
                except WebSearchUnavailable:
                    # Propagate to caller - must not swallow this
                    raise
                except SearchBlocked:
                    results = []
                    blocked = True
                    blocked_count += 1
                    blocked_targets.update([q_target] if q_target else targets)
                except Exception as e:
                    results = []
                    errored = True
                    log.debug(f"web_search failed: {e}")
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
                        "detail": "blocked (DuckDuckGo bot detection)",
                    })
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
        async for ev in _emit_search_unavailable(person, targets, search_tile, blocked_count):
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

    # Perform searches
    for query in queries:
        executed.add(query)
        q_target = _query_target(query, targets)
        errored = False
        try:
            results = await hooks.web_search(query)
        except WebSearchUnavailable:
            # Propagate to caller - must not swallow this
            raise
        except SearchBlocked:
            blocked_count += 1
            blocked_targets.update([q_target] if q_target else targets)
            search_tile.items.append({
                "kind": "query",
                "label": query,
                "detail": "blocked (DuckDuckGo bot detection)",
            })
            continue
        except Exception as e:
            log.debug(f"web_search failed: {e}")
            results = []
            errored = True
        if not errored:
            ok_count += 1
            if q_target:
                ok_targets.add(q_target)
        results = results or []
        raw_total += len(results)

        # Filter results by name matching (drops social posts/directories)
        matching_results = [res for res in results if _accept_result(name, res)]

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
        async for ev in _emit_search_unavailable(person, targets, search_tile, blocked_count):
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
        f"{len(queries)} search(es), {len(all_results)} matching result(s) · direct search"
    )
    search_tile.items.append({
        "kind": "note",
        "label": f"Verdict by {answer_model}",
    })
    search_tile.output_preview = verdict_text[:300]
    search_tile.ms = int(time.time() * 1000) - search_tile.started_ms
    yield {"tile": search_tile.to_dict()}
