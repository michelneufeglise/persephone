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

# Web lookup target keywords
WEB_TARGET_KWS = {
    "linkedin": ["linkedin", "linked in", "linked-in"],
    "web": [
        "check online", "search online", "search the web", "web search",
        "look up online", "look him up", "look her up", "look them up",
        "google", "verify online", "check the internet", "on the internet",
        "does this person exist", "really exist", "real person", "actually exist",
        "exists online", "find online", "find this person online",
        "zoek op internet", "bestaat deze persoon", "echt bestaat",
    ],
}

# System prompt for tool-capable models
VERIFY_SYSTEM_PROMPT = """You are a web search assistant. Your task is to verify whether a person exists online.

Use web_search to find information about the person. For LinkedIn targets, prefer searching with site:linkedin.com/in queries.

Perform up to 3 search rounds. On the final response (after searching or if no more searches are needed), provide your answer in this exact format:

**[Verdict]** — one of: "**Likely match found**", "**Possible match**", "**No match found**"

Followed by:
- Up to 3 candidate profiles as a bullet list with [name](url) and snippet evidence
- Which details match the document (name / role / employer)
- A one-line caveat: "Note: Search results are not proof of identity."

Never cite URLs that did not appear in search results. If search failed, be honest about it."""

# Prompt for direct search fallback (no tools)
VERDICT_PROMPT = """Given the person details from the document and the search results below, provide your verdict:

**Person from document:**
Name: {name}
Role: {role}
Organization: {org}

**Search results:**
{results}

Provide your answer in this exact format:

**[Verdict]** — one of: "**Likely match found**", "**Possible match**", "**No match found**"

Followed by:
- Up to 3 candidate profiles as a bullet list with [name](url) if URLs exist in results, or just name if not
- Which details match the search results (name / role / employer)
- A one-line caveat: "Note: Search results are not proof of identity."

Refer to people by their name; do not assume gender or use he/she.

If no results, respond with: **No match found** — no LinkedIn profiles or public records with this name appeared in the search results. Note: Search results are not proof of identity."""

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
            "description": "Fetch a public web page as text. LinkedIn pages require login and are not fetchable — rely on search snippets for LinkedIn.",
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


# ── Pure functions ────────────────────────────────────────────────────────


def rules_web_lookup(message: str) -> tuple[Optional[str], list[str]]:
    """
    Detect if message requests web lookup via keyword rules.

    Returns (target, matched_keywords):
    - target: "linkedin" | "web" | None
    - matched_keywords: list of keywords that matched
    """
    if not message or not message.strip():
        return None, []

    msg_lower = message.lower()
    matched = []

    # Check LinkedIn keywords first
    for kw in WEB_TARGET_KWS["linkedin"]:
        if kw in msg_lower:
            matched.append(kw)
    if matched:
        return "linkedin", matched

    # Check generic web keywords
    for kw in WEB_TARGET_KWS["web"]:
        if kw in msg_lower:
            matched.append(kw)
    if matched:
        return "web", matched

    return None, []


def strip_web_clause(message: str) -> str:
    """
    Remove the sentence/clause containing web lookup keywords from message.

    This prevents rules_intent from picking the wrong intent when the message
    contains both a real intent (e.g., "who is this document about") and web
    keywords (e.g., "check linkedin if this person really exists").

    Handles commas before "and" (e.g., ", and check LinkedIn ...").

    Returns the stripped message, or the original if stripping would leave <3 chars.
    """
    target, _ = rules_web_lookup(message)

    if not target:
        # No web clause; return original
        return message

    # Split on sentence boundaries and commas before "and"
    # Handle patterns like "... who is this? and check linkedin" or "... who is this, and check linkedin"
    sentences = re.split(r'[.!?;]', message)
    kept_sentences = []

    for sent in sentences:
        sent_stripped = sent.strip()
        if not sent_stripped:
            continue

        # Check for "and <web_keywords>" pattern and split
        # Match ", and ..." or " and ..." followed by web keywords
        and_match = re.match(r"^(.+?)(?:,?\s+and\s+)(.+)$", sent_stripped, re.IGNORECASE)
        if and_match:
            before_and = and_match.group(1).strip()
            after_and = and_match.group(2).strip()

            # Check if the part after "and" has web keywords
            _, kws_after = rules_web_lookup(after_and)
            if kws_after:
                # Web clause is after "and"; keep the before part
                if before_and and len(before_and) >= 3:
                    kept_sentences.append(before_and)
            else:
                # No web keywords in after part; keep entire sentence
                _, kws = rules_web_lookup(sent_stripped)
                if not kws:
                    kept_sentences.append(sent_stripped)
        else:
            # No "and" pattern; check if sentence has web keywords
            _, kws = rules_web_lookup(sent_stripped)
            if not kws:
                kept_sentences.append(sent_stripped)

    result = ". ".join(kept_sentences).strip()
    if result and len(result) >= 3:
        return result
    return message


