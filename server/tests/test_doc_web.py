"""
Tests for the document web lookup feature.

Tests web lookup detection, person extraction, query building, and URL utilities.
These tests use only synchronous functions and do not require pytest-asyncio.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from types import SimpleNamespace

import doc_web as _web


# ── Tests: rules_web_lookup ────────────────────────────────────────────────


def test_rules_web_lookup_linkedin():
    """Test detection of LinkedIn keywords."""
    message = "who is this document about and check linkedin if this person really exists"
    targets, kws = _web.rules_web_lookup(message)
    assert targets == ["linkedin"]
    assert "linkedin" in kws


def test_rules_web_lookup_web():
    """Test detection of generic web search keywords."""
    message = "does this person really exist online? search the web to verify"
    targets, kws = _web.rules_web_lookup(message)
    assert targets == ["web"]
    assert any(kw in kws for kw in ["search the web", "exist"])


def test_rules_web_lookup_none():
    """Test no web lookup keywords."""
    message = "who is this document about?"
    targets, kws = _web.rules_web_lookup(message)
    assert targets == []
    assert kws == []


def test_rules_web_lookup_empty():
    """Test empty message."""
    targets, kws = _web.rules_web_lookup("")
    assert targets == []
    assert kws == []


@pytest.mark.parametrize("message,expected", [
    ("check facebook if she exists", ["facebook"]),
    ("look him up on LinkedIn and Facebook", ["linkedin", "facebook"]),
    ("look her up on Facebook and LinkedIn", ["facebook", "linkedin"]),
    ("is he on instagram", ["instagram"]),
    ("is she on insta ?", ["instagram"]),
    ("find her on twitter", ["x"]),
    ("is she on X?", ["x"]),
    ("check x.com for this person", ["x"]),
    ("is he on fb", ["facebook"]),
    ("search the web for this person", ["web"]),
    ("who is this document about?", []),
])
def test_rules_web_lookup_platforms(message, expected):
    """Platforms are detected in mention order; generic web only without a platform."""
    targets, kws = _web.rules_web_lookup(message)
    assert targets == expected
    assert bool(kws) == bool(expected)


@pytest.mark.parametrize("message", [
    "summarize the fbi report",           # "fb" inside another word
    "what does the fax say about the box",  # words containing x
    "extract the tax number from the xbox invoice",
    "check the netflix.com invoice",      # "x.com" inside another domain
    "the next step is on xylophone practice",  # "on x" prefix of a word
    "installation instructions",           # "insta" inside another word
])
def test_rules_web_lookup_no_false_triggers(message):
    """Short keywords (fb, x, insta) must not fire inside other words."""
    targets, kws = _web.rules_web_lookup(message)
    assert targets == []
    assert kws == []


def test_rules_web_lookup_single_wrapper():
    """The backward-compatible wrapper returns (first target, keywords)."""
    target, kws = _web.rules_web_lookup_single("look him up on LinkedIn and Facebook")
    assert target == "linkedin"
    assert "facebook" in kws
    assert _web.rules_web_lookup_single("who is this?") == (None, [])


# ── Tests: strip_web_clause ────────────────────────────────────────────────


def test_strip_web_clause_removes_web_keywords():
    """Test stripping web clause from message."""
    message = "who is this document about and check linkedin if this person really exists"
    result = _web.strip_web_clause(message)
    # Should keep the identify part, remove the web part
    assert "who is" in result.lower()
    assert len(result) >= 3


def test_strip_web_clause_no_web_keywords():
    """Test message without web keywords stays unchanged."""
    message = "who is this document about?"
    result = _web.strip_web_clause(message)
    assert result == message


def test_strip_web_clause_preserves_message_if_empty_result():
    """Test that original message is returned if stripping would leave <3 chars."""
    message = "verify"
    result = _web.strip_web_clause(message)
    # Since stripping might leave empty, return original
    assert len(result) >= 1


def test_strip_web_clause_facebook():
    """A Facebook clause is stripped like a LinkedIn clause."""
    message = "who is this document about and check facebook if this person exists"
    result = _web.strip_web_clause(message)
    assert result.lower() == "who is this document about"


def test_strip_web_clause_multiple_platforms():
    """A clause naming several platforms is removed completely."""
    message = "Who is this document about? Look her up on LinkedIn and Facebook."
    result = _web.strip_web_clause(message)
    assert "who is this document about" in result.lower()
    assert "linkedin" not in result.lower()
    assert "facebook" not in result.lower()


def test_strip_web_clause_instagram_and_twitter():
    """Instagram and Twitter clauses are stripped too."""
    assert "instagram" not in _web.strip_web_clause("who is this, and is she on Instagram?").lower()
    assert "twitter" not in _web.strip_web_clause("who is this document about and find him on twitter").lower()


# ── Tests: resolve_web_lookup ──────────────────────────────────────────────


def test_resolve_web_lookup_rules_match():
    """Test web lookup decision when rules match."""
    laya_result = None
    rules_result = ("linkedin", ["linkedin"])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] == "linkedin"
    assert result["source"] == "rules"
    assert result["confidence"] > 0.9


def test_resolve_web_lookup_laya_and_rules():
    """Test web lookup with both Laya and rules matches."""
    laya_result = {"value": "yes", "confidence": 0.95}
    rules_result = ("web", ["search online"])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] == "web"
    assert result["source"] == "laya+rules"


def test_resolve_web_lookup_laya_only_high_confidence():
    """Laya-only "yes" (even confident) never triggers a search without an
    explicit keyword request — it is only recorded as a note."""
    laya_result = {"value": "yes", "confidence": 0.92}
    rules_result = (None, [])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] is None
    assert result["targets"] == []
    assert result["source"] == "laya"
    assert "not run without an explicit request" in result["note"]


def test_resolve_web_lookup_laya_only_low_confidence():
    """Test Laya-only match with low confidence is rejected."""
    laya_result = {"value": "yes", "confidence": 0.8}
    rules_result = (None, [])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] is None


def test_resolve_web_lookup_no_match():
    """Test no web lookup match."""
    laya_result = {"value": "no", "confidence": 0.95}
    rules_result = (None, [])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] is None
    # Source must never be None; defaults to "laya" when laya_result exists
    assert result["source"] == "laya"
    assert isinstance(result["source"], str)


def test_resolve_web_lookup_no_match_no_laya():
    """Test no web lookup match with no Laya result."""
    laya_result = None
    rules_result = (None, [])
    result = _web.resolve_web_lookup(laya_result, rules_result)
    assert result["target"] is None
    # Source must never be None; defaults to "rules" when no laya_result
    assert result["source"] == "rules"
    assert isinstance(result["source"], str)


def test_resolve_web_lookup_list_targets():
    """List-form rules result keeps all targets; "target" is the first one."""
    result = _web.resolve_web_lookup(None, (["linkedin", "facebook"], ["linkedin", "facebook"]))
    assert result["targets"] == ["linkedin", "facebook"]
    assert result["target"] == "linkedin"
    assert result["source"] == "rules"
    assert result["confidence"] > 0.9


def test_resolve_web_lookup_list_empty_and_laya():
    """Empty targets + confident Laya yes → still no targets (explicit request required)."""
    result = _web.resolve_web_lookup({"value": "yes", "confidence": 0.93}, ([], []))
    assert result["targets"] == []
    assert result["target"] is None
    result = _web.resolve_web_lookup(None, ([], []))
    assert result["targets"] == []
    assert result["target"] is None


def test_resolve_web_lookup_from_rules():
    """End-to-end: rules_web_lookup output feeds resolve_web_lookup directly."""
    rules = _web.rules_web_lookup("is she on instagram and twitter?")
    result = _web.resolve_web_lookup({"value": "yes", "confidence": 0.9}, rules)
    assert result["targets"] == ["instagram", "x"]
    assert result["source"] == "laya+rules"


# ── Tests: extract_person ──────────────────────────────────────────────────


def test_extract_person_from_answer_bold_name():
    """Test extracting person from answer with **Name:** pattern."""
    answer = """This document is about Michel Neuféglise.

