"""
Tests for `server/benchmarks.py` — per-model tok/s estimator.
"""

from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import benchmarks  # noqa: E402


# ── Sample model dicts (subset of model_catalog fields we care about) ───────
def make_model(id_: str, params: str, size_gb: float, ram_min_gb: float = 0):
    return {"id": id_, "params": params, "size_gb": size_gb, "ram_min_gb": ram_min_gb}


PROFILE_M1_MAX = {
    "chip_family":        "apple_silicon_m1",
    "chip_variant":       "max",
    "ram_gb":             64,
    "mem_bandwidth_gb_s": 400,
}
PROFILE_M2_BASE = {
    "chip_family":        "apple_silicon_m2",
    "chip_variant":       "base",
    "ram_gb":             16,
    "mem_bandwidth_gb_s": 100,
}
PROFILE_INTEL_LOW = {
    "chip_family":        "intel_11th",
    "chip_variant":       "n/a",
    "ram_gb":             16,
    "mem_bandwidth_gb_s": 50,
}


# ── MoE detection ───────────────────────────────────────────────────────────
class TestMoeDetection:
    def test_qwen36_a3b_is_moe(self):
        assert benchmarks._is_moe("qwen3.6:35b-a3b")

    def test_nemotron_nano_is_moe(self):
        assert benchmarks._is_moe("nemotron-3-nano:30b")

    def test_mixtral_is_moe(self):
        assert benchmarks._is_moe("mixtral:8x7b")

    def test_dense_llama_is_not_moe(self):
        assert not benchmarks._is_moe("llama3.3:70b")

    def test_dense_qwen25_is_not_moe(self):
        assert not benchmarks._is_moe("qwen2.5:32b")


# ── Active-params parsing ───────────────────────────────────────────────────
class TestActiveParams:
    def test_a3b_extracted_from_35b_a3b(self):
        # 22GB total × (3/35) = ~1.89GB active
        active = benchmarks._parse_moe_active_gb(22.0, "35B-A3B")
        assert 1.5 < active < 2.3

    def test_a3b_extracted_from_30b_a3b(self):
        active = benchmarks._parse_moe_active_gb(24.0, "30B-A3B")
        # 24 * 3/30 = 2.4
        assert 2.2 < active < 2.6

    def test_no_moe_label_uses_fallback(self):
        # Plain "70B" → falls back to 15% heuristic → 10.5GB
        active = benchmarks._parse_moe_active_gb(43.0, "70B")
        assert 6 < active < 8

    def test_case_insensitive(self):
        active = benchmarks._parse_moe_active_gb(20.0, "35b-a3b")
        assert 1.5 < active < 2.2


# ── Tok/s estimation ────────────────────────────────────────────────────────
class TestTokPerSEstimation:
    def test_qwen36_a3b_fast_on_m1_max(self):
        # Override table sets this to 45.
        m = make_model("qwen3.6:35b-a3b", "35B-A3B", 22.0, 20)
        assert benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX) == 45.0

    def test_deepseek_r1_70b_slow_on_m1_max(self):
        m = make_model("deepseek-r1:70b", "70B", 43.0, 40)
        est = benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX)
        # Override says 5.0.
        assert est == 5.0

    def test_model_too_big_returns_zero(self):
        m = make_model("giant:200b", "200B", 120.0, 130)
        assert benchmarks.estimate_tok_per_s(m, PROFILE_M2_BASE) == 0.0

    def test_qwen25_7b_fast_on_m1_max(self):
        # Not in override table — uses formula: 400 * 0.7 / 4.7 ≈ 59.6
        m = make_model("qwen2.5:7b", "7B", 4.7)
        est = benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX)
        assert 40 < est < 80

    def test_qwen25_7b_slow_on_intel_low(self):
        # 50 * 0.7 / 4.7 ≈ 7.4 tok/s — meets 'slow' rating.
        m = make_model("qwen2.5:7b", "7B", 4.7)
        est = benchmarks.estimate_tok_per_s(m, PROFILE_INTEL_LOW)
        assert 5 < est < 10

    def test_moe_uses_active_params_not_total(self):
        # A dense 22GB model would be 400*0.7/22 ≈ 12.7 tok/s.
        # MoE with 3B active: 400*0.8/1.9 ≈ 168 → clipped to 45 by override.
        # Without override — remove the model_id substring:
        m = make_model("some-unknown-moe:35b-a3b", "35B-A3B", 22.0)
        est = benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX)
        assert est > 40   # dramatically faster than dense-22GB would be

    def test_ceiling_at_250(self):
        # Very small model, very fast machine — should cap.
        m = make_model("tiny:0.5b", "0.5B", 0.4)
        est = benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX)
        assert est <= 250

    def test_size_zero_returns_zero(self):
        m = make_model("weird:0b", "0B", 0)
        assert benchmarks.estimate_tok_per_s(m, PROFILE_M1_MAX) == 0.0


# ── Fit rating ──────────────────────────────────────────────────────────────
class TestFitRating:
    def test_top(self):
        assert benchmarks.fit_rating(50.0, min_target=20.0) == "top"

    def test_good(self):
        assert benchmarks.fit_rating(25.0, min_target=20.0) == "good"

    def test_acceptable(self):
        assert benchmarks.fit_rating(12.0, min_target=20.0) == "acceptable"

    def test_slow(self):
        assert benchmarks.fit_rating(5.0, min_target=20.0) == "slow"

    def test_unsupported(self):
        assert benchmarks.fit_rating(0.0) == "unsupported"

    def test_negative_treated_as_unsupported(self):
        assert benchmarks.fit_rating(-1.0) == "unsupported"

    def test_custom_target(self):
        # If user relaxes target to 10 tok/s, 12 → good.
        assert benchmarks.fit_rating(12.0, min_target=10.0) == "good"
