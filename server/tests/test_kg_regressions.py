import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from doc_agent_hooks import pick_signature_model


class TestModelResolutionEmptyConfig:
    def test_pick_signature_model_prefers_gemma_over_deepseek(self):
        cfg = {"handwriting_model": "", "vision_model": ""}
        installed = ["deepseek-r1:14b", "gemma4:12b"]
        is_vision = lambda n: "vision" in n.lower() or "gemma" in n.lower()
        result = pick_signature_model(cfg, installed, is_vision)
        assert result == "gemma4:12b", f"Should prefer gemma4 over deepseek, got {result}"


class TestFactDeduplication:
    def test_deduplicate_repeated_facts(self):
        facts_with_duplicates = """alice -- candidate_profile --> https://linkedin.com/in/alice
alice -- candidate_profile --> https://linkedin.com/in/alice
alice -- candidate_profile --> https://linkedin.com/in/alice
alice -- has_role --> Software Engineer
alice -- works_at --> TechCorp""".strip()

        lines = facts_with_duplicates.split('\n')
        unique_facts = list(dict.fromkeys(lines))

        assert len(unique_facts) == 3, f"Should deduplicate to 3 unique facts, got {len(unique_facts)}"


class TestHeadlineExtraction:
    def test_extract_headline_from_long_cv(self):
        cv_text = ("2023-01/ present\n" * 100) + "\n2023-01/ present\nSolution Architect – Rabobank\nRBO app"

        lines = cv_text.split('\n')
        headline = None
        for line in reversed(lines):
            line = line.strip()
            if line and "Architect" in line and "Rabobank" in line:
                headline = line
                break

        assert headline is not None, "Should extract headline from long text"
        assert "Solution Architect" in headline, f"Should contain role, got {headline}"
        assert "Rabobank" in headline, f"Should contain org, got {headline}"


class TestKGStoreURLFiltering:
    def test_filter_pub_dir_urls(self):
        candidates = [
            {"url": "https://example.com/profile", "name": "Alice"},
            {"url": "https://example.com/pub/dir/profile", "name": "Bob"},
            {"url": "https://example.com/about", "name": "Charlie"},
        ]

        filtered = [c for c in candidates if "/pub/dir/" not in c.get("url", "")]

        assert len(filtered) == 2, f"Should filter out /pub/dir/ URL, got {len(filtered)}"
        assert all("/pub/dir/" not in c["url"] for c in filtered), "Filtered URLs should not contain /pub/dir/"
        assert any(c["name"] == "Alice" for c in filtered), "Should keep Alice"
        assert any(c["name"] == "Charlie" for c in filtered), "Should keep Charlie"
        assert not any(c["name"] == "Bob" for c in filtered), "Should remove Bob with /pub/dir/"