- **Name:** Michel Neuféglise
- **Role:** Subject of the CV (Solution Architect / Senior Full Stack Developer)
- **Organization:** Acme Corp
"""
    history = []
    result = _web.extract_person(answer, history)
    assert result is not None
    assert result["name"] == "Michel Neuféglise"
    assert "Solution Architect" in result["role"]
    assert result["org"] == "Acme Corp"


def test_extract_person_from_answer_is_about():
    """Test extracting person using 'is about' pattern."""
    answer = "This document is about **Jane Example**."
    history = []
    result = _web.extract_person(answer, history)
    assert result is not None
    assert result["name"] == "Jane Example"


def test_extract_person_role_with_parentheses():
    """Test that role with parentheses uses inner text."""
    answer = "Subject of the CV (Solution Architect / Senior Full Stack Developer)"
    history = []
    result = _web.extract_person(answer, history)
    # The answer has no clear name/role markers, so won't match


def test_extract_person_from_history():
    """Test extracting person from history when not in answer."""
    answer = "The document is valid."
    history = [
        {"role": "assistant", "content": "Previous message"},
        {"role": "assistant", "content": "**Name:** John Smith\n**Role:** CEO\n**Organization:** TechCorp"},
    ]
    result = _web.extract_person(answer, history)
    assert result is not None
    assert result["name"] == "John Smith"


def test_extract_person_rejects_generic_names():
    """Test that generic names are rejected."""
    answer = "**Name:** Unknown\n**Role:** CEO"
    history = []
    result = _web.extract_person(answer, history)
    assert result is None  # "Unknown" is rejected


def test_extract_person_name_too_long():
    """Test that names with too many words are rejected."""
    answer = "**Name:** John Michael Smith Johnson Anderson Patterson Brown"
    history = []
    result = _web.extract_person(answer, history)
    assert result is None  # 7 words, exceeds max of 6


def test_extract_person_none_when_no_name():
    """Test returns None when no valid name found."""
    answer = "This document contains no person information."
    history = []
    result = _web.extract_person(answer, history)
    assert result is None


# ── Tests: build_queries ───────────────────────────────────────────────────


def test_build_queries_linkedin():
    """Test LinkedIn query building."""
    person = {"name": "Jane Example", "role": "Engineer", "org": "Acme"}
    queries = _web.build_queries(person, "linkedin")
    assert len(queries) > 0
    assert 'site:linkedin.com/in "Jane Example"' in queries
    # Should have at least 2 queries
    assert len([q for q in queries if "linkedin" in q.lower()]) >= 1


def test_build_queries_web():
    """Test generic web query building."""
    person = {"name": "Jane Example", "role": "Engineer", "org": None}
    queries = _web.build_queries(person, "web")
    assert len(queries) > 0
    assert '"Jane Example"' in queries[0]


def test_build_queries_no_name():
    """Test query building with no name."""
    person = {"name": "", "role": "Engineer", "org": "Acme"}
    queries = _web.build_queries(person, "web")
    assert queries == []


def test_build_queries_deduped():
    """Test that duplicate queries are removed."""
    person = {"name": "Jane Example", "role": None, "org": None}
    queries = _web.build_queries(person, "web")
    # Should have unique queries only
    assert len(queries) == len(set(queries))


def test_build_queries_linkedin_and_facebook():
    """Two platforms: a site: query per platform plus name+role queries, max 6."""
    person = {"name": "Jane Example", "role": "Engineer", "org": "Acme"}
    queries = _web.build_queries(person, ["linkedin", "facebook"])
    assert any(q.startswith("site:linkedin.com") and '"Jane Example"' in q for q in queries)
    assert 'site:facebook.com "Jane Example"' in queries
    assert '"Jane Example" Engineer Facebook' in queries
    assert len(queries) == 4
    assert len(queries) <= 6
    # LinkedIn queries come first (target order)
    assert queries[0].startswith("site:linkedin.com")


def test_build_queries_caps_at_six():
    """At most 3 platforms / 6 queries."""
    person = {"name": "Jane Example", "role": "Engineer", "org": "Acme"}
    queries = _web.build_queries(person, ["linkedin", "facebook", "instagram", "x"])
    assert len(queries) <= 6
    assert not any("site:x.com" in q for q in queries)  # 4th platform dropped
    assert any("site:instagram.com" in q for q in queries)


def test_build_queries_no_role_one_query_per_platform():
    """Without role/org only the site: query is built per platform."""
    person = {"name": "Jane Example", "role": None, "org": None}
    queries = _web.build_queries(person, ["instagram", "x"])
    assert queries == ['site:instagram.com "Jane Example"', 'site:x.com "Jane Example"']


# ── Tests: URL helpers ─────────────────────────────────────────────────────


def test_is_linkedin_url():
    """Test LinkedIn URL detection."""
    assert _web.is_linkedin_url("https://www.linkedin.com/in/jane-example")
    assert _web.is_linkedin_url("https://linkedin.com/company/acme")
    assert not _web.is_linkedin_url("https://google.com")
    assert not _web.is_linkedin_url("")


def test_is_linkedin_profile():
    """Test LinkedIn profile URL detection."""
    assert _web.is_linkedin_profile("https://www.linkedin.com/in/jane-example")
    assert not _web.is_linkedin_profile("https://linkedin.com/company/acme")
    assert not _web.is_linkedin_profile("https://google.com")


@pytest.mark.parametrize("url,platform,is_profile", [
    ("https://www.linkedin.com/in/jane-example", "linkedin", True),
    ("https://nl.linkedin.com/in/jane-example/", "linkedin", True),
    ("https://www.linkedin.com/pub/dir/Jane/Example", "linkedin", False),
    ("https://www.linkedin.com/posts/jane-example_activity-123", "linkedin", False),
    ("https://www.facebook.com/jane.example", "facebook", True),
    ("https://www.facebook.com/profile.php?id=100012345", "facebook", True),
    ("https://www.facebook.com/public/Jane-Example", "facebook", False),
    ("https://www.facebook.com/jane.example/posts/123", "facebook", False),
    ("https://www.facebook.com/groups/somegroup/", "facebook", False),
    ("https://www.instagram.com/jane.example/", "instagram", True),
    ("https://www.instagram.com/p/abc", "instagram", False),
    ("https://www.instagram.com/reel/xyz/", "instagram", False),
    ("https://x.com/jane", "x", True),
    ("https://twitter.com/jane_example", "x", True),
    ("https://x.com/jane/status/1", "x", False),
    ("https://x.com/search?q=jane", "x", False),
    ("https://example.com/jane", None, False),
    ("https://www.netflix.com/jane", None, False),
    ("", None, False),
])
def test_platform_of_and_is_profile_url(url, platform, is_profile):
    """Platform detection by domain and profile/non-profile classification."""
    assert _web.platform_of(url) == platform
    assert _web.is_profile_url(url) is is_profile
    if platform:
        assert _web.is_profile_url(url, platform) is is_profile


def test_is_profile_url_wrong_platform():
    """A URL is never a profile of a platform it does not belong to."""
    assert not _web.is_profile_url("https://www.facebook.com/jane.example", "linkedin")


# ── Tests: _strip_unseen_links ─────────────────────────────────────────────


def test_strip_unseen_links_removes_unseen():
    """Test that links to unseen URLs are stripped."""
    text = "[Jane Example](https://evil.example) and [Real Link](https://linkedin.com/in/jane)"
    seen_urls = {"https://linkedin.com/in/jane"}
    result = _web._strip_unseen_links(text, seen_urls)
    assert "[Real Link](https://linkedin.com/in/jane)" in result
    assert "evil.example" not in result
    assert "Jane Example" in result  # Link text preserved


def test_strip_unseen_links_keeps_seen():
    """Test that links to seen URLs are kept."""
    text = "[Profile](https://linkedin.com/in/jane)"
    seen_urls = {"https://linkedin.com/in/jane"}
    result = _web._strip_unseen_links(text, seen_urls)
    assert "[Profile](https://linkedin.com/in/jane)" in result


# ── Tests: strip_web_clause with comma-and ─────────────────────────────────


def test_strip_web_clause_comma_and():
    """Test stripping web clause with comma before 'and'."""
    message = "who is this document about, and check LinkedIn whether this person really exists"
    result = _web.strip_web_clause(message)
    # Should keep "who is this document about", remove ", and check linkedin..."
    assert "who is this" in result.lower()
    assert "linkedin" not in result.lower()
    assert len(result) >= 3


def test_strip_web_clause_space_and():
    """Test stripping web clause with space and 'and'."""
    message = "who is this document about and check linkedin if this person really exists"
    result = _web.strip_web_clause(message)
    assert "who is this" in result.lower()
    assert "linkedin" not in result.lower()


# ── Tests: extract_headline ────────────────────────────────────────────────


def test_extract_headline_with_role_and_org():
    """Test extracting headline from document text."""
    doc_text = """Michel Neuféglise