def resolve_web_lookup(
    laya_result: Optional[dict],
    rules_result: tuple[Optional[str], list[str]]
) -> dict:
    """
    Resolve web lookup decision from Laya and rules.

    Args:
        laya_result: {"value": "yes"|"no", "confidence": float} or None
        rules_result: (target, keywords) from rules_web_lookup

    Returns:
        {
            "target": "linkedin" | "web" | None,
            "source": "laya" | "rules" | "laya+rules",
            "confidence": float | None,
            "note": str | None
        }

    The source field is never None; defaults to "rules" when no match is found.
    """
    rules_target, rules_kws = rules_result

    # Rules match → use it
    if rules_target:
        source = "rules"
        confidence = 0.95  # High confidence for rules
        note = None
        if laya_result and laya_result.get("value") == "yes":
            source = "laya+rules"
            confidence = min(1.0, (0.95 + laya_result.get("confidence", 0.5)) / 2)
        return {
            "target": rules_target,
            "source": source,
            "confidence": confidence,
            "note": note,
        }

    # Laya yes WITHOUT rules → use only if high confidence
    if laya_result and laya_result.get("value") == "yes":
        conf = laya_result.get("confidence", 0.5)
        if conf >= 0.9:
            return {
                "target": "web",
                "source": "laya",
                "confidence": conf,
                "note": None,
            }

    # No match: source defaults to "laya" if laya_result exists, else "rules"
    return {
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
            role_line = line.strip()

            # Check for separator patterns (in order): " – ", " — ", " - ", " | ", " at ", " bij ", " @ "
            separators = [" – ", " — ", " - ", " | ", " at ", " bij ", " @ "]
            org = None

            for sep in separators:
                # Case-insensitive search for the separator
                sep_idx = role_line.lower().find(sep.lower())
                if sep_idx != -1:
                    # Split on this separator
                    role_part = role_line[:sep_idx].strip()
                    org_part = role_line[sep_idx + len(sep):].strip()

                    if org_part and 1 <= len(org_part) <= 60:
                        result["role"] = role_part
                        result["org"] = org_part
                        return result
                    else:
                        # Separator found but org part invalid; keep role, try fallback below
                        result["role"] = role_part
                        break

            # Fallback: if no separator split happened, use existing regex on this line or next line
            if not result["org"]:
                org_match = re.search(r'(?:at|bij|@)\s+([A-Za-z0-9\s&\-]+?)(?:,|$)', role_line, re.IGNORECASE)
                if not org_match and i + 1 < len(lines):
                    # Try next line
                    next_line = lines[i + 1]
                    org_match = re.search(r'(?:at|bij|@)\s+([A-Za-z0-9\s&\-]+?)(?:,|$)', next_line, re.IGNORECASE)

                if org_match:
                    org = org_match.group(1).strip()
                    if org and len(org) < 100:  # Sanity check
                        result["org"] = org

            if not result["role"]:
                result["role"] = role_line

            return result

    return result


def _is_generic_role(role: str) -> bool:
    """Check if role is a generic placeholder that should be treated as None."""
    if not role:
        return True
    # Strip parentheses content first
    role_stripped = re.sub(r'\([^)]+\)', '', role).strip()
    generic_roles = {
        "holder", "subject", "subject of the cv", "candidate", "applicant",
        "candidate/applicant", "owner", "author", "signer", "signatory",
        "person", "individual", "employee", "n/a", "unknown",
        "professional profile", "profile", "cv", "resume", "curriculum vitae",
        "cv owner", "document owner", "subject of the document"
    }
    return role_stripped.lower() in generic_roles


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
    def clean_text(text: str) -> str:
        """Remove markdown formatting."""
        if not text:
            return ""
        # Remove **bold**, __bold__, *italic*, _italic_, `code`
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        # Remove surrounding quotes
        text = text.strip('\'"')
        return text.strip()

    def is_valid_name(name: str) -> bool:
        """Check if name is valid (2–6 words, letters + accents/hyphens/apostrophes)."""
        if not name:
            return False
        # Reject generic values
        if name.lower() in ("unknown", "not specified", "n/a", "none", "not given"):
            return False
        words = name.split()
        if not (2 <= len(words) <= 6):
            return False
        # Allow letters, accents, hyphens, apostrophes
        return bool(re.match(r"^[a-zA-Zàâäéèêëìîïóòôöùûüçñ\-']+(\s+[a-zA-Zàâäéèêëìîïóòôöùûüçñ\-']+)*$", name))

    def extract_from_text(text: str) -> dict:
        """Extract name, role, org from text."""
        result = {"name": None, "role": None, "org": None}

        # Pattern: **Name:** X or Name: X
        name_match = re.search(r'\*\*Name:\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if not name_match:
            name_match = re.search(r'(?:^|\n)Name:\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if name_match:
            name = clean_text(name_match.group(1).strip())
            if is_valid_name(name):
                result["name"] = name

        # Pattern: This document is about **X** or is about X.
        if not result["name"]:
            about_match = re.search(r'This document is about \*\*(.+?)\*\*', text, re.IGNORECASE)
            if about_match:
                name = clean_text(about_match.group(1).strip())
                if is_valid_name(name):
                    result["name"] = name
            if not result["name"]:
                about_match = re.search(r'is about ([^.]+)\.', text, re.IGNORECASE)
                if about_match:
                    name = clean_text(about_match.group(1).strip())
                    if is_valid_name(name):
                        result["name"] = name

        # Pattern: **Role:** X or Role: X
        role_match = re.search(r'\*\*Role:\*?\*?\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if not role_match:
            role_match = re.search(r'(?:^|\n)Role:\s+(.+?)(?:\n|$)', text, re.IGNORECASE)
        if role_match:
            role = clean_text(role_match.group(1).strip())
            # If role contains parentheses, prefer text inside
            paren_match = re.search(r'\(([^)]+)\)', role)
            if paren_match:
                role = clean_text(paren_match.group(1))
            # Check if this is a generic role
            if role and not _is_generic_role(role):
                result["role"] = role

        # Pattern: **Organization:** X, **Company:** X, or **Employer:** X
        for org_label in ["Organization", "Company", "Employer"]:
            if result["org"]:
                break
            org_match = re.search(
                rf'\*\*{org_label}:\*?\*?\s+(.+?)(?:\n|$)',
                text,
                re.IGNORECASE
            )
            if not org_match:
                org_match = re.search(
                    rf'(?:^|\n){org_label}:\s+(.+?)(?:\n|$)',
                    text,
                    re.IGNORECASE
                )
            if org_match:
                org = clean_text(org_match.group(1).strip())
                if org:
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


def build_queries(person: dict, target: str) -> list[str]:
    """
    Build search queries for a person.

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        target: "linkedin" | "web"

    Returns:
        list of unique search queries (deduplicated)
    """
    name = (person.get("name") or "").strip()
    role = (person.get("role") or "").strip()
    org = (person.get("org") or "").strip()

    if not name:
        return []

    queries = []

    if target == "linkedin":
        # LinkedIn-specific queries
        queries.append(f'site:linkedin.com/in "{name}"')
        if role or org:
            queries.append(f'"{name}" {role or org} linkedin')
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

    return unique


def is_linkedin_url(url: str) -> bool:
    """Check if URL is a LinkedIn domain."""
    if not url:
        return False
    return "linkedin.com" in url.lower()


def is_linkedin_profile(url: str) -> bool:
    """Check if URL is a LinkedIn profile."""
    if not url:
        return False
    return "linkedin.com/in/" in url.lower()


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


def _build_candidates(all_results: dict) -> list[dict]:
    """
    Build a list of candidates from all_results.

    Returns unique name-matching results, LinkedIn profiles first, max 6.
    Format: [{"title": str, "url": str, "snippet": str}, ...]
    """
    candidates = []

    # Sort LinkedIn profiles first
    linkedin_urls = [url for url in all_results if is_linkedin_profile(url)]
    other_urls = [url for url in all_results if url not in linkedin_urls]
    sorted_urls = linkedin_urls + other_urls

    for url in sorted_urls[:6]:  # Max 6 candidates
        res = all_results[url]
        candidates.append({
            "title": res.get("title", ""),
            "url": url,
            "snippet": res.get("snippet", ""),
        })

    return candidates


def _build_verdict_prompt(person: dict, doc_excerpt: str, candidates: list[dict]) -> str:
    """
    Build verdict prompt with person dict, document excerpt, and candidates.

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        doc_excerpt: First ~1500 chars of document
        candidates: List of {"title": str, "url": str, "snippet": str}

    Returns:
        Formatted verdict prompt
    """
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
        candidates_section += f"{i}. [{cand.get('title', 'Unknown')}]({cand.get('url', '')})\n"
        if cand.get('snippet'):
            candidates_section += f"   {cand.get('snippet', '')}\n"

    prompt = f"""{person_section}{doc_section}{candidates_section}

**Your task:**
Compare each candidate's headline/snippet with the document excerpt (if provided). If several candidates share the name:
- Identify which one matches the document (same role, employer, location, education)
- Note which ones appear to be different people

Output format:
1. First line MUST be exactly one of:
   - "**Likely match found**"
   - "**Possible match**"
   - "**No match found**"

   Followed by a short reason on the same line or next line.

2. Then bullet points with each candidate:
   - `[title](url) — why it matches / doesn't`

3. Last line: "_Search results are not proof of identity._"

Only use URLs that appear above. Do not fabricate or change URLs."""

    return prompt


# ── Web lookup execution ──────────────────────────────────────────────────


async def run_web_lookup(
    person: dict,
    target: str,
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
    - Handles tool calls, fallback paths, and error cases

    Args:
        person: {"name": str, "role": str|None, "org": str|None}
        target: "linkedin" | "web"
        hooks: AgentHooks with web_search, fetch_page, chat_tools, etc.
        answer_model: Model name for fallback LLM verdict
        now_ms: Start time in ms
        doc_excerpt: First ~1500 chars of subject document text (for headline extraction) [DEPRECATED]
        doc_text: Full subject document text (for headline extraction)

    Yields:
        Event dicts: {"tile": ...}, {"content": ...}
    """
    from doc_agent import Tile  # Import here to avoid circular dependency

    # Merge headline data (role/org from document text)
    # Prefer full doc_text over excerpt; if only excerpt provided, use it
    text_for_headline = doc_text if doc_text else doc_excerpt
    if text_for_headline:
        person = merge_person_with_headline(person, text_for_headline)

    # Build queries
    queries = build_queries(person, target)
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
        detail=f"Person: {name}" + (f" · {role}" if role else "") + (f" · {org}" if org else ""),
        items=[{"kind": "query", "label": q} for q in queries],
    )
    yield {"tile": plan_tile.to_dict()}

    # Create main web-search tile
    search_tile = Tile(
        id="web-search",
        kind="web",
        title="Web lookup" + (" · LinkedIn" if target == "linkedin" else ""),
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
                tool_model, queries, person, target, search_tile, hooks,
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
            queries, person, target, search_tile, answer_model, hooks,
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
    target: str,
    search_tile: Tile,
    hooks: Any,
    *,
    answer_model: str,
    doc_excerpt: str = "",
) -> AsyncIterator[dict]:
    """Execute tool-based web lookup with query rewriting and name filtering."""
    name = person.get("name", "")
    role = person.get("role", "")
    org = person.get("org", "")

    # Build initial message
    user_msg = f"Person from the document: name={name}"
    if role:
        user_msg += f"; role={role}"
    if org:
        user_msg += f"; organization={org}"
    user_msg += f". Target: {target}."
    if queries:
        user_msg += f" Suggested first query: {queries[0]}"

    messages = [
        {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    all_results = {}  # url -> result dict (with name_matching info)
    seen_urls = set()
    search_count = 0  # Track web_search calls to cap at 4
    tool_results_raw = []  # Store raw results for verdict
    tool_queries_executed = []  # Track queries actually sent

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
                if search_count >= 4:
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
                original_query = query
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

                try:
                    results = await hooks.web_search(query)
                except WebSearchUnavailable:
                    # Propagate to caller - must not swallow this
                    raise
                except Exception as e:
                    results = []
                    log.debug(f"web_search failed: {e}")

                # Filter results by name matching and sort LinkedIn profiles first
                matching_results = []
                for res in results:
                    title = res.get("title", "")
                    url = res.get("url", "")
                    snippet = res.get("snippet", "")

                    # Check if result matches the person's name
                    if name_matches(name, f"{title} {url} {snippet}"):
                        matching_results.append(res)

                total_results = len(results)
                matching_count = len(matching_results)

                # Sort LinkedIn profiles first
                linkedin_results = [r for r in matching_results if is_linkedin_profile(r.get("url", ""))]
                other_results = [r for r in matching_results if not is_linkedin_profile(r.get("url", ""))]
                sorted_results = linkedin_results + other_results

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
                        }
                        tool_results_raw.append(res)
                    search_tile.items.append({
                        "kind": "result",
                        "label": res.get("title", url),
                        "url": url,
                        "detail": res.get("snippet", "")[:160],
                    })

                # Tool message for LLM (only matching results)
                tool_content = "\n".join(
                    f"{i+1}. {r.get('title', 'No title')} — {r.get('url', 'No URL')} — {r.get('snippet', '')[:100]}"
                    for i, r in enumerate(sorted_results)
                ) or "No matching results."

                messages.append({
                    "role": "tool",
                    "content": tool_content,
                })

                tool_queries_executed.append(original_query)

                yield {"tile": search_tile.to_dict()}

            elif tool_name == "fetch_page":
                url = arguments.get("url", "")
                if not url:
                    continue

                seen_urls.add(url)

                if is_linkedin_url(url):
                    tool_content = "LinkedIn pages require login; use the search snippets instead."
                    search_tile.items.append({
                        "kind": "note",
                        "label": f"Skipped {url} (LinkedIn requires login)",
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

    # Generate dedicated verdict from matching results
    candidates = _build_candidates(all_results)

    verdict_text = ""
    if not candidates:
        # No matches: emit verdict without LLM call
        verdict_text = "**No match found** — no LinkedIn profiles (or pages) with this exact name appeared in the search results.\n\n_Search results are not proof of identity._"
    else:
        # Use LLM to generate verdict
        verdict_prompt = _build_verdict_prompt(person, doc_excerpt, candidates)
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

    # Emit internal _web_result event for kg_ingest (before yielding content)
    candidates = _build_candidates(all_results)
    yield {
        "_web_result": {
            "person": person,
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
    target: str,
    search_tile: Tile,
    answer_model: str,
    hooks: Any,
    *,
    doc_excerpt: str = "",
) -> AsyncIterator[dict]:
    """Execute fallback direct search + LLM verdict."""
    name = person.get("name", "")
    all_results = {}  # url -> result dict
    seen_urls = set()

    # Perform searches
    for query in queries:
        try:
            results = await hooks.web_search(query)
        except WebSearchUnavailable:
            # Propagate to caller - must not swallow this
            raise
        except Exception as e:
            log.debug(f"web_search failed: {e}")
            results = []

        # Filter results by name matching
        matching_results = []
        for res in results:
            title = res.get("title", "")
            url = res.get("url", "")
            snippet = res.get("snippet", "")
            if name_matches(name, f"{title} {url} {snippet}"):
                matching_results.append(res)

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
                }
                seen_urls.add(url)

    # Format results for display
    result_items = []
    if target == "linkedin":
        # Sort LinkedIn profiles first
        linkedin_urls = [url for url in all_results if is_linkedin_profile(url)]
        other_urls = [url for url in all_results if url not in linkedin_urls]
        sorted_urls = linkedin_urls + other_urls
    else:
        sorted_urls = list(all_results.keys())

    for url in sorted_urls[:10]:  # Limit to 10 for tile display
        res = all_results[url]
        result_items.append({
            "kind": "result",
            "label": res.get("title", url),
            "url": url,
            "detail": res.get("snippet", "")[:160],
        })

    search_tile.items.extend(result_items)

    # Generate dedicated verdict from matching results
    candidates = _build_candidates(all_results)

    verdict_text = ""
    if not candidates:
        # No matches: emit verdict without LLM call
        verdict_text = "**No match found** — no LinkedIn profiles (or pages) with this exact name appeared in the search results.\n\n_Search results are not proof of identity._"
    else:
        # Use LLM to generate verdict
        verdict_prompt = _build_verdict_prompt(person, doc_excerpt, candidates)
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

    # Emit internal _web_result event for kg_ingest (before yielding content)
    candidates = _build_candidates(all_results)
    yield {
        "_web_result": {
            "person": person,
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
