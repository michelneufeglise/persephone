"""
Catalog coverage tests — the setup wizard's "must-include" families are all
present, and the tok/s estimator returns something for every model on
representative hardware profiles.
"""

from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import benchmarks       # noqa: E402
import model_catalog    # noqa: E402


# ── Required families ───────────────────────────────────────────────────────
# The user's original ask: "Include a deepseek model, multiple gemma models,
# multiple qwen models, MOE models with thinking, and off course the vision
# and other models."

REQUIRED_MODEL_IDS: list[str] = [
    # DeepSeek — multiple sizes
    "deepseek-r1:8b",
    "deepseek-r1:14b",
    "deepseek-r1:32b",
    "deepseek-r1:70b",
    # Gemma — multiple sizes
    "gemma3:9b",
    "gemma3:27b",
    "gemma4:12b",
    "gemma4:26b",
    # Qwen — multiple sizes
    "qwen2.5:7b",
    "qwen2.5:14b",
    "qwen2.5:32b",
    # MoE thinkers
    "qwen3.6:35b-a3b",
    "nemotron-3-nano:30b",
    # Vision
    "minicpm-v:latest",
    "qwen2.5vl:7b",
    "qwen2.5vl:32b",
    # Embeddings
    "mxbai-embed-large",
    "nomic-embed-text",
]


class TestRequiredCoverage:
    def _all_ids(self) -> set[str]:
        return {m["id"] for m in model_catalog.MODELS}

    def test_all_required_models_in_catalog(self):
        ids = self._all_ids()
        missing = [m for m in REQUIRED_MODEL_IDS if m not in ids]
        assert not missing, f"Missing from catalog: {missing}"

    def test_moe_thinkers_are_flagged_correctly(self):
        assert benchmarks._is_moe("qwen3.6:35b-a3b")
        assert benchmarks._is_moe("nemotron-3-nano:30b")

    def test_reasoning_thinkers_are_flagged(self):
        assert benchmarks._is_thinker("deepseek-r1:70b")
        assert benchmarks._is_thinker("deepseek-r1:14b")
        assert benchmarks._is_thinker("qwen3.6:35b-a3b")


# ── Every catalog model returns a sensible tok/s ────────────────────────────
_M1_MAX = {
    "chip_family":        "apple_silicon_m1",
    "chip_variant":       "max",
    "ram_gb":             64,
    "mem_bandwidth_gb_s": 400,
}
_M4_BASE = {
    "chip_family":        "apple_silicon_m4",
    "chip_variant":       "base",
    "ram_gb":             16,
    "mem_bandwidth_gb_s": 120,
}


class TestCatalogEstimation:
    def test_every_model_returns_nonnegative(self):
        for m in model_catalog.MODELS:
            est = benchmarks.estimate_tok_per_s(m, _M1_MAX)
            assert est >= 0, f"{m['id']} returned negative: {est}"

    def test_every_model_capped_at_ceiling(self):
        for m in model_catalog.MODELS:
            est = benchmarks.estimate_tok_per_s(m, _M1_MAX)
            assert est <= 250, f"{m['id']} exceeded ceiling: {est}"

    def test_small_models_run_on_16gb(self):
        # A 7B model should run on the M4 base 16GB profile.
        m = next(x for x in model_catalog.MODELS if x["id"] == "qwen2.5:7b")
        est = benchmarks.estimate_tok_per_s(m, _M4_BASE)
        assert est > 0

    def test_70b_model_wont_run_on_16gb(self):
        m = next(x for x in model_catalog.MODELS if x["id"] == "deepseek-r1:70b")
        est = benchmarks.estimate_tok_per_s(m, _M4_BASE)
        assert est == 0.0

    def test_moe_beats_similar_dense_on_same_hardware(self):
        moe   = next(x for x in model_catalog.MODELS if x["id"] == "qwen3.6:35b-a3b")
        dense = next(x for x in model_catalog.MODELS if x["id"] == "qwen2.5:32b")
        moe_est   = benchmarks.estimate_tok_per_s(moe,   _M1_MAX)
        dense_est = benchmarks.estimate_tok_per_s(dense, _M1_MAX)
        # MoE should be dramatically faster (roughly 3x on M1 Max).
        assert moe_est > 2 * dense_est, (
            f"MoE {moe_est} should beat dense {dense_est} by a wide margin"
        )