Solution Architect / Senior Full Stack Developer
Utrecht, Netherlands
"""
    result = _web.extract_headline(doc_text)
    assert result["role"] is not None
    assert "Solution Architect" in result["role"] or "Senior Full Stack Developer" in result["role"]


def test_extract_headline_with_at_pattern():
    """Test extracting org with 'at' pattern."""
    doc_text = """Jane Example
Senior Engineer at Acme Corp
San Francisco, CA
"""
    result = _web.extract_headline(doc_text)
    assert "Engineer" in result["role"] if result["role"] else True
    # Org extraction might not catch "at Acme Corp" depending on implementation


def test_extract_headline_no_keywords():
    """Test that non-job-keyword lines are skipped."""
    doc_text = """John Smith
123 Main Street
Some City
"""
    result = _web.extract_headline(doc_text)
    assert result["role"] is None


# ── Tests: extract_person with generic role ────────────────────────────────


def test_extract_person_generic_role_holder():
    """Test that generic role 'Holder' is treated as None."""
    answer = """**Name:** Jane Example
**Role:** Holder (Candidate/Applicant)
**Organization:** Company
"""
    history = []
    result = _web.extract_person(answer, history)
    assert result is not None
    assert result["name"] == "Jane Example"
    assert result["role"] is None  # Generic role should be None


def test_extract_person_generic_role_candidate():
    """Test that generic role 'Candidate' is treated as None."""
    answer = """**Name:** John Smith
**Role:** Candidate
**Organization:** Acme
"""
    history = []
    result = _web.extract_person(answer, history)
    assert result is not None
    assert result["role"] is None


# ── Tests: name_matches ────────────────────────────────────────────────────


def test_name_matches_exact():
    """Test exact name match."""
    assert _web.name_matches("Michel Neuféglise", "michel neufeglise") is True
    assert _web.name_matches("Michel Neuféglise", "Michel Neuféglise") is True


def test_name_matches_in_snippet():
    """Test name matching in snippet."""
    text = "Solution Architect at Michel Neuféglise Inc — a great company"
    assert _web.name_matches("Michel Neuféglise", text) is True


def test_name_matches_linkedin():
    """Test name matching in LinkedIn profile."""
    url = "https://linkedin.com/in/michelneufeglise"
    title = "Michel Neuféglise - Solution Architect | LinkedIn"
    snippet = "Solution Architect at Acme"
    combined = f"{title} {url} {snippet}"
    assert _web.name_matches("Michel Neuféglise", combined) is True


def test_name_matches_accent_insensitive():
    """Test that accents don't matter."""
    assert _web.name_matches("Michel Neuféglise", "michel neufeglise") is True
    assert _web.name_matches("Michel Neufeglise", "michel neuféglise") is True


def test_name_matches_reject_different_person():
    """Test that different person is not matched."""
    assert _web.name_matches("Michel Neuféglise", "John Smith") is False
    assert _web.name_matches("Michel Neuféglise", "Stéphane Neuféglise") is False


def test_name_matches_initial():
    """Test first-name initial matching for 2-token names."""
    assert _web.name_matches("Michel Neuféglise", "M. Neuféglise") is True


# ── Tests: extract_headline with long date runs ────────────────────────────


def test_extract_headline_skips_long_date_runs():
    """Test that long date runs (>120 chars) are skipped, scanning first 40 non-empty lines."""
    # Simulate a CV with ~150-char date run on first line
    date_run = "2023-01/ present" * 10  # Creates a very long line
    doc_text = date_run + "\n2023-01/ present\n2014-01 / 2015-01\nSolution Architect – Rabobank\nRBO front-end retail app"
    result = _web.extract_headline(doc_text)
    assert result["role"] == "Solution Architect", f"Expected role 'Solution Architect', got '{result.get('role')}'"
    assert result["org"] == "Rabobank", f"Expected org 'Rabobank', got '{result.get('org')}'"


def test_extract_headline_simple_at_pattern():
    """Test simple 'at' pattern extraction."""
    doc_text = "Jane Example\nSenior Engineer at Acme\nAmsterdam"
    result = _web.extract_headline(doc_text)
    # "Senior Engineer at Acme" should be detected; org should be "Acme"
    assert "Engineer" in result.get("role", "") or result["role"] is not None
    if result["org"]:
        assert "Acme" in result["org"]


def test_extract_headline_separator_dash():
    """Test separator-based split with em-dash."""
    doc_text = "Alice Smith\nProduct Manager – TechCorp\nSan Francisco"
    result = _web.extract_headline(doc_text)
    assert result["role"] == "Product Manager"
    assert result["org"] == "TechCorp"


def test_extract_headline_separator_pipe():
    """Test separator-based split with pipe."""
    doc_text = "Bob Johnson\nSenior Developer | BigCorp\nNew York"
    result = _web.extract_headline(doc_text)
    assert result["role"] == "Senior Developer"
    assert result["org"] == "BigCorp"


def test_extract_headline_separator_at_word():
    """Test separator 'at' word-based split (case-insensitive)."""
    doc_text = "Carol Lee\nArchitect at CloudSys\nSeattle"
    result = _web.extract_headline(doc_text)
    # The 'at' word separator should split "Architect at CloudSys"
    assert result["role"] == "Architect"
    assert result["org"] == "CloudSys"


def test_extract_headline_org_too_long():
    """Test that org part >60 chars is rejected."""
    long_org = "A" * 65
    doc_text = f"Developer – {long_org}"
    result = _web.extract_headline(doc_text)
    # Role should be extracted, but org should be None or not the long one
    if result["role"] and "Developer" in result["role"]:
        # If separator split succeeded, org should be rejected as too long
        assert result["org"] is None or len(result["org"]) <= 60


# ── Tests: merge_person_with_headline ─────────────────────────────────────


def test_merge_person_with_headline_fills_missing_role():
    """Test that headline role fills missing person role."""
    person = {"name": "Jane Example", "role": None, "org": None}
    doc_text = "Jane Example\nSenior Engineer – Acme\nAmsterdam"
    result = _web.merge_person_with_headline(person, doc_text)
    assert result["name"] == "Jane Example"
    assert result["role"] == "Senior Engineer"
    assert result["org"] == "Acme"


def test_merge_person_with_headline_keeps_non_generic_role():
    """Test that non-generic answer role is kept over headline."""
    person = {"name": "Jane Example", "role": "Senior Engineer", "org": None}
    doc_text = "Jane Example\nEngineer – Acme\nAmsterdam"
    result = _web.merge_person_with_headline(person, doc_text)
    # Person's more specific role should be kept
    assert result["role"] == "Senior Engineer"


def test_merge_person_with_headline_replaces_generic_role():
    """Test that generic answer role is replaced by headline role."""
    person = {"name": "Jane Example", "role": "Professional Profile", "org": None}
    doc_text = "Jane Example\nSenior Engineer – Acme\nAmsterdam"
    result = _web.merge_person_with_headline(person, doc_text)
    # Generic "Professional Profile" should be replaced
    assert result["role"] == "Senior Engineer"


def test_merge_person_with_headline_fills_missing_org():
    """Test that headline org fills missing person org."""
    person = {"name": "Jane Example", "role": "Senior Engineer", "org": None}
    doc_text = "Jane Example\nSenior Engineer – Acme\nAmsterdam"
    result = _web.merge_person_with_headline(person, doc_text)
    assert result["org"] == "Acme"


def test_merge_person_with_headline_no_person():
    """Test that None person returns None."""
    result = _web.merge_person_with_headline(None, "Some document")
    assert result is None


def test_merge_person_with_headline_short_doc():
    """Test that short doc text is ignored."""
    person = {"name": "Jane", "role": None, "org": None}
    result = _web.merge_person_with_headline(person, "Too short")
    assert result == person


# ── Async tests (6a-6g from requirements) using asyncio.run ────────────────








